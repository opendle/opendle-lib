"""Strict private JSON decoding shared by public clients."""

from __future__ import annotations

import json
from enum import Enum
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from opendle.contracts import JsonObject, JsonValue


class StrictJsonErrorReason(Enum):
    """Name one strict JSON rejection reason."""

    DUPLICATE_KEY = "duplicate_key"
    NON_FINITE_NUMBER = "non_finite_number"
    INVALID_UNICODE = "invalid_unicode"


class StrictJsonError(ValueError):
    """Report one strict JSON rejection with a stable private reason."""

    def __init__(self, reason: StrictJsonErrorReason) -> None:
        """Initialize one strict JSON error."""
        self.reason = reason
        super().__init__(reason.value)


def strict_json_loads(body: bytes) -> JsonValue:
    """Decode UTF-8 JSON and reject duplicate keys and unsafe values."""
    value = cast(
        "JsonValue",
        json.loads(
            body,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        ),
    )
    _validate_unicode(value)
    return value


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(StrictJsonErrorReason.DUPLICATE_KEY)
        result[key] = value
    return result


def _reject_constant(_value: str) -> JsonValue:
    raise StrictJsonError(StrictJsonErrorReason.NON_FINITE_NUMBER)


def _validate_unicode(value: JsonValue) -> None:
    pending: list[JsonValue] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise StrictJsonError(StrictJsonErrorReason.INVALID_UNICODE) from None
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
