"""Higgsfield lifecycle, credential routing, and failure tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

import httpx
import pytest

from opendle.higgsfield import (
    HiggsfieldGenerationError,
    HiggsfieldProtocolError,
    HiggsfieldTimeoutError,
    generate_image,
)

_API = "https://api.higgsfield.ai"
_STATUS = f"{_API}/requests/job/status"
_COMPLETE: dict[str, object] = {
    "request_id": "job",
    "status": "completed",
    "images": [{"url": "https://cdn.example.com/image.jpg"}],
}
_PENDING: dict[str, object] = {
    "request_id": "job",
    "status": "queued",
    "status_url": _STATUS,
}


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Run polling against a deterministic monotonic clock."""
    now = [0.0]
    monkeypatch.setattr("opendle.higgsfield.time.monotonic", lambda: now[0])

    def sleep(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr("opendle.higgsfield.time.sleep", sleep)
    return now


def _client(payloads: list[object], requests: list[httpx.Request]) -> httpx.Client:
    values: Iterator[object] = iter(payloads)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=next(values))

    return httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)


def test_generate_polls_without_resubmission() -> None:
    """One POST preserves arguments and GET polls return image output."""
    requests: list[httpx.Request] = []
    pending = {**_PENDING, "status": "in_progress"}
    with _client([_PENDING, pending, _COMPLETE], requests) as client:
        client.auth = httpx.BasicAuth("unrelated", "credential")
        result = generate_image(
            client,
            api_key="test:key",
            model="higgsfield-ai/soul/v2/standard",
            arguments={"prompt": "Alpine lake", "resolution": "720p", "batch_size": 1},
        )
    assert result.request_id == "job"
    assert result.image_urls == ("https://cdn.example.com/image.jpg",)
    assert result.payload == _COMPLETE
    assert "payload=" not in repr(result)
    assert [request.method for request in requests] == ["POST", "GET", "GET"]
    assert [str(request.url) for request in requests] == [
        f"{_API}/higgsfield-ai/soul/v2/standard",
        _STATUS,
        _STATUS,
    ]
    assert all(
        request.headers["authorization"] == "Key test:key" for request in requests
    )
    assert requests[0].content == (
        b'{"prompt":"Alpine lake","resolution":"720p","batch_size":1}'
    )
    assert requests[1].content == b""
    assert requests[0].extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 30.0
    )


@pytest.mark.parametrize("status", ["failed", "nsfw", "canceled"])
def test_terminal_failure_is_safe(status: str) -> None:
    """Terminal failures retain identity but omit provider content."""
    with (
        _client(
            [{**_PENDING, "status": status, "error": "private body"}], []
        ) as client,
        pytest.raises(HiggsfieldGenerationError) as caught,
    ):
        generate_image(client, api_key="test:key", model="soul", arguments={})
    assert caught.value.status == status
    assert caught.value.request_id == "job"
    assert "private body" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {**_PENDING, "request_id": None},
        {**_PENDING, "request_id": ""},
        {**_PENDING, "status": "unknown"},
        {**_PENDING, "status_url": None},
        {**_COMPLETE, "images": None},
        {**_COMPLETE, "images": []},
        {**_COMPLETE, "images": [None]},
        {**_COMPLETE, "images": [{"url": None}]},
        {**_COMPLETE, "images": [{"url": ""}]},
    ],
)
def test_malformed_response(payload: object) -> None:
    """Malformed provider responses raise a safe protocol error."""
    with (
        _client([payload], []) as client,
        pytest.raises(HiggsfieldProtocolError),
    ):
        generate_image(client, api_key="test:key", model="soul", arguments={})


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/status",
        "http://api.higgsfield.ai/status",
        "https://api.higgsfield.ai:444/status",
        "https://user:password@api.higgsfield.ai/status",
        "https://api.higgsfield.ai/status#fragment",
        "/status",
        "https://[invalid/status",
        "https://api.higgsfield.ai:invalid/status",
    ],
)
def test_invalid_poll_destination_is_never_contacted(url: str) -> None:
    """Never send credentials to an unapproved origin or URL."""
    requests: list[httpx.Request] = []
    with (
        _client([{**_PENDING, "status_url": url}], requests) as client,
        pytest.raises(HiggsfieldProtocolError),
    ):
        generate_image(client, api_key="test:key", model="soul", arguments={})
    assert len(requests) == 1


def test_request_identity_cannot_change() -> None:
    """Reject output that belongs to another job."""
    with (
        _client([_PENDING, {**_COMPLETE, "request_id": "other"}], []) as client,
        pytest.raises(HiggsfieldProtocolError, match="request ID changed"),
    ):
        generate_image(client, api_key="test:key", model="soul", arguments={})


@pytest.mark.parametrize("code", [301, 401, 403, 422, 500])
def test_http_errors_and_redirects_are_not_retried(code: int) -> None:
    """HTTP errors propagate, including redirects with a client that follows them."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(code, headers={"Location": "https://evil.example"})

    with (
        httpx.Client(
            transport=httpx.MockTransport(handle), follow_redirects=True
        ) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        generate_image(client, api_key="test:key", model="soul", arguments={})
    assert len(requests) == 1


def test_invalid_json_is_safe() -> None:
    """Invalid JSON does not expose response text in the protocol error."""
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text="private"))
        ) as client,
        pytest.raises(HiggsfieldProtocolError, match="Invalid Higgsfield JSON"),
    ):
        generate_image(client, api_key="test:key", model="soul", arguments={})


def test_ambiguous_submission_is_not_retried() -> None:
    """A transport timeout may hide accepted work, so the client must stop."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        message = "timeout"
        raise httpx.ReadTimeout(message)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(httpx.ReadTimeout),
    ):
        generate_image(client, api_key="test:key", model="soul", arguments={})
    assert len(requests) == 1


@pytest.mark.parametrize("slow_response", [False, True])
def test_deadline_preserves_accepted_request(
    clock: list[float], *, slow_response: bool
) -> None:
    """Stop polling at the deadline and retain recovery metadata."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if slow_response:
            clock[0] += 2
        return httpx.Response(200, json=_PENDING)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(HiggsfieldTimeoutError) as caught,
    ):
        generate_image(
            client, api_key="test:key", model="soul", arguments={}, timeout=1
        )
    assert caught.value.request_id == "job"
    assert caught.value.status_url == _STATUS
    assert len(requests) == 1
    assert cast("dict[str, float]", requests[0].extensions["timeout"])["read"] == 1.0


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_deadline_and_interval(value: float) -> None:
    """Reject unbounded or nonpositive timing parameters before submission."""
    with _client([], []) as client:
        with pytest.raises(ValueError, match="positive and finite"):
            generate_image(
                client, api_key="key", model="soul", arguments={}, timeout=value
            )
        with pytest.raises(ValueError, match="positive and finite"):
            generate_image(
                client, api_key="key", model="soul", arguments={}, poll_interval=value
            )


@pytest.mark.parametrize(
    ("endpoint", "model"),
    [
        (f"{_API}?query", "soul"),
        (_API, "../soul"),
        (_API, "soul/../standard"),
        (_API, "soul/./standard"),
        (_API, "https://evil.example"),
    ],
)
def test_invalid_endpoint_and_model(endpoint: str, model: str) -> None:
    """Reject model paths that change the configured destination."""
    with (
        _client([], []) as client,
        pytest.raises(ValueError, match="Invalid Higgsfield"),
    ):
        generate_image(
            client, api_key="key", model=model, arguments={}, endpoint=endpoint
        )
