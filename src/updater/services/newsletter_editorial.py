"""Render empty editorial pages and a January annual data draft.

Annual sections follow the January 2026 feature (revision 91016278).
The draft banner and noteTA-lite call follow the audited October
2026 subpages.
Statistics use supplied annual facts and full missing-data sentences.
"""

# Authored Chinese output deliberately uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import math
import re
from typing import TYPE_CHECKING

import polars as pl

from backend.newsletter.statistics import (
    HISTORICAL_DYK,
    QUALITY_GRADES,
)
from shared.newsletter import DYK_PROJECTS, ROOT

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from shared.newsletter import Issue

_GRADES = {
    "FA": "典范条目",
    "FL": "特色列表",
    "A": "甲级条目",
    "AL": "甲级列表",
    "GA": "优良条目",
    "Bplus": "乙上级条目",
    "FT": "典范话题",
    "GT": "优良话题",
}
_ANNUAL_QUALITY_GRADES = ("FA", "FL", "A", "AL", "GA", "Bplus")
_ANNUAL_QUALITY_GROUPS = (
    (("FA", "FL"), "[[PJ:VG/FA|典範內容]]"),
    (("A", "AL"), "[[PJ:VG/A|甲級內容]]"),
    (("GA",), "[[PJ:VG/GA|優良條目]]"),
    (("Bplus",), "[[PJ:VG/BPCA|乙上級條目]]"),
)
_DYK_PROJECT_HEADINGS = {
    "虚构角色": "角色與元素",
    "传记": "公司與人物",
    "公司": "公司與人物",
    "音乐": "音樂",
}
_OVERVIEW = {
    "total": "條目總數",
    "fa": "典范条目",
    "fl": "特色列表",
    "ga": "优良条目",
    "dyk": "新條目推薦",
    "bplus": "乙上级条目",
    "topics": "优良话题",
}
LITERAL_ESCAPES = str.maketrans({
    **{character: f"&#{ord(character)};" for character in "[]{}|\r\n"},
    "<": "&lt;",
    ">": "&gt;",
})
_MONTHS = 12
_PIE_TOTAL_WIDTH = 400
_PIE_HEIGHT = 250
_PIE_TEXT_PADDING = 6
_FIRST_BAND_END = 2009
_SECOND_BAND_END = 2019
_ICON_GRADES = frozenset({"Bplus", "GA", "A", "FA", "FL"})
_CREDIT_GRADES = {
    "FA": "典範條目",
    "FL": "特色列表",
    "A": "甲級條目",
    "AL": "甲級列表",
    "GA": "優良條目",
    "Bplus": "乙上級條目",
    "FT": "典範話題",
    "GT": "優良話題",
}
_AWARD_THRESHOLD = 10
_DYK_BASELINE_YEAR = 2025


def _literal(value: object) -> str:
    """Escape source text for a wiki table cell or link label.

    Returns:
        Literal text with markup delimiters encoded.
    """
    text = str(value).translate(LITERAL_ESCAPES)
    return re.sub(r"'{2,}", lambda match: "&#39;" * len(match[0]), text)


def _link(title: str, label: str | None = None) -> str:
    """Render a literal title without transclusion or embeds.

    Returns:
        A safe link with an optional literal label.
    """
    target = _literal(title).lstrip(":")
    return f"[[:{target}|{_literal(label)}]]" if label else f"[[:{target}]]"


def _article(title: str, grade: str | None = None) -> str:
    """Mark verified Bplus-or-higher assessments before article links.

    Returns:
        The article link with its known quality icon, if applicable.
    """
    grade = "A" if grade == "AL" else grade
    icon = f"{{{{icon|{grade}}}}}&thinsp;" if grade in _ICON_GRADES else ""
    return icon + _link(title)


def _dated_articles(
    articles: Iterable[str],
    dates: Iterable[str | None],
) -> list[str]:
    """Append known article listing dates for manual draft editing.

    Args:
        articles: Rendered articles in listing-date order.
        dates: Corresponding ISO listing dates, with missing dates null.

    Returns:
        Articles followed by MMDD comments; unknown dates stay unmarked.
    """
    result = []
    for article, date in zip(articles, dates, strict=True):
        comment = f"<!-- {date[5:7]}{date[8:10]} -->" if date else ""
        result.append(article + comment)
    return result


def _count(value: object) -> str:
    """Distinguish an observed integer from missing statistics.

    Returns:
        A formatted integer or a blank missing-data slot.
    """
    return f"{value:,}" if type(value) is int else ""


def _annual_data_through(
    review: Mapping,
    *,
    data_through: str | None = None,
) -> dt.date | None:
    """Resolve the inclusive date covered by annual statistics.

    Args:
        review: Saved annual facts with an exclusive observation cutoff.
        data_through: Inclusive date for independently dated data.

    Returns:
        Inclusive data date, or none when no valid date was retained.
    """
    try:
        return (
            dt.date.fromisoformat(data_through)
            if data_through is not None
            else dt.date.fromisoformat(review["observation_end"])
            - dt.timedelta(days=1)
        )
    except KeyError, TypeError, ValueError:
        return None


def _annual_as_of(
    review: Mapping,
    *,
    data_through: str | None = None,
) -> str:
    """Mark the inclusive date covered by incomplete annual statistics.

    Args:
        review: Saved annual facts with an exclusive observation cutoff.
        data_through: Inclusive date for independently dated data.

    Returns:
        An inline Memo date marker, or a missing-date sentence.
    """
    day = _annual_data_through(review, data_through=data_through)
    if day is None:
        return "{{Memo|統計截至日期待補。|inline:info}}"
    return f"{{{{Memo|截至{day.year}年{day.month}月{day.day}日|inline:info}}}}"


def _annual_intro(
    review: Mapping,
    sentence: str,
    *,
    partial: bool | None = None,
    data_through: str | None = None,
) -> str:
    """Keep a figure or list's coverage date in its introduction.

    Args:
        review: Saved annual facts with the shared observation cutoff.
        sentence: Prose immediately preceding the figure or list.
        partial: Override the annual observation's incomplete status.
        data_through: Inclusive date for independently dated data.

    Returns:
        The introduction with an inline coverage mark when needed.
    """
    incomplete = not review.get("complete") if partial is None else partial
    return sentence + (
        _annual_as_of(review, data_through=data_through) if incomplete else ""
    )


def _table(
    headers: tuple[str, ...],
    rows: list[list[str]],
    *,
    style: str = "",
) -> str:
    """Render already escaped cells as a simple sortable data table.

    Args:
        headers: Column labels ready for wikitext rendering.
        rows: Escaped cell values in display order.
        style: Trusted inline CSS for this generated table.

    Returns:
        Complete table wikitext.
    """
    attributes = f' style="{style}"' if style else ""
    lines = [
        '{| class="wikitable sortable"' + attributes,
        "! " + " !! ".join(headers),
    ]
    for cells in rows:
        lines.extend(("|-", "| " + " || ".join(cells)))
    lines.append("|}")
    return "\n".join(lines)


def _banner(issue: Issue) -> str:
    """Reuse the verified draft and Chinese conversion templates.

    Returns:
        The issue-specific draft notice and conversion call.
    """
    return (
        "{{mbox\n"
        "| image=[[File:Schedule (cropped).jpg|50px|link=|"
        "class=notpageimage skin-invert-image|編寫中]]\n"
        f"| text=此為草稿，預定於{issue.year}年{issue.month}月出刊，"
        f"歡迎[[WikiProject talk:電子遊戲/簡訊#{issue.value}|討論]]和編輯。\n"
        "}}\n\n{{noteTA-lite|G1=電子遊戲}}\n\n"
    )


def _invitation(issue: Issue) -> str:
    """Open the feature's comment preload with an empty heading.

    Returns:
        A styled new-section link using the verified newsletter preload.
    """
    url = (
        f"{{{{fullurl:{ROOT}/{issue.value}/專題"
        f"|action=edit&section=new&preload={ROOT}/preload}}}}"
    )
    return (
        "<center>{{Clickable button 2|1=📝&nbsp;新增感想|url="
        + url
        + "|class=mw-ui-progressive"
        + "|style=display:inline-flex;align-items:center;"
        + "justify-content:center;box-sizing:border-box;min-height:32px;"
        + "padding:0 11px;border:1px solid transparent;border-radius:2px;"
        + "background:#36c;color:#fff;font-weight:700;line-height:1.375"
        + "}}</center>"
    )


def _quality(review: Mapping) -> tuple[str, str]:
    """Render separate quality lists and author-credit table.

    Returns:
        Promotions in listing-date order and the author-credit table.
        Empty article groups are omitted.
    """
    changes = review.get("quality_changes")
    frame = (
        pl
        .DataFrame(
            changes or [],
            schema={
                "date": pl.String,
                "title": pl.String,
                "grade": pl.String,
                "action": pl.String,
                "nominator": pl.String,
                "main_author": pl.Boolean,
                "authors": pl.List(pl.String),
            },
        )
        .filter(pl.col("action") == "added")
        .sort("date", "title", "grade", nulls_last=True)
    )
    groups = {
        row["grade"]: row
        for row in frame
        .unique(["grade", "title"], keep="first", maintain_order=True)
        .group_by("grade")
        .agg(pl.col("title"), pl.col("date"))
        .iter_rows(named=True)
    }
    lines = []
    for grade, label in _GRADES.items():
        if grade not in groups:
            continue
        titles = groups[grade]["title"]
        count = (
            str(len(titles)) if changes is not None and grade != "GT" else ""
        )
        if titles:
            count = str(len(titles))
        icon_grade = "A" if grade == "AL" else grade
        lines.append(
            f"* {{{{icon|{icon_grade}}}}}&thinsp;'''{label}（{count}）'''："
            + "、".join(
                _dated_articles(
                    map(_link, titles),
                    groups[grade]["date"],
                ),
            ),
        )
    return "\n".join(lines), _credits(frame)


def _credits(changes: pl.DataFrame) -> str:
    """Credit nominators and explain retained authorship per article.

    Returns:
        The reference table with nominators preferred over computed
        authors, retaining tied author fallbacks and explicit notices.
    """
    contributions = (
        changes
        .with_columns(
            pl
            .when(pl.col("nominator").is_null())
            .then(pl.col("authors"))
            .otherwise(pl.concat_list("nominator"))
            .alias("editor"),
            pl
            .col("grade")
            .replace_strict(
                {"FA": 0, "FL": 0, "A": 1, "AL": 1, "GA": 2, "Bplus": 3},
                default=4,
            )
            .alias("grade_order"),
        )
        .explode("editor", empty_as_null=True)
        .unique(
            [
                "editor",
                "title",
                "grade",
                "main_author",
                "authors",
                "nominator",
            ],
            keep="first",
            maintain_order=True,
        )
        .group_by("editor")
        .agg(
            pl
            .struct(
                "title",
                "grade",
                "main_author",
                "authors",
                "nominator",
                "date",
            )
            .sort_by("grade_order", "date", "title", nulls_last=True)
            .alias("articles"),
            pl.col("title").n_unique().alias("count"),
            *(
                pl
                .col("title")
                .filter(pl.col("grade") == grade)
                .n_unique()
                .alias(grade)
                for grade in _CREDIT_GRADES
            ),
        )
        .sort(
            "count",
            "FA",
            "GA",
            "Bplus",
            "editor",
            descending=[True, True, True, True, False],
        )
        .with_columns(
            pl
            .struct("count", "FA", "GA", "Bplus")
            .rank("min", descending=True)
            .alias("rank"),
            pl.len().over("count", "FA", "GA", "Bplus").alias("rank_span"),
        )
    )
    rows = []
    previous_rank = None
    for row in contributions.iter_rows(named=True):
        articles = []
        dates = []
        for article in row["articles"]:
            text = _article(article["title"], article["grade"])
            if article["main_author"] is False:
                text += "（非主編）"
            authors = article["authors"]
            if authors and article["nominator"] not in authors:
                text += "{{small|主要文字貢獻者為" + _users(authors) + "。}}"
            articles.append(text)
            dates.append(article["date"])
        editor = (
            _link("User:" + row["editor"], row["editor"])
            if row["editor"]
            else ""
        )
        summary = "、".join(
            f"{row[grade]}篇{label}"
            for grade, label in _CREDIT_GRADES.items()
            if row[grade]
        )
        listing = "\n".join([
            "{{collapsible list",
            "| titlestyle = font-weight: normal;",
            f"| title = {summary}",
            *(
                f"| {number} = {text}"
                for number, text in enumerate(
                    _dated_articles(articles, dates),
                    1,
                )
            ),
            "}}",
        ])
        cells = [editor, listing]
        if row["rank"] != previous_rank:
            rank = str(row["rank"])
            if row["rank_span"] > 1:
                rank = f'rowspan="{row["rank_span"]}" | {rank}'
            cells.insert(0, rank)
        rows.append(cells)
        previous_rank = row["rank"]
    return _table(
        ("#", "編者", "條目"),
        rows,
        style=(
            "margin-left: auto; margin-right: auto; "
            "min-width: max(80%, 255px); "
            "width: 864px; max-width: 100%;"
        ),
    ) + (
        "\n注：名單依照總條目數排序；總數相同的，"
        "再先後按照典範、優良、乙上級內容數排列。"
    )


def _users(users: list[str]) -> str:
    """Format prepared contributor names as literal user links.

    Returns:
        A compact comma-separated attribution.
    """
    return "、".join(_link("User:" + user, user) for user in users)


def _dyk(review: Mapping, year: int) -> str:
    """Group games and observed project subjects without duplicates.

    Returns:
        Five game era bands, verified subject groups, and unknowns.
    """
    records = review.get("dyk")
    if records is None:
        return "本年度新條目推薦名單待補充。"
    if not records:
        return "* （無）"
    frame = (
        pl
        .DataFrame(
            records or [],
            schema={
                "date": pl.String,
                "title": pl.String,
                "is_game": pl.Boolean,
                "contributors": pl.List(pl.String),
                "release_year": pl.Int32,
                "grade": pl.String,
                "projects": pl.List(pl.String),
            },
        )
        .sort("date", "title")
        .unique("title", keep="first", maintain_order=True)
    )
    bands = [
        "2009年及以前",
        "2010年—2019年",
        f"2020年—{year - 1}年",
        f"{year}年",
        "其他",
    ]
    grouped = (
        frame
        .with_columns(
            pl
            .when(pl.col("is_game") & pl.col("release_year").is_not_null())
            .then(
                pl
                .when(pl.col("release_year") <= _FIRST_BAND_END)
                .then(pl.lit(bands[0]))
                .when(pl.col("release_year") <= _SECOND_BAND_END)
                .then(pl.lit(bands[1]))
                .when(pl.col("release_year") < year)
                .then(pl.lit(bands[2]))
                .when(pl.col("release_year") == year)
                .then(pl.lit(bands[3]))
                .otherwise(pl.lit("game_other")),
            )
            .otherwise(
                pl.coalesce([
                    pl
                    .when(pl.col("projects").list.contains(project))
                    .then(pl.lit(_DYK_PROJECT_HEADINGS[project]))
                    .otherwise(None)
                    for project in DYK_PROJECTS
                ]),
            )
            .alias("band"),
        )
        .group_by("band")
        .agg(pl.col("title"), pl.col("grade"), pl.col("date"))
    )
    by_band = {row["band"]: row for row in grouped.iter_rows(named=True)}
    game_count = (
        grouped
        .filter(
            pl.col("band").is_in([*bands[:-1], "game_other"]),
        )
        .select(pl.col("title").list.len().sum())
        .item()
    )
    count = _count(game_count)
    lines = [f"* '''遊戲作品（{count}）'''："]
    labels = [
        *(("**", band, band) for band in bands[:-1]),
        ("**", bands[-1], "game_other"),
        *(
            ("*", label, label)
            for label in (
                "角色與元素",
                "公司與人物",
                "音樂",
                "開發與業界",
                "其他",
            )
        ),
    ]
    if None in by_band:
        labels.append(("*", "待分類", None))
    for marker, label, key in labels:
        row = by_band.get(key)
        text = (
            "、".join(
                _dated_articles(
                    map(
                        _article,
                        row["title"],
                        row["grade"],
                        strict=True,
                    ),
                    row["date"],
                ),
            )
            if row
            else ""
        )
        count = str(len(row["title"]) if row else 0)
        text = text or "（無）"
        lines.append(f"{marker} '''{label}（{count}）'''：{text}")
    return "\n".join(lines)


def _pie_percent_labels(counts: list[int], plot_width: float) -> str:
    """Place eligible percentages inside the native pie's sectors.

    The native renderer adds a 14-pixel left margin and a title above
    the plot. Its circle radius follows the smaller plot dimension.

    Args:
        counts: Positive slice values in native chart order.
        plot_width: Native plot width excluding the legend and padding.

    Returns:
        Static black, regular-weight labels for exact shares of at least
        five percent. Labels never intercept native chart interaction.
    """
    total = sum(counts)
    center_x = _PIE_TEXT_PADDING / 2 + 14 + plot_width / 2
    center_y = 32 + _PIE_HEIGHT / 2
    outer_radius = min(plot_width, _PIE_HEIGHT) / 2 - 6
    angle = -math.pi / 2
    labels = []
    for count in counts:
        sweep = math.tau * count / total
        midpoint = angle + sweep / 2
        if count * 20 >= total:
            # Narrow sectors need a wider arc for regular-width text.
            radius = outer_radius * ((103 if count * 10 < total else 80) / 119)
            x = center_x + radius * math.cos(midpoint)
            y = center_y + radius * math.sin(midpoint)
            labels.append(
                '<span class="pie-percentage" '
                f'style="position:absolute;left:{x:.2f}px;top:{y:.2f}px;'
                "transform:translate(-50%,-50%);color:#000;"
                "font-size:11px;font-weight:400;line-height:1.2;"
                'pointer-events:none">'
                f"{count / total:.1%}</span>",
            )
        angle += sweep
    return "".join(labels)


def _quality_charts(review: Mapping, year: int) -> str:
    """Render the preceding and current year assessment pie charts.

    Returns:
        Paired native pies with their coverage date in one introduction.
        Inner percentages and legends retain native chart behavior.
    """
    records = [
        row
        for row in review.get("quality_statistics") or []
        if row["year"] in {year - 1, year}
    ]
    charts = []
    provisional = False
    for row in records:
        observed = [
            (code, meta)
            for code, meta in QUALITY_GRADES.items()
            if row["counts"].get(code, 0)
        ]
        if not observed:
            continue
        labels = [
            f"{meta[0]}：{row['counts'][code]}" for code, meta in observed
        ]
        legend_width = min(220, max(80, max(map(len, labels)) * 7.5 + 32))
        plot_width = _PIE_TOTAL_WIDTH - 28 - legend_width - _PIE_TEXT_PADDING
        counts = [row["counts"][code] for code, _ in observed]
        provisional = provisional or row["provisional"]
        charts.append(
            '<div style="display:inline-block;max-width:100%;'
            "overflow-x:auto;"
            'vertical-align:top;text-align:center">\n'
            f'<div style="width:{_PIE_TOTAL_WIDTH}px;'
            'position:relative;line-height:0">\n'
            "{{Graph:Chart\n"
            f"| type = pie | width = {plot_width:g} | height = {_PIE_HEIGHT}\n"
            f"| title = {row['year']}年條目品質概況\n"
            "| x = "
            + ", ".join(labels)
            + "\n| y1 = "
            + ", ".join(map(str, counts))
            + "\n| colors = "
            + ", ".join(meta[1] for _, meta in observed)
            + "\n| legend = true | showValues = false\n}}"
            + _pie_percent_labels(counts, plot_width)
            + "\n</div></div>",
        )
    if not charts:
        return ""
    return (
        _annual_intro(
            review,
            "各年度條目品質分布如下：",
            partial=provisional,
        )
        + '\n<div class="annual-quality-pies">\n'
        + "\n".join(charts)
        + "\n</div>"
    )


def _assessment_prose(review: Mapping, year: int) -> str:
    """Keep the reference's year-end grade share comparisons.

    Returns:
        Observed grade shares and complete missing-data sentences.
    """
    records = review.get("quality_statistics") or []
    current = next(
        (row["counts"] for row in records if row["year"] == year),
        None,
    )
    previous = next(
        (row["counts"] for row in records if row["year"] == year - 1),
        None,
    )

    def share(counts: Mapping | None, grades: tuple[str, ...]) -> float | None:
        """Compute a share from complete observed grade counts.

        Returns:
            Percentage or unavailable for absent/empty totals.
        """
        if not counts or not sum(counts.values()):
            return None
        return sum(counts[key] for key in grades) / sum(counts.values()) * 100

    def comparison(label: str, grades: tuple[str, ...]) -> str:
        """Describe observed shares and year-on-year direction.

        Returns:
            A complete share sentence without invented comparisons.
        """
        after, before = share(current, grades), share(previous, grades)
        if after is None:
            return f"{label}占比資料待補充。"
        sentence = f"{label}占比{after:.1f}%"
        if before is None:
            return sentence + "，去年占比資料待補充。"
        change = round(after - before, 1)
        if change == 0:
            return sentence + "，與去年持平。"
        direction = "上升" if change > 0 else "下降"
        return sentence + f"，較去年{direction}{abs(change):.1f}個百分點。"

    b_now = sum(current[key] for key in ("b", "bl")) if current else None
    b_before = sum(previous[key] for key in ("b", "bl")) if previous else None
    b_sentence = (
        f"乙級內容今年共有{_count(b_now)}篇"
        if b_now is not None
        else "乙級內容今年的數量待補充"
    )
    b_sentence += (
        f"，去年為{_count(b_before)}篇。"
        if b_before is not None
        else "，去年數量待補充。"
    )
    return (
        "條目質量方面，"
        + comparison("小作品級內容", ("stub", "sl"))
        + comparison("初級內容", ("start", "list"))
        + comparison("丙級內容", ("c", "cl"))
        + b_sentence
    )


def _article_growth_prose(
    review: Mapping,
    year: int,
    current: int | None,
    previous: int | None,
) -> str:
    """Describe observed article totals and their year-on-year change.

    Args:
        review: Saved annual observations and actual collection dates.
        year: Calendar year being reviewed.
        current: Observed article total, or none when unavailable.
        previous: Previous year-end total, or none when unavailable.

    Returns:
        Natural growth prose with explicit missing-data sentences.
    """
    delta = (
        current - previous
        if type(current) is int and type(previous) is int
        else None
    )
    start = dt.date(year, 1, 1)
    year_days = (dt.date(year + 1, 1, 1) - start).days
    data_through = (
        str(review["overview_observed_at"])[:10]
        if review.get("overview_observed_at")
        else None
    )
    boundary = _annual_data_through(review, data_through=data_through)
    elapsed_days = (
        min(year_days, max(0, (boundary - start).days + 1))
        if boundary is not None
        else 0
    )
    days = year_days if review.get("complete") else elapsed_days or None
    marker = (
        _annual_as_of(
            review,
            data_through=data_through,
        )
        if not review.get("complete")
        else ""
    )
    timing = f"截至{year}年年末" if review.get("complete") else "本年度統計中"
    if current is None:
        introduction = "本年度條目總數與增長統計待補充。"
        if previous is not None:
            introduction += f"去年年末共有{_count(previous)}篇條目。"
    elif previous is None:
        introduction = (
            f"{timing}，電子遊戲專題共有{_count(current)}篇條目。"
            "去年年末的條目總數與本年度增長統計待補充。"
        )
    elif delta == 0:
        introduction = (
            f"{timing}，電子遊戲專題共有{_count(current)}篇條目，"
            f"與去年年末的{_count(previous)}篇持平"
            + (
                "，日均淨增長為0.00篇。"
                if days is not None
                else "。日均增長統計待補充。"
            )
        )
    else:
        direction = "增長" if delta > 0 else "減少"
        introduction = (
            f"{timing}，電子遊戲專題共有{_count(current)}篇條目，"
            f"較去年年末的{_count(previous)}篇淨{direction}"
            f"{_count(abs(delta))}篇"
            + (
                f"，日均淨{direction}{abs(delta) / days:.2f}篇。"
                if days is not None
                else "。日均增長統計待補充。"
            )
        )
    return introduction + marker


def _annual_ranking(
    rows: list[str] | None,
    *,
    introduction: str,
    missing: str,
    empty: str,
    marker: str = "",
) -> str:
    """Render rankings while preserving unavailable observations.

    Args:
        rows: Rendered ranking entries, or none when unavailable.
        introduction: Prose introducing an observed nonempty ranking.
        missing: Complete sentence for unavailable observations.
        empty: Complete sentence for an observed empty ranking.
        marker: Inline coverage mark attached to the list introduction.

    Returns:
        Ranking columns or the corresponding missing-data sentence.
    """
    if rows is None:
        return missing
    return (
        (introduction if rows else empty)
        + marker
        + "\n{{div col|colwidth=25em}}\n"
        + ("\n".join(rows) if rows else "* （無）")
        + "\n{{div col end}}"
    )


def _article_report(  # ruff: ignore[too-many-locals]
    review: Mapping,
    year: int,
) -> str:
    """Render annual reference prose and measured popularity rankings.

    Returns:
        Formula, quality charts, monthly winners, and two top-25 lists.
        Incomplete observations retain their measured date intervals.
    """
    current = (review.get("overview") or {}).get("total")
    previous = (review.get("previous_overview") or {}).get("total")
    records = review.get("quality_statistics") or []
    if previous is None:
        baseline = next((r for r in records if r["year"] == year - 1), None)
        if baseline:
            previous = sum(baseline["counts"].values())
    days = (dt.date(year + 1, 1, 1) - dt.date(year, 1, 1)).days
    parts = [
        _article_growth_prose(review, year, current, previous),
        _assessment_prose(review, year),
        _quality_charts(review, year),
        _annual_intro(
            review,
            f"以下列出{year}年各月日均瀏覽量最高的條目：",
        ),
    ]
    months = {r["month"]: r for r in review.get("monthly_popularity") or []}
    monthly = []
    for month in range(1, _MONTHS + 1):
        row = months.get(month)
        text = (
            _link(row["title"])
            + f"<small>（日均{row['views'] / row['days']:,.0f}次浏览）</small>"
            if row
            else "本月資料待補充。"
        )
        label = f"{month}月"
        if row and row.get("revision_id"):
            label = _link(
                f"Special:PermaLink/{row['revision_id']}#月瀏覽量",
                label,
            )
        monthly.append(f"* {label}：{text}")
    parts.append(
        "{{div col|colwidth=25em}}\n"
        + "\n".join(monthly)
        + "\n{{div col end}}",
    )
    popularity = review.get("annual_popularity")
    period_label = (
        "在統計期間內"
        if popularity and popularity.get("provisional")
        else f"{year}年"
    )
    count = popularity.get("article_count") if popularity else current
    total = popularity.get("total_views") if popularity else None
    interval_days = (
        (
            dt.date.fromisoformat(popularity["end"])
            - dt.date.fromisoformat(popularity["start"])
        ).days
        + 1
        if popularity
        else days
    )
    if type(count) is int and type(total) is int:
        popularity_prose = (
            f"{period_label}，专题{count / 10000:.2f}万篇条目的总浏览量为"
            f"{total / 10000:,.0f}万，"
            f"日均总浏览量为{total / interval_days / 10000:.1f}万。"
        )
    elif type(total) is int:
        popularity_prose = (
            f"{period_label}，專題總瀏覽量為{total / 10000:,.0f}萬，"
            f"日均總瀏覽量為{total / interval_days / 10000:.1f}萬。"
            "參與統計的條目數待補充。"
        )
    elif type(count) is int:
        popularity_prose = (
            f"{period_label}，參與瀏覽量統計的條目共有{count:,}篇。"
            "總瀏覽量與日均瀏覽量待補充。"
        )
    else:
        popularity_prose = "本年度瀏覽量統計待補充。"
    ranking_period = (
        f"{popularity['start']}至{popularity['end']}統計期間內"
        if popularity
        else "本年度"
    )
    parts.append(  # ruff: ignore[repeated-append]
        popularity_prose
        + "\n\n"
        + _annual_ranking(
            [
                f"# {_link(row['title'])}：{row['views']:,}次浏览"
                f"<small>（日均{row['views'] / interval_days:,.0f}次）</small>"
                for row in popularity["items"][:25]
            ]
            if popularity is not None
            else None,
            introduction=(
                f"以下列出{ranking_period}瀏覽量最多的條目，至多25篇；"
                "前200名可參見"
                "[[WikiProject:电子游戏/热门条目#年瀏覽量|熱門條目頁]]："
            ),
            missing="本年度瀏覽量排行待補充。",
            empty="本年度沒有可列出的瀏覽量排行紀錄。",
            marker=(
                _annual_as_of(review, data_through=popularity["end"])
                if popularity and popularity.get("provisional")
                else ""
            ),
        ),
    )
    parts.append(
        _annual_ranking(
            [
                f"# {_link(row['title'])}：{row['revisions']:,}笔修订"
                for row in review["revision_ranking"][:25]
            ]
            if review.get("revision_ranking") is not None
            else None,
            introduction="以修訂次數統計，以下列出本年度排名前25名的條目：",
            missing="本年度修訂次數排行待補充。",
            empty="本年度沒有可列出的修訂次數紀錄。",
            marker=_annual_as_of(review) if not review.get("complete") else "",
        ),
    )
    return "\n\n".join(part for part in parts if part)


def _awards(review: Mapping) -> str:
    """List every supplied annual score and verified newcomer marker.

    Returns:
        Blank manual honors followed by the ACG score list.
    """
    newcomers = set(review.get("newcomers") or [])
    awards = review.get("awards")
    if awards is None:
        summary = "本年度ACG專題獎得分統計待補充。"
    else:
        summary = f"年内共有{len(awards)}名编辑获得ACG专题奖得分。"
        if awards and review.get("newcomers") is None:
            summary += "首次得分者與首次獲獎人數待補充。"
        elif awards:
            first_awards = sum(
                row["total"] >= _AWARD_THRESHOLD
                for row in awards
                if row["user"] in newcomers
            )
            summary += (
                f"其中{len(newcomers)}名編者首次得分，"
                f"{first_awards}名首次達到10分。"
            )
    lines = [
        "社群表彰：",
        "本年度社群表彰資料待補充。",
        "",
        summary,
        "",
    ]
    if awards:
        lines.extend((
            "年度得分名單如下，本年度首次得分者以綠色「N」字標記。",
            "",
        ))
    lines.extend((
        _annual_intro(review, "ACG專題獎年度得分名單如下："),
        "{{div col|colwidth=25em}}",
    ))
    for row in review.get("awards") or []:
        marker = " {{color|green|N}}" if row["user"] in newcomers else ""
        gained = (
            f"共得{_count(row['gained'])}分"
            if type(row.get("gained")) is int
            else "本年度得分待補充。"
        )
        total = (
            f"<small>（累計分數{_count(row['total'])}分）</small>"
            if type(row.get("total")) is int
            else "；累計分數待補充。"
        )
        lines.append(
            f"# {_link('User:' + row['user'], row['user'])}："
            + gained
            + total
            + marker,
        )
    if not awards:
        lines.append("年度得分名單待補充。" if awards is None else "* （無）")
    lines.append("{{div col end}}")
    return "\n".join(lines)


def _quality_history_chart(review: Mapping) -> str:
    """Render annual cohorts using native chart parameters.

    Returns:
        Centered native parameters with year-only labels and ordered
        grade groups, without expanded SVG or custom overlays.
    """
    rows = review.get("quality_year_ends")
    if not rows:
        return ""
    frame = (
        pl
        .DataFrame(rows)
        .unnest("grades")
        .with_columns(
            (pl.col("FA") + pl.col("FL")).alias("featured"),
            (pl.col("A") + pl.col("AL")).alias("a"),
        )
    )
    groups = tuple(
        group
        for group in (
            ("featured", "典范级", QUALITY_GRADES["fa"][1]),
            ("a", "甲级", QUALITY_GRADES["a"][1]),
            ("GA", "优良级", QUALITY_GRADES["ga"][1]),
            ("Bplus", "乙上级", QUALITY_GRADES["bplus"][1]),
        )
        if group[0] != "a" or (frame["a"] > 0).any()
    )
    labels = frame["year"].cast(pl.String)
    parameters = [
        "{{Graph:Chart",
        "| type = stackedrect | width = 600 | height = 300",
        "| xType = string | x = " + ", ".join(labels),
    ]
    for index, (key, label, _color) in enumerate(groups, start=1):
        values = ", ".join(str(value) for value in frame[key])
        parameters.append(f"| y{index} = {values} | y{index}Title = {label}")
    parameters.extend((
        "| colors = " + ", ".join(color for _key, _label, color in groups),
        "| legend = true | interactive = true | animate = true",
        "| xAxisAngle = -30 | xAxisTitle = 年份",
        (
            "| yAxisTitle = 入選篇數 | yType = integer "
            "| yAxisMin = 0 | yGrid = true"
        ),
        "}}",
    ))
    return (
        _annual_intro(review, "歷年高品質條目入選數量如下：")
        + '\n<div class="center" '
        'style="max-width:100%;overflow-x:auto;text-align:center">\n'
        '<div style="min-width:800px">\n'
        + "\n".join(parameters)
        + "\n</div></div>"
    )


def _milestone_charts(review: Mapping, year: int) -> tuple[str, str]:
    """Extend audited GA and DYK charts with observed annual milestones.

    Returns:
        Annual quality additions and quarterly/current cumulative DYK.
    """
    quality = _quality_history_chart(review)
    dyk = [
        (day, count) for day, count in HISTORICAL_DYK if day[:4] < str(year)
    ]
    if year == _DYK_BASELINE_YEAR + 1:
        dyk.extend(
            (row["date"], HISTORICAL_DYK[-1][1] + row["count"])
            for row in review.get("dyk_new_quarters") or []
        )
        current = review.get("dyk_current")
        if current is not None and (not dyk or current["date"] > dyk[-1][0]):
            dyk.append((
                current["date"],
                HISTORICAL_DYK[-1][1] + current["count"],
            ))
    dyk = [(day, count) for day, count in dyk if day[:4] <= str(year)]
    chart = (
        "{{Graph:Chart | type = area | width = 400 | height = 250 "
        "| xType = date | x = "
        + ", ".join(day for day, _ in dyk)
        + " | y1 = "
        + ", ".join(str(count) for _, count in dyk)
        + " | colors = #b4bbff | xAxisTitle = 日期 | yAxisTitle = 條目數 }}"
    )
    return quality, (
        _annual_intro(
            review,
            "歷年新條目推薦累計數量如下：",
            partial=(
                not review.get("complete")
                or bool(dyk and dyk[-1][0] < f"{year}-12-31")
            ),
            data_through=dyk[-1][0] if dyk else None,
        )
        + "\n{{Image frame\n| align = right | width = 400\n| content = "
        + chart
        + "\n| caption = 電子遊戲專題累計新條目推薦數量\n}}"
    )


def _quality_summary(review: Mapping, year: int) -> str:
    """Summarize annual quality cohorts and their measured change.

    Returns:
        Linked positive quality groups or full-sentence placeholders.
        Comparisons require both yearly totals to be observed.
    """
    history = review.get("quality_year_ends")
    counts = next(
        (row["grades"] for row in history or [] if row["year"] == year),
        None,
    )
    previous = next(
        (
            sum(row["grades"].values())
            for row in history or []
            if row["year"] == year - 1
        ),
        None,
    )
    if history == []:
        counts = dict.fromkeys(_ANNUAL_QUALITY_GRADES, 0)
        previous = 0
    total = sum(counts.values()) if counts is not None else None
    changes = review.get("quality_changes")
    if history is None and changes is not None:
        observation = (
            pl
            .from_dicts(
                changes,
                schema={
                    "title": pl.String,
                    "grade": pl.String,
                    "action": pl.String,
                },
            )
            .filter(
                (pl.col("action") == "added")
                & pl.col("grade").is_in(_ANNUAL_QUALITY_GRADES),
            )
            .select(
                pl.col("title").n_unique().alias("total"),
                *(
                    pl
                    .col("title")
                    .filter(pl.col("grade") == grade)
                    .n_unique()
                    .alias(grade)
                    for grade in _ANNUAL_QUALITY_GRADES
                ),
            )
            .row(0, named=True)
        )
        total = observation.pop("total")
        counts = observation
    if counts is None:
        summary = f"{year}年度高品質條目的總數及各級內容數量待補充。"
    else:
        groups = (
            (sum(counts[grade] for grade in grades), label)
            for grades, label in _ANNUAL_QUALITY_GROUPS
        )
        details = "、".join(
            label + _count(count) + "篇" for count, label in groups if count
        )
        summary = f"{year}年度，專題共誕生高品質條目{_count(total)}篇"
        summary += ("，其中" + details if details else "") + "。"
    if not review.get("complete"):
        summary += _annual_as_of(review)
    if total is None or previous is None:
        return summary
    difference = total - previous
    if difference:
        direction = "增加" if difference > 0 else "減少"
        comparison = (
            f"較去年{_count(previous)}篇{direction}"
            f"{_count(abs(difference))}篇。"
        )
    else:
        comparison = f"與去年{_count(previous)}篇持平。"
    return summary + "\n\n" + comparison


def _dyk_summary(review: Mapping, year: int) -> str:
    """Keep the annual reference's numerical DYK introduction.

    Returns:
        Distinct annual DYK counts without invented prior observations.
    """
    rows = review.get("dyk")
    summary = "[[WikiProject:电子游戏/认证条目/DYK|新條目推薦]]方面，"
    if rows is None:
        summary += f"{year}年的首頁展示統計待補充。"
    else:
        count = (
            pl
            .from_dicts(rows, schema={"title": pl.String})
            .select(pl.col("title").n_unique())
            .item()
        )
        summary += f"{year}年有{count}篇条目登上首页展示。"
    previous = review.get("previous_dyk_count")
    summary += (
        f"去年共有{_count(previous)}篇條目登上首頁展示。"
        if type(previous) is int
        else "去年的首頁展示統計待補充。"
    )
    return summary + (
        "\n\n" + _annual_intro(review, "本年度新條目推薦名單如下：")
        if rows is not None
        else ""
    )


def _annual(issue: Issue, review: Mapping) -> str:
    """Build the annual reference layout with blank editorial prose.

    Returns:
        Annual wikitext without introductions or editor comments.
    """
    year = issue.year - 1
    quality_chart, dyk_chart = _milestone_charts(review, year)
    quality_list, author_table = _quality(review)
    if quality_list:
        quality_list = (
            _annual_intro(review, "誕生條目具體如下：") + "\n" + quality_list
        )
    elif review.get("quality_changes") is not None:
        quality_list = "本年度名單未收錄新增的高品質條目。"
    else:
        quality_list = "本年度高品質條目的具體名單待補充。"
    quality_section = "\n\n".join(
        part
        for part in (
            _quality_summary(review, year),
            quality_chart,
            quality_list,
            "本年度高品質條目的題材分布待補充。",
            _annual_intro(review, "作者名單如下：") + "\n" + author_table,
        )
        if part
    )
    sections = [
        _invitation(issue),
        (
            f"== 專題報導兼訪談：{year}年年終回顧 ==\n"
            "{{WikiProject:電子遊戲/簡訊/年终回顾导航模板}}\n"
            ": {{small|資料整理者待補。}}"
        ),
        "=== 引言 ===",
        "==== 內容評審 ====\n\n" + quality_section,
        "==== 新章推薦 ====\n\n"
        + dyk_chart
        + "\n\n"
        + _dyk_summary(review, year)
        + "\n\n"
        + _dyk(review, year),
        "==== 条目报告 ====\n\n" + _article_report(review, year),
        "==== 榮譽表彰 ====\n\n" + _awards(review),
        _invitation(issue),
        (
            "[[Category:電子遊戲專題簡訊報導]]\n"
            "[[Category:電子遊戲專題簡訊訪談]]"
        ),
    ]
    return _banner(issue) + "\n\n".join(sections)


def render_editorial(issue: Issue, facts: Mapping) -> dict[str, str]:
    """Prepare editorial templates or January's annual feature.

    Args:
        issue: Validated publication month.
        facts: Prepared issue facts with an optional annual observation.

    Returns:
        Wikitext by suffix; January has no separate interview page.
    """
    if issue.month == 1:
        return {
            "專題": render_annual_feature(
                issue,
                facts.get("year_review") or {},
            ),
        }
    return {
        suffix: _banner(issue)
        + f"== {heading}：待定 ==\n\n<!-- 在此編寫{heading}。 -->\n\n"
        + f"[[Category:電子遊戲專題簡訊{category}]]"
        for suffix, heading, category in (
            ("專題", "專題報導", "報導"),
            ("訪談", "訪談", "訪談"),
        )
    }


def render_annual_feature(issue: Issue, year_review: Mapping) -> str:
    """Render January's annual page independently of quarterly drafts.

    Args:
        issue: January publication reviewing the preceding year.
        year_review: Saved annual observations supplied by the backend.

    Returns:
        Complete original feature wikitext ready for daily capture.

    Raises:
        ValueError: The publication is not a January issue.
    """
    if issue.month != 1:
        message = "Annual features require a January issue."
        raise ValueError(message)
    return _annual(issue, year_review)
