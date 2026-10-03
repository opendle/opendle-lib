"""Optional bounded HTTPX transport for the shared OpenID Connect client."""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING

import httpx

from opendle._internal.http import (
    HeaderLimitError,
    HeaderProtocolError,
    normalize_headers,
)
from opendle.oidc import (
    OidcResponseLimitError,
    OidcTransportError,
    OidcTransportResponse,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["HttpxOidcTransport"]

_CRITICAL_HEADERS = frozenset({"content-length", "content-type", "content-encoding"})
_MAXIMUM_LENGTH_DIGITS = 20


class HttpxOidcTransport:
    """Read bounded OIDC responses without redirects or environment proxies."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout | None = None,
        maximum_response_bytes: int = 1_048_576,
    ) -> None:
        """Use a host-owned client or create and close a client per request.

        A supplied client must disable environment proxies. The host owns its
        lifetime. The transport always disables redirects on each request.
        """
        if client is not None and transport is not None:
            msg = "Supply one HTTPX client or transport."
            raise ValueError(msg)
        if type(maximum_response_bytes) is not int or maximum_response_bytes < 1:
            msg = "The OIDC response byte bound must be a positive integer."
            raise ValueError(msg)
        self._client = client
        self._transport = transport
        self._timeout = timeout
        self._maximum_response_bytes = maximum_response_bytes

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OidcTransportResponse:
        """Read one response within header and decoded-body byte bounds."""
        client_context = (
            nullcontext(self._client)
            if self._client is not None
            else httpx.Client(transport=self._transport, trust_env=False)
        )
        request_headers = httpx.Headers(headers)
        request_headers["accept-encoding"] = "identity"
        try:
            with (
                client_context as client,
                client.stream(
                    method,
                    url,
                    headers=request_headers,
                    content=body,
                    timeout=self._timeout or timeout,
                    follow_redirects=False,
                ) as response,
            ):
                checked_headers = self._headers(response)
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > self._maximum_response_bytes:
                        msg = "The OIDC response exceeds the document byte bound."
                        raise OidcResponseLimitError(msg)
                    content.extend(chunk)
                return OidcTransportResponse(
                    status=response.status_code,
                    headers=checked_headers,
                    body=bytes(content),
                )
        except httpx.HTTPError:
            msg = "The OpenID Connect provider request failed."
            raise OidcTransportError(msg) from None

    def _headers(self, response: httpx.Response) -> dict[str, str]:
        try:
            headers = normalize_headers(
                response.headers.multi_items(),
                maximum_count=100,
                maximum_bytes=65_536,
                critical_headers=_CRITICAL_HEADERS,
            )
        except HeaderLimitError:
            msg = "The OIDC response headers exceed a safety bound."
            raise OidcResponseLimitError(msg) from None
        except HeaderProtocolError:
            msg = "The OIDC response headers are invalid."
            raise OidcTransportError(msg) from None
        if (
            response.headers.get("content-encoding", "identity").strip().casefold()
            != "identity"
        ):
            msg = "The OIDC response encoding is invalid."
            raise OidcTransportError(msg)
        length = response.headers.get("content-length")
        if length is not None and (
            not length.isascii()
            or not length.isdecimal()
            or len(length) > _MAXIMUM_LENGTH_DIGITS
            or int(length) > self._maximum_response_bytes
        ):
            msg = "The OIDC response length is invalid."
            raise OidcTransportError(msg)
        return headers
