---
name: shared-library-change
description: Extract, add, or change shared Python backend behavior and update every affected OpenDLE consumer. Use for public APIs, compatibility changes, shared-code extraction, and consumer migrations in opendle-lib.
---

# Change the shared library

Keep the library and all known consumers compatible in one task.

## Establish the boundary

Inspect the source implementations and tests in `../fj2`, `../crewday`,
`../llmrouter`, `../ontology`, and `../xbot`. Confirm that the candidate code
has shared behavior, invariants, and change reasons. Do not extract code only
because its syntax is similar.

Define the intended public import, types, errors, dependency effect, and
supported consumer versions. Use the question tool before a high-level choice
that changes the public contract or consumer behavior. Give a recommendation
and the benefits and costs of each option.

## Make the change

Preserve source behavior with focused tests before or during extraction. Keep
framework-neutral behavior in a normal module. Isolate a framework integration
and its optional dependency. Do not import consumer code from `opendle`.

Use explicit public exports. Treat import paths, signatures, types, exceptions,
and documented behavior as compatibility contracts.

Run `scripts/check_public_types.py` before consumer migration. Do not expose an
unknown or ambiguous public type.

## Migrate and verify consumers

Search all known consumers again for copied implementations and API use.
Update each affected repository in the same work. Remove a replaced copy only
after its consumer uses the shared package and its tests cover the shared path.

Each consumer must use this direct dependency:

```toml
"opendle-lib @ git+https://github.com/opendle/opendle-lib.git@main"
```

Push the validated library commit to `main`, refresh each affected consumer
lock from `main`, and run the consumer static type check. A lock can record the
resolved commit. Do not replace the Git dependency with a copied package or a
permanent local path.

Run LSP diagnostics and focused tests in this repository and each changed
consumer. Then run each repository's complete required check. Report any
consumer that could not be verified. Commit and push each completed repository
to `main` unless the user gives a different instruction. Do not publish a
package release unless the user explicitly asks for it.
