"""Count curated new articles from complete historical daily records.

Read the register and archive at an explicit observation boundary.
The register uses ``vgc`` links; archives retain ordinary wikilinks.
Caller-supplied assessments provide monthly grade breakdowns. Missing
snapshots remain unavailable rather than using later assessments.
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

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from shared.newsletter import Issue

_REGISTER = "WikiProject:电子游戏/新进条目"
_DAY = re.compile(r"^\*\s*(\d{1,2})月(\d{1,2})日\s*[-－]\s*(.*)$")
_YEAR = re.compile(r"^==\s*(\d{4})年\s*==$")
_SCHEMA = {"date": pl.Date, "titles": pl.List(pl.String)}
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_MAX_MONTHLY_ARTICLES = 100_000
_LAST_MONTH = 12
_GRADE_GROUPS = (
    ("FA", "fa", "典范", "典範"),
    ("FL", "fl", "特色列表", "典表"),
    ("A", "a", "甲"),
    ("AL", "al", "甲表", "甲级列表", "甲級列表"),
    ("GA", "ga", "优良", "優良"),
    ("Bplus", "bplus", "b+", "乙上"),
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
    ("Unassessed", "unassessed", "未评", "未評"),
)
_GRADE_ALIASES = {
    alias: code for code, *aliases in _GRADE_GROUPS for alias in aliases
}
# Accept legacy observations; new reports use Unassessed for no class.
_GRADE_CODES = frozenset(code for code, *_ in _GRADE_GROUPS) | {"Unknown"}
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
    grade = (
        pl
        .col("pa_class")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_lowercase()
        .str.replace(r"[级級]$", "")
    )
    members = (
        assessments.filter(pl.col("page_namespace") == 0)
        if "page_namespace" in assessments.columns
        else assessments
    )
    result = members.select(
        pl.col("full_title").str.replace_all("_", " ").alias("title"),
        grade.replace_strict(
            _GRADE_ALIASES,
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


def collect_new_pages(  # ruff: ignore[too-many-arguments]
    read: Callable[[str, dt.datetime], str],
    issue: Issue,
    *,
    through: dt.date | None = None,
    assessments: pl.DataFrame | None = None,
    assessment_observed_at: str | None = None,
    resolve: Callable[[str], str] | None = None,
    resolve_many: Callable[[Iterable[str]], None] | None = None,
    resolve_page_id: Callable[[str], int | None] | None = None,
) -> dict[str, object]:
    """Count elapsed curated records through a completed UTC date.

    Args:
        read: Historical source callable accepting title and boundary.
        issue: Validated quarterly publication month.
        through: Exclusive observed date, or the quarter boundary.
        assessments: Internal article assessments for the issue.
            Omit when no appropriate historical snapshot is available.
        assessment_observed_at: Actual timestamp of that observation.
        resolve: Canonical title lookup for unmatched register subjects.
        resolve_many: Optional batch primer for title resolution.
        resolve_page_id: Primed subject identity lookup through renames.

    Returns:
        Nullable elapsed totals and monthly counts with observed days.
        Future dates contribute neither zeroes nor missing coverage.
        Blank, missing, or conflicting elapsed dates remain unknown.

    Raises:
        ValueError: The observation boundary lies outside the quarter.
    """
    through = through or issue.end
    if not issue.start <= through <= issue.end:
        message = "New-page observations must lie within the issue quarter."
        raise ValueError(message)
    boundary = dt.datetime.combine(through, dt.time.min, dt.UTC)
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


def _valid_month(
    row: object,
    month: str,
    observed: dt.date,
) -> bool:
    """Check one month's known counts and elapsed coverage.

    Returns:
        Whether observed days, gaps, and complete counts agree.
    """
    if not isinstance(row, dict) or row.get("month") != month:
        return False
    count = row.get("count")
    month_start = dt.date.fromisoformat(f"{month}-01")
    month_end = (
        dt.date(month_start.year + 1, 1, 1)
        if month_start.month == _LAST_MONTH
        else dt.date(month_start.year, month_start.month + 1, 1)
    )
    elapsed_days = max(0, (min(observed, month_end) - month_start).days)
    missing, known = row.get("missing_days"), row.get("known_count")
    if type(row.get("observed_days")) is not int or (
        row["observed_days"] != elapsed_days
    ):
        return False
    if type(missing) is not int or not 0 <= missing <= elapsed_days:
        return False
    if (
        type(known) is not int
        or not 0 <= known <= _MAX_MONTHLY_ARTICLES
        or (elapsed_days == 0 and known != 0)
    ):
        return False
    expected_count = known if elapsed_days and not missing else None
    return count == expected_count and (count is None or type(count) is int)


def _valid_grades(value: object, count: int) -> bool:
    """Check bounded grade counts against the registered article total.

    Returns:
        Whether every bucket is supported and all articles are counted.
    """
    return (
        isinstance(value, dict)
        and set(value).issubset(_GRADE_CODES)
        and all(
            type(number) is int and 0 < number <= count
            for number in value.values()
        )
        and sum(value.values()) == count
    )


def _valid_breakdown(value: dict, observed: dt.date) -> bool:
    """Check optional monthly grade counts and their observation time.

    Returns:
        Whether monthly observations sum to the quarterly breakdown.
    """
    grades = value.get("grades")
    if grades is None:
        return "grades_observed_at" not in value and all(
            row.get("grades") is None for row in value["months"]
        )
    if not _valid_grades(grades, value["known_count"]) or not all(
        _valid_grades(row.get("grades"), row["known_count"])
        for row in value["months"]
    ):
        return False
    if any(
        count != sum(row["grades"].get(grade, 0) for row in value["months"])
        for grade, count in grades.items()
    ):
        return False
    if "grades_observed_at" in value:
        try:
            stamp = dt.datetime.fromisoformat(value["grades_observed_at"])
        except ValueError, TypeError:
            return False
        return stamp.utcoffset() == dt.timedelta() and stamp.date() == observed
    return True


def valid_new_pages(  # ruff: ignore[too-many-return-statements]
    value: object,
    issue: Issue,
    *,
    through: dt.date | None = None,
) -> bool:
    """Validate optional curated counts and their quarter identity.

    Args:
        value: Decoded saved count observations, or none.
        issue: Expected reporting quarter.
        through: Expected exclusive observation boundary, if supplied.

    Returns:
        Whether nullable totals and all three monthly counts agree.
    """
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    try:
        observed = dt.date.fromisoformat(value["observation_end"])
    except TypeError, ValueError, KeyError:
        return False
    if not issue.start <= observed <= issue.end or (
        through is not None and through != observed
    ):
        return False
    months = value.get("months")
    expected = [
        f"{issue.start.year:04}-{month:02}"
        for month in range(issue.start.month, issue.start.month + 3)
    ]
    if not isinstance(months, list) or len(months) != len(expected):
        return False
    for row, month in zip(months, expected, strict=True):
        if not _valid_month(row, month, observed):
            return False
    counts = [row.get("count") for row in months if row["observed_days"]]
    total = None if not counts or None in counts else sum(counts)
    return (
        value.get("total") == total
        and (total is None or type(value["total"]) is int)
        and type(value.get("known_count")) is int
        and value["known_count"] == sum(row["known_count"] for row in months)
        and type(value.get("missing_days")) is int
        and value["missing_days"] == sum(row["missing_days"] for row in months)
        and _valid_breakdown(value, observed)
    )
