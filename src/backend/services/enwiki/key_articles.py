"""Prepare reusable English comparison data and interpret saved reports.

The backend owns English and Wikidata source reads, assessment joins,
listing dates, and parsing of previous report members. Frontends receive
complete prepared rows without fetching or interpreting source pages.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl

from backend.services.enwiki import quality_listing_dates
from backend.services.enwiki.article_changes import (
    ArticleChanges,
    OldArticle,
    compare_articles,
)
from backend.support.report_membership import (
    load_membership,
    save_membership,
)
from shared.sources.quarry import enwp
from shared.support.regions import region_content
from shared.support.templates import template_page

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pywikibot.site import BaseSite

REPORT_ITEM_TEMPLATE = "PJ:VG/DBR/EN/item"
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    """Configure backend selection and retained membership parsing.

    Attributes:
        name: Stable data key independent of the report's layout.
        filter_column: Prepared assessment field selecting members.
        accepted_values: Assessment values included in the report.
        membership_region: Managed range containing previous members,
            or none to retain whole-page membership interpretation.
        requires_listing_dates: Whether preparation needs listing dates.
    """

    name: str
    filter_column: str
    accepted_values: tuple[str, ...]
    membership_region: str | None = None
    requires_listing_dates: bool = False


@dataclass(frozen=True)
class ReportData:
    """Keep shared report data without publication destinations.

    Attributes:
        rows: Enriched current English article rows.
        old_articles: Previous article memberships keyed by report kind.
        linked_titles: Current and previous articles' Chinese sitelinks.
        calculated_at: Original UTC instant when rows were prepared.
    """

    rows: pl.DataFrame
    old_articles: Mapping[str, Mapping[str, OldArticle]]
    linked_titles: Mapping[int, str]
    calculated_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class PreparedPublication:
    """Keep selected facts, differences, and cache progression metadata.

    Attributes:
        rows: Complete current members selected for this report.
        changes: Identity and English quality differences from old rows.
        members: Current titles and IDs for confirmed live publication.
        preview_members: Original titles and available page IDs.
    """

    rows: pl.DataFrame
    changes: ArticleChanges
    members: Mapping[str, int | None]
    preview_members: Mapping[str, int | None]


def _page_identities(rows: pl.DataFrame) -> dict[str, int | None]:
    """Read prepared English page identities with explicit unknown IDs.

    Returns:
        English titles and known page IDs, or an empty mapping.
    """
    if rows.is_empty():
        return {}
    identifier = (
        pl.col("page_id")
        if "page_id" in rows.columns
        else pl.lit(None, dtype=pl.Int64)
    )
    return dict(
        rows.select("en_title", identifier.alias("page_id")).iter_rows(),
    )


def prepare_publication(
    data: ReportData,
    definition: ReportDefinition,
    site: BaseSite,
    title: str,
    original_text: str,
) -> PreparedPublication:
    """Compare source facts with validated retained report identities.

    Args:
        data: Shared current observations and parsed previous members.
        definition: Membership selection for this report.
        site: Destination wiki identity for its validated local cache.
        title: Caller-selected destination title.
        original_text: Current destination text validating the cache.

    Returns:
        Selected rows, differences, and publication identity metadata.
    """
    cached = load_membership(site, title, original_text)
    previous = {
        name: replace(article, page_id=cached.get(name) or article.page_id)
        for name, article in data.old_articles[definition.name].items()
    }
    rows = (
        data.rows
        if data.rows.is_empty()
        else data.rows.filter(
            pl.col(definition.filter_column).is_in(definition.accepted_values),
        )
    )
    current = _page_identities(data.rows)
    changes = compare_articles(previous, rows)
    _LOGGER.info(
        "Prepared changes for %s: %d current items, %d added, %d removed.",
        title,
        changes.current_total,
        len(changes.added),
        len(changes.removed),
    )
    return PreparedPublication(
        rows,
        changes,
        _page_identities(rows),
        {
            name: article.page_id or current.get(name)
            for name, article in previous.items()
        },
    )


def record_publication(  # ruff: ignore[too-many-arguments]
    prepared: PreparedPublication,
    site: BaseSite,
    title: str,
    original_text: str,
    text: str,
    *,
    confirmed: bool,
    dry: bool,
) -> None:
    """Retain identities for confirmed content or the preview baseline.

    Args:
        prepared: Backend-selected live and baseline identity mappings.
        site: Destination wiki identity.
        title: Caller-selected publication destination.
        original_text: Original content validated during preparation.
        text: Proposed destination content after confirmed publication.
        confirmed: Live content was unchanged or successfully saved.
        dry: Retain only original-text identities during a preview.
    """
    if dry:
        save_membership(site, title, original_text, prepared.preview_members)
    elif confirmed:
        save_membership(site, title, text, prepared.members)


def build_enriched_rows(
    en_frame: pl.DataFrame,
    sitelinks_frame: pl.DataFrame,
    labels_frame: pl.DataFrame,
    zh_frame: pl.DataFrame,
) -> pl.DataFrame:
    """Join shared article data used by both reports.

    Args:
        en_frame: Qualifying English Wikipedia article rows.
        sitelinks_frame: Wikidata Chinese sitelinks.
        labels_frame: Preferred Wikidata Chinese labels.
        zh_frame: Chinese Wikipedia assessment states.

    Returns:
        Enriched article rows ready for filtering and rendering.
    """
    if en_frame.is_empty():
        return en_frame

    return (
        en_frame
        .join(
            sitelinks_frame,
            on="item_id",
            how="left",
        )
        .join(
            labels_frame,
            on="item_id",
            how="left",
        )
        .join(
            zh_frame,
            on="zh_title",
            how="left",
        )
        .with_columns(
            pl
            .when(pl.col("zh_title").is_null())
            .then(pl.lit("请求"))
            .when(
                pl.col("zh_class").is_not_null()
                & (pl.col("zh_class").str.len_chars() > 0),
            )
            .then(pl.col("zh_class"))
            .when(pl.col("zh_is_redirect") == 1)
            .then(pl.lit("请求"))
            .otherwise(pl.lit("搁置"))
            .alias("final_zh_class"),
            pl
            .when(
                pl.col("zh_title").is_not_null()
                & pl.col("zh_class").is_not_null()
                & (pl.col("zh_class").str.len_chars() > 0),
            )
            .then(
                pl.coalesce(
                    [
                        pl.col("zh_importance"),
                        pl.lit("无"),
                    ],
                ),
            )
            .otherwise(pl.lit("无"))
            .alias("final_zh_importance"),
            pl.coalesce(
                [
                    pl.col("zh_title"),
                    pl.col("label"),
                    pl.col("en_title"),
                ],
            ).alias("display_title"),
        )
        .select(
            "page_id",
            "en_title",
            "en_display_title",
            "qid",
            "item_id",
            "sort_value",
            "en_class",
            "en_importance",
            "zh_title",
            "display_title",
            "final_zh_class",
            "final_zh_importance",
        )
    )


def parse_item_id(value: str) -> int | None:
    """Parse a numeric or Q-prefixed Wikidata item identifier.

    Args:
        value: Numeric or Q-prefixed identifier.

    Returns:
        Numeric item ID, or None for an invalid identifier.
    """
    normalized = value.strip().upper().removeprefix("Q")
    if not normalized.isdigit():
        return None
    return int(normalized)


def parse_old_articles(
    page_text: str,
    site: BaseSite,
) -> dict[str, OldArticle]:
    """Extract existing report articles from page wikitext.

    Args:
        page_text: Existing report page wikitext.
        site: Site providing template title and namespace rules.

    Returns:
        Existing articles keyed by English Wikipedia title.
    """
    expected_name = template_page(REPORT_ITEM_TEMPLATE, site)
    articles: dict[str, OldArticle] = {}
    wikicode = mwparserfromhell.parse(page_text)

    for template in wikicode.filter_templates():
        if template_page(template.name, site) != expected_name:
            continue
        if not template.has("en"):
            continue

        english_title = html.unescape(
            str(template.get("en").value).strip(),
        )
        if not english_title:
            continue

        item_id = None
        if template.has("wd"):
            item_id = parse_item_id(
                html.unescape(
                    str(template.get("wd").value),
                ),
            )
        articles[english_title] = OldArticle(
            english_title=english_title,
            item_id=item_id,
            en_class=(
                html
                .unescape(str(template.get("en_cls").value))
                .strip()
                .upper()
                or None
                if template.has("en_cls")
                else None
            ),
        )

    return articles


def build_item_to_zh_title(
    sitelinks_frame: pl.DataFrame,
) -> dict[int, str]:
    """Build a numeric Wikidata ID to zhwiki title mapping.

    Args:
        sitelinks_frame: Item IDs and available Chinese sitelinks.

    Returns:
        Chinese titles keyed by their numeric Wikidata IDs.
    """
    if sitelinks_frame.is_empty():
        return {}

    return {
        int(row["item_id"]): str(row["zh_title"])
        for row in sitelinks_frame.to_dicts()
        if row.get("zh_title")
    }


def normalize_en_pages(frame: pl.DataFrame) -> pl.DataFrame:
    """Normalize database titles and article display/sort fallbacks.

    Args:
        frame: Raw English article rows returned by the source adapter.

    Returns:
        Unique article rows with numeric item IDs and normalized titles.
    """
    if "page_id" not in frame.columns:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.Int64).alias("page_id"),
        )
    return (
        frame
        .with_columns(
            pl.col("en_title").str.replace_all("_", " ", literal=True),
            pl
            .col("qid")
            .str.strip_prefix("Q")
            .cast(pl.Int64)
            .alias("item_id"),
            pl.col("en_class", "en_importance").fill_null(""),
        )
        .with_columns(
            pl
            .when(
                pl.col("en_displaytitle").is_not_null()
                & (pl.col("en_displaytitle").str.len_chars() > 0),
            )
            .then(pl.col("en_displaytitle"))
            .otherwise(pl.col("en_title"))
            .alias("en_display_title"),
            pl
            .when(
                pl.col("en_defaultsort").is_not_null()
                & (pl.col("en_defaultsort").str.len_chars() > 0),
            )
            .then(pl.col("en_defaultsort"))
            .otherwise(pl.col("en_title"))
            .alias("sort_value"),
        )
        .unique("en_title", keep="last", maintain_order=True)
        .select(
            "page_id",
            "en_title",
            "en_display_title",
            "qid",
            "item_id",
            "sort_value",
            "en_class",
            "en_importance",
        )
    )


def prepare_reports(
    existing: Mapping[str, str],
    specs: Sequence[ReportDefinition],
    site: BaseSite,
) -> ReportData:
    """Prepare shared article data from supplied report wikitext.

    Args:
        existing: Existing report wikitext keyed by report kind.
        specs: Report membership and marker configurations.
        site: Site providing template title and namespace rules.

    Returns:
        Shared rows and memberships for rendering and summaries.
        Quality reports include cached English listing dates; source
        reads refresh disposable date snapshots when needed.

    Raises:
        ValueError: A required managed body region is absent.
    """
    old_articles = {}
    for spec in specs:
        text = existing[spec.name]
        if spec.membership_region is not None:
            body = region_content(text, spec.membership_region)
            if body is None:
                message = "Reports require a managed body region."
                raise ValueError(message)
            text = body
        old_articles[spec.name] = parse_old_articles(text, site)
    _LOGGER.info("Collecting English comparison articles and assessments.")
    articles = normalize_en_pages(enwp.fetch_en_key_pages())
    new_ids = articles.get_column("item_id").to_list()
    old_ids = [
        article.item_id
        for report in old_articles.values()
        for article in report.values()
        if article.item_id is not None
    ]
    sitelinks = enwp.fetch_wikidata_sitelinks(sorted(set(new_ids + old_ids)))
    labels = enwp.fetch_wikidata_labels(new_ids)
    states = enwp.fetch_zh_page_states(
        sitelinks.get_column("zh_title").to_list(),
    )
    rows = build_enriched_rows(articles, sitelinks, labels, states)
    if any(spec.requires_listing_dates for spec in specs):
        rows = rows.join(
            quality_listing_dates.listing_dates(articles),
            on="page_id",
            how="left",
        )
    _LOGGER.info(
        "Prepared %d English articles for %d comparison reports.",
        rows.height,
        len(specs),
    )
    return ReportData(
        rows=rows,
        old_articles=old_articles,
        linked_titles=build_item_to_zh_title(sitelinks),
        calculated_at=dt.datetime.now(dt.UTC),
    )
