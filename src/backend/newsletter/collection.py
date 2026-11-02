"""Acquire newsletter facts during scheduled background refreshes.

Use collect_source_facts for a complete observation. Refresh owns
persistence. Requests read saved facts through newsletter.schema.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from pywikibot.exceptions import NoPageError

from backend.articles.history import prepared_news
from backend.newsletter.annual import (
    collect_year_new_pages,
    enrich_year_dyk,
    prepare_history,
    prepare_quality_year_ends,
    prepare_year_dyk_current,
    prepare_year_dyk_quarters,
    prepare_year_history,
)
from backend.newsletter.homepage import collect_newsletter_homepage
from backend.newsletter.layout import (
    prepare_main_layout,
)
from backend.newsletter.newpages import (
    collect_new_pages,
)
from backend.newsletter.publication import (
    read_publication_plan,
    reporting_start,
)
from backend.newsletter.schema import STAT_KEYS, valid_newsletter
from backend.newsletter.statistics import (
    collect_popularity,
    quality_statistics,
)
from backend.newsletter.subpages import (
    prepare_subpage_layout,
)
from shared.newsletter import ROOT, SUFFIXES, Issue
from shared.support.regions import region_content

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from shared.sources.wiki import WikiReader

_DYK_PAGE = "WikiProject:电子游戏/新条目推荐"
_AWARDS_PAGE = "Module:ACGaward/list"
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
    *,
    start: dt.date | None = None,
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
            if (start or issue.start) <= day < through:
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
    *,
    limit: int | None = _AWARD_PLACES,
    first_score: bool = False,
) -> tuple[list[dict[str, object]] | None, list[str] | None]:
    """Compare award totals, optionally limiting ranks with ties.

    Args:
        before: Award totals before the reporting period.
        after: Award totals after the reporting period.
        limit: Last included rank, or none for every positive scorer.
        first_score: Mark first points instead of award eligibility.

    Returns:
        The period ranking and first-time award recipients.
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
        .filter(
            pl.lit(value=True) if limit is None else pl.col("rank") <= limit,
        )
        .sort(["gained", "user"], descending=[True, False])
        .select("user", "gained", "total")
    )
    threshold = 1 if first_score else _AWARD_THRESHOLD
    newcomers = (
        changes
        .filter(
            (pl.col("previous") < threshold) & (pl.col("total") >= threshold),
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
    prepared = prepare_history(events, issue.start, through)
    facts["quality_source"] = "article_history"
    undated = events.filter(pl.col("date").is_null()).group_by("kind").len()
    facts["history_undated"] = {
        **dict.fromkeys(("dyk", "news", "history", "quality"), 0),
        **dict(undated.iter_rows()),
    }
    facts["dyk"] = prepared["dyk"]
    facts["quality_changes"] = prepared["quality_changes"]
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


def _year_assessment_options(
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


def _year_review(  # ruff: ignore[too-many-arguments]
    historical: Callable[[str, dt.datetime], str] | None,
    issue: Issue,
    now: dt.datetime,
    through: dt.date,
    *,
    article_history: pl.DataFrame | None,
    quality_history: pl.DataFrame | None,
    current_counts: Mapping[str, int] | None,
    current_assessments: pl.DataFrame | None,
    dyk_projects: pl.DataFrame | None,
    assessments_at: (
        Callable[[dt.date], tuple[pl.DataFrame, str] | None] | None
    ),
    overview_at: (
        Callable[[dt.date], tuple[Mapping[str, int | None], str] | None] | None
    ),
    provenance: dict,
    revision_counts: Callable[[dt.date, dt.date], pl.DataFrame] | None,
    source_for: Callable[[str], Mapping | None],
    annual_pageviews: Callable[[int, dt.date], dict | None] | None = None,
    revision_history: (
        Callable[[str, dt.date, dt.date], list[dict[str, object]]] | None
    ) = None,
) -> dict:
    """Collect January's preceding calendar year at one source cutoff.

    Returns:
        Annual observations with unavailable values kept as null.
    """
    start = dt.date(issue.year - 1, 1, 1)
    result = {
        "year": start.year,
        "period": f"{start}/{issue.end}",
        "observation_end": through.isoformat(),
        "complete": through == issue.end,
        "overview": dict.fromkeys(STAT_KEYS),
        "previous_overview": dict.fromkeys(STAT_KEYS),
        "overview_observed_at": None,
        "previous_overview_observed_at": None,
        "new_pages_quarters": None,
        "awards": None,
        "newcomers": None,
    }
    assessment_options = _year_assessment_options(
        issue,
        now,
        current_assessments,
        assessments_at,
    )
    history = prepare_year_history(
        article_history,
        issue,
        through,
        assessments=assessment_options["assessments"],
    )
    for source in history.pop("sources"):
        provenance[source["url"]] = source
    result.update(history)
    result["quality_year_ends"] = prepare_quality_year_ends(
        quality_history if quality_history is not None else article_history,
        through,
        assessments=assessment_options["assessments"],
    )
    result["dyk_new_quarters"] = prepare_year_dyk_quarters(
        article_history,
        issue,
        through,
    )
    result["dyk_current"] = prepare_year_dyk_current(
        article_history,
        issue,
        now.date(),
    )
    result["previous_dyk_count"] = None
    if article_history is not None:
        previous = prepare_history(
            article_history,
            dt.date(start.year - 1, 1, 1),
            start,
        )
        result["dyk_new_count"] = (
            article_history
            .filter(
                pl.col("kind") == "dyk",
            )
            .group_by("title")
            .agg(pl.col("date").min())
            .filter(
                pl.col("date").is_between(start, through, closed="left"),
            )
            .height
        )
        result["previous_dyk_count"] = len({
            row["title"] for row in previous["dyk"] or []
        })
    if overview_at is not None:
        for key, boundary in (
            ("previous_overview", start),
            ("overview", through),
        ):
            counts, observed = overview_at(boundary) or ({}, None)
            result[key].update(counts)
            result[f"{key}_observed_at"] = observed
    if through < issue.end and current_counts:
        result["overview"].update(current_counts)
        result["overview_observed_at"] = now.isoformat(timespec="seconds")
    if historical is not None:
        boundary = dt.datetime.combine(through, dt.time.min, dt.UTC)
        result["new_pages_quarters"] = collect_year_new_pages(
            historical,
            issue,
            through,
        )
        result["awards"], result["newcomers"] = _awards(
            _read_optional(
                lambda title: historical(
                    title,
                    dt.datetime.combine(start, dt.time.min, dt.UTC),
                ),
                _AWARDS_PAGE,
            ),
            _read_optional(
                lambda title: historical(title, boundary),
                _AWARDS_PAGE,
            ),
            limit=None,
            first_score=True,
        )
        if article_history is None:
            result["dyk"] = _dyk(
                _read_optional(
                    lambda title: historical(title, boundary),
                    _DYK_PAGE,
                ),
                issue,
                through,
                start=start,
            )
    result["dyk"] = enrich_year_dyk(
        result["dyk"],
        historical,
        through,
        assessment_options["assessments"],
        projects=dyk_projects,
    )
    result["dyk_assessments_observed_at"] = (
        assessment_options["assessment_observed_at"]
        if assessment_options["assessments"] is not None
        else None
    )
    result["quality_statistics"] = quality_statistics(
        assessment_options["assessments"],
        start.year,
        complete=through == issue.end,
    )
    result["revision_ranking"] = (
        revision_counts(start, through).to_dicts()
        if revision_counts is not None
        else None
    )
    if historical is not None:
        result.update(
            collect_popularity(
                historical,
                issue,
                now,
                source_for=source_for,
                revision_history=revision_history,
                annual_pageviews=annual_pageviews,
            ),
        )
    result["dyk_projects_observed_at"] = (
        now.isoformat(timespec="seconds") if dyk_projects is not None else None
    )
    return result


def _issue_facts(  # ruff: ignore[complex-structure, too-many-arguments, too-many-statements]
    read: WikiReader,
    issue: Issue,
    now: dt.datetime,
    current_counts: Mapping[str, int] | None,
    *,
    members: set[str] | None,
    member_ids: set[int] | None,
    article_history: pl.DataFrame | None,
    quality_history: pl.DataFrame | None,
    today_history_path: Path | None,
    overview_at: (
        Callable[[dt.date], tuple[Mapping[str, int | None], str] | None] | None
    ),
    current_assessments: pl.DataFrame | None,
    dyk_projects: pl.DataFrame | None,
    assessments_at: (
        Callable[[dt.date], tuple[pl.DataFrame, str] | None] | None
    ),
    prior: Mapping[str, object] | None,
    revision_counts: Callable[[dt.date, dt.date], pl.DataFrame] | None,
    annual_pageviews: Callable[[int, dt.date], dict | None] | None,
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
        sources = read.sources
        if title in sources:
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

    def source_for(title: str) -> dict | None:
        """Read the original reader's metadata for its last exact read.

        Returns:
            A source copy, or none when metadata is unavailable.
        """
        source = read.sources.get(title)
        return source.copy() if source is not None else None

    def historical_revisions(
        title: str,
        start: dt.date,
        end: dt.date,
    ) -> list[dict[str, object]]:
        """Retain exact revisions from a bounded publication window.

        Returns:
            Historical rows with every source revision in provenance.
        """
        rows = read.history(title, start, end)
        for row in rows:
            revision = row["revision"]
            url = f"https://zh.wikipedia.org/w/index.php?oldid={revision}"
            provenance[url] = {"label": title, "url": url}
        return rows

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
        "overview": dict.fromkeys(STAT_KEYS),
        "overview_observed_at": None,
        "previous_overview": dict.fromkeys(STAT_KEYS),
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
    if observation_end > issue.start:
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
            historical,
            issue,
            through=observation_end,
            observed_at=now,
            assessments=current_assessments,
            assessment_observed_at=now.isoformat(timespec="seconds"),
            resolve=read.resolve,
            resolve_many=read.resolve_many,
            resolve_page_id=read.resolve_page_id,
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
    elif current_counts and issue.start <= now.date() < issue.end:
        if overview_at is not None:
            baseline, _ = overview_at(issue.start) or ({}, None)
            facts["previous_overview"].update(baseline)
        facts["overview"].update(current_counts)
        facts["overview_observed_at"] = now.isoformat(timespec="seconds")
        facts["available"] = True
    if members is not None and now.date() >= issue.start:
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
    if issue.month == 1:
        facts["year_review"] = _year_review(
            historical,
            issue,
            now,
            max(dt.date(issue.year - 1, 1, 1), min(issue.end, now.date())),
            article_history=article_history,
            quality_history=quality_history,
            current_counts=current_counts,
            current_assessments=current_assessments,
            dyk_projects=dyk_projects,
            assessments_at=assessments_at,
            overview_at=overview_at,
            provenance=provenance,
            revision_counts=revision_counts,
            annual_pageviews=annual_pageviews,
            source_for=source_for,
            revision_history=historical_revisions,
        )
    facts["sources"] = list(provenance.values())
    return facts


def collect_source_facts(  # ruff: ignore[too-many-arguments]
    read: WikiReader,
    *,
    now: dt.datetime,
    current_counts: Mapping[str, int] | None = None,
    members: set[str] | None = None,
    member_ids: set[int] | None = None,
    article_history: pl.DataFrame | None = None,
    quality_history: pl.DataFrame | None = None,
    today_history_path: Path | None = None,
    overview_at: (
        Callable[[dt.date], tuple[Mapping[str, int | None], str] | None] | None
    ) = None,
    current_assessments: pl.DataFrame | None = None,
    dyk_projects: pl.DataFrame | None = None,
    assessments_at: (
        Callable[[dt.date], tuple[pl.DataFrame, str] | None] | None
    ) = None,
    prior_facts: Mapping[str, object] | None = None,
    revision_counts: Callable[[dt.date, dt.date], pl.DataFrame] | None = None,
    annual_pageviews: Callable[[int, dt.date], dict | None] | None = None,
) -> dict:
    """Prepare quarterly drafts and the current year's annual feature.

    Args:
        read: Current-page reader with historical ``at`` support.
        now: Aware collection time, separate from source dates.
        current_counts: Fresh counts used only for open quarters.
        members: Current article titles from the project database query.
            When supplied, collect quarter-specific homepage history.
        member_ids: Project article IDs for anniversary scope joins.
        article_history: Events recorded on project talk pages.
            Complete snapshots supply DYK and quality events directly.
        quality_history: Full Parquet-backed milestone inventory for
            annual recognition counts, including former project members.
            Defaults to supplied article_history for existing callers.
        today_history_path: Separate wiki-wide daily-source archive.
        overview_at: Read retained database counts for an exclusive UTC
            date boundary with their actual observation timestamp.
            Missing snapshots leave their counts unknown.
        current_assessments: Fresh internal grades for registered new
            articles in every active draft, including closed quarters.
            Historical annual statistics retain their own boundaries.
        dyk_projects: Complete current article memberships with
            ``full_title`` and ``project`` columns for annual groups.
            Their observation time remains separate from the historical
            source cutoff, including for completed years.
        assessments_at: Read retained assessment rows for an exact UTC
            date with their actual observation time. Missing historical
            snapshots leave historical annual statistics unavailable.
        revision_counts: Replica ranking for an explicit annual
            UTC interval. Missing readers leave revisions unavailable.
        annual_pageviews: Read prepared calendar-year Pageviews through
            an exclusive date boundary. Missing observations remain
            unknown.
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
        key not in STAT_KEYS
        or type(value) is not int
        or not 0 <= value <= _MAX_COUNT
        for key, value in current_counts.items()
    ):
        message = "Current newsletter counts must be bounded observations."
        raise ValueError(message)
    now = now.astimezone(dt.UTC)
    lifecycle = read_publication_plan(read, now.date())
    prior = (prior_facts or {}).get("issues", {})
    result = {
        "lifecycle": lifecycle,
        "issues": {
            key: facts
            for key, facts in prior.items()
            if key not in lifecycle["active"]
            and reporting_start(Issue.parse(key)) <= now.date()
        },
    }
    result["issues"].update({
        key: _issue_facts(
            read,
            Issue.parse(key),
            now,
            current_counts,
            members=members,
            member_ids=member_ids,
            article_history=article_history,
            quality_history=quality_history,
            today_history_path=today_history_path,
            overview_at=overview_at,
            current_assessments=current_assessments,
            dyk_projects=dyk_projects,
            assessments_at=assessments_at,
            prior=prior.get(key),
            revision_counts=revision_counts,
            annual_pageviews=annual_pageviews,
        )
        for key in lifecycle["active"]
    })
    if not valid_newsletter(result):
        message = "Newsletter facts failed bounded snapshot validation."
        raise ValueError(message)
    return result
