"""Tests for bounded public OpenRouter catalog source facts."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from opendle import (
    OpenRouterCapability,
    OpenRouterCatalogError,
    OpenRouterConstraint,
    OpenRouterDuplicateModelError,
    OpenRouterInputModality,
    OpenRouterModelNotFoundError,
    OpenRouterOutputModality,
    OpenRouterPriceUnit,
    OpenRouterReferenceError,
    normalize_openrouter_model_reference,
    parse_openrouter_catalog_snapshot,
)

_MAXIMUM_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAXIMUM_MODEL_ID_BYTES = 240
_TOP_CONTEXT_BOUND = 131_072
_TOP_OUTPUT_BOUND = 8192
_ROOT_CONTEXT_BOUND = 4096
_ROOT_OUTPUT_BOUND = 1024
_MAXIMUM_INTEGER_BOUND = 2_147_483_647


def _snapshot(*rows: object, **extra: object) -> bytes:
    return json.dumps({"data": list(rows), **extra}).encode()


def _row(**replacements: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "google/gemma-4-31b-it",
        "name": " Google: Gemma 4 31B Instruct ",
        "context_length": 100_000,
        "architecture": {
            "input_modalities": ["text", "image", "audio", "video", "file"],
            "output_modalities": [
                "text",
                "image",
                "video",
                "audio",
                "embeddings",
            ],
        },
        "top_provider": {
            "context_length": "131072",
            "max_completion_tokens": 8192,
        },
        "supported_parameters": [
            "tools",
            "response_format",
            "stream",
            "reasoning",
            "max_tokens",
            "max_completion_tokens",
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "seed",
            "stop",
            "frequency_penalty",
            "presence_penalty",
            "repetition_penalty",
            "logit_bias",
            "logprobs",
            "top_logprobs",
        ],
        "reasoning": {"mandatory": True},
        "pricing": {
            "prompt": "0.00000013",
            "completion": 0.0000004,
            "input_cache_read": 0,
            "input_cache_write": "1e-8",
            "image": "0.01",
            "request": 1,
            "web_search": "0.02",
            "internal_reasoning": "0.00000002",
            "audio": "0.00000003",
            "future_unit": "not interpreted",
        },
    }
    row.update(replacements)
    return row


def test_reference_accepts_exact_ids_and_supported_https_urls() -> None:
    """Safe identifiers and the two supported URL paths keep the exact id."""
    references = (
        "google/gemma-4-31b-it",
        "https://openrouter.ai/google/gemma-4-31b-it",
        "https://openrouter.ai/google/gemma-4-31b-it/",
        "https://openrouter.ai/models/google/gemma-4-31b-it",
        "HTTPS://OPENROUTER.AI/models/google/gemma-4-31b-it",
    )
    for reference in references:
        assert (
            normalize_openrouter_model_reference(reference) == "google/gemma-4-31b-it"
        )


def test_reference_accepts_exact_identifier_boundaries() -> None:
    """The documented vendor and complete identifier maxima are inclusive."""
    identifier = f"{'v' * 80}/{'m' * 159}"
    assert len(identifier.encode()) == _MAXIMUM_MODEL_ID_BYTES
    assert normalize_openrouter_model_reference(identifier) == identifier


@pytest.mark.parametrize(
    "reference",
    [
        "",
        " google/model",
        "google/model ",
        "google",
        "google/model/extra",
        "google/mo del",
        "google/.model",
        "google/model:",
        "googlé/model",
        "google/mo\tdel",
        f"{'v' * 81}/model",
        f"vendor/{'m' * 234}",
        "http://openrouter.ai/google/model",
        "ftp://openrouter.ai/google/model",
        "https://www.openrouter.ai/google/model",
        "https://example.com/google/model",
        "https://openrouter.ai.evil/google/model",
        "https://user:secret@openrouter.ai/google/model",
        "https://openrouter.ai:443/google/model",
        "https://openrouter.ai/google/model?key=secret",
        "https://openrouter.ai/google/model#secret",
        "https://openrouter.ai/google/%6dodel",
        "https://openrouter.ai//google/model",
        "https://openrouter.ai/models//google/model",
        "https://openrouter.ai/google/model/extra",
        "https://openrouter.ai",
        "//openrouter.ai/google/model",
        "https://[invalid/google/model",
    ],
)
def test_reference_rejects_unsafe_or_malformed_values(reference: str) -> None:
    """Reference failures use one stable error without source content."""
    with pytest.raises(OpenRouterReferenceError) as captured:
        normalize_openrouter_model_reference(reference)
    assert str(captured.value) == "The OpenRouter model reference is invalid."
    assert "secret" not in str(captured.value)


def test_reference_property_style_round_trips_safe_ascii_parts() -> None:
    """Representative safe ASCII parts round-trip without normalization."""
    vendors = ("a", "openai", "meta-llama", "vendor_1", "Vendor.Name")
    models = ("m", "gpt-4o", "model_1", "v1.2", "model:free")
    for vendor in vendors:
        for model in models:
            identifier = f"{vendor}/{model}"
            assert normalize_openrouter_model_reference(identifier) == identifier


def test_reference_rejects_non_string_runtime_input_safely() -> None:
    """A dynamically typed caller still receives the stable public error."""
    with pytest.raises(OpenRouterReferenceError):
        normalize_openrouter_model_reference(3)  # type: ignore[arg-type]


def test_catalog_returns_complete_typed_source_facts() -> None:
    """One complete row maps only explicit facts into immutable typed values."""
    facts = parse_openrouter_catalog_snapshot(_snapshot(_row())).model(
        "https://openrouter.ai/models/google/gemma-4-31b-it"
    )

    assert facts.model_id == "google/gemma-4-31b-it"
    assert facts.canonical_source_name == "gemma-4-31b-it"
    assert facts.display_source_name == "Google: Gemma 4 31B Instruct"
    assert facts.input_modalities == tuple(OpenRouterInputModality)
    assert facts.output_modalities == tuple(OpenRouterOutputModality)
    assert facts.capabilities == tuple(OpenRouterCapability)
    assert facts.context_window_tokens == _TOP_CONTEXT_BOUND
    assert facts.maximum_output_tokens == _TOP_OUTPUT_BOUND
    assert facts.reasoning.supported is True
    assert facts.reasoning.mandatory is True
    assert facts.supported_constraints == tuple(OpenRouterConstraint)
    assert tuple(price.unit for price in facts.price_source_values) == tuple(
        OpenRouterPriceUnit
    )
    assert facts.price_source_values[0].amount == Decimal("0.00000013")
    assert facts.price_source_values[1].amount == Decimal("0.0000004")
    assert facts.price_source_values[2].amount == Decimal(0)
    assert facts.price_source_values[3].amount == Decimal("1e-8")
    assert facts.price_source_values[-1].source_field == "audio"
    assert {price.currency for price in facts.price_source_values} == {"USD"}
    with pytest.raises(FrozenInstanceError):
        facts.model_id = "changed/model"  # type: ignore[misc]


def test_catalog_preserves_missing_and_unknown_facts_as_uncertainty() -> None:
    """Missing facts stay absent, and unknown source labels are not invented."""
    catalog = parse_openrouter_catalog_snapshot(
        _snapshot(
            {
                "id": "new-vendor/new-model",
                "architecture": {
                    "input_modalities": ["text", "future_modality"],
                    "output_modalities": ["future_output"],
                },
                "supported_parameters": ["future_parameter"],
                "pricing": {"future_price": "0.5", "prompt": None},
            }
        )
    )
    facts = catalog.model("new-vendor/new-model")

    assert facts.display_source_name is None
    assert facts.input_modalities == (OpenRouterInputModality.TEXT,)
    assert facts.output_modalities == ()
    assert facts.capabilities == ()
    assert facts.context_window_tokens is None
    assert facts.maximum_output_tokens is None
    assert facts.reasoning.supported is False
    assert facts.reasoning.mandatory is None
    assert facts.supported_constraints == ()
    assert facts.price_source_values == ()


def test_catalog_maps_all_capability_aliases_and_reasoning_shapes() -> None:
    """Recognized aliases map once, and a reasoning object keeps uncertainty."""
    rows = (
        _row(
            id="aliases/model",
            supported_parameters=[
                "functions",
                "structured_outputs",
                "streaming",
                "include_reasoning",
            ],
            reasoning=None,
            pricing=None,
        ),
        _row(
            id="reasoning/object",
            supported_parameters=[],
            reasoning={},
            pricing=None,
        ),
        _row(
            id="reasoning/optional",
            supported_parameters=[],
            reasoning={"mandatory": False},
            pricing=None,
        ),
    )
    catalog = parse_openrouter_catalog_snapshot(_snapshot(*rows))

    assert catalog.model("aliases/model").capabilities == tuple(OpenRouterCapability)
    assert catalog.model("aliases/model").reasoning.mandatory is None
    assert catalog.model("reasoning/object").reasoning == catalog.model(
        "reasoning/object"
    ).reasoning.__class__(supported=True, mandatory=None)
    assert catalog.model("reasoning/optional").reasoning.mandatory is False


def test_catalog_uses_root_bounds_when_top_provider_has_no_value() -> None:
    """Root integer strings supply bounds only when preferred facts are absent."""
    facts = parse_openrouter_catalog_snapshot(
        _snapshot(
            _row(
                top_provider=None,
                context_length="4096",
                max_completion_tokens="1024",
                pricing=None,
            )
        )
    ).models[0]
    assert facts.context_window_tokens == _ROOT_CONTEXT_BOUND
    assert facts.maximum_output_tokens == _ROOT_OUTPUT_BOUND


def test_catalog_missing_model_uses_stable_safe_error() -> None:
    """An absent exact row does not put the requested identifier in the error."""
    catalog = parse_openrouter_catalog_snapshot(_snapshot(_row()))
    with pytest.raises(OpenRouterModelNotFoundError) as captured:
        catalog.model("secret-vendor/secret-model")
    assert str(captured.value) == "The OpenRouter model is not in the catalog snapshot."
    assert "secret" not in str(captured.value)


def test_catalog_rejects_duplicate_exact_rows() -> None:
    """Two rows with one exact model identifier make the snapshot ambiguous."""
    with pytest.raises(OpenRouterDuplicateModelError) as captured:
        parse_openrouter_catalog_snapshot(_snapshot(_row(), _row(name="Other")))
    assert str(captured.value) == (
        "The OpenRouter catalog has a duplicate model identifier."
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        b"",
        "",
        b"{",
        b"\xff",
        b"NaN",
        b'{"data":[],"data":[]}',
        _snapshot(**{"k" * 129: None}),
        _snapshot(**{"unsafe\u202e": None}),
        b"[]",
        b"{}",
        b'{"data":{}}',
        _snapshot(None),
        _snapshot({}),
        _snapshot({"id": 1}),
        _snapshot({"id": "bad"}),
        _snapshot({"id": "googl\u00e9/model"}),
    ],
)
def test_catalog_rejects_invalid_json_and_row_shapes(snapshot: bytes | str) -> None:
    """Invalid snapshot and row shapes use one safe catalog error."""
    with pytest.raises(OpenRouterCatalogError) as captured:
        parse_openrouter_catalog_snapshot(snapshot)
    assert str(captured.value) == "The OpenRouter catalog snapshot is invalid."


def test_catalog_accepts_exact_snapshot_byte_boundary() -> None:
    """A valid snapshot at the exact byte limit is accepted."""
    prefix = b'{"data":[]}'
    snapshot = prefix + (b" " * (_MAXIMUM_SNAPSHOT_BYTES - len(prefix)))
    assert parse_openrouter_catalog_snapshot(snapshot).models == ()


def test_catalog_rejects_snapshot_above_byte_boundary_and_surrogate_text() -> None:
    """One excess byte and a non-UTF-8 source string fail before parsing."""
    with pytest.raises(OpenRouterCatalogError):
        parse_openrouter_catalog_snapshot(b" " * (_MAXIMUM_SNAPSHOT_BYTES + 1))
    with pytest.raises(OpenRouterCatalogError):
        parse_openrouter_catalog_snapshot('{"data":[],"x":"\ud800"}')
    with pytest.raises(OpenRouterCatalogError):
        parse_openrouter_catalog_snapshot(3)  # type: ignore[arg-type]


def test_catalog_accepts_exact_collection_and_field_boundaries() -> None:
    """Collection, object, depth, text, token, and price maxima are inclusive."""
    rows = [{"id": f"vendor/model-{index}"} for index in range(10_000)]
    assert len(parse_openrouter_catalog_snapshot(_snapshot(*rows)).models) == len(rows)

    object_at_limit: dict[str, object] = {"id": "vendor/object-limit"}
    object_at_limit.update({f"field_{index}": index for index in range(127)})
    parse_openrouter_catalog_snapshot(_snapshot(object_at_limit))

    nested: object = None
    for _index in range(6):
        nested = [nested]
    parse_openrouter_catalog_snapshot(_snapshot(_row(extra=nested, pricing=None)))
    parse_openrouter_catalog_snapshot(
        _snapshot(_row(extra=[None] * 10_000, pricing=None))
    )

    modalities = [f"modality_{index}" for index in range(16)]
    parameters = [f"parameter_{index}" for index in range(128)]
    facts = parse_openrouter_catalog_snapshot(
        _snapshot(
            _row(
                name="n" * 500,
                architecture={"input_modalities": modalities},
                supported_parameters=parameters,
                pricing={
                    "prompt": "1000000000000",
                    "completion": "1e-100",
                },
            )
        )
    ).models[0]
    assert facts.display_source_name == "n" * 500
    assert facts.input_modalities == ()
    assert facts.supported_constraints == ()
    assert tuple(price.amount for price in facts.price_source_values) == (
        Decimal(1000000000000),
        Decimal("1e-100"),
    )


def test_catalog_rejects_unsafe_container_bounds() -> None:
    """Row count, list size, member count, depth, nodes, and text are bounded."""
    invalid_values: list[object] = [
        {"data": [{}] * 10_001},
        {"data": [_row(extra=[None] * 10_001)]},
        {"data": [_row(extra={f"k{index}": index for index in range(129)})]},
        {"data": [_row(extra=[[[[[[[[[None]]]]]]]]])]},
        {
            "data": [
                {"id": f"vendor/model-{index}", "extra": [0] * 20}
                for index in range(10_000)
            ]
        },
        {"data": [_row(extra="x" * 16_385)]},
        {"data": [_row(extra=10**40)]},
        {"data": [_row(extra=1e101)]},
        {"data": [_row(extra=float("inf"))]},
    ]
    for value in invalid_values:
        with pytest.raises(OpenRouterCatalogError):
            parse_openrouter_catalog_snapshot(json.dumps(value).encode())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", 3),
        ("name", " "),
        ("name", "x" * 501),
        ("name", "unsafe\u202e"),
        ("architecture", []),
        ("top_provider", []),
        ("reasoning", []),
        ("reasoning", {"mandatory": "yes"}),
        ("pricing", []),
        ("context_length", True),
        ("context_length", 0),
        ("context_length", -1),
        ("context_length", 2_147_483_648),
        ("context_length", "1.5"),
        ("context_length", 1.5),
        ("max_completion_tokens", False),
    ],
)
def test_catalog_rejects_malformed_or_oversized_model_fields(
    field: str, value: object
) -> None:
    """Malformed relevant scalar and object fields fail closed."""
    row = _row(top_provider=None, pricing=None)
    row[field] = value
    with pytest.raises(OpenRouterCatalogError):
        parse_openrouter_catalog_snapshot(_snapshot(row))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("input_modalities", "text"),
        ("input_modalities", ["text"] * 17),
        ("input_modalities", [1]),
        ("input_modalities", ["x" * 65]),
        ("input_modalities", ["Uppercase"]),
        ("input_modalities", ["text", "text"]),
        ("output_modalities", "text"),
    ],
)
def test_catalog_rejects_malformed_modality_lists(field: str, value: object) -> None:
    """Modality arrays have closed shape, item, count, and duplicate bounds."""
    architecture = {field: value}
    with pytest.raises(OpenRouterCatalogError):
        parse_openrouter_catalog_snapshot(
            _snapshot(_row(architecture=architecture, pricing=None))
        )


@pytest.mark.parametrize(
    "value",
    [
        "temperature",
        ["temperature"] * 129,
        [1],
        ["x" * 65],
        ["Uppercase"],
        ["temperature", "temperature"],
    ],
)
def test_catalog_rejects_malformed_supported_parameter_lists(value: object) -> None:
    """Supported-parameter arrays use the same bounded source token rules."""
    with pytest.raises(OpenRouterCatalogError):
        parse_openrouter_catalog_snapshot(
            _snapshot(_row(supported_parameters=value, pricing=None))
        )


@pytest.mark.parametrize(
    "value",
    [
        True,
        -1,
        "-1",
        "01",
        "not-a-price",
        "1000000000001",
        "0." + ("1" * 101),
        "1e101",
    ],
)
def test_catalog_rejects_invalid_price_source_values(value: object) -> None:
    """Price facts must be finite, bounded, non-negative decimal values."""
    with pytest.raises(OpenRouterCatalogError) as captured:
        parse_openrouter_catalog_snapshot(_snapshot(_row(pricing={"prompt": value})))
    assert "not-a-price" not in str(captured.value)


def test_catalog_accepts_empty_optional_lists_and_exact_integer_bound() -> None:
    """Empty arrays and the inclusive integer maximum remain valid facts."""
    facts = parse_openrouter_catalog_snapshot(
        _snapshot(
            _row(
                name=None,
                architecture=None,
                top_provider={"context_length": 2_147_483_647},
                supported_parameters=None,
                reasoning=None,
                pricing=None,
            )
        )
    ).models[0]
    assert facts.context_window_tokens == _MAXIMUM_INTEGER_BOUND
    assert facts.input_modalities == ()
    assert facts.output_modalities == ()
