"""Tests for the emoji counts of a text."""

import re

import pytest

from opendle.emoji import EMOJI_PATTERN, emoji_counts


def test_counts_keep_the_order_of_the_first_use() -> None:
    """Count each emoji and keep the order of the text."""
    assert emoji_counts("Go 🔥 now 🙏🔥 ✨") == {"🔥": 2, "🙏": 1, "✨": 1}


@pytest.mark.parametrize(
    "emoji",
    [
        "👍🏽",
        "☝🏽",
        "❤️",
        "❤️‍🔥",
        "👨‍👩‍👧",
        "🧑🏽‍💻",
        "🏳️‍🌈",
        "🏴‍☠️",
        "👁️‍🗨️",
        "🇫🇷",
        "1️⃣",
        "#⃣",
        "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
        "🅰",
        "🟢",
        "🫶",
    ],
)
def test_a_sequence_is_one_emoji(emoji: str) -> None:
    """Keep skin tones, joined parts, flags, keycaps and tags in one emoji."""
    assert emoji_counts(f"a {emoji} b") == {emoji: 1}


@pytest.mark.parametrize(
    "text",
    [
        "Plain text, 123, #tag, *bold*.",
        "© 2026 ™ ❤ → ★ ♟",
        "⌚︎",
        "🄰🄱 \U0001f1eb",
        "🏽",
    ],
)
def test_text_symbols_are_not_emoji(text: str) -> None:
    """Ignore text-style symbols, boxed letters, lone flag letters and tones."""
    assert emoji_counts(text) == {}


def test_the_pattern_has_only_ascii_escapes() -> None:
    """Let a database use the same expression as Python."""
    assert EMOJI_PATTERN.isascii()
    assert re.fullmatch(EMOJI_PATTERN, "🔥") is not None
