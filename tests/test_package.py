"""Tests for the package scaffold."""

from importlib.metadata import metadata

import opendle


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
