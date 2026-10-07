"""Test English quality-class changes in the key-article summaries."""

from __future__ import annotations

from unittest import TestCase

import polars as pl
from wiki_fixtures import OfflineSite

from aranami.services.zhwiki import enwp_key_articles as reports


def _rows(*articles: tuple[str, int, int, str]) -> pl.DataFrame:
    """Build current rows with explicit identities and English classes.

    Args:
        articles: English title, item ID, page ID, and quality class.

    Returns:
        Current report rows, including an explicit empty schema.
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


class TestQualitySummaries(TestCase):
    """Name English quality actions for stable article identities."""

    @staticmethod
    def test_parse_stored_english_class() -> None:
        """Read classes from report templates and legacy rows."""
        parsed = reports.parse_old_articles(
            "{{PJ:VG/DBR/EN/item|en=Known|en_cls= ga |wd=Q1}}"
            '<!-- aranami-enwp page-id="10" -->\n'
            "{{PJ:VG/DBR/EN/item|en=Legacy|wd=2}}",
            OfflineSite("zh", "wikipedia"),
        )
        assert parsed == {
            "Known": reports.OldArticle("Known", 1, 10, "GA"),
            "Legacy": reports.OldArticle("Legacy", 2),
        }

    @staticmethod
    def test_new_english_classes_use_requested_verbs() -> None:
        """Name GA listings and FA and FL promotions independently."""
        summary = reports.build_edit_summary(
            {},
            _rows(("Good", 1, 10, "GA"), ("Article", 2, 20, "FA")),
            {},
            quality=True,
        )
        assert summary == (
            "2 items total. GA listed «[[:en:Good]]»; "
            "FA prompted «[[:en:Article]]»."
        )
        assert (
            reports.build_edit_summary(
                {},
                _rows(("List", 3, 30, "FL")),
                {},
                quality=True,
            )
            == "1 item total. FL prompted «[[:en:List]]»."
        )

    @staticmethod
    def test_removed_english_classes_use_previous_class() -> None:
        """Name GA delistings and FA and FL removals."""
        previous = {
            "Good": reports.OldArticle("Good", 1, 10, "GA"),
            "Article": reports.OldArticle("Article", 2, 20, "FA"),
            "List": reports.OldArticle("List", 3, 30, "FL"),
        }
        assert reports.build_edit_summary(
            previous,
            _rows(),
            {},
            quality=True,
        ) == (
            "0 items total. GA delisted «[[:en:Good]]»; "
            "FA removed «[[:en:Article]]»; FL removed «[[:en:List]]»."
        )

    @staticmethod
    def test_class_change_matches_page_id_after_rename() -> None:
        """Match one transition despite changed titles and item IDs."""
        previous = {"Before": reports.OldArticle("Before", 1, 10, "GA")}
        current = _rows(("After", 2, 10, "FA"))
        summary = reports.build_edit_summary(
            previous,
            current,
            {},
            quality=True,
        )
        assert summary == (
            "1 item total. FA prompted «[[:en:After]]» (from GA)."
        )
        assert reports.build_edit_summary(previous, current, {}) == (
            "1 item total."
        )

    @staticmethod
    def test_class_change_uses_wikidata_or_title_for_legacy_rows() -> None:
        """Find transitions when earlier rows have no stored page ID."""
        cases = (
            ({"Before": reports.OldArticle("Before", 1, en_class="GA")}, 1),
            ({"After": reports.OldArticle("After", None, en_class="GA")}, 2),
        )
        for previous, item_id in cases:
            assert (
                reports.build_edit_summary(
                    previous,
                    _rows(("After", item_id, 10, "FA")),
                    {},
                    quality=True,
                )
                == "1 item total. FA prompted «[[:en:After]]» (from GA)."
            )

    @staticmethod
    def test_class_change_mentions_current_chinese_sitelink() -> None:
        """Preserve bilingual links in transition mentions."""
        assert reports.build_edit_summary(
            {"Before": reports.OldArticle("Before", 1, 10, "GA")},
            _rows(("After", 1, 10, "FA")),
            {1: "後"},
            quality=True,
        ) == (
            "1 item total. FA prompted «[[:en:After]]» "
            "&#91;[[後]]&#93; (from GA)."
        )

    @staticmethod
    def test_same_class_ignores_renames_and_other_assessments() -> None:
        """Ignore importance and Chinese assessment changes."""
        current = _rows(("After", 2, 10, "GA")).with_columns(
            pl.lit("Top").alias("en_importance"),
            pl.lit("典范条目").alias("final_zh_class"),
            pl.lit("高").alias("final_zh_importance"),
        )
        assert (
            reports.build_edit_summary(
                {"Before": reports.OldArticle("Before", 1, 10, "GA")},
                current,
                {},
                quality=True,
            )
            == "1 item total."
        )

    @staticmethod
    def test_recreated_page_does_not_match_previous_page_id() -> None:
        """Report both memberships when a page is recreated in place."""
        assert reports.build_edit_summary(
            {"Same": reports.OldArticle("Same", 1, 10, "GA")},
            _rows(("Same", 1, 20, "GA")),
            {},
            quality=True,
        ) == (
            "1 item total. GA listed «[[:en:Same]]»; "
            "GA delisted «[[:en:Same]]»."
        )

    @staticmethod
    def test_unknown_previous_class_does_not_imply_promotion() -> None:
        """Keep legacy members quiet without a known baseline class."""
        previous = {"Before": reports.OldArticle("Before", 1, 10)}
        assert (
            reports.build_edit_summary(
                previous,
                _rows(("After", 1, 10, "FA")),
                {},
                quality=True,
            )
            == "1 item total."
        )
        assert (
            reports.build_edit_summary(
                previous,
                _rows(),
                {},
                quality=True,
            )
            == "0 items total. Removed «[[:en:Before]]»."
        )

    @staticmethod
    def test_quality_groups_preserve_utf8_budget() -> None:
        """Replace an oversized transition with an omission count."""
        summary_budget = 55
        summary = reports.build_edit_summary(
            {"Before": reports.OldArticle("Before", 1, 10, "GA")},
            _rows(("中文" * 200, 1, 10, "FA")),
            {1: "中" * 200},
            max_bytes=summary_budget,
            quality=True,
        )
        assert len(summary.encode()) <= summary_budget
        assert summary == "1 item total. FA prompted 1 more."
