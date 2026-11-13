"""Verify assessment-log identity changes and durable retry behavior."""

# Preserve Chinese punctuation and unittest exception assertions.
# ruff: file-ignore[ambiguous-unicode-character-string]
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import TestCase, addModuleCleanup
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.reports import ReportBundle, ReportStore, report_key
from runtime.jobs import JobContext, assessment_log as job
from tasks.zhwiki import assessment_log as source
from updater.edit_summary import MAX_EDIT_SUMMARY_BYTES, EditSummary
from updater.services.zhwiki.assessment_log import (
    build_edit_summary,
    update_text,
)

_NOW = dt.datetime(2026, 10, 25, 12, 34, tzinfo=dt.UTC)
_TITLE = "WikiProject:电子游戏/评级/日志"
_KEY = report_key("assessment-log", "zhwiki", _TITLE)
_TEXT = (
    'Introduction\n<!-- aranami begin="assessment-log" days="30" -->\n'
    '<!-- aranami end="assessment-log" -->\nFooter [[Unmanaged]]'
)
_SCHEMA = {
    "page_id": pl.Int64,
    "title": pl.String,
    "class": pl.String,
    "importance": pl.String,
}


def _members(
    *rows: tuple[int, str, str | None, str | None],
) -> pl.DataFrame:
    """Build explicitly typed membership observations.

    Args:
        rows: Subject page identities, titles, and assessments.

    Returns:
        Complete membership table, including typed empty results.
    """
    return pl.DataFrame(rows, schema=_SCHEMA, orient="row")


def _replica_rows(members: pl.DataFrame) -> pl.DataFrame:
    """Project complete observations into the existing source contract.

    Args:
        members: Deterministic subject identities and assessments.

    Returns:
        Replica-shaped rows without external requests.
    """
    namespace = (
        pl
        .col("title")
        .str.extract(r"^([^:]+):", 1)
        .replace_strict(
            {
                "Talk": 1,
                "User": 2,
                "Wikipedia": 4,
                "File": 6,
                "Template": 10,
                "Category": 14,
                "WikiProject": 102,
            },
            default=0,
            return_dtype=pl.Int64,
        )
    )
    return members.select(
        "page_id",
        namespace.alias("page_namespace"),
        pl
        .when(namespace != 0)
        .then(
            pl.col("title").str.replace(r"^[^:]+:", ""),
        )
        .otherwise(pl.col("title"))
        .alias("page_title"),
        pl.col("title").alias("full_title"),
        (pl.col("page_id") + 100).alias("talk_page_id"),
        pl.col("class").alias("pa_class"),
        pl.col("importance").alias("pa_importance"),
    )


def setUpModule() -> None:
    """Keep summary year fixtures independent of the actual UTC year."""
    clock = patch("updater.edit_summary._current_year", return_value=2026)
    clock.start()
    addModuleCleanup(clock.stop)


class TestAssessmentChanges(TestCase):
    """Compare project identities, titles, and assessments."""

    @staticmethod
    def test_rename_and_reassessment_keep_the_same_member() -> None:
        """Record renames and reassessments for one stable member."""
        previous = _members((1, "Old title", "C", "Low"))
        current = _members((1, "New title", "B", "High"))
        changes = source.compare_members(previous, current)
        assert set(changes["kind"]) == {"renamed", "reassessed"}
        assert set(changes["page_id"]) == {1}
        assert set(changes["old_title"]) == {"Old title"}
        assert set(changes["title"]) == {"New title"}
        assert set(changes["old_class"]) == {"C"}
        assert set(changes["class"]) == {"B"}
        assert set(changes["old_importance"]) == {"Low"}
        assert set(changes["importance"]) == {"High"}

    @staticmethod
    def test_recreated_title_is_removal_and_addition() -> None:
        """Distinguish replacement pages with matching titles."""
        changes = source.compare_members(
            _members((1, "Same title", "B", "High")),
            _members((2, "Same title", "Stub", "Low")),
        )
        assert dict(changes.select("kind", "page_id").iter_rows()) == {
            "removed": 1,
            "added": 2,
        }
        removed = changes.filter(pl.col("kind") == "removed")
        assert removed.item(0, "old_class") == "B"
        assert removed.item(0, "old_importance") == "High"

    @staticmethod
    def test_unknown_assessments_remain_null() -> None:
        """Preserve null grades when detecting new observations."""
        previous = _members((1, "Unknown", None, None))
        assert source.compare_members(previous, previous).is_empty()
        changes = source.compare_members(
            previous,
            _members((1, "Unknown", "Start", None)),
        )
        assert changes["kind"].to_list() == ["reassessed"]
        assert changes.item(0, "old_class") is None
        assert changes.item(0, "old_importance") is None
        assert changes.item(0, "importance") is None

    @staticmethod
    def test_empty_current_set_preserves_every_removed_assessment() -> None:
        """Keep every former assessment when all members leave."""
        changes = source.compare_members(
            _members((1, "One", "FA", "Top"), (2, "Two", None, None)),
            _members(),
        )
        assert set(changes["kind"]) == {"removed"}
        assert set(changes["old_title"]) == {"One", "Two"}
        assert (
            changes.filter(pl.col("page_id") == 1).item(
                0,
                "old_class",
            )
            == "FA"
        )

    @staticmethod
    def test_compact_assessments_compare_without_titles() -> None:
        """Compare stable IDs and grades before resolving names."""
        previous = _members(
            (1, "Removed", None, None),
            (2, "Same", "C", "Low"),
            (3, "Unchanged", "B", "High"),
        ).drop("title")
        current = _members(
            (2, "Renamed", "B", "High"),
            (3, "Unchanged", "B", "High"),
            (4, "Added", None, None),
        ).drop("title")
        changes = source.compare_assessments(previous, current)
        assert dict(changes.select("page_id", "kind").iter_rows()) == {
            1: "removed",
            2: "reassessed",
            4: "added",
        }
        assert changes["title"].null_count() == changes.height
        assert changes["old_title"].null_count() == changes.height


class _AssessmentLogFixture(TestCase):
    """Prepare isolated bundles and offline wiki observations."""

    @override
    def setUp(self) -> None:
        """Isolate durable and preview generations for every test."""
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.enterContext(
            patch(
                "runtime.jobs.context.open_run_log",
                return_value=nullcontext(),
            ),
        )
        self.site = OfflineSite("zh", "wikipedia")

    def _prepare(
        self,
        members: pl.DataFrame,
        text: str = _TEXT,
        *,
        source_rows: pl.DataFrame | None = None,
        observed_at: dt.datetime = _NOW,
    ) -> source.AssessmentLogData:
        """Prepare a report from deterministic complete source rows.

        Args:
            members: Current full project membership.
            text: Exact current destination content.
            source_rows: Optional rows with observed revision links.
            observed_at: Actual time of this full source observation.

        Returns:
            Backend data with real baseline recovery and retention.
        """
        with patch.object(
            source,
            "get_snapshot",
            return_value=Mock(
                tables={
                    "members": (
                        _replica_rows(members)
                        if source_rows is None
                        else source_rows
                    ),
                },
                manifest={
                    "calculated_at": observed_at.isoformat(),
                    "report_key": "project-assessments-zhwiki-fixture",
                    "generation": "1" * 32,
                },
            ),
        ):
            return source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                text,
                observed_at=observed_at,
            )

    @staticmethod
    def _retain(
        prepared: source.AssessmentLogData,
        text: str,
        sequence: int,
        *,
        state: str = "saved",
        dry: bool = False,
    ) -> ReportBundle:
        """Persist a proposal with a chosen publication outcome.

        Args:
            prepared: Complete service facts and baseline metadata.
            text: Generated destination text for these facts.
            sequence: Increasing run identity and start-time offset.
            state: Observed publication state to retain.
            dry: Select isolated preview storage.

        Returns:
            Selected durable proposal bundle.
        """
        store = ReportStore(dry=dry)
        bundle = store.write(
            _KEY,
            prepared.result,
            text,
            run_id=f"{sequence:032x}",
            started_at=prepared.calculated_at + dt.timedelta(seconds=sequence),
            target={"site": "zhwiki", "title": _TITLE},
        )
        store.set_publication(bundle, state)
        return bundle


class TestAssessmentEditSummary(_AssessmentLogFixture):
    """Describe changes with actions while preserving date semantics."""

    def test_renames_and_assessment_actions_remain_distinct(self) -> None:
        """Count separate actions for overlapping page changes."""
        previous = _members(
            (1, "Old title", "C", "Low"),
            (2, "Departed", "B", "High"),
        )
        current = _members(
            (1, "New title", "B", "High"),
            (3, "New member", "Stub", "Low"),
        )
        prepared = replace(
            self._prepare(current),
            changes=source.compare_members(previous, current),
        )
        assert build_edit_summary(prepared) == (
            "Rename 1 page; change 1 assessment; add 1 assessment; "
            "remove 1 assessment for Oct 24."
        )
        summary = EditSummary.with_attribution(build_edit_summary(prepared))
        assert len(summary.encode()) <= MAX_EDIT_SUMMARY_BYTES
        renamed = replace(
            prepared,
            changes=prepared.changes.filter(pl.col("kind") == "renamed"),
        )
        assert build_edit_summary(renamed) == "Rename 1 page for Oct 24."

    def test_pending_and_empty_history_do_not_claim_fresh_changes(
        self,
    ) -> None:
        """Keep retained history separate from fresh counts."""
        prepared = self._prepare(_members((1, "Existing", "B", "High")))
        assert build_edit_summary(prepared) == (
            "Update assessment log for Oct 24."
        )
        assert build_edit_summary(replace(prepared, pending=True)) == (
            "Publish pending assessment changes for Oct 24."
        )


class TestAssessmentLogEmptyDays(_AssessmentLogFixture):
    """Distinguish observed empty days from initial unknown history."""

    def test_first_runtime_snapshot_does_not_invent_additions(self) -> None:
        """Seed existing members without changing the wiki text."""
        members = _members((1, "Existing", "B", "High"))
        prepared = self._prepare(members)
        assert prepared.members.equals(members)
        assert prepared.changes.is_empty()
        assert not prepared.unchanged
        assert update_text(_TEXT, prepared) == _TEXT
        assert not (self.root / "reports").exists()

    def test_same_day_seed_retry_does_not_claim_no_changes(self) -> None:
        """Keep a daily self-comparison distinct from zero changes."""
        members = _members((1, "Existing", "B", "High"))
        self._retain(self._prepare(members), _TEXT, 1)
        retry = self._prepare(
            members,
            observed_at=_NOW + dt.timedelta(hours=1),
        )
        assert retry.changes.is_empty()
        assert not retry.unchanged
        assert update_text(_TEXT, retry) == _TEXT

    def test_later_unchanged_day_is_recorded_once(self) -> None:
        """Render an observed empty day and preserve saved retries."""
        members = _members((1, "Existing", "B", "High"))
        self._retain(self._prepare(members), _TEXT, 1)
        next_day = _NOW + dt.timedelta(days=1)
        prepared = self._prepare(members, observed_at=next_day)
        assert prepared.unchanged
        assert prepared.changes.is_empty()
        rendered = update_text(_TEXT, prepared)
        assert "=== 2026年10月25日 ===\n\n" in rendered
        assert "\n\n<!-- aranami-assessment-totals schema=2 " in rendered
        assert "±0" not in rendered
        assert "合计未知：" in rendered
        assert "重定向级未知" in rendered
        assert "2026年10月24日" not in rendered
        assert "====" not in rendered
        assert rendered.startswith("Introduction\n")
        assert rendered.endswith("Footer [[Unmanaged]]")
        self._retain(prepared, rendered, 2)
        retry = self._prepare(members, rendered, observed_at=next_day)
        assert retry.days == {next_day.date() - dt.timedelta(days=1): {}}
        assert not retry.unchanged
        assert update_text(rendered, retry) == rendered

    def test_no_change_observation_does_not_fill_unobserved_dates(
        self,
    ) -> None:
        """Add only the actual snapshot date after a collection gap."""
        members = _members((1, "Existing", "B", "High"))
        self._retain(self._prepare(members), _TEXT, 1)
        prepared = self._prepare(
            members,
            observed_at=_NOW + dt.timedelta(days=3),
        )
        rendered = update_text(_TEXT, prepared)
        assert "=== 2026年10月27日 ===\n\n" in rendered
        assert "\n\n無變更\n\n" in rendered
        for day in (24, 25, 26):
            assert f"2026年10月{day}日" not in rendered

    def test_same_day_changes_replace_an_empty_day_marker(self) -> None:
        """Replace the no-change marker when real entries appear."""
        members = _members((1, "Existing", "B", "High"))
        seed = self._prepare(members, observed_at=_NOW - dt.timedelta(days=1))
        self._retain(seed, _TEXT, 1)
        unchanged = self._prepare(members)
        empty_day = update_text(_TEXT, unchanged)
        self._retain(unchanged, empty_day, 2)
        current = _members(
            (1, "Existing", "B", "High"),
            (2, "Added", "C", "Low"),
        )
        changed = self._prepare(current, empty_day)
        rendered = update_text(empty_day, changed)
        assert rendered.count("=== 2026年10月24日 ===") == 1
        assert "無變更" not in rendered
        assert "==== 新增評級 ====\n\n" in rendered
        assert "'''[[:Added]]'''已評級。" in rendered
        self._retain(changed, rendered, 3)
        retry = self._prepare(current, rendered)
        assert retry.changes.is_empty()
        assert update_text(rendered, retry) == rendered

    def test_failed_empty_days_survive_same_and_next_day_retries(self) -> None:
        """Carry pending empty days without duplicate headings."""
        members = _members((1, "Existing", "B", "High"))
        seed = self._prepare(members, observed_at=_NOW - dt.timedelta(days=1))
        self._retain(seed, _TEXT, 1)
        first = self._prepare(members)
        proposal = update_text(_TEXT, first)
        self._retain(first, proposal, 2, state="unconfirmed")
        retry = self._prepare(members)
        assert retry.pending
        assert not retry.unchanged
        assert update_text(_TEXT, retry) == proposal
        self._retain(retry, proposal, 3, state="unconfirmed")
        later = self._prepare(members, observed_at=_NOW + dt.timedelta(days=1))
        assert later.pending
        assert later.unchanged
        rendered = update_text(_TEXT, later)
        days = (24, 25)
        assert rendered.count("無變更") == len(days)
        for day in days:
            assert rendered.count(f"=== 2026年10月{day}日 ===") == 1
        self._retain(later, rendered, 4)
        saved = self._prepare(
            members,
            rendered,
            observed_at=_NOW + dt.timedelta(days=1),
        )
        assert not saved.pending
        assert update_text(rendered, saved) == rendered


class TestAssessmentLogState(_AssessmentLogFixture):
    """Exercise durable membership and dated-history recovery."""

    def test_missing_markers_fail_before_source_reads(self) -> None:
        """Leave initial page setup outside the recurring updater."""
        with (
            patch.object(source, "get_snapshot") as query,
            self.assertRaises(ValueError),
        ):
            source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                "Existing page without managed markers",
                observed_at=_NOW,
            )
        query.assert_not_called()

    def test_daily_snapshot_keeps_its_actual_time_and_preview_mode(
        self,
    ) -> None:
        """Keep the original daily observation time on later reads."""
        snapshot_at = _NOW.replace(hour=0, minute=0)
        snapshot = Mock(
            tables={
                "members": _replica_rows(_members((1, "Existing", "B", None))),
            },
            manifest={
                "calculated_at": snapshot_at.isoformat(),
                "report_key": "project-assessments-zhwiki-fixture",
                "generation": "1" * 32,
            },
        )
        with patch.object(
            source,
            "get_snapshot",
            return_value=snapshot,
        ) as get:
            prepared = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                _TEXT,
                observed_at=_NOW,
                dry=True,
            )
        get.assert_called_once_with(
            self.site,
            "电子游戏",
            observed_at=_NOW,
            dry=True,
        )
        assert prepared.calculated_at == snapshot_at
        assert prepared.result.calculated_at == snapshot_at

    def test_invalid_source_does_not_replace_baseline(self) -> None:
        """Reject invalid project identities before storage."""
        members = _members((1, "Existing", "B", "High"))
        original = self._retain(self._prepare(members), _TEXT, 1)
        for invalid in (
            _members(),
            _members((1, "One", "B", None), (1, "Two", "C", None)),
            _members((0, "Invalid identity", "B", None)),
        ):
            with (
                self.subTest(rows=invalid.to_dicts()),
                self.assertRaises(
                    ValueError,
                ),
            ):
                self._prepare(invalid)
            assert ReportStore().read(_KEY).path == original.path

    def test_changes_keep_current_and_removed_source_namespaces(self) -> None:
        """Use current namespaces and retain removed-page namespaces."""
        original = _members(
            (1, "Category:Former", None, None),
            (2, "Moved article", "C", "Low"),
            (3, "Remaining", "B", "High"),
            (4, "Template:Former", "Template", None),
        )
        self._retain(self._prepare(original), _TEXT, 1)
        prepared = self._prepare(
            _members(
                (2, "Template:Moved", "Template", "NA"),
                (3, "Remaining", "B", "High"),
                (5, "Category:Added", "Category", None),
            ),
        )
        assert dict(
            prepared.changes
            .select("page_id", "page_namespace")
            .unique()
            .iter_rows(),
        ) == {1: 14, 2: 10, 4: 10, 5: 14}
        assert prepared.changes.schema["page_namespace"] == pl.Int64
        removed = prepared.changes.filter(pl.col("kind") == "removed")
        assert dict(
            removed.select("old_title", "page_namespace").iter_rows(),
        ) == {"Category:Former": 14, "Template:Former": 10}

    def test_repeated_failed_proposals_keep_confirmed_members(self) -> None:
        """Retain the baseline after its original bundle expires."""
        members = _members((1, "Removed", "GA", "High"))
        original = self._retain(self._prepare(members), _TEXT, 1)
        previous_title = "Removed"
        for sequence in (2, 3, 4):
            current = _members((sequence, f"New {sequence}", "Stub", "Low"))
            prepared = self._prepare(current)
            assert prepared.baseline.equals(members)
            removed = prepared.changes.filter(pl.col("kind") == "removed")
            assert removed["old_title"].to_list() == [previous_title]
            rendered = update_text(_TEXT, prepared)
            assert rendered.count("[[:Removed]]") == 1
            self._retain(
                prepared,
                rendered,
                sequence,
                state="unconfirmed",
            )
            previous_title = f"New {sequence}"
        assert not original.path.exists()

    def test_failed_removal_survives_next_day_restoration(self) -> None:
        """Retain both observed transitions across a failed save."""
        original = _members(
            (1, "Returning", "B", "High"),
            (2, "Remaining", "C", "Low"),
        )
        seed = self._prepare(original, observed_at=_NOW - dt.timedelta(days=1))
        self._retain(seed, _TEXT, 1)
        missing = _members((2, "Remaining", "C", "Low"))
        first = self._prepare(missing)
        failed_text = update_text(_TEXT, first)
        self._retain(first, failed_text, 2, state="unconfirmed")
        next_day = _NOW + dt.timedelta(days=1)
        restored = self._prepare(original, observed_at=next_day)
        assert restored.pending
        assert restored.baseline.equals(original)
        assert restored.changes["kind"].to_list() == ["added"]
        assert restored.changes["page_id"].to_list() == [1]
        assert (
            "已移除評級"
            in restored.days[_NOW.date() - dt.timedelta(days=1)]["移除評級"]
        )
        rendered = update_text(_TEXT, restored)
        assert rendered.count("已移除評級") == 1
        assert rendered.count("已評級") == 1
        older = rendered[rendered.index("=== 2026年10月24日 ===") :]
        newer = rendered[
            rendered.index("=== 2026年10月25日 ===") : rendered.index(
                "=== 2026年10月24日 ===",
            )
        ]
        assert "已移除評級" in older
        assert "已評級" not in older
        assert "已評級" in newer
        assert "已移除評級" not in newer
        self._retain(restored, rendered, 3)
        confirmed = self._prepare(original, rendered, observed_at=next_day)
        assert not confirmed.pending
        assert confirmed.changes.is_empty()
        assert update_text(rendered, confirmed) == rendered

    def test_failed_history_retry_keeps_its_original_observation_date(
        self,
    ) -> None:
        """Replay unsaved history without duplicating or redating it."""
        original = _members(
            (1, "Removed", "B", "High"),
            (2, "Remaining", "C", "Low"),
        )
        self._retain(self._prepare(original), _TEXT, 1)
        current = _members((2, "Remaining", "C", "Low"))
        first = self._prepare(current)
        proposal = update_text(_TEXT, first)
        self._retain(first, proposal, 2, state="unconfirmed")
        for sequence in (3, 4):
            observed_at = _NOW + dt.timedelta(days=sequence - 2)
            retry = self._prepare(
                current,
                observed_at=observed_at,
            )
            assert retry.pending
            assert retry.unchanged
            assert retry.baseline.equals(original)
            assert retry.changes.is_empty()
            rendered = update_text(_TEXT, retry)
            assert rendered.count("已移除評級") == 1
            assert rendered.count("無變更") == sequence - 2
            assert (
                f"=== 2026年10月{observed_at.day - 1}日 ===\n\n"
            ) in rendered
            original_day = rendered[rendered.index("=== 2026年10月24日 ===") :]
            assert "已移除評級" in original_day
            assert "無變更" not in original_day
            proposal = rendered
            self._retain(retry, proposal, sequence, state="unconfirmed")

    def test_unconfirmed_save_is_recovered_from_actual_wiki_body(self) -> None:
        """Recover an uncertain save from the actual wiki body."""
        self._retain(
            self._prepare(_members((1, "Removed", "B", "High"))),
            _TEXT,
            1,
        )
        current = _members((2, "Remaining", "C", None))
        prepared = self._prepare(current)
        published = update_text(_TEXT, prepared)
        self._retain(prepared, published, 2, state="unconfirmed")
        restarted = self._prepare(current, published)
        assert restarted.changes.is_empty()
        assert restarted.baseline.equals(current)
        assert update_text(published, restarted) == published

    def test_overlapping_proposal_recovers_previous_published_body(
        self,
    ) -> None:
        """Recover a saved generation overtaken by a stale proposal."""
        original = _members((1, "Original", "B", "High"))
        self._retain(self._prepare(original), _TEXT, 1)
        first_members = _members((2, "Published", "C", "Low"))
        later_members = _members((3, "Stale proposal", "Stub", None))
        first = self._prepare(first_members)
        later = self._prepare(later_members)
        published = update_text(_TEXT, first)
        stale = update_text(_TEXT, later)
        assert published != stale
        saved = self._retain(first, published, 2)
        self._retain(later, stale, 3, state="unconfirmed")
        assert ReportStore().read(_KEY).tables["baseline"].equals(original)
        assert ReportStore().read(_KEY, previous=True).path == saved.path
        recovered = self._prepare(first_members, published)
        assert recovered.baseline.equals(first_members)
        assert recovered.changes.is_empty()
        assert update_text(published, recovered) == published

    def test_unmanaged_edits_do_not_invalidate_membership(self) -> None:
        """Match the managed body while retaining outside page edits."""
        members = _members((1, "Existing", "B", "High"))
        self._retain(self._prepare(members), _TEXT, 1)
        edited = _TEXT.replace("Introduction", "Edited introduction")
        prepared = self._prepare(members, edited)
        assert prepared.changes.is_empty()
        assert update_text(edited, prepared) == edited

    def test_unrecognized_managed_edit_fails_closed(self) -> None:
        """Require a matching published or carried baseline body."""
        members = _members((1, "Existing", "B", "High"))
        self._retain(self._prepare(members), _TEXT, 1)
        edited = _TEXT.replace(
            '<!-- aranami end="assessment-log" -->',
            'Unexpected manual change\n<!-- aranami end="assessment-log" -->',
        )
        with self.assertRaises(ValueError):
            self._prepare(members, edited)

    def test_preview_storage_cannot_advance_production_baseline(self) -> None:
        """Keep repeated previews separate from live membership."""
        members = _members((1, "Removed", "B", "High"))
        original = self._retain(self._prepare(members), _TEXT, 1)
        current = _members((2, "New", "Stub", "Low"))
        for sequence in (2, 3):
            prepared = self._prepare(current)
            self._retain(
                prepared,
                update_text(_TEXT, prepared),
                sequence,
                state="preview",
                dry=True,
            )
            assert prepared.baseline.equals(members)
            assert ReportStore().read(_KEY).path == original.path
        assert ReportStore(dry=True).read(_KEY) is not None

    def test_prunes_only_dates_older_than_thirty_day_window(self) -> None:
        """Retain the date boundary and surrounding wiki text."""
        body = (
            "=== 2026年9月24日 ===\n==== 移除評級 ====\n* [[Expired]]\n"
            "=== 2026年9月25日 ===\n==== 移除評級 ====\n* [[Boundary]]\n"
            "=== 2026年10月24日 ===\n==== 新增評級 ====\n* [[Recent]]\n"
        )
        text = _TEXT.replace(
            '<!-- aranami end="assessment-log" -->',
            body + '<!-- aranami end="assessment-log" -->',
        )
        prepared = self._prepare(_members((1, "Existing", None, None)), text)
        assert prepared.pruned
        rendered = update_text(text, prepared)
        assert "Expired" not in rendered
        assert "Boundary" in rendered
        assert "Recent" in rendered
        assert rendered.startswith("Introduction\n")
        assert rendered.endswith("Footer [[Unmanaged]]")

    def test_empty_days_use_the_same_retention_boundary(self) -> None:
        """Prune old empty days without claiming an initial date."""
        body = (
            "=== 2026年9月24日 ===\n\n無變更\n\n"
            "=== 2026年9月25日 ===\n\n無變更\n\n"
            "=== 2026年10月24日 ===\n\n無變更\n\n"
        )
        text = _TEXT.replace(
            '<!-- aranami end="assessment-log" -->',
            body + '<!-- aranami end="assessment-log" -->',
        )
        prepared = self._prepare(_members((1, "Existing", "B", None)), text)
        assert prepared.pruned
        assert not prepared.unchanged
        assert set(prepared.days) == {
            dt.date(2026, 9, 25),
            dt.date(2026, 10, 24),
        }
        rendered = update_text(text, prepared)
        assert "2026年9月24日" not in rendered
        assert "2026年9月25日" in rendered
        assert "2026年10月24日" in rendered
        assert "2026年10月25日" not in rendered
        assert rendered.count("無變更") == len(prepared.days)

    def test_ambiguous_empty_day_text_fails_before_source_reads(self) -> None:
        """Accept only the exact standalone no-change statement."""
        for body in (
            "",
            "Unknown observation\n",
            "無變更。\n",
            "無變更\n無變更\n",
            "無變更\n==== 新增評級 ====\n* [[Added]]\n",
            "==== 無變更 ====\n",
        ):
            text = _TEXT.replace(
                '<!-- aranami end="assessment-log" -->',
                "=== 2026年10月25日 ===\n"
                + body
                + '<!-- aranami end="assessment-log" -->',
            )
            with (
                self.subTest(body=body),
                patch.object(source, "get_snapshot") as query,
                self.assertRaises(ValueError),
            ):
                source.prepare_data(
                    self.site,
                    "电子游戏",
                    _TITLE,
                    text,
                    observed_at=_NOW,
                )
            query.assert_not_called()

    def test_empty_day_preview_does_not_advance_production(self) -> None:
        """Keep no-change preview proposals outside live membership."""
        members = _members((1, "Existing", "B", "High"))
        baseline = self._retain(self._prepare(members), _TEXT, 1)
        observed_at = _NOW + dt.timedelta(days=1)
        for sequence in (2, 3):
            prepared = self._prepare(members, observed_at=observed_at)
            assert prepared.unchanged
            rendered = update_text(_TEXT, prepared)
            assert rendered.count("無變更") == 1
            self._retain(
                prepared,
                rendered,
                sequence,
                state="preview",
                dry=True,
            )
            assert ReportStore().read(_KEY).path == baseline.path


class TestAssessmentLogRendering(_AssessmentLogFixture):
    """Verify natural sentences, links, ordering, and spacing."""

    def test_daily_totals_count_articles_and_lists_in_quality_order(
        self,
    ) -> None:
        """Combine article aliases and omit zero-count classes."""
        existing = (1, "Existing", "FA", "Top")
        self._retain(self._prepare(_members(existing)), _TEXT, 1)
        articles = pl.DataFrame(
            {
                "page_id": pl.int_range(1, 1235, eager=True),
                "title": [f"Article {number}" for number in range(1, 1235)],
                "class": ["FA"] * 1233 + ["典範级"],
                "importance": ["Top"] * 1234,
            },
            schema=_SCHEMA,
        )
        members = pl.concat(
            [
                articles,
                _members(
                    (1235, "List one", "FL", "High"),
                    (1236, "List two", "特色列表", "高"),
                    (1237, "Good", "GA", "Mid"),
                    (1238, "Upper", "Bplus", "Low"),
                    (1239, "B article", "乙级", "Low"),
                    (1240, "B list", "乙级列表", "Low"),
                    (1241, "Unknown", None, None),
                    (1242, "Category:Not an article", "FA", "Top"),
                    (1243, "Template:Not an article", "Template", "NA"),
                ),
            ],
        )
        prepared = self._prepare(members)
        rendered = update_text(_TEXT, prepared)
        assert (
            "条目页+1,240（典范级+1,233、特色列表级+2、优良级+1"
            "、乙上级+1、乙级+1、乙级列表级+1、未评级+1，极高重要度+1,233"
            "、高重要度+2、中重要度+1、低重要度+3、未知重要度+1）"
        ) in rendered
        assert (
            "<!-- aranami-assessment-totals schema=2 "
            "合计未知：典范1,234、特色列表2、甲0、甲级列表0、优良1"
        ) in rendered
        assert "模板级1" in rendered
        assert "重定向级未知" in rendered
        totals = next(
            line for line in rendered.splitlines() if line.startswith("条目页")
        ).split("<!--", 1)[0]
        assert "甲+" not in totals
        assert "不适用重要度" not in totals

    def test_failed_days_keep_their_own_snapshot_totals(self) -> None:
        """Retain each day's own totals across failed publications."""
        seed = _members((1, "Original", "B", "High"))
        self._retain(self._prepare(seed), _TEXT, 1)
        first_members = _members(
            (1, "Original", "B", "High"),
            (2, "Added", "C", "Low"),
        )
        first = self._prepare(
            first_members,
            observed_at=_NOW + dt.timedelta(days=1),
        )
        proposal = update_text(_TEXT, first)
        self._retain(first, proposal, 2, state="unconfirmed")
        second = self._prepare(seed, observed_at=_NOW + dt.timedelta(days=2))
        rendered = update_text(_TEXT, second)
        older = rendered[rendered.index("=== 2026年10月25日 ===") :]
        newer = rendered[: rendered.index("=== 2026年10月25日 ===")]
        assert "条目页+1（丙级+1，低重要度+1）" in older
        assert "条目页-1（丙级-1，低重要度-1）" in newer
        self._retain(second, rendered, 3)
        retry = self._prepare(
            seed,
            rendered,
            observed_at=_NOW + dt.timedelta(days=2),
        )
        assert update_text(rendered, retry) == rendered

    def test_midnight_snapshot_reports_the_previous_utc_year(self) -> None:
        """Use UTC boundaries and retain the actual collection time."""
        seed = _members((1, "Existing", "B", "High"))
        self._retain(self._prepare(seed), _TEXT, 1)
        observed_at = dt.datetime(
            2027,
            1,
            1,
            8,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        prepared = self._prepare(seed, observed_at=observed_at)
        rendered = update_text(_TEXT, prepared)
        assert prepared.report_date == dt.date(2026, 12, 31)
        assert prepared.calculated_at == observed_at
        assert prepared.result.metadata["report_date"] == "2026-12-31"
        assert "=== 2026年12月31日 ===" in rendered
        assert "2027年1月1日" not in rendered

    def test_renderer_groups_follow_requested_order_and_preserve_unknowns(
        self,
    ) -> None:
        """Render natural sentences for all observed change kinds."""
        old = _members(
            (1, "Category:Former", None, None),
            (2, "Old title", "C", "Low"),
        )
        self._retain(self._prepare(old), _TEXT, 1)
        prepared = self._prepare(
            _members((2, "New title", "B", "High"), (3, "Added", None, None)),
        )
        rendered = update_text(_TEXT, prepared)
        groups = ("重命名", "變更評級", "新增評級", "移除評級")
        positions = [rendered.index(f"==== {group} ====") for group in groups]
        assert positions == sorted(positions)
        assert ("=== 2026年10月24日 ===\n\n条目页+1（") in rendered
        for group in groups:
            assert f"==== {group} ====\n\n* " in rendered
        assert "。\n\n==== 變更評級 ====" in rendered
        assert "* '''[[:Category:Former]]'''已移除評級。\n" in rendered
        assert "12:34 UTC" not in rendered
        assert (
            "* '''[[:Old title]]'''更名為'''[[:New title]]'''。\n"
        ) in rendered
        assert (
            "'''[[:New title]]'''重新評級。品質由'''丙级'''改為'''乙级'''。"
            "重要度由'''低重要度'''改為'''高重要度'''。"
        ) in rendered
        assert (
            "'''[[:Added]]'''已評級。品質獲評'''未评级'''。"
            "重要度獲評'''未知重要度'''。"
        ) in rendered
        assert "Special:PermanentLink" not in rendered
        assert "→" not in rendered
        assert "—" not in rendered
        assert '<!-- aranami begin="assessment-log" days="30" -->' in rendered
        assert rendered.startswith("Introduction\n")
        assert rendered.endswith("Footer [[Unmanaged]]")

    def test_reference_links_use_corresponding_full_snapshots(self) -> None:
        """Use current links and retain removed members' old links."""
        old = _members((1, "Former", "B", "High"), (2, "Same", "C", "Low"))
        old_rows = _replica_rows(old).with_columns(
            pl.concat_str(pl.lit("Talk:"), pl.col("full_title")).alias(
                "talk_title",
            ),
            (pl.col("page_id") * 10 + 1).alias("page_revision"),
            (pl.col("page_id") * 10 + 2).alias("talk_revision"),
        )
        self._retain(self._prepare(old, source_rows=old_rows), _TEXT, 1)
        current = _members(
            (2, "Same", "B", "High"),
            (3, "Added", "Draft", "NA"),
        )
        current_rows = _replica_rows(current).with_columns(
            pl.concat_str(pl.lit("Talk:"), pl.col("full_title")).alias(
                "talk_title",
            ),
            (pl.col("page_id") * 10 + 101).alias("page_revision"),
            (pl.col("page_id") * 10 + 102).alias("talk_revision"),
        )
        prepared = self._prepare(current, source_rows=current_rows)
        assert prepared.references == {
            1: ("Talk:Former", 11, 12),
            2: ("Talk:Same", 121, 122),
            3: ("Talk:Added", 131, 132),
        }
        rendered = update_text(_TEXT, prepared)
        assert (
            "* '''[[:Former]]'''（[[:Talk:Former|討論]]）已移除評級。\n"
        ) in rendered
        assert "（[[:Talk:Same|討論]]）重新評級。" in rendered
        assert "（[[:Talk:Added|討論]]）已評級。" in rendered
        assert "PermanentLink/11" not in rendered
        for revision, label in (
            (121, "固定版本"),
            (122, "討論頁"),
            (131, "固定版本"),
            (132, "討論頁"),
        ):
            link = f"[[:Special:PermanentLink/{revision}|{label}]]"
            assert rendered.count(link) == 1
        assert (
            "品質獲評'''草稿级'''。重要度獲評'''不适用重要度'''。"
            "（[[:Special:PermanentLink/131|固定版本]]"
            "·[[:Special:PermanentLink/132|討論頁]]）"
        ) in rendered
        assert "'''[[:Talk:" not in rendered
        assert "'''[[:Special:" not in rendered

    def test_renderer_sorts_namespace_then_current_or_removed_title(
        self,
    ) -> None:
        """Order changes by namespace and their displayed titles."""
        old = _members(
            (1, "Existing", "B", "High"),
            (2, "Category:Old", "Category", "NA"),
            (3, "Zulu old", "Stub", "Low"),
            (4, "Template:Old", "Template", "NA"),
            (5, "File:Old", "File", "NA"),
            (6, "Alpha old", "Stub", "Low"),
        )
        old_rows = _replica_rows(old).with_columns(
            pl
            .col("page_id")
            .replace_strict({1: 0, 2: 14, 3: 0, 4: 10, 5: 6, 6: 0})
            .alias("page_namespace"),
        )
        self._retain(self._prepare(old, source_rows=old_rows), _TEXT, 1)
        current = _members(
            (1, "Existing", "B", "High"),
            (7, "Category:New", "Category", "NA"),
            (8, "Zulu new", "Stub", "Low"),
            (9, "Template:New", "Template", "NA"),
            (10, "File:New", "File", "NA"),
            (11, "Alpha new", "Stub", "Low"),
        )
        current_rows = _replica_rows(current).with_columns(
            pl
            .col("page_id")
            .replace_strict({1: 0, 7: 14, 8: 0, 9: 10, 10: 6, 11: 0})
            .alias("page_namespace"),
        )
        prepared = self._prepare(current, source_rows=current_rows)
        rendered = update_text(_TEXT, prepared)
        for titles in (
            (
                "Alpha old",
                "Zulu old",
                "File:Old",
                "Template:Old",
                "Category:Old",
            ),
            (
                "Alpha new",
                "Zulu new",
                "File:New",
                "Template:New",
                "Category:New",
            ),
        ):
            positions = [rendered.index(f"[[:{title}]]") for title in titles]
            assert positions == sorted(positions)

    def test_reassessment_omits_unchanged_dimension(self) -> None:
        """Use new wording without rewriting retained reassessments."""
        legacy_line = (
            "* '''[[:Legacy]]'''重新評級。"
            "品質由'''重定向级'''變為'''小作品级'''。\n"
        )
        text = _TEXT.replace(
            '<!-- aranami end="assessment-log" -->',
            "=== 2026年10月23日 ===\n\n==== 變更評級 ====\n\n"
            + legacy_line
            + '\n<!-- aranami end="assessment-log" -->',
        )
        old = _members((1, "Same", "GA", "High"))
        seed = self._prepare(old, text)
        assert update_text(text, seed) == text
        self._retain(seed, text, 1)
        current = _members((1, "Same", "FA", "High"))
        prepared = self._prepare(current, text)
        rendered = update_text(text, prepared)
        assert (
            "* '''[[:Same]]'''重新評級。品質由'''优良级'''改為'''典范级'''。\n"
        ) in rendered
        assert legacy_line in rendered
        assert legacy_line.replace("變為", "改為") not in rendered
        change_line = next(
            line
            for line in rendered.splitlines()
            if line.startswith("* '''[[:Same]]'''")
        )
        assert "重要度" not in change_line
        self._retain(prepared, rendered, 2)
        retry = self._prepare(current, rendered)
        assert update_text(rendered, retry) == rendered

    def test_added_unassessed_and_unknown_ratings_are_explicit(self) -> None:
        """Show unassessed values without duplicate suffixes."""
        existing = (1, "Existing", "B", "High")
        self._retain(self._prepare(_members(existing)), _TEXT, 1)
        prepared = self._prepare(
            _members(
                existing,
                (2, "Missing", None, None),
                (3, "Blank", "", ""),
                (4, "English", "Unassessed", "Unknown"),
                (5, "Bare label", "未评", "未知"),
                (6, "Full label", "未评级", "未知重要度"),
            ),
        )
        rendered = update_text(_TEXT, prepared)
        for title in (
            "Missing",
            "Blank",
            "English",
            "Bare label",
            "Full label",
        ):
            assert (
                f"'''[[:{title}]]'''已評級。品質獲評'''未评级'''。"
                "重要度獲評'''未知重要度'''。"
            ) in rendered

    def test_reassessment_preserves_absent_rating_labels(self) -> None:
        """Show transitions into and out of absent assessments."""
        old = _members(
            (1, "Restored", None, None),
            (2, "Missing", "B", "High"),
            (3, "Blank", "B", "High"),
        )
        self._retain(self._prepare(old), _TEXT, 1)
        prepared = self._prepare(
            _members(
                (1, "Restored", "B", "High"),
                (2, "Missing", None, None),
                (3, "Blank", "", ""),
            ),
        )
        rendered = update_text(_TEXT, prepared)
        assert (
            "'''[[:Restored]]'''重新評級。"
            "品質由'''未评级'''改為'''乙级'''。"
            "重要度由'''未知重要度'''改為'''高重要度'''。"
        ) in rendered
        for title in ("Missing", "Blank"):
            assert (
                f"'''[[:{title}]]'''重新評級。"
                "品質由'''乙级'''改為'''未评级'''。"
                "重要度由'''高重要度'''改為'''未知重要度'''。"
            ) in rendered


class TestAssessmentLogJob(_AssessmentLogFixture):
    """Exercise capture, preview, and guarded publication boundaries."""

    def test_job_captures_source_before_reading_destination(self) -> None:
        """Retain reusable data even when the wiki destination fails."""
        context = JobContext(self.site, _NOW.date(), dry=False)
        with (
            patch.object(job, "get_snapshot") as capture,
            patch.object(job, "read_pages", side_effect=OSError("offline")),
            patch.object(job, "prepare_data") as prepare,
            self.assertRaises(OSError),
        ):
            job.run(title=_TITLE, context=context)
        capture.assert_called_once()
        assert capture.call_args.kwargs["dry"] is False
        prepare.assert_not_called()

    def test_unchanged_log_confirms_its_actual_existing_report_date(
        self,
    ) -> None:
        """Retain the last dated report rather than the attempt date."""
        text = _TEXT.replace(
            '<!-- aranami end="assessment-log" -->',
            "=== 2026年10月23日 ===\n\n無變更\n\n"
            '<!-- aranami end="assessment-log" -->',
        )
        prepared = self._prepare(_members((1, "Existing", "B", "High")), text)
        context = JobContext(self.site, _NOW.date(), dry=False)
        with (
            patch.object(job, "get_snapshot"),
            patch.object(job, "read_pages", return_value=[Mock(text=text)]),
            patch.object(job, "prepare_data", return_value=prepared),
            patch(
                "updater.publisher.pywikibot.Page",
                return_value=Mock(get=Mock(return_value=text)),
            ) as page,
        ):
            job.run(title=_TITLE, context=context)
        page.return_value.get.assert_called_once_with(force=True)
        page.return_value.save.assert_not_called()
        assert context.routine_results()[0]["status"] == "no_update"
        assert context.tasks[0].report_date == dt.date(2026, 10, 23)

    def test_undated_baseline_does_not_claim_a_completed_report(self) -> None:
        """Keep initial membership capture separate from a dated log."""
        prepared = self._prepare(_members((1, "Existing", "B", "High")))
        context = JobContext(self.site, _NOW.date(), dry=False)
        with (
            patch.object(job, "get_snapshot"),
            patch.object(job, "read_pages", return_value=[Mock(text=_TEXT)]),
            patch.object(job, "prepare_data", return_value=prepared),
            patch(
                "updater.publisher.pywikibot.Page",
                return_value=Mock(get=Mock(return_value=_TEXT)),
            ),
        ):
            job.run(title=_TITLE, context=context)
        assert context.routine_results()[0]["status"] == "no_update"
        assert context.tasks[0].report_date is None

    def test_successful_save_confirms_new_daily_report(self) -> None:
        """Advance the date only after the new section is saved."""
        old = _members((1, "Existing", "C", "High"))
        self._retain(self._prepare(old), _TEXT, 1)
        prepared = self._prepare(_members((1, "Existing", "B", "High")))
        context = JobContext(
            self.site,
            _NOW.date(),
            dry=False,
            started_at=_NOW + dt.timedelta(seconds=10),
        )
        with (
            patch.object(job, "get_snapshot"),
            patch.object(job, "read_pages", return_value=[Mock(text=_TEXT)]),
            patch.object(job, "prepare_data", return_value=prepared),
            patch(
                "updater.publisher.pywikibot.Page",
                return_value=Mock(text=_TEXT, get=Mock(return_value=_TEXT)),
            ) as page,
        ):
            job.run(title=_TITLE, context=context)
        page.return_value.save.assert_called_once()
        assert page.return_value.save.call_args.kwargs["summary"] == (
            "Change 1 assessment for Oct 24. ([[toolforge:aranami|Aranami]])"
        )
        assert context.routine_results()[0]["status"] == "updated"
        assert context.tasks[0].report_date == dt.date(2026, 10, 24)

    def test_failed_save_does_not_confirm_a_proposed_report_date(self) -> None:
        """Keep unconfirmed daily sections out of successful status."""
        old = _members((1, "Existing", "C", "High"))
        self._retain(self._prepare(old), _TEXT, 1)
        prepared = self._prepare(_members((1, "Existing", "B", "High")))
        context = JobContext(
            self.site,
            _NOW.date(),
            dry=False,
            started_at=_NOW + dt.timedelta(seconds=10),
        )
        page = Mock(text=_TEXT)
        page.get.side_effect = lambda _page=page, **_kwargs: _page.text
        page.save.side_effect = OSError("save response lost")
        with (
            patch.object(job, "get_snapshot"),
            patch.object(job, "read_pages", return_value=[Mock(text=_TEXT)]),
            patch.object(job, "prepare_data", return_value=prepared),
            patch("updater.publisher.pywikibot.Page", return_value=page),
            self.assertRaises(OSError),
        ):
            job.run(title=_TITLE, context=context)
        assert context.tasks[0].report_date is None
        assert context.routine_results()[0]["status"] == "failed"

    def test_failed_durable_write_prevents_wiki_publication(self) -> None:
        """Retain the prior baseline when proposal storage fails."""
        old = _members((1, "Former", "B", "High"))
        original = self._retain(self._prepare(old), _TEXT, 1)
        prepared = self._prepare(_members((2, "New", "Stub", "Low")))
        context = JobContext(
            self.site,
            _NOW.date(),
            dry=False,
            started_at=_NOW + dt.timedelta(seconds=10),
        )
        with (
            patch.object(job, "get_snapshot"),
            patch.object(job, "read_pages", return_value=[Mock(text=_TEXT)]),
            patch.object(job, "prepare_data", return_value=prepared),
            patch.object(ReportStore, "write", side_effect=OSError("full")),
            patch("updater.publisher.pywikibot.Page") as page,
            self.assertRaises(OSError),
        ):
            job.run(title=_TITLE, context=context)
        page.assert_not_called()
        assert ReportStore().read(_KEY).path == original.path

    def test_job_preview_preserves_production_and_retains_proposal(
        self,
    ) -> None:
        """Run previews without changing production membership."""
        old = _members((1, "Former", "B", "High"))
        original = self._retain(self._prepare(old), _TEXT, 1)
        current = _members((2, "New", "Stub", "Low"))
        prepared = self._prepare(current)
        context = JobContext(
            self.site,
            _NOW.date(),
            dry=True,
            started_at=_NOW + dt.timedelta(seconds=10),
        )
        with (
            patch.object(job, "get_snapshot") as capture,
            patch.object(job, "read_pages", return_value=[Mock(text=_TEXT)]),
            patch.object(
                job,
                "prepare_data",
                return_value=prepared,
            ) as prepare,
            patch("updater.publisher.pywikibot.Page") as page,
        ):
            job.run(title=_TITLE, context=context)
        assert capture.call_args.kwargs["dry"] is True
        assert prepare.call_args.kwargs["dry"] is True
        assert (
            capture.call_args.kwargs["observed_at"]
            == prepare.call_args.kwargs["observed_at"]
        )
        page.assert_not_called()
        assert ReportStore().read(_KEY).path == original.path
        preview = ReportStore(dry=True).read(_KEY)
        assert preview is not None
        assert preview.tables["members"].equals(current)
        assert preview.tables["baseline"].equals(old)
        assert context.edits[0].summary == (
            "Add 1 assessment; remove 1 assessment for Oct 24. "
            "([[toolforge:aranami|Aranami]])"
        )
        assert context.tasks[0].report_date is None


class TestCompactAssessmentLog(_AssessmentLogFixture):
    """Compare compact datasets before resolving changed page names."""

    @staticmethod
    def _snapshot(members: pl.DataFrame, instant: dt.datetime) -> Mock:
        """Build a compact saved dataset with article-only aggregates.

        Args:
            members: Complete deterministic assessment observations.
            instant: Actual saved observation time.

        Returns:
            Snapshot containing no per-page titles or revision columns.
        """
        return Mock(
            tables={
                "members": members.select(
                    "page_id",
                    pl.col("class").alias("pa_class"),
                    pl.col("importance").alias("pa_importance"),
                ),
            },
            manifest={
                "calculated_at": instant.isoformat(),
                "report_key": "data/zhwiki/电子游戏",
                "generation": "1" * 32,
                "metadata": {
                    "article_totals": {
                        "count": members.height,
                        "class": members
                        .group_by("class")
                        .len(name="count")
                        .rename({"class": "value"})
                        .to_dicts(),
                        "importance": members
                        .group_by("importance")
                        .len(name="count")
                        .rename({"importance": "value"})
                        .to_dicts(),
                    },
                },
            },
        )

    def test_resolves_only_changed_ids_after_comparing_saved_datasets(
        self,
    ) -> None:
        """Resolve only the IDs whose assessments changed."""
        prior = _members(
            (1, "Former", "C", "Low"),
            (2, "Old name", "C", "Low"),
            (3, "Same", "B", "High"),
        )
        current = _members(
            (2, "Current name", "B", "High"),
            (3, "Same", "B", "High"),
            (4, "Added", "Stub", None),
        )
        metadata = _replica_rows(
            _members(
                (1, "Current departed name", "C", "Low"),
                (2, "Current name", "B", "High"),
                (4, "Added", "Stub", None),
            ),
        ).with_columns(
            pl.concat_str(pl.lit("Talk:"), pl.col("full_title")).alias(
                "talk_title",
            ),
            (pl.col("page_id") * 10).alias("page_revision"),
            (pl.col("page_id") * 10 + 1).alias("talk_revision"),
        )
        with (
            patch.object(
                source,
                "get_snapshot",
                return_value=self._snapshot(current, _NOW),
            ),
            patch.object(
                source,
                "read_previous_snapshot",
                return_value=self._snapshot(
                    prior,
                    _NOW - dt.timedelta(days=1),
                ),
            ) as previous,
            patch.object(
                source,
                "page_metadata",
                return_value=metadata,
            ) as lookup,
        ):
            prepared = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                _TEXT,
                observed_at=_NOW,
            )
        previous.assert_called_once()
        assert set(lookup.call_args.args[1]) == {1, 2, 4}
        assert prepared.result.tables["source"].columns == [
            "page_id",
            "pa_class",
            "pa_importance",
        ]
        assert "title" not in prepared.members.columns
        assert set(prepared.changes["kind"]) == {
            "added",
            "removed",
            "reassessed",
        }
        removed = prepared.changes.filter(pl.col("kind") == "removed")
        assert removed.item(0, "old_title") == "Current departed name"
        rendered = update_text(_TEXT, prepared)
        assert "\n\n条目页（乙级+1" in rendered
        assert "高重要度+1" in rendered
        assert "合计未知：" in rendered
        assert "[[:Current name]]" in rendered
        assert "重命名" not in rendered

    def test_unknown_prior_totals_do_not_invent_count_changes(self) -> None:
        """Keep changes unknown for older compact observations."""
        prior = _members((1, "Existing", "B", "High"))
        current = _members(
            (1, "Existing", "B", "High"),
            (2, "Added", "GA", "Low"),
        )
        previous = self._snapshot(prior, _NOW - dt.timedelta(days=1))
        previous.manifest["metadata"].pop("article_totals")
        with (
            patch.object(
                source,
                "get_snapshot",
                return_value=self._snapshot(current, _NOW),
            ),
            patch.object(
                source,
                "read_previous_snapshot",
                return_value=previous,
            ),
            patch.object(
                source,
                "page_metadata",
                return_value=_replica_rows(current),
            ),
        ):
            prepared = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                _TEXT,
                observed_at=_NOW,
            )
        rendered = update_text(_TEXT, prepared)
        assert "\n\n<!-- aranami-assessment-totals schema=2 " in rendered
        assert "无可比记录" not in rendered
        assert '<small style="color: gray;">' not in rendered

    def test_aggregate_changes_cover_articles_and_nonarticles(self) -> None:
        """Compare displayed count dimensions with the prior capture."""
        prior = _members(
            (1, "Existing", "B", "High"),
            (2, "Changed", "B", "High"),
            (3, "Good", "GA", "High"),
            (4, "Template:Same", "Template", "NA"),
        )
        current = _members(
            (1, "Existing", "B", "High"),
            (2, "Template:Changed", "Template", "NA"),
            (3, "Good", "GA", "Low"),
            (4, "Template:Same", "Template", "NA"),
        )
        before = self._snapshot(prior, _NOW - dt.timedelta(days=1))
        after = self._snapshot(current, _NOW)
        before.manifest["metadata"]["article_totals"] = {
            "count": 3,
            "class": [{"value": "B", "count": 2}, {"value": "GA", "count": 1}],
            "importance": [{"value": "High", "count": 3}],
            "nonarticles": {
                "count": 1,
                "class": [{"value": "Template", "count": 1}],
            },
            "redirects": 0,
        }
        after.manifest["metadata"]["article_totals"] = {
            "count": 2,
            "class": [{"value": "B", "count": 1}, {"value": "GA", "count": 1}],
            "importance": [
                {"value": "High", "count": 1},
                {"value": "Low", "count": 1},
            ],
            "nonarticles": {
                "count": 2,
                "class": [{"value": "Template", "count": 2}],
            },
            "redirects": 0,
        }
        with (
            patch.object(source, "get_snapshot", return_value=after),
            patch.object(
                source,
                "read_previous_snapshot",
                return_value=before,
            ),
            patch.object(
                source,
                "page_metadata",
                return_value=_replica_rows(current),
            ),
        ):
            prepared = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                _TEXT,
                observed_at=_NOW,
            )
        rendered = update_text(_TEXT, prepared)
        for fragment in (
            "条目页-1（乙级-1，高重要度-2、低重要度+1）",
            "非条目页+1（模板级+1）",
            "合计4：",
        ):
            assert fragment in rendered
        assert "优良1" in rendered
        assert "重定向+0" not in rendered
        self._retain(prepared, rendered, 1)
        with (
            patch.object(source, "get_snapshot", return_value=after),
            patch.object(source, "read_previous_snapshot", return_value=None),
        ):
            retry = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                rendered,
                observed_at=_NOW,
            )
        assert update_text(rendered, retry) == rendered

    def test_unchanged_or_first_dataset_does_not_resolve_titles(self) -> None:
        """Avoid name reads for initial and unchanged datasets."""
        members = _members((1, "Irrelevant name", "C", None))
        for previous in (
            None,
            self._snapshot(members, _NOW - dt.timedelta(days=1)),
        ):
            with (
                self.subTest(previous=previous is not None),
                patch.object(
                    source,
                    "get_snapshot",
                    return_value=self._snapshot(members, _NOW),
                ),
                patch.object(
                    source,
                    "read_previous_snapshot",
                    return_value=previous,
                ),
                patch.object(source, "page_metadata") as lookup,
            ):
                prepared = source.prepare_data(
                    self.site,
                    "电子游戏",
                    _TITLE,
                    _TEXT,
                    observed_at=_NOW,
                )
            assert prepared.changes.is_empty()
            lookup.assert_not_called()

    def test_deleted_ids_keep_stable_links_without_inventing_titles(
        self,
    ) -> None:
        """Keep removed IDs visible when no current page exists."""
        prior = _members((1, "Deleted", "B", "High"), (2, "Same", "C", None))
        current = _members((2, "Same", "C", None))
        metadata = pl.DataFrame(
            schema={
                "page_id": pl.Int64,
                "full_title": pl.String,
                "page_namespace": pl.Int64,
                "talk_title": pl.String,
                "page_revision": pl.Int64,
                "talk_revision": pl.Int64,
            },
        )
        with (
            patch.object(
                source,
                "get_snapshot",
                return_value=self._snapshot(current, _NOW),
            ),
            patch.object(
                source,
                "read_previous_snapshot",
                return_value=self._snapshot(
                    prior,
                    _NOW - dt.timedelta(days=1),
                ),
            ),
            patch.object(source, "page_metadata", return_value=metadata),
        ):
            prepared = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                _TEXT,
                observed_at=_NOW,
            )
        assert (
            prepared.changes.item(0, "old_title") == "Special:Redirect/page/1"
        )
        assert prepared.changes.item(0, "page_namespace") is None

    def test_removed_page_uses_saved_title_or_verified_current_title(
        self,
    ) -> None:
        """Keep deleted titles and follow an existing page's move."""
        prior = _members((1, "Original", "B", "High"), (2, "Same", "C", None))
        current = _members((2, "Same", "C", None))
        snapshot = self._snapshot(prior, _NOW - dt.timedelta(days=1))
        snapshot.tables["members"] = _replica_rows(prior).select(
            "page_id",
            "page_title",
            "page_namespace",
            "pa_class",
            "pa_importance",
        )
        metadata = pl.DataFrame(
            [(1, "Draft:天河傳說", 118, "Draft talk:天河傳說", 17, 18)],
            schema={
                "page_id": pl.Int64,
                "full_title": pl.String,
                "page_namespace": pl.Int64,
                "talk_title": pl.String,
                "page_revision": pl.Int64,
                "talk_revision": pl.Int64,
            },
            orient="row",
        )
        for observed, title, talk in (
            (metadata, "Draft:天河傳說", "Draft talk:天河傳說"),
            (metadata.clear(), "Original", "Talk:Original"),
        ):
            with (
                self.subTest(title=title),
                patch.object(
                    source,
                    "get_snapshot",
                    return_value=self._snapshot(current, _NOW),
                ),
                patch.object(
                    source,
                    "read_previous_snapshot",
                    return_value=snapshot,
                ),
                patch.object(source, "page_metadata", return_value=observed),
            ):
                prepared = source.prepare_data(
                    self.site,
                    "电子游戏",
                    _TITLE,
                    _TEXT,
                    observed_at=_NOW,
                )
            assert prepared.changes.item(0, "old_title") == title
            assert (
                f"'''[[:{title}]]'''（[[:{talk}|討論]]）已移除評級。"
                in update_text(_TEXT, prepared)
            )

    def test_retained_unknown_removal_is_repaired_without_new_changes(
        self,
    ) -> None:
        """Repair retained removal links using public identities."""
        line = "* '''[[:Special:Redirect/page/8543576]]'''已移除評級。"
        unrelated = "Preserved comment: " + line
        original = _TEXT.replace(
            '<!-- aranami end="assessment-log" -->',
            "=== 2026年10月24日 ===\n\n==== 移除評級 ====\n\n"
            + line
            + "\n"
            + unrelated
            + '\n\n<!-- aranami end="assessment-log" -->',
        )
        members = _members((2, "Same", "C", None))
        metadata = pl.DataFrame(
            [(8543576, "Draft:天河傳說", "Draft talk:天河傳說")],
            schema={
                "page_id": pl.Int64,
                "full_title": pl.String,
                "talk_title": pl.String,
            },
            orient="row",
        )
        with (
            patch.object(
                source,
                "get_snapshot",
                return_value=self._snapshot(members, _NOW),
            ),
            patch.object(source, "read_previous_snapshot", return_value=None),
            patch.object(
                source,
                "page_metadata",
                side_effect=[metadata.clear(), metadata],
            ),
        ):
            prepared = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                original,
                observed_at=_NOW,
            )
        assert prepared.changes.is_empty()
        rendered = update_text(original, prepared)
        assert unrelated in rendered
        assert "\n" + line + "\n" not in rendered
        assert (
            "'''[[:Draft:天河傳說]]'''"
            "（[[:Draft talk:天河傳說|討論]]）已移除評級。" in rendered
        )

    def test_same_day_summary_migration_uses_preceding_daily_observation(
        self,
    ) -> None:
        """Use prior daily totals on same-day retries."""
        prior_members = _members((1, "First", "C", "Low"))
        current = _members(
            (1, "First", "C", "Low"),
            (2, "Second", "B", "High"),
        )
        before = self._snapshot(prior_members, _NOW - dt.timedelta(days=1))
        after = self._snapshot(current, _NOW)
        with (
            patch.object(source, "get_snapshot", return_value=after),
            patch.object(
                source,
                "read_previous_snapshot",
                return_value=before,
            ),
            patch.object(
                source,
                "page_metadata",
                return_value=_replica_rows(current),
            ),
        ):
            prepared = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                _TEXT,
                observed_at=_NOW,
            )
        rendered = update_text(_TEXT, prepared)
        summary = next(
            line
            for line in rendered.splitlines()
            if "aranami-assessment-totals schema=2" in line
        )
        legacy = rendered.replace(summary, "'''总条目'''2篇。")
        self._retain(prepared, legacy, 1)
        for retained in (before, None):
            with (
                self.subTest(retained=retained is not None),
                patch.object(source, "get_snapshot", return_value=after),
                patch.object(
                    source,
                    "read_previous_snapshot",
                    return_value=retained,
                ),
            ):
                retry = source.prepare_data(
                    self.site,
                    "电子游戏",
                    _TITLE,
                    legacy,
                    observed_at=_NOW,
                )
            assert retry.changes.is_empty()
            if retained is not None:
                assert (
                    retry.previous_totals
                    == before.manifest["metadata"]["article_totals"]
                )
                assert "条目页+1（乙级+1，高重要度+1）" in update_text(
                    legacy,
                    retry,
                )
            else:
                assert retry.previous_source is None
                assert retry.previous_totals is None
                assert (
                    "\n\n<!-- aranami-assessment-totals schema=2 "
                    in update_text(
                        legacy,
                        retry,
                    )
                )

    def test_failed_compact_proposal_retains_reversed_grade_changes(
        self,
    ) -> None:
        """Keep daily transitions after an unconfirmed publication."""
        original = _members((1, "Subject", "B", "High"))
        changed = _members((1, "Subject", "C", "Low"))
        observed = _NOW
        with (
            patch.object(source, "read_previous_snapshot", return_value=None),
            patch.object(
                source,
                "get_snapshot",
                return_value=self._snapshot(original, observed),
            ),
        ):
            initial = source.prepare_data(
                self.site,
                "电子游戏",
                _TITLE,
                _TEXT,
                observed_at=observed,
            )
        self._retain(initial, _TEXT, 1)
        for sequence, members in ((2, changed), (3, original)):
            observed = _NOW + dt.timedelta(days=sequence - 1)
            with (
                patch.object(
                    source,
                    "read_previous_snapshot",
                    return_value=None,
                ),
                patch.object(
                    source,
                    "get_snapshot",
                    return_value=self._snapshot(members, observed),
                ),
                patch.object(
                    source,
                    "page_metadata",
                    return_value=_replica_rows(members),
                ),
            ):
                prepared = source.prepare_data(
                    self.site,
                    "电子游戏",
                    _TITLE,
                    _TEXT,
                    observed_at=observed,
                )
            rendered = update_text(_TEXT, prepared)
            assert "乙级-1、丙级+1，高重要度-1、低重要度+1" in rendered
            if sequence == 3:  # ruff: ignore[magic-value-comparison]
                assert "乙级+1、丙级-1，高重要度+1、低重要度-1" in rendered
            self._retain(prepared, rendered, sequence, state="unconfirmed")
        assert prepared.pending
        assert prepared.changes.item(0, "old_class") == "C"
        assert prepared.changes.item(0, "class") == "B"
        expected_transitions = 2
        assert rendered.count("重新評級") == expected_transitions
