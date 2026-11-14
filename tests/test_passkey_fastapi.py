"""Check the FastAPI passkey routes and dependencies over HTTP."""

from contextlib import contextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from opendle.local_auth import Principal, new_secret
from opendle.passkey import PasskeyConfig, PasskeyService
from opendle.passkey_fastapi import AuthStore, PasskeyAuth
from passkey_support import (
    CONFIG,
    ORIGIN,
    FakeClock,
    MemoryStore,
    new_passkey,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    import httpx2

    from opendle.contracts import JsonObject

_SESSION_COOKIE = "__Host-app_session"
_CHALLENGE_COOKIE = "__Host-app_challenge"
_SAME_ORIGIN = {"origin": ORIGIN, "sec-fetch-site": "same-origin"}


class Site:
    """Hold one application with its store, client, and passkey."""

    def __init__(self, config: PasskeyConfig = CONFIG) -> None:
        """Mount the passkey routes and two protected routes."""
        self.store = MemoryStore()
        self.clock = FakeClock()
        self.commits = 0
        self.device = new_passkey()
        self.auth = PasskeyAuth(
            config=config,
            store_factory=self._unit_of_work,
            cookie_prefix="app",
            session_days=2,
            clock=self.clock,
        )
        app = FastAPI()
        app.include_router(self.auth.router(), prefix="/auth")

        @app.api_route("/any", methods=["GET", "POST"])
        def any_principal(  # pyright: ignore[reportUnusedFunction]
            principal: Annotated[Principal, Depends(self.auth.principal)],
        ) -> dict[str, str | None]:
            return {"kind": principal.kind, "token_id": principal.token_id}

        @app.get("/browser")
        def browser_principal(  # pyright: ignore[reportUnusedFunction]
            principal: Annotated[Principal, Depends(self.auth.session_principal)],
        ) -> dict[str, str]:
            return {"user_id": principal.user_id}

        self.client = TestClient(app, base_url=config.origin, headers=_SAME_ORIGIN)

    @contextmanager
    def _unit_of_work(self) -> Generator[AuthStore]:
        yield self.store
        self.commits += 1

    def setup_token(self) -> str:
        """Create a setup grant as the operator."""
        service = PasskeyService(self.store, CONFIG, clock=self.clock)
        return service.create_setup_grant("Owner")

    def register(self) -> httpx2.Response:
        """Register the passkey of the site through a new setup grant."""
        token = self.setup_token()
        started = self.client.post(
            "/auth/register/options", json={"setup_token": token}
        )
        assert started.status_code == HTTPStatus.OK
        return self.client.post(
            "/auth/register/verify",
            json={
                "challenge_id": started.json()["challenge_id"],
                "setup_token": token,
                "credential": self.device.create(_options(started)),
                "label": "Laptop",
            },
        )

    def login(self) -> httpx2.Response:
        """Sign in with the passkey of the site."""
        started = self.client.post("/auth/login/options")
        return self.client.post(
            "/auth/login/verify",
            json={
                "challenge_id": started.json()["challenge_id"],
                "credential": self.device.get(_options(started)),
            },
        )


def _options(response: httpx2.Response) -> JsonObject:
    return cast("JsonObject", response.json()["options"])


def test_registration_sets_a_host_session_cookie_and_signs_in() -> None:
    """The first passkey creates the user and an HTTP-only session cookie."""
    site = Site()

    assert site.client.get("/auth/session").json() == {"user": None}

    token = site.setup_token()
    started = site.client.post("/auth/register/options", json={"setup_token": token})
    challenge_cookie = started.headers["set-cookie"]

    assert started.headers["cache-control"] == "no-store"
    assert challenge_cookie.startswith(f"{_CHALLENGE_COOKIE}=")
    assert "HttpOnly" in challenge_cookie
    assert "Max-Age=300" in challenge_cookie

    finished = site.client.post(
        "/auth/register/verify",
        json={
            "challenge_id": started.json()["challenge_id"],
            "setup_token": token,
            "credential": site.device.create(_options(started)),
        },
    )
    session_cookie = finished.headers.get_list("set-cookie")[0]
    user_id = next(iter(site.store.users))

    assert finished.status_code == HTTPStatus.OK
    assert finished.json() == {"user": {"id": user_id, "name": "Owner"}}
    assert session_cookie.startswith(f"{_SESSION_COOKIE}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=172800"):
        assert attribute in session_cookie
    assert site.client.cookies.get(_SESSION_COOKIE) not in site.store.sessions
    assert site.client.get("/auth/session").json() == finished.json()
    assert site.client.get("/browser").json() == {"user_id": user_id}


def test_sign_in_replaces_the_session_and_logout_ends_it() -> None:
    """A sign-in deletes the old session record; logout clears the cookie."""
    site = Site()
    site.register()
    first_hash = next(iter(site.store.sessions))

    signed_in = site.login()

    assert signed_in.status_code == HTTPStatus.OK
    assert list(site.store.sessions) != [first_hash]
    assert len(site.store.sessions) == 1

    signed_out = site.client.post("/auth/logout")

    assert signed_out.status_code == HTTPStatus.NO_CONTENT
    assert site.store.sessions == {}
    assert site.client.get("/any").status_code == HTTPStatus.UNAUTHORIZED
    assert site.client.post("/auth/logout").status_code == HTTPStatus.NO_CONTENT


def test_a_refused_ceremony_keeps_its_used_challenge_and_reports_a_status() -> None:
    """The adapter commits after an `AuthError` and maps the reason."""
    site = Site()
    bad_grant = site.client.post(
        "/auth/register/options",
        json={"setup_token": new_secret()},
    )

    assert bad_grant.status_code == HTTPStatus.FORBIDDEN
    assert bad_grant.json() == {"detail": "The setup link is not valid."}

    site.register()
    started = site.client.post("/auth/login/options")
    commits = site.commits
    site.client.cookies.set(_CHALLENGE_COOKIE, new_secret(), domain="app.example")
    refused = site.client.post(
        "/auth/login/verify",
        json={
            "challenge_id": started.json()["challenge_id"],
            "credential": site.device.get(_options(started)),
        },
    )

    assert refused.status_code == HTTPStatus.BAD_REQUEST
    assert site.store.challenges == {}
    assert site.commits == commits + 1


def test_a_session_manages_passkeys() -> None:
    """A browser session lists, adds, and removes passkeys."""
    site = Site()
    site.register()
    first_id = site.device.credential_id
    second = new_passkey()

    listed = site.client.get("/auth/passkeys").json()
    assert [(item["id"], item["label"]) for item in listed] == [(first_id, "Laptop")]

    last = site.client.delete(f"/auth/passkeys/{first_id}")
    assert last.status_code == HTTPStatus.CONFLICT

    started = site.client.post("/auth/passkeys/options")
    added = site.client.post(
        "/auth/passkeys/verify",
        json={
            "challenge_id": started.json()["challenge_id"],
            "credential": second.create(_options(started)),
            "label": "Phone",
        },
    )

    assert added.status_code == HTTPStatus.OK
    assert added.json()["label"] == "Phone"
    assert added.json()["last_used_at"] is None
    renamed = site.client.patch(
        f"/auth/passkeys/{first_id}",
        json={"label": "Work laptop"},
    )
    assert renamed.status_code == HTTPStatus.OK
    assert renamed.json()["label"] == "Work laptop"
    unknown = site.client.patch("/auth/passkeys/unknown", json={"label": "Key"})
    assert unknown.status_code == HTTPStatus.NOT_FOUND
    missing = site.client.delete("/auth/passkeys/unknown")
    assert missing.status_code == HTTPStatus.NOT_FOUND
    removed = site.client.delete(f"/auth/passkeys/{first_id}")
    assert removed.status_code == HTTPStatus.NO_CONTENT
    assert [item["id"] for item in site.client.get("/auth/passkeys").json()] == [
        second.credential_id,
    ]


def test_a_session_manages_api_tokens_and_a_token_signs_in() -> None:
    """A token shows its secret one time and works without cookies."""
    site = Site()
    site.register()

    created = site.client.post("/auth/tokens", json={"name": "agent", "days": 3})
    body = created.json()
    token_id = body["token"]["id"]

    assert created.headers["cache-control"] == "no-store"
    assert body["token"]["name"] == "agent"
    assert site.client.get("/auth/tokens").json() == [body["token"]]
    bad_name = site.client.post("/auth/tokens", json={"name": " "})
    assert bad_name.status_code == HTTPStatus.BAD_REQUEST

    agent = TestClient(site.client.app, base_url=ORIGIN)
    bearer = {"authorization": f"Bearer {body['secret']}"}

    assert agent.post("/any", headers=bearer).json() == {
        "kind": "token",
        "token_id": token_id,
    }
    assert agent.get("/browser", headers=bearer).status_code == HTTPStatus.FORBIDDEN
    for bad in ("Basic abc", f"Bearer {new_secret()}"):
        refused = agent.get("/any", headers={"authorization": bad})
        assert refused.status_code == HTTPStatus.UNAUTHORIZED
    assert agent.get("/auth/tokens", headers=bearer).status_code == HTTPStatus.FORBIDDEN

    missing = site.client.delete("/auth/tokens/unknown")
    assert missing.status_code == HTTPStatus.NOT_FOUND
    revoked = site.client.delete(f"/auth/tokens/{token_id}")
    assert revoked.status_code == HTTPStatus.NO_CONTENT
    assert agent.get("/any", headers=bearer).status_code == HTTPStatus.UNAUTHORIZED


def test_a_session_write_must_come_from_the_same_origin() -> None:
    """A read is free; a write needs the origin and a same-origin fetch."""
    site = Site()
    site.register()

    assert site.client.get(
        "/any", headers={"origin": "https://evil.example"}
    ).is_success
    assert site.client.post("/any").is_success
    for headers in (
        {"origin": "https://evil.example"},
        {"sec-fetch-site": "cross-site"},
    ):
        for path in ("/any", "/auth/logout"):
            refused = site.client.post(path, headers=headers)
            assert refused.status_code == HTTPStatus.FORBIDDEN
            assert refused.json() == {"detail": "Invalid request origin."}
    assert len(site.store.sessions) == 1


def test_a_local_http_origin_uses_plain_cookie_names() -> None:
    """Development on HTTP cannot use the `__Host-` prefix or `Secure`."""
    local = PasskeyConfig.from_origin("http://localhost:8000", rp_name="Example")
    site = Site(local)

    started = site.client.post(
        "/auth/login/options",
        headers={"origin": local.origin},
    )
    cookie = started.headers["set-cookie"]

    assert cookie.startswith("app_challenge=")
    assert "Secure" not in cookie
