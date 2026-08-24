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
from typing import Final, cast
from urllib.parse import urlsplit

__all__ = [
    "OpenRouterCapability",
    "OpenRouterCatalog",
    "OpenRouterCatalogError",
    "OpenRouterConstraint",
    "OpenRouterDuplicateModelError",
    "OpenRouterError",
    "OpenRouterInputModality",
    "OpenRouterModelFacts",
    "OpenRouterModelNotFoundError",
    "OpenRouterOutputModality",
    "OpenRouterPriceSourceValue",
    "OpenRouterPriceUnit",
    "OpenRouterReasoningFacts",
    "OpenRouterReferenceError",
    "normalize_openrouter_model_reference",
    "parse_openrouter_catalog_snapshot",
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
_MAXIMUM_MODALITIES: Final[int] = 16
_MAXIMUM_SUPPORTED_PARAMETERS: Final[int] = 128
_MAXIMUM_SOURCE_TOKEN_CHARACTERS: Final[int] = 64
_MAXIMUM_TOKEN_BOUND: Final[int] = 2_147_483_647
_MAXIMUM_NUMBER_DIGITS: Final[int] = 40
_MAXIMUM_NUMBER_EXPONENT: Final[int] = 100
_MAXIMUM_PRICE: Final[Decimal] = Decimal(1000000000000)
_ASCII_SPACE: Final[int] = 0x20
_ASCII_DELETE: Final[int] = 0x7F
_MODEL_ID_PARTS: Final[int] = 2

_MODEL_PART = r"[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9])?"
_MODEL_ID = re.compile(rf"^(?P<vendor>{_MODEL_PART})/(?P<model>{_MODEL_PART})$")
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
    """Report duplicate exact model identifiers in one catalog snapshot."""

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
    IMAGE = "image"
    REQUEST = "request"
    WEB_SEARCH = "web_search"
    INTERNAL_REASONING_TOKEN = "internal_reasoning_token"  # noqa: S105 - Unit.
    AUDIO_INPUT_TOKEN = "audio_input_token"  # noqa: S105 - Usage unit.


@dataclass(frozen=True, slots=True)
class OpenRouterReasoningFacts:
    """Contain explicit OpenRouter reasoning support and requirement facts."""

    supported: bool
    mandatory: bool | None


@dataclass(frozen=True, slots=True)
class OpenRouterPriceSourceValue:
    """Contain one exact non-negative USD price from an OpenRouter row."""

    unit: OpenRouterPriceUnit
    amount: Decimal
    source_field: str
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class OpenRouterModelFacts:
    """Contain immutable source facts for one public OpenRouter model row."""

    model_id: str
    canonical_source_name: str
    display_source_name: str | None
    input_modalities: tuple[OpenRouterInputModality, ...]
    output_modalities: tuple[OpenRouterOutputModality, ...]
    capabilities: tuple[OpenRouterCapability, ...]
    context_window_tokens: int | None
    maximum_output_tokens: int | None
    reasoning: OpenRouterReasoningFacts
    supported_constraints: tuple[OpenRouterConstraint, ...]
    price_source_values: tuple[OpenRouterPriceSourceValue, ...]


@dataclass(frozen=True, slots=True)
class OpenRouterCatalog:
    """Contain one validated immutable public OpenRouter catalog snapshot."""

    models: tuple[OpenRouterModelFacts, ...]

    def model(self, model_id_or_url: str) -> OpenRouterModelFacts:
        """Return one exact model or raise a stable safe missing-model error."""
        model_id = normalize_openrouter_model_reference(model_id_or_url)
        for model in self.models:
            if model.model_id == model_id:
                return model
        raise OpenRouterModelNotFoundError


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
    "image": OpenRouterPriceUnit.IMAGE,
    "request": OpenRouterPriceUnit.REQUEST,
    "web_search": OpenRouterPriceUnit.WEB_SEARCH,
    "internal_reasoning": OpenRouterPriceUnit.INTERNAL_REASONING_TOKEN,
    "audio": OpenRouterPriceUnit.AUDIO_INPUT_TOKEN,
}


def normalize_openrouter_model_reference(value: str) -> str:
    """Return an exact ``vendor/model`` identifier from one safe reference.

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


def parse_openrouter_catalog_snapshot(
    snapshot: bytes | str,
) -> OpenRouterCatalog:
    """Parse one bounded JSON catalog snapshot without network access.

    The UTF-8 snapshot can contain at most 8 MiB, 10,000 model rows, 200,000
    JSON nodes, 10 container levels, 10,000 list items, and 128 object members.
    Duplicate JSON members and duplicate exact model identifiers are invalid.
    """
    raw = _snapshot_bytes(snapshot)
    try:
        return _decode_catalog(raw)
    except OpenRouterDuplicateModelError:
        raise
    except (
        json.JSONDecodeError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
        _InvalidCatalogError,
    ):
        raise OpenRouterCatalogError from None


def _decode_catalog(raw: bytes) -> OpenRouterCatalog:
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

    seen: set[str] = set()
    models: list[OpenRouterModelFacts] = []
    for value in rows:
        if not isinstance(value, dict):
            raise _InvalidCatalogError
        facts = _model_facts(cast("dict[str, object]", value))
        if facts.model_id in seen:
            raise OpenRouterDuplicateModelError
        seen.add(facts.model_id)
        models.append(facts)
    return OpenRouterCatalog(tuple(models))


class _InvalidCatalogError(Exception):
    """Mark one internal catalog validation failure."""


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


def _model_facts(row: dict[str, object]) -> OpenRouterModelFacts:
    model_id_value = row.get("id")
    if not isinstance(model_id_value, str):
        raise _InvalidCatalogError
    model_id = _validated_model_id(model_id_value, _InvalidCatalogError)
    canonical_name = model_id.split("/", 1)[1]
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
    return OpenRouterModelFacts(
        model_id=model_id,
        canonical_source_name=canonical_name,
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
        price_source_values=_price_source_values(row.get("pricing")),
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


def _optional_display_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _InvalidCatalogError
    display_name = value.strip()
    if (
        not display_name
        or len(display_name) > _MAXIMUM_DISPLAY_NAME_CHARACTERS
        or any(
            unicodedata.category(character).startswith("C")
            for character in display_name
        )
    ):
        raise _InvalidCatalogError
    return display_name


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
    mandatory_value = reasoning.get("mandatory")
    if mandatory_value is not None and not isinstance(mandatory_value, bool):
        raise _InvalidCatalogError
    return OpenRouterReasoningFacts(
        supported=True,
        mandatory=mandatory_value,
    )


def _preferred_positive_integer(*values: object) -> int | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            raise _InvalidCatalogError
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and value.isascii() and value.isdecimal():
            number = int(value)
        else:
            raise _InvalidCatalogError
        if number < 1 or number > _MAXIMUM_TOKEN_BOUND:
            raise _InvalidCatalogError
        return number
    return None


def _price_source_values(value: object) -> tuple[OpenRouterPriceSourceValue, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise _InvalidCatalogError
    pricing = cast("dict[str, object]", value)
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


def _price_amount(value: object) -> Decimal:
    if isinstance(value, bool):
        raise _InvalidCatalogError
    if isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, Decimal):
        amount = value
    elif isinstance(value, str) and _PRICE_TEXT.fullmatch(value) is not None:
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
