"""Tests for the byte count text."""

import pytest

from opendle.sizes import format_bytes


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1 KB"),
        (1550, "1.6 KB"),
        (999_999, "1 MB"),
        (10_000_000_000, "10 GB"),
        (2_345_000_000_000, "2.3 TB"),
        (5 * 10**18, "5000 PB"),
    ],
)
def test_sizes_use_the_largest_decimal_unit(size: int, text: str) -> None:
    """Use decimal units and one decimal place at most."""
    assert format_bytes(size) == text


def test_a_negative_size_is_refused() -> None:
    """Refuse a value that is not a byte count."""
    with pytest.raises(ValueError, match="negative"):
        format_bytes(-1)
