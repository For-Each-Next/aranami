"""Verify structured routine results count confirmed saves."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
import json
import logging
from contextlib import nullcontext
from functools import partial
from typing import override
from unittest import TestCase
from unittest.mock import Mock, patch

from aranami.jobs import JobContext, ProposedEdit, job_run, pexbot


def _results(records: list[logging.LogRecord]) -> list[dict[str, object]]:
    """Decode terminal results and verify their observable log origin.

    Args:
        records: Captured lifecycle and publication log records.

    Returns:
        Structured results in execution order.
    """
    prefix = "Routine result: "
    results = []
    for record in records:
        message = record.getMessage()
        assert not message.startswith(
            ("Routine finished", "Routine failed", "Routine deferred"),
        )
        if not message.startswith(prefix):
            continue
        assert record.funcName == "job_run"
        result = json.loads(message.removeprefix(prefix))
        assert record.name == f"aranami.jobs.{result['routine']}"
        assert result["schema"] == 1
        assert result["duration_seconds"] >= 0
        results.append(result)
    return results


class TestRoutineResults(TestCase):
    """Distinguish saves, skipped writes, and uncertain delegation."""

    @override
    def setUp(self) -> None:
        """Keep lifecycle logging and replace its filesystem sink."""
        for sink in (
            patch(
                "aranami.jobs._execution.open_run_log",
                side_effect=nullcontext,
            ),
            patch(
                "aranami.jobs._execution.log_project_event",
                return_value=True,
            ),
        ):
            sink.start()
            self.addCleanup(sink.stop)

    def test_successful_saves_are_separate_from_unchanged_proposals(
        self,
    ) -> None:
        """Count one save and reset the next routine's count."""
        context = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
        page = Mock(text="old")
        with (
            patch("aranami.jobs._execution.pywikibot.Page", return_value=page),
            self.assertLogs("aranami.jobs", level="INFO") as captured,
        ):
            with job_run("dyks", context=context):
                context.publish(
                    ProposedEdit(
                        context.site,
                        "Changed",
                        "new",
                        "summary",
                        original_text="old",
                    ),
                )
                context.publish(
                    ProposedEdit(
                        context.site,
                        "Unchanged proposal",
                        "same",
                        "summary",
                        original_text="same",
                    ),
                )
            with job_run("new_pages", context=context):
                context.publish(
                    ProposedEdit(
                        context.site,
                        "Already current",
                        "new",
                        "summary",
                    ),
                )
        results = _results(captured.records)
        assert [(row["status"], row["updated"]) for row in results] == [
            ("updated", 1),
            ("no_update", 0),
        ]
        assert all(row["dry"] is False for row in results)
        assert [edit.title for edit in context.edits] == [
            "Changed",
            "Unchanged proposal",
            "Already current",
        ]
        page.save.assert_called_once()

    def test_dry_run_records_suppressed_writes(self) -> None:
        """Mark previews explicitly without counting their proposals."""
        context = JobContext(Mock(), dt.date(2026, 10, 18), dry=True)
        with (
            patch("aranami.jobs._execution.pywikibot.Page") as page,
            self.assertLogs("aranami.jobs", level="INFO") as captured,
            job_run("dyks", dry=False, context=context),
        ):
            context.publish(
                ProposedEdit(context.site, "Target", "new", "summary"),
            )
        (result,) = _results(captured.records)
        assert result["dry"] is True
        assert result["status"] == "no_update"
        assert result["updated"] == 0
        page.assert_not_called()

    def test_failed_and_deferred_runs_keep_partial_saves(self) -> None:
        """Prioritize failures while preserving completed saves."""
        for status in ("failed", "deferred"):
            context = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
            action = Mock(
                side_effect=(
                    RuntimeError("Source failed after the first save.")
                    if status == "failed"
                    else partial(
                        context.defer,
                        "Source temporarily unavailable.",
                    )
                ),
            )
            with (
                self.subTest(status=status),
                patch(
                    "aranami.jobs._execution.pywikibot.Page",
                    return_value=Mock(text="old"),
                ),
                self.assertLogs("aranami.jobs", level="INFO") as captured,
                self.assertRaises(RuntimeError)
                if status == "failed"
                else nullcontext(),
                job_run("dyks", context=context),
            ):
                context.publish(
                    ProposedEdit(context.site, "Target", "new", "summary"),
                )
                action()
            (result,) = _results(captured.records)
            assert result["status"] == status
            assert result["updated"] == 1
            terminal = captured.records[-1]
            assert bool(terminal.exc_info) is (status == "failed")

    def test_failed_save_is_not_counted(self) -> None:
        """A raised save operation cannot become a confirmed update."""
        context = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
        page = Mock(text="old")
        page.save.side_effect = RuntimeError("Save rejected.")
        with (
            patch("aranami.jobs._execution.pywikibot.Page", return_value=page),
            self.assertLogs("aranami.jobs", level="INFO") as captured,
            self.assertRaises(RuntimeError),
            job_run("dyks", context=context),
        ):
            context.publish(
                ProposedEdit(context.site, "Target", "new", "summary"),
            )
        (result,) = _results(captured.records)
        assert result["status"] == "failed"
        assert result["updated"] == 0

    def test_delegated_refresh_never_claims_a_confirmed_edit(self) -> None:
        """Keep refresh completion unknown and skipped writes zero."""
        for dry, skipped, expected in (
            (False, False, "unknown"),
            (True, False, "no_update"),
            (False, True, "no_update"),
        ):
            site = Mock()
            site.dbName.return_value = "zhwiki"
            context = JobContext(site, dt.date(2026, 10, 18), dry=dry)
            with (
                self.subTest(dry=dry, skipped=skipped),
                patch.object(
                    pexbot,
                    "subscribed_titles",
                    return_value=["Target"],
                ),
                patch.object(
                    pexbot,
                    "edited_by_on_date",
                    return_value=skipped,
                ),
                patch.object(pexbot, "_refresh") as refresh,
                self.assertLogs("aranami.jobs", level="INFO") as captured,
            ):
                pexbot.run(context=context)
            (result,) = _results(captured.records)
            assert result["status"] == expected
            assert result["updated"] == (None if expected == "unknown" else 0)
            assert result["dry"] is dry
            assert refresh.call_count == int(not dry and not skipped)

    def test_delegated_failure_has_an_unknown_edit_count(self) -> None:
        """Keep uncertain remote writes distinct from zero."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        context = JobContext(site, dt.date(2026, 10, 18), dry=False)
        with (
            patch.object(pexbot, "subscribed_titles", return_value=["Target"]),
            patch.object(pexbot, "edited_by_on_date", return_value=False),
            patch.object(
                pexbot,
                "_refresh",
                side_effect=RuntimeError("Stream lost."),
            ),
            self.assertLogs("aranami.jobs", level="INFO") as captured,
            self.assertRaises(ExceptionGroup),
        ):
            pexbot.run(context=context)
        (result,) = _results(captured.records)
        assert result["status"] == "failed"
        assert result["updated"] is None
