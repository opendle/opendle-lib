# Architecture

## Purpose

OpenDLE Lib contains reusable Python backend behavior and common backend
contracts. It reduces duplicate implementation and makes tested improvements
available to all OpenDLE projects.

The package does not own product behavior. Each consumer keeps its routes,
domain policy, application configuration, data ownership, and service
composition.

## Package shape

The distribution name is `opendle-lib`. The Python namespace is `opendle`.
Source code uses the `src/` layout so a test cannot import an uninstalled copy
by accident.

Use a small top-level module for each cohesive capability. Public imports must
be deliberate. A module can use a private `_internal` package, but a consumer
must not import it.

Framework-neutral code is the default. A framework integration must be in an
isolated module. If it adds a runtime dependency, use an optional extra unless
the dependency is necessary for all consumers.

## Extraction rule

Shared syntax is not sufficient evidence for a shared abstraction. Extract
code only when the consumers also need the same behavior, invariants, and
change lifecycle. Prefer a clear local implementation when the domains can
diverge.

When an extraction starts, preserve behavior with tests in the source project.
Move or reproduce the relevant tests here. Update all affected consumers and
run their applicable checks before delivery.

## Compatibility

The initial version is `0.0.0`, and the first development releases will remain
below version 1.0. Even before version 1.0, an agent must identify breaking
changes, record the reason, and update all known consumers in the same work.

The supported runtime is Python 3.14. The package ships a `py.typed` marker.
Public code must pass strict static analysis.

Each consumer depends directly on the Git `main` branch. Its lock records the
resolved commit for reproducibility. A shared-library change refreshes each
affected consumer lock and runs its strict type check. This check detects a
changed signature, return type, or public type before runtime.

## Quality

The repository uses Ruff, Pyright, Mypy, pytest, coverage, pip-audit, Hatchling,
and Twine. The lock file and exact development versions make local and CI
checks reproducible. The dynamic `uv` dependency-age gate prevents selection
of releases that are less than 14 complete days old.

Pyright package verification rejects an unknown or ambiguous public type. It
also rejects a public function or class without documentation. This package
check and consumer type checks form the static compatibility gate.

CI builds both the source archive and wheel. It validates package metadata and
runs the complete repository check.
