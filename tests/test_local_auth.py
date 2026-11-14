"""Check local sessions and API tokens through their public service."""

from __future__ import annotations

import pytest

from opendle.local_auth import (
    AuthError,
    LocalSessions,
    hash_secret,
    is_secret,
    new_secret,
    utc_now,
)
from passkey_support import FakeClock, MemoryStore

_SECRET_LENGTH = 43
_MALFORMED = "short"


def _service() -> tuple[LocalSessions, MemoryStore, FakeClock]:
    store = MemoryStore()
    clock = FakeClock()
    return LocalSessions(store, session_days=2, clock=clock), store, clock


def test_secrets_have_one_format_and_the_store_gets_only_hashes() -> None:
    """A secret is 43 URL-safe characters and its hash is SHA-256 text."""
    secret = new_secret()

    assert len(secret) == _SECRET_LENGTH
    assert is_secret(secret)
    assert not is_secret(secret + "x")
    assert not is_secret(None)
    assert hash_secret("a") == (
        "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    )
    assert utc_now().tzinfo is not None


def test_a_session_lifetime_below_one_day_is_refused() -> None:
    """The service does not accept a session that expires immediately."""
    with pytest.raises(ValueError, match="one day"):
        LocalSessions(MemoryStore(), session_days=0)


def test_a_session_signs_in_until_it_expires_or_ends() -> None:
    """A session secret authenticates only while its record is current."""
    service, store, clock = _service()
    issued = service.start_session("user-1")

    assert hash_secret(issued.secret) in store.sessions
    assert issued.secret not in store.sessions
    principal = service.authenticate(session_secret=issued.secret)
    assert principal is not None
    assert (principal.user_id, principal.kind, principal.token_id) == (
        "user-1",
        "session",
        None,
    )
    assert service.authenticate() is None
    assert service.authenticate(session_secret=_MALFORMED) is None
    assert service.authenticate(session_secret=new_secret()) is None

    replacement = service.start_session("user-1", replaced_secret=issued.secret)
    assert service.authenticate(session_secret=issued.secret) is None

    clock.advance(days=2)
    assert service.authenticate(session_secret=replacement.secret) is None

    service.end_session("not a secret")
    service.end_session(replacement.secret)
    assert store.sessions == {}


def test_an_api_token_signs_in_until_it_expires_or_is_revoked() -> None:
    """A bearer token has priority and does not fall back to a session."""
    service, store, clock = _service()
    session = service.start_session("user-1")
    limited = service.create_token("user-1", "  agent  ", days=1)
    lasting = service.create_token("user-1", "lasting")

    assert limited.token.name == "agent"
    assert lasting.token.expires_at is None
    assert limited.secret not in store.token_rows
    assert [token.id for token in service.tokens("user-1")] == [
        limited.token.id,
        lasting.token.id,
    ]
    principal = service.authenticate(
        session_secret=session.secret,
        bearer_token=limited.secret,
    )
    assert principal is not None
    assert (principal.kind, principal.token_id) == ("token", limited.token.id)
    assert (
        service.authenticate(
            session_secret=session.secret,
            bearer_token=_MALFORMED,
        )
        is None
    )
    assert service.authenticate(bearer_token=new_secret()) is None

    clock.advance(days=1)
    assert service.authenticate(bearer_token=limited.secret) is None
    assert service.authenticate(bearer_token=lasting.secret) is not None

    assert not service.revoke_token("user-2", lasting.token.id)
    assert service.revoke_token("user-1", lasting.token.id)
    assert service.authenticate(bearer_token=lasting.secret) is None


@pytest.mark.parametrize(
    ("name", "days"),
    [("", None), (" ", None), ("x" * 101, None), ("agent", 0), ("agent", 3651)],
)
def test_a_token_with_a_bad_name_or_lifetime_is_refused(
    name: str,
    days: int | None,
) -> None:
    """The service reports bad token input as an `input` error."""
    service, store, _ = _service()

    with pytest.raises(AuthError) as raised:
        service.create_token("user-1", name, days=days)

    assert raised.value.reason == "input"
    assert store.token_rows == {}
