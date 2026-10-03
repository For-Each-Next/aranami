"""Test ENWP rendering, article changes, and replica selections."""

from __future__ import annotations

from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import Mock, patch

import mwparserfromhell
import polars as pl
from sqlalchemy.dialects import mysql
from sqlalchemy.sql.selectable import Select
from wiki_fixtures import OfflineSite

from aranami.jobs import enwp_key_articles as job
from aranami.services.zhwiki import enwp_key_articles as reports
from aranami.sources.quarry import enwp


def _old_text(spec: reports.ReportSpec, body: str = "old body") -> str:
    """Build existing report markers around supplied body text.

    Args:
        spec: Report whose count markers should be included.
        body: Existing report content.

    Returns:
        Wikitext with surrounding content and complete report markers.
    """
    markers = ["count_all", *(name for _, name in spec.count_markers)]
    counts = " ".join(
        f'<!-- update begin="{name}" -->99<!-- update begin="{name}" -->'
        for name in markers
    )
    return (
        f"Before {counts}\n"
        f'<!-- update begin="body" -->{body}<!-- update end="body" -->'
        "\nAfter"
    )


def _english_rows() -> pl.DataFrame:
    """Provide raw source rows with distinct report memberships.

    Returns:
        Important-only and quality-only English articles.
    """
    return pl.DataFrame({
        "en_title": ["Alpha_Game", "Éclair"],
        "qid": ["Q1", "Q2"],
        "en_defaultsort": [None, "Éclair"],
        "en_displaytitle": [None, "<i>Éclair</i>"],
        "en_class": ["B", "GA"],
        "en_importance": ["Top", "Low"],
    })


class TestEnglishReportService(TestCase):
    """Exercise report membership, rendering, and summary behavior."""

    @staticmethod
    def test_nested_counter_markers_preserve_surroundings() -> None:
        """Replace markers inside a template without rewriting it."""
        text = '{{Box|n=<!-- update begin="count_all" -->9'
        text += '<!-- update begin="count_all" -->|other=keep}}'
        result = reports.replace_marker_value(text, "count_all", "12")
        assert "|other=keep}}" in result
        assert '<!-- aranami begin="count_all" -->12' in result
        assert '<!-- aranami end="count_all" -->' in result
        failure = None
        try:
            reports.replace_body("No report markers", "replacement")
        except ValueError as error:
            failure = error
        assert failure is not None
        assert "body" in str(failure)

    @staticmethod
    def test_normalization_preserves_display_titles_and_fallbacks() -> None:
        """Fall back to titles for absent display or sort values."""
        result = reports.normalize_en_pages(_english_rows())
        assert result.get_column("en_title").to_list() == [
            "Alpha Game",
            "Éclair",
        ]
        assert result.get_column("item_id").to_list() == [1, 2]
        assert result.get_column("en_display_title").to_list() == [
            "Alpha Game",
            "<i>Éclair</i>",
        ]
        assert reports.heading_key("Éclair") == "E"
        assert reports.heading_key("2 games") == "#"

    @staticmethod
    def test_chinese_fallbacks_and_assessments() -> None:
        """Prefer sitelinks, then labels, and classify missing pages."""
        english = reports.normalize_en_pages(_english_rows())
        links = pl.DataFrame({"item_id": [1], "zh_title": ["阿爾法"]})
        labels = pl.DataFrame({"item_id": [1, 2], "label": ["unused", "閃電"]})
        states = pl.DataFrame({
            "zh_title": ["阿爾法"],
            "zh_is_redirect": [0],
            "zh_class": ["乙"],
            "zh_importance": ["高"],
        })
        rows = reports.build_enriched_rows(english, links, labels, states)
        assert rows.get_column("display_title").to_list() == ["阿爾法", "閃電"]
        assert rows.get_column("final_zh_class").to_list() == ["乙", "请求"]
        assert rows.get_column("final_zh_importance").to_list() == ["高", "无"]

    @staticmethod
    def test_templates_escape_delimiters_and_parse_old_items() -> None:
        """Keep literal delimiters inside article parameters."""
        row = {
            "display_title": "A|B={C}",
            "final_zh_class": "请求",
            "final_zh_importance": "无",
            "en_title": "A|B",
            "en_display_title": "A|B",
            "en_class": "GA",
            "en_importance": "Low",
            "item_id": 9,
        }
        template = reports.build_report_item_template(row)
        parsed = mwparserfromhell.parse(str(template)).filter_templates()
        assert len(parsed) == 1
        expected_parameter_count = 8
        assert len(parsed[0].params) == expected_parameter_count
        assert "&#124;" in str(template)
        assert reports.parse_old_articles(
            str(template),
            OfflineSite("zh", "wikipedia"),
        ) == {
            "A|B": reports.OldArticle("A|B", 9),
        }

    @staticmethod
    def test_summary_reports_removed_sitelinks_and_bounds_utf8() -> None:
        """Mention linked article changes within the byte budget."""
        old = {"Old": reports.OldArticle("Old", 8)}
        new = pl.DataFrame({"en_title": ["New"], "item_id": [9]})
        summary = reports.build_edit_summary(old, new, {8: "舊", 9: "新"})
        assert "add [[:en:New|New]] ([[新|新]])" in summary
        assert "remove [[:en:Old|Old]] ([[舊|舊]])" in summary
        long_rows = pl.DataFrame({
            "en_title": ["中文" * 200],
            "item_id": [1],
        })
        short = reports.build_edit_summary({}, long_rows, {}, max_bytes=50)
        summary_budget = 50
        assert len(short.encode()) <= summary_budget
        assert "1 article changes" in short

    @staticmethod
    def test_builds_both_reports_with_shared_reads() -> None:
        """Process text and resolve removed items with shared reads."""
        site = OfflineSite("zh", "wikipedia")
        old_item = "{{PJ:VG/DBR/EN/item|en=Old|wd=8}}"
        existing = {
            spec.name: _old_text(spec, old_item) for spec in job.REPORT_SPECS
        }
        sitelinks = pl.DataFrame({
            "item_id": [1, 8],
            "zh_title": ["阿爾法", "舊"],
        })
        labels = pl.DataFrame({"item_id": [2], "label": ["閃電"]})
        states = pl.DataFrame(
            schema={
                "zh_title": pl.String,
                "zh_is_redirect": pl.Int64,
                "zh_class": pl.String,
                "zh_importance": pl.String,
            },
        )
        with (
            patch.object(
                enwp,
                "fetch_en_key_pages",
                return_value=_english_rows(),
            ),
            patch.object(
                enwp,
                "fetch_wikidata_sitelinks",
                return_value=sitelinks,
            ) as links,
            patch.object(
                enwp,
                "fetch_wikidata_labels",
                return_value=labels,
            ) as label,
            patch.object(enwp, "fetch_zh_page_states", return_value=states),
        ):
            data = reports.prepare_reports(existing, job.REPORT_SPECS, site)
            texts = reports.build_reports(existing, data, job.REPORT_SPECS)
        summaries = {
            spec.name: reports.build_edit_summary(
                data.old_articles[spec.name],
                reports.filter_report_rows(data.rows, spec),
                data.linked_titles,
            )
            for spec in job.REPORT_SPECS
        }
        links.assert_called_once_with([1, 2, 8])
        label.assert_called_once_with([1, 2])
        assert list(texts) == [spec.name for spec in job.REPORT_SPECS]
        assert "en=Alpha Game" in texts["important"]
        assert "en=Éclair" not in texts["important"]
        assert "en=Éclair" in texts["quality"]
        assert "en=Alpha Game" not in texts["quality"]
        assert all(
            text.startswith("Before") and text.endswith("After")
            for text in texts.values()
        )
        assert all(
            "remove [[:en:Old|Old]] ([[舊|舊]])" in summary
            for summary in summaries.values()
        )
        custom = reports.ReportSpec(
            name="custom",
            filter_column="en_class",
            accepted_values=("B",),
            count_markers=(("B", "count_custom"),),
        )
        custom_texts = reports.build_reports(
            {custom.name: _old_text(custom)},
            data,
            (custom,),
        )
        assert list(custom_texts) == [custom.name]
        assert "en=Alpha Game" in custom_texts[custom.name]
        assert "en=Éclair" not in custom_texts[custom.name]
        assert (
            '<!-- aranami begin="count_custom" -->1'
            in custom_texts[custom.name]
        )


class TestEnglishReplicaSource(TestCase):
    """Check read-only selections, batching, and label ordering."""

    @staticmethod
    def test_english_selection_is_parameterized_and_read_only() -> None:
        """Bind filters and retain optional page properties."""
        replica = Mock()
        replica.query.return_value.collect.side_effect = [
            pl.DataFrame({"project_title": ["Video games"]}),
            pl.DataFrame(
                schema=dict.fromkeys(_english_rows().columns, pl.String),
            ),
        ]
        with patch.object(enwp, "Replica", return_value=replica):
            result = enwp.fetch_en_key_pages()
        statement = replica.query.call_args.args[0]
        assert isinstance(statement, Select)
        compiled = statement.compile(dialect=mysql.dialect())
        sql = str(compiled)
        assert "LEFT OUTER JOIN page_props" in sql
        assert "page_namespace" in sql
        assert "Video games" in compiled.params.values()
        assert "Video games" not in sql
        expected_schema = pl.Schema(
            dict.fromkeys(_english_rows().columns, pl.String),
        )
        assert result.schema == expected_schema

    @staticmethod
    def test_label_priority_uses_termstore_and_label_type_join() -> None:
        """Prefer zh-tw labels and exclude other term types."""
        replica = Mock()
        replica.query.return_value.collect.return_value = pl.DataFrame({
            "item_id": [1, 1, 1, 2],
            "lang": ["zh", "zh-cn", "zh-tw", "yue"],
            "label": ["general", "simplified", "traditional", "Cantonese"],
        })
        with patch.object(
            enwp.Replica,
            "wikidata_terms",
            return_value=replica,
        ) as terms:
            labels = enwp.fetch_wikidata_labels([2, 1, 1])
        terms.assert_called_once_with()
        assert labels.to_dict(as_series=False) == {
            "item_id": [1, 2],
            "label": ["traditional", "Cantonese"],
        }
        statement = replica.query.call_args.args[0]
        compiled = statement.compile(dialect=mysql.dialect())
        assert "JOIN wbt_type" in str(compiled)
        assert "label" in compiled.params.values()

    @staticmethod
    def test_label_languages_can_be_prioritized_by_another_report() -> None:
        """Honor language priority for shared character reports."""
        replica = Mock()
        replica.query.return_value.collect.return_value = pl.DataFrame({
            "item_id": [1, 1],
            "lang": ["zh", "en"],
            "label": ["中文", "English"],
        })
        with patch.object(
            enwp.Replica,
            "wikidata_terms",
            return_value=replica,
        ):
            labels = enwp.fetch_wikidata_labels([1], languages=("en", "zh"))
        assert labels.get_column("label").to_list() == ["English"]

    @staticmethod
    def test_sitelinks_batch_deduplicated_ids() -> None:
        """Bound each query to 500 IDs and avoid duplicate requests."""
        replica = Mock()
        replica.query.return_value.collect.return_value = pl.DataFrame(
            schema={"item_id": pl.Int64, "zh_title": pl.String},
        )
        with patch.object(enwp, "Replica", return_value=replica):
            result = enwp.fetch_wikidata_sitelinks([*range(501), 0])
        expected_batch_count = 2
        assert replica.query.call_count == expected_batch_count
        assert result.schema == pl.Schema({
            "item_id": pl.Int64,
            "zh_title": pl.String,
        })
        for call in replica.query.call_args_list:
            statement = call.args[0]
            lists = [
                value
                for value in statement.compile().params.values()
                if isinstance(value, list)
                and value
                and isinstance(value[0], int)
            ]
            assert len(lists) == 1
            batch_limit = 500
            assert len(lists[0]) <= batch_limit


class TestEnglishReportJob(TestCase):
    """Check that jobs delegate publication through their context."""

    @staticmethod
    def test_job_publishes_proposed_edits_through_context() -> None:
        """Read targets and wrap processed text for publication."""
        context = Mock()
        context.site = OfflineSite("zh", "wikipedia")
        existing = {spec.name: _old_text(spec) for spec in job.REPORT_SPECS}
        pages = [Mock(text=existing[name]) for name in job.REPORT_TARGETS]
        data = reports.ReportData(
            rows=reports.normalize_en_pages(_english_rows()),
            old_articles={name: {} for name in job.REPORT_TARGETS},
            linked_titles={},
        )
        texts = {name: f"new {name}" for name in job.REPORT_TARGETS}
        with (
            patch.object(
                job,
                "job_run",
                return_value=nullcontext(context),
            ) as run,
            patch.object(job, "read_pages", return_value=pages) as read,
            patch.object(
                reports,
                "prepare_reports",
                return_value=data,
            ) as prepare,
            patch.object(
                reports,
                "build_reports",
                return_value=texts,
            ) as build,
        ):
            job.run(dry_run=True, context=context)
        run.assert_called_once_with(
            "enwp_key_articles",
            dry_run=True,
            context=context,
        )
        read.assert_called_once_with(
            context.site,
            list(job.REPORT_TARGETS.values()),
        )
        prepare.assert_called_once_with(
            existing,
            job.REPORT_SPECS,
            context.site,
        )
        build.assert_called_once_with(existing, data, job.REPORT_SPECS)
        edits = [call.args[0] for call in context.publish.call_args_list]
        assert [edit.title for edit in edits] == list(
            job.REPORT_TARGETS.values(),
        )
        for name, edit in zip(job.REPORT_TARGETS, edits, strict=True):
            assert edit.site is context.site
            assert edit.text == texts[name]
            assert edit.original_text == existing[name]
            assert edit.tags == ("enwp-key-articles", name)
            assert "add [[:en:" in edit.summary
