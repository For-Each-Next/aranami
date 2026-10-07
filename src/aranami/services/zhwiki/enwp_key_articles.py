"""Construct English key-article reports without publishing wiki edits.

Reports share replica lookups and preserve page surroundings,
article membership summaries, Chinese labels, alphabetical importance
groups, and dated quality groups.
"""

from __future__ import annotations

import html
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Heading, Wikilink

from aranami.services.enwiki import quality_listing_dates
from aranami.sources.quarry import enwp
from aranami.support.edit_summary import MAX_EDIT_SUMMARY_BYTES, EditSummary
from aranami.support.templates import template_page
from aranami.support.wikitext import region_content, replace_by_tag

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite


REPORT_HEADER = "{{PJ:VG/DBR/EN/header}}"


REPORT_ITEM_TEMPLATE = "PJ:VG/DBR/EN/item"


REPORT_FOOTER = "{{PJ:VG/DBR/EN/footer}}"


@dataclass(frozen=True)
class ReportSpec:
    """Describe one ENWP key-article report.

    Attributes:
        name: Short identifier used to key report data and text.
        filter_column: Row column used to select report members.
        accepted_values: Values included in the report.
        count_markers: Pairs of row values and page counter markers.
    """

    name: str
    filter_column: str
    accepted_values: tuple[str, ...]
    count_markers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class OldArticle:
    """Represent an article found in an existing report page.

    Attributes:
        english_title: English Wikipedia article title.
        item_id: Numeric Wikidata item identifier, when available.
        page_id: Stable English Wikipedia page ID, when known locally.
        en_class: Stored English quality class, when available.
    """

    english_title: str
    item_id: int | None
    page_id: int | None = None
    en_class: str | None = None


class _ArticleMembership:
    """Match stable page IDs and legacy report identities."""

    def __init__(self, articles: Mapping[str, OldArticle]) -> None:
        """Index stable IDs and fallback identities for legacy rows.

        Args:
            articles: Report articles keyed by their English title.
        """
        self.articles = articles
        self.by_page_id = {
            article.page_id: article
            for article in articles.values()
            if article.page_id is not None
        }
        self.by_item_id = {
            article.item_id: article
            for article in articles.values()
            if article.item_id is not None
        }
        self.legacy_by_item_id = {
            article.item_id: article
            for article in articles.values()
            if article.page_id is None and article.item_id is not None
        }

    def contains(self, article: OldArticle) -> bool:
        """Check membership by stable identity across title changes.

        Args:
            article: Candidate article to compare with this report.

        Returns:
            Whether the same article occurs in the indexed report.
        """
        return self.find(article) is not None

    def find(self, article: OldArticle) -> OldArticle | None:
        """Return the article with the same stable or legacy identity.

        Args:
            article: Candidate article to compare with this report.

        Returns:
            Matching article, or None when no identity matches. Known
            conflicting page IDs never fall back to a Wikidata ID.
        """
        previous = self.articles.get(article.english_title)
        if article.page_id is not None:
            match = self.by_page_id.get(
                article.page_id,
            ) or self.legacy_by_item_id.get(article.item_id)
        elif article.item_id is not None:
            match = self.by_item_id.get(article.item_id)
        else:
            return previous
        if match is not None:
            return match
        if (
            previous is not None
            and previous.page_id is None
            and previous.item_id is None
        ):
            return previous
        return None


@dataclass(frozen=True)
class ReportData:
    """Keep shared report data without publication destinations.

    Attributes:
        rows: Enriched current English article rows.
        old_articles: Previous article memberships keyed by report kind.
        linked_titles: Current and previous articles' Chinese sitelinks.
    """

    rows: pl.DataFrame
    old_articles: Mapping[str, Mapping[str, OldArticle]]
    linked_titles: Mapping[int, str]


def english_sort_key(title: str) -> str:
    """Return a case-insensitive, accent-insensitive sort key.

    Args:
        title: Article title or configured sort value.

    Returns:
        Normalized sortable text.
    """
    normalized = unicodedata.normalize("NFKD", title)
    stripped = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return stripped.casefold()


def heading_key(title: str) -> str:
    """Return the alphabetical report heading for a title.

    Args:
        title: Article sort value.

    Returns:
        An uppercase Latin letter or the fallback number heading.
    """
    normalized = english_sort_key(title).lstrip()
    if not normalized:
        return "#"

    first = normalized[0].upper()
    if "A" <= first <= "Z":
        return first
    return "#"


def safe_text(value: object, default: str = "无") -> str:
    """Convert a value to non-empty text or return a default.

    Args:
        value: Value to convert.
        default: Fallback for missing or empty values.

    Returns:
        Nonempty text or the supplied fallback.
    """
    if value is None:
        return default

    text_value = str(value)
    return text_value or default


def escape_template_value(value: object, default: str = "无") -> str:
    """Escape characters that would alter template parameter parsing.

    Args:
        value: Value to encode.
        default: Fallback for missing or empty values.

    Returns:
        Text safe to use in an individual template parameter.
    """
    text_value = safe_text(value, default=default)
    return (
        text_value
        .replace("|", "&#124;")
        .replace("=", "&#61;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
    )


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


def filter_report_rows(
    rows_frame: pl.DataFrame,
    spec: ReportSpec,
) -> pl.DataFrame:
    """Select rows belonging to one report.

    Args:
        rows_frame: Shared enriched article rows.
        spec: Report configuration.

    Returns:
        Rows whose configured field matches an accepted value.
    """
    if rows_frame.is_empty():
        return rows_frame

    return rows_frame.filter(
        pl.col(spec.filter_column).is_in(spec.accepted_values),
    )


def count_report_rows(
    rows_frame: pl.DataFrame,
    spec: ReportSpec,
) -> dict[str, int]:
    """Count report rows by the configured assessment values.

    Args:
        rows_frame: Filtered article rows.
        spec: Assessment values to count.

    Returns:
        A count for each accepted assessment value.
    """
    if rows_frame.is_empty():
        return dict.fromkeys(spec.accepted_values, 0)
    counts = dict(rows_frame.group_by(spec.filter_column).len().iter_rows())
    return {value: counts.get(value, 0) for value in spec.accepted_values}


def build_report_item_template(
    row: Mapping[str, object],
) -> mwparserfromhell.nodes.Template:
    """Build one report item template from an article row.

    Args:
        row: Enriched article fields.

    Returns:
        The editable report item template.
    """
    template = mwparserfromhell.nodes.Template(
        REPORT_ITEM_TEMPLATE,
    )
    template.add(
        "1",
        escape_template_value(row["display_title"]),
        showkey=False,
        preserve_spacing=False,
    )
    template.add(
        "2",
        escape_template_value(row["final_zh_class"]),
        showkey=False,
        preserve_spacing=False,
    )
    template.add(
        "3",
        escape_template_value(row["final_zh_importance"]),
        showkey=False,
        preserve_spacing=False,
    )
    template.add(
        "en",
        escape_template_value(row["en_title"], default=""),
        preserve_spacing=False,
    )
    template.add(
        "en_disp",
        escape_template_value(
            row["en_display_title"],
            default="",
        ),
        preserve_spacing=False,
    )
    template.add(
        "en_cls",
        escape_template_value(row["en_class"], default=""),
        preserve_spacing=False,
    )
    template.add(
        "en_imp",
        escape_template_value(
            row["en_importance"],
            default="",
        ),
        preserve_spacing=False,
    )
    template.add(
        "wd",
        str(int(row["item_id"])),
        preserve_spacing=False,
    )
    return template


def render_item(row: Mapping[str, object]) -> str:
    """Render one bulleted report item.

    Args:
        row: Enriched article fields.

    Returns:
        One bulleted item in the established report format.
    """
    return f"* {build_report_item_template(row)}"


def render_body(rows_frame: pl.DataFrame) -> str:
    """Render an alphabetically grouped report body.

    Args:
        rows_frame: Enriched article rows to render.

    Returns:
        Wikitext grouped by normalized alphabetical headings.
    """
    if rows_frame.is_empty():
        return ""
    # Build templates and Unicode sort keys at the text boundary.
    # Polars handles ordering, grouping, and concatenation.
    items = pl.DataFrame([
        {
            "heading": heading_key(str(row["sort_value"])),
            "sort_key": english_sort_key(str(row["sort_value"])),
            "title_key": english_sort_key(str(row["en_title"])),
            "item": render_item(row),
        }
        for row in rows_frame.iter_rows(named=True)
    ])
    groups = (
        items
        .sort("heading", "sort_key", "title_key")
        .group_by("heading", maintain_order=True)
        .agg(pl.col("item").str.join("\n"))
    )
    return "\n\n".join(
        f"== {heading} ==\n{REPORT_HEADER}\n{items}\n{REPORT_FOOTER}"
        for heading, items in groups.iter_rows()
    )


def render_quality_body(rows_frame: pl.DataFrame) -> str:
    """Render quality articles by their latest English listing date.

    Args:
        rows_frame: Enriched quality rows with nullable listing dates.

    Returns:
        Year sections in descending date order, with a date comment
        after each dated item and unresolved dates in the final section.
        Same-day and unresolved entries use English title sort keys.
    """
    if rows_frame.is_empty():
        return ""
    items = pl.DataFrame(
        [
            {
                "quality_date": row.get("quality_date"),
                "sort_key": english_sort_key(str(row["sort_value"])),
                "title_key": english_sort_key(str(row["en_title"])),
                "item": render_item(row),
            }
            for row in rows_frame.iter_rows(named=True)
        ],
        schema={
            "quality_date": pl.Date,
            "sort_key": pl.String,
            "title_key": pl.String,
            "item": pl.String,
        },
    ).with_columns(pl.col("quality_date").dt.year().alias("year"))
    groups = (
        items
        .sort(
            "quality_date",
            "sort_key",
            "title_key",
            descending=[True, False, False],
            nulls_last=True,
        )
        .with_columns(
            pl.concat_str(
                pl.col("item"),
                pl
                .when(pl.col("quality_date").is_not_null())
                .then(
                    pl.concat_str(
                        pl.lit("<!-- "),
                        pl.col("quality_date").dt.strftime("%Y-%m-%d"),
                        pl.lit(" -->"),
                    ),
                )
                .otherwise(pl.lit("")),
            ).alias("item"),
        )
        .group_by("year", maintain_order=True)
        .agg(pl.col("item").str.join("\n"))
    )
    return "\n\n".join(
        f"{Heading(f' {year}年 ' if year else ' 年份未知 ', 2)}"
        f"\n\n{REPORT_HEADER}\n{items}\n{REPORT_FOOTER}"
        for year, items in groups.iter_rows()
    )


def update_page_text(
    old_text: str,
    body: str,
    counts: Mapping[str, int],
    spec: ReportSpec,
) -> str:
    """Update the managed body and applicable importance counters.

    Args:
        old_text: Existing page wikitext.
        body: Newly rendered report body.
        counts: Article counts keyed by assessment value.
        spec: Report configuration.

    Returns:
        Updated page wikitext. Quality reports preserve all surrounding
        text and require only body markers.
    """
    if spec.name == "quality":
        return replace_body(old_text, body)
    new_text = replace_marker_value(
        old_text,
        "count_all",
        f"{sum(counts.values()):,}",
    )
    for value, marker_name in spec.count_markers:
        new_text = replace_marker_value(
            new_text,
            marker_name,
            f"{counts.get(value, 0):,}",
        )
    return replace_body(new_text, body)


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

    for template, page_id in _templates_with_page_ids(wikicode):
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
            page_id=page_id,
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


def _templates_with_page_ids(
    code: Wikicode,
) -> Iterator[tuple[mwparserfromhell.nodes.Template, int | None]]:
    """Yield templates with page IDs from their following HTML comments.

    Args:
        code: Root or nested wikitext containing report items.

    Yields:
        Each template and its adjacent stable page ID, when available.
    """
    for index, node in enumerate(code.nodes):
        if isinstance(node, mwparserfromhell.nodes.Template):
            page_id = None
            following = code.nodes[index + 1 : index + 2]
            if following and isinstance(
                following[0],
                mwparserfromhell.nodes.Comment,
            ):
                marker = str(following[0].contents).strip()
                prefix = 'aranami-enwp page-id="'
                if marker.startswith(prefix) and marker.endswith('"'):
                    value = marker[len(prefix) : -1]
                    if value.isdigit() and int(value) > 0:
                        page_id = int(value)
            yield node, page_id
        for child in node.__children__():
            yield from _templates_with_page_ids(child)


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


def format_summary_article(
    english_title: str,
    chinese_title: str | None,
) -> str:
    """Link an English title and bracket its Chinese counterpart.

    Args:
        english_title: English Wikipedia article title.
        chinese_title: Chinese Wikipedia title, when one exists. An
            empty title omits the Chinese link.

    Returns:
        Quoted English interwiki link followed by an optional Chinese
        link in square brackets. The brackets use character references
        so the Chinese link remains valid wikitext.
    """
    english_link = f"«{Wikilink(':en:' + english_title)}»"
    if not chinese_title:
        return english_link
    return f"{english_link} &#91;{Wikilink(chinese_title)}&#93;"


def encode_length(value: str) -> int:
    """Return the UTF-8 byte length of a string.

    Args:
        value: Text to measure.

    Returns:
        Number of UTF-8 bytes.
    """
    return len(value.encode("utf-8"))


def truncate_summary_parts(
    parts: Sequence[str],
    max_bytes: int,
) -> str:
    """Fit summary parts within a UTF-8 byte limit.

    Args:
        parts: Complete summary clauses.
        max_bytes: Maximum allowed UTF-8 byte length.

    Returns:
        Joined clauses, with an omission count when truncated.
    """
    return (
        EditSummary("", max_bytes=max_bytes)
        .add_group("", parts, separator="; ", group_separator="")
        .render()
    )


def build_edit_summary(
    old_articles: Mapping[str, OldArticle],
    new_rows: pl.DataFrame,
    item_to_zh_title: Mapping[int, str],
    max_bytes: int = MAX_EDIT_SUMMARY_BYTES,
    *,
    quality: bool = False,
) -> str:
    """Build a count and membership-change summary for one report.

    Args:
        old_articles: Existing report articles by English title.
        new_rows: Newly generated report rows.
        item_to_zh_title: Wikidata item IDs mapped to zhwiki titles.
        max_bytes: Requested UTF-8 summary budget, capped at 255 bytes.
        quality: Whether to describe English quality-class changes.

    Returns:
        Total count followed by added and removed article links. Renames
        retaining their page ID keep only the count. Quality reports
        describe GA listings and delistings and FA/FL promotions and
        removals, including earlier classes for known transitions.
        Importance and Chinese assessment changes are ignored. Legacy
        entries use their Wikidata item or title. Chinese links require
        a Chinese Wikipedia sitelink.
    """
    new_articles = {
        str(row["en_title"]): OldArticle(
            english_title=str(row["en_title"]),
            item_id=int(row["item_id"]),
            page_id=(
                int(row["page_id"]) if row.get("page_id") is not None else None
            ),
            en_class=(str(row.get("en_class") or "").strip().upper() or None),
        )
        for row in new_rows.to_dicts()
    }
    count = len(new_articles)
    noun = "item" if count == 1 else "items"
    summary = EditSummary(f"{count:,} {noun} total.", max_bytes=max_bytes)
    if quality:
        return _build_quality_summary(
            summary,
            old_articles,
            new_articles,
            item_to_zh_title,
        )
    old_membership = _ArticleMembership(old_articles)
    new_membership = _ArticleMembership(new_articles)
    added: list[str] = []
    for english_title in sorted(
        new_articles,
        key=english_sort_key,
    ):
        new_article = new_articles[english_title]
        if old_membership.contains(new_article):
            continue
        article = format_summary_article(
            english_title,
            item_to_zh_title.get(new_article.item_id),
        )
        added.append(article)

    removed: list[str] = []
    for english_title in sorted(
        old_articles,
        key=english_sort_key,
    ):
        old_article = old_articles[english_title]
        if new_membership.contains(old_article):
            continue
        chinese_title = None
        if old_article.item_id is not None:
            chinese_title = item_to_zh_title.get(
                old_article.item_id,
            )
        article = format_summary_article(
            english_title,
            chinese_title,
        )
        removed.append(article)

    summary.add_group("Added ", added, group_separator=" ")
    summary.add_group(
        "removed " if added else "Removed ",
        removed,
        group_separator="; " if added else " ",
    )
    return summary.render()


def _build_quality_summary(
    summary: EditSummary,
    old_articles: Mapping[str, OldArticle],
    new_articles: Mapping[str, OldArticle],
    item_to_zh_title: Mapping[int, str],
) -> str:
    """Describe quality membership and known class transitions.

    Args:
        summary: Builder containing the total and byte budget.
        old_articles: Previous members and stored English classes.
        new_articles: Current members and English classes.
        item_to_zh_title: Chinese sitelinks for article mentions.

    Returns:
        Summary grouped by English class and quality action. Matched
        legacy entries without a stored class do not imply a promotion.
    """
    actions = {
        "GA": ("listed", "delisted"),
        "FA": ("prompted", "removed"),
        "FL": ("prompted", "removed"),
    }
    old_membership = _ArticleMembership(old_articles)
    new_membership = _ArticleMembership(new_articles)
    groups: dict[tuple[str | None, bool], list[str]] = {}
    for english_title in sorted(new_articles, key=english_sort_key):
        new_article = new_articles[english_title]
        old_article = old_membership.find(new_article)
        if old_article is not None and (
            old_article.en_class is None
            or old_article.en_class == new_article.en_class
        ):
            continue
        article = format_summary_article(
            english_title,
            item_to_zh_title.get(new_article.item_id),
        )
        if old_article is not None and old_article.en_class in actions:
            article += f" (from {old_article.en_class})"
        en_class = (
            new_article.en_class if new_article.en_class in actions else None
        )
        groups.setdefault((en_class, True), []).append(article)

    for english_title in sorted(old_articles, key=english_sort_key):
        old_article = old_articles[english_title]
        if new_membership.contains(old_article):
            continue
        article = format_summary_article(
            english_title,
            item_to_zh_title.get(old_article.item_id),
        )
        en_class = (
            old_article.en_class if old_article.en_class in actions else None
        )
        groups.setdefault((en_class, False), []).append(article)

    for added in (True, False):
        for en_class, verbs in actions.items():
            summary.add_group(
                f"{en_class} {verbs[0 if added else 1]} ",
                groups.get((en_class, added), []),
                group_separator="; ",
            )
        summary.add_group(
            "Added " if added else "Removed ",
            groups.get((None, added), []),
            group_separator="; ",
        )
    return summary.render()


def replace_marker_value(
    source: str,
    name: str,
    new_value: str,
) -> str:
    """Replace content between named marker comments.

    Counter markers also accept the legacy repeated ``begin`` spelling
    for their closing marker. Body markers require ``end``.

    Args:
        source: Existing page wikitext with marker comments.
        name: Marker name to locate.
        new_value: Replacement wikitext between the comments.

    Returns:
        Wikitext with the first matching region replaced.

    Raises:
        ValueError: If a complete marker pair cannot be found.
    """  # ruff: ignore[docstring-extraneous-exception]
    return replace_by_tag(
        name,
        new_value,
        source,
        legacy_begin="begin",
        allow_repeated_begin=name != "body",
    )


def replace_body(source: str, new_body: str) -> str:
    """Replace the report body while retaining both marker comments.

    Args:
        source: Existing report page wikitext.
        new_body: Complete replacement report body.

    Returns:
        Updated wikitext with a newline on each side of the report body.
    """
    return replace_marker_value(source, "body", f"\n{new_body}\n")


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
    specs: Sequence[ReportSpec],
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
        ValueError: If a quality report has no managed body region.
    """
    old_articles = {}
    for spec in specs:
        text = existing[spec.name]
        if spec.name == "quality":
            body = region_content(text, "body", legacy_begin="begin")
            if body is None:
                message = "Quality reports require a managed body region."
                raise ValueError(message)
            text = body
        old_articles[spec.name] = parse_old_articles(text, site)
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
    if any(spec.name == "quality" for spec in specs):
        rows = rows.join(
            quality_listing_dates.listing_dates(articles),
            on="page_id",
            how="left",
        )
    return ReportData(
        rows=rows,
        old_articles=old_articles,
        linked_titles=build_item_to_zh_title(sitelinks),
    )


def build_reports(
    existing: Mapping[str, str],
    data: ReportData,
    specs: Sequence[ReportSpec],
) -> dict[str, str]:
    """Return updated report wikitext from shared prepared data.

    Args:
        existing: Existing report wikitext keyed by report kind.
        data: Shared article data prepared for the configured reports.
        specs: Report membership and marker configurations.

    Returns:
        Updated report wikitext keyed by report kind.
    """
    texts = {}
    for spec in specs:
        rows = filter_report_rows(data.rows, spec)
        texts[spec.name] = update_page_text(
            existing[spec.name],
            render_quality_body(rows)
            if spec.name == "quality"
            else render_body(rows),
            count_report_rows(rows, spec),
            spec,
        )
    return texts
