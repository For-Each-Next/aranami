"""Count curated new articles within a selected reporting quarter.

Read the register and archive at an explicit acquisition time, separate
from the completed dates selected for the reporting quarter.
The register uses ``vgc`` links; archives retain ordinary wikilinks.
Caller-supplied assessments provide monthly grade breakdowns with their
actual observation time. Missing assessments remain unavailable.
The register, 2026 archive, and Template:Vgc were checked on
31 October 2026. Keyword-filter results are never used here.
"""

# Source contracts include literal Chinese punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from pywikibot.exceptions import NoPageError

from data.newsletter.grades import NEW_PAGE_GRADES
from shared.assessment_profiles import assessment_profile

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from shared.newsletter import Issue

_REGISTER = "WikiProject:电子游戏/新进条目"
_DAY = re.compile(r"^\*\s*(\d{1,2})月(\d{1,2})日\s*[-－]\s*(.*)$")
_YEAR = re.compile(r"^==\s*(\d{4})年\s*==$")
_SCHEMA = {"date": pl.Date, "titles": pl.List(pl.String)}
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_ASSESSMENTS = assessment_profile("zhwiki", "电子游戏")
_NON_ARTICLE_PREFIXES = frozenset({
    "category",
    "file",
    "image",
    "template",
    "draft",
    "wikipedia",
    "wikiproject",
    "user",
    "talk",
    "portal",
    "help",
    "module",
    "分类",
    "分類",
    "文件",
    "檔案",
    "图像",
    "圖像",
    "模板",
    "草稿",
    "用户",
    "使用者",
    "用戶",
    "讨论",
    "討論",
    "主题",
    "主題",
    "维基百科",
    "維基百科",
    "维基专题",
    "維基專題",
    "模块",
    "模組",
})


def _article_title(value: str) -> str | None:
    """Return a literal article title or none for unsupported input."""
    title = value.strip().lstrip(":").replace("_", " ")
    if (
        not title
        or re.search(r"[\[\]{}|<>\r\n]", title)
        or title.partition(":")[0].casefold() in _NON_ARTICLE_PREFIXES
    ):
        return None
    return title


def _titles(body: str) -> list[str] | None:
    """Parse one primary daily row without counting its namespace notes.

    Returns:
        Registered article titles, an explicit empty list, or unknown.
    """
    if body.strip() in {"無新條目", "无新条目"}:
        return []
    code = mwparserfromhell.parse(body)
    titles = []
    for template in code.filter_templates(recursive=False):
        if template.name.matches("vgc") and template.has("1"):
            title = _article_title(str(template.get("1").value))
            if title is None:
                return None
            titles.append(title)
        elif not template.name.matches("dykico"):
            return None
        code.remove(template)
    for link in code.filter_wikilinks(recursive=False):
        title = _article_title(str(link.title))
        if title is None:
            return None
        titles.append(title)
        code.remove(link)
    for comment in code.filter_comments(recursive=False):
        code.remove(comment)
    remainder = str(code).strip(" \t、,，'。")
    return sorted(set(titles)) if titles and not remainder else None


def _daily(text: str, issue: Issue) -> pl.DataFrame:
    """Parse dated register rows within one reporting quarter.

    Returns:
        Typed daily records, including unknown blank or malformed rows.

    Raises:
        ValueError: The source exceeds the bounded parsing limit.
    """
    if len(text.encode()) > _MAX_SOURCE_BYTES:
        message = "The curated new-page register exceeds the source limit."
        raise ValueError(message)
    year = None
    rows = []
    for line in text.splitlines():
        heading = _YEAR.fullmatch(line.strip())
        if heading:
            year = int(heading[1])
        match = _DAY.fullmatch(line)
        if match is None or year is None:
            continue
        day = dt.date(year, int(match[1]), int(match[2]))
        if issue.start <= day < issue.end:
            rows.append({"date": day, "titles": _titles(match[3])})
    return pl.DataFrame(rows, schema=_SCHEMA)


def _assessment_grades(assessments: pl.DataFrame) -> pl.DataFrame:
    """Normalize project classes, using unassessed when none applies.

    Returns:
        Unique article titles and their canonical observed grades.

    Raises:
        ValueError: Required columns or unique identities are absent.
    """
    if not {"full_title", "pa_class"}.issubset(assessments.columns):
        message = "New-page grades require article titles and assessments."
        raise ValueError(message)
    grade = _ASSESSMENTS.expression(
        "pa_class",
        "class",
        preserve_unknown=False,
    )
    members = (
        assessments.filter(pl.col("page_namespace") == 0)
        if "page_namespace" in assessments.columns
        else assessments
    )
    result = members.select(
        pl.col("full_title").str.replace_all("_", " ").alias("title"),
        grade.replace_strict(
            {code: code for code in NEW_PAGE_GRADES},
            default="Unassessed",
        ).alias("grade"),
        *([pl.col("page_id")] if "page_id" in members.columns else []),
    ).unique()
    if result.get_column("title").n_unique() != result.height:
        message = "New-page assessment titles have conflicting grades."
        raise ValueError(message)
    return result


def _grade_counts(  # ruff: ignore[too-many-arguments]
    daily: pl.DataFrame,
    assessments: pl.DataFrame,
    through: dt.date,
    *,
    resolve: Callable[[str], str] | None,
    resolve_many: Callable[[Iterable[str]], None] | None,
    resolve_page_id: Callable[[str], int | None] | None,
) -> pl.DataFrame:
    """Join registered articles to one supplied assessment observation.

    Returns:
        Monthly grade counts for unambiguous elapsed register rows.
        Subjects without a valid project class are Unassessed.
    """
    members = _assessment_grades(assessments)
    records = (
        daily
        .filter((pl.col("date") < through) & pl.col("count").is_not_null())
        .select("date", "titles")
        .explode("titles", empty_as_null=False)
        .rename({"titles": "title"})
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .join(
            members.select("title", "grade"),
            on="title",
            how="left",
            validate="m:1",
        )
    )
    if resolve is not None:
        missing = (
            records
            .filter(pl.col("grade").is_null())
            .select("title")
            .unique()
            .get_column("title")
            .to_list()
        )
        if missing and resolve_many is not None:
            resolve_many(missing)
        aliases = pl.DataFrame(
            {
                "title": missing,
                "canonical": [resolve(title) for title in missing],
            },
            schema={"title": pl.String, "canonical": pl.String},
        )
        records = records.join(
            aliases,
            on="title",
            how="left",
            validate="m:1",
        ).join(
            members.select("title", "grade").rename({
                "title": "canonical",
                "grade": "resolved_grade",
            }),
            on="canonical",
            how="left",
            validate="m:1",
        )
        if resolve_page_id is not None and "page_id" in members.columns:
            identifiers = pl.DataFrame(
                {
                    "title": missing,
                    "page_id": [resolve_page_id(title) for title in missing],
                },
                schema={"title": pl.String, "page_id": pl.Int64},
            )
            records = (
                records
                .join(identifiers, on="title", how="left", validate="m:1")
                .join(
                    members.select(
                        "page_id",
                        pl.col("grade").alias("identity_grade"),
                    ),
                    on="page_id",
                    how="left",
                    validate="m:1",
                )
                .with_columns(
                    pl.coalesce(
                        "grade",
                        "identity_grade",
                        "resolved_grade",
                    ).alias("grade"),
                )
            )
        else:
            records = records.with_columns(
                pl.coalesce("grade", "resolved_grade").alias("grade"),
            )
    return (
        records
        .with_columns(pl.col("grade").fill_null("Unassessed"))
        .group_by("month", "grade")
        .len(name="count")
        .sort("month", "grade")
    )


def _grade_mapping(counts: pl.DataFrame) -> dict[str, int]:
    """Serialize a small, already aggregated grade-count table.

    Returns:
        Canonical grade codes with their positive observed counts.
    """
    return dict(counts.select("grade", "count").iter_rows())


def _source_boundary(
    through: dt.date,
    observed_at: dt.datetime | None,
) -> dt.datetime:
    """Select an aware source read time after the selected date range.

    Returns:
        The actual observation time or the historical UTC boundary.

    Raises:
        ValueError: An acquisition time is naive or precedes the range.
    """
    boundary = dt.datetime.combine(through, dt.time.min, dt.UTC)
    if observed_at is None:
        return boundary
    if observed_at.utcoffset() is None or observed_at < boundary:
        message = "New-page sources require an aware acquisition time."
        raise ValueError(message)
    return observed_at.astimezone(dt.UTC)


def collect_new_pages(  # ruff: ignore[too-many-arguments]
    read: Callable[[str, dt.datetime], str],
    issue: Issue,
    *,
    through: dt.date | None = None,
    observed_at: dt.datetime | None = None,
    assessments: pl.DataFrame | None = None,
    assessment_observed_at: str | None = None,
    resolve: Callable[[str], str] | None = None,
    resolve_many: Callable[[Iterable[str]], None] | None = None,
    resolve_page_id: Callable[[str], int | None] | None = None,
) -> dict[str, object]:
    """Count curated records from an explicit source observation.

    Args:
        read: Historical source callable accepting title and boundary.
        issue: Validated quarterly publication month.
        through: Exclusive selected date, or the quarter boundary.
        observed_at: Actual source acquisition time. Later observations
            include register corrections for the same selected quarter.
            Omit only for an exact historical boundary read.
        assessments: Internal article assessments for the issue.
            Omit when no appropriate observation is available.
        assessment_observed_at: Actual timestamp of that observation.
        resolve: Canonical title lookup for unmatched register subjects.
        resolve_many: Optional batch primer for title resolution.
        resolve_page_id: Primed subject identity lookup through renames.

    Returns:
        Nullable elapsed totals and monthly counts with observed days.
        Future dates contribute neither zeroes nor missing coverage.
        Blank, missing, or conflicting elapsed dates remain unknown.

    Raises:
        ValueError: The selected dates or acquisition time are invalid.
    """
    through = through or issue.end
    if not issue.start <= through <= issue.end:
        message = "New-page observations must lie within the issue quarter."
        raise ValueError(message)
    boundary = _source_boundary(through, observed_at)
    frames = []
    if through > issue.start:
        for title in (_REGISTER, f"{_REGISTER}/{issue.start.year}年存档"):
            try:
                text = read(title, boundary)
            except NoPageError:
                text = ""
            frames.append(_daily(text, issue))
    if not frames:
        frames.append(pl.DataFrame(schema=_SCHEMA))
    daily = (
        pl
        .concat(frames)
        .group_by("date")
        .agg(
            pl.col("titles").drop_nulls().n_unique().alias("versions"),
            pl.col("titles").drop_nulls().first(),
        )
        .with_columns(
            pl
            .when(pl.col("versions") == 1)
            .then(pl.col("titles").list.len())
            .otherwise(None)
            .alias("count"),
        )
    )
    calendar = pl.DataFrame({
        "date": pl.date_range(
            issue.start,
            issue.end - dt.timedelta(days=1),
            eager=True,
        ),
    })
    months = (
        calendar
        .join(daily, on="date", how="left")
        .with_columns(
            pl.col("date").dt.strftime("%Y-%m").alias("month"),
            (pl.col("date") < pl.lit(through)).alias("observed"),
        )
        .group_by("month")
        .agg(
            pl.col("observed").sum().alias("observed_days"),
            pl
            .col("count")
            .filter(pl.col("observed"))
            .sum()
            .alias("known_count"),
            pl
            .col("count")
            .filter(pl.col("observed"))
            .null_count()
            .alias("missing_days"),
            pl
            .when(
                (pl.col("observed").sum() > 0)
                & (
                    pl.col("count").filter(pl.col("observed")).null_count()
                    == 0
                ),
            )
            .then(pl.col("count").filter(pl.col("observed")).sum())
            .otherwise(None)
            .alias("count"),
        )
        .sort("month")
    )
    elapsed = months.filter(pl.col("observed_days") > 0)
    total = (
        elapsed.select(
            pl
            .when(pl.col("count").null_count() == 0)
            .then(pl.col("count").sum())
            .otherwise(None),
        ).item()
        if elapsed.height
        else None
    )
    result = {
        "total": total,
        "known_count": months.get_column("known_count").sum(),
        "missing_days": months.get_column("missing_days").sum(),
        "observation_end": through.isoformat(),
        "months": months.to_dicts(),
        "grades": None,
    }
    if observed_at is not None:
        result["source_observed_at"] = boundary.isoformat(timespec="seconds")
    if assessments is not None:
        grades = _grade_counts(
            daily,
            assessments,
            through,
            resolve=resolve,
            resolve_many=resolve_many,
            resolve_page_id=resolve_page_id,
        )
        result["grades"] = _grade_mapping(
            grades.group_by("grade").agg(pl.col("count").sum()),
        )
        for row in result["months"]:
            row["grades"] = _grade_mapping(
                grades.filter(pl.col("month") == row["month"]),
            )
        if assessment_observed_at is not None:
            result["grades_observed_at"] = assessment_observed_at
    return result
