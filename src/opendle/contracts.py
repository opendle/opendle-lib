"""General provider-neutral JSON contract values and canonical encoding."""

from __future__ import annotations

import json
import math
from typing import cast

__all__ = ["JsonObject", "JsonValue", "canonical_json_bytes"]

type JsonValue = bool | int | float | str | list[JsonValue] | JsonObject | None
type JsonObject = dict[str, JsonValue]

_MAXIMUM_SAFE_INTEGER = 9_007_199_254_740_991
_PLAIN_DECIMAL_MINIMUM = 1e-6
_PLAIN_DECIMAL_LIMIT = 1e21


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Return the RFC 8785 canonical JSON encoding of one public value.

    Args:
        value: One JSON-equivalent value.

    Returns:
        The canonical UTF-8 JSON bytes.

    Raises:
        TypeError: If a mapping key is not text.
        ValueError: If the value is not an interoperable JSON value.

    """
    return _canonical_json(value).encode("utf-8")


def _canonical_json(value: object) -> str:  # noqa: C901, PLR0911
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if not -_MAXIMUM_SAFE_INTEGER <= value <= _MAXIMUM_SAFE_INTEGER:
            msg = "A JSON integer is outside the interoperable range."
            raise ValueError(msg)
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, str):
        _valid_unicode(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        items = cast("list[object]", value)
        return "[" + ",".join(_canonical_json(item) for item in items) + "]"
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        for key in mapping:
            if not isinstance(key, str):
                msg = "A JSON mapping key is not a string."
                raise TypeError(msg)
            _valid_unicode(key)
        string_mapping = cast("dict[str, object]", mapping)
        keys = sorted(string_mapping, key=lambda key: key.encode("utf-16be"))
        return (
            "{"
            + ",".join(
                f"{_canonical_json(key)}:{_canonical_json(string_mapping[key])}"
                for key in keys
            )
            + "}"
        )
    msg = "The value is not a JSON-equivalent value."
    raise ValueError(msg)


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        msg = "A JSON number must be finite."
        raise ValueError(msg)
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    source = repr(abs(value)).lower()
    if "e" in source:
        coefficient, exponent_text = source.split("e", 1)
        exponent = int(exponent_text)
    else:
        coefficient = source
        exponent = 0
    integer, _dot, fraction = coefficient.partition(".")
    digits = integer + fraction
    decimal_position = len(integer) + exponent
    if _PLAIN_DECIMAL_MINIMUM <= abs(value) < _PLAIN_DECIMAL_LIMIT:
        if decimal_position <= 0:
            result = "0." + ("0" * -decimal_position) + digits
        elif decimal_position >= len(digits):
            result = digits + ("0" * (decimal_position - len(digits)))
        else:
            result = digits[:decimal_position] + "." + digits[decimal_position:]
        return sign + result.rstrip("0").rstrip(".") if "." in result else sign + result
    normalized = digits[0]
    remainder = digits[1:].rstrip("0")
    if remainder:
        normalized += "." + remainder
    scientific_exponent = decimal_position - 1
    exponent_sign = "+" if scientific_exponent >= 0 else ""
    return f"{sign}{normalized}e{exponent_sign}{scientific_exponent}"


def _valid_unicode(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        msg = "A JSON string has invalid Unicode text."
        raise ValueError(msg) from None
