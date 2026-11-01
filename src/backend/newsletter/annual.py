"""Prepare annual newsletter facts independently of quarter totals.

January features use the preceding year's recorded milestones and
new-page register. All source reads share the issue observation cutoff;
missing dates and author evidence remain unknown.
"""

from __future__ import annotations

import datetime as dt
import re
from functools import cache
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from pywikibot.exceptions import NoPageError

from backend.newsletter.newpages import collect_new_pages
from shared.newsletter import DYK_PROJECTS, Issue

if TYPE_CHECKING:
    from collections.abc import Callable

_HIGH_GRADES = {
    "fa": "FA",
    "典范": "FA",
    "典範": "FA",
    "fl": "FL",
    "典表": "FL",
    "特色列表": "FL",
    "a": "A",
    "甲": "A",
    "al": "AL",
    "甲表": "AL",
    "甲级列表": "AL",
    "甲級列表": "AL",
    "ga": "GA",
    "优良": "GA",
    "優良": "GA",
    "bplus": "Bplus",
    "b+": "Bplus",
    "乙上": "Bplus",
}
_DYK_SCHEMA = {
    "title": pl.String,
    "date": pl.String,
    "is_game": pl.Boolean,
    "release_year": pl.Int32,
}
QUALITY_HISTORY_GRADES = ("FA", "FL", "A", "AL", "GA", "Bplus")
_GRADE_PRIORITY = {"FA": 4, "FL": 4, "A": 3, "AL": 3, "GA": 2, "Bplus": 1}


def _observed_quality(
    events: pl.DataFrame,
    assessments: pl.DataFrame | None,
    *,
    year: int | None = None,
) -> pl.DataFrame:
    """Keep promotions compatible with an observed boundary grade.

    Args:
        events: Active quality additions, optionally grouped by year.
        assessments: Current or exact-boundary project assessments.
            Missing rows or null grades cannot establish status loss.
        year: Only constrain this annual cohort when supplied.

    Returns:
        Matching promotions without changing historical evidence.
    """
    if assessments is None:
        return events
    members = assessments
    if "page_namespace" in members.columns:
        members = members.filter(pl.col("page_namespace") == 0)
    identity = (
        "page_id"
        if "page_id" in events.columns and "page_id" in members.columns
        else "title"
    )
    grades = members.select(
        pl.col("page_id")
        if identity == "page_id"
        else pl.col("full_title").str.replace_all("_", " ").alias("title"),
        pl.col("pa_class").is_not_null().alias("_grade_observed"),
        pl
        .col("pa_class")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_lowercase()
        .str.replace(r"[级級]$", "")
        .replace_strict(_HIGH_GRADES, default=None)
        .alias("_observed_grade"),
    ).unique()
    compatible = ~pl.col("_grade_observed").fill_null(value=False) | (
        pl.col("grade") == pl.col("_observed_grade")
    ).fill_null(value=False)
    return (
        events
        .join(grades, on=identity, how="left", validate="m:1")
        .filter(
            (pl.col("year") != year) | compatible
            if year is not None
            else compatible,
        )
        .drop("_grade_observed", "_observed_grade")
    )


def _active_quality(
    events: pl.DataFrame,
    through: dt.date,
    *,
    groups: tuple[str, ...] = (),
) -> pl.DataFrame:
    """Select the latest surviving quality addition at a cutoff.

    Args:
        events: Recorded quality events, optionally expanded by cohort.
        through: Exclusive observation date.
        groups: Cohort columns whose statuses remain independent.

    Returns:
        One addition per article, with higher grades winning same-day
        ties. Removal clears that grade without reviving earlier awards.
    """
    identity = "page_id" if "page_id" in events.columns else "title"
    observed = events.filter(
        (pl.col("kind") == "quality")
        & pl.col("grade").is_in(QUALITY_HISTORY_GRADES)
        & (pl.col("date") < through),
    )
    latest = (
        observed
        .filter(pl.col("action") == "added")
        .with_columns(
            pl.col("grade").replace_strict(_GRADE_PRIORITY).alias("_priority"),
        )
        .sort("date", "_priority", "grade")
        .unique([*groups, identity], keep="last")
        .drop("_priority")
    )
    removed = (
        observed
        .filter(pl.col("action") == "removed")
        .group_by(*groups, identity, "grade")
        .agg(pl.col("date").max().alias("_removed"))
    )
    return (
        latest
        .join(removed, on=[*groups, identity, "grade"], how="left")
        .filter(
            pl.col("_removed").is_null()
            | (pl.col("_removed") < pl.col("date")),
        )
        .drop("_removed")
    )


def _game_release(text: str) -> tuple[bool | None, int | None]:
    """Read game-year categories before falling back to infobox dates.

    Direct categories matching YYYY年[电電]子[游遊][戏戲] establish
    game status and the earliest year in the supported 1900-2099 range.
    Linked categories, comments, and nested examples establish no years.
    The Infobox VG documentation (revision 94973340) defines released
    and released header. Vgr (90334001) alternates region and value;
    Video game release redirects to it (42868215). References and
    unsupported date templates cannot establish a release year.

    Returns:
        Known-game status and earliest explicit year, otherwise null.
    """
    code = mwparserfromhell.parse(text)
    category_years = [
        int(match[1])
        for link in code.filter_wikilinks(recursive=False)
        if (
            match := re.fullmatch(
                r"(?:Category|CAT|分类|分類)\s*:\s*"
                r"((?:19|20)\d{2})年[电電]子[游遊][戏戲]",
                str(link.title).strip(),
                re.IGNORECASE,
            )
        )
    ]
    if category_years:
        return True, min(category_years)
    boxes = [
        template
        for template in code.filter_templates(recursive=False)
        if str(template.name).strip().replace("_", " ").casefold()
        in {"infobox vg", "infobox video game"}
    ]
    if len(boxes) != 1:
        return None, None
    parts = []
    for field in ("released", "released header"):
        if not boxes[0].has(field):
            continue
        value = mwparserfromhell.parse(str(boxes[0].get(field).value))
        for tag in value.filter_tags():
            if str(tag.tag).casefold() in {"ref", "s", "del"}:
                value.remove(tag)
        for template in value.filter_templates(recursive=False):
            if str(template.name).strip().replace("_", " ").casefold() in {
                "vgr",
                "video game release",
            }:
                parts.extend(
                    str(parameter.value.strip_code())
                    for parameter in template.params
                    if str(parameter.name).strip().isdigit()
                    and int(str(parameter.name).strip()) % 2 == 0
                )
            value.remove(template)
        parts.append(str(value.strip_code()))
    release = "\n".join(parts)
    if re.search(
        r"取消|待定|cancelled|canceled|TBA|TBD",
        release,
        re.IGNORECASE,
    ):
        return True, None
    years = re.findall(
        r"(?<!\d)((?:19|20)\d{2})(?=年|[-/]\d|\s*(?:$|\n))",
        release,
    )
    return True, min(map(int, years)) if years else None


def enrich_year_dyk(
    rows: list[dict] | None,
    read: Callable[[str, dt.datetime], str] | None,
    through: dt.date,
    assessments: pl.DataFrame | None,
    *,
    projects: pl.DataFrame | None = None,
) -> list[dict] | None:
    """Attach game years, project memberships, and quality grades.

    Args:
        rows: Dated annual DYK observations, or unavailable.
        read: Historical reader, or none when sources are unavailable.
        through: Exclusive source cutoff for game metadata.
        assessments: Current open-year or exact closed-year snapshot.
        projects: Current subject-project rows with full_title and
            project columns, or none when membership is unobserved.
            Only the configured exact database project names are used.

    Returns:
        Original observations with nullable game metadata and grades.
        Missing game categories and infoboxes leave subjects unknown.
    """
    if rows is None:
        return None
    frame = pl.DataFrame(
        rows,
        schema={
            "title": pl.String,
            "date": pl.String,
            "contributors": pl.List(pl.String),
        },
    )
    boundary = dt.datetime.combine(through, dt.time.min, dt.UTC)
    metadata = []
    for title in (
        frame.select("title").unique().sort("title").get_column("title")
    ):
        try:
            is_game, year = (
                _game_release(read(title, boundary))
                if read is not None
                else (None, None)
            )
        except NoPageError:
            is_game, year = None, None
        metadata.append({
            "title": title,
            "is_game": is_game,
            "release_year": year,
        })
    frame = frame.join(
        pl.DataFrame(
            metadata,
            schema={
                key: dtype
                for key, dtype in _DYK_SCHEMA.items()
                if key != "date"
            },
        ),
        on="title",
        how="left",
        validate="m:1",
    )
    if projects is None:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.List(pl.String)).alias("projects"),
        )
    else:
        membership = (
            projects
            .filter(pl.col("project").is_in(DYK_PROJECTS))
            .select(
                pl.col("full_title").str.replace_all("_", " ").alias("title"),
                "project",
                pl
                .col("project")
                .replace_strict(
                    {name: rank for rank, name in enumerate(DYK_PROJECTS)},
                    default=len(DYK_PROJECTS),
                )
                .alias("priority"),
            )
            .unique(["title", "project"])
            .group_by("title")
            .agg(pl.col("project").sort_by("priority").alias("projects"))
        )
        frame = frame.join(
            membership,
            on="title",
            how="left",
            validate="m:1",
        ).with_columns(
            pl.col("projects").fill_null(pl.lit([], dtype=pl.List(pl.String))),
        )
    if assessments is None:
        return frame.with_columns(
            pl.lit(None, dtype=pl.String).alias("grade"),
        ).to_dicts()
    members = assessments
    if "page_namespace" in members.columns:
        members = members.filter(pl.col("page_namespace") == 0)
    grades = members.select(
        pl.col("full_title").str.replace_all("_", " ").alias("title"),
        pl
        .col("pa_class")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_lowercase()
        .str.replace(r"[级級]$", "")
        .replace_strict(_HIGH_GRADES, default=None)
        .alias("grade"),
    ).unique()
    return (
        frame
        .join(grades, on="title", how="left", validate="m:1")
        .sort("date", "title")
        .to_dicts()
    )


def year_quarters(issue: Issue) -> tuple[Issue, ...]:
    """Return the four reporting quarters for a January feature.

    Args:
        issue: January publication following the reviewed year.

    Returns:
        April, July, October, and January publication identities.
    """
    return (
        *(Issue(issue.year - 1, month) for month in (4, 7, 10)),
        issue,
    )


def collect_year_new_pages(
    read: Callable[[str, dt.datetime], str],
    issue: Issue,
    through: dt.date,
) -> list[dict]:
    """Count twelve months from one historical register observation.

    Args:
        read: Historical source reader accepting an exclusive boundary.
        issue: January publication following the reviewed year.
        through: Exclusive observation date within the reporting year.

    Returns:
        Four quarterly count records, retaining gaps and future months.
        Past quarters include corrections recorded before this cutoff.
    """
    boundary = dt.datetime.combine(through, dt.time.min, dt.UTC)

    @cache
    def source(title: str) -> str:
        """Read each annual source once at the common cutoff.

        Returns:
            Original historical source text.
        """
        try:
            return read(title, boundary)
        except NoPageError:
            return ""

    return [
        collect_new_pages(
            lambda title, _boundary: source(title),
            quarter,
            through=max(quarter.start, min(quarter.end, through)),
        )
        for quarter in year_quarters(issue)
    ]


def prepare_year_history(
    events: pl.DataFrame | None,
    issue: Issue,
    through: dt.date,
    *,
    assessments: pl.DataFrame | None = None,
) -> dict:
    """Select recorded annual milestones with their source evidence.

    Args:
        events: Complete project article histories, or unavailable.
        issue: January publication following the reviewed year.
        through: Exclusive observation date within the reviewed year.
        assessments: Current or exact-boundary project grades. Missing
            observations leave the recorded milestone state unchanged.

    Returns:
        Nullable DYK and quality event lists plus revision provenance.
        Nominators are recorded separately from verified authorship.
        Only the latest surviving annual quality addition is listed.
        Delisted and superseded grades supply no list or author credit.
        Duplicate milestones count once; conflicting attribution stays
        unknown. Removed events and their sources remain retained.
    """
    start = dt.date(issue.year - 1, 1, 1)
    result = prepare_history(events, start, through)
    if events is None:
        return result
    active = _observed_quality(_active_quality(events, through), assessments)
    added = events.filter(
        (pl.col("action") == "added") & (pl.col("kind") == "quality"),
    ).join(
        active.select("title", "date", "grade").unique(),
        on=["title", "date", "grade"],
        how="semi",
    )
    selected = pl.concat([
        events.filter(
            (pl.col("kind") != "quality") | (pl.col("action") != "added"),
        ),
        added,
    ])
    result["quality_changes"] = prepare_history(selected, start, through)[
        "quality_changes"
    ]
    return result


def prepare_quality_year_ends(
    events: pl.DataFrame | None,
    through: dt.date,
    *,
    assessments: pl.DataFrame | None = None,
) -> list[dict] | None:
    """Classify annual quality promotions at each year's boundary.

    Args:
        events: Complete retained milestone histories, or unavailable.
            Stable IDs keep renamed and redirected subjects distinct.
        through: Exclusive observation date. Completed years use the
            following January 1; the open year uses this date.
        assessments: Current or exact-boundary project grades for the
            final cohort. Earlier years retain their historical state.

    Returns:
        Each year's articles with at least one dated quality addition,
        counted once by their grade at year end or the current cutoff.
        An upgrade counts in its own year's cohort even when the article
        held another quality grade before that year. The latest addition
        selects one grade; higher grades win same-day ties. A subsequent
        removal of that selected grade clears it without reviving older
        awards. Removing another grade cannot clear the selected grade.
        Later years never alter earlier cohorts. Unknown dates have no
        inferred year. Missing history stays null; an observed history
        without dated additions is empty. Years without additions have
        zero counts within the supplied evidence's observed span.
    """
    if events is None:
        return None
    observed = events.filter(
        (pl.col("kind") == "quality")
        & pl.col("grade").is_in(QUALITY_HISTORY_GRADES)
        & (pl.col("date") < through),
    )
    additions = observed.filter(pl.col("action") == "added")
    if additions.is_empty():
        return []
    first_year = additions["date"].min().year
    last_year = (through - dt.timedelta(days=1)).year
    boundaries = pl.DataFrame(
        {"year": range(first_year, last_year + 1)},
        schema={"year": pl.Int32},
    ).with_columns(
        pl.min_horizontal(
            pl.date(pl.col("year") + 1, 1, 1),
            pl.lit(through),
        ).alias("boundary"),
    )
    identity = "page_id" if "page_id" in events.columns else "title"
    expanded = observed.join(boundaries, how="cross").filter(
        pl.col("date") < pl.col("boundary"),
    )
    annual_members = (
        expanded
        .filter(
            (pl.col("action") == "added")
            & (pl.col("date").dt.year() == pl.col("year")),
        )
        .select("year", identity)
        .unique()
    )
    active = _active_quality(expanded, through, groups=("year",)).join(
        annual_members,
        on=["year", identity],
        how="semi",
    )
    counts = (
        _observed_quality(active, assessments, year=last_year)
        .group_by("year")
        .agg(
            pl
            .col(identity)
            .filter(pl.col("grade") == grade)
            .n_unique()
            .alias(
                grade,
            )
            for grade in QUALITY_HISTORY_GRADES
        )
    )
    return (
        boundaries
        .select("year")
        .join(counts, on="year", how="left")
        .with_columns(pl.col(*QUALITY_HISTORY_GRADES).fill_null(0))
        .sort("year")
        .select("year", pl.struct(*QUALITY_HISTORY_GRADES).alias("grades"))
        .to_dicts()
    )


def prepare_year_dyk_quarters(
    events: pl.DataFrame | None,
    issue: Issue,
    through: dt.date,
) -> list[dict] | None:
    """Count first-time DYK articles at completed quarter boundaries.

    Args:
        events: Complete recorded project histories, or unavailable.
        issue: January issue following the reviewed calendar year.
        through: Exclusive annual observation date.

    Returns:
        Completed quarter ends and cumulative distinct first-time DYK
        counts since January 1. Earlier-year articles and repeated
        appearances never increase counts. Missing histories remain
        null; observed empty histories retain explicit zero counts.
    """
    if events is None:
        return None
    ends = [
        end
        for quarter in year_quarters(issue)
        if (end := quarter.end - dt.timedelta(days=1)) < through
    ]
    if not ends:
        return []
    first_dates = (
        events
        .filter(pl.col("kind") == "dyk")
        .group_by("title")
        .agg(pl.col("date").min())
        .filter(pl.col("date") >= dt.date(issue.year - 1, 1, 1))
    )
    return (
        first_dates
        .select(
            (pl.col("date") <= end).sum().alias(end.isoformat())
            for end in ends
        )
        .unpivot(variable_name="date", value_name="count")
        .sort("date")
        .to_dicts()
    )


def prepare_year_dyk_current(
    events: pl.DataFrame | None,
    issue: Issue,
    observed_on: dt.date,
) -> dict | None:
    """Count first-time DYK articles through the observed UTC date.

    Args:
        events: Complete recorded project histories, or unavailable.
        issue: January issue following the reviewed calendar year.
        observed_on: Inclusive UTC date of the source observation.

    Returns:
        The observation date and cumulative distinct first-time count,
        capped at December 31. Missing history or an observation before
        the reviewed year returns null; observed empty history is zero.
    """
    start = dt.date(issue.year - 1, 1, 1)
    day = min(observed_on, issue.end - dt.timedelta(days=1))
    if events is None or day < start:
        return None
    count = (
        events
        .filter(pl.col("kind") == "dyk")
        .group_by("title")
        .agg(pl.col("date").min())
        .filter(pl.col("date").is_between(start, day))
        .height
    )
    return {"date": day.isoformat(), "count": count}


def prepare_history(
    events: pl.DataFrame | None,
    start: dt.date,
    through: dt.date,
) -> dict:
    """Project recorded milestones for one bounded reporting period.

    Args:
        events: Complete source events, or unavailable.
        start: Inclusive first reporting date.
        through: Exclusive observation date.

    Returns:
        Deduplicated milestones and exact source revision provenance.
        Explicit DYK contributors merge across equivalent notices;
        conflicting nomination or computed authorship stays unknown.
    """
    if events is None:
        return {"dyk": None, "quality_changes": None, "sources": []}
    dated = events.filter(
        pl.col("date").is_between(
            start,
            through,
            closed="left",
        ),
    )
    attribution = (
        ("nominator", pl.String),
        ("main_author", pl.Boolean),
        ("nomination_link", pl.String),
        ("authors", pl.List(pl.String)),
    )
    dated = dated.with_columns(
        *(
            pl.lit(None, dtype=dtype).alias(key)
            for key, dtype in (
                *attribution,
                ("contributors", pl.List(pl.String)),
            )
            if key not in dated.columns
        ),
    )
    sources = (
        dated
        .select(pl.col("sources").explode(empty_as_null=False))
        .drop_nulls("sources")
        .unnest("sources")
        .select(pl.col("title").alias("label"), "url")
        .unique(maintain_order=True)
        .to_dicts()
        if "sources" in dated.columns
        else [
            {
                "label": f"Talk:{title}",
                "url": f"https://zh.wikipedia.org/w/index.php?oldid={revision}",
            }
            for title, revision in (
                dated
                .filter(pl.col("revision_id") > 0)
                .select("title", "revision_id")
                .unique()
                .iter_rows()
            )
        ]
    )
    return {
        "dyk": (
            dated
            .filter(pl.col("kind") == "dyk")
            .select(
                "title",
                pl.col("date").dt.to_string("%Y-%m-%d"),
                "contributors",
            )
            .group_by("title", "date")
            .agg(
                pl
                .when(pl.col("contributors").is_not_null().any())
                .then(
                    pl
                    .col("contributors")
                    .explode(empty_as_null=False)
                    .drop_nulls()
                    .unique()
                    .sort()
                    .implode(),
                )
                .otherwise(pl.lit(None, dtype=pl.List(pl.String)))
                .alias("contributors"),
            )
            .sort("date", "title")
            .to_dicts()
        ),
        "quality_changes": (
            dated
            .filter(pl.col("kind") == "quality")
            .select(
                "title",
                pl.col("date").dt.to_string("%Y-%m-%d"),
                "grade",
                "action",
                *(
                    pl.col(key)
                    if key in dated.columns
                    else pl.lit(None, dtype=dtype).alias(key)
                    for key, dtype in attribution
                ),
            )
            .group_by("title", "date", "grade", "action")
            .agg(
                *(
                    pl
                    .when(pl.col(key).drop_nulls().n_unique() == 1)
                    .then(pl.col(key).drop_nulls().first())
                    .otherwise(pl.lit(None, dtype=dtype))
                    .alias(key)
                    for key, dtype in attribution
                ),
            )
            .with_columns(
                pl
                .when(pl.col("nominator").is_not_null())
                .then(pl.col("main_author"))
                .otherwise(None)
                .alias("main_author"),
            )
            .sort("date", "grade", "action", "title")
            .to_dicts()
        ),
        "sources": sources,
    }
