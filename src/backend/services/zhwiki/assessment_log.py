"""Compare complete project assessments with the published log baseline.

Use ``prepare_data`` with the destination's existing managed log.
Durable bundles retain both proposed and published membership so failed
saves cannot consume removals. The first observation establishes a
baseline without inventing historical changes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Heading

from backend.services.project_assessments import get_snapshot
from shared.reports import ReportResult, ReportStore, report_key
from shared.support.dates import parse_complete_date
from shared.support.regions import (
    integer_option,
    region_content,
    region_options,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pywikibot.site import BaseSite

    from shared.reports import ReportBundle

_MEMBERS = {
    "page_id": pl.Int64,
    "title": pl.String,
    "class": pl.String,
    "importance": pl.String,
}
_CHANGES = {
    "page_id": pl.Int64,
    "kind": pl.String,
    "old_title": pl.String,
    "title": pl.String,
    "old_class": pl.String,
    "class": pl.String,
    "old_importance": pl.String,
    "importance": pl.String,
}
_GROUPS = {"重命名", "變更評級", "新增評級", "移除評級"}
_DAY_LEVEL = 3
_GROUP_LEVEL = 4


@dataclass(frozen=True)
class AssessmentLogData:
    """Carry complete snapshots, changes, and retained dated sections.

    Attributes:
        members: Complete currently observed project assessment set.
        baseline: Members corresponding to the existing published log.
        changes: Identity changes with observed numeric namespaces.
        prefix: Retained text before the first dated section.
        days: Retained day/group bodies, without their headings.
        pruned: Whether expired sections were removed.
        calculated_at: Actual UTC observation time, not an edit time.
        result: Complete data to retain before publication.
        references: Observed talk titles and subject/talk revision IDs.
        pending: Retained unsaved history still needs publication.
        unchanged: A newer daily observation confirms no changes.
        summaries: Retained per-day assessment totals paragraphs.
    """

    members: pl.DataFrame
    baseline: pl.DataFrame
    changes: pl.DataFrame
    prefix: str
    days: dict[dt.date, dict[str, str]]
    pruned: bool
    calculated_at: dt.datetime
    result: ReportResult
    references: Mapping[int, tuple[str | None, int | None, int | None]] = (
        field(default_factory=dict)
    )
    pending: bool = False
    unchanged: bool = False
    summaries: Mapping[dt.date, str] = field(default_factory=dict)

    @property
    def report_date(self) -> dt.date:
        """The completed UTC day represented by the snapshot."""
        return self.calculated_at.astimezone(dt.UTC).date() - dt.timedelta(
            days=1,
        )


@dataclass(frozen=True)
class _LogBaseline:
    """Separate published membership from the last retained observation.

    Attributes:
        members: Membership corresponding to the live managed body.
        source: Full source rows corresponding to published membership.
        observed_members: Most recent durably retained membership.
        observed_source: Full source rows of that observation.
        body: Published or pending history of the last observation.
        observed_at: Actual UTC time of the last retained observation.
    """

    members: pl.DataFrame
    source: pl.DataFrame
    observed_members: pl.DataFrame
    observed_source: pl.DataFrame
    body: str
    observed_at: dt.datetime


def _validated_members(frame: pl.DataFrame) -> pl.DataFrame:
    """Require unambiguous stable identities and nullable assessments.

    Args:
        frame: Proposed or persisted membership snapshot.

    Returns:
        Validated snapshot sorted by stable page ID.

    Raises:
        ValueError: Schema, identities, or titles are invalid.
    """
    if (
        frame.schema != pl.Schema(_MEMBERS)
        or not frame.filter(
            pl.col("page_id").is_null()
            | (pl.col("page_id") <= 0)
            | pl.col("page_id").is_duplicated()
            | pl.col("title").is_null()
            | (pl.col("title").str.strip_chars().str.len_chars() == 0)
            | pl.col("title").is_duplicated(),
        ).is_empty()
    ):
        message = "Invalid assessment-log membership snapshot."
        raise ValueError(message)
    return frame.sort("page_id")


def compare_members(
    previous: pl.DataFrame,
    current: pl.DataFrame,
) -> pl.DataFrame:
    """Compare complete membership sets using stable subject page IDs.

    A rename and reassessment can both be recorded for the same page.
    Recreating a title with a new ID produces removal and addition.

    Args:
        previous: Previous complete membership and assessments.
        current: Fresh complete observations from the same project.

    Returns:
        Typed changes preserving unknown old and new assessments.
    """
    old = _validated_members(previous).rename(
        {name: f"old_{name}" for name in _MEMBERS if name != "page_id"},
    )
    joined = old.join(
        _validated_members(current),
        on="page_id",
        how="full",
        coalesce=True,
        validate="1:1",
    )
    matched = pl.col("old_title").is_not_null() & pl.col("title").is_not_null()
    conditions = {
        "removed": pl.col("title").is_null(),
        "added": pl.col("old_title").is_null(),
        "renamed": matched & (pl.col("old_title") != pl.col("title")),
        "reassessed": matched
        & (
            pl.col("old_class").ne_missing(pl.col("class"))
            | pl.col("old_importance").ne_missing(pl.col("importance"))
        ),
    }
    return pl.concat(
        joined
        .filter(condition)
        .with_columns(pl.lit(kind).alias("kind"))
        .select(list(_CHANGES))
        for kind, condition in conditions.items()
    ).sort("kind", "title", "old_title", "page_id", nulls_last=True)


def _daily_summaries(
    introductions: Mapping[dt.date, str],
    days: Mapping[dt.date, Mapping[str, str]],
) -> dict[dt.date, str]:
    """Read totals while validating each day's introductory text.

    Args:
        introductions: Text before each day's first change group.
        days: Parsed change groups for every observation date.

    Returns:
        Exact totals paragraphs, without no-change markers.

    Raises:
        ValueError: Daily text has unsupported or contradictory content.
    """
    summaries = {}
    for day, introduction in introductions.items():
        paragraphs = introduction.strip().split("\n\n", maxsplit=1)
        if re.fullmatch(r"'''总条目'''[0-9,]+篇。[^\r\n]*", paragraphs[0]):
            summaries[day] = paragraphs.pop(0)
        if "\n\n".join(paragraphs).strip() != ("" if days[day] else "無變更"):
            message = (
                "Assessment log needs change groups or standalone 無變更."
            )
            raise ValueError(message)
    return summaries


def _history(
    body: str,
    cutoff: dt.date,
) -> tuple[str, dict[dt.date, dict[str, str]], dict[dt.date, str], bool]:
    """Read existing daily sections without interpreting their entries.

    Args:
        body: Existing managed-region contents.
        cutoff: Earliest retained UTC date.

    Returns:
        Preserved introduction, group bodies, daily totals, and pruning.

    Raises:
        ValueError: Headings or groups are ambiguous or unsupported.
    """
    prefix = ""
    days: dict[dt.date, dict[str, str]] = {}
    introductions: dict[dt.date, str] = {}
    day = None
    group = None
    for node in mwparserfromhell.parse(body).nodes:
        if isinstance(node, Heading):
            label = str(node.title).strip()
            if node.level == _DAY_LEVEL:
                day = parse_complete_date(label)
                if day is None or day in days:
                    message = "Assessment log needs unique dated headings."
                    raise ValueError(message)
                days[day] = {}
                introductions[day] = ""
                group = None
            elif node.level == _GROUP_LEVEL and day is not None:
                if label not in _GROUPS or label in days[day]:
                    message = "Assessment log has an unsupported group."
                    raise ValueError(message)
                group = label
                days[day][group] = ""
            else:
                message = "Assessment log has an unsupported heading."
                raise ValueError(message)
        elif day is None:
            prefix += str(node)
        elif group is not None:
            days[day][group] += str(node)
        else:
            introductions[day] += str(node)
    summaries = _daily_summaries(introductions, days)
    retained = {day: groups for day, groups in days.items() if day >= cutoff}
    return (
        prefix,
        retained,
        {day: summary for day, summary in summaries.items() if day >= cutoff},
        len(retained) != len(days),
    )


def _digest(body: str) -> str:
    """Identify the exact managed text associated with a snapshot.

    Args:
        body: Published managed-region contents.

    Returns:
        SHA-256 digest independent of surrounding page text.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _baseline(
    key: str,
    project: str,
    body: str,
) -> _LogBaseline | None:
    """Recover published membership across repeated unsuccessful saves.

    Compare live managed text with the latest complete proposal first.
    Matching text establishes publication even if a previous process
    lost the save response. Otherwise carry its original baseline
    forward; an unsuccessful proposal never becomes the baseline.
    Retain its observed members and dated history separately so retries
    cannot erase an observed transition when later changes reverse it.

    Args:
        key: Destination-specific durable report key.
        project: Exact project whose snapshot is expected.
        body: Current managed log contents.

    Returns:
        Published baseline and retained observation, or none initially.

    Raises:
        ValueError: Retained data mismatches the destination or scope.
    """
    store = ReportStore()
    bundle = store.read(key)
    if bundle is None:
        return None
    metadata = bundle.manifest["metadata"]
    if (
        bundle.manifest["family"] != "assessment-log"
        or metadata.get("project") != project
    ):
        message = "Assessment-log snapshot scope does not match."
        raise ValueError(message)
    proposal_body = region_content(bundle.wikitext, "assessment-log")
    if proposal_body is None:
        message = "Assessment-log proposal is missing its managed body."
        raise ValueError(message)
    if proposal_body == body:
        name = "members"
    elif metadata.get("baseline_sha256") == _digest(body):
        name = "baseline"
    else:
        previous = store.read(key, previous=True)
        if (
            previous is not None
            and previous.manifest["family"] == "assessment-log"
            and previous.manifest["metadata"].get("project") == project
            and region_content(previous.wikitext, "assessment-log") == body
            and "members" in previous.tables
        ):
            bundle = previous
            name = "members"
        else:
            message = (
                "Published assessment log differs from its saved baseline."
            )
            raise ValueError(message)
    members, source = _baseline_tables(bundle, name)
    observed_members, observed_source = (
        _baseline_tables(bundle, "members")
        if name == "baseline"
        else (members, source)
    )
    return _LogBaseline(
        members,
        source,
        observed_members,
        observed_source,
        proposal_body if name == "baseline" else body,
        dt.datetime.fromisoformat(bundle.manifest["calculated_at"]),
    )


def _baseline_tables(
    bundle: ReportBundle,
    name: str,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Select corresponding identities and original source references.

    Args:
        bundle: Validated durable report matching the live log.
        name: Membership table selected by exact body comparison.

    Returns:
        Matched membership and its complete original source table.

    Raises:
        ValueError: Either required table is missing.
    """
    source_name = "source" if name == "members" else "baseline-source"
    if name not in bundle.tables or source_name not in bundle.tables:
        message = "Assessment-log snapshot is missing its membership table."
        raise ValueError(message)
    return _validated_members(bundle.tables[name]), bundle.tables[source_name]


def _references(
    source: pl.DataFrame,
    baseline_source: pl.DataFrame,
    changes: pl.DataFrame,
) -> dict[int, tuple[str | None, int | None, int | None]]:
    """Project available snapshot revisions for changed-page links.

    Args:
        source: Current complete snapshot rows.
        baseline_source: Previous rows with removed-page references.
        changes: Changes whose page IDs need output links.

    Returns:
        Talk titles and subject/talk revisions, preserving unknowns.
    """
    columns = {
        "talk_title": pl.String,
        "page_revision": pl.Int64,
        "talk_revision": pl.Int64,
    }
    references = (
        pl
        .concat(
            frame.select(
                "page_id",
                *(
                    pl.col(name)
                    if name in frame.columns
                    else pl.lit(None, dtype=dtype).alias(name)
                    for name, dtype in columns.items()
                ),
            )
            for frame in (source, baseline_source)
        )
        .unique("page_id", keep="first")
        .join(
            changes.select("page_id").unique(),
            on="page_id",
        )
    )
    return {
        identifier: (talk, page_revision, talk_revision)
        for (
            identifier,
            talk,
            page_revision,
            talk_revision,
        ) in references.iter_rows()
    }


def prepare_data(  # ruff: ignore[too-many-arguments, too-many-locals]
    site: BaseSite,
    project: str,
    report_title: str,
    original_text: str,
    *,
    observed_at: dt.datetime | None = None,
    dry: bool = False,
) -> AssessmentLogData:
    """Prepare assessment changes and retained daily log bodies.

    Existing paired markers are mandatory. First-time wiki setup and
    historical backfill are separate operations. A missing durable
    snapshot seeds current membership without calling it newly assessed.

    Args:
        site: Wiki supplying complete project membership.
        project: Exact PageAssessments project name.
        report_title: Caller-selected destination identity.
        original_text: Destination text with assessment-log markers.
        observed_at: Actual aware observation time, or current UTC time.
        dry: Isolate newly collected daily snapshots from production.

    Returns:
        Complete snapshots, differences, and parsed retained sections.

    Raises:
        ValueError: Invalid settings, destination, snapshot, or source.
    """
    body = region_content(original_text, "assessment-log")
    if body is None:
        message = "Missing managed-region markers for 'assessment-log'."
        raise ValueError(message)
    options = region_options(original_text, "assessment-log")
    days = integer_option(options, "days", default=30, minimum=1, maximum=365)
    instant = observed_at or dt.datetime.now(dt.UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        message = "Assessment-log observation time must include a timezone."
        raise ValueError(message)
    instant = instant.astimezone(dt.UTC)
    cutoff = instant.date() - dt.timedelta(days=days)
    prefix, history, summaries, pruned = _history(body, cutoff)
    key = report_key("assessment-log", site.dbName(), report_title)
    previous = _baseline(key, project, body)
    pending = previous is not None and previous.body != body
    if pending:
        prefix, history, summaries, pending_pruned = _history(
            previous.body,
            cutoff,
        )
        pruned = pruned or pending_pruned
    snapshot = get_snapshot(site, project, observed_at=instant, dry=dry)
    source = snapshot.tables["members"]
    instant = dt.datetime.fromisoformat(snapshot.manifest["calculated_at"])
    members = _validated_members(
        source.select(
            "page_id",
            pl.col("full_title").alias("title"),
            pl
            .col("pa_class")
            .str.strip_chars()
            .replace("", None)
            .alias(
                "class",
            ),
            pl
            .col("pa_importance")
            .str.strip_chars()
            .replace("", None)
            .alias(
                "importance",
            ),
        ),
    )
    if members.is_empty():
        message = "An empty project snapshot cannot establish removals."
        raise ValueError(message)
    state = previous or _LogBaseline(
        members.clone(),
        source,
        members,
        source,
        body,
        instant,
    )
    namespaces = pl.concat(
        frame.select("page_id", "page_namespace")
        for frame in (source, state.observed_source)
    ).unique("page_id", keep="first")
    changes = compare_members(state.observed_members, members).join(
        namespaces,
        on="page_id",
        how="left",
        validate="m:1",
    )
    result = ReportResult(
        family="assessment-log",
        tables={
            "members": members,
            "baseline": state.members,
            "changes": changes,
            "source": source,
            "baseline-source": state.source,
        },
        metadata={
            "project": project,
            "days": days,
            "report_date": (instant.date() - dt.timedelta(days=1)).isoformat(),
            "baseline_sha256": _digest(body),
        },
        provenance={
            "wiki": site.dbName(),
            "sources": ["project-assessments"],
            "snapshot_key": snapshot.manifest["report_key"],
            "snapshot_generation": snapshot.manifest["generation"],
        },
        calculated_at=instant,
    )
    return AssessmentLogData(
        members,
        state.members,
        changes,
        prefix,
        history,
        pruned,
        instant,
        result,
        references=_references(source, state.observed_source, changes),
        pending=pending,
        unchanged=(
            previous is not None
            and state.observed_at.date() < instant.date()
            and changes.is_empty()
        ),
        summaries=summaries,
    )
