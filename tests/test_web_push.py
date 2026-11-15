"""Check subscription validation, key separation and push result handling."""

import base64
from http import HTTPStatus
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from opendle.web_push import PushSubscription, derive_vapid_key, send_push


def _subscription(
    endpoint: str = "https://fcm.googleapis.com/fcm/send/test",
) -> PushSubscription:
    key = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )
    return PushSubscription(
        endpoint,
        base64.urlsafe_b64encode(key).decode(),
        base64.urlsafe_b64encode(b"a" * 16).decode(),
    )


def test_vapid_keys_are_stable_and_separate_origins() -> None:
    """The same secret and origin keep browser subscriptions valid."""
    private, public = derive_vapid_key(b"s" * 32, b"app:https://one.test")
    assert (private, public) == derive_vapid_key(b"s" * 32, b"app:https://one.test")
    assert public != derive_vapid_key(b"s" * 32, b"app:https://two.test")[1]
    loaded = serialization.load_der_private_key(
        base64.urlsafe_b64decode(private + "=="), None
    )
    assert isinstance(loaded, ec.EllipticCurvePrivateKey)
    assert loaded.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    ) == base64.urlsafe_b64decode(public + "==")
    with pytest.raises(ValueError, match="secret"):
        derive_vapid_key(b"short", b"app")
    with pytest.raises(ValueError, match="context"):
        derive_vapid_key(b"s" * 32, b"")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1/push",
        "https://fcm.googleapis.com.evil.test/push",
        "http://fcm.googleapis.com/push",
        "https://user@fcm.googleapis.com/push",
        "https://fcm.googleapis.com:8443/push",
        "https://fcm.googleapis.com/push#fragment",
    ],
)
def test_push_rejects_other_destinations(endpoint: str) -> None:
    """A browser token cannot cause a request to an arbitrary host."""
    with pytest.raises(ValueError, match="endpoint"):
        _subscription(endpoint)


def test_push_rejects_invalid_keys() -> None:
    """Validate the point and authentication secret before any send."""
    subscription = _subscription()
    for key, auth in [
        ("invalid!", subscription.auth),
        ("AA", subscription.auth),
        (subscription.p256dh, "AA"),
    ]:
        with pytest.raises(ValueError, match="key"):
            PushSubscription(subscription.endpoint, key, auth)


def test_push_uses_encrypted_delivery_and_classifies_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass the encryption fields to the maintained Web Push implementation."""
    subscription = _subscription()
    backend = Mock()
    backend.WebPushException = RuntimeError
    backend.webpush.return_value.status_code = 201
    monkeypatch.setattr(
        "opendle.web_push.importlib.import_module", Mock(return_value=backend)
    )
    assert (
        send_push(
            subscription,
            '{"title":"Hello"}',
            private_key="private",
            subject="https://app.test",
        )
        == HTTPStatus.CREATED
    )
    sent = backend.webpush.call_args.kwargs
    assert sent["subscription_info"] == {
        "endpoint": subscription.endpoint,
        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
    }
    assert sent["data"] == '{"title":"Hello"}'
    assert sent["timeout"] > 0
    error = RuntimeError("private token")
    backend.webpush.side_effect = error
    assert (
        send_push(
            subscription, "hello", private_key="private", subject="https://app.test"
        )
        is None
    )
    response = Mock(status_code=410)
    error.response = response  # type: ignore[attr-defined]
    assert (
        send_push(
            subscription, "hello", private_key="private", subject="https://app.test"
        )
        == HTTPStatus.GONE
    )
