"""Validate configurable provider session headers without transport state."""

from opendle._internal.http import HeaderProtocolError, normalize_headers

__all__ = ["build_session_headers", "validate_session_header_name"]

_MAXIMUM_LENGTH = 255
_RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
        "te",
        "trailer",
        "upgrade",
        "accept",
        "accept-encoding",
        "user-agent",
        "x-api-key",
        "api-key",
        "anthropic-version",
        "anthropic-beta",
        "openai-organization",
        "openai-project",
        "http-referer",
        "x-title",
    }
)


def validate_session_header_name(header_name: str) -> None:
    """Accept a blank setting or a non-reserved HTTP token of at most 255 characters.

    Raise ValueError for an invalid name. Names are preserved exactly; reserved
    transport, authentication, and provider control names are case-insensitive.
    """
    if not header_name:
        return
    if len(header_name) > _MAXIMUM_LENGTH:
        msg = "The session header name must have at most 255 characters."
        raise ValueError(msg)
    if header_name.casefold() in _RESERVED_HEADERS:
        msg = "The session header name is reserved for another HTTP function."
        raise ValueError(msg)
    try:
        normalize_headers(
            ((header_name, ""),),
            maximum_count=1,
            maximum_bytes=_MAXIMUM_LENGTH + 4,
            critical_headers=frozenset(),
        )
    except HeaderProtocolError:
        msg = "The session header name must be a valid HTTP field name."
        raise ValueError(msg) from None


def build_session_headers(header_name: str, session_id: str) -> dict[str, str]:
    """Build one header from a configured name and a caller-owned session ID.

    A blank name returns an empty dictionary without checking the ID. Otherwise,
    raise ValueError for an invalid name or an ID outside 1 to 255 printable
    ASCII characters without whitespace. The caller owns ID creation and reuse.
    Each call returns a new dictionary and preserves the supplied name and ID.
    """
    # USER DECISION: Configure the header name so other providers can use it.
    validate_session_header_name(header_name)
    if not header_name:
        return {}
    if not 1 <= len(session_id) <= _MAXIMUM_LENGTH or any(
        not "!" <= character <= "~" for character in session_id
    ):
        msg = "The session ID must have 1 to 255 ASCII characters without whitespace."
        raise ValueError(msg)
    return {header_name: session_id}
