"""Verify the completeness of the public package type contract."""

import json
import shutil
import subprocess
import sys
from typing import TypedDict, cast


class SymbolCounts(TypedDict):
    """Counts for one public symbol category."""

    withKnownType: int
    withAmbiguousType: int
    withUnknownType: int


class TypeCompleteness(TypedDict):
    """Required fields from the Pyright type-completeness report."""

    exportedSymbolCounts: SymbolCounts
    missingFunctionDocStringCount: int
    missingClassDocStringCount: int
    completenessScore: float


class TypeReport(TypedDict):
    """Required top-level Pyright report fields."""

    summary: ReportSummary
    typeCompleteness: TypeCompleteness


class ReportSummary(TypedDict):
    """Required Pyright diagnostic summary fields."""

    errorCount: int


def validate_report(report: TypeReport) -> None:
    """Reject an incomplete public type or documentation contract."""
    if report["summary"]["errorCount"]:
        msg = "Pyright found an error in the public package."
        raise ValueError(msg)

    completeness = report["typeCompleteness"]
    counts = completeness["exportedSymbolCounts"]

    if counts["withAmbiguousType"] or counts["withUnknownType"]:
        msg = "The public package contains an ambiguous or unknown type."
        raise ValueError(msg)
    if completeness["missingFunctionDocStringCount"]:
        msg = "A public function does not have a docstring."
        raise ValueError(msg)
    if completeness["missingClassDocStringCount"]:
        msg = "A public class does not have a docstring."
        raise ValueError(msg)
    if counts["withKnownType"] and completeness["completenessScore"] != 1:
        msg = "The public package type-completeness score is not 100%."
        raise ValueError(msg)


def main() -> None:
    """Run Pyright package verification and validate its JSON report."""
    pyright_path = shutil.which("pyright")
    if pyright_path is None:
        msg = "Pyright is not available in the locked environment."
        raise SystemExit(msg)

    completed = subprocess.run(  # noqa: S603 - shutil resolves the executable.
        (
            pyright_path,
            "--verifytypes",
            "opendle",
            "--ignoreexternal",
            "--outputjson",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    report = cast("TypeReport", json.loads(completed.stdout))
    validate_report(report)
    sys.stdout.write("Public package types are complete.\n")


if __name__ == "__main__":
    main()
