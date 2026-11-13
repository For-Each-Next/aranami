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
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

import polars as pl
import pywikibot

from shared.identity import USER_AGENT

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_ROOT = "https://wikiwho-api.wmcloud.org/zh/api/v1.0.0-beta/"
_LIMIT = 32 * 1024 * 1024
_MAX_NAME = 1000
_retry_at: dt.datetime | None = None
REVISION_SCHEMA = {"revision_id": pl.Int64, "timestamp": pl.String}
TOKEN_SCHEMA = {"editor": pl.String, "origin": pl.Int64, "text": pl.String}
TOKEN_ID_SCHEMA = {**TOKEN_SCHEMA, "token_id": pl.String}


@dataclass(frozen=True, slots=True)
class RevisionSource:
    """Retain an exact historical source and its actual parent identity.

    Attributes:
        revision_id: Revision whose original wikitext was read.
        parent_revision: Actual parent, or zero for page creation.
        text: Exact original source without rendering or conversion.
    """

    revision_id: int
    parent_revision: int
    text: str


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
            "User-Agent": USER_AGENT,
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


def range_tokens(
    title: str,
    page_id: int,
    listing_revision: int,
    end_revision: int | None = None,
) -> pl.DataFrame:
    """Read the listing snapshot through a bounded half-open range.

    Args:
        title: Canonical article title for the range endpoint.
        page_id: Expected article identity.
        listing_revision: Final revision of the listing UTC day.
        end_revision: Next processed revision, excluded by WikiWho's
            half-open range. If none exists, use the exact-revision
            endpoint because an equal-endpoint range returns no data.

    Returns:
        One row per token surviving in the listing revision, retaining
        token strings, repeated occurrences and original revision IDs
        tracing surviving text to creation. The successor supplies only
        an exclusive range bound; its text is never attributed. The
        caller checks origins against the creation/listing interval.

    Raises:
        ValueError: Identity, revision, or token provenance is
            incomplete.
    """
    identifiers = (page_id, listing_revision) + (
        () if end_revision is None else (end_revision,)
    )
    if (
        any(type(value) is not int or value <= 0 for value in identifiers)
        or end_revision == listing_revision
    ):
        message = "WikiWho requires positive page and revision identifiers."
        raise ValueError(message)
    endpoint = (
        f"range_rev_content/{quote(title, safe='')}/{listing_revision}/"
        f"{end_revision}/"
        if end_revision is not None
        else f"rev_content/rev_id/{listing_revision}/"
    )
    return _tokens(
        _fetch(endpoint + "?editor=true&o_rev_id=true"),
        page_id,
        listing_revision,
    )


def _tokens(
    result: dict,
    page_id: int,
    revision_id: int,
    *,
    token_ids: bool = False,
) -> pl.DataFrame:
    """Validate one exact ordered snapshot and optional stable IDs.

    Returns:
        Typed token rows without coalescing repeated text occurrences.

    Raises:
        ValueError: The response identity or token facts are incomplete.
    """
    revisions_ = result.get("revisions")
    if (
        result.get("page_id") != page_id
        or not isinstance(revisions_, list)
        or len(revisions_) != 1
    ):
        message = "WikiWho returned an incomplete article revision range."
        raise ValueError(message)
    if (
        not isinstance(revisions_[0], dict)
        or set(revisions_[0]) != {str(revision_id)}
        or not isinstance(revisions_[0][str(revision_id)], dict)
    ):
        message = "WikiWho returned a different article revision."
        raise ValueError(message)
    rows = revisions_[0][str(revision_id)].get("tokens")
    if not isinstance(rows, list) or not rows:
        message = "WikiWho returned no usable token evidence."
        raise ValueError(message)
    values = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("str"), str):
            message = "WikiWho returned invalid token metadata."
            raise ValueError(message)  # ruff: ignore[type-check-without-type-error] - Invalid remote JSON is a source-data failure.
        if (
            not isinstance(row.get("editor"), str)
            or not re.fullmatch(r"[1-9][0-9]*|0\|[^\x00-\x20]+", row["editor"])
            or type(row.get("o_rev_id")) is not int
            or row["o_rev_id"] <= 0
        ):
            message = "WikiWho returned incomplete token attribution."
            raise ValueError(message)
        value = (row["editor"], row["o_rev_id"], row["str"])
        if token_ids:
            identifier = row.get("token_id")
            if (
                type(identifier) not in {str, int}
                or re.fullmatch(r"0|[1-9][0-9]{0,19}", str(identifier)) is None
            ):
                message = "WikiWho returned an invalid stable token identity."
                raise ValueError(message)
            value += (str(identifier),)
        values.append(value)
    frame = pl.DataFrame(
        values,
        schema=TOKEN_ID_SCHEMA if token_ids else TOKEN_SCHEMA,
        orient="row",
    )
    if token_ids and frame["token_id"].n_unique() != frame.height:
        message = "WikiWho returned duplicate surviving token identities."
        raise ValueError(message)
    return frame


def revision_tokens(page_id: int, revision_id: int) -> pl.DataFrame:
    """Read stable token identities for an exact provenance proof.

    Args:
        page_id: Expected positive Chinese Wikipedia article identity.
        revision_id: Exact snapshot, without range or latest inference.

    Returns:
        Ordered origin/editor/text rows with unique stable token IDs.

    Raises:
        ValueError: Page or revision identity is invalid.
    """
    if any(
        type(value) is not int or value <= 0
        for value in (page_id, revision_id)
    ):
        message = "WikiWho proof snapshots require positive identities."
        raise ValueError(message)
    return _tokens(
        _fetch(
            f"rev_content/rev_id/{revision_id}/"
            "?editor=true&o_rev_id=true&token_id=true",
        ),
        page_id,
        revision_id,
        token_ids=True,
    )


def revision_source(
    site: BaseSite,
    title: str,
    page_id: int,
    revision_id: int,
) -> RevisionSource:
    """Read an exact source and parent through high-level Pywikibot.

    Args:
        site: Existing Chinese Wikipedia source client.
        title: Canonical subject title whose history is being checked.
        page_id: Expected stable article identity.
        revision_id: Exact historical source revision.

    Returns:
        Original source and independently observed parent revision.

    Raises:
        ValueError: Source identity, parent or content is unavailable.
    """
    page = pywikibot.Page(site, title)
    if page.pageid != page_id:
        message = "Authorship proof article identity changed."
        raise ValueError(message)
    row = page.get_revision(revision_id, content=True)
    parent = row.get("parentid")
    if not isinstance(row.text, str) or len(row.text.encode()) > _LIMIT:
        message = "Authorship proof source text is unavailable."
        raise ValueError(message)
    if (
        row.get("revid") != revision_id
        or type(parent) is not int
        or parent < 0
        or parent == revision_id
    ):
        message = "Authorship proof source or parent is unavailable."
        raise ValueError(message)
    return RevisionSource(revision_id, parent, row.text)


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


def revision_text(
    site: BaseSite,
    title: str,
    page_id: int,
    revision_id: int,
) -> str:
    """Read exact listing-revision wikitext for byte-score alignment.

    Args:
        site: Existing Chinese Wikipedia source site.
        title: Canonical title whose history contains the revision.
        page_id: Expected stable article identifier.
        revision_id: Exact historical listing-day revision.

    Returns:
        Original wikitext from the selected historical revision.

    Raises:
        ValueError: Identity changed or text is absent or oversized.
    """
    page = pywikibot.Page(site, title)
    if page.pageid != page_id:
        message = "Listing article identity changed."
        raise ValueError(message)
    text = page.get_revision(revision_id, content=True).text
    if not isinstance(text, str) or len(text.encode()) > _LIMIT:
        message = "Listing article text is unavailable or oversized."
        raise ValueError(message)
    return text


def listing_metadata(
    site: BaseSite,
    title: str,
    page_id: int,
    cutoff: dt.datetime,
) -> tuple[int, int, dt.datetime]:
    """Resolve the actual listing revision and creation instant on-wiki.

    A live metadata read prevents lagging replica or WikiWho snapshots
    from substituting an earlier revision for the listing revision.

    Args:
        site: Existing Chinese Wikipedia site.
        title: Article title to resolve.
        page_id: Expected article identity.
        cutoff: Exclusive UTC boundary after the listing date.

    Returns:
        Exact listing revision ID, first revision ID and creation time.

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
    return selected.revid, first.revid, creation
