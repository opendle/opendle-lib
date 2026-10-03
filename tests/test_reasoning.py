"""Reasoning parameter and system-message behavior."""

import pytest

from opendle.reasoning import reasoning_parameters, reasoning_system_text


def test_parameter_strategies_keep_the_mapped_effort() -> None:
    """Endpoints receive the host's exact configured provider value."""
    assert reasoning_parameters("effort", "none", "low") == {"reasoning_effort": "low"}
    assert reasoning_parameters("nested_effort", "high", "xhigh") == {
        "reasoning": {"effort": "xhigh"}
    }
    assert reasoning_parameters("thinking_type", "none", "low") == {
        "thinking": {"type": "disabled"}
    }
    assert reasoning_parameters("thinking_type", "low", "minimal") == {
        "thinking": {"type": "enabled"}
    }
    assert reasoning_parameters("native", "none", "false") == {"think": False}
    assert reasoning_parameters("native", "medium", "true") == {"think": True}
    assert reasoning_parameters("native", "high", "high") == {"think": "high"}
    assert reasoning_parameters("none", "high", "high") == {}
    assert reasoning_parameters("system_token", "high", "high") == {}


def test_system_token_switches_are_idempotent() -> None:
    """Enabled and disabled requests do not accumulate instructions."""
    enabled = reasoning_system_text("Follow these rules.", "medium")
    assert enabled == "<|think|>\nFollow these rules."
    assert reasoning_system_text(enabled, "high") == enabled
    disabled = reasoning_system_text(enabled, "none")
    assert "<|think|>" not in disabled
    assert disabled == "Follow these rules.\n\nDo not think or reason. Answer directly."
    assert reasoning_system_text(disabled, "none") == disabled
    assert reasoning_system_text(disabled, "low") == enabled
    assert reasoning_system_text("", "low") == "<|think|>"
    assert (
        reasoning_system_text("", "none") == "Do not think or reason. Answer directly."
    )


def test_unknown_strategy_is_rejected() -> None:
    """Runtime callers cannot silently ignore a spelling error."""
    with pytest.raises(ValueError, match="strategy is not supported"):
        reasoning_parameters("wrong", "high", "high")  # type: ignore[arg-type]
