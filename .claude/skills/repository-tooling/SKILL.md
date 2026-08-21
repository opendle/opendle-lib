---
name: repository-tooling
description: Build or improve durable repository tools, package checks, test fixtures, quality gates, and concise agent guidance. Use when repeated manual work, an unreliable check, or repository friction slows delivery or lowers confidence.
---

# Improve repository tooling

Turn repeatable friction into a repository capability. Keep one-time diagnosis
inside the current task.

Use the smallest suitable mechanism:

1. Improve an existing deterministic script or check.
2. Add a regression test or quality gate for a detectable failure.
3. Add or update a skill for a workflow that needs judgment.
4. Add a nested `AGENTS.md` only for directory-specific rules.

Prefer an existing tool over a parallel tool. Make scripts safe to repeat,
non-interactive, and clear on failure. Add a check to
`scripts/check-repository.sh` when it must protect all future changes.

Use `uv` for Python environments, dependencies, locks, commands, builds, and
tools. Use `scripts/dependency-age-gate.sh` for dependency and lock changes.
Use `uv run --frozen` for project commands. Preserve exact development
versions and the 14-day minimum age.

Test a new tool for success, expected failure, and unsafe input when those
paths apply. Run the focused check, then `./scripts/check-repository.sh`.
