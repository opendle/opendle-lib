# Agent instructions

These instructions apply to the complete repository.

## Mission

Build and maintain the shared Python backend library for OpenDLE projects.
Move reusable backend code here so `fj2`, `crewday`, `llmrouter`, `ontology`,
`xbot`, and future projects do not maintain copies of the same behavior.
Use `opendle-ui` for shared React frontend code.

This is a public, source-available repository under FSL-1.1-ALv2. It changes
to Apache-2.0 on the license schedule. Do not describe its current FSL version
as open source.

Never commit secrets, credentials, tokens, private data, internal-only
configuration, or unpublished third-party material. Do not add product rules
or runtime data. Keep the library independent of one host application.

## Working rules

1. Read this file, `README.md`, and the relevant architecture or decision
   documents before a change.
2. Inspect `git status --short --branch`. Preserve work that you do not own.
3. Use LSP tools for symbols, definitions, references, callers, diagnostics,
   renames, formatting, and safe edit previews when they apply. Use `rg`, Git,
   and shell tools for broad searches and commands.
4. Before a behavior change, use LSP to inspect the affected public symbols
   and callers. After the edit, run LSP diagnostics and repository checks.
5. Use ASD-STE100 Simplified Technical English in reports, documentation,
   pull requests, comments, and agent-created content.
6. Use `uv` for all supported Python environment, dependency, lock, command,
   build, and tool operations. Do not use `pip`, `pipx`, Poetry, or a manually
   created virtual environment. Use `uv run --frozen` for commands in the
   locked project environment.
7. Keep the runtime dependency set small. Do not add a dependency to avoid a
   small amount of clear, maintainable code.
8. Run the `selfreview` skill after non-trivial edits. Review all owned changes
   again, and then run `./scripts/check-repository.sh`.
9. After a task is complete, commit all owned changes and push `main` to
   `origin`, unless the user gives a different instruction. Do not report
   completion until the push succeeds.

## Critical review and questions

Be critical of each request. State a concern before work when the likely blast
radius, security risk, maintenance cost, coupling, or consumer change is larger
than the request suggests. Give a safer or simpler recommendation.

Make low-level, reversible technical choices without interruption. Stop and
use the question tool for a high-level choice that changes the public API,
supported Python versions, license, dependency policy, release policy,
security posture, or visible consumer behavior. Give the context, one clear
recommendation, and the benefits and costs of each option.

## Shared library boundary

- Add code here when it has a credible use in more than one OpenDLE backend or
  when it defines a common backend contract.
- Do not create a generic abstraction only because two code blocks look
  similar. Confirm that their behavior and change reasons are also shared.
- Prefer small, cohesive modules with explicit public exports. Keep internal
  implementation under an `_internal` package when it must not be public.
- Do not import a consumer application from this package.
- Do not make Django, FastAPI, a data store, or another framework a base
  dependency. Put framework integrations in optional, isolated modules and
  extras when implementation starts.
- Use type hints for all functions. Document each public symbol. Test public
  behavior and error behavior.
- Treat an import path, function signature, exception, type, and documented
  behavior as a public compatibility contract after release.

## Consumer responsibility

Assume that the named sibling repositories are the only consumers unless the
user gives other information. An agent that changes this library also owns the
required changes in each affected consumer.

Before a public API change, search `../fj2`, `../crewday`, `../llmrouter`,
`../ontology`, and `../xbot` for consumers. Use the `shared-library-change`
skill. Update and verify each affected repository in the same task. Do not
leave compatibility work for another agent or silently break a consumer.

A consumer that imports `opendle` must depend directly on the `main` branch:

```toml
dependencies = [
  "opendle-lib @ git+https://github.com/opendle/opendle-lib.git@main",
]
```

Do not vendor, copy, or use a stale local path for shared code. Refresh the
consumer lock after each applicable library change so it resolves the current
`main` commit. The lock can record the resolved commit for reproducible builds.

Run strict static type checks in this repository and each affected consumer.
The library check must verify type completeness for the public `opendle`
package. Consumer checks must include each module that imports `opendle`.

## Dependencies and releases

- Support Python 3.14 only. A change to this policy needs a user decision.
- Use exact versions for development dependencies and build tools.
- Do not select a dependency release that is less than 14 complete days old.
- Commit `uv.lock`. Use `scripts/dependency-age-gate.sh` for `uv add`, `uv
  remove`, `uv lock`, `uv sync`, and lock checks.
- Keep the package below version 1.0 until the user accepts a stable API.
- Use focused commits. Never force-push or rewrite shared history.
- Do not publish a package release unless the user explicitly asks for it.
- Before a push, fetch `origin` and preserve all valid concurrent changes.
  Push only `main` unless the user asks for a different branch.

## Structure

- `src/opendle/`: public package and future internal modules
- `tests/`: tests that follow the package structure
- `docs/architecture.md`: durable package boundaries
- `docs/decisions/`: accepted architecture decisions
- `scripts/`: deterministic development and quality tools
- `.claude/skills/`: reusable workflows, exposed through `.agents/skills` and
  `.codex/skills`

Add a nested `AGENTS.md` only when a directory needs different rules. State
only the differences.

Keep this file limited to durable policy. Put repeatable workflows in a skill
and deterministic operations in `scripts/`. Improve these instructions, skills,
and checks when repeated friction shows a clear need. Do not weaken a check to
make a task pass.
