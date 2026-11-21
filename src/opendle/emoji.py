"""Find the emoji in a text and count them."""

import re

__all__ = ["EMOJI_PATTERN", "emoji_counts"]

# Pictographs above U+FFFF that show as emoji without a variation selector.
# The ranges leave out letters in boxes, chess symbols, arrows and other
# symbols of the same blocks that are not emoji.
_PICTOGRAPHS = (
    r"\U0001F004\U0001F0CF\U0001F170\U0001F171\U0001F17E\U0001F17F\U0001F18E"
    r"\U0001F191-\U0001F19A\U0001F201\U0001F202\U0001F21A\U0001F22F"
    r"\U0001F232-\U0001F23A\U0001F250\U0001F251\U0001F300-\U0001F3FA"
    r"\U0001F400-\U0001F64F\U0001F680-\U0001F6FF\U0001F7E0-\U0001F7EB"
    r"\U0001F7F0\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF"
)
# Characters below U+FFFF with the Unicode property Emoji_Presentation.
_PRESENTATION = (
    "["
    r"\u231A\u231B\u23E9-\u23EC\u23F0\u23F3\u25FD\u25FE\u2614\u2615"
    r"\u2648-\u2653\u267F\u2693\u26A1\u26AA\u26AB\u26BD\u26BE\u26C4\u26C5"
    r"\u26CE\u26D4\u26EA\u26F2\u26F3\u26F5\u26FA\u26FD\u2705\u270A\u270B"
    r"\u2728\u274C\u274E\u2753-\u2755\u2757\u2795-\u2797\u27B0\u27BF"
    r"\u2B1B\u2B1C\u2B50\u2B55" + _PICTOGRAPHS + "]"
)
# Characters that show as text unless U+FE0F or a skin tone follows them.
_TEXT_STYLE = (
    r"[\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u2B55\u3030\u303D\u3297\u3299]"
)
_MODIFIER = r"[\U0001F3FB-\U0001F3FF]"
_TAGS = r"[\U000E0020-\U000E007E]+\U000E007F"
_ELEMENT = (
    rf"(?:{_PRESENTATION}(?:{_TAGS}|\uFE0F{_MODIFIER}?|{_MODIFIER}|(?!\uFE0E))"
    rf"|{_TEXT_STYLE}(?:\uFE0F{_MODIFIER}?|{_MODIFIER}))"
)
_JOINED = rf"(?:{_PRESENTATION}|{_TEXT_STYLE})\uFE0F?{_MODIFIER}?"

EMOJI_PATTERN = (
    rf"[\U0001F1E6-\U0001F1FF]{{2}}|[0-9#*]\uFE0F?\u20E3"
    rf"|{_ELEMENT}(?:\u200D{_JOINED})*"
)
"""The regular expression of one emoji, with escapes only.

Python `re` and PostgreSQL regular expressions accept it. One match is one
flag, one keycap, or one emoji with its skin tone, tags and joined parts.
"""

_EMOJI = re.compile(EMOJI_PATTERN)


def emoji_counts(text: str) -> dict[str, int]:
    """Return each emoji of a text with its number of uses, first use first.

    A character that shows as text by default counts only with U+FE0F or a
    skin tone after it. The emoji keep the exact characters of the text.
    """
    counts: dict[str, int] = {}
    for match in _EMOJI.finditer(text):
        counts[match.group()] = counts.get(match.group(), 0) + 1
    return counts
