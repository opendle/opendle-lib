"""Tests for the public OpenID Connect authorization primitives."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import Message
from typing import TYPE_CHECKING, Self, cast
from urllib.parse import parse_qs, urlsplit

import pytest

from opendle import (
    OidcClient,
    OidcClientAuthenticationMethod,
    OidcHTTPError,
    OidcMetadata,
    OidcProtocolError,
    OidcResponseLimitError,
    OidcSigningKeys,
    OidcTransportError,
    OidcTransportResponse,
    build_authorization_code_url,
    pkce_s256_challenge,
    validate_canonical_token,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from opendle import JsonObject
    from opendle._internal.http import UrllibHttpClient

_STATE = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
_NONCE = base64.urlsafe_b64encode(bytes(range(1, 33))).rstrip(b"=").decode("ascii")
_VERIFIER = base64.urlsafe_b64encode(bytes(range(2, 34))).rstrip(b"=").decode("ascii")
_ISSUER = "https://identity.example.test"
_CLIENT_ID = "client:one"
_CLIENT_SECRET = "test+client-secret"  # noqa: S105 - Public test fixture.
_NOW = datetime(2026, 8, 25, 10, tzinfo=UTC)
_RSA_EXPONENT = 65_537
_RSA_MODULUS = int(
    "2318723235032714595342117179370633889289530079893687331652820099874007"
    "4189918815705054112433417906063866551337492159771799641174411341890873"
    "9741150260728650805917625801641989124754256112835099654905068201366174"
    "6421305342102084157459883804405251586928220578800091195484981305313395"
    "4214935622775559445448377141562477512753541254734653056417421149111257"
    "0050978871124639346400585382979975194852902575835336528515432249870271"
    "2474360417690566933297957403060060600920767540814879928955634614289643"
    "7929386625793009966352109717003410005995673179918815968637166815897057"
    "475447910256457736532241713494352591525918799193106362827"
)
_RSA_PRIVATE_EXPONENT = int(
    "2037909247263139305914307178109289592490912501363754677559278541171289"
    "9176637987466791535715166568339696863711179156855755669799442960356963"
    "8663506950546565854721077348285374270806493310638291959084668636017694"
    "7279661682791095830899939074625669338039054356148210214992064378687635"
    "6299194224207275646700741921913478036920545192035912813141697878024312"
    "1851605117753336354218327110465077131382641715856718009024237059387016"
    "5504460495942077402861866944877049384532804966020347560298363490399522"
    "4333039206362197341553747266998230856714657014625114255904102693958322"
    "5067401080694199454712994192114800650845102467227190273"
)


@dataclass(frozen=True, slots=True)
class OidcCall:
    """Record one OIDC transport call."""

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    timeout: float


class QueueOidcTransport:
    """Return queued OIDC responses and record requests."""

    def __init__(self, responses: list[object]) -> None:
        """Store the queued responses."""
        self.responses = list(responses)
        self.calls: list[OidcCall] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OidcTransportResponse:
        """Record one request and return or raise the next item."""
        self.calls.append(OidcCall(method, url, dict(headers), body, timeout))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return cast("OidcTransportResponse", value)


def oidc_response(
    value: object,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> OidcTransportResponse:
    """Build one strict JSON provider response."""
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    return OidcTransportResponse(
        status,
        {"Content-Type": "application/json", **(headers or {})},
        body,
    )


def discovery_document(
    method: str = "client_secret_basic",
) -> JsonObject:
    """Return one complete accepted discovery document."""
    return {
        "issuer": _ISSUER,
        "authorization_endpoint": f"{_ISSUER}/authorize",
        "token_endpoint": f"{_ISSUER}/token",
        "jwks_uri": f"{_ISSUER}/jwks",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": [method],
        "scopes_supported": ["openid"],
    }


def client(
    transport: QueueOidcTransport,
    *,
    method: OidcClientAuthenticationMethod = (
        OidcClientAuthenticationMethod.CLIENT_SECRET_BASIC
    ),
    clock: Callable[[], datetime] | None = None,
    maximum: int = 1_048_576,
    client_secret: str = _CLIENT_SECRET,
) -> OidcClient:
    """Build one exact test OIDC client."""
    return OidcClient(
        issuer=_ISSUER,
        client_id=_CLIENT_ID,
        client_secret=client_secret,
        redirect_uri="https://service.example.test/callback",
        token_authentication_method=method,
        transport=transport,
        clock=clock or (lambda: _NOW),
        maximum_document_bytes=maximum,
    )


def trusted_metadata(  # noqa: PLR0913 - Test factory exposes metadata fields.
    subject: OidcClient,
    *,
    issuer: str = _ISSUER,
    authorization_endpoint: str | None = None,
    token_endpoint: str | None = None,
    jwks_uri: str | None = None,
    method: OidcClientAuthenticationMethod = (
        OidcClientAuthenticationMethod.CLIENT_SECRET_BASIC
    ),
) -> OidcMetadata:
    """Build one test metadata value that belongs to the supplied client."""
    value = OidcMetadata(
        issuer,
        authorization_endpoint or f"{_ISSUER}/authorize",
        token_endpoint or f"{_ISSUER}/token",
        jwks_uri or f"{_ISSUER}/jwks",
        method,
    )
    object.__setattr__(
        value, "_client_binding", object.__getattribute__(subject, "_binding")
    )
    return value


def rsa_jwk(**updates: object) -> JsonObject:
    """Return one public test RSA signing key."""
    value: JsonObject = {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "key_ops": ["verify"],
        "kid": "current-key",
        "n": _integer_b64(_RSA_MODULUS),
        "e": _integer_b64(_RSA_EXPONENT),
    }
    value.update(cast("JsonObject", updates))
    return value


def signed_token(
    *,
    header: JsonObject | None = None,
    claims: JsonObject | None = None,
    header_bytes: bytes | None = None,
    claims_bytes: bytes | None = None,
    signature: bytes | None = None,
) -> str:
    """Create one deterministic RS256 test token without a crypto dependency."""
    token_header: JsonObject = {"alg": "RS256", "kid": "current-key", "typ": "JWT"}
    token_claims: JsonObject = {
        "iss": _ISSUER,
        "sub": "allowed-subject",
        "aud": _CLIENT_ID,
        "iat": int(_NOW.timestamp()),
        "exp": int((_NOW + timedelta(minutes=5)).timestamp()),
        "nonce": _NONCE,
    }
    token_header.update(header or {})
    token_claims.update(claims or {})
    encoded_header = _b64(
        header_bytes
        if header_bytes is not None
        else json.dumps(token_header, separators=(",", ":")).encode()
    )
    encoded_claims = _b64(
        claims_bytes
        if claims_bytes is not None
        else json.dumps(token_claims, separators=(",", ":")).encode()
    )
    signed = f"{encoded_header}.{encoded_claims}".encode()
    token_signature = signature if signature is not None else _rsa_sign(signed)
    return f"{encoded_header}.{encoded_claims}.{_b64(token_signature)}"


def _rsa_sign(value: bytes) -> bytes:
    digest = (
        bytes.fromhex("3031300d060960864801650304020105000420")
        + hashlib.sha256(value).digest()
    )
    encoded = b"\x00\x01" + b"\xff" * (256 - len(digest) - 3) + b"\x00" + digest
    signature = pow(int.from_bytes(encoded, "big"), _RSA_PRIVATE_EXPONENT, _RSA_MODULUS)
    return signature.to_bytes(256, "big")


def _integer_b64(value: int) -> str:
    return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


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


def test_authorization_url_can_omit_refresh_token_access() -> None:
    """A caller that does not use refresh tokens can request only OpenID."""
    result = build_authorization_code_url(
        authorization_endpoint="https://auth.example.test/authorize",
        client_id="client-one",
        redirect_uri="https://service.example.test/oidc/callback",
        state=_STATE,
        nonce=_NONCE,
        code_verifier=_VERIFIER,
        include_offline_access=False,
    )
    assert parse_qs(urlsplit(result).query)["scope"] == ["openid"]


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


def test_oidc_client_completes_basic_discovery_exchange_and_verification() -> None:
    """Use strict discovery, Basic client authentication, JWKS, and RS256."""
    transport = QueueOidcTransport(
        [
            oidc_response(discovery_document()),
            oidc_response(
                {
                    "id_token": signed_token(
                        claims={"profile": {"groups": ["operators"]}}
                    ),
                    "token_type": "Bearer",
                }
            ),
            oidc_response(
                {"keys": [rsa_jwk()]},
                headers={"Content-Type": "application/jwk-set+json"},
            ),
        ]
    )
    subject = client(transport)
    metadata = subject.discover()
    assert metadata == OidcMetadata(
        _ISSUER,
        f"{_ISSUER}/authorize",
        f"{_ISSUER}/token",
        f"{_ISSUER}/jwks",
        OidcClientAuthenticationMethod.CLIENT_SECRET_BASIC,
    )
    assert "client_secret=<protected>" in repr(subject)
    assert _CLIENT_SECRET not in repr(subject)
    url = subject.authorization_url(
        metadata, state=_STATE, nonce=_NONCE, code_verifier=_VERIFIER
    )
    assert parse_qs(urlsplit(url).query)["scope"] == ["openid"]
    token = subject.exchange_code(metadata, code="one-code", code_verifier=_VERIFIER)
    verified = subject.verify_id_token(metadata, id_token=token, expected_nonce=_NONCE)
    assert (verified.issuer, verified.subject, verified.audiences) == (
        _ISSUER,
        "allowed-subject",
        (_CLIENT_ID,),
    )
    assert verified.issued_at == _NOW
    assert verified.expires_at == _NOW + timedelta(minutes=5)
    with pytest.raises(TypeError):
        cast("dict[str, object]", verified.claims)["sub"] = "changed"
    profile = cast("Mapping[str, object]", verified.claims["profile"])
    assert profile["groups"] == ("operators",)
    with pytest.raises(TypeError):
        cast("dict[str, object]", profile)["groups"] = ()
    authorization = transport.calls[1].headers["Authorization"]
    encoded_credentials = authorization.removeprefix("Basic ")
    assert base64.b64decode(encoded_credentials).decode() == (
        "client%3Aone:test%2Bclient-secret"
    )
    assert _CLIENT_SECRET.encode() not in (transport.calls[1].body or b"")


def test_oidc_client_supports_post_authentication_and_discovery_defaults() -> None:
    """Send credentials in the form and accept standard Basic discovery defaults."""
    post_transport = QueueOidcTransport([oidc_response({"id_token": signed_token()})])
    post_client = client(
        post_transport,
        method=OidcClientAuthenticationMethod.CLIENT_SECRET_POST,
    )
    metadata = trusted_metadata(
        post_client,
        method=OidcClientAuthenticationMethod.CLIENT_SECRET_POST,
    )
    post_client.exchange_code(metadata, code="code", code_verifier=_VERIFIER)
    form = parse_qs((post_transport.calls[0].body or b"").decode())
    assert form["client_id"] == [_CLIENT_ID]
    assert form["client_secret"] == [_CLIENT_SECRET]
    assert "Authorization" not in post_transport.calls[0].headers

    document = discovery_document()
    for key in (
        "grant_types_supported",
        "token_endpoint_auth_methods_supported",
        "scopes_supported",
    ):
        document.pop(key)
    default_client = client(QueueOidcTransport([oidc_response(document)]))
    assert default_client.discover().issuer == _ISSUER


def test_oidc_client_checks_signing_keys_before_a_one_use_exchange() -> None:
    """Check key availability without sending an authorization code."""
    transport = QueueOidcTransport(
        [
            oidc_response(
                {"keys": [rsa_jwk()]},
                headers={"Content-Type": "application/jwk-set+json"},
            )
        ]
    )
    subject = client(transport)
    metadata = trusted_metadata(subject)

    signing_keys = subject.ensure_signing_keys_available(metadata)

    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == f"{_ISSUER}/jwks"
    assert signing_keys.issuer == _ISSUER
    assert signing_keys.jwks_uri == f"{_ISSUER}/jwks"
    with pytest.raises(TypeError):
        cast("dict[str, object]", signing_keys.keys[0])["kid"] = "changed"
    assert signing_keys.keys[0]["key_ops"] == ("verify",)

    verified = subject.verify_id_token(
        metadata,
        id_token=signed_token(),
        expected_nonce=_NONCE,
        signing_keys=signing_keys,
    )
    assert verified.subject == "allowed-subject"
    assert len(transport.calls) == 1


def test_oidc_client_rejects_caller_created_metadata_and_key_snapshots() -> None:
    """Do not send secrets or verify tokens with caller-created authority data."""
    transport = QueueOidcTransport([])
    subject = client(transport)
    forged_metadata = OidcMetadata(
        _ISSUER,
        f"{_ISSUER}/authorize",
        "https://attacker.example.test/token",
        f"{_ISSUER}/jwks",
        OidcClientAuthenticationMethod.CLIENT_SECRET_BASIC,
    )
    with pytest.raises(ValueError, match="does not belong"):
        subject.exchange_code(forged_metadata, code="code", code_verifier=_VERIFIER)
    assert transport.calls == []

    metadata = trusted_metadata(subject)
    forged_keys = OidcSigningKeys(
        issuer=_ISSUER,
        jwks_uri=f"{_ISSUER}/jwks",
        keys=(rsa_jwk(),),
    )
    with pytest.raises(ValueError, match="do not belong"):
        subject.verify_id_token(
            metadata,
            id_token=signed_token(),
            expected_nonce=_NONCE,
            signing_keys=forged_keys,
        )


def test_oidc_client_rejects_key_sets_without_a_usable_signing_key() -> None:
    """Reject a bounded key set when it cannot verify an RS256 token."""
    subject = client(
        QueueOidcTransport(
            [oidc_response({"keys": [rsa_jwk(n="bad="), rsa_jwk(use="enc")]})]
        )
    )
    metadata = trusted_metadata(subject)

    with pytest.raises(OidcProtocolError, match="no usable signing key"):
        subject.ensure_signing_keys_available(metadata)


def test_oidc_client_rejects_a_signing_snapshot_from_other_metadata() -> None:
    """Do not use a key snapshot that belongs to another metadata source."""
    subject = client(QueueOidcTransport([oidc_response({"keys": [rsa_jwk()]})]))
    metadata = trusted_metadata(subject)
    signing_keys = subject.fetch_signing_keys(metadata)
    other_metadata = trusted_metadata(subject, jwks_uri=f"{_ISSUER}/other-jwks")

    with pytest.raises(ValueError, match="do not belong"):
        subject.verify_id_token(
            other_metadata,
            id_token=signed_token(),
            expected_nonce=_NONCE,
            signing_keys=signing_keys,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("issuer", "https://other.example.test"),
        ("authorization_endpoint", None),
        ("token_endpoint", "http://provider.example.test/token"),
        ("jwks_uri", "relative"),
        ("response_types_supported", ["token"]),
        ("grant_types_supported", ["implicit"]),
        ("code_challenge_methods_supported", ["plain"]),
        ("id_token_signing_alg_values_supported", ["none"]),
        ("scopes_supported", ["profile"]),
        ("token_endpoint_auth_methods_supported", ["client_secret_post"]),
        ("response_types_supported", []),
        ("response_types_supported", ["code", "code"]),
        ("response_types_supported", [1]),
        ("response_types_supported", ["x" * 501]),
    ],
)
def test_discovery_rejects_wrong_endpoints_and_capabilities(
    field: str, value: object
) -> None:
    """Reject a discovery value that weakens or changes the exact client."""
    document = discovery_document()
    document[field] = value  # type: ignore[assignment]
    with pytest.raises(OidcProtocolError):
        client(QueueOidcTransport([oidc_response(document)])).discover()


@pytest.mark.parametrize(
    "values",
    [
        {"issuer": "https://identity.example.test/path?tenant=one"},
        {"client_id": "bad client"},
        {"client_secret": ""},
        {"redirect_uri": "http://service.example.test/callback"},
        {"timeout": 0},
        {"timeout": True},
        {"maximum_document_bytes": 0},
        {"maximum_document_bytes": True},
    ],
)
def test_oidc_client_rejects_invalid_configuration(values: dict[str, object]) -> None:
    """Reject unsafe issuer, client, redirect, timeout, and document settings."""
    arguments: dict[str, object] = {
        "issuer": _ISSUER,
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "redirect_uri": "https://service.example.test/callback",
    }
    arguments.update(values)
    with pytest.raises(ValueError, match=r"invalid|positive|finite"):
        OidcClient(**arguments)  # type: ignore[arg-type]


def test_oidc_client_rejects_wrong_authentication_method_type() -> None:
    """Use a type error for an invalid authentication method type."""
    with pytest.raises(TypeError):
        OidcClient(
            issuer=_ISSUER,
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
            redirect_uri="https://service.example.test/callback",
            token_authentication_method=cast(
                "OidcClientAuthenticationMethod", "client_secret_basic"
            ),
        )


@pytest.mark.parametrize(
    "response",
    [
        cast("OidcTransportResponse", object()),
        OidcTransportResponse(
            cast("int", True),  # noqa: FBT003 - Invalid protocol fixture.
            {},
            b"{}",
        ),
        OidcTransportResponse(99, {}, b"{}"),
        OidcTransportResponse(200, {}, cast("bytes", bytearray())),
        OidcTransportResponse(200, cast("Mapping[str, str]", []), b"{}"),
        OidcTransportResponse(200, cast("Mapping[str, str]", {1: "x"}), b"{}"),
        OidcTransportResponse(200, {"Bad\nName": "x"}, b"{}"),
        OidcTransportResponse(200, {"X-Test": "bad\rvalue"}, b"{}"),
        OidcTransportResponse(
            200,
            {"Content-Type": "application/json", "content-type": "application/json"},
            b"{}",
        ),
        OidcTransportResponse(200, {f"X-{index}": "x" for index in range(101)}, b"{}"),
        OidcTransportResponse(200, {"X-Large": "x" * 65_537}, b"{}"),
    ],
)
def test_oidc_transport_response_shape_and_headers_are_strict(
    response: OidcTransportResponse,
) -> None:
    """Reject invalid transport response values and bounded headers."""
    with pytest.raises((OidcProtocolError, OidcResponseLimitError)):
        client(QueueOidcTransport([response])).discover()


@pytest.mark.parametrize(
    "response",
    [
        OidcTransportResponse(200, {"Content-Type": "text/plain"}, b"{}"),
        OidcTransportResponse(
            200, {"Content-Type": "application/json", "Content-Length": "bad"}, b"{}"
        ),
        OidcTransportResponse(
            200, {"Content-Type": "application/json", "Content-Length": "1"}, b"{}"
        ),
        oidc_response(b"{"),
        oidc_response(b'{"a":1,"a":2}'),
        oidc_response(b'{"a":NaN}'),
        oidc_response(b'"array"'),
        oidc_response(b'{"a":"\xed\xa0\x80"}'),
    ],
)
def test_oidc_response_requires_exact_bounded_strict_json(
    response: OidcTransportResponse,
) -> None:
    """Reject media, length, syntax, duplicate, number, shape, and Unicode errors."""
    with pytest.raises(OidcProtocolError):
        client(QueueOidcTransport([response])).discover()


def test_oidc_response_limits_and_errors_are_secret_safe() -> None:
    """Keep provider failures bounded and client-created errors secret-safe."""
    with pytest.raises(OidcTransportError, match="request failed") as transport_error:
        client(QueueOidcTransport([RuntimeError(_CLIENT_SECRET)])).discover()
    assert _CLIENT_SECRET not in str(transport_error.value)
    with pytest.raises(OidcProtocolError, match="direct"):
        client(QueueOidcTransport([OidcProtocolError("direct")])).discover()
    with pytest.raises(OidcHTTPError) as http_error:
        client(QueueOidcTransport([oidc_response({}, status=503)])).discover()
    assert http_error.value.status == 503  # noqa: PLR2004 - Exact fixture status.
    with pytest.raises(OidcResponseLimitError):
        client(QueueOidcTransport([oidc_response(b"1234")]), maximum=3).discover()


def test_oidc_response_does_not_reject_text_that_matches_a_short_secret() -> None:
    """Accept valid provider JSON when common text contains a short secret."""
    subject = client(
        QueueOidcTransport([oidc_response(discovery_document())]),
        client_secret="e",  # noqa: S106 - Public short-secret regression fixture.
    )

    assert subject.discover().issuer == _ISSUER


def test_exchange_rejects_wrong_inputs_metadata_and_token_payloads() -> None:
    """Reject foreign metadata, invalid controls, and malformed token responses."""
    subject = client(QueueOidcTransport([]))
    valid = trusted_metadata(subject)
    wrong_values = (
        cast("OidcMetadata", object()),
        trusted_metadata(
            subject,
            issuer="https://other.test",
        ),
        trusted_metadata(
            subject,
            token_endpoint="http://other.test/token",  # noqa: S106
        ),
        trusted_metadata(
            subject,
            method=OidcClientAuthenticationMethod.CLIENT_SECRET_POST,
        ),
    )
    for metadata in wrong_values:
        with pytest.raises(ValueError, match="metadata"):
            subject.authorization_url(
                metadata, state=_STATE, nonce=_NONCE, code_verifier=_VERIFIER
            )
    with pytest.raises(ValueError, match="authorization code"):
        subject.exchange_code(valid, code="bad code", code_verifier=_VERIFIER)
    with pytest.raises(ValueError, match="canonical"):
        subject.exchange_code(valid, code="code", code_verifier="bad")
    for payload in (
        {},
        {"id_token": ""},
        {"id_token": "x" * 32_769},
        {"id_token": "token", "token_type": "MAC"},
        {"id_token": "token", "token_type": 1},
    ):
        current = client(QueueOidcTransport([oidc_response(payload)]))
        with pytest.raises(OidcProtocolError):
            current.exchange_code(
                trusted_metadata(current), code="code", code_verifier=_VERIFIER
            )


@pytest.mark.parametrize(
    "token",
    [
        "",
        "one.two",
        "x" * 32_769,
        "=.eA.eA",
        signed_token(header_bytes=b"{"),
        signed_token(header_bytes=b'{"alg":"RS256","alg":"RS256"}'),
        signed_token(header={"alg": "HS256"}),
        signed_token(header={"kid": ""}),
        signed_token(header={"kid": "bad key"}),
        signed_token(header={"typ": "OTHER"}),
        signed_token(header={"crit": ["unsupported"]}),
        signed_token(signature=b"short"),
        signed_token(signature=(1).to_bytes(256, "big")),
        signed_token(signature=_RSA_MODULUS.to_bytes(256, "big")),
    ],
)
def test_id_token_rejects_invalid_encoding_header_and_signature(token: str) -> None:
    """Reject invalid compact JWT encoding, protected headers, and signatures."""
    subject = client(QueueOidcTransport([oidc_response({"keys": [rsa_jwk()]})]))
    with pytest.raises(OidcProtocolError):
        subject.verify_id_token(
            trusted_metadata(subject),
            id_token=token,
            expected_nonce=_NONCE,
        )


def test_id_token_rejects_large_or_non_object_json_and_base64_edges() -> None:
    """Reject large JWT objects and decoded Base64URL boundary failures."""
    large_header = {f"x{index}": index for index in range(101)}
    invalid_tokens = (
        signed_token(header_bytes=json.dumps(large_header).encode()),
        signed_token(claims_bytes=b"[]"),
        "A.e30.A",
        "AB.e30.AA",
        signed_token().rsplit(".", 1)[0] + "." + "A" * 2048,
    )
    for token in invalid_tokens:
        subject = client(QueueOidcTransport([oidc_response({"keys": [rsa_jwk()]})]))
        with pytest.raises(OidcProtocolError):
            subject.verify_id_token(
                trusted_metadata(subject), id_token=token, expected_nonce=_NONCE
            )


@pytest.mark.parametrize(
    "keys",
    [
        None,
        [],
        ["bad"],
        [rsa_jwk()] * 101,
        [rsa_jwk(kid="other")],
        [rsa_jwk(), rsa_jwk()],
        [rsa_jwk(kty="EC")],
        [rsa_jwk(use="enc")],
        [rsa_jwk(alg="PS256")],
        [rsa_jwk(key_ops=["sign"])],
        [rsa_jwk(n=None)],
        [rsa_jwk(n="bad=")],
        [rsa_jwk(n=_integer_b64(3))],
        [rsa_jwk(e=_integer_b64(3))],
        [rsa_jwk(e=_integer_b64(65_538))],
    ],
)
def test_jwks_rejects_invalid_sets_and_rsa_keys(keys: object) -> None:
    """Reject invalid JWKS shape, selection, modulus, and exponent values."""
    subject = client(QueueOidcTransport([oidc_response({"keys": keys})]))
    with pytest.raises(OidcProtocolError):
        subject.verify_id_token(
            trusted_metadata(subject),
            id_token=signed_token(),
            expected_nonce=_NONCE,
        )


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": None},
        {"sub": ""},
        {"sub": "bad subject"},
        {"iss": "https://other.test"},
        {"aud": "other"},
        {"aud": []},
        {"aud": [_CLIENT_ID, _CLIENT_ID]},
        {"aud": [_CLIENT_ID, "other"]},
        {"aud": [_CLIENT_ID, "other"], "azp": "other"},
        {"aud": _CLIENT_ID, "azp": "other"},
        {"nonce": "other"},
        {"iat": None},
        {"iat": int((_NOW + timedelta(seconds=61)).timestamp())},
        {"iat": int((_NOW + timedelta(minutes=6)).timestamp())},
        {"iat": -253_402_300_799},
        {"exp": int(_NOW.timestamp())},
        {"exp": float("inf")},
        {"exp": 253_402_300_800.5},
        {"nbf": "bad"},
        {"nbf": int((_NOW + timedelta(seconds=1)).timestamp())},
    ],
)
def test_id_token_rejects_invalid_authority_audience_nonce_and_time(
    claims: JsonObject,
) -> None:
    """Reject invalid required authority and time claims."""
    subject = client(QueueOidcTransport([oidc_response({"keys": [rsa_jwk()]})]))
    with pytest.raises(OidcProtocolError):
        subject.verify_id_token(
            trusted_metadata(subject),
            id_token=signed_token(claims=claims),
            expected_nonce=_NONCE,
        )


def test_id_token_accepts_multiple_audiences_with_exact_authorized_party() -> None:
    """Accept bounded audiences only when the authorized party identifies the client."""
    claims: JsonObject = {"aud": [_CLIENT_ID, "other"], "azp": _CLIENT_ID}
    key = rsa_jwk()
    for name in ("use", "alg", "key_ops"):
        key.pop(name)
    subject = client(QueueOidcTransport([oidc_response({"keys": [key]})]))
    verified = subject.verify_id_token(
        trusted_metadata(subject),
        id_token=signed_token(claims=claims),
        expected_nonce=_NONCE,
    )
    assert verified.audiences == (_CLIENT_ID, "other")


def test_id_token_accepts_a_safe_noncritical_header_extension() -> None:
    """Ignore a protected header extension that does not claim critical handling."""
    subject = client(QueueOidcTransport([oidc_response({"keys": [rsa_jwk()]})]))

    verified = subject.verify_id_token(
        trusted_metadata(subject),
        id_token=signed_token(header={"x5t": "test-thumbprint"}),
        expected_nonce=_NONCE,
    )

    assert verified.subject == "allowed-subject"


def test_oidc_discovery_supports_an_issuer_path() -> None:
    """Use the standard discovery suffix after an exact issuer path."""
    issuer = f"{_ISSUER}/tenant"
    document = discovery_document()
    document.update(
        {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/jwks",
        }
    )
    transport = QueueOidcTransport([oidc_response(document)])
    subject = OidcClient(
        issuer=issuer,
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        redirect_uri="https://service.example.test/callback",
        transport=transport,
    )

    assert subject.discover().issuer == issuer
    assert transport.calls[0].url == (f"{issuer}/.well-known/openid-configuration")


def test_id_token_rejects_bad_nonce_clock_and_missing_required_claim() -> None:
    """Reject local control errors and a token without all required claims."""
    subject = client(QueueOidcTransport([]))
    with pytest.raises(ValueError, match="canonical"):
        subject.verify_id_token(
            trusted_metadata(subject), id_token=signed_token(), expected_nonce="bad"
        )

    def naive_clock() -> datetime:
        return datetime(2026, 8, 25, 10)  # noqa: DTZ001 - Invalid clock fixture.

    naive = client(
        QueueOidcTransport([oidc_response({"keys": [rsa_jwk()]})]),
        clock=naive_clock,
    )
    with pytest.raises(OidcProtocolError, match="time zone"):
        naive.verify_id_token(
            trusted_metadata(naive), id_token=signed_token(), expected_nonce=_NONCE
        )
    claims = cast("JsonObject", {"iss": _ISSUER})
    missing = signed_token(claims_bytes=json.dumps(claims).encode())
    current = client(QueueOidcTransport([oidc_response({"keys": [rsa_jwk()]})]))
    with pytest.raises(OidcProtocolError, match="missing claims"):
        current.verify_id_token(
            trusted_metadata(current), id_token=missing, expected_nonce=_NONCE
        )


class DefaultOidcResponse:
    """Act as one standard-library OIDC discovery response."""

    status = 200

    def __init__(self, header_items: tuple[tuple[str, str], ...] | None = None) -> None:
        """Create strict JSON response headers and content."""
        self.headers = Message()
        for name, value in header_items or (("Content-Type", "application/json"),):
            self.headers[name] = value
        self.body = json.dumps(discovery_document()).encode()

    def __enter__(self) -> Self:
        """Enter the response context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Exit the response context."""

    def read(self, amount: int | None = None) -> bytes:
        """Return the bounded response content."""
        return self.body if amount is None else self.body[:amount]


class DefaultOidcOpener:
    """Return one default OIDC response."""

    def __init__(self, header_items: tuple[tuple[str, str], ...] | None = None) -> None:
        """Store optional response header fixtures."""
        self._header_items = header_items

    def open(self, *_args: object, **_kwargs: object) -> DefaultOidcResponse:
        """Open one local test response."""
        return DefaultOidcResponse(self._header_items)


def test_default_oidc_transport_uses_the_shared_bounded_http_client() -> None:
    """Use the shared no-redirect standard-library transport by default."""
    subject = OidcClient(
        issuer=_ISSUER,
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        redirect_uri="https://service.example.test/callback",
    )
    transport = object.__getattribute__(subject, "_transport")
    http = cast("UrllibHttpClient", object.__getattribute__(transport, "_http"))
    http.replace_opener(DefaultOidcOpener())
    assert subject.discover().issuer == _ISSUER


def test_default_oidc_transport_maps_shared_header_errors() -> None:
    """Map default transport header bounds to public OIDC errors."""
    subject = OidcClient(
        issuer=_ISSUER,
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        redirect_uri="https://service.example.test/callback",
    )
    transport = object.__getattribute__(subject, "_transport")
    http = cast("UrllibHttpClient", object.__getattribute__(transport, "_http"))
    cases = (
        (
            tuple((f"X-{index}", "x") for index in range(101)),
            OidcResponseLimitError,
        ),
        (
            (
                ("Content-Type", "application/json"),
                ("content-type", "application/json"),
            ),
            OidcProtocolError,
        ),
    )
    for headers, error_type in cases:
        http.replace_opener(DefaultOidcOpener(headers))
        with pytest.raises(error_type):
            subject.discover()


def test_discovery_and_metadata_reject_loopback_http_for_an_https_issuer() -> None:
    """Do not downgrade a discovered or supplied endpoint to loopback HTTP."""
    document = discovery_document()
    document["token_endpoint"] = "http://127.0.0.1:8000/token"  # noqa: S105 - Public endpoint fixture.
    with pytest.raises(OidcProtocolError, match="transport"):
        client(QueueOidcTransport([oidc_response(document)])).discover()
    subject = client(QueueOidcTransport([]))
    metadata = trusted_metadata(
        subject,
        token_endpoint="http://127.0.0.1:8000/token",  # noqa: S106
    )
    with pytest.raises(ValueError, match="transport"):
        subject.exchange_code(metadata, code="code", code_verifier=_VERIFIER)
