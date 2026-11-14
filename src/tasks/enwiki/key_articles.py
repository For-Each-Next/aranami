"""Prepare reusable English comparison data and interpret saved reports.

The runtime owns English and Wikidata source reads, assessment joins,
listing dates, and parsing of previous report members. Frontends receive
complete prepared rows without fetching or interpreting source pages.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from dataclasses import replace
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl

from data.article_changes import (
    NOMINATION_GRADES,
    OldArticle,
    compare_articles,
    compare_nominations,
)
from data.key_articles import PreparedPublication, ReportData, ReportDefinition
from data.reports import ReportStore, report_key
from data.runtime.report_membership import load_membership, save_membership
from shared.sources.quarry import enwp
from shared.support.regions import region_content
from shared.support.templates import template_page
from tasks.enwiki import quality_listing_dates, reviews as review_sources

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pywikibot.site import BaseSite

REPORT_ITEM_TEMPLATE = "PJ:VG/DBR/EN/item"
_LOGGER = logging.getLogger(__name__)


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


def _stored_articles(
    definition: ReportDefinition,
    site: BaseSite,
    title: str,
    original_text: str,
) -> dict[str, OldArticle] | None:
    """Read production identities only for the exact published content.

    Args:
        definition: Report kind identifying its retained snapshots.
        site: Destination wiki identity.
        title: Destination page title.
        original_text: Current live content, including during previews.

    Returns:
        Saved members, or none when neither snapshot matches live text.

    Raises:
        ValueError: Matching evidence has invalid identity or members.
    """
    database = site.dbName()
    key = report_key(
        "english-comparisons",
        database,
        title,
        variant=definition.name,
    )
    store = ReportStore()
    if not store.data_path(key).exists():
        return None
    with store.locked(key):
        for previous in (False, True):
            bundle = store.read(key, previous=previous, locked=True)
            if bundle is None or bundle.wikitext != original_text:
                continue
            if bundle.manifest[
                "family"
            ] != "enwp_key_articles" or bundle.manifest["target"] != {
                "site": database,
                "title": title,
            }:
                message = "Saved English comparison has an invalid identity."
                raise ValueError(message)
            members = _stored_identity_rows(bundle.tables, definition.name)
            if members is None:
                continue
            if members.is_empty():
                return {}
            if "en_title" not in members.columns:
                message = "Saved English comparison members lack titles."
                raise ValueError(message)
            articles = {}
            for row in members.iter_rows(named=True):
                name = row["en_title"]
                if not isinstance(name, str) or not name or name in articles:
                    message = "Saved English comparison titles are invalid."
                    raise ValueError(message)
                articles[name] = OldArticle(
                    name,
                    int(row["item_id"])
                    if row.get("item_id") is not None
                    else None,
                    int(row["page_id"])
                    if row.get("page_id") is not None
                    else None,
                    str(row.get("en_class") or "").strip().upper() or None,
                )
            return articles
    return None


def _stored_identity_rows(
    tables: Mapping[str, pl.DataFrame],
    kind: str,
) -> pl.DataFrame | None:
    """Include candidate identities without changing quality membership.

    Returns:
        Stored report rows and its separately retained candidate rows.
    """
    members = tables.get("members")
    if members is not None and kind == "quality" and "reviews" in tables:
        return pl.concat([members, tables["reviews"]], how="diagonal_relaxed")
    return members


def _nomination_baseline(
    definition: ReportDefinition,
    site: BaseSite,
    text: str,
    stored: Mapping[str, OldArticle],
    cached: Mapping[str, int | None],
) -> dict[str, dict[str, OldArticle]]:
    """Bind parsed candidate flags to validated prior report page IDs.

    Returns:
        Non-quality candidates keyed by kind with available stable IDs.
    """
    parsed = parse_old_nominations(
        region_content(text, definition.membership_region)
        if definition.membership_region is not None
        else text,
        site,
    )
    return {
        kind: {
            name: replace(
                article,
                page_id=(stored[name].page_id if name in stored else None)
                or cached.get(name),
            )
            for name, article in group.items()
        }
        for kind, group in parsed.items()
    }


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
    stored = _stored_articles(definition, site, title, original_text)
    baseline = (
        stored if stored is not None else data.old_articles[definition.name]
    )
    nominations = (
        _nomination_baseline(definition, site, original_text, baseline, cached)
        if definition.name == "quality"
        else {}
    )
    previous = {
        name: replace(article, page_id=article.page_id or cached.get(name))
        for name, article in baseline.items()
        if definition.name != "quality"
        or (
            article.en_class in {*definition.accepted_values, None}
            and not (
                article.en_class is None
                and any(name in group for group in nominations.values())
            )
        )
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
    candidates = (
        data.reviews if data.reviews is not None else data.rows.clear()
    )
    if definition.name == "quality":
        changes = replace(
            changes,
            nominations=compare_nominations(
                nominations,
                candidates,
                rows,
            ),
        )
    new_nominees = (
        data.reviews.height
        if definition.name == "quality" and data.reviews is not None
        else 0
    )
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
        {
            **_page_identities(rows),
            **(
                _page_identities(candidates)
                if definition.name == "quality"
                else {}
            ),
        },
        {
            name: article.page_id or current.get(name)
            for name, article in {
                **previous,
                **{
                    name: article
                    for group in nominations.values()
                    for name, article in group.items()
                },
            }.items()
        },
        new_nominees,
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
    if "en_reviews" not in en_frame.columns:
        en_frame = en_frame.with_columns(
            pl.lit(None, dtype=pl.String).alias("en_reviews"),
        )

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
            "en_reviews",
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
                .split("/", 1)[0]
                or None
                if template.has("en_cls")
                else None
            ),
        )

    return articles


def parse_old_nominations(
    page_text: str | None,
    site: BaseSite,
) -> dict[str, dict[str, OldArticle]]:
    """Read prior non-quality candidate kinds from the managed report.

    Args:
        page_text: Previously published candidate and quality sections.
        site: Destination site defining the exact report-item template.

    Returns:
        Earlier FAC/FLC/GAN candidates, keyed by kind and English title.

    Raises:
        ValueError: The caller-selected managed report region is absent.
    """
    if page_text is None:
        message = "Nomination comparison requires the managed report body."
        raise ValueError(message)
    groups = {kind: {} for kind, _grade in NOMINATION_GRADES}
    templates = mwparserfromhell.parse(page_text).filter_templates()
    if not templates:
        return groups
    expected = template_page(REPORT_ITEM_TEMPLATE, site)
    for template in templates:
        if (
            template_page(template.name, site) != expected
            or not template.has("en")
            or not template.has("en_cls")
        ):
            continue
        parts = [
            html.unescape(part).strip().upper()
            for part in str(template.get("en_cls").value).split("/")
        ]
        if not parts or parts[0] in {"FA", "FL", "GA"}:
            continue
        title = html.unescape(str(template.get("en").value)).strip()
        if not title:
            continue
        article = OldArticle(
            title,
            parse_item_id(html.unescape(str(template.get("wd").value)))
            if template.has("wd")
            else None,
            en_class=parts[0] or None,
        )
        for kind in parts[1:]:
            if kind in groups:
                groups[kind][title] = article
    return groups


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
    if "en_reviews" not in frame.columns:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.String).alias("en_reviews"),
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
            "en_reviews",
        )
    )


def prepare_reports(
    existing: Mapping[str, str],
    specs: Sequence[ReportDefinition],
    site: BaseSite,
    *,
    english_members: pl.DataFrame | None = None,
) -> ReportData:
    """Prepare shared article data from supplied report wikitext.

    Args:
        existing: Existing report wikitext keyed by report kind.
        specs: Report membership and marker configurations.
        site: Site providing template title and namespace rules.
        english_members: Completed daily English project page table.

    Returns:
        Shared rows and memberships for rendering and summaries.
        Quality reports include cached English listing dates; source
        reads refresh disposable date snapshots when needed.

    Raises:
        ValueError: A required managed body region is absent.
    """
    old_articles = {}
    old_ids = []
    for spec in specs:
        text = existing[spec.name]
        if spec.membership_region is not None:
            body = region_content(text, spec.membership_region)
            if body is None:
                message = "Reports require a managed body region."
                raise ValueError(message)
            text = body
        parsed = parse_old_articles(text, site)
        old_ids.extend(
            article.item_id
            for article in parsed.values()
            if article.item_id is not None
        )
        old_articles[spec.name] = (
            {
                name: article
                for name, article in parsed.items()
                if article.en_class in {*spec.accepted_values, None}
            }
            if spec.requires_listing_dates
            else parsed
        )
    _LOGGER.info("Collecting English comparison articles and assessments.")
    articles = normalize_en_pages(
        enwp.fetch_en_key_pages()
        if english_members is None
        else enwp.fetch_en_key_pages(english_members),
    )
    new_ids = articles.get_column("item_id").to_list()
    sitelinks = enwp.fetch_wikidata_sitelinks(sorted(set(new_ids + old_ids)))
    labels = enwp.fetch_wikidata_labels(new_ids)
    states = enwp.fetch_zh_page_states(
        sitelinks.get_column("zh_title").to_list(),
    )
    rows = build_enriched_rows(articles, sitelinks, labels, states)
    review_rows = None
    if any(spec.requires_listing_dates for spec in specs):
        rows = rows.join(
            quality_listing_dates.listing_dates(articles),
            on="page_id",
            how="left",
        )
        review_rows = rows.filter(
            pl.col("en_reviews").is_not_null()
            & pl.col("en_reviews").str.contains(
                r"(?:^|,)(?:FAC|FLC|GAN)(?:,|$)",
            )
            & ~pl.col("en_class").is_in(("FA", "FL", "GA")),
        )
        if not review_rows.is_empty():
            review_rows = review_rows.join(
                review_sources.review_dates(review_rows),
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
        reviews=review_rows,
    )
