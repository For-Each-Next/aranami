"""Test manual quality milestones, prose measurements, and summaries."""

from __future__ import annotations

import math
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, call, patch

import polars as pl
from sqlalchemy.dialects import mysql
from wiki_fixtures import OfflineSite

from aranami.services import quality_contents as contents
from aranami.services.enwiki import (
    prose as en_prose,
    quality_contents as en_contents,
    quality_dates as en_dates,
)
from aranami.services.zhwiki import (
    prose as zh_prose,
    quality_contents as zh_contents,
    quality_dates as zh_dates,
)
from aranami.sources.quarry import quality
from aranami.support import words
from aranami.support.dates import parse_complete_date
from aranami.support.prose import extract_prose


class TestQualityMilestones(TestCase):
    """Check promotion history precedence and Chinese aliases."""

    @staticmethod
    def test_history_action_order_overrides_calendar_order() -> None:
        """Select re-promotions using history action order."""
        text = (
            "{{Article history|currentstatus=FA"
            "|action1=FAC|action1result=promoted|action1date=1 January 2024"
            "|action1oldid=123456"
            "|action2=FAC|action2result=failed|action2date=1 January 2025"
            "|action3=FAC|action3result=promoted|action3date=1 January 2023"
            "|action3oldid=654321}}"
        )
        latest = en_dates.extract_milestone(
            text,
            "FA",
            most_recent=True,
            site=OfflineSite("en", "wikipedia"),
        )
        earliest = en_dates.extract_milestone(
            text,
            "FA",
            most_recent=False,
            site=OfflineSite("en", "wikipedia"),
        )
        assert latest is not None
        assert earliest is not None
        assert (latest.date, latest.oldid) == ("2023-01-01", "654321")
        assert earliest.date == "2024-01-01"

    @staticmethod
    def test_chinese_history_and_calendar_dates() -> None:
        """Recognize FAN and Chinese date/result spellings."""
        text = (
            "{{模板:Article history|currentstatus=FA|action1=FAN"
            "|action1result=入選|action1date=2024年2月29日}}"
        )
        milestone = zh_dates.extract_milestone(
            text,
            "FA",
            most_recent=True,
            site=OfflineSite("zh", "wikipedia"),
        )
        assert milestone is not None
        assert milestone.date == "2024-02-29"
        assert milestone.source == "article_history"
        assert parse_complete_date("unresolved") is None

    @staticmethod
    def test_english_ga_template_supplies_promotion_date() -> None:
        """Use English GA's documented date and oldid fields."""
        text = (
            "{{Article history|action1=GAN|action1result=failed"
            "|action1date=1 January 2020}}\n"
            "{{GA|February 2, 2021|oldid=123456}}"
        )
        milestone = en_dates.extract_milestone(
            text,
            "GA",
            most_recent=True,
            site=OfflineSite("en", "wikipedia"),
        )
        assert milestone is not None
        assert milestone.date == "2021-02-02"
        assert milestone.oldid == "123456"
        assert milestone.source == "status_template"

    @staticmethod
    def test_ga_reassessment_kept_does_not_reset_listing_date() -> None:
        """Exclude retention decisions from promotion dates."""
        text = (
            "{{Article history|currentstatus=GA"
            "|action1=GAR|action1result=kept"
            "|action1date=3 March 2023}}"
        )
        milestone = en_dates.extract_milestone(
            text,
            "GA",
            most_recent=True,
            site=OfflineSite("en", "wikipedia"),
        )
        assert milestone is None

    @staticmethod
    def test_gar_listing_is_only_an_english_promotion() -> None:
        """Distinguish identical GAR events using per-wiki contracts."""
        text = (
            "{{Article history|currentstatus=GA|action1=GAR"
            "|action1result=listed|action1date=3 March 2023}}"
        )
        english = en_dates.extract_milestone(
            text,
            "GA",
            site=OfflineSite("en", "wikipedia"),
        )
        chinese = zh_dates.extract_milestone(
            text,
            "GA",
            site=OfflineSite("zh", "wikipedia"),
        )
        assert english is not None
        assert english.date == "2023-03-03"
        assert chinese is None

    @staticmethod
    def test_incomplete_dates_are_not_filled_with_defaults() -> None:
        """Leave dates without a year, month, or day unresolved."""
        for value in ("January 2024", "January 3", "2024"):
            text = (
                "{{Article history|action1=GAN|action1result=listed"
                f"|action1date={value}" + "}}"
            )
            assert (
                en_dates.extract_milestone(
                    text,
                    "GA",
                    site=OfflineSite("en", "wikipedia"),
                )
                is None
            )

    @staticmethod
    def test_chinese_config_aliases_and_ignored_actions() -> None:
        """Use FLN and skip ignored actions."""
        text = (
            "{{Article history|currentstatus=FA|action1=FLN"
            "|action1result=入选|action1date=2024年2月1日"
            "|action2=FLN|action2result=promoted"
            "|action2date=2025年2月1日|action2ignore=yes}}"
        )
        milestone = zh_dates.extract_milestone(
            text,
            "FL",
            site=OfflineSite("zh", "wikipedia"),
        )
        assert milestone is not None
        assert milestone.date == "2024-02-01"

    @staticmethod
    def test_display_names_are_not_action_aliases() -> None:
        """Reject human labels absent from the module config aliases."""
        text = (
            "{{Article history|action1=good article nominee"
            "|action1result=listed|action1date=3 March 2023}}"
        )
        assert (
            en_dates.extract_milestone(
                text,
                "GA",
                site=OfflineSite("en", "wikipedia"),
            )
            is None
        )


class TestQualityProse(TestCase):
    """Check prose extraction and language-specific measurements."""

    @staticmethod
    def test_english_prose_includes_episode_summary() -> None:
        """Include episode summaries and discard references."""
        text = (
            "Alpha beta.\n{{Episode list|ShortSummary=Gamma delta}}\n"
            "== References ==\nThese words are excluded."
        )
        prose = extract_prose(
            text,
            site=OfflineSite("en", "wikipedia"),
            profile=en_prose.PROFILE,
        )
        expected_words = 4
        assert words.count_words(prose, language="en") == expected_words
        assert len(prose.encode("utf-8")) == len(b"Alpha beta. Gamma delta")

    @staticmethod
    def test_tokenizer_cache_is_caller_relative() -> None:
        """Use precise segmentation and a caller-relative cache."""
        tokenizer = Mock()
        tokenizer.cut.return_value = ["中文", " ", "游戏", "!"]
        with (
            TemporaryDirectory() as directory,
            patch.object(words.Path, "cwd", return_value=Path(directory)),
            patch.object(
                words,
                "_tokenizer",
                return_value=tokenizer,
            ) as factory,
        ):
            prose = extract_prose(
                "中文游戏!",
                site=OfflineSite("zh", "wikipedia"),
                profile=zh_prose.PROFILE,
            )
            count = words.count_words(prose, language="zh")
        expected_words = 2
        assert count == expected_words
        assert len(prose.encode("utf-8")) == len("中文游戏".encode())
        factory.assert_called_once_with(
            Path(directory) / "cache" / "words",
        )
        tokenizer.cut.assert_called_once_with(
            "中文游戏",
            cut_all=False,
            HMM=True,
        )


class TestQualityAnalysis(TestCase):
    """Check batched source integration and Polars summary semantics."""

    @staticmethod
    def test_manual_analysis_preloads_without_publication() -> None:
        """Analyze current content and preserve unresolved dates."""
        site = OfflineSite("en", "wikipedia")
        metadata = pl.DataFrame(
            {
                "article_title": ["A_Game"],
                "quality_status": ["GA"],
                "importance": ["High"],
                "article_sort_key": [None],
                "article_display_title": [None],
            },
            schema={
                "article_title": pl.String,
                "quality_status": pl.String,
                "importance": pl.String,
                "article_sort_key": pl.String,
                "article_display_title": pl.String,
            },
        )
        article = Mock(text="One two three.", latest_revision_id=100)
        talk = Mock(text="No promotion information.", latest_revision_id=101)
        talk.exists.return_value = True
        with (
            patch.object(
                contents,
                "fetch_quality_articles",
                return_value=metadata,
            ) as queries,
            patch.object(
                contents,
                "template_aliases",
                return_value=("Template:Article history",),
            ),
            patch.object(
                contents,
                "read_pages",
                side_effect=[[article], [talk]],
            ) as reads,
        ):
            result = contents.analyze_quality_contents(site)
        assert [call.args[1] for call in reads.call_args_list] == [
            ["A Game"],
            ["Talk:A Game"],
        ]
        row = result.row(0, named=True)
        assert row["article_title"] == "A Game"
        assert row["article_display_title"] == "A Game"
        assert row["listed_date"] is None
        expected_words = 3
        assert row["prose_words"] == expected_words
        assert row["source_bytes"] == len(article.text.encode())
        queries.assert_called_once_with(
            site,
            project_title="Video games",
            classes=("FA", "FL", "GA"),
        )
        article.save.assert_not_called()
        talk.save.assert_not_called()

    @staticmethod
    def test_wiki_profiles_normalize_their_own_assessments() -> None:
        """Inject local assessment names and template maps."""
        for language, profile, qualities, importance in (
            (
                "en",
                en_contents.PROFILE,
                ["FA", "FL", "GA"],
                ["Top", "High", None],
            ),
            (
                "zh",
                zh_contents.PROFILE,
                ["典范", "特色列表", "优良"],
                ["极高", "高", None],
            ),
        ):
            site = OfflineSite(language, "wikipedia")
            metadata = pl.DataFrame(
                {
                    "article_title": ["Game_1", "Game_2", "Game_3"],
                    "quality_status": qualities,
                    "importance": importance,
                    "article_sort_key": [None] * 3,
                    "article_display_title": [None] * 3,
                },
                schema={
                    "article_title": pl.String,
                    "quality_status": pl.String,
                    "importance": pl.String,
                    "article_sort_key": pl.String,
                    "article_display_title": pl.String,
                },
            )
            articles = [
                Mock(text="One two.", latest_revision_id=index)
                for index in range(3)
            ]
            talks = [
                Mock(text="No milestone.", latest_revision_id=index)
                for index in range(3)
            ]
            with (
                patch.object(
                    contents,
                    "fetch_quality_articles",
                    return_value=metadata,
                ) as queries,
                patch.object(
                    contents,
                    "template_aliases",
                    return_value=(),
                ) as aliases,
                patch.object(
                    contents,
                    "read_pages",
                    side_effect=[articles, talks],
                ),
                patch.object(
                    contents,
                    "count_words",
                    return_value=2,
                ) as counter,
            ):
                result = contents.analyze_quality_contents(site).sort(
                    "article_title",
                )
            assert result["quality_status"].to_list() == ["FA", "FL", "GA"]
            assert result["importance"].to_list() == ["Top", "High", "Unknown"]
            queries.assert_called_once_with(
                site,
                project_title=profile.project_title,
                classes=profile.classes,
            )
            assert aliases.call_args_list == [
                call(site, title) for title in profile.templates.values()
            ]
            assert (
                counter.call_args_list
                == [
                    call("One two.", language=profile.language),
                ]
                * 3
            )

    def test_unsupported_site_is_rejected_before_reads(self) -> None:
        """Reject sites with no profile before accessing any source."""
        site = Mock()
        site.dbName.return_value = "frwiki"
        with (
            patch.object(contents, "fetch_quality_articles") as queries,
            patch.object(contents, "template_aliases") as aliases,
            patch.object(contents, "read_pages") as reads,
            self.assertRaisesRegex(ValueError, "frwiki"),  # ruff: ignore[pytest-unittest-raises-assertion]
        ):
            contents.analyze_quality_contents(site)
        queries.assert_not_called()
        aliases.assert_not_called()
        reads.assert_not_called()

    @staticmethod
    def test_empty_source_preserves_output_schema_without_content_reads() -> (
        None
    ):
        """Keep empty analysis typed without fetching aliases."""
        for language in ("en", "zh"):
            with (
                patch.object(
                    contents,
                    "fetch_quality_articles",
                    return_value=pl.DataFrame(),
                ),
                patch.object(contents, "template_aliases") as aliases,
                patch.object(contents, "read_pages") as reads,
            ):
                frame = contents.analyze_quality_contents(
                    OfflineSite(language, "wikipedia"),
                )
            assert frame.is_empty()
            assert frame.schema["quality_status"] == pl.String
            assert frame.schema["listed_date"] == pl.String
            assert frame.schema["prose_words"] == pl.Int64
            assert frame.schema["article_revision_id"] == pl.Int64
            aliases.assert_not_called()
            reads.assert_not_called()

    @staticmethod
    def test_statistics_preserve_nulls_and_linear_quantiles() -> None:
        """Keep null measurements distinct from explicit zero words."""
        frame = pl.DataFrame({
            "quality_status": ["FA", "FA", "FA"],
            "prose_words": [0, 100, None],
            "listed_date": ["2020-01-01", "2020-12-01", None],
            "importance": ["High", "High", "Low"],
        })
        result = contents.length_statistics(frame).row(0, named=True)
        expected = {
            "quality_status": "FA",
            "pages": 3,
            "measured": 2,
            "median_words": 50.0,
            "central_68_low": 16.0,
            "central_68_high": 84.0,
            "central_95_low": 2.5,
            "central_95_high": 97.5,
        }
        assert result.keys() == expected.keys()
        assert all(
            math.isclose(result[key], value)
            if isinstance(value, float)
            else result[key] == value
            for key, value in expected.items()
        )
        assert contents.year_statistics(frame).to_dicts() == [
            {
                "year": 2020,
                "quality_status": "FA",
                "importance": "High",
                "articles": 2,
            },
        ]

    @staticmethod
    def test_quality_selection_does_not_require_a_wikidata_item() -> None:
        """Read assessments and optional article properties."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        replica = Mock()
        with patch.object(quality.Replica, "from_site", return_value=replica):
            quality.fetch_quality_articles(
                site,
                project_title="电子游戏",
                classes=("典范", "特色列表", "优良"),
            )
        statement = replica.query.call_args.args[0]
        compiled = statement.compile(dialect=mysql.dialect())
        assert "wikibase_item" not in compiled.params.values()
        assert "电子游戏" in compiled.params.values()
        assert "LEFT OUTER JOIN" in str(compiled)
        assert ["典范", "特色列表", "优良"] in compiled.params.values()

    @staticmethod
    def test_quality_source_uses_explicit_project_and_class_filter() -> None:
        """Keep SQL independent of language-specific grades."""
        site, replica = Mock(), Mock()
        with patch.object(quality.Replica, "from_site", return_value=replica):
            result = quality.fetch_quality_articles(
                site,
                project_title="Another project",
                classes=("Selected class",),
            )
        statement = replica.query.call_args.args[0]
        parameters = statement.compile(dialect=mysql.dialect()).params.values()
        assert "Another project" in parameters
        assert ["Selected class"] in parameters
        assert "Video games" not in parameters
        assert "电子游戏" not in parameters
        site.dbName.assert_not_called()
        replica.query.return_value.collect.assert_called_once_with()
        assert result is replica.query.return_value.collect.return_value
