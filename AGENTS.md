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

## Shared OpenDLE boundary

This repository is the home for reusable backend behavior and common backend
contracts. Before adding backend code in a consumer, check this repository.
If the behavior has credible use in more than one OpenDLE project, add it
here, add focused tests, and update the consumer to use the direct Git
`main` dependency. Keep product rules, routes, data models, and framework
adapters in the consumer. Do not copy shared code between projects.

Reusable React components, tokens, and interaction patterns belong in
`../opendle-ui`. Host projects must check that repository first and use its
current `main` dependency. Keep only product-specific UI in the host project.

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

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:970c3bf2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
