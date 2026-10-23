"""Share safe HTML, localization, and observation display mechanics.

Use these helpers to render backend-prepared facts. They read no runtime
files, initiate no requests, and never change source observations.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import html
import json
import math
import operator
import re
from dataclasses import dataclass
from functools import cmp_to_key, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode, urlsplit

if TYPE_CHECKING:
    from collections.abc import Mapping

type Record = dict[str, object]
_MINUTE = 60
_HOUR_MINUTES = 60
_DAY_MINUTES = 1440
_SCHEDULE_MAX_AGE = 120
_PAGE_SIZE = 50
_MAX_URL_LENGTH = 4096
_DISPLAY_LIMIT = 12
_VOID_ELEMENTS = frozenset({"meta", "link", "img", "input"})
_QUALITY = (
    ("FA", "fa", "典范", "典範"),
    ("FL", "fl", "特色列表", "典表"),
    ("GA", "ga", "优良", "優良"),
    ("A", "a", "甲"),
    ("AL", "al", "甲表", "甲级列表", "甲級列表"),
    ("Bplus", "bplus", "乙上"),
    ("B", "b", "乙"),
    ("BL", "bl", "乙表", "乙级列表", "乙級列表"),
    ("C", "c", "丙"),
    ("CL", "cl", "丙表", "丙级列表", "丙級列表"),
    ("D", "d", "丁"),
    ("Start", "start", "初"),
    ("Stub", "stub", "小作品"),
    ("List", "list", "列表"),
    ("SL", "sl", "stublist", "小列表"),
    ("Substub", "substub", "小小作品"),
    ("Disambig", "disambig", "消歧义", "消歧義"),
    ("Unassessed", "unassessed", "未评级", "未評級"),
)
_IMPORTANCE = (
    ("Top", "top", "极高", "極高"),
    ("High", "high", "高"),
    ("Mid", "mid", "中"),
    ("Low", "low", "低"),
    ("Bottom", "bottom", "极低", "極低"),
    ("NA", "na", "不适用", "不適用"),
    ("Unknown", "unknown", "未评级", "未評級"),
)
_ALIASES = {
    dimension: {name: code for code, *names in groups for name in names}
    for dimension, groups in (("class", _QUALITY), ("importance", _IMPORTANCE))
}
_RANKS = {
    "class": {
        "FA": 10,
        "FL": 10,
        "A": 9,
        "AL": 9,
        "GA": 8,
        "Bplus": 7,
        "B": 6,
        "BL": 6,
        "C": 5,
        "CL": 5,
        "D": 4,
        "Start": 3,
        "List": 3,
        "Stub": 2,
        "SL": 2,
        "Substub": 1,
    },
    "importance": {"Top": 5, "High": 4, "Mid": 3, "Low": 2, "Bottom": 1},
}


@dataclass(frozen=True, slots=True)
class Markup:
    """Identify HTML already escaped by the shared element builder."""

    value: str

    def __str__(self) -> str:
        """Return the already escaped HTML.

        Returns:
            The safely composed HTML text.
        """
        return self.value


def _escaped(value: object) -> str:
    """Escape data while retaining explicitly built elements.

    Returns:
        Escaped text suitable for an HTML text or attribute value.
    """
    if value is None:
        return ""
    return str(value) if isinstance(value, Markup) else html.escape(str(value))


def fragment(*children: object) -> Markup:
    """Join safe elements and escaped text without a container.

    Returns:
        Safely composed HTML without a wrapping element.
    """
    return Markup("".join(_escaped(child) for child in children))


def element(tag_name: str, *children: object, **attributes: object) -> Markup:
    """Build one HTML element with escaped text and quoted attributes.

    Args:
        tag_name: Literal tag selected by the renderer.
        children: Plain text or elements returned by these builders.
        attributes: Literal attribute names with underscore separators.

    Returns:
        Escaped markup safe to compose with other generated elements.
    """
    attrs = []
    for key, value in attributes.items():
        if value is None or value is False:
            continue
        attribute = key.rstrip("_").replace("_", "-")
        attrs.append(
            attribute
            if value is True
            else f'{attribute}="{html.escape(str(value))}"',
        )
    opening = f"<{tag_name}{' ' if attrs else ''}{' '.join(attrs)}>"
    if tag_name in _VOID_ELEMENTS:
        return Markup(opening)
    return Markup(f"{opening}{fragment(*children)}</{tag_name}>")


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
    if "hant" in parts:
        return "zh-hant"
    if "hans" in parts:
        return "zh-hans"
    if set(parts) & {"tw", "hk", "mo"}:
        return "zh-hant"
    return "zh-hans" if set(parts) & {"cn", "sg", "my"} else "zh"


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
    def language_tag(self) -> str:
        """The standard HTML and response language tag."""
        return {"zh-hans": "zh-Hans", "zh-hant": "zh-Hant"}.get(
            self.locale,
            self.locale,
        )

    def text(self, key: str, **parameters: object) -> str:
        """Translate plain text without interpreting markup.

        Returns:
            Localized plain text with supplied placeholders replaced.
        """
        translations = catalog()[key]
        locale = "zh-hans" if self.locale == "zh" else self.locale
        message = translations.get(locale, translations["en"])
        return re.sub(
            r"\{([a-z_]+)\}",
            lambda match: str(parameters.get(match[1], match[0])),
            message,
        )

    def date(self, value: str, *, month_only: bool = False) -> str:
        """Display a saved date without timezone conversion.

        Returns:
            A localized calendar date or month label.
        """
        day = dt.date.fromisoformat(value + "-01" if month_only else value)
        if self.locale != "en":
            suffix = "" if month_only else f"{day.day}日"
            return f"{day.year}年{day.month}月{suffix}"
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
        locale = "zh-hans" if self.locale == "zh" else self.locale
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
    return (
        element("a", label, href=target, title=title, aria_label=title)
        if target
        else fragment(label)
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


def utc_time(value: object, *, seconds: bool = False) -> str:
    """Display actual source timestamps in UTC.

    Returns:
        A UTC display string, or an explicit unknown marker.
    """
    instant = timestamp(value)
    if instant is None:
        return "—"
    return instant.strftime(
        "%Y-%m-%d %H:%M:%S UTC" if seconds else "%Y-%m-%d %H:%M UTC",
    )


def assessment_code(value: object, dimension: str) -> str | None:
    """Normalize verified names without assigning missing grades.

    Returns:
        The recognized canonical code, or None.
    """
    return (
        _ALIASES.get(dimension, {}).get(value.strip().lower())
        if isinstance(value, str)
        else None
    )


def assessment_rank(value: object, dimension: str) -> int | None:
    """Rank comparable grades and retain unknowns as null.

    Returns:
        The comparable grade rank, or None for unranked values.
    """
    return _RANKS.get(dimension, {}).get(assessment_code(value, dimension))


def maintenance_sections(groups: list[Record]) -> dict[str, list[Record]]:
    """Group maintenance tasks in their configured section order.

    Returns:
        Sections and their tasks in first-appearance order.
    """
    sections = {}
    for group in groups:
        sections.setdefault(group.get("section_key", "other"), []).append(
            group,
        )
    return sections


def maintenance_selection(
    parameters: Mapping[str, list[str]],
) -> dict[str, str]:
    """Validate controls without converting huge page values.

    Returns:
        Validated scalar controls with display defaults.

    Raises:
        ValueError: A control is duplicated, unsupported, or malformed.
    """
    allowed = {"task", "page", "sort", "order", "seed"}
    if set(parameters) - allowed or any(
        len(values) != 1 for values in parameters.values()
    ):
        message = "Invalid maintenance query."
        raise ValueError(message)
    values = {key: items[0] for key, items in parameters.items()}
    values.setdefault("page", "1")
    values.setdefault("sort", "oldest")
    values.setdefault(
        "order",
        "desc" if values["sort"] in {"class", "importance"} else "asc",
    )
    patterns = {
        "task": r"[a-z][a-z0-9_]{0,63}",
        "page": r"[1-9][0-9]*",
        "sort": r"oldest|title|class|importance|random",
        "order": r"asc|desc",
        "seed": r"[0-9a-f]{16}",
    }
    if "task" not in values or any(
        not re.fullmatch(patterns[key], value) for key, value in values.items()
    ):
        message = "Invalid maintenance query."
        raise ValueError(message)
    return values


def maintenance_items(
    items: list[Record],
    sort: str = "oldest",
    order: str = "asc",
    seed: str = "",
) -> list[Record]:
    """Sort worklists with stable IDs and unknown values last.

    Random sorting uses a seed and SHA-256 to stay stable across pages.
    The saved input is never mutated.

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

    def compare(left: Record, right: Record) -> int:
        """Compare sort fields with fixed ascending title and ID ties.

        Returns:
            Negative, zero, or positive ordering for the two rows.
        """
        if sort in {"oldest", "class", "importance"}:
            a, b = (
                (
                    item.get("oldest_issue_month")
                    if sort == "oldest"
                    else assessment_rank(item.get(f"assessment_{sort}"), sort)
                )
                for item in (left, right)
            )
            if (a is None) != (b is None):
                return 1 if a is None else -1
            if a != b:
                result = (a > b) - (a < b)
                return -result if order == "desc" else result
        result = (left["title"] > right["title"]) - (
            left["title"] < right["title"]
        )
        if sort == "title" and order == "desc":
            result = -result
        return result or left["page_id"] - right["page_id"]

    return sorted(items, key=cmp_to_key(compare))


def maintenance_page(items: list[Record], requested: str = "1") -> Record:
    """Select fifty rows and clamp oversized decimal pages.

    Returns:
        Page numbers, row offset, and at most fifty articles.
    """
    pages = max(1, (len(items) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = (
        pages
        if len(requested) > len(str(pages))
        else min(int(requested), pages)
    )
    offset = (page - 1) * _PAGE_SIZE
    return {
        "page": page,
        "pages": pages,
        "offset": offset,
        "items": items[offset : offset + _PAGE_SIZE],
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
            if section == "featured":
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


def homepage_sections(snapshot: Record | None) -> list[Record]:
    """Omit empty sections and put scheduled quality content first.

    Returns:
        Visible sections and their bounded article lists.
    """
    snapshot = snapshot or {}
    homepage = snapshot.get("homepage") or {}
    recognized = merged_items(homepage, ("featured", "good"))
    news = merged_items(homepage, ("news", "history"))
    upcoming = homepage.get("upcoming")
    if upcoming is not None:
        upcoming = sorted(
            upcoming,
            key=operator.itemgetter("date", "title", "kind"),
        )
    quality = (
        None
        if recognized["items"] is None and upcoming is None
        else [*(upcoming or []), *(recognized["items"] or [])]
    )
    views = (snapshot.get("pageviews") or {}).get("items")
    dyk = homepage.get("dyk")
    sections = (
        (
            "pageviews",
            views[:_DISPLAY_LIMIT] if views is not None else None,
            views == [],
        ),
        (
            "recognized",
            quality,
            quality == []
            and not recognized["missing"]
            and upcoming is not None,
        ),
        ("dyk", dyk[:_DISPLAY_LIMIT] if dyk is not None else None, dyk == []),
        (
            "news_history",
            news["items"],
            news["items"] == [] and not news["missing"],
        ),
    )
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
    elif language == "zh-hant" and "extract_hant" in item:
        field = "extract_hant"
    return language, item.get(field)


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
    exact: bool = False,
) -> str:
    """Localize durations without conflating unknown and zero.

    Returns:
        A localized duration or the explicit unknown marker.
    """
    if (
        not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds < 0
    ):
        return "—"
    if exact and not running:
        return interface.text("duration.seconds", seconds=f"{seconds:.3f}")
    rounded = (
        math.floor(seconds)
        if running
        else math.floor(seconds * 100 + 0.5) / 100
    )
    if rounded >= _MINUTE:
        whole = math.floor(seconds) if running else math.floor(seconds + 0.5)
        label = interface.text(
            "duration.minutes",
            minutes=whole // _MINUTE,
            seconds=whole % _MINUTE,
        )
    else:
        label = interface.text(
            "duration.seconds",
            seconds=str(rounded) if running else f"{rounded:.2f}",
        )
    return label + ("…" if running else "")


def event_duration(
    event: Record | None,
    interface: Interface,
    now: dt.datetime,
    *,
    exact: bool = False,
) -> str:
    """Display measured completion or unfinished elapsed time.

    Returns:
        Localized measured or elapsed duration, possibly unknown.
    """
    if event is None:
        return "—"
    started = timestamp(event.get("started_at"))
    if event.get("status") == "running":
        seconds = max(0, (now - started).total_seconds()) if started else None
        return duration(seconds, interface, running=True)
    return duration(event.get("duration_seconds"), interface, exact=exact)


def schedule_state(
    snapshot: Record | None,
    definitions: Record,
    now: dt.datetime,
) -> Record:
    """Suppress stale or mismatched scheduler predictions.

    Returns:
        Displayable actual schedule state with unsafe times hidden.
    """
    unknown = {
        "state": "missing",
        "routines": {},
        "updated_at": None,
        "started_at": None,
    }
    if snapshot is None:
        return unknown
    updated = timestamp(snapshot.get("updated_at"))
    if updated is None or snapshot.get("state") not in {
        "running",
        "paused",
        "stopped",
    }:
        return unknown
    age = (now - updated).total_seconds()
    if age < 0 or (age > _SCHEDULE_MAX_AGE and snapshot["state"] != "stopped"):
        return {
            **unknown,
            "state": "stale",
            "updated_at": snapshot["updated_at"],
        }
    routines = {}
    for key, definition in definitions.items():
        row = snapshot.get("routines", {}).get(key)
        if row is None or any(
            row.get(field) != definition.get(field)
            for field in ("pages", "hour", "minute")
        ):
            continue
        if list(row.get("prefixes", ())) != list(
            definition.get("prefixes", ()),
        ):
            continue
        routines[key] = {
            "state": row["state"],
            "next_run_at": row.get("next_run_at")
            if snapshot["state"] == "running" and row["state"] == "scheduled"
            else None,
        }
    started = timestamp(snapshot.get("started_at"))
    return {
        "state": snapshot["state"],
        "routines": routines,
        "updated_at": snapshot["updated_at"],
        "started_at": snapshot["started_at"]
        if started is not None
        and started <= updated
        and snapshot["state"] in {"running", "paused"}
        else None,
    }


def next_run(schedule: Record, routine: str, interface: Interface) -> str:
    """Show actual next execution or unavailable schedule state.

    Returns:
        The actual next UTC time or a localized state explanation.
    """
    row = schedule["routines"].get(routine, {})
    if (
        schedule["state"] == "running"
        and row.get("state") == "scheduled"
        and row.get("next_run_at") is not None
    ):
        return utc_time(row["next_run_at"])
    labels = {
        "stale": "ops.schedule_stale",
        "stopped": "ops.schedule_stopped",
        "paused": "ops.schedule_paused",
    }
    key = labels.get(
        schedule["state"],
        "ops.schedule_paused"
        if row.get("state") == "paused"
        else "ops.schedule_missing",
    )
    return interface.text(key)


def uptime(
    schedule: Record,
    version: str,
    interface: Interface,
    now: dt.datetime,
) -> str:
    """Show runtime only when fresh observations confirm it.

    Returns:
        Localized continuous uptime or an unavailable explanation.
    """
    started = timestamp(schedule.get("started_at"))
    updated = timestamp(schedule.get("updated_at"))
    if started is None or updated is None:
        return interface.text("footer.uptime_unknown", version=version)
    if (
        schedule.get("state") not in {"running", "paused"}
        or started > updated
        or updated > now
        or (now - updated).total_seconds() > _SCHEDULE_MAX_AGE
    ):
        return interface.text("footer.uptime_unknown", version=version)
    minutes = int((now - started).total_seconds() // _MINUTE)
    return interface.text(
        "footer.uptime",
        version=version,
        days=minutes // _DAY_MINUTES,
        hours=minutes % _DAY_MINUTES // _HOUR_MINUTES,
        minutes=minutes % _HOUR_MINUTES,
        since=utc_time(schedule["started_at"]).removesuffix(" UTC"),
    )


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
