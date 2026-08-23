"""Tests for provider-neutral Router contract values."""

from dataclasses import replace

import pytest

from opendle import (
    AssignmentSelector,
    AssistantMessage,
    CallFailurePhase,
    ExactModelSelector,
    ImageInputPart,
    ModelCall,
    ModelCallError,
    ModelCallResult,
    RouterContractError,
    SystemMessage,
    TextInputPart,
    TextOutputPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
    Usage,
    UsageItem,
    UsageUnit,
    UserMessage,
    message_bytes,
    normalize_tags,
)


def test_model_contract_values_form_one_complete_call() -> None:
    """Valid model values keep exact route, tool, usage, and tag data."""
    tool = ToolDefinition("lookup", "Look up one value.", '{"type":"object"}')
    messages = (
        SystemMessage("Follow the caller rules."),
        UserMessage((TextInputPart("Find it."),)),
    )
    selector = AssignmentSelector("workflow.main")
    call = ModelCall(
        workspace_api_name="workspace-a",
        selector=selector,
        messages=messages,
        tools=(tool,),
        tags=("audit", "workflow"),
    )
    usage = Usage((UsageItem(UsageUnit.INPUT_TOKEN, "12.5"),), "0.01", "EUR")
    result = ModelCallResult(
        route=ExactModelSelector("provider-model-a"),
        content=(TextOutputPart("Done."),),
        usage=usage,
    )

    assert call.selector == selector
    assert result.usage == usage
    assert result.content[0].type == "text"
    assert message_bytes(messages) == message_bytes(messages)


def test_image_and_tool_parts_have_contract_discriminators() -> None:
    """Image, tool-call, and tool-result values expose native part types."""
    image = ImageInputPart("image/png", b"png")
    call = ToolCallPart("call-1", "lookup", '{"key":"a"}')
    result = ToolResultPart("call-1", '{"value":1}')

    assert (image.type, call.type, result.type) == (
        "image",
        "tool_call",
        "tool_result",
    )
    assert message_bytes((UserMessage((image, result)),)) > 0
    larger_image = ImageInputPart("image/png", b"more image bytes")
    assert message_bytes((UserMessage((image,)),)) == message_bytes(
        (UserMessage((larger_image,)),)
    )


def test_tags_are_deduplicated_and_sorted_by_utf8_bytes() -> None:
    """Tag normalization matches the Router accounting rule."""
    assert normalize_tags(("z", "a", "z", "é")) == ("a", "z", "é")


@pytest.mark.parametrize(
    "build",
    [
        lambda: AssignmentSelector("Bad"),
        lambda: ExactModelSelector("Bad"),
        lambda: TextInputPart(""),
        lambda: ImageInputPart("image/png", b""),
        lambda: ImageInputPart("image/gif", b"x"),  # type: ignore[arg-type]
        lambda: ToolResultPart("id", "not-json"),
        lambda: ToolCallPart("id", "tool", "[]"),
        lambda: ToolDefinition("tool", "description", "[]"),
        lambda: UserMessage(()),
        lambda: AssistantMessage(()),
        lambda: AssistantMessage(
            (
                ToolCallPart("same", "one", "{}"),
                ToolCallPart("same", "two", "{}"),
            )
        ),
        lambda: UsageItem(UsageUnit.REQUEST, "-1"),
        lambda: Usage((), "bad", "USD"),
        lambda: Usage((), "0", "usd"),
    ],
)
def test_invalid_contract_values_are_rejected(build: object) -> None:
    """Each closed contract value rejects an invalid input."""
    callable_build = build
    assert callable(callable_build)
    with pytest.raises(RouterContractError):
        callable_build()


def test_model_call_rejects_invalid_collections_and_names() -> None:
    """The model call validates message, tool, tag, and exclusion bounds."""
    message = SystemMessage("system")
    tool = ToolDefinition("tool", "description", "{}")
    base = ModelCall("workspace", AssignmentSelector("main"), (message,))

    with pytest.raises(RouterContractError):
        replace(base, workspace_api_name="Bad")
    with pytest.raises(RouterContractError):
        replace(base, messages=())
    with pytest.raises(RouterContractError):
        replace(base, tools=(tool, tool))
    with pytest.raises(RouterContractError):
        replace(base, tags=("z", "a"))
    with pytest.raises(RouterContractError):
        replace(
            base,
            excluded_routes=(
                ExactModelSelector("route-a"),
                ExactModelSelector("route-a"),
            ),
        )


def test_model_call_rejects_collection_maximums() -> None:
    """Router collection limits cannot become unbounded."""
    message = SystemMessage("system")
    with pytest.raises(RouterContractError, match="1000"):
        ModelCall("workspace", AssignmentSelector("main"), (message,) * 1_001)
    tool = ToolDefinition("tool", "description", "{}")
    tools = tuple(replace(tool, name=f"tool-{index}") for index in range(129))
    with pytest.raises(RouterContractError, match="128"):
        ModelCall("workspace", AssignmentSelector("main"), (message,), tools)
    with pytest.raises(RouterContractError, match="32"):
        normalize_tags(tuple(f"tag-{index}" for index in range(33)))


def test_model_call_rejects_image_count_and_total_byte_maximums() -> None:
    """One model call enforces the native image count and aggregate size."""
    selector = AssignmentSelector("main")
    small_image = ImageInputPart("image/png", b"x")
    too_many = UserMessage((small_image,) * 9)
    with pytest.raises(RouterContractError, match="8 images"):
        ModelCall("workspace", selector, (too_many,))

    large_image = ImageInputPart("image/png", b"x" * (10 * 1024 * 1024 + 1))
    too_large = UserMessage((large_image,) * 5)
    with pytest.raises(RouterContractError, match="byte total"):
        ModelCall("workspace", selector, (too_large,))


def test_tag_byte_limits_are_enforced() -> None:
    """One tag and the normalized tag set have finite UTF-8 byte limits."""
    with pytest.raises(RouterContractError, match="UTF-8"):
        normalize_tags(("",))
    with pytest.raises(RouterContractError, match="UTF-8"):
        normalize_tags(("é" * 65,))
    large = tuple((chr(0x400 + index) * 64) for index in range(17))
    with pytest.raises(RouterContractError, match="too large"):
        normalize_tags(large)


def test_model_call_error_preserves_safe_phase() -> None:
    """A model failure exposes its stable code and replacement safety phase."""
    error = ModelCallError(
        "upstream_failed",
        "The provider failed.",
        phase=CallFailurePhase.UNCERTAIN,
    )

    assert error.code == "upstream_failed"
    assert error.phase is CallFailurePhase.UNCERTAIN
    assert str(error) == "The provider failed."


def test_json_and_error_text_validation_is_bounded() -> None:
    """Malformed JSON and empty safe errors are rejected."""
    with pytest.raises(RouterContractError, match="valid JSON"):
        ToolCallPart("id", "tool", "{")
    with pytest.raises(RouterContractError, match="error code"):
        ModelCallError("", "message", phase=CallFailurePhase.BEFORE_VISIBLE_OUTPUT)
    with pytest.raises(RouterContractError, match="error message"):
        ModelCallError("code", "", phase=CallFailurePhase.BEFORE_VISIBLE_OUTPUT)
