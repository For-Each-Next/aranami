"""Share safe HTML, localization, and observation display mechanics.

Use these helpers to render runtime-prepared facts. They read no runtime
files, initiate no requests, and never change source observations.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import math
import operator
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode, urlsplit

import polars as pl
from markupsafe import Markup, escape
from opencc import OpenCC

from shared.assessment_profiles import assessment_profile

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


type Record = dict[str, object]
_GAME_TITLE_LABELS: ContextVar[Mapping[str, str] | None] = ContextVar(
    "game_title_labels",
    default=None,
)
_MINUTE = 60
_HOUR_MINUTES = 60
_DAY_MINUTES = 1440
_MONTH_MINUTES = 43830
_WEEK_MINUTES = 7 * _DAY_MINUTES
_YEAR_MINUTES = 12 * _MONTH_MINUTES
_RELATIVE_UNITS = (
    ("years", _YEAR_MINUTES),
    ("months", _MONTH_MINUTES),
    ("weeks", _WEEK_MINUTES),
    ("days", _DAY_MINUTES),
    ("hours", _HOUR_MINUTES),
    ("minutes", 1),
)
_PAGE_SIZE = 10
_MAX_URL_LENGTH = 4096
_DISPLAY_LIMIT = 6
_FAILURE_REASONS = {
    "TimeoutError": "timeout",
    "ConnectionError": "connection",
    "ConnectionRefusedError": "connection",
    "ConnectionResetError": "connection",
    "URLError": "connection",
    "HTTPError": "http",
    "ExceptionGroup": "multiple",
    "BaseExceptionGroup": "multiple",
    "MultipleFailures": "multiple",
    "IncompletePageviewsDayError": "day_incomplete",
    "PageviewsUnavailableError": "pageviews_unavailable",
    "PageviewsDeferredError": "pageviews_deferred",
    "PermissionError": "permission",
    "NoUsernameError": "authentication",
    "NoPasswordError": "authentication",
    "ClientLoginTokenError": "authentication",
    "EditConflictError": "edit_conflict",
    "LockedPageError": "permission",
    "NoPageError": "page_missing",
    "FileNotFoundError": "file_missing",
    "OSError": "storage",
    "ValueError": "invalid_data",
    "TypeError": "invalid_data",
}
# This application serves the configured project. The shared registry
# defines grades; locale messages remain presentation content.
_ASSESSMENTS = assessment_profile("zhwiki", "电子游戏")
_CODES = {
    dimension: tuple(
        label.code
        for label in sorted(
            _ASSESSMENTS.labels(dimension),
            key=lambda label: (label.order is None, label.order or 0),
        )
        if label.order is not None or label.code == "Unknown"
    )
    for dimension in ("class", "importance")
}
_RANKS = {
    dimension: _ASSESSMENTS.ranks(dimension)
    for dimension in ("class", "importance")
}
_SORT_RANKS = {
    dimension: _ASSESSMENTS.sort_ranks(dimension)
    for dimension in ("class", "importance")
}
_LIST_GROUPS = {
    label.code: label.article_group
    for label in _ASSESSMENTS.classes
    if label.article_group is not None and label.article_group != label.code
}


def failure_description(event: Record, interface: Interface) -> str | None:
    """Translate safe failure facts into an explanation for readers.

    Args:
        event: Validated routine or destination observation.
        interface: Locale used for the surrounding status view.

    Returns:
        A human-readable cause, or none when no cause was observed.
        Unknown identifiers never expose diagnostic text.
    """
    reason = event.get("failure_reason")
    if not reason:
        return None
    context = event.get("failure_context")
    if reason == "PageviewsUnavailableError" and isinstance(context, dict):
        return interface.text(
            "failure.pageviews_missing",
            missing=f"{context['missing']:,}",
            total=f"{context['total']:,}",
        )
    if isinstance(reason, str) and re.fullmatch(r"HTTP[1-5][0-9]{2}", reason):
        status = int(reason[4:])
        key = (
            "http_unavailable"
            if status >= HTTPStatus.INTERNAL_SERVER_ERROR
            else "http_permission"
            if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
            else "http_rate_limit"
            if status == HTTPStatus.TOO_MANY_REQUESTS
            else "http_missing"
            if status == HTTPStatus.NOT_FOUND
            else "http_status"
        )
        return interface.text(f"failure.{key}", status=status)
    key = _FAILURE_REASONS.get(reason, "unknown")
    return interface.text(f"failure.{key}")


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, str]]:
    """Read authored interface messages once per process.

    Returns:
        Authored messages indexed by key and interface language.
    """
    return json.loads(Path(__file__).with_name("messages.json").read_text())


def _range_locale(value: str) -> str | None:
    """Map a language range with explicit script priority.

    Returns:
        A supported locale, or None for an unsupported language.
    """
    parts = value.lower().split("-")
    if parts[0] == "en" or value == "*":
        return "en"
    if parts[0] != "zh":
        return None
    traditional = "hant" in parts or (
        "hans" not in parts and set(parts) & {"tw", "hk", "mo"}
    )
    if traditional:
        return (
            "zh-tw"
            if "tw" in parts
            else "zh-hk"
            if set(parts) & {"hk", "mo"}
            else "zh-hant"
        )
    return (
        "zh-hans"
        if "hans" in parts or set(parts) & {"cn", "sg", "my"}
        else "zh"
    )


def _language_weight(parameters: list[str]) -> float:
    """Reject duplicate or malformed preference weights.

    Returns:
        A valid preference weight, or zero for invalid factors.
    """
    qualities = [
        part for part in parameters if re.match(r"q\s*=", part, re.IGNORECASE)
    ]
    if not qualities:
        return 1.0
    if len(qualities) != 1:
        return 0.0
    match = re.fullmatch(
        r"q\s*=\s*(0(?:\.\d{0,3})?|1(?:\.0{0,3})?)",
        qualities[0],
        re.IGNORECASE,
    )
    return float(match[1]) if match else 0.0


def select_locale(header: str) -> str:
    """Select a supported language by quality and header order.

    Generic Chinese remains distinct for original-script introductions.
    Unsupported or malformed preferences fall back to English.

    Returns:
        English, generic Chinese, or an explicit Chinese script.
    """
    preferences = []
    for order, entry in enumerate(header[:8192].split(",")[:100]):
        value, *parameters = [
            part.strip().lower() for part in entry.split(";")
        ]
        if not re.fullmatch(r"(?:[a-z]{1,8}(?:-[a-z0-9]{1,8})*|\*)", value):
            continue
        locale = _range_locale(value)
        weight = _language_weight(parameters)
        if locale is not None and weight > 0:
            preferences.append((-weight, order, locale))
    return min(preferences)[2] if preferences else "en"


@dataclass(frozen=True)
class Interface:
    """Format copy and dates for one negotiated browser language."""

    locale: str

    @property
    def message_locale(self) -> str:
        """Resolve a regional content preference to authored UI copy."""
        if self.locale in {"zh-tw", "zh-hk"}:
            return "zh-hant"
        return "zh-hans" if self.locale == "zh" else self.locale

    @property
    def language_tag(self) -> str:
        """The standard HTML and response language tag."""
        return {
            "zh-hans": "zh-Hans",
            "zh-hant": "zh-Hant",
            "zh-tw": "zh-Hant-TW",
            "zh-hk": "zh-Hant-HK",
        }.get(
            self.locale,
            self.locale,
        )

    def text(self, key: str, **parameters: object) -> str:
        """Translate plain text without interpreting markup.

        Returns:
            Localized plain text with supplied placeholders replaced.
        """
        if parameters.get("count") == 1 and key + "_one" in catalog():
            key += "_one"
        translations = catalog()[key]
        locale = self.message_locale
        message = translations.get(locale, translations["en"])
        return re.sub(
            r"\{([a-z_][a-z0-9_]*)\}",
            lambda match: str(parameters.get(match[1], match[0])),
            message,
        )

    def date(
        self,
        value: str,
        *,
        month_only: bool = False,
        compact: bool = False,
        reference_year: int | None = None,
    ) -> str:
        """Display a saved date without timezone conversion.

        Args:
            value: Saved ISO calendar date or month.
            month_only: Display the month and year without a day.
            compact: Abbreviate English full dates for card metadata.
            reference_year: Omit this year in compact English dates.
                Without a reference, retain every date's year.

        Returns:
            A localized calendar date or month label.
        """
        day = dt.date.fromisoformat(value + "-01" if month_only else value)
        if self.locale != "en":
            suffix = "" if month_only else f"{day.day}日"
            return f"{day.year}年{day.month}月{suffix}"
        if compact and not month_only:
            label = f"{calendar.month_abbr[day.month]} {day.day}"
            return (
                label if day.year == reference_year else f"{label}, {day.year}"
            )
        month = calendar.month_name[day.month]
        return (
            f"{month} {day.year}"
            if month_only
            else f"{month} {day.day}, {day.year}"
        )

    def label(self, value: object, fallback: str) -> str:
        """Resolve configured labels without changing source text.

        Returns:
            The selected authored label or the supplied fallback.
        """
        if isinstance(value, str):
            return value
        if not isinstance(value, dict) or not value:
            return fallback
        locale = self.message_locale
        return value.get(locale, value.get("en", next(iter(value.values()))))


def local_url(path: str, **parameters: object) -> str:
    """Add query parameters before a URL fragment.

    Returns:
        A local or fixed external URL with encoded parameters.
    """
    base, separator, fragment_id = path.partition("#")
    query = "?" + urlencode(parameters) if parameters else ""
    return base + query + (separator + fragment_id if separator else "")


def wiki_url(value: object) -> str | None:
    """Allow only HTTPS Wikipedia or Wikidata page URLs.

    Returns:
        The permitted URL, or None for unsafe source links.
    """
    if (
        not isinstance(value, str)
        or len(value) > _MAX_URL_LENGTH
        or re.search(r"[\x00-\x20\\]", value)
    ):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    allowed_host = (
        re.fullmatch(r"[a-z0-9-]+\.wikipedia\.org", parsed.netloc)
        or parsed.netloc == "www.wikidata.org"
    )
    allowed_path = (
        parsed.path.startswith("/wiki/") or parsed.path == "/w/index.php"
    )
    return (
        value
        if parsed.scheme == "https" and allowed_host and allowed_path
        else None
    )


def title_url(title: str, site: str = "zhwiki") -> str | None:
    """Link a literal report title on a supported wiki.

    Returns:
        The encoded page URL, or None for an unsupported wiki.
    """
    host = {"zhwiki": "zh.wikipedia.org", "enwiki": "en.wikipedia.org"}.get(
        site,
    )
    return (
        f"https://{host}/wiki/{quote(title.replace(' ', '_'), safe='')}"
        if host
        else None
    )


def wiki_link(url: object, label: str, title: str | None = None) -> Markup:
    """Escape a source link or retain an unlinked label.

    Returns:
        Escaped HTML for the selected presentation content.
    """
    target = wiki_url(url)
    if not target:
        return escape(label)
    if title is None:
        return Markup('<a href="{}">{}</a>').format(target, label)
    return Markup(
        '<a href="{}" title="{}" aria-label="{}">{}</a>',
    ).format(
        target,
        title,
        title,
        label,
    )


def timestamp(value: object) -> dt.datetime | None:
    """Parse an aware timestamp, retaining missing values as null.

    Returns:
        An aware UTC timestamp, or None for unavailable values.
    """
    if not isinstance(value, str):
        return None
    try:
        result = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return result.astimezone(dt.UTC) if result.tzinfo is not None else None


def utc_time(
    value: object,
    *,
    seconds: bool = False,
    milliseconds: bool = False,
) -> str:
    """Display actual source timestamps in UTC without a zone label.

    Args:
        value: An aware source timestamp, or an unavailable value.
        seconds: Show whole seconds instead of whole minutes.
        milliseconds: Show seconds and three fractional digits, taking
            precedence over seconds. Finer precision is truncated.

    Returns:
        A date and time without a zone suffix, or an unknown marker.
    """
    instant = timestamp(value)
    if instant is None:
        return "—"
    if milliseconds:
        return instant.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return instant.strftime(
        "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M",
    )


def relative_units(seconds: float) -> tuple[str, int]:
    """Select a whole relative age without changing source timestamps.

    Args:
        seconds: Elapsed or remaining interval from an observed time.

    Returns:
        The largest whole unit and count, using seven-day weeks and
        365.25-day years split into twelve equal months. Intervals under
        one minute return zero minutes for an explicit just-now label.
    """
    minutes = int(abs(seconds) // _MINUTE)
    unit, divisor = next(
        (
            (unit, divisor)
            for unit, divisor in _RELATIVE_UNITS
            if minutes >= divisor
        ),
        ("minutes", 1),
    )
    return unit, minutes // divisor


def relative_time(
    value: object,
    interface: Interface,
    now: dt.datetime,
) -> str:
    """Describe an observed time with whole elapsed or remaining units.

    Args:
        value: An aware source timestamp, or an unavailable value.
        interface: Locale for the parenthesized annotation.
        now: Aware rendering time shared with the absolute timestamp.

    Returns:
        Whole years, months, weeks, days, hours or minutes, rounded
        down.
        Sub-minute intervals have short explicit labels. Invalid or
        missing timestamps produce no annotation.
    """
    instant = timestamp(value)
    if instant is None:
        return ""
    seconds = (instant - now).total_seconds()
    unit, count = relative_units(seconds)
    if count == 0:
        return interface.text("time.soon" if seconds > 0 else "time.just_now")
    direction = "ahead" if seconds > 0 else "ago"
    return interface.text(f"time.{unit}_{direction}", count=count)


def assessment_code(value: object, dimension: str) -> str | None:
    """Normalize verified names without assigning missing grades.

    Returns:
        The recognized canonical code, or None.
    """
    if dimension not in _CODES:
        return None
    label = _ASSESSMENTS.resolve(value, dimension)
    return (
        label.code
        if label is not None and label.code in _CODES[dimension]
        else None
    )


def assessment_rank(value: object, dimension: str) -> int | None:
    """Rank comparable grades and retain unknowns as null.

    Returns:
        The comparable grade rank, or None for unranked values.
    """
    return _RANKS.get(dimension, {}).get(assessment_code(value, dimension))


def maintenance_selection(
    parameters: Mapping[str, list[str]],
) -> dict[str, str]:
    """Validate controls without converting huge page values.

    Returns:
        Validated scalar controls with display defaults.

    Raises:
        ValueError: A control is duplicated, unsupported, or malformed.
    """
    allowed = {
        "task",
        "page",
        "sort",
        "order",
        "seed",
        "q",
        "quality",
        "importance",
        "regex",
        "quality_op",
        "quality_to",
        "importance_op",
        "importance_to",
        "genre",
        "limit",
    }
    if set(parameters) - allowed or any(
        len(values) != 1 for values in parameters.values()
    ):
        message = "Invalid maintenance query."
        raise ValueError(message)
    values = {key: items[0] for key, items in parameters.items()}
    values.setdefault("page", "1")
    values.setdefault("sort", "oldest")
    for key in ("q", "quality", "importance", "quality_to", "importance_to"):
        if not values.get(key):
            values.pop(key, None)
    values.setdefault(
        "order",
        "desc" if values["sort"] in {"class", "importance"} else "asc",
    )
    patterns = {
        "task": r"[a-z][a-z0-9_]{0,63}",
        "page": r"[1-9][0-9]*",
        "sort": r"oldest|title|class|importance|size|random",
        "order": r"asc|desc",
        "seed": r"[0-9a-f]{16}",
        "q": r"[^\x00-\x1f\x7f]{1,200}",
        "quality": "|".join(_CODES["class"]),
        "importance": "|".join(_CODES["importance"]),
        "regex": "1",
        "quality_op": "eq|gte|lte|range",
        "importance_op": "eq|gte|lte|range",
        "quality_to": "|".join(_CODES["class"]),
        "importance_to": "|".join(_CODES["importance"]),
        "genre": "all|article|list",
        "limit": "10|20|50",
    }
    if "task" not in values or any(
        not re.fullmatch(patterns[key], value) for key, value in values.items()
    ):
        message = "Invalid maintenance query."
        raise ValueError(message)
    for key, dimension in (("quality", "class"), ("importance", "importance")):
        operation = values.get(f"{key}_op", "eq")
        if operation not in {"eq", "range"} and (
            values.get(key) not in _RANKS[dimension]
            or (operation == "range" and f"{key}_to" not in values)
        ):
            message = "A comparable assessment is required for a range."
            raise ValueError(message)
    if values.get("regex") and values.get("q"):
        try:
            pl.select(pl.lit("validate").str.contains("(?i)" + values["q"]))
        except pl.exceptions.PolarsError as error:
            message = "Invalid title regular expression."
            raise ValueError(message) from error
    return values


def assessment_condition(
    code: pl.Expr,
    dimension: str,
    selected: str,
    operation: str = "eq",
    endpoint: str | None = None,
) -> pl.Expr:
    """Build a shared grade comparison for quality or importance.

    Args:
        code: Canonical assessment, with missing facts preserved.
        dimension: Assessment dimension whose established ranks apply.
        selected: Exact grade or first comparable endpoint.
        operation: Equality, minimum, maximum, or inclusive range.
        endpoint: Second endpoint for a range, in either order.

    Returns:
        A Polars predicate; unranked observations never match a range.
    """
    if operation == "eq":
        return code == selected
    ranks = {
        **_RANKS[dimension],
        **(
            {"Unassessed": 0, "Unknown": 0}
            if dimension == "class"
            else {"Unknown": 0}
        ),
    }
    rank = code.replace_strict(ranks, default=None, return_dtype=pl.Int64)
    boundary = ranks[selected]
    if operation == "gte":
        return rank >= boundary
    if operation == "lte":
        return rank <= boundary
    if endpoint is None:
        return rank <= boundary
    lower, upper = sorted((boundary, ranks[endpoint]))
    return rank.is_between(lower, upper)


def maintenance_filter(
    items: list[Record],
    selection: Mapping[str, str],
) -> list[Record]:
    """Filter saved titles and assessments before sorting and paging.

    Args:
        items: Complete observations for the selected task.
        selection: Validated title query and optional assessment codes.

    Returns:
        Original observations with source order and fields kept. Title
        search is case-insensitive, with an optional Rust regular
        expression. Missing quality is
        unassessed; unrecognized values remain unknown.
    """
    if not any(
        selection.get(key)
        for key in (
            "q",
            "quality",
            "quality_to",
            "importance",
            "importance_to",
            "genre",
        )
    ):
        return items
    frame = pl.from_dicts(
        items,
        schema={
            "title": pl.String,
            "assessment_class": pl.String,
            "assessment_importance": pl.String,
        },
    ).with_row_index("position")
    conditions = []
    if query := selection.get("q"):
        title = pl.col("title")
        conditions.append(
            title.str.contains("(?i)" + query)
            if selection.get("regex")
            else title.str.to_lowercase().str.contains(
                query.lower(),
                literal=True,
            ),
        )
    for key, dimension in (("quality", "class"), ("importance", "importance")):
        selected = selection.get(key)
        endpoint = selection.get(f"{key}_to")
        if (
            selected
            or endpoint
            or (
                dimension == "class"
                and selection.get("genre") in {"article", "list"}
            )
        ):
            value = pl.col(f"assessment_{dimension}").str.strip_chars()
            fallback = (
                pl
                .when(value.is_null() | (value.str.len_chars() == 0))
                .then(pl.lit("Unassessed"))
                .otherwise(pl.lit("Unknown"))
                if dimension == "class"
                else pl.lit("Unknown")
            )
            code = _ASSESSMENTS.expression(
                value,
                dimension,
                preserve_unknown=False,
            ).fill_null(fallback)
            if dimension == "class":
                is_list = code.is_in(_LIST_GROUPS)
                genre = selection.get("genre")
                if genre in {"article", "list"}:
                    conditions.append(is_list if genre == "list" else ~is_list)
                code = code.replace(_LIST_GROUPS)
            if selected:
                conditions.append(
                    assessment_condition(
                        code,
                        dimension,
                        selected,
                        selection.get(f"{key}_op", "eq"),
                        endpoint,
                    ),
                )
            elif endpoint:
                conditions.append(
                    assessment_condition(code, dimension, endpoint, "gte"),
                )
    positions = frame.filter(*conditions).get_column("position")
    return [items[position] for position in positions]


def maintenance_items(
    items: list[Record],
    sort: str = "oldest",
    order: str = "asc",
    seed: str = "",
) -> list[Record]:
    """Sort worklists with stable IDs and unknown values last.

    Assessment sorting follows the recognized hierarchy, with article
    grades ahead of the corresponding list grades in descending order.
    Random sorting uses a seed and SHA-256 to stay stable across pages.
    Other ties retain ascending titles and page IDs. The saved input is
    never mutated.

    Returns:
        A new sorted list retaining every saved article.
    """
    if sort == "random":
        return sorted(
            items,
            key=lambda item: (
                hashlib.sha256(f"{seed}:{item['page_id']}".encode()).digest(),
                item["page_id"],
            ),
            reverse=order == "desc",
        )

    frame = pl.from_dicts(
        items,
        schema={
            "page_id": pl.Int64,
            "title": pl.String,
            "oldest_issue_month": pl.String,
            "page_length": pl.Int64,
            "assessment_class": pl.String,
            "assessment_importance": pl.String,
        },
    ).with_row_index("position")
    if sort in _SORT_RANKS:
        field = _ASSESSMENTS.expression(
            f"assessment_{sort}",
            sort,
            preserve_unknown=False,
        ).replace_strict(
            _SORT_RANKS[sort],
            default=None,
            return_dtype=pl.Int64,
        )
    else:
        field = pl.col(
            {"oldest": "oldest_issue_month", "size": "page_length"}.get(
                sort,
                "title",
            ),
        )
    positions = frame.sort(
        field,
        "title",
        "page_id",
        descending=[order == "desc", False, False],
        nulls_last=True,
        maintain_order=True,
    ).get_column("position")
    return [items[position] for position in positions]


def maintenance_page(
    items: list[Record],
    requested: str = "1",
    limit: int = _PAGE_SIZE,
) -> Record:
    """Select the requested row count and clamp oversized decimal pages.

    Returns:
        Page numbers, row offset, and the selected article range.
    """
    pages = max(1, (len(items) + limit - 1) // limit)
    page = (
        pages
        if len(requested) > len(str(pages))
        else min(int(requested), pages)
    )
    offset = (page - 1) * limit
    return {
        "page": page,
        "pages": pages,
        "offset": offset,
        "items": items[offset : offset + limit],
    }


def merged_items(homepage: Record, sections: tuple[str, ...]) -> Record:
    """Merge highlights while retaining unavailable sources.

    Returns:
        Chronological items and a list of unavailable sections.
    """
    merged = None
    missing = []
    for section in sections:
        rows = homepage.get(section)
        if rows is None:
            missing.append(section)
            continue
        if merged is None:
            merged = []
        for item in rows:
            kind = "ga" if section == "good" else section
            if section == "upcoming":
                kind = item.get("kind")
            elif section == "featured":
                kind = (
                    item.get("kind")
                    if item.get("kind") in {"fa", "fl"}
                    else "featured"
                )
            merged.append({**item, "kind": kind})
    if merged is not None:
        merged.sort(key=operator.itemgetter("title", "kind"))
        merged.sort(key=lambda item: item.get("date") or "", reverse=True)
    return {"items": merged, "missing": missing}


def homepage_sections(  # ruff: ignore[too-many-locals]
    snapshot: Record | None,
    now: dt.datetime | None = None,
) -> list[Record]:
    """Show six homepage highlights in descending display-date order.

    Args:
        snapshot: Saved homepage facts, possibly unavailable.
        now: Actual page clock for expiring old highlights.

    Returns:
        Visible sections and their bounded article lists.
    """
    snapshot = snapshot or {}
    homepage = snapshot.get("homepage") or {}
    observed = now or timestamp(snapshot.get("updated_at"))
    today = observed.date() if observed else dt.datetime.now(dt.UTC).date()
    previous_month = today.replace(day=1) - dt.timedelta(days=1)
    month_start = previous_month.replace(
        day=min(
            today.day,
            calendar.monthrange(previous_month.year, previous_month.month)[1],
        ),
    )
    quality = merged_items(homepage, ("featured", "good", "upcoming"))
    current = merged_items(homepage, ("news", "history"))
    candidates = []
    for item in quality["items"] or []:
        day = item.get("date")
        minimum = (
            today - dt.timedelta(days=6)
            if item.get("kind") == "ga"
            else month_start + dt.timedelta(days=1)
        )
        if day and (
            minimum.isoformat() <= day <= today.isoformat()
            or (
                item.get("date_kind") == "scheduled"
                and today.isoformat()
                < day
                <= (today + dt.timedelta(days=2)).isoformat()
            )
        ):
            candidates.append(item)
    candidates.extend(
        item
        for item in current["items"] or []
        if item.get("date") == today.isoformat()
        or (
            item.get("kind") == "history"
            and item.get("date_kind") == "scheduled"
            and today.isoformat()
            < (item.get("date") or "")
            <= (today + dt.timedelta(days=2)).isoformat()
        )
    )
    dyk = homepage.get("dyk")
    candidates.extend(
        {**item, "kind": "dyk"}
        for item in dyk or []
        if (item.get("date") or "") <= today.isoformat()
        or (
            item.get("date_kind") == "estimated"
            and today.isoformat()
            < (item.get("date") or "")
            <= (today + dt.timedelta(days=2)).isoformat()
        )
    )
    candidates.sort(key=lambda item: item.get("date") or "", reverse=True)
    combined = []
    seen = set()
    for item in candidates:
        if item["title"] not in seen:
            seen.add(item["title"])
            combined.append(item)
    known = (
        quality["items"] is not None
        or current["items"] is not None
        or dyk is not None
    )
    highlights = combined[:_DISPLAY_LIMIT] if known else None
    views = (snapshot.get("pageviews") or {}).get("items")
    sections = [
        (
            "pageviews",
            views[:_DISPLAY_LIMIT] if views is not None else None,
            views == [],
        ),
        (
            "recognized",
            highlights,
            highlights == []
            and not quality["missing"]
            and not current["missing"]
            and dyk is not None,
        ),
    ]
    return [
        {"key": key, "items": items}
        for key, items, empty in sections
        if not empty
    ]


def article_extract(item: Record, locale: str) -> tuple[str, str | None]:
    """Select saved script text without filling unknown originals.

    Returns:
        The introduction language and saved text, possibly None.
    """
    language = "zh-hans" if locale == "en" else locale
    field = "extract"
    if language == "zh":
        field = "extract_zh"
    elif language in {"zh-hant", "zh-tw", "zh-hk"} and "extract_hant" in item:
        field = "extract_hant"
    regional_field = {"zh-tw": "extract_tw", "zh-hk": "extract_hk"}.get(
        language,
    )
    if regional_field in item:
        return language, item[regional_field]
    text = item.get(field)
    if text and language in {"zh-tw", "zh-hk"}:
        text = _regional_converter(language).convert(text)
    return language, text


@contextmanager
def title_display_scope(labels: Mapping[str, str]) -> Iterator[None]:
    """Bind saved regional labels for the current rendering request.

    Args:
        labels: Validated canonical-title to Taiwan-label mapping.

    Yields:
        A rendering scope that restores prior labels on exit.
    """
    token = _GAME_TITLE_LABELS.set(labels)
    try:
        yield
    finally:
        _GAME_TITLE_LABELS.reset(token)


def display_title(title: str, locale: str) -> str:
    """Convert a visible Chinese title without changing its identity.

    Args:
        title: Original saved article or discussion heading.
        locale: Negotiated script or regional Chinese preference.

    Returns:
        A saved conversion-group label for Taiwan when available,
        otherwise a local dictionary conversion for Chinese preferences.
        Generic Chinese and English retain the source spelling.
    """
    if locale not in {"zh-hans", "zh-hant", "zh-tw", "zh-hk"}:
        return title
    if locale == "zh-tw" and (
        label := (_GAME_TITLE_LABELS.get() or {}).get(title)
    ):
        return label
    return _title_converter(locale).convert(title)


@lru_cache(maxsize=4)
def _title_converter(locale: str) -> OpenCC:
    """Reuse the local dictionary for a Chinese title preference.

    Returns:
        An OpenCC converter for the selected script or region.
    """
    return OpenCC(
        {
            "zh-hans": "t2s",
            "zh-hant": "s2t",
            "zh-tw": "s2tw",
            "zh-hk": "s2hk",
        }[locale],
    )


@lru_cache(maxsize=2)
def _regional_converter(language: str) -> OpenCC:
    """Reuse a local converter for regional Chinese content.

    Returns:
        The local OpenCC converter for the requested regional variant.
    """
    return OpenCC("t2tw" if language == "zh-tw" else "t2hk")


def pageviews_url(item: Record, day: str) -> str:
    """Link thirty inclusive days ending on the measured day.

    Returns:
        A Pageviews Analysis URL with the exact source settings.
    """
    start = dt.date.fromisoformat(day) - dt.timedelta(days=29)
    return local_url(
        "https://pageviews.wmcloud.org/",
        project="zh.wikipedia.org",
        platform="all-access",
        agent="user",
        start=start.isoformat(),
        end=day,
        pages=item["title"],
    )


def duration(
    seconds: object,
    interface: Interface,
    *,
    running: bool = False,
) -> str:
    """Format execution durations with explicit localized units.

    Returns:
        Days, hours, minutes, and seconds, omitting leading zero units.
        Completed values retain hundredths; active work uses whole
        seconds and an ellipsis. Unknown observations stay explicit.
    """
    if (
        not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds < 0
    ):
        return "—"
    whole = (
        math.floor(seconds)
        if running
        else math.floor(seconds * 100 + 0.5) / 100
    )
    days, rest = divmod(whole, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, remainder = divmod(rest, 60)
    unit = (
        "days"
        if days
        else "hours"
        if hours
        else "minutes"
        if minutes
        else "seconds"
    )
    label = interface.text(
        f"duration.{unit}",
        days=int(days),
        hours=int(hours),
        minutes=int(minutes),
        seconds=str(int(remainder)) if running else f"{remainder:.2f}",
    )
    return label + ("…" if running else "")


def readable_size(size: int) -> str:
    """Format binary file sizes for the daily-log listing.

    Returns:
        A binary-unit size label with useful display precision.
    """
    amount = float(size)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    boundary = 1024
    unit = 0
    while amount >= boundary and unit < len(units) - 1:
        amount /= boundary
        unit += 1
    return f"{size} B" if unit == 0 else f"{amount:.1f} {units[unit]}"
