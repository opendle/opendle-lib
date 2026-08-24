# Router Python SDK

## Client scope

`RouterClient` is the official dependency-free client for the native Router
version 1 service API. One client has one Router base URL and one private
backend service key. The key goes only in the HTTP `Authorization` header.
Client and error representations do not contain it.

The client has typed operations for these native service resources:

- workspaces;
- assignments and observed requirements;
- service-key metadata, creation, and revocation;
- available provider-model discovery;
- synchronous model calls and model streams;
- embedding batches;
- media jobs, polling, and retained content;
- service statistics.

The client does not contain global administrator operations. It also does not
contain provider-specific, OpenAI-compatible, agent-run, durable model-call
status, cancellation, replay, resume, or service-token exchange operations.

## Harness integration

`RouterClient` implements the async `ModelCaller` port. Supply the client as
the `model_caller` when you create `ConversationHarness`. The client maps a
reported native call failure to `ModelCallError` before visible output. A
transport loss after the Router can accept request bytes is uncertain and
stops the harness. The client does not make a replacement call.

The harness tries its exact sticky provider-model first. After a safe failure
before visible output, it calls the current assignment and excludes the failed
provider-model. The next successful route replaces the sticky route. The
harness preserves the workspace and normalized tags on each attempt. Model
compaction stays pinned to the preceding successful exact route and has no
fallback.

## Transport and stream safety

The default transport uses the Python standard library. Plain HTTP is valid
only for an explicit loopback endpoint. Other endpoints must use HTTPS. The
client does not follow redirects because a redirect could forward the service
key to a different endpoint. It also ignores environment HTTP and HTTPS proxy
settings so they cannot route the private key through an unconfigured proxy.

The client does not retry an accepted model, embedding, or media request. It
returns `RouterTransportError` with an uncertain result after an applicable
connection failure. A model stream must start once and finish with one
`completed` event or one typed native error. A connection end before a terminal
event returns `RouterStreamError`. The error phase states whether model output
was already visible.

Successful complete responses have a caller-selected byte bound. Cursor page
iteration and media polling also require caller-selected finite bounds. The
client rejects response objects with missing, unknown, or invalid native
fields.

The client applies the Router 70 MiB complete HTTP request-body limit after
JSON, UTF-8, and Base64 encoding and before it calls the transport. It rejects
a response with more than 100 headers or more than 64 KiB of header data.
Critical response headers must occur once, and a declared complete response
length must match the received body. Complete and stream JSON reject duplicate
object keys and non-finite numbers. Every native HTTP error envelope must use
the `application/json` media type before the client parses it.

A model stream accepts no more than 100000 events and 10000000 bytes of
provider-neutral output. One event accepts no more than 2 MiB. The parser
accepts LF and CRLF framing, including CRLF that crosses transport chunks.

## Example

```python
from opendle import RouterClient

client = RouterClient(
    base_url=configuration.router_url,
    service_key=configuration.router_service_key,
)

workspace = client.create_workspace("conversation-42", "Conversation 42")
models = client.list_provider_models(limit=50)
```

Keep `configuration.router_service_key` in backend configuration. Do not put a
service key in browser code, a URL, a log field, a prompt, or model content.
