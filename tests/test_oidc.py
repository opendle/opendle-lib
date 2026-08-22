"""Tests for the public OpenID Connect authorization primitives."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from opendle import (
    build_authorization_code_url,
    pkce_s256_challenge,
    validate_canonical_token,
)

_STATE = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
_NONCE = base64.urlsafe_b64encode(bytes(range(1, 33))).rstrip(b"=").decode("ascii")
_VERIFIER = base64.urlsafe_b64encode(bytes(range(2, 34))).rstrip(b"=").decode("ascii")


def test_canonical_token_and_pkce_challenge_use_exact_256_bit_values() -> None:
    """The token and challenge helpers must use canonical unpadded Base64URL."""
    assert validate_canonical_token(_VERIFIER) == _VERIFIER
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(_VERIFIER.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert pkce_s256_challenge(_VERIFIER) == expected


@pytest.mark.parametrize(
    "value",
    [
        "short",
        "A" * 42 + "=",
        "é" * 43,
        "B" * 43,
    ],
)
def test_canonical_token_rejects_invalid_or_noncanonical_values(value: str) -> None:
    """The token helper must reject bad length, alphabet, and byte count."""
    with pytest.raises(ValueError, match="canonical 256-bit"):
        validate_canonical_token(value)


def test_authorization_url_preserves_safe_query_and_supports_recent_login() -> None:
    """The URL builder must preserve safe provider values and add exact controls."""
    result = build_authorization_code_url(
        authorization_endpoint="https://auth.example.test/authorize?tenant=one",
        client_id="client-one",
        redirect_uri="https://service.example.test/oidc/callback",
        state=_STATE,
        nonce=_NONCE,
        code_verifier=_VERIFIER,
        max_age_zero=True,
        prompt_login=True,
    )
    parsed = urlsplit(result)
    query = parse_qs(parsed.query, strict_parsing=True)
    assert (parsed.scheme, parsed.netloc, parsed.path, parsed.fragment) == (
        "https",
        "auth.example.test",
        "/authorize",
        "",
    )
    assert query == {
        "tenant": ["one"],
        "response_type": ["code"],
        "client_id": ["client-one"],
        "redirect_uri": ["https://service.example.test/oidc/callback"],
        "scope": ["openid offline_access"],
        "state": [_STATE],
        "nonce": [_NONCE],
        "code_challenge": [pkce_s256_challenge(_VERIFIER)],
        "code_challenge_method": ["S256"],
        "max_age": ["0"],
        "prompt": ["login"],
    }


def test_authorization_url_supports_loopback_without_optional_controls() -> None:
    """Loopback development can use HTTP and omit recent-login controls."""
    result = build_authorization_code_url(
        authorization_endpoint="http://127.0.0.1:1411/authorize",
        client_id="local-client",
        redirect_uri="http://localhost:8000/oidc/callback",
        state=_STATE,
        nonce=_NONCE,
        code_verifier=_VERIFIER,
    )
    query = parse_qs(urlsplit(result).query)
    assert "max_age" not in query
    assert "prompt" not in query


@pytest.mark.parametrize("parameter", ["state", "prompt", "redirect_uri"])
def test_authorization_url_rejects_reserved_endpoint_parameters(
    parameter: str,
) -> None:
    """The endpoint must not supply a parameter that this request owns."""
    with pytest.raises(ValueError, match="reserved request parameter"):
        build_authorization_code_url(
            authorization_endpoint=(
                f"https://auth.example.test/authorize?{parameter}=attacker"
            ),
            client_id="client-one",
            redirect_uri="https://service.example.test/oidc/callback",
            state=_STATE,
            nonce=_NONCE,
            code_verifier=_VERIFIER,
        )


@pytest.mark.parametrize(
    ("authorization_endpoint", "redirect_uri", "client_id"),
    [
        (
            "http://auth.example.test/authorize",
            "https://service.example.test/oidc/callback",
            "client-one",
        ),
        (
            "https://user@auth.example.test/authorize",
            "https://service.example.test/oidc/callback",
            "client-one",
        ),
        (
            "https://auth.example.test:99999/authorize",
            "https://service.example.test/oidc/callback",
            "client-one",
        ),
        (
            "https://auth.example.test/authorize#fragment",
            "https://service.example.test/oidc/callback",
            "client-one",
        ),
        (
            "https://auth.example.test/authorize",
            "https://service.example.test/callback?query=one",
            "client-one",
        ),
        (
            "https://auth.example.test/authorize",
            " https://service.example.test/callback",
            "client-one",
        ),
        (
            "https://auth.example.test/authorize",
            "https://service.example.test/oidc/callback",
            "client one",
        ),
    ],
)
def test_authorization_url_rejects_unsafe_configuration(
    authorization_endpoint: str,
    redirect_uri: str,
    client_id: str,
) -> None:
    """The URL builder must reject unsafe endpoints, redirects, and clients."""
    with pytest.raises(ValueError, match="invalid"):
        build_authorization_code_url(
            authorization_endpoint=authorization_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=_STATE,
            nonce=_NONCE,
            code_verifier=_VERIFIER,
        )
