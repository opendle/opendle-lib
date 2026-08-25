"""Tests for the package scaffold."""

from importlib.metadata import metadata

import opendle
from opendle.contracts import canonical_json_bytes as contract_canonical_json_bytes
from opendle.ontology import canonical_json_bytes as ontology_canonical_json_bytes


def test_package_uses_the_opendle_namespace() -> None:
    """The distribution must expose the selected import namespace."""
    assert opendle.__name__ == "opendle"


def test_distribution_metadata_matches_the_repository_contract() -> None:
    """The built distribution must keep its selected public identity."""
    package_metadata = metadata("opendle-lib")

    assert package_metadata["Name"] == "opendle-lib"
    requires_python = package_metadata["Requires-Python"]
    assert requires_python is not None
    assert set(requires_python.split(",")) == {">=3.14", "<3.15"}


def test_general_contract_exports_keep_the_ontology_compatibility_alias() -> None:
    """Expose canonical JSON generally without breaking the old import path."""
    assert opendle.canonical_json_bytes is contract_canonical_json_bytes
    assert ontology_canonical_json_bytes is contract_canonical_json_bytes
    assert contract_canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
