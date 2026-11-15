"""Validate browser subscriptions and send encrypted RFC 8291 Web Push.

Install the ``web-push`` extra. Callers own storage, authorization, routing,
notification text and delivery timing. No exception text contains a token.
"""

from __future__ import annotations

import base64
import binascii
import importlib
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

__all__ = ["PushSubscription", "derive_vapid_key", "send_push"]

# Browser push providers. Exact hosts prevent requests to arbitrary services.
_PUSH_HOSTS = frozenset(
    {
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "web.push.apple.com",
    }
)
_ENDPOINT_LIMIT = 4096
_PUBLIC_KEY_BYTES = 65
_UNCOMPRESSED_POINT = 4
_AUTH_BYTES = 16
_SEED_BYTES = 32
_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error) as error:
        message = "The push key is not valid base64url."
        raise ValueError(message) from error


@dataclass(frozen=True, slots=True)
class PushSubscription:
    """Hold a validated endpoint and browser encryption keys."""

    endpoint: str
    p256dh: str
    auth: str

    def __post_init__(self) -> None:
        """Reject unsupported destinations and invalid encryption keys."""
        parts = urlsplit(self.endpoint)
        host = parts.hostname or ""
        allowed = host in _PUSH_HOSTS or (
            host.endswith(".notify.windows.com") and host != ".notify.windows.com"
        )
        if (
            not allowed
            or parts.scheme != "https"
            or parts.port not in (None, 443)
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or not parts.path
            or len(self.endpoint) > _ENDPOINT_LIMIT
        ):
            message = "The browser push endpoint is not supported."
            raise ValueError(message)
        public = _decode(self.p256dh)
        if (
            len(public) != _PUBLIC_KEY_BYTES
            or public[0] != _UNCOMPRESSED_POINT
            or len(_decode(self.auth)) != _AUTH_BYTES
        ):
            message = "The browser push keys are not valid."
            raise ValueError(message)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)


def derive_vapid_key(seed: bytes, context: bytes) -> tuple[str, str]:
    """Derive stable private and public VAPID keys from a secret and context.

    Use a random secret of at least 32 bytes and a context unique to the app
    and origin. Return base64url DER private and uncompressed public keys.
    """
    if len(seed) < _SEED_BYTES or not context:
        message = "VAPID needs a secret of at least 32 bytes and a context."
        raise ValueError(message)
    material = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=context
    ).derive(seed)
    scalar = int.from_bytes(material, "big") % (_P256_ORDER - 1) + 1
    private = ec.derive_private_key(scalar, ec.SECP256R1())
    private_bytes = private.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return (
        base64.urlsafe_b64encode(private_bytes).rstrip(b"=").decode("ascii"),
        base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii"),
    )


class _Response(Protocol):
    status_code: int


class _Backend(Protocol):
    WebPushException: type[Exception]

    def webpush(self, **kwargs: object) -> _Response: ...


def send_push(
    subscription: PushSubscription,
    payload: str,
    *,
    private_key: str,
    subject: str,
) -> int | None:
    """Send encrypted text with VAPID. Return the HTTP status or no response.

    Use a HTTPS origin or a mailto URL as subject. Delete a subscription on
    404 or 410. Other delivery decisions belong to the caller. The send has
    a ten-second timeout and a one-day TTL.
    """
    backend = cast("_Backend", importlib.import_module("pywebpush"))
    try:
        response = backend.webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth": subscription.auth,
                },
            },
            data=payload,
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
            ttl=86400,
            timeout=10,
        )
    except (backend.WebPushException, OSError) as error:
        status: object = getattr(getattr(error, "response", None), "status_code", None)
        return status if isinstance(status, int) else None
    return response.status_code
