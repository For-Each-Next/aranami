"""Verify metadata around report preparation, storage, and saves."""

# Preserve the suite's standard unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
import json
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from backend.support.execution import RuntimeContext, routine_run
from backend.support.report_status import report_update_key
from shared.reports import ReportBundle, ReportResult, ReportStore, report_key
from updater.jobs import JobContext, ProposedEdit, dyks


def _context(*, dry: bool = False) -> JobContext:
    """Create a publication context with explicit offline wiki identity.

    Returns:
        Frontend publisher using backend lifecycle and storage behavior.
    """
    site = Mock()
    site.dbName.return_value = "zhwiki"
    return JobContext(site, dt.date(2026, 10, 20), dry=dry)


def _edit(context: RuntimeContext, title: str) -> ProposedEdit:
    """Build one complete structured proposal without source access.

    Args:
        context: Caller selecting the destination wiki.
        title: Exact destination title recorded in status and storage.

    Returns:
        Proposal whose prepared result requires durable storage.
    """
    return ProposedEdit(
        site=context.site,
        title=title,
        text="Prepared report text",
        summary="Updated report.",
        original_text="Previous report text",
        report_key=report_key("fixture", "zhwiki", title),
        result=ReportResult(
            "fixture",
            {"articles": pl.DataFrame({"title": ["Example"]})},
        ),
    )


class TestReportLifecycle(TestCase):
    """Retain page outcomes independently of diagnostic logs."""

    @override
    def setUp(self) -> None:
        """Isolate outputs while keeping real publication tracking."""
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.enterContext(
            patch(
                "backend.support.execution.open_run_log",
                return_value=nullcontext(),
            ),
        )
        self.enterContext(
            patch(
                "updater.jobs._execution.open_run_log",
                return_value=nullcontext(),
            ),
        )
        self.enterContext(
            patch("backend.support.execution.write_routine_status"),
        )

    def _status(self, title: str) -> dict[str, object]:
        """Read authoritative metadata for one destination.

        Returns:
            Exact backend record without interpreting diagnostic logs.
        """
        snapshot = json.loads(
            (self.root / "state/report-updates.json").read_text(),
        )
        return snapshot["reports"][report_update_key("zhwiki", title)]

    def test_destination_read_failure_records_start_and_failed_result(
        self,
    ) -> None:
        """Keep attempted targets when destination reads fail."""
        context = _context()
        title = "Wikipedia:DYK fixture"
        failure = RuntimeError("destination unavailable")

        def fail_read(*_args: object) -> None:
            """Require a start record before a read failure."""
            assert self._status(title)["status"] == "running"
            raise failure

        with (
            patch.object(dyks, "read_pages", side_effect=fail_read),
            patch.object(dyks.data, "prepare_report") as prepare,
            self.assertRaisesRegex(RuntimeError, "destination unavailable"),
        ):
            dyks.run(context=context, title=title)
        record = self._status(title)
        assert record["status"] == "failed"
        assert record["confirmed_edits"] == 0
        assert record["finished_at"] is not None
        assert record["run_id"] == context.tasks[0].run_id
        prepare.assert_not_called()

    def test_backend_preparation_failure_records_known_zero_edits(
        self,
    ) -> None:
        """Record preparation errors before rendering or publication."""
        context = _context()
        title = "Wikipedia:Preparation fixture"
        with (
            patch.object(
                dyks,
                "read_pages",
                return_value=[Mock(text="Existing")],
            ),
            patch.object(
                dyks.data,
                "prepare_report",
                side_effect=RuntimeError("source failed"),
            ),
            patch.object(dyks, "update_text") as render,
            self.assertRaisesRegex(RuntimeError, "source failed"),
        ):
            dyks.run(context=context, title=title)
        assert self._status(title)["status"] == "failed"
        assert self._status(title)["confirmed_edits"] == 0
        render.assert_not_called()

    def test_no_proposal_and_deferred_preparation_have_explicit_outcomes(
        self,
    ) -> None:
        """Finalize checks and unavailable data without edits."""
        for outcome in ("no_update", "deferred"):
            context = _context()
            title = f"Wikipedia:{outcome} fixture"
            with (
                self.subTest(outcome=outcome),
                routine_run("pageviews", context),
            ):
                context.register_targets([title])
                assert self._status(title)["status"] == "running"
                if outcome == "deferred":
                    context.defer("Source not ready")
            record = self._status(title)
            assert record["status"] == outcome
            assert record["confirmed_edits"] == 0
            assert record["finished_at"] is not None

    def test_required_results_exist_before_confirmed_save_or_unchanged(
        self,
    ) -> None:
        """Retain data before publication and count actual edits."""
        for outcome, status, edits in (
            ("saved", "updated", 1),
            ("unchanged", "no_update", 0),
        ):
            context = _context()
            title = f"Wikipedia:{outcome} fixture"
            proposal = _edit(context, title)

            def save(edit: ProposedEdit, expected: str = outcome) -> str:
                """Confirm retention before a simulated wiki result.

                Returns:
                    Explicit saved or unchanged publication evidence.
                """
                assert self._status(edit.title)["status"] == "running"
                directory = self.root / "reports" / edit.report_key
                selected = json.loads((directory / "latest.json").read_text())
                generation = directory / selected["latest"]
                manifest = json.loads(
                    (generation / "manifest.json").read_text(),
                )
                assert (
                    generation / "report.wikitext"
                ).read_text() == edit.text
                assert manifest["publication"]["state"] == "unconfirmed"
                return expected

            with self.subTest(outcome=outcome), routine_run("dyks", context):
                context.register_targets([title])
                assert context.publish_proposal(proposal, save) is True
            record = self._status(title)
            assert record["status"] == status
            assert record["confirmed_edits"] == edits
            assert record["finished_at"] is not None

    def test_save_failure_keeps_unknown_edit_count_and_prepared_data(
        self,
    ) -> None:
        """Retain uncertain saves with unknown confirmed effects."""
        context = _context()
        title = "Wikipedia:Failed save fixture"
        proposal = _edit(context, title)
        save = Mock(side_effect=RuntimeError("save failed"))
        with (
            self.assertRaisesRegex(RuntimeError, "save failed"),
            routine_run("dyks", context),
        ):
            context.register_targets([title])
            context.publish_proposal(proposal, save)
        save.assert_called_once_with(proposal)
        record = self._status(title)
        assert record["status"] == "failed"
        assert record["confirmed_edits"] is None
        bundle = ReportStore().read(proposal.report_key)
        assert bundle is not None
        assert bundle.manifest["publication"]["state"] == "unconfirmed"

    def test_required_storage_failure_prevents_save_and_records_zero_edits(
        self,
    ) -> None:
        """Fail before publication when data cannot be stored."""
        context = _context()
        title = "Wikipedia:Storage fixture"
        save = Mock()
        with (
            patch.object(
                ReportStore,
                "write",
                side_effect=OSError("storage failed"),
            ),
            self.assertRaisesRegex(OSError, "storage failed"),
            routine_run("dyks", context),
        ):
            context.register_targets([title])
            context.publish_proposal(_edit(context, title), save)
        save.assert_not_called()
        record = self._status(title)
        assert record["status"] == "failed"
        assert record["confirmed_edits"] == 0

    def test_later_storage_failure_does_not_erase_a_confirmed_save(
        self,
    ) -> None:
        """Keep wiki success if later generation metadata fails."""
        context = _context()
        title = "Wikipedia:Saved metadata fixture"
        proposal = _edit(context, title)
        original = ReportStore.set_publication
        failure = OSError("outcome storage failed")

        def store_outcome(
            store: ReportStore,
            bundle: ReportBundle,
            state: str,
            **options: object,
        ) -> None:
            """Persist initial state and fail the outcome write."""
            if state == "saved":
                raise failure
            original(store, bundle, state, **options)

        save = Mock(return_value="saved")
        with (
            patch.object(
                ReportStore,
                "set_publication",
                autospec=True,
                side_effect=store_outcome,
            ),
            self.assertRaisesRegex(OSError, "outcome storage failed"),
            routine_run("dyks", context),
        ):
            context.register_targets([title])
            context.publish_proposal(proposal, save)
        save.assert_called_once_with(proposal)
        assert self._status(title)["status"] == "updated"
        assert self._status(title)["confirmed_edits"] == 1

    def test_first_target_success_survives_later_preparation_failure(
        self,
    ) -> None:
        """Keep completed pages when another target fails."""
        context = _context()
        first, second = "Wikipedia:First fixture", "Wikipedia:Second fixture"
        failure = RuntimeError("second preparation failed")
        with (
            self.assertRaisesRegex(RuntimeError, "second preparation failed"),
            routine_run("assessment_lists", context),
        ):
            context.register_targets([first, second])
            assert context.publish_proposal(
                _edit(context, first),
                Mock(return_value="saved"),
            )
            raise failure
        assert self._status(first)["status"] == "updated"
        assert self._status(first)["confirmed_edits"] == 1
        assert self._status(second)["status"] == "failed"
        assert self._status(second)["confirmed_edits"] == 0

    def test_preview_keeps_only_preview_data_and_no_public_status(
        self,
    ) -> None:
        """Keep dry-run datasets without public state or wiki writes."""
        context = _context(dry=True)
        title = "Wikipedia:Preview fixture"
        save = Mock()
        with routine_run("dyks", context):
            context.register_targets([title])
            assert (
                context.publish_proposal(_edit(context, title), save) is False
            )
        save.assert_not_called()
        assert not (self.root / "state").exists()
        assert not (self.root / "reports").exists()
        assert (self.root / "dry-run/reports").is_dir()
