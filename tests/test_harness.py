"""Tests for the stateless Router multi-turn harness."""

import asyncio
from typing import TYPE_CHECKING

import pytest

from opendle import (
    AssignmentSelector,
    AssistantMessage,
    CallFailurePhase,
    CompactionError,
    CompactionFailureMode,
    CompactionRequest,
    ContextLimits,
    ContextMethod,
    ContextPolicy,
    ConversationHarness,
    ConversationNotFoundError,
    ConversationState,
    ExactModelSelector,
    HarnessConfig,
    HarnessTool,
    ImageInputPart,
    InMemoryConversationStore,
    InvalidConversationError,
    ModelCall,
    ModelCallError,
    ModelCallResult,
    RouterContractError,
    RouteState,
    SequentialToolExecutor,
    StoreLimitError,
    SystemMessage,
    TextInputPart,
    TextOutputPart,
    ToolCallPart,
    ToolDefinition,
    ToolProtocolError,
    ToolResultPart,
    TurnLimitError,
    Usage,
    UserMessage,
    prune_messages,
    run_stored,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

type Response = ModelCallResult | Exception
_MULTI_TOOL_MESSAGE_COUNT = 4
_SINGLE_TURN_MESSAGE_COUNT = 2


class FakeModelCaller:
    """Return queued results and record each complete model call."""

    def __init__(self, responses: list[Response]) -> None:
        """Initialize the queued responses and empty call log."""
        self.responses = responses
        self.calls: list[ModelCall] = []

    async def __call__(self, call: ModelCall, /) -> ModelCallResult:
        """Return or raise the next queued response."""
        self.calls.append(call)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _state(*messages: object, route: str | None = None) -> ConversationState:
    typed_messages = tuple(messages)
    assert all(
        isinstance(message, (SystemMessage, UserMessage, AssistantMessage))
        for message in typed_messages
    )
    return ConversationState(
        messages=typed_messages,  # type: ignore[arg-type]
        route=RouteState(ExactModelSelector(route) if route else None),
    )


def _config(
    *,
    method: ContextMethod = ContextMethod.PRUNE,
    max_messages: int = 100,
    max_bytes: int = 10_000,
    failure: CompactionFailureMode = CompactionFailureMode.STOP,
    turns: int = 8,
) -> HarnessConfig:
    return HarnessConfig(
        workspace_api_name="workspace",
        assignment_api_name="workflow",
        context=ContextPolicy(method, ContextLimits(max_messages, max_bytes), failure),
        tags=("agent",),
        max_model_turns=turns,
    )


def _result(route: str, *content: object) -> ModelCallResult:
    assert all(isinstance(part, (TextOutputPart, ToolCallPart)) for part in content)
    return ModelCallResult(
        ExactModelSelector(route),
        tuple(content),  # type: ignore[arg-type]
        Usage((), "0", "USD"),
    )


def test_sequential_multi_tool_order_and_state_ownership() -> None:
    """Default execution is sequential and does not mutate the input state."""
    events: list[str] = []

    async def first(call: ToolCallPart) -> str:
        events.append(f"start-{call.id}")
        await asyncio.sleep(0)
        events.append(f"end-{call.id}")
        return '{"first":true}'

    async def second(call: ToolCallPart) -> str:
        events.append(f"start-{call.id}")
        events.append(f"end-{call.id}")
        return '{"second":true}'

    tools = (
        HarnessTool(ToolDefinition("first", "First tool.", "{}"), first),
        HarnessTool(ToolDefinition("second", "Second tool.", "{}"), second),
    )
    caller = FakeModelCaller(
        [
            _result(
                "route-a",
                TextOutputPart("Working."),
                ToolCallPart("one", "first", "{}"),
                ToolCallPart("two", "second", "{}"),
            ),
            _result("route-a", TextOutputPart("Done.")),
        ]
    )
    original = _state(UserMessage((TextInputPart("Start."),)))
    harness = ConversationHarness(model_caller=caller, tools=tools, config=_config())

    updated = asyncio.run(harness.run(original))

    assert events == ["start-one", "end-one", "start-two", "end-two"]
    assert len(original.messages) == 1
    assert len(updated.messages) == _MULTI_TOOL_MESSAGE_COUNT
    result_message = updated.messages[2]
    assert isinstance(result_message, UserMessage)
    result_parts = tuple(
        part for part in result_message.content if isinstance(part, ToolResultPart)
    )
    assert [part.tool_call_id for part in result_parts] == ["one", "two"]
    assert isinstance(caller.calls[0].selector, AssignmentSelector)
    assert caller.calls[1].selector == ExactModelSelector("route-a")
    assert updated.route.sticky == ExactModelSelector("route-a")


def test_custom_executor_replaces_complete_tool_execution() -> None:
    """A custom executor can replace the complete ordered tool batch."""
    batches: list[tuple[str, ...]] = []

    async def unused(_call: ToolCallPart) -> str:
        msg = "The default handler must not run."
        raise AssertionError(msg)

    async def custom(
        calls: tuple[ToolCallPart, ...], _tools: tuple[HarnessTool, ...]
    ) -> tuple[ToolResultPart, ...]:
        batches.append(tuple(call.id for call in calls))
        return tuple(
            ToolResultPart(call.id, f'{{"custom":"{call.id}"}}') for call in calls
        )

    caller = FakeModelCaller(
        [
            _result("route-a", ToolCallPart("one", "tool", "{}")),
            _result("route-a", TextOutputPart("Done.")),
        ]
    )
    harness = ConversationHarness(
        model_caller=caller,
        tools=(HarnessTool(ToolDefinition("tool", "Tool.", "{}"), unused),),
        config=_config(),
        tool_executor=custom,
    )

    asyncio.run(harness.run(_state(UserMessage((TextInputPart("Start."),)))))

    assert batches == [("one",)]


def test_sticky_failure_falls_back_once_and_replaces_route() -> None:
    """A safe sticky failure excludes that route from assignment fallback."""
    failure = ModelCallError(
        "upstream_failed",
        "Failed before output.",
        phase=CallFailurePhase.BEFORE_VISIBLE_OUTPUT,
    )
    caller = FakeModelCaller([failure, _result("route-b", TextOutputPart("Done."))])
    harness = ConversationHarness(model_caller=caller, tools=(), config=_config())

    updated = asyncio.run(
        harness.run(_state(UserMessage((TextInputPart("Start."),)), route="route-a"))
    )

    assert caller.calls[0].selector == ExactModelSelector("route-a")
    assert caller.calls[1].selector == AssignmentSelector("workflow")
    assert caller.calls[1].excluded_routes == (ExactModelSelector("route-a"),)
    assert updated.route.sticky == ExactModelSelector("route-b")


@pytest.mark.parametrize(
    "phase",
    [CallFailurePhase.AFTER_VISIBLE_OUTPUT, CallFailurePhase.UNCERTAIN],
)
def test_visible_or_uncertain_failure_does_not_create_replacement_call(
    phase: CallFailurePhase,
) -> None:
    """Unsafe sticky failures stop without an automatic replacement call."""
    failure = ModelCallError("upstream_failed", "Failed.", phase=phase)
    caller = FakeModelCaller([failure])
    harness = ConversationHarness(model_caller=caller, tools=(), config=_config())

    with pytest.raises(ModelCallError):
        asyncio.run(
            harness.run(
                _state(UserMessage((TextInputPart("Start."),)), route="route-a")
            )
        )
    assert len(caller.calls) == 1


def test_pruning_keeps_system_prefix_and_newest_complete_exchange() -> None:
    """Pruning drops oldest groups and never splits tool calls from results."""
    system = SystemMessage("System.")
    old = UserMessage((TextInputPart("Old."),))
    assistant = AssistantMessage((ToolCallPart("call", "tool", "{}"),))
    result = UserMessage((ToolResultPart("call", "{}"),))
    current = UserMessage((TextInputPart("Current."),))
    messages = (system, old, assistant, result, current)

    pruned = prune_messages(messages, ContextLimits(4, 10_000))

    assert pruned == (system, assistant, result, current)
    assert prune_messages((system,), ContextLimits(1, 10_000)) == (system,)


def test_harness_applies_selected_pruning_before_a_model_call() -> None:
    """The pruning policy changes returned state when current state is too large."""
    caller = FakeModelCaller([_result("route-a", TextOutputPart("Done."))])
    harness = ConversationHarness(
        model_caller=caller,
        tools=(),
        config=_config(max_messages=1),
    )
    current = UserMessage((TextInputPart("Current."),))

    updated = asyncio.run(
        harness.run(_state(UserMessage((TextInputPart("Old."),)), current))
    )

    assert caller.calls[0].messages == (current,)
    assert updated.messages[0] == current


def test_pruning_rejects_prefix_or_newest_group_that_cannot_fit() -> None:
    """Required context is never truncated inside a message."""
    with pytest.raises(InvalidConversationError, match="system prefix"):
        prune_messages(
            (
                SystemMessage("A long system message."),
                UserMessage((TextInputPart("x"),)),
            ),
            ContextLimits(2, 10),
        )
    with pytest.raises(InvalidConversationError, match="newest"):
        prune_messages(
            (UserMessage((TextInputPart("A long current message."),)),),
            ContextLimits(1, 10),
        )


def test_pruning_keeps_a_contiguous_newest_suffix() -> None:
    """Pruning does not keep an older group after a newer group does not fit."""
    old = UserMessage((TextInputPart("old"),))
    large_middle = UserMessage((TextInputPart("middle" * 100),))
    current = UserMessage((TextInputPart("current"),))
    current_limit = ContextLimits(3, 200)

    assert prune_messages((old, large_middle, current), current_limit) == (current,)


def test_model_compaction_uses_pinned_route_and_accepted_result() -> None:
    """Compaction receives the preceding exact route and replaces old context."""
    requests: list[CompactionRequest] = []
    system = SystemMessage("System.")
    current = UserMessage((TextInputPart("Current."),))

    async def compact(request: CompactionRequest) -> tuple[object, ...]:
        requests.append(request)
        return (system, current)

    caller = FakeModelCaller([_result("route-a", TextOutputPart("Done."))])
    harness = ConversationHarness(
        model_caller=caller,
        tools=(),
        config=_config(method=ContextMethod.MODEL, max_messages=2),
        compactor=compact,  # type: ignore[arg-type]
    )
    state = _state(
        system,
        UserMessage((TextInputPart("Old."),)),
        current,
        route="route-a",
    )

    updated = asyncio.run(harness.run(state))

    assert requests[0].route == ExactModelSelector("route-a")
    assert requests[0].messages == state.messages
    assert caller.calls[0].selector == ExactModelSelector("route-a")
    assert updated.messages[:2] == (system, current)


def test_compaction_failure_can_stop_or_prune() -> None:
    """The selected compaction failure mode is final or deterministic pruning."""

    async def fail(_request: CompactionRequest) -> tuple[object, ...]:
        msg = "compactor failed"
        raise RuntimeError(msg)

    state = _state(
        UserMessage((TextInputPart("Old."),)),
        UserMessage((TextInputPart("Current."),)),
        route="route-a",
    )
    stop = ConversationHarness(
        model_caller=FakeModelCaller([]),
        tools=(),
        config=_config(method=ContextMethod.MODEL, max_messages=1),
        compactor=fail,  # type: ignore[arg-type]
    )
    with pytest.raises(CompactionError) as captured:
        asyncio.run(stop.run(state))
    assert isinstance(captured.value.__cause__, RuntimeError)

    caller = FakeModelCaller([_result("route-a", TextOutputPart("Done."))])
    fallback = ConversationHarness(
        model_caller=caller,
        tools=(),
        config=_config(
            method=ContextMethod.MODEL,
            max_messages=1,
            failure=CompactionFailureMode.PRUNE,
        ),
        compactor=fail,  # type: ignore[arg-type]
    )
    updated = asyncio.run(fallback.run(state))
    assert updated.messages[0] == state.messages[-1]


def test_compaction_without_route_or_with_invalid_result_uses_policy() -> None:
    """Compaction needs a pinned route and must preserve prefix and suffix."""

    async def invalid(_request: CompactionRequest) -> tuple[object, ...]:
        return (UserMessage((TextInputPart("Changed."),)),)

    no_route = ConversationHarness(
        model_caller=FakeModelCaller([]),
        tools=(),
        config=_config(method=ContextMethod.MODEL, max_messages=1),
        compactor=invalid,  # type: ignore[arg-type]
    )
    with pytest.raises(CompactionError, match="preceding"):
        asyncio.run(
            no_route.run(
                _state(
                    UserMessage((TextInputPart("Old."),)),
                    UserMessage((TextInputPart("Current."),)),
                )
            )
        )

    state = _state(
        SystemMessage("System."),
        UserMessage((TextInputPart("Current."),)),
        route="route-a",
    )
    invalid_harness = ConversationHarness(
        model_caller=FakeModelCaller([]),
        tools=(),
        config=_config(method=ContextMethod.MODEL, max_messages=1),
        compactor=invalid,  # type: ignore[arg-type]
    )
    with pytest.raises(CompactionError, match=r"unbounded|prefix|suffix"):
        asyncio.run(invalid_harness.run(state))


def test_compaction_rejects_empty_and_changed_suffix_results() -> None:
    """A compactor cannot remove the active suffix or return empty context."""
    state = _state(
        UserMessage((TextInputPart("Old."),)),
        UserMessage((TextInputPart("Current."),)),
        route="route-a",
    )

    async def empty(_request: CompactionRequest) -> tuple[object, ...]:
        return ()

    empty_harness = ConversationHarness(
        model_caller=FakeModelCaller([]),
        tools=(),
        config=_config(method=ContextMethod.MODEL, max_messages=1),
        compactor=empty,  # type: ignore[arg-type]
    )
    with pytest.raises(CompactionError, match="unbounded"):
        asyncio.run(empty_harness.run(state))

    async def changed(_request: CompactionRequest) -> tuple[object, ...]:
        return (UserMessage((TextInputPart("Changed."),)),)

    changed_harness = ConversationHarness(
        model_caller=FakeModelCaller([]),
        tools=(),
        config=_config(method=ContextMethod.MODEL, max_messages=1),
        compactor=changed,  # type: ignore[arg-type]
    )
    with pytest.raises(CompactionError, match="suffix"):
        asyncio.run(changed_harness.run(state))


def test_load_and_save_callbacks_wrap_stateless_execution() -> None:
    """Small callbacks load once and save only the returned updated state."""
    events: list[str] = []
    state = _state(UserMessage((TextInputPart("Start."),)))

    async def load(key: str) -> ConversationState | None:
        events.append(f"load-{key}")
        return state

    async def save(key: str, updated: ConversationState) -> None:
        events.append(f"save-{key}-{len(updated.messages)}")

    harness = ConversationHarness(
        model_caller=FakeModelCaller([_result("route-a", TextOutputPart("Done."))]),
        tools=(),
        config=_config(),
    )

    updated = asyncio.run(run_stored(harness, "conversation", load=load, save=save))

    assert events == ["load-conversation", "save-conversation-2"]
    assert len(updated.messages) == _SINGLE_TURN_MESSAGE_COUNT


def test_missing_loaded_state_is_not_saved() -> None:
    """A missing caller state reports a clear error before model work."""
    saved = False

    async def load(_key: str) -> ConversationState | None:
        return None

    async def save(_key: str, _state: ConversationState) -> None:
        nonlocal saved
        saved = True

    harness = ConversationHarness(
        model_caller=FakeModelCaller([]), tools=(), config=_config()
    )
    with pytest.raises(ConversationNotFoundError):
        asyncio.run(run_stored(harness, "missing", load=load, save=save))
    assert saved is False


def test_in_memory_store_is_bounded_and_non_evicting() -> None:
    """The reference store rejects excess count and state size."""
    store = InMemoryConversationStore(
        max_conversations=1,
        max_messages_per_conversation=1,
        max_bytes_per_conversation=1_000,
    )
    state = _state(UserMessage((TextInputPart("One."),)))
    asyncio.run(store.save("one", state))
    assert asyncio.run(store.load("one")) == state
    with pytest.raises(StoreLimitError, match="capacity"):
        asyncio.run(store.save("two", state))
    too_large = _state(
        UserMessage((TextInputPart("One."),)),
        UserMessage((TextInputPart("Two."),)),
    )
    with pytest.raises(StoreLimitError, match="state limit"):
        asyncio.run(store.save("one", too_large))
    assert asyncio.run(store.delete("one")) is True
    assert asyncio.run(store.delete("one")) is False

    image_store = InMemoryConversationStore(max_bytes_per_conversation=500)
    image_state = _state(UserMessage((ImageInputPart("image/png", b"x" * 1_000),)))
    with pytest.raises(StoreLimitError, match="state limit"):
        asyncio.run(image_store.save("image", image_state))


def test_in_memory_store_rejects_invalid_keys() -> None:
    """Each store operation applies the bounded key contract."""
    store = InMemoryConversationStore()
    state = _state(UserMessage((TextInputPart("x"),)))
    operations: tuple[Awaitable[object], ...] = (
        store.load(""),
        store.save("", state),
        store.delete(""),
    )
    for operation in operations:
        with pytest.raises(RouterContractError, match="key"):
            asyncio.run(operation)


def test_input_validation_rejects_invalid_configuration_and_state() -> None:
    """Harness, context, tool, store, and message inputs are bounded."""
    with pytest.raises(RouterContractError):
        ContextLimits(0, 1)
    with pytest.raises(RouterContractError):
        ContextLimits(1, 2 * 1024 * 1024 + 1)
    with pytest.raises(RouterContractError, match="normalized"):
        HarnessConfig(
            "workspace",
            "workflow",
            ContextPolicy(ContextMethod.PRUNE, ContextLimits(1, 100)),
            tags=("z", "a"),
        )
    with pytest.raises(RouterContractError, match="turn limit"):
        HarnessConfig(
            "workspace",
            "workflow",
            ContextPolicy(ContextMethod.PRUNE, ContextLimits(1, 100)),
            max_model_turns=0,
        )
    with pytest.raises(RouterContractError, match="normalized"):
        CompactionRequest(
            "workspace",
            (UserMessage((TextInputPart("x"),)),),
            ExactModelSelector("route"),
            ("z", "a"),
            ContextLimits(1, 100),
        )
    tool = HarnessTool(
        ToolDefinition("tool", "Tool.", "{}"),
        lambda _call: asyncio.sleep(0, result="{}"),
    )
    with pytest.raises(RouterContractError, match="unique"):
        ConversationHarness(
            model_caller=FakeModelCaller([]),
            tools=(tool, tool),
            config=_config(),
        )
    many_tools = tuple(
        HarnessTool(
            ToolDefinition(f"tool-{index}", "Tool.", "{}"),
            lambda _call: asyncio.sleep(0, result="{}"),
        )
        for index in range(129)
    )
    with pytest.raises(RouterContractError, match="128"):
        ConversationHarness(
            model_caller=FakeModelCaller([]), tools=many_tools, config=_config()
        )
    with pytest.raises(RouterContractError, match="compactor"):
        ConversationHarness(
            model_caller=FakeModelCaller([]),
            tools=(),
            config=_config(method=ContextMethod.MODEL),
        )
    with pytest.raises(RouterContractError, match="in-memory"):
        InMemoryConversationStore(max_conversations=0)
    with pytest.raises(InvalidConversationError, match="must have"):
        ConversationState(())
    with pytest.raises(InvalidConversationError, match="leading"):
        _state(UserMessage((TextInputPart("x"),)), SystemMessage("late"))
    with pytest.raises(InvalidConversationError, match="too many"):
        ConversationState((UserMessage((TextInputPart("x"),)),) * 1_001)


@pytest.mark.parametrize(
    "messages",
    [
        (UserMessage((ToolResultPart("call", "{}"),)),),
        (
            AssistantMessage((ToolCallPart("call", "tool", "{}"),)),
            UserMessage((ToolResultPart("call", "{}"),)),
            AssistantMessage((ToolCallPart("call", "tool", "{}"),)),
            UserMessage((ToolResultPart("call", "{}"),)),
        ),
        (AssistantMessage((ToolCallPart("call", "tool", "{}"),)),),
        (
            AssistantMessage((ToolCallPart("call", "tool", "{}"),)),
            UserMessage((ToolResultPart("other", "{}"),)),
        ),
    ],
)
def test_conversation_rejects_incompatible_tool_sequences(
    messages: tuple[object, ...],
) -> None:
    """Conversation state rejects orphaned, duplicate, or misordered tool data."""
    with pytest.raises(InvalidConversationError):
        _state(*messages)


def test_tool_protocol_errors_cover_unknown_and_misaligned_results() -> None:
    """Default and custom executors must return complete correlated results."""
    call = ToolCallPart("call", "missing", "{}")
    with pytest.raises(ToolProtocolError, match="unknown"):
        asyncio.run(SequentialToolExecutor()((call,), ()))

    async def wrong(
        _calls: tuple[ToolCallPart, ...], _tools: tuple[HarnessTool, ...]
    ) -> tuple[ToolResultPart, ...]:
        return (ToolResultPart("other", "{}"),)

    caller = FakeModelCaller([_result("route-a", call)])
    harness = ConversationHarness(
        model_caller=caller,
        tools=(),
        config=_config(),
        tool_executor=wrong,
    )
    with pytest.raises(ToolProtocolError, match="per call"):
        asyncio.run(harness.run(_state(UserMessage((TextInputPart("x"),)))))


def test_turn_limit_stops_a_tool_loop() -> None:
    """A repeated tool loop stops at the selected finite model turn limit."""

    async def handler(_call: ToolCallPart) -> str:
        return "{}"

    caller = FakeModelCaller(
        [
            _result("route-a", ToolCallPart("one", "tool", "{}")),
            _result("route-a", ToolCallPart("two", "tool", "{}")),
        ]
    )
    harness = ConversationHarness(
        model_caller=caller,
        tools=(HarnessTool(ToolDefinition("tool", "Tool.", "{}"), handler),),
        config=_config(turns=2),
    )

    with pytest.raises(TurnLimitError, match="2"):
        asyncio.run(harness.run(_state(UserMessage((TextInputPart("x"),)))))
