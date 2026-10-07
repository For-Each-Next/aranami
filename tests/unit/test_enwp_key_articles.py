"""Test ENWP rendering, article changes, and replica selections."""

# Preserve punctuation in the user's Chinese article title.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import Mock, patch

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import HTMLEntity
from sqlalchemy.dialects import mysql
from sqlalchemy.sql.selectable import Select
from wiki_fixtures import OfflineSite

from aranami.jobs import enwp_key_articles as job
from aranami.services.zhwiki import enwp_key_articles as reports
from aranami.sources.quarry import enwp
from aranami.support.report_membership import load_membership, save_membership

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


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
        "page_id": [1001, 1002],
        "en_title": ["Alpha_Game", "Éclair"],
        "qid": ["Q1", "Q2"],
        "en_defaultsort": [None, "Éclair"],
        "en_displaytitle": [None, "<i>Éclair</i>"],
        "en_class": ["B", "GA"],
        "en_importance": ["Top", "Low"],
    })


def _old_change_reports() -> dict[str, str]:
    """Provide both report pages before their memberships change.

    Returns:
        Existing report text without embedded page identities.
    """
    body = (
        "* {{PJ:VG/DBR/EN/item|en=Old title|wd=1}}\n"
        "* {{PJ:VG/DBR/EN/item|en=Gone|wd=2}}"
    )
    return {spec.name: _old_text(spec, body) for spec in job.REPORT_SPECS}


@contextmanager
def _report_change_sources(
    context: Mock,
    existing: Mapping[str, str],
) -> Iterator[Mock]:
    """Supply offline source data for actual report construction.

    Args:
        context: Publication context used by the job.
        existing: Both current target texts.

    Yields:
        English source mock for verifying the shared query count.
    """
    english = pl.DataFrame(
        {
            "page_id": [101, 103, 104],
            "en_title": ["Renamed", "New_important", "New_quality"],
            "qid": ["Q9", "Q3", "Q4"],
            "en_defaultsort": [None, None, None],
            "en_displaytitle": [None, None, None],
            "en_class": ["GA", "B", "GA"],
            "en_importance": ["Top", "High", "Low"],
        },
        schema_overrides={
            "en_defaultsort": pl.String,
            "en_displaytitle": pl.String,
        },
    )
    with (
        patch.object(job, "job_run", return_value=nullcontext(context)),
        patch.object(
            job,
            "read_pages",
            return_value=[
                Mock(text=existing[name]) for name in job.REPORT_TARGETS
            ],
        ),
        patch.object(
            enwp,
            "fetch_en_key_pages",
            return_value=english,
        ) as source,
        patch.object(
            enwp,
            "fetch_wikidata_sitelinks",
            return_value=pl.DataFrame(
                schema={"item_id": pl.Int64, "zh_title": pl.String},
            ),
        ),
        patch.object(
            enwp,
            "fetch_wikidata_labels",
            return_value=pl.DataFrame(
                schema={"item_id": pl.Int64, "label": pl.String},
            ),
        ),
        patch.object(
            enwp,
            "fetch_zh_page_states",
            return_value=pl.DataFrame(
                schema={
                    "zh_title": pl.String,
                    "zh_is_redirect": pl.Int64,
                    "zh_class": pl.String,
                    "zh_importance": pl.String,
                },
            ),
        ),
    ):
        yield source


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
        assert result.get_column("page_id").to_list() == [1001, 1002]
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
            "page_id": 123,
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
        item = reports.render_item(row)
        assert "page-id" not in item
        assert reports.parse_old_articles(
            item,
            OfflineSite("zh", "wikipedia"),
        ) == {"A|B": reports.OldArticle("A|B", 9)}
        legacy_item = item + '<!-- aranami-enwp page-id="123" -->'
        assert reports.parse_old_articles(
            legacy_item,
            OfflineSite("zh", "wikipedia"),
        ) == {"A|B": reports.OldArticle("A|B", 9, 123)}
        managed = _old_text(job.REPORT_SPECS[0], item)
        assert item in reports.replace_body(managed, item)

    @staticmethod
    def test_summary_reports_removed_sitelinks_and_bounds_utf8() -> None:
        """Mention linked article changes within the byte budget."""
        old = {"Old": reports.OldArticle("Old", 8)}
        new = pl.DataFrame({"en_title": ["New"], "item_id": [9]})
        summary = reports.build_edit_summary(old, new, {8: "舊", 9: "新"})
        assert summary == (
            "1 item total. Added «[[:en:New]]» &#91;[[新]]&#93;; "
            "removed «[[:en:Old]]» &#91;[[舊]]&#93;."
        )
        long_rows = pl.DataFrame({
            "en_title": ["中文" * 200],
            "item_id": [1],
        })
        short = reports.build_edit_summary({}, long_rows, {}, max_bytes=50)
        summary_budget = 50
        assert len(short.encode()) <= summary_budget
        assert short == "1 item total. Added 1 more."

    @staticmethod
    def test_summary_links_parenthesized_chinese_counterpart() -> None:
        """Keep both titles linked with distinct delimiters."""
        english = "Blade (Honkai)"
        chinese = "刃 (崩壞：星穹鐵道)"
        mention = reports.format_summary_article(english, chinese)
        assert mention == (
            "«[[:en:Blade (Honkai)]]» &#91;[[刃 (崩壞：星穹鐵道)]]&#93;"
        )
        parsed = mwparserfromhell.parse(mention)
        assert str(parsed) == mention
        assert [str(link.title) for link in parsed.filter_wikilinks()] == [
            f":en:{english}",
            chinese,
        ]
        assert [
            node.normalize()
            for node in parsed.nodes
            if isinstance(node, HTMLEntity)
        ] == ["[", "]"]

    @staticmethod
    def test_summary_omits_missing_or_empty_chinese_counterpart() -> None:
        """Keep only the English link without a Chinese title."""
        for chinese in (None, ""):
            mention = reports.format_summary_article(
                "Blade (Honkai)",
                chinese,
            )
            assert mention == "«[[:en:Blade (Honkai)]]»"
            assert [
                str(link.title)
                for link in mwparserfromhell.parse(mention).filter_wikilinks()
            ] == [":en:Blade (Honkai)"]

    @staticmethod
    def test_summary_ignores_renames_and_assessment_changes() -> None:
        """Keep only the total for renamed or reassessed pages."""
        old = {"Old": reports.OldArticle("Old", 8, 123)}
        new = pl.DataFrame({
            "page_id": [123],
            "en_title": ["New"],
            "item_id": [9],
            "en_class": ["FA"],
            "en_importance": ["Top"],
        })
        assert reports.build_edit_summary(old, new, {}) == "1 item total."

    @staticmethod
    def test_summary_reports_recreated_pages_with_the_same_title() -> None:
        """Treat a different page ID as changed report membership."""
        old = {"Same": reports.OldArticle("Same", 8, 123)}
        new = pl.DataFrame({
            "page_id": [456],
            "en_title": ["Same"],
            "item_id": [8],
        })
        assert reports.build_edit_summary(old, new, {}) == (
            "1 item total. Added «[[:en:Same]]»; removed «[[:en:Same]]»."
        )

    @staticmethod
    def test_summary_migrates_legacy_identity_and_formats_totals() -> None:
        """Match legacy IDs and separate thousands in the total."""
        old = {"Old": reports.OldArticle("Old", 8)}
        new = pl.DataFrame({
            "page_id": [123],
            "en_title": ["New"],
            "item_id": [8],
        })
        assert reports.build_edit_summary(old, new, {}) == "1 item total."
        count = 1234
        titles = [f"Article {index}" for index in range(count)]
        new = pl.DataFrame({
            "page_id": list(range(1, count + 1)),
            "en_title": titles,
            "item_id": list(range(1, count + 1)),
        })
        old = {
            title: reports.OldArticle(title, index, index)
            for index, title in enumerate(titles, start=1)
        }
        assert reports.build_edit_summary(old, new, {}) == "1,234 items total."

    @staticmethod
    def test_summary_caps_utf8_bytes_and_preserves_complete_links() -> None:
        """Honor byte budgets while retaining complete article links."""
        old = {"Old": reports.OldArticle("Old", 8, 100)}
        new = pl.DataFrame({
            "page_id": list(range(1, 11)),
            "en_title": [f"New {index} 中文中文中文" for index in range(10)],
            "item_id": list(range(1, 11)),
        })
        chinese = {index: f"中文標題 {index}" for index in range(1, 11)}
        for requested, budget in [(90, 90), (500, 255)]:
            summary = reports.build_edit_summary(
                old,
                new,
                chinese,
                max_bytes=requested,
            )
            assert len(summary.encode("utf-8")) <= budget
            assert summary.startswith("10 items total.")
            assert summary.count("[[") == summary.count("]]")
            assert summary.count("«") == summary.count("»")
            assert summary.count("&#91;") == summary.count("&#93;")
            assert "more" in summary
        assert not reports.build_edit_summary(old, new, {}, max_bytes=0)

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
            "removed «[[:en:Old]]» &#91;[[舊]]&#93;" in summary
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
                schema={
                    **dict.fromkeys(_english_rows().columns, pl.String),
                    "page_id": pl.Int64,
                },
            ),
        ]
        with patch.object(enwp, "Replica", return_value=replica):
            result = enwp.fetch_en_key_pages()
        statement = replica.query.call_args.args[0]
        assert isinstance(statement, Select)
        assert "page_id" in statement.selected_columns
        compiled = statement.compile(dialect=mysql.dialect())
        sql = str(compiled)
        assert "LEFT OUTER JOIN page_props" in sql
        assert "page_namespace" in sql
        assert "page.page_id" in sql
        assert "Video games" in compiled.params.values()
        assert "Video games" not in sql
        expected_schema = pl.Schema(
            {
                **dict.fromkeys(_english_rows().columns, pl.String),
                "page_id": pl.Int64,
            },
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
        context.dry = True
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
            patch.object(job, "load_membership", return_value={}) as load,
            patch.object(job, "save_membership") as save,
        ):
            job.run(dry=True, context=context)
        run.assert_called_once_with(
            "enwp_key_articles",
            dry=True,
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
        assert load.call_count == len(job.REPORT_SPECS)
        assert save.call_args_list == [
            ((context.site, title, existing[name], {}), {})
            for name, title in job.REPORT_TARGETS.items()
        ]
        edits = [call.args[0] for call in context.publish.call_args_list]
        assert [edit.title for edit in edits] == list(
            job.REPORT_TARGETS.values(),
        )
        for name, edit in zip(job.REPORT_TARGETS, edits, strict=True):
            assert edit.site is context.site
            assert edit.text == texts[name]
            assert edit.original_text == existing[name]
            assert edit.tags == ("enwp-key-articles", name)
            assert edit.summary.startswith("1 item total. Added «[[:en:")

    @staticmethod
    def test_both_reports_cache_ids_and_summarize_english_membership() -> None:
        """List English changes and suppress same-ID renames."""
        context = Mock(site=OfflineSite("zh", "wikipedia"), dry=False)
        existing = _old_change_reports()
        old_members = {"Old title": 101, "Gone": 102}
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            _report_change_sources(context, existing) as source,
        ):
            for name, title in job.REPORT_TARGETS.items():
                save_membership(
                    context.site,
                    title,
                    existing[name],
                    old_members,
                )
            job.run(context=context)
            source.assert_called_once_with()
            edits = [call.args[0] for call in context.publish.call_args_list]
            assert [edit.title for edit in edits] == list(
                job.REPORT_TARGETS.values(),
            )
            for spec, edit in zip(job.REPORT_SPECS, edits, strict=True):
                added_title = f"New {spec.name}"
                assert edit.summary == (
                    f"2 items total. Added «[[:en:{added_title}]]»; "
                    "removed «[[:en:Gone]]»."
                )
                assert "page-id" not in edit.text
                assert "en=Renamed" in edit.text
                assert "wd=9" in edit.text
                assert "en=Old title" not in edit.text
                added_id = 103 if spec.name == "important" else 104
                assert load_membership(
                    context.site,
                    edit.title,
                    edit.text,
                ) == {
                    "Renamed": 101,
                    added_title: added_id,
                }
                assert (
                    load_membership(
                        context.site,
                        edit.title,
                        existing[spec.name],
                    )
                    == {}
                )

    @staticmethod
    def test_dry_run_keeps_original_cached_report_membership() -> None:
        """Preserve live membership and text hashes during previews."""
        context = Mock(site=OfflineSite("zh", "wikipedia"), dry=True)
        existing = _old_change_reports()
        old_members = {"Old title": 101, "Gone": 102}
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            _report_change_sources(context, existing),
        ):
            for name, title in job.REPORT_TARGETS.items():
                save_membership(
                    context.site,
                    title,
                    existing[name],
                    old_members,
                )
            job.run(dry=True, context=context)
            for spec, call in zip(
                job.REPORT_SPECS,
                context.publish.call_args_list,
                strict=True,
            ):
                edit = call.args[0]
                assert (
                    load_membership(
                        context.site,
                        edit.title,
                        existing[spec.name],
                    )
                    == old_members
                )
                assert (
                    load_membership(context.site, edit.title, edit.text) == {}
                )
                assert "Old title" not in edit.summary
                assert "Renamed" not in edit.summary

    @staticmethod
    def test_dry_run_seeds_only_exact_original_titles() -> None:
        """Seed IDs for exact original titles during previews."""
        context = Mock(site=OfflineSite("zh", "wikipedia"), dry=True)
        existing = _old_change_reports()
        existing = {
            name: text.replace("en=Old title|wd=1", "en=Renamed|wd=9")
            for name, text in existing.items()
        }
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            _report_change_sources(context, existing),
        ):
            job.run(dry=True, context=context)
            for name, title in job.REPORT_TARGETS.items():
                assert load_membership(
                    context.site,
                    title,
                    existing[name],
                ) == {
                    "Renamed": 101,
                    "Gone": None,
                }

    @staticmethod
    def test_failed_publication_preserves_cached_report_membership() -> None:
        """Keep the live snapshot after publication fails."""
        context = Mock(site=OfflineSite("zh", "wikipedia"), dry=False)
        context.publish.side_effect = RuntimeError("Publication failed.")
        existing = _old_change_reports()
        old_members = {"Old title": 101, "Gone": 102}
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            _report_change_sources(context, existing),
        ):
            for name, title in job.REPORT_TARGETS.items():
                save_membership(
                    context.site,
                    title,
                    existing[name],
                    old_members,
                )
            failure = None
            try:
                job.run(context=context)
            except RuntimeError as error:
                failure = error
            assert failure is not None
            assert str(failure) == "Publication failed."
            for name, title in job.REPORT_TARGETS.items():
                assert load_membership(
                    context.site,
                    title,
                    existing[name],
                ) == (old_members)
            context.publish.assert_called_once()
            edit = context.publish.call_args.args[0]
            assert load_membership(context.site, edit.title, edit.text) == {}
