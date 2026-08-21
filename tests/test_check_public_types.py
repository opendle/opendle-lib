"""Tests for public type-contract validation."""

import pytest
from scripts.check_public_types import TypeReport, validate_report


def _report(
    *,
    counts: tuple[int, int, int] = (0, 0, 0),
    missing_docs: tuple[int, int] = (0, 0),
    score: float = 0,
    errors: int = 0,
) -> TypeReport:
    known, ambiguous, unknown = counts
    functions_without_docs, classes_without_docs = missing_docs
    return {
        "summary": {"errorCount": errors},
        "typeCompleteness": {
            "exportedSymbolCounts": {
                "withKnownType": known,
                "withAmbiguousType": ambiguous,
                "withUnknownType": unknown,
            },
            "missingFunctionDocStringCount": functions_without_docs,
            "missingClassDocStringCount": classes_without_docs,
            "completenessScore": score,
        },
    }


def test_empty_package_is_valid_before_implementation() -> None:
    """The package can have no public symbols during initialization."""
    validate_report(_report())


def test_complete_public_types_are_valid() -> None:
    """A fully known and documented public contract must pass."""
    validate_report(_report(counts=(2, 0, 0), score=1))


@pytest.mark.parametrize(
    ("report", "message"),
    [
        (_report(counts=(0, 0, 1)), "ambiguous or unknown"),
        (_report(counts=(0, 1, 0)), "ambiguous or unknown"),
        (_report(missing_docs=(1, 0)), "function does not have"),
        (_report(missing_docs=(0, 1)), "class does not have"),
        (_report(counts=(1, 0, 0), score=0.5), "score is not 100%"),
        (_report(errors=1), "Pyright found an error"),
    ],
)
def test_incomplete_public_contract_is_rejected(
    report: TypeReport,
    message: str,
) -> None:
    """An incomplete public type or documentation contract must fail."""
    with pytest.raises(ValueError, match=message):
        validate_report(report)
