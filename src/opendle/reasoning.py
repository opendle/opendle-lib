"""Framework-neutral reasoning controls for compatible text endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from opendle.contracts import JsonObject

__all__ = [
    "ReasoningLevel",
    "ReasoningStrategy",
    "reasoning_parameters",
    "reasoning_system_text",
]

type ReasoningLevel = Literal["none", "low", "medium", "high"]
type ReasoningStrategy = Literal[
    "none", "effort", "nested_effort", "thinking_type", "system_token", "native"
]

_THINK_TOKEN = "<|think|>"  # noqa: S105  # Public prompt delimiter.
_DIRECT_INSTRUCTION = "Do not think or reason. Answer directly."


def reasoning_parameters(
    strategy: ReasoningStrategy, level: ReasoningLevel, provider_value: str
) -> JsonObject:
    """Build control parameters without changing caller-owned request data.

    Hosts resolve their adapter default and level mapping before this call.
    ``system_token`` needs ``reasoning_system_text`` instead of a parameter.
    ``native`` supplies a boolean switch for mapped true/false values.

    Raises:
        ValueError: A strategy is not supported.

    """
    if strategy in {"none", "system_token"}:
        return {}
    if strategy == "effort":
        return {"reasoning_effort": provider_value}
    if strategy == "nested_effort":
        return {"reasoning": {"effort": provider_value}}
    if strategy == "thinking_type":
        return {"thinking": {"type": "disabled" if level == "none" else "enabled"}}
    if strategy == "native":
        value: str | bool = provider_value
        if provider_value in {"true", "false"}:
            value = provider_value == "true"
        return {"think": value}
    msg = "The reasoning strategy is not supported."
    raise ValueError(msg)


def reasoning_system_text(text: str, level: ReasoningLevel) -> str:
    """Apply a system token or direct-answer instruction exactly once.

    The input is one system message. The host retains message structure and
    decides where to put a new system message when none is present.

    """
    result = text.lstrip()
    if result.startswith(_THINK_TOKEN):
        result = result[len(_THINK_TOKEN) :].lstrip("\r\n ")
    if level == "none":
        if _DIRECT_INSTRUCTION in result:
            return result
        return f"{result}\n\n{_DIRECT_INSTRUCTION}" if result else _DIRECT_INSTRUCTION
    result = result.replace(_DIRECT_INSTRUCTION, "").rstrip()
    return f"{_THINK_TOKEN}\n{result}" if result else _THINK_TOKEN
