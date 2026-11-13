"""Select saved redirect-recovery findings for bounded web pages.

Selection never reads Wikipedia or changes saved scan results. Browser
hiding is deliberately separate from this complete saved worklist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from webservice.presentation import display_title, local_url

if TYPE_CHECKING:
    from webservice.presentation import Record

_PAGE_SIZES = {10, 20}
_MAX_PAGE = 100_000


def _assessor_url(url: str, *, redirect: bool = False) -> str:
    """Open a saved article URL with the WikiProject assessor gadget.

    Returns:
        The article URL with its existing query and fragment preserved.
    """
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "wpvg-assessor" and (not redirect or key != "redirect")
    ]
    if redirect:
        query.append(("redirect", "no"))
    query.append(("wpvg-assessor", "1"))
    return urlunsplit(parts._replace(query=urlencode(query)))


def redirect_selection(
    snapshot: Record | None,
    parameters: dict[str, list[str]],
    locale: str,
) -> Record:
    """Paginate the complete saved findings without discarding evidence.

    Args:
        snapshot: Validated incremental scan data, when available.
        parameters: Untrusted page and page-size query selections.
        locale: Selected interface language retained by navigation.

    Returns:
        A bounded selection with native previous and next page links.

    Raises:
        ValueError: Query fields repeat or selections are unsupported.
    """
    if set(parameters) - {"page", "limit"} or any(
        len(values) != 1
        or not values[0].isascii()
        or not values[0].isdecimal()
        for values in parameters.values()
    ):
        message = "Choose one page and supported page size."
        raise ValueError(message)
    page = int(parameters.get("page", ["1"])[0])
    limit = int(parameters.get("limit", ["10"])[0])
    if not 1 <= page <= _MAX_PAGE or limit not in _PAGE_SIZES:
        message = "Page or page size is outside its supported range."
        raise ValueError(message)
    rows = (snapshot or {}).get("items", [])
    total = len(rows)
    last = max(1, (total + limit - 1) // limit)
    page = min(page, last)
    start = (page - 1) * limit

    def url(number: int) -> str:
        """Retain language and page size in a native navigation link.

        Returns:
            A same-site redirect worklist URL.
        """
        return local_url("/redirects/", page=number, limit=limit, lang=locale)

    return {
        "items": [
            {
                **row,
                "title_label": display_title(row["title"], locale),
                "assessor_url": _assessor_url(row["url"], redirect=True),
                "content_assessor_url": _assessor_url(row["content_url"])
                if row["content_url"]
                else None,
            }
            for row in rows[start : start + limit]
        ],
        "page": page,
        "limit": limit,
        "total": total,
        "first": start + 1 if total else 0,
        "last": min(start + limit, total),
        "pages": last,
        "previous": url(page - 1) if page > 1 else None,
        "following": url(page + 1) if page < last else None,
    }
