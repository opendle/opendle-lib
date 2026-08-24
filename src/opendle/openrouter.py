"""Parse bounded public OpenRouter model catalog snapshots.

This module does not fetch a catalog. A host owns HTTP authority, deadlines,
response byte limits, caching, and storage. The parser returns only source
facts that the public catalog states or that follow directly from one exact
model identifier.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final, Literal, cast
from urllib.parse import urlsplit

__all__ = [
    "OpenRouterCapability",
    "OpenRouterCatalogError",
    "OpenRouterConstraint",
    "OpenRouterDuplicateModelError",
    "OpenRouterError",
    "OpenRouterInputModality",
    "OpenRouterModelFacts",
    "OpenRouterModelNotFoundError",
    "OpenRouterOutputModality",
    "OpenRouterPriceOverride",
    "OpenRouterPriceSourceValue",
    "OpenRouterPriceUnit",
    "OpenRouterReasoningFacts",
    "OpenRouterReferenceError",
    "normalize_openrouter_model_reference",
    "parse_openrouter_model_snapshot",
]

_MAXIMUM_REFERENCE_BYTES: Final[int] = 512
_MAXIMUM_MODEL_ID_BYTES: Final[int] = 240
_MAXIMUM_VENDOR_BYTES: Final[int] = 80
_MAXIMUM_DISPLAY_NAME_CHARACTERS: Final[int] = 500
_MAXIMUM_SNAPSHOT_BYTES: Final[int] = 8 * 1024 * 1024
_MAXIMUM_CONTAINER_ITEMS: Final[int] = 10_000
_MAXIMUM_OBJECT_MEMBERS: Final[int] = 128
_MAXIMUM_CONTAINER_DEPTH: Final[int] = 10
_MAXIMUM_JSON_NODES: Final[int] = 200_000
_MAXIMUM_JSON_STRING_CHARACTERS: Final[int] = 16_384
_MAXIMUM_JSON_KEY_CHARACTERS: Final[int] = 128
_MAXIMUM_JSON_CHARACTER_SOURCE_BYTES: Final[int] = 12
_MAXIMUM_MODALITIES: Final[int] = 16
_MAXIMUM_SUPPORTED_PARAMETERS: Final[int] = 128
_MAXIMUM_REASONING_EFFORTS: Final[int] = 16
_MAXIMUM_PRICE_OVERRIDES: Final[int] = 128
_MAXIMUM_SOURCE_TOKEN_CHARACTERS: Final[int] = 64
_MAXIMUM_TOKEN_BOUND: Final[int] = 2_147_483_647
_MAXIMUM_NUMBER_DIGITS: Final[int] = 40
_MAXIMUM_NUMBER_EXPONENT: Final[int] = 100
_MAXIMUM_PRICE: Final[Decimal] = Decimal(1000000000000)
_ASCII_SPACE: Final[int] = 0x20
_ASCII_DELETE: Final[int] = 0x7F
_MODEL_ID_PARTS: Final[int] = 2
_MAXIMUM_UTC_CLOCK: Final[int] = 2359
_MAXIMUM_UTC_MINUTE: Final[int] = 59

_MODEL_PART = r"[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9])?"
_MODEL_ID = re.compile(rf"^(?P<vendor>~?{_MODEL_PART})/(?P<model>{_MODEL_PART})$")
_SOURCE_TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")
_PRICE_TEXT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?$")


class OpenRouterError(ValueError):
    """Base error for bounded OpenRouter catalog parsing."""


class OpenRouterReferenceError(OpenRouterError):
    """Report an unsafe or malformed OpenRouter model reference."""

    def __init__(self) -> None:
        """Create the stable safe reference error."""
        super().__init__("The OpenRouter model reference is invalid.")


class OpenRouterCatalogError(OpenRouterError):
    """Report an unsafe or malformed OpenRouter catalog snapshot."""

    def __init__(self) -> None:
        """Create the stable safe catalog error."""
        super().__init__("The OpenRouter catalog snapshot is invalid.")


class OpenRouterDuplicateModelError(OpenRouterCatalogError):
    """Report duplicate rows for the selected exact model identifier."""

    def __init__(self) -> None:
        """Create the stable safe duplicate-model error."""
        OpenRouterError.__init__(
            self, "The OpenRouter catalog has a duplicate model identifier."
        )


class OpenRouterModelNotFoundError(OpenRouterError):
    """Report that one exact model identifier is absent from a catalog."""

    def __init__(self) -> None:
        """Create the stable safe missing-model error."""
        super().__init__("The OpenRouter model is not in the catalog snapshot.")


class OpenRouterInputModality(Enum):
    """Name one recognized OpenRouter input modality source fact."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"


class OpenRouterOutputModality(Enum):
    """Name one recognized OpenRouter output modality source fact."""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    EMBEDDING = "embedding"


class OpenRouterCapability(Enum):
    """Name one recognized OpenRouter call capability source fact."""

    TOOL_CALLING = "tool_calling"
    STRUCTURED_JSON = "structured_json"
    STREAMING = "streaming"
    REASONING = "reasoning"


class OpenRouterConstraint(Enum):
    """Name one recognized parameter constraint supported by a model."""

    MAXIMUM_OUTPUT_TOKENS = "maximum_output_tokens"
    TEMPERATURE = "temperature"
    TOP_P = "top_p"
    TOP_K = "top_k"
    MIN_P = "min_p"
    SEED = "seed"
    STOP = "stop"
    FREQUENCY_PENALTY = "frequency_penalty"
    PRESENCE_PENALTY = "presence_penalty"
    REPETITION_PENALTY = "repetition_penalty"
    LOGIT_BIAS = "logit_bias"
    LOGPROBS = "logprobs"
    TOP_LOGPROBS = "top_logprobs"


class OpenRouterPriceUnit(Enum):
    """Name one recognized unit used by an OpenRouter source price."""

    INPUT_TOKEN = "input_token"  # noqa: S105 - This is a usage unit.
    OUTPUT_TOKEN = "output_token"  # noqa: S105 - This is a usage unit.
    CACHED_INPUT_TOKEN = "cached_input_token"  # noqa: S105 - Usage unit.
    CACHE_WRITE_INPUT_TOKEN = "cache_write_input_token"  # noqa: S105 - Usage unit.
    CACHE_WRITE_1H_INPUT_TOKEN = "cache_write_1h_input_token"  # noqa: S105
    INPUT_IMAGE = "input_image"
    OUTPUT_IMAGE = "output_image"
    IMAGE_TOKEN = "image_token"  # noqa: S105 - This is a usage unit.
    REQUEST = "request"
    WEB_SEARCH = "web_search"
    INTERNAL_REASONING_TOKEN = "internal_reasoning_token"  # noqa: S105 - Unit.
    AUDIO_INPUT_TOKEN = "audio_input_token"  # noqa: S105 - Usage unit.
    AUDIO_OUTPUT_TOKEN = "audio_output_token"  # noqa: S105 - Usage unit.
    CACHED_AUDIO_INPUT_TOKEN = "cached_audio_input_token"  # noqa: S105


@dataclass(frozen=True, slots=True)
class OpenRouterReasoningFacts:
    """Contain explicit OpenRouter reasoning support and configuration facts.

    ``source_configuration_available`` distinguishes a catalog reasoning object
    from support that only follows from ``supported_parameters``. A
    ``supported_efforts`` value of ``None`` is an unrestricted source value
    only when that source configuration is available.
    """

    supported: bool
    mandatory: bool | None
    source_configuration_available: bool = False
    default_enabled: bool | None = None
    default_effort: str | None = None
    supported_efforts: tuple[str, ...] | None = None
    supports_max_tokens: bool | None = None


@dataclass(frozen=True, slots=True)
class OpenRouterPriceSourceValue:
    """Contain the exact non-negative USD price for one named source unit."""

    unit: OpenRouterPriceUnit
    amount: Decimal
    source_field: str
    currency: Literal["USD"] = "USD"


@dataclass(frozen=True, slots=True)
class OpenRouterPriceOverride:
    """Contain one conditional OpenRouter price-source override.

    All non-null conditions apply together. Overrides stay in catalog order,
    where a later matching value replaces an earlier value for the same unit.
    """

    minimum_prompt_tokens: int | None
    utc_start: int | None
    utc_end: int | None
    price_source_values: tuple[OpenRouterPriceSourceValue, ...]


@dataclass(frozen=True, slots=True)
class OpenRouterModelFacts:
    """Contain immutable source facts for one public OpenRouter model row.

    ``model_id`` is the routable identifier. ``canonical_slug`` is the exact
    permanent source slug when the catalog supplies one. The parser does not
    derive a product model name from either identifier.
    """

    model_id: str
    canonical_slug: str | None
    display_source_name: str | None
    input_modalities: tuple[OpenRouterInputModality, ...]
    output_modalities: tuple[OpenRouterOutputModality, ...]
    capabilities: tuple[OpenRouterCapability, ...]
    context_window_tokens: int | None
    maximum_output_tokens: int | None
    reasoning: OpenRouterReasoningFacts
    supported_constraints: tuple[OpenRouterConstraint, ...]
    price_source_values: tuple[OpenRouterPriceSourceValue, ...]
    price_overrides: tuple[OpenRouterPriceOverride, ...]


_INPUT_MODALITIES: Final[dict[str, OpenRouterInputModality]] = {
    modality.value: modality for modality in OpenRouterInputModality
}
_OUTPUT_MODALITIES: Final[dict[str, OpenRouterOutputModality]] = {
    modality.value: modality for modality in OpenRouterOutputModality
}
_OUTPUT_MODALITIES["embeddings"] = OpenRouterOutputModality.EMBEDDING

_CAPABILITY_PARAMETERS: Final[
    tuple[tuple[OpenRouterCapability, frozenset[str]], ...]
] = (
    (
        OpenRouterCapability.TOOL_CALLING,
        frozenset({"functions", "tool_choice", "tools"}),
    ),
    (
        OpenRouterCapability.STRUCTURED_JSON,
        frozenset({"response_format", "structured_outputs"}),
    ),
    (OpenRouterCapability.STREAMING, frozenset({"stream", "streaming"})),
    (
        OpenRouterCapability.REASONING,
        frozenset({"include_reasoning", "reasoning"}),
    ),
)
_CONSTRAINT_PARAMETERS: Final[dict[str, OpenRouterConstraint]] = {
    "max_tokens": OpenRouterConstraint.MAXIMUM_OUTPUT_TOKENS,
    "max_completion_tokens": OpenRouterConstraint.MAXIMUM_OUTPUT_TOKENS,
    "temperature": OpenRouterConstraint.TEMPERATURE,
    "top_p": OpenRouterConstraint.TOP_P,
    "top_k": OpenRouterConstraint.TOP_K,
    "min_p": OpenRouterConstraint.MIN_P,
    "seed": OpenRouterConstraint.SEED,
    "stop": OpenRouterConstraint.STOP,
    "frequency_penalty": OpenRouterConstraint.FREQUENCY_PENALTY,
    "presence_penalty": OpenRouterConstraint.PRESENCE_PENALTY,
    "repetition_penalty": OpenRouterConstraint.REPETITION_PENALTY,
    "logit_bias": OpenRouterConstraint.LOGIT_BIAS,
    "logprobs": OpenRouterConstraint.LOGPROBS,
    "top_logprobs": OpenRouterConstraint.TOP_LOGPROBS,
}
_PRICE_FIELDS: Final[dict[str, OpenRouterPriceUnit]] = {
    "prompt": OpenRouterPriceUnit.INPUT_TOKEN,
    "completion": OpenRouterPriceUnit.OUTPUT_TOKEN,
    "input_cache_read": OpenRouterPriceUnit.CACHED_INPUT_TOKEN,
    "input_cache_write": OpenRouterPriceUnit.CACHE_WRITE_INPUT_TOKEN,
    "input_cache_write_1h": OpenRouterPriceUnit.CACHE_WRITE_1H_INPUT_TOKEN,
    "image": OpenRouterPriceUnit.INPUT_IMAGE,
    "image_output": OpenRouterPriceUnit.OUTPUT_IMAGE,
    "image_token": OpenRouterPriceUnit.IMAGE_TOKEN,
    "request": OpenRouterPriceUnit.REQUEST,
    "web_search": OpenRouterPriceUnit.WEB_SEARCH,
    "internal_reasoning": OpenRouterPriceUnit.INTERNAL_REASONING_TOKEN,
    "audio": OpenRouterPriceUnit.AUDIO_INPUT_TOKEN,
    "audio_output": OpenRouterPriceUnit.AUDIO_OUTPUT_TOKEN,
    "input_audio_cache": OpenRouterPriceUnit.CACHED_AUDIO_INPUT_TOKEN,
}


def normalize_openrouter_model_reference(value: str) -> str:
    """Return an exact ``vendor/model`` or ``~vendor/model`` identifier.

    The input can be an identifier or an HTTPS URL with the exact
    ``openrouter.ai`` authority. Supported URL paths are ``/vendor/model`` and
    ``/models/vendor/model``. Query strings, fragments, credentials, ports,
    escaped path bytes, non-ASCII text, and additional path parts are invalid.
    """
    if type(value) is not str:
        raise OpenRouterReferenceError
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise OpenRouterReferenceError from None
    if not encoded or len(encoded) > _MAXIMUM_REFERENCE_BYTES or value != value.strip():
        raise OpenRouterReferenceError
    if any(byte <= _ASCII_SPACE or byte == _ASCII_DELETE for byte in encoded):
        raise OpenRouterReferenceError

    candidate = value
    if "://" in value or value.startswith("//"):
        candidate = _model_id_from_url(value)

    return _validated_model_id(candidate, OpenRouterReferenceError)


def _model_id_from_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise OpenRouterReferenceError from None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != "openrouter.ai"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or "%" in parsed.path
    ):
        raise OpenRouterReferenceError
    path = parsed.path.removesuffix("/")
    if not path.startswith("/"):
        raise OpenRouterReferenceError
    path_parts = path.split("/")[1:]
    if path_parts[:1] == ["models"]:
        path_parts = path_parts[1:]
    if len(path_parts) != _MODEL_ID_PARTS:
        raise OpenRouterReferenceError
    return "/".join(path_parts)


def parse_openrouter_model_snapshot(
    snapshot: bytes | str,
    model_id_or_url: str,
) -> OpenRouterModelFacts:
    """Parse one selected model from a bounded catalog snapshot.

    The UTF-8 snapshot can contain at most 8 MiB, 10,000 model rows, 200,000
    JSON nodes, 10 container levels, 10,000 list items, and 128 object members.
    Duplicate JSON members and duplicate rows for the selected exact model are
    invalid. The parser maps and validates model fields only in the selected
    row. Snapshot-wide JSON safety bounds still apply to all rows.
    """
    model_id = normalize_openrouter_model_reference(model_id_or_url)
    raw = _snapshot_bytes(snapshot)
    try:
        _preflight_json_bounds(raw)
        return _decode_model(raw, model_id)
    except OpenRouterDuplicateModelError, OpenRouterModelNotFoundError:
        raise
    except (
        json.JSONDecodeError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
        _InvalidCatalogError,
    ):
        raise OpenRouterCatalogError from None


def _decode_model(raw: bytes, model_id: str) -> OpenRouterModelFacts:
    decoded = json.loads(
        raw,
        parse_float=Decimal,
        parse_int=int,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_unique_object,
    )
    root = cast("object", decoded)
    _validate_json_bounds(root)
    if not isinstance(root, dict):
        raise _InvalidCatalogError
    root_object = cast("dict[str, object]", root)
    data = root_object.get("data")
    if not isinstance(data, list):
        raise _InvalidCatalogError
    rows = cast("list[object]", data)

    selected_rows: list[dict[str, object]] = []
    for value in rows:
        if isinstance(value, dict):
            row = cast("dict[str, object]", value)
            if row.get("id") == model_id:
                selected_rows.append(row)
        if len(selected_rows) > 1:
            raise OpenRouterDuplicateModelError
    if not selected_rows:
        raise OpenRouterModelNotFoundError
    return _model_facts(selected_rows[0], model_id)


class _InvalidCatalogError(Exception):
    """Mark one internal catalog validation failure."""


@dataclass(slots=True)
class _JsonContainerScan:
    """Track one JSON container without allocating its decoded values."""

    opener: int
    separators: int = 0


def _preflight_json_bounds(raw: bytes) -> None:  # noqa: C901, PLR0912, PLR0915
    """Reject allocation-heavy JSON shapes before the standard decoder runs."""
    containers: list[_JsonContainerScan] = []
    nodes = 0
    index = 0
    while index < len(raw):
        byte = raw[index]
        if byte in b" \t\r\n":
            index += 1
            continue
        if byte == ord('"'):
            string_end = _json_string_end(raw, index + 1)
            following = string_end + 1
            while following < len(raw) and raw[following] in b" \t\r\n":
                following += 1
            maximum_raw_bytes = (
                _MAXIMUM_JSON_KEY_CHARACTERS * _MAXIMUM_JSON_CHARACTER_SOURCE_BYTES
                if following < len(raw) and raw[following] == ord(":")
                else _MAXIMUM_JSON_STRING_CHARACTERS
                * _MAXIMUM_JSON_CHARACTER_SOURCE_BYTES
            )
            if string_end - index - 1 > maximum_raw_bytes:
                raise _InvalidCatalogError
            if not (following < len(raw) and raw[following] == ord(":")):
                nodes += 1
            index = string_end + 1
        elif byte in {ord("["), ord("{")}:
            nodes += 1
            containers.append(_JsonContainerScan(opener=byte))
            if len(containers) > _MAXIMUM_CONTAINER_DEPTH:
                raise _InvalidCatalogError
            index += 1
        elif byte in {ord("]"), ord("}")}:
            expected = ord("[") if byte == ord("]") else ord("{")
            if not containers or containers[-1].opener != expected:
                raise _InvalidCatalogError
            containers.pop()
            index += 1
        elif byte == ord(","):
            if not containers:
                raise _InvalidCatalogError
            container = containers[-1]
            container.separators += 1
            maximum = (
                _MAXIMUM_CONTAINER_ITEMS
                if container.opener == ord("[")
                else _MAXIMUM_OBJECT_MEMBERS
            )
            if container.separators >= maximum:
                raise _InvalidCatalogError
            index += 1
        elif byte == ord(":"):
            index += 1
        else:
            token_end = index + 1
            while token_end < len(raw) and raw[token_end] not in b" \t\r\n,]}:":
                token_end += 1
            if token_end - index > _MAXIMUM_NUMBER_DIGITS + 8:
                raise _InvalidCatalogError
            nodes += 1
            index = token_end
        if nodes > _MAXIMUM_JSON_NODES:
            raise _InvalidCatalogError
    if containers:
        raise _InvalidCatalogError


def _json_string_end(raw: bytes, start: int) -> int:
    escaped = False
    for index in range(start, len(raw)):
        byte = raw[index]
        if escaped:
            escaped = False
        elif byte == ord("\\"):
            escaped = True
        elif byte == ord('"'):
            return index
    raise _InvalidCatalogError


def _snapshot_bytes(snapshot: bytes | str) -> bytes:
    if type(snapshot) is str:
        try:
            raw = snapshot.encode("utf-8")
        except UnicodeEncodeError:
            raise OpenRouterCatalogError from None
    elif type(snapshot) is bytes:
        raw = snapshot
    else:
        raise OpenRouterCatalogError
    if not raw or len(raw) > _MAXIMUM_SNAPSHOT_BYTES:
        raise OpenRouterCatalogError
    return raw


def _reject_json_constant(_value: str) -> object:
    raise _InvalidCatalogError


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if (
            key in result
            or len(key) > _MAXIMUM_JSON_KEY_CHARACTERS
            or any(unicodedata.category(character).startswith("C") for character in key)
        ):
            raise _InvalidCatalogError
        result[key] = value
    return result


def _validate_json_bounds(root: object) -> None:  # noqa: C901, PLR0912
    nodes = 0
    pending: list[tuple[object, int]] = [(root, 1)]
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > _MAXIMUM_JSON_NODES or depth > _MAXIMUM_CONTAINER_DEPTH:
            raise _InvalidCatalogError
        if isinstance(value, dict):
            object_value = cast("dict[str, object]", value)
            if len(object_value) > _MAXIMUM_OBJECT_MEMBERS:
                raise _InvalidCatalogError
            pending.extend((item, depth + 1) for item in object_value.values())
        elif isinstance(value, list):
            list_value = cast("list[object]", value)
            if len(list_value) > _MAXIMUM_CONTAINER_ITEMS:
                raise _InvalidCatalogError
            pending.extend((item, depth + 1) for item in list_value)
        elif isinstance(value, str):
            if len(value) > _MAXIMUM_JSON_STRING_CHARACTERS:
                raise _InvalidCatalogError
        elif isinstance(value, bool) or value is None:
            continue
        elif isinstance(value, int):
            if len(str(abs(value))) > _MAXIMUM_NUMBER_DIGITS:
                raise _InvalidCatalogError
        elif isinstance(value, Decimal):
            _validate_decimal_bounds(value)
        else:  # pragma: no cover - The configured JSON decoder is closed.
            raise _InvalidCatalogError


def _model_facts(row: dict[str, object], model_id: str) -> OpenRouterModelFacts:
    canonical_slug = _optional_model_id(row.get("canonical_slug"))
    display_name = _optional_display_name(row.get("name"))
    architecture = _optional_object(row.get("architecture"))
    input_values = _source_tokens(
        architecture.get("input_modalities"), maximum=_MAXIMUM_MODALITIES
    )
    output_values = _source_tokens(
        architecture.get("output_modalities"), maximum=_MAXIMUM_MODALITIES
    )
    supported_parameters = frozenset(
        _source_tokens(
            row.get("supported_parameters"),
            maximum=_MAXIMUM_SUPPORTED_PARAMETERS,
        )
    )
    reasoning = _reasoning_facts(row.get("reasoning"), supported_parameters)
    capabilities = tuple(
        capability
        for capability, parameters in _CAPABILITY_PARAMETERS
        if parameters.intersection(supported_parameters)
        or (capability is OpenRouterCapability.REASONING and reasoning.supported)
    )
    constraint_facts = {
        _CONSTRAINT_PARAMETERS[parameter]
        for parameter in supported_parameters
        if parameter in _CONSTRAINT_PARAMETERS
    }
    constraints = tuple(
        constraint
        for constraint in OpenRouterConstraint
        if constraint in constraint_facts
    )
    top_provider = _optional_object(row.get("top_provider"))
    context_window = _preferred_positive_integer(
        top_provider.get("context_length"), row.get("context_length")
    )
    maximum_output = _preferred_positive_integer(
        top_provider.get("max_completion_tokens"),
        row.get("max_completion_tokens"),
    )
    price_source_values, price_overrides = _pricing_facts(row.get("pricing"))
    return OpenRouterModelFacts(
        model_id=model_id,
        canonical_slug=canonical_slug,
        display_source_name=display_name,
        input_modalities=tuple(
            modality
            for modality in OpenRouterInputModality
            if modality in {_INPUT_MODALITIES.get(value) for value in input_values}
        ),
        output_modalities=tuple(
            modality
            for modality in OpenRouterOutputModality
            if modality in {_OUTPUT_MODALITIES.get(value) for value in output_values}
        ),
        capabilities=capabilities,
        context_window_tokens=context_window,
        maximum_output_tokens=maximum_output,
        reasoning=reasoning,
        supported_constraints=constraints,
        price_source_values=price_source_values,
        price_overrides=price_overrides,
    )


def _validated_model_id(value: str, error_type: type[Exception]) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise error_type from None
    match = _MODEL_ID.fullmatch(value)
    if (
        match is None
        or len(encoded) > _MAXIMUM_MODEL_ID_BYTES
        or len(match.group("vendor").encode("ascii")) > _MAXIMUM_VENDOR_BYTES
    ):
        raise error_type
    return value


def _optional_model_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _InvalidCatalogError
    return _validated_model_id(value, _InvalidCatalogError)


def _optional_display_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _InvalidCatalogError
    if (
        not value
        or value != value.strip()
        or len(value) > _MAXIMUM_DISPLAY_NAME_CHARACTERS
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise _InvalidCatalogError
    return value


def _optional_object(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _InvalidCatalogError
    return cast("dict[str, object]", value)


def _source_tokens(value: object, *, maximum: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _InvalidCatalogError
    items = cast("list[object]", value)
    if len(items) > maximum:
        raise _InvalidCatalogError
    tokens: list[str] = []
    for item in items:
        if (
            not isinstance(item, str)
            or len(item) > _MAXIMUM_SOURCE_TOKEN_CHARACTERS
            or _SOURCE_TOKEN.fullmatch(item) is None
            or item in tokens
        ):
            raise _InvalidCatalogError
        tokens.append(item)
    return tuple(tokens)


def _reasoning_facts(
    value: object, supported_parameters: frozenset[str]
) -> OpenRouterReasoningFacts:
    if value is None:
        return OpenRouterReasoningFacts(
            supported=bool(
                supported_parameters.intersection({"include_reasoning", "reasoning"})
            ),
            mandatory=None,
        )
    if not isinstance(value, dict):
        raise _InvalidCatalogError
    reasoning = cast("dict[str, object]", value)
    mandatory_value = _optional_bool(reasoning.get("mandatory"))
    default_enabled = _optional_bool(reasoning.get("default_enabled"))
    supports_max_tokens = _optional_bool(reasoning.get("supports_max_tokens"))
    default_effort = _optional_source_token(reasoning.get("default_effort"))
    supported_efforts_value = reasoning.get("supported_efforts")
    supported_efforts = (
        None
        if supported_efforts_value is None
        else _source_tokens(
            supported_efforts_value,
            maximum=_MAXIMUM_REASONING_EFFORTS,
        )
    )
    return OpenRouterReasoningFacts(
        supported=True,
        mandatory=mandatory_value,
        source_configuration_available=True,
        default_enabled=default_enabled,
        default_effort=default_effort,
        supported_efforts=supported_efforts,
        supports_max_tokens=supports_max_tokens,
    )


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _InvalidCatalogError
    return value


def _optional_source_token(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > _MAXIMUM_SOURCE_TOKEN_CHARACTERS
        or _SOURCE_TOKEN.fullmatch(value) is None
    ):
        raise _InvalidCatalogError
    return value


def _preferred_positive_integer(*values: object) -> int | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise _InvalidCatalogError
        number = value
        if number < 1 or number > _MAXIMUM_TOKEN_BOUND:
            raise _InvalidCatalogError
        return number
    return None


def _pricing_facts(
    value: object,
) -> tuple[
    tuple[OpenRouterPriceSourceValue, ...],
    tuple[OpenRouterPriceOverride, ...],
]:
    if value is None:
        return (), ()
    if not isinstance(value, dict):
        raise _InvalidCatalogError
    pricing = cast("dict[str, object]", value)
    overrides_value = pricing.get("overrides")
    overrides: tuple[OpenRouterPriceOverride, ...]
    if overrides_value is None:
        overrides = ()
    elif isinstance(overrides_value, list):
        override_items = cast("list[object]", overrides_value)
        if len(override_items) > _MAXIMUM_PRICE_OVERRIDES:
            raise _InvalidCatalogError
        overrides = tuple(_price_override(item) for item in override_items)
    else:
        raise _InvalidCatalogError
    return _price_source_values(pricing), overrides


def _price_source_values(
    pricing: dict[str, object],
) -> tuple[OpenRouterPriceSourceValue, ...]:
    prices: list[OpenRouterPriceSourceValue] = []
    for source_field, unit in _PRICE_FIELDS.items():
        raw_amount = pricing.get(source_field)
        if raw_amount is None:
            continue
        amount = _price_amount(raw_amount)
        prices.append(
            OpenRouterPriceSourceValue(
                unit=unit,
                amount=amount,
                source_field=source_field,
            )
        )
    return tuple(prices)


def _price_override(value: object) -> OpenRouterPriceOverride:
    if not isinstance(value, dict):
        raise _InvalidCatalogError
    override = cast("dict[str, object]", value)
    minimum_prompt_tokens = _optional_non_negative_integer(
        override.get("min_prompt_tokens")
    )
    utc_start = _optional_utc_clock(override.get("utc_start"))
    utc_end = _optional_utc_clock(override.get("utc_end"))
    if (utc_start is None) != (utc_end is None) or (
        utc_start is not None and utc_start == utc_end
    ):
        raise _InvalidCatalogError
    prices = _price_source_values(override)
    if (minimum_prompt_tokens is None and utc_start is None) or not prices:
        raise _InvalidCatalogError
    return OpenRouterPriceOverride(
        minimum_prompt_tokens=minimum_prompt_tokens,
        utc_start=utc_start,
        utc_end=utc_end,
        price_source_values=prices,
    )


def _optional_non_negative_integer(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InvalidCatalogError
    if value < 0 or value > _MAXIMUM_TOKEN_BOUND:
        raise _InvalidCatalogError
    return value


def _optional_utc_clock(value: object) -> int | None:
    number = _optional_non_negative_integer(value)
    if number is not None and (
        number > _MAXIMUM_UTC_CLOCK or number % 100 > _MAXIMUM_UTC_MINUTE
    ):
        raise _InvalidCatalogError
    return number


def _price_amount(value: object) -> Decimal:
    if isinstance(value, str) and _PRICE_TEXT.fullmatch(value) is not None:
        amount = Decimal(value)
    else:
        raise _InvalidCatalogError
    _validate_decimal_bounds(amount)
    if amount < 0 or amount > _MAXIMUM_PRICE:
        raise _InvalidCatalogError
    return amount


def _validate_decimal_bounds(value: Decimal) -> None:
    decimal_tuple = value.as_tuple()
    exponent = decimal_tuple.exponent
    if (
        not value.is_finite()
        or len(decimal_tuple.digits) > _MAXIMUM_NUMBER_DIGITS
        or not isinstance(exponent, int)
        or abs(exponent) > _MAXIMUM_NUMBER_EXPONENT
    ):
        raise _InvalidCatalogError
