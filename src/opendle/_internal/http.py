"""Private bounded standard-library HTTP and header helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, Self, cast, override
from urllib.error import HTTPError
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping
    from http.client import HTTPMessage
    from urllib.request import OpenerDirector

_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_ASCII_SPACE = 0x20
_ASCII_DELETE = 0x7F


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Contain one complete private HTTP response."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class HttpStreamResponse:
    """Contain one private streamed HTTP response."""

    status: int
    headers: tuple[tuple[str, str], ...]
    chunks: Iterable[bytes]
    close: Callable[[], None]


class HeaderErrorReason(Enum):
    """Name one private HTTP header rejection reason."""

    BYTE_LIMIT = "byte_limit"
    COUNT_LIMIT = "count_limit"
    DUPLICATE = "duplicate"
    INVALID_NAME = "invalid_name"
    INVALID_TYPE = "invalid_type"
    INVALID_UNICODE = "invalid_unicode"
    INVALID_VALUE = "invalid_value"


class HeaderProtocolError(ValueError):
    """Report an invalid HTTP response header."""

    def __init__(self, reason: HeaderErrorReason) -> None:
        """Initialize one private header error."""
        self.reason = reason
        super().__init__(reason.value)


class HeaderLimitError(HeaderProtocolError):
    """Report HTTP response headers that exceed a safety bound."""


class _UrlResponse(Protocol):
    """Supply the standard-library response operations that are required."""

    status: int
    headers: HTTPMessage

    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> None: ...

    def close(self) -> None: ...

    def read(self, amount: int | None = None) -> bytes: ...


class _NoRedirect(HTTPRedirectHandler):
    """Reject all redirects before credentials can move to another URL."""

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
        del req, fp, code, msg, headers, newurl


class UrllibHttpClient:
    """Send bounded complete and streamed requests without redirects."""

    def __init__(
        self,
        maximum_response_bytes: int,
        *,
        maximum_error_response_bytes: int | None = None,
        use_environment_proxy: bool = False,
    ) -> None:
        """Initialize the private transport bounds and proxy policy."""
        self._maximum_response_bytes = maximum_response_bytes
        self._maximum_error_response_bytes = (
            maximum_response_bytes
            if maximum_error_response_bytes is None
            else maximum_error_response_bytes
        )
        handlers = (
            (_NoRedirect(),)
            if use_environment_proxy
            else (ProxyHandler({}), _NoRedirect())
        )
        self._opener = build_opener(*handlers)

    @property
    def opener(self) -> object:
        """Return the active opener for a host adapter or focused test."""
        return self._opener

    def replace_opener(self, opener: object) -> None:
        """Replace the opener for a host adapter or focused test."""
        self._opener = cast("OpenerDirector", opener)

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """Return one bounded complete response."""
        request = Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
        try:
            with cast(
                "_UrlResponse", self._opener.open(request, timeout=timeout)
            ) as response:
                return HttpResponse(
                    response.status,
                    tuple(response.headers.raw_items()),
                    response.read(self._maximum_response_bytes + 1),
                )
        except HTTPError as error:
            try:
                return HttpResponse(
                    error.code,
                    tuple(error.headers.raw_items()) if error.headers else (),
                    error.read(self._maximum_error_response_bytes + 1),
                )
            finally:
                error.close()

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> HttpStreamResponse:
        """Return one response with lazily read byte chunks."""
        request = Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
        try:
            response = cast("_UrlResponse", self._opener.open(request, timeout=timeout))
        except HTTPError as error:
            try:
                return HttpStreamResponse(
                    error.code,
                    tuple(error.headers.raw_items()) if error.headers else (),
                    (error.read(self._maximum_error_response_bytes + 1),),
                    lambda: None,
                )
            finally:
                error.close()

        try:
            status = response.status
            response_headers = tuple(response.headers.raw_items())
        except BaseException:
            response.close()
            raise

        def chunks() -> Iterator[bytes]:
            with response:
                while chunk := response.read(65_536):
                    yield chunk

        return HttpStreamResponse(status, response_headers, chunks(), response.close)


def normalize_headers(
    items: Iterable[tuple[str, str]],
    *,
    maximum_count: int,
    maximum_bytes: int,
    critical_headers: frozenset[str],
) -> dict[str, str]:
    """Validate and normalize one bounded sequence of response headers."""
    result: dict[str, str] = {}
    seen_critical: set[str] = set()
    total_bytes = 0
    raw_items = cast("Iterable[tuple[object, object]]", items)
    for count, (name, value) in enumerate(raw_items, start=1):
        if count > maximum_count:
            raise HeaderLimitError(HeaderErrorReason.COUNT_LIMIT)
        if not isinstance(name, str) or not isinstance(value, str):
            raise HeaderProtocolError(HeaderErrorReason.INVALID_TYPE)
        try:
            total_bytes += len(name.encode()) + len(value.encode()) + 4
        except UnicodeEncodeError:
            raise HeaderProtocolError(HeaderErrorReason.INVALID_UNICODE) from None
        if total_bytes > maximum_bytes:
            raise HeaderLimitError(HeaderErrorReason.BYTE_LIMIT)
        if _HEADER_NAME.fullmatch(name) is None:
            raise HeaderProtocolError(HeaderErrorReason.INVALID_NAME)
        if any(
            (ord(character) < _ASCII_SPACE and character != "\t")
            or ord(character) == _ASCII_DELETE
            for character in value
        ):
            raise HeaderProtocolError(HeaderErrorReason.INVALID_VALUE)
        normalized = name.casefold()
        if normalized in critical_headers:
            if normalized in seen_critical:
                raise HeaderProtocolError(HeaderErrorReason.DUPLICATE)
            seen_critical.add(normalized)
        result[name] = value
    return result


def header_value(headers: Mapping[str, str], name: str) -> str:
    """Return one case-insensitive header value or an empty value."""
    for key, value in headers.items():
        if key.casefold() == name.casefold():
            return value
    return ""


def media_type(headers: Mapping[str, str], name: str = "Content-Type") -> str:
    """Return one normalized HTTP media type without parameters."""
    return header_value(headers, name).split(";", 1)[0].strip().casefold()
