"""Verify routine jobs own publication targets."""

from contextlib import nullcontext
from datetime import date
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from aranami.jobs import assessment_lists, dyks, enwp_key_articles, new_pages
from aranami.services.zhwiki import enwp_key_articles as english_reports


class TestJobDestinations(TestCase):
    """Check destination overrides preserve service scope."""

    @staticmethod
    def test_new_pages_custom_destination_preserves_keyword_scope() -> None:
        """Read and propose a custom page with the keyword scope."""
        context = Mock(today=date(2026, 10, 3))
        target = "User:Example/New pages"
        original = "Existing managed report"
        with (
            patch.object(
                new_pages,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                new_pages,
                "read_pages",
                return_value=[Mock(text=original)],
            ) as read,
            patch.object(
                new_pages,
                "update_text",
                return_value="Updated report",
            ) as update,
            patch.object(
                new_pages,
                "record_dates",
                side_effect=[{context.today}, set()],
            ),
        ):
            new_pages.run(title=target, context=context)
        read.assert_called_once_with(context.site, [target])
        update.assert_called_once_with(
            original,
            context.site,
            context.today,
            project="电子游戏",
        )
        edit = context.publish.call_args.args[0]
        assert edit.title == target
        assert edit.text == "Updated report"
        assert edit.original_text == original

    @staticmethod
    def test_dyk_destination_is_used_in_statistics_source() -> None:
        """Pass selected destinations into statistics generation."""
        context = Mock(today=date(2026, 10, 3))
        target = "User:Example/DYK report"
        statistics = "c:Data:Example/DYK.tab"
        original = "Existing DYK content"
        with (
            patch.object(dyks, "job_run", return_value=nullcontext(context)),
            patch.object(
                dyks,
                "read_pages",
                return_value=[Mock(text=original)],
            ) as read,
            patch.object(
                dyks,
                "update_text",
                return_value="Updated DYK content",
            ) as update,
        ):
            dyks.run(
                title=target,
                statistics_title=statistics,
                context=context,
            )
        read.assert_called_once_with(context.site, [target])
        update.assert_called_once_with(
            original,
            context.site,
            context.today,
            project="电子游戏",
            report_title=target,
            statistics_title=statistics,
        )
        assert context.publish.call_args.args[0].title == target

    @staticmethod
    def test_assessment_custom_destination_preserves_category() -> None:
        """Keep assessment configuration separate from its target."""
        context = Mock()
        target = "User:Example/Assessment report"
        config = assessment_lists.ASSESSMENT_LISTS[0][1]
        original = "<section begin=list />\n<section end=list />"
        with (
            patch.object(
                assessment_lists,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                assessment_lists,
                "read_pages",
                return_value=[Mock(text=original)],
            ) as read,
            patch.object(
                assessment_lists,
                "prepare_text",
                return_value=original,
            ) as prepare,
        ):
            assessment_lists.run(lists=[(target, config)], context=context)
        read.assert_called_once_with(context.site, [target])
        prepare.assert_called_once_with(context.site, config, original)
        assert context.publish.call_args.args[0].title == target

    @staticmethod
    def test_english_destinations_preserve_report_specs() -> None:
        """Preserve report membership when overriding destinations."""
        context = Mock()
        targets = {
            name: f"User:Example/{name}"
            for name in enwp_key_articles.REPORT_TARGETS
        }
        data = english_reports.ReportData(
            rows=pl.DataFrame(
                schema={"en_importance": pl.String, "en_class": pl.String},
            ),
            old_articles={name: {} for name in targets},
            linked_titles={},
        )
        with (
            patch.object(
                enwp_key_articles,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                enwp_key_articles,
                "read_pages",
                return_value=[Mock(text="old") for _ in targets],
            ) as read,
            patch.object(
                english_reports,
                "prepare_reports",
                return_value=data,
            ) as prepare,
            patch.object(
                english_reports,
                "build_reports",
                return_value=dict.fromkeys(targets, "new"),
            ),
            patch.object(
                english_reports,
                "build_edit_summary",
                return_value="updated",
            ),
        ):
            enwp_key_articles.run(targets=targets, context=context)
        read.assert_called_once_with(context.site, list(targets.values()))
        assert prepare.call_args.args[1] is enwp_key_articles.REPORT_SPECS
        assert [
            call.args[0].title for call in context.publish.call_args_list
        ] == list(targets.values())
