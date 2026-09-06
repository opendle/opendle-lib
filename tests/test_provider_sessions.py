"""Tests for configurable provider session headers."""

import pytest

from opendle.provider_sessions import (
    build_session_headers,
    validate_session_header_name,
)


def test_blank_header_disables_session_headers() -> None:
    """A disabled setting does not require a session ID."""
    validate_session_header_name("")
    assert build_session_headers("", "") == {}


@pytest.mark.parametrize("name", ["x-opencode-session", "X-Conversation", "x" * 255])
def test_custom_header_preserves_name_and_session(name: str) -> None:
    """Return the caller's exact name and ID without shared mutable state."""
    validate_session_header_name(name)
    first = build_session_headers(name, "conversation-42")
    first[name] = "changed"
    assert build_session_headers(name, "conversation-42") == {name: "conversation-42"}


@pytest.mark.parametrize("name", ["x y", "x:y", "x\r\ny", "x\n", "é", "x" * 256])
def test_invalid_header_name_is_rejected(name: str) -> None:
    """Reject names that cannot be HTTP field names or exceed the bound."""
    with pytest.raises(ValueError, match="session header"):
        validate_session_header_name(name)
    with pytest.raises(ValueError, match="session header"):
        build_session_headers(name, "session")


@pytest.mark.parametrize(
    "name",
    [
        "Authorization",
        "Proxy-Authorization",
        "Cookie",
        "Set-Cookie",
        "Host",
        "Content-Length",
        "Content-Type",
        "Transfer-Encoding",
        "Connection",
        "TE",
        "Trailer",
        "Upgrade",
        "Accept",
        "Accept-Encoding",
        "User-Agent",
        "X-Api-Key",
        "Api-Key",
        "Anthropic-Version",
        "Anthropic-Beta",
        "OpenAI-Organization",
        "OpenAI-Project",
        "HTTP-Referer",
        "X-Title",
    ],
)
def test_reserved_headers_are_rejected_case_insensitively(name: str) -> None:
    """A session setting must not replace transport or provider controls."""
    for variant in (name, name.lower(), name.upper()):
        with pytest.raises(ValueError, match="session header"):
            validate_session_header_name(variant)


@pytest.mark.parametrize(
    "session_id", ["", "x y", "\t", "\r\n", "é", "\x7f", "\x00", "x" * 256]
)
def test_invalid_session_id_is_rejected(session_id: str) -> None:
    """Reject blank, non-ASCII, control, whitespace, and oversized IDs."""
    with pytest.raises(ValueError, match="session ID"):
        build_session_headers("X-Session", session_id)


def test_http_token_punctuation_and_session_bound_are_accepted() -> None:
    """Accept the full token alphabet and the exact session length bound."""
    name = (
        "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    )
    assert build_session_headers(name, "!" * 255) == {name: "!" * 255}
