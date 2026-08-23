"""Provider-neutral contract values for Router model calls."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

__all__ = [
    "AssignmentSelector",
    "AssistantMessage",
    "CallFailurePhase",
    "ExactModelSelector",
    "ImageInputPart",
    "ModelCall",
    "ModelCallError",
    "ModelCallResult",
    "ModelCaller",
    "ModelMessage",
    "ModelSelector",
    "RouterContractError",
    "SystemMessage",
    "TextInputPart",
    "TextOutputPart",
    "ToolCallPart",
    "ToolDefinition",
    "ToolResultPart",
    "Usage",
    "UsageItem",
    "UsageUnit",
    "UserMessage",
    "message_bytes",
    "normalize_tags",
]

_API_NAME = re.compile(r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ASSIGNMENT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_MAXIMUM_JSON_TEXT = 1_000_000
_MAXIMUM_IMAGE_BYTES = 20 * 1024 * 1024
_MAXIMUM_IMAGES = 8
_MAXIMUM_IMAGE_SET_BYTES = 50 * 1024 * 1024
_MAXIMUM_MODEL_CALL_JSON_BYTES = 2 * 1024 * 1024
_MAXIMUM_TOOL_SCHEMA = 100_000
_MAXIMUM_ASSIGNMENT_NAME = 127
_MAXIMUM_EXCLUDED_ROUTES = 16
_MAXIMUM_MESSAGES = 1_000
_MAXIMUM_TAGS = 32
_MAXIMUM_TOOLS = 128
_MAXIMUM_TAG_BYTES = 128
_MAXIMUM_TAG_SET_BYTES = 2_048


class RouterContractError(ValueError):
    """Report a value that does not match the Router model contract."""


class CallFailurePhase(Enum):
    """State when a model call failed."""

    BEFORE_VISIBLE_OUTPUT = "before_visible_output"
    AFTER_VISIBLE_OUTPUT = "after_visible_output"
    UNCERTAIN = "uncertain"


class ModelCallError(RuntimeError):
    """Report one safe provider-neutral model call failure.

    ``BEFORE_VISIBLE_OUTPUT`` permits the harness sticky-route fallback.
    ``AFTER_VISIBLE_OUTPUT`` and ``UNCERTAIN`` never permit a replacement call.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        phase: CallFailurePhase,
    ) -> None:
        """Initialize the failure with its stable code and failure phase."""
        _bounded_text(code, name="error code", maximum=200)
        _bounded_text(message, name="error message", maximum=1_000)
        self.code = code
        self.phase = phase
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AssignmentSelector:
    """Select one named Router assignment and its fallback chain."""

    assignment_api_name: str

    def __post_init__(self) -> None:
        """Validate the assignment name."""
        valid_length = 1 <= len(self.assignment_api_name) <= _MAXIMUM_ASSIGNMENT_NAME
        if not valid_length or not _ASSIGNMENT_NAME.fullmatch(self.assignment_api_name):
            msg = "The assignment API name is invalid."
            raise RouterContractError(msg)


@dataclass(frozen=True, slots=True)
class ExactModelSelector:
    """Select one exact enabled Router provider-model route."""

    provider_model_api_name: str

    def __post_init__(self) -> None:
        """Validate the provider-model API name."""
        _validate_api_name(self.provider_model_api_name, "provider-model")


type ModelSelector = AssignmentSelector | ExactModelSelector


@dataclass(frozen=True, slots=True)
class TextInputPart:
    """Contain one non-empty user text input part."""

    text: str
    type: Literal["text"] = "text"

    def __post_init__(self) -> None:
        """Validate the text input."""
        _bounded_text(self.text, name="text input", maximum=_MAXIMUM_JSON_TEXT)


@dataclass(frozen=True, slots=True)
class ImageInputPart:
    """Contain one bounded JPEG, PNG, or WebP user image."""

    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: bytes
    type: Literal["image"] = "image"

    def __post_init__(self) -> None:
        """Validate the image type and byte limit."""
        if self.media_type not in {"image/jpeg", "image/png", "image/webp"}:
            msg = "The image media type is invalid."
            raise RouterContractError(msg)
        if not 1 <= len(self.data) <= _MAXIMUM_IMAGE_BYTES:
            msg = "The image byte size is invalid."
            raise RouterContractError(msg)


@dataclass(frozen=True, slots=True)
class ToolResultPart:
    """Contain one JSON tool result correlated to a model tool call."""

    tool_call_id: str
    result_json: str
    type: Literal["tool_result"] = "tool_result"

    def __post_init__(self) -> None:
        """Validate the correlation ID and JSON result."""
        _bounded_text(self.tool_call_id, name="tool call ID", maximum=200)
        _json_text(self.result_json, name="tool result", maximum=_MAXIMUM_JSON_TEXT)


type UserContentPart = TextInputPart | ImageInputPart | ToolResultPart


@dataclass(frozen=True, slots=True)
class TextOutputPart:
    """Contain one model text output part."""

    text: str
    type: Literal["text"] = "text"

    def __post_init__(self) -> None:
        """Require text that the native UTF-8 contract can encode."""
        _validate_utf8(self.text, name="text output")


@dataclass(frozen=True, slots=True)
class ToolCallPart:
    """Contain one ordered model request to call an eligible tool."""

    id: str
    name: str
    arguments_json: str
    type: Literal["tool_call"] = "tool_call"

    def __post_init__(self) -> None:
        """Validate the call identity, tool name, and JSON arguments."""
        _bounded_text(self.id, name="tool call ID", maximum=200)
        _bounded_text(self.name, name="tool name", maximum=200)
        value = _json_text(
            self.arguments_json,
            name="tool arguments",
            maximum=_MAXIMUM_JSON_TEXT,
        )
        if not isinstance(value, dict):
            msg = "The tool arguments must be one JSON object."
            raise RouterContractError(msg)


type AssistantContentPart = TextOutputPart | ToolCallPart


@dataclass(frozen=True, slots=True)
class SystemMessage:
    """Contain one system message at the conversation prefix."""

    content: str
    role: Literal["system"] = "system"

    def __post_init__(self) -> None:
        """Validate the system content."""
        _bounded_text(self.content, name="system message", maximum=_MAXIMUM_JSON_TEXT)


@dataclass(frozen=True, slots=True)
class UserMessage:
    """Contain one ordered set of user input or tool-result parts."""

    content: tuple[UserContentPart, ...]
    role: Literal["user"] = "user"

    def __post_init__(self) -> None:
        """Require at least one user content part."""
        if not self.content:
            msg = "A user message must have content."
            raise RouterContractError(msg)


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """Contain one ordered set of model text or tool-call parts."""

    content: tuple[AssistantContentPart, ...]
    role: Literal["assistant"] = "assistant"

    def __post_init__(self) -> None:
        """Require content and unique tool call IDs."""
        if not self.content:
            msg = "An assistant message must have content."
            raise RouterContractError(msg)
        call_ids = [part.id for part in self.content if isinstance(part, ToolCallPart)]
        if len(call_ids) != len(set(call_ids)):
            msg = "Tool call IDs must be unique in one message."
            raise RouterContractError(msg)


type ModelMessage = SystemMessage | UserMessage | AssistantMessage


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Define one caller-provided tool for a Router model call."""

    name: str
    description: str
    input_schema_json: str

    def __post_init__(self) -> None:
        """Validate the name, description, and JSON Schema object."""
        _bounded_text(self.name, name="tool name", maximum=200)
        _bounded_text(self.description, name="tool description", maximum=2_000)
        schema = _json_text(
            self.input_schema_json,
            name="tool input schema",
            maximum=_MAXIMUM_TOOL_SCHEMA,
        )
        if not isinstance(schema, dict):
            msg = "The tool input schema must be one JSON object."
            raise RouterContractError(msg)


class UsageUnit(Enum):
    """Name one provider-neutral Router usage unit."""

    INPUT_TOKEN = "input_token"  # noqa: S105 - This is a public usage unit.
    OUTPUT_TOKEN = "output_token"  # noqa: S105 - This is a public usage unit.
    CACHED_INPUT_TOKEN = "cached_input_token"  # noqa: S105 - This is a usage unit.
    IMAGE = "image"
    VIDEO_SECOND = "video_second"
    AUDIO_SECOND = "audio_second"
    REQUEST = "request"
    PROVIDER_UNIT = "provider_unit"


@dataclass(frozen=True, slots=True)
class UsageItem:
    """Contain one non-negative decimal usage quantity."""

    unit: UsageUnit
    quantity: str

    def __post_init__(self) -> None:
        """Validate the decimal quantity representation."""
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", self.quantity):
            msg = "The usage quantity is invalid."
            raise RouterContractError(msg)


@dataclass(frozen=True, slots=True)
class Usage:
    """Contain the provider-neutral usage and cost for one model call."""

    units: tuple[UsageItem, ...]
    cost: str
    currency: str

    def __post_init__(self) -> None:
        """Validate the decimal cost and ISO-style currency code."""
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", self.cost):
            msg = "The usage cost is invalid."
            raise RouterContractError(msg)
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            msg = "The usage currency is invalid."
            raise RouterContractError(msg)


@dataclass(frozen=True, slots=True)
class ModelCall:
    """Describe one stateless model call through a Router transport.

    ``excluded_routes`` is a harness execution constraint. A transport must
    ensure that an assignment fallback does not use one of these routes.
    This constraint keeps a failed sticky route from running twice.
    """

    workspace_api_name: str
    selector: ModelSelector
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    tags: tuple[str, ...] = ()
    excluded_routes: tuple[ExactModelSelector, ...] = ()

    def __post_init__(self) -> None:
        """Validate model call bounds and normalized tags."""
        _validate_api_name(self.workspace_api_name, "workspace")
        if not 1 <= len(self.messages) <= _MAXIMUM_MESSAGES:
            msg = "A model call must have 1 through 1000 messages."
            raise RouterContractError(msg)
        if len(self.tools) > _MAXIMUM_TOOLS:
            msg = "A model call can have no more than 128 tools."
            raise RouterContractError(msg)
        if len({tool.name for tool in self.tools}) != len(self.tools):
            msg = "Tool names must be unique."
            raise RouterContractError(msg)
        images = tuple(
            part
            for message in self.messages
            if isinstance(message, UserMessage)
            for part in message.content
            if isinstance(part, ImageInputPart)
        )
        if len(images) > _MAXIMUM_IMAGES:
            msg = "A model call can have no more than 8 images."
            raise RouterContractError(msg)
        if sum(len(image.data) for image in images) > _MAXIMUM_IMAGE_SET_BYTES:
            msg = "The model call image byte total is too large."
            raise RouterContractError(msg)
        if self.tags != normalize_tags(self.tags):
            msg = "Model call tags must be normalized."
            raise RouterContractError(msg)
        _validate_excluded_routes(self.selector, self.excluded_routes)
        if _model_call_bytes(self) > _MAXIMUM_MODEL_CALL_JSON_BYTES:
            msg = "The model call JSON body is too large."
            raise RouterContractError(msg)


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """Contain one successful standard model result and its exact route."""

    route: ExactModelSelector
    content: tuple[AssistantContentPart, ...]
    usage: Usage

    def __post_init__(self) -> None:
        """Require one or more result content parts."""
        AssistantMessage(self.content)


class ModelCaller(Protocol):
    """Call one model through a caller-provided Router transport."""

    async def __call__(self, call: ModelCall, /) -> ModelCallResult:
        """Return one result or raise ``ModelCallError``."""
        ...


def normalize_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    """Validate, deduplicate, and sort Router tags by UTF-8 byte order."""
    if len(tags) > _MAXIMUM_TAGS:
        msg = "A model call can have no more than 32 tags."
        raise RouterContractError(msg)
    encoded: list[tuple[bytes, str]] = []
    for tag in tags:
        tag_bytes = _validate_utf8(tag, name="Router tag")
        if not 1 <= len(tag_bytes) <= _MAXIMUM_TAG_BYTES:
            msg = "A Router tag has an invalid UTF-8 byte size."
            raise RouterContractError(msg)
        encoded.append((tag_bytes, tag))
    normalized = tuple(value for _raw, value in sorted(set(encoded)))
    if sum(len(value.encode("utf-8")) for value in normalized) > _MAXIMUM_TAG_SET_BYTES:
        msg = "The normalized Router tag set is too large."
        raise RouterContractError(msg)
    return normalized


def message_bytes(messages: tuple[ModelMessage, ...]) -> int:
    """Return compact message JSON bytes without uploaded image payload bytes."""
    return len(
        json.dumps(
            [_message_value(message) for message in messages],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _model_call_bytes(call: ModelCall) -> int:
    value: dict[str, object] = {
        "workspace_api_name": call.workspace_api_name,
        "selector": _selector_value(call.selector),
        "messages": [_message_value(message) for message in call.messages],
    }
    if call.tools:
        value["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema_json": tool.input_schema_json,
            }
            for tool in call.tools
        ]
    if call.tags:
        value["tags"] = list(call.tags)
    if call.excluded_routes:
        value["excluded_provider_model_api_names"] = [
            route.provider_model_api_name for route in call.excluded_routes
        ]
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _selector_value(selector: ModelSelector) -> dict[str, str]:
    if isinstance(selector, AssignmentSelector):
        return {"assignment_api_name": selector.assignment_api_name}
    return {"provider_model_api_name": selector.provider_model_api_name}


def _validate_excluded_routes(
    selector: ModelSelector,
    routes: tuple[ExactModelSelector, ...],
) -> None:
    if len(routes) > _MAXIMUM_EXCLUDED_ROUTES:
        msg = "A model call can exclude no more than 16 routes."
        raise RouterContractError(msg)
    if len(set(routes)) != len(routes):
        msg = "Excluded model routes must be unique."
        raise RouterContractError(msg)
    if routes and not isinstance(selector, AssignmentSelector):
        msg = "Only an assignment call can exclude model routes."
        raise RouterContractError(msg)


def _message_value(message: ModelMessage) -> dict[str, object]:
    if isinstance(message, SystemMessage):
        return {"role": message.role, "content": message.content}
    return {
        "role": message.role,
        "content": [_content_value(part) for part in message.content],
    }


def _content_value(part: UserContentPart | AssistantContentPart) -> dict[str, object]:
    if isinstance(part, ImageInputPart):
        return {
            "type": part.type,
            "media_type": part.media_type,
            "data_base64": "",
        }
    if isinstance(part, ToolResultPart):
        return {
            "type": part.type,
            "tool_call_id": part.tool_call_id,
            "result_json": part.result_json,
        }
    if isinstance(part, ToolCallPart):
        return {
            "type": part.type,
            "id": part.id,
            "name": part.name,
            "arguments_json": part.arguments_json,
        }
    return {"type": part.type, "text": part.text}


def _validate_api_name(value: str, kind: str) -> None:
    if not _API_NAME.fullmatch(value):
        msg = f"The {kind} API name is invalid."
        raise RouterContractError(msg)


def _bounded_text(value: str, *, name: str, maximum: int) -> None:
    if not 1 <= len(value) <= maximum:
        msg = f"The {name} is invalid."
        raise RouterContractError(msg)
    _validate_utf8(value, name=name)


def _validate_utf8(value: str, *, name: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        msg = f"The {name} is not valid UTF-8 text."
        raise RouterContractError(msg) from None


def _json_text(value: str, *, name: str, maximum: int) -> object:
    _bounded_text(value, name=name, maximum=maximum)
    try:
        return json.loads(value)
    except json.JSONDecodeError, RecursionError:
        msg = f"The {name} is not valid JSON."
        raise RouterContractError(msg) from None
