"""Construct English key-article reports without publishing wiki edits.

Render backend-prepared rows and change metadata while preserving page
surroundings and the distinct importance and quality section layouts.
"""

from __future__ import annotations

import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, override

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Heading

from backend.services.enwiki.key_articles import ReportDefinition
from shared.reports import ReportResult
from shared.support.regions import replace_by_tag
from updater.support.edit_summary import (
    MAX_EDIT_SUMMARY_BYTES,
    EditSummary,
    article_mention,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from backend.services.enwiki.article_changes import ArticleChanges
    from backend.services.enwiki.key_articles import ReportData


REPORT_HEADER = "{{PJ:VG/DBR/EN/header}}"


REPORT_ITEM_TEMPLATE = "PJ:VG/DBR/EN/item"


REPORT_FOOTER = "{{PJ:VG/DBR/EN/footer}}"


@dataclass(frozen=True)
class ReportSpec(ABC):
    """Define selection and layout behavior for one English report.

    Attributes:
        name: Short identifier used to key report data and text.
        filter_column: Row column used to select report members.
        accepted_values: Values included in the report.
        count_markers: Pairs of row values and page counter markers.
        requires_listing_dates: Whether preparation needs English dates.
    """

    name: str
    filter_column: str
    accepted_values: tuple[str, ...]
    count_markers: tuple[tuple[str, str], ...]
    requires_listing_dates: ClassVar[bool] = False

    membership_region: ClassVar[str | None] = None

    @property
    def definition(self) -> ReportDefinition:
        """Backend data requirements without layout behavior.

        Returns:
            Selection, membership range, and source date requirements.
        """
        return ReportDefinition(
            self.name,
            self.filter_column,
            self.accepted_values,
            self.membership_region,
            self.requires_listing_dates,
        )

    @abstractmethod
    def render_body(self, rows_frame: pl.DataFrame) -> str:
        """Render selected rows with this report's section structure.

        Args:
            rows_frame: Prepared article rows belonging to this report.

        Returns:
            Complete generated body without surrounding page content.
        """

    @abstractmethod
    def build_edit_summary(
        self,
        changes: ArticleChanges,
        linked_titles: Mapping[int, str],
    ) -> str:
        """Describe membership changes using this report's semantics.

        Args:
            changes: Backend-prepared membership and class differences.
            linked_titles: Chinese sitelinks for report members.

        Returns:
            Bounded edit summary with report-specific change labels.
        """


class ImportanceReportSpec(ReportSpec):
    """Render importance sections and membership changes."""

    @override
    def render_body(self, rows_frame: pl.DataFrame) -> str:
        """Group importance report rows under alphabetical headings.

        Args:
            rows_frame: Selected importance report rows.

        Returns:
            Alphabetical report sections without listing-date comments.
        """
        return render_body(rows_frame)

    @override
    def build_edit_summary(
        self,
        changes: ArticleChanges,
        linked_titles: Mapping[int, str],
    ) -> str:
        """Describe importance membership additions and removals.

        Args:
            changes: Backend-prepared membership and class differences.
            linked_titles: Chinese sitelinks for report members.

        Returns:
            Bounded membership summary without quality action labels.
        """
        return build_edit_summary(changes, linked_titles)


class QualityReportSpec(ReportSpec):
    """Render dated quality sections and English class transitions."""

    requires_listing_dates: ClassVar[bool] = True
    membership_region: ClassVar[str | None] = "body"

    @override
    def render_body(self, rows_frame: pl.DataFrame) -> str:
        """Group quality rows by listing year with unknown dates last.

        Args:
            rows_frame: Selected quality rows with listing dates.

        Returns:
            Dated quality sections with ISO comments for known dates.
        """
        return render_quality_body(rows_frame)

    @override
    def build_edit_summary(
        self,
        changes: ArticleChanges,
        linked_titles: Mapping[int, str],
    ) -> str:
        """Describe English quality listings and class transitions.

        Args:
            changes: Backend-prepared membership and class differences.
            linked_titles: Chinese sitelinks for report members.

        Returns:
            Bounded summary identifying quality-specific changes.
        """
        return build_edit_summary(
            changes,
            linked_titles,
            quality=True,
        )


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


def report_result(data: ReportData, spec: ReportSpec) -> ReportResult:
    """Retain comparison data and one report's selected members.

    Args:
        data: Prepared English/Chinese rows used by the renderer.
        spec: Importance or quality selection for one target.

    Returns:
        Complete rows, selected members, counts, and provenance.
    """
    members = filter_report_rows(data.rows, spec)
    return ReportResult(
        family="enwp_key_articles",
        tables={"prepared_rows": data.rows, "members": members},
        metadata={
            "selection": spec.name,
            "filter_column": spec.filter_column,
            "accepted_values": list(spec.accepted_values),
            "counts": count_report_rows(members, spec),
        },
        provenance={
            "sources": ["enwiki", "zhwiki", "wikidatawiki"],
            "collection": "Wiki Replica and English listing history",
        },
        calculated_at=data.calculated_at,
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
    """Update the managed body and configured assessment counters.

    Args:
        old_text: Existing page wikitext with body and counter markers.
        body: Newly rendered report body.
        counts: Article counts keyed by assessment value.
        spec: Report configuration.

    Returns:
        Updated total, assessment counts, and report body, preserving
        text outside the configured managed ranges.
    """
    new_text = replace_by_tag(
        "count_all",
        f"{sum(counts.values()):,}",
        old_text,
    )
    for value, marker_name in spec.count_markers:
        new_text = replace_by_tag(
            marker_name,
            f"{counts.get(value, 0):,}",
            new_text,
        )
    return replace_body(new_text, body)


def format_summary_article(
    english_title: str,
    chinese_title: str | None,
) -> str:
    """Quote an English title and parenthesize its Chinese counterpart.

    Args:
        english_title: English Wikipedia article title.
        chinese_title: Chinese Wikipedia title, when one exists. An
            empty title omits the Chinese link.

    Returns:
        Quoted English interwiki link followed by an optional Chinese
        companion link in parentheses, without additional guillemets.
    """
    return article_mention(
        english_title,
        interwiki="en",
        companion=chinese_title,
    )


def build_edit_summary(
    changes: ArticleChanges,
    item_to_zh_title: Mapping[int, str],
    max_bytes: int = MAX_EDIT_SUMMARY_BYTES,
    *,
    quality: bool = False,
) -> str:
    """Format backend change metadata as one bounded edit summary.

    Args:
        changes: Backend-prepared identities and class differences.
        item_to_zh_title: Wikidata item IDs mapped to zhwiki titles.
        max_bytes: Requested UTF-8 summary budget, capped at 255 bytes.
        quality: Whether to describe English quality-class changes.

    Returns:
        Total count followed by added and removed article links. Renames
        retaining their page ID keep only the count. Quality reports
        describe GA listings and delistings and FA/FL promotions and
        removals, including earlier classes for known transitions.
        Importance and Chinese assessment changes are ignored. Uncached
        entries use their Wikidata item or title. Chinese links require
        a Chinese Wikipedia sitelink.
    """
    count = changes.current_total
    noun = "item" if count == 1 else "items"
    summary = EditSummary(f"{count:,} {noun} total.", max_bytes=max_bytes)
    if quality:
        return _build_quality_summary(
            summary,
            changes,
            item_to_zh_title,
        )
    added = [
        format_summary_article(
            article.english_title,
            item_to_zh_title.get(article.item_id),
        )
        for article in changes.added
    ]
    removed = [
        format_summary_article(
            article.english_title,
            item_to_zh_title.get(article.item_id),
        )
        for article in changes.removed
    ]

    summary.add_group("Added ", added, group_separator=" ")
    summary.add_group(
        "removed " if added else "Removed ",
        removed,
        group_separator="; " if added else " ",
    )
    return summary.render()


def _build_quality_summary(
    summary: EditSummary,
    changes: ArticleChanges,
    item_to_zh_title: Mapping[int, str],
) -> str:
    """Describe quality membership and known class transitions.

    Args:
        summary: Builder containing the total and byte budget.
        changes: Backend-prepared quality additions and transitions.
        item_to_zh_title: Chinese sitelinks for article mentions.

    Returns:
        Summary grouped by English class and quality action. Matched
        entries without a stored class do not imply a promotion.
    """
    actions = {
        "GA": ("listed", "delisted"),
        "FA": ("prompted", "removed"),
        "FL": ("prompted", "removed"),
    }
    groups: dict[tuple[str | None, bool], list[str]] = {}
    for change in changes.quality_changes:
        old_article = change.previous
        new_article = change.current
        article = format_summary_article(
            new_article.english_title,
            item_to_zh_title.get(new_article.item_id),
        )
        if old_article is not None and old_article.en_class in actions:
            article += f" (from {old_article.en_class})"
        en_class = (
            new_article.en_class if new_article.en_class in actions else None
        )
        groups.setdefault((en_class, True), []).append(article)

    for old_article in changes.removed:
        article = format_summary_article(
            old_article.english_title,
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


def replace_body(source: str, new_body: str) -> str:
    """Replace the report body while retaining both marker comments.

    Args:
        source: Existing report page wikitext.
        new_body: Complete replacement report body.

    Returns:
        Updated wikitext with a newline on each side of the report body.
    """
    return replace_by_tag("body", f"\n{new_body}\n", source)


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
            spec.render_body(rows),
            count_report_rows(rows, spec),
            spec,
        )
    return texts
