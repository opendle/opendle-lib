# Shared library roadmap

## Purpose

This document lists backend capabilities that may belong in `opendle-lib`.
It covers the planned consumers:

- `fj2`
- `crewday`
- `llmrouter`
- `ontology`
- `xbot`
- future OpenDLE backend projects

The list is a planning proposal. It is not a promise to move every item.
The package must contain only behavior that has the same rules, invariants,
and change reason in more than one consumer.

The current package is a scaffold. No item in this document is implemented in
`opendle-lib` yet.

## Consumer status

No named consumer currently declares `opendle-lib` or imports `opendle` in the
local workspace.

- `fj2`, `crewday`, `llmrouter`, and `ontology` have backend code that can
  provide extraction evidence.
- `xbot` has product and contract documents but no Python backend yet. Its
  first backend should use stable shared primitives where the boundary is
  already clear.
- `llmrouter` currently targets Python 3.13, while `opendle-lib` supports
  Python 3.14 only. It needs a runtime upgrade before it can adopt the
  package.

The roadmap therefore contains both extraction work and future implementation
work. A future feature is not a reason to add a speculative public API now.

## Evidence and confidence

The review used source files, tests, accepted decisions, and specifications in
the five named repositories. Some repositories are early designs. A planned
feature is evidence of a likely need, but it is not evidence of a working
implementation.

For `fj2`, the `specs/` directory can describe intended behavior that differs
from current source. Use `apps/` and tests as the final evidence before an
extraction.

| Label | Meaning |
| --- | --- |
| Strong | The same behavior is already present, or is accepted, in at least two consumers. |
| Possible | The behavior is likely to repeat, but the common contract needs a second implementation or a design review. |
| Local | The behavior is product policy or a framework adapter. Keep it in the consumer unless later evidence changes the boundary. |

## Candidate summary

| Capability | `fj2` | `crewday` | `llmrouter` | `ontology` | `xbot` | Confidence | Recommended library boundary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Opaque IDs, time, canonical values | E | E | E | E | P | Strong | Framework-neutral value types and helpers. |
| Request, tenant, and actor context | E | E | E | E | P | Strong | Async-safe context values and scope carriers. |
| Events, relays, and adapter ports | P | E | E | E | P | Possible | Transport-neutral event and side-effect protocols. |
| API errors, request identity, pagination | E | E | E | E | P | Strong | Transport-neutral contract types and optional HTTP adapters. |
| Idempotency and request fingerprints | E | E | E | E | P | Strong | Key validation, canonical hashing, and storage protocols. |
| Secret handling and cryptographic primitives | E | E | E | E | P | Strong | Small primitives and ports. Keep key custody local. |
| Configuration and endpoint safety | E | E | E | E | P | Possible | Secret-safe settings and transport checks. |
| Human authentication | E | E | E/P | E/P | P | Possible | Protocol helpers only. Keep account and permission data local. |
| Service credentials and short-lived tokens | E | E | E | E | P | Strong | Token claims, exchange rules, and safe client lifecycle. |
| Scope and authorization helpers | E | E | E | E | P | Possible | Scope value types and policy hooks. Do not add shared domain roles. |
| Rate limiting and abuse controls | E | E | P | P | P | Possible | Algorithms and decision values, not product thresholds. |
| Object storage and attachment safety | E | E | E | E | P | Possible | Metadata, validation, and storage ports. Keep vendor adapters local. |
| Audit events and redaction | E | E | E | E | P | Strong | Event shape, actor references, redaction, and sink ports. |
| Privacy, retention, export, deletion | E | E | E | E | P | Possible | Lifecycle types and operation protocols. Keep deadlines and policy local. |
| Retry, leases, worker state, circuits | E | E | E | P | P | Strong | Framework-neutral reliability primitives. |
| Health, readiness, metrics, tracing | E | E | E | E | P | Strong | Common models and instrumentation hooks. |
| SSRF-safe outbound requests | E | E | P | P | P | Possible | URL, DNS, redirect, scheme, timeout, and response-size safety. |
| Streaming and reconnect control | E | E | E | P | P | Possible | Stream identity, admission, resume, and terminal states. |
| Webhooks and signed callbacks | E | E | E | E | P | Possible | Signing, delivery state, retry, and replay protection. |
| Notifications and external messages | E | E | P | P | P | Possible | Channel ports only, after a common contract exists. |
| LLM provider and model types | E | E | E | P | E/P | Possible | Small provider-neutral types only. Routing stays in `llmrouter`. |
| LLM response parsing and error classification | E | E | E | P | P | Possible | JSON extraction, typed provider errors, and retry classification. |
| Financial and usage value types | E | E | E | P | P | Possible | Currency, minor units, usage, and cost values only. |
| Generated clients and contract checks | P | P | E | E | P | Possible | Shared contract tooling; service clients stay with each service. |
| Database models and migrations | E | E | E | E | P | Local | Keep ORM models and schema ownership in each consumer. |
| Product domain behavior | E | E | E | E | P | Local | Do not move articles, bookings, ontology, routing, or agent policy. |

`E` means an existing implementation or detailed specification. `P` means a
planned or future feature. A cell does not mean that the project currently
depends on `opendle-lib`.

## Recommended first implementation group

The first library work should be small and framework-neutral. It should not
start with a Django, FastAPI, SQLAlchemy, Celery, or storage-vendor package.

### 1. Common value types

Implement and test:

- opaque identifiers;
- UUIDv7 creation and validation, where the request lifecycle needs it;
- UTC time helpers and an injectable clock protocol;
- bounded text, byte size, and collection limits;
- normalized service, workspace, actor, and operation references;
- typed success, conflict, validation, and retryable outcomes.

Evidence:

- `fj2` uses identifiers, clock-sensitive jobs, and bounded API values across
  `apps/core`, `apps/api`, and migration code.
- `crewday` has `app/util/clock.py`, ULID helpers, bounded API types, and
  workspace-scoped records.
- `llmrouter` requires client-generated UUIDv7 request identity in decision
  0031 and uses opaque service and workspace scope.
- `ontology` uses opaque identifiers, commit tokens, service scope, and
  workspace scope in its versioned API decision.
- `xbot` plans opaque actor references and workspace-scoped operations.

Do not add a shared database model for these values. Consumers must select
their own persistence types.

### 2. Contract-safe request helpers

Implement and test:

- canonical JSON encoding for request fingerprints;
- SHA-256 request fingerprints that exclude credentials and transport data;
- request identity and correlation value types;
- stable pagination inputs and outputs;
- machine-readable problem details;
- explicit conflict and retryable error classes;
- fixed limit validation;
- safe redaction of authorization headers, tokens, and secret values.

Evidence:

- `crewday/app/api/middleware/idempotency.py`,
  `app/api/middleware/request_id.py`, `app/api/pagination.py`, and
  `app/api/errors.py` implement these concerns in a FastAPI application.
- `llmrouter/docs/api/request-fingerprint.md`, the request identity decision,
  and `packages/backend-role` define the same concerns for service calls.
- `ontology` requires versioned HTTP JSON, stable pagination, idempotency,
  fixed limits, and machine-readable errors in
  `docs/decisions/0002-versioned-http-json-contract.md`.
- `fj2/apps/api/exceptions.py`, `authentication.py`, and `throttling.py`
  provide related API boundary behavior.

The library should provide values and algorithms. A consumer adapter should
map them to Django REST Framework, FastAPI, or another transport.

### 3. Idempotency and replay protection

Implement and test:

- idempotency-key syntax and size limits;
- binding of a key to an operation, caller, scope, and request fingerprint;
- replay of the first successful result;
- conflict on a changed request with the same key;
- retention and cleanup hooks;
- one-use and expiry checks for capabilities and callbacks.

Evidence:

- `crewday/app/api/middleware/idempotency.py` protects mutations.
- `llmrouter` uses an atomic request binding and fingerprint in decision 0031.
- `ontology` requires idempotency keys for mutations, workspace lifecycle,
  and file operations.
- `fj2` migration and queue code has explicit idempotent re-run behavior.
- `xbot` requires duplicate and uncertain-result protection for external
  writes in `docs/specs/06-security-privacy-and-quality.md`.

Do not put a database-backed idempotency table in the base package. Provide a
protocol and a reference in-memory implementation for tests. Each consumer
must choose its transaction and retention boundary.

### Common context, events, and adapter ports

These are possible shared-kernel parts. They are useful only when their
contracts stay independent of one ORM and one web framework.

Possible library work:

- immutable request, tenant, service, workspace, and actor context;
- async-safe context carrier with explicit enter and reset behavior;
- typed domain event base and duplicate-name validation;
- ordered in-process event bus;
- optional relay protocol for durable delivery;
- side-effect ports for storage, mail, LLM calls, database sessions, and
  units of work;
- test fakes for these ports.

Evidence:

- `crewday/app/tenancy/context.py` and `app/tenancy/current.py` define an
  immutable, async-safe context carrier.
- `crewday/app/events/registry.py` and `app/events/bus.py` define immutable
  events, a registry, ordered handlers, and an optional relay.
- `crewday/app/adapters/storage/ports.py`, `mail/ports.py`, `llm/ports.py`,
  and `db/ports.py` define clear side-effect boundaries.
- `llmrouter` has authority request context, audit events, and durable
  service-facing ports.
- `ontology` has request scope, audit events, and storage and source ports.
- `xbot` needs explicit context for user, workspace, agent, and shared-service
  calls.

Keep event names, domain payloads, SQLAlchemy filters, transaction handling,
and service-specific adapters in the consumer.

## Security and identity

### 4. Cryptographic primitives and secret safety

This is a strong candidate, but it must stay small. Implement and test:

- constant-time comparison;
- secure random token creation;
- one-way token and API-key verification;
- HMAC signing and verification with key rotation support;
- authenticated encryption envelope interfaces;
- key-purpose and key-version validation;
- secret redaction for logs, errors, and serialized payloads;
- secure handling of `SecretStr`-like values without accidental string output.

Evidence:

- `fj2/apps/core/encryption.py`, `apps/core/utils/secrets.py`, API-key
  authentication, and provider-key handling use these concepts.
- `crewday/app/adapters/storage/envelope.py`, `app/security/hmac_signer.py`,
  `app/auth/keys.py`, and secret rotation commands implement envelope and
  signing behavior.
- `crewday/app/util/redact.py` provides recursive PII and secret redaction for
  audit and LLM data.
- `llmrouter` has encrypted credential storage, service-secret exchange, and
  short-lived token decisions.
- `ontology` has client credentials, protected record envelopes, and
  security-lifecycle requirements.
- `xbot` requires secret removal before a request, protected secret storage,
  and redacted logs.

The library must not own a root key, a database secret row, a deployment key,
or a product-specific key rotation command. Those are consumer concerns.

### 5. Service credentials and short-lived tokens

Implement and test a framework-neutral contract for:

- a show-once bootstrap secret or equivalent enrollment result;
- storage of only a verifier for long-lived bootstrap material;
- exchange for a short-lived token;
- exact audience, service, workspace, operation, credential-generation, and
  expiry claims;
- token renewal, rotation, revocation, and clock-skew handling;
- fail-closed scope checks;
- token confusion and replay tests.

Evidence:

- `llmrouter/docs/decisions/0011-exchange-service-secrets-for-short-lived-tokens.md`
  defines the complete machine-credential pattern.
- `ontology/app/backend/src/ontology_service/adapters/client_credentials.py`
  and its API and tests implement a related client-credential flow.
- `crewday/app/auth/tokens.py`, `app/auth/keys.py`, and `app/agent/tokens.py`
  contain local token handling.
- `fj2/apps/api/authentication.py` handles API keys.
- `xbot` plans service references and shared-service calls.

The library may provide claim types, validation, and ports. It must not
provide a universal authorization policy. Each service owns its grants.

### 6. Human authentication protocol helpers

This is a possible candidate with a narrow boundary.

The shared Pocket ID design applies to `llmrouter` and `ontology`. Both use
OpenID Connect for administrator authentication, while each application keeps
its own local grants and sessions. `xbot` explicitly owns a separate product
passkey identity. `fj2` and `crewday` have their own user authentication
requirements.

Possible library work:

- OIDC issuer, audience, state, nonce, PKCE, and time validation;
- immutable issuer and subject identity values;
- recent-authentication checks;
- local-session and revocation protocols;
- WebAuthn challenge replay and origin-validation primitives, only if a second
  implementation confirms the same rules.

Keep these parts local:

- user and passkey records;
- account enrollment and recovery screens;
- administrator grants;
- membership, role, and workspace permission records;
- provider-specific deployment configuration.

Evidence:

- `llmrouter/docs/decisions/0037-use-shared-pocket-id-for-human-authentication.md`.
- `ontology/docs/decisions/0010-use-shared-pocket-id-for-human-authentication.md`.
- `xbot/docs/decisions/0004-own-xbot-passkey-identity-and-control-data.md`.
- `fj2/apps/accounts/passkey.py`, `apps/accounts/totp.py`, and
  `specs/03-authentication.md`.
- `crewday/app/auth/passkey.py`, `app/auth/webauthn.py`, and
  `app/auth/session.py`.

Do not place Pocket ID deployment code in `opendle-lib`. Do not force Xbot to
use the shared administrator identity.

### Configuration and endpoint safety

This is a possible candidate for a small settings-validation module.
Implement only:

- required and forbidden secret-field checks;
- endpoint scheme and host validation;
- explicit allowance for loopback-only insecure development endpoints;
- production rejection of insecure transport;
- bounded configuration values and safe error messages.

Evidence:

- `llmrouter/packages/backend-role/src/llmrouter_backend/configuration/schema.py`
  rejects secret fields and restricts plain HTTP to explicit loopback
  endpoints.
- `ontology/app/backend/src/ontology_service/config.py` and
  `adapters/configuration.py` validate deployment and transport settings.
- `crewday/app/config.py` and `app/adapters/mail/smtp_config.py` validate
  environment-driven settings.
- `fj2/config/settings/base.py` and production settings validate storage,
  API, proxy, and security configuration.

Keep environment names, deployment defaults, secret sources, and application
settings models in the consumer.

### 7. Scope and authorization value types

Implement only common data and checks:

- service, workspace, actor, audience, operation, and capability scope;
- explicit scope intersection and containment checks;
- expected-state revision checks;
- recent-authentication requirements;
- policy decision results with a reason and audit context.

Do not implement shared roles such as `admin`, `editor`, `author`, `god
administrator`, or `workspace member`. These roles have different meaning in
the consumers.

Evidence:

- `crewday/app/authz` has workspace, owner, membership, scope, and approval
  checks.
- `llmrouter` has service-tree, workspace, authority, and grant enforcement.
- `ontology` has service, workspace, client, and god-administrator checks.
- `fj2` has roles and API permissions in `apps/api` and `apps/admin_api`.
- `xbot` requires current user and workspace checks for every agent action.

### Outbound URL safety

This is a possible security candidate. It has clear value for any consumer
that fetches a URL, but the library must not become an image or web-scraping
package.

Possible library work:

- allowed schemes and explicit host policy;
- private, loopback, link-local, and metadata-address rejection;
- DNS resolution and rebinding checks;
- redirect re-validation;
- connection, read, and total timeouts;
- response-size and content-type limits;
- a safe result type that does not include credentials in errors or logs.

Evidence:

- `fj2/apps/core/safe_fetch.py` implements scheme, DNS, private-address,
  redirect, timeout, and response-size checks. Its tests cover IPv4, IPv6,
  DNS rebinding, metadata addresses, redirects, and content types.
- `crewday/app/net/fetch_guard.py` implements DNS-pinned fetch checks, size
  limits, and safe external retrieval for calendar and integration data.
- `fj2` uses this behavior from API URL checks, social clients, LLM vision,
  image proxy code, monetization, and autopublish.
- `crewday`, `llmrouter`, `ontology`, and `xbot` all have planned external
  integrations or document and connector fetches.

Keep image downloading, scraping, provider allow-lists, and product URL rules
in the consumer. Preserve the SSRF tests if this capability moves.

### Rate limiting and abuse controls

This is a possible candidate for algorithms and result values:

- a bounded request key;
- a time window or token bucket decision;
- retry-after and remaining-capacity values;
- atomic check-and-consume protocol;
- separate authentication, API, and resource-abuse classifications.

Evidence:

- `fj2/apps/api/throttling.py`, account login limits, and Turnstile checks.
- `crewday/app/abuse/throttle.py`, `app/abuse/window_store.py`, and API rate
  limit middleware.
- `llmrouter` has service, workspace, provider, budget, and spool pressure
  controls.
- `ontology` and `xbot` require bounded operations and resource-exhaustion
  protection.

Keep thresholds, exemptions, identity keys, and product abuse policy local.
The library must not provide one default limit for all projects.

## Data, files, and privacy

### 8. Object storage and attachment safety

This is a possible candidate. The common part is the file lifecycle, not the
storage vendor or the product file model.

Implement and test ports and value types for:

- opaque object keys;
- content type, byte length, and checksum metadata;
- upload, completion, read, delete, and cleanup states;
- short-lived upload and download capabilities;
- multipart upload state;
- incomplete-upload expiry;
- integrity verification before activation;
- soft delete followed by durable cleanup;
- storage errors that are safe to retry.

Evidence:

- `ontology/docs/decisions/0005-s3-compatible-file-storage.md` requires
  multipart upload, checksums, controlled download, and cleanup.
- `llmrouter/docs/decisions/0048-use-immutable-attachments-and-explicit-compatibility-diagnostics.md`
  requires immutable attachments, scope, media type, length, and SHA-256.
- `crewday/app/adapters/storage/ports.py`, `localfs.py`, `s3.py`, and
  `app/api/uploads.py` provide storage and upload adapters.
- `fj2/apps/core/storage.py`, S3 settings, and media migration commands
  provide local and S3-compatible media behavior.
- `xbot` plans media and document handling while its data remains in the
  appropriate shared or protected service.

Keep local:

- database file models and migrations;
- bucket names and credentials;
- vendor-specific URL or multipart code, unless a stable optional adapter is
  later justified;
- article, asset, ontology, or agent-document rules.

### 9. Content and media validation

Implement small, framework-neutral validators for:

- maximum byte size;
- declared versus detected media type;
- safe file-name metadata;
- checksum calculation;
- accepted text and binary format policy supplied by the caller;
- rejection of executable or ambiguous content;
- bounded JSON and text decoding.

Evidence:

- `crewday/app/adapters/storage/mime.py` performs magic-byte and narrow JSON
  checks.
- `llmrouter` defines attachment media, size, count, and digest limits.
- `fj2` has image processing, media storage, and safe fetch code.
- `ontology` requires file metadata and checksum verification.
- `xbot/docs/specs/06-security-privacy-and-quality.md` treats media and
  document input as untrusted.

The allowed content list belongs to each product. The library should not
declare that every consumer accepts the same image, audio, or document types.

### 10. Audit events and safe provenance

This is a strong candidate. Implement:

- an immutable audit event value type;
- actor, service, workspace, operation, and target references;
- event time and correlation/request identity;
- success, failure, and reason fields;
- redaction rules and safe serialization;
- an audit sink protocol;
- retention and export hooks;
- a distinction between user, service, worker, and system provenance.

Evidence:

- `fj2/apps/core/audit.py`, `apps/admin_agent/audit.py`, and the moderation
  and migration audit code.
- `crewday/app/audit`, `app/auth/audit.py`, and deployment and worker audit
  records.
- `llmrouter` authority, administration, request, and retention decisions.
- `ontology/app/backend/src/ontology_service/adapters/audit.py` and secure
  audit migrations.
- `xbot` requires audit for sessions, permissions, agent actions, external
  operations, and security events.

Do not put one shared audit table or retention period in the base package.
Each application owns its audit store and product retention rule.

### 11. Privacy and data lifecycle primitives

Implement only reusable lifecycle mechanics:

- retention rule values and validation;
- deletion-operation state and deadlines;
- export manifests and checksums;
- short-lived export capability values;
- pseudonymous actor references;
- redaction and unlinking helpers;
- cleanup retry and proof-of-completion results.

Evidence:

- `fj2/apps/accounts/gdpr.py` and `specs/18-consent.md` define export and
  account deletion behavior.
- `crewday/app/domain/privacy`, privacy worker tasks, and data-export APIs
  define lifecycle work.
- `llmrouter` decisions 0012, 0013, 0042, and 0051 define capture retention,
  audit retention, and backup rules.
- `ontology` security and lifecycle specifications define deletion, export,
  backup, and file cleanup behavior.
- `xbot/docs/specs/06-security-privacy-and-quality.md` defines account
  deletion, member unlinking, retention, and shared-service deletion.

Keep the following local:

- which records are retained and for how long;
- legal basis and product consent text;
- deletion order for product records;
- backup system behavior;
- domain-specific privacy exceptions.

## Reliability and operations

### 12. Retry, backoff, and uncertain-result handling

Implement and test:

- bounded exponential backoff with jitter;
- retry classification by error type;
- deadline and attempt limits;
- idempotency requirements before a retry;
- uncertain-result states;
- cancellation and stopped-operation states;
- retry-after values;
- structured retry decisions for logs and metrics.

Evidence:

- `fj2/apps/core/utils/retry.py`, task settings, and migration retry tools.
- `crewday/app/worker/tasks`, webhook delivery, email delivery, and job
  failure handling.
- `llmrouter` decisions 0007, 0021, 0032, 0033, 0040, and 0050 define
  bounded retries, recovery, cancellation, and attempt timeouts.
- `ontology` uses retryable read-after-write results and cleanup retries.
- `xbot` requires duplicate and uncertain-result tests for external writes.

Provider-specific retry rules, HTTP client settings, and business retry
decisions remain in the consumer.

### 13. Leases, worker state, and durable operation protocols

Implement only framework-neutral parts:

- operation state values;
- ownership leases and expiry;
- heartbeat values;
- generation or revision checks;
- stale-worker detection;
- safe resume and terminal failure states;
- bounded failure counters and operator reset results.

Evidence:

- `crewday/app/worker/job_state.py`, `app/worker/heartbeat.py`, and
  `app/worker/scheduler.py` implement worker state and failure thresholds.
- `fj2` uses Celery tasks, recovery tasks, and migration jobs with retry and
  idempotency requirements.
- `llmrouter` defines durable agent runs, spools, request recovery, node
  draining, and local health circuits.
- `ontology` defines durable deletion, export, and workspace lifecycle
  operations.
- `xbot` plans durable stage handoffs, dependency checks, and external-write
  recovery.

Do not add a shared scheduler. Celery, APScheduler, and custom workers have
different execution models. The library can provide state machines and
protocols that those workers use.

### 14. Health, readiness, circuits, metrics, and tracing

Implement and test:

- health result values with component, status, reason, and expiry;
- liveness versus readiness distinction;
- dependency health checks;
- bounded local circuit state;
- request and operation correlation;
- metric names and label validation;
- instrumentation hooks that do not require one framework.

Evidence:

- `fj2/config/urls.py`, `config/settings/production.py`,
  `apps/core/metrics.py`, and health checks.
- `crewday/app/api/health.py`, request ID and metrics middleware, and worker
  heartbeat code.
- `llmrouter` decisions 0034, 0035, 0036, and the `health` package define
  health circuits and operational readiness.
- `ontology` has health and HTTP boundary tests and service readiness rules.
- `xbot` requires explicit shared-service outage checks and safe degraded
  modes.

Keep dashboards, deployment probes, Prometheus or OpenTelemetry exporters,
and product-specific health dependencies in the consumer or an optional
integration package.

### Streaming and reconnect control

This is a possible candidate for applications that expose long-running or
server-sent streams. Implement only state and safety values:

- opaque stream and operation identity;
- admission and capacity result;
- heartbeat and last-seen values;
- reconnect cursor or event sequence;
- resume and terminal state;
- cancellation and expiry;
- no-duplicate event guidance.

Evidence:

- `fj2/apps/admin_agent/stream_admission.py`, `stream_bridge.py`,
  `stream_events.py`, and `stream_http.py` handle admission and event flow.
- `crewday/app/api/transport/sse.py` and `admin_sse.py` handle stream
  lifecycle and reconnect behavior.
- `llmrouter` has stream events, cancellation, request recovery, and bounded
  stream decisions.
- `xbot` plans durable agent stage handoffs and reconnect-safe work.

Keep SSE, WebSocket, HTTP response objects, event payloads, and UI behavior
in the consumer or an optional web adapter.

### 15. Webhooks and signed callbacks

This is a possible candidate. Implement only after two consumers confirm the
same delivery contract:

- per-subscription signing secrets;
- canonical payload bytes;
- timestamp and replay window;
- signature rotation;
- delivery attempt state;
- bounded retry and dead-letter state;
- idempotent receiver guidance;
- safe response classification.

Evidence:

- `crewday/app/api/chat_gateway/webhooks.py`, integration webhooks, and
  `app/worker/tasks/webhook_dispatch.py`.
- `crewday/app/security/hmac_signer.py` documents separate deployment and
  subscription signing boundaries.
- `fj2` has external notification and search-engine callback patterns.
- `llmrouter` and `ontology` expose service and client integration contracts.
- `xbot` requires replay protection for connector callbacks and webhooks.

Do not put a universal webhook event catalog in the library.

## Integration and client support

### 16. Generated contracts and conformance checks

The library should provide shared tooling only when it does not make one
service's API the common API. Good candidates are:

- OpenAPI or JSON Schema normalization helpers;
- contract digest calculation;
- compatibility comparison results;
- fixture validation;
- client boundary checks;
- generated-client test helpers;
- stable error and pagination fixture formats.

Evidence:

- `llmrouter/docs/api`, its Python client, TypeScript client decision, and
  contract conformance documents.
- `ontology/docs/api`, OpenAPI, JSON Schema, generated Python client, and
  contract tests.
- `xbot/docs/contracts` maps Xbot to shared service contracts.
- `fj2` and `crewday` expose automation APIs that need stable boundary tests.

Keep official Router and Ontology clients in their service repositories. A
client for one service is not a shared-library feature. A common Python
transport helper is useful only if it has no Router or Ontology policy.

### 17. LLM and model provider types

This is a possible candidate with a narrow boundary. A shared package may
define:

- provider capability values;
- model and embedding request metadata;
- usage and cost measurement values;
- prompt and content safety result types;
- provider adapter protocols;
- cancellation and retry result types.

Evidence:

- `fj2/apps/llm_providers` has provider clients, capability data, usage, and
  moderation integration.
- `crewday/app/adapters/llm` and `app/domain/llm` have provider ports,
  embeddings, budgets, and consent checks.
- `llmrouter` owns routing, provider admission, budgets, health, accounting,
  and official clients.
- `xbot` plans to call the shared Router and must not duplicate its routing
  logic.

Keep routing, provider selection, budgets, prompts, moderation policy, model
catalogs, and service-specific data profiles in the owning application.

### Financial and usage value types

This is a possible small value-type module. It must not become a billing
system. Possible types include:

- currency code;
- integer minor-unit amount;
- bounded usage quantity;
- provider cost and rate values;
- rounding and comparison results.

Evidence:

- `crewday/app/util/money.py` provides minor-unit arithmetic for billing and
  payroll.
- `crewday/app/domain/llm/budget.py` uses budget and usage values.
- `fj2/apps/llm_providers` tracks provider prices and usage.
- `llmrouter` owns hierarchical budgets, synchronized prices, and accounting.

Keep tax, payroll, invoices, billing rules, provider price catalogs, and budget
policy in the consumer.

### LLM response parsing and error classification

This is a possible candidate for consumers that call more than one model
provider. Implement only provider-neutral behavior:

- extraction of JSON objects and arrays from bounded text;
- removal of known code-fence and preamble forms;
- schema validation hooks supplied by the caller;
- typed configuration, transport, provider, response, and retryable errors;
- classification that preserves the original safe reason;
- bounded response and error sizes.

Evidence:

- `fj2/apps/llm_providers/clients/json_utils.py` is used by article tags,
  focal-point detection, admin-agent extraction, comments, messaging, and
  autopublish. Its tests cover code fences, preambles, arrays, objects, and
  validation.
- `fj2/apps/llm_providers/clients/base.py` defines provider-neutral response
  values and `exceptions.py` classifies provider errors.
- `crewday/app/adapters/llm` defines provider ports and shared response
  handling.
- `llmrouter` owns the public model request and provider error contract.

Keep prompts, schemas, moderation thresholds, provider names, model catalogs,
and routing decisions in the consumer.

### 18. Notifications and external message delivery

This is a possible candidate only as ports and delivery state:

- message destination and channel values;
- template rendering input boundaries;
- delivery attempt and provider response values;
- retry and suppression decisions;
- unsubscribe and consent checks as caller-supplied policy.

Evidence:

- `fj2/apps/messaging`, `apps/newsletter`, `apps/accounts/tasks.py`, and
  `specs/19-notifications.md`.
- `crewday/app/domain/messaging`, `app/adapters/notifications`, mail, push,
  and chat gateway adapters.
- `xbot` plans notification links and preferences.

Do not move message text, notification types, recipient rules, or provider
adapters into the base package.

### Contract validation and generator support

This is a tooling candidate. It should not make one service API the shared
runtime API.

Possible library or repository-tooling work:

- strict JSON loading with duplicate-key rejection;
- OpenAPI and JSON Schema normalization;
- contract digest calculation;
- compatibility comparison results;
- fixture validation;
- generated-client boundary checks;
- stable error and pagination fixture formats.

Evidence:

- `llmrouter/scripts/generate-contract-models.py` generates Python and
  TypeScript contract models and digest files.
- `llmrouter/docs/api` contains contract policy, fixtures, OpenAPI, and
  conformance documents.
- `ontology/scripts/generate-python-contracts.py` and
  `generate-typescript-contracts.py` generate service and client models.
- `ontology/docs/api` contains OpenAPI, JSON Schema, compatibility baselines,
  and contract tests.
- `xbot/docs/contracts` maps Xbot to shared service contracts.

Keep service models, service clients, API operation names, and release policy
in the owning repository. This capability may fit repository tooling better
than the runtime package.

## Keep in the consumers

The following parts should remain local based on the current evidence:

- `fj2` articles, comments, moderation queues, SEO, migration mappings,
  France-Jeunes roles, and site publishing policy;
- `crewday` properties, stays, employees, billing, inventory, hospitality
  workflows, and its agent domain;
- `llmrouter` model routing, provider selection, budgets, spools, agent runs,
  service tree, and Router-specific API clients;
- `ontology` object/link schema, inheritance, query planning, revision
  publication, workspace records, and ontology-specific storage policy;
- `xbot` personas, content planning, social connectors, agent policy,
  workspace orchestration, and product-member identity;
- all ORM models, database migrations, framework middleware, URL routes,
  templates, and product-specific background task registration;
- deployment manifests, bucket names, service URLs, root keys, and other
  runtime configuration;
- React code, design systems, and browser clients. Shared React code belongs
  in `opendle-ui`.

Similar names are not enough evidence for extraction. For example, a
`Workspace` in `crewday`, `llmrouter`, `ontology`, and `xbot` does not prove
that the records or permission rules should be shared.

## Suggested implementation order

1. Add common value types, clocks, bounds, errors, canonical JSON, and safe
   redaction.
2. Add idempotency and request-fingerprint protocols with in-memory test
   implementations.
3. Add cryptographic primitives and service-token claim validation.
4. Add audit event values and reliability state machines.
5. Add contract validation tooling and fixture checks for service boundaries.
6. Add storage and attachment ports after the first two consumers agree on
   upload, integrity, and cleanup states.
7. Add OIDC helpers for the LLM Router and Ontology administrator flow only
   after their integration tests define the exact contract.
8. Add optional FastAPI and Django adapters only when two consumers need the
   same adapter behavior.
9. Revisit webhook, notification, LLM, privacy, and financial value
   helpers after more than one consumer has a stable implementation.

Every implemented capability must include success, error, boundary, replay,
scope, and unauthorized-use tests where the capability supports those cases.
Each consumer migration must use the direct Git `main` dependency and refresh
its lock before the migration is considered complete.

## Review triggers

Review this roadmap when:

- a second consumer implements a candidate with different rules;
- a proposed module needs Django, FastAPI, SQLAlchemy, Celery, or a vendor
  dependency;
- a candidate needs a shared database schema or migration;
- a candidate changes a public import, error, signature, or supported Python
  version;
- an accepted consumer decision conflicts with a proposed shared behavior;
- `xbot` or another future consumer supplies new backend evidence.
