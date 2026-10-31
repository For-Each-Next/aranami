"""Count curated new articles from complete historical daily records.

Read the register and archive at an explicit observation boundary.
The register uses ``vgc`` links; archives retain ordinary wikilinks.
Neither records historical assessments, so grade counts remain unknown.
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
    from shared.newsletter import Issue

_REGISTER = "WikiProject:电子游戏/新进条目"
_DAY = re.compile(r"^\*\s*(\d{1,2})月(\d{1,2})日\s*[-－]\s*(.*)$")
_YEAR = re.compile(r"^==\s*(\d{4})年\s*==$")
_SCHEMA = {"date": pl.Date, "titles": pl.List(pl.String)}
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_MAX_MONTHLY_ARTICLES = 100_000
_LAST_MONTH = 12
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


def collect_new_pages(
    read: object,
    issue: Issue,
    *,
    through: dt.date | None = None,
) -> dict[str, object]:
    """Count elapsed curated records through a completed UTC date.

    Args:
        read: Wiki reader exposing historical ``at(title, boundary)``.
        issue: Validated quarterly publication month.
        through: Exclusive observed date, or the quarter boundary.

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
                text = read.at(title, boundary)
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
    return {
        "total": total,
        "known_count": months.get_column("known_count").sum(),
        "missing_days": months.get_column("missing_days").sum(),
        "observation_end": through.isoformat(),
        "months": months.to_dicts(),
        "grades": None,
    }


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
    if not isinstance(value, dict) or value.get("grades") is not None:
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
    )
