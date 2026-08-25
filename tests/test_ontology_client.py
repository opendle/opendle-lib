"""Tests for the dependency-free dynamic Ontology client."""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from typing import TYPE_CHECKING, Self, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit

import pytest

import opendle.ontology as ontology_module
from opendle.ontology import (
    JsonObject,
    JsonValue,
    OntologyAuthenticationError,
    OntologyAuthorizationError,
    OntologyClient,
    OntologyConflictError,
    OntologyNotFoundError,
    OntologyPageLimitError,
    OntologyProtocolError,
    OntologyResponseLimitError,
    OntologyTransportError,
    OntologyTransportResponse,
    OntologyUnavailableError,
    OntologyValidationError,
    canonical_json_bytes,
    fingerprint_occurrence_selector,
    value_fingerprint,
    value_occurrence_selector,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from opendle._internal.http import UrllibHttpClient

_CREDENTIAL = "test-only-credential"
_ACCEPTED_MAXIMUM_FILE_BYTES = 10_485_760
_MAXIMUM_FILE_NAME_WIRE_LENGTH = 1363


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """Contain one request received by the test transport."""

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    timeout: float


class QueueTransport:
    """Return queued complete responses and record exact requests."""

    def __init__(
        self, responses: list[OntologyTransportResponse] | None = None
    ) -> None:
        """Initialize the transport with optional responses."""
        self.responses = list(responses or [])
        self.calls: list[RecordedRequest] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OntologyTransportResponse:
        """Record one request and return the next response."""
        self.calls.append(RecordedRequest(method, url, dict(headers), body, timeout))
        if self.responses:
            return self.responses.pop(0)
        if headers.get("Accept") == "application/octet-stream":
            return response(200, b"")
        return response(200, {})


class FailingTransport:
    """Raise one transport error that contains the supplied service key."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OntologyTransportResponse:
        """Raise an error that simulates an unsafe third-party transport."""
        del method, url, body, timeout
        msg = f"failed with {headers['Authorization']}"
        raise OSError(msg)


def response(status: int, value: JsonValue | bytes) -> OntologyTransportResponse:
    """Build one test transport response."""
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    content_type = (
        "application/octet-stream" if isinstance(value, bytes) else "application/json"
    )
    return OntologyTransportResponse(status, {"Content-Type": content_type}, body)


def client(transport: QueueTransport | FailingTransport) -> OntologyClient:
    """Build one client under the standard test scope."""
    return OntologyClient(
        base_url="http://localhost:8000/",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
        timeout=4.5,
        transport=transport,
    )


def test_each_service_key_operation_has_an_explicit_client_method() -> None:
    """Cover every operation that accepts the OpenAPI service-key scheme."""
    transport = QueueTransport()
    subject = client(transport)
    empty: JsonObject = {}
    calls: list[tuple[str, str, Callable[[], object]]] = [
        ("GET", "/v1/services/xbot", subject.get_service),
        ("GET", "/v1/services/xbot/ontology", subject.get_ontology),
        ("PUT", "/v1/services/xbot/ontology", lambda: subject.apply_ontology(empty)),
        (
            "GET",
            "/v1/services/xbot/ontology/effective",
            subject.get_effective_ontology,
        ),
        (
            "POST",
            "/v1/services/xbot/ontology/validations",
            lambda: subject.validate_ontology(empty),
        ),
        (
            "POST",
            "/v1/services/xbot/ontology/migrations",
            lambda: subject.create_ontology_migration(empty),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces",
            lambda: subject.list_workspaces(cursor="v1.page", limit=7),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces",
            lambda: subject.create_workspace(empty),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main",
            lambda: subject.get_workspace("main"),
        ),
        (
            "PATCH",
            "/v1/services/xbot/workspaces/main",
            lambda: subject.update_workspace("main", empty),
        ),
        (
            "DELETE",
            "/v1/services/xbot/workspaces/main",
            lambda: subject.delete_workspace(
                "main", impact_confirmation="Delete workspace main"
            ),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces/main/objects",
            lambda: subject.create_object("main", empty),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/objects/object%2F1",
            lambda: subject.get_object("main", "object/1", expand=("timestamps",)),
        ),
        (
            "PATCH",
            "/v1/services/xbot/workspaces/main/objects/object-1",
            lambda: subject.update_object("main", "object-1", empty),
        ),
        (
            "DELETE",
            "/v1/services/xbot/workspaces/main/objects/object-1",
            lambda: subject.delete_object("main", "object-1"),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces/main/links",
            lambda: subject.create_link("main", empty),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/links/link-1",
            lambda: subject.get_link("main", "link-1", expand=("bags",)),
        ),
        (
            "PATCH",
            "/v1/services/xbot/workspaces/main/links/link-1",
            lambda: subject.update_link("main", "link-1", empty),
        ),
        (
            "DELETE",
            "/v1/services/xbot/workspaces/main/links/link-1",
            lambda: subject.delete_link("main", "link-1"),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/bags",
            lambda: subject.list_metadata_bags("main", cursor="v1.page", limit=7),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces/main/bags",
            lambda: subject.create_metadata_bag("main", empty),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/bags/bag-1",
            lambda: subject.get_metadata_bag("main", "bag-1"),
        ),
        (
            "PATCH",
            "/v1/services/xbot/workspaces/main/bags/bag-1",
            lambda: subject.update_metadata_bag("main", "bag-1", empty),
        ),
        (
            "DELETE",
            "/v1/services/xbot/workspaces/main/bags/bag-1",
            lambda: subject.delete_metadata_bag("main", "bag-1"),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces/main/files",
            lambda: subject.upload_file(
                "main", name="note.txt", media_type="text/plain", content=b"hello"
            ),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/files/file-1",
            lambda: subject.get_file_metadata("main", "file-1"),
        ),
        (
            "DELETE",
            "/v1/services/xbot/workspaces/main/files/file-1",
            lambda: subject.delete_file("main", "file-1"),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/files/file-1/content",
            lambda: subject.download_file("main", "file-1"),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/views",
            lambda: subject.list_saved_views(
                "main", cursor="v1.page", limit=7, label="pinned"
            ),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces/main/views",
            lambda: subject.create_saved_view("main", empty),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/views/view-1",
            lambda: subject.get_saved_view(
                "main", "view-1", expand=("timestamps", "bags")
            ),
        ),
        (
            "PATCH",
            "/v1/services/xbot/workspaces/main/views/view-1",
            lambda: subject.update_saved_view("main", "view-1", empty),
        ),
        (
            "DELETE",
            "/v1/services/xbot/workspaces/main/views/view-1",
            lambda: subject.delete_saved_view("main", "view-1"),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/views/view-1/graph",
            lambda: subject.open_saved_view_graph(
                "main",
                "view-1",
                expand=("timestamps", "bags"),
                cursor="v1.page",
                limit=7,
            ),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces/main/query",
            lambda: subject.query_workspace("main", empty),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces/main/count",
            lambda: subject.count_workspace("main", empty),
        ),
        (
            "POST",
            "/v1/services/xbot/workspaces/main/graph",
            lambda: subject.expand_graph("main", empty),
        ),
        (
            "GET",
            "/v1/services/xbot/workspaces/main/changes",
            lambda: subject.read_changed_since(
                "main",
                since="2026-01-01T00:00:00Z",
                cursor="v1.page",
                limit=7,
                expand=("timestamps", "bags"),
            ),
        ),
        (
            "POST",
            "/v1/services/xbot/bulk",
            lambda: subject.apply_service_bulk_mutation(empty),
        ),
        ("GET", "/v1/services/xbot/jobs/job-1", lambda: subject.get_job("job-1")),
    ]

    for _method, _path, call in calls:
        call()

    actual = [(call.method, urlsplit(call.url).path) for call in transport.calls]
    assert actual == [(method, path) for method, path, _call in calls]
    assert all(
        call.headers["Authorization"] == f"Bearer {_CREDENTIAL}"
        for call in transport.calls
    )
    allowed_query_parameters = {
        ("PUT", "/v1/services/xbot/ontology"): {"visibility"},
        ("POST", "/v1/services/xbot/ontology/migrations"): {"visibility"},
        ("GET", "/v1/services/xbot/workspaces"): {"cursor", "limit"},
        ("POST", "/v1/services/xbot/workspaces/main/objects"): {"visibility"},
        ("GET", "/v1/services/xbot/workspaces/main/objects/object%2F1"): {"expand"},
        ("PATCH", "/v1/services/xbot/workspaces/main/objects/object-1"): {"visibility"},
        ("DELETE", "/v1/services/xbot/workspaces/main/objects/object-1"): {
            "visibility"
        },
        ("POST", "/v1/services/xbot/workspaces/main/links"): {"visibility"},
        ("GET", "/v1/services/xbot/workspaces/main/links/link-1"): {"expand"},
        ("PATCH", "/v1/services/xbot/workspaces/main/links/link-1"): {"visibility"},
        ("DELETE", "/v1/services/xbot/workspaces/main/links/link-1"): {"visibility"},
        ("GET", "/v1/services/xbot/workspaces/main/bags"): {"cursor", "limit"},
        ("POST", "/v1/services/xbot/workspaces/main/bags"): {"visibility"},
        ("PATCH", "/v1/services/xbot/workspaces/main/bags/bag-1"): {"visibility"},
        ("DELETE", "/v1/services/xbot/workspaces/main/bags/bag-1"): {"visibility"},
        ("DELETE", "/v1/services/xbot/workspaces/main/files/file-1"): {"visibility"},
        ("GET", "/v1/services/xbot/workspaces/main/views"): {
            "cursor",
            "label",
            "limit",
        },
        ("POST", "/v1/services/xbot/workspaces/main/views"): {"visibility"},
        ("GET", "/v1/services/xbot/workspaces/main/views/view-1"): {"expand"},
        ("PATCH", "/v1/services/xbot/workspaces/main/views/view-1"): {"visibility"},
        ("DELETE", "/v1/services/xbot/workspaces/main/views/view-1"): {"visibility"},
        ("GET", "/v1/services/xbot/workspaces/main/views/view-1/graph"): {
            "cursor",
            "expand",
            "limit",
        },
        ("GET", "/v1/services/xbot/workspaces/main/changes"): {
            "cursor",
            "expand",
            "limit",
            "since",
        },
        ("POST", "/v1/services/xbot/bulk"): {"visibility"},
    }
    allowed_header_parameters = {
        ("DELETE", "/v1/services/xbot/workspaces/main"): {"X-Impact-Confirmation"},
        ("POST", "/v1/services/xbot/workspaces/main/files"): {
            "X-File-Media-Type",
            "X-File-Name",
            "X-File-SHA256",
            "X-File-Size",
        },
    }
    for recorded in transport.calls:
        parsed = urlsplit(recorded.url)
        operation = (recorded.method, parsed.path)
        query_names = {name for name, _value in parse_qsl(parsed.query)}
        header_names = set(recorded.headers) - {
            "Accept",
            "Authorization",
            "Content-Type",
        }
        assert query_names == allowed_query_parameters.get(operation, set())
        assert header_names == allowed_header_parameters.get(operation, set())


def test_requests_are_canonical_and_file_transfer_is_exact() -> None:
    """Canonicalize JSON and calculate exact file integrity metadata."""
    transport = QueueTransport(
        [response(200, {}), response(201, {}), response(200, b"exact bytes")]
    )
    subject = client(transport)

    subject.create_object(
        "space name", {"z": 1, "a": [True, None]}, visibility="read_after_write"
    )
    subject.upload_file(
        "space name", name="résumé.txt", media_type="text/plain", content=b"abc"
    )
    assert subject.download_file("space name", "file/id") == b"exact bytes"

    create = transport.calls[0]
    assert create.body == b'{"a":[true,null],"z":1}'
    assert create.url.endswith(
        "/v1/services/xbot/workspaces/space%20name/objects?visibility=read_after_write"
    )
    upload = transport.calls[1]
    assert upload.body == b"abc"
    assert upload.headers["X-File-Name"] == "u8.csOpc3Vtw6kudHh0"
    assert upload.headers["X-File-Size"] == "3"
    assert upload.headers["X-File-SHA256"] == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert transport.calls[2].headers["Accept"] == "application/octet-stream"
    assert transport.calls[2].url.endswith("/files/file%2Fid/content")


@pytest.mark.parametrize(
    ("name", "wire_name"),
    [
        ("note.txt", "u8.bm90ZS50eHQ"),
        ("résumé.txt", "u8.csOpc3Vtw6kudHh0"),
        ("文件.txt", "u8.5paH5Lu2LnR4dA"),
        ("🧠.txt", "u8.8J-noC50eHQ"),
    ],
)
def test_upload_encodes_file_names_as_marked_utf8_base64url(
    name: str, wire_name: str
) -> None:
    """Send one canonical ASCII header for each accepted Unicode name."""
    transport = QueueTransport()

    client(transport).upload_file(
        "main", name=name, media_type="text/plain", content=b""
    )

    actual = transport.calls[0].headers["X-File-Name"]
    assert actual == wire_name
    assert actual.isascii()
    assert "=" not in actual
    payload = actual.removeprefix("u8.")
    assert all(character.isalnum() or character in "_-" for character in payload)
    padding = "=" * (-len(payload) % 4)
    assert base64.urlsafe_b64decode(payload + padding) == name.encode("utf-8")


def test_upload_preserves_exact_utf8_without_unicode_normalization() -> None:
    """Keep composed and decomposed Unicode names as different exact bytes."""
    transport = QueueTransport()

    client(transport).upload_file(
        "main", name="é", media_type="text/plain", content=b""
    )
    client(transport).upload_file(
        "main", name="e\u0301", media_type="text/plain", content=b""
    )

    assert [call.headers["X-File-Name"] for call in transport.calls] == [
        "u8.w6k",
        "u8.ZcyB",
    ]


def test_upload_accepts_the_maximum_decoded_file_name_length() -> None:
    """Encode 255 four-byte Unicode scalars within the exact wire maximum."""
    name = "🧠" * 255
    transport = QueueTransport()

    client(transport).upload_file(
        "main", name=name, media_type="text/plain", content=b""
    )

    wire_name = transport.calls[0].headers["X-File-Name"]
    assert len(wire_name) == _MAXIMUM_FILE_NAME_WIRE_LENGTH
    assert wire_name.startswith("u8.")
    assert wire_name.isascii()


def test_workspace_delete_can_send_bounded_impact_confirmation() -> None:
    """Send the optional cascading-delete confirmation only when selected."""
    transport = QueueTransport(
        [response(204, b""), response(204, b""), response(204, b"")]
    )
    subject = client(transport)

    subject.delete_workspace("main")
    subject.delete_workspace("main", impact_confirmation="Delete workspace main")
    subject.delete_workspace("main", impact_confirmation="x" * 1000)
    request_count = len(transport.calls)

    assert "X-Impact-Confirmation" not in transport.calls[0].headers
    assert transport.calls[1].headers["X-Impact-Confirmation"] == (
        "Delete workspace main"
    )
    assert transport.calls[2].headers["X-Impact-Confirmation"] == "x" * 1000
    with pytest.raises(ValueError, match="header value"):
        subject.delete_workspace("main", impact_confirmation="unsafe\nvalue")
    with pytest.raises(ValueError, match="too long"):
        subject.delete_workspace("main", impact_confirmation="x" * 1001)
    assert len(transport.calls) == request_count


def test_json_and_yaml_ontology_documents_use_exact_media_types() -> None:
    """Send canonical JSON and caller-owned UTF-8 safe YAML bodies."""
    transport = QueueTransport()
    subject = client(transport)

    subject.validate_ontology({"z": 2, "a": 1})
    subject.apply_ontology('documentVersion: "1"\n', media_type="application/yaml")

    assert transport.calls[0].body == b'{"a":1,"z":2}'
    assert transport.calls[0].headers["Content-Type"] == "application/json"
    assert transport.calls[1].body == b'documentVersion: "1"\n'
    assert transport.calls[1].headers["Content-Type"] == "application/yaml"


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, OntologyAuthenticationError),
        (403, OntologyAuthorizationError),
        (404, OntologyNotFoundError),
        (409, OntologyConflictError),
        (422, OntologyValidationError),
        (503, OntologyUnavailableError),
    ],
)
def test_http_errors_have_stable_typed_fields(
    status: int, error_type: type[Exception]
) -> None:
    """Map public status groups without changing server error fields."""
    body: JsonObject = {
        "code": "not_found",
        "message": "No visible resource.",
        "field": "objectKey",
        "retryable": False,
        "details": {"action": "Check the key."},
    }
    subject = client(QueueTransport([response(status, body)]))

    with pytest.raises(error_type) as captured:
        subject.get_object("main", "missing")

    error = cast("OntologyNotFoundError", captured.value)
    assert error.status == status
    assert error.code == "not_found"
    assert error.field == "objectKey"
    assert not error.retryable
    assert error.details == {"action": "Check the key."}


def test_service_key_is_redacted_from_all_client_created_text() -> None:
    """Keep a key out of repr, transport failures, and public HTTP errors."""
    subject = client(FailingTransport())
    assert _CREDENTIAL not in repr(subject)
    with pytest.raises(OntologyTransportError) as transport_error:
        subject.get_service()
    assert _CREDENTIAL not in str(transport_error.value)
    assert "[REDACTED]" in str(transport_error.value)

    unsafe_error: JsonObject = {
        "code": f"bad-{_CREDENTIAL}",
        "message": f"Do not show {_CREDENTIAL}",
        "field": f"field-{_CREDENTIAL}",
        "retryable": False,
        "details": {f"key-{_CREDENTIAL}": [f"value-{_CREDENTIAL}"]},
    }
    unsafe_subject = client(QueueTransport([response(400, unsafe_error)]))
    with pytest.raises(OntologyValidationError) as http_error:
        unsafe_subject.get_service()
    rendered = repr(http_error.value.__dict__) + str(http_error.value)
    assert _CREDENTIAL not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"code":"bad"}'],
)
def test_malformed_error_payloads_raise_protocol_errors(body: bytes) -> None:
    """Reject an error response that cannot supply stable public fields."""
    subject = client(
        QueueTransport(
            [OntologyTransportResponse(400, {"Content-Type": "application/json"}, body)]
        )
    )
    with pytest.raises(OntologyProtocolError):
        subject.get_service()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"value":NaN}',
        b'{"value":1,"value":2}',
        b'{"value":"\\ud800"}',
    ],
)
def test_malformed_success_payloads_raise_protocol_errors(body: bytes) -> None:
    """Reject a successful JSON response that is not a public object."""
    subject = client(
        QueueTransport(
            [OntologyTransportResponse(200, {"Content-Type": "application/json"}, body)]
        )
    )
    with pytest.raises(OntologyProtocolError):
        subject.get_service()


def test_json_response_content_type_is_required_and_case_insensitive() -> None:
    """Accept JSON parameters and reject a response with another media type."""
    valid = OntologyTransportResponse(
        200, {"content-type": "application/json; charset=utf-8"}, b"{}"
    )
    assert client(QueueTransport([valid])).get_service() == {}

    invalid = OntologyTransportResponse(200, {"Content-Type": "text/plain"}, b"{}")
    with pytest.raises(OntologyProtocolError, match="content type"):
        client(QueueTransport([invalid])).get_service()


def test_error_response_bounds_and_shape_are_enforced() -> None:
    """Reject an error body that can create an unbounded or ambiguous error."""
    too_large = OntologyTransportResponse(
        400,
        {"Content-Type": "application/json"},
        b"x" * 1_048_577,
    )
    with pytest.raises(OntologyProtocolError, match="safety bound"):
        client(QueueTransport([too_large])).get_service()

    invalid_details: JsonObject = {
        "code": "invalid_request",
        "message": "Invalid request.",
        "retryable": False,
        "details": {str(index): index for index in range(21)},
    }
    with pytest.raises(OntologyProtocolError, match="public contract"):
        client(QueueTransport([response(400, invalid_details)])).get_service()

    extra_field: JsonObject = {
        "code": "invalid_request",
        "message": "Invalid request.",
        "retryable": False,
        "details": {},
        "internal": "hidden",
    }
    with pytest.raises(OntologyProtocolError, match="public contract"):
        client(QueueTransport([response(400, extra_field)])).get_service()


@pytest.mark.parametrize(
    "invalid",
    [
        cast("OntologyTransportResponse", object()),
        OntologyTransportResponse(cast("int", "200"), {}, b"{}"),
        OntologyTransportResponse(
            status=cast("int", True),  # noqa: FBT003 - Protocol validation fixture.
            headers={},
            body=b"{}",
        ),
        OntologyTransportResponse(99, {}, b"{}"),
        OntologyTransportResponse(200, cast("Mapping[str, str]", []), b"{}"),
        OntologyTransportResponse(200, cast("Mapping[str, str]", {1: "value"}), b"{}"),
        OntologyTransportResponse(200, {}, cast("bytes", bytearray())),
    ],
)
def test_transport_response_shape_is_validated(
    invalid: OntologyTransportResponse,
) -> None:
    """Reject a transport result that cannot be one complete HTTP response."""
    transport = QueueTransport()
    transport.responses.append(invalid)
    with pytest.raises(OntologyProtocolError, match="invalid response"):
        client(transport).get_service()


def test_transport_preserves_client_errors_and_rejects_bounded_headers() -> None:
    """Preserve typed client errors and map shared header failures."""

    class TypedFailingTransport(QueueTransport):
        def request(
            self,
            method: str,
            url: str,
            headers: Mapping[str, str],
            body: bytes | None,
            timeout: float,
        ) -> OntologyTransportResponse:
            del method, url, headers, body, timeout
            msg = "direct protocol failure"
            raise OntologyProtocolError(msg)

    with pytest.raises(OntologyProtocolError, match="direct protocol"):
        client(TypedFailingTransport()).get_service()
    responses = (
        OntologyTransportResponse(
            200,
            {f"X-{index}": "x" for index in range(101)},
            b"{}",
        ),
        OntologyTransportResponse(200, {"X-Test": "bad\rvalue"}, b"{}"),
    )
    with pytest.raises(OntologyResponseLimitError):
        client(QueueTransport([responses[0]])).get_service()
    with pytest.raises(OntologyProtocolError, match="headers"):
        client(QueueTransport([responses[1]])).get_service()


def test_cursor_iteration_preserves_request_and_fails_at_caller_bound() -> None:
    """Change only the cursor and fail when more pages exceed the bound."""
    transport = QueueTransport(
        [
            response(200, {"items": [{"key": "a"}], "nextCursor": "v1.second"}),
            response(200, {"items": [{"key": "b"}], "nextCursor": "v1.third"}),
        ]
    )
    subject = client(transport)
    request: JsonObject = {
        "kind": "object",
        "filter": {"labels": ["article:1"]},
        "sort": [],
        "limit": 17,
    }

    with pytest.raises(OntologyPageLimitError):
        list(subject.iter_query_pages("main", request, max_pages=2))

    first = cast("JsonObject", json.loads(transport.calls[0].body or b""))
    second = cast("JsonObject", json.loads(transport.calls[1].body or b""))
    assert first == request
    assert second == {**request, "cursor": "v1.second"}
    assert request.get("cursor") is None


@pytest.mark.parametrize("cursor", [None, ["invalid"]])
def test_cursor_iteration_preserves_an_invalid_initial_cursor(
    cursor: JsonValue,
) -> None:
    """Let the server return its public error for an invalid request cursor."""
    transport = QueueTransport(
        [response(200, {"items": []}), response(200, {"objects": [], "links": []})]
    )
    subject = client(transport)
    query: JsonObject = {"kind": "object", "filter": {}, "sort": [], "cursor": cursor}
    graph: JsonObject = {
        "startObjects": ["a"],
        "depth": 1,
        "limit": 4,
        "cursor": cursor,
    }

    list(subject.iter_query_pages("main", query, max_pages=1))
    list(subject.iter_graph_pages("main", graph, max_pages=1))

    assert json.loads(transport.calls[0].body or b"")["cursor"] == cursor
    assert json.loads(transport.calls[1].body or b"")["cursor"] == cursor


def test_cursor_iteration_detects_a_cycle_to_the_initial_cursor() -> None:
    """Reject a next cursor that returns to the caller-supplied cursor."""
    transport = QueueTransport(
        [response(200, {"items": [], "nextCursor": "v1.initial"})]
    )
    subject = client(transport)
    request: JsonObject = {
        "kind": "object",
        "filter": {},
        "sort": [],
        "cursor": "v1.initial",
    }

    pages = subject.iter_query_pages("main", request, max_pages=2)
    with pytest.raises(OntologyProtocolError, match="cycle"):
        next(pages)


def test_graph_iteration_replaces_only_the_next_page_cursor() -> None:
    """Keep graph fields and replace only the cursor after the first page."""
    transport = QueueTransport(
        [
            response(
                200,
                {
                    "objects": [],
                    "links": [],
                    "truncated": True,
                    "nextCursor": "v1.second",
                },
            ),
            response(200, {"objects": [], "links": [], "truncated": False}),
        ]
    )
    subject = client(transport)
    request: JsonObject = {
        "startObjects": ["a"],
        "depth": 1,
        "limit": 4,
        "cursor": "v1.first",
    }

    pages = list(subject.iter_graph_pages("main", request, max_pages=2))
    assert [page["truncated"] for page in pages] == [True, False]
    assert json.loads(transport.calls[0].body or b"")["cursor"] == "v1.first"
    assert json.loads(transport.calls[1].body or b"")["cursor"] == "v1.second"


def test_cursor_iteration_rejects_cycles_and_malformed_pages() -> None:
    """Reject repeated cursors and missing page item arrays."""
    cycle = client(
        QueueTransport(
            [
                response(200, {"items": [], "nextCursor": "v1.same"}),
                response(200, {"items": [], "nextCursor": "v1.same"}),
            ]
        )
    )
    with pytest.raises(OntologyProtocolError, match="cycle"):
        list(cycle.iter_workspace_pages(max_pages=3))

    malformed = client(QueueTransport([response(200, {"nextCursor": "v1.next"})]))
    with pytest.raises(OntologyProtocolError, match="items"):
        list(malformed.iter_workspace_pages(max_pages=1))


@pytest.mark.parametrize("cursor", ["", 42])
def test_cursor_iteration_rejects_invalid_next_cursors(cursor: object) -> None:
    """Require one non-empty string when a page has a next cursor."""
    page = cast("JsonObject", {"items": [], "nextCursor": cursor})
    subject = client(QueueTransport([response(200, page)]))
    with pytest.raises(OntologyProtocolError, match="invalid next cursor"):
        list(subject.iter_workspace_pages(max_pages=1))


def test_all_cursor_helpers_return_terminal_pages() -> None:
    """Cover each bounded list, graph, query, and changed-page helper."""
    transport = QueueTransport(
        [
            response(200, {"items": []}),
            response(200, {"items": []}),
            response(200, {"objects": [], "links": [], "truncated": False}),
            response(200, {"objects": [], "links": [], "truncated": False}),
            response(200, {"objects": [], "links": [], "truncated": False}),
            response(200, {"items": []}),
        ]
    )
    subject = client(transport)

    assert len(list(subject.iter_metadata_bag_pages("main", max_pages=1))) == 1
    assert len(list(subject.iter_saved_view_pages("main", max_pages=1))) == 1
    assert (
        len(list(subject.iter_saved_view_graph_pages("main", "view-1", max_pages=1)))
        == 1
    )
    graph: JsonObject = {
        "startObjects": ["a"],
        "depth": 1,
        "limit": 4,
        "cursor": "v1.initial",
    }
    assert len(list(subject.iter_graph_pages("main", graph, max_pages=1))) == 1
    graph.pop("cursor")
    assert len(list(subject.iter_graph_pages("main", graph, max_pages=1))) == 1
    assert (
        len(
            list(
                subject.iter_changed_since_pages(
                    "main", since="2026-08-01T00:00:00Z", max_pages=1
                )
            )
        )
        == 1
    )
    assert b'"cursor":"v1.initial"' in (transport.calls[3].body or b"")
    assert b'"cursor"' not in (transport.calls[4].body or b"")


def test_cursor_helpers_reject_invalid_page_bounds() -> None:
    """Reject zero, negative, and Boolean local page bounds."""
    subject = client(QueueTransport())
    for maximum in (0, -1, True):
        with pytest.raises(ValueError, match="page bound"):
            list(subject.iter_workspace_pages(max_pages=maximum))


def test_empty_delete_response_returns_no_value() -> None:
    """Return None for the OpenAPI 204 deletion response."""
    subject = client(QueueTransport([response(204, b"")]))
    assert subject.delete_workspace("main") is None


def test_value_fingerprint_uses_rfc_8785_canonical_json() -> None:
    """Use exact object order, number form, digest length, and Base64URL form."""
    value: JsonObject = {
        "b": 1,
        "a": [True, None, 1e-6, 1e20, 1e-7, -0.0],
    }
    assert canonical_json_bytes(value) == (
        b'{"a":[true,null,0.000001,100000000000000000000,1e-7,0],"b":1}'
    )
    assert value_fingerprint("Ada") == "v1:o5r-7X0zGSE756I1"
    assert value_occurrence_selector("Ada") == {"value": "Ada"}
    assert value_occurrence_selector("Ada", bag_id="source-1") == {
        "value": "Ada",
        "bagId": "source-1",
    }
    assert fingerprint_occurrence_selector("Ada") == {
        "fingerprint": "v1:o5r-7X0zGSE756I1"
    }
    assert fingerprint_occurrence_selector("Ada", bag_id="source-1") == {
        "fingerprint": "v1:o5r-7X0zGSE756I1",
        "bagId": "source-1",
    }


def test_canonical_json_rejects_values_outside_the_public_model() -> None:
    """Reject unsafe integers, strings, mapping keys, numbers, and objects."""
    with pytest.raises(ValueError, match="integer"):
        canonical_json_bytes(9_007_199_254_740_992)
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes(float("inf"))
    with pytest.raises(ValueError, match="Unicode"):
        canonical_json_bytes("\ud800")
    with pytest.raises(TypeError, match="mapping key"):
        canonical_json_bytes(cast("JsonValue", {1: "bad"}))
    with pytest.raises(ValueError, match="JSON-equivalent"):
        canonical_json_bytes(cast("JsonValue", object()))


def test_canonical_numbers_cover_plain_and_scientific_forms() -> None:
    """Use ECMAScript plain and scientific number boundaries."""
    assert canonical_json_bytes(-12.5) == b"-12.5"
    assert canonical_json_bytes(1.25e-5) == b"0.0000125"
    assert canonical_json_bytes(1.25e30) == b"1.25e+30"


def test_ontology_body_rejects_wrong_media_shapes() -> None:
    """Keep JSON canonical and YAML limited to caller-owned UTF-8 text."""
    subject = client(QueueTransport())
    with pytest.raises(ValueError, match="JSON ontology"):
        subject.apply_ontology("{}")
    subject.apply_ontology(b"title: test\n", media_type="application/yaml")
    with pytest.raises(ValueError, match="YAML ontology"):
        subject.apply_ontology({}, media_type="application/yaml")
    with pytest.raises(ValueError, match="media type"):
        subject.apply_ontology(
            {},
            media_type=cast("object", "text/plain"),  # type: ignore[arg-type]
        )


def test_unclassified_http_status_keeps_the_base_error_type() -> None:
    """Keep a stable base error for a valid unexpected public status."""
    body: JsonObject = {
        "code": "invalid_request",
        "message": "Invalid request.",
        "retryable": False,
        "details": {"number": 1},
    }
    subject = client(QueueTransport([response(418, body)]))
    with pytest.raises(ontology_module.OntologyHTTPError) as captured:
        subject.get_service()
    assert type(captured.value) is ontology_module.OntologyHTTPError


class DefaultURLResponse:
    """Act as one standard-library URL response context manager."""

    status = 200
    headers = Message()
    headers["Content-Type"] = "application/json"

    def __enter__(self) -> Self:
        """Return this response."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the response without suppressing an error."""

    def read(self, amount: int | None = None) -> bytes:
        """Return one public JSON object."""
        body = b'{"apiName":"xbot"}'
        return body if amount is None else body[:amount]


class BoundedURLResponse:
    """Record the standard-library successful response read bound."""

    status = 200

    def __init__(self, body: bytes) -> None:
        """Store one binary response body."""
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = "application/octet-stream"
        self.read_amounts: list[int | None] = []

    def __enter__(self) -> Self:
        """Return this response."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the response without suppressing an error."""

    def read(self, amount: int | None = None) -> bytes:
        """Return no more than the requested number of bytes."""
        self.read_amounts.append(amount)
        return self.body if amount is None else self.body[:amount]


class CallableOpener:
    """Adapt one test callable to the standard-library opener interface."""

    def __init__(self, callback: Callable[..., object]) -> None:
        """Store the test callback."""
        self._callback = callback

    def open(self, *args: object, **kwargs: object) -> object:
        """Call the configured response function."""
        return self._callback(*args, **kwargs)


def replace_default_opener(
    subject: OntologyClient, callback: Callable[..., object]
) -> None:
    """Replace the shared private opener in one default client transport."""
    transport = object.__getattribute__(subject, "_transport")
    http = cast("UrllibHttpClient", object.__getattribute__(transport, "_http"))
    http.replace_opener(CallableOpener(callback))


def test_default_standard_library_transport_handles_success_and_http_error() -> None:
    """Use the dependency-free default transport for complete HTTP responses."""
    subject = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
    )

    def succeed(*_args: object, **_kwargs: object) -> DefaultURLResponse:
        return DefaultURLResponse()

    replace_default_opener(subject, succeed)
    assert subject.get_service() == {"apiName": "xbot"}

    error_body = json.dumps(
        {
            "code": "invalid_request",
            "message": "Invalid request.",
            "retryable": False,
            "details": {},
        }
    ).encode()
    http_errors: list[HTTPError] = []

    def fail(*_args: object, **_kwargs: object) -> object:
        url = "http://localhost:8000"
        headers = Message()
        headers["Content-Type"] = "application/json"
        error = HTTPError(url, 400, "bad", headers, BytesIO(error_body))
        http_errors.append(error)
        raise error

    replace_default_opener(subject, fail)
    with pytest.raises(OntologyValidationError):
        subject.get_service()
    assert http_errors[0].closed


def test_success_response_bound_accepts_exact_custom_transport_body() -> None:
    """Accept a custom transport response at the exact caller byte bound."""
    transport = QueueTransport([response(200, b"exact")])
    subject = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
        maximum_success_response_bytes=5,
        transport=transport,
    )

    assert subject.download_file("main", "file-1") == b"exact"


def test_success_response_bound_rejects_custom_transport_overage() -> None:
    """Reject an over-limit custom transport body without exposing its bytes."""
    secret_body = _CREDENTIAL.encode()
    transport = QueueTransport([response(200, secret_body)])
    subject = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
        maximum_success_response_bytes=len(secret_body) - 1,
        transport=transport,
    )

    with pytest.raises(OntologyResponseLimitError) as captured:
        subject.download_file("main", "file-1")
    assert _CREDENTIAL not in str(captured.value)


def test_default_transport_caps_exact_and_over_limit_success_reads() -> None:
    """Read at most one byte beyond the finite successful-response bound."""
    exact = BoundedURLResponse(b"abc")
    over = BoundedURLResponse(b"abcd")
    responses = iter((exact, over))

    def succeed(*_args: object, **_kwargs: object) -> BoundedURLResponse:
        return next(responses)

    subject = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
        maximum_success_response_bytes=3,
    )
    replace_default_opener(subject, succeed)

    assert subject.download_file("main", "file-1") == b"abc"
    with pytest.raises(OntologyResponseLimitError):
        subject.download_file("main", "file-1")
    assert exact.read_amounts == [4]
    assert over.read_amounts == [4]


def test_default_success_response_bound_accepts_the_managed_file_maximum() -> None:
    """Keep the default response bound above the accepted file maximum."""
    subject = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
        transport=QueueTransport(),
    )

    assert subject.maximum_success_response_bytes >= _ACCEPTED_MAXIMUM_FILE_BYTES


def test_default_transport_wraps_url_errors() -> None:
    """Redact a URL transport failure and return one stable client error."""

    def fail(*_args: object, **_kwargs: object) -> object:
        reason = "offline"
        raise URLError(reason)

    subject = OntologyClient(
        base_url="http://localhost:8000",
        service_api_name="xbot",
        service_key=_CREDENTIAL,
    )
    replace_default_opener(subject, fail)
    with pytest.raises(OntologyTransportError, match="offline"):
        subject.get_service()


def test_default_transport_does_not_forward_a_key_to_a_redirect() -> None:
    """Reject a cross-host redirect before it can receive Authorization."""
    received_authorization: list[str | None] = []

    class TargetHandler(BaseHTTPRequestHandler):
        """Record an unexpected redirected request."""

        def do_GET(self) -> None:
            """Record the key header if the client follows the redirect."""
            received_authorization.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(
            self,
            format: str,  # noqa: A002 - Standard-library override name.
            *args: object,
        ) -> None:
            """Keep the local regression test quiet."""
            del format, args

    target = HTTPServer(("127.0.0.1", 0), TargetHandler)
    target.timeout = 0.2

    class RedirectHandler(BaseHTTPRequestHandler):
        """Redirect one request to the second loopback origin."""

        def do_GET(self) -> None:
            """Return one redirect to the target server."""
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{target.server_port}/target"
            )
            self.end_headers()

        def log_message(
            self,
            format: str,  # noqa: A002 - Standard-library override name.
            *args: object,
        ) -> None:
            """Keep the local regression test quiet."""
            del format, args

    redirect = HTTPServer(("127.0.0.1", 0), RedirectHandler)
    redirect.timeout = 0.2
    redirect_thread = threading.Thread(target=redirect.handle_request)
    target_thread = threading.Thread(target=target.handle_request)
    redirect_thread.start()
    target_thread.start()
    try:
        subject = OntologyClient(
            base_url=f"http://127.0.0.1:{redirect.server_port}",
            service_api_name="xbot",
            service_key=_CREDENTIAL,
        )
        with pytest.raises(OntologyProtocolError):
            subject.get_service()
        redirect_thread.join(timeout=1)
        target_thread.join(timeout=1)
        assert not redirect_thread.is_alive()
        assert not target_thread.is_alive()
        assert received_authorization == []
    finally:
        redirect.server_close()
        target.server_close()


@pytest.mark.parametrize(
    "field", ["base_url", "service_api_name", "service_key", "timeout"]
)
def test_client_rejects_unsafe_connection_configuration(
    field: str,
) -> None:
    """Reject a connection value that can weaken the backend boundary."""
    base_url = "file:///tmp/socket" if field == "base_url" else "http://localhost:8000"
    service_api_name = "" if field == "service_api_name" else "xbot"
    service_key = "" if field == "service_key" else _CREDENTIAL
    timeout = 0.0 if field == "timeout" else 5.0
    with pytest.raises(ValueError, match="Ontology"):
        OntologyClient(
            base_url=base_url,
            service_api_name=service_api_name,
            service_key=service_key,
            timeout=timeout,
            transport=QueueTransport(),
        )


@pytest.mark.parametrize(
    "timeout",
    [
        cast("float", True),  # noqa: FBT003 - Runtime validation fixture.
        cast("float", "30"),
        float("inf"),
    ],
)
def test_client_rejects_an_invalid_timeout(timeout: float) -> None:
    """Require a real finite timeout instead of a Boolean or another value."""
    with pytest.raises(ValueError, match="timeout"):
        OntologyClient(
            base_url="http://localhost:8000",
            service_api_name="xbot",
            service_key=_CREDENTIAL,
            timeout=timeout,
            transport=QueueTransport(),
        )


@pytest.mark.parametrize(
    "bound",
    [
        0,
        -1,
        cast("int", True),  # noqa: FBT003 - Runtime validation fixture.
        cast("int", 1.5),
        cast("int", None),
    ],
)
def test_client_requires_a_finite_positive_success_response_bound(
    bound: int,
) -> None:
    """Reject an invalid byte bound and any unbounded opt-out."""
    with pytest.raises(ValueError, match="response bound"):
        OntologyClient(
            base_url="http://localhost:8000",
            service_api_name="xbot",
            service_key=_CREDENTIAL,
            maximum_success_response_bytes=bound,
            transport=QueueTransport(),
        )


def test_client_rejects_invalid_unicode_in_headers_and_paths() -> None:
    """Reject a surrogate before it can enter an HTTP request."""
    with pytest.raises(ValueError, match="Unicode"):
        OntologyClient(
            base_url="http://localhost:8000",
            service_api_name="xbot",
            service_key="invalid-\ud800",
            transport=QueueTransport(),
        )

    subject = client(QueueTransport())
    with pytest.raises(ValueError, match="Unicode"):
        subject.get_object("main", "invalid-\ud800")


@pytest.mark.parametrize(
    "base_url", ["http://localhost:not-a-port", "http://localhost:65536"]
)
def test_client_rejects_invalid_base_url_ports(base_url: str) -> None:
    """Reject malformed or out-of-range URL ports before a request."""
    with pytest.raises(ValueError, match="base URL"):
        OntologyClient(
            base_url=base_url,
            service_api_name="xbot",
            service_key=_CREDENTIAL,
            transport=QueueTransport(),
        )


@pytest.mark.parametrize(
    ("name", "media_type"),
    [
        ("", "text/plain"),
        ("bad/name", "text/plain"),
        ("bad\\name", "text/plain"),
        ("bad\x1fname", "text/plain"),
        ("bad\x80name", "text/plain"),
        ("bad\ud800name", "text/plain"),
        ("a" * 256, "text/plain"),
        ("name.txt", "Text/Plain"),
    ],
)
def test_upload_rejects_invalid_file_headers(name: str, media_type: str) -> None:
    """Reject values that do not match the exact file-header contract."""
    with pytest.raises(ValueError, match=r"file|Unicode"):
        client(QueueTransport()).upload_file(
            "main", name=name, media_type=media_type, content=b""
        )
