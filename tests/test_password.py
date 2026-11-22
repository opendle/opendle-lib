"""Tests for password hashes."""

from __future__ import annotations

import pytest

from opendle.password import (
    MAXIMUM_PASSWORD_LENGTH,
    hash_password,
    password_problem,
    verify_password,
)

_PASSWORD = "correct horse battery"  # noqa: S105 - Public test fixture.


def test_a_hash_checks_its_own_password_only() -> None:
    """A hash has a new salt and accepts only the same password."""
    stored = hash_password(_PASSWORD)

    assert stored.startswith("scrypt$32768$8$1$")
    assert stored != hash_password(_PASSWORD)
    assert verify_password(_PASSWORD, stored)
    assert not verify_password(_PASSWORD + "!", stored)


def test_a_check_without_a_valid_hash_is_false() -> None:
    """An unknown account, a damaged hash and a long password are refused."""
    assert not verify_password(_PASSWORD, None)
    assert not verify_password(_PASSWORD, None)
    assert not verify_password(_PASSWORD, "bcrypt$x")
    assert not verify_password(_PASSWORD, "scrypt$x$8$1$abc$def")
    assert not verify_password("x" * (MAXIMUM_PASSWORD_LENGTH + 1), None)


def test_short_and_long_passwords_are_refused() -> None:
    """The length limits apply to a new password."""
    assert password_problem("short") == "Use 12 characters or more."
    assert password_problem("x" * (MAXIMUM_PASSWORD_LENGTH + 1)) is not None
    assert password_problem(_PASSWORD) is None
    with pytest.raises(ValueError, match="12 characters"):
        hash_password("short")
