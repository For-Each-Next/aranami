"""Build the manually requested video-game character report.

This report was not part of the routine notebook. Call ``build_report``
explicitly to obtain its list and assessment summary without publishing
to an invented destination or writing daily Pageviews data.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl
from mwparserfromhell.nodes import Template

from aranami.services.zhwiki.pageviews import aggregate_views
from aranami.sources.quarry.enwp import (
    fetch_wikidata_labels,
    fetch_wikidata_sitelinks,
)
from aranami.sources.quarry.projects import (
    query_pages_by_wikiproject,
    query_tags,
)
from aranami.sources.wikidata import fetch_franchises

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from pywikibot.site import BaseSite

CLASS_ORDER = (
    "典范",
    "特色列表",
    "优良",
    "乙",
    "乙级列表",
    "丙",
    "丙级列表",
    "初",
    "列表",
    "小作品",
    "小列表",
    "未评",
    "草稿",
)
ARTICLE_TAGS = (
    ("XFD", "main", "cat", "所有刪除候選"),
    ("CV", "main", "cat", "怀疑侵犯版权页面"),
    ("CSD", "main", "cat", "快速删除候选"),
    ("N", "main", "cat", "主題關注度不足的條目"),
    ("SUB", "main", "tl", "Substub"),
    ("INU", "main", "cat", "需要區分事實與虛構的條目"),
    ("RW", "main", "cat", "所有需擴充現實世界視角內容的條目"),
    ("FAN", "main", "tl", "Fanpov"),
    ("PLOT", "main", "cat", "需要清理剧情摘要的条目"),
    ("NOREF", "main", "tl", "Unreferenced"),
    ("FAC", "talk", "cat", "典範條目候選"),
    ("FLC", "talk", "cat", "特色列表候選"),
    ("GAN", "talk", "cat", "優良條目評選"),
    ("DYKC", "talk", "cat", "新條目推薦候選"),
    ("PR", "talk", "cat", "维基百科同行评审"),
    ("RA", "talk", "cat", "请求重新评级的电子游戏条目"),
    ("FFA", "talk", "cat", "已撤銷的典範條目"),
    ("FFA", "talk", "cat", "已撤销的特色列表"),
    ("DGA", "talk", "cat", "已撤銷的優良條目"),
    ("FFAC", "talk", "cat", "典範條目落選"),
    ("FGAN", "talk", "cat", "優良條目落選"),
)
_CHINESE_SITES = (
    "zhwiki",
    "zhwikibooks",
    "zhwikinews",
    "zhwikiquote",
    "zhwikisource",
    "zhwikiversity",
    "zhwikivoyage",
    "zhwiktionary",
)
_CHINESE_LANGUAGES = (
    "zh-cn",
    "zh-tw",
    "zh-hk",
    "zh-mo",
    "zh-my",
    "zh-sg",
    "zh-hans",
    "zh-hant",
    "zh",
)
_LOGGER = logging.getLogger(__name__)


def _franchise_names(item_ids: Sequence[int]) -> pl.DataFrame:
    """Prefer Chinese titles and labels before English and item IDs.

    Args:
        item_ids: Numeric franchise IDs selected by P8345 statements.

    Returns:
        Franchise IDs and their preferred display names.
    """
    frame = pl.DataFrame({"item_id": item_ids}, schema={"item_id": pl.Int64})
    candidates = (
        fetch_wikidata_sitelinks(item_ids, sites=_CHINESE_SITES).rename(
            {"zh_title": "chinese_title"},
        ),
        fetch_wikidata_labels(item_ids, languages=_CHINESE_LANGUAGES).rename(
            {"label": "chinese_label"},
        ),
        fetch_wikidata_sitelinks(item_ids, sites=("enwiki",)).rename(
            {"zh_title": "english_title"},
        ),
        fetch_wikidata_labels(item_ids, languages=("en",)).rename(
            {"label": "english_label"},
        ),
    )
    for candidate in candidates:
        frame = frame.join(candidate, on="item_id", how="left")
    return frame.select(
        pl.col("item_id").alias("franchise_qid"),
        pl.coalesce(
            "chinese_title",
            "chinese_label",
            "english_title",
            "english_label",
            pl.lit("d:Q") + pl.col("item_id").cast(pl.String),
        ).alias("franchise"),
    )


def render_report(frame: pl.DataFrame) -> tuple[str, str]:
    """Render character rows and an assessment-count template.

    Args:
        frame: Project page metadata with tags, totals, and franchises.

    Returns:
        The report list and its quality summary as wikitext.
    """
    summary = Template("PJ:VG/CHAR/summary")
    for assessment, count in (
        frame["pa_class"].value_counts(sort=True).iter_rows()
    ):
        summary.add(assessment, str(count), preserve_spacing=False)
    ordered = frame.sort(
        pl.col("defaultsort").fill_null(
            pl.col("page_title").str.replace_all("_", " ", literal=True),
        ),
    )
    items = [str(Template("PJ:VG/CHAR/header"))]
    for row in ordered.iter_rows(named=True):
        item = Template("PJ:VG/CHAR/item")
        parameters = {
            "1": row["full_title"],
            "2": row["pa_class"],
            "3": row["pa_importance"],
            "len": row["page_len"],
            "latest": row["latest_timestamp"],
            "tags": row["tags"],
            "views": row["views"],
            "qid": row["wikibase_qid"],
            "franchise": row["franchise"],
        }
        for name, value in parameters.items():
            item.add(
                name,
                "" if value is None else str(value),
                preserve_spacing=False,
            )
        items.append(str(item))
    items.append(str(Template("PJ:VG/CHAR/footer")))
    return "\n".join(items), str(summary)


def build_report(site: BaseSite, today: date) -> tuple[str, str]:
    """Build the character report with the previous 60 days' views.

    Args:
        site: Authenticated Chinese Wikipedia site.
        today: Exclusive UTC endpoint of the 60-day reporting period.

    Returns:
        Character report and assessment summary wikitext, without edits.
    """
    character_ids = query_pages_by_wikiproject(site, "虚构角色").select(
        "page_id",
    )
    members = (
        query_pages_by_wikiproject(site, "电子游戏")
        .join(character_ids.unique(), on="page_id", how="semi")
        .filter(pl.col("pa_class").is_in(CLASS_ORDER))
    )
    members = query_tags(site, members, ARTICLE_TAGS).with_columns(
        pl.col("tags").list.join(", "),
    )
    views = aggregate_views(
        site,
        members["full_title"].to_list(),
        [(today - timedelta(days=60), today)],
    ).select(pl.col("title").alias("full_title"), "views")
    franchises = fetch_franchises(
        site,
        members["wikibase_qid"].drop_nulls().unique().to_list(),
    )
    names = _franchise_names(franchises["franchise_qid"].unique().to_list())
    frame = members.join(views, on="full_title", how="left").join(
        franchises.join(names, on="franchise_qid", how="left").select(
            "wikibase_qid",
            "franchise",
        ),
        on="wikibase_qid",
        how="left",
    )
    _LOGGER.info(
        "Built manual character report for %d articles.",
        frame.height,
    )
    return render_report(frame)
