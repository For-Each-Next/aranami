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

from backend.website.newsletter_history import prepared_news
from backend.website.newsletter_homepage import collect_newsletter_homepage
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
    from pathlib import Path

_STAT_KEYS = ("total", "fa", "fl", "ga", "dyk", "bplus", "topics")
_HOMEPAGE_KEYS = {"featured", "good", "news", "history"}
_DYK_PAGE = "WikiProject:电子游戏/新条目推荐"
_AWARDS_PAGE = "Module:ACGaward/list"
_MAX_TEXT = 250_000
_MAX_ROWS = 5000
_MAX_SOURCES = 1600
_MAX_ISSUES = 4
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


def _apply_article_history(
    facts: dict,
    events: pl.DataFrame,
    issue: Issue,
    through: dt.date,
    provenance: dict,
) -> None:
    """Use recorded talk-page dates for quarterly newsletter facts."""
    dated = events.filter(
        pl.col("date").is_between(issue.start, through, closed="left"),
    )
    facts["quality_source"] = "article_history"
    undated = events.filter(pl.col("date").is_null()).group_by("kind").len()
    facts["history_undated"] = {
        **dict.fromkeys(("dyk", "news", "history", "quality"), 0),
        **dict(undated.iter_rows()),
    }
    facts["dyk"] = (
        dated
        .filter(pl.col("kind") == "dyk")
        .select("title", pl.col("date").dt.to_string("%Y-%m-%d"))
        .unique()
        .sort("date", "title")
        .to_dicts()
    )
    facts["quality_changes"] = (
        dated
        .filter(pl.col("kind") == "quality")
        .select(
            pl.col("date").dt.to_string("%Y-%m-%d"),
            "title",
            "grade",
            "action",
        )
        .unique()
        .sort("date", "grade", "action", "title")
        .to_dicts()
    )
    for title, revision in (
        events
        .filter(
            pl.col("date").is_null()
            | pl.col("date").is_between(issue.start, through, closed="left"),
        )
        .select("title", "revision_id")
        .unique()
        .iter_rows()
    ):
        url = f"https://zh.wikipedia.org/w/index.php?oldid={revision}"
        provenance[url] = {"label": f"Talk:{title}", "url": url}
    if "homepage" in facts:
        facts["homepage"]["news"] = prepared_news(events, issue.start, through)
    facts["available"] = True


def _retain_overview(
    facts: dict,
    prior: Mapping[str, object] | None,
    issue: Issue,
) -> None:
    """Reuse saved counts and timestamps for identical periods."""
    if not prior or prior.get("issue") != issue.value:
        return
    facts["previous_overview"].update(prior.get("previous_overview") or {})
    if (
        prior.get("observation_end") == facts["observation_end"]
        and prior.get("complete") == facts["complete"]
    ):
        facts["overview"].update(prior.get("overview") or {})
        facts["overview_observed_at"] = prior.get("overview_observed_at")


def _new_page_assessment_options(
    issue: Issue,
    now: dt.datetime,
    current: pl.DataFrame | None,
    lookup: Callable[[dt.date], tuple[pl.DataFrame, str] | None] | None,
) -> dict[str, object]:
    """Select grades without rewriting closed-quarter history.

    Returns:
        Grade collection options with the actual observation timestamp.
        An unavailable closed-quarter snapshot stays unavailable.
    """
    if now.date() < issue.end:
        assessments, observed_at = current, now.isoformat(timespec="seconds")
    else:
        assessments, observed_at = (
            lookup(issue.end) or (None, None)
            if lookup is not None
            else (None, None)
        )
    return {
        "assessments": assessments,
        "assessment_observed_at": observed_at,
    }


def _issue_facts(  # ruff: ignore[complex-structure, too-many-arguments]
    read: object,
    issue: Issue,
    now: dt.datetime,
    current_counts: Mapping[str, int] | None,
    *,
    members: set[str] | None,
    member_ids: set[int] | None,
    article_history: pl.DataFrame | None,
    today_history_path: Path | None,
    overview_at: (
        Callable[[dt.date], tuple[Mapping[str, int | None], str] | None] | None
    ),
    current_assessments: pl.DataFrame | None,
    assessments_at: (
        Callable[[dt.date], tuple[pl.DataFrame, str] | None] | None
    ),
    prior: Mapping[str, object] | None,
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
    _retain_overview(facts, prior, issue)
    if observation_end > issue.start and callable(getattr(read, "at", None)):
        start = dt.datetime.combine(issue.start, dt.time.min, dt.UTC)
        end = dt.datetime.combine(observation_end, dt.time.min, dt.UTC)
        if overview_at is not None:
            baseline, _ = overview_at(issue.start) or ({}, None)
            facts["previous_overview"].update(baseline)
            latest, observed = overview_at(observation_end) or (
                {},
                facts["overview_observed_at"],
            )
            facts["overview"].update(latest)
            facts["overview_observed_at"] = observed
        if main_layout and main_layout["source_issue"] == issue.previous:
            facts["previous_overview"] = {
                key: value
                if value is not None
                else main_layout["baseline_counts"][key]
                for key, value in facts["previous_overview"].items()
            }
        if not facts["complete"] and current_counts:
            facts["overview"].update(current_counts)
            facts["overview_observed_at"] = now.isoformat(timespec="seconds")
        if article_history is None:
            facts["dyk"] = _dyk(
                _read_optional(
                    lambda title: historical(title, end),
                    _DYK_PAGE,
                ),
                issue,
                observation_end,
            )
        facts["new_pages_data"] = collect_new_pages(
            SimpleNamespace(at=historical),
            issue,
            through=observation_end,
            resolve=getattr(read, "resolve", None),
            resolve_many=getattr(read, "resolve_many", None),
            resolve_page_id=getattr(read, "resolve_page_id", None),
            **_new_page_assessment_options(
                issue,
                now,
                current_assessments,
                assessments_at,
            ),
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
        if overview_at is not None:
            baseline, _ = overview_at(issue.start) or ({}, None)
            facts["previous_overview"].update(baseline)
        facts["overview"].update(current_counts)
        facts["overview_observed_at"] = now.isoformat(timespec="seconds")
        facts["available"] = True
    if members is not None:
        facts.update(
            collect_newsletter_homepage(
                read,
                issue,
                through=observation_end,
                members=members,
                member_ids=member_ids,
                now=now,
                history_path=today_history_path,
            ),
        )
        facts["available"] |= any(
            rows is not None for rows in facts["homepage"].values()
        )
    if article_history is not None and observation_end > issue.start:
        _apply_article_history(
            facts,
            article_history,
            issue,
            observation_end,
            provenance,
        )
    facts["sources"] = list(provenance.values())
    return facts


def collect_newsletter(  # ruff: ignore[too-many-arguments]
    read: object,
    *,
    now: dt.datetime,
    current_counts: Mapping[str, int] | None = None,
    members: set[str] | None = None,
    member_ids: set[int] | None = None,
    article_history: pl.DataFrame | None = None,
    today_history_path: Path | None = None,
    overview_at: (
        Callable[[dt.date], tuple[Mapping[str, int | None], str] | None] | None
    ) = None,
    current_assessments: pl.DataFrame | None = None,
    assessments_at: (
        Callable[[dt.date], tuple[pl.DataFrame, str] | None] | None
    ) = None,
    prior_facts: Mapping[str, object] | None = None,
) -> dict:
    """Collect drafts for the latest quarter and its successor.

    Args:
        read: Current-page reader with historical ``at`` support.
        now: Aware collection time, separate from source dates.
        current_counts: Fresh counts used only for open quarters.
        members: Current article titles from the project database query.
            When supplied, collect quarter-specific homepage history.
        member_ids: Project article IDs for anniversary scope joins.
        article_history: Events recorded on project talk pages.
            Complete snapshots supply DYK and quality events directly.
        today_history_path: Separate wiki-wide daily-source archive.
        overview_at: Read retained database counts for an exclusive UTC
            date boundary with their actual observation timestamp.
            Missing snapshots leave their counts unknown.
        current_assessments: Fresh internal grades for open-quarter new
            articles. Closed quarters never use these observations.
        assessments_at: Read retained assessment rows for an exact UTC
            date with their actual observation time. Missing historical
            snapshots leave new-article grade breakdowns unavailable.
        prior_facts: Previously validated saved issues. Retain their
            overview counts only for identical reporting boundaries.

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
            issue.value: _issue_facts(
                read,
                issue,
                now,
                current_counts,
                members=members,
                member_ids=member_ids,
                article_history=article_history,
                today_history_path=today_history_path,
                overview_at=overview_at,
                current_assessments=current_assessments,
                assessments_at=assessments_at,
                prior=(prior_facts or {}).get("issues", {}).get(issue.value),
            )
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
        and value["grade"] in {"FA", "FL", "GA", "A", "Bplus"}
        and isinstance(value.get("action"), str)
        and value["action"] in {"added", "removed"}
    )


def _homepage_row(
    row: object,
    section: str,
    issue: Issue,
    through: dt.date,
) -> bool:
    """Validate a primary subject and its observed display date.

    Returns:
        Whether the record belongs to this section and elapsed quarter.
    """
    if not isinstance(row, dict) or not _title(row.get("title")):
        return False
    try:
        day = dt.date.fromisoformat(row["date"])
    except KeyError, TypeError, ValueError:
        return False
    return (
        issue.start <= day < through
        and row["date"] == day.isoformat()
        and _source({"label": row["title"], "url": row.get("url")})
        and (
            isinstance(row.get("kind"), str) and row["kind"] in {"fa", "fl"}
            if section == "featured"
            else row.get("kind") == "ga"
            if section == "good"
            else True
        )
        and (
            row.get("date_kind") == "appearance"
            or (row.get("date_kind") == "window_start" and day == issue.start)
            if section == "news"
            else row.get("date_kind") is None
        )
    )


def _homepage_facts(  # ruff: ignore[too-many-return-statements]
    facts: dict,
    issue: Issue,
    through: dt.date,
) -> bool:
    """Validate optional quarter-specific homepage data and evidence.

    Returns:
        Whether source coverage agrees, or legacy fields are absent.
    """
    if "homepage" not in facts:
        return "homepage_sources" not in facts
    sections = facts["homepage"]
    sources = facts.get("homepage_sources")
    if (
        not isinstance(sections, dict)
        or set(sections) != _HOMEPAGE_KEYS
        or not isinstance(sources, dict)
    ):
        return False
    if through == issue.start:
        return all(rows is None for rows in sections.values()) and not sources
    if set(sources) != _HOMEPAGE_KEYS:
        return False
    for section, rows in sections.items():
        evidence = sources[section]
        if (
            rows is None
            or not _rows(
                rows,
                lambda row, section=section: _homepage_row(
                    row,
                    section,
                    issue,
                    through,
                ),
            )
            or not isinstance(evidence, dict)
        ):
            return False
        if (
            evidence.get("period")
            != {"start": str(issue.start), "end": str(through)}
            or evidence.get("updated_at") != facts["updated_at"]
            or not isinstance(evidence.get("sources"), list)
            or len(evidence["sources"]) > _MAX_SOURCES
            or not all(_source(source) for source in evidence["sources"])
        ):
            return False
    return True


def _valid_observation_stamp(
    observed: object,
    stamp: dt.datetime,
    observation_end: dt.date,
) -> bool:
    """Check that an actual observation belongs to its saved day.

    Returns:
        Whether no timestamp was retained or the aware observation is
        within its boundary's day and no later than collection.
    """
    if observed is None:
        return True
    try:
        observation = dt.datetime.fromisoformat(observed)
    except ValueError, TypeError:
        return False
    return (
        observation.tzinfo is not None
        and observation.utcoffset() is not None
        and stamp.tzinfo is not None
        and stamp.utcoffset() is not None
        and observation.astimezone(dt.UTC).date() == observation_end
        and observation <= stamp
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
            or facts.get("overview_observed_at") is not None
        )
        and stamp.utcoffset() == dt.timedelta()
        and _counts(facts.get("overview"))
        and _valid_observation_stamp(
            facts.get("overview_observed_at"),
            stamp,
            observation_end,
        )
        and _counts(facts.get("previous_overview"))
        and (
            facts.get("quality_source") is None
            or facts["quality_source"] == "article_history"
        )
        and (
            "history_undated" not in facts
            or (
                isinstance(facts["history_undated"], dict)
                and set(facts["history_undated"])
                == {"quality", "dyk", "news", "history"}
                and all(
                    type(count) is int and 0 <= count <= _MAX_COUNT
                    for count in facts["history_undated"].values()
                )
            )
        )
        and valid_main_layout(facts.get("main_layout"))
        and valid_subpage_layouts(facts.get("subpage_layouts"))
        and valid_new_pages(
            facts.get("new_pages_data"),
            issue,
            through=observation_end,
        )
        and _valid_observation_stamp(
            (facts.get("new_pages_data") or {}).get("grades_observed_at"),
            stamp,
            observation_end,
        )
        and _homepage_facts(facts, issue, observation_end)
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
