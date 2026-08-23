"""Stateless multi-turn harness for Router model calls and caller tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Protocol, cast

from opendle.router import (
    AssignmentSelector,
    AssistantMessage,
    CallFailurePhase,
    ExactModelSelector,
    ImageInputPart,
    ModelCall,
    ModelCaller,
    ModelCallError,
    ModelCallResult,
    ModelMessage,
    RouterContractError,
    SystemMessage,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
    UserMessage,
    message_bytes,
    normalize_tags,
)

__all__ = [
    "CompactionError",
    "CompactionFailureMode",
    "CompactionRequest",
    "Compactor",
    "ContextLimits",
    "ContextMethod",
    "ContextPolicy",
    "ConversationHarness",
    "ConversationNotFoundError",
    "ConversationState",
    "HarnessConfig",
    "HarnessError",
    "HarnessTool",
    "InMemoryConversationStore",
    "InvalidConversationError",
    "ModelProtocolError",
    "RouteState",
    "SequentialToolExecutor",
    "StateLoader",
    "StateSaver",
    "StoreLimitError",
    "ToolExecutor",
    "ToolHandler",
    "ToolProtocolError",
    "TurnLimitError",
    "prune_messages",
    "run_stored",
]

_MAXIMUM_CONTEXT_BYTES = 2 * 1024 * 1024
_MAXIMUM_MESSAGES = 1_000
_MAXIMUM_MODEL_TURNS = 1_000
_MAXIMUM_CONVERSATION_KEY = 200
_MAXIMUM_HARNESS_TOOLS = 128
_MAXIMUM_STORED_CONVERSATIONS = 100_000


class HarnessError(RuntimeError):
    """Base error for shared harness execution."""


class InvalidConversationError(HarnessError):
    """Report an incompatible or unbounded conversation state."""


class ToolProtocolError(HarnessError):
    """Report an invalid tool call, result, or executor response."""


class ModelProtocolError(HarnessError):
    """Report a result that violates its model call route constraint."""


class CompactionError(HarnessError):
    """Report a model compaction failure that stops the workflow."""


class TurnLimitError(HarnessError):
    """Report a workflow that reached its bounded model turn limit."""


class ConversationNotFoundError(HarnessError):
    """Report a conversation key that a load callback cannot find."""


class StoreLimitError(HarnessError):
    """Report an in-memory store capacity or state-size limit."""


@dataclass(frozen=True, slots=True)
class RouteState:
    """Carry the exact sticky route as explicit conversation data."""

    sticky: ExactModelSelector | None = None

    def after_success(self, route: ExactModelSelector) -> RouteState:
        """Return route state with the last successful route as sticky."""
        return RouteState(sticky=route)


@dataclass(frozen=True, slots=True)
class ConversationState:
    """Contain caller-owned immutable conversation and sticky-route state."""

    messages: tuple[ModelMessage, ...]
    route: RouteState = field(default_factory=RouteState)

    def __post_init__(self) -> None:
        """Validate conversation compatibility and Router bounds."""
        if not self.messages:
            msg = "A conversation must have a message."
            raise InvalidConversationError(msg)
        _conversation_groups(self.messages)
        if len(self.messages) > _MAXIMUM_MESSAGES:
            msg = "A conversation has too many messages."
            raise InvalidConversationError(msg)


@dataclass(frozen=True, slots=True)
class ContextLimits:
    """Select finite message and UTF-8 JSON byte limits for context."""

    max_messages: int
    max_bytes: int

    def __post_init__(self) -> None:
        """Validate limits against the Router request maximums."""
        if not 1 <= self.max_messages <= _MAXIMUM_MESSAGES:
            msg = "The context message limit is invalid."
            raise RouterContractError(msg)
        if not 1 <= self.max_bytes <= _MAXIMUM_CONTEXT_BYTES:
            msg = "The context byte limit is invalid."
            raise RouterContractError(msg)


class ContextMethod(Enum):
    """Select deterministic pruning or model-based compaction."""

    PRUNE = "prune"
    MODEL = "model"


class CompactionFailureMode(Enum):
    """Select the bounded response to model compaction failure."""

    STOP = "stop"
    PRUNE = "prune"


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Configure one bounded context reduction method."""

    method: ContextMethod
    limits: ContextLimits
    compaction_failure: CompactionFailureMode = CompactionFailureMode.STOP


@dataclass(frozen=True, slots=True)
class CompactionRequest:
    """Request model compaction on one exact pinned route."""

    workspace_api_name: str
    messages: tuple[ModelMessage, ...]
    route: ExactModelSelector
    tags: tuple[str, ...]
    limits: ContextLimits
    tools: tuple[ToolDefinition, ...] = ()

    def __post_init__(self) -> None:
        """Validate the workspace, messages, tags, and conversation shape."""
        call = ModelCall(
            workspace_api_name=self.workspace_api_name,
            selector=self.route,
            messages=self.messages,
            tools=self.tools,
            tags=normalize_tags(self.tags),
        )
        if call.tags != self.tags:
            msg = "Compaction tags must be normalized."
            raise RouterContractError(msg)
        _conversation_groups(self.messages)


class Compactor(Protocol):
    """Replace over-limit context through a caller-provided model hook."""

    async def __call__(self, request: CompactionRequest, /) -> tuple[ModelMessage, ...]:
        """Return bounded compatible messages or raise an exception."""
        ...


class ToolHandler(Protocol):
    """Execute one caller-owned tool call and return JSON result text."""

    async def __call__(self, call: ToolCallPart, /) -> str:
        """Execute one tool call."""
        ...


@dataclass(frozen=True, slots=True)
class HarnessTool:
    """Bind one eligible Router tool definition to its caller handler."""

    definition: ToolDefinition
    handler: ToolHandler


class ToolExecutor(Protocol):
    """Execute the complete ordered tool-call batch for one model turn."""

    async def __call__(
        self,
        calls: tuple[ToolCallPart, ...],
        tools: tuple[HarnessTool, ...],
        /,
    ) -> tuple[ToolResultPart, ...]:
        """Return one correlated result for each tool call in model order."""
        ...


class SequentialToolExecutor:
    """Execute caller tools sequentially in model order."""

    async def __call__(
        self,
        calls: tuple[ToolCallPart, ...],
        tools: tuple[HarnessTool, ...],
        /,
    ) -> tuple[ToolResultPart, ...]:
        """Run each handler only after the preceding handler is complete."""
        handlers = {tool.definition.name: tool.handler for tool in tools}
        results: list[ToolResultPart] = []
        for call in calls:
            handler = handlers.get(call.name)
            if handler is None:
                msg = f"The model requested unknown tool '{call.name}'."
                raise ToolProtocolError(msg)
            result_json = await handler(call)
            results.append(
                ToolResultPart(tool_call_id=call.id, result_json=result_json)
            )
        return tuple(results)


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """Configure one bounded workspace and assignment harness."""

    workspace_api_name: str
    assignment_api_name: str
    context: ContextPolicy
    tags: tuple[str, ...] = ()
    max_model_turns: int = 32

    def __post_init__(self) -> None:
        """Validate names, normalized tags, and the turn limit."""
        call = ModelCall(
            workspace_api_name=self.workspace_api_name,
            selector=AssignmentSelector(self.assignment_api_name),
            messages=(SystemMessage("validation"),),
            tags=normalize_tags(self.tags),
        )
        if call.tags != self.tags:
            msg = "Harness tags must be normalized."
            raise RouterContractError(msg)
        if not 1 <= self.max_model_turns <= _MAXIMUM_MODEL_TURNS:
            msg = "The harness model turn limit is invalid."
            raise RouterContractError(msg)


class ConversationHarness:
    """Run a stateless multi-turn model and tool workflow."""

    def __init__(
        self,
        *,
        model_caller: ModelCaller,
        tools: tuple[HarnessTool, ...],
        config: HarnessConfig,
        tool_executor: ToolExecutor | None = None,
        compactor: Compactor | None = None,
    ) -> None:
        """Initialize caller ports and validate the eligible tool set."""
        if len(tools) > _MAXIMUM_HARNESS_TOOLS:
            msg = "A harness can have no more than 128 tools."
            raise RouterContractError(msg)
        names = [tool.definition.name for tool in tools]
        if len(names) != len(set(names)):
            msg = "Harness tool names must be unique."
            raise RouterContractError(msg)
        if config.context.method is ContextMethod.MODEL and compactor is None:
            msg = "Model context reduction needs a compactor."
            raise RouterContractError(msg)
        self._model_caller = model_caller
        self._tools = tools
        self._config = config
        self._tool_executor = tool_executor or SequentialToolExecutor()
        self._compactor = compactor

    async def run(self, state: ConversationState, /) -> ConversationState:
        """Return updated state without changing the caller-owned input state."""
        messages = state.messages
        route = state.route
        for _turn in range(self._config.max_model_turns):
            messages = await self._fit_context(messages, route)
            result = await self._call_model(messages, route)
            route = route.after_success(result.route)
            assistant = AssistantMessage(result.content)
            messages = (*messages, assistant)
            tool_calls = tuple(
                part for part in result.content if isinstance(part, ToolCallPart)
            )
            if not tool_calls:
                return ConversationState(messages=messages, route=route)
            tool_results = await self._tool_executor(tool_calls, self._tools)
            _validate_tool_results(tool_calls, tool_results)
            messages = (*messages, UserMessage(content=tool_results))
        msg = (
            "The harness reached its model turn limit of "
            f"{self._config.max_model_turns}."
        )
        raise TurnLimitError(msg)

    async def _call_model(
        self,
        messages: tuple[ModelMessage, ...],
        route: RouteState,
    ) -> ModelCallResult:
        definitions = tuple(tool.definition for tool in self._tools)
        sticky = route.sticky
        if sticky is not None:
            sticky_call = ModelCall(
                workspace_api_name=self._config.workspace_api_name,
                selector=sticky,
                messages=messages,
                tools=definitions,
                tags=self._config.tags,
            )
            try:
                result = await self._model_caller(sticky_call)
            except ModelCallError as error:
                if error.phase is not CallFailurePhase.BEFORE_VISIBLE_OUTPUT:
                    raise
            else:
                _validate_model_result(sticky_call, result)
                return result
        assignment_call = ModelCall(
            workspace_api_name=self._config.workspace_api_name,
            selector=AssignmentSelector(self._config.assignment_api_name),
            messages=messages,
            tools=definitions,
            tags=self._config.tags,
            excluded_routes=(sticky,) if sticky is not None else (),
        )
        result = await self._model_caller(assignment_call)
        _validate_model_result(assignment_call, result)
        return result

    async def _fit_context(
        self,
        messages: tuple[ModelMessage, ...],
        route: RouteState,
    ) -> tuple[ModelMessage, ...]:
        limits = self._config.context.limits
        if _within_limits(messages, limits):
            return messages
        if self._config.context.method is ContextMethod.PRUNE:
            return prune_messages(messages, limits)
        try:
            compacted = await self._compact(messages, route, limits)
        except Exception as error:
            if self._config.context.compaction_failure is CompactionFailureMode.PRUNE:
                return prune_messages(messages, limits)
            if isinstance(error, CompactionError):
                raise
            msg = "The pinned model compaction failed."
            raise CompactionError(msg) from error
        else:
            return compacted

    async def _compact(
        self,
        messages: tuple[ModelMessage, ...],
        route: RouteState,
        limits: ContextLimits,
    ) -> tuple[ModelMessage, ...]:
        if route.sticky is None:
            msg = "Model compaction needs a preceding successful workflow route."
            raise CompactionError(msg)
        compactor = cast("Compactor", self._compactor)
        compacted = await compactor(
            CompactionRequest(
                workspace_api_name=self._config.workspace_api_name,
                messages=messages,
                route=route.sticky,
                tags=self._config.tags,
                limits=limits,
                tools=tuple(tool.definition for tool in self._tools),
            )
        )
        _validate_compaction(messages, compacted, limits)
        return compacted


class StateLoader(Protocol):
    """Load caller-owned state by one bounded conversation key."""

    async def __call__(self, key: str, /) -> ConversationState | None:
        """Return the current state or ``None`` when it does not exist."""
        ...


class StateSaver(Protocol):
    """Save caller-owned state by one bounded conversation key."""

    async def __call__(self, key: str, state: ConversationState, /) -> None:
        """Save the complete updated state."""
        ...


async def run_stored(
    harness: ConversationHarness,
    key: str,
    *,
    load: StateLoader,
    save: StateSaver,
) -> ConversationState:
    """Load state, run one workflow, save it, and return it."""
    _validate_key(key)
    state = await load(key)
    if state is None:
        msg = f"Conversation '{key}' does not exist."
        raise ConversationNotFoundError(msg)
    updated = await harness.run(state)
    await save(key, updated)
    return updated


class InMemoryConversationStore:
    """Provide a bounded non-durable store for tests and short-lived processes."""

    def __init__(
        self,
        *,
        max_conversations: int = 100,
        max_messages_per_conversation: int = 1_000,
        max_bytes_per_conversation: int = _MAXIMUM_CONTEXT_BYTES,
    ) -> None:
        """Initialize explicit finite capacity and state-size limits."""
        if not 1 <= max_conversations <= _MAXIMUM_STORED_CONVERSATIONS:
            msg = "The in-memory conversation limit is invalid."
            raise RouterContractError(msg)
        self._state_limits = ContextLimits(
            max_messages=max_messages_per_conversation,
            max_bytes=max_bytes_per_conversation,
        )
        self._max_conversations = max_conversations
        self._states: dict[str, ConversationState] = {}
        self._lock = Lock()

    async def load(self, key: str, /) -> ConversationState | None:
        """Return one immutable state or ``None`` when it does not exist."""
        _validate_key(key)
        with self._lock:
            return self._states.get(key)

    async def save(self, key: str, state: ConversationState, /) -> None:
        """Save one bounded state without eviction."""
        _validate_key(key)
        if not _within_store_limits(state.messages, self._state_limits):
            msg = "The conversation exceeds the in-memory state limit."
            raise StoreLimitError(msg)
        with self._lock:
            if key not in self._states and len(self._states) >= self._max_conversations:
                msg = "The in-memory conversation capacity is full."
                raise StoreLimitError(msg)
            self._states[key] = state

    async def delete(self, key: str, /) -> bool:
        """Delete one state and report if it existed."""
        _validate_key(key)
        with self._lock:
            return self._states.pop(key, None) is not None


def prune_messages(
    messages: tuple[ModelMessage, ...],
    limits: ContextLimits,
) -> tuple[ModelMessage, ...]:
    """Keep the system prefix and newest complete exchanges within limits."""
    groups = _conversation_groups(messages)
    prefix = (
        groups[0]
        if groups and all(isinstance(message, SystemMessage) for message in groups[0])
        else ()
    )
    candidates = groups[1:] if prefix else groups
    kept = prefix
    if not _within_limits(kept, limits):
        msg = "The system prefix exceeds the context limits."
        raise InvalidConversationError(msg)
    for position, group in enumerate(reversed(candidates)):
        proposed = (*group, *kept[len(prefix) :])
        proposed = (*prefix, *proposed)
        if _within_limits(proposed, limits):
            kept = proposed
        elif position == 0:
            msg = (
                "The newest complete conversation exchange exceeds the context limits."
            )
            raise InvalidConversationError(msg)
        else:
            break
    return kept


def _conversation_groups(  # noqa: C901 - Validation follows the message sequence.
    messages: tuple[ModelMessage, ...],
) -> tuple[tuple[ModelMessage, ...], ...]:
    groups: list[tuple[ModelMessage, ...]] = []
    index = 0
    prefix: list[ModelMessage] = []
    while index < len(messages) and isinstance(messages[index], SystemMessage):
        prefix.append(messages[index])
        index += 1
    if prefix:
        groups.append(tuple(prefix))
    if any(isinstance(message, SystemMessage) for message in messages[index:]):
        msg = "System messages must form one leading prefix."
        raise InvalidConversationError(msg)
    seen_calls: set[str] = set()
    while index < len(messages):
        message = messages[index]
        if isinstance(message, UserMessage):
            if any(isinstance(part, ToolResultPart) for part in message.content):
                msg = "A tool result has no preceding tool call."
                raise InvalidConversationError(msg)
            groups.append((message,))
            index += 1
            continue
        calls = tuple(
            part for part in message.content if isinstance(part, ToolCallPart)
        )
        for call in calls:
            if call.id in seen_calls:
                msg = "Tool call IDs must be unique in a conversation."
                raise InvalidConversationError(msg)
            seen_calls.add(call.id)
        if not calls:
            groups.append((message,))
            index += 1
            continue
        has_result = index + 1 < len(messages) and isinstance(
            messages[index + 1], UserMessage
        )
        if not has_result:
            msg = "A tool-call message needs its result message."
            raise InvalidConversationError(msg)
        result_message = messages[index + 1]
        results = tuple(
            part for part in result_message.content if isinstance(part, ToolResultPart)
        )
        call_ids = tuple(call.id for call in calls)
        result_ids = tuple(result.tool_call_id for result in results)
        if call_ids != result_ids:
            msg = "Tool results must match tool calls in model order."
            raise InvalidConversationError(msg)
        groups.append((message, result_message))
        index += 2
    return tuple(groups)


def _validate_tool_results(
    calls: tuple[ToolCallPart, ...],
    results: tuple[ToolResultPart, ...],
) -> None:
    if tuple(call.id for call in calls) != tuple(
        result.tool_call_id for result in results
    ):
        msg = "The tool executor must return one result per call in order."
        raise ToolProtocolError(msg)


def _validate_model_result(call: ModelCall, result: ModelCallResult) -> None:
    if isinstance(call.selector, ExactModelSelector) and result.route != call.selector:
        msg = "The model caller returned a route that does not match the exact call."
        raise ModelProtocolError(msg)
    if result.route in call.excluded_routes:
        msg = "The model caller returned an excluded route."
        raise ModelProtocolError(msg)


def _validate_compaction(
    original: tuple[ModelMessage, ...],
    compacted: tuple[ModelMessage, ...],
    limits: ContextLimits,
) -> None:
    original_groups = _conversation_groups(original)
    compacted_groups = _conversation_groups(compacted)
    if not compacted or not _within_limits(compacted, limits):
        msg = "Model compaction returned unbounded context."
        raise CompactionError(msg)
    original_prefix = _system_prefix(original_groups)
    compacted_prefix = _system_prefix(compacted_groups)
    if original_prefix != compacted_prefix:
        msg = "Model compaction changed the system prefix."
        raise CompactionError(msg)
    if original_groups[-1] != compacted_groups[-1]:
        msg = "Model compaction changed the active conversation suffix."
        raise CompactionError(msg)


def _system_prefix(
    groups: tuple[tuple[ModelMessage, ...], ...],
) -> tuple[ModelMessage, ...]:
    if groups and all(isinstance(message, SystemMessage) for message in groups[0]):
        return groups[0]
    return ()


def _within_limits(messages: tuple[ModelMessage, ...], limits: ContextLimits) -> bool:
    return (
        len(messages) <= limits.max_messages
        and message_bytes(messages) <= limits.max_bytes
    )


def _within_store_limits(
    messages: tuple[ModelMessage, ...], limits: ContextLimits
) -> bool:
    image_bytes = sum(
        len(part.data)
        for message in messages
        if isinstance(message, UserMessage)
        for part in message.content
        if isinstance(part, ImageInputPart)
    )
    return (
        len(messages) <= limits.max_messages
        and message_bytes(messages) + image_bytes <= limits.max_bytes
    )


def _validate_key(key: str) -> None:
    if not 1 <= len(key) <= _MAXIMUM_CONVERSATION_KEY:
        msg = "The conversation key is invalid."
        raise RouterContractError(msg)
