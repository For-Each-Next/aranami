"""Verify publication, cache clearing, and delegated actions."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]
# Prime glyphs are required in elapsed edit summaries.
# ruff: file-ignore[ambiguous-unicode-character-string]

import datetime as dt
import json
from contextlib import nullcontext
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
            dry=True,
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
        context = JobContext(site, dt.date(2026, 10, 3), dry=True)
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
        context = JobContext(site, dt.date(2026, 10, 3), dry=True)
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
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry=False)
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
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry=False)
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
        page = Mock(text="old")
        with (
            patch(
                "aranami.support.edit_summary.version",
                return_value="0.3.5",
            ),
            patch("aranami.jobs._execution.perf_counter", return_value=100),
            patch(
                "aranami.jobs._execution.pywikibot.Page",
                return_value=page,
            ),
        ):
            context = JobContext(Mock(), dt.date(2026, 10, 3), dry=False)
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
        page.save.assert_called_once_with(
            summary="summary ([[toolforge:aranami|Aranami 0.3.5]] in 0.00″)",
        )
        assert page.text == "new"

    @staticmethod
    def test_publish_records_timed_summary_in_live_and_dry_runs() -> None:
        """Keep recorded and published summaries identical."""
        for dry in (False, True):
            page = Mock(text="old")
            with (
                patch(
                    "aranami.support.edit_summary.version",
                    return_value="0.3.5",
                ),
                patch(
                    "aranami.jobs._execution.perf_counter",
                    return_value=100,
                ) as clock,
                patch(
                    "aranami.jobs._execution.pywikibot.Page",
                    return_value=page,
                ) as page_factory,
            ):
                context = JobContext(
                    Mock(),
                    dt.date(2026, 10, 3),
                    dry=dry,
                )
                context.site.dbName.return_value = "zhwiki"
                proposal = ProposedEdit(
                    context.site,
                    "Target",
                    "new",
                    "Updated for 5 May 2025.",
                    original_text="old",
                )
                clock.return_value = 1413.95
                context.publish(proposal)
                expected = (
                    "Updated for 5 May 2025. "
                    "([[toolforge:aranami|Aranami 0.3.5]] in 21′54″)"
                )
                assert context.edits[0].summary == expected
                assert proposal.summary == "Updated for 5 May 2025."
                if dry:
                    page_factory.assert_not_called()
                    with (
                        TemporaryDirectory() as directory,
                        patch(
                            "pathlib.Path.cwd",
                            return_value=Path(directory),
                        ),
                    ):
                        report = context.write_report()
                        assert report is not None
                        assert (
                            "Updated for 5 May 2025. "
                            r"(\[\[toolforge:aranami|Aranami 0.3.5\]\] "
                            "in 21′54″)"
                        ) in report.read_text(encoding="utf-8")
                else:
                    page.save.assert_called_once_with(summary=expected)

    @staticmethod
    def test_publish_times_each_proposal_from_its_own_routine() -> None:
        """Reset timing between routines and measure each proposal."""
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch(
                "aranami.support.edit_summary.version",
                return_value="0.3.5",
            ),
            patch(
                "aranami.jobs._execution.perf_counter",
                return_value=100,
            ) as clock,
        ):
            context = JobContext(Mock(), dt.date(2026, 10, 3), dry=True)
            clock.return_value = 200
            with job_run("first", context=context):
                clock.return_value = 260.5
                context.publish(
                    ProposedEdit(context.site, "First", "one", "summary"),
                )
                clock.return_value = 270
                context.publish(
                    ProposedEdit(context.site, "Second", "two", "summary"),
                )
            clock.return_value = 500
            with job_run("second", context=context):
                clock.return_value = 511.25
                context.publish(
                    ProposedEdit(context.site, "Third", "three", "summary"),
                )
            clock.return_value = 600
            context.publish(
                ProposedEdit(context.site, "Direct", "four", "summary"),
            )
        assert [edit.summary for edit in context.edits] == [
            "summary ([[toolforge:aranami|Aranami 0.3.5]] in 1′00″)",
            "summary ([[toolforge:aranami|Aranami 0.3.5]] in 1′10″)",
            "summary ([[toolforge:aranami|Aranami 0.3.5]] in 11.25″)",
            "summary ([[toolforge:aranami|Aranami 0.3.5]] in 8′20″)",
        ]
        assert [task.elapsed_seconds for task in context.tasks] == [70, 11.25]

    @staticmethod
    def test_publish_replaces_existing_execution_suffix() -> None:
        """Retain one suffix when a proposal is published again."""
        with (
            patch(
                "aranami.support.edit_summary.version",
                return_value="0.3.5",
            ),
            patch(
                "aranami.jobs._execution.perf_counter",
                return_value=100,
            ) as clock,
        ):
            context = JobContext(Mock(), dt.date(2026, 10, 3), dry=True)
            clock.return_value = 101
            context.publish(
                ProposedEdit(context.site, "Target", "text", "summary"),
            )
            clock.return_value = 102
            context.publish(context.edits[0])
        assert (
            context.edits[0].summary
            == "summary ([[toolforge:aranami|Aranami 0.3.5]] in 1.00″)"
        )
        assert (
            context.edits[1].summary
            == "summary ([[toolforge:aranami|Aranami 0.3.5]] in 2.00″)"
        )

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
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry=True)
        context.site.dbName.return_value = "zhwiki"
        targets = ["Target", "Second target"]
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch.object(
                pexbot,
                "subscribed_titles",
                return_value=targets,
            ),
            patch.object(
                pexbot,
                "edited_by_on_date",
                return_value=False,
            ) as history,
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
        assert history.call_count == len(targets)
        assert "Target" in context.notes[0]

    @staticmethod
    def test_partial_failure_keeps_successful_and_failed_task_proposals() -> (
        None
    ):
        """Retain outputs and error details after a routine failure."""
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry=True)
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
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry=True)
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
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry=True)
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
        context = JobContext(Mock(), dt.date(2026, 10, 3), dry=True)
        with self.assertRaises(RuntimeError):
            context.defer("Source unavailable.")

    def test_standalone_routine_finalizes_live_and_dry_output(self) -> None:
        """Finalize standalone output in the chosen mode."""
        for dry in (False, True):
            page = Mock(text="old")
            with (
                self.subTest(dry=dry),
                TemporaryDirectory() as directory,
                patch("pathlib.Path.cwd", return_value=Path(directory)),
                patch("aranami.jobs._execution.pywikibot.Site") as site,
                patch(
                    "aranami.jobs._execution.pywikibot.Page",
                    return_value=page,
                ) as page_factory,
            ):
                site.return_value.dbName.return_value = "zhwiki"
                with job_run("dyks", dry=dry) as context:
                    context.publish(
                        ProposedEdit(
                            context.site,
                            "Target",
                            "text",
                            "summary",
                        ),
                    )
                assert context.tasks[0].status == "success"
                if dry:
                    report = next(
                        (Path(directory) / "dry-run").glob("*.md"),
                    )
                    content = report.read_text(encoding="utf-8")
                    assert "- status: success" in content
                    assert content.count("  - task ") == 1
                    assert "  - task 1: dyks — success" in content
                    assert context.finished_at is not None
                    page_factory.assert_not_called()
                else:
                    page.save.assert_called_once_with(
                        summary=context.edits[0].summary,
                    )

                    assert not (Path(directory) / "dry-run").exists()

    def test_shared_context_overrides_conflicting_dry_keyword(self) -> None:
        """Honor shared mode and retain its pending report."""
        for dry in (False, True):
            page = Mock(text="old")
            context = JobContext(Mock(), dt.date(2026, 10, 3), dry=dry)
            with (
                self.subTest(dry=dry),
                TemporaryDirectory() as directory,
                patch("pathlib.Path.cwd", return_value=Path(directory)),
                patch("aranami.jobs._execution.pywikibot.Site") as site,
                patch(
                    "aranami.jobs._execution.pywikibot.Page",
                    return_value=page,
                ) as page_factory,
            ):
                with job_run("dyks", dry=not dry, context=context) as active:
                    assert active is context
                    active.publish(
                        ProposedEdit(
                            active.site,
                            "Target",
                            "text",
                            "summary",
                        ),
                    )
                site.assert_not_called()
                assert context.dry is dry
                assert context.tasks[0].status == "success"
                assert context.finished_at is None
                assert not (Path(directory) / "dry-run").exists()
                if dry:
                    page_factory.assert_not_called()
                else:
                    page.save.assert_called_once_with(
                        summary=context.edits[0].summary,
                    )


class TestRoutineStatusLifecycle(TestCase):
    """Persist live lifecycle state independently from logs."""

    def setUp(self) -> None:
        """Give each lifecycle test its own runtime directory."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        working_directory = patch("pathlib.Path.cwd", return_value=self.root)
        working_directory.start()
        self.addCleanup(working_directory.stop)

    def _record(self, name: str = "dyks") -> dict[str, object]:
        """Read one directly reported routine status.

        Args:
            name: Stable routine identifier in this test's snapshot.

        Returns:
            Most recently persisted lifecycle fields for the routine.
        """
        snapshot = json.loads(
            (self.root / "logs" / "aranami-status.json").read_text(),
        )
        assert snapshot["schema"] == 1
        assert snapshot["updated_at"].endswith("Z")
        return snapshot["routines"][name]

    def test_running_state_and_confirmed_save_share_one_run_identity(
        self,
    ) -> None:
        """Report a running routine and its confirmed save."""
        context = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
        page = Mock(text="old")
        with (
            patch(
                "aranami.jobs._execution.pywikibot.Page",
                return_value=page,
            ),
            job_run("dyks", context=context),
        ):
            running = self._record()
            assert running["status"] == "running"
            assert running["dry"] is False
            assert running["updated"] is None
            assert running["duration_seconds"] is None
            assert running["finished_at"] is None
            context.publish(
                ProposedEdit(
                    context.site,
                    "Target",
                    "new",
                    "summary",
                    original_text="old",
                ),
            )
        completed = self._record()
        assert completed["status"] == "updated"
        assert completed["updated"] == 1
        assert completed["run_id"] == running["run_id"]
        assert completed["started_at"] == running["started_at"]
        assert completed["finished_at"] >= running["started_at"]
        assert completed["duration_seconds"] >= 0
        page.save.assert_called_once()

    def test_terminal_states_preserve_confirmed_and_unknown_counts(
        self,
    ) -> None:
        """Keep terminal outcomes and uncertain counts distinct."""
        cases = (
            ("no_update", False, 0),
            ("unknown", True, None),
            ("deferred", False, 0),
            ("deferred", True, None),
            ("failed", False, 0),
            ("failed", True, None),
        )
        for expected_status, delegated, count in cases:
            context = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
            with (
                self.subTest(status=expected_status, delegated=delegated),
                self.assertRaisesRegex(RuntimeError, "Primary source failed")
                if expected_status == "failed"
                else nullcontext(),
                job_run("dyks", context=context),
            ):
                if delegated:
                    context.mark_updates_unknown()
                if expected_status == "deferred":
                    context.defer("Source data is not available yet.")
                elif expected_status == "failed":
                    _fail("Primary source failed.")
            record = self._record()
            assert record["status"] == expected_status
            assert record["updated"] == count
            assert record["finished_at"] is not None
            assert record["duration_seconds"] >= 0

    def test_preview_does_not_replace_public_live_state(self) -> None:
        """Omit previews despite conflicting invocation keywords."""
        live = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
        with job_run("dyks", context=live):
            pass
        path = self.root / "logs" / "aranami-status.json"
        before = path.read_bytes(), path.stat().st_mtime_ns
        preview = JobContext(Mock(), dt.date(2026, 10, 18), dry=True)
        with (
            patch("aranami.jobs._execution.write_routine_status") as writer,
            job_run("dyks", dry=False, context=preview),
        ):
            preview.mark_updates_unknown()
        writer.assert_not_called()
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        assert self._record()["run_id"] == live.tasks[0].run_id

    def test_older_context_cannot_overwrite_a_newer_run(self) -> None:
        """Retain the newer state when an older context finishes."""
        older = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
        newer = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
        with job_run("dyks", context=older):
            older.defer("The older attempt is still waiting.")
            with job_run("dyks", context=newer):
                assert self._record()["run_id"] == newer.tasks[0].run_id
            completed = self._record()
            assert completed["status"] == "no_update"
        assert self._record() == completed
        assert older.tasks[0].status == "deferred"

    def test_status_failures_preserve_saves_and_primary_exceptions(
        self,
    ) -> None:
        """Keep status failures outside the wiki result path."""
        for fail in (False, True):
            context = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
            page = Mock(text="old")
            primary = RuntimeError("The original wiki failure.")
            action = Mock(side_effect=primary if fail else None)
            with (
                self.subTest(fail=fail),
                patch(
                    "aranami.jobs._execution.write_routine_status",
                    side_effect=OSError("Status storage unavailable."),
                ) as writer,
                patch(
                    "aranami.jobs._execution.pywikibot.Page",
                    return_value=page,
                ),
                self.assertLogs("aranami.jobs._execution", level="WARNING"),
            ):
                observed_error = None
                try:
                    with job_run("dyks", context=context):
                        context.publish(
                            ProposedEdit(
                                context.site,
                                "Target",
                                "new",
                                "summary",
                                original_text="old",
                            ),
                        )
                        action()
                except RuntimeError as caught:
                    observed_error = caught
                assert observed_error is (primary if fail else None)
            page.save.assert_called_once()
            expected_writes = 2
            assert writer.call_count == expected_writes
            assert context.tasks[0].updated == 1
            assert context.tasks[0].status == ("failed" if fail else "success")
