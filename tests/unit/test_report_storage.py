"""Verify complete report retention and publication failures."""

# Keep unittest exception assertions consistent with the existing suite.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data.reports import ReportResult, ReportStore, report_key
from runtime.jobs import JobContext, ProposedEdit, job_run
from shared.deployment import project_version
from tasks.zhwiki.new_pages import build_result

if TYPE_CHECKING:
    from data.reports import ReportBundle

_START = dt.datetime(2026, 10, 20, tzinfo=dt.UTC)
_ARTICLE_COUNT = 75
_RETAINED_GENERATIONS = 2


def _result(value: int = 0) -> ReportResult:
    """Build observations retaining measured zero and unknown rank.

    Args:
        value: Observed view count used for each test article.

    Returns:
        Complete typed result extending beyond displayed report limits.
    """
    return ReportResult(
        family="test-report",
        tables={
            "members": pl.DataFrame(
                {
                    "page_id": list(range(_ARTICLE_COUNT)),
                    "views": [value] * _ARTICLE_COUNT,
                    "site_rank": [None] * _ARTICLE_COUNT,
                },
                schema={
                    "page_id": pl.Int64,
                    "views": pl.Int64,
                    "site_rank": pl.Int64,
                },
            ),
        },
        metadata={"report_date": "2026-10-19"},
        provenance={"source_timestamp": "2026-10-19T18:00:00+00:00"},
    )


def _write(
    store: ReportStore,
    day: int,
    *,
    run_id: str | None = None,
) -> ReportBundle:
    """Write one ordered generation in an isolated store.

    Args:
        store: Isolated storage used by this test.
        day: Day offset defining the original run start.
        run_id: Explicit UUID hex identity, when needed.

    Returns:
        Selected or superseded report bundle.
    """
    return store.write(
        "test-zhwiki-main",
        _result(day),
        f"report {day}",
        run_id=run_id or f"{day:032x}",
        started_at=_START + dt.timedelta(days=day),
        target={"site": "zhwiki", "title": "Wikipedia:Report"},
    )


class TestReportStore(TestCase):
    """Exercise atomic selection, retention, and validated readers."""

    @staticmethod
    def test_timestamped_comparisons_keep_two_with_a_frozen_clock() -> None:
        """Keep distinct results when writes share a second."""
        clock = Mock(UTC=dt.UTC)
        clock.datetime = Mock(wraps=dt.datetime)
        clock.datetime.now.return_value = _START
        with (
            TemporaryDirectory() as directory,
            patch("data.reports.dt", clock),
        ):
            store = ReportStore(Path(directory))
            key = report_key("english-comparisons", "zhwiki", "Report")
            bundles = [
                store.write(
                    key,
                    replace(_result(day), family="enwp_key_articles"),
                    f"comparison {day}",
                    run_id=f"{day:032x}",
                    started_at=_START + dt.timedelta(days=day),
                    target={"site": "zhwiki", "title": "Report"},
                )
                for day in range(1, 5)
            ]
            prefix = "enwp_key_articles-20261020000000"
            assert [bundle.path.name for bundle in bundles] == [
                prefix,
                f"{prefix}-1",
                f"{prefix}-2",
                f"{prefix}-3",
            ]
            assert not bundles[0].path.exists()
            assert not bundles[1].path.exists()
            latest = store.read(key)
            previous = store.read(key, previous=True)
            assert latest.wikitext == "comparison 4"
            assert previous.wikitext == "comparison 3"
            assert not latest.tables["members"].equals(
                previous.tables["members"],
            )

    @staticmethod
    def test_legacy_uuid_predecessor_remains_readable_until_pruned() -> None:
        """Read old selected evidence through the naming transition."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            bundle = _write(store, 1)
            legacy_name = "a" * 32
            legacy = bundle.path.with_name(legacy_name)
            bundle.path.rename(legacy)
            manifest = dict(bundle.manifest, generation=legacy_name)
            (legacy / "manifest.json").write_text(json.dumps(manifest))
            (legacy.parent / "latest.json").write_text(
                json.dumps({
                    "schema": 1,
                    "latest": legacy_name,
                    "previous": None,
                }),
            )
            assert store.read("test-zhwiki-main").path == legacy
            _write(store, 2)
            assert store.read("test-zhwiki-main", previous=True).path == legacy
            _write(store, 3)
            assert not legacy.exists()

    @staticmethod
    def test_complete_tables_and_source_timestamps_round_trip() -> None:
        """Retain rows beyond displayed limits, zeroes, and unknowns."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            bundle = _write(store, 0, run_id="1" * 32)
            saved = store.read("test-zhwiki-main")
            assert saved is not None
            assert saved.tables["members"].height == _ARTICLE_COUNT
            assert saved.tables["members"].item(0, "views") == 0
            assert saved.tables["members"].item(0, "site_rank") is None
            assert saved.wikitext == "report 0"
            assert saved.manifest["calculated_at"] is None
            assert saved.manifest["application_version"] == project_version()
            assert saved.manifest["provenance"] == _result().provenance
            assert saved.manifest["publication"]["state"] == "unattempted"
            assert bundle.path == saved.path

    @staticmethod
    def test_assessment_enums_are_stored_and_decoded_after_validation() -> (
        None
    ):
        """Validate stored Enums before restoring String columns."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            result = replace(
                _result(),
                tables={
                    "members": pl.DataFrame({
                        "pa_class": ["FA", None, "FutureClass"],
                        "pa_importance": ["极高", "无", None],
                    }),
                },
            )
            bundle = store.write(
                "test-zhwiki-main",
                result,
                "report",
                run_id="1" * 32,
                started_at=_START,
                target={"site": "zhwiki", "title": "Report"},
            )
            schema = pl.read_parquet_schema(bundle.path / "members.parquet")
            assert isinstance(schema["pa_class"], pl.Enum)
            assert isinstance(schema["pa_importance"], pl.Enum)
            assert bundle.tables["members"].equals(result.tables["members"])
            assert bundle.manifest["tables"]["members"]["schema"][
                "pa_class"
            ] == str(schema["pa_class"])

    @staticmethod
    def test_retains_latest_and_predecessor_and_rejects_older_run() -> None:
        """Keep two complete bundles without regressing their order."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            oldest = _write(store, 1)
            middle = _write(store, 2)
            newest = _write(store, 3)
            stale = _write(store, 1)
            assert not stale.selected
            assert not oldest.path.exists()
            assert middle.path.exists()
            assert newest.path.exists()
            assert store.read("test-zhwiki-main").wikitext == "report 3"
            assert (
                store.read("test-zhwiki-main", previous=True).wikitext
                == "report 2"
            )
            assert (
                len(list(newest.path.parent.glob("*/manifest.json")))
                == _RETAINED_GENERATIONS
            )

    @staticmethod
    def test_same_run_can_advance_only_its_current_generation() -> None:
        """Accept ordered successors while rejecting stale data."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            first = _write(store, 1)
            assert not _write(store, 1).selected
            successor = store.write(
                "test-zhwiki-main",
                _result(2),
                "successor",
                run_id=first.manifest["run_id"],
                started_at=_START + dt.timedelta(days=1),
                target={"site": "zhwiki", "title": "Wikipedia:Report"},
                previous_generation=first.manifest["generation"],
            )
            assert successor.selected
            assert store.read("test-zhwiki-main").wikitext == "successor"
            assert (
                store.read("test-zhwiki-main", previous=True).path
                == first.path
            )
            stale = store.write(
                "test-zhwiki-main",
                _result(3),
                "stale successor",
                run_id=first.manifest["run_id"],
                started_at=_START + dt.timedelta(days=1),
                target={"site": "zhwiki", "title": "Wikipedia:Report"},
                previous_generation=first.manifest["generation"],
            )
            assert not stale.selected
            assert store.read("test-zhwiki-main").path == successor.path
            newest = _write(store, 2)
            late = store.write(
                "test-zhwiki-main",
                _result(4),
                "late old-run successor",
                run_id=first.manifest["run_id"],
                started_at=_START + dt.timedelta(days=1),
                target={"site": "zhwiki", "title": "Wikipedia:Report"},
                previous_generation=successor.manifest["generation"],
            )
            assert not late.selected
            assert store.read("test-zhwiki-main").path == newest.path

    @staticmethod
    def test_concurrent_late_finisher_cannot_replace_newer_start() -> None:
        """Serialize writes while comparing original start times."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            newer_done = Event()

            def old_run() -> ReportBundle:
                """Finish an earlier calculation after its successor.

                Returns:
                    Superseded bundle after the newer run finishes.
                """
                assert newer_done.wait(timeout=10)
                return _write(store, 1)

            def new_run() -> ReportBundle:
                """Select the later run before the old one finishes.

                Returns:
                    Newly selected complete bundle.
                """
                result = _write(store, 2)
                newer_done.set()
                return result

            with ThreadPoolExecutor(max_workers=2) as executor:
                old = executor.submit(old_run)
                new = executor.submit(new_run)
                assert new.result(timeout=15).selected
                assert not old.result(timeout=15).selected
            assert store.read("test-zhwiki-main").wikitext == "report 2"

    def test_partial_write_preserves_last_selected_bundle(self) -> None:
        """Reject generations whose required table writes fail."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            first = _write(store, 1)
            with (
                patch.object(
                    pl.DataFrame,
                    "write_parquet",
                    side_effect=OSError("disk full"),
                ),
                self.assertRaises(OSError),
            ):
                _write(store, 2)
            assert store.read("test-zhwiki-main").path == first.path
            assert not list(first.path.parent.glob(".generation-*"))

    def test_failed_selector_keeps_both_prior_generations(self) -> None:
        """Keep both inputs until the new selector commits."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            first = _write(store, 1)
            second = _write(store, 2)
            replace_path = Path.replace

            def fail_selector(path: Path, target: Path) -> Path:
                """Fail after installing the complete bundle.

                Returns:
                    The installed path for writes outside the selector.

                Raises:
                    OSError: The new selector cannot be committed.
                """
                if target.name == "latest.json":
                    message = "Selector replacement failed."
                    raise OSError(message)
                return replace_path(path, target)

            with (
                patch.object(Path, "replace", fail_selector),
                self.assertRaises(OSError),
            ):
                _write(store, 3)
            assert first.path.exists()
            assert second.path.exists()
            assert store.read("test-zhwiki-main").path == second.path
            assert (
                store.read("test-zhwiki-main", previous=True).path
                == first.path
            )
            newest = _write(store, 4)
            assert store.read("test-zhwiki-main").path == newest.path
            assert (
                len(list(newest.path.parent.glob("*/manifest.json")))
                == _RETAINED_GENERATIONS
            )

    def test_reader_rejects_tampered_artifact_and_unsafe_keys(self) -> None:
        """Reject incomplete data and titles used as paths."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            bundle = _write(store, 1)
            (bundle.path / "report.wikitext").write_text(
                "tampered",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                store.read("test-zhwiki-main")
            with self.assertRaises(ValueError):
                store.read("../../outside")
            first = report_key("assessment-lists", "zhwiki", "../../Unsafe")
            second = report_key("assessment-lists", "zhwiki", "Other")
            assert first != second
            assert first == "assessment-lists/zhwiki/%2E.%2F..%2FUnsafe"

    @staticmethod
    def test_readable_records_and_isolated_staging() -> None:
        """Keep source names and UTC record times visible."""
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        ):
            store = ReportStore()
            key = report_key(
                "assessment-lists",
                "zhwiki",
                "專題:電子遊戲/列表",
            )
            bundle = store.write(
                key,
                _result(),
                "report",
                run_id="1" * 32,
                started_at=_START,
                target={"site": "zhwiki", "title": "專題:電子遊戲/列表"},
            )
            assert store.root == Path(directory) / "data/daily"
            assert key == "assessment-lists/zhwiki/專題:電子遊戲%2F列表"
            generated = dt.datetime.fromisoformat(
                str(bundle.manifest["generated_at"]),
            )
            assert bundle.path.name == f"test-report-{generated:%Y%m%d%H%M%S}"
            assert (bundle.path / "members.parquet").exists()
            assert bundle.path.relative_to(store.root).as_posix().isascii()
            assert bundle.manifest["report_key"] == key
            assert bundle.manifest["target"]["title"] == "專題:電子遊戲/列表"
            assert not list(store.root.rglob(".generation-*"))
            assert store.lock_root.is_relative_to(Path(directory) / "state")

    @staticmethod
    def test_daily_observation_installs_named_files_once() -> None:
        """Retain the first observation directly in its date folder."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory) / "data/daily", daily=True)
            key = report_key(
                "project-assessments",
                "zhwiki",
                "電子遊戲",
                variant="20261020",
            )
            first = store.write(
                key,
                _result(1),
                "first",
                run_id="1" * 32,
                started_at=_START,
                target={"site": "zhwiki", "title": "電子遊戲"},
            )
            again = store.write(
                key,
                _result(2),
                "second",
                run_id="2" * 32,
                started_at=_START + dt.timedelta(hours=1),
                target={"site": "zhwiki", "title": "電子遊戲"},
            )
            assert first.path == store.data_path(key)
            assert first.manifest["report_key"].endswith("20261020")
            assert (first.path / "members.parquet").exists()
            assert again.wikitext == "first"
            assert store.read(key).wikitext == "first"
            assert store.read(key, previous=True) is None


class TestUnicodeReportStorage(TestCase):
    """Preserve retained report identities during path migration."""

    def test_legacy_unicode_bundle_is_readable_without_shadow_writes(
        self,
    ) -> None:
        """Keep old evidence until an explicit path migration."""
        with TemporaryDirectory() as directory:
            store = ReportStore(Path(directory))
            title = "專題:電子遊戲/列表"
            key = report_key("assessment-lists", "zhwiki", title)
            bundle = store.write(
                key,
                _result(),
                "retained report",
                run_id="1" * 32,
                started_at=_START,
                target={"site": "zhwiki", "title": title},
            )
            current = bundle.path.parent
            legacy = store.root / key
            current.rename(legacy)
            original = (legacy / "latest.json").read_bytes()
            saved = store.read(key)
            assert saved.wikitext == "retained report"
            assert saved.path == legacy / bundle.path.name
            assert saved.manifest["report_key"] == key
            with self.assertRaisesRegex(ValueError, "[Mm]igrat"):
                store.write(
                    key,
                    _result(1),
                    "later report",
                    run_id="2" * 32,
                    started_at=_START + dt.timedelta(days=1),
                    target={"site": "zhwiki", "title": title},
                )
            assert (legacy / "latest.json").read_bytes() == original
            assert not current.exists()


class TestStoredPublication(TestCase):
    """Keep calculated data independent of save and preview outcomes."""

    @staticmethod
    def test_daily_source_references_survive_durable_publication() -> None:
        """Retain daily identities with original source metadata."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        reference = {
            "wiki": "zhwiki",
            "project": "Games",
            "snapshot_date": "2026-11-02",
            "boundary_date": "2026-11-03",
            "sha256": "a" * 64,
            "generation": "20261103T000001Z",
            "observed_at": "2026-11-03T00:00:01+00:00",
            "scheduled_for": "2026-11-03T00:00:00+00:00",
        }
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch(
                "runtime.execution.project_snapshot_references",
                return_value=[reference],
            ),
        ):
            context = JobContext(site, _START.date(), dry=True)
            context.publish(
                ProposedEdit(
                    site,
                    "Target",
                    "body",
                    "summary",
                    report_key="test-zhwiki-main",
                    result=_result(),
                ),
            )
            saved = ReportStore(dry=True).read("test-zhwiki-main")
        assert saved is not None
        assert saved.manifest["provenance"]["project_snapshots"] == [reference]
        assert saved.manifest["provenance"]["source_timestamp"] == (
            "2026-10-19T18:00:00+00:00"
        )

    @staticmethod
    def test_one_routine_publishes_successive_complete_results() -> None:
        """Retain and publish each day under one routine identity."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        page = Mock(text="old")
        page.get.side_effect = lambda _page=page, **_kwargs: _page.text
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch("updater.publisher.pywikibot.Page", return_value=page),
        ):
            context = JobContext(site, _START.date(), dry=False)
            with job_run("pageviews", context=context):
                context.register_targets(["Wikipedia:Report"])
                for old, new in (("old", "first"), ("first", "second")):
                    with context.report_attempt("Wikipedia:Report"):
                        assert context.publish(
                            ProposedEdit(
                                site,
                                "Wikipedia:Report",
                                new,
                                "summary",
                                original_text=old,
                                report_key="test-zhwiki-main",
                                result=_result(),
                            ),
                        )
            latest = ReportStore().read("test-zhwiki-main")
            previous = ReportStore().read("test-zhwiki-main", previous=True)
        assert latest.wikitext == "second"
        assert previous.wikitext == "first"
        assert latest.manifest["run_id"] == previous.manifest["run_id"]
        assert latest.manifest["publication"]["state"] == "saved"
        expected_saves = 2
        assert page.save.call_count == expected_saves
        assert context.tasks[0].updated == expected_saves

    def test_failed_save_preserves_complete_unconfirmed_result(self) -> None:
        """Store complete data before a potentially ambiguous save."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        page = Mock(text="old")
        page.get.side_effect = lambda _page=page, **_kwargs: _page.text
        page.save.side_effect = RuntimeError("connection lost")
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch(
                "updater.publisher.pywikibot.Page",
                return_value=page,
            ),
        ):
            context = JobContext(site, _START.date(), dry=False)
            edit = ProposedEdit(
                site,
                "Target",
                "new",
                "summary",
                original_text="old",
                report_key="test-zhwiki-main",
                result=_result(),
            )
            with self.assertRaises(RuntimeError):
                context.publish(edit)
            bundle = ReportStore().read("test-zhwiki-main")
            assert bundle is not None
            assert bundle.tables["members"].height == _ARTICLE_COUNT
            assert bundle.manifest["calculation"] == "complete"
            assert bundle.manifest["publication"]["state"] == "unconfirmed"
            assert bundle.manifest["publication"]["confirmed_edits"] is None

    def test_storage_failure_prevents_save_and_dry_store_is_isolated(
        self,
    ) -> None:
        """Prevent unstored saves and isolate preview outputs."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        edit = ProposedEdit(
            site,
            "Target",
            "new",
            "summary",
            original_text="old",
            report_key="test-zhwiki-main",
            result=_result(),
        )
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch(
                "updater.publisher.pywikibot.Page",
            ) as page,
        ):
            with (
                patch.object(
                    ReportStore,
                    "write",
                    side_effect=OSError("disk full"),
                ),
                self.assertRaises(OSError),
            ):
                JobContext(site, _START.date(), dry=False).publish(edit)
            page.assert_not_called()
            JobContext(site, _START.date(), dry=True).publish(edit)
            preview = ReportStore(dry=True).read("test-zhwiki-main")
            assert preview is not None
            assert preview.manifest["publication"]["state"] == "preview"
            assert ReportStore().read("test-zhwiki-main") is None
            page.assert_not_called()

    @staticmethod
    def test_historical_new_page_bootstrap_does_not_invent_observations() -> (
        None
    ):
        """Keep unknown fields without historical discovery reads."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        page = Mock()
        page.title.return_value = "Old game"
        page.namespace.return_value = 0
        text = (
            '<!-- aranami begin="new-pages" -->'
            "\n=== 2026年10月19日 ===\n* [[Old game]]\n"
            '<!-- aranami end="new-pages" -->'
        )
        with patch(
            "tasks.zhwiki.new_pages.Page",
            return_value=page,
        ):
            result = build_result(
                text,
                site,
                source_revision=123,
                source_timestamp=_START,
            )
        row = result.tables["articles"].row(0, named=True)
        assert row["page_id"] is None
        assert row["created_at"] is None
        assert row["redirect_conversion"] is None
        assert row["class"] is None
        assert (
            result.tables["daily_records"].item(0, "discovery_count") is None
        )
        assert result.calculated_at is None
        assert result.provenance["source_timestamp"] == _START.isoformat()
        json.dumps(dict(result.metadata), allow_nan=False)
