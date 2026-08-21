# OpenDLE Lib

OpenDLE Lib is the shared Python backend library for OpenDLE projects. It will
reduce duplicate code in `fj2`, `crewday`, `llmrouter`, `ontology`, `xbot`, and
future projects. `opendle-ui` has the same role for shared React frontend code.

This repository is public and source-available under FSL-1.1-ALv2. Each
version changes to Apache-2.0 on its license schedule. The current FSL version
is not an open-source release.

This repository contains the package and development scaffold only. It does
not contain reusable library behavior yet.

Do not commit secrets, credentials, tokens, private data, internal-only
configuration, or unpublished third-party material.

## Package contract

- Distribution name: `opendle-lib`
- Import namespace: `opendle`
- Python: 3.14 only
- Runtime dependencies: none at this stage
- License: FSL-1.1-ALv2

The package starts at version `0.0.0`. Use pre-1.0 releases while the public
API can still change. Record a breaking public API change and update all known
consumers in the same work.

A project that imports `opendle` must depend directly on the latest `main`
branch:

```toml
dependencies = [
  "opendle-lib @ git+https://github.com/opendle/opendle-lib.git@main",
]
```

The consumer lock records the resolved Git commit. Refresh that lock after an
applicable library change.

## Development

Install the exact locked environment:

```bash
./scripts/dependency-age-gate.sh sync
```

Add or remove a dependency through the age gate:

```bash
./scripts/dependency-age-gate.sh add PACKAGE
./scripts/dependency-age-gate.sh remove PACKAGE
```

Run all repository checks:

```bash
./scripts/check-repository.sh
```

The checks use strict Pyright and Mypy analysis. They also verify that each
public library symbol has a complete type and documentation contract. A
consumer must run its strict type check after it refreshes the library lock.

Use `uv` for dependency changes, commands, builds, and tools. Do not use
`pip`, `pipx`, Poetry, or a manually created virtual environment. Use
`uv run --frozen` for a command that uses the locked project environment.

## Repository map

- `src/opendle/`: public Python package
- `tests/`: package and behavior tests
- `docs/architecture.md`: package boundaries and evolution rules
- `docs/shared-library-roadmap.md`: planned reusable backend capabilities
- `docs/decisions/`: accepted architecture decisions
- `scripts/`: deterministic repository checks
- `.claude/skills/`: reusable agent workflows

Read `AGENTS.md` before a change.
