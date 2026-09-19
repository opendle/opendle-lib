"""Submit one Higgsfield image request and poll with caller-owned HTTPX transport.

Install ``opendle-lib[higgsfield]``. The caller owns model parameters, download
policy, and HTTP transport. This module never retries a generation submission.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Literal, cast

import httpx

__all__ = [
    "HiggsfieldError",
    "HiggsfieldGenerationError",
    "HiggsfieldImageResult",
    "HiggsfieldProtocolError",
    "HiggsfieldTimeoutError",
    "generate_image",
]


class HiggsfieldError(Exception):
    """Base class for safe Higgsfield lifecycle errors."""


class HiggsfieldProtocolError(HiggsfieldError):
    """The provider returned an invalid lifecycle response."""


class HiggsfieldGenerationError(HiggsfieldError):
    """A generation reached a terminal failure or moderation state."""

    def __init__(
        self, status: Literal["failed", "nsfw", "canceled"], request_id: str
    ) -> None:
        """Retain the terminal state without including provider error text."""
        self.status = status
        self.request_id = request_id
        super().__init__(f"Higgsfield generation ended with status {status}.")


class HiggsfieldTimeoutError(HiggsfieldError):
    """The polling deadline expired; an accepted request may still complete."""

    def __init__(self, request_id: str | None, status_url: str | None) -> None:
        """Retain request metadata so a caller can recover the accepted request."""
        self.request_id = request_id
        self.status_url = status_url
        super().__init__("Higgsfield generation exceeded its deadline.")


@dataclass(frozen=True)
class HiggsfieldImageResult:
    """Completed image URLs, request identity, and the provider response."""

    image_urls: tuple[str, ...]
    request_id: str
    payload: dict[str, object] = field(repr=False)


def _url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        message = "Invalid Higgsfield URL."
        raise HiggsfieldProtocolError(message) from None
    if url.scheme != "https" or not url.host or url.userinfo or url.fragment:
        message = "Invalid Higgsfield URL."
        raise HiggsfieldProtocolError(message)
    return url


def _status_url(value: object, endpoint: httpx.URL) -> str:
    if not isinstance(value, str):
        message = "Missing Higgsfield status URL."
        raise HiggsfieldProtocolError(message)
    url = _url(value)
    if (url.scheme, url.host, url.port) != (
        endpoint.scheme,
        endpoint.host,
        endpoint.port,
    ):
        message = "Higgsfield status URL has a different origin."
        raise HiggsfieldProtocolError(message)
    return str(url)


def _payload(response: httpx.Response) -> dict[str, object]:
    response.raise_for_status()
    try:
        value: object = response.json()
    except ValueError:
        message = "Invalid Higgsfield JSON response."
        raise HiggsfieldProtocolError(message) from None
    if not isinstance(value, dict):
        message = "Invalid Higgsfield response object."
        raise HiggsfieldProtocolError(message)
    return cast("dict[str, object]", value)


def _result(payload: dict[str, object], request_id: str) -> HiggsfieldImageResult:
    images = payload.get("images")
    if not isinstance(images, list) or not images:
        message = "Missing Higgsfield image output."
        raise HiggsfieldProtocolError(message)
    urls: list[str] = []
    for image in cast("list[object]", images):
        if not isinstance(image, dict):
            message = "Invalid Higgsfield image output."
            raise HiggsfieldProtocolError(message)
        url = cast("dict[str, object]", image).get("url")
        if not isinstance(url, str):
            message = "Invalid Higgsfield image URL."
            raise HiggsfieldProtocolError(message)
        urls.append(str(_url(url)))
    return HiggsfieldImageResult(tuple(urls), request_id, payload)


def _endpoint(
    endpoint: str, model: str, timeout: float, poll_interval: float
) -> httpx.URL:
    if not all(
        math.isfinite(value) and value > 0 for value in (timeout, poll_interval)
    ):
        msg = "Timeout and poll interval must be positive and finite."
        raise ValueError(msg)
    base = _url(endpoint.rstrip("/"))
    if base.query or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*", model):
        msg = "Invalid Higgsfield endpoint or model."
        raise ValueError(msg)
    if any(part in {".", ".."} for part in model.split("/")):
        msg = "Invalid Higgsfield model path."
        raise ValueError(msg)
    return base


def generate_image(  # noqa: PLR0913
    client: httpx.Client,
    *,
    api_key: str,
    model: str,
    arguments: dict[str, object],
    endpoint: str = "https://api.higgsfield.ai",
    timeout: float = 300.0,
    poll_interval: float = 2.0,
) -> HiggsfieldImageResult:
    """Submit once and poll until images, a terminal failure, or the deadline.

    The combined key uses ``KEY_ID:KEY_SECRET``. Status URLs must have the
    configured HTTPS origin. Requests disable redirects and client-level auth.
    HTTPX errors propagate without retry; callers must not resubmit after an
    uncertain transport error. Each network phase is limited to 30 seconds or
    the remaining deadline. The deadline is checked between requests.
    """
    base = _endpoint(endpoint, model, timeout, poll_interval)
    headers = {"Authorization": f"Key {api_key}"}
    deadline = time.monotonic() + timeout
    request_id: str | None = None
    status_url: str | None = None
    method = "POST"
    url = f"{base}/{model}"
    body: dict[str, object] | None = arguments
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HiggsfieldTimeoutError(request_id, status_url)
        payload = _payload(
            client.request(
                method,
                url,
                headers=headers,
                json=body,
                timeout=min(30.0, remaining),
                follow_redirects=False,
                auth=None,
            )
        )
        current_id = payload.get("request_id")
        if not isinstance(current_id, str) or not current_id:
            message = "Missing Higgsfield request ID."
            raise HiggsfieldProtocolError(message)
        if request_id is not None and current_id != request_id:
            message = "Higgsfield request ID changed during polling."
            raise HiggsfieldProtocolError(message)
        request_id = current_id
        status = payload.get("status")
        if status == "completed":
            return _result(payload, request_id)
        if status in ("failed", "nsfw", "canceled"):
            raise HiggsfieldGenerationError(status, request_id)
        if status not in ("queued", "in_progress"):
            message = "Unknown Higgsfield request status."
            raise HiggsfieldProtocolError(message)
        if status_url is None:
            status_url = _status_url(payload.get("status_url"), base)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HiggsfieldTimeoutError(request_id, status_url)
        time.sleep(min(poll_interval, remaining))
        method, url, body = "GET", status_url, None
