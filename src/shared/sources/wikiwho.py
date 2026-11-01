"""Read bounded Chinese Wikipedia token provenance from WikiWho.

The caller selects historical revisions and owns persistence. No
credentials are sent to WikiWho. User-ID lookup uses the Action API
because Pywikibot's users method accepts names, not numeric identifiers.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import polars as pl
import pywikibot

from shared.deployment import project_version

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

CONTRACT = "wikiwho-zh-v1.0.0-beta-token-count-v1"
_ROOT = "https://wikiwho-api.wmcloud.org/zh/api/v1.0.0-beta/"
_LIMIT = 32 * 1024 * 1024
_MAX_NAME = 1000
_retry_at: dt.datetime | None = None
REVISION_SCHEMA = {"revision_id": pl.Int64, "timestamp": pl.String}
TOKEN_SCHEMA = {"editor": pl.String, "origin": pl.Int64}


class DeferredError(RuntimeError):
    """Signal that a remote service has asked this process to wait."""


def _defer(error: urllib.error.HTTPError) -> None:
    """Remember Retry-After without blocking a collection worker."""
    global _retry_at  # ruff: ignore[global-statement] - Process-local service cooldown.
    now = dt.datetime.now(dt.UTC)
    header = error.headers.get("Retry-After", "60")
    try:
        delay = max(1, int(header))
        retry_at = now + dt.timedelta(seconds=delay)
    except ValueError, OverflowError:
        try:
            retry_at = parsedate_to_datetime(header).astimezone(dt.UTC)
        except ValueError, TypeError, OverflowError:
            retry_at = now + dt.timedelta(minutes=1)
    _retry_at = max(now + dt.timedelta(seconds=1), retry_at)


def _fetch(endpoint: str) -> dict:
    """Read one bounded JSON response without immediate retries.

    Returns:
        Decoded response object.

    Raises:
        ValueError: WikiWho returned incomplete or oversized data.
        DeferredError: A previous response requests a later retry.
        urllib.error.HTTPError: The remote server rejected the request.
    """
    if _retry_at is not None and dt.datetime.now(dt.UTC) < _retry_at:
        message = "WikiWho requests are deferred by Retry-After."
        raise DeferredError(message)
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        _ROOT + endpoint,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                f"Aranami/{project_version()} (https://aranami.toolforge.org/)"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:  # ruff: ignore[suspicious-url-open-usage]
            content = response.read(_LIMIT + 1)
    except urllib.error.HTTPError as error:
        if error.code in {429, 503}:
            _defer(error)
        raise
    if len(content) > _LIMIT:
        message = "WikiWho response exceeds its size bound."
        raise ValueError(message)
    result = json.loads(content)
    if not isinstance(result, dict) or result.get("success") is not True:
        message = "WikiWho has no complete provenance result."
        raise ValueError(message)
    return result


def revisions(page_id: int) -> pl.DataFrame:
    """Read processed revision IDs and timestamps for one article.

    Args:
        page_id: Positive Chinese Wikipedia article identifier.

    Returns:
        Explicitly typed historical revision metadata.

    Raises:
        ValueError: The response has wrong identity or malformed
            metadata.
    """
    if type(page_id) is not int or page_id <= 0:
        message = "WikiWho requires a positive article identifier."
        raise ValueError(message)
    result = _fetch(f"rev_ids/page_id/{page_id}/?timestamp=true")
    rows = result.get("revisions")
    if result.get("page_id") != page_id or not isinstance(rows, list):
        message = "WikiWho returned inconsistent article metadata."
        raise ValueError(message)
    values = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or type(row.get("id")) is not int
            or row["id"] <= 0
            or not isinstance(row.get("timestamp"), str)
        ):
            message = "WikiWho returned invalid revision metadata."
            raise ValueError(message)
        values.append((row["id"], row["timestamp"]))
    return pl.DataFrame(values, schema=REVISION_SCHEMA, orient="row")


def tokens(page_id: int, revision_id: int) -> pl.DataFrame:
    """Read original authors of all surviving wikitext tokens.

    Args:
        page_id: Expected article identity.
        revision_id: Exact historical revision to attribute.

    Returns:
        One row per surviving token, retaining repeated token strings.

    Raises:
        ValueError: Identity, revision, or token provenance is
            incomplete.
    """
    if min(page_id, revision_id) <= 0:
        message = "WikiWho requires positive page and revision identifiers."
        raise ValueError(message)
    result = _fetch(
        f"rev_content/rev_id/{revision_id}/?editor=true&o_rev_id=true",
    )
    revisions_ = result.get("revisions")
    if (
        result.get("page_id") != page_id
        or not isinstance(revisions_, list)
        or len(revisions_) != 1
        or not isinstance(revisions_[0], dict)
        or set(revisions_[0]) != {str(revision_id)}
    ):
        message = "WikiWho returned the wrong article revision."
        raise ValueError(message)
    rows = revisions_[0][str(revision_id)].get("tokens")
    if not isinstance(rows, list) or not rows:
        message = "WikiWho returned no usable token evidence."
        raise ValueError(message)
    values = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("editor"), str)
            or not re.fullmatch(r"[1-9][0-9]*|0\|[^\x00-\x20]+", row["editor"])
            or type(row.get("o_rev_id")) is not int
            or row["o_rev_id"] <= 0
        ):
            message = "WikiWho returned incomplete token attribution."
            raise ValueError(message)
        values.append((row["editor"], row["o_rev_id"]))
    return pl.DataFrame(values, schema=TOKEN_SCHEMA, orient="row")


def usernames(site: BaseSite, identifiers: list[str]) -> dict[str, str]:
    """Resolve registered IDs and preserve anonymous identities.

    Args:
        site: Existing Chinese Wikipedia site.
        identifiers: WikiWho editor identifiers.

    Returns:
        Safe names only; hidden or unresolvable users remain absent.
    """
    result = {
        value: value[2:] for value in identifiers if value.startswith("0|")
    }
    registered = [value for value in identifiers if value.isdecimal()]
    for offset in range(0, len(registered), 50):
        response = site.simple_request(
            action="query",
            list="users",
            ususerids="|".join(registered[offset : offset + 50]),
        ).submit()
        for user in response.get("query", {}).get("users", []):
            if "missing" not in user and "invalid" not in user:
                result[str(user.get("userid"))] = user.get("name")
    return {
        key: name
        for key, name in result.items()
        if isinstance(name, str)
        and 0 < len(name.encode()) <= _MAX_NAME
        and name.strip()
        and not re.search(r"[\[\]{}|<>\x00-\x1f\x7f]", name)
    }


def listing_metadata(
    site: BaseSite,
    title: str,
    page_id: int,
    cutoff: dt.datetime,
) -> tuple[int, dt.datetime]:
    """Resolve the actual listing revision and creation instant on-wiki.

    A live metadata read prevents lagging replica or WikiWho snapshots
    from substituting an earlier revision for the listing revision.

    Args:
        site: Existing Chinese Wikipedia site.
        title: Article title to resolve.
        page_id: Expected article identity.
        cutoff: Exclusive UTC boundary after the listing date.

    Returns:
        Exact listing revision ID and article creation timestamp.

    Raises:
        ValueError: Identity or historical metadata is unavailable.
    """
    page = pywikibot.Page(site, title)
    if page.pageid != page_id:
        message = "Listing article identity changed."
        raise ValueError(message)
    selected = next(
        page.revisions(
            content=False,
            starttime=pywikibot.Timestamp.fromISOformat(
                (cutoff - dt.timedelta(seconds=1)).isoformat(),
            ),
            total=1,
        ),
        None,
    )
    first = next(page.revisions(content=False, reverse=True, total=1), None)
    if selected is None or first is None:
        message = "Listing revision metadata is unavailable."
        raise ValueError(message)
    creation = first.timestamp.replace(tzinfo=dt.UTC)
    if not creation <= selected.timestamp.replace(tzinfo=dt.UTC) < cutoff:
        message = "Listing revision lies outside its historical interval."
        raise ValueError(message)
    return selected.revid, creation
