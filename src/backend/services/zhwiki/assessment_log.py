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
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Heading

from backend.services.project_assessments import (
    get_snapshot,
    read_previous_snapshot,
)
from shared.reports import ReportResult, ReportStore, report_key
from shared.sources.quarry.projects import (
    page_identity_metadata,
    page_metadata,
)
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
_ASSESSMENTS = {
    key: dtype for key, dtype in _MEMBERS.items() if key != "title"
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
class AssessmentSummaryChange:
    """Retain one generated summary category and its observed changes.

    Attributes:
        label: Project, article, nonarticle, or redirect category.
        change: Original signed change, or unknown comparison.
        dimensions: Ordered quality/importance label and change pairs.
    """

    label: str
    change: int | None
    dimensions: tuple[tuple[tuple[str, int], ...], ...] = ()


@dataclass(frozen=True)
class AssessmentSummaryMigration:
    """Preserve generated summary facts for presentation-only migration.

    Attributes:
        changes: Original categories with their measured signed changes.
        totals: Escaped full totals from the original inert comment.
    """

    changes: tuple[AssessmentSummaryChange, ...]
    totals: str


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
        previous_source: Prior source rows with known namespaces.
        previous_totals: Prior article-only aggregate facts.
        removed_links: Opaque removal lines with verified titles.
        summary_migrations: Original facts from old generated summaries.
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
    previous_source: pl.DataFrame | None = None
    previous_totals: Mapping[str, object] | None = None
    removed_links: Mapping[str, tuple[str, str | None]] = field(
        default_factory=dict,
    )
    summary_migrations: Mapping[dt.date, AssessmentSummaryMigration] = field(
        default_factory=dict,
    )

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
        observed_totals: Article-only aggregates of that observation.
    """

    members: pl.DataFrame
    source: pl.DataFrame
    observed_members: pl.DataFrame
    observed_source: pl.DataFrame
    body: str
    observed_at: dt.datetime
    observed_totals: Mapping[str, object] | None = None


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
        and frame.schema != pl.Schema(_ASSESSMENTS)
    ) or not frame.filter(
        pl.col("page_id").is_null()
        | (pl.col("page_id") <= 0)
        | pl.col("page_id").is_duplicated()
        | (
            pl.col("title").is_null()
            | (pl.col("title").str.strip_chars().str.len_chars() == 0)
            | pl.col("title").is_duplicated()
            if "title" in frame.columns
            else pl.lit(value=False)
        ),
    ).is_empty():
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


def compare_assessments(
    previous: pl.DataFrame,
    current: pl.DataFrame,
) -> pl.DataFrame:
    """Compare assessment observations before resolving page names.

    Args:
        previous: Complete former page IDs, classes, and importances.
        current: Complete current observations with the same columns.

    Returns:
        Added, removed, and reassessed IDs with both observed grades.
        Names and revision references are resolved only for these IDs.
    """
    old = (
        _validated_members(previous)
        .select(list(_ASSESSMENTS))
        .rename({
            "class": "old_class",
            "importance": "old_importance",
        })
        .with_columns(pl.lit(value=True).alias("_old"))
    )
    joined = old.join(
        _validated_members(current)
        .select(list(_ASSESSMENTS))
        .with_columns(
            pl.lit(value=True).alias("_new"),
        ),
        on="page_id",
        how="full",
        coalesce=True,
        validate="1:1",
    )
    matched = pl.col("_old").is_not_null() & pl.col("_new").is_not_null()
    conditions = {
        "removed": pl.col("_new").is_null(),
        "added": pl.col("_old").is_null(),
        "reassessed": matched
        & (
            pl.col("old_class").ne_missing(pl.col("class"))
            | pl.col("old_importance").ne_missing(pl.col("importance"))
        ),
    }
    return pl.concat(
        joined
        .filter(condition)
        .with_columns(
            pl.lit(kind).alias("kind"),
            pl.lit(None, dtype=pl.String).alias("old_title"),
            pl.lit(None, dtype=pl.String).alias("title"),
        )
        .select(list(_CHANGES))
        for kind, condition in conditions.items()
    ).sort("kind", "page_id")


def _assessment_members(source: pl.DataFrame) -> pl.DataFrame:
    """Project compact source observations without materializing titles.

    Args:
        source: Persisted page IDs and PageAssessments dimensions.

    Returns:
        Validated report assessment rows with unknown grades retained.
    """
    return _validated_members(
        source.select(
            "page_id",
            pl
            .col("pa_class")
            .str.strip_chars()
            .replace("", None)
            .alias("class"),
            pl
            .col("pa_importance")
            .str.strip_chars()
            .replace("", None)
            .alias("importance"),
        ),
    )


def _resolve_changes(
    site: BaseSite,
    changes: pl.DataFrame,
    source: pl.DataFrame,
    previous_source: pl.DataFrame,
    previous_members: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Resolve current metadata for changed IDs after comparison.

    Args:
        site: Wiki supplying current identity metadata.
        changes: Already compared assessment rows, without titles.
        source: Current saved page identities when available.
        previous_source: Previous identities, including removed pages.
        previous_members: Legacy identities with known full titles.

    Returns:
        Current names or saved/deletion titles for missing pages.
        Truly unknown identities retain stable Special:Redirect links.
    """
    if changes.is_empty():
        return changes.with_columns(
            pl.lit(None, dtype=pl.Int64).alias("page_namespace"),
        ), pl.DataFrame(
            schema={
                "page_id": pl.Int64,
                "talk_title": pl.String,
                "page_revision": pl.Int64,
                "talk_revision": pl.Int64,
            },
        )
    metadata = page_metadata(
        site,
        changes.select("page_id").unique()["page_id"].to_list(),
    )
    identities = pl.concat(
        _saved_identities(
            site,
            frame.join(
                changes.filter(condition).select("page_id").unique(),
                on="page_id",
            ),
        )
        for frame, condition in (
            (previous_source, pl.col("kind") == "removed"),
            (previous_members, pl.col("kind") == "removed"),
            (source, pl.col("kind") != "removed"),
        )
    ).unique("page_id", keep="first")
    columns = [
        "page_id",
        "full_title",
        "page_namespace",
        "talk_title",
        "page_revision",
        "talk_revision",
    ]
    metadata = pl.concat((
        metadata.select(
            pl.col(name)
            if name in metadata.columns
            else pl.lit(None, dtype=identities.schema[name]).alias(name)
            for name in columns
        ),
        identities,
    )).unique("page_id", keep="first")
    missing = (
        changes
        .select("page_id")
        .unique()
        .join(
            metadata.select("page_id"),
            on="page_id",
            how="anti",
        )
    )
    if not missing.is_empty():
        deleted = page_metadata(
            site,
            missing["page_id"].to_list(),
            include_deleted=True,
        )
        metadata = pl.concat((metadata, deleted.select(columns)))
    resolved = (
        changes
        .join(
            metadata.select("page_id", "full_title", "page_namespace"),
            on="page_id",
            how="left",
            validate="m:1",
        )
        .with_columns(
            pl.coalesce(
                "full_title",
                pl.concat_str(
                    pl.lit("Special:Redirect/page/"),
                    pl.col("page_id"),
                ),
            ).alias("_title"),
        )
        .with_columns(
            pl
            .when(pl.col("kind") != "removed")
            .then(pl.col("_title"))
            .otherwise(None)
            .alias("title"),
            pl
            .when(pl.col("kind") != "added")
            .then(pl.col("_title"))
            .otherwise(None)
            .alias("old_title"),
        )
        .select(*_CHANGES, "page_namespace")
    )
    return resolved, metadata


def _saved_identities(site: BaseSite, source: pl.DataFrame) -> pl.DataFrame:
    """Project only known saved identities into link metadata.

    Args:
        site: Wiki namespace definitions used to name saved identities.
        source: Current or historical assessment rows.

    Returns:
        Known original subject and discussion targets; legacy compact
        rows without identity columns produce no invented names.
    """
    schema = {
        "page_id": pl.Int64,
        "full_title": pl.String,
        "page_namespace": pl.Int64,
        "talk_title": pl.String,
        "page_revision": pl.Int64,
        "talk_revision": pl.Int64,
    }
    required = {"page_id", "page_title", "page_namespace"}
    if not required.issubset(source.columns):
        title = next(
            (
                name
                for name in ("full_title", "title")
                if name in source.columns
            ),
            None,
        )
        if title is None:
            return pl.DataFrame(schema=schema)
        source = source.select(
            "page_id",
            pl.col(title).alias("full_title"),
        ).filter(
            pl.col("full_title").is_not_null()
            & ~pl.col("full_title").str.contains(
                r"^Special:Redirect/page/[1-9][0-9]*$",
            ),
        )
    else:
        source = source.drop_nulls(["page_title", "page_namespace"])
    return page_identity_metadata(
        site,
        source,
    ).select(*schema)


def _removed_link_repairs(
    site: BaseSite,
    history: Mapping[dt.date, Mapping[str, str]],
    *sources: pl.DataFrame,
) -> dict[str, tuple[str, str | None]]:
    """Resolve retained generated removal lines with unavailable titles.

    Args:
        site: Wiki supplying exact public page identity records.
        history: Parsed retained change groups.
        sources: Saved observations with potential original titles.

    Returns:
        Exact generated lines mapped to verified subject/discussion
        targets. Unknown IDs and unrelated user text remain untouched.
    """
    lines = {}
    pattern = re.compile(
        r"\* '''\[\[:Special:Redirect/page/([1-9][0-9]*)\]\]'''"
        r"已移除評級。",
    )
    for sections in history.values():
        for line in sections.get("移除評級", "").splitlines():
            matched = pattern.fullmatch(line)
            if matched:
                lines[line] = int(matched[1])
    if not lines:
        return {}
    metadata = page_metadata(site, lines.values())
    identifiers = pl.DataFrame(
        {"page_id": list(set(lines.values()))},
        schema={"page_id": pl.Int64},
    )
    columns = ["page_id", "full_title", "talk_title"]
    metadata = pl.concat((
        metadata.select(columns),
        *(
            _saved_identities(
                site,
                frame.join(identifiers, on="page_id"),
            ).select(columns)
            for frame in sources
        ),
    )).unique("page_id", keep="first")
    missing = identifiers.join(metadata, on="page_id", how="anti")
    if not missing.is_empty():
        metadata = pl.concat((
            metadata,
            page_metadata(
                site,
                missing["page_id"].to_list(),
                include_deleted=True,
            ).select(columns),
        ))
    titles = {
        identifier: (title, talk)
        for identifier, title, talk in metadata.select(
            "page_id",
            "full_title",
            "talk_title",
        ).iter_rows()
    }
    return {
        line: titles[identifier]
        for line, identifier in lines.items()
        if identifier in titles
    }


def _summary_group(text: str) -> AssessmentSummaryChange | None:
    """Parse one category from the narrowly generated legacy summary.

    Args:
        text: Exact category fragment from a retained one-line summary.

    Returns:
        Original changes without inventing unknown comparisons, or null
        when the fragment does not follow the generated format.
    """
    number = r"[0-9]+(?:,[0-9]{3})*"
    matched = re.fullmatch(
        rf"(专题页面|条目|非条目|重定向)(?:([+-]{number})|"
        rf"(?:{number}|未知)\uFF08无可比记录\uFF09)"
        r"(?:\uFF08([^\uFF08\uFF09]*)\uFF09)?",
        text,
    )
    if matched is None:
        return None
    dimensions = []
    if matched[3]:
        for dimension in matched[3].split(" | "):
            values = []
            for item in dimension.split(" · "):
                pair = re.fullmatch(rf"(.+?)([+-]{number})", item)
                if pair is None:
                    return None
                values.append((pair[1], int(pair[2].replace(",", ""))))
            dimensions.append(tuple(values))
    return AssessmentSummaryChange(
        matched[1],
        int(matched[2].replace(",", "")) if matched[2] else None,
        tuple(dimensions),
    )


def _summary_migrations(
    summaries: Mapping[dt.date, str],
) -> dict[dt.date, AssessmentSummaryMigration]:
    """Recover original signed facts from generated legacy summaries.

    Args:
        summaries: Validated retained daily summary paragraphs.

    Returns:
        Only unambiguous generated one-line summaries. Full totals stay
        escaped and unknown comparisons remain unknown during migration.
    """
    migrations = {}
    for day, summary in summaries.items():
        visible, separator, comment = summary.partition("<!-- ")
        if (
            not separator
            or not comment.endswith(" -->")
            or "--" in comment[:-4]
            or comment.startswith("aranami-assessment-totals:")
            or not visible.startswith("专题页面")
        ):
            continue
        project, colon, categories = visible.partition("\uff1a")
        if not colon:
            continue
        pieces = [
            project,
            *re.split(r" · (?![^\uFF08\uFF09]*\uFF09)", categories),
        ]
        groups = tuple(_summary_group(piece) for piece in pieces)
        if any(group is None for group in groups):
            continue
        labels = tuple(group.label for group in groups)
        if labels[:2] != ("专题页面", "条目") or labels != tuple(
            label
            for label in ("专题页面", "条目", "非条目", "重定向")
            if label in labels
        ):
            continue
        migrations[day] = AssessmentSummaryMigration(groups, comment[:-4])
    return migrations


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
    delta = r'<small style="color: gray;">\uFF08[+-][0-9,]+\uFF09</small>'
    for day, introduction in introductions.items():
        paragraphs = introduction.strip().split("\n\n")
        if (
            re.fullmatch(
                r"(?:(?:专题页面|条目|非条目|重定向)[^\r\n<]*)?"
                r"<!-- aranami-assessment-totals:v2 [^<>\r\n]* -->",
                paragraphs[0],
            )
            or re.fullmatch(
                r"专题页面(?:[+-][0-9,]+|(?:[0-9,]+|未知)\uFF08无可比记录\uFF09)"
                r"\uFF1A[^\r\n<]*<!-- [^\r\n]* -->",
                paragraphs[0],
            )
            or re.fullmatch(r"'''总条目'''[0-9,]+篇。[^\r\n]*", paragraphs[0])
        ):
            summaries[day] = paragraphs.pop(0)
        elif re.fullmatch(
            rf"'''條目頁[0-9,]+篇'''(?:{delta})?\uFF1A\n\* [^\r]+",
            paragraphs[0],
        ):
            summary = [paragraphs.pop(0)]
            summary.extend(
                paragraphs.pop(0)
                for pattern in (
                    rf"'''非條目[0-9,]+頁'''(?:{delta})?\uFF1A[^\r\n]+",
                    (
                        rf"'''專題共計[0-9,]+頁'''(?:{delta})?。"
                        rf"(?:\uFF08不含[0-9,]+重定向頁(?:{delta})?\uFF09)?"
                    ),
                )
                if paragraphs and re.fullmatch(pattern, paragraphs[0])
            )
            summaries[day] = "\n\n".join(summary)
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


def latest_report_date(
    text: str,
    *,
    through: dt.date,
) -> dt.date | None:
    """Read the latest completed dated report in a managed log.

    Args:
        text: Existing or proposed complete destination wikitext.
        through: Latest completed UTC day supported by the source data.

    Returns:
        The latest valid retained report date through the source day,
        or none for an initial baseline without any dated report.

    Raises:
        ValueError: Managed markers or dated report bodies are invalid.
    """
    body = region_content(text, "assessment-log")
    if body is None:
        message = "Missing managed-region markers for 'assessment-log'."
        raise ValueError(message)
    _, days, _, _ = _history(body, dt.date.min)
    return max((day for day in days if day <= through), default=None)


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
        bundle.manifest["metadata"].get("article_totals"),
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


def _compact_baseline(
    source: pl.DataFrame,
    instant: dt.datetime,
    *,
    previous: _LogBaseline | None,
    body: str,
    retained: ReportBundle | None,
) -> _LogBaseline:
    """Prefer retained publication history, then the preceding dataset.

    Args:
        source: Complete current compact source observations.
        instant: Actual current observation time.
        previous: Published/pending report baseline when retained.
        body: Current live managed report text.
        retained: Most recent saved dataset before the current date.

    Returns:
        A baseline preserving pending transitions across failed saves.
    """
    if previous is not None:
        if retained is not None and previous.body == body:
            retained_at = dt.datetime.fromisoformat(
                retained.manifest["calculated_at"],
            )
            if retained_at > previous.observed_at:
                source = retained.tables["members"]
                return replace(
                    previous,
                    observed_members=_assessment_members(source),
                    observed_source=source,
                    observed_at=retained_at,
                    observed_totals=retained.manifest["metadata"].get(
                        "article_totals",
                    ),
                )
        return previous
    baseline_source = retained.tables["members"] if retained else source
    baseline_time = (
        dt.datetime.fromisoformat(retained.manifest["calculated_at"])
        if retained
        else instant
    )
    members = _assessment_members(baseline_source)
    return _LogBaseline(
        members,
        baseline_source,
        members,
        baseline_source,
        body,
        baseline_time,
        retained.manifest["metadata"].get("article_totals")
        if retained
        else None,
    )


def _prepare_compact(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    project: str,
    snapshot: ReportBundle,
    previous: _LogBaseline | None,
    body: str,
    *,
    days: int,
    prefix: str,
    history: dict[dt.date, dict[str, str]],
    summaries: Mapping[dt.date, str],
    pruned: bool,
    pending: bool,
    dry: bool,
) -> AssessmentLogData:
    """Compare saved assessment IDs before resolving current metadata.

    Args:
        site: Source wiki identity.
        project: Exact project whose datasets are compared.
        snapshot: Current saved compact daily dataset.
        previous: Saved publication baseline when available.
        body: Current managed report text.
        days: Retained log duration in days.
        prefix: Retained log introduction.
        history: Retained dated change groups.
        summaries: Retained daily summary paragraphs.
        pruned: Whether expired log sections were removed.
        pending: Whether an unsaved proposal remains pending.
        dry: Keep preview observations isolated.

    Returns:
        Complete compact baselines and enriched changed-page records.

    Raises:
        ValueError: An empty observation cannot establish removals.
    """
    source = snapshot.tables["members"]
    instant = dt.datetime.fromisoformat(snapshot.manifest["calculated_at"])
    members = _assessment_members(source)
    if members.is_empty():
        message = "An empty project snapshot cannot establish removals."
        raise ValueError(message)
    retained = read_previous_snapshot(
        site,
        project,
        instant.date() - dt.timedelta(days=1),
        dry=dry,
    )
    state = _compact_baseline(
        source,
        instant,
        previous=previous,
        body=body,
        retained=retained,
    )
    compared = compare_assessments(
        _assessment_members(state.observed_source),
        members,
    )
    changes, metadata = _resolve_changes(
        site,
        compared,
        source,
        state.observed_source,
        state.observed_members,
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
            "article_totals": snapshot.manifest["metadata"].get(
                "article_totals",
            ),
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
        references=_references(metadata, metadata.clear(), changes),
        pending=pending,
        unchanged=state.observed_at.date() < instant.date()
        and changes.is_empty(),
        summaries=summaries,
        previous_source=(
            state.observed_source
            if state.observed_at.date() < instant.date()
            else retained.tables["members"]
            if retained
            else None
        ),
        previous_totals=(
            state.observed_totals
            if state.observed_at.date() < instant.date()
            else retained.manifest["metadata"].get("article_totals")
            if retained
            else None
        ),
        removed_links=_removed_link_repairs(
            site,
            history,
            state.observed_source,
            state.observed_members,
            state.source,
            state.members,
        ),
        summary_migrations=_summary_migrations(summaries),
    )


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
    if "full_title" not in source.columns:
        return _prepare_compact(
            site,
            project,
            snapshot,
            previous,
            body,
            days=days,
            prefix=prefix,
            history=history,
            summaries=summaries,
            pruned=pruned,
            pending=pending,
            dry=dry,
        )
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
        previous_source=state.observed_source,
        previous_totals=state.observed_totals,
        summary_migrations=_summary_migrations(summaries),
    )
