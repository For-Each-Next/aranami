"""Verify candidates and retained quality chronology."""

# Fixture identities and counts describe public report contracts.
# ruff: file-ignore[private-member-access]

import datetime as dt
from unittest import TestCase

import mwparserfromhell
import polars as pl
import test_enwp_quality_rendering as fixtures
from wiki_fixtures import OfflineSite

from data.article_changes import ArticleChanges, OldArticle, QualityChange
from tasks.enwiki.key_articles import parse_old_articles
from tasks.enwiki.reviews import extract_review_start
from updater.services.zhwiki import enwp_key_articles as reports


class EnglishReviewTests(TestCase):
    """Keep nominations separate from retained quality status."""

    @staticmethod
    def test_new_candidate_matches_the_requested_template_format() -> None:
        """Retain the grade, display markup and start date."""
        rows = (
            fixtures
            ._rows()
            .head(1)
            .with_columns(
                pl.lit("Convergence (The Last of Us)").alias("en_title"),
                pl.lit("Convergence (The Last of Us)").alias("display_title"),
                pl.lit("Convergence (<i>The Last of Us</i>)").alias(
                    "en_display_title",
                ),
                pl.lit("B").alias("en_class"),
                pl.lit("Low").alias("en_importance"),
                pl.lit("GAN").alias("en_reviews"),
                pl.lit(133867390).alias("item_id"),
                pl.lit(dt.date(2026, 11, 11)).alias("review_date"),
            )
        )
        rendered = reports.render_review_body(rows)
        assert rendered.startswith("== 参评条目 ==\n\n{{PJ:VG/DBR/EN/header}}")
        assert "en_cls=B/GAN|en_imp=Low|wd=133867390" in rendered
        assert "<!-- Review started: 2026-11-11 -->" in rendered
        assert not reports.render_review_body(None)
        assert not reports.render_review_body(rows.clear())

    @staticmethod
    def test_nomination_dates_descend_with_unknowns_last() -> None:
        """Order all nomination types by start date with title ties."""
        rows = fixtures._rows().with_columns(
            pl.Series("en_reviews", ["GAN", "FAC", "FLC", "GAN", "FAC"]),
            pl.Series(
                "review_date",
                [
                    dt.date(2026, 11, 8),
                    None,
                    dt.date(2026, 11, 10),
                    dt.date(2026, 11, 10),
                    None,
                ],
                dtype=pl.Date,
            ),
        )
        rendered = reports.render_review_body(rows)
        code = mwparserfromhell.parse(rendered)
        templates = [
            template
            for template in code.filter_templates()
            if str(template.name) == reports.REPORT_ITEM_TEMPLATE
        ]
        assert [str(template.get("en").value) for template in templates] == [
            "Beta FL",
            "Unknown GA",
            "Alpha FA",
            "Old FL",
            "Zulu GA",
        ]
        assert [
            str(comment.contents).strip() for comment in code.filter_comments()
        ] == [
            "Review started: 2026-11-10",
            "Review started: 2026-11-10",
            "Review started: 2026-11-08",
        ]
        assert "en_cls=FL/FLC|" in rendered
        assert "en_cls=GA/GAN|" in rendered
        assert "en_cls=FA/GAN|" in rendered

    @staticmethod
    def test_candidates_without_date_column_remain_alphabetical() -> None:
        """Preserve undated candidate frames without inventing dates."""
        rendered = reports.render_review_body(fixtures._rows())
        code = mwparserfromhell.parse(rendered)
        templates = [
            template
            for template in code.filter_templates()
            if str(template.name) == reports.REPORT_ITEM_TEMPLATE
        ]
        assert [str(template.get("en").value) for template in templates] == [
            "Alpha FA",
            "Beta FL",
            "Old FL",
            "Unknown GA",
            "Zulu GA",
        ]
        assert not code.filter_comments()

    @staticmethod
    def test_existing_reviews_change_only_the_displayed_class() -> None:
        """Keep original dates, years, ordering and source classes."""
        rows = fixtures._rows()
        expected = reports.render_quality_body(rows).replace(
            "en_cls=GA|",
            "en_cls=GA/GAR|",
        )
        reviewed = rows.with_columns(
            pl
            .when(pl.col("en_class") == "GA")
            .then(pl.lit("GAR"))
            .otherwise(None)
            .alias("en_reviews"),
        )
        assert reports.render_quality_body(reviewed) == expected
        assert reviewed["en_class"].to_list() == rows["en_class"].to_list()
        old = parse_old_articles(expected, OfflineSite("zh", "wikipedia"))
        assert old["Zulu GA"].en_class == "GA"

    @staticmethod
    def test_summary_omits_zero_and_names_current_nominees() -> None:
        """Append current candidates separately from quality totals."""
        article = OldArticle("New article", 1, 1, "GA")
        changes = ArticleChanges(
            current_total=3045,
            added=(article,),
            removed=(),
            quality_changes=(QualityChange(None, article),),
            quality_counts=(("FA", 301), ("FL", 101), ("GA", 2643)),
        )
        summary = reports.build_edit_summary(
            changes,
            {},
            quality=True,
            new_nominees=3,
        )
        assert summary.startswith(
            "3\u202f045 articles (+1): FA 301, FL 101, "
            "GA 2\u202f643 (+1); 3 new nominees.",
        )
        assert "nominee" not in reports.build_edit_summary(
            changes,
            {},
            quality=True,
        )

    @staticmethod
    def test_nomination_sources_do_not_borrow_promotion_dates() -> None:
        """Read the active GAN timestamp or exact FAC subpage only."""
        site = OfflineSite("en", "wikipedia")
        aliases = {
            "GAN": ["Template:GA nominee", "Template:Good article nominee"],
            "FAC": ["Template:Featured article candidates"],
            "FLC": ["Template:Featured list candidates"],
        }
        assert extract_review_start(
            "<!-- {{GA nominee|1 January 2000}} -->"
            "{{GA nominee|12:00, 11 November 2026 (UTC)|page=1}}",
            "GAN",
            site=site,
            aliases=aliases,
        ) == (dt.date(2026, 11, 11), None)
        assert extract_review_start(
            "{{Featured article candidates|Game/archive2}}",
            "FAC",
            site=site,
            aliases=aliases,
        ) == (None, "Wikipedia:Featured article candidates/Game/archive2")
        assert extract_review_start(
            "{{Template:Good article nominee|06:06, 12 October 2026 (UTC)}}",
            "GAN",
            site=site,
            aliases=aliases,
        ) == (dt.date(2026, 10, 12), None)
        assert extract_review_start(
            "{{featured list candidates|List of JoJo&#39;s games/archive2}}",
            "FLC",
            site=site,
            aliases=aliases,
        ) == (
            None,
            "Wikipedia:Featured list candidates/List of JoJo's games/archive2",
        )
