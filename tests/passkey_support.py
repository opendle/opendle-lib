"""Supply an in-memory store, a clock, and a software passkey for tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from opendle.passkey import PasskeyConfig
from opendle.passkey_testing import SoftPasskey

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opendle.local_auth import ApiToken
    from opendle.passkey import Passkey, SetupGrant, StoredChallenge

__all__ = ["CONFIG", "ORIGIN", "FakeClock", "MemoryStore", "SoftPasskey", "new_passkey"]

ORIGIN = "https://app.example"
CONFIG = PasskeyConfig.from_origin(ORIGIN, rp_name="Example")


def new_passkey() -> SoftPasskey:
    """Return a software passkey for the test relying party."""
    return SoftPasskey(origin=ORIGIN, rp_id=CONFIG.rp_id)


class FakeClock:
    """Give a time that a test can move."""

    def __init__(self) -> None:
        """Start at a fixed time."""
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        """Return the current test time."""
        return self.now

    def advance(self, **interval: float) -> None:
        """Move the time forward."""
        self.now += timedelta(**interval)


class MemoryStore:
    """Keep all authentication records in memory."""

    def __init__(self) -> None:
        """Start without records."""
        self.sessions: dict[str, tuple[str, datetime]] = {}
        self.token_rows: dict[str, ApiToken] = {}
        self.challenges: dict[str, StoredChallenge] = {}
        self.users: dict[str, str] = {}
        self.passkey_rows: dict[str, Passkey] = {}
        self.grants: dict[str, tuple[SetupGrant, datetime]] = {}
        self.refuse_grant_use = False

    def add_session(
        self,
        session_hash: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        """Store one session."""
        self.sessions[session_hash] = (user_id, expires_at)

    def session_user(self, session_hash: str, now: datetime) -> str | None:
        """Return the user of a current session."""
        row = self.sessions.get(session_hash)
        return row[0] if row is not None and row[1] > now else None

    def remove_session(self, session_hash: str) -> None:
        """Delete one session."""
        self.sessions.pop(session_hash, None)

    def add_token(self, token: ApiToken, token_hash: str) -> None:
        """Store one API token."""
        self.token_rows[token_hash] = token

    def token_by_hash(self, token_hash: str, now: datetime) -> ApiToken | None:
        """Return a current API token."""
        token = self.token_rows.get(token_hash)
        if token is None or (token.expires_at is not None and token.expires_at <= now):
            return None
        return token

    def tokens(self, user_id: str) -> Sequence[ApiToken]:
        """Return the API tokens of one user."""
        return [token for token in self.token_rows.values() if token.user_id == user_id]

    def remove_token(self, user_id: str, token_id: str) -> bool:
        """Delete one API token."""
        for token_hash, token in self.token_rows.items():
            if token.id == token_id and token.user_id == user_id:
                del self.token_rows[token_hash]
                return True
        return False

    def add_challenge(self, challenge: StoredChallenge) -> None:
        """Store one pending ceremony."""
        self.challenges[challenge.id] = challenge

    def take_challenge(self, challenge_id: str) -> StoredChallenge | None:
        """Delete and return one pending ceremony."""
        return self.challenges.pop(challenge_id, None)

    def add_user(self, user_id: str, name: str) -> None:
        """Store one user."""
        self.users[user_id] = name

    def user_name(self, user_id: str) -> str | None:
        """Return the name of a user."""
        return self.users.get(user_id)

    def add_passkey(self, passkey: Passkey) -> None:
        """Store one passkey."""
        self.passkey_rows[passkey.id] = passkey

    def passkey(self, passkey_id: str) -> Passkey | None:
        """Return one passkey."""
        return self.passkey_rows.get(passkey_id)

    def passkeys(self, user_id: str) -> Sequence[Passkey]:
        """Return the passkeys of one user."""
        return [
            passkey
            for passkey in self.passkey_rows.values()
            if passkey.user_id == user_id
        ]

    def record_passkey_use(
        self,
        passkey_id: str,
        sign_count: int,
        used_at: datetime,
    ) -> None:
        """Store the counter and the time of a sign-in."""
        self.passkey_rows[passkey_id] = replace(
            self.passkey_rows[passkey_id],
            sign_count=sign_count,
            last_used_at=used_at,
        )

    def remove_passkey(self, user_id: str, passkey_id: str) -> bool:
        """Delete one passkey."""
        passkey = self.passkey_rows.get(passkey_id)
        if passkey is None or passkey.user_id != user_id:
            return False
        del self.passkey_rows[passkey_id]
        return True

    def add_setup_grant(
        self,
        grant_hash: str,
        grant: SetupGrant,
        expires_at: datetime,
    ) -> None:
        """Store one setup grant."""
        self.grants[grant_hash] = (grant, expires_at)

    def setup_grant(self, grant_hash: str, now: datetime) -> SetupGrant | None:
        """Return a current setup grant."""
        row = self.grants.get(grant_hash)
        return row[0] if row is not None and row[1] > now else None

    def consume_setup_grant(self, grant_hash: str, now: datetime) -> bool:
        """Use one setup grant."""
        if self.refuse_grant_use or self.setup_grant(grant_hash, now) is None:
            return False
        del self.grants[grant_hash]
        return True
