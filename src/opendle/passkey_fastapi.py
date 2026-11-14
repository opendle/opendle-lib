"""Optional FastAPI routes and dependencies for passkey sign-in.

The module joins `opendle.local_auth` and `opendle.passkey` to FastAPI. The
caller supplies the store and mounts the router below its own path prefix.
"""

# FastAPI keeps the route functions; no other code calls them.
# pyright: reportUnusedFunction=false

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic reads it at runtime.
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from opendle.local_auth import (
    ApiToken,
    AuthError,
    AuthErrorReason,
    Clock,
    LocalSessions,
    Principal,
    SessionStore,
    utc_now,
)
from opendle.passkey import (
    Passkey,
    PasskeyConfig,
    PasskeyService,
    PasskeyStore,
    StartedCeremony,
)

__all__ = [
    "AuthStore",
    "AuthStoreFactory",
    "PasskeyAuth",
]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CHALLENGE_SECONDS = 300
_BEARER_PREFIX = "Bearer "
_STATUS_BY_REASON: dict[AuthErrorReason, int] = {
    "challenge": 400,
    "input": 400,
    "last_passkey": 409,
    "passkey": 400,
    "setup_grant": 403,
}


class AuthStore(SessionStore, PasskeyStore, Protocol):
    """Store all records for sessions, API tokens, and passkeys."""


type AuthStoreFactory = Callable[[], AbstractContextManager[AuthStore]]


class UserView(BaseModel):
    """Describe the signed-in user."""

    id: str
    name: str


class SessionView(BaseModel):
    """Describe the browser session. `user` is null without a session."""

    user: UserView | None


class CeremonyView(BaseModel):
    """Carry WebAuthn options and the challenge ID for the verify request."""

    challenge_id: str
    options: dict[str, object]


class RegistrationOptionsRequest(BaseModel):
    """Start a registration with a setup grant."""

    setup_token: str = Field(max_length=100)


class RegistrationRequest(BaseModel):
    """Finish a registration with the browser credential JSON."""

    challenge_id: str = Field(max_length=100)
    setup_token: str = Field(max_length=100)
    credential: dict[str, object]
    label: str = Field(default="", max_length=100)


class LoginRequest(BaseModel):
    """Finish a sign-in with the browser credential JSON."""

    challenge_id: str = Field(max_length=100)
    credential: dict[str, object]


class PasskeyRequest(BaseModel):
    """Finish the addition of a passkey with the browser credential JSON."""

    challenge_id: str = Field(max_length=100)
    credential: dict[str, object]
    label: str = Field(default="", max_length=100)


class PasskeyLabelRequest(BaseModel):
    """Give a passkey a new label."""

    label: str = Field(min_length=1, max_length=64)


class PasskeyView(BaseModel):
    """Describe one passkey of the user."""

    id: str
    label: str
    created_at: datetime
    last_used_at: datetime | None


class TokenRequest(BaseModel):
    """Create an API token. Without `days`, the token has no expiry."""

    name: str = Field(max_length=100)
    days: int | None = None


class TokenView(BaseModel):
    """Describe one API token without its secret."""

    id: str
    name: str
    created_at: datetime
    expires_at: datetime | None


class IssuedTokenView(BaseModel):
    """Carry a new API token and its secret. The secret shows one time."""

    token: TokenView
    secret: str


@dataclass(frozen=True, slots=True)
class _Services:
    store: AuthStore
    sessions: LocalSessions
    passkeys: PasskeyService


class PasskeyAuth:
    """Supply the passkey routes and the request dependencies of a host."""

    def __init__(
        self,
        *,
        config: PasskeyConfig,
        store_factory: AuthStoreFactory,
        cookie_prefix: str,
        session_days: int = 14,
        clock: Clock = utc_now,
    ) -> None:
        """Configure the adapter.

        `store_factory` returns a context manager for one unit of work. It
        must commit on a normal exit and roll back on an exception. The
        adapter leaves the context normally after an `AuthError`, so a used
        challenge stays used.
        """
        self._config = config
        self._store_factory = store_factory
        self._session_days = session_days
        self._clock = clock
        host_prefix = "__Host-" if config.secure else ""
        self._session_cookie = f"{host_prefix}{cookie_prefix}_session"
        self._challenge_cookie = f"{host_prefix}{cookie_prefix}_challenge"

    def principal(self, request: Request) -> Principal:
        """Return the principal of a request. Use as a FastAPI dependency.

        The function accepts a bearer API token or a browser session. A
        session request that is not safe must come from the same origin.

        Raises:
            HTTPException: 401 without a valid credential, 403 for a session
                request from a different origin.

        """
        authorization = request.headers.get("authorization")
        bearer_token = None
        if authorization is not None:
            if not authorization.startswith(_BEARER_PREFIX):
                raise HTTPException(status_code=401, detail="Sign in.")
            bearer_token = authorization.removeprefix(_BEARER_PREFIX)
        principal = self._run(
            lambda services: services.sessions.authenticate(
                session_secret=request.cookies.get(self._session_cookie),
                bearer_token=bearer_token,
            ),
        )
        if principal is None:
            raise HTTPException(status_code=401, detail="Sign in.")
        if principal.kind == "session":
            self.require_same_origin(request)
        return principal

    def session_principal(self, request: Request) -> Principal:
        """Return the principal of a browser session. Refuse an API token.

        Raises:
            HTTPException: 401 without a valid credential, 403 for an API
                token or for a request from a different origin.

        """
        principal = self.principal(request)
        if principal.kind != "session":
            raise HTTPException(status_code=403, detail="Use a browser session.")
        return principal

    def require_same_origin(self, request: Request) -> None:
        """Refuse a request that is not safe and not from the same origin.

        Raises:
            HTTPException: 403 when the `Origin` header is not the configured
                origin or `Sec-Fetch-Site` is not `same-origin`.

        """
        if request.method in _SAFE_METHODS:
            return
        fetch_site = request.headers.get("sec-fetch-site")
        if request.headers.get("origin") != self._config.origin or fetch_site not in {
            None,
            "same-origin",
        }:
            raise HTTPException(status_code=403, detail="Invalid request origin.")

    def router(self) -> APIRouter:  # noqa: C901 - one flat route table.
        """Return the routes for sign-in, passkeys, and API tokens."""
        router = APIRouter(dependencies=[Depends(self.require_same_origin)])

        @router.get("/session")
        def read_session(request: Request, response: Response) -> SessionView:
            response.headers["Cache-Control"] = "no-store"
            return self._run(
                lambda services: _session_view(
                    services,
                    services.sessions.authenticate(
                        session_secret=request.cookies.get(self._session_cookie),
                    ),
                ),
            )

        @router.post("/register/options")
        def start_registration(
            body: RegistrationOptionsRequest,
            response: Response,
        ) -> CeremonyView:
            started = self._run(
                lambda services: services.passkeys.begin_registration(body.setup_token),
            )
            return self._ceremony_view(started, response)

        @router.post("/register/verify")
        def finish_registration(
            body: RegistrationRequest,
            request: Request,
            response: Response,
        ) -> SessionView:
            def operation(services: _Services) -> tuple[str, SessionView]:
                passkey = services.passkeys.finish_registration(
                    challenge_id=body.challenge_id,
                    binding=request.cookies.get(self._challenge_cookie),
                    credential=body.credential,
                    setup_token=body.setup_token,
                    label=body.label,
                )
                return self._signed_in(services, passkey, request)

            return self._session_response(self._run(operation), response)

        @router.post("/login/options")
        def start_login(response: Response) -> CeremonyView:
            started = self._run(lambda services: services.passkeys.begin_login())
            return self._ceremony_view(started, response)

        @router.post("/login/verify")
        def finish_login(
            body: LoginRequest,
            request: Request,
            response: Response,
        ) -> SessionView:
            def operation(services: _Services) -> tuple[str, SessionView]:
                passkey = services.passkeys.finish_login(
                    challenge_id=body.challenge_id,
                    binding=request.cookies.get(self._challenge_cookie),
                    credential=body.credential,
                )
                return self._signed_in(services, passkey, request)

            return self._session_response(self._run(operation), response)

        @router.post("/logout", status_code=204)
        def logout(request: Request, response: Response) -> None:
            secret = request.cookies.get(self._session_cookie)
            if secret is not None:
                self._run(lambda services: services.sessions.end_session(secret))
            self._set_cookie(response, self._session_cookie, "", 0)

        @router.get("/passkeys")
        def list_passkeys(
            principal: Annotated[Principal, Depends(self.session_principal)],
        ) -> list[PasskeyView]:
            return self._run(
                lambda services: [
                    _passkey_view(passkey)
                    for passkey in services.passkeys.passkeys(principal.user_id)
                ],
            )

        @router.post("/passkeys/options")
        def start_passkey(
            principal: Annotated[Principal, Depends(self.session_principal)],
            response: Response,
        ) -> CeremonyView:
            started = self._run(
                lambda services: services.passkeys.begin_addition(principal.user_id),
            )
            return self._ceremony_view(started, response)

        @router.post("/passkeys/verify")
        def finish_passkey(
            body: PasskeyRequest,
            principal: Annotated[Principal, Depends(self.session_principal)],
            request: Request,
            response: Response,
        ) -> PasskeyView:
            passkey = self._run(
                lambda services: services.passkeys.finish_addition(
                    user_id=principal.user_id,
                    challenge_id=body.challenge_id,
                    binding=request.cookies.get(self._challenge_cookie),
                    credential=body.credential,
                    label=body.label,
                ),
            )
            self._set_cookie(response, self._challenge_cookie, "", 0)
            return _passkey_view(passkey)

        @router.patch("/passkeys/{passkey_id}")
        def rename_passkey(
            passkey_id: str,
            body: PasskeyLabelRequest,
            principal: Annotated[Principal, Depends(self.session_principal)],
        ) -> PasskeyView:
            passkey = self._run(
                lambda services: services.passkeys.rename_passkey(
                    principal.user_id,
                    passkey_id,
                    body.label,
                ),
            )
            if passkey is None:
                raise HTTPException(status_code=404, detail="Passkey not found.")
            return _passkey_view(passkey)

        @router.delete("/passkeys/{passkey_id}", status_code=204)
        def remove_passkey(
            passkey_id: str,
            principal: Annotated[Principal, Depends(self.session_principal)],
        ) -> None:
            removed = self._run(
                lambda services: services.passkeys.remove_passkey(
                    principal.user_id,
                    passkey_id,
                ),
            )
            if not removed:
                raise HTTPException(status_code=404, detail="Passkey not found.")

        @router.get("/tokens")
        def list_tokens(
            principal: Annotated[Principal, Depends(self.session_principal)],
        ) -> list[TokenView]:
            return self._run(
                lambda services: _token_views(
                    services.sessions.tokens(principal.user_id),
                ),
            )

        @router.post("/tokens")
        def create_token(
            body: TokenRequest,
            principal: Annotated[Principal, Depends(self.session_principal)],
            response: Response,
        ) -> IssuedTokenView:
            response.headers["Cache-Control"] = "no-store"
            issued = self._run(
                lambda services: services.sessions.create_token(
                    principal.user_id,
                    body.name,
                    days=body.days,
                ),
            )
            return IssuedTokenView(
                token=_token_views([issued.token])[0],
                secret=issued.secret,
            )

        @router.delete("/tokens/{token_id}", status_code=204)
        def revoke_token(
            token_id: str,
            principal: Annotated[Principal, Depends(self.session_principal)],
        ) -> None:
            revoked = self._run(
                lambda services: services.sessions.revoke_token(
                    principal.user_id,
                    token_id,
                ),
            )
            if not revoked:
                raise HTTPException(status_code=404, detail="Token not found.")

        return router

    def _run[T](self, operation: Callable[[_Services], T]) -> T:
        failure: AuthError
        with self._store_factory() as store:
            services = _Services(
                store=store,
                sessions=LocalSessions(
                    store,
                    session_days=self._session_days,
                    clock=self._clock,
                ),
                passkeys=PasskeyService(store, self._config, clock=self._clock),
            )
            try:
                return operation(services)
            except AuthError as error:
                # Leave the context normally: the store commits used secrets.
                failure = error
        raise HTTPException(
            status_code=_STATUS_BY_REASON[failure.reason],
            detail=str(failure),
        ) from failure

    def _signed_in(
        self,
        services: _Services,
        passkey: Passkey,
        request: Request,
    ) -> tuple[str, SessionView]:
        issued = services.sessions.start_session(
            passkey.user_id,
            replaced_secret=request.cookies.get(self._session_cookie),
        )
        principal = Principal(user_id=passkey.user_id, kind="session")
        return issued.secret, _session_view(services, principal)

    def _session_response(
        self,
        signed_in: tuple[str, SessionView],
        response: Response,
    ) -> SessionView:
        secret, view = signed_in
        self._set_cookie(
            response,
            self._session_cookie,
            secret,
            self._session_days * 86400,
        )
        self._set_cookie(response, self._challenge_cookie, "", 0)
        response.headers["Cache-Control"] = "no-store"
        return view

    def _ceremony_view(
        self,
        started: StartedCeremony,
        response: Response,
    ) -> CeremonyView:
        self._set_cookie(
            response,
            self._challenge_cookie,
            started.binding,
            _CHALLENGE_SECONDS,
        )
        response.headers["Cache-Control"] = "no-store"
        return CeremonyView(
            challenge_id=started.challenge_id,
            options=dict(started.options),
        )

    def _set_cookie(
        self,
        response: Response,
        name: str,
        value: str,
        max_age: int,
    ) -> None:
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path="/",
            secure=self._config.secure,
            httponly=True,
            samesite="lax",
        )


def _session_view(services: _Services, principal: Principal | None) -> SessionView:
    if principal is None:
        return SessionView(user=None)
    name = services.store.user_name(principal.user_id)
    return SessionView(user=UserView(id=principal.user_id, name=name or ""))


def _passkey_view(passkey: Passkey) -> PasskeyView:
    return PasskeyView(
        id=passkey.id,
        label=passkey.label,
        created_at=passkey.created_at,
        last_used_at=passkey.last_used_at,
    )


def _token_views(tokens: Sequence[ApiToken]) -> list[TokenView]:
    return [
        TokenView(
            id=token.id,
            name=token.name,
            created_at=token.created_at,
            expires_at=token.expires_at,
        )
        for token in tokens
    ]
