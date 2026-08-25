"""Framework-neutral OpenID Connect authorization request primitives."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, TypeIs, cast
from urllib.parse import (
    SplitResult,
    parse_qsl,
    quote_plus,
    urlencode,
    urlsplit,
    urlunsplit,
)

from opendle._internal.http import (
    HeaderLimitError,
    HeaderProtocolError,
    UrllibHttpClient,
    header_value,
    media_type,
    normalize_headers,
)
from opendle._internal.json import StrictJsonError, strict_json_loads

if TYPE_CHECKING:
    from collections.abc import Callable

    from opendle.contracts import JsonObject, JsonValue

__all__ = [
    "OidcClaimValue",
    "OidcClient",
    "OidcClientAuthenticationMethod",
    "OidcError",
    "OidcHTTPError",
    "OidcMetadata",
    "OidcProtocolError",
    "OidcResponseLimitError",
    "OidcSigningKeys",
    "OidcTransport",
    "OidcTransportError",
    "OidcTransportResponse",
    "VerifiedIdToken",
    "build_authorization_code_url",
    "pkce_s256_challenge",
    "validate_canonical_token",
]

type OidcClaimValue = (
    bool
    | int
    | float
    | str
    | tuple["OidcClaimValue", ...]
    | Mapping[str, "OidcClaimValue"]
    | None
)

_BASE64URL_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
_TOKEN_CHARACTERS = 43
_TOKEN_BYTES = 32
_MAXIMUM_CLIENT_ID_CHARACTERS = 2_000
_MAXIMUM_URL_CHARACTERS = 4_096
_MAXIMUM_PORT = 65_535
_MAXIMUM_OIDC_DOCUMENT_BYTES = 1_048_576
_MAXIMUM_RESPONSE_HEADERS = 100
_MAXIMUM_RESPONSE_HEADER_BYTES = 65_536
_HTTP_OK = 200
_HTTP_STATUS_MINIMUM = 100
_HTTP_STATUS_MAXIMUM = 599
_MAXIMUM_NUMERIC_DATE = 253_402_300_799
_MAXIMUM_TOKEN_CHARACTERS = 32_768
_MAXIMUM_JWK_COUNT = 100
_MAXIMUM_JWT_HEADER_BYTES = 4_096
_MAXIMUM_JWT_CLAIMS_BYTES = 24_576
_MAXIMUM_JWT_SIGNATURE_BYTES = 1_024
_MAXIMUM_RSA_MODULUS_BYTES = 1_024
_MAXIMUM_RSA_EXPONENT_BYTES = 8
_MAXIMUM_RSA_BITS = 8_192
_MINIMUM_RSA_BITS = 2_048
_MAXIMUM_KEY_ID_CHARACTERS = 500
_MAXIMUM_SUBJECT_CHARACTERS = 500
_MAXIMUM_AUDIENCES = 20
_MAXIMUM_FUTURE_ISSUED_AT_SECONDS = 60
_MAXIMUM_DISCOVERY_VALUES = 100
_MAXIMUM_DISCOVERY_VALUE_CHARACTERS = 500
_MAXIMUM_JWT_OBJECT_FIELDS = 100
_JWT_SEGMENTS = 3
_CONTROL_CHARACTER_END = 0x20
_DELETE_CHARACTER = 0x7F
_MINIMUM_RSA_EXPONENT = 65_537
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
_CRITICAL_RESPONSE_HEADERS = frozenset(
    {"content-type", "content-length", "content-encoding"}
)
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


class OidcClientAuthenticationMethod(StrEnum):
    """Select the confidential-client authentication method."""

    CLIENT_SECRET_BASIC = "client_secret_basic"  # noqa: S105 - Protocol identifier.
    CLIENT_SECRET_POST = "client_secret_post"  # noqa: S105 - Protocol identifier.


@dataclass(frozen=True, slots=True)
class OidcMetadata:
    """Contain validated OpenID Connect discovery endpoints."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_endpoint_authentication_method: OidcClientAuthenticationMethod
    _client_binding: object | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def belongs_to_client(self, binding: object) -> bool:
        """Return true when the client binding created this metadata."""
        return self._client_binding is binding


@dataclass(frozen=True, slots=True)
class VerifiedIdToken:
    """Contain verified identity-token authority and public claims."""

    issuer: str
    subject: str
    audiences: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    claims: Mapping[str, OidcClaimValue] = field(repr=False)


@dataclass(frozen=True, slots=True)
class OidcSigningKeys:
    """Contain one immutable bounded signing-key snapshot."""

    issuer: str
    jwks_uri: str
    keys: tuple[Mapping[str, object], ...] = field(repr=False)
    _client_binding: object | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def belongs_to_client(self, binding: object) -> bool:
        """Return true when the client binding created this key snapshot."""
        return self._client_binding is binding


@dataclass(frozen=True, slots=True)
class OidcTransportResponse:
    """Contain one complete response from an OIDC HTTP transport."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class OidcTransport(Protocol):
    """Send one complete bounded OpenID Connect HTTP request.

    An injected transport must not follow redirects or use an untrusted proxy.
    It must bound response headers and bytes before it returns. It must not log
    request headers, request bodies, provider response bodies, or URL query
    values because they can contain credentials and tokens.
    """

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OidcTransportResponse:
        """Send one request and return its complete response."""
        ...


class OidcError(RuntimeError):
    """Report a safe OpenID Connect client failure."""


class OidcTransportError(OidcError):
    """Report a failure before a complete OIDC response is available."""


class OidcProtocolError(OidcError):
    """Report invalid provider metadata, JSON, keys, or token data."""


class OidcResponseLimitError(OidcProtocolError):
    """Report an OIDC response that exceeds a safety bound."""


class OidcHTTPError(OidcError):
    """Report one non-success OpenID Connect HTTP response."""

    def __init__(self, status: int) -> None:
        """Initialize a safe provider HTTP error."""
        self.status = status
        super().__init__(f"The OpenID Connect provider returned HTTP {status}.")


class _UrllibOidcTransport:
    """Adapt the private bounded standard-library HTTP client."""

    def __init__(self, maximum_response_bytes: int) -> None:
        self._http = UrllibHttpClient(maximum_response_bytes)

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OidcTransportResponse:
        response = self._http.request(method, url, headers, body, timeout)
        try:
            response_headers = normalize_headers(
                response.headers,
                maximum_count=_MAXIMUM_RESPONSE_HEADERS,
                maximum_bytes=_MAXIMUM_RESPONSE_HEADER_BYTES,
                critical_headers=_CRITICAL_RESPONSE_HEADERS,
            )
        except HeaderLimitError:
            msg = "The OpenID Connect response headers exceed a safety bound."
            raise OidcResponseLimitError(msg) from None
        except HeaderProtocolError:
            msg = "The OpenID Connect response headers are invalid."
            raise OidcProtocolError(msg) from None
        return OidcTransportResponse(response.status, response_headers, response.body)


class OidcClient:
    """Use one exact confidential OIDC client with bounded verification."""

    __slots__ = (
        "_binding",
        "_client_id",
        "_client_secret",
        "_clock",
        "_issuer",
        "_maximum_document_bytes",
        "_redirect_uri",
        "_timeout",
        "_token_authentication_method",
        "_transport",
    )

    def __init__(  # noqa: PLR0913 - OIDC safety has fixed controls.
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_authentication_method: OidcClientAuthenticationMethod = (
            OidcClientAuthenticationMethod.CLIENT_SECRET_BASIC
        ),
        timeout: float = 10.0,
        maximum_document_bytes: int = _MAXIMUM_OIDC_DOCUMENT_BYTES,
        transport: OidcTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize one client without host authorization or session policy."""
        self._binding = object()
        self._issuer = _exact_issuer(issuer)
        self._client_id = _client_value(client_id, "client ID", 2_000)
        self._client_secret = _client_value(client_secret, "client secret", 16_384)
        _redirect_uri(redirect_uri)
        self._redirect_uri = redirect_uri
        raw_authentication_method = cast("object", token_authentication_method)
        if not isinstance(raw_authentication_method, OidcClientAuthenticationMethod):
            msg = "The OpenID Connect client authentication method is invalid."
            raise TypeError(msg)
        timeout_value = cast("object", timeout)
        if (
            isinstance(timeout_value, bool)
            or not isinstance(timeout_value, int | float)
            or not math.isfinite(timeout_value)
            or timeout_value <= 0
        ):
            msg = "The OpenID Connect timeout must be finite and positive."
            raise ValueError(msg)
        document_bound = cast("object", maximum_document_bytes)
        if (
            isinstance(document_bound, bool)
            or not isinstance(document_bound, int)
            or document_bound <= 0
        ):
            msg = "The OpenID Connect document bound must be positive."
            raise ValueError(msg)
        self._token_authentication_method = token_authentication_method
        self._timeout = float(timeout_value)
        self._maximum_document_bytes = document_bound
        self._transport = transport or _UrllibOidcTransport(document_bound)
        self._clock = clock or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        """Return client settings without the confidential secret."""
        return (
            f"OidcClient(issuer={self._issuer!r}, client_id={self._client_id!r}, "
            f"redirect_uri={self._redirect_uri!r}, "
            "client_secret=<protected>, "
            "token_authentication_method="
            f"{self._token_authentication_method.value!r}, timeout={self._timeout!r}, "
            f"maximum_document_bytes={self._maximum_document_bytes!r})"
        )

    def discover(self) -> OidcMetadata:
        """Fetch and validate discovery metadata for the exact issuer."""
        document = self._json_request(
            "GET", f"{self._issuer}/.well-known/openid-configuration"
        )
        if document.get("issuer") != self._issuer:
            msg = "The OpenID Connect discovery issuer does not match."
            raise OidcProtocolError(msg)
        authorization_endpoint = _document_endpoint(document, "authorization_endpoint")
        token_endpoint = _document_endpoint(document, "token_endpoint")
        jwks_uri = _document_endpoint(document, "jwks_uri")
        if urlsplit(self._issuer).scheme == "https" and any(
            urlsplit(endpoint).scheme != "https"
            for endpoint in (authorization_endpoint, token_endpoint, jwks_uri)
        ):
            msg = "The OpenID Connect discovery endpoint transport is invalid."
            raise OidcProtocolError(msg)
        _require_supported(document, "response_types_supported", "code")
        _require_supported(
            document,
            "grant_types_supported",
            "authorization_code",
            default=("authorization_code", "implicit"),
        )
        _require_supported(document, "code_challenge_methods_supported", "S256")
        _require_supported(document, "id_token_signing_alg_values_supported", "RS256")
        if "scopes_supported" in document:
            _require_supported(document, "scopes_supported", "openid")
        _require_supported(
            document,
            "token_endpoint_auth_methods_supported",
            self._token_authentication_method.value,
            default=(OidcClientAuthenticationMethod.CLIENT_SECRET_BASIC.value,),
        )
        metadata = OidcMetadata(
            issuer=self._issuer,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            jwks_uri=jwks_uri,
            token_endpoint_authentication_method=self._token_authentication_method,
        )
        object.__setattr__(metadata, "_client_binding", self._binding)
        return metadata

    def authorization_url(
        self,
        metadata: OidcMetadata,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
        include_offline_access: bool = False,
    ) -> str:
        """Build the authorization-code request for validated metadata."""
        self._validate_metadata(metadata)
        return build_authorization_code_url(
            authorization_endpoint=metadata.authorization_endpoint,
            client_id=self._client_id,
            redirect_uri=self._redirect_uri,
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            include_offline_access=include_offline_access,
        )

    def ensure_signing_keys_available(self, metadata: OidcMetadata) -> OidcSigningKeys:
        """Return one snapshot that has at least one usable RS256 key."""
        return self.fetch_signing_keys(metadata)

    def fetch_signing_keys(self, metadata: OidcMetadata) -> OidcSigningKeys:
        """Fetch one immutable key snapshot before a one-use code exchange."""
        self._validate_metadata(metadata)
        jwks = self._json_request("GET", metadata.jwks_uri, jwks=True)
        keys = _jwks_keys(jwks)
        for key in keys:
            if not _is_rs256_verification_key(key):
                continue
            try:
                _rsa_parameters(key)
            except OidcProtocolError:
                continue
            snapshot = OidcSigningKeys(
                issuer=metadata.issuer,
                jwks_uri=metadata.jwks_uri,
                keys=tuple(_snapshot_rsa_key(item) for item in keys),
            )
            object.__setattr__(snapshot, "_client_binding", self._binding)
            return snapshot
        msg = "The OpenID Connect JSON Web Key Set has no usable signing key."
        raise OidcProtocolError(msg)

    def exchange_code(
        self, metadata: OidcMetadata, *, code: str, code_verifier: str
    ) -> str:
        """Exchange one code and return the bounded identity token."""
        self._validate_metadata(metadata)
        code_value = _client_value(code, "authorization code", 4_000)
        validate_canonical_token(code_verifier)
        values = {
            "grant_type": "authorization_code",
            "code": code_value,
            "redirect_uri": self._redirect_uri,
            "code_verifier": code_verifier,
        }
        headers = {"Accept": "application/json"}
        if (
            self._token_authentication_method
            is OidcClientAuthenticationMethod.CLIENT_SECRET_BASIC
        ):
            credentials = base64.b64encode(
                (
                    f"{quote_plus(self._client_id, safe='')}:"
                    f"{quote_plus(self._client_secret, safe='')}"
                ).encode("ascii")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {credentials}"
        else:
            values["client_id"] = self._client_id
            values["client_secret"] = self._client_secret
        body = urlencode(values).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        document = self._json_request(
            "POST", metadata.token_endpoint, headers=headers, body=body
        )
        token_type = document.get("token_type")
        if token_type is not None and (
            not isinstance(token_type, str) or token_type.casefold() != "bearer"
        ):
            msg = "The OpenID Connect token type is invalid."
            raise OidcProtocolError(msg)
        token = document.get("id_token")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= _MAXIMUM_TOKEN_CHARACTERS
        ):
            msg = "The OpenID Connect identity token is invalid."
            raise OidcProtocolError(msg)
        return token

    def verify_id_token(
        self,
        metadata: OidcMetadata,
        *,
        id_token: str,
        expected_nonce: str,
        signing_keys: OidcSigningKeys | None = None,
    ) -> VerifiedIdToken:
        """Verify one RS256 identity token with supplied or current keys."""
        self._validate_metadata(metadata)
        validate_canonical_token(expected_nonce)
        header, claims, signed, signature = _jwt_parts(id_token)
        key_id = _id_token_key_id(header)
        snapshot = signing_keys or self.fetch_signing_keys(metadata)
        raw_snapshot = cast("object", snapshot)
        if (
            not isinstance(raw_snapshot, OidcSigningKeys)
            or not snapshot.belongs_to_client(self._binding)
            or snapshot.issuer != metadata.issuer
            or snapshot.jwks_uri != metadata.jwks_uri
        ):
            msg = "The OpenID Connect signing keys do not belong to this metadata."
            raise ValueError(msg)
        key = _select_rsa_key(snapshot.keys, key_id)
        _verify_rs256(key, signed, signature)
        return self._verified_claims(claims, expected_nonce)

    def _verified_claims(
        self, claims: JsonObject, expected_nonce: str
    ) -> VerifiedIdToken:
        required = {"iss", "sub", "aud", "exp", "iat", "nonce"}
        if not required <= set(claims):
            msg = "The OpenID Connect identity token has missing claims."
            raise OidcProtocolError(msg)
        issuer = claims.get("iss")
        subject = claims.get("sub")
        if issuer != self._issuer:
            msg = "The OpenID Connect identity token issuer is invalid."
            raise OidcProtocolError(msg)
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= _MAXIMUM_SUBJECT_CHARACTERS
            or any(
                ord(character) <= _CONTROL_CHARACTER_END
                or ord(character) == _DELETE_CHARACTER
                for character in subject
            )
        ):
            msg = "The OpenID Connect identity token subject is invalid."
            raise OidcProtocolError(msg)
        audiences = _audiences(claims, self._client_id)
        if claims.get("nonce") != expected_nonce:
            msg = "The OpenID Connect identity token nonce is invalid."
            raise OidcProtocolError(msg)
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "The OpenID Connect clock must have a time zone."
            raise OidcProtocolError(msg)
        now_timestamp = now.astimezone(UTC).timestamp()
        issued_at_value = claims.get("iat")
        expires_at_value = claims.get("exp")
        not_before_value = claims.get("nbf")
        if (
            not _is_numeric_date(issued_at_value)
            or not _is_numeric_date(expires_at_value)
            or expires_at_value <= now_timestamp
            or issued_at_value > now_timestamp + _MAXIMUM_FUTURE_ISSUED_AT_SECONDS
            or issued_at_value >= expires_at_value
            or (
                not_before_value is not None
                and (
                    not _is_numeric_date(not_before_value)
                    or not_before_value > now_timestamp
                )
            )
        ):
            msg = "The OpenID Connect identity token time is invalid."
            raise OidcProtocolError(msg)
        issued_at = _numeric_datetime(issued_at_value)
        expires_at = _numeric_datetime(expires_at_value)
        return VerifiedIdToken(
            issuer=self._issuer,
            subject=subject,
            audiences=audiences,
            issued_at=issued_at,
            expires_at=expires_at,
            claims=cast("Mapping[str, OidcClaimValue]", _freeze_claim_value(claims)),
        )

    def _json_request(  # noqa: C901, PLR0912, PLR0915 - Fixed response checks.
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        jwks: bool = False,
    ) -> JsonObject:
        _absolute_oidc_url(url, "provider endpoint", allow_query=True)
        request_headers = {"Accept": "application/json", **(headers or {})}
        try:
            response = self._transport.request(
                method, url, request_headers, body, self._timeout
            )
        except OidcError:
            raise
        except Exception:  # noqa: BLE001 - A custom transport can raise any error.
            msg = "The OpenID Connect provider request failed."
            raise OidcTransportError(msg) from None
        raw_response = cast("object", response)
        if not isinstance(raw_response, OidcTransportResponse):
            msg = "The OpenID Connect transport returned an invalid response."
            raise OidcProtocolError(msg)
        response = raw_response
        status = cast("object", response.status)
        response_body = cast("object", response.body)
        if (
            isinstance(status, bool)
            or not isinstance(status, int)
            or not _HTTP_STATUS_MINIMUM <= status <= _HTTP_STATUS_MAXIMUM
            or not isinstance(response_body, bytes)
        ):
            msg = "The OpenID Connect transport returned an invalid response."
            raise OidcProtocolError(msg)
        raw_headers = cast("object", response.headers)
        if not isinstance(raw_headers, Mapping):
            msg = "The OpenID Connect response headers are invalid."
            raise OidcProtocolError(msg)
        try:
            response_headers = normalize_headers(
                cast("Mapping[str, str]", raw_headers).items(),
                maximum_count=_MAXIMUM_RESPONSE_HEADERS,
                maximum_bytes=_MAXIMUM_RESPONSE_HEADER_BYTES,
                critical_headers=_CRITICAL_RESPONSE_HEADERS,
            )
        except (AttributeError, TypeError, HeaderProtocolError) as error:
            if isinstance(error, HeaderLimitError):
                msg = "The OpenID Connect response headers exceed a safety bound."
                raise OidcResponseLimitError(msg) from None
            msg = "The OpenID Connect response headers are invalid."
            raise OidcProtocolError(msg) from None
        if len(response_body) > self._maximum_document_bytes:
            msg = "The OpenID Connect response exceeds the document byte bound."
            raise OidcResponseLimitError(msg)
        declared_length = header_value(response_headers, "Content-Length")
        if declared_length and (
            not declared_length.isascii()
            or not declared_length.isdecimal()
            or int(declared_length) != len(response_body)
        ):
            msg = "The OpenID Connect response Content-Length is invalid."
            raise OidcProtocolError(msg)
        if status != _HTTP_OK:
            raise OidcHTTPError(status)
        accepted_media_types = (
            {"application/json", "application/jwk-set+json"}
            if jwks
            else {"application/json"}
        )
        if media_type(response_headers) not in accepted_media_types:
            msg = "The OpenID Connect response media type is invalid."
            raise OidcProtocolError(msg)
        try:
            value = strict_json_loads(response_body)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            StrictJsonError,
        ):
            msg = "The OpenID Connect response is not valid strict JSON."
            raise OidcProtocolError(msg) from None
        if not isinstance(value, dict):
            msg = "The OpenID Connect response JSON is not an object."
            raise OidcProtocolError(msg)
        return value

    def _validate_metadata(self, metadata: OidcMetadata) -> None:
        raw_metadata = cast("object", metadata)
        if (
            not isinstance(raw_metadata, OidcMetadata)
            or not metadata.belongs_to_client(self._binding)
            or metadata.issuer != self._issuer
            or metadata.token_endpoint_authentication_method
            is not self._token_authentication_method
        ):
            msg = "The OpenID Connect metadata does not belong to this client."
            raise ValueError(msg)
        try:
            endpoints = (
                _absolute_oidc_url(
                    metadata.authorization_endpoint,
                    "authorization endpoint",
                    allow_query=True,
                ),
                _absolute_oidc_url(
                    metadata.token_endpoint, "token endpoint", allow_query=True
                ),
                _absolute_oidc_url(metadata.jwks_uri, "JWKS URI", allow_query=True),
            )
        except ValueError as error:
            msg = "The OpenID Connect metadata endpoint is invalid."
            raise ValueError(msg) from error
        if urlsplit(self._issuer).scheme == "https" and any(
            endpoint.scheme != "https" for endpoint in endpoints
        ):
            msg = "The OpenID Connect metadata endpoint transport is invalid."
            raise ValueError(msg)


def _exact_issuer(value: str) -> str:
    _absolute_oidc_url(value, "issuer", allow_query=False)
    return value.rstrip("/")


def _client_value(value: str, name: str, maximum: int) -> str:
    raw_value = cast("object", value)
    if (
        not isinstance(raw_value, str)
        or not 1 <= len(raw_value) <= maximum
        or raw_value.strip() != raw_value
        or any(character.isspace() for character in raw_value)
        or "\x00" in raw_value
    ):
        msg = f"The OpenID Connect {name} is invalid."
        raise ValueError(msg)
    return raw_value


def _document_endpoint(document: JsonObject, name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str):
        msg = f"The OpenID Connect discovery {name} is invalid."
        raise OidcProtocolError(msg)
    try:
        _absolute_oidc_url(value, name.replace("_", " "), allow_query=True)
    except ValueError as error:
        msg = f"The OpenID Connect discovery {name} is invalid."
        raise OidcProtocolError(msg) from error
    return value


def _require_supported(
    document: JsonObject,
    name: str,
    required: str,
    *,
    default: tuple[str, ...] | None = None,
) -> None:
    raw = document.get(name)
    if raw is None and default is not None:
        values = default
    elif (
        isinstance(raw, list)
        and 1 <= len(raw) <= _MAXIMUM_DISCOVERY_VALUES
        and all(
            isinstance(item, str)
            and 1 <= len(item) <= _MAXIMUM_DISCOVERY_VALUE_CHARACTERS
            and item.strip() == item
            for item in raw
        )
    ):
        values = tuple(cast("list[str]", raw))
    else:
        msg = f"The OpenID Connect discovery {name} is invalid."
        raise OidcProtocolError(msg)
    if len(set(values)) != len(values) or required not in values:
        msg = f"The OpenID Connect discovery does not support {required}."
        raise OidcProtocolError(msg)


def _jwt_parts(token: str) -> tuple[JsonObject, JsonObject, bytes, bytes]:
    raw_token = cast("object", token)
    if (
        not isinstance(raw_token, str)
        or not 1 <= len(raw_token) <= _MAXIMUM_TOKEN_CHARACTERS
    ):
        msg = "The OpenID Connect identity token is invalid."
        raise OidcProtocolError(msg)
    segments = raw_token.split(".")
    if len(segments) != _JWT_SEGMENTS:
        msg = "The OpenID Connect identity token is invalid."
        raise OidcProtocolError(msg)
    header = _jwt_object(segments[0], maximum=_MAXIMUM_JWT_HEADER_BYTES)
    claims = _jwt_object(segments[1], maximum=_MAXIMUM_JWT_CLAIMS_BYTES)
    signature = _base64url_decode(segments[2], maximum=_MAXIMUM_JWT_SIGNATURE_BYTES)
    return (
        header,
        claims,
        f"{segments[0]}.{segments[1]}".encode("ascii"),
        signature,
    )


def _jwt_object(segment: str, *, maximum: int) -> JsonObject:
    raw = _base64url_decode(segment, maximum=maximum)
    try:
        value = strict_json_loads(raw)
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError, StrictJsonError:
        msg = "The OpenID Connect identity token JSON is invalid."
        raise OidcProtocolError(msg) from None
    if not isinstance(value, dict) or len(value) > _MAXIMUM_JWT_OBJECT_FIELDS:
        msg = "The OpenID Connect identity token JSON is invalid."
        raise OidcProtocolError(msg)
    return value


def _id_token_key_id(header: JsonObject) -> str:
    key_id = header.get("kid")
    if (
        header.get("alg") != "RS256"
        or not _is_key_id(key_id)
        or (header.get("typ") is not None and header.get("typ") != "JWT")
        or "crit" in header
    ):
        msg = "The OpenID Connect identity token header is invalid."
        raise OidcProtocolError(msg)
    return key_id


def _freeze_claim_value(value: JsonValue) -> OidcClaimValue:
    if isinstance(value, dict):
        return MappingProxyType(
            {name: _freeze_claim_value(item) for name, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_claim_value(item) for item in value)
    return value


def _is_key_id(value: object) -> TypeIs[str]:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= _MAXIMUM_KEY_ID_CHARACTERS
        and not any(
            ord(character) <= _CONTROL_CHARACTER_END
            or ord(character) == _DELETE_CHARACTER
            for character in value
        )
    )


def _jwks_keys(jwks: JsonObject) -> list[JsonObject]:
    keys = jwks.get("keys")
    if (
        not isinstance(keys, list)
        or not 1 <= len(keys) <= _MAXIMUM_JWK_COUNT
        or any(not isinstance(key, dict) for key in keys)
    ):
        msg = "The OpenID Connect JSON Web Key Set is invalid."
        raise OidcProtocolError(msg)
    return cast("list[JsonObject]", keys)


def _is_rs256_verification_key(key: Mapping[str, object]) -> bool:
    return (
        _is_key_id(key.get("kid"))
        and key.get("kty") == "RSA"
        and key.get("use", "sig") == "sig"
        and key.get("alg", "RS256") == "RS256"
        and key.get("key_ops", ("verify",)) in (["verify"], ("verify",))
    )


def _snapshot_rsa_key(key: JsonObject) -> Mapping[str, object]:
    snapshot: dict[str, object] = {}
    for name in ("kid", "kty", "use", "alg", "key_ops", "n", "e"):
        if name not in key:
            continue
        value = key[name]
        snapshot[name] = (
            tuple(value) if name == "key_ops" and isinstance(value, list) else value
        )
    return MappingProxyType(snapshot)


def _select_rsa_key(
    keys: tuple[Mapping[str, object], ...], key_id: str
) -> Mapping[str, object]:
    matches = [
        key
        for key in keys
        if key.get("kid") == key_id and _is_rs256_verification_key(key)
    ]
    if len(matches) != 1:
        msg = "The OpenID Connect identity token key is invalid."
        raise OidcProtocolError(msg)
    return matches[0]


def _verify_rs256(key: Mapping[str, object], signed: bytes, signature: bytes) -> None:
    modulus, exponent = _rsa_parameters(key)
    modulus_bytes = (modulus.bit_length() + 7) // 8
    if len(signature) != modulus_bytes:
        msg = "The OpenID Connect identity token signature is invalid."
        raise OidcProtocolError(msg)
    signature_integer = int.from_bytes(signature, "big")
    if signature_integer >= modulus:
        msg = "The OpenID Connect identity token signature is invalid."
        raise OidcProtocolError(msg)
    encoded = pow(signature_integer, exponent, modulus).to_bytes(modulus_bytes, "big")
    digest_info = _SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(signed).digest()
    padding_bytes = modulus_bytes - len(digest_info) - 3
    expected = b"\x00\x01" + (b"\xff" * padding_bytes) + b"\x00" + digest_info
    if not hmac.compare_digest(encoded, expected):
        msg = "The OpenID Connect identity token signature is invalid."
        raise OidcProtocolError(msg)


def _rsa_parameters(key: Mapping[str, object]) -> tuple[int, int]:
    modulus_value = key.get("n")
    exponent_value = key.get("e")
    if not isinstance(modulus_value, str) or not isinstance(exponent_value, str):
        msg = "The OpenID Connect RSA key is invalid."
        raise OidcProtocolError(msg)
    modulus = int.from_bytes(
        _base64url_decode(modulus_value, maximum=_MAXIMUM_RSA_MODULUS_BYTES), "big"
    )
    exponent = int.from_bytes(
        _base64url_decode(exponent_value, maximum=_MAXIMUM_RSA_EXPONENT_BYTES), "big"
    )
    if (
        not _MINIMUM_RSA_BITS <= modulus.bit_length() <= _MAXIMUM_RSA_BITS
        or not _MINIMUM_RSA_EXPONENT <= exponent <= 2**31 - 1
        or exponent % 2 == 0
    ):
        msg = "The OpenID Connect RSA key is invalid."
        raise OidcProtocolError(msg)
    return modulus, exponent


def _base64url_decode(value: str, *, maximum: int) -> bytes:
    if (
        not value
        or len(value) > maximum * 2
        or "=" in value
        or not set(value) <= _BASE64URL_ALPHABET
    ):
        msg = "The OpenID Connect Base64URL value is invalid."
        raise OidcProtocolError(msg)
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except ValueError, UnicodeEncodeError, binascii.Error:
        msg = "The OpenID Connect Base64URL value is invalid."
        raise OidcProtocolError(msg) from None
    if (
        not decoded
        or len(decoded) > maximum
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value
    ):
        msg = "The OpenID Connect Base64URL value is invalid."
        raise OidcProtocolError(msg)
    return decoded


def _audiences(claims: JsonObject, client_id: str) -> tuple[str, ...]:
    audience = claims.get("aud")
    audiences: tuple[str, ...]
    if isinstance(audience, str):
        audiences = (audience,)
    elif (
        isinstance(audience, list)
        and 1 <= len(audience) <= _MAXIMUM_AUDIENCES
        and all(
            isinstance(item, str) and 1 <= len(item) <= _MAXIMUM_CLIENT_ID_CHARACTERS
            for item in audience
        )
    ):
        audiences = tuple(cast("list[str]", audience))
    else:
        msg = "The OpenID Connect identity token audience is invalid."
        raise OidcProtocolError(msg)
    if len(set(audiences)) != len(audiences) or client_id not in audiences:
        msg = "The OpenID Connect identity token audience is invalid."
        raise OidcProtocolError(msg)
    authorized_party = claims.get("azp")
    if (len(audiences) > 1 or authorized_party is not None) and (
        authorized_party != client_id
    ):
        msg = "The OpenID Connect identity token authorized party is invalid."
        raise OidcProtocolError(msg)
    return audiences


def _is_numeric_date(value: object) -> TypeIs[int | float]:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if isinstance(value, int):
        return abs(value) <= _MAXIMUM_NUMERIC_DATE
    return math.isfinite(value) and abs(value) <= _MAXIMUM_NUMERIC_DATE


def _numeric_datetime(value: float) -> datetime:
    try:
        return datetime.fromtimestamp(value, UTC)
    except OverflowError, OSError, ValueError:
        msg = "The OpenID Connect identity token time is invalid."
        raise OidcProtocolError(msg) from None


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
