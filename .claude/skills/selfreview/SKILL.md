---
name: selfreview
description: Review recent opendle-lib package, API, tool, documentation, or consumer changes with a skeptical pass. Use after non-trivial edits and before commit, push, or delivery.
---

# Self-review

Review against the user request, `AGENTS.md`, the architecture, accepted
decisions, and the complete current diff.

## Gather evidence

1. Run `git status --short --branch` in this repository and each changed
   consumer.
2. Read working and staged diffs. Preserve changes that you do not own.
3. Use LSP references and diagnostics for affected public symbols when they
   apply.

## Review the change

Check for:

- behavior or structure that the user did not request;
- a public import, type, exception, default, or behavior change without a
  consumer migration;
- copied code whose invariants or change reasons are not truly shared;
- product-specific policy or a framework dependency in the base package;
- missing success, error, edge, and compatibility tests;
- incomplete type information, documentation, or `py.typed` packaging;
- undeclared or unnecessary dependencies and releases newer than the cutoff;
- package metadata, source archive, wheel, or clean-install defects;
- security, secret, private-data, performance, and concurrency risks;
- stale documentation, skills, checks, or dependent project code.
- a consumer that does not use the direct Git `main` dependency or whose lock
  was not refreshed after an applicable library change;
- a completed repository with owned changes that were not committed and
  pushed to `main` without a user exception;

Name each real finding as `BUG`, `MISSING`, `RISKY`, or `NITPICK`. Give the
file, trigger, effect, and direct fix. Do not invent findings.

Fix each `BUG`, `MISSING`, and `RISKY` item that is in scope. Fix a `NITPICK`
only when the edit is small and safe. Run focused checks after the final related
fix. Review the complete diff again, and then run
`./scripts/check-repository.sh` once for the final state.
