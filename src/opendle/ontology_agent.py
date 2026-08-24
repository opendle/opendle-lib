"""Compact dynamic helpers for agents that call the Ontology Service."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, cast

from opendle.ontology import JsonObject, JsonValue, OntologyClient, canonical_json_bytes

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "OntologyAgentHelpers",
    "OntologyAgentOutputLimitError",
    "compact_yaml",
]

_DEFAULT_MAXIMUM_OUTPUT_BYTES = 65_536
_METADATA_BAG_FIELDS = frozenset(
    {"id", "source", "location", "at", "from", "to", "createdAt", "updatedAt"}
)


class OntologyAgentOutputLimitError(ValueError):
    """Report that a compact agent view exceeds its caller output bound."""


class OntologyAgentHelpers:
    """Create bounded YAML views for one explicit service and workspace.

    This class is a normal SDK wrapper. It does not define an agent protocol,
    change authorization, retry requests, or generate ontology-specific types.
    """

    __slots__ = ("_client", "_max_output_bytes", "_workspace_api_name")

    def __init__(
        self,
        client: OntologyClient,
        *,
        workspace_api_name: str,
        max_output_bytes: int = _DEFAULT_MAXIMUM_OUTPUT_BYTES,
    ) -> None:
        """Initialize helpers with an explicit service and workspace scope."""
        if not workspace_api_name or workspace_api_name.strip() != workspace_api_name:
            msg = "The Ontology agent workspace API name is invalid."
            raise ValueError(msg)
        output_bound = cast("object", max_output_bytes)
        if (
            not isinstance(output_bound, int)
            or isinstance(output_bound, bool)
            or output_bound < 1
        ):
            msg = "The Ontology agent output bound must be a positive integer."
            raise ValueError(msg)
        self._client = client
        self._workspace_api_name = workspace_api_name
        self._max_output_bytes = output_bound

    @property
    def service_api_name(self) -> str:
        """Return the exact service scope of these helpers."""
        return self._client.service_api_name

    @property
    def workspace_api_name(self) -> str:
        """Return the exact workspace scope of these helpers."""
        return self._workspace_api_name

    def __repr__(self) -> str:
        """Return the explicit scopes without client credentials."""
        return (
            f"{type(self).__name__}(service_api_name={self.service_api_name!r}, "
            f"workspace_api_name={self.workspace_api_name!r}, "
            f"max_output_bytes={self._max_output_bytes!r})"
        )

    def yaml(
        self,
        value: JsonValue,
        *,
        include_timestamps: bool = False,
        include_bags: bool = False,
    ) -> str:
        """Return one deterministic bounded compact YAML view."""
        return compact_yaml(
            value,
            include_timestamps=include_timestamps,
            include_bags=include_bags,
            max_output_bytes=self._max_output_bytes,
        )

    def get_object_yaml(
        self,
        object_key: str,
        *,
        include_timestamps: bool = False,
        include_bags: bool = False,
    ) -> str:
        """Read one object and return its compact YAML view."""
        value = self._client.get_object(
            self._workspace_api_name,
            object_key,
            expand=_expansions(
                include_timestamps=include_timestamps, include_bags=include_bags
            ),
        )
        return self.yaml(
            value,
            include_timestamps=include_timestamps,
            include_bags=include_bags,
        )

    def get_link_yaml(
        self,
        link_key: str,
        *,
        include_timestamps: bool = False,
        include_bags: bool = False,
    ) -> str:
        """Read one link and return its compact YAML view."""
        value = self._client.get_link(
            self._workspace_api_name,
            link_key,
            expand=_expansions(
                include_timestamps=include_timestamps, include_bags=include_bags
            ),
        )
        return self.yaml(
            value,
            include_timestamps=include_timestamps,
            include_bags=include_bags,
        )

    def query_yaml(
        self,
        request: Mapping[str, JsonValue],
        *,
        include_timestamps: bool = False,
        include_bags: bool = False,
    ) -> str:
        """Run one caller-bounded query and return its compact YAML view."""
        value = self._client.query_workspace(
            self._workspace_api_name,
            _request_with_expansions(
                request,
                include_timestamps=include_timestamps,
                include_bags=include_bags,
            ),
        )
        return self.yaml(
            value,
            include_timestamps=include_timestamps,
            include_bags=include_bags,
        )

    def expand_graph_yaml(
        self,
        request: Mapping[str, JsonValue],
        *,
        include_timestamps: bool = False,
        include_bags: bool = False,
    ) -> str:
        """Run one caller-bounded graph expansion and return compact YAML."""
        value = self._client.expand_graph(
            self._workspace_api_name,
            _request_with_expansions(
                request,
                include_timestamps=include_timestamps,
                include_bags=include_bags,
            ),
        )
        return self.yaml(
            value,
            include_timestamps=include_timestamps,
            include_bags=include_bags,
        )

    def read_changed_since_yaml(
        self,
        *,
        since: str,
        cursor: str | None = None,
        limit: int = 50,
        include_timestamps: bool = False,
        include_bags: bool = False,
    ) -> str:
        """Read one caller-bounded changed page and return compact YAML."""
        value = self._client.read_changed_since(
            self._workspace_api_name,
            since=since,
            cursor=cursor,
            limit=limit,
            expand=_expansions(
                include_timestamps=include_timestamps, include_bags=include_bags
            ),
        )
        return self.yaml(
            value,
            include_timestamps=include_timestamps,
            include_bags=include_bags,
        )


def compact_yaml(
    value: JsonValue,
    *,
    include_timestamps: bool = False,
    include_bags: bool = False,
    max_output_bytes: int = _DEFAULT_MAXIMUM_OUTPUT_BYTES,
) -> str:
    """Return deterministic YAML with optional detail and a strict byte bound.

    The output uses only the JSON-equivalent YAML subset. Mapping keys are
    sorted, and all strings use JSON quoting. This keeps the view deterministic
    without a YAML dependency or application-specific tags.

    Raises:
        OntologyAgentOutputLimitError: If the complete output exceeds the bound.
        ValueError: If the bound or value is invalid.

    """
    output_bound = cast("object", max_output_bytes)
    if (
        not isinstance(output_bound, int)
        or isinstance(output_bound, bool)
        or output_bound < 1
    ):
        msg = "The Ontology agent output bound must be a positive integer."
        raise ValueError(msg)
    selected = _select_detail(
        value,
        include_timestamps=include_timestamps,
        include_bags=include_bags,
    )
    # Validate the complete JSON-equivalent value before YAML rendering.
    canonical_json_bytes(selected)
    rendered = "\n".join(_yaml_lines(selected, indentation=0)) + "\n"
    if len(rendered.encode("utf-8")) > output_bound:
        msg = "The compact Ontology YAML view exceeds the caller output bound."
        raise OntologyAgentOutputLimitError(msg)
    return rendered


def _request_with_expansions(
    request: Mapping[str, JsonValue],
    *,
    include_timestamps: bool,
    include_bags: bool,
) -> JsonObject:
    current = dict(request)
    requested = _expansions(
        include_timestamps=include_timestamps, include_bags=include_bags
    )
    if "expand" in current and not isinstance(current["expand"], list):
        return current
    existing = current.get("expand")
    expansions = list(existing) if isinstance(existing, list) else []
    expansions.extend(item for item in requested if item not in expansions)
    if expansions:
        current["expand"] = expansions
    return current


def _expansions(
    *, include_timestamps: bool, include_bags: bool
) -> tuple[Literal["timestamps", "bags"], ...]:
    result: list[Literal["timestamps", "bags"]] = []
    if include_timestamps:
        result.append("timestamps")
    if include_bags:
        result.append("bags")
    return tuple(result)


def _select_detail(  # noqa: PLR0911 - Each public JSON envelope has one branch.
    value: JsonValue,
    *,
    include_timestamps: bool,
    include_bags: bool,
) -> JsonValue:
    if isinstance(value, list):
        return [
            _select_detail(
                item,
                include_timestamps=include_timestamps,
                include_bags=include_bags,
            )
            for item in value
        ]
    if isinstance(value, dict):
        if _is_record(value):
            return _select_record_detail(
                value,
                include_timestamps=include_timestamps,
                include_bags=include_bags,
            )
        if _is_metadata_bag(value):
            return {
                key: _copy_json(item)
                for key, item in value.items()
                if include_timestamps or key not in {"createdAt", "updatedAt"}
            }
        if _is_item_page(value):
            return {
                key: (
                    [
                        _select_detail(
                            nested,
                            include_timestamps=include_timestamps,
                            include_bags=include_bags,
                        )
                        for nested in item
                    ]
                    if key == "items" and isinstance(item, list)
                    else _copy_json(item)
                )
                for key, item in value.items()
            }
        if _is_graph_page(value):
            return {
                key: (
                    [
                        _select_detail(
                            nested,
                            include_timestamps=include_timestamps,
                            include_bags=include_bags,
                        )
                        for nested in item
                    ]
                    if key in {"objects", "links"} and isinstance(item, list)
                    else _copy_json(item)
                )
                for key, item in value.items()
            }
        return _copy_json(value)
    return value


def _is_record(value: JsonObject) -> bool:
    return (
        isinstance(value.get("type"), str)
        and isinstance(value.get("properties"), dict)
        and (isinstance(value.get("key"), str) or isinstance(value.get("id"), str))
    ) or (
        isinstance(value.get("id"), str)
        and isinstance(value.get("objects"), list)
        and isinstance(value.get("labels"), list)
    )


def _is_metadata_bag(value: JsonObject) -> bool:
    return (
        isinstance(value.get("id"), str)
        and {"createdAt", "updatedAt"}.issubset(value)
        and set(value).issubset(_METADATA_BAG_FIELDS)
    )


def _is_item_page(value: JsonObject) -> bool:
    return isinstance(value.get("items"), list) and set(value).issubset(
        {"items", "nextCursor"}
    )


def _is_graph_page(value: JsonObject) -> bool:
    return (
        isinstance(value.get("objects"), list)
        and isinstance(value.get("links"), list)
        and isinstance(value.get("truncated"), bool)
        and set(value).issubset({"objects", "links", "truncated", "nextCursor"})
    )


def _select_record_detail(
    value: JsonObject,
    *,
    include_timestamps: bool,
    include_bags: bool,
) -> JsonObject:
    result: JsonObject = {}
    for key, item in value.items():
        if not include_timestamps and key == "timestamps":
            continue
        if not include_bags and key == "bags":
            continue
        if key == "properties" and isinstance(item, dict):
            result[key] = _select_property_map(
                item, include_timestamps=include_timestamps
            )
        elif key == "bags" and isinstance(item, list):
            result[key] = [
                _select_detail(
                    bag,
                    include_timestamps=include_timestamps,
                    include_bags=include_bags,
                )
                for bag in item
            ]
        else:
            result[key] = _copy_json(item)
    return result


def _select_property_map(value: JsonObject, *, include_timestamps: bool) -> JsonObject:
    result: JsonObject = {}
    for property_name, occurrences in value.items():
        if not isinstance(occurrences, list):
            result[property_name] = _copy_json(occurrences)
            continue
        selected: list[JsonValue] = []
        for occurrence in occurrences:
            if not isinstance(occurrence, dict):
                selected.append(_copy_json(occurrence))
                continue
            selected.append(
                {
                    key: _copy_json(item)
                    for key, item in occurrence.items()
                    if include_timestamps or key not in {"createdAt", "updatedAt"}
                }
            )
        result[property_name] = selected
    return result


def _copy_json(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy_json(item) for key, item in value.items()}
    return value


def _yaml_lines(  # noqa: C901
    value: JsonValue, *, indentation: int
) -> list[str]:
    prefix = " " * indentation
    scalar = _yaml_scalar(value)
    if scalar is not None:
        return [prefix + scalar]
    if isinstance(value, list):
        if not value:
            return [prefix + "[]"]
        lines: list[str] = []
        for item in value:
            item_scalar = _yaml_scalar(item)
            if item_scalar is not None:
                lines.append(prefix + "- " + item_scalar)
            elif _is_empty_collection(item):
                lines.append(prefix + "- " + ("[]" if isinstance(item, list) else "{}"))
            else:
                lines.append(prefix + "-")
                lines.extend(_yaml_lines(item, indentation=indentation + 2))
        return lines
    mapping = cast("JsonObject", value)
    if not mapping:
        return [prefix + "{}"]
    lines = []
    for key in sorted(mapping, key=lambda item: item.encode("utf-16be")):
        item = mapping[key]
        key_text = json.dumps(key, ensure_ascii=False)
        item_scalar = _yaml_scalar(item)
        if item_scalar is not None:
            lines.append(f"{prefix}{key_text}: {item_scalar}")
        elif _is_empty_collection(item):
            empty = "[]" if isinstance(item, list) else "{}"
            lines.append(f"{prefix}{key_text}: {empty}")
        else:
            lines.append(f"{prefix}{key_text}:")
            lines.extend(_yaml_lines(item, indentation=indentation + 2))
    return lines


def _yaml_scalar(value: JsonValue) -> str | None:  # noqa: PLR0911
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return canonical_json_bytes(value).decode("utf-8")
    if isinstance(value, float):
        return canonical_json_bytes(value).decode("utf-8")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return None


def _is_empty_collection(value: JsonValue) -> bool:
    return isinstance(value, (list, dict)) and not value
