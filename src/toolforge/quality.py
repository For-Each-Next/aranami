"""Collect recorded Wikipedia quality badges from Wikidata.

Supply project Wikidata item IDs to receive an explicit-schema Polars
frame and quality counts for every recorded Wikipedia language edition.
The source is Wikidata's current sitelink badges, which may lag local
status changes. Every language uses FA, FL, GA, and recommended badges.
Dates, nominations, and unsupported DYK badges are not inferred.
``collect_video_game_quality`` directly queries items whose instance of
is video game (Q7889). It returns Wikipedia editions and their same-item
Chinese companion links without requiring project membership batches.
Companion classes use recognized Chinese sitelink badges as a fallback
for callers that enrich them with local WikiProject assessments.

The badge identities and supported badges were verified through the
Wikidata API on 2026-10-18. API contracts are documented at
https://www.wikidata.org/wiki/Special:ApiHelp/wbgetentities and
https://www.mediawiki.org/wiki/Extension:SiteMatrix#API.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import polars as pl
from pywikibot.comms import http

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_API_URL = "https://www.wikidata.org/w/api.php"
_QUERY_URL = "https://query.wikidata.org/sparql"
_BATCH_SIZE = 50
_OBSERVATION_DAYS = 30
_IDENTITY = ["item", "site", "status"]
_OBSERVED_TIME = pl.Datetime("us", "UTC")
_LOGGER = logging.getLogger(__name__)
_ITEM_ID = re.compile(r"Q[1-9][0-9]*")
_USER_AGENT = (
    "Aranami-Toolforge-Dashboard/1.0 "
    "(https://aranami.toolforge.org/; "
    "https://meta.wikimedia.org/wiki/User:For_Each_..._Next/)"
)
_STATUSES = {
    "Q17437796": "FA",
    "Q17506997": "FL",
    "Q17437798": "GA",
    "Q17559452": "recommended",
}
_VIDEO_GAME_QUERY = """
SELECT DISTINCT ?item WHERE {
  ?item wdt:P31 wd:Q7889.
  ?article schema:about ?item;
    wikibase:badge ?badge.
  VALUES ?badge { wd:Q17437796 wd:Q17506997 wd:Q17437798 wd:Q17559452 }
}
""".strip()
RECORD_SCHEMA = pl.Schema({
    "item": pl.String,
    "site": pl.String,
    "language": pl.String,
    "title": pl.String,
    "url": pl.String,
    "status": pl.String,
    "zh_title": pl.String,
    "zh_url": pl.String,
    "zh_class": pl.String,
})
_OBSERVATION_SCHEMA = pl.Schema({
    **dict(RECORD_SCHEMA),
    "badge_observed_at": _OBSERVED_TIME,
    "_active": pl.Boolean,
})


def _observation_cache(path: Path) -> pl.DataFrame | None:
    """Read a usable comparison baseline or reset an unusable cache.

    Args:
        path: Disposable Parquet baseline and recent observation file.

    Returns:
        Complete prior state, or none when a new baseline is required.
    """
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        _LOGGER.warning("Ignoring a nonregular recognition comparison cache.")
        return None
    try:
        previous = pl.read_parquet(path)
    except (OSError, pl.exceptions.PolarsError):
        _LOGGER.warning("Ignoring an unreadable recognition comparison cache.")
        return None
    if (
        previous.schema != _OBSERVATION_SCHEMA
        or previous.select(_IDENTITY).is_duplicated().any()
        or previous.filter(
            pl.any_horizontal(pl.col([*_IDENTITY, "_active"]).is_null()),
        ).height
    ):
        _LOGGER.warning("Ignoring an obsolete recognition comparison cache.")
        return None
    return previous


def _save_observations(path: Path, records: pl.DataFrame) -> None:
    """Atomically commit the complete next comparison state.

    Args:
        path: Disposable comparison cache destination.
        records: Current identities and retained recent observations.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        records.write_parquet(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def observe_quality(
    records: pl.DataFrame,
    path: Path,
    *,
    observed_at: dt.datetime,
    now: dt.datetime | None = None,
) -> pl.DataFrame:
    """Compare badge results without inferring promotion dates.

    First use establishes a baseline with no new observations, including
    when the baseline is empty. Later additions use the source query's
    UTC observation time. Identity is item, site, and badge status, so
    title or assessment changes do not create additions. A removed badge
    that returns receives a new observation time.

    Retain current identities and thirty days of observation dates in an
    atomically replaced disposable cache. Missing or invalid cache data
    establishes a new baseline; deleting it resets this history. Failed
    writes propagate and preserve the previous cache. Call only after a
    complete successful source read, before project-grade enrichment.

    Args:
        records: Complete successful raw Wikidata quality records.
        path: Disposable Parquet comparison cache destination.
        observed_at: Successful source query time, reused with TTL.
        now: Optional aware clock for the thirty-day retention boundary.

    Returns:
        Current records with nullable ISO UTC ``badge_observed_at``.
        A null date denotes baseline data or an expired observation.

    Raises:
        ValueError: Records or observation timestamps are invalid.
    """
    clock = now or dt.datetime.now(dt.UTC)
    if observed_at.tzinfo is None or clock.tzinfo is None:
        message = "Quality observations require timezone-aware timestamps."
        raise ValueError(message)
    observed_at = observed_at.astimezone(dt.UTC).replace(microsecond=0)
    clock = clock.astimezone(dt.UTC).replace(microsecond=0)
    if (
        records.schema != RECORD_SCHEMA
        or records.select(_IDENTITY).is_duplicated().any()
        or records.filter(
            pl.any_horizontal(pl.col(_IDENTITY).is_null()),
        ).height
        or observed_at > clock
    ):
        message = "Quality observation identities or timestamps are invalid."
        raise ValueError(message)
    previous = _observation_cache(path)
    if previous is None:
        current = records.with_columns(
            pl.lit(None, dtype=_OBSERVED_TIME).alias("badge_observed_at"),
            pl.lit(value=True).alias("_active"),
        )
        combined = current
    else:
        current = (
            records
            .join(
                previous.select(
                    *_IDENTITY,
                    "badge_observed_at",
                    pl.col("_active").alias("_was_active"),
                ),
                on=_IDENTITY,
                how="left",
                validate="1:1",
            )
            .with_columns(
                pl
                .when(pl.col("_was_active").fill_null(value=False))
                .then(pl.col("badge_observed_at"))
                .otherwise(pl.lit(observed_at, dtype=_OBSERVED_TIME))
                .alias("badge_observed_at"),
                pl.lit(value=True).alias("_active"),
            )
            .drop("_was_active")
        )
        removed = previous.join(
            records.select(_IDENTITY),
            on=_IDENTITY,
            how="anti",
        ).with_columns(pl.lit(value=False).alias("_active"))
        combined = pl.concat([current, removed])
    cutoff = clock - dt.timedelta(days=_OBSERVATION_DAYS)
    combined = (
        combined
        .with_columns(
            pl
            .when(pl.col("badge_observed_at") >= cutoff)
            .then(pl.col("badge_observed_at"))
            .otherwise(None)
            .alias("badge_observed_at"),
        )
        .filter(pl.col("_active") | pl.col("badge_observed_at").is_not_null())
        .select(_OBSERVATION_SCHEMA.names())
        .sort(_IDENTITY)
    )
    _save_observations(path, combined)
    return (
        combined
        .filter(pl.col("_active"))
        .drop("_active")
        .with_columns(
            pl.col("badge_observed_at").dt.to_string("%Y-%m-%dT%H:%M:%SZ"),
        )
    )


def _request(parameters: Mapping[str, str]) -> Mapping[str, object]:
    """Fetch one bounded, read-only Wikidata API response.

    Pywikibot supplies its configured ``socket_timeout`` to Requests.
    Another timeout duplicates that argument at the Requests boundary.

    Args:
        parameters: Action parameters for the fixed API endpoint.

    Returns:
        Decoded API object after HTTP status validation.

    Raises:
        TypeError: The response is not a JSON object.
    """
    response = http.fetch(
        _API_URL,
        params={
            "format": "json",
            "formatversion": "2",
            "maxlag": "5",
            **parameters,
        },
        headers={"user-agent": _USER_AGENT},
    )
    if isinstance(response, Exception):
        raise response
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        message = "Wikidata returned a non-object API response."
        raise TypeError(message)
    return value


def _object(value: object, name: str) -> Mapping[str, object]:
    """Validate an expected named API object.

    Args:
        value: Decoded source field to validate.
        name: Field description used in failure diagnostics.

    Returns:
        String-keyed mapping from the source response.

    Raises:
        TypeError: The expected API object is missing or malformed.
    """
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        message = f"Wikidata returned an invalid {name} object."
        raise TypeError(message)
    return value


def _video_game_request(
    parameters: Mapping[str, str],
) -> Mapping[str, object]:
    """Read site metadata or the fixed video-game SPARQL query.

    Args:
        parameters: Site matrix action or SPARQL query parameters.

    Returns:
        Decoded successful JSON object from the corresponding endpoint.

    Raises:
        TypeError: The SPARQL response is not a JSON object.
    """  # ruff: ignore[docstring-extraneous-exception]
    if "query" not in parameters:
        return _request(parameters)
    response = http.fetch(
        _QUERY_URL,
        params=parameters,
        headers={
            "user-agent": _USER_AGENT,
            "accept": "application/sparql-results+json",
        },
    )
    if isinstance(response, Exception):
        raise response
    response.raise_for_status()
    return _object(response.json(), "SPARQL response")


def _query_bindings(
    response: Mapping[str, object],
) -> list[object]:
    """Validate the complete SPARQL result envelope.

    Args:
        response: Decoded SPARQL JSON result.

    Returns:
        Result bindings, including an explicit empty result.

    Raises:
        TypeError: The result bindings have an unexpected type.
        ValueError: The result omits expected variables.
    """
    head = _object(response.get("head"), "SPARQL head")
    variables = head.get("vars")
    if not isinstance(variables, list) or "item" not in variables:
        message = "The video-game query omitted expected result fields."
        raise ValueError(message)
    results = _object(response.get("results"), "SPARQL results")
    rows = results.get("bindings")
    if not isinstance(rows, list):
        message = "The video-game query has no result bindings."
        raise TypeError(message)
    return rows


def _query_value(
    row: Mapping[str, object],
    name: str,
    kind: str,
) -> str:
    """Read one required RDF URI or literal binding.

    Args:
        row: One SPARQL result binding.
        name: Requested result variable.
        kind: Required SPARQL binding kind.

    Returns:
        Nonempty source value of the expected kind.

    Raises:
        ValueError: A required binding is absent or malformed.
    """
    binding = _object(row.get(name), f"SPARQL {name}")
    value = binding.get("value")
    if binding.get("type") != kind or not isinstance(value, str) or not value:
        message = f"The video-game query has an invalid {name} binding."
        raise ValueError(message)
    return value


def _item_from_uri(value: str) -> str:
    """Validate a canonical Wikidata entity URI before reading its ID.

    Args:
        value: Entity URI supplied by the query service.

    Returns:
        Validated item identifier.

    Raises:
        ValueError: The URI does not identify a Wikidata item.
    """
    target = urlsplit(value)
    item = target.path.removeprefix("/entity/")
    if (
        target.scheme not in {"http", "https"}
        or target.netloc != "www.wikidata.org"
        or target.query
        or target.fragment
        or _ITEM_ID.fullmatch(item) is None
    ):
        message = "The video-game query has an invalid entity URI."
        raise ValueError(message)
    return item


def _api_result(value: object) -> Mapping[str, object]:
    """Reject API errors before interpreting source data.

    Args:
        value: Decoded response from the read-only transport.

    Returns:
        Successful source response mapping.

    Raises:
        ValueError: The API returned an error or an invalid response.
    """
    result = _object(value, "response")
    if "error" in result:
        error = _object(result["error"], "error")
        message = f"Wikidata API failed: {error.get('code', 'unknown')}."
        raise ValueError(message)
    return result


def _wikipedia_sites(
    fetch: Callable[[Mapping[str, str]], Mapping[str, object]],
) -> dict[str, tuple[str, str]]:
    """Read Wikipedia identities from the official site matrix once.

    Args:
        fetch: API transport receiving action-specific parameters.

    Returns:
        Public open Wikipedia database IDs with language and base URL.

    Raises:
        TypeError: Site identities contain values of unexpected types.
        ValueError: Site identities are absent or incomplete.
    """
    response = _api_result(
        fetch({
            "action": "sitematrix",
            "smtype": "language",
            "smlangprop": "code|site",
            "smsiteprop": "code|dbname|url",
            "smlimit": "max",
        }),
    )
    if "continue" in response:
        message = "The Wikidata site matrix response is incomplete."
        raise ValueError(message)
    matrix = _object(response.get("sitematrix"), "site matrix")
    sites: dict[str, tuple[str, str]] = {}
    for key, value in matrix.items():
        if not key.isdecimal():
            continue
        group = _object(value, "language group")
        language = group.get("code")
        entries = group.get("site")
        if not isinstance(language, str) or not isinstance(entries, list):
            message = "The Wikidata language group has invalid site data."
            raise TypeError(message)
        for site_value in entries:
            entry = _object(site_value, "site")
            if entry.get("code") != "wiki" or any(
                flag in entry for flag in ("closed", "private", "fishbowl")
            ):
                continue
            identifier, url = entry.get("dbname"), entry.get("url")
            if not isinstance(identifier, str) or not isinstance(url, str):
                message = "A Wikipedia site has no database ID or URL."
                raise TypeError(message)
            origin = urlsplit(url)
            if origin.scheme != "https" or not origin.netloc:
                message = "A Wikipedia site has an invalid public URL."
                raise ValueError(message)
            sites[identifier] = (language, url)
    if not sites:
        message = "The Wikidata site matrix contains no public Wikipedias."
        raise ValueError(message)
    return sites


def _article_link(value: object, base: str) -> tuple[str, str]:
    """Read an article link and validate its official Wikipedia origin.

    Args:
        value: Sitelink object supplied by Wikidata.
        base: Official base URL from the site matrix.

    Returns:
        Source title and URL without synthesizing missing identities.

    Raises:
        ValueError: The title or URL is missing or has the wrong origin.
    """
    link = _object(value, "sitelink")
    title, url = link.get("title"), link.get("url")
    if not isinstance(title, str) or not title or not isinstance(url, str):
        message = "A quality sitelink has no article title or URL."
        raise ValueError(message)
    target, origin = urlsplit(url), urlsplit(base)
    if target.scheme != origin.scheme or target.netloc != origin.netloc:
        message = "A quality sitelink URL does not match its Wikipedia."
        raise ValueError(message)
    return title, url


def _chinese_link(
    links: Mapping[str, object],
    sites: Mapping[str, tuple[str, str]],
) -> tuple[str | None, str | None, str | None]:
    """Read only the same item's exact Chinese Wikipedia association.

    Args:
        links: Current entity's sitelinks.
        sites: Official Wikipedia site identities.

    Returns:
        Canonical Chinese title, URL, and recognized badge fallback.
        All values are null when there is no Chinese association.

    Raises:
        ValueError: Chinese site metadata or badges are malformed.
    """
    if "zhwiki" not in links:
        return None, None, None
    if "zhwiki" not in sites:
        message = "Chinese Wikipedia is missing from the site matrix."
        raise ValueError(message)
    link = _object(links["zhwiki"], "Chinese sitelink")
    title, url = _article_link(link, sites["zhwiki"][1])
    badges = link.get("badges")
    if not isinstance(badges, list) or any(
        not isinstance(badge, str) for badge in badges
    ):
        message = "The Chinese sitelink has invalid quality badges."
        raise ValueError(message)
    grade = next(
        (status for badge, status in _STATUSES.items() if badge in badges),
        None,
    )
    return title, url, grade


def _sitelink_records(
    item: str,
    links: Mapping[str, object],
    sites: Mapping[str, tuple[str, str]],
) -> list[dict[str, str | None]]:
    """Read recognized badges from validated Wikipedia sitelinks.

    Args:
        item: Canonical Wikidata item identifier.
        links: Sitelinks returned for this entity.
        sites: Official Wikipedia site identities and languages.

    Returns:
        Presentation records for every recognized badge association.

    Raises:
        ValueError: A relevant sitelink has malformed identity fields.
    """
    records: list[dict[str, str | None]] = []
    zh_title, zh_url, zh_class = _chinese_link(links, sites)
    for site, value in links.items():
        if site not in sites:
            continue
        link = _object(value, "sitelink")
        badges = link.get("badges")
        if not isinstance(badges, list) or any(
            not isinstance(badge, str) for badge in badges
        ):
            message = "A Wikipedia sitelink has invalid quality badges."
            raise ValueError(message)
        statuses = [_STATUSES[badge] for badge in badges if badge in _STATUSES]
        if not statuses:
            continue
        language, base = sites[site]
        title, url = _article_link(link, base)
        records.extend(
            {
                "item": item,
                "site": site,
                "language": language,
                "title": title,
                "url": url,
                "status": status,
                "zh_title": zh_title,
                "zh_url": zh_url,
                "zh_class": zh_class,
            }
            for status in statuses
        )
    return records


def _summary(
    records: list[dict[str, str | None]] | pl.DataFrame,
) -> dict[str, object]:
    """Summarize every observed status before presentation limits.

    Args:
        records: Parsed badge associations or a filtered typed frame.

    Returns:
        Typed frame, language/status counts, and distinct page totals.
    """
    frame = (
        pl
        .DataFrame(records, schema=RECORD_SCHEMA)
        .unique()
        .sort(
            ["language", "status", "title", "item"],
        )
    )
    counts = (
        frame
        .group_by("language", "status")
        .len(name="count")
        .sort(
            ["language", "status"],
        )
    )
    return {
        "records": frame,
        "counts": counts.to_dicts(),
        "total": frame.select("item", "site").unique().height,
        "languages": frame["language"].n_unique(),
    }


def _batch_entities(
    response: Mapping[str, object],
    requested: list[str],
) -> Mapping[str, object]:
    """Require each requested item to have an entity or redirect result.

    Args:
        response: Successful entity API response.
        requested: Item identities supplied in this API batch.

    Returns:
        Entity mapping, including explicit missing-item observations.

    Raises:
        TypeError: Redirect identities have unexpected types.
        ValueError: A requested item or redirect target was omitted.
    """
    entities = _object(response.get("entities"), "entities")
    redirects = response.get("redirects", [])
    if not isinstance(redirects, list):
        message = "The entity response has invalid redirect metadata."
        raise TypeError(message)
    targets = {}
    for value in redirects:
        redirect = _object(value, "redirect")
        source, target = redirect.get("from"), redirect.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            message = "An entity redirect has invalid item identities."
            raise TypeError(message)
        targets[source] = target
    if any(
        item not in entities and targets.get(item) not in entities
        for item in requested
    ):
        message = "The entity API response omitted requested item data."
        raise ValueError(message)
    return entities


def collect_quality(
    items: Iterable[str],
    fetch: Callable[[Mapping[str, str]], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Collect current recorded quality statuses for project items.

    Fetch at most fifty items per request and retain all recognized
    Wikipedia records carrying FA, FL, GA, or recommended badges.
    Totals count article sitelinks; language/status counts retain every
    badge if Wikidata records several quality badges for one sitelink.
    Transport and source failures propagate so a worker can retain its
    previous complete snapshot. No requests are made for an empty input.
    Each record includes its same-item Chinese sitelink when present;
    absent Chinese associations remain null. Its Chinese class uses the
    first recognized badge in FA, FL, GA, recommended priority order.

    Args:
        items: Wikidata item IDs from the caller's project scope.
        fetch: Optional API transport for offline verification.

    Returns:
        Typed ``records`` frame, ``counts`` by language/status,
        distinct article ``total``, and observed quality ``languages``.

    Raises:
        TypeError: Upstream fields have unexpected response types.
        ValueError: Item IDs or upstream response contracts are invalid.
    """  # ruff: ignore[docstring-extraneous-exception]
    identifiers = list(dict.fromkeys(items))
    if any(
        not isinstance(item, str) or _ITEM_ID.fullmatch(item) is None
        for item in identifiers
    ):
        message = "Quality collection requires valid Wikidata item IDs."
        raise ValueError(message)
    if not identifiers:
        return _summary([])
    transport = fetch or _request
    sites = _wikipedia_sites(transport)
    records: list[dict[str, str | None]] = []
    for offset in range(0, len(identifiers), _BATCH_SIZE):
        batch = identifiers[offset : offset + _BATCH_SIZE]
        response = _api_result(
            transport({
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "sitelinks/urls",
                "redirects": "yes",
            }),
        )
        entities = _batch_entities(response, batch)
        for value in entities.values():
            entity = _object(value, "entity")
            if "missing" in entity:
                continue
            item = entity.get("id")
            if not isinstance(item, str) or _ITEM_ID.fullmatch(item) is None:
                message = "An entity has no canonical Wikidata item ID."
                raise ValueError(message)
            links = _object(entity.get("sitelinks"), "sitelinks")
            records.extend(_sitelink_records(item, links, sites))
    return _summary(records)


def collect_video_game_quality(
    fetch: Callable[[Mapping[str, str]], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Collect quality badges for exact video-game instances.

    Query ``P31=Q7889`` directly across Wikipedia language editions.
    Apply FA, FL, GA, and recommended badges uniformly to each language.
    Chinese pages are included for language statistics, and their
    same-item links remain available beside foreign articles. Callers
    can rank foreign editions separately. This scope omits subclass
    traversal, lists, and wider project membership.
    Missing or delayed Wikidata badges do not establish the absence of a
    local quality designation.
    Failures propagate without falling back to a different topic scope.

    Args:
        fetch: Optional transport receiving SPARQL ``query`` and
            ``format`` parameters, then site matrix and entity actions.

    Returns:
        Typed ``records`` frame, ``counts`` by language/status,
        distinct article ``total``, and observed quality ``languages``.

    Raises:
        TypeError: Upstream fields have unexpected response types.
        ValueError: Query or site identities violate their contracts.
    """  # ruff: ignore[docstring-extraneous-exception]
    transport = fetch or _video_game_request
    response = _api_result(
        transport({"query": _VIDEO_GAME_QUERY, "format": "json"}),
    )
    identifiers = []
    for value in _query_bindings(response):
        row = _object(value, "SPARQL binding")
        identifiers.append(_item_from_uri(_query_value(row, "item", "uri")))
    return collect_quality(identifiers, transport)
