"""Tests for the official dependency-free Router client."""

from __future__ import annotations

import asyncio
import json
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit
from urllib.request import ProxyHandler

import pytest

import opendle
from opendle import (
    AssignmentDefinitionKind,
    AssignmentSelector,
    CallFailurePhase,
    ContextLimits,
    ContextMethod,
    ContextPolicy,
    ConversationHarness,
    ConversationState,
    ExactModelSelector,
    ImageInputPart,
    InputModality,
    MediaJobState,
    MediaKind,
    ModelCall,
    ModelCallError,
    ModelCallResult,
    ModelCapability,
    ModelStreamCompleted,
    ModelStreamStart,
    ModelStreamTextDelta,
    ModelStreamToolCall,
    ObservedRequirement,
    OutputModality,
    ReasoningLevel,
    RouterAPIError,
    RouterAuthenticationError,
    RouterAuthorizationError,
    RouterClient,
    RouterConflictError,
    RouterContentUnavailableError,
    RouterContractError,
    RouterInternalError,
    RouterNotFoundError,
    RouterPageLimitError,
    RouterProtocolError,
    RouterRateLimitError,
    RouterResponseLimitError,
    RouterStreamError,
    RouterStreamResponse,
    RouterTransportError,
    RouterTransportResponse,
    RouterUnavailableError,
    RouterUpstreamError,
    RouterValidationError,
    RouteState,
    StatisticsDimension,
    StructuredModelCallResult,
    SystemMessage,
    TextInputPart,
    TextOutputPart,
    ToolCallPart,
    UsageUnit,
    UserMessage,
)
from opendle import router_client as router_client_module

if TYPE_CHECKING:
    from collections.abc import Callable, ItemsView, Iterable, Iterator

type PrivateFunction = Callable[..., object]

_SERVICE_KEY = "test-only-router-service-key-with-256-bits-placeholder"
_TIME = "2026-08-24T00:00:00Z"
_NEXT_TIME = "2026-08-25T00:00:00Z"
_EXPECTED_ATTEMPTS = 2
_EXPECTED_CALL_TIMEOUT = 4.5
_FORBIDDEN_STATUS = 403
_EXPECTED_PAGE_COUNT = 2


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """Contain one exact fake-Router request."""

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    timeout: float


class FakeRouterTransport:
    """Return queued native responses and record exact requests."""

    def __init__(
        self,
        responses: list[RouterTransportResponse] | None = None,
        streams: list[RouterStreamResponse] | None = None,
    ) -> None:
        """Initialize queued complete and streaming responses."""
        self.responses = list(responses or [])
        self.streams = list(streams or [])
        self.calls: list[RecordedRequest] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RouterTransportResponse:
        """Record one complete request and return its queued response."""
        self.calls.append(RecordedRequest(method, url, dict(headers), body, timeout))
        return self.responses.pop(0)

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> RouterStreamResponse:
        """Record one stream request and return its queued response."""
        self.calls.append(RecordedRequest(method, url, dict(headers), body, timeout))
        return self.streams.pop(0)


class FailingRouterTransport:
    """Raise one unsafe transport error for each operation."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RouterTransportResponse:
        """Raise an error that contains the private service key."""
        del method, url, body, timeout
        msg = f"failed with {headers['Authorization']}"
        raise OSError(msg)


class BrokenHeaderMapping(Mapping[str, str]):
    """Expose one mapping that fails while it returns header items."""

    def __getitem__(self, key: str) -> str:
        """Reject direct access."""
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        """Return one empty key iterator."""
        return iter(())

    def __len__(self) -> int:
        """Return an empty nominal size."""
        return 0

    def items(self) -> ItemsView[str, str]:
        """Simulate one invalid custom mapping implementation."""
        raise TypeError

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> RouterStreamResponse:
        """Raise an error that contains the private service key."""
        del method, url, body, timeout
        msg = f"failed with {headers['Authorization']}"
        raise OSError(msg)


def response(
    status: int, value: object, *, media_type: str = "application/json"
) -> RouterTransportResponse:
    """Build one complete fake-Router response."""
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    return RouterTransportResponse(status, {"Content-Type": media_type}, body)


def stream(*events: tuple[str, object]) -> RouterStreamResponse:
    """Build one native SSE response with split transport chunks."""
    body = b"".join(
        f"event: {name}\ndata: {json.dumps(value)}\n\n".encode()
        for name, value in events
    )
    split = max(1, len(body) // 3)
    return RouterStreamResponse(
        200,
        {"content-type": "text/event-stream; charset=utf-8"},
        (body[:split], body[split : split * 2], body[split * 2 :]),
    )


def client(transport: object, *, maximum: int = 16 * 1024 * 1024) -> RouterClient:
    """Build one service-scoped client with a custom transport."""
    return RouterClient(
        base_url="http://localhost:8000/",
        service_key=_SERVICE_KEY,
        timeout=4.5,
        maximum_success_response_bytes=maximum,
        transport=transport,  # type: ignore[arg-type]
    )


def page(items: list[object], *, more: bool = False) -> dict[str, object]:
    """Build one native cursor page."""
    page_value: dict[str, object] = {"has_more": more}
    if more:
        page_value["next_cursor"] = "next"
    return {"items": items, "page": page_value}


def usage() -> dict[str, object]:
    """Build one complete native usage value."""
    return {
        "units": [{"unit": "input_token", "quantity": "2"}],
        "cost": "0.01",
        "currency": "EUR",
    }


def workspace(name: str = "main") -> dict[str, object]:
    """Build one native workspace."""
    return {"api_name": name, "display_name": name.title(), "created_at": _TIME}


def assignment(kind: str = "direct_chain") -> dict[str, object]:
    """Build one native assignment with all applicable optional data."""
    value: dict[str, object] = {
        "api_name": "workflow.main",
        "display_name": "Workflow",
        "definition_kind": kind,
        "defined_by_service_api_name": "service-a",
        "effective_chain": [{"provider_model_api_name": "route-a"}],
        "observed_requirements": ["text_input", "tool_calling"],
        "reasoning_level": "medium",
        "last_used_at": _TIME,
        "created_at": _TIME,
    }
    if kind == "direct_chain":
        value["direct_chain"] = [{"provider_model_api_name": "route-a"}]
    elif kind == "inherited_assignment":
        value["inherits_assignment_api_name"] = "default"
    return value


def invalid_assignment(**updates: object) -> dict[str, object]:
    """Build one assignment response with selected invalid fields."""
    value = assignment()
    value.update(updates)
    return value


def updated(value: dict[str, object], **updates: object) -> dict[str, object]:
    """Copy one native response fixture and replace selected fields."""
    result = dict(value)
    result.update(updates)
    return result


def service_key(*, include_last_used: bool = True) -> dict[str, object]:
    """Build safe service-key metadata."""
    value: dict[str, object] = {"id": "key-1", "name": "worker", "created_at": _TIME}
    if include_last_used:
        value["last_used_at"] = _TIME
    return value


def provider_model() -> dict[str, object]:
    """Build one service-safe provider-model discovery value."""
    return {
        "api_name": "route-a",
        "display_name": "Route A",
        "input_modalities": ["text", "image"],
        "output_modalities": ["text", "embedding", "image"],
        "capabilities": ["tool_calling", "streaming", "reasoning"],
        "constraints": {
            "embedding_dimensions": [2],
            "max_input_images": 8,
            "max_input_image_bytes": 20_971_520,
            "max_output_duration_seconds": 60,
        },
        "effective_price": {
            "currency": "EUR",
            "unit_prices": [{"unit": "request", "amount": "0.1"}],
            "source": "manual",
            "synchronized_at": _TIME,
        },
    }


def media_job(state: str = "succeeded") -> dict[str, object]:
    """Build one native media job."""
    value: dict[str, object] = {
        "id": "job-1",
        "workspace_api_name": "main",
        "provider_model_api_name": "route-a",
        "kind": "image",
        "state": state,
        "created_at": _TIME,
    }
    if state == "succeeded":
        value["content"] = {"media_type": "image/png", "size_bytes": 3}
        value["completed_at"] = _TIME
    if state == "failed":
        value["error"] = {"code": "upstream_failed", "message": "Generation failed."}
        value["completed_at"] = _TIME
    return value


def model_call(*, structured: bool = False) -> ModelCall:
    """Build one exact native model call."""
    return ModelCall(
        "main",
        ExactModelSelector("route-a"),
        (SystemMessage("System."),),
        output_schema_json='{"type":"object"}' if structured else None,
        output_limit=20,
        temperature=0.2,
    )


def private(name: str) -> PrivateFunction:
    """Return one private parser for direct defensive-contract tests."""
    return cast("PrivateFunction", getattr(router_client_module, name))


def test_complete_native_service_key_contract_and_types() -> None:  # noqa: PLR0915
    """Each native service-key operation has a typed client method."""
    statistics = {
        "from": _TIME,
        "to": "2026-08-25T00:00:00Z",
        "group_by": ["workspace", "tag"],
        "buckets": [
            {
                "dimensions": ["main", "agent"],
                "calls": 1,
                "attempts": 2,
                "units": [{"unit": "request", "quantity": "2"}],
                "cost": "0.2",
                "currency": "EUR",
            }
        ],
    }
    transport = FakeRouterTransport(
        [
            response(200, page([workspace()])),
            response(201, workspace()),
            response(200, workspace()),
            response(204, b"", media_type=""),
            response(200, page([assignment()])),
            response(200, assignment("inherited_assignment")),
            response(200, assignment("inherited_assignment")),
            response(200, assignment()),
            response(204, b"", media_type=""),
            response(204, b"", media_type=""),
            response(200, page([service_key()])),
            response(
                201, {"key": service_key(include_last_used=False), "secret": "s" * 32}
            ),
            response(204, b"", media_type=""),
            response(200, page([provider_model()])),
            response(
                200,
                {
                    "output_type": "standard",
                    "provider_model_api_name": "route-a",
                    "content": [
                        {"type": "text", "text": "Done."},
                        {
                            "type": "tool_call",
                            "id": "call-1",
                            "name": "lookup",
                            "arguments_json": "{}",
                        },
                    ],
                    "usage": usage(),
                },
            ),
            response(
                200,
                {
                    "output_type": "structured_json",
                    "provider_model_api_name": "route-a",
                    "structured_output_json": "{}",
                    "usage": usage(),
                },
            ),
            response(
                200,
                {
                    "provider_model_api_name": "route-a",
                    "embeddings": [
                        {"index": 0, "values": [1, 2.5]},
                        {"index": 1, "values": [3.0, 4]},
                    ],
                    "usage": usage(),
                },
            ),
            response(202, media_job()),
            response(200, media_job("failed")),
            response(200, media_job()),
            response(200, b"png", media_type="image/png"),
            response(200, statistics),
        ]
    )
    subject = client(transport)

    assert subject.list_workspaces().items[0].api_name == "main"
    assert subject.create_workspace("main", "Main").display_name == "Main"
    assert subject.get_workspace("main").created_at == _TIME
    subject.delete_workspace("main")
    assignments = subject.list_assignments().items
    assert assignments[0].definition_kind is AssignmentDefinitionKind.DIRECT_CHAIN
    assert assignments[0].observed_requirements == (
        ObservedRequirement.TEXT_INPUT,
        ObservedRequirement.TOOL_CALLING,
    )
    assert (
        subject.get_assignment("workflow.main").inherits_assignment_api_name
        == "default"
    )
    assert (
        subject.put_assignment(
            "workflow.main",
            inherits_assignment_api_name="default",
            display_name="Workflow",
            reasoning_level=ReasoningLevel.MEDIUM,
        ).definition_kind
        is AssignmentDefinitionKind.INHERITED_ASSIGNMENT
    )
    assert (
        subject.put_assignment("workflow.main", direct_chain=("route-a",)).direct_chain
        is not None
    )
    subject.delete_assignment("workflow.main")
    subject.remove_observed_assignment_requirement(
        "workflow.main", ObservedRequirement.TEXT_INPUT
    )
    assert subject.list_service_keys().items[0].last_used_at == _TIME
    created = subject.create_service_key("worker")
    assert created.key.last_used_at is None
    assert created.secret == "s" * 32
    assert "s" * 32 not in repr(created)
    subject.revoke_service_key("key-1")
    available = subject.list_provider_models().items[0]
    assert available.input_modalities == (InputModality.TEXT, InputModality.IMAGE)
    assert available.output_modalities == (
        OutputModality.TEXT,
        OutputModality.EMBEDDING,
        OutputModality.IMAGE,
    )
    assert available.capabilities == (
        ModelCapability.TOOL_CALLING,
        ModelCapability.STREAMING,
        ModelCapability.REASONING,
    )
    assert available.constraints is not None
    assert available.constraints.embedding_dimensions == (2,)
    assert available.effective_price is not None
    assert available.effective_price.unit_prices[0].unit is UsageUnit.REQUEST

    standard = subject.model_call(model_call())
    assert isinstance(standard, ModelCallResult)
    assert standard.content == (
        TextOutputPart("Done."),
        ToolCallPart("call-1", "lookup", "{}"),
    )
    structured = subject.model_call(model_call(structured=True))
    assert isinstance(structured, StructuredModelCallResult)
    assert structured.structured_output_json == "{}"
    embedding = subject.create_embedding(
        "main", AssignmentSelector("embedding"), ("one", "two"), tags=("agent",)
    )
    assert embedding.route == ExactModelSelector("route-a")
    assert embedding.embeddings[1].values == (3.0, 4.0)
    job = subject.create_media_job(
        "main",
        AssignmentSelector("image"),
        MediaKind.IMAGE,
        "Draw it.",
        input_images=(ImageInputPart("image/png", b"png"),),
        tags=("agent",),
    )
    assert job.state is MediaJobState.SUCCEEDED
    assert subject.get_media_job("job-1").error is not None
    assert subject.wait_media_job("job-1", timeout=0).content is not None
    content = subject.get_media_job_content("job-1")
    assert (content.media_type, content.data) == ("image/png", b"png")
    result = subject.get_statistics(
        _TIME,
        "2026-08-25T00:00:00Z",
        workspace="main",
        assignment="workflow.main",
        provider_model="route-a",
        outcome="succeeded",
        tag="agent",
        group_by=(StatisticsDimension.WORKSPACE, StatisticsDimension.TAG),
    )
    assert result.buckets[0].attempts == _EXPECTED_ATTEMPTS

    paths = [urlsplit(call.url).path for call in transport.calls]
    assert paths == [
        "/v1/workspaces",
        "/v1/workspaces",
        "/v1/workspaces/main",
        "/v1/workspaces/main",
        "/v1/assignments",
        "/v1/assignments/workflow.main",
        "/v1/assignments/workflow.main",
        "/v1/assignments/workflow.main",
        "/v1/assignments/workflow.main",
        "/v1/assignments/workflow.main/observed-requirements/text_input",
        "/v1/service-keys",
        "/v1/service-keys",
        "/v1/service-keys/key-1",
        "/v1/provider-models",
        "/v1/model-calls",
        "/v1/model-calls",
        "/v1/embeddings",
        "/v1/media-jobs",
        "/v1/media-jobs/job-1",
        "/v1/media-jobs/job-1",
        "/v1/media-jobs/job-1/content",
        "/v1/statistics",
    ]
    assert all(
        call.headers["Authorization"] == f"Bearer {_SERVICE_KEY}"
        for call in transport.calls
    )
    assert all(call.timeout == _EXPECTED_CALL_TIMEOUT for call in transport.calls)
    model_body = json.loads(cast("bytes", transport.calls[14].body))
    assert model_body["selector"] == {"provider_model_api_name": "route-a"}
    assert "output_format" not in model_body
    assert (model_body["output_limit"], model_body["temperature"]) == (20, 0.2)


def test_model_body_preserves_images_exclusions_workspace_and_tags() -> None:
    """The native adapter maps each harness call field without losing scope."""
    transport = FakeRouterTransport(
        [
            response(
                200,
                {
                    "output_type": "standard",
                    "provider_model_api_name": "route-b",
                    "content": [{"type": "text", "text": "Done."}],
                    "usage": usage(),
                },
            )
        ]
    )
    subject = client(transport)
    call = ModelCall(
        "main",
        AssignmentSelector("workflow.main"),
        (opendle.UserMessage((ImageInputPart("image/png", b"png"),)),),
        tags=("agent", "workflow"),
        excluded_routes=(ExactModelSelector("route-a"),),
    )

    subject.model_call(call)

    body = json.loads(cast("bytes", transport.calls[0].body))
    assert body == {
        "workspace_api_name": "main",
        "selector": {"assignment_api_name": "workflow.main"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "media_type": "image/png",
                        "data_base64": "cG5n",
                    }
                ],
            }
        ],
        "tags": ["agent", "workflow"],
        "excluded_provider_model_api_names": ["route-a"],
    }


def test_client_and_harness_replace_failed_sticky_route_without_duplicate() -> None:
    """Exclude a failed exact route and make the integrated success sticky."""
    transport = FakeRouterTransport(
        [
            response(
                502,
                {"error": {"code": "upstream_failed", "message": "Route failed."}},
            ),
            response(
                200,
                {
                    "output_type": "standard",
                    "provider_model_api_name": "route-b",
                    "content": [{"type": "text", "text": "Done."}],
                    "usage": usage(),
                },
            ),
        ]
    )
    harness = ConversationHarness(
        model_caller=client(transport),
        tools=(),
        config=opendle.HarnessConfig(
            workspace_api_name="main",
            assignment_api_name="workflow",
            context=ContextPolicy(ContextMethod.PRUNE, ContextLimits(10, 10_000)),
            tags=("agent",),
        ),
    )
    state = ConversationState(
        (UserMessage((TextInputPart("Run."),)),),
        RouteState(ExactModelSelector("route-a")),
    )

    updated = asyncio.run(harness.run(state))

    first = json.loads(cast("bytes", transport.calls[0].body))
    second = json.loads(cast("bytes", transport.calls[1].body))
    assert first["selector"] == {"provider_model_api_name": "route-a"}
    assert "excluded_provider_model_api_names" not in first
    assert second["selector"] == {"assignment_api_name": "workflow"}
    assert second["excluded_provider_model_api_names"] == ["route-a"]
    assert first["workspace_api_name"] == second["workspace_api_name"] == "main"
    assert first["tags"] == second["tags"] == ["agent"]
    assert updated.route.sticky == ExactModelSelector("route-b")


def test_stream_protocol_covers_visible_events_and_completion() -> None:
    """The client yields each native stream event in exact order."""
    transport = FakeRouterTransport(
        streams=[
            stream(
                ("start", {"provider_model_api_name": "route-a"}),
                ("text_delta", {"delta": "Hi"}),
                (
                    "tool_call",
                    {
                        "tool_call": {
                            "type": "tool_call",
                            "id": "call-1",
                            "name": "lookup",
                            "arguments_json": "{}",
                        }
                    },
                ),
                ("completed", {"provider_model_api_name": "route-a", "usage": usage()}),
            )
        ]
    )

    events = tuple(client(transport).stream_model(model_call()))

    assert isinstance(events[0], ModelStreamStart)
    assert events[1] == ModelStreamTextDelta("Hi")
    assert events[2] == ModelStreamToolCall(ToolCallPart("call-1", "lookup", "{}"))
    assert isinstance(events[3], ModelStreamCompleted)
    assert transport.calls[0].headers["Accept"] == "text/event-stream"


@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        ("authentication_required", RouterAuthenticationError),
        ("permission_denied", RouterAuthorizationError),
        ("invalid_request", RouterValidationError),
        ("not_found", RouterNotFoundError),
        ("conflict", RouterConflictError),
        ("provider_unavailable", RouterUnavailableError),
        ("upstream_failed", RouterUpstreamError),
        ("content_unavailable", RouterContentUnavailableError),
        ("rate_limited", RouterRateLimitError),
        ("internal_error", RouterInternalError),
    ],
)
def test_safe_native_errors_are_typed_and_exclude_secrets(
    code: str, error_type: type[RouterAPIError]
) -> None:
    """Foreign-scope and other native failures expose only safe fields."""
    transport = FakeRouterTransport(
        [
            response(
                403,
                {
                    "error": {
                        "code": code,
                        "message": "Safe corrective message.",
                        "details": {"field": "workspace_api_name", "reason": "foreign"},
                    }
                },
            )
        ]
    )
    subject = client(transport)

    with pytest.raises(error_type) as captured:
        subject.get_workspace("foreign")

    assert captured.value.code == code
    assert captured.value.status == _FORBIDDEN_STATUS
    assert captured.value.field_name == "workspace_api_name"
    assert captured.value.reason == "foreign"
    assert _SERVICE_KEY not in str(captured.value)
    assert _SERVICE_KEY not in repr(subject)


def test_assignment_cycle_error_and_stream_error_envelope_are_typed() -> None:
    """The last stable error and an SSE error use the same native envelope."""
    transport = FakeRouterTransport(
        [response(409, {"error": {"code": "assignment_cycle", "message": "Cycle."}})],
        [
            stream(
                ("start", {"provider_model_api_name": "route-a"}),
                ("error", {"error": {"code": "upstream_failed", "message": "Failed."}}),
            )
        ],
    )
    subject = client(transport)

    with pytest.raises(opendle.RouterAssignmentCycleError):
        subject.put_assignment("workflow", inherits_assignment_api_name="workflow")
    with pytest.raises(RouterUpstreamError) as stream_error:
        tuple(subject.stream_model(model_call()))
    assert stream_error.value.phase is CallFailurePhase.BEFORE_VISIBLE_OUTPUT


def test_transport_failure_is_uncertain_and_harness_does_not_retry() -> None:
    """Accepted request loss stays uncertain and stops sticky replacement."""
    subject = client(FailingRouterTransport())
    with pytest.raises(RouterTransportError) as sync_error:
        subject.model_call(model_call())
    assert sync_error.value.uncertain_result is True
    assert _SERVICE_KEY not in str(sync_error.value)

    with pytest.raises(ModelCallError) as async_error:
        asyncio.run(subject(model_call()))
    assert async_error.value.phase is CallFailurePhase.UNCERTAIN
    assert _SERVICE_KEY not in str(async_error.value)

    with pytest.raises(RouterTransportError):
        tuple(subject.stream_model(model_call()))

    with pytest.raises(RouterTransportError) as mutation_error:
        subject.create_workspace("main", "Main")
    assert mutation_error.value.uncertain_result is True
    with pytest.raises(RouterTransportError) as read_error:
        subject.get_workspace("main")
    assert read_error.value.uncertain_result is False


def test_stream_disconnect_phase_uses_output_visibility() -> None:
    """A stream disconnect is uncertain before output and final after output."""
    before = client(
        FakeRouterTransport(
            streams=[stream(("start", {"provider_model_api_name": "route-a"}))]
        )
    )
    with pytest.raises(RouterStreamError) as before_error:
        tuple(before.stream_model(model_call()))
    assert before_error.value.phase is CallFailurePhase.UNCERTAIN

    after = client(
        FakeRouterTransport(
            streams=[
                stream(
                    ("start", {"provider_model_api_name": "route-a"}),
                    ("text_delta", {"delta": "visible"}),
                )
            ]
        )
    )
    with pytest.raises(RouterStreamError) as after_error:
        tuple(after.stream_model(model_call()))
    assert after_error.value.phase is CallFailurePhase.AFTER_VISIBLE_OUTPUT


def test_bounded_pagination_and_media_polling() -> None:
    """Caller-selected page and media polling bounds stop finite workflows."""
    transport = FakeRouterTransport(
        [
            response(200, page([], more=True)),
            response(200, page([], more=False)),
            response(200, page([], more=True)),
            response(200, media_job("pending")),
            response(200, media_job("running")),
            response(200, media_job()),
        ]
    )
    subject = client(transport)

    assert len(tuple(subject.iter_workspace_pages(max_pages=2))) == _EXPECTED_PAGE_COUNT
    with pytest.raises(RouterPageLimitError):
        tuple(subject.iter_assignment_pages(max_pages=1))
    assert (
        subject.wait_media_job("job-1", timeout=1, poll_interval=0.001).state
        is MediaJobState.SUCCEEDED
    )


def test_removed_public_surfaces_are_not_added() -> None:
    """The SDK has no compatibility, provider-specific, or recovery methods."""
    removed = {
        "cancel_model_call",
        "get_model_call",
        "resume_model_stream",
        "create_agent_run",
        "openai_chat_completion",
        "exchange_service_token",
        "get_request_logs",
        "get_provider_credentials",
    }
    assert removed.isdisjoint(dir(RouterClient))


def test_public_exports_include_complete_typed_sdk() -> None:
    """Top-level and module imports expose the same official client types."""
    assert opendle.RouterClient is router_client_module.RouterClient
    assert opendle.ModelResponse is not None
    assert opendle.RouterTransport is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": "http://example.com", "service_key": _SERVICE_KEY},
        {"base_url": "ftp://localhost", "service_key": _SERVICE_KEY},
        {"base_url": "http://user@localhost", "service_key": _SERVICE_KEY},
        {"base_url": "http://localhost?query=1", "service_key": _SERVICE_KEY},
        {"base_url": "http://localhost#fragment", "service_key": _SERVICE_KEY},
        {"base_url": "http://localhost:", "service_key": _SERVICE_KEY},
        {"base_url": "http://localhost:bad", "service_key": _SERVICE_KEY},
        {"base_url": "http://localhost", "service_key": ""},
        {"base_url": "http://localhost", "service_key": " key"},
        {"base_url": "http://localhost", "service_key": "é"},
        {"base_url": "http://localhost", "service_key": "x" * 501},
        {"base_url": "http://localhost", "service_key": _SERVICE_KEY, "timeout": 0},
        {"base_url": "http://localhost", "service_key": _SERVICE_KEY, "timeout": True},
        {"base_url": "http://localhost", "service_key": _SERVICE_KEY, "timeout": "1"},
        {
            "base_url": "http://localhost",
            "service_key": _SERVICE_KEY,
            "maximum_success_response_bytes": 0,
        },
        {
            "base_url": "http://localhost",
            "service_key": _SERVICE_KEY,
            "maximum_success_response_bytes": True,
        },
        {
            "base_url": "http://localhost",
            "service_key": _SERVICE_KEY,
            "maximum_success_response_bytes": "1",
        },
    ],
)
def test_client_connection_configuration_is_strict(kwargs: dict[str, object]) -> None:
    """Credentials, endpoints, timeouts, and response bounds are finite."""
    with pytest.raises(ValueError, match=r".+"):
        RouterClient(**kwargs)  # type: ignore[arg-type]


def test_public_request_bounds_reject_invalid_values() -> None:
    """Each public SDK request applies the native finite input bounds."""
    subject = client(FakeRouterTransport())
    assignment_operations: tuple[Callable[[], object], ...] = (
        lambda: subject.put_assignment("workflow"),
        lambda: subject.put_assignment(
            "workflow", inherits_assignment_api_name="default", direct_chain=("route",)
        ),
        lambda: subject.put_assignment("workflow", direct_chain=()),
        lambda: subject.put_assignment("workflow", direct_chain=("route", "route")),
    )
    for operation in assignment_operations:
        with pytest.raises(ValueError, match=r".+"):
            operation()
    with pytest.raises(ValueError, match=r".+"):
        subject.create_workspace("Bad", "Main")
    with pytest.raises(ValueError, match=r".+"):
        subject.create_workspace("main", "")
    with pytest.raises(ValueError, match=r".+"):
        subject.get_assignment("Bad")
    with pytest.raises(ValueError, match=r".+"):
        subject.list_workspaces(limit=0)
    with pytest.raises(ValueError, match=r".+"):
        subject.list_workspaces(cursor="")
    with pytest.raises(ValueError, match=r".+"):
        tuple(subject.iter_provider_model_pages(max_pages=0))
    with pytest.raises(ValueError, match=r".+"):
        subject.create_embedding("main", AssignmentSelector("a"), ())
    with pytest.raises(ValueError, match=r".+"):
        subject.create_embedding("main", AssignmentSelector("a"), ("x",) * 33)
    with pytest.raises(ValueError, match=r".+"):
        subject.create_embedding("main", AssignmentSelector("a"), ("é" * 16_385,))
    with pytest.raises(ValueError, match=r".+"):
        subject.create_embedding("main", AssignmentSelector("a"), ("x" * 8_193,) * 32)
    with pytest.raises(ValueError, match=r".+"):
        subject.create_embedding(
            "main", AssignmentSelector("a"), ("x",), tags=("z", "a")
        )
    with pytest.raises(ValueError, match=r".+"):
        subject.create_media_job(
            "main",
            AssignmentSelector("a"),
            MediaKind.AUDIO,
            "Audio.",
            input_images=(ImageInputPart("image/png", b"x"),),
        )
    with pytest.raises(ValueError, match=r".+"):
        subject.create_media_job(
            "main",
            AssignmentSelector("a"),
            MediaKind.IMAGE,
            "Image.",
            input_images=(ImageInputPart("image/png", b"x"),) * 9,
        )
    with pytest.raises(ValueError, match=r".+"):
        subject.create_media_job(
            "main",
            AssignmentSelector("a"),
            MediaKind.IMAGE,
            "Image.",
            tags=("z", "a"),
        )
    with pytest.raises(ValueError, match=r".+"):
        subject.wait_media_job("job", timeout=-1)
    with pytest.raises(ValueError, match=r".+"):
        subject.wait_media_job("job", timeout=1, poll_interval=0)
    for invalid_timeout in (True, "1"):
        with pytest.raises(ValueError, match=r".+"):
            subject.wait_media_job("job", timeout=cast("float", invalid_timeout))
    for invalid_interval in (True, "1"):
        with pytest.raises(ValueError, match=r".+"):
            subject.wait_media_job(
                "job", timeout=1, poll_interval=cast("float", invalid_interval)
            )
    timeout_transport = FakeRouterTransport([response(200, media_job("running"))])
    with pytest.raises(TimeoutError):
        client(timeout_transport).wait_media_job("job", timeout=0)
    with pytest.raises(ValueError, match=r".+"):
        subject.get_statistics(
            _TIME, _NEXT_TIME, group_by=(StatisticsDimension.TAG,) * 2
        )

    invalid_statistics: tuple[tuple[str, str, dict[str, str]], ...] = (
        ("invalid", _NEXT_TIME, {}),
        ("2026-08-24T00:00:00", _NEXT_TIME, {}),
        (_TIME, "2028-08-25T00:00:00Z", {}),
        (_NEXT_TIME, _TIME, {}),
        (_TIME, _NEXT_TIME, {"workspace": "Bad"}),
        (_TIME, _NEXT_TIME, {"assignment": "Bad"}),
        (_TIME, _NEXT_TIME, {"provider_model": "Bad"}),
        (_TIME, _NEXT_TIME, {"outcome": "other"}),
        (_TIME, _NEXT_TIME, {"tag": ""}),
    )
    for start, end, filters in invalid_statistics:
        with pytest.raises(ValueError, match=r".+"):
            subject.get_statistics(start, end, **filters)  # type: ignore[arg-type]


def test_async_model_caller_maps_reported_and_structured_failures() -> None:
    """The harness adapter exposes safe phases for non-standard outcomes."""
    error_client = client(
        FakeRouterTransport(
            [
                response(
                    503, {"error": {"code": "upstream_failed", "message": "Failed."}}
                )
            ]
        )
    )
    with pytest.raises(ModelCallError) as reported:
        asyncio.run(error_client(model_call()))
    assert reported.value.phase is CallFailurePhase.BEFORE_VISIBLE_OUTPUT

    validation_client = client(
        FakeRouterTransport(
            [
                response(
                    400,
                    {"error": {"code": "invalid_request", "message": "Invalid."}},
                )
            ]
        )
    )
    with pytest.raises(RouterValidationError):
        asyncio.run(validation_client(model_call()))

    structured_client = client(
        FakeRouterTransport(
            [
                response(
                    200,
                    {
                        "output_type": "structured_json",
                        "provider_model_api_name": "route-a",
                        "structured_output_json": "{}",
                        "usage": usage(),
                    },
                )
            ]
        )
    )
    with pytest.raises(ModelCallError) as structured:
        asyncio.run(structured_client(model_call(structured=True)))
    assert structured.value.code == "invalid_response"

    success_client = client(
        FakeRouterTransport(
            [
                response(
                    200,
                    {
                        "output_type": "standard",
                        "provider_model_api_name": "route-a",
                        "content": [{"type": "text", "text": "Done."}],
                        "usage": usage(),
                    },
                )
            ]
        )
    )
    assert asyncio.run(success_client(model_call())).content == (
        TextOutputPart("Done."),
    )


def test_direct_model_and_stream_routes_must_match_the_call() -> None:
    """Direct SDK use cannot accept a wrong exact or excluded result route."""
    wrong_result = {
        "output_type": "standard",
        "provider_model_api_name": "route-b",
        "content": [{"type": "text", "text": "Wrong."}],
        "usage": usage(),
    }
    with pytest.raises(RouterProtocolError, match="exact"):
        client(FakeRouterTransport([response(200, wrong_result)])).model_call(
            model_call()
        )

    excluded_call = ModelCall(
        "main",
        AssignmentSelector("workflow"),
        (SystemMessage("System."),),
        excluded_routes=(ExactModelSelector("route-b"),),
    )
    with pytest.raises(RouterProtocolError, match="excluded"):
        client(FakeRouterTransport([response(200, wrong_result)])).model_call(
            excluded_call
        )

    wrong_start = stream(
        ("start", {"provider_model_api_name": "route-b"}),
        ("completed", {"provider_model_api_name": "route-b", "usage": usage()}),
    )
    with pytest.raises(RouterProtocolError, match="exact"):
        tuple(
            client(FakeRouterTransport(streams=[wrong_start])).stream_model(
                model_call()
            )
        )

    changed_completion = stream(
        ("start", {"provider_model_api_name": "route-a"}),
        ("completed", {"provider_model_api_name": "route-b", "usage": usage()}),
    )
    with pytest.raises(RouterProtocolError, match="different"):
        tuple(
            client(FakeRouterTransport(streams=[changed_completion])).stream_model(
                model_call()
            )
        )

    embedding_value = {
        "provider_model_api_name": "route-b",
        "embeddings": [{"index": 0, "values": [1]}],
        "usage": usage(),
    }
    with pytest.raises(RouterProtocolError, match="exact"):
        client(FakeRouterTransport([response(200, embedding_value)])).create_embedding(
            "main", ExactModelSelector("route-a"), ("one",)
        )
    assert client(
        FakeRouterTransport([response(200, embedding_value)])
    ).create_embedding(
        "main", AssignmentSelector("embedding"), ("one",)
    ).route == ExactModelSelector("route-b")

    wrong_job = media_job()
    wrong_job["provider_model_api_name"] = "route-b"
    with pytest.raises(RouterProtocolError, match="exact"):
        client(FakeRouterTransport([response(202, wrong_job)])).create_media_job(
            "main", ExactModelSelector("route-a"), MediaKind.IMAGE, "Draw."
        )
    assert client(FakeRouterTransport([response(202, wrong_job)])).create_media_job(
        "main", AssignmentSelector("image"), MediaKind.IMAGE, "Draw."
    ).route == ExactModelSelector("route-b")
    for field, wrong_value in (("workspace_api_name", "other"), ("kind", "video")):
        mismatched_job = media_job()
        mismatched_job[field] = wrong_value
        with pytest.raises(RouterProtocolError, match="creation request"):
            client(
                FakeRouterTransport([response(202, mismatched_job)])
            ).create_media_job(
                "main", ExactModelSelector("route-a"), MediaKind.IMAGE, "Draw."
            )


def test_json_response_parser_rejects_duplicates_and_non_finite_numbers() -> None:
    """Complete and SSE JSON reject nested duplicates and non-finite constants."""
    invalid_complete = (
        b'{"items":[],"page":{"has_more":false,"has_more":false}}',
        b'{"items":[],"page":{"has_more":false},"value":NaN}',
        b'{"items":[],"page":{"has_more":false},"value":Infinity}',
        b'{"items":[],"page":{"has_more":false},"value":-Infinity}',
    )
    for body in invalid_complete:
        with pytest.raises(RouterProtocolError):
            client(
                FakeRouterTransport(
                    [
                        RouterTransportResponse(
                            200, {"Content-Type": "application/json"}, body
                        )
                    ]
                )
            ).list_workspaces()

    invalid_stream = RouterStreamResponse(
        200,
        {"Content-Type": "text/event-stream"},
        (
            (
                b'event: start\ndata: {"provider_model_api_name":"route-a",'
                b'"nested":{"x":1,"x":2}}\n\n'
            ),
        ),
    )
    with pytest.raises(RouterProtocolError, match="duplicate"):
        tuple(
            client(FakeRouterTransport(streams=[invalid_stream])).stream_model(
                model_call()
            )
        )


@pytest.mark.parametrize(
    "body",
    [
        b'{"items":[],"page":{"has_more":true,"next_cursor":"\\ud800"}}',
        b'{"items":[],"page":{"has_more":false,"\\udfff":1}}',
        (
            b'{"items":[{"api_name":"main","display_name":"\\ud800",'
            b'"created_at":"2026-08-24T00:00:00Z"}],"page":{"has_more":false}}'
        ),
    ],
)
def test_complete_json_rejects_nested_lone_unicode_surrogates(body: bytes) -> None:
    """Response keys and nested string values must be valid UTF-8 text."""
    with pytest.raises(RouterProtocolError, match="Unicode"):
        client(
            FakeRouterTransport(
                [
                    RouterTransportResponse(
                        200, {"Content-Type": "application/json"}, body
                    )
                ]
            )
        ).list_workspaces()


@pytest.mark.parametrize(
    "data",
    [
        b'{"provider_model_api_name":"\\ud800"}',
        b'{"provider_model_api_name":"route-a","nested":{"\\udfff":1}}',
    ],
)
def test_sse_json_rejects_nested_lone_unicode_surrogates(data: bytes) -> None:
    """The common JSON decoder also validates all SSE data Unicode."""
    response_value = RouterStreamResponse(
        200,
        {"Content-Type": "text/event-stream"},
        (b"event: start\ndata: " + data + b"\n\n",),
    )
    with pytest.raises(RouterProtocolError, match="Unicode"):
        tuple(
            client(FakeRouterTransport(streams=[response_value])).stream_model(
                model_call()
            )
        )


def test_stream_accepts_split_crlf_and_bounds_total_events_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSE framing survives split CRLF and complete streams stay finite."""
    start = b'event: start\r\ndata: {"provider_model_api_name":"route-a"}\r\n\r\n'
    completed = (
        b'event: completed\r\ndata: {"provider_model_api_name":"route-a","usage":'
        + json.dumps(usage()).encode()
        + b"}\r\n\r\n"
    )
    split_crlf = RouterStreamResponse(
        200,
        {"Content-Type": "text/event-stream"},
        (start[:-1], start[-1:] + completed[:-3], completed[-3:-2], completed[-2:]),
    )
    events = tuple(
        client(FakeRouterTransport(streams=[split_crlf])).stream_model(model_call())
    )
    assert isinstance(events[-1], ModelStreamCompleted)

    private("_validate_stream_event_count")(100_000)
    with pytest.raises(RouterResponseLimitError, match="many events"):
        private("_validate_stream_event_count")(100_001)
    private("_validate_stream_output_size")(10_000_000)
    with pytest.raises(RouterResponseLimitError, match="output"):
        private("_validate_stream_output_size")(10_000_001)

    monkeypatch.setattr(router_client_module, "_MAXIMUM_STREAM_EVENTS", 100)
    too_many = start.replace(b"\r\n", b"\n") + (
        b'event: text_delta\ndata: {"delta":"x"}\n\n' * 100
    )
    with pytest.raises(RouterResponseLimitError, match="many events"):
        for _event in cast("Iterable[object]", private("_stream_events")((too_many,))):
            pass

    monkeypatch.setattr(router_client_module, "_MAXIMUM_STREAM_OUTPUT_BYTES", 1_000)
    output = start.replace(b"\r\n", b"\n") + b"".join(
        b'event: text_delta\ndata: {"delta":"' + (b"x" * 100) + b'"}\n\n'
        for _index in range(11)
    )
    with pytest.raises(RouterResponseLimitError, match="output"):
        for _event in cast("Iterable[object]", private("_stream_events")((output,))):
            pass


def test_request_json_enforces_exact_router_http_body_limit_before_transport() -> None:
    """The exact 70 MiB request boundary rejects expanded media before I/O."""
    maximum = 70 * 1024 * 1024
    private("_validate_request_size")(maximum)
    with pytest.raises(RouterContractError, match="70 MiB"):
        private("_validate_request_size")(maximum + 1)

    transport = FakeRouterTransport()
    subject = client(transport)
    shared_20_mib = b"x" * (20 * 1024 * 1024)
    with pytest.raises(RouterContractError, match="70 MiB"):
        subject.create_media_job(
            "main",
            ExactModelSelector("route-a"),
            MediaKind.IMAGE,
            "\U0001f642" * 1_000_000,
            input_images=(
                ImageInputPart("image/png", shared_20_mib),
                ImageInputPart("image/png", shared_20_mib),
                ImageInputPart("image/png", b"x" * (10 * 1024 * 1024)),
            ),
        )
    assert transport.calls == []


def test_response_status_media_type_body_and_size_failures() -> None:
    """All response forms keep closed media, error, empty, and byte contracts."""
    error = {"error": {"code": "not_found", "message": "Missing."}}
    operations: list[
        tuple[RouterClient, Callable[[RouterClient], object], type[Exception]]
    ] = [
        (
            client(FakeRouterTransport([response(404, error)])),
            lambda value: value.delete_workspace("main"),
            RouterNotFoundError,
        ),
        (
            client(FakeRouterTransport([response(204, b"unexpected", media_type="")])),
            lambda value: value.delete_workspace("main"),
            RouterProtocolError,
        ),
        (
            client(
                FakeRouterTransport([response(200, page([]), media_type="text/plain")])
            ),
            lambda value: value.list_workspaces(),
            RouterProtocolError,
        ),
        (
            client(FakeRouterTransport([response(200, b"xx")]), maximum=1),
            lambda value: value.list_workspaces(),
            RouterResponseLimitError,
        ),
        (
            client(FakeRouterTransport([response(404, error)])),
            lambda value: value.get_media_job_content("job"),
            RouterNotFoundError,
        ),
        (
            client(FakeRouterTransport([response(200, b"bytes", media_type="")])),
            lambda value: value.get_media_job_content("job"),
            RouterProtocolError,
        ),
    ]
    for subject, operation, error_type in operations:
        with pytest.raises(error_type):
            operation(subject)

    stream_errors = (
        RouterStreamResponse(
            404, {"Content-Type": "application/json"}, (json.dumps(error).encode(),)
        ),
        RouterStreamResponse(200, {"Content-Type": "application/json"}, (b"",)),
    )
    with pytest.raises(RouterNotFoundError):
        tuple(
            client(FakeRouterTransport(streams=[stream_errors[0]])).stream_model(
                model_call()
            )
        )
    with pytest.raises(RouterProtocolError):
        tuple(
            client(FakeRouterTransport(streams=[stream_errors[1]])).stream_model(
                model_call()
            )
        )

    oversized_error = RouterStreamResponse(
        400,
        {"Content-Type": "application/json"},
        (b"x", b"x"),
    )
    with pytest.raises(RouterResponseLimitError):
        tuple(
            client(
                FakeRouterTransport(streams=[oversized_error]), maximum=1
            ).stream_model(model_call())
        )

    non_byte_error = RouterStreamResponse(
        400,
        {"Content-Type": "application/json"},
        cast("object", ("not bytes",)),  # type: ignore[arg-type]
    )
    with pytest.raises(RouterProtocolError):
        tuple(
            client(FakeRouterTransport(streams=[non_byte_error])).stream_model(
                model_call()
            )
        )

    unsafe_error_chunks = RouterStreamResponse(
        400,
        {"Content-Type": "application/json"},
        cast("object", BrokenChunks(b"")),  # type: ignore[arg-type]
    )
    with pytest.raises(RouterTransportError) as unsafe_error:
        tuple(
            client(FakeRouterTransport(streams=[unsafe_error_chunks])).stream_model(
                model_call()
            )
        )
    assert "unsafe transport detail" not in str(unsafe_error.value)

    short_secret = client(
        FakeRouterTransport(
            [
                response(
                    201,
                    {"key": service_key(include_last_used=False), "secret": "short"},
                )
            ]
        )
    )
    with pytest.raises(RouterProtocolError, match="secret"):
        short_secret.create_service_key("worker")


def test_every_native_error_envelope_requires_json_media_type() -> None:
    """Complete, empty, media, and stream error paths require exact JSON."""
    error = {"error": {"code": "not_found", "message": "Missing."}}
    operations: tuple[Callable[[], object], ...] = (
        lambda: client(
            FakeRouterTransport([response(404, error, media_type="text/plain")])
        ).list_workspaces(),
        lambda: client(
            FakeRouterTransport([response(404, error, media_type="text/plain")])
        ).delete_workspace("main"),
        lambda: client(
            FakeRouterTransport([response(404, error, media_type="text/plain")])
        ).get_media_job_content("job"),
        lambda: tuple(
            client(
                FakeRouterTransport(
                    streams=[
                        RouterStreamResponse(
                            404,
                            {"Content-Type": "text/plain"},
                            (json.dumps(error).encode(),),
                        )
                    ]
                )
            ).stream_model(model_call())
        ),
    )
    for operation in operations:
        with pytest.raises(RouterProtocolError, match="media type"):
            operation()


def test_oversized_stream_chunks_fail_before_buffer_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One oversized custom chunk fails before parsing or body extension."""

    def unexpected_parse(buffer: bytes) -> tuple[bytes, bytes] | None:
        del buffer
        msg = "The oversized chunk reached the parser."
        raise AssertionError(msg)

    monkeypatch.setattr(router_client_module, "_next_sse_block", unexpected_parse)
    with pytest.raises(RouterResponseLimitError, match="event"):
        tuple(
            cast(
                "Iterable[object]",
                private("_stream_events")((b"x" * (2 * 1024 * 1024 + 1),)),
            )
        )
    with pytest.raises(RouterResponseLimitError, match="configured byte limit"):
        private("_bounded_stream_body")((b"xx",), 1)


@pytest.mark.parametrize(
    ("headers", "error_type"),
    [
        (
            {f"X-{index}": "x" for index in range(101)},
            RouterResponseLimitError,
        ),
        ({"X-Large": "x" * (64 * 1024 + 1)}, RouterResponseLimitError),
        (
            {"Content-Type": "application/json", "content-type": "application/json"},
            RouterProtocolError,
        ),
        ({"Content-Length": "bad"}, RouterProtocolError),
        ({"Content-Length": "2"}, RouterProtocolError),
        ({"Bad\nName": "x"}, RouterProtocolError),
        ({"X-Test": "bad\rvalue"}, RouterProtocolError),
    ],
)
def test_custom_transport_response_headers_are_bounded_and_unambiguous(
    headers: Mapping[str, str], error_type: type[Exception]
) -> None:
    """Custom response headers have finite size and one critical value."""
    with pytest.raises(error_type):
        client(
            FakeRouterTransport(
                [
                    RouterTransportResponse(
                        200, headers, b'{"items":[],"page":{"has_more":false}}'
                    )
                ]
            )
        ).list_workspaces()


def test_custom_transport_response_shape_and_stream_headers_are_validated() -> None:
    """Custom transports cannot bypass status, body, or stream header validation."""
    invalid_responses = (
        RouterTransportResponse(cast("int", bool(1)), {}, b""),
        RouterTransportResponse(99, {}, b""),
        RouterTransportResponse(200, {}, cast("bytes", bytearray())),
        RouterTransportResponse(200, cast("Mapping[str, str]", []), b""),
        RouterTransportResponse(200, cast("Mapping[str, str]", {1: "x"}), b""),
        RouterTransportResponse(200, BrokenHeaderMapping(), b""),
        RouterTransportResponse(200, {"X-Test": "\ud800"}, b""),
    )
    for invalid in invalid_responses:
        with pytest.raises(RouterProtocolError):
            client(FakeRouterTransport([invalid])).list_workspaces()

    invalid_stream = RouterStreamResponse(
        200,
        {"Content-Encoding": "identity", "content-encoding": "identity"},
        (),
    )
    with pytest.raises(RouterProtocolError, match="duplicate"):
        tuple(
            client(FakeRouterTransport(streams=[invalid_stream])).stream_model(
                model_call()
            )
        )

    invalid_status_stream = RouterStreamResponse(600, {}, ())
    with pytest.raises(RouterProtocolError, match="invalid stream"):
        tuple(
            client(FakeRouterTransport(streams=[invalid_status_stream])).stream_model(
                model_call()
            )
        )

    class ProtocolStreamTransport(FakeRouterTransport):
        def stream(
            self,
            method: str,
            url: str,
            headers: Mapping[str, str],
            body: bytes,
            timeout: float,
        ) -> RouterStreamResponse:
            """Raise one already safe protocol error."""
            del method, url, headers, body, timeout
            msg = "safe protocol failure"
            raise RouterProtocolError(msg)

    with pytest.raises(RouterProtocolError, match="safe protocol"):
        tuple(client(ProtocolStreamTransport()).stream_model(model_call()))


class BrokenChunks:
    """Raise a connection failure while a client consumes SSE chunks."""

    def __init__(self, first: bytes) -> None:
        """Store the one chunk returned before the failure."""
        self.first = first

    def __iter__(self) -> object:
        """Yield one chunk and then simulate connection loss."""
        yield self.first
        msg = "unsafe transport detail"
        raise OSError(msg)


@pytest.mark.parametrize(
    ("chunks", "error_type"),
    [
        (("not bytes",), RouterProtocolError),
        ((b"x" * (2 * 1024 * 1024 + 1),), RouterResponseLimitError),
        ((b"x" * (2 * 1024 * 1024 + 1) + b"\n\n",), RouterResponseLimitError),
        ((b"event: start\ndata: {}",), RouterProtocolError),
        (
            (
                b'event: start\ndata: {"provider_model_api_name":"route-a"}\n\n'
                b'event: completed\ndata: {"provider_model_api_name":"route-a","usage":'
                + json.dumps(usage()).encode()
                + b"}\n\n",
                b'event: text_delta\ndata: {"delta":"late"}\n\n',
            ),
            RouterProtocolError,
        ),
        (
            (
                b'event: start\ndata: {"provider_model_api_name":"route-a"}\n\n'
                b'event: completed\ndata: {"provider_model_api_name":"route-a","usage":'
                + json.dumps(usage()).encode()
                + b'}\n\nevent: text_delta\ndata: {"delta":"late"}\n\n',
            ),
            RouterProtocolError,
        ),
    ],
)
def test_stream_chunk_contract_is_strict(
    chunks: object, error_type: type[Exception]
) -> None:
    """SSE chunks are bytes, bounded, complete, and terminal."""
    response_value = RouterStreamResponse(
        200,
        {"Content-Type": "text/event-stream"},
        chunks,  # type: ignore[arg-type]
    )
    with pytest.raises(error_type):
        tuple(
            client(FakeRouterTransport(streams=[response_value])).stream_model(
                model_call()
            )
        )


def test_stream_transport_loss_preserves_visibility_phase() -> None:
    """An iterator failure uses the visibility state without unsafe detail."""
    prefix = (
        b'event: start\ndata: {"provider_model_api_name":"route-a"}\n\n'
        b'event: text_delta\ndata: {"delta":"visible"}\n\n'
    )
    response_value = RouterStreamResponse(
        200,
        {"Content-Type": "text/event-stream"},
        cast("object", BrokenChunks(prefix)),  # type: ignore[arg-type]
    )
    with pytest.raises(RouterStreamError) as captured:
        tuple(
            client(FakeRouterTransport(streams=[response_value])).stream_model(
                model_call()
            )
        )
    assert captured.value.phase is CallFailurePhase.AFTER_VISIBLE_OUTPUT


@pytest.mark.parametrize(
    ("block", "started"),
    [
        (b"\xff", False),
        (b"bad framing", False),
        (b'event: text_delta\ndata: {"delta":"x"}', False),
        (b'event: start\ndata: {"provider_model_api_name":"route-a"}', True),
        (b'event: text_delta\ndata: {"delta":""}', True),
        (b"event: unknown\ndata: {}", True),
    ],
)
def test_stream_event_validation_rejects_invalid_native_events(
    block: bytes,
    started: bool,  # noqa: FBT001 - Pytest supplies this parameter.
) -> None:
    """The SSE parser rejects invalid framing, order, values, and names."""
    with pytest.raises(RouterProtocolError):
        private("_stream_event")(block, started=started)


_PRIVATE_PARSER_CASES: list[tuple[str, tuple[object, ...], dict[str, object]]] = [
    ("_model_response", ({"output_type": "other"},), {}),
    ("_assistant_part", ({"type": "image"},), {}),
    (
        "_tool_call",
        ({"type": "text", "id": "id", "name": "tool", "arguments_json": "{}"},),
        {},
    ),
    ("_usage_item", ({"unit": "other", "quantity": "1"},), {}),
    (
        "_assignment",
        (
            {
                "api_name": "a",
                "display_name": "A",
                "definition_kind": "other",
                "effective_chain": [],
                "observed_requirements": [],
            },
        ),
        {},
    ),
    ("_unit_price", ({"unit": "other", "amount": "1"},), {}),
    (
        "_provider_model",
        (
            {
                "api_name": "route",
                "display_name": "Route",
                "input_modalities": ["other"],
                "output_modalities": [],
                "capabilities": [],
            },
        ),
        {},
    ),
    (
        "_embedding_result",
        (
            {
                "provider_model_api_name": "route",
                "embeddings": [],
                "usage": usage(),
            },
        ),
        {"expected_count": 1},
    ),
    (
        "_embedding_result",
        (
            {
                "provider_model_api_name": "route",
                "embeddings": [
                    {"index": 0, "values": [1]},
                    {"index": 1, "values": [1, 2]},
                ],
                "usage": usage(),
            },
        ),
        {"expected_count": 2},
    ),
    ("_embedding_vector", ({"index": 0, "values": []},), {}),
    (
        "_media_job",
        (
            {
                "id": "job",
                "workspace_api_name": "main",
                "provider_model_api_name": "route",
                "kind": "other",
                "state": "pending",
                "created_at": _TIME,
            },
        ),
        {},
    ),
    (
        "_statistics",
        ({"from": _TIME, "to": _TIME, "group_by": ["other"], "buckets": []},),
        {},
    ),
    (
        "_statistics",
        (
            {
                "from": _TIME,
                "to": _TIME,
                "group_by": [],
                "buckets": [
                    {
                        "dimensions": [],
                        "calls": 0,
                        "attempts": 0,
                        "units": [],
                        "cost": "0",
                        "currency": "EUR",
                    }
                ]
                * 1001,
            },
        ),
        {},
    ),
    ("_page", ({"has_more": True},), {}),
    ("_error_value", ({"code": "other", "message": "Bad."},), {"status": 500}),
    ("_api_string", ("Bad",), {}),
    ("_json_object", (b"not-json",), {}),
    ("_object", ([],), {}),
    ("_closed", ({"extra": 1}, {"required"}), {}),
    ("_array", ({}, "array"), {}),
    ("_string", (1, "string"), {}),
    ("_integer", (True, "integer"), {"minimum": 0}),
    ("_number", (math.inf, "number"), {}),
    ("_boolean", (1, "Boolean"), {}),
    ("_decimal", ("-1", "decimal"), {}),
    ("_currency", ("usd",), {}),
    ("_response_size", (b"xx", 1), {}),
    (
        "_assignment",
        (
            invalid_assignment(
                effective_chain=[
                    {"provider_model_api_name": "route-a"},
                    {"provider_model_api_name": "route-a"},
                ]
            ),
        ),
        {},
    ),
    ("_assignment", (invalid_assignment(direct_chain=[]),), {}),
    (
        "_assignment",
        (invalid_assignment(observed_requirements=["text_input", "text_input"]),),
        {},
    ),
    (
        "_assignment",
        (invalid_assignment(definition_kind="implicit"),),
        {},
    ),
    ("_constraints", ({"embedding_dimensions": []},), {}),
    ("_constraints", ({"embedding_dimensions": [65_537]},), {}),
    ("_constraints", ({"embedding_dimensions": [1, 1]},), {}),
    ("_constraints", ({"max_input_images": 9},), {}),
    ("_price", ({"currency": "EUR", "unit_prices": []},), {}),
    (
        "_price",
        (
            {
                "currency": "EUR",
                "unit_prices": [
                    {"unit": "request", "amount": "1"},
                    {"unit": "request", "amount": "2"},
                ],
            },
        ),
        {},
    ),
    (
        "_provider_model",
        (
            {
                "api_name": "route",
                "display_name": "Route",
                "input_modalities": [],
                "output_modalities": ["text"],
                "capabilities": [],
            },
        ),
        {},
    ),
    (
        "_statistics",
        (
            {
                "from": _TIME,
                "to": _NEXT_TIME,
                "group_by": ["date", "date"],
                "buckets": [],
            },
        ),
        {},
    ),
    (
        "_statistics_bucket",
        (
            {
                "dimensions": [],
                "calls": 0,
                "attempts": 0,
                "units": [],
                "cost": "0",
                "currency": "EUR",
            },
        ),
        {"expected_dimensions": 1},
    ),
    ("_bounded_array", ([None, None], "items", 1), {}),
    ("_bounded_response_string", ("xx", "text", 1), {}),
    ("_bounded_nonempty_response_string", ("", "text", 1), {}),
    ("_optional_int", ({"value": 2}, "value"), {"maximum": 1}),
    (
        "_error_value",
        ({"code": "not_found", "message": ""},),
        {"status": 404},
    ),
    (
        "_error_value",
        (
            {
                "code": "not_found",
                "message": "Missing.",
                "details": {"field": "x" * 201},
            },
        ),
        {"status": 404},
    ),
    ("_page", ({"has_more": True, "next_cursor": "x" * 501},), {}),
    ("_workspace", (updated(workspace(), display_name=""),), {}),
    (
        "_workspace",
        (updated(workspace(), created_at="2026-08-24T00:00:00"),),
        {},
    ),
    (
        "_workspace",
        (updated(workspace(), created_at="2026-02-30T00:00:00Z"),),
        {},
    ),
    ("_assignment", (invalid_assignment(api_name="Bad"),), {}),
    ("_assignment", (invalid_assignment(display_name=""),), {}),
    (
        "_assignment",
        (invalid_assignment(defined_by_service_api_name="Bad"),),
        {},
    ),
    (
        "_assignment",
        (invalid_assignment(inherits_assignment_api_name="Bad"),),
        {},
    ),
    ("_assignment", (invalid_assignment(created_at="not-time"),), {}),
    ("_service_key", (updated(service_key(), id=""),), {}),
    ("_service_key", (updated(service_key(), name="x" * 201),), {}),
    ("_service_key", (updated(service_key(), created_at="not-time"),), {}),
    (
        "_price",
        (
            {
                "currency": "EUR",
                "unit_prices": [{"unit": "request", "amount": "1"}],
                "source": "x" * 501,
            },
        ),
        {},
    ),
    (
        "_price",
        (
            {
                "currency": "EUR",
                "unit_prices": [{"unit": "request", "amount": "1"}],
                "synchronized_at": "not-time",
            },
        ),
        {},
    ),
    (
        "_unit_price",
        ({"unit": "request", "amount": "1" * 65},),
        {},
    ),
    (
        "_provider_model",
        (updated(provider_model(), display_name=""),),
        {},
    ),
    ("_media_job", (updated(media_job(), id="x" * 201),), {}),
    ("_media_job", (updated(media_job(), workspace_api_name="Bad"),), {}),
    ("_media_job", (updated(media_job(), completed_at="not-time"),), {}),
    ("_media_content", ({"media_type": "", "size_bytes": 1},), {}),
    (
        "_statistics",
        ({"from": "not-time", "to": _NEXT_TIME, "group_by": [], "buckets": []},),
        {},
    ),
    (
        "_model_response",
        (
            {
                "output_type": "standard",
                "provider_model_api_name": "route",
                "content": [],
                "usage": usage(),
            },
        ),
        {},
    ),
    (
        "_model_response",
        (
            {
                "output_type": "structured_json",
                "provider_model_api_name": "route",
                "structured_output_json": "[",
                "usage": usage(),
            },
        ),
        {},
    ),
    (
        "_tool_call",
        ({"type": "tool_call", "id": "", "name": "tool", "arguments_json": "{}"},),
        {},
    ),
    (
        "_tool_call",
        ({"type": "tool_call", "id": "id", "name": "tool", "arguments_json": "[]"},),
        {},
    ),
]


@pytest.mark.parametrize(("name", "args", "kwargs"), _PRIVATE_PARSER_CASES)
def test_defensive_native_response_parsers_are_closed(
    name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    """Malformed native values fail at the exact typed SDK boundary."""
    with pytest.raises(RouterProtocolError):
        private(name)(*args, **kwargs)


def test_private_value_validation_covers_invalid_text_and_request_json() -> None:
    """Invalid Unicode and cyclic request data cannot enter HTTP transport."""
    with pytest.raises(ValueError, match=r".+"):
        private("_text")("\ud800", "text", 10)
    with pytest.raises(ValueError, match=r".+"):
        private("_text")("", "text", 10)
    cyclic: dict[str, object] = {}
    cyclic["value"] = cyclic
    with pytest.raises(ValueError, match=r".+"):
        private("_json_bytes")(cyclic)


def test_missing_response_header_returns_empty_value() -> None:
    """A missing case-insensitive response header has one safe empty value."""
    assert private("_header")({}, "Content-Type") == ""


def test_optional_request_fields_and_parser_loop_branches() -> None:
    """Empty optional fields still produce valid closed native requests."""
    pending_audio = media_job("pending")
    pending_audio["kind"] = "audio"
    transport = FakeRouterTransport(
        [
            response(
                200,
                {
                    "provider_model_api_name": "route-a",
                    "embeddings": [{"index": 0, "values": [1]}],
                    "usage": usage(),
                },
            ),
            response(202, pending_audio),
            response(
                200,
                {"from": _TIME, "to": _NEXT_TIME, "group_by": [], "buckets": []},
            ),
        ]
    )
    subject = client(transport)
    subject.create_embedding("main", ExactModelSelector("route-a"), ("one",))
    subject.create_media_job(
        "main", ExactModelSelector("route-a"), MediaKind.AUDIO, "Speak."
    )
    subject.get_statistics(_TIME, _NEXT_TIME)
    private("_model_body")(
        ModelCall(
            "main",
            AssignmentSelector("workflow"),
            (opendle.UserMessage((TextInputPart("Text."),)),),
        )
    )
    assert private("_constraints")({}) is not None
    assert (
        private("_header")(
            {"X-Test": "value", "Content-Type": "application/json"}, "Content-Type"
        )
        == "application/json"
    )

    leading_blank = RouterStreamResponse(
        200,
        {"Content-Type": "text/event-stream"},
        (
            b'\n\nevent: start\ndata: {"provider_model_api_name":"route-a"}\n\n'
            b'event: completed\ndata: {"provider_model_api_name":"route-a","usage":'
            + json.dumps(usage()).encode()
            + b"}\n\n",
        ),
    )
    assert (
        len(
            tuple(
                client(FakeRouterTransport(streams=[leading_blank])).stream_model(
                    model_call()
                )
            )
        )
        == _EXPECTED_PAGE_COUNT
    )


class LocalRouterHandler(BaseHTTPRequestHandler):
    """Serve a small localhost Router for standard-library transport tests."""

    stream_calls = 0

    def do_GET(self) -> None:
        """Return success, native error, or redirect responses."""
        if self.path.startswith("/v1/workspaces?"):
            self._send(200, page([]))
            return
        if self.path == "/v1/workspaces/missing":
            self._send(404, {"error": {"code": "not_found", "message": "Missing."}})
            return
        if self.path == "/v1/workspaces/duplicate":
            body = json.dumps(workspace()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(302)
        self.send_header("Location", "/v1/workspaces/missing")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {"error": {"code": "not_found", "message": "No redirect."}}
            ).encode()
        )

    def do_POST(self) -> None:
        """Return one successful stream and then one HTTP stream error."""
        type(self).stream_calls += 1
        if type(self).stream_calls == 1:
            body = b"".join(
                (
                    b'event: start\ndata: {"provider_model_api_name":"route-a"}\n\n',
                    b"event: completed\ndata: {"
                    b'"provider_model_api_name":"route-a","usage":'
                    + json.dumps(usage()).encode()
                    + b"}\n\n",
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send(
            400,
            {"error": {"code": "invalid_request", "message": "Invalid stream."}},
        )

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the test server quiet."""
        del format, args

    def _send(self, status: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_default_transport_disables_environment_proxy_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service-key transport installs no environment proxy handler."""
    monkeypatch.setenv("HTTP_PROXY", "http://hostile.invalid:9000")
    monkeypatch.setenv("HTTPS_PROXY", "http://hostile.invalid:9000")
    transport = private("_UrllibTransport")(1024)
    opener = vars(transport)["_opener"]
    handlers = cast("Iterable[object]", vars(opener)["handlers"])
    proxy_handlers = tuple(
        handler for handler in handlers if isinstance(handler, ProxyHandler)
    )
    assert proxy_handlers == ()


def test_standard_library_transport_uses_localhost_and_rejects_redirects() -> None:
    """The default transport handles complete, error, stream, and redirect paths."""
    LocalRouterHandler.stream_calls = 0
    server = HTTPServer(("127.0.0.1", 0), LocalRouterHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        subject = RouterClient(
            base_url=f"http://127.0.0.1:{server.server_port}",
            service_key=_SERVICE_KEY,
        )
        assert subject.list_workspaces().items == ()
        with pytest.raises(RouterNotFoundError):
            subject.get_workspace("missing")
        with pytest.raises(RouterNotFoundError, match="redirect"):
            subject.get_workspace("redirect")
        with pytest.raises(RouterProtocolError, match="duplicate"):
            subject.get_workspace("duplicate")
        assert isinstance(
            tuple(subject.stream_model(model_call()))[-1], ModelStreamCompleted
        )
        with pytest.raises(RouterValidationError):
            tuple(subject.stream_model(model_call()))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
