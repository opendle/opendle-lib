"""Check the shared HTTPX OIDC transport at its HTTP boundary."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest

from opendle.oidc import (
    OidcResponseLimitError,
    OidcTransportError,
)
from opendle.oidc_httpx import HttpxOidcTransport

if TYPE_CHECKING:
    from collections.abc import Iterator

_URL = "https://identity.example/token"


class BodyStream(httpx.SyncByteStream):
    """Track body access and resource cleanup."""

    def __init__(self, chunks: tuple[bytes, ...] = (b"{}",)) -> None:
        """Keep controlled chunks for a streamed response."""
        self.chunks = chunks
        self.read = False
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        """Mark a body read before it supplies its first chunk."""
        self.read = True
        yield from self.chunks

    def close(self) -> None:
        """Mark resource cleanup."""
        self.closed = True


def test_owned_client_request_is_bounded_and_does_not_follow_redirects() -> None:
    """Disable proxy environment and redirects while keeping request inputs."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://other.example"})

    with patch("opendle.oidc_httpx.httpx.Client", wraps=httpx.Client) as factory:
        response = HttpxOidcTransport(transport=httpx.MockTransport(respond)).request(
            "POST", _URL, {"authorization": "protected-test-value"}, b"code=test", 7
        )
    factory.assert_called_once()
    assert factory.call_args.kwargs["trust_env"] is False
    assert response.status == HTTPStatus.FOUND
    assert len(requests) == 1
    assert requests[0].content == b"code=test"
    assert requests[0].headers["authorization"] == "protected-test-value"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert requests[0].extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 7
    )


def test_supplied_client_retains_ownership_and_specific_timeouts() -> None:
    """Keep the host client open and preserve its phase timeout policy."""
    timeouts: list[object] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, content=b"{}")

    with httpx.Client(
        transport=httpx.MockTransport(respond), trust_env=False
    ) as client:
        transport = HttpxOidcTransport(
            client=client, timeout=httpx.Timeout(5, connect=3, pool=3)
        )
        for _ in range(2):
            response = transport.request("GET", _URL, {}, None, 9)
            assert response.body == b"{}"
            assert not client.is_closed
    assert timeouts == [{"connect": 3, "read": 5, "write": 5, "pool": 3}] * 2


@pytest.mark.parametrize("bound", [True, 0, -1])
def test_invalid_bounds_are_rejected(bound: int) -> None:
    """Reject invalid response bounds before network work."""
    with pytest.raises(ValueError, match="positive integer"):
        HttpxOidcTransport(maximum_response_bytes=bound)


def test_client_and_transport_are_mutually_exclusive() -> None:
    """Reject two transport owners."""
    with (
        httpx.Client(trust_env=False) as client,
        pytest.raises(ValueError, match="Supply one HTTPX client or transport"),
    ):
        HttpxOidcTransport(
            client=client,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        )


@pytest.mark.parametrize(
    ("headers", "error"),
    [
        ([("content-length", "2"), ("Content-Length", "2")], OidcTransportError),
        ([("content-type", "a"), ("Content-Type", "b")], OidcTransportError),
        ([("content-encoding", "identity")] * 2, OidcTransportError),
        ([("content-encoding", "gzip")], OidcTransportError),
        ([("content-encoding", "deflate")], OidcTransportError),
        ([("x-test", "bad\x00value")], OidcTransportError),
        ([("x-test", "x" * 65_536)], OidcResponseLimitError),
        ([(f"x-{index}", "a") for index in range(101)], OidcResponseLimitError),
        ([("content-length", "")], OidcTransportError),
        ([("content-length", "\uff12")], OidcTransportError),
        ([("content-length", "-1")], OidcTransportError),
        ([("content-length", "9" * 21)], OidcTransportError),
        ([("content-length", "100")], OidcTransportError),
    ],
)
def test_invalid_headers_are_rejected_before_body_access(
    headers: list[tuple[str, str]], error: type[Exception]
) -> None:
    """Validate critical headers and all header bounds before a body read."""
    stream = BodyStream()
    # HTTPX needs an explicit encoding to construct the Unicode length fixture.
    encoded_headers = [
        (name.encode(), value.encode("utf-8")) for name, value in headers
    ]
    response = httpx.Response(200, headers=encoded_headers, stream=stream)
    transport = HttpxOidcTransport(
        transport=httpx.MockTransport(lambda _: response), maximum_response_bytes=8
    )
    with pytest.raises(error):
        transport.request("GET", _URL, {}, None, 5)
    assert not stream.read
    assert stream.closed


@pytest.mark.parametrize("chunks", [(b"123456789",), (b"1234", b"56789")])
def test_decoded_body_bounds_close_the_response(chunks: tuple[bytes, ...]) -> None:
    """Reject oversized bodies with no Content-Length and close resources."""
    stream = BodyStream(chunks)
    transport = HttpxOidcTransport(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
        maximum_response_bytes=8,
    )
    with pytest.raises(OidcResponseLimitError):
        transport.request("GET", _URL, {}, None, 5)
    assert stream.closed


def test_exact_body_bound_is_accepted() -> None:
    """Accept a complete response exactly at the selected byte bound."""
    stream = BodyStream((b"1234", b"5678"))
    transport = HttpxOidcTransport(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
        maximum_response_bytes=8,
    )
    assert transport.request("GET", _URL, {}, None, 5).body == b"12345678"
    assert stream.closed


def test_http_errors_do_not_expose_request_or_provider_content() -> None:
    """Map HTTP failures to a fixed safe shared error."""

    def fail(_: httpx.Request) -> httpx.Response:
        message = "protected provider details"
        raise httpx.ReadTimeout(message)

    transport = HttpxOidcTransport(transport=httpx.MockTransport(fail))
    with pytest.raises(OidcTransportError) as caught:
        transport.request("POST", _URL, {}, b"protected code", 5)
    assert str(caught.value) == "The OpenID Connect provider request failed."
    assert caught.value.__suppress_context__
