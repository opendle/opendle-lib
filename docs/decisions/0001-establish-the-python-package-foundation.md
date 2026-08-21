# 0001: Establish the Python package foundation

- Status: Accepted
- Date: 2026-08-21

## Context

OpenDLE projects repeat backend Python behavior. A shared package needs a
stable identity and a strict foundation before code moves into it.

The named consumers use Python 3.13 and Python 3.14 today. The user selected
Python 3.14 only and accepted that a consumer must upgrade before it can use
the package.

## Decision

- Use `opendle-lib` as the distribution name.
- Use `opendle` as the import namespace.
- Support Python 3.14 only.
- Use the `src/` package layout and Hatchling.
- Use `uv` for all supported Python operations.
- Start at version `0.0.0` with no runtime dependencies.
- Use FSL-1.1-ALv2.
- Keep shared React code in `opendle-ui`.
- Make each consumer depend directly on the Git `main` branch.
- Use strict library and consumer type checks as the static compatibility
  gate.

## Consequences

The package can grow through cohesive modules without a second top-level
namespace. Strict package and quality checks apply before behavior enters the
library. A project on Python 3.13 cannot install the package until it upgrades.
Consumer lock files record a resolved Git commit, but each applicable shared
change refreshes them from `main`.
