"""Private bounded cursor iteration shared by public clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class CursorCycleError(RuntimeError):
    """Report a repeated cursor in one page sequence."""


class PageLimitError(RuntimeError):
    """Report a page sequence that exceeds its caller bound."""


def iterate_cursor_pages[PageT](
    load: Callable[[str | None], PageT],
    next_cursor: Callable[[PageT], str | None],
    *,
    maximum_pages: int,
    initial_cursor: str | None,
) -> Iterator[PageT]:
    """Iterate one cursor sequence with page and cycle bounds."""
    cursor = initial_cursor
    seen: set[str] = {cursor} if cursor is not None else set()
    for _page_number in range(maximum_pages):
        page = load(cursor)
        following_cursor = next_cursor(page)
        if following_cursor is not None and following_cursor in seen:
            raise CursorCycleError
        yield page
        if following_cursor is None:
            return
        cursor = following_cursor
        seen.add(cursor)
    raise PageLimitError
