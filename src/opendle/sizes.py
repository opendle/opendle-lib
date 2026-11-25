"""Write a byte count as a short text for people."""

__all__ = ["format_bytes"]

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_STEP = 1000


def format_bytes(size: int) -> str:
    """Return a byte count in decimal units, for example `1.5 GB`.

    The text uses the largest unit that keeps a value of 1 or more, and one
    decimal place at most. Bytes have no decimal place.

    Raises:
        ValueError: If the size is negative.

    """
    if size < 0:
        msg = "A byte count must not be negative."
        raise ValueError(msg)
    value = float(size)
    unit = 0
    # Round first, so 999 999 bytes give `1 MB` and not `1000 KB`.
    while unit < len(_UNITS) - 1 and round(value, 1) >= _STEP:
        value /= _STEP
        unit += 1
    if unit == 0:
        return f"{size} B"
    text = f"{value:.1f}".removesuffix(".0")
    return f"{text} {_UNITS[unit]}"
