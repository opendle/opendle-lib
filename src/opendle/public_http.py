"""Public-only HTTPX transports with DNS addresses fixed at connection time.

Install ``opendle-lib[public-http]`` and set ``trust_env=False`` on the client.
Each new connection validates every DNS answer before it opens a socket.
The original URL still controls Host, TLS SNI, certificate checks and pooling.
Callers own redirect limits, content rules, byte limits and operation deadlines.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from builtins import TimeoutError as SocketTimeoutError
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING

import httpcore
import httpx
from anyio import fail_after
from anyio.to_thread import run_sync
from httpcore import (
    SOCKET_OPTION,
    ConnectError,
    ConnectTimeout,
    NetworkBackend,
    NetworkStream,
)
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.sync import SyncStream

if TYPE_CHECKING:
    import ssl

__all__ = [
    "AsyncPinnedHTTPTransport",
    "ContinuationGuard",
    "DeadlineNetworkStream",
    "PinnedHTTPTransport",
    "PinnedNetworkBackend",
    "SSRFError",
    "is_private_ip",
]


class SSRFError(Exception):
    """Reject a connection whose destination is not a public IP address."""


def is_private_ip(ip_str: str) -> bool:
    """Return true for invalid, private, reserved or non-global addresses."""
    try:
        address = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return not address.is_global or address.is_multicast or address.is_reserved


def _validate_answers(
    answers: Sequence[tuple[int, int, int, str, tuple[object, ...]]],
) -> None:
    if not answers:
        message = "DNS resolution returned no addresses"
        raise ConnectError(message)
    for _family, _socktype, _proto, _canonname, sockaddr in answers:
        if (
            not sockaddr
            or not isinstance(sockaddr[0], str)
            or is_private_ip(sockaddr[0])
        ):
            message = "DNS resolution included a non-public address"
            raise SSRFError(message)


ContinuationGuard = Callable[[], float | None]


class DeadlineNetworkStream(NetworkStream):
    """Clamp each blocking socket operation to a caller-owned deadline."""

    def __init__(
        self, stream: NetworkStream, continuation_guard: ContinuationGuard
    ) -> None:
        """Wrap a socket stream with the caller's continuation guard."""
        self._stream = stream
        self._continuation_guard = continuation_guard

    def _timeout(self, timeout: float | None) -> float | None:
        remaining = self._continuation_guard()
        if isinstance(remaining, int | float) and not isinstance(remaining, bool):
            return (
                max(0.001, remaining)
                if timeout is None
                else max(0.001, min(timeout, remaining))
            )
        return timeout

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        """Read with the remaining operation timeout."""
        return self._stream.read(max_bytes, timeout=self._timeout(timeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        """Write with the remaining operation timeout."""
        self._stream.write(buffer, timeout=self._timeout(timeout))

    def close(self) -> None:
        """Close the wrapped socket stream."""
        self._stream.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> NetworkStream:
        """Start TLS with the original hostname and remaining timeout."""
        stream = self._stream.start_tls(
            ssl_context,
            server_hostname=server_hostname,
            timeout=self._timeout(timeout),
        )
        return DeadlineNetworkStream(stream, self._continuation_guard)

    def get_extra_info(self, info: str) -> object:
        """Return connection metadata from the wrapped stream."""
        return self._stream.get_extra_info(info)


def _set_socket_options(
    sock: socket.socket, options: Iterable[SOCKET_OPTION] | None
) -> None:
    for option in options or ():
        if len(option) == 3:  # noqa: PLR2004 - The socket API has two tuple forms.
            sock.setsockopt(option[0], option[1], option[2])
        else:
            sock.setsockopt(option[0], option[1], option[2], option[3])


class PinnedNetworkBackend(NetworkBackend):
    """Resolve a hostname once, validate every answer, then connect by sockaddr."""

    def __init__(
        self,
        *,
        resolver: Callable[
            [str, int, int, int],
            Sequence[tuple[int, int, int, str, tuple[object, ...]]],
        ] = socket.getaddrinfo,
        socket_factory: Callable[[int, int, int], socket.socket] = socket.socket,
        continuation_guard: ContinuationGuard | None = None,
    ) -> None:
        """Use the supplied DNS, socket and optional operation-deadline hooks."""
        self._resolver = resolver
        self._socket_factory = socket_factory
        self._continuation_guard = continuation_guard

    def _timeout(self, timeout: float | None) -> float | None:
        if self._continuation_guard is None:
            return timeout
        remaining = self._continuation_guard()
        if isinstance(remaining, int | float) and not isinstance(remaining, bool):
            return (
                max(0.001, remaining)
                if timeout is None
                else max(0.001, min(timeout, remaining))
            )
        return timeout

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> NetworkStream:
        """Connect directly to one answer from a single validated resolution."""
        answers = self.resolve_addresses(host, port)
        _validate_answers(answers)

        last_error: OSError | None = None
        timed_out = False
        for family, socktype, proto, _canonname, sockaddr in answers:
            connect_timeout = self._timeout(timeout)
            sock: socket.socket | None = None
            try:
                sock = self._socket_factory(family, socktype, proto)
                sock.settimeout(connect_timeout)
                if local_address is not None:
                    bind_address = (
                        (local_address, 0, 0, 0)
                        if family == socket.AF_INET6
                        else (local_address, 0)
                    )
                    sock.bind(bind_address)
                _set_socket_options(sock, socket_options)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.connect(sockaddr)
            except SocketTimeoutError as exc:
                timed_out = True
                last_error = exc
                if sock is not None:
                    sock.close()
                continue
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    sock.close()
                continue
            stream: NetworkStream = SyncStream(sock)
            if self._continuation_guard is not None:
                stream = DeadlineNetworkStream(stream, self._continuation_guard)
            return stream
        if timed_out:
            message = "All validated addresses timed out"
            raise ConnectTimeout(message) from last_error
        message = "Could not connect to any validated address"
        raise ConnectError(message) from last_error

    def resolve_addresses(
        self,
        host: str,
        port: int,
    ) -> Sequence[tuple[int, int, int, str, tuple[object, ...]]]:
        """Resolve cooperatively so DNS cannot escape the caller's deadline."""
        if self._continuation_guard is None:
            try:
                return self._resolver(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            except socket.gaierror as exc:
                message = "DNS resolution failed"
                raise ConnectError(message) from exc

        finished = threading.Event()
        result: list[tuple[int, int, int, str, tuple[object, ...]]] = []
        failure: list[BaseException] = []

        def resolve() -> None:
            try:
                result.extend(
                    self._resolver(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                )
            except BaseException as exc:  # noqa: BLE001 - The resolver thread must report every failure.
                failure.append(exc)
            finally:
                finished.set()

        threading.Thread(
            target=resolve, name="pinned-dns-resolver", daemon=True
        ).start()
        while not finished.is_set():
            remaining = self._continuation_guard()
            wait_for = (
                min(0.1, remaining) if isinstance(remaining, int | float) else 0.1
            )
            if wait_for <= 0 or (
                not finished.wait(wait_for)
                and isinstance(remaining, int | float)
                and remaining <= wait_for
            ):
                message = "DNS resolution exceeded the fetch deadline"
                raise ConnectTimeout(message)
        self._continuation_guard()
        if failure:
            if isinstance(failure[0], socket.gaierror):
                message = "DNS resolution failed"
                raise ConnectError(message) from failure[0]
            message = "DNS resolver failed"
            raise ConnectError(message) from failure[0]
        return result

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> NetworkStream:
        """Reject local Unix sockets for public-web fetching."""
        del path, timeout, socket_options
        message = "Unix sockets are disabled for pinned public fetching"
        raise ConnectError(message)


class PinnedHTTPTransport(httpx.HTTPTransport):
    """httpx transport backed by a single-resolution public-only connector."""

    def __init__(
        self,
        backend: NetworkBackend | None = None,
        *,
        continuation_guard: ContinuationGuard | None = None,
    ) -> None:
        """Build a verified TLS pool around the supplied public backend."""
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
            network_backend=backend
            or PinnedNetworkBackend(continuation_guard=continuation_guard),
        )


class _AsyncPinnedNetworkBackend(AnyIOBackend):
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - Required httpcore backend signature.
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            with fail_after(timeout):
                answers = await run_sync(
                    PinnedNetworkBackend().resolve_addresses,
                    host,
                    port,
                    abandon_on_cancel=True,
                )
                _validate_answers(answers)
                last_error: ConnectError | ConnectTimeout | None = None
                for _family, _socktype, _proto, _canonname, sockaddr in answers:
                    try:
                        return await super().connect_tcp(
                            str(sockaddr[0]),
                            port,
                            timeout,
                            local_address,
                            socket_options,
                        )
                    except (ConnectError, ConnectTimeout) as exc:
                        last_error = exc
        except TimeoutError as exc:
            message = "DNS resolution or connection exceeded the timeout"
            raise ConnectTimeout(message) from exc
        message = "Could not connect to any validated address"
        raise ConnectError(message) from last_error


class AsyncPinnedHTTPTransport(httpx.AsyncHTTPTransport):
    """Public-only async transport with original URL and TLS identity.

    Each new connection resolves its hostname in a worker thread, validates
    every answer and connects to a numeric public IP. The caller must set
    ``trust_env=False`` on the client to prevent environment proxy routing.
    """

    def __init__(self) -> None:
        """Build a verified TLS pool with a public-only connection backend."""
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=_AsyncPinnedNetworkBackend(),
        )
