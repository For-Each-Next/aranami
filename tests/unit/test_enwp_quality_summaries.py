"""Test English quality-class changes in the key-article summaries."""

from __future__ import annotations

from unittest import TestCase

import polars as pl
from wiki_fixtures import OfflineSite

from backend.services.enwiki import key_articles as data_service
from backend.services.enwiki.article_changes import compare_articles
from updater.services.zhwiki import enwp_key_articles as reports


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
        """Read template classes and ignore obsolete ID comments."""
        parsed = data_service.parse_old_articles(
            "{{PJ:VG/DBR/EN/item|en=Known|en_cls= ga |wd=Q1}}"
            '<!-- aranami-enwp page-id="10" -->\n'
            "{{PJ:VG/DBR/EN/item|en=Legacy|wd=2}}",
            OfflineSite("zh", "wikipedia"),
        )
        assert parsed == {
            "Known": data_service.OldArticle("Known", 1, en_class="GA"),
            "Legacy": data_service.OldArticle("Legacy", 2),
        }

    @staticmethod
    def test_new_english_classes_use_requested_verbs() -> None:
        """Name GA listings and FA and FL promotions independently."""
        summary = reports.build_edit_summary(
            compare_articles(
                {},
                _rows(("Good", 1, 10, "GA"), ("Article", 2, 20, "FA")),
            ),
            {},
            quality=True,
        )
        assert summary == (
            "2 articles: 1 FA, 0 FL, 1 GA. GA listed «[[:en:Good]]»; "
            "FA prompted «[[:en:Article]]»."
        )
        assert (
            reports.build_edit_summary(
                compare_articles({}, _rows(("List", 3, 30, "FL"))),
                {},
                quality=True,
            )
            == "1 article: 0 FA, 1 FL, 0 GA. FL prompted «[[:en:List]]»."
        )

    @staticmethod
    def test_removed_english_classes_use_previous_class() -> None:
        """Name GA delistings and FA and FL removals."""
        previous = {
            "Good": data_service.OldArticle("Good", 1, 10, "GA"),
            "Article": data_service.OldArticle("Article", 2, 20, "FA"),
            "List": data_service.OldArticle("List", 3, 30, "FL"),
        }
        assert reports.build_edit_summary(
            compare_articles(previous, _rows()),
            {},
            quality=True,
        ) == (
            "0 articles: 0 FA, 0 FL, 0 GA. GA delisted «[[:en:Good]]»; "
            "FA removed «[[:en:Article]]»; FL removed «[[:en:List]]»."
        )

    @staticmethod
    def test_class_change_matches_page_id_after_rename() -> None:
        """Match one transition despite changed titles and item IDs."""
        previous = {"Before": data_service.OldArticle("Before", 1, 10, "GA")}
        current = _rows(("After", 2, 10, "FA"))
        summary = reports.build_edit_summary(
            compare_articles(previous, current),
            {},
            quality=True,
        )
        assert summary == (
            "1 article: 1 FA, 0 FL, 0 GA. "
            "FA prompted «[[:en:After]]» (from GA)."
        )
        assert reports.build_edit_summary(
            compare_articles(previous, current),
            {},
        ) == ("1 article.")

    @staticmethod
    def test_class_change_uses_wikidata_or_title_without_cached_ids() -> None:
        """Find transitions when earlier rows have no stored page ID."""
        cases = (
            (
                {
                    "Before": data_service.OldArticle(
                        "Before",
                        1,
                        en_class="GA",
                    ),
                },
                1,
            ),
            (
                {
                    "After": data_service.OldArticle(
                        "After",
                        None,
                        en_class="GA",
                    ),
                },
                2,
            ),
        )
        for previous, item_id in cases:
            assert (
                reports.build_edit_summary(
                    compare_articles(
                        previous,
                        _rows(("After", item_id, 10, "FA")),
                    ),
                    {},
                    quality=True,
                )
                == "1 article: 1 FA, 0 FL, 0 GA. "
                "FA prompted «[[:en:After]]» (from GA)."
            )

    @staticmethod
    def test_class_change_mentions_current_chinese_sitelink() -> None:
        """Preserve bilingual links in transition mentions."""
        assert reports.build_edit_summary(
            compare_articles(
                {"Before": data_service.OldArticle("Before", 1, 10, "GA")},
                _rows(("After", 1, 10, "FA")),
            ),
            {1: "後"},
            quality=True,
        ) == (
            "1 article: 1 FA, 0 FL, 0 GA. "
            "FA prompted «[[:en:After]]» ([[後]]) (from GA)."
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
                compare_articles(
                    {"Before": data_service.OldArticle("Before", 1, 10, "GA")},
                    current,
                ),
                {},
                quality=True,
            )
            == "1 article: 0 FA, 0 FL, 1 GA."
        )

    @staticmethod
    def test_recreated_page_does_not_match_previous_page_id() -> None:
        """Report both memberships when a page is recreated in place."""
        assert reports.build_edit_summary(
            compare_articles(
                {"Same": data_service.OldArticle("Same", 1, 10, "GA")},
                _rows(("Same", 1, 20, "GA")),
            ),
            {},
            quality=True,
        ) == (
            "1 article: 0 FA, 0 FL, 1 GA. GA listed «[[:en:Same]]»; "
            "GA delisted «[[:en:Same]]»."
        )

    @staticmethod
    def test_unknown_previous_class_does_not_imply_promotion() -> None:
        """Keep matched members quiet without a known baseline class."""
        previous = {"Before": data_service.OldArticle("Before", 1, 10)}
        assert (
            reports.build_edit_summary(
                compare_articles(previous, _rows(("After", 1, 10, "FA"))),
                {},
                quality=True,
            )
            == "1 article: 1 FA, 0 FL, 0 GA."
        )
        assert (
            reports.build_edit_summary(
                compare_articles(previous, _rows()),
                {},
                quality=True,
            )
            == "0 articles: 0 FA, 0 FL, 0 GA. Removed «[[:en:Before]]»."
        )

    @staticmethod
    def test_quality_groups_preserve_utf8_budget() -> None:
        """Replace an oversized transition with an omission count."""
        summary_budget = 55
        summary = reports.build_edit_summary(
            compare_articles(
                {"Before": data_service.OldArticle("Before", 1, 10, "GA")},
                _rows(("中文" * 200, 1, 10, "FA")),
            ),
            {1: "中" * 200},
            max_bytes=summary_budget,
            quality=True,
        )
        assert len(summary.encode()) <= summary_budget
        assert summary == "1 article: 1 FA, 0 FL, 0 GA. FA prompted 1 more."
