"""Verify daily membership selection and temporary hourly isolation."""

# Assertions follow the project's unittest conventions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir
from contextvars import copy_context
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data.observations import (
    membership_observed_at,
    project_snapshot_references,
)
from runtime import hub
from runtime.project_observations import project_cycle
from shared.sources.cycle import read_membership
from tasks.website import collect


def _snapshot(rows: pl.DataFrame) -> SimpleNamespace:
    """Create a complete immutable-source reference fixture.

    Returns:
        Stored rows with actual observation and boundary metadata.
    """
    return SimpleNamespace(
        tables={"members": rows},
        manifest={
            "metadata": {
                "snapshot_date": "2026-11-02",
                "boundary_date": "2026-11-03",
                "scheduled_for": "2026-11-03T00:00:00+00:00",
            },
            "calculated_at": "2026-11-03T00:00:04+00:00",
            "sha256": "a" * 64,
            "generation": "20261103T000004Z",
        },
    )


class TestProjectObservations(TestCase):
    """Keep temporary hourly reads separate from daily facts."""

    @staticmethod
    def test_midnight_invalidates_every_website_dataset() -> None:
        """Refresh every website section at the next boundary."""
        before = dt.datetime(2026, 11, 3, 23, 59, tzinfo=dt.UTC)
        after = dt.datetime(2026, 11, 4, tzinfo=dt.UTC)
        with patch.object(collect, "read_report_cache", return_value=None):
            previous = collect._report_plan(  # ruff: ignore[private-member-access]
                None,
                collect.report_intervals(),
                100,
                before,
            )
            current = collect._report_plan(  # ruff: ignore[private-member-access]
                None,
                collect.report_intervals(),
                100,
                after,
            )
        for name in collect.REPORT_KEYS:
            assert previous["identities"][name] != current["identities"][name]

    @staticmethod
    def test_daily_capture_is_reused_and_preserves_revision_fields() -> None:
        """Reuse selected rows with source-native revision names."""
        instant = dt.datetime(2026, 11, 3, tzinfo=dt.UTC)
        site = Mock()
        site.dbName.return_value = "zhwiki"
        rows = pl.DataFrame({"rev_id": [51], "talk_page_rev": [92]})
        snapshot = _snapshot(rows)
        fetch = Mock(side_effect=AssertionError("Live fallback"))
        with (
            patch(
                "runtime.project_observations.project_capture.capture",
                return_value=snapshot,
            ) as capture,
            project_cycle(now=instant, dry=True),
        ):
            first = read_membership("zhwiki", "Games", fetch, site=site)
            second = read_membership("zhwiki", "Games", fetch, site=site)
            assert membership_observed_at("zhwiki", "Games") == (
                instant + dt.timedelta(seconds=4)
            )
            references = project_snapshot_references()
            references[0]["wiki"] = "modified"
            assert project_snapshot_references()[0]["wiki"] == "zhwiki"
            with project_cycle(now=instant):
                assert project_snapshot_references() == []
            assert project_snapshot_references()[0]["wiki"] == "zhwiki"
            expired = copy_context()
        assert first.equals(second)
        assert first["page_revision"].to_list() == [51]
        assert first["talk_revision"].to_list() == [92]
        capture.assert_called_once_with(
            site,
            "Games",
            instant.date(),
            dry=True,
        )
        fetch.assert_not_called()
        assert project_snapshot_references() == []
        assert expired.run(project_snapshot_references) == []

    def test_hourly_and_pre_activation_passes_remain_temporary(self) -> None:
        """Keep legacy and hourly reads temporary."""
        for instant in (
            dt.datetime(2026, 11, 2, tzinfo=dt.UTC),
            dt.datetime(2026, 11, 3, 1, tzinfo=dt.UTC),
        ):
            with self.subTest(instant=instant):
                fetch = Mock(return_value=pl.DataFrame({"page_id": [1]}))
                with (
                    patch(
                        "runtime.project_observations.project_capture.capture",
                    ) as capture,
                    patch(
                        "runtime.project_observations.collect_project_membership",
                        side_effect=lambda *_, read=fetch, **__: read(),
                    ),
                    project_cycle(now=instant),
                ):
                    read_membership("zhwiki", "Games", fetch, site=Mock())
                    read_membership("zhwiki", "Games", fetch, site=Mock())
                fetch.assert_called_once()
                capture.assert_not_called()

    def test_failed_daily_capture_has_no_live_fallback(self) -> None:
        """Defer failed daily work without a live fallback."""
        fetch = Mock()
        with (
            patch(
                "runtime.project_observations.project_capture.capture",
                side_effect=ValueError("Incomplete daily snapshot"),
            ),
            project_cycle(
                now=dt.datetime(2026, 11, 4, 18, tzinfo=dt.UTC),
                daily=True,
            ),
            self.assertRaises(ValueError),
        ):
            read_membership("zhwiki", "Games", fetch, site=Mock())
        fetch.assert_not_called()

    def test_absent_historical_boundary_never_uses_live_rows(self) -> None:
        """Reject missing history without collecting new rows."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        fetch = Mock()
        for boundary in (dt.date(2026, 11, 2), dt.date(2026, 11, 3)):
            with (
                self.subTest(boundary=boundary),
                TemporaryDirectory() as directory,
                chdir(directory),
                patch(
                    "tasks.project_data.query_pages_by_wikiproject",
                ) as query,
                project_cycle(
                    now=dt.datetime(2026, 11, 4, 20, tzinfo=dt.UTC),
                    boundary_date=boundary,
                ),
                self.assertRaisesRegex(ValueError, "Only today's activated"),
            ):
                read_membership("zhwiki", "Games", fetch, site=site)
            query.assert_not_called()
        fetch.assert_not_called()

    @staticmethod
    def test_capture_reads_current_source_outside_its_own_selector() -> None:
        """Read current replica rows without recursing."""
        site = Mock()
        fetch = Mock(return_value=pl.DataFrame({"rev_id": [7]}))

        def capture(*_args: object, **_kwargs: object) -> SimpleNamespace:
            """Simulate the daily store's source adapter read.

            Returns:
                Snapshot created from a fresh raw source observation.
            """
            observed = read_membership("zhwiki", "Games", fetch, site=site)
            return _snapshot(
                observed.with_columns(
                    pl.lit(None, dtype=pl.Int64).alias("talk_page_rev"),
                ),
            )

        with (
            patch(
                "runtime.project_observations.project_capture.capture",
                side_effect=capture,
            ),
            project_cycle(now=dt.datetime(2026, 11, 4, tzinfo=dt.UTC)),
        ):
            observed = read_membership("zhwiki", "Games", fetch, site=site)
        assert observed["page_revision"].to_list() == [7]
        fetch.assert_called_once()


class TestProjectCollection(TestCase):
    """Recover only today's snapshots and isolate project failures."""

    @staticmethod
    def test_before_activation_never_constructs_clients() -> None:
        """Keep pre-activation source collection unchanged."""
        clock = Mock(UTC=dt.UTC)
        clock.datetime.now.return_value = dt.datetime(
            2026,
            11,
            2,
            tzinfo=dt.UTC,
        )
        with (
            patch.object(hub, "dt", clock),
            patch.object(hub.pywikibot, "Site") as site,
        ):
            hub.refresh_project_data()
        site.assert_not_called()

    def test_restart_only_captures_today_and_continues_other_projects(
        self,
    ) -> None:
        """Keep successful projects when another source fails."""
        instant = dt.datetime(2026, 11, 5, 17, tzinfo=dt.UTC)
        clock = Mock(UTC=dt.UTC)
        clock.datetime.now.return_value = instant
        with (
            patch.object(hub, "dt", clock),
            patch.object(hub, "configure_pywikibot"),
            patch.object(
                hub,
                "PROJECT_DATA_SCOPES",
                (
                    ("zh", "Games"),
                    ("zh", "Games/Subproject"),
                ),
            ),
            patch.object(hub.pywikibot, "Site"),
            patch.object(hub, "collect_project_membership"),
            patch.object(hub, "collect_project_categories"),
            patch.object(
                hub.project_capture,
                "capture",
                side_effect=[ConnectionError("replica"), Mock()],
            ) as capture,
            patch.object(hub._LOGGER, "exception"),  # ruff: ignore[private-member-access]
            self.assertRaises(ExceptionGroup),
        ):
            hub.refresh_project_data()
        assert [call.args[1] for call in capture.call_args_list] == [
            "Games",
            "Games/Subproject",
        ]
        assert all(
            call.args[2] == instant.date() and "observed_at" not in call.kwargs
            for call in capture.call_args_list
        )
