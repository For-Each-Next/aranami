"""Prepare quarterly newsletter facts from historical wiki revisions.

The hub calls ``collect_newsletter`` during website refreshes. Public
requests only consume its validated saved result. Missing historical
observations remain unknown. Upcoming issues collect the elapsed part of
their quarter, using completed UTC dates rather than future boundaries.
"""

from __future__ import annotations

import datetime as dt
import re
from itertools import starmap
from types import SimpleNamespace
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import mwparserfromhell
import polars as pl
from pywikibot.exceptions import NoPageError

from backend.services.zhwiki.dyks import article_members
from backend.website.newsletter_layout import (
    prepare_main_layout,
    valid_main_layout,
)
from backend.website.newsletter_newpages import (
    collect_new_pages,
    valid_new_pages,
)
from backend.website.newsletter_subpage_layout import (
    prepare_subpage_layout,
    valid_subpage_layouts,
)
from shared.newsletter import ROOT, SUFFIXES, Issue
from shared.support.regions import region_content

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_STAT_KEYS = ("total", "fa", "fl", "ga", "dyk", "bplus", "topics")
_COUNT_PAGES = {
    "fa": "WikiProject:电子游戏/认证条目/FA",
    "fl": "WikiProject:电子游戏/认证条目/FL",
    "ga": "WikiProject:电子游戏/认证条目/GA",
    "bplus": "WikiProject:电子游戏/认证条目/Bplus",
    "topics": "WikiProject:电子游戏/认证条目/GT",
}
_DYK_PAGE = "WikiProject:电子游戏/新条目推荐"
_AWARDS_PAGE = "Module:ACGaward/list"
_MAX_TEXT = 250_000
_MAX_ROWS = 5000
_MAX_REVISIONS = 500
_MAX_SOURCES = 1600
_MAX_ISSUES = 4
_MAX_STATES = 500_000
_MAX_COUNT = 10_000_000
_AWARD_PLACES = 5
_AWARD_THRESHOLD = 10
_LAYOUT_LOOKBACK = 4
_SCORE = re.compile(
    r'^\s*\["([^"\\\n]+)"\]\s*=\s*(\d+(?:\.5)?),?\s*$',
    re.MULTILINE,
)


def _read_optional(read: Callable[[str], str], title: str) -> str | None:
    """Read optional sources, preserving transport failures.

    Returns:
        Source text, or none when the page does not exist.
    """
    try:
        return read(title)
    except NoPageError:
        return None


def _count(text: str | None) -> int | None:
    """Read the verified literal labelled count section.

    Returns:
        The reported count, or none if the section is unavailable.
    """
    if text is None:
        return None
    managed = region_content(text, "assessment-count")
    if managed is not None and re.fullmatch(r"\s*[\d,]+\s*", managed):
        return int(managed.strip().replace(",", ""))
    matches = re.findall(
        r'<section\s+begin=["\']?count["\']?\s*/>\s*([\d,]+)'
        r'\s*<section\s+end=["\']?count["\']?\s*/>',
        text,
        re.IGNORECASE,
    )
    return int(matches[0].replace(",", "")) if len(matches) == 1 else None


def _summary_total(text: str | None, boundary: dt.datetime) -> int | None:
    """Read an exact reporting day's observed article total.

    Returns:
        The observed total, or none without that day's summary.
    """
    if text is None:
        return None
    day = boundary.date() - dt.timedelta(days=1)
    expected = f"{day.year}年{day.month}月{day.day}日"
    for section in mwparserfromhell.parse(text).get_sections(
        levels=[3],
        include_lead=False,
    ):
        headings = section.filter_headings(recursive=False)
        if headings and str(headings[0].title).strip() == expected:
            match = re.search(r"'''总条目'''([\d,]+)篇。", str(section))
            return int(match[1].replace(",", "")) if match else None
    return None


def _dyk(
    text: str | None,
    issue: Issue,
    through: dt.date,
) -> list[dict[str, str]] | None:
    """Extract dated DYK entries within the selected quarter.

    Returns:
        Dated article records, or none for an unavailable source.
    """
    if text is None or 'begin="dyk"' not in text:
        return None
    rows = []
    for template in mwparserfromhell.parse(
        region_content(text, "dyk") or "",
    ).filter_templates():
        if not template.name.matches("PJ:VG/DYK/item"):
            continue
        if not template.has("1") or not template.has("date"):
            return None
        title = str(template.get("1").value).strip().replace("_", " ")
        for label in str(template.get("date").value).split("、"):
            if label.strip() == "日期不詳":
                continue
            day = dt.date.fromisoformat(label.strip())
            if issue.start <= day < through:
                rows.append({"title": title, "date": day.isoformat()})
    return (
        pl
        .DataFrame(rows, schema={"title": pl.String, "date": pl.String})
        .unique()
        .sort("date", "title")
        .to_dicts()
    )


def _scores(text: str | None) -> pl.DataFrame | None:
    """Read the audited Lua score table without executing it.

    Returns:
        Typed scores, or none when the literal table is invalid.
    """
    if text is None:
        return None
    stripped = re.sub(r"--[^\n]*", "", text).strip()
    if not stripped.startswith("return {") or not stripped.endswith("}"):
        return None
    body = stripped[len("return {") : -1]
    rows = [
        {"user": name, "score": float(score)}
        for name, score in _SCORE.findall(body)
    ]
    if _SCORE.sub("", body).strip():
        return None
    frame = pl.DataFrame(
        rows,
        schema={"user": pl.String, "score": pl.Float64},
    )
    if frame.get_column("user").n_unique() != frame.height:
        return None
    return frame


def _awards(
    before: str | None,
    after: str | None,
) -> tuple[list[dict[str, object]] | None, list[str] | None]:
    """Compare award totals, retaining fifth-place ties.

    Returns:
        The quarterly ranking and first-time award recipients.
    """
    first, last = _scores(before), _scores(after)
    if first is None or last is None:
        return None, None
    changes = (
        last
        .rename({"score": "total"})
        .join(first.rename({"score": "previous"}), on="user", how="left")
        .with_columns(pl.col("previous").fill_null(0))
        .with_columns(
            (pl.col("total").floor() - pl.col("previous").floor())
            .cast(pl.Int64)
            .alias("gained"),
            pl.col("total").floor().cast(pl.Int64),
        )
    )
    winners = (
        changes
        .filter(pl.col("gained") > 0)
        .with_columns(
            pl.col("gained").rank(method="min", descending=True).alias("rank"),
        )
        .filter(pl.col("rank") <= _AWARD_PLACES)
        .sort(["gained", "user"], descending=[True, False])
        .select("user", "gained", "total")
    )
    newcomers = (
        changes
        .filter(
            (pl.col("previous") < _AWARD_THRESHOLD)
            & (pl.col("total") >= _AWARD_THRESHOLD),
        )
        .sort("user")
        .get_column("user")
        .to_list()
    )
    return winners.to_dicts(), newcomers


def _announcements(text: str | None) -> str | None:
    """Preserve the existing issue's announcement section.

    Returns:
        Editable prose, or none if no section exists.
    """
    if text is None:
        return None
    code = mwparserfromhell.parse(text)
    for section in code.get_sections(levels=[3], include_lead=False):
        headings = section.filter_headings(recursive=False)
        if headings and str(headings[0].title).strip() == "公告":
            section.remove(headings[0])
            return str(section).strip()
    return None


def _members(text: str) -> list[str] | None:
    """Read the recognized-content list, checking its count.

    Returns:
        Unique titles, or none if the verified contract differs.
    """
    match = re.search(
        r'<section\s+begin=["\']?list["\']?\s*/>(.*?)'
        r'<section\s+end=["\']?list["\']?\s*/>',
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        return None
    titles = [
        str(link.title).lstrip(":").strip().replace("_", " ")
        for link in mwparserfromhell.parse(match[1]).filter_wikilinks()
    ]
    if len(titles) != len(set(titles)) or _count(text) != len(titles):
        return None
    return titles


def _quality_changes(
    read: object,
    issue: Issue,
    provenance: dict,
    through: dt.date,
) -> list | None:
    """Compare list revisions without inventing review dates.

    Returns:
        Observed list changes, or none without complete history.
    """
    if not callable(getattr(read, "history", None)):
        return None
    changes = []
    for grade in ("fa", "fl", "ga"):
        title = _COUNT_PAGES[grade]
        try:
            history = read.history(title, issue.start, through)
        except NoPageError:
            return None
        if not history or len(history) > _MAX_REVISIONS:
            return None
        revisions, members = [], []
        for index, row in enumerate(history):
            stamp = row["timestamp"]
            titles = _members(row["text"])
            if (
                titles is None
                or len(titles) > _MAX_ROWS
                or (index == 0 and stamp.date() >= issue.start)
            ):
                return None
            if type(row.get("revision")) is int:
                url = f"https://zh.wikipedia.org/w/index.php?oldid={row['revision']}"
                provenance[url] = {"label": title, "url": url}
            revisions.append({
                "index": index,
                "date": stamp.date().isoformat(),
            })
            members.extend(
                {"index": index, "title": name, "present": True}
                for name in titles
            )
        observations = pl.DataFrame(
            members,
            schema={
                "index": pl.Int64,
                "title": pl.String,
                "present": pl.Boolean,
            },
        )
        if (
            observations.get_column("title").n_unique() * len(revisions)
            > _MAX_STATES
        ):
            return None
        states = (
            pl
            .DataFrame(
                revisions,
                schema={"index": pl.Int64, "date": pl.String},
            )
            .join(observations.select("title").unique(), how="cross")
            .join(observations, on=["index", "title"], how="left")
            .with_columns(pl.col("present").fill_null(value=False))
            .sort("title", "index")
            .with_columns(
                pl.col("present").shift().over("title").alias("previous"),
            )
            .filter(
                pl.col("previous").is_not_null()
                & (pl.col("present") != pl.col("previous"))
                & (pl.col("date") >= issue.start.isoformat())
                & (pl.col("date") < through.isoformat()),
            )
            .select(
                "date",
                "title",
                pl.lit(grade.upper()).alias("grade"),
                pl
                .when("present")
                .then(pl.lit("added"))
                .otherwise(pl.lit("removed"))
                .alias("action"),
            )
        )
        changes.append(states)
    return (
        pl.concat(changes).sort("date", "grade", "action", "title").to_dicts()
    )


def _overview(
    read_at: Callable[[str, dt.datetime], str],
    boundary: dt.datetime,
) -> tuple[dict[str, int | None], str | None]:
    """Prepare historical counts and their DYK source.

    Returns:
        Nullable counts and the retained historical DYK text.
    """

    def read(title: str) -> str:
        """Apply one exclusive boundary to every overview source.

        Returns:
            The historical source text.
        """
        return read_at(title, boundary)

    counts = dict.fromkeys(_STAT_KEYS)
    counts["total"] = _summary_total(
        _read_optional(read, "WikiProject:电子游戏/评级/日志"),
        boundary,
    )
    for key, title in _COUNT_PAGES.items():
        counts[key] = _count(_read_optional(read, title))
    dyk_text = _read_optional(read, _DYK_PAGE)
    if dyk_text is not None and 'begin="dyk"' in dyk_text:
        counts["dyk"] = len(article_members(dyk_text, range_name="dyk"))
    return counts, dyk_text


def _previous_main_layout(
    read: Callable[[str], str],
    issue: Issue,
) -> dict | None:
    """Read the nearest supported prior issue layout.

    Returns:
        Actual source preparation, or none without a supported layout.
    """
    candidate = issue
    for _ in range(_LAYOUT_LOOKBACK):
        try:
            candidate = Issue.parse(candidate.previous)
        except ValueError:
            break
        original = _read_optional(read, f"{ROOT}/{candidate.value}")
        if original is not None:
            layout = prepare_main_layout(original, candidate)
            if layout is not None:
                return layout
    return None


def _previous_subpage_layout(
    read: Callable[[str], str],
    issue: Issue,
    suffix: str,
    kind: str,
) -> dict | None:
    """Read a supported original subpage from a preceding issue.

    Returns:
        Original source preparation, or none when unavailable.
    """
    candidate = issue
    for _ in range(_LAYOUT_LOOKBACK):
        try:
            candidate = Issue.parse(candidate.previous)
        except ValueError:
            break
        original = _read_optional(read, f"{ROOT}/{candidate.value}/{suffix}")
        if original is not None:
            layout = prepare_subpage_layout(original, candidate, kind)
            if layout is not None:
                return layout
    return None


def _issue_facts(
    read: object,
    issue: Issue,
    now: dt.datetime,
    current_counts: Mapping[str, int] | None,
) -> dict:
    """Prepare elapsed-quarter observations for one newsletter issue.

    Returns:
        Bounded facts, original drafts, and source evidence.
    """
    provenance = {}

    def remember(title: str, text: str) -> str:
        """Retain each revision, including both boundary reads.

        Returns:
            The unchanged source text.
        """
        sources = getattr(read, "sources", {})
        if isinstance(sources, dict) and title in sources:
            source = sources[title]
            provenance[source["url"]] = source.copy()
        return text

    def current(title: str) -> str:
        """Read a target's current draft and record provenance.

        Returns:
            The unchanged editorial source.
        """
        return remember(title, read(title))

    def historical(title: str, boundary: dt.datetime) -> str:
        """Read historical source and record its revision.

        Returns:
            The source text at the exclusive boundary.
        """
        return remember(title, read.at(title, boundary))

    existing = {
        suffix: _read_optional(
            current,
            f"{ROOT}/{issue.value}" + (f"/{suffix}" if suffix else ""),
        )
        for suffix in SUFFIXES
    }
    main_layout = _previous_main_layout(current, issue)
    subpage_layouts = {
        suffix: _previous_subpage_layout(current, issue, suffix, kind)
        for suffix, kind in (
            ("特優內容", "quality"),
            ("宣告", "announcements"),
        )
    }
    observation_end = max(issue.start, min(issue.end, now.date()))
    facts = {
        "issue": issue.value,
        "period": f"{issue.start.isoformat()}/{issue.end.isoformat()}",
        "updated_at": now.isoformat(timespec="seconds"),
        "observation_end": observation_end.isoformat(),
        "complete": observation_end == issue.end,
        "available": False,
        "overview": dict.fromkeys(_STAT_KEYS),
        "overview_observed_at": None,
        "previous_overview": dict.fromkeys(_STAT_KEYS),
        "quality": None,
        "quality_changes": None,
        "new_pages": None,
        "new_pages_data": None,
        "announcements": _announcements(existing["宣告"]),
        "dyk": None,
        "awards": None,
        "newcomers": None,
        "existing": existing,
        "main_layout": main_layout,
        "subpage_layouts": subpage_layouts,
        "sources": [],
    }
    if observation_end > issue.start and callable(getattr(read, "at", None)):
        start = dt.datetime.combine(issue.start, dt.time.min, dt.UTC)
        end = dt.datetime.combine(observation_end, dt.time.min, dt.UTC)
        facts["previous_overview"], _ = _overview(historical, start)
        if main_layout and main_layout["source_issue"] == issue.previous:
            facts["previous_overview"] = {
                key: value
                if value is not None
                else main_layout["baseline_counts"][key]
                for key, value in facts["previous_overview"].items()
            }
        facts["overview"], dyk_text = _overview(historical, end)
        if not facts["complete"] and current_counts:
            facts["overview"].update(current_counts)
            facts["overview_observed_at"] = now.isoformat(timespec="seconds")
        facts["dyk"] = _dyk(dyk_text, issue, observation_end)
        facts["quality_changes"] = _quality_changes(
            read,
            issue,
            provenance,
            observation_end,
        )
        facts["new_pages_data"] = collect_new_pages(
            SimpleNamespace(at=historical),
            issue,
            through=observation_end,
        )
        facts["awards"], facts["newcomers"] = _awards(
            _read_optional(
                lambda title: historical(title, start),
                _AWARDS_PAGE,
            ),
            _read_optional(lambda title: historical(title, end), _AWARDS_PAGE),
        )
        facts["available"] = (
            any(count is not None for count in facts["overview"].values())
            or any(
                facts[name] is not None
                for name in ("dyk", "quality_changes", "awards")
            )
            or any(
                row["count"] is not None
                for row in facts["new_pages_data"]["months"]
            )
            or facts["new_pages_data"]["known_count"] > 0
        )
    elif current_counts and callable(getattr(read, "at", None)):
        boundary = dt.datetime.combine(issue.start, dt.time.min, dt.UTC)
        baseline, _ = _overview(historical, boundary)
        facts["previous_overview"] = baseline
        facts["overview"] = {**baseline, **current_counts}
        facts["overview_observed_at"] = now.isoformat(timespec="seconds")
        facts["available"] = True
    facts["sources"] = list(provenance.values())
    return facts


def collect_newsletter(
    read: object,
    *,
    now: dt.datetime,
    current_counts: Mapping[str, int] | None = None,
) -> dict:
    """Collect drafts for the latest quarter and its successor.

    Args:
        read: Current-page reader with historical ``at`` support.
        now: Aware collection time, separate from source dates.
        current_counts: Fresh counts used only for open quarters.

    Returns:
        Saved issue facts and original editorial drafts for the website.

    Raises:
        ValueError: The clock is naive or collected data exceeds bounds.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        message = "Newsletter collection requires an aware timestamp."
        raise ValueError(message)
    if current_counts is not None and any(
        key not in _STAT_KEYS
        or type(value) is not int
        or not 0 <= value <= _MAX_COUNT
        for key, value in current_counts.items()
    ):
        message = "Current newsletter counts must be bounded observations."
        raise ValueError(message)
    now = now.astimezone(dt.UTC)
    month = ((now.month - 1) // 3) * 3 + 1
    current = Issue.parse(f"{now.year:04d}-{month:02d}")
    issues = (current, Issue.parse(current.next))
    result = {
        "issues": {
            issue.value: _issue_facts(read, issue, now, current_counts)
            for issue in issues
        },
    }
    if not valid_newsletter(result):
        message = "Newsletter facts failed bounded snapshot validation."
        raise ValueError(message)
    return result


def _text(value: object, maximum: int = _MAX_TEXT) -> bool:
    """Check text bounds and supported control characters.

    Returns:
        Whether the text is safe to retain.
    """
    if not isinstance(value, str):
        return False
    try:
        size = len(value.encode())
    except UnicodeEncodeError:
        return False
    return (
        size <= maximum
        and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value) is None
    )


def _counts(value: object) -> bool:
    """Check nullable integer observations.

    Returns:
        Whether every observation is a bounded integer or null.
    """
    return (
        isinstance(value, dict)
        and set(value) == set(_STAT_KEYS)
        and all(
            item is None or (type(item) is int and 0 <= item <= _MAX_COUNT)
            for item in value.values()
        )
    )


def _rows(value: object, predicate: Callable[[object], bool]) -> bool:
    """Validate optional bounded observation lists.

    Returns:
        Whether every present row satisfies its validator.
    """
    return value is None or (
        isinstance(value, list)
        and len(value) <= _MAX_ROWS
        and all(predicate(row) for row in value)
    )


def _title(value: object) -> bool:
    """Require a wiki title safe for link interpolation.

    Returns:
        Whether the value is a bounded plain title.
    """
    return (
        _text(value, 1000)
        and bool(value.strip())
        and re.search(r"[\[\]{}|<>\r\n\t]", value) is None
    )


def _source(value: object) -> bool:
    """Require bounded HTTPS Wikipedia provenance links.

    Returns:
        Whether the source label and URL are valid.
    """
    if not isinstance(value, dict) or not _text(value.get("label"), 1000):
        return False
    url = value.get("url")
    if not _text(url, 4096) or re.search(r"[\x00-\x20\\]", url):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "zh.wikipedia.org"
        and (parsed.path == "/w/index.php" or parsed.path.startswith("/wiki/"))
    )


def _quality_row(value: object, issue: Issue, through: dt.date) -> bool:
    """Validate a list change inside the reporting quarter.

    Returns:
        Whether the event has valid date, grade, and identity.
    """
    if not isinstance(value, dict) or not _title(value.get("title")):
        return False
    try:
        day = dt.date.fromisoformat(value["date"])
    except TypeError, ValueError, KeyError:
        return False
    return (
        issue.start <= day < through
        and isinstance(value.get("grade"), str)
        and value["grade"] in {"FA", "FL", "GA"}
        and isinstance(value.get("action"), str)
        and value["action"] in {"added", "removed"}
    )


def _valid_issue(key: str, facts: object) -> bool:
    """Validate one saved issue and its presentation fields.

    Returns:
        Whether the entire issue satisfies the saved contract.
    """
    if not isinstance(facts, dict):
        return False
    try:
        issue = Issue.parse(key)
        stamp = dt.datetime.fromisoformat(facts["updated_at"])
        observation_end = dt.date.fromisoformat(facts["observation_end"])
        dated = facts.get("dyk") or []
        dates_valid = all(
            isinstance(row, dict)
            and _title(row.get("title"))
            and issue.start
            <= dt.date.fromisoformat(row["date"])
            < observation_end
            for row in dated
        )
    except ValueError, TypeError, KeyError, AttributeError:
        return False
    existing = facts.get("existing")
    return (
        facts.get("issue") == key
        and facts.get("period") == f"{issue.start}/{issue.end}"
        and observation_end == max(issue.start, min(issue.end, stamp.date()))
        and type(facts.get("complete")) is bool
        and facts["complete"] == (observation_end == issue.end)
        and type(facts.get("available")) is bool
        and (
            not facts["available"]
            or observation_end > issue.start
            or facts.get("overview_observed_at") == facts["updated_at"]
        )
        and stamp.utcoffset() == dt.timedelta()
        and _counts(facts.get("overview"))
        and (
            facts.get("overview_observed_at") is None
            or (
                facts["overview_observed_at"] == facts["updated_at"]
                and not facts["complete"]
                and observation_end >= issue.start
            )
        )
        and _counts(facts.get("previous_overview"))
        and valid_main_layout(facts.get("main_layout"))
        and valid_subpage_layouts(facts.get("subpage_layouts"))
        and valid_new_pages(
            facts.get("new_pages_data"),
            issue,
            through=observation_end,
        )
        and all(
            facts.get(name) is None or _text(facts[name])
            for name in ("quality", "new_pages", "announcements")
        )
        and isinstance(existing, dict)
        and set(existing) == set(SUFFIXES)
        and all(value is None or _text(value) for value in existing.values())
        and dates_valid
        and _rows(facts.get("dyk"), lambda row: isinstance(row, dict))
        and _rows(
            facts.get("quality_changes"),
            lambda row: _quality_row(row, issue, observation_end),
        )
        and _rows(facts.get("newcomers"), _title)
        and _rows(
            facts.get("awards"),
            lambda row: (
                isinstance(row, dict)
                and _title(row.get("user"))
                and all(
                    type(row.get(field)) is int
                    and 0 <= row[field] <= _MAX_COUNT
                    for field in ("gained", "total")
                )
            ),
        )
        and isinstance(facts.get("sources"), list)
        and len(facts["sources"]) <= _MAX_SOURCES
        and all(_source(source) for source in facts["sources"])
    )


def valid_newsletter(value: object) -> bool:
    """Validate saved newsletter facts without external reads.

    Args:
        value: Untrusted decoded snapshot value.

    Returns:
        Whether all issues satisfy the bounded presentation contract.
    """
    return (
        isinstance(value, dict)
        and isinstance(value.get("issues"), dict)
        and len(value["issues"]) <= _MAX_ISSUES
        and all(starmap(_valid_issue, value["issues"].items()))
    )
