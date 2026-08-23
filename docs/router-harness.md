# Router model contract and harness

## Public imports

The `opendle` package exports the Router model values and harness values. A
consumer can also import the same names from `opendle.router` and
`opendle.harness`.

Use these Router contract groups:

- `SystemMessage`, `UserMessage`, and `AssistantMessage` for conversation data;
- text, image, tool-call, and tool-result parts for message content;
- `AssignmentSelector` and `ExactModelSelector` for route selection;
- `ModelCall`, `ModelCallResult`, and `ModelCaller` for a transport port;
- `Usage` and `UsageItem` for provider-neutral call accounting data.

Use these harness groups:

- `ConversationState` and `RouteState` for caller-owned state;
- `HarnessTool`, `ToolHandler`, and `ToolExecutor` for caller tools;
- `ContextLimits`, `ContextPolicy`, and the context enums for finite reduction;
- `CompactionRequest` and `Compactor` for pinned model compaction;
- `ConversationHarness` and `HarnessConfig` for execution;
- `StateLoader`, `StateSaver`, and `run_stored` for small storage callbacks;
- `InMemoryConversationStore` only for tests or short-lived processes.

All public values have complete type hints and documentation. All collections
use immutable tuples. The harness does not change the input state.

## Model transport port

`ModelCaller` is async and has no HTTP dependency. It receives one complete
`ModelCall` and returns one `ModelCallResult`. A caller or a later SDK adapter
implements the live Router transport.

An exact selector identifies one configured provider-model. The provider-model
identifies the exact provider connection and wire model for sticky calls and
compaction. An assignment call after a failed sticky call contains that route
in `excluded_routes`. The transport must not run an excluded route in that
assignment attempt.

Raise `ModelCallError` for a safe provider-neutral call failure. Only
`CallFailurePhase.BEFORE_VISIBLE_OUTPUT` permits the harness to continue from a
failed sticky call to the current assignment. `AFTER_VISIBLE_OUTPUT` and
`UNCERTAIN` stop the workflow and do not create a replacement call.

## Tool loop

The caller supplies the complete eligible tool set. The default
`SequentialToolExecutor` calls each handler in model order. It waits for one
handler before it starts the next handler. A caller can replace the complete
batch executor.

The executor must return one `ToolResultPart` for each call in the same order.
Tool input validation, authorization, effects, error recovery, and durable
result storage stay with the caller.

## Context reduction

Every context policy has finite message and byte limits. Deterministic pruning
keeps the complete leading system prefix and the newest complete message
groups. It does not split an assistant tool-call message from its result
message. It fails when the required prefix or newest group cannot fit.

Model compaction runs only when context exceeds the selected limits. The
compaction request pins the exact route from the preceding successful workflow
call. It does not contain assignment fallback. The hook receives the compatible
conversation, workspace, tags, route, and limits. Its result must preserve the
system prefix and active suffix and must fit both limits.

The caller selects one compaction failure mode. `STOP` raises
`CompactionError`. `PRUNE` uses deterministic pruning. The harness never retries
compaction on another route.

## State and storage

`ConversationHarness.run` accepts one `ConversationState` and returns new
state. The caller owns durable storage, deletion, authorization, and domain
links.

`run_stored` is a small load-run-save helper. It loads once and saves the final
successful state once. `InMemoryConversationStore` has explicit conversation,
message, and byte limits. Its byte limit includes uploaded image bytes. It does
not evict data and does not provide durable storage.
