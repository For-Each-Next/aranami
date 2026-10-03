"""Verify publication, cache clearing, and delegated actions."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from aranami.jobs import JobContext, ProposedEdit, job_run, pexbot
from aranami.support.cache import clear_runtime_cache


def _fail(message: str) -> None:
    """Simulate a routine failure for lifecycle outcome assertions.

    Args:
        message: Diagnostic that should appear in the summary.

    Raises:
        RuntimeError: Always, to model an unavailable source.
    """
    raise RuntimeError(message)


class TestRoutineOutput(TestCase):
    """Verify dry runs and cache maintenance cannot edit Wikipedia."""

    @staticmethod
    def test_dry_run_combines_edits_in_one_markdown_file() -> None:
        """Link exact proposals without embedding their contents."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        context = JobContext(
            site,
            dt.date(2026, 10, 3),
            dry_run=True,
            started_at=dt.datetime(2026, 10, 3, 1, 2, 3, tzinfo=dt.UTC),
            finished_at=dt.datetime(2026, 10, 3, 1, 3, 4, tzinfo=dt.UTC),
        )
        first_text = "```\n维基百科\ntext\n"
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch("aranami.jobs._execution.pywikibot.Page") as page,
        ):
            context.publish(
                ProposedEdit(
                    site,
                    "First",
                    first_text,
                    "first summary",
                    ("report",),
                ),
            )
            context.publish(
                ProposedEdit(site, "Second", "body", "second summary"),
            )
            path = context.write_report()
            assert path is not None
            content = path.read_text(encoding="utf-8")
            assert "First" in content
            assert "Second" in content
            assert "zhwiki" in content
            assert "first summary" in content
            assert content.startswith("# Aranami dry run — 2026-10-03 UTC\n")
            assert "- start: 2026-10-03 01:02:03 UTC" in content
            assert "- end: 2026-10-03 01:03:04 UTC" in content
            assert (
                "- status: success\n  - task 1: proposed edits — success"
                in content
            )
            assert "- tags: report" in content
            assert first_text not in content
            assert "```" not in content
            first_output = path.with_name(f"{path.stem}-001.wikitext")
            second_output = path.with_name(f"{path.stem}-002.wikitext")
            assert first_output.read_bytes() == first_text.encode("utf-8")
            assert second_output.read_bytes() == b"body"
            assert f"]({first_output.name})" in content
            assert f"]({second_output.name})" in content
            assert set(path.parent.iterdir()) == {
                path,
                first_output,
                second_output,
            }
            page.assert_not_called()

    @staticmethod
    def test_wikitext_paths_are_flat_and_independent_of_page_titles() -> None:
        """Keep unsafe titles and empty text in uniquely named files."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        context = JobContext(site, dt.date(2026, 10, 3), dry_run=True)
        context.publish(
            ProposedEdit(site, "../../outside/页面", "", "summary"),
        )
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        ):
            report = context.write_report()
            assert report is not None
            artifact = report.with_name(f"{report.stem}-001.wikitext")
            assert artifact.parent == Path(directory) / "dry-run"
            assert artifact.read_bytes() == b""
            assert set(artifact.parent.iterdir()) == {report, artifact}
            assert "../../outside/页面" in report.read_text(encoding="utf-8")
            assert not (Path(directory) / "outside").exists()

    @staticmethod
    def test_repeated_reports_preserve_previous_wikitext() -> None:
        """Preserve earlier proposals across repeated output calls."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        context = JobContext(site, dt.date(2026, 10, 3), dry_run=True)
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        ):
            context.publish(ProposedEdit(site, "Target", "first", "summary"))
            first_report = context.write_report()
            context.edits.clear()
            context.publish(ProposedEdit(site, "Target", "second", "summary"))
            second_report = context.write_report()
            assert first_report is not None
            assert second_report is not None
            assert first_report != second_report
            first_output = first_report.with_name(
                f"{first_report.stem}-001.wikitext",
            )
            second_output = second_report.with_name(
                f"{second_report.stem}-001.wikitext",
            )
            assert first_output.read_bytes() == b"first"
            assert second_output.read_bytes() == b"second"
            assert set(first_report.parent.iterdir()) == {
                first_report,
                second_report,
                first_output,
                second_output,
            }

    @staticmethod
    def test_live_report_writes_no_local_artifacts() -> None:
        """Avoid creating dry-run output during live execution."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry_run=False)
        context.edits.append(
            ProposedEdit(context.site, "Target", "text", "summary"),
        )
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        ):
            assert context.write_report() is None
            assert not (Path(directory) / "dry-run").exists()

    def test_live_publish_checks_concurrent_changes(self) -> None:
        """Refuse to overwrite a concurrently modified page."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry_run=False)
        page = Mock(text="someone else's update")
        with (
            patch("aranami.jobs._execution.pywikibot.Page", return_value=page),
            self.assertRaises(RuntimeError),
        ):
            context.publish(
                ProposedEdit(
                    context.site,
                    "Target",
                    "new",
                    "summary",
                    original_text="old",
                ),
            )
        page.save.assert_not_called()

    @staticmethod
    def test_live_publish_and_unchanged_skip() -> None:
        """Save changed text and avoid redundant writes."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry_run=False)
        page = Mock(text="old")
        with patch(
            "aranami.jobs._execution.pywikibot.Page",
            return_value=page,
        ):
            context.publish(
                ProposedEdit(
                    context.site,
                    "Target",
                    "new",
                    "summary",
                    original_text="old",
                ),
            )
            context.publish(
                ProposedEdit(
                    context.site,
                    "Target",
                    "new",
                    "summary",
                    original_text="new",
                ),
            )
        page.save.assert_called_once_with(summary="summary")
        assert page.text == "new"

    @staticmethod
    def test_clear_cache_leaves_logs_and_reports() -> None:
        """Remove cached data while retaining durable output files."""
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        ):
            root = Path(directory)
            for name in ("cache", "logs", "dry-run"):
                (root / name).mkdir()
                (root / name / "keep.txt").write_text("data", encoding="utf-8")
            clear_runtime_cache()
            clear_runtime_cache()
            assert not (root / "cache").exists()
            assert (root / "logs" / "keep.txt").exists()
            assert (root / "dry-run" / "keep.txt").exists()

    @staticmethod
    def test_delegated_refresh_is_never_called_in_dry_run() -> None:
        """Group PexBot targets into one task without write requests."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry_run=True)
        context.site.dbName.return_value = "zhwiki"
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch.object(
                pexbot,
                "subscribed_titles",
                return_value=["Target", "Second target"],
            ),
            patch.object(pexbot, "_refresh") as refresh,
        ):
            pexbot.run(context=context)
            report = context.write_report()
            assert report is not None
            content = report.read_text(encoding="utf-8")
            assert content.count("  - task ") == 1
            assert "  - task 1: pexbot — success" in content
            assert "Target" in content
            assert "Second target" in content
            assert not list(report.parent.glob("*.wikitext"))
        refresh.assert_not_called()
        assert "Target" in context.notes[0]

    @staticmethod
    def test_partial_failure_keeps_successful_and_failed_task_proposals() -> (
        None
    ):
        """Retain outputs and error details after a routine failure."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry_run=True)
        context.site.dbName.return_value = "zhwiki"
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        ):
            with job_run("dyks", context=context):
                context.publish(
                    ProposedEdit(context.site, "DYK", "completed", "summary"),
                )
            try:
                with job_run("pageviews", context=context):
                    context.publish(
                        ProposedEdit(
                            context.site,
                            "Pageviews",
                            "partial proposal",
                            "summary",
                        ),
                    )
                    _fail("HTTP Error 504: Gateway Timeout")
            except RuntimeError:
                pass
            report = context.write_report()
            assert report is not None
            content = report.read_text(encoding="utf-8")
            assert "- status: partly success" in content
            assert "  - task 1: dyks — success" in content
            assert "  - task 2: pageviews — failed" in content
            assert "RuntimeError: HTTP Error 504: Gateway Timeout" in content
            assert "partial proposal" not in content
            assert (
                report.with_name(f"{report.stem}-001.wikitext").read_text(
                    encoding="utf-8",
                )
                == "completed"
            )
            assert (
                report.with_name(f"{report.stem}-002.wikitext").read_text(
                    encoding="utf-8",
                )
                == "partial proposal"
            )
            assert all(task.finished_at is not None for task in context.tasks)
            assert all(task.elapsed_seconds >= 0 for task in context.tasks)

    @staticmethod
    def test_all_failed_tasks_report_failed() -> None:
        """Compute the overall outcome from failed routine results."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry_run=True)
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        ):
            for name in ("dyks", "pageviews"):
                try:
                    with job_run(name, context=context):
                        _fail("Source unavailable.")
                except RuntimeError:
                    pass
            report = context.write_report()
            assert report is not None
            content = report.read_text(encoding="utf-8")
            assert "- status: failed\n" in content
            assert "  - task 1: dyks — failed" in content
            assert "  - task 2: pageviews — failed" in content
            assert not list(report.parent.glob("*.wikitext"))

    @staticmethod
    def test_deferred_task_reports_partial_success_without_exception() -> None:
        """Keep a postponed routine distinct from completed work."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry_run=True)
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        ):
            with job_run("pageviews", context=context):
                context.defer("HTTP Error 504; retry during the next run.")
            with job_run("new_pages", context=context):
                pass
            report = context.write_report()
            assert report is not None
            content = report.read_text(encoding="utf-8")
            assert "- status: partly success" in content
            assert "  - task 1: pageviews — deferred" in content
            assert r"  - task 2: new\_pages — success" in content
            assert "HTTP Error 504" in content

    def test_deferred_result_requires_a_routine(self) -> None:
        """Require a routine before recording its deferred result."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry_run=True)
        with self.assertRaises(RuntimeError):
            context.defer("Source unavailable.")

    @staticmethod
    def test_standalone_routine_writes_its_one_completed_task() -> None:
        """Finalize one standalone routine's timing and output."""
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch("aranami.jobs._execution.pywikibot.Site") as site,
        ):
            site.return_value.dbName.return_value = "zhwiki"
            with job_run("dyks", dry_run=True) as context:
                context.publish(
                    ProposedEdit(context.site, "Target", "text", "summary"),
                )
            report = next((Path(directory) / "dry-run").glob("*.md"))
            content = report.read_text(encoding="utf-8")
            assert "- status: success" in content
            assert content.count("  - task ") == 1
            assert "  - task 1: dyks — success" in content
            assert context.finished_at is not None
