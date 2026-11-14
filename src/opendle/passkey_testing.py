"""Supply a software passkey for the tests of a passkey consumer.

The module needs the `passkey` extra. Do not use it in production code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import TYPE_CHECKING, cast

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

if TYPE_CHECKING:
    from opendle.contracts import JsonObject

__all__ = ["SoftPasskey"]


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class SoftPasskey:
    """Act as one discoverable passkey with user verification."""

    def __init__(self, *, origin: str, rp_id: str) -> None:
        """Create a P-256 key and a random credential ID."""
        self._key = ec.generate_private_key(ec.SECP256R1())
        self._origin = origin
        self._rp_id_hash = hashlib.sha256(rp_id.encode("ascii")).digest()
        self._sign_count = 0
        self._user_handle = ""
        self.credential_id: str = _b64url(os.urandom(16))

    def use_identity_of(self, credential_id: str, user_id: str) -> None:
        """Claim the credential ID and the user of a different passkey."""
        self.credential_id = credential_id
        self._user_handle = _b64url(user_id.encode("utf-8"))

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
            "attestationObject": _b64url(
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
                "authenticatorData": _b64url(authenticator_data),
                "signature": _b64url(self._key.sign(signed, ec.ECDSA(hashes.SHA256()))),
                "userHandle": self._user_handle,
            },
        )

    def _client_data(self, ceremony: str, options: JsonObject) -> str:
        return _b64url(
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
