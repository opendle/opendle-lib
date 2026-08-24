"""Dependency-free official client for the LLM Router native service API."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, override
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from opendle.router import (
    AssignmentSelector,
    AssistantContentPart,
    CallFailurePhase,
    ExactModelSelector,
    ImageInputPart,
    ModelCall,
    ModelCallError,
    ModelCallResult,
    ModelSelector,
    RouterContractError,
    StructuredModelCallResult,
    TextOutputPart,
    ToolCallPart,
    Usage,
    UsageItem,
    UsageUnit,
    _model_call_value,  # pyright: ignore[reportPrivateUsage]
    normalize_tags,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from http.client import HTTPMessage

__all__ = [
    "Assignment",
    "AssignmentDefinitionKind",
    "AssignmentPage",
    "AvailableProviderModel",
    "AvailableProviderModelPage",
    "EmbeddingResult",
    "EmbeddingVector",
    "InputModality",
    "MediaContent",
    "MediaContentResult",
    "MediaJob",
    "MediaJobState",
    "MediaKind",
    "ModelCapability",
    "ModelConstraints",
    "ModelResponse",
    "ModelStreamCompleted",
    "ModelStreamEvent",
    "ModelStreamStart",
    "ModelStreamTextDelta",
    "ModelStreamToolCall",
    "ObservedRequirement",
    "OutputModality",
    "PageInfo",
    "Price",
    "ProviderModelCandidate",
    "ReasoningLevel",
    "RouterAPIError",
    "RouterAssignmentCycleError",
    "RouterAuthenticationError",
    "RouterAuthorizationError",
    "RouterClient",
    "RouterConflictError",
    "RouterContentUnavailableError",
    "RouterError",
    "RouterInternalError",
    "RouterNotFoundError",
    "RouterPageLimitError",
    "RouterProtocolError",
    "RouterRateLimitError",
    "RouterResponseLimitError",
    "RouterStreamError",
    "RouterStreamResponse",
    "RouterTransport",
    "RouterTransportError",
    "RouterTransportResponse",
    "RouterUnavailableError",
    "RouterUpstreamError",
    "RouterValidationError",
    "ServiceKey",
    "ServiceKeyCreated",
    "ServiceKeyPage",
    "StatisticsBucket",
    "StatisticsDimension",
    "StatisticsResult",
    "UnitPrice",
    "Workspace",
    "WorkspacePage",
]

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)
type JsonObject = dict[str, JsonValue]
type ModelResponse = ModelCallResult | StructuredModelCallResult

_API_NAME = re.compile(r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ASSIGNMENT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DECIMAL = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RFC3339_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_DEFAULT_MAXIMUM_RESPONSE_BYTES = 16 * 1024 * 1024
_MAXIMUM_COMPLETE_REQUEST_BYTES = 70 * 1024 * 1024
_MAXIMUM_EVENT_BYTES = 2 * 1024 * 1024
_MAXIMUM_STREAM_EVENTS = 100_000
_MAXIMUM_STREAM_OUTPUT_BYTES = 10_000_000
_MAXIMUM_STREAM_RESPONSE_BYTES = 128 * 1024 * 1024
_MAXIMUM_REQUEST_TIMEOUT_SECONDS = 900
_MAXIMUM_MEDIA_WAIT_SECONDS = 86_400
_MAXIMUM_RESPONSE_HEADERS = 100
_MAXIMUM_RESPONSE_HEADER_BYTES = 64 * 1024
_HTTP_SUCCESS_MINIMUM = 200
_HTTP_SUCCESS_LIMIT = 300
_HTTP_OK = 200
_HTTP_NO_CONTENT = 204
_HTTP_STATUS_MINIMUM = 100
_HTTP_STATUS_MAXIMUM = 599
_MAXIMUM_ASSIGNMENT_CHAIN = 16
_MAXIMUM_EMBEDDING_ITEMS = 32
_MAXIMUM_EMBEDDING_ITEM_BYTES = 32_768
_MAXIMUM_EMBEDDING_BATCH_BYTES = 262_144
_MAXIMUM_EMBEDDING_DIMENSIONS = 65_536
_MAXIMUM_SIGNED_32_BIT_INTEGER = 2_147_483_647
_MAXIMUM_INPUT_IMAGES = 8
_MAXIMUM_PAGE_SIZE = 200
_MAXIMUM_STATISTICS_GROUPS = 8
_MAXIMUM_STATISTICS_BUCKETS = 1_000
_MAXIMUM_ASSIGNMENT_NAME = 127
_MAXIMUM_SERVICE_KEY = 500
_MINIMUM_SERVICE_KEY = 32
_PRINTABLE_ASCII_START = 0x21
_PRINTABLE_ASCII_END = 0x7E
_ASCII_SPACE = 0x20
_ASCII_DELETE = 0x7F
_CRITICAL_RESPONSE_HEADERS = frozenset(
    {"content-type", "content-length", "content-encoding"}
)


class RouterError(RuntimeError):
    """Base error for the official Router client."""


class RouterProtocolError(RouterError):
    """Report a response that does not match the native Router contract."""

    def __init__(self, message: str, *, phase: CallFailurePhase | None = None) -> None:
        """Initialize one safe protocol failure and optional stream phase."""
        self.phase = phase
        super().__init__(message)


class RouterResponseLimitError(RouterProtocolError):
    """Report a successful response that exceeds the configured byte limit."""


class RouterPageLimitError(RouterError):
    """Report pagination that exceeds the caller-selected page limit."""


class RouterTransportError(RouterError):
    """Report a safe transport failure without transport or credential detail."""

    def __init__(self, *, uncertain_result: bool) -> None:
        """Initialize the safe failure and its result uncertainty."""
        self.uncertain_result = uncertain_result
        message = (
            "The Router connection ended with an uncertain result."
            if uncertain_result
            else "The Router connection could not complete."
        )
        super().__init__(message)


class RouterStreamError(RouterError):
    """Report an incomplete model stream and its safe failure phase."""

    def __init__(self, *, phase: CallFailurePhase) -> None:
        """Initialize an incomplete stream failure."""
        self.phase = phase
        super().__init__("The Router model stream ended before a terminal event.")


class RouterAPIError(RouterError):
    """Report one stable safe error from the native Router API."""

    def __init__(  # noqa: PLR0913 - Native errors have fixed safe fields.
        self,
        code: str,
        message: str,
        *,
        status: int,
        field_name: str | None = None,
        reason: str | None = None,
        phase: CallFailurePhase | None = None,
    ) -> None:
        """Initialize the safe error fields."""
        self.code = code
        self.status = status
        self.field_name = field_name
        self.reason = reason
        self.phase = phase
        super().__init__(message)


class RouterAuthenticationError(RouterAPIError):
    """Report a missing or invalid service key."""


class RouterAuthorizationError(RouterAPIError):
    """Report an operation outside the authenticated service authority."""


class RouterValidationError(RouterAPIError):
    """Report a native request validation error."""


class RouterNotFoundError(RouterAPIError):
    """Report a resource that is absent from the service scope."""


class RouterConflictError(RouterAPIError):
    """Report a conflicting resource or relationship."""


class RouterAssignmentCycleError(RouterAPIError):
    """Report an invalid assignment inheritance cycle."""


class RouterUnavailableError(RouterAPIError):
    """Report that no eligible provider-model can accept a call."""


class RouterUpstreamError(RouterAPIError):
    """Report a safe provider-neutral upstream failure."""


class RouterContentUnavailableError(RouterAPIError):
    """Report media content that is not ready or retained."""


class RouterRateLimitError(RouterAPIError):
    """Report an applicable Router request limit."""


class RouterInternalError(RouterAPIError):
    """Report a safe internal Router failure."""


_ERROR_TYPES: dict[str, type[RouterAPIError]] = {
    "authentication_required": RouterAuthenticationError,
    "permission_denied": RouterAuthorizationError,
    "invalid_request": RouterValidationError,
    "not_found": RouterNotFoundError,
    "conflict": RouterConflictError,
    "assignment_cycle": RouterAssignmentCycleError,
    "provider_unavailable": RouterUnavailableError,
    "upstream_failed": RouterUpstreamError,
    "content_unavailable": RouterContentUnavailableError,
    "rate_limited": RouterRateLimitError,
    "internal_error": RouterInternalError,
}


class ReasoningLevel(Enum):
    """Select one native common reasoning level."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AssignmentDefinitionKind(Enum):
    """Name the effective form of one assignment definition."""

    IMPLICIT = "implicit"
    INHERITED_ASSIGNMENT = "inherited_assignment"
    DIRECT_CHAIN = "direct_chain"


class ObservedRequirement(Enum):
    """Name one capability or modality observed on an assignment."""

    TEXT_INPUT = "text_input"
    IMAGE_INPUT = "image_input"
    TEXT_OUTPUT = "text_output"
    STRUCTURED_JSON_OUTPUT = "structured_json_output"
    TOOL_CALLING = "tool_calling"
    STREAMING = "streaming"
    REASONING = "reasoning"
    EMBEDDING_OUTPUT = "embedding_output"
    IMAGE_OUTPUT = "image_output"
    VIDEO_OUTPUT = "video_output"
    AUDIO_OUTPUT = "audio_output"


class InputModality(Enum):
    """Name one provider-model input modality."""

    TEXT = "text"
    IMAGE = "image"


class OutputModality(Enum):
    """Name one provider-model output modality."""

    TEXT = "text"
    STRUCTURED_JSON = "structured_json"
    EMBEDDING = "embedding"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class ModelCapability(Enum):
    """Name one provider-model call capability."""

    TOOL_CALLING = "tool_calling"
    STREAMING = "streaming"
    REASONING = "reasoning"


class MediaKind(Enum):
    """Select one native generated-media kind."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class MediaJobState(Enum):
    """Name one forward-only media job state."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StatisticsDimension(Enum):
    """Name one bounded statistics grouping dimension."""

    DATE = "date"
    SERVICE = "service"
    WORKSPACE = "workspace"
    ASSIGNMENT = "assignment"
    PROVIDER_MODEL = "provider_model"
    OUTCOME = "outcome"
    TAG = "tag"


@dataclass(frozen=True, slots=True)
class PageInfo:
    """Contain bounded cursor pagination state."""

    has_more: bool
    next_cursor: str | None = None


class _PageValue(Protocol):
    @property
    def page(self) -> PageInfo: ...


_PageT = TypeVar("_PageT", bound=_PageValue)


@dataclass(frozen=True, slots=True)
class Workspace:
    """Contain one workspace in the authenticated service."""

    api_name: str
    display_name: str
    created_at: str


@dataclass(frozen=True, slots=True)
class WorkspacePage:
    """Contain one workspace page."""

    items: tuple[Workspace, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class ProviderModelCandidate:
    """Name one exact provider-model candidate in assignment order."""

    provider_model_api_name: str


@dataclass(frozen=True, slots=True)
class Assignment:
    """Contain one current service assignment and its effective chain."""

    api_name: str
    display_name: str
    definition_kind: AssignmentDefinitionKind
    effective_chain: tuple[ProviderModelCandidate, ...]
    observed_requirements: tuple[ObservedRequirement, ...]
    defined_by_service_api_name: str | None = None
    inherits_assignment_api_name: str | None = None
    direct_chain: tuple[ProviderModelCandidate, ...] | None = None
    reasoning_level: ReasoningLevel | None = None
    last_used_at: str | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class AssignmentPage:
    """Contain one assignment page."""

    items: tuple[Assignment, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class ServiceKey:
    """Contain safe metadata for one backend-only service key."""

    id: str
    name: str
    created_at: str
    last_used_at: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceKeyCreated:
    """Contain one new service key and its one-time secret."""

    key: ServiceKey
    secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ServiceKeyPage:
    """Contain one service-key metadata page."""

    items: tuple[ServiceKey, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class ModelConstraints:
    """Contain optional bounded provider-model constraints."""

    embedding_dimensions: tuple[int, ...] = ()
    max_input_images: int | None = None
    max_input_image_bytes: int | None = None
    max_output_duration_seconds: int | None = None
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class UnitPrice:
    """Contain one fixed-decimal price for a typed usage unit."""

    unit: UsageUnit
    amount: str


@dataclass(frozen=True, slots=True)
class Price:
    """Contain one currency-specific provider-model price."""

    currency: str
    unit_prices: tuple[UnitPrice, ...]
    source: str | None = None
    synchronized_at: str | None = None


@dataclass(frozen=True, slots=True)
class AvailableProviderModel:
    """Contain one enabled provider-model safe for service discovery."""

    api_name: str
    display_name: str
    input_modalities: tuple[InputModality, ...]
    output_modalities: tuple[OutputModality, ...]
    capabilities: tuple[ModelCapability, ...]
    constraints: ModelConstraints | None = None
    effective_price: Price | None = None


@dataclass(frozen=True, slots=True)
class AvailableProviderModelPage:
    """Contain one service-safe provider-model page."""

    items: tuple[AvailableProviderModel, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    """Contain one finite embedding vector at its input index."""

    index: int
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Contain one complete ordered embedding batch result."""

    route: ExactModelSelector
    embeddings: tuple[EmbeddingVector, ...]
    usage: Usage


@dataclass(frozen=True, slots=True)
class MediaContent:
    """Describe retained generated media content."""

    media_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class MediaJob:
    """Contain one media job in the authenticated service scope."""

    id: str
    workspace_api_name: str
    route: ExactModelSelector
    kind: MediaKind
    state: MediaJobState
    created_at: str
    content: MediaContent | None = None
    error: RouterAPIError | None = None
    completed_at: str | None = None


@dataclass(frozen=True, slots=True)
class MediaContentResult:
    """Contain generated bytes and their Router response media type."""

    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class StatisticsBucket:
    """Contain one bounded statistics group."""

    dimensions: tuple[str, ...]
    calls: int
    attempts: int
    units: tuple[UsageItem, ...]
    cost: str
    currency: str


@dataclass(frozen=True, slots=True)
class StatisticsResult:
    """Contain one bounded service statistics result."""

    from_time: str
    to_time: str
    group_by: tuple[StatisticsDimension, ...]
    buckets: tuple[StatisticsBucket, ...]


@dataclass(frozen=True, slots=True)
class ModelStreamStart:
    """Report the exact route selected for a model stream."""

    route: ExactModelSelector
    event: str = "start"


@dataclass(frozen=True, slots=True)
class ModelStreamTextDelta:
    """Contain one visible non-empty text delta."""

    delta: str
    event: str = "text_delta"


@dataclass(frozen=True, slots=True)
class ModelStreamToolCall:
    """Contain one visible complete tool call from a stream."""

    tool_call: ToolCallPart
    event: str = "tool_call"


@dataclass(frozen=True, slots=True)
class ModelStreamCompleted:
    """Contain the exact route and usage for a completed stream."""

    route: ExactModelSelector
    usage: Usage
    event: str = "completed"


type ModelStreamEvent = (
    ModelStreamStart | ModelStreamTextDelta | ModelStreamToolCall | ModelStreamCompleted
)


@dataclass(frozen=True, slots=True)
class RouterTransportResponse:
    """Contain one complete response from a custom Router transport."""

    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class RouterStreamResponse:
    """Contain one streamed response from a custom Router transport."""

    status: int
    headers: Mapping[str, str]
    chunks: Iterable[bytes]


class RouterTransport(Protocol):
    """Send complete and streamed native Router HTTP requests."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RouterTransportResponse:
        """Return one complete response without following redirects."""
        ...

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> RouterStreamResponse:
        """Return one streamed response without following redirects."""
        ...


class _NoRedirect(HTTPRedirectHandler):
    @override
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl


class _UrllibTransport:
    def __init__(self, maximum_response_bytes: int) -> None:
        self._maximum_response_bytes = maximum_response_bytes
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RouterTransportResponse:
        request = Request(  # noqa: S310 - RouterClient validates the URL scheme.
            url, data=body, headers=dict(headers), method=method
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return RouterTransportResponse(
                    response.status,
                    _response_headers(response.headers.raw_items()),
                    response.read(self._maximum_response_bytes + 1),
                )
        except HTTPError as error:
            try:
                return RouterTransportResponse(
                    error.code,
                    _response_headers(error.headers.raw_items()),
                    error.read(self._maximum_response_bytes + 1),
                )
            finally:
                error.close()

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> RouterStreamResponse:
        request = Request(  # noqa: S310 - RouterClient validates the URL scheme.
            url, data=body, headers=dict(headers), method=method
        )
        try:
            response = self._opener.open(request, timeout=timeout)
        except HTTPError as error:
            try:
                return RouterStreamResponse(
                    error.code,
                    _response_headers(error.headers.raw_items()),
                    (error.read(self._maximum_response_bytes + 1),),
                )
            finally:
                error.close()

        try:
            status = response.status
            response_headers = _response_headers(response.headers.raw_items())
        except BaseException:
            response.close()
            raise

        def chunks() -> Iterator[bytes]:
            with response:
                while chunk := response.read(65_536):
                    yield chunk

        return RouterStreamResponse(
            status,
            response_headers,
            chunks(),
        )


class RouterClient:
    """Call one Router with one private backend-only service key."""

    __slots__ = (
        "_base_url",
        "_maximum_response_bytes",
        "_service_key",
        "_timeout",
        "_transport",
    )

    def __init__(
        self,
        *,
        base_url: str,
        service_key: str,
        timeout: float = 900.0,
        maximum_success_response_bytes: int = _DEFAULT_MAXIMUM_RESPONSE_BYTES,
        transport: RouterTransport | None = None,
    ) -> None:
        """Initialize one dependency-free service-scoped client."""
        self._base_url = _base_url(base_url)
        if not _valid_service_key(service_key):
            msg = "The Router service key is invalid."
            raise ValueError(msg)
        if service_key in self._base_url or service_key in unquote(self._base_url):
            msg = "The Router base URL cannot contain the service key."
            raise ValueError(msg)
        raw_timeout = cast("object", timeout)
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or not math.isfinite(raw_timeout)
            or not 0 < raw_timeout <= _MAXIMUM_REQUEST_TIMEOUT_SECONDS
        ):
            msg = (
                "The Router timeout must be greater than 0 and no more than "
                "900 seconds."
            )
            raise ValueError(msg)
        raw_maximum = cast("object", maximum_success_response_bytes)
        if (
            isinstance(raw_maximum, bool)
            or not isinstance(raw_maximum, int)
            or raw_maximum <= 0
        ):
            msg = "The Router response byte limit must be positive."
            raise ValueError(msg)
        self._service_key = service_key
        self._timeout = timeout
        self._maximum_response_bytes = maximum_success_response_bytes
        self._transport = transport or _UrllibTransport(maximum_success_response_bytes)

    def __repr__(self) -> str:
        """Return a representation that does not contain the service key."""
        return (
            f"RouterClient(base_url={self._base_url!r}, timeout={self._timeout!r}, "
            f"maximum_success_response_bytes={self._maximum_response_bytes!r})"
        )

    async def __call__(self, call: ModelCall, /) -> ModelCallResult:
        """Adapt this client to the async shared-harness model caller port."""
        try:
            result = await asyncio.to_thread(self.model_call, call)
        except (RouterUnavailableError, RouterUpstreamError) as error:
            raise ModelCallError(
                error.code,
                str(error),
                phase=CallFailurePhase.BEFORE_VISIBLE_OUTPUT,
            ) from None
        except RouterTransportError:
            code = "transport_error"
            message = "The Router connection ended with an uncertain result."
            raise ModelCallError(
                code,
                message,
                phase=CallFailurePhase.UNCERTAIN,
            ) from None
        if isinstance(result, StructuredModelCallResult):
            code = "invalid_response"
            message = "The harness model caller received structured output."
            raise ModelCallError(
                code,
                message,
                phase=CallFailurePhase.BEFORE_VISIBLE_OUTPUT,
            )
        return result

    def list_workspaces(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> WorkspacePage:
        """Read one bounded workspace page."""
        return _workspace_page(
            self._get("/v1/workspaces", _page_query(cursor, limit)),
            maximum_items=limit,
        )

    def iter_workspace_pages(
        self, *, max_pages: int, cursor: str | None = None, limit: int = 50
    ) -> Iterator[WorkspacePage]:
        """Iterate workspace pages under one strict caller page bound."""
        return self._iterate_pages(
            lambda current: self.list_workspaces(cursor=current, limit=limit),
            max_pages=max_pages,
            cursor=cursor,
        )

    def create_workspace(self, api_name: str, display_name: str) -> Workspace:
        """Create one workspace in the authenticated service."""
        _api_name(api_name, "workspace")
        _text(display_name, "workspace display name", 200)
        workspace = _workspace(
            self._json(
                "POST",
                "/v1/workspaces",
                {"api_name": api_name, "display_name": display_name},
                expected_status=201,
                uncertain=True,
            )
        )
        if workspace.api_name != api_name or workspace.display_name != display_name:
            msg = "The Router workspace does not match its creation request."
            raise RouterProtocolError(msg)
        return workspace

    def get_workspace(self, workspace_api_name: str) -> Workspace:
        """Read one workspace in the authenticated service."""
        workspace = _workspace(
            self._get(f"/v1/workspaces/{_segment(workspace_api_name, api_name=True)}")
        )
        if workspace.api_name != workspace_api_name:
            msg = "The Router workspace does not match its request path."
            raise RouterProtocolError(msg)
        return workspace

    def delete_workspace(self, workspace_api_name: str) -> None:
        """Delete one workspace and its Router-owned dependent data."""
        self._empty(
            "DELETE", f"/v1/workspaces/{_segment(workspace_api_name, api_name=True)}"
        )

    def list_assignments(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> AssignmentPage:
        """Read one bounded assignment page."""
        return _assignment_page(
            self._get("/v1/assignments", _page_query(cursor, limit)),
            maximum_items=limit,
        )

    def iter_assignment_pages(
        self, *, max_pages: int, cursor: str | None = None, limit: int = 50
    ) -> Iterator[AssignmentPage]:
        """Iterate assignment pages under one strict caller page bound."""
        return self._iterate_pages(
            lambda current: self.list_assignments(cursor=current, limit=limit),
            max_pages=max_pages,
            cursor=cursor,
        )

    def get_assignment(self, assignment_api_name: str) -> Assignment:
        """Read one current service assignment."""
        assignment = _assignment(
            self._get(f"/v1/assignments/{_assignment_segment(assignment_api_name)}")
        )
        if assignment.api_name != assignment_api_name:
            msg = "The Router assignment does not match its request path."
            raise RouterProtocolError(msg)
        return assignment

    def put_assignment(
        self,
        assignment_api_name: str,
        *,
        inherits_assignment_api_name: str | None = None,
        direct_chain: Sequence[str] | None = None,
        display_name: str | None = None,
        reasoning_level: ReasoningLevel | None = None,
    ) -> Assignment:
        """Create or replace one complete local assignment definition."""
        name = _assignment_segment(assignment_api_name)
        if (inherits_assignment_api_name is None) == (direct_chain is None):
            msg = "Select exactly one inherited assignment or direct chain."
            raise ValueError(msg)
        body: JsonObject = {}
        if display_name is not None:
            _text(display_name, "assignment display name", 200)
            body["display_name"] = display_name
        expected_chain: tuple[ProviderModelCandidate, ...] | None = None
        if inherits_assignment_api_name is not None:
            _assignment_name(inherits_assignment_api_name)
            body["inherits_assignment_api_name"] = inherits_assignment_api_name
        else:
            chain = tuple(cast("Sequence[str]", direct_chain))
            if not 1 <= len(chain) <= _MAXIMUM_ASSIGNMENT_CHAIN or len(
                set(chain)
            ) != len(chain):
                msg = (
                    "The direct assignment chain must contain 1 through 16 "
                    "unique routes."
                )
                raise ValueError(msg)
            for route in chain:
                _api_name(route, "provider-model")
            expected_chain = tuple(ProviderModelCandidate(route) for route in chain)
            body["direct_chain"] = [
                {"provider_model_api_name": route} for route in chain
            ]
        if reasoning_level is not None:
            body["reasoning_level"] = reasoning_level.value
        assignment = _assignment(
            self._json(
                "PUT",
                f"/v1/assignments/{name}",
                body,
                expected_status=200,
                uncertain=True,
            )
        )
        expected_kind = (
            AssignmentDefinitionKind.INHERITED_ASSIGNMENT
            if inherits_assignment_api_name is not None
            else AssignmentDefinitionKind.DIRECT_CHAIN
        )
        if (
            assignment.api_name != assignment_api_name
            or assignment.definition_kind is not expected_kind
            or assignment.inherits_assignment_api_name != inherits_assignment_api_name
            or assignment.direct_chain != expected_chain
            or assignment.reasoning_level is not reasoning_level
            or (display_name is not None and assignment.display_name != display_name)
        ):
            msg = "The Router assignment does not match its replacement request."
            raise RouterProtocolError(msg)
        return assignment

    def delete_assignment(self, assignment_api_name: str) -> None:
        """Delete one local assignment definition."""
        self._empty(
            "DELETE", f"/v1/assignments/{_assignment_segment(assignment_api_name)}"
        )

    def remove_observed_assignment_requirement(
        self, assignment_api_name: str, requirement: ObservedRequirement
    ) -> None:
        """Remove one observed assignment requirement."""
        path = (
            f"/v1/assignments/{_assignment_segment(assignment_api_name)}"
            f"/observed-requirements/{quote(requirement.value, safe='')}"
        )
        self._empty("DELETE", path)

    def list_service_keys(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> ServiceKeyPage:
        """Read one bounded page of safe service-key metadata."""
        return _service_key_page(
            self._get("/v1/service-keys", _page_query(cursor, limit)),
            maximum_items=limit,
        )

    def create_service_key(self, name: str) -> ServiceKeyCreated:
        """Create one backend-only key and return its secret once."""
        _text(name, "service key name", 200)
        value = _closed(
            self._json(
                "POST",
                "/v1/service-keys",
                {"name": name},
                expected_status=201,
                uncertain=True,
            ),
            {"key", "secret"},
        )
        secret = _string(value["secret"], "service key secret")
        if not _MINIMUM_SERVICE_KEY <= len(secret) <= _MAXIMUM_SERVICE_KEY:
            msg = "The Router service key secret has an invalid length."
            raise RouterProtocolError(msg)
        key = _service_key(_object(value["key"]))
        if key.name != name or not _valid_service_key(secret):
            msg = "The Router service key does not match its creation request."
            raise RouterProtocolError(msg)
        return ServiceKeyCreated(key, secret)

    def revoke_service_key(self, key_id: str) -> None:
        """Revoke one service key by its opaque identity."""
        self._empty("DELETE", f"/v1/service-keys/{_segment(key_id)}")

    def list_provider_models(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> AvailableProviderModelPage:
        """Read one page of enabled service-safe provider-models."""
        return _provider_model_page(
            self._get("/v1/provider-models", _page_query(cursor, limit)),
            maximum_items=limit,
        )

    def iter_provider_model_pages(
        self, *, max_pages: int, cursor: str | None = None, limit: int = 50
    ) -> Iterator[AvailableProviderModelPage]:
        """Iterate provider-model pages under one strict caller page bound."""
        return self._iterate_pages(
            lambda current: self.list_provider_models(cursor=current, limit=limit),
            max_pages=max_pages,
            cursor=cursor,
        )

    def iter_service_key_pages(
        self, *, max_pages: int, cursor: str | None = None, limit: int = 50
    ) -> Iterator[ServiceKeyPage]:
        """Iterate service-key pages under one strict caller page bound."""
        return self._iterate_pages(
            lambda current: self.list_service_keys(cursor=current, limit=limit),
            max_pages=max_pages,
            cursor=cursor,
        )

    def model_call(self, call: ModelCall) -> ModelResponse:
        """Make one native synchronous model call without an automatic retry."""
        value = self._json(
            "POST",
            "/v1/model-calls",
            _model_body(call),
            expected_status=200,
            uncertain=True,
        )
        result = _model_response(value)
        _validate_result_route(call, result.route)
        if (call.output_schema_json is None) != isinstance(result, ModelCallResult):
            msg = "The Router model result does not match the requested output format."
            raise RouterProtocolError(msg)
        return result

    def stream_model(self, call: ModelCall) -> Iterator[ModelStreamEvent]:
        """Yield one native model stream and require one terminal event."""
        body = _request_json_bytes(_model_body(call))
        response = self._stream_request("/v1/model-streams", body)
        if not _HTTP_SUCCESS_MINIMUM <= response.status < _HTTP_SUCCESS_LIMIT:
            try:
                error_body = _bounded_stream_body(
                    response.chunks, self._maximum_response_bytes
                )
                _validate_declared_length(response.headers, len(error_body))
                _require_json_media_type(response.headers)
                error = _api_error(
                    response.status,
                    _safe_json_object(error_body, self._service_key),
                )
            except RouterProtocolError as protocol_error:
                protocol_error.phase = CallFailurePhase.UNCERTAIN
                raise
            except RouterError:
                raise
            except Exception:  # noqa: BLE001 - Custom iterators can raise any error.
                raise RouterTransportError(uncertain_result=True) from None
            error.phase = CallFailurePhase.BEFORE_VISIBLE_OUTPUT
            raise error
        if response.status != _HTTP_OK:
            msg = "The Router stream returned an unexpected success status."
            raise RouterProtocolError(msg, phase=CallFailurePhase.UNCERTAIN)
        media_type = _header_media_type(response.headers, "Content-Type")
        if media_type != "text/event-stream":
            msg = "The Router stream response media type is invalid."
            raise RouterProtocolError(msg, phase=CallFailurePhase.UNCERTAIN)
        return _validated_stream_events(
            _stream_events(response.chunks, service_key=self._service_key), call
        )

    def create_embedding(
        self,
        workspace_api_name: str,
        selector: ModelSelector,
        inputs: Sequence[str],
        *,
        tags: tuple[str, ...] = (),
    ) -> EmbeddingResult:
        """Make one complete native embedding batch call."""
        _api_name(workspace_api_name, "workspace")
        if isinstance(inputs, (str, bytes, bytearray)):
            msg = "Embedding inputs must be one sequence of text items."
            raise TypeError(msg)
        raw_items = tuple(cast("Sequence[object]", inputs))
        if not 1 <= len(raw_items) <= _MAXIMUM_EMBEDDING_ITEMS:
            msg = "An embedding batch must contain 1 through 32 items."
            raise ValueError(msg)
        items: list[str] = []
        encoded_total = 0
        for item in raw_items:
            if not isinstance(item, str):
                msg = "Each embedding input must be text."
                raise TypeError(msg)
            items.append(item)
            encoded = _text(item, "embedding input", _MAXIMUM_EMBEDDING_ITEM_BYTES)
            if len(encoded) > _MAXIMUM_EMBEDDING_ITEM_BYTES:
                msg = "An embedding input exceeds 32768 UTF-8 bytes."
                raise ValueError(msg)
            encoded_total += len(encoded)
        if encoded_total > _MAXIMUM_EMBEDDING_BATCH_BYTES:
            msg = "The embedding batch exceeds 262144 UTF-8 bytes."
            raise ValueError(msg)
        normalized = normalize_tags(tags)
        if normalized != tags:
            msg = "Embedding tags must be normalized."
            raise ValueError(msg)
        body: JsonObject = {
            "workspace_api_name": workspace_api_name,
            "selector": _selector_value(selector),
            "inputs": list(items),
        }
        if tags:
            body["tags"] = list(tags)
        value = self._json(
            "POST", "/v1/embeddings", body, expected_status=200, uncertain=True
        )
        result = _embedding_result(value, expected_count=len(items))
        _validate_selector_route(selector, result.route)
        return result

    def create_media_job(  # noqa: PLR0913 - Native request has fixed fields.
        self,
        workspace_api_name: str,
        selector: ModelSelector,
        kind: MediaKind,
        prompt: str,
        *,
        input_images: tuple[ImageInputPart, ...] = (),
        tags: tuple[str, ...] = (),
    ) -> MediaJob:
        """Start one native image, video, or audio generation job."""
        _api_name(workspace_api_name, "workspace")
        _text(prompt, "media prompt", 1_000_000)
        if kind is MediaKind.AUDIO and input_images:
            msg = "Audio generation does not accept input images."
            raise ValueError(msg)
        if (
            len(input_images) > _MAXIMUM_INPUT_IMAGES
            or sum(len(image.data) for image in input_images) > 50 * 1024 * 1024
        ):
            msg = "The media input image set is too large."
            raise ValueError(msg)
        normalized = normalize_tags(tags)
        if normalized != tags:
            msg = "Media tags must be normalized."
            raise ValueError(msg)
        body: JsonObject = {
            "workspace_api_name": workspace_api_name,
            "selector": _selector_value(selector),
            "kind": kind.value,
            "prompt": prompt,
        }
        if input_images:
            body["input_images"] = [_image_value(image) for image in input_images]
        if tags:
            body["tags"] = list(tags)
        job = _media_job(
            self._json(
                "POST",
                "/v1/media-jobs",
                body,
                expected_status=202,
                uncertain=True,
            )
        )
        _validate_selector_route(selector, job.route)
        if job.workspace_api_name != workspace_api_name or job.kind is not kind:
            msg = "The Router media job does not match its creation request."
            raise RouterProtocolError(msg)
        return job

    def get_media_job(self, media_job_id: str) -> MediaJob:
        """Read one media job in the authenticated service scope."""
        return self._get_media_job(media_job_id, request_timeout=None)

    def _get_media_job(
        self, media_job_id: str, *, request_timeout: float | None
    ) -> MediaJob:
        job = _media_job(
            self._get(
                f"/v1/media-jobs/{_segment(media_job_id)}",
                request_timeout=request_timeout,
            )
        )
        if job.id != media_job_id:
            msg = "The Router media job does not match its request path."
            raise RouterProtocolError(msg)
        return job

    def wait_media_job(
        self,
        media_job_id: str,
        *,
        timeout: float,
        poll_interval: float = 1.0,
    ) -> MediaJob:
        """Poll one media job until it reaches a terminal state or a bound."""
        raw_timeout = cast("object", timeout)
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or not math.isfinite(raw_timeout)
            or not 0 <= raw_timeout <= _MAXIMUM_MEDIA_WAIT_SECONDS
        ):
            msg = "The media wait timeout must be from 0 through 86400 seconds."
            raise ValueError(msg)
        raw_interval = cast("object", poll_interval)
        if (
            isinstance(raw_interval, bool)
            or not isinstance(raw_interval, (int, float))
            or not math.isfinite(raw_interval)
            or raw_interval <= 0
        ):
            msg = "The media poll interval must be positive and finite."
            raise ValueError(msg)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                msg = "The media job did not finish before the wait timeout."
                raise TimeoutError(msg)
            job = self._get_media_job(
                media_job_id, request_timeout=min(self._timeout, remaining)
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                msg = "The media job did not finish before the wait timeout."
                raise TimeoutError(msg)
            if job.state in {MediaJobState.SUCCEEDED, MediaJobState.FAILED}:
                return job
            time.sleep(min(poll_interval, remaining))

    def get_media_job_content(self, media_job_id: str) -> MediaContentResult:
        """Read retained generated media bytes through the Router."""
        response = self._request(
            "GET",
            f"/v1/media-jobs/{_segment(media_job_id)}/content",
            None,
            uncertain=False,
        )
        if not _HTTP_SUCCESS_MINIMUM <= response.status < _HTTP_SUCCESS_LIMIT:
            _require_json_media_type(response.headers)
            raise _api_error(
                response.status,
                _safe_json_object(response.body, self._service_key),
            )
        if response.status != _HTTP_OK:
            msg = "The Router media content returned an unexpected success status."
            raise RouterProtocolError(msg)
        _response_size(response.body, self._maximum_response_bytes)
        media_type = _header_media_type(response.headers, "Content-Type")
        if not media_type:
            msg = "The generated media response has no media type."
            raise RouterProtocolError(msg)
        if self._service_key.encode("ascii") in response.body:
            msg = "The Router media response contains the service key."
            raise RouterProtocolError(msg)
        return MediaContentResult(media_type, response.body)

    def get_statistics(  # noqa: C901, PLR0913 - Native filters are explicit.
        self,
        from_time: str,
        to_time: str,
        *,
        workspace: str | None = None,
        assignment: str | None = None,
        provider_model: str | None = None,
        outcome: str | None = None,
        tag: str | None = None,
        group_by: tuple[StatisticsDimension, ...] = (),
    ) -> StatisticsResult:
        """Read service statistics through bounded native filters and groups."""
        start = _timestamp(from_time, "statistics start time")
        end = _timestamp(to_time, "statistics end time")
        if end <= start or end - start > timedelta(days=366):
            msg = "The statistics time range is invalid."
            raise ValueError(msg)
        if len(group_by) > _MAXIMUM_STATISTICS_GROUPS or len(set(group_by)) != len(
            group_by
        ):
            msg = "Statistics can use no more than 8 unique groups."
            raise ValueError(msg)
        if workspace is not None:
            _api_name(workspace, "workspace")
        if assignment is not None and assignment != "(exact)":
            _assignment_name(assignment)
        if provider_model is not None:
            _api_name(provider_model, "provider-model")
        if outcome is not None and outcome not in {"succeeded", "failed"}:
            msg = "The statistics outcome filter is invalid."
            raise ValueError(msg)
        if tag is not None:
            normalize_tags((tag,))
        query: list[tuple[str, str]] = [("from", from_time), ("to", to_time)]
        for key, value in (
            ("workspace", workspace),
            ("assignment", assignment),
            ("provider_model", provider_model),
            ("outcome", outcome),
            ("tag", tag),
        ):
            if value is not None:
                query.append((key, value))
        query.extend(("group_by", item.value) for item in group_by)
        result = _statistics(self._get("/v1/statistics", query))
        if (
            datetime.fromisoformat(result.from_time) != start
            or datetime.fromisoformat(result.to_time) != end
            or result.group_by != group_by
        ):
            msg = "The Router statistics result does not match its request."
            raise RouterProtocolError(msg)
        return result

    def _get(
        self,
        path: str,
        query: Sequence[tuple[str, str]] = (),
        *,
        request_timeout: float | None = None,
    ) -> JsonObject:
        url_path = path if not query else f"{path}?{urlencode(query)}"
        return self._json(
            "GET",
            url_path,
            None,
            expected_status=200,
            uncertain=False,
            request_timeout=request_timeout,
        )

    def _empty(self, method: str, path: str) -> None:
        response = self._request(method, path, None, uncertain=True)
        if not _HTTP_SUCCESS_MINIMUM <= response.status < _HTTP_SUCCESS_LIMIT:
            _require_json_media_type(response.headers)
            raise _api_error(
                response.status,
                _safe_json_object(response.body, self._service_key),
            )
        if response.status != _HTTP_NO_CONTENT:
            msg = "The Router empty operation returned an unexpected success status."
            raise RouterProtocolError(msg)
        if response.body:
            msg = "The Router returned a body for an empty response."
            raise RouterProtocolError(msg)

    def _json(  # noqa: PLR0913 - HTTP calls have fixed transport controls.
        self,
        method: str,
        path: str,
        body: JsonObject | None,
        *,
        expected_status: int,
        uncertain: bool,
        request_timeout: float | None = None,
    ) -> JsonObject:
        response = self._request(
            method,
            path,
            None if body is None else _request_json_bytes(body),
            uncertain=uncertain,
            request_timeout=request_timeout,
        )
        _response_size(response.body, self._maximum_response_bytes)
        _require_json_media_type(response.headers)
        value = _safe_json_object(response.body, self._service_key)
        if not _HTTP_SUCCESS_MINIMUM <= response.status < _HTTP_SUCCESS_LIMIT:
            raise _api_error(response.status, value)
        if response.status != expected_status:
            msg = "The Router JSON operation returned an unexpected success status."
            raise RouterProtocolError(msg)
        return value

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        uncertain: bool,
        request_timeout: float | None = None,
    ) -> RouterTransportResponse:
        self._validate_request_secrecy(path, body)
        headers = self._headers("application/json", has_body=body is not None)
        try:
            response = self._transport.request(
                method,
                self._base_url + path,
                headers,
                body,
                self._timeout if request_timeout is None else request_timeout,
            )
        except RouterError:
            raise
        except Exception:  # noqa: BLE001 - Custom transports can raise any error.
            raise RouterTransportError(uncertain_result=uncertain) from None
        response = RouterTransportResponse(
            response.status,
            _validate_complete_response(response),
            response.body,
        )
        self._validate_response_header_secrecy(response.headers)
        _response_size(response.body, self._maximum_response_bytes)
        return response

    def _stream_request(self, path: str, body: bytes) -> RouterStreamResponse:
        self._validate_request_secrecy(path, body)
        try:
            response = self._transport.stream(
                "POST",
                self._base_url + path,
                self._headers("text/event-stream", has_body=True),
                body,
                self._timeout,
            )
        except RouterProtocolError as error:
            error.phase = CallFailurePhase.UNCERTAIN
            raise
        except RouterError:
            raise
        except Exception:  # noqa: BLE001 - Custom transports can raise any error.
            raise RouterTransportError(uncertain_result=True) from None
        try:
            headers = _validate_stream_response(response)
        except RouterProtocolError as error:
            error.phase = CallFailurePhase.UNCERTAIN
            raise
        try:
            self._validate_response_header_secrecy(headers)
        except RouterProtocolError as error:
            error.phase = CallFailurePhase.UNCERTAIN
            raise
        return RouterStreamResponse(response.status, headers, response.chunks)

    def _validate_request_secrecy(self, path: str, body: bytes | None) -> None:
        url = self._base_url + path
        if self._service_key in url or self._service_key in unquote(url):
            msg = "A Router request URL cannot contain the service key."
            raise ValueError(msg)
        if body is not None and _json_string_bytes(self._service_key) in body:
            msg = "A Router request body cannot contain the service key."
            raise ValueError(msg)

    def _validate_response_header_secrecy(self, headers: Mapping[str, str]) -> None:
        if any(
            self._service_key in name or self._service_key in value
            for name, value in headers.items()
        ):
            msg = "The Router response headers contain the service key."
            raise RouterProtocolError(msg)

    def _headers(self, accept: str, *, has_body: bool) -> dict[str, str]:
        headers = {"Accept": accept, "Authorization": f"Bearer {self._service_key}"}
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _iterate_pages(
        self,
        load: Callable[[str | None], _PageT],
        *,
        max_pages: int,
        cursor: str | None,
    ) -> Iterator[_PageT]:
        raw_max_pages = cast("object", max_pages)
        if (
            isinstance(raw_max_pages, bool)
            or not isinstance(raw_max_pages, int)
            or raw_max_pages <= 0
        ):
            msg = "The Router page limit must be positive."
            raise ValueError(msg)
        current = cursor
        for _index in range(max_pages):
            page = load(current)
            yield page
            info = page.page
            if not info.has_more:
                return
            current = info.next_cursor
        msg = "The Router response exceeds the selected page limit."
        raise RouterPageLimitError(msg)


def _model_body(call: ModelCall) -> JsonObject:
    value = cast("JsonObject", _model_call_value(call))
    for message_index, message in enumerate(call.messages):
        if not hasattr(message, "content") or isinstance(message.content, str):
            continue
        for part_index, part in enumerate(message.content):
            if isinstance(part, ImageInputPart):
                messages = cast("list[JsonValue]", value["messages"])
                message_value = _object(messages[message_index])
                content = cast("list[JsonValue]", message_value["content"])
                content[part_index] = _image_value(part)
    return value


def _image_value(image: ImageInputPart) -> JsonObject:
    return {
        "type": "image",
        "media_type": image.media_type,
        "data_base64": base64.b64encode(image.data).decode("ascii"),
    }


def _selector_value(selector: ModelSelector) -> JsonObject:
    if isinstance(selector, AssignmentSelector):
        return {"assignment_api_name": selector.assignment_api_name}
    return {"provider_model_api_name": selector.provider_model_api_name}


def _validate_result_route(call: ModelCall, route: ExactModelSelector) -> None:
    _validate_selector_route(call.selector, route)
    if route in call.excluded_routes:
        msg = "The Router result used an excluded provider-model route."
        raise RouterProtocolError(msg)


def _validate_selector_route(
    selector: ModelSelector, route: ExactModelSelector
) -> None:
    if isinstance(selector, ExactModelSelector) and route != selector:
        msg = "The Router result route does not match the exact selector."
        raise RouterProtocolError(msg)


def _validated_stream_events(
    events: Iterator[ModelStreamEvent], call: ModelCall
) -> Iterator[ModelStreamEvent]:
    route: ExactModelSelector | None = None
    visible = False
    try:
        for event in events:
            if isinstance(event, ModelStreamStart):
                route = event.route
                _validate_result_route(call, route)
            elif isinstance(event, (ModelStreamTextDelta, ModelStreamToolCall)):
                visible = True
            elif event.route != route:
                msg = "The Router completed a stream on a different route."
                raise RouterProtocolError(msg)  # noqa: TRY301
            yield event
    except RouterProtocolError as error:
        if error.phase is None:
            error.phase = (
                CallFailurePhase.AFTER_VISIBLE_OUTPUT
                if visible
                else CallFailurePhase.UNCERTAIN
            )
        raise


def _stream_events(  # noqa: C901, PLR0912, PLR0915 - Fixed SSE state branches.
    chunks: Iterable[bytes],
    *,
    service_key: str | None = None,
) -> Iterator[ModelStreamEvent]:
    buffer = b""
    started = False
    visible = False
    terminal = False
    event_count = 0
    output_bytes = 0
    response_bytes = 0
    try:
        for raw_chunk in cast("Iterable[object]", chunks):
            if not isinstance(raw_chunk, bytes):
                msg = "The Router stream transport returned a non-byte chunk."
                raise RouterProtocolError(msg)  # noqa: TRY301
            response_bytes += len(raw_chunk)
            if response_bytes > _MAXIMUM_STREAM_RESPONSE_BYTES:
                msg = "The Router stream exceeds the wire byte limit."
                raise RouterResponseLimitError(msg)  # noqa: TRY301
            offset = 0
            while offset < len(raw_chunk):
                capacity = _MAXIMUM_EVENT_BYTES + 4 - len(buffer)
                if capacity <= 0:
                    msg = "A Router stream event is too large."
                    raise RouterResponseLimitError(msg)  # noqa: TRY301
                take = min(capacity, len(raw_chunk) - offset)
                buffer += raw_chunk[offset : offset + take]
                offset += take
                while (framed := _next_sse_block(buffer)) is not None:
                    block, buffer = framed
                    if len(block) > _MAXIMUM_EVENT_BYTES:
                        msg = "A Router stream event is too large."
                        raise RouterResponseLimitError(msg)  # noqa: TRY301
                    if not block:
                        continue
                    if terminal:
                        msg = "The Router stream has data after its terminal event."
                        raise RouterProtocolError(msg)  # noqa: TRY301
                    event_count += 1
                    _validate_stream_event_count(event_count)
                    event = _stream_event(
                        block.replace(b"\r\n", b"\n"),
                        started=started,
                        service_key=service_key,
                    )
                    if isinstance(event, ModelStreamStart):
                        started = True
                    elif isinstance(event, (ModelStreamTextDelta, ModelStreamToolCall)):
                        visible = True
                        output_bytes += _stream_output_bytes(event)
                        _validate_stream_output_size(output_bytes)
                    else:
                        terminal = True
                    yield event
                    if terminal and buffer.strip():
                        msg = "The Router stream has data after its terminal event."
                        raise RouterProtocolError(msg)  # noqa: TRY301
                if len(buffer) > _MAXIMUM_EVENT_BYTES and not _possible_sse_suffix(
                    buffer[_MAXIMUM_EVENT_BYTES:]
                ):
                    msg = "A Router stream event is too large."
                    raise RouterResponseLimitError(msg)  # noqa: TRY301
        if buffer.strip():
            msg = "The Router stream ended inside one event."
            raise RouterProtocolError(msg)  # noqa: TRY301
    except RouterAPIError as error:
        error.phase = (
            CallFailurePhase.AFTER_VISIBLE_OUTPUT
            if visible
            else CallFailurePhase.BEFORE_VISIBLE_OUTPUT
        )
        raise
    except RouterProtocolError as error:
        error.phase = (
            CallFailurePhase.AFTER_VISIBLE_OUTPUT
            if visible
            else CallFailurePhase.UNCERTAIN
        )
        raise
    except RouterError:
        raise
    except Exception:  # noqa: BLE001 - Chunk iterators can raise any error.
        phase = (
            CallFailurePhase.AFTER_VISIBLE_OUTPUT
            if visible
            else CallFailurePhase.UNCERTAIN
        )
        raise RouterStreamError(phase=phase) from None
    if not terminal:
        phase = (
            CallFailurePhase.AFTER_VISIBLE_OUTPUT
            if visible
            else CallFailurePhase.UNCERTAIN
        )
        raise RouterStreamError(phase=phase)


def _next_sse_block(buffer: bytes) -> tuple[bytes, bytes] | None:
    positions = tuple(
        (position, separator)
        for separator in (b"\n\n", b"\r\n\r\n")
        if (position := buffer.find(separator)) >= 0
    )
    if not positions:
        return None
    position, separator = min(positions, key=lambda item: item[0])
    end = position + len(separator)
    return buffer[:position], buffer[end:]


def _possible_sse_suffix(value: bytes) -> bool:
    return any(separator.startswith(value) for separator in (b"\n\n", b"\r\n\r\n"))


def _stream_output_bytes(
    event: ModelStreamTextDelta | ModelStreamToolCall,
) -> int:
    if isinstance(event, ModelStreamTextDelta):
        return len(event.delta.encode("utf-8"))
    call = event.tool_call
    return (
        len(call.id.encode("utf-8"))
        + len(call.name.encode("utf-8"))
        + len(call.arguments_json.encode("utf-8"))
    )


def _validate_stream_event_count(count: int) -> None:
    if count > _MAXIMUM_STREAM_EVENTS:
        msg = "The Router stream has too many events."
        raise RouterResponseLimitError(msg)


def _validate_stream_output_size(size: int) -> None:
    if size > _MAXIMUM_STREAM_OUTPUT_BYTES:
        msg = "The Router stream output exceeds the byte limit."
        raise RouterResponseLimitError(msg)


def _stream_event(  # noqa: C901 - The native protocol has five event names.
    block: bytes, *, started: bool, service_key: str | None = None
) -> ModelStreamEvent:
    try:
        text = block.decode("utf-8")
    except UnicodeDecodeError:
        msg = "The Router stream is not valid UTF-8."
        raise RouterProtocolError(msg) from None
    lines = text.split("\n")
    if (
        len(lines) != 2  # noqa: PLR2004 - SSE has exactly two lines.
        or not lines[0].startswith("event: ")
        or not lines[1].startswith("data: ")
    ):
        msg = "The Router stream event framing is invalid."
        raise RouterProtocolError(msg)
    name = lines[0][7:]
    value = _safe_json_object(lines[1][6:].encode(), service_key)
    if not started and name != "start":
        msg = "The Router stream does not start with a start event."
        raise RouterProtocolError(msg)
    if started and name == "start":
        msg = "The Router stream has more than one start event."
        raise RouterProtocolError(msg)
    if name == "start":
        item = _closed(value, {"provider_model_api_name"})
        return ModelStreamStart(
            ExactModelSelector(_api_string(item["provider_model_api_name"]))
        )
    if name == "text_delta":
        item = _closed(value, {"delta"})
        delta = _string(item["delta"], "stream text delta")
        if not delta:
            msg = "A Router stream text delta is empty."
            raise RouterProtocolError(msg)
        return ModelStreamTextDelta(delta)
    if name == "tool_call":
        item = _closed(value, {"tool_call"})
        return ModelStreamToolCall(_tool_call(_object(item["tool_call"])))
    if name == "completed":
        item = _closed(value, {"provider_model_api_name", "usage"})
        return ModelStreamCompleted(
            ExactModelSelector(_api_string(item["provider_model_api_name"])),
            _usage(_object(item["usage"])),
        )
    if name == "error":
        raise _api_error(200, value)
    msg = "The Router stream event name is invalid."
    raise RouterProtocolError(msg)


def _model_response(value: JsonObject) -> ModelResponse:
    output_type = _string(value.get("output_type"), "model output type")
    if output_type == "standard":
        item = _closed(
            value, {"output_type", "provider_model_api_name", "content", "usage"}
        )
        content = tuple(
            _assistant_part(_object(part))
            for part in _array(item["content"], "model content")
        )
        if not content:
            msg = "The Router model response has no content."
            raise RouterProtocolError(msg)
        return ModelCallResult(
            ExactModelSelector(_api_string(item["provider_model_api_name"])),
            content,
            _usage(_object(item["usage"])),
        )
    if output_type == "structured_json":
        item = _closed(
            value,
            {
                "output_type",
                "provider_model_api_name",
                "structured_output_json",
                "usage",
            },
        )
        structured = _bounded_nonempty_response_string(
            item["structured_output_json"], "structured model output", 1_000_000
        )
        try:
            return StructuredModelCallResult(
                ExactModelSelector(_api_string(item["provider_model_api_name"])),
                structured,
                _usage(_object(item["usage"])),
            )
        except RouterContractError as error:
            raise RouterProtocolError(str(error)) from None
    msg = "The Router model output type is invalid."
    raise RouterProtocolError(msg)


def _assistant_part(value: JsonObject) -> AssistantContentPart:
    part_type = _string(value.get("type"), "assistant content type")
    if part_type == "text":
        item = _closed(value, {"type", "text"})
        return TextOutputPart(_string(item["text"], "text output"))
    if part_type == "tool_call":
        return _tool_call(value)
    msg = "The Router assistant content type is invalid."
    raise RouterProtocolError(msg)


def _tool_call(value: JsonObject) -> ToolCallPart:
    item = _closed(value, {"type", "id", "name", "arguments_json"})
    if item["type"] != "tool_call":
        msg = "The Router tool-call discriminator is invalid."
        raise RouterProtocolError(msg)
    try:
        return ToolCallPart(
            _opaque_id(item["id"], "tool call ID"),
            _bounded_nonempty_response_string(item["name"], "tool name", 200),
            _bounded_nonempty_response_string(
                item["arguments_json"], "tool arguments", 1_000_000
            ),
        )
    except RouterContractError as error:
        raise RouterProtocolError(str(error)) from None


def _usage(value: JsonObject) -> Usage:
    item = _closed(value, {"units", "cost", "currency"})
    units = tuple(
        _usage_item(_object(unit)) for unit in _array(item["units"], "usage units")
    )
    return Usage(
        units, _decimal(item["cost"], "usage cost"), _currency(item["currency"])
    )


def _usage_item(value: JsonObject) -> UsageItem:
    item = _closed(value, {"unit", "quantity"})
    try:
        unit = UsageUnit(_string(item["unit"], "usage unit"))
    except ValueError:
        msg = "The Router usage unit is invalid."
        raise RouterProtocolError(msg) from None
    return UsageItem(unit, _decimal(item["quantity"], "usage quantity"))


def _workspace(value: JsonObject) -> Workspace:
    item = _closed(value, {"api_name", "display_name", "created_at"})
    return Workspace(
        _api_string(item["api_name"]),
        _bounded_nonempty_response_string(
            item["display_name"], "workspace display name", 200
        ),
        _timestamp_response(item["created_at"], "workspace creation time"),
    )


def _workspace_page(value: JsonObject, *, maximum_items: int) -> WorkspacePage:
    item = _closed(value, {"items", "page"})
    items = _bounded_array(item["items"], "workspace items", maximum_items)
    return WorkspacePage(
        tuple(_workspace(_object(value)) for value in items),
        _page(_object(item["page"])),
    )


def _candidate(value: JsonObject) -> ProviderModelCandidate:
    item = _closed(value, {"provider_model_api_name"})
    return ProviderModelCandidate(_api_string(item["provider_model_api_name"]))


def _assignment(value: JsonObject) -> Assignment:
    required = {
        "api_name",
        "display_name",
        "definition_kind",
        "effective_chain",
        "observed_requirements",
    }
    optional = {
        "defined_by_service_api_name",
        "inherits_assignment_api_name",
        "direct_chain",
        "reasoning_level",
        "last_used_at",
        "created_at",
    }
    item = _closed(value, required, optional)
    try:
        kind = AssignmentDefinitionKind(
            _string(item["definition_kind"], "assignment definition kind")
        )
        requirements = tuple(
            ObservedRequirement(_string(value, "observed requirement"))
            for value in _bounded_array(
                item["observed_requirements"], "observed requirements", 11
            )
        )
        reasoning = (
            ReasoningLevel(_string(item["reasoning_level"], "reasoning level"))
            if "reasoning_level" in item
            else None
        )
    except ValueError:
        msg = "The Router assignment enum value is invalid."
        raise RouterProtocolError(msg) from None
    direct = (
        tuple(
            _candidate(_object(value))
            for value in _bounded_array(
                item["direct_chain"], "direct chain", _MAXIMUM_ASSIGNMENT_CHAIN
            )
        )
        if "direct_chain" in item
        else None
    )
    effective = tuple(
        _candidate(_object(value))
        for value in _bounded_array(
            item["effective_chain"],
            "effective chain",
            _MAXIMUM_ASSIGNMENT_CHAIN,
        )
    )
    if len(set(effective)) != len(effective):
        msg = "The Router effective assignment chain has duplicate routes."
        raise RouterProtocolError(msg)
    if direct is not None and (not direct or len(set(direct)) != len(direct)):
        msg = "The Router direct assignment chain is invalid."
        raise RouterProtocolError(msg)
    if len(set(requirements)) != len(requirements):
        msg = "The Router observed assignment requirements have duplicates."
        raise RouterProtocolError(msg)
    inherits = (
        _assignment_string(item["inherits_assignment_api_name"])
        if "inherits_assignment_api_name" in item
        else None
    )
    if (
        (kind is AssignmentDefinitionKind.IMPLICIT and (inherits or direct is not None))
        or (
            kind is AssignmentDefinitionKind.INHERITED_ASSIGNMENT
            and (inherits is None or direct is not None)
        )
        or (
            kind is AssignmentDefinitionKind.DIRECT_CHAIN
            and (inherits is not None or direct is None)
        )
    ):
        msg = "The Router assignment definition does not match its kind."
        raise RouterProtocolError(msg)
    return Assignment(
        _assignment_string(item["api_name"]),
        _bounded_nonempty_response_string(
            item["display_name"], "assignment display name", 200
        ),
        kind,
        effective,
        requirements,
        (
            _api_string(item["defined_by_service_api_name"])
            if "defined_by_service_api_name" in item
            else None
        ),
        inherits,
        direct,
        reasoning,
        _optional_timestamp(item, "last_used_at"),
        _optional_timestamp(item, "created_at"),
    )


def _assignment_page(value: JsonObject, *, maximum_items: int) -> AssignmentPage:
    item = _closed(value, {"items", "page"})
    items = _bounded_array(item["items"], "assignment items", maximum_items)
    return AssignmentPage(
        tuple(_assignment(_object(value)) for value in items),
        _page(_object(item["page"])),
    )


def _service_key(value: JsonObject) -> ServiceKey:
    item = _closed(value, {"id", "name", "created_at"}, {"last_used_at"})
    return ServiceKey(
        _opaque_id(item["id"], "service key ID"),
        _bounded_nonempty_response_string(item["name"], "service key name", 200),
        _timestamp_response(item["created_at"], "service key creation time"),
        _optional_timestamp(item, "last_used_at"),
    )


def _service_key_page(value: JsonObject, *, maximum_items: int) -> ServiceKeyPage:
    item = _closed(value, {"items", "page"})
    items = _bounded_array(item["items"], "service key items", maximum_items)
    return ServiceKeyPage(
        tuple(_service_key(_object(value)) for value in items),
        _page(_object(item["page"])),
    )


def _constraints(value: JsonObject) -> ModelConstraints:
    item = _closed(
        value,
        set(),
        {
            "embedding_dimensions",
            "max_input_images",
            "max_input_image_bytes",
            "max_output_duration_seconds",
            "max_context_tokens",
            "max_output_tokens",
        },
    )
    dimensions = tuple(
        _integer(value, "embedding dimension", minimum=1)
        for value in _bounded_array(
            item.get("embedding_dimensions", []), "embedding dimensions", 64
        )
    )
    if (
        any(value > _MAXIMUM_EMBEDDING_DIMENSIONS for value in dimensions)
        or len(set(dimensions)) != len(dimensions)
        or ("embedding_dimensions" in item and not dimensions)
    ):
        msg = "The Router embedding dimension constraints are invalid."
        raise RouterProtocolError(msg)
    return ModelConstraints(
        embedding_dimensions=dimensions,
        max_input_images=_optional_int(item, "max_input_images", maximum=8),
        max_input_image_bytes=_optional_int(
            item, "max_input_image_bytes", maximum=20 * 1024 * 1024
        ),
        max_output_duration_seconds=_optional_int(
            item, "max_output_duration_seconds", maximum=86_400
        ),
        max_context_tokens=_optional_int(
            item,
            "max_context_tokens",
            maximum=_MAXIMUM_SIGNED_32_BIT_INTEGER,
        ),
        max_output_tokens=_optional_int(
            item,
            "max_output_tokens",
            maximum=_MAXIMUM_SIGNED_32_BIT_INTEGER,
        ),
    )


def _price(value: JsonObject) -> Price:
    item = _closed(value, {"currency", "unit_prices"}, {"source", "synchronized_at"})
    prices = tuple(
        _unit_price(_object(value))
        for value in _bounded_array(item["unit_prices"], "unit prices", 16)
    )
    if not prices or len({price.unit for price in prices}) != len(prices):
        msg = "The Router unit price collection is invalid."
        raise RouterProtocolError(msg)
    return Price(
        _currency(item["currency"]),
        prices,
        (
            _bounded_response_string(item["source"], "price source", 500)
            if "source" in item
            else None
        ),
        _optional_timestamp(item, "synchronized_at"),
    )


def _unit_price(value: JsonObject) -> UnitPrice:
    item = _closed(value, {"unit", "amount"})
    try:
        unit = UsageUnit(_string(item["unit"], "price unit"))
    except ValueError:
        msg = "The Router price unit is invalid."
        raise RouterProtocolError(msg) from None
    return UnitPrice(unit, _bounded_decimal(item["amount"], "unit price", 64))


def _provider_model(value: JsonObject) -> AvailableProviderModel:
    required = {
        "api_name",
        "display_name",
        "input_modalities",
        "output_modalities",
        "capabilities",
    }
    item = _closed(value, required, {"constraints", "effective_price"})
    try:
        inputs = tuple(
            InputModality(_string(value, "input modality"))
            for value in _array(item["input_modalities"], "input modalities")
        )
        outputs = tuple(
            OutputModality(_string(value, "output modality"))
            for value in _array(item["output_modalities"], "output modalities")
        )
        capabilities = tuple(
            ModelCapability(_string(value, "model capability"))
            for value in _array(item["capabilities"], "model capabilities")
        )
    except ValueError:
        msg = "The Router provider-model enum value is invalid."
        raise RouterProtocolError(msg) from None
    if (
        not inputs
        or len(set(inputs)) != len(inputs)
        or not outputs
        or len(set(outputs)) != len(outputs)
        or len(set(capabilities)) != len(capabilities)
    ):
        msg = "The Router provider-model capability collections are invalid."
        raise RouterProtocolError(msg)
    return AvailableProviderModel(
        _api_string(item["api_name"]),
        _bounded_nonempty_response_string(
            item["display_name"], "provider-model display name", 200
        ),
        inputs,
        outputs,
        capabilities,
        _constraints(_object(item["constraints"])) if "constraints" in item else None,
        _price(_object(item["effective_price"])) if "effective_price" in item else None,
    )


def _provider_model_page(
    value: JsonObject, *, maximum_items: int
) -> AvailableProviderModelPage:
    item = _closed(value, {"items", "page"})
    items = _bounded_array(item["items"], "provider-model items", maximum_items)
    return AvailableProviderModelPage(
        tuple(_provider_model(_object(value)) for value in items),
        _page(_object(item["page"])),
    )


def _embedding_result(value: JsonObject, *, expected_count: int) -> EmbeddingResult:
    item = _closed(value, {"provider_model_api_name", "embeddings", "usage"})
    embeddings = tuple(
        _embedding_vector(_object(value))
        for value in _bounded_array(
            item["embeddings"], "embeddings", _MAXIMUM_EMBEDDING_ITEMS
        )
    )
    if len(embeddings) != expected_count or tuple(
        vector.index for vector in embeddings
    ) != tuple(range(expected_count)):
        msg = "The Router embedding result does not match the input order."
        raise RouterProtocolError(msg)
    dimensions = {len(vector.values) for vector in embeddings}
    if len(dimensions) != 1:
        msg = "The Router embedding vectors have different dimensions."
        raise RouterProtocolError(msg)
    return EmbeddingResult(
        ExactModelSelector(_api_string(item["provider_model_api_name"])),
        embeddings,
        _usage(_object(item["usage"])),
    )


def _embedding_vector(value: JsonObject) -> EmbeddingVector:
    item = _closed(value, {"index", "values"})
    values = tuple(
        _number(value, "embedding value")
        for value in _bounded_array(
            item["values"], "embedding values", _MAXIMUM_EMBEDDING_DIMENSIONS
        )
    )
    if not values:
        msg = "A Router embedding vector is empty."
        raise RouterProtocolError(msg)
    return EmbeddingVector(
        _integer(item["index"], "embedding index", minimum=0), values
    )


def _media_job(value: JsonObject) -> MediaJob:
    required = {
        "id",
        "workspace_api_name",
        "provider_model_api_name",
        "kind",
        "state",
        "created_at",
    }
    item = _closed(value, required, {"content", "error", "completed_at"})
    try:
        kind = MediaKind(_string(item["kind"], "media kind"))
        state = MediaJobState(_string(item["state"], "media job state"))
    except ValueError:
        msg = "The Router media job enum value is invalid."
        raise RouterProtocolError(msg) from None
    content = _media_content(_object(item["content"])) if "content" in item else None
    error = (
        _error_value(_object(item["error"]), status=200) if "error" in item else None
    )
    return MediaJob(
        _opaque_id(item["id"], "media job ID"),
        _api_string(item["workspace_api_name"]),
        ExactModelSelector(_api_string(item["provider_model_api_name"])),
        kind,
        state,
        _timestamp_response(item["created_at"], "media job creation time"),
        content,
        error,
        _optional_timestamp(item, "completed_at"),
    )


def _media_content(value: JsonObject) -> MediaContent:
    item = _closed(value, {"media_type", "size_bytes"})
    return MediaContent(
        _bounded_nonempty_response_string(item["media_type"], "media type", 200),
        _integer(item["size_bytes"], "media size", minimum=0),
    )


def _statistics(value: JsonObject) -> StatisticsResult:
    item = _closed(value, {"from", "to", "group_by", "buckets"})
    try:
        groups = tuple(
            StatisticsDimension(_string(value, "statistics dimension"))
            for value in _bounded_array(
                item["group_by"], "statistics groups", _MAXIMUM_STATISTICS_GROUPS
            )
        )
    except ValueError:
        msg = "The Router statistics dimension is invalid."
        raise RouterProtocolError(msg) from None
    if len(set(groups)) != len(groups):
        msg = "The Router statistics groups are invalid."
        raise RouterProtocolError(msg)
    buckets = tuple(
        _statistics_bucket(_object(value), expected_dimensions=len(groups))
        for value in _bounded_array(
            item["buckets"], "statistics buckets", _MAXIMUM_STATISTICS_BUCKETS
        )
    )
    return StatisticsResult(
        _timestamp_response(item["from"], "statistics start time"),
        _timestamp_response(item["to"], "statistics end time"),
        groups,
        buckets,
    )


def _statistics_bucket(
    value: JsonObject, *, expected_dimensions: int
) -> StatisticsBucket:
    item = _closed(
        value, {"dimensions", "calls", "attempts", "units", "cost", "currency"}
    )
    dimensions = tuple(
        _bounded_response_string(value, "statistics dimension value", 200)
        for value in _bounded_array(
            item["dimensions"], "statistics dimensions", expected_dimensions
        )
    )
    if len(dimensions) != expected_dimensions:
        msg = "The Router statistics bucket dimensions do not match its groups."
        raise RouterProtocolError(msg)
    units = tuple(
        _usage_item(_object(value))
        for value in _array(item["units"], "statistics units")
    )
    return StatisticsBucket(
        dimensions,
        _integer(item["calls"], "call count", minimum=0),
        _integer(item["attempts"], "attempt count", minimum=0),
        units,
        _decimal(item["cost"], "statistics cost"),
        _currency(item["currency"]),
    )


def _page(value: JsonObject) -> PageInfo:
    item = _closed(value, {"has_more"}, {"next_cursor"})
    has_more = _boolean(item["has_more"], "page continuation")
    cursor = (
        _bounded_nonempty_response_string(item["next_cursor"], "page cursor", 500)
        if "next_cursor" in item
        else None
    )
    if has_more != (cursor is not None):
        msg = "The Router page continuation is invalid."
        raise RouterProtocolError(msg)
    return PageInfo(has_more, cursor)


def _api_error(status: int, value: JsonObject) -> RouterAPIError:
    envelope = _closed(value, {"error"})
    return _error_value(_object(envelope["error"]), status=status)


def _error_value(value: JsonObject, *, status: int) -> RouterAPIError:
    item = _closed(value, {"code", "message"}, {"details"})
    code = _string(item["code"], "Router error code")
    error_type = _ERROR_TYPES.get(code)
    if error_type is None:
        msg = "The Router error code is invalid."
        raise RouterProtocolError(msg)
    field_name: str | None = None
    reason: str | None = None
    if "details" in item:
        details = _closed(_object(item["details"]), set(), {"field", "reason"})
        field_name = (
            _bounded_nonempty_response_string(details["field"], "error field", 200)
            if "field" in details
            else None
        )
        reason = (
            _bounded_nonempty_response_string(details["reason"], "error reason", 500)
            if "reason" in details
            else None
        )
    return error_type(
        code,
        _bounded_nonempty_response_string(
            item["message"], "Router error message", 1000
        ),
        status=status,
        field_name=field_name,
        reason=reason,
    )


def _base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        msg = "The Router base URL is invalid."
        raise ValueError(msg) from None
    host = parsed.hostname
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in {"http", "https"}
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and not loopback)
        or (port is None and ":" in parsed.netloc and parsed.netloc.endswith(":"))
    ):
        msg = "The Router base URL is invalid."
        raise ValueError(msg)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _valid_service_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and _MINIMUM_SERVICE_KEY <= len(value) <= _MAXIMUM_SERVICE_KEY
        and value.strip() == value
        and all(
            _PRINTABLE_ASCII_START <= ord(character) <= _PRINTABLE_ASCII_END
            for character in value
        )
    )


def _page_query(cursor: str | None, limit: int) -> list[tuple[str, str]]:
    raw_limit = cast("object", limit)
    if (
        isinstance(raw_limit, bool)
        or not isinstance(raw_limit, int)
        or not 1 <= raw_limit <= _MAXIMUM_PAGE_SIZE
    ):
        msg = "The Router page size must be from 1 through 200."
        raise ValueError(msg)
    query = [("limit", str(limit))]
    if cursor is not None:
        _text(cursor, "Router cursor", 500)
        query.append(("cursor", cursor))
    return query


def _segment(value: str, *, api_name: bool = False) -> str:
    if api_name:
        _api_name(value, "resource")
    else:
        _text(value, "Router resource ID", 200)
    return quote(value, safe="")


def _assignment_segment(value: str) -> str:
    _assignment_name(value)
    return quote(value, safe="")


def _assignment_name(value: str) -> None:
    if (
        not 1 <= len(value) <= _MAXIMUM_ASSIGNMENT_NAME
        or _ASSIGNMENT_NAME.fullmatch(value) is None
    ):
        msg = "The assignment API name is invalid."
        raise ValueError(msg)


def _api_name(value: str, kind: str) -> None:
    if _API_NAME.fullmatch(value) is None:
        msg = f"The {kind} API name is invalid."
        raise ValueError(msg)


def _api_string(value: JsonValue) -> str:
    result = _string(value, "API name")
    try:
        _api_name(result, "resource")
    except ValueError as error:
        raise RouterProtocolError(str(error)) from None
    return result


def _assignment_string(value: JsonValue) -> str:
    result = _string(value, "assignment API name")
    try:
        _assignment_name(result)
    except ValueError as error:
        raise RouterProtocolError(str(error)) from None
    return result


def _opaque_id(value: JsonValue, name: str) -> str:
    return _bounded_nonempty_response_string(value, name, 200)


def _timestamp_response(value: JsonValue, name: str) -> str:
    result = _string(value, name)
    if _RFC3339_TIMESTAMP.fullmatch(result) is None:
        msg = f"The Router {name} is not one valid timestamp."
        raise RouterProtocolError(msg)
    try:
        datetime.fromisoformat(result)
    except ValueError:
        msg = f"The Router {name} is not one valid timestamp."
        raise RouterProtocolError(msg) from None
    return result


def _optional_timestamp(value: JsonObject, key: str) -> str | None:
    return _timestamp_response(value[key], key) if key in value else None


def _text(value: str, name: str, maximum: int) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        msg = f"The {name} is not valid UTF-8 text."
        raise ValueError(msg) from None
    if not 1 <= len(value) <= maximum:
        msg = f"The {name} is invalid."
        raise ValueError(msg)
    return encoded


def _timestamp(value: str, name: str) -> datetime:
    _text(value, name, 100)
    if _RFC3339_TIMESTAMP.fullmatch(value) is None:
        msg = f"The {name} is not one valid RFC 3339 timestamp."
        raise ValueError(msg)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        msg = f"The {name} is not one valid timestamp."
        raise ValueError(msg) from None
    return parsed


def _json_bytes(value: JsonObject) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except UnicodeEncodeError, ValueError, RecursionError:
        msg = "The Router request is not valid bounded JSON."
        raise ValueError(msg) from None


def _json_string_bytes(value: str) -> bytes:
    return json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8")


def _request_json_bytes(value: JsonObject) -> bytes:
    result = _json_bytes(value)
    _validate_request_size(len(result))
    return result


def _validate_request_size(size: int) -> None:
    if size > _MAXIMUM_COMPLETE_REQUEST_BYTES:
        msg = "The Router request exceeds the 70 MiB HTTP body limit."
        raise RouterContractError(msg)


def _json_object(value: bytes) -> JsonObject:
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except RouterProtocolError:
        raise
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError:
        msg = "The Router response is not valid JSON."
        raise RouterProtocolError(msg) from None
    result = cast("JsonValue", parsed)
    _validate_json_unicode(result)
    return _object(result)


def _safe_json_object(value: bytes, service_key: str | None) -> JsonObject:
    result = _json_object(value)
    if service_key is not None and _json_contains_string(result, service_key):
        msg = "The Router JSON response contains the service key."
        raise RouterProtocolError(msg)
    return result


def _json_contains_string(value: JsonValue, expected: str) -> bool:
    pending: list[JsonValue] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if expected in item:
                return True
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            if any(expected in key for key in item):
                return True
            pending.extend(item.values())
    return False


def _unique_json_object(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            msg = "The Router response JSON has a duplicate object key."
            raise RouterProtocolError(msg)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> JsonValue:
    del value
    msg = "The Router response JSON has a non-finite number."
    raise RouterProtocolError(msg)


def _validate_json_unicode(value: JsonValue) -> None:
    pending: list[JsonValue] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                msg = "The Router response JSON has invalid Unicode text."
                raise RouterProtocolError(msg) from None
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())


def _object(value: JsonValue) -> JsonObject:
    raw = cast("object", value)
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) for key in cast("dict[object, object]", raw)
    ):
        msg = "The Router response value is not one JSON object."
        raise RouterProtocolError(msg)
    return cast("JsonObject", raw)


def _closed(
    value: JsonObject, required: set[str], optional: set[str] | None = None
) -> JsonObject:
    allowed = required | (optional or set())
    if not required.issubset(value) or not set(value).issubset(allowed):
        msg = "The Router response object does not match its closed schema."
        raise RouterProtocolError(msg)
    return value


def _array(value: JsonValue, name: str) -> list[JsonValue]:
    if not isinstance(value, list):
        msg = f"The Router {name} value is not one array."
        raise RouterProtocolError(msg)
    return value


def _bounded_array(value: JsonValue, name: str, maximum: int) -> list[JsonValue]:
    result = _array(value, name)
    if len(result) > maximum:
        msg = f"The Router {name} collection exceeds its item limit."
        raise RouterProtocolError(msg)
    return result


def _string(value: JsonValue, name: str) -> str:
    if not isinstance(value, str):
        msg = f"The Router {name} value is not text."
        raise RouterProtocolError(msg)
    return value


def _bounded_response_string(value: JsonValue, name: str, maximum: int) -> str:
    result = _string(value, name)
    if len(result) > maximum:
        msg = f"The Router {name} value exceeds its text limit."
        raise RouterProtocolError(msg)
    return result


def _bounded_nonempty_response_string(value: JsonValue, name: str, maximum: int) -> str:
    result = _bounded_response_string(value, name, maximum)
    if not result:
        msg = f"The Router {name} value is empty."
        raise RouterProtocolError(msg)
    return result


def _integer(value: JsonValue, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        msg = f"The Router {name} value is invalid."
        raise RouterProtocolError(msg)
    return value


def _optional_int(value: JsonObject, key: str, *, maximum: int) -> int | None:
    if key not in value:
        return None
    result = _integer(value[key], key, minimum=1)
    if result > maximum:
        msg = f"The Router {key} value exceeds its limit."
        raise RouterProtocolError(msg)
    return result


def _number(value: JsonValue, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        msg = f"The Router {name} value is not finite."
        raise RouterProtocolError(msg)
    return float(value)


def _boolean(value: JsonValue, name: str) -> bool:
    if not isinstance(value, bool):
        msg = f"The Router {name} value is not a Boolean."
        raise RouterProtocolError(msg)
    return value


def _decimal(value: JsonValue, name: str) -> str:
    result = _string(value, name)
    if _DECIMAL.fullmatch(result) is None:
        msg = f"The Router {name} value is not a non-negative decimal."
        raise RouterProtocolError(msg)
    return result


def _bounded_decimal(value: JsonValue, name: str, maximum: int) -> str:
    result = _decimal(value, name)
    if len(result) > maximum:
        msg = f"The Router {name} value exceeds its text limit."
        raise RouterProtocolError(msg)
    return result


def _currency(value: JsonValue) -> str:
    result = _string(value, "currency")
    if _CURRENCY.fullmatch(result) is None:
        msg = "The Router currency value is invalid."
        raise RouterProtocolError(msg)
    return result


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def _header_media_type(headers: Mapping[str, str], name: str) -> str:
    return _header(headers, name).split(";", 1)[0].strip().lower()


def _require_json_media_type(headers: Mapping[str, str]) -> None:
    media_type = _header_media_type(headers, "Content-Type")
    if media_type != "application/json":
        msg = "The Router JSON response media type is invalid."
        raise RouterProtocolError(msg)


def _response_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    seen_critical: set[str] = set()
    total_bytes = 0
    for count, (name, value) in enumerate(items, start=1):
        raw_name = cast("object", name)
        raw_value = cast("object", value)
        if count > _MAXIMUM_RESPONSE_HEADERS:
            msg = "The Router response has too many headers."
            raise RouterResponseLimitError(msg)
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            msg = "The Router response has an invalid header."
            raise RouterProtocolError(msg)
        try:
            total_bytes += (
                len(raw_name.encode("utf-8")) + len(raw_value.encode("utf-8")) + 4
            )
        except UnicodeEncodeError:
            msg = "The Router response has an invalid header."
            raise RouterProtocolError(msg) from None
        if total_bytes > _MAXIMUM_RESPONSE_HEADER_BYTES:
            msg = "The Router response headers exceed the byte limit."
            raise RouterResponseLimitError(msg)
        if _HTTP_HEADER_NAME.fullmatch(raw_name) is None:
            msg = "The Router response has an invalid header name."
            raise RouterProtocolError(msg)
        if any(
            (ord(character) < _ASCII_SPACE and character != "\t")
            or ord(character) == _ASCII_DELETE
            for character in raw_value
        ):
            msg = "The Router response has an invalid header value."
            raise RouterProtocolError(msg)
        normalized = raw_name.lower()
        if normalized in _CRITICAL_RESPONSE_HEADERS:
            if normalized in seen_critical:
                msg = "The Router response has a duplicate critical header."
                raise RouterProtocolError(msg)
            seen_critical.add(normalized)
        result[raw_name] = raw_value
    return result


def _validated_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    raw_headers = cast("object", headers)
    if not isinstance(raw_headers, Mapping):
        msg = "The Router transport returned invalid response headers."
        raise RouterProtocolError(msg)
    try:
        return _response_headers(cast("Mapping[str, str]", raw_headers).items())
    except AttributeError, TypeError, ValueError:
        msg = "The Router transport returned invalid response headers."
        raise RouterProtocolError(msg) from None


def _validate_complete_response(response: RouterTransportResponse) -> dict[str, str]:
    raw_status = cast("object", response.status)
    raw_body = cast("object", response.body)
    if (
        isinstance(raw_status, bool)
        or not isinstance(raw_status, int)
        or not _HTTP_STATUS_MINIMUM <= raw_status <= _HTTP_STATUS_MAXIMUM
        or not isinstance(raw_body, bytes)
    ):
        msg = "The Router transport returned an invalid complete response."
        raise RouterProtocolError(msg)
    headers = _validated_response_headers(response.headers)
    _validate_declared_length(headers, len(response.body))
    return headers


def _validate_stream_response(response: RouterStreamResponse) -> dict[str, str]:
    raw_status = cast("object", response.status)
    if (
        isinstance(raw_status, bool)
        or not isinstance(raw_status, int)
        or not _HTTP_STATUS_MINIMUM <= raw_status <= _HTTP_STATUS_MAXIMUM
    ):
        msg = "The Router transport returned an invalid stream response."
        raise RouterProtocolError(msg)
    return _validated_response_headers(response.headers)


def _validate_declared_length(headers: Mapping[str, str], actual: int) -> None:
    declared = _header(headers, "Content-Length")
    if not declared:
        return
    if not declared.isascii() or not declared.isdecimal():
        msg = "The Router response Content-Length is invalid."
        raise RouterProtocolError(msg)
    if int(declared) != actual:
        msg = "The Router response body does not match Content-Length."
        raise RouterProtocolError(msg)


def _response_size(value: bytes | bytearray, maximum: int) -> None:
    if len(value) > maximum:
        msg = "The Router response exceeds the configured byte limit."
        raise RouterResponseLimitError(msg)


def _bounded_stream_body(chunks: Iterable[bytes], maximum: int) -> bytes:
    body = bytearray()
    for raw_chunk in cast("Iterable[object]", chunks):
        if not isinstance(raw_chunk, bytes):
            msg = "The Router stream transport returned a non-byte chunk."
            raise RouterProtocolError(msg)
        if len(raw_chunk) > maximum - len(body):
            msg = "The Router response exceeds the configured byte limit."
            raise RouterResponseLimitError(msg)
        body.extend(raw_chunk)
    return bytes(body)
