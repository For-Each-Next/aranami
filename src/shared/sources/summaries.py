"""Read Chinese Wikipedia lead text from the Page Previews summary API.

The Page Content Service supplies the first paragraph used by previews.
Pywikibot has no high-level PCS summary reader, so this adapter isolates
the REST request. Callers own caching and display-length decisions;
downloaded HTML is never returned or executed.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from http import HTTPStatus
from urllib.parse import quote

from shared.deployment import project_version

_API_ROOT = "https://zh.wikipedia.org/api/rest_v1/page/summary/"
_RESPONSE_LIMIT = 128 * 1024


def fetch_summary(title: str, *, variant: str = "zh") -> str | None:
    """Fetch one article's plain-text introductory paragraph.

    Args:
        title: Canonical Chinese Wikipedia title, without URL encoding.
        variant: Original or script-specific Chinese preference.

    Returns:
        The plain-text lead, or none for missing/non-article pages and
        explicitly empty extracts. One request attempt is made.

    Raises:
        ValueError: The title or response violates the summary contract.
        TypeError: A response extract is not plain text.
        urllib.error.HTTPError: The response fails other than HTTP 404.
        urllib.error.URLError: The request fails other than HTTP 404.
    """  # ruff: ignore[docstring-extraneous-exception]
    if not title.strip() or re.search(r"[\x00-\x1f\x7f]", title):
        message = "A summary requires a nonempty article title."
        raise ValueError(message)
    if variant not in {"zh", "zh-hans", "zh-hant"}:
        message = "A summary requires a supported Chinese script variant."
        raise ValueError(message)
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage] - Fixed HTTPS origin and encoded title.
        _API_ROOT + quote(title, safe=""),
        headers={
            "Accept": "application/json",
            "Accept-Language": variant,
            "User-Agent": (
                f"Aranami/{project_version()} (https://aranami.toolforge.org/)"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # ruff: ignore[suspicious-url-open-usage] - Fixed HTTPS origin.
            content = response.read(_RESPONSE_LIMIT + 1)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code != HTTPStatus.NOT_FOUND:
            raise
        return None
    if len(content) > _RESPONSE_LIMIT:
        message = "The summary response exceeds its size limit."
        raise ValueError(message)
    result = json.loads(content)
    if (
        not isinstance(result, dict)  # ruff: ignore[too-many-boolean-expressions]
        or result.get("type")
        not in {"standard", "disambiguation", "no-extract"}
        or not isinstance(result.get("title"), str)
        or type(result.get("pageid")) is not int
        or result["pageid"] <= 0
        or not isinstance(result.get("namespace"), dict)
        or type(result["namespace"].get("id")) is not int
    ):
        message = "The summary response has invalid article metadata."
        raise ValueError(message)
    if result["namespace"]["id"] != 0:
        return None
    extract = result.get("extract")
    if extract is None and result["type"] == "no-extract":
        return None
    if not isinstance(extract, str):
        message = "The summary response has no plain-text extract."
        raise TypeError(message)
    return extract.strip() or None
