"""Verify configured publication targets use backend-prepared data."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import date
from importlib import reload
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from backend.services.enwiki.key_articles import ReportData
from backend.support.report_membership import MembershipReport
from shared.config import TASK_DEFINITIONS
from shared.support.regions import managed_region
from updater.jobs import assessment_lists, dyks, enwp_key_articles, new_pages


def _context() -> Mock:
    """Provide a live publisher with explicit confirmed-save behavior.

    Returns:
        Isolated context targeting Chinese Wikipedia.
    """
    context = Mock(today=date(2026, 10, 3), dry=False)
    context.site.dbName.return_value = "zhwiki"
    context.publish.return_value = True
    return context


def _new_pages(context: Mock) -> Mock:
    """Provide backend discovery metadata without making source reads.

    Args:
        context: Context whose anchor date defines the completed record.

    Returns:
        Prepared daily facts sufficient for publication coordination.
    """
    return Mock(
        filled_dates={context.today},
        latest_date=context.today,
        articles=1,
        non_articles=0,
        result=None,
    )


class TestJobDestinations(TestCase):
    """Check target overrides preserve backend data scope."""

    def setUp(self) -> None:
        """Isolate disposable membership checkpoints."""
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        )

    @staticmethod
    def test_configured_destination_becomes_the_job_default() -> None:
        """Use central configuration when a job is loaded."""
        context = _context()
        target = "User:Example/Configured new pages"
        definition = replace(
            TASK_DEFINITIONS["new_pages"],
            pages={"report": target},
        )
        try:
            with patch.dict(TASK_DEFINITIONS, {"new_pages": definition}):
                reload(new_pages)
                with (
                    patch.object(
                        new_pages,
                        "job_run",
                        return_value=nullcontext(context),
                    ),
                    patch.object(
                        new_pages,
                        "read_pages",
                        return_value=[
                            Mock(text=managed_region("new-pages", "")),
                        ],
                    ) as read,
                    patch.object(
                        new_pages.data,
                        "prepare_report",
                        return_value=_new_pages(context),
                    ),
                    patch.object(new_pages.data, "completion"),
                    patch.object(
                        new_pages,
                        "update_text",
                        return_value=managed_region("new-pages", "Updated"),
                    ),
                ):
                    new_pages.run(context=context)
                read.assert_called_once_with(context.site, [target])
                assert context.publish.call_args.args[0].title == target
        finally:
            reload(new_pages)

    @staticmethod
    def test_new_pages_custom_destination_preserves_keyword_scope() -> None:
        """Pass destination text to backend and keep discovery scope."""
        context = _context()
        target = "User:Example/New pages"
        original = managed_region("new-pages", "Existing")
        updated = managed_region("new-pages", "Updated")
        prepared = _new_pages(context)
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
                new_pages.data,
                "prepare_report",
                return_value=prepared,
            ) as prepare,
            patch.object(new_pages.data, "completion"),
            patch.object(
                new_pages,
                "update_text",
                return_value=updated,
            ) as render,
        ):
            new_pages.run(title=target, context=context)
        read.assert_called_once_with(context.site, [target])
        assert prepare.call_args.args == (
            original,
            context.site,
            context.today,
        )
        assert prepare.call_args.kwargs["project"] == "电子游戏"
        render.assert_called_once_with(original, prepared)
        edit = context.publish.call_args.args[0]
        assert (edit.title, edit.text, edit.original_text) == (
            target,
            updated,
            original,
        )

    @staticmethod
    def test_dyk_destination_is_used_in_statistics_source() -> None:
        """Keep report and statistics targets in rendering settings."""
        context = _context()
        target = "User:Example/DYK report"
        statistics = "c:Data:Example/DYK.tab"
        original = "Existing DYK content"
        changes = Mock(
            articles=1,
            nominees=0,
            new=(),
            passed=(),
            failed=(),
            removed=(),
        )
        prepared = Mock(result=None, changes=changes)
        with (
            patch.object(dyks, "job_run", return_value=nullcontext(context)),
            patch.object(
                dyks,
                "read_pages",
                return_value=[Mock(text=original)],
            ) as read,
            patch.object(
                dyks.data,
                "prepare_report",
                return_value=prepared,
            ) as prepare,
            patch.object(
                dyks,
                "update_text",
                return_value="Updated DYK content",
            ) as render,
            patch.object(dyks.data, "record_publication") as record,
        ):
            dyks.run(
                title=target,
                statistics_title=statistics,
                context=context,
            )
        read.assert_called_once_with(context.site, [target])
        prepare.assert_called_once_with(
            original,
            context.site,
            context.today,
            project="电子游戏",
            report_title=target,
        )
        render.assert_called_once_with(
            original,
            prepared,
            report_title=target,
            statistics_title=statistics,
        )
        assert context.publish.call_args.args[0].title == target
        assert record.call_args.args[2] == target

    @staticmethod
    def test_assessment_custom_destination_preserves_category() -> None:
        """Keep discovery in backend when overriding the edit target."""
        context = _context()
        target = "User:Example/Assessment report"
        config = assessment_lists.ASSESSMENT_LISTS[0][1]
        original = managed_region("assessment-list", "")
        assessments = pl.DataFrame()
        prepared = Mock(changes=Mock(added=(), removed=(), count=0))
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
                "read_assessments",
                return_value=assessments,
            ),
            patch.object(
                assessment_lists,
                "prepare_data",
                return_value=prepared,
            ) as prepare,
            patch.object(
                assessment_lists,
                "prepare_report",
                return_value=MembershipReport(original, {}),
            ) as render,
            patch.object(assessment_lists, "load_membership", return_value={}),
            patch.object(assessment_lists, "save_membership") as save,
        ):
            assessment_lists.run(lists=[(target, config)], context=context)
        read.assert_called_once_with(context.site, [target])
        prepare.assert_called_once_with(
            context.site,
            config,
            original,
            assessments=assessments,
            previous_members={},
        )
        render.assert_called_once_with(prepared, original)
        assert context.publish.call_args.args[0].title == target
        save.assert_called_once_with(context.site, target, original, {})

    @staticmethod
    def test_english_destinations_preserve_report_specs() -> None:
        """Preserve selection and layout when overriding targets."""
        context = _context()
        targets = {
            name: f"User:Example/{name}"
            for name in enwp_key_articles.REPORT_TARGETS
        }
        data = ReportData(
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
                enwp_key_articles,
                "prepare_reports",
                return_value=data,
            ) as prepare,
            patch.object(
                enwp_key_articles.reports,
                "build_reports",
                return_value=dict.fromkeys(targets, "new"),
            ),
            patch.object(
                enwp_key_articles.reports,
                "build_edit_summary",
                return_value="updated",
            ),
        ):
            enwp_key_articles.run(targets=targets, context=context)
        read.assert_called_once_with(context.site, list(targets.values()))
        assert prepare.call_args.args[1] == tuple(
            spec.definition for spec in enwp_key_articles.REPORT_SPECS
        )
        assert [
            call.args[0].title for call in context.publish.call_args_list
        ] == list(targets.values())
