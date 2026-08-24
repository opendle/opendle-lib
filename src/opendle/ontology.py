"""Dependency-free dynamic client for the Ontology Service API."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, Self, cast, override
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if TYPE_CHECKING:
    from http.client import HTTPMessage

__all__ = [
    "JsonObject",
    "JsonValue",
    "OntologyAuthenticationError",
    "OntologyAuthorizationError",
    "OntologyClient",
    "OntologyConflictError",
    "OntologyError",
    "OntologyHTTPError",
    "OntologyNotFoundError",
    "OntologyPageLimitError",
    "OntologyProtocolError",
    "OntologyResponseLimitError",
    "OntologyTransport",
    "OntologyTransportError",
    "OntologyTransportResponse",
    "OntologyUnavailableError",
    "OntologyValidationError",
    "canonical_json_bytes",
    "fingerprint_occurrence_selector",
    "value_fingerprint",
    "value_occurrence_selector",
]

type JsonValue = bool | int | float | str | list[JsonValue] | JsonObject | None
type JsonObject = dict[str, JsonValue]
type WriteVisibility = Literal["eventual", "read_after_write"]
type OntologyMediaType = Literal["application/json", "application/yaml"]

_MAXIMUM_SAFE_INTEGER = 9_007_199_254_740_991
_DEFAULT_TIMEOUT_SECONDS = 30.0
_JSON_CONTENT_TYPE: Literal["application/json"] = "application/json"
_YAML_CONTENT_TYPE: Literal["application/yaml"] = "application/yaml"
_BINARY_CONTENT_TYPE = "application/octet-stream"
_CONTROL_CHARACTER_END = 0x20
_DELETE_CHARACTER = 0x7F
_HIGH_SURROGATE_START = 0xD800
_LOW_SURROGATE_END = 0xDFFF
_HTTP_SUCCESS_MINIMUM = 200
_HTTP_SUCCESS_LIMIT = 300
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HTTP_SERVER_ERROR_MINIMUM = 500
_HTTP_STATUS_MINIMUM = 100
_HTTP_STATUS_MAXIMUM = 599
_HTTP_VALIDATION_STATUSES = frozenset({400, 413, 415, 422})
_MAXIMUM_ERROR_BODY_BYTES = 1_048_576
_MAXIMUM_ERROR_DETAILS = 20
_MAXIMUM_ERROR_FIELD_LENGTH = 200
_MAXIMUM_ERROR_MESSAGE_LENGTH = 500
_DEFAULT_MAXIMUM_SUCCESS_RESPONSE_BYTES = 16_777_216
_PLAIN_DECIMAL_MINIMUM = 1e-6
_PLAIN_DECIMAL_LIMIT = 1e21
_MAXIMUM_PORT = 65_535
_MAXIMUM_FILE_NAME_LENGTH = 255
_MAXIMUM_MEDIA_TYPE_LENGTH = 200
_MAXIMUM_IMPACT_CONFIRMATION_LENGTH = 1000
_C1_CONTROL_CHARACTER_END = 0x9F
_MEDIA_TYPE_PATTERN = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")


@dataclass(frozen=True, slots=True)
class OntologyTransportResponse:
    """Contain one complete response from an Ontology HTTP transport."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class OntologyTransport(Protocol):
    """Send one complete bounded Ontology HTTP request."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OntologyTransportResponse:
        """Send one request and return its status, headers, and body."""
        ...


class _URLResponse(Protocol):
    """Supply the standard-library response operations that the client uses."""

    status: int
    headers: Mapping[str, str]

    def __enter__(self) -> Self:
        """Enter the response context."""
        ...

    def __exit__(self, *args: object) -> None:
        """Exit the response context."""
        ...

    def read(self, amount: int | None = None) -> bytes:
        """Read all bytes or at most the selected amount."""
        ...


class OntologyError(RuntimeError):
    """Report a safe failure from the dynamic Ontology client."""


class OntologyTransportError(OntologyError):
    """Report a failure before a complete HTTP response is available."""


class OntologyProtocolError(OntologyError):
    """Report a response that does not match the Ontology HTTP contract."""


class OntologyPageLimitError(OntologyProtocolError):
    """Report that a caller page bound stopped cursor iteration."""


class OntologyResponseLimitError(OntologyProtocolError):
    """Report that a successful response exceeded the caller byte bound."""


class OntologyHTTPError(OntologyError):
    """Report one stable public Ontology HTTP error.

    Attributes:
        status: The HTTP response status.
        code: The stable public error code.
        retryable: Whether the server says that a later retry can succeed.
        field: The optional public input field.
        details: Bounded safe corrective data from the server.

    """

    def __init__(  # noqa: PLR0913 - Public errors have five stable fields.
        self,
        *,
        status: int,
        code: str,
        message: str,
        retryable: bool,
        field: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Initialize one public HTTP error."""
        self.status = status
        self.code = code
        self.retryable = retryable
        self.field = field
        self.details: JsonObject = dict(details or {})
        super().__init__(message)


class OntologyAuthenticationError(OntologyHTTPError):
    """Report failed service-key authentication."""


class OntologyAuthorizationError(OntologyHTTPError):
    """Report a forbidden authenticated request."""


class OntologyNotFoundError(OntologyHTTPError):
    """Report an absent, hidden, or expired resource."""


class OntologyConflictError(OntologyHTTPError):
    """Report a request that conflicts with current state."""


class OntologyValidationError(OntologyHTTPError):
    """Report an invalid request, value, cursor, or file."""


class OntologyUnavailableError(OntologyHTTPError):
    """Report a capability or service that is not available."""


class _UrllibTransport:
    """Send requests through the Python standard library."""

    __slots__ = ("_maximum_success_response_bytes",)

    def __init__(self, maximum_success_response_bytes: int) -> None:
        self._maximum_success_response_bytes = maximum_success_response_bytes

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> OntologyTransportResponse:
        request = Request(  # noqa: S310 - The client validates the URL scheme.
            url, data=body, headers=dict(headers), method=method
        )
        try:
            with _open_without_redirects(request, timeout=timeout) as response:
                return OntologyTransportResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(self._maximum_success_response_bytes + 1),
                )
        except HTTPError as error:
            try:
                return OntologyTransportResponse(
                    status=error.code,
                    headers=dict(error.headers.items()) if error.headers else {},
                    body=error.read(_MAXIMUM_ERROR_BODY_BYTES + 1),
                )
            finally:
                error.close()
        except URLError:
            raise


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Reject redirects before they can forward a service key."""

    @override
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        """Reject every redirect response."""
        del req, fp, code, msg, headers, newurl


def _open_without_redirects(request: Request, *, timeout: float) -> _URLResponse:
    opener = build_opener(_RejectRedirectHandler())
    return cast("_URLResponse", opener.open(request, timeout=timeout))


class OntologyClient:
    """Call one exact Ontology service with one backend-only service key."""

    __slots__ = (
        "_base_url",
        "_maximum_success_response_bytes",
        "_service_api_name",
        "_service_key",
        "_timeout",
        "_transport",
    )

    def __init__(  # noqa: PLR0913 - Connection safety has fixed controls.
        self,
        *,
        base_url: str,
        service_api_name: str,
        service_key: str,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        maximum_success_response_bytes: int = (_DEFAULT_MAXIMUM_SUCCESS_RESPONSE_BYTES),
        transport: OntologyTransport | None = None,
    ) -> None:
        """Initialize a client with an explicit service scope.

        The client stores the key only for backend Authorization headers. Its
        representation and all client-created exceptions omit or redact it.
        """
        self._base_url = _validate_base_url(base_url)
        self._service_api_name = _required_segment(
            service_api_name, name="service API name"
        )
        _valid_unicode(service_key)
        if (
            not service_key
            or service_key.strip() != service_key
            or any(
                ord(character) < _CONTROL_CHARACTER_END
                or ord(character) == _DELETE_CHARACTER
                for character in service_key
            )
        ):
            msg = "The Ontology service key is invalid."
            raise ValueError(msg)
        timeout_value = cast("object", timeout)
        if (
            isinstance(timeout_value, bool)
            or not isinstance(timeout_value, (int, float))
            or not math.isfinite(timeout_value)
            or timeout_value <= 0
        ):
            msg = "The Ontology request timeout must be finite and positive."
            raise ValueError(msg)
        self._service_key = service_key
        self._timeout = float(timeout_value)
        response_bound = cast("object", maximum_success_response_bytes)
        if (
            not isinstance(response_bound, int)
            or isinstance(response_bound, bool)
            or response_bound < 1
        ):
            msg = "The Ontology success response bound must be a positive integer."
            raise ValueError(msg)
        self._maximum_success_response_bytes = response_bound
        self._transport = transport or _UrllibTransport(response_bound)

    @property
    def service_api_name(self) -> str:
        """Return the exact service scope without exposing its key."""
        return self._service_api_name

    @property
    def maximum_success_response_bytes(self) -> int:
        """Return the finite successful-response byte bound."""
        return self._maximum_success_response_bytes

    def __repr__(self) -> str:
        """Return a representation that does not contain the service key."""
        return (
            f"{type(self).__name__}(base_url={self._base_url!r}, "
            f"service_api_name={self._service_api_name!r}, "
            f"timeout={self._timeout!r}, "
            "maximum_success_response_bytes="
            f"{self._maximum_success_response_bytes!r})"
        )

    def get_service(self) -> JsonObject:
        """Read the current service record."""
        return self._json("GET", self._service_path())

    def get_ontology(self) -> JsonObject:
        """Read the canonical local mutable ontology."""
        return self._json("GET", self._service_path("ontology"))

    def apply_ontology(
        self,
        document: Mapping[str, JsonValue] | str | bytes,
        *,
        visibility: WriteVisibility = "eventual",
        media_type: OntologyMediaType = _JSON_CONTENT_TYPE,
    ) -> JsonObject:
        """Validate and apply one complete JSON or safe YAML ontology."""
        body = _ontology_body(document, media_type)
        return self._json(
            "PUT",
            self._service_path("ontology"),
            query={"visibility": visibility},
            body=body,
            content_type=media_type,
        )

    def get_effective_ontology(self) -> JsonObject:
        """Read the canonical effective ontology with inherited definitions."""
        return self._json("GET", self._service_path("ontology", "effective"))

    def validate_ontology(
        self,
        document: Mapping[str, JsonValue] | str | bytes,
        *,
        media_type: OntologyMediaType = _JSON_CONTENT_TYPE,
    ) -> JsonObject:
        """Validate one complete JSON or safe YAML ontology without applying it."""
        return self._json(
            "POST",
            self._service_path("ontology", "validations"),
            body=_ontology_body(document, media_type),
            content_type=media_type,
        )

    def create_ontology_migration(
        self,
        migration: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Create one compatible ontology data migration."""
        return self._json_body(
            "POST",
            self._service_path("ontology", "migrations"),
            migration,
            query={"visibility": visibility},
        )

    def list_workspaces(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> JsonObject:
        """Read one bounded workspace page."""
        return self._json(
            "GET",
            self._service_path("workspaces"),
            query={"cursor": cursor, "limit": limit},
        )

    def iter_workspace_pages(
        self, *, limit: int = 50, max_pages: int
    ) -> Iterator[JsonObject]:
        """Iterate workspace pages under one strict caller page bound."""
        return self._iter_pages(
            lambda cursor: self.list_workspaces(cursor=cursor, limit=limit),
            max_pages=max_pages,
            item_fields=("items",),
        )

    def create_workspace(self, workspace: Mapping[str, JsonValue]) -> JsonObject:
        """Create one workspace in this service."""
        return self._json_body("POST", self._service_path("workspaces"), workspace)

    def get_workspace(self, workspace_api_name: str) -> JsonObject:
        """Read one workspace in this service."""
        return self._json("GET", self._workspace_path(workspace_api_name))

    def update_workspace(
        self, workspace_api_name: str, update: Mapping[str, JsonValue]
    ) -> JsonObject:
        """Apply a last-write-wins workspace update."""
        return self._json_body(
            "PATCH", self._workspace_path(workspace_api_name), update
        )

    def delete_workspace(
        self,
        workspace_api_name: str,
        *,
        impact_confirmation: str | None = None,
    ) -> JsonObject | None:
        """Delete one workspace and its dependent resources."""
        headers = (
            {"X-Impact-Confirmation": _impact_confirmation_header(impact_confirmation)}
            if impact_confirmation is not None
            else None
        )
        return self._optional_json(
            "DELETE",
            self._workspace_path(workspace_api_name),
            extra_headers=headers,
        )

    def create_object(
        self,
        workspace_api_name: str,
        value: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Create one object in an explicit workspace."""
        return self._json_body(
            "POST",
            self._workspace_path(workspace_api_name, "objects"),
            value,
            query={"visibility": visibility},
        )

    def get_object(
        self,
        workspace_api_name: str,
        object_key: str,
        *,
        expand: Sequence[Literal["timestamps", "bags"]] = (),
    ) -> JsonObject:
        """Read one current object from an explicit workspace."""
        return self._json(
            "GET",
            self._workspace_path(workspace_api_name, "objects", object_key),
            query={"expand": expand},
        )

    def update_object(
        self,
        workspace_api_name: str,
        object_key: str,
        patch: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Apply a last-write-wins object patch."""
        return self._json_body(
            "PATCH",
            self._workspace_path(workspace_api_name, "objects", object_key),
            patch,
            query={"visibility": visibility},
        )

    def delete_object(
        self,
        workspace_api_name: str,
        object_key: str,
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject | None:
        """Delete one object and its dependent links."""
        return self._optional_json(
            "DELETE",
            self._workspace_path(workspace_api_name, "objects", object_key),
            query={"visibility": visibility},
        )

    def create_link(
        self,
        workspace_api_name: str,
        value: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Create or merge one link in an explicit workspace."""
        return self._json_body(
            "POST",
            self._workspace_path(workspace_api_name, "links"),
            value,
            query={"visibility": visibility},
        )

    def get_link(
        self,
        workspace_api_name: str,
        link_key: str,
        *,
        expand: Sequence[Literal["timestamps", "bags"]] = (),
    ) -> JsonObject:
        """Read one current link from an explicit workspace."""
        return self._json(
            "GET",
            self._workspace_path(workspace_api_name, "links", link_key),
            query={"expand": expand},
        )

    def update_link(
        self,
        workspace_api_name: str,
        link_key: str,
        patch: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Apply a last-write-wins link patch."""
        return self._json_body(
            "PATCH",
            self._workspace_path(workspace_api_name, "links", link_key),
            patch,
            query={"visibility": visibility},
        )

    def delete_link(
        self,
        workspace_api_name: str,
        link_key: str,
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject | None:
        """Delete one link from an explicit workspace."""
        return self._optional_json(
            "DELETE",
            self._workspace_path(workspace_api_name, "links", link_key),
            query={"visibility": visibility},
        )

    def list_metadata_bags(
        self,
        workspace_api_name: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JsonObject:
        """Read one bounded metadata-bag page."""
        return self._json(
            "GET",
            self._workspace_path(workspace_api_name, "bags"),
            query={"cursor": cursor, "limit": limit},
        )

    def iter_metadata_bag_pages(
        self,
        workspace_api_name: str,
        *,
        limit: int = 50,
        max_pages: int,
    ) -> Iterator[JsonObject]:
        """Iterate metadata-bag pages under one strict caller page bound."""
        return self._iter_pages(
            lambda cursor: self.list_metadata_bags(
                workspace_api_name, cursor=cursor, limit=limit
            ),
            max_pages=max_pages,
            item_fields=("items",),
        )

    def create_metadata_bag(
        self,
        workspace_api_name: str,
        value: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Create one metadata bag in an explicit workspace."""
        return self._json_body(
            "POST",
            self._workspace_path(workspace_api_name, "bags"),
            value,
            query={"visibility": visibility},
        )

    def get_metadata_bag(self, workspace_api_name: str, bag_id: str) -> JsonObject:
        """Read one metadata bag from an explicit workspace."""
        return self._json(
            "GET", self._workspace_path(workspace_api_name, "bags", bag_id)
        )

    def update_metadata_bag(
        self,
        workspace_api_name: str,
        bag_id: str,
        update: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Apply a last-write-wins metadata-bag update."""
        return self._json_body(
            "PATCH",
            self._workspace_path(workspace_api_name, "bags", bag_id),
            update,
            query={"visibility": visibility},
        )

    def delete_metadata_bag(
        self,
        workspace_api_name: str,
        bag_id: str,
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject | None:
        """Delete one unreferenced metadata bag."""
        return self._optional_json(
            "DELETE",
            self._workspace_path(workspace_api_name, "bags", bag_id),
            query={"visibility": visibility},
        )

    def upload_file(
        self,
        workspace_api_name: str,
        *,
        name: str,
        media_type: str,
        content: bytes,
    ) -> JsonObject:
        """Upload exact managed-file bytes with calculated integrity headers."""
        file_name = _file_name_header(name)
        file_media_type = _media_type_header(media_type)
        digest = hashlib.sha256(content).hexdigest()
        return self._json(
            "POST",
            self._workspace_path(workspace_api_name, "files"),
            body=content,
            content_type=_BINARY_CONTENT_TYPE,
            extra_headers={
                "X-File-Name": file_name,
                "X-File-Media-Type": file_media_type,
                "X-File-Size": str(len(content)),
                "X-File-SHA256": digest,
            },
        )

    def get_file_metadata(self, workspace_api_name: str, file_id: str) -> JsonObject:
        """Read current metadata for one managed file."""
        return self._json(
            "GET", self._workspace_path(workspace_api_name, "files", file_id)
        )

    def delete_file(
        self,
        workspace_api_name: str,
        file_id: str,
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject | None:
        """Delete one unreferenced managed file."""
        return self._optional_json(
            "DELETE",
            self._workspace_path(workspace_api_name, "files", file_id),
            query={"visibility": visibility},
        )

    def download_file(self, workspace_api_name: str, file_id: str) -> bytes:
        """Download the exact bytes of one managed file."""
        response = self._request(
            "GET",
            self._workspace_path(workspace_api_name, "files", file_id, "content"),
            accept=_BINARY_CONTENT_TYPE,
        )
        _require_content_type(response, _BINARY_CONTENT_TYPE)
        return response.body

    def list_saved_views(
        self,
        workspace_api_name: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        label: str | None = None,
    ) -> JsonObject:
        """Read one bounded saved-view page."""
        return self._json(
            "GET",
            self._workspace_path(workspace_api_name, "views"),
            query={"cursor": cursor, "limit": limit, "label": label},
        )

    def iter_saved_view_pages(
        self,
        workspace_api_name: str,
        *,
        limit: int = 50,
        label: str | None = None,
        max_pages: int,
    ) -> Iterator[JsonObject]:
        """Iterate saved-view pages under one strict caller page bound."""
        return self._iter_pages(
            lambda cursor: self.list_saved_views(
                workspace_api_name, cursor=cursor, limit=limit, label=label
            ),
            max_pages=max_pages,
            item_fields=("items",),
        )

    def create_saved_view(
        self,
        workspace_api_name: str,
        value: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Create one saved view in an explicit workspace."""
        return self._json_body(
            "POST",
            self._workspace_path(workspace_api_name, "views"),
            value,
            query={"visibility": visibility},
        )

    def get_saved_view(
        self,
        workspace_api_name: str,
        view_id: str,
        *,
        expand: Sequence[Literal["timestamps", "bags"]] = (),
    ) -> JsonObject:
        """Read one current saved view."""
        return self._json(
            "GET",
            self._workspace_path(workspace_api_name, "views", view_id),
            query={"expand": expand},
        )

    def update_saved_view(
        self,
        workspace_api_name: str,
        view_id: str,
        update: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Apply a last-write-wins saved-view update."""
        return self._json_body(
            "PATCH",
            self._workspace_path(workspace_api_name, "views", view_id),
            update,
            query={"visibility": visibility},
        )

    def delete_saved_view(
        self,
        workspace_api_name: str,
        view_id: str,
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject | None:
        """Delete one saved view."""
        return self._optional_json(
            "DELETE",
            self._workspace_path(workspace_api_name, "views", view_id),
            query={"visibility": visibility},
        )

    def open_saved_view_graph(
        self,
        workspace_api_name: str,
        view_id: str,
        *,
        expand: Sequence[Literal["timestamps", "bags"]] = (),
        cursor: str | None = None,
        limit: int = 50,
    ) -> JsonObject:
        """Read one bounded graph page for a saved view."""
        return self._json(
            "GET",
            self._workspace_path(workspace_api_name, "views", view_id, "graph"),
            query={"expand": expand, "cursor": cursor, "limit": limit},
        )

    def iter_saved_view_graph_pages(
        self,
        workspace_api_name: str,
        view_id: str,
        *,
        expand: Sequence[Literal["timestamps", "bags"]] = (),
        limit: int = 50,
        max_pages: int,
    ) -> Iterator[JsonObject]:
        """Iterate saved-view graph pages under a strict caller page bound."""
        return self._iter_pages(
            lambda cursor: self.open_saved_view_graph(
                workspace_api_name,
                view_id,
                expand=expand,
                cursor=cursor,
                limit=limit,
            ),
            max_pages=max_pages,
            item_fields=("objects", "links"),
        )

    def query_workspace(
        self, workspace_api_name: str, request: Mapping[str, JsonValue]
    ) -> JsonObject:
        """Run one bounded object or link query."""
        return self._json_body(
            "POST", self._workspace_path(workspace_api_name, "query"), request
        )

    def iter_query_pages(
        self,
        workspace_api_name: str,
        request: Mapping[str, JsonValue],
        *,
        max_pages: int,
    ) -> Iterator[JsonObject]:
        """Iterate query pages without changing any request field or limit."""
        original = dict(request)
        first_request = True

        def fetch(cursor: str | None) -> JsonObject:
            nonlocal first_request
            current = dict(original)
            if first_request:
                first_request = False
            else:
                current["cursor"] = cast("str", cursor)
            return self.query_workspace(workspace_api_name, current)

        requested_cursor = original.get("cursor")
        initial = requested_cursor if isinstance(requested_cursor, str) else None
        return self._iter_pages(
            fetch, max_pages=max_pages, item_fields=("items",), initial_cursor=initial
        )

    def count_workspace(
        self, workspace_api_name: str, request: Mapping[str, JsonValue]
    ) -> JsonObject:
        """Run one bounded filtered count."""
        return self._json_body(
            "POST", self._workspace_path(workspace_api_name, "count"), request
        )

    def expand_graph(
        self, workspace_api_name: str, request: Mapping[str, JsonValue]
    ) -> JsonObject:
        """Run one bounded graph expansion."""
        return self._json_body(
            "POST", self._workspace_path(workspace_api_name, "graph"), request
        )

    def iter_graph_pages(
        self,
        workspace_api_name: str,
        request: Mapping[str, JsonValue],
        *,
        max_pages: int,
    ) -> Iterator[JsonObject]:
        """Iterate graph pages without changing any request field or limit."""
        original = dict(request)
        first_request = True

        def fetch(cursor: str | None) -> JsonObject:
            nonlocal first_request
            current = dict(original)
            if first_request:
                first_request = False
            else:
                current["cursor"] = cast("str", cursor)
            return self.expand_graph(workspace_api_name, current)

        requested_cursor = original.get("cursor")
        initial = requested_cursor if isinstance(requested_cursor, str) else None
        return self._iter_pages(
            fetch,
            max_pages=max_pages,
            item_fields=("objects", "links"),
            initial_cursor=initial,
        )

    def read_changed_since(
        self,
        workspace_api_name: str,
        *,
        since: str,
        cursor: str | None = None,
        limit: int = 50,
        expand: Sequence[Literal["timestamps", "bags"]] = (),
    ) -> JsonObject:
        """Read one bounded page of current records changed after a time."""
        return self._json(
            "GET",
            self._workspace_path(workspace_api_name, "changes"),
            query={
                "since": since,
                "cursor": cursor,
                "limit": limit,
                "expand": expand,
            },
        )

    def iter_changed_since_pages(
        self,
        workspace_api_name: str,
        *,
        since: str,
        limit: int = 50,
        expand: Sequence[Literal["timestamps", "bags"]] = (),
        max_pages: int,
    ) -> Iterator[JsonObject]:
        """Iterate changed-since pages under one strict caller page bound."""
        return self._iter_pages(
            lambda cursor: self.read_changed_since(
                workspace_api_name,
                since=since,
                cursor=cursor,
                limit=limit,
                expand=expand,
            ),
            max_pages=max_pages,
            item_fields=("items",),
        )

    def apply_service_bulk_mutation(
        self,
        request: Mapping[str, JsonValue],
        *,
        visibility: WriteVisibility = "eventual",
    ) -> JsonObject:
        """Apply one bounded service-wide bulk mutation."""
        return self._json_body(
            "POST",
            self._service_path("bulk"),
            request,
            query={"visibility": visibility},
        )

    def get_job(self, job_id: str) -> JsonObject:
        """Read one minimal job in this service."""
        return self._json("GET", self._service_path("jobs", job_id))

    def _iter_pages(
        self,
        fetch: Callable[[str | None], JsonObject],
        *,
        max_pages: int,
        item_fields: tuple[str, ...],
        initial_cursor: str | None = None,
    ) -> Iterator[JsonObject]:
        page_bound = cast("object", max_pages)
        if (
            not isinstance(page_bound, int)
            or isinstance(page_bound, bool)
            or page_bound < 1
        ):
            msg = "The Ontology page bound must be a positive integer."
            raise ValueError(msg)
        cursor = initial_cursor
        seen: set[str] = {cursor} if cursor is not None else set()
        for _page_number in range(page_bound):
            page = fetch(cursor)
            for field in item_fields:
                if not isinstance(page.get(field), list):
                    msg = f"The Ontology page has no valid {field!r} array."
                    raise OntologyProtocolError(msg)
            next_cursor = page.get("nextCursor")
            if next_cursor is not None and (
                not isinstance(next_cursor, str) or not next_cursor
            ):
                msg = "The Ontology page has an invalid next cursor."
                raise OntologyProtocolError(msg)
            if next_cursor is not None and next_cursor in seen:
                msg = "The Ontology cursor sequence contains a cycle."
                raise OntologyProtocolError(msg)
            yield page
            if next_cursor is None:
                return
            seen.add(next_cursor)
            cursor = next_cursor
        msg = "The Ontology cursor sequence exceeds the caller page bound."
        raise OntologyPageLimitError(msg)

    def _service_path(self, *parts: str) -> str:
        return "/".join(
            ("v1", "services", _segment(self._service_api_name), *map(_segment, parts))
        )

    def _workspace_path(self, workspace_api_name: str, *parts: str) -> str:
        workspace = _required_segment(workspace_api_name, name="workspace API name")
        return self._service_path("workspaces", workspace, *parts)

    def _json_body(
        self,
        method: str,
        path: str,
        body: Mapping[str, JsonValue],
        *,
        query: Mapping[str, object] | None = None,
    ) -> JsonObject:
        return self._json(
            method,
            path,
            query=query,
            body=canonical_json_bytes(dict(body)),
            content_type=_JSON_CONTENT_TYPE,
        )

    def _json(  # noqa: PLR0913 - HTTP requests have fixed optional parts.
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> JsonObject:
        response = self._request(
            method,
            path,
            query=query,
            body=body,
            content_type=content_type,
            extra_headers=extra_headers,
        )
        return _response_object(response)

    def _optional_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> JsonObject | None:
        response = self._request(method, path, query=query, extra_headers=extra_headers)
        if not response.body:
            return None
        return _response_object(response)

    def _request(  # noqa: PLR0913 - HTTP requests have fixed optional parts.
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
        accept: str = _JSON_CONTENT_TYPE,
    ) -> OntologyTransportResponse:
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self._service_key}",
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        url = self._url(path, query)
        try:
            response = self._transport.request(
                method, url, headers, body, self._timeout
            )
        except Exception as error:  # noqa: BLE001 - Transports can raise any error.
            safe = _redact_text(str(error), self._service_key)
            msg = f"The Ontology HTTP transport failed: {safe}"
            raise OntologyTransportError(msg) from None
        _validate_transport_response(response)
        if _HTTP_SUCCESS_MINIMUM <= response.status < _HTTP_SUCCESS_LIMIT:
            if len(response.body) > self._maximum_success_response_bytes:
                msg = "The Ontology success response exceeds the caller byte bound."
                raise OntologyResponseLimitError(msg)
        else:
            raise _http_error(response, secret=self._service_key)
        return response

    def _url(self, path: str, query: Mapping[str, object] | None) -> str:
        parameters: list[tuple[str, str]] = []
        if query:
            for name in sorted(query):
                value = query[name]
                if value is None or value in ((), []):
                    continue
                if isinstance(value, Sequence) and not isinstance(
                    value, (str, bytes, bytearray)
                ):
                    values = cast("Sequence[object]", value)
                    parameters.extend((name, str(item)) for item in values)
                else:
                    parameters.append((name, str(value)))
        suffix = f"?{urlencode(parameters)}" if parameters else ""
        return f"{self._base_url}/{path}{suffix}"


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Return the RFC 8785 canonical JSON encoding of one public value.

    Raises:
        ValueError: If the value is not an interoperable JSON value.

    """
    return _canonical_json(value).encode("utf-8")


def value_fingerprint(value: JsonValue) -> str:
    """Return the version 1 Ontology occurrence fingerprint for one value."""
    digest = hashlib.sha256(canonical_json_bytes(value)).digest()[:12]
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"v1:{encoded}"


def value_occurrence_selector(
    value: JsonValue, *, bag_id: str | None = None
) -> JsonObject:
    """Select one occurrence by its exact current value and optional bag."""
    selector: JsonObject = {"value": value}
    if bag_id is not None:
        selector["bagId"] = bag_id
    return selector


def fingerprint_occurrence_selector(
    value: JsonValue, *, bag_id: str | None = None
) -> JsonObject:
    """Select one occurrence by its calculated value fingerprint and bag."""
    selector: JsonObject = {"fingerprint": value_fingerprint(value)}
    if bag_id is not None:
        selector["bagId"] = bag_id
    return selector


def _canonical_json(value: object) -> str:  # noqa: C901, PLR0911
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if not -_MAXIMUM_SAFE_INTEGER <= value <= _MAXIMUM_SAFE_INTEGER:
            msg = "An Ontology JSON integer is outside the interoperable range."
            raise ValueError(msg)
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, str):
        _valid_unicode(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        items = cast("list[object]", value)
        return "[" + ",".join(_canonical_json(item) for item in items) + "]"
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        for key in mapping:
            if not isinstance(key, str):
                msg = "An Ontology JSON mapping key is not a string."
                raise TypeError(msg)
            _valid_unicode(key)
        string_mapping = cast("dict[str, object]", mapping)
        keys = sorted(string_mapping, key=lambda key: key.encode("utf-16be"))
        return (
            "{"
            + ",".join(
                f"{_canonical_json(key)}:{_canonical_json(string_mapping[key])}"
                for key in keys
            )
            + "}"
        )
    msg = "The value is not an Ontology JSON-equivalent value."
    raise ValueError(msg)


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        msg = "An Ontology JSON number must be finite."
        raise ValueError(msg)
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    source = repr(abs(value)).lower()
    if "e" in source:
        coefficient, exponent_text = source.split("e", 1)
        exponent = int(exponent_text)
    else:
        coefficient = source
        exponent = 0
    integer, _dot, fraction = coefficient.partition(".")
    digits = integer + fraction
    decimal_position = len(integer) + exponent
    if _PLAIN_DECIMAL_MINIMUM <= abs(value) < _PLAIN_DECIMAL_LIMIT:
        if decimal_position <= 0:
            result = "0." + ("0" * -decimal_position) + digits
        elif decimal_position >= len(digits):
            result = digits + ("0" * (decimal_position - len(digits)))
        else:
            result = digits[:decimal_position] + "." + digits[decimal_position:]
        return sign + result.rstrip("0").rstrip(".") if "." in result else sign + result
    normalized = digits[0]
    remainder = digits[1:].rstrip("0")
    if remainder:
        normalized += "." + remainder
    scientific_exponent = decimal_position - 1
    exponent_sign = "+" if scientific_exponent >= 0 else ""
    return f"{sign}{normalized}e{exponent_sign}{scientific_exponent}"


def _ontology_body(
    document: Mapping[str, JsonValue] | str | bytes, media_type: OntologyMediaType
) -> bytes:
    if media_type == _JSON_CONTENT_TYPE:
        if not isinstance(document, Mapping):
            msg = "A JSON ontology document must be a mapping."
            raise ValueError(msg)
        return canonical_json_bytes(dict(document))
    if media_type == _YAML_CONTENT_TYPE:
        if isinstance(document, str):
            return document.encode("utf-8")
        if isinstance(document, bytes):
            document.decode("utf-8")
            return document
        msg = "A YAML ontology document must be text or UTF-8 bytes."
        raise ValueError(msg)
    msg = "The ontology document media type is not supported."
    raise ValueError(msg)


def _http_error(  # noqa: C901 - The public error has fixed validated fields.
    response: OntologyTransportResponse, *, secret: str
) -> OntologyHTTPError:
    if len(response.body) > _MAXIMUM_ERROR_BODY_BYTES:
        msg = "The Ontology error response exceeds the client safety bound."
        raise OntologyProtocolError(msg)
    _require_content_type(response, _JSON_CONTENT_TYPE)
    try:
        raw = _strict_json_loads(response.body)
    except UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError:
        msg = "The Ontology error response is not valid JSON."
        raise OntologyProtocolError(msg) from None
    if not isinstance(raw, dict):
        msg = "The Ontology error response is not an object."
        raise OntologyProtocolError(msg)
    payload = cast("Mapping[str, object]", raw)
    code = payload.get("code")
    message = payload.get("message")
    retryable = payload.get("retryable")
    details = payload.get("details")
    field = payload.get("field")
    if (
        not isinstance(code, str)
        or not code
        or not isinstance(message, str)
        or not 1 <= len(message) <= _MAXIMUM_ERROR_MESSAGE_LENGTH
        or not isinstance(retryable, bool)
        or not isinstance(details, dict)
        or len(cast("dict[object, object]", details)) > _MAXIMUM_ERROR_DETAILS
        or (
            field is not None
            and (
                not isinstance(field, str)
                or not 1 <= len(field) <= _MAXIMUM_ERROR_FIELD_LENGTH
            )
        )
        or set(payload) - {"code", "message", "field", "retryable", "details"}
    ):
        msg = "The Ontology error response does not match the public contract."
        raise OntologyProtocolError(msg)
    safe_message = _redact_text(message, secret)
    safe_field = _redact_text(field, secret) if field is not None else None
    safe_details = cast("JsonObject", _redact_json(cast("JsonValue", details), secret))
    error_type: type[OntologyHTTPError]
    if response.status == _HTTP_UNAUTHORIZED:
        error_type = OntologyAuthenticationError
    elif response.status == _HTTP_FORBIDDEN:
        error_type = OntologyAuthorizationError
    elif response.status == _HTTP_NOT_FOUND:
        error_type = OntologyNotFoundError
    elif response.status == _HTTP_CONFLICT:
        error_type = OntologyConflictError
    elif response.status in _HTTP_VALIDATION_STATUSES:
        error_type = OntologyValidationError
    elif response.status >= _HTTP_SERVER_ERROR_MINIMUM:
        error_type = OntologyUnavailableError
    else:
        error_type = OntologyHTTPError
    return error_type(
        status=response.status,
        code=_redact_text(code, secret),
        message=safe_message,
        retryable=retryable,
        field=safe_field,
        details=safe_details,
    )


def _response_object(response: OntologyTransportResponse) -> JsonObject:
    _require_content_type(response, _JSON_CONTENT_TYPE)
    try:
        raw = _strict_json_loads(response.body)
    except UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError:
        msg = "The Ontology success response is not valid JSON."
        raise OntologyProtocolError(msg) from None
    if not isinstance(raw, dict):
        msg = "The Ontology success response is not an object."
        raise OntologyProtocolError(msg)
    return cast("JsonObject", raw)


def _strict_json_loads(body: bytes) -> object:
    value = cast(
        "object",
        json.loads(
            body,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        ),
    )
    _validate_json_unicode(value)
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            msg = "An Ontology JSON object has a duplicate key."
            raise ValueError(msg)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    del value
    msg = "An Ontology JSON number is not finite."
    raise ValueError(msg)


def _validate_json_unicode(value: object) -> None:
    if isinstance(value, str):
        _valid_unicode(value)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            _validate_json_unicode(item)
    elif isinstance(value, dict):
        for key, item in cast("dict[str, object]", value).items():
            _valid_unicode(key)
            _validate_json_unicode(item)


def _validate_transport_response(response: object) -> None:
    if not isinstance(response, OntologyTransportResponse):
        msg = "The Ontology transport returned an invalid response."
        raise OntologyProtocolError(msg)
    status = cast("object", response.status)
    headers = cast("object", response.headers)
    body = cast("object", response.body)
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not _HTTP_STATUS_MINIMUM <= status <= _HTTP_STATUS_MAXIMUM
        or not isinstance(headers, Mapping)
        or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in cast("Mapping[object, object]", headers).items()
        )
        or not isinstance(body, bytes)
    ):
        msg = "The Ontology transport returned an invalid response."
        raise OntologyProtocolError(msg)


def _require_content_type(response: OntologyTransportResponse, expected: str) -> None:
    values = [
        value
        for name, value in response.headers.items()
        if name.casefold() == "content-type"
    ]
    if len(values) != 1 or values[0].partition(";")[0].strip().casefold() != expected:
        msg = "The Ontology response has an invalid content type."
        raise OntologyProtocolError(msg)


def _redact_json(value: JsonValue, secret: str) -> JsonValue:
    if isinstance(value, str):
        return _redact_text(value, secret)
    if isinstance(value, list):
        return [_redact_json(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            _redact_text(key, secret): _redact_json(item, secret)
            for key, item in value.items()
        }
    return value


def _redact_text(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]")


def _header_value(value: str) -> str:
    _valid_unicode(value)
    if any(
        ord(character) < _CONTROL_CHARACTER_END or ord(character) == _DELETE_CHARACTER
        for character in value
    ):
        msg = "An Ontology HTTP header value is invalid."
        raise ValueError(msg)
    return value


def _file_name_header(value: str) -> str:
    if not 1 <= len(value) <= _MAXIMUM_FILE_NAME_LENGTH or any(
        character in "/\\"
        or ord(character) < _CONTROL_CHARACTER_END
        or _DELETE_CHARACTER <= ord(character) <= _C1_CONTROL_CHARACTER_END
        for character in value
    ):
        msg = "The Ontology file name is invalid."
        raise ValueError(msg)
    _valid_unicode(value)
    payload = base64.urlsafe_b64encode(value.encode("utf-8")).rstrip(b"=")
    return "u8." + payload.decode("ascii")


def _impact_confirmation_header(value: str) -> str:
    if len(value) > _MAXIMUM_IMPACT_CONFIRMATION_LENGTH:
        msg = "The Ontology impact confirmation is too long."
        raise ValueError(msg)
    return _header_value(value)


def _media_type_header(value: str) -> str:
    if (
        len(value) > _MAXIMUM_MEDIA_TYPE_LENGTH
        or _MEDIA_TYPE_PATTERN.fullmatch(value) is None
    ):
        msg = "The Ontology file media type is invalid."
        raise ValueError(msg)
    return value


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    try:
        port = parsed.port
    except ValueError:
        msg = "The Ontology base URL is invalid."
        raise ValueError(msg) from None
    if (
        parsed.scheme not in ({"http", "https"} if loopback else {"https"})
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in value)
        or (port is not None and not 1 <= port <= _MAXIMUM_PORT)
    ):
        msg = "The Ontology base URL is invalid."
        raise ValueError(msg)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _required_segment(value: str, *, name: str) -> str:
    _valid_unicode(value)
    if (
        not value
        or value.strip() != value
        or any(
            ord(character) < _CONTROL_CHARACTER_END
            or ord(character) == _DELETE_CHARACTER
            for character in value
        )
    ):
        msg = f"The Ontology {name} is invalid."
        raise ValueError(msg)
    return value


def _segment(value: str) -> str:
    return quote(_required_segment(value, name="path value"), safe="")


def _valid_unicode(value: str) -> None:
    if any(
        _HIGH_SURROGATE_START <= ord(character) <= _LOW_SURROGATE_END
        for character in value
    ):
        msg = "An Ontology string contains an invalid Unicode scalar."
        raise ValueError(msg)
