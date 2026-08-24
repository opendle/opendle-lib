# OpenDLE Lib

OpenDLE Lib is the shared Python backend library for OpenDLE projects. It will
reduce duplicate code in `fj2`, `crewday`, `llmrouter`, `ontology`, `xbot`, and
future projects. `opendle-ui` has the same role for shared React frontend code.

This repository is public and source-available under FSL-1.1-ALv2. Each
version changes to Apache-2.0 on its license schedule. The current FSL version
is not an open-source release.

The package supplies dependency-free OpenID Connect authorization request
primitives. It validates canonical 256-bit Base64URL tokens, creates RFC 7636
S256 challenges, and builds bounded authorization-code URLs without letting an
endpoint replace request-owned parameters. A caller can omit refresh-token
access when it needs only an OpenID identity token.

The package also supplies the official dependency-free Python Router client,
provider-neutral Router model contract values, and a stateless multi-turn
harness. One client binds one private backend service key to the complete
native service-key API surface. The harness uses caller-owned conversation
state, tools, storage callbacks, and the client model-caller port. It supports
exact sticky routes, bounded pruning, and pinned model compaction. It does not
execute in Router and does not own durable storage. See `docs/router-sdk.md`
and `docs/router-harness.md` for the public APIs.

The package supplies a dependency-free parser for public OpenRouter model
catalog snapshots. It accepts strict model identifiers and supported HTTPS
`openrouter.ai` model URLs. It validates fixed input, container, field, and
number bounds and returns immutable typed source facts. The caller owns the
catalog HTTP request, deadline, authority, cache, and product mapping.

The package supplies a dependency-free dynamic Ontology client. One client
binds one service API name and one backend-only service key. It has one method
for each operation that accepts a service key in the accepted Ontology OpenAPI
contract. It supports exact JSON and safe YAML ontology requests, bounded
cursor pages, value fingerprints, managed file transfer, and stable typed HTTP
errors. The key goes only in the HTTP `Authorization` header. Client and error
representations do not contain it. Successful response bodies have a finite
caller-configurable byte bound. The default is 16 MiB, which accepts the full
10 MiB managed-file limit. A caller that needs a larger bounded JSON response
can set `maximum_success_response_bytes` to a larger positive integer.
File uploads encode the exact UTF-8 file name as canonical unpadded Base64URL
after the ASCII `u8.` marker. The client does not normalize Unicode.

`OntologyAgentHelpers` binds the client to one explicit workspace. It can
return deterministic compact YAML for object, link, query, graph, and
changed-since results. Timestamps and expanded bags are opt-in. Each complete
YAML result has a caller-selected byte bound. These helpers are normal SDK
calls. They do not define an agent protocol or generate ontology-specific
types.

```python
from opendle import OntologyAgentHelpers, OntologyClient

client = OntologyClient(
    base_url=configuration.ontology_url,
    service_api_name=configuration.ontology_service,
    service_key=configuration.ontology_service_key,
    maximum_success_response_bytes=16_777_216,
)
helpers = OntologyAgentHelpers(client, workspace_api_name="conversation-42")
object_yaml = helpers.get_object_yaml("message-1")
```

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
- `docs/router-harness.md`: Router contract and harness public API
- `docs/router-sdk.md`: official native Router Python client API
- `docs/shared-library-roadmap.md`: planned reusable backend capabilities
- `docs/decisions/`: accepted architecture decisions
- `scripts/`: deterministic repository checks
- `.claude/skills/`: reusable agent workflows

Read `AGENTS.md` before a change.
