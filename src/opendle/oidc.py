"""Framework-neutral OpenID Connect authorization request primitives."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = [
    "build_authorization_code_url",
    "pkce_s256_challenge",
    "validate_canonical_token",
]

_BASE64URL_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
_TOKEN_CHARACTERS = 43
_TOKEN_BYTES = 32
_MAXIMUM_CLIENT_ID_CHARACTERS = 2_000
_MAXIMUM_URL_CHARACTERS = 4_096
_MAXIMUM_PORT = 65_535
_AUTHORIZATION_PARAMETERS = frozenset(
    {
        "client_id",
        "code_challenge",
        "code_challenge_method",
        "max_age",
        "nonce",
        "prompt",
        "redirect_uri",
        "response_type",
        "scope",
        "state",
    }
)


def validate_canonical_token(value: str) -> str:
    """Validate and return one unpadded 256-bit Base64URL token.

    Args:
        value: The token to validate.

    Returns:
        The unchanged canonical token.

    Raises:
        ValueError: If the value is not the canonical encoding of 32 bytes.

    """
    if len(value) != _TOKEN_CHARACTERS or not set(value) <= _BASE64URL_ALPHABET:
        msg = "The token must be a canonical 256-bit Base64URL value."
        raise ValueError(msg)
    decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if len(decoded) != _TOKEN_BYTES or canonical != value:
        msg = "The token must be a canonical 256-bit Base64URL value."
        raise ValueError(msg)
    return value


def pkce_s256_challenge(code_verifier: str) -> str:
    """Build the RFC 7636 S256 challenge for one canonical verifier.

    Args:
        code_verifier: One canonical 256-bit Base64URL verifier.

    Returns:
        The unpadded Base64URL encoding of the verifier SHA-256 digest.

    Raises:
        ValueError: If the verifier is not a canonical 256-bit token.

    """
    validate_canonical_token(code_verifier)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorization_code_url(  # noqa: PLR0913 - The OIDC request has fixed fields.
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_verifier: str,
    max_age_zero: bool = False,
    prompt_login: bool = False,
    include_offline_access: bool = True,
) -> str:
    """Build an exact OIDC authorization-code URL with S256 PKCE.

    The endpoint can contain unrelated query parameters. It cannot contain a
    parameter that this function owns. HTTPS is required except for an HTTP
    loopback endpoint or redirect URI.

    Args:
        authorization_endpoint: The exact provider authorization endpoint.
        client_id: The confidential OpenID Connect client ID.
        redirect_uri: The exact callback URI without a query or fragment.
        state: One canonical 256-bit state token.
        nonce: One canonical 256-bit nonce.
        code_verifier: One canonical 256-bit PKCE verifier.
        max_age_zero: Add ``max_age=0`` when true.
        prompt_login: Add ``prompt=login`` when true.
        include_offline_access: Request refresh-token access when true.

    Returns:
        The complete authorization URL.

    Raises:
        ValueError: If a value is invalid or the endpoint has a reserved
            request parameter.

    """
    endpoint = _authorization_endpoint(authorization_endpoint)
    _redirect_uri(redirect_uri)
    if (
        not 1 <= len(client_id) <= _MAXIMUM_CLIENT_ID_CHARACTERS
        or client_id.strip() != client_id
        or any(character.isspace() for character in client_id)
    ):
        msg = "The OpenID Connect client ID is invalid."
        raise ValueError(msg)
    validate_canonical_token(state)
    validate_canonical_token(nonce)
    challenge = pkce_s256_challenge(code_verifier)
    parameters: list[tuple[str, str]] = [
        ("response_type", "code"),
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("scope", "openid offline_access" if include_offline_access else "openid"),
        ("state", state),
        ("nonce", nonce),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
    ]
    if max_age_zero:
        parameters.append(("max_age", "0"))
    if prompt_login:
        parameters.append(("prompt", "login"))
    query = urlencode([*parse_qsl(endpoint.query, keep_blank_values=True), *parameters])
    return urlunsplit((endpoint.scheme, endpoint.netloc, endpoint.path, query, ""))


def _authorization_endpoint(value: str) -> SplitResult:
    parsed = _absolute_oidc_url(value, "authorization endpoint", allow_query=True)
    if any(
        name in _AUTHORIZATION_PARAMETERS
        for name, _value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        msg = "The authorization endpoint has a reserved request parameter."
        raise ValueError(msg)
    return parsed


def _redirect_uri(value: str) -> None:
    _absolute_oidc_url(value, "redirect URI", allow_query=False)


def _absolute_oidc_url(value: str, name: str, *, allow_query: bool) -> SplitResult:
    if not 1 <= len(value) <= _MAXIMUM_URL_CHARACTERS or value.strip() != value:
        msg = f"The OpenID Connect {name} is invalid."
        raise ValueError(msg)
    parsed = urlsplit(value)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    try:
        port = parsed.port
    except ValueError:
        msg = f"The OpenID Connect {name} is invalid."
        raise ValueError(msg) from None
    if (
        parsed.scheme not in ({"http", "https"} if loopback else {"https"})
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (parsed.query and not allow_query)
        or any(character.isspace() for character in value)
        or (port is not None and not 1 <= port <= _MAXIMUM_PORT)
    ):
        msg = f"The OpenID Connect {name} is invalid."
        raise ValueError(msg)
    return parsed
