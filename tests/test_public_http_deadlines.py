"""Public connections must release resources when their deadline expires."""

from __future__ import annotations

import asyncio
import socket
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from opendle.public_http import AsyncPinnedHTTPTransport, PinnedNetworkBackend

ANSWERS: list[tuple[int, int, int, str, tuple[str, int]]] = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
]


def test_expired_guard_does_not_allocate_socket() -> None:
    """A deadline failure before connect must not leave an unused socket open."""
    factory = MagicMock()
    backend = PinnedNetworkBackend(
        socket_factory=factory,
        continuation_guard=MagicMock(side_effect=RuntimeError("deadline expired")),
    )
    with (
        patch.object(backend, "resolve_addresses", return_value=ANSWERS),
        pytest.raises(RuntimeError, match="deadline expired"),
    ):
        backend.connect_tcp("public.example", 443)
    factory.assert_not_called()


def test_async_connect_timeout_includes_dns() -> None:
    """A slow DNS worker must not escape HTTPX's connection timeout."""
    release = threading.Event()
    timer = threading.Timer(0.5, release.set)

    def resolve(
        host: str, port: int
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        del host, port
        release.wait()
        return ANSWERS

    async def fetch() -> None:
        async with httpx.AsyncClient(
            transport=AsyncPinnedHTTPTransport(), trust_env=False, timeout=0.01
        ) as client:
            with pytest.raises(httpx.ConnectTimeout):
                await client.get("https://public.example/image")

    with (
        patch.object(PinnedNetworkBackend, "resolve_addresses", side_effect=resolve),
        patch(
            "opendle.public_http.AnyIOBackend.connect_tcp",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("DNS escaped the deadline"),
        ) as connect,
    ):
        timer.start()
        try:
            asyncio.run(fetch())
        finally:
            release.set()
            timer.cancel()
        connect.assert_not_called()
