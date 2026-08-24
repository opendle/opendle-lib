"""Tests for compact scoped Ontology agent helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from opendle.ontology import (
    JsonObject,
    OntologyClient,
    OntologyTransportResponse,
)
from opendle.ontology_agent import (
    OntologyAgentHelpers,
    OntologyAgentOutputLimitError,
    compact_yaml,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_CREDENTIAL = "test-only-credential"


class AgentTransport:
    """Record agent-helper calls and return one selected JSON response."""

    def __init__(self, value: JsonObject) -> None:
        """Initialize the response value."""
        self.value = value
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OntologyTransportResponse:
        """Record one call and return the configured response."""
        del timeout
        self.calls.append((method, url, dict(headers), body))
        return OntologyTransportResponse(
            200, {"Content-Type": "application/json"}, json.dumps(self.value).encode()
        )


def helpers(
    value: JsonObject, *, maximum: int = 65_536
) -> tuple[OntologyAgentHelpers, AgentTransport]:
    """Build helpers with one explicit service and workspace scope."""
    transport = AgentTransport(value)
    client = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
        transport=transport,
    )
    return (
        OntologyAgentHelpers(
            client, workspace_api_name="conversation-7", max_output_bytes=maximum
        ),
        transport,
    )


def test_compact_yaml_is_deterministic_and_omits_optional_detail() -> None:
    """Sort mappings and omit timestamps and expanded bags by default."""
    value: JsonObject = {
        "key": "object-1",
        "type": "message",
        "title": None,
        "labels": [],
        "properties": {
            "content": [
                {
                    "value": {
                        "createdAt": "business data",
                        "bags": {"source": "user data"},
                        "items": [True, None, [], {}, {"b": 2, "a": "x:y"}],
                    },
                    "createdAt": "2026-08-23T00:00:00Z",
                    "updatedAt": "2026-08-23T00:00:00Z",
                }
            ]
        },
        "timestamps": {"createdAt": "2026-08-23T00:00:00Z"},
        "bags": [
            {
                "id": "bag-1",
                "source": "mail",
                "createdAt": "2026-08-23T00:00:00Z",
                "updatedAt": "2026-08-23T00:00:00Z",
            }
        ],
    }
    compact = compact_yaml(value)
    assert '"timestamps"' not in compact
    assert '"createdAt": "business data"' in compact
    assert '"bags":' in compact
    assert '"source": "user data"' in compact
    assert '"updatedAt"' not in compact
    detailed = compact_yaml(value, include_timestamps=True, include_bags=True)
    assert '"timestamps"' in detailed
    assert '"id": "bag-1"' in detailed
    assert '"updatedAt"' in detailed
    assert compact_yaml(value) == compact_yaml(dict(reversed(list(value.items()))))


def test_helpers_keep_explicit_service_and_workspace_scope() -> None:
    """Bind each request to the configured backend scopes without a protocol."""
    subject, transport = helpers(
        {
            "key": "object-1",
            "type": "message",
            "properties": {},
            "timestamps": {"createdAt": "hidden"},
        }
    )

    output = subject.get_object_yaml("object-1")

    assert subject.service_api_name == "xbot"
    assert subject.workspace_api_name == "conversation-7"
    assert _CREDENTIAL not in repr(subject)
    assert output == ('"key": "object-1"\n"properties": {}\n"type": "message"\n')
    method, url, headers, body = transport.calls[0]
    assert method == "GET"
    assert body is None
    assert "/services/xbot/workspaces/conversation-7/objects/object-1" in url
    assert headers["Authorization"] == f"Bearer {_CREDENTIAL}"


def test_helpers_request_only_explicit_extra_detail() -> None:
    """Add timestamp and bag expansion only when the caller selects them."""
    subject, transport = helpers(
        {
            "items": [],
            "timestamps": {"createdAt": "2026-08-23T00:00:00Z"},
            "bags": {"bag-1": {"source": "mail"}},
        }
    )
    request: JsonObject = {
        "kind": "object",
        "filter": {},
        "sort": [],
        "limit": 7,
    }

    output = subject.query_yaml(request, include_timestamps=True, include_bags=True)

    sent = cast("JsonObject", json.loads(transport.calls[0][3] or b""))
    assert sent == {**request, "expand": ["timestamps", "bags"]}
    assert request.get("expand") is None
    assert '"timestamps"' in output
    assert '"bags"' in output

    existing_request: JsonObject = {**request, "expand": ["bags"]}
    subject.query_yaml(existing_request, include_timestamps=True)
    second = cast("JsonObject", json.loads(transport.calls[1][3] or b""))
    assert second["expand"] == ["bags", "timestamps"]

    invalid_request: JsonObject = {**request, "expand": "invalid"}
    subject.query_yaml(invalid_request, include_timestamps=True)
    third = cast("JsonObject", json.loads(transport.calls[2][3] or b""))
    assert third["expand"] == "invalid"


def test_graph_link_and_change_helpers_use_the_same_workspace() -> None:
    """Keep graph, link, and changed reads in the configured workspace."""
    subject, transport = helpers({"objects": [], "links": [], "truncated": False})
    graph: JsonObject = {"startObjects": ["a"], "depth": 1, "limit": 9}

    subject.expand_graph_yaml(graph)
    subject.get_link_yaml("link-1", include_bags=True)
    subject.read_changed_since_yaml(
        since="2026-08-01T00:00:00Z",
        cursor="v1.page",
        limit=11,
        include_timestamps=True,
    )

    assert all("/workspaces/conversation-7/" in call[1] for call in transport.calls)
    assert json.loads(transport.calls[0][3] or b"") == graph
    assert transport.calls[1][1].endswith("?expand=bags")
    assert "cursor=v1.page" in transport.calls[2][1]
    assert "expand=timestamps" in transport.calls[2][1]
    assert "limit=11" in transport.calls[2][1]


def test_compact_yaml_rejects_output_over_the_caller_bound() -> None:
    """Fail without returning a partial YAML document when output is too large."""
    with pytest.raises(OntologyAgentOutputLimitError):
        compact_yaml({"value": "long text"}, max_output_bytes=10)

    subject, _transport = helpers({"value": "long text"}, maximum=10)
    with pytest.raises(OntologyAgentOutputLimitError):
        subject.get_object_yaml("object-1")


def test_compact_yaml_supports_scalar_and_empty_root_values() -> None:
    """Render each compact JSON-equivalent root shape deterministically."""
    assert compact_yaml("text") == '"text"\n'
    assert compact_yaml([]) == "[]\n"
    assert compact_yaml({}) == "{}\n"
    assert compact_yaml(1.5) == "1.5\n"


def test_compact_yaml_and_helpers_reject_invalid_scope_and_bound() -> None:
    """Require an explicit workspace and a positive direct output bound."""
    client = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
        transport=AgentTransport({}),
    )
    with pytest.raises(ValueError, match="workspace API name"):
        OntologyAgentHelpers(client, workspace_api_name="")
    with pytest.raises(ValueError, match="output bound"):
        compact_yaml({}, max_output_bytes=0)


@pytest.mark.parametrize("maximum", [0, -1, True, cast("int", 1.5)])
def test_agent_helpers_reject_invalid_output_bounds(maximum: int) -> None:
    """Require one finite positive byte bound for each complete result."""
    client = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
        transport=AgentTransport({}),
    )
    with pytest.raises(ValueError, match="output bound"):
        OntologyAgentHelpers(
            client, workspace_api_name="main", max_output_bytes=maximum
        )


def test_compact_yaml_rejects_non_json_values() -> None:
    """Do not create YAML tags or ambiguous values outside the JSON model."""
    with pytest.raises(ValueError, match="finite"):
        compact_yaml({"bad": float("nan")})


def test_compact_yaml_preserves_dynamic_malformed_property_shapes() -> None:
    """Keep dynamic response data when a server property shape is malformed."""
    value: JsonObject = {
        "key": "object-1",
        "type": "message",
        "properties": {
            "notOccurrences": "keep this value",
            "mixed": ["keep this item", {"value": "valid occurrence"}],
        },
    }

    rendered = compact_yaml(value)

    assert '"notOccurrences": "keep this value"' in rendered
    assert '"keep this item"' in rendered


def test_compact_yaml_does_not_treat_arbitrary_data_as_a_page() -> None:
    """Keep record-shaped values in an arbitrary mapping unchanged."""
    value: JsonObject = {
        "domain": "user data",
        "items": [
            {
                "key": "nested",
                "type": "shape",
                "properties": {},
                "timestamps": {"createdAt": "keep this value"},
            }
        ],
    }

    assert '"createdAt": "keep this value"' in compact_yaml(value)


def test_compact_yaml_removes_detail_from_records_in_an_item_page() -> None:
    """Apply detail selection to each record in a public item page."""
    page: JsonObject = {
        "items": [
            {
                "key": "object-1",
                "type": "message",
                "properties": {},
                "timestamps": {"createdAt": "omit this value"},
            }
        ],
        "nextCursor": "v1.next",
    }

    rendered = compact_yaml(page)

    assert '"key": "object-1"' in rendered
    assert '"timestamps"' not in rendered
    assert '"nextCursor": "v1.next"' in rendered
