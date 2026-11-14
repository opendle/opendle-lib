"""Supply an in-memory store, a clock, and a software passkey for tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from opendle.passkey import PasskeyConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opendle.contracts import JsonObject
    from opendle.local_auth import ApiToken
    from opendle.passkey import Passkey, SetupGrant, StoredChallenge

ORIGIN = "https://app.example"
CONFIG = PasskeyConfig.from_origin(ORIGIN, rp_name="Example")


def b64url(value: bytes) -> str:
    """Return unpadded base64url text."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


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


class SoftPasskey:
    """Act as one discoverable passkey with user verification."""

    def __init__(self, *, origin: str = ORIGIN, rp_id: str = CONFIG.rp_id) -> None:
        """Create a P-256 key and a random credential ID."""
        self._key = ec.generate_private_key(ec.SECP256R1())
        self._origin = origin
        self._rp_id_hash = hashlib.sha256(rp_id.encode("ascii")).digest()
        self._sign_count = 0
        self._user_handle = ""
        self.credential_id = b64url(os.urandom(16))

    def use_identity_of(self, credential_id: str, user_id: str) -> None:
        """Claim the credential ID and the user of a different passkey."""
        self.credential_id = credential_id
        self._user_handle = b64url(user_id.encode("utf-8"))

    def create(
        self,
        options: JsonObject,
        *,
        transports: list[object] | None = None,
    ) -> dict[str, object]:
        """Answer registration options as `navigator.credentials.create`."""
        user = cast("JsonObject", options["user"])
        self._user_handle = cast("str", user["id"])
        numbers = self._key.public_key().public_numbers()
        public_key = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            },
        )
        raw_id = base64.urlsafe_b64decode(self.credential_id + "==")
        authenticator_data = (
            self._rp_id_hash
            + bytes([0x45])
            + self._sign_count.to_bytes(4, "big")
            + bytes(16)
            + len(raw_id).to_bytes(2, "big")
            + raw_id
            + public_key
        )
        response: dict[str, object] = {
            "clientDataJSON": self._client_data("webauthn.create", options),
            "attestationObject": b64url(
                cbor2.dumps(
                    {"fmt": "none", "attStmt": {}, "authData": authenticator_data},
                ),
            ),
        }
        if transports is not None:
            response["transports"] = transports
        return self._credential(response)

    def get(self, options: JsonObject) -> dict[str, object]:
        """Answer sign-in options as `navigator.credentials.get`."""
        self._sign_count += 1
        client_data = self._client_data("webauthn.get", options)
        authenticator_data = (
            self._rp_id_hash + bytes([0x05]) + self._sign_count.to_bytes(4, "big")
        )
        signed = (
            authenticator_data
            + hashlib.sha256(
                base64.urlsafe_b64decode(client_data + "=="),
            ).digest()
        )
        return self._credential(
            {
                "clientDataJSON": client_data,
                "authenticatorData": b64url(authenticator_data),
                "signature": b64url(self._key.sign(signed, ec.ECDSA(hashes.SHA256()))),
                "userHandle": self._user_handle,
            },
        )

    def _client_data(self, ceremony: str, options: JsonObject) -> str:
        return b64url(
            json.dumps(
                {
                    "type": ceremony,
                    "challenge": options["challenge"],
                    "origin": self._origin,
                    "crossOrigin": False,
                },
            ).encode("utf-8"),
        )

    def _credential(self, response: dict[str, object]) -> dict[str, object]:
        return {
            "id": self.credential_id,
            "rawId": self.credential_id,
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
        }
