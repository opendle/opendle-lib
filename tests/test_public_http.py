"""Check public-only connections, TLS identity and deadline propagation."""

from __future__ import annotations

import asyncio
import socket
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import httpcore
import httpx
import pytest

from opendle.public_http import (
    AsyncPinnedHTTPTransport,
    DeadlineNetworkStream,
    PinnedHTTPTransport,
    PinnedNetworkBackend,
    SSRFError,
    is_private_ip,
)

PUBLIC_IP = "93.184.216.34"


def _answer(ip: str = PUBLIC_IP) -> tuple[int, int, int, str, tuple[str, int]]:
    return socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "::1",
        "10.0.0.1",
        "169.254.169.254",
        "192.0.2.1",
        "100.64.0.1",
        "ff00::1",
        "224.0.0.1",
        "bad",
        "240.0.0.1",
    ],
)
def test_non_public_addresses(ip: str) -> None:
    """Reject internal, reserved, multicast and invalid numeric addresses."""
    assert is_private_ip(ip)


@pytest.mark.parametrize("ip", [PUBLIC_IP, "2001:4860:4860::8888"])
def test_public_addresses(ip: str) -> None:
    """Accept ordinary global IPv4 and IPv6 destinations."""
    assert not is_private_ip(ip)


def test_connection_uses_validated_sockaddr_once() -> None:
    """A second DNS answer cannot replace the numeric connection target."""
    resolver = MagicMock(side_effect=[[_answer()], [_answer("127.0.0.1")]])
    sock = MagicMock(spec=socket.socket)
    backend = PinnedNetworkBackend(
        resolver=resolver, socket_factory=MagicMock(return_value=sock)
    )
    stream = backend.connect_tcp("rebind.example", 443, timeout=3)
    resolver.assert_called_once_with(
        "rebind.example", 443, socket.AF_UNSPEC, socket.SOCK_STREAM
    )
    sock.connect.assert_called_once_with((PUBLIC_IP, 443))
    stream.close()
    sock.close.assert_called_once()


@pytest.mark.parametrize(
    "addresses",
    [
        [],
        [_answer(), _answer("127.0.0.1")],
        [(2, 1, 6, "", ())],
        [(2, 1, 6, "", (None, 443))],
    ],
)
def test_invalid_dns_answers_never_open_a_socket(
    addresses: list[tuple[int, int, int, str, tuple[object, ...]]],
) -> None:
    """Reject empty, mixed, and malformed DNS results before the first connect."""
    factory = MagicMock()
    backend = PinnedNetworkBackend(
        resolver=MagicMock(return_value=addresses), socket_factory=factory
    )
    with pytest.raises((SSRFError, httpcore.ConnectError)):
        backend.connect_tcp("blocked.example", 443)
    factory.assert_not_called()


def test_resolution_error_is_a_connection_error() -> None:
    """Map a DNS lookup error to the transport error vocabulary."""
    backend = PinnedNetworkBackend(resolver=MagicMock(side_effect=socket.gaierror()))
    with pytest.raises(httpcore.ConnectError, match="resolution failed"):
        backend.connect_tcp("missing.example", 443)


@pytest.mark.parametrize("failure", [OSError(), TimeoutError()])
def test_failed_socket_is_closed_and_next_public_address_is_tried(
    failure: OSError,
) -> None:
    """A failed public address does not prevent fallback to another public IP."""
    failed = MagicMock(spec=socket.socket)
    failed.connect.side_effect = failure
    working = MagicMock(spec=socket.socket)
    backend = PinnedNetworkBackend(
        resolver=MagicMock(return_value=[_answer(), _answer("8.8.8.8")]),
        socket_factory=MagicMock(side_effect=[failed, working]),
    )
    backend.connect_tcp("public.example", 443)
    failed.close.assert_called_once()
    working.connect.assert_called_once_with(("8.8.8.8", 443))


@pytest.mark.parametrize(
    ("failure", "error"),
    [(OSError(), httpcore.ConnectError), (TimeoutError(), httpcore.ConnectTimeout)],
)
@pytest.mark.parametrize("during_creation", [False, True])
def test_connection_failures_preserve_error_and_close_socket(
    failure: OSError, error: type[Exception], *, during_creation: bool
) -> None:
    """Both socket creation and connection errors fail without leaked sockets."""
    sock = MagicMock(spec=socket.socket)
    sock.connect.side_effect = failure
    factory = (
        MagicMock(side_effect=failure)
        if during_creation
        else MagicMock(return_value=sock)
    )
    backend = PinnedNetworkBackend(
        resolver=MagicMock(return_value=[_answer()]), socket_factory=factory
    )
    with pytest.raises(error):
        backend.connect_tcp("public.example", 443)
    assert sock.close.call_count == (0 if during_creation else 1)


@pytest.mark.parametrize(
    ("family", "address", "binding"),
    [
        (socket.AF_INET, (PUBLIC_IP, 443), ("127.0.0.1", 0)),
        (socket.AF_INET6, ("2001:4860:4860::8888", 443, 0, 0), ("::1", 0, 0, 0)),
    ],
)
def test_socket_options_and_local_binding(
    family: int, address: tuple[object, ...], binding: tuple[object, ...]
) -> None:
    """Keep supported socket options and address-family-specific local binding."""
    sock = MagicMock(spec=socket.socket)
    backend = PinnedNetworkBackend(
        resolver=MagicMock(return_value=[(family, 1, 6, "", address)]),
        socket_factory=MagicMock(return_value=sock),
    )
    backend.connect_tcp(
        "public.example",
        443,
        local_address=str(binding[0]),
        socket_options=[(1, 2, 3), (1, 2, None, 4)],
    )
    sock.bind.assert_called_once_with(binding)
    sock.setsockopt.assert_any_call(1, 2, 3)
    sock.setsockopt.assert_any_call(1, 2, None, 4)


def test_unix_sockets_are_rejected() -> None:
    """The public web backend must not access local Unix services."""
    with pytest.raises(httpcore.ConnectError, match="Unix sockets"):
        PinnedNetworkBackend().connect_unix_socket("/run/private.sock")


class _HTTPStream(httpcore.NetworkStream):
    def __init__(self) -> None:
        self.hostname: str | None = None
        self.writes: list[bytes] = []
        self.sent = False
        self.context: ssl.SSLContext | None = None

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del max_bytes, timeout
        self.sent = True
        return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(buffer)

    def close(self) -> None:
        self.sent = False

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        del timeout
        self.hostname = server_hostname
        self.context = ssl_context
        return self

    def get_extra_info(self, info: str) -> object:
        del info
        return None


def test_original_host_and_tls_identity_are_preserved() -> None:
    """HTTP and TLS use the URL hostname while the TCP destination is pinned."""
    stream = _HTTPStream()
    backend = MagicMock(spec=httpcore.NetworkBackend)
    backend.connect_tcp.return_value = stream
    with httpx.Client(
        transport=PinnedHTTPTransport(backend), trust_env=False
    ) as client:
        response = client.get("https://public.example/report")
    assert response.is_success
    assert stream.hostname == "public.example"
    assert b"Host: public.example" in b"".join(stream.writes)
    assert stream.context is not None
    assert stream.context.check_hostname
    assert stream.context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize(
    ("remaining", "timeout", "expected"),
    [(None, 4, 4), (2, None, 2), (2, 4, 2), (False, 4, 4)],
)
def test_deadline_stream_keeps_identity_and_clamps_io(
    remaining: float | None, timeout: float | None, expected: float | None
) -> None:
    """Deadline checks apply to all blocking operations without changing TLS."""
    upstream = MagicMock(spec=httpcore.NetworkStream)
    upstream.read.return_value = b"body"
    upstream.start_tls.return_value = upstream
    upstream.get_extra_info.return_value = "metadata"
    stream = DeadlineNetworkStream(upstream, lambda: remaining)
    assert stream.read(4, timeout) == b"body"
    stream.write(b"data", timeout)
    context = ssl.create_default_context()
    stream.start_tls(context, "public.example", timeout)
    assert stream.get_extra_info("socket") == "metadata"
    stream.close()
    upstream.read.assert_called_once_with(4, timeout=expected)
    upstream.write.assert_called_once_with(b"data", timeout=expected)
    upstream.start_tls.assert_called_once_with(
        context, server_hostname="public.example", timeout=expected
    )
    upstream.close.assert_called_once()


@pytest.mark.parametrize("remaining", [None, 2.0])
def test_guarded_resolution_and_connect(remaining: float | None) -> None:
    """A deadline guard is called during resolution and each connection."""
    guard = MagicMock(return_value=remaining)
    sock = MagicMock(spec=socket.socket)
    backend = PinnedNetworkBackend(
        resolver=MagicMock(return_value=[_answer()]),
        socket_factory=MagicMock(return_value=sock),
        continuation_guard=guard,
    )
    backend.connect_tcp("public.example", 443, timeout=4)
    assert guard.call_count >= 2  # noqa: PLR2004 - DNS and connection each check the deadline.


@pytest.mark.parametrize("failure", [socket.gaierror(), RuntimeError()])
def test_guarded_resolution_reports_thread_failure(failure: Exception) -> None:
    """A DNS worker failure must not look like a successful empty response."""
    backend = PinnedNetworkBackend(
        resolver=MagicMock(side_effect=failure), continuation_guard=lambda: 1
    )
    with pytest.raises(httpcore.ConnectError, match="failed"):
        backend.connect_tcp("public.example", 443)


def test_guarded_resolution_deadline() -> None:
    """A stalled resolver must not keep the operation alive past its deadline."""
    with (
        patch("opendle.public_http.threading.Thread"),
        patch("opendle.public_http.threading.Event") as event_factory,
    ):
        event_factory.return_value.is_set.return_value = False
        event_factory.return_value.wait.return_value = False
        backend = PinnedNetworkBackend(continuation_guard=lambda: 0.001)
        with pytest.raises(httpcore.ConnectTimeout, match="deadline"):
            backend.connect_tcp("slow.example", 443)


def test_default_transports_construct_and_close() -> None:
    """The default sync pool is created without a proxy or custom backend."""
    PinnedHTTPTransport().close()


class _AsyncHTTPStream(httpcore.AsyncNetworkStream):
    def __init__(self) -> None:
        self.stream = _HTTPStream()

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:  # noqa: ASYNC109 - Required network API.
        return self.stream.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:  # noqa: ASYNC109 - Required network API.
        self.stream.write(buffer, timeout)

    async def aclose(self) -> None:
        self.stream.close()

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - Required network API.
    ) -> httpcore.AsyncNetworkStream:
        self.stream.start_tls(ssl_context, server_hostname, timeout)
        return self

    def get_extra_info(self, info: str) -> object:
        return self.stream.get_extra_info(info)


def test_async_connections_pin_addresses_and_preserve_tls_identity() -> None:
    """A DNS change cannot direct either async connection to a private IP."""
    stream = _AsyncHTTPStream()

    async def fetch() -> None:
        async with httpx.AsyncClient(
            transport=AsyncPinnedHTTPTransport(), trust_env=False
        ) as client:
            await client.get("https://rebind.example/report")
            with pytest.raises(SSRFError, match="non-public"):
                await client.get("https://rebind.example/next")

    with (
        patch(
            "opendle.public_http.PinnedNetworkBackend.resolve_addresses",
            side_effect=[[_answer()], [_answer("127.0.0.1")]],
        ) as resolver,
        patch(
            "opendle.public_http.AnyIOBackend.connect_tcp",
            new_callable=AsyncMock,
            return_value=stream,
        ) as connect,
    ):
        asyncio.run(fetch())
    assert resolver.call_count == 2  # noqa: PLR2004 - One lookup per new connection.
    connect.assert_awaited_once_with(PUBLIC_IP, 443, 5.0, None, None)
    assert stream.stream.hostname == "rebind.example"
    assert b"Host: rebind.example" in b"".join(stream.stream.writes)
    assert stream.stream.context is not None
    assert stream.stream.context.check_hostname
    assert stream.stream.context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("success", [False, True])
def test_async_public_address_fallback(*, success: bool) -> None:
    """An async connect can use another checked IP after a public route fails."""
    stream = _AsyncHTTPStream()
    outcomes = [
        httpcore.ConnectError(),
        stream if success else httpcore.ConnectTimeout(),
    ]

    async def fetch() -> None:
        async with httpx.AsyncClient(
            transport=AsyncPinnedHTTPTransport(), trust_env=False
        ) as client:
            if success:
                await client.get("https://public.example/report")
            else:
                with pytest.raises(httpx.ConnectError):
                    await client.get("https://public.example/report")

    with (
        patch(
            "opendle.public_http.PinnedNetworkBackend.resolve_addresses",
            return_value=[_answer(), _answer("8.8.8.8")],
        ),
        patch(
            "opendle.public_http.AnyIOBackend.connect_tcp",
            new_callable=AsyncMock,
            side_effect=outcomes,
        ),
    ):
        asyncio.run(fetch())


def test_dns_wait_can_continue_until_resolution_finishes() -> None:
    """A resolver that completes within the deadline is allowed to proceed."""
    with patch("opendle.public_http.threading.Event") as event_factory:
        event = event_factory.return_value
        event.is_set.side_effect = [False, True]
        event.wait.return_value = True
        backend = PinnedNetworkBackend(
            resolver=MagicMock(return_value=[_answer()]),
            socket_factory=MagicMock(return_value=MagicMock(spec=socket.socket)),
            continuation_guard=lambda: None,
        )
        with patch("opendle.public_http.threading.Thread") as thread:

            def run_dns() -> None:
                thread.call_args.kwargs["target"]()

            thread.return_value.start.side_effect = run_dns
            backend.connect_tcp("public.example", 443)
        event.wait.assert_called_once_with(0.1)
