"""Local browser sessions and bearer API tokens with caller-owned storage.

The module creates and checks secrets. The caller stores only their SHA-256
hashes through `SessionStore`. The module has no dependency and no rate limit.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "ApiToken",
    "AuthError",
    "AuthErrorReason",
    "Clock",
    "IssuedSession",
    "IssuedToken",
    "LocalSessions",
    "Principal",
    "SessionStore",
    "hash_secret",
    "is_secret",
    "new_secret",
    "utc_now",
]

type Clock = Callable[[], datetime]
type AuthErrorReason = Literal[
    "challenge",
    "input",
    "last_passkey",
    "passkey",
    "setup_grant",
]

_SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_MAXIMUM_NAME_LENGTH = 100
_MAXIMUM_TOKEN_DAYS = 3650


class AuthError(Exception):
    """Report a refused local authentication operation.

    The service raises this error before it writes a user, passkey, session,
    or token record. A caller can thus commit its transaction after this
    error, which keeps a used challenge or setup grant used.
    """

    def __init__(self, message: str, *, reason: AuthErrorReason) -> None:
        """Keep a message that is safe to show and a stable reason."""
        super().__init__(message)
        self.reason: AuthErrorReason = reason


def utc_now() -> datetime:
    """Return the current time with the UTC time zone."""
    return datetime.now(UTC)


def new_secret() -> str:
    """Return a new random secret of 32 bytes as 43 URL-safe characters."""
    return secrets.token_urlsafe(32)


def is_secret(value: object) -> bool:
    """Return whether a value has the format of `new_secret`."""
    return isinstance(value, str) and _SECRET_PATTERN.fullmatch(value) is not None


def hash_secret(value: str) -> str:
    """Return the SHA-256 hexadecimal digest that a store keeps for a secret."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Principal:
    """Identify the user of one request and the credential type."""

    user_id: str
    kind: Literal["session", "token"]
    token_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApiToken:
    """Describe one bearer API token without its secret."""

    id: str
    user_id: str
    name: str
    created_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Carry a new session secret. Give the secret only to the browser."""

    secret: str
    user_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """Carry a new API token and its secret. Show the secret one time."""

    token: ApiToken
    secret: str


class SessionStore(Protocol):
    """Store session and API token records for `LocalSessions`."""

    def add_session(
        self,
        session_hash: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        """Store one session by the hash of its secret."""
        ...

    def session_user(self, session_hash: str, now: datetime) -> str | None:
        """Return the user of a session that expires after `now`."""
        ...

    def remove_session(self, session_hash: str) -> None:
        """Delete one session. A missing session is not an error."""
        ...

    def add_token(self, token: ApiToken, token_hash: str) -> None:
        """Store one API token by the hash of its secret."""
        ...

    def token_by_hash(self, token_hash: str, now: datetime) -> ApiToken | None:
        """Return a token that has no expiry or expires after `now`."""
        ...

    def tokens(self, user_id: str) -> Sequence[ApiToken]:
        """Return the API tokens of one user, oldest first."""
        ...

    def remove_token(self, user_id: str, token_id: str) -> bool:
        """Delete one API token of a user. Return whether it existed."""
        ...


class LocalSessions:
    """Create and check browser sessions and bearer API tokens."""

    def __init__(
        self,
        store: SessionStore,
        *,
        session_days: int = 14,
        clock: Clock = utc_now,
    ) -> None:
        """Use a caller-owned store and a fixed session lifetime in days."""
        if session_days < 1:
            msg = "The session lifetime must be one day or more."
            raise ValueError(msg)
        self._store = store
        self._session_lifetime = timedelta(days=session_days)
        self._clock = clock

    def authenticate(
        self,
        *,
        session_secret: str | None = None,
        bearer_token: str | None = None,
    ) -> Principal | None:
        """Return the principal of a request, or `None`.

        A bearer token has priority. A request that sends a bad bearer token
        does not fall back to its session.
        """
        now = self._clock()
        if bearer_token is not None:
            if not is_secret(bearer_token):
                return None
            token = self._store.token_by_hash(hash_secret(bearer_token), now)
            if token is None:
                return None
            return Principal(user_id=token.user_id, kind="token", token_id=token.id)
        if session_secret is None or not is_secret(session_secret):
            return None
        user_id = self._store.session_user(hash_secret(session_secret), now)
        if user_id is None:
            return None
        return Principal(user_id=user_id, kind="session")

    def start_session(
        self,
        user_id: str,
        *,
        replaced_secret: str | None = None,
    ) -> IssuedSession:
        """Create a session for a user and delete the session it replaces."""
        if replaced_secret is not None:
            self.end_session(replaced_secret)
        secret = new_secret()
        expires_at = self._clock() + self._session_lifetime
        self._store.add_session(hash_secret(secret), user_id, expires_at)
        return IssuedSession(secret=secret, user_id=user_id, expires_at=expires_at)

    def end_session(self, session_secret: str) -> None:
        """Delete the session of a secret. An unknown secret is not an error."""
        if is_secret(session_secret):
            self._store.remove_session(hash_secret(session_secret))

    def create_token(
        self,
        user_id: str,
        name: str,
        *,
        days: int | None = None,
    ) -> IssuedToken:
        """Create an API token. `days=None` gives a token without expiry.

        Raises:
            AuthError: If the name or the lifetime is not valid.

        """
        clean_name = name.strip()
        if not clean_name or len(clean_name) > _MAXIMUM_NAME_LENGTH:
            msg = "The token name must have from 1 to 100 characters."
            raise AuthError(msg, reason="input")
        if days is not None and not 1 <= days <= _MAXIMUM_TOKEN_DAYS:
            msg = "The token lifetime must be from 1 to 3650 days."
            raise AuthError(msg, reason="input")
        now = self._clock()
        token = ApiToken(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=clean_name,
            created_at=now,
            expires_at=None if days is None else now + timedelta(days=days),
        )
        secret = new_secret()
        self._store.add_token(token, hash_secret(secret))
        return IssuedToken(token=token, secret=secret)

    def tokens(self, user_id: str) -> Sequence[ApiToken]:
        """Return the API tokens of one user, oldest first."""
        return self._store.tokens(user_id)

    def revoke_token(self, user_id: str, token_id: str) -> bool:
        """Delete one API token of a user. Return whether it existed."""
        return self._store.remove_token(user_id, token_id)
