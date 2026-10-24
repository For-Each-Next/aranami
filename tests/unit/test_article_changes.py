"""Verify backend membership and quality-class differences."""

from __future__ import annotations

from unittest import TestCase

import polars as pl

from backend.services.enwiki.article_changes import (
    OldArticle,
    QualityChange,
    compare_articles,
)


def _rows(*articles: tuple[str, int, int | None, str | None]) -> pl.DataFrame:
    """Build current observations with nullable identities and classes.

    Args:
        articles: English title, Wikidata item, page ID, and class.

    Returns:
        Current rows with a schema that also supports empty membership.
    """
    return pl.DataFrame(
        articles,
        schema={
            "en_title": pl.String,
            "item_id": pl.Int64,
            "page_id": pl.Int64,
            "en_class": pl.String,
        },
        orient="row",
    )


class TestArticleChanges(TestCase):
    """Distinguish membership changes from quality-class transitions."""

    @staticmethod
    def test_added_removed_and_current_total_are_source_metadata() -> None:
        """Return identities and classes without publication text."""
        removed = OldArticle("Before", 1, 10, "GA")
        current = OldArticle("After", 2, 20, "FA")
        changes = compare_articles(
            {"Before": removed},
            _rows(("After", 2, 20, "FA")),
        )
        assert changes.current_total == 1
        assert changes.added == (current,)
        assert changes.removed == (removed,)
        assert changes.quality_changes == (QualityChange(None, current),)

    @staticmethod
    def test_stable_page_id_suppresses_renames_and_item_changes() -> None:
        """Keep a renamed article despite a changed Wikidata item."""
        changes = compare_articles(
            {"Before": OldArticle("Before", 1, 10, "GA")},
            _rows(("After", 2, 10, "GA")),
        )
        assert changes.current_total == 1
        assert (
            changes.added == changes.removed == changes.quality_changes == ()
        )

    @staticmethod
    def test_known_class_transition_retains_previous_identity() -> None:
        """Record class transitions separately from membership."""
        previous = OldArticle("Before", 1, 10, "GA")
        current = OldArticle("After", 2, 10, "FA")
        changes = compare_articles(
            {"Before": previous},
            _rows(("After", 2, 10, "FA")),
        )
        assert changes.added == changes.removed == ()
        assert changes.quality_changes == (QualityChange(previous, current),)

    @staticmethod
    def test_recreated_page_does_not_match_title_or_wikidata() -> None:
        """Preserve replacement evidence when page IDs conflict."""
        previous = OldArticle("Same", 1, 10, "GA")
        current = OldArticle("Same", 1, 20, "GA")
        changes = compare_articles(
            {"Same": previous},
            _rows(("Same", 1, 20, "GA")),
        )
        assert changes.added == (current,)
        assert changes.removed == (previous,)
        assert changes.quality_changes == (QualityChange(None, current),)

    @staticmethod
    def test_missing_page_ids_allow_wikidata_matching() -> None:
        """Recognize renames when either page ID is missing."""
        for old_id, new_id in ((None, 10), (10, None), (None, None)):
            previous = OldArticle("Before", 1, old_id, "GA")
            current = OldArticle("After", 1, new_id, "FA")
            changes = compare_articles(
                {"Before": previous},
                _rows(("After", 1, new_id, "FA")),
            )
            assert changes.added == changes.removed == ()
            assert changes.quality_changes == (
                QualityChange(previous, current),
            )

    @staticmethod
    def test_title_matching_requires_an_unknown_previous_identity() -> None:
        """Use title fallbacks only without known identity conflicts."""
        current = _rows(("Same", 2, 10, "FA"))
        unknown = OldArticle("Same", None, en_class="GA")
        matched = compare_articles({"Same": unknown}, current)
        assert matched.added == matched.removed == ()
        assert matched.quality_changes[0].previous == unknown
        known = OldArticle("Same", 1, en_class="GA")
        replaced = compare_articles({"Same": known}, current)
        assert replaced.added == (OldArticle("Same", 2, 10, "FA"),)
        assert replaced.removed == (known,)

    @staticmethod
    def test_unknown_previous_class_does_not_establish_transition() -> None:
        """Suppress an inferred promotion without a known baseline."""
        previous = OldArticle("Before", 1, 10)
        changes = compare_articles(
            {"Before": previous},
            _rows(("After", 1, 10, "FA")),
        )
        assert (
            changes.added == changes.removed == changes.quality_changes == ()
        )
        assert compare_articles({"Before": previous}, _rows()).removed == (
            previous,
        )

    @staticmethod
    def test_current_classes_normalize_without_inventing_unknowns() -> None:
        """Retain newly unknown classes and normalize known values."""
        previous = OldArticle("Same", 1, 10, "GA")
        unknown = compare_articles(
            {"Same": previous},
            _rows(("Same", 1, 10, " ")),
        )
        assert unknown.quality_changes == (
            QualityChange(previous, OldArticle("Same", 1, 10)),
        )
        normalized = compare_articles({}, _rows(("New", 2, None, " ga ")))
        assert normalized.added == (OldArticle("New", 2, en_class="GA"),)

    @staticmethod
    def test_nonidentity_assessments_do_not_affect_differences() -> None:
        """Ignore importance and Chinese assessment metadata."""
        rows = _rows(("Same", 1, 10, "GA")).with_columns(
            pl.lit("Top").alias("en_importance"),
            pl.lit("FA").alias("final_zh_class"),
            pl.lit("High").alias("final_zh_importance"),
        )
        changes = compare_articles(
            {"Same": OldArticle("Same", 1, 10, "GA")},
            rows,
        )
        assert (
            changes.added == changes.removed == changes.quality_changes == ()
        )

    @staticmethod
    def test_ordering_deduplication_and_optional_current_fields() -> None:
        """Sort source metadata and retain the last duplicate title."""
        rows = pl.DataFrame({
            "en_title": ["Zulu", "Éclair", "Alpha", "Alpha"],
            "item_id": [1, 2, 3, 4],
        })
        changes = compare_articles({}, rows)
        expected_total = 3
        assert changes.current_total == expected_total
        assert changes.added == (
            OldArticle("Alpha", 4),
            OldArticle("Éclair", 2),
            OldArticle("Zulu", 1),
        )
        assert tuple(change.current for change in changes.quality_changes) == (
            changes.added
        )
        removed = compare_articles(
            {
                article.english_title: article
                for article in reversed(changes.added)
            },
            _rows(),
        )
        assert removed.current_total == 0
        assert removed.removed == changes.added

    @staticmethod
    def test_counts_keep_last_titles_and_distinguish_missing_columns() -> None:
        """Count English assessments without duplicate titles."""
        rows = _rows(
            ("Same", 1, 10, "GA"),
            ("Same", 1, 10, " fa "),
            ("List", 2, 20, "FL"),
            ("Unknown", 3, 30, None),
        ).with_columns(
            pl.Series("en_importance", ["High", "Top", "High", None]),
        )
        changes = compare_articles({}, rows)
        expected_total = 3
        assert changes.current_total == expected_total
        assert changes.quality_counts == (("FA", 1), ("FL", 1), ("GA", 0))
        assert changes.importance_counts == (("Top", 1), ("High", 1))
        empty = compare_articles({}, rows.head(0))
        assert empty.quality_counts == (("FA", 0), ("FL", 0), ("GA", 0))
        assert empty.importance_counts == (("Top", 0), ("High", 0))
        empty_counts_only = compare_articles(
            {},
            rows.head(0).select("en_class", "en_importance"),
        )
        assert empty_counts_only.quality_counts == empty.quality_counts
        assert empty_counts_only.importance_counts == empty.importance_counts
        missing = compare_articles({}, rows.select("en_title", "item_id"))
        assert missing.quality_counts == missing.importance_counts == ()
