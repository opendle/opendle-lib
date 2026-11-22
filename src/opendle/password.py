"""Hash and check user passwords with scrypt from the standard library.

A stored hash has the form `scrypt$N$r$p$salt$hash` with URL-safe base64
parts, so the cost can change later without a second format.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

__all__ = [
    "MAXIMUM_PASSWORD_LENGTH",
    "MINIMUM_PASSWORD_LENGTH",
    "hash_password",
    "password_problem",
    "verify_password",
]

MINIMUM_PASSWORD_LENGTH = 12
MAXIMUM_PASSWORD_LENGTH = 256
_COST = 2**15
_BLOCK_SIZE = 8
_PARALLEL = 1
_KEY_BYTES = 32
_SALT_BYTES = 16
_MAXIMUM_MEMORY = 64 * 1024 * 1024
_PREFIX = "scrypt"
# A hash of a random password. A check without a stored hash uses it, so the
# answer takes the same time for an unknown account.
_UNKNOWN_HASH: list[str] = []


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _derive(password: str, salt: bytes, cost: int, block: int, parallel: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=cost,
        r=block,
        p=parallel,
        maxmem=_MAXIMUM_MEMORY,
        dklen=_KEY_BYTES,
    )


def password_problem(password: str) -> str | None:
    """Return why a new password is not accepted, or `None` when it is."""
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        return f"Use {MINIMUM_PASSWORD_LENGTH} characters or more."
    if len(password) > MAXIMUM_PASSWORD_LENGTH:
        return f"Use {MAXIMUM_PASSWORD_LENGTH} characters or fewer."
    return None


def hash_password(password: str) -> str:
    """Return a new salted hash of a password.

    Raises:
        ValueError: If `password_problem` refuses the password.

    """
    problem = password_problem(password)
    if problem is not None:
        raise ValueError(problem)
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(password, salt, _COST, _BLOCK_SIZE, _PARALLEL)
    return "$".join(
        (
            _PREFIX,
            str(_COST),
            str(_BLOCK_SIZE),
            str(_PARALLEL),
            _encode(salt),
            _encode(key),
        )
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Return whether a password agrees with a stored hash.

    Without a stored hash, the function does the same work and returns
    `False`. A hash in an unknown form also returns `False`.
    """
    if len(password) > MAXIMUM_PASSWORD_LENGTH:
        return False
    if stored is None:
        if not _UNKNOWN_HASH:
            _UNKNOWN_HASH.append(hash_password(secrets.token_urlsafe(24)))
        verify_password(password, _UNKNOWN_HASH[0])
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _PREFIX:  # noqa: PLR2004 - the fixed form.
        return False
    try:
        cost, block, parallel = (int(part) for part in parts[1:4])
        salt, expected = _decode(parts[4]), _decode(parts[5])
        actual = _derive(password, salt, cost, block, parallel)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)
