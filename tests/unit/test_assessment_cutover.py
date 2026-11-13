"""Verify daily snapshot comparisons start at the new baseline."""

# Reuse established offline report fixtures and their storage contracts.
# ruff: file-ignore[private-member-access]

import datetime as dt
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

import test_assessment_log as fixtures

from tasks.zhwiki import assessment_log as source
from updater.services.zhwiki.assessment_log import update_text

if TYPE_CHECKING:
    import polars as pl

_START = dt.datetime(2026, 11, 3, tzinfo=dt.UTC)


class AssessmentCutoverTests(fixtures._AssessmentLogFixture):
    """Preserve pending text while starting daily comparisons."""

    def _daily(
        self,
        members: pl.DataFrame,
        *,
        now: dt.datetime = _START,
        previous: Mock | None = None,
    ) -> source.AssessmentLogData:
        """Prepare new daily facts with a selected adjacent snapshot.

        Returns:
            Prepared report retaining real durable publication state.
        """
        snapshot = fixtures.TestCompactAssessmentLog._snapshot(members, now)
        snapshot.manifest["family"] = "project-data"
        with (
            patch.object(source, "get_snapshot", return_value=snapshot),
            patch.object(
                source,
                "read_previous_snapshot",
                return_value=previous,
            ),
            patch.object(
                source,
                "page_metadata",
                return_value=fixtures._replica_rows(members),
            ),
        ):
            return source.prepare_data(
                self.site,
                "电子游戏",
                fixtures._TITLE,
                fixtures._TEXT,
                observed_at=now,
            )

    def test_first_daily_file_seeds_despite_existing_legacy_report(
        self,
    ) -> None:
        """Exclude all changes before the new series' first boundary."""
        old = fixtures._members((1, "Existing", "B", "High"))
        current = fixtures._members((2, "New baseline", "C", "Low"))
        self._retain(
            self._prepare(old, observed_at=_START - dt.timedelta(days=1)),
            fixtures._TEXT,
            1,
        )
        prepared = self._daily(current)
        assert prepared.changes.is_empty()
        assert not prepared.unchanged
        assert update_text(fixtures._TEXT, prepared) == fixtures._TEXT

    def test_november_fourth_compares_adjacent_daily_members(self) -> None:
        """Use November 3 facts even when the old report is older."""
        old = fixtures._members((1, "Existing", "Stub", "Low"))
        baseline = fixtures._members((1, "Existing", "B", "Low"))
        current = fixtures._members((1, "Existing", "GA", "High"))
        self._retain(
            self._prepare(old, observed_at=_START - dt.timedelta(days=1)),
            fixtures._TEXT,
            1,
        )
        prepared = self._daily(
            current,
            now=_START + dt.timedelta(days=1),
            previous=fixtures.TestCompactAssessmentLog._snapshot(
                baseline,
                _START,
            ),
        )
        assert prepared.changes["old_class"].to_list() == ["B"]
        assert prepared.changes["class"].to_list() == ["GA"]
        assert "2026年11月3日" in update_text(fixtures._TEXT, prepared)

    def test_missing_adjacent_day_seeds_without_claiming_no_changes(
        self,
    ) -> None:
        """Do not compare older reports across a missing daily file."""
        old = fixtures._members((1, "Existing", "B", "High"))
        current = fixtures._members((2, "After gap", "GA", "Low"))
        self._retain(
            self._daily(old),
            fixtures._TEXT,
            1,
        )
        prepared = self._daily(current, now=_START + dt.timedelta(days=2))
        assert prepared.changes.is_empty()
        assert not prepared.unchanged
        assert update_text(fixtures._TEXT, prepared) == fixtures._TEXT

    def test_first_daily_baseline_retains_unsaved_legacy_history(self) -> None:
        """Keep pending dated text without adding cutover changes."""
        old = fixtures._members((1, "Existing", "B", "High"))
        changed = fixtures._members((1, "Existing", "GA", "High"))
        current = fixtures._members((2, "New baseline", "C", "Low"))
        self._retain(
            self._prepare(old, observed_at=_START - dt.timedelta(days=2)),
            fixtures._TEXT,
            1,
        )
        pending = self._prepare(
            changed,
            observed_at=_START - dt.timedelta(days=1),
        )
        proposal = update_text(fixtures._TEXT, pending)
        self._retain(pending, proposal, 2, state="unconfirmed")
        prepared = self._daily(current)
        assert prepared.pending
        assert prepared.changes.is_empty()
        assert update_text(fixtures._TEXT, prepared) == proposal
