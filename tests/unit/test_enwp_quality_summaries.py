"""Test English quality-class changes in the key-article summaries."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.article_changes import (
    ArticleChanges,
    QualityChange,
    compare_articles,
)
from tasks.enwiki import key_articles as data_service
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
    def test_retained_nominees_still_appear_in_summary_totals() -> None:
        """Count current candidates regardless of report additions."""
        members = _rows(("Existing quality", 1, 10, "GA")).with_columns(
            pl.lit("FAC").alias("en_reviews"),
        )
        candidates = _rows(
            ("Existing candidate", 2, 20, "B"),
            ("Other candidate", 3, 30, "C"),
        ).with_columns(pl.lit("GAN").alias("en_reviews"))
        data = data_service.ReportData(
            pl.concat([members, candidates]),
            {
                "quality": {
                    "Existing quality": data_service.OldArticle(
                        "Existing quality",
                        1,
                        10,
                        "GA",
                    ),
                },
            },
            {},
            reviews=candidates,
        )
        definition = data_service.ReportDefinition(
            "quality",
            "en_class",
            ("FA", "FL", "GA"),
        )
        original = (
            "{{PJ:VG/DBR/EN/item|en=Existing quality|wd=1|en_cls=GA/FAC}}"
            "{{PJ:VG/DBR/EN/item|en=Existing candidate|wd=2|en_cls=B/GAN}}"
            "{{PJ:VG/DBR/EN/item|en=Other candidate|wd=3|en_cls=C/GAN}}"
        )
        with (
            patch.object(data_service, "load_membership", return_value={}),
            patch.object(data_service, "_stored_articles", return_value=None),
        ):
            prepared = data_service.prepare_publication(
                data,
                definition,
                OfflineSite("zh", "wikipedia"),
                "Wikipedia:Quality",
                original,
            )
        assert not prepared.changes.added
        assert not prepared.changes.removed
        assert prepared.new_nominees == candidates.height
        assert (
            reports.build_edit_summary(
                prepared.changes,
                {},
                quality=True,
                new_nominees=prepared.new_nominees,
            )
            == "1 article: FA 0, FL 0, GA 1; 2 new nominees."
        )

    @staticmethod
    def test_empty_or_missing_nominees_omit_summary_suffix() -> None:
        """Keep zero candidates quiet after previous nominees leave."""
        members = _rows(("Existing quality", 1, 10, "GA"))
        definition = data_service.ReportDefinition(
            "quality",
            "en_class",
            ("FA", "FL", "GA"),
        )
        for candidates in (None, members.clear()):
            data = data_service.ReportData(
                members,
                {
                    "quality": {
                        "Existing quality": data_service.OldArticle(
                            "Existing quality",
                            1,
                            10,
                            "GA",
                        ),
                    },
                },
                {},
                reviews=candidates,
            )
            with (
                patch.object(data_service, "load_membership", return_value={}),
                patch.object(
                    data_service,
                    "_stored_articles",
                    return_value=None,
                ),
            ):
                prepared = data_service.prepare_publication(
                    data,
                    definition,
                    OfflineSite("zh", "wikipedia"),
                    "Wikipedia:Quality",
                    "{{PJ:VG/DBR/EN/item|en=Old candidate|wd=2|en_cls=B/GAN}}",
                )
            assert prepared.new_nominees == 0
            assert "nominee" not in reports.build_edit_summary(
                prepared.changes,
                {},
                quality=True,
                new_nominees=prepared.new_nominees,
            )

    @staticmethod
    def test_label_led_totals_and_compact_class_code() -> None:
        """Match the requested totals, adjacent delta and class code."""
        article = data_service.OldArticle(
            "Top Gear Hyper Bike",
            1,
            10,
            "GA",
        )
        changes = ArticleChanges(
            current_total=3042,
            added=(article,),
            removed=(),
            quality_changes=(QualityChange(None, article),),
            quality_counts=(("FA", 301), ("FL", 101), ("GA", 2640)),
        )
        assert reports.build_edit_summary(changes, {}, quality=True) == (
            "3\u202f042 articles (+1): FA 301, FL 101, GA 2\u202f640 (+1). GA "
            "«[[:en:Top Gear Hyper Bike]]»."
        )

    @staticmethod
    def test_signed_class_deltas_use_the_same_count_format() -> None:
        """Group large positive and negative class differences."""
        changes = ArticleChanges(
            current_total=1234,
            added=(),
            removed=(),
            quality_changes=tuple(
                QualityChange(
                    data_service.OldArticle(
                        f"Game {index}",
                        index,
                        index,
                        "FA",
                    ),
                    data_service.OldArticle(
                        f"Game {index}",
                        index,
                        index,
                        "GA",
                    ),
                )
                for index in range(1, 1235)
            ),
            quality_counts=(("FA", 0), ("FL", 0), ("GA", 1234)),
        )
        summary = reports.build_edit_summary(changes, {}, quality=True)
        assert summary.startswith(
            "1\u202f234 articles: FA 0 (-1\u202f234), FL 0, "
            "GA 1\u202f234 (+1\u202f234).",
        )
        assert len(summary.encode()) <= reports.MAX_EDIT_SUMMARY_BYTES

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
    def test_new_english_classes_use_requested_codes() -> None:
        """Name new classes and attach their measured signed deltas."""
        summary = reports.build_edit_summary(
            compare_articles(
                {},
                _rows(("Good", 1, 10, "GA"), ("Article", 2, 20, "FA")),
            ),
            {},
            quality=True,
        )
        assert summary == (
            "2 articles (+2): FA 1 (+1), FL 0, GA 1 (+1). GA "
            "«[[:en:Good]]»; FA «[[:en:Article]]»."
        )
        assert (
            reports.build_edit_summary(
                compare_articles({}, _rows(("List", 3, 30, "FL"))),
                {},
                quality=True,
            )
            == "1 article (+1): FA 0, FL 1 (+1), GA 0. FL «[[:en:List]]»."
        )

    @staticmethod
    def test_removed_english_classes_use_previous_class() -> None:
        """Use DGA for delisted GA and FFA/FFL for former FA/FL."""
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
            "0 articles (-3): FA 0 (-1), FL 0 (-1), GA 0 (-1). DGA "
            "«[[:en:Good]]»; FFA «[[:en:Article]]»; FFL «[[:en:List]]»."
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
            "1 article: FA 1 (+1), FL 0, GA 0 (-1). FA «[[:en:After]]» from"
            " GA."
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
            assert reports.build_edit_summary(
                compare_articles(
                    previous,
                    _rows(("After", item_id, 10, "FA")),
                ),
                {},
                quality=True,
            ) == (
                "1 article: FA 1 (+1), FL 0, GA 0 (-1). "
                "FA «[[:en:After]]» from GA."
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
            "1 article: FA 1 (+1), FL 0, GA 0 (-1). FA «[[:en:After]]» "
            "&#91;[[後]]&#93; from GA."
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
            == "1 article: FA 0, FL 0, GA 1."
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
            "1 article (+1/-1): FA 0, FL 0, GA 1 (+1/-1). GA "
            "«[[:en:Same]]»; DGA «[[:en:Same]]»."
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
            == "1 article: FA 1, FL 0, GA 0."
        )
        assert (
            reports.build_edit_summary(
                compare_articles(previous, _rows()),
                {},
                quality=True,
            )
            == "0 articles (-1): FA 0, FL 0, GA 0. Remove «[[:en:Before]]»."
        )
        current = data_service.OldArticle("After", 1, 10, "FA")
        unknown_transition = ArticleChanges(
            current_total=1,
            added=(),
            removed=(),
            quality_changes=(QualityChange(previous["Before"], current),),
            quality_counts=(("FA", 1), ("FL", 0), ("GA", 0)),
        )
        assert (
            reports.build_edit_summary(
                unknown_transition,
                {},
                quality=True,
            )
            == "1 article: FA 1, FL 0, GA 0."
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
        assert summary == ("1 article: FA 1 (+1), FL 0, GA 0 (-1). FA 1 more.")
