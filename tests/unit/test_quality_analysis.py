"""Verify cached metrics, provenance and precomputed summaries."""

# Keep source IDs and summary coverage explicit.
# ruff: file-ignore[magic-value-comparison,private-member-access,pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import subprocess  # ruff: ignore[suspicious-subprocess-import] - Static interpreter probe.
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.articles import history_store, quality_analysis as store
from data.reports import ReportResult, ReportStore
from tasks.articles import quality_analysis as collector
from tasks.zhwiki.article_history import extract_events

_NOW = dt.datetime(2026, 11, 13, 12, tzinfo=dt.UTC)
_DAY = dt.date(2020, 1, 2)


def _membership() -> pl.DataFrame:
    """Prepare one quality article and one Bplus project member.

    Returns:
        Shared current replica-shaped identities without source bodies.
    """
    return pl.DataFrame([
        {
            "page_id": identifier,
            "full_title": f"Game {identifier}",
            "page_namespace": 0,
            "page_is_redirect": 0,
            "page_revision": identifier + 100,
            "page_length": 1000,
            "talk_page_id": identifier + 200,
            "talk_revision": identifier + 300,
            "pa_class": grade,
            "pa_importance": importance,
        }
        for identifier, grade, importance in (
            (1, "典范", "高"),
            (2, "乙上", None),
        )
    ])


def _history() -> pl.DataFrame:
    """Prepare exact source heads with dated successful quality events.

    Returns:
        Current talk snapshots including first-class Bplus history.
    """
    return pl.DataFrame(
        [
            {
                "page_id": identifier,
                "title": f"Game {identifier}",
                "talk_page_id": identifier + 200,
                "revision_id": identifier + 300,
                "contract": "fixture",
                "observed_at": _NOW,
                "events": [
                    {
                        "kind": "quality",
                        "date": _DAY,
                        "grade": grade,
                        "action": "added",
                        "parameter": "action1",
                        "oldid": 50,
                        "nominator": None,
                        "main_author": None,
                        "nomination_link": None,
                        "contributors": None,
                        "action_type": None,
                        "question": None,
                    },
                ],
            }
            for identifier, grade in ((1, "FA"), (2, "Bplus"))
        ],
        schema=history_store.SCHEMA,
    )


def _members() -> pl.DataFrame:
    """Prepare current articles without fetching visible text.

    Returns:
        Complete typed quality cohort with pending measurements.
    """
    return collector.prepare_membership(_membership(), _history(), _NOW)


def _measured(frame: pl.DataFrame, words: int = 100) -> pl.DataFrame:
    """Attach known metrics to the supplied exact revision rows.

    Returns:
        A bounded update with independently observed measurement time.
    """
    return frame.with_columns(
        pl.lit(words).alias("prose_words"),
        pl.lit(400).alias("prose_bytes"),
        pl.lit(_NOW).alias("measured_at"),
        pl.lit(_NOW).alias("attempted_at"),
    ).cast(store.ARTICLE_SCHEMA)


def _save_legacy(
    root: Path,
    frame: pl.DataFrame,
    *,
    old_lengths: bool = True,
) -> None:
    """Retain an earlier report without the importance summary.

    Args:
        root: Isolated runtime fixture owner.
        frame: Current saved article facts and measurements.
        old_lengths: Also retain the superseded prose-comparison schema.
    """
    tables = store._summaries(frame)
    del tables["importance_totals"]
    if old_lengths:
        tables["lengths"] = (
            frame
            .group_by("grade", "importance")
            .agg(
                pl.len().alias("pages"),
                pl.col("prose_words").count().alias("measured"),
                *store._quantiles(),
            )
            .cast(store._LEGACY_LENGTH_SCHEMA)
        )
    ReportStore(root / "data/hourly").write(
        store._KEY,
        ReportResult(
            family="quality-analysis",
            tables=tables,
            metadata={
                "observed_at": frame["observed_at"].item(0).isoformat(),
            },
        ),
        "",
        run_id="b" * 32,
        started_at=_NOW,
        target={"site": "zhwiki", "title": "legacy fixture"},
    )


class QualityAnalysisStoreTests(TestCase):
    """Keep saved readers and selected generations reproducible."""

    @staticmethod
    def test_missing_reads_never_create_runtime_files() -> None:
        """Expose first-use absence without writing runtime files."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = store.read_quality_analysis(root)
            assert not report["available"]
            assert not report["importance_available"]
            assert report["importance_totals"] == []
            assert store.quality_analysis_identity(root) == (None,)
            assert list(root.iterdir()) == []

    @staticmethod
    def test_precomputed_quantiles_preserve_unknown_and_observed_zero() -> (
        None
    ):
        """Read saved summaries without grouping or writer locks."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            members = _members()
            store.checkpoint(root, members=members, observed_at=_NOW)
            store.checkpoint(
                root,
                updates=_measured(members.head(1), 0),
                observed_at=_NOW,
            )
            identity = store.quality_analysis_identity(root)
            with (
                patch.object(store, "_summaries", side_effect=AssertionError),
                patch.object(
                    pl.DataFrame,
                    "group_by",
                    side_effect=AssertionError,
                ),
                patch.object(
                    ReportStore,
                    "locked",
                    side_effect=AssertionError,
                ),
            ):
                report = store.read_quality_analysis(root)
            assert report["coverage"] == {
                "total": 2,
                "measured": 1,
                "dated": 2,
                "median_words": 0.0,
            }
            assert report["articles"][0]["prose_words"] == 0
            assert report["articles"][1]["prose_words"] is None
            assert report["years"] == [
                {
                    "year": 2020,
                    "pages": 2,
                    "grades": {
                        "FA": 1,
                        "FL": 0,
                        "A": 0,
                        "AL": 0,
                        "GA": 0,
                        "Bplus": 1,
                        "B": 0,
                        "BL": 0,
                        **dict.fromkeys(
                            (
                                "C",
                                "CL",
                                "Start",
                                "List",
                                "Stub",
                                "SL",
                                "Unassessed",
                            ),
                            0,
                        ),
                    },
                },
            ]
            assert report["observed_at"] == _NOW.isoformat()
            assert report["importance_available"]
            assert report["importance_totals"] == [
                {
                    "importance": "High",
                    "pages": 1,
                    "measured": 1,
                    "dated": 1,
                },
                {
                    "importance": "Unknown",
                    "pages": 1,
                    "measured": 0,
                    "dated": 1,
                },
            ]
            assert report["lengths_available"]
            assert len(report["lengths"]) == 7
            assert store.quality_analysis_identity(root) == identity
            assert list(root.rglob("*.parquet"))

    @staticmethod
    def test_exact_revision_and_policy_control_metric_reuse() -> None:
        """Reuse exact revisions with matching extraction rules."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            members = _members()
            store.checkpoint(root, members=members, observed_at=_NOW)
            store.checkpoint(
                root,
                updates=_measured(members),
                observed_at=_NOW,
            )
            revised = members.with_columns(pl.lit("Renamed").alias("title"))
            result = store.checkpoint(root, members=revised, observed_at=_NOW)
            assert result["prose_words"].to_list() == [100, 100]
            revised = revised.with_columns(
                (pl.col("article_revision_id") + 1).alias(
                    "article_revision_id",
                ),
            )
            result = store.checkpoint(root, members=revised, observed_at=_NOW)
            assert result["prose_words"].null_count() == 2
            store.checkpoint(root, updates=_measured(result), observed_at=_NOW)
            revised = revised.with_columns(
                pl.lit("different-prose-contract").alias(
                    "measurement_contract",
                ),
            )
            result = store.checkpoint(root, members=revised, observed_at=_NOW)
            assert result["prose_words"].null_count() == 2
            with TestCase().assertRaises(ValueError):
                store.checkpoint(
                    root,
                    updates=_measured(members),
                    observed_at=_NOW,
                )

    @staticmethod
    def test_linear_intervals_and_unknown_dates_are_saved() -> None:
        """Compute linear ranges from observed counts only."""
        members = _members().with_columns(pl.lit("FA").alias("grade"))
        frame = _measured(members).with_columns(
            pl.Series("prose_words", [100, 300], dtype=pl.Int64),
            pl.Series("listed_date", [_DAY, None], dtype=pl.Date),
        )
        tables = store._summaries(frame)
        grade = tables["grade_totals"].row(0, named=True)
        assert grade["pages"] == 2
        assert grade["dated"] == 1
        assert grade["median_words"] == 200
        assert grade["central_68_low"] == 132
        assert grade["central_68_high"] == 268
        assert tables["year_totals"]["pages"].sum() == 1

    @staticmethod
    def test_corrupt_summary_is_rejected_instead_of_becoming_empty() -> None:
        """Reject invalid schemas despite valid storage hashes."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tables = store._summaries(_members())
            tables["coverage"] = pl.DataFrame({"total": [2]})
            ReportStore(root / "data/hourly").write(
                store._KEY,
                ReportResult(family="quality-analysis", tables=tables),
                "",
                run_id="a" * 32,
                started_at=_NOW,
                target={"site": "zhwiki", "title": "fixture"},
            )
            with TestCase().assertRaises(ValueError):
                store.read_quality_analysis(root)

    @staticmethod
    def test_late_metric_update_preserves_newer_cohort_clock() -> None:
        """Keep the newer cohort clock through older worker updates."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            members = _members()
            newer = _NOW + dt.timedelta(hours=1)
            store.checkpoint(
                root,
                members=members.with_columns(
                    pl.lit(newer).alias("observed_at"),
                ),
                observed_at=newer,
            )
            store.checkpoint(
                root,
                updates=_measured(members),
                observed_at=_NOW,
            )
            assert (
                store.read_quality_analysis(root)["observed_at"]
                == newer.isoformat()
            )

    @staticmethod
    def test_summary_intervals_and_coverage_reject_invalid_facts() -> None:
        """Reject nonfinite bounds and impossible summary facts."""
        for column, value in (
            ("central_68_low", float("inf")),
            ("central_95_high", None),
            ("grade", "unrecognized"),
        ):
            tables = store._summaries(_measured(_members()))
            tables["grade_totals"] = tables["grade_totals"].with_columns(
                pl.lit(value, dtype=store.GRADE_SCHEMA[column]).alias(column),
            )
            with TestCase().assertRaises(ValueError):
                store._validate(tables)
        tables = store._summaries(_measured(_members()))
        tables["coverage"] = tables["coverage"].with_columns(
            pl.lit(3, dtype=pl.Int64).alias("measured"),
        )
        with TestCase().assertRaises(ValueError):
            store._validate(tables)

    @staticmethod
    def test_grade_summary_uses_audited_profile_order() -> None:
        """Keep the central profile hierarchy in saved summaries."""
        frame = (
            _members()
            .head(1)
            .drop("grade")
            .join(
                pl.DataFrame({"grade": list(reversed(store.GRADES))}),
                how="cross",
            )
        )
        totals = store._summaries(frame)["grade_totals"]
        assert totals["grade"].to_list() == list(store.GRADES)

    @staticmethod
    def test_prose_comparisons_pool_only_fa_ga_bplus() -> None:
        """Retain list and A totals outside the prose comparisons."""
        frame = (
            _members()
            .head(1)
            .drop("page_id", "grade", "importance")
            .join(
                pl.DataFrame({
                    "page_id": range(1, 10),
                    "grade": [
                        "FA",
                        "FA",
                        "GA",
                        "GA",
                        "Bplus",
                        "Bplus",
                        "FL",
                        "A",
                        "AL",
                    ],
                    "importance": [
                        "Top",
                        "High",
                        "Top",
                        "Mid",
                        "Low",
                        "Unknown",
                        "Top",
                        "Top",
                        "Top",
                    ],
                }),
                how="cross",
            )
            .select(list(store.ARTICLE_SCHEMA))
        )
        frame = _measured(frame).with_columns(
            pl.Series(
                "prose_words",
                [100, 300, 500, 700, 900, 1100, 1000000, 2000000, 3000000],
                dtype=pl.Int64,
            ),
        )
        tables = store._summaries(frame)
        store._validate(tables)
        lengths = tables["lengths"]
        assert lengths["group"].to_list() == [
            "grade",
            "grade",
            "grade",
            "importance",
            "importance",
            "importance",
            "importance",
        ]
        assert lengths["grade"].to_list() == [
            "FA",
            "GA",
            "Bplus",
            None,
            None,
            None,
            None,
        ]
        assert lengths["importance"].to_list() == [
            None,
            None,
            None,
            "Top",
            "High",
            "Mid",
            "Low",
        ]
        assert lengths["pages"].to_list() == [2, 2, 2, 2, 1, 1, 1]
        assert lengths["median_words"].to_list() == [
            200,
            600,
            1000,
            300,
            300,
            700,
            900,
        ]
        top = lengths.filter(pl.col("importance") == "Top").row(
            0,
            named=True,
        )
        assert top["central_68_low"] == 164
        assert top["central_68_high"] == 436
        assert tables["coverage"].row(0) == (9, 9, 9, 900)
        assert tables["articles"].height == 9
        assert tables["years"]["pages"].sum() == 9
        assert tables["grade_totals"]["grade"].to_list() == list(
            store.QUALITY_GRADES,
        )
        importance = tables["importance_totals"]
        assert importance.schema == pl.Schema(store.IMPORTANCE_SCHEMA)
        assert importance["importance"].to_list() == [
            "Top",
            "High",
            "Mid",
            "Low",
            "Unknown",
        ]
        assert importance["pages"].to_list() == [5, 1, 1, 1, 1]
        assert importance["measured"].to_list() == [5, 1, 1, 1, 1]
        assert importance["dated"].to_list() == [5, 1, 1, 1, 1]

    @staticmethod
    def test_empty_prose_comparisons_keep_unknown_intervals() -> None:
        """Keep absent comparisons distinct from observed zero words."""
        tables = store._summaries(_measured(_members().head(1), 0))
        store._validate(tables)
        lengths = tables["lengths"]
        empty = lengths.filter(pl.col("pages") == 0)
        measured = lengths.filter(pl.col("pages") > 0)
        assert empty.height == 5
        assert empty["measured"].to_list() == [0] * 5
        assert empty["median_words"].null_count() == 5
        assert measured["median_words"].to_list() == [0, 0]
        assert (
            store._summaries(
                pl.DataFrame(schema=store.ARTICLE_SCHEMA),
            )["lengths"]["pages"].to_list()
            == [0] * 7
        )
        empty_importance = store._summaries(
            pl.DataFrame(schema=store.ARTICLE_SCHEMA),
        )["importance_totals"]
        assert empty_importance.schema == pl.Schema(store.IMPORTANCE_SCHEMA)
        assert empty_importance.height == 0

    @staticmethod
    def test_prose_comparison_schema_and_coverage_reject_corruption() -> None:
        """Reject missing comparisons and invalid pooled counts."""
        mutations = (
            lambda frame: frame.head(6),
            lambda frame: frame.with_columns(
                pl
                .when(pl.col("grade") == "FA")
                .then(pl.lit("FL"))
                .otherwise(pl.col("grade"))
                .alias("grade"),
            ),
            lambda frame: frame.with_columns(
                pl
                .when(pl.col("group") == "importance")
                .then(pl.lit(100, dtype=pl.Int64))
                .otherwise(pl.col("pages"))
                .alias("pages"),
            ),
        )
        for mutate in mutations:
            tables = store._summaries(_measured(_members()))
            tables["lengths"] = mutate(tables["lengths"])
            with TestCase().assertRaises(ValueError):
                store._validate(tables)

    @staticmethod
    def test_legacy_summaries_wait_for_background_rebuild_without_fetch() -> (
        None
    ):
        """Read old cohorts and rebuild from cached prose metrics."""
        observed = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        members = _measured(_members()).with_columns(
            pl.lit(observed).alias("observed_at"),
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _save_legacy(root, members)
            identity = store.quality_analysis_identity(root)
            with (
                patch.object(store, "_summaries", side_effect=AssertionError),
                patch.object(
                    pl.DataFrame,
                    "group_by",
                    side_effect=AssertionError,
                ),
                patch.object(
                    ReportStore,
                    "locked",
                    side_effect=AssertionError,
                ),
            ):
                report = store.read_quality_analysis(root)
                cached = store.load_articles(root)
            assert report["available"]
            assert not report["importance_available"]
            assert report["importance_totals"] == []
            assert not report["lengths_available"]
            assert report["lengths"] == []
            assert report["coverage"]["measured"] == 2
            assert cached["prose_words"].to_list() == [100, 100]
            assert store.quality_analysis_identity(root) == identity
            with (
                patch.object(
                    collector.history_store,
                    "load",
                    return_value=_history(),
                ),
                patch.object(
                    collector,
                    "read_revision_contents",
                    side_effect=AssertionError,
                ) as fetch,
            ):
                collector.refresh_quality_analysis(
                    OfflineSite("zh"),
                    _membership(),
                    runtime=root,
                    max_pages=0,
                    time_budget=0,
                )
            fetch.assert_not_called()
            report = store.read_quality_analysis(root)
            assert report["importance_available"]
            assert report["importance_totals"] == [
                {"importance": "High", "pages": 1, "measured": 1, "dated": 1},
                {
                    "importance": "Unknown",
                    "pages": 1,
                    "measured": 1,
                    "dated": 1,
                },
            ]
            assert report["lengths_available"]
            assert len(report["lengths"]) == 7
            assert [row["prose_words"] for row in report["articles"]] == [
                100,
                100,
            ]

    @staticmethod
    def test_missing_importance_keeps_current_prose_and_cached_facts() -> None:
        """Upgrade one summary while retaining cached article facts."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            frame = _measured(_members())
            _save_legacy(root, frame, old_lengths=False)
            with (
                patch.object(store, "_summaries", side_effect=AssertionError),
                patch.object(
                    pl.DataFrame,
                    "group_by",
                    side_effect=AssertionError,
                ),
            ):
                report = store.read_quality_analysis(root)
            assert report["lengths_available"]
            assert not report["importance_available"]
            original = store.load_articles(root)
            store.checkpoint(root, observed_at=_NOW)
            upgraded = store.read_quality_analysis(root)
            assert upgraded["importance_available"]
            assert upgraded["lengths"] == report["lengths"]
            assert upgraded["coverage"] == report["coverage"]
            assert store.load_articles(root).equals(original)

    @staticmethod
    def test_importance_summary_rejects_invalid_schema_and_counts() -> None:
        """Reject malformed or contradictory saved importance totals."""
        for column, value in (
            ("importance", "unrecognized"),
            ("importance", "High"),
            ("pages", 3),
            ("measured", 3),
            ("dated", None),
        ):
            tables = store._summaries(_measured(_members()))
            tables["importance_totals"] = tables[
                "importance_totals"
            ].with_columns(
                pl.lit(value, dtype=store.IMPORTANCE_SCHEMA[column]).alias(
                    column,
                ),
            )
            with TestCase().assertRaises(ValueError):
                store._validate(tables)
        tables = store._summaries(_members())
        tables["importance_totals"] = tables["importance_totals"].drop("dated")
        with TestCase().assertRaises(ValueError):
            store._validate(tables)


class QualityAnalysisCollectionTests(TestCase):
    """Reuse saved talk evidence while acquiring changed bodies."""

    @staticmethod
    def test_collection_lease_defers_rivals_and_releases_after_finish() -> (
        None
    ):
        """Defer rivals while permitting independent checkpoints."""
        pages = [
            Mock(page_id=i, revision_id=i + 100, text="正文文字")
            for i in (1, 2)
        ]
        with (
            TemporaryDirectory() as directory,
            patch.object(
                collector.history_store,
                "load",
                return_value=_history(),
            ) as history,
            patch.object(
                collector,
                "read_revision_contents",
                return_value={page.revision_id: page for page in pages},
            ) as read,
            patch.object(collector, "extract_prose", return_value="正文文字"),
            patch.object(collector, "count_chinese_units", return_value=2),
            patch.object(collector, "sleep"),
        ):
            root = Path(directory)
            with store.collection_lease(root) as acquired:
                assert acquired
                store.checkpoint(root, members=_members(), observed_at=_NOW)
                collector.refresh_quality_analysis(
                    OfflineSite("zh", "wikipedia"),
                    _membership(),
                    runtime=root,
                )
                read.assert_not_called()
                history.assert_not_called()
                with store.collection_lease(root) as rival:
                    assert not rival
            collector.refresh_quality_analysis(
                OfflineSite("zh", "wikipedia"),
                _membership(),
                runtime=root,
            )
            read.assert_called_once()
            assert (
                store.load_articles(root)["prose_characters"].null_count() == 0
            )

    @staticmethod
    def test_collection_lease_is_nonblocking_across_processes() -> None:
        """Release the OS lease when its owner finishes."""
        script = (
            "import fcntl, os, sys\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_NOFOLLOW)\n"
            "try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except BlockingIOError: sys.exit(23)\n"
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = [
                sys.executable,
                "-B",
                "-c",
                script,
                str(root / "state/quality-analysis-collection.lock"),
            ]
            with store.collection_lease(root) as acquired:
                assert acquired
                result = subprocess.run(command, check=False, timeout=10)  # ruff: ignore[subprocess-without-shell-equals-true] - Static local interpreter and probe.
                assert result.returncode == 23
            result = subprocess.run(command, check=False, timeout=10)  # ruff: ignore[subprocess-without-shell-equals-true] - Static local interpreter and probe.
            assert result.returncode == 0

    @staticmethod
    def test_unchanged_and_talk_only_edits_avoid_article_reads() -> None:
        """Reuse cached prose through talk-only changes."""
        site = OfflineSite("zh", "wikipedia")
        pages = [
            Mock(page_id=i, revision_id=i + 100, text="正文文字")
            for i in (1, 2)
        ]
        with (
            TemporaryDirectory() as directory,
            patch.object(
                collector.history_store,
                "load",
                return_value=_history(),
            ),
            patch.object(
                collector,
                "read_revision_contents",
                return_value={page.revision_id: page for page in pages},
            ) as read,
            patch.object(collector, "extract_prose", return_value="正文文字"),
            patch.object(collector, "count_chinese_units", return_value=2),
            patch.object(collector, "sleep"),
        ):
            root = Path(directory)
            collector.refresh_quality_analysis(
                site,
                _membership(),
                runtime=root,
            )
            collector.refresh_quality_analysis(
                site,
                _membership(),
                runtime=root,
            )
            changed = _membership().with_columns(
                (pl.col("talk_revision") + 1).alias("talk_revision"),
            )
            collector.refresh_quality_analysis(site, changed, runtime=root)
            read.assert_called_once()
            report = store.read_quality_analysis(root)
            assert (
                store.load_articles(root)["prose_characters"].null_count() == 0
            )
            assert report["coverage"]["dated"] == 0

    @staticmethod
    def test_failed_and_ahead_sources_remain_unknown_with_fair_retries() -> (
        None
    ):
        """Rotate pending sources and keep absent bodies unknown."""
        site = OfflineSite("zh", "wikipedia")
        ahead = Mock(page_id=1, revision_id=999, text="updated")
        with (
            TemporaryDirectory() as directory,
            patch.object(
                collector.history_store,
                "load",
                return_value=_history(),
            ),
            patch.object(
                collector,
                "read_revision_contents",
                side_effect=[{101: ahead}, {}],
            ) as read,
            patch.object(collector, "sleep"),
        ):
            root = Path(directory)
            for _ in range(2):
                collector.refresh_quality_analysis(
                    site,
                    _membership(),
                    runtime=root,
                    max_pages=1,
                )
            assert [call.args[1] for call in read.call_args_list] == [
                [101],
                [102],
            ]
            report = store.read_quality_analysis(root)
            assert report["coverage"]["measured"] == 0
            assert all(
                row["prose_characters"] is None for row in report["articles"]
            )

    @staticmethod
    def test_latest_action_order_and_exact_talk_head_select_dates() -> None:
        """Select listing dates by current-grade action order."""
        history = _history()
        row = history.row(0, named=True)
        event = row["events"][0]
        row["events"] = [
            {**event, "parameter": "action2", "date": dt.date(2024, 1, 1)},
            {**event, "parameter": "action7", "date": _DAY},
        ]
        history = pl.DataFrame([row], schema=history_store.SCHEMA)
        members = collector.prepare_membership(_membership(), history, _NOW)
        assert members["listed_date"].to_list() == [_DAY, None]
        row["events"].append({
            **event,
            "parameter": "action8",
            "action": "removed",
        })
        history = pl.DataFrame([row], schema=history_store.SCHEMA)
        members = collector.prepare_membership(_membership(), history, _NOW)
        assert members["listed_date"].null_count() == 2

    @staticmethod
    def test_nonquality_redirects_and_departed_members_leave_the_cohort() -> (
        None
    ):
        """Include Bplus and B while excluding current redirects."""
        membership = _membership()
        extra = membership.head(1).with_columns(
            pl.lit(3, dtype=pl.Int64).alias("page_id"),
            pl.lit("乙").alias("pa_class"),
        )
        membership = pl.concat([membership, extra]).with_columns(
            pl
            .when(pl.col("page_id") == 1)
            .then(1)
            .otherwise(0)
            .alias("page_is_redirect"),
        )
        members = collector.prepare_membership(membership, _history(), _NOW)
        assert members["page_id"].to_list() == [2, 3]
        assert members["grade"].to_list() == ["Bplus", "B"]

    @staticmethod
    def test_no_admitted_work_still_saves_full_nullable_membership() -> None:
        """Retain coverage when foreground work stops acquisition."""
        with (
            TemporaryDirectory() as directory,
            patch.object(
                collector.history_store,
                "load",
                return_value=_history(),
            ),
            patch.object(collector, "read_revision_contents") as read,
        ):
            root = Path(directory)
            collector.refresh_quality_analysis(
                OfflineSite("zh", "wikipedia"),
                _membership(),
                runtime=root,
                should_yield=lambda: True,
            )
            read.assert_not_called()
            assert store.read_quality_analysis(root)["coverage"]["total"] == 2

    @staticmethod
    def test_changed_article_only_fetches_its_new_revision() -> None:
        """Fetch only the changed article revision."""
        site = OfflineSite("zh", "wikipedia")
        pages = [
            Mock(page_id=i, revision_id=i + 100, text="正文文字")
            for i in (1, 2)
        ]
        updated = Mock(page_id=1, revision_id=1001, text="新增正文")
        with (
            TemporaryDirectory() as directory,
            patch.object(
                collector.history_store,
                "load",
                return_value=_history(),
            ),
            patch.object(
                collector,
                "read_revision_contents",
                side_effect=[
                    {page.revision_id: page for page in pages},
                    {1001: updated},
                ],
            ) as read,
            patch.object(collector, "extract_prose", return_value="正文文字"),
            patch.object(
                collector,
                "count_chinese_units",
                side_effect=[2, 2, 4],
            ),
            patch.object(collector, "sleep"),
        ):
            root = Path(directory)
            collector.refresh_quality_analysis(
                site,
                _membership(),
                runtime=root,
            )
            changed = _membership().with_columns(
                pl
                .when(pl.col("page_id") == 1)
                .then(1001)
                .otherwise(pl.col("page_revision"))
                .alias("page_revision"),
            )
            collector.refresh_quality_analysis(site, changed, runtime=root)
            assert [call.args[1] for call in read.call_args_list] == [
                [101, 102],
                [1001],
            ]
            assert [
                row["prose_characters"]
                for row in store.read_quality_analysis(root)["articles"]
            ] == [4, 2]

    @staticmethod
    def test_future_latest_date_stays_unknown_without_older_fallback() -> None:
        """Keep a malformed latest date isolated to its article row."""
        history = _history()
        row = history.row(0, named=True)
        event = row["events"][0]
        row["events"].append({
            **event,
            "parameter": "action2",
            "date": _NOW.date() + dt.timedelta(days=1),
        })
        history = pl.DataFrame([row], schema=history_store.SCHEMA)
        members = collector.prepare_membership(_membership(), history, _NOW)
        assert members["listed_date"].null_count() == 2
        assert members["listed_oldid"].null_count() == 2

    @staticmethod
    def test_list_a_grade_reuses_shared_war_listing_evidence() -> None:
        """Match AL to the central A group for WAR actions."""
        membership = (
            _membership()
            .head(1)
            .with_columns(
                pl.lit("甲级列表").alias("pa_class"),
            )
        )
        row = _history().row(0, named=True)
        row["events"][0]["grade"] = "A"
        history = pl.DataFrame([row], schema=history_store.SCHEMA)
        members = collector.prepare_membership(membership, history, _NOW)
        assert members["grade"].item() == "AL"
        assert members["listed_date"].item() == _DAY

    @staticmethod
    def test_supplied_multiple_histories_recover_dates() -> None:
        """Recover later dates and preserve absent oldids."""
        site = OfflineSite("zh", "wikipedia")
        cases = (
            (
                "Girl's Garden",
                "article history",
                "2020年8月7日",
                61028288,
                dt.date(2020, 8, 7),
            ),
            (
                "刺激賽車",
                "article history",
                "2020-08-12",
                None,
                dt.date(2020, 8, 12),
            ),
            (
                "变形 (游戏)",
                "ArticleHistory",
                "2020年3月23日",
                64893713,
                dt.date(2020, 3, 23),
            ),
        )
        memberships, snapshots = [], []
        for identifier, (title, alias, recorded, oldid, _day) in enumerate(
            cases,
            1,
        ):
            row = _membership().row(0, named=True)
            row.update(
                page_id=identifier,
                full_title=title,
                pa_class="优良",
                talk_page_id=identifier + 200,
                talk_revision=identifier + 300,
            )
            memberships.append(row)
            text = (
                "{{Article history|dykdate=2019-01-01}}\n{{"
                + alias
                + "|action1=GAN|action1date="
                + recorded
                + "|action1result=listed|dykdate=|dykentry=PRNo"
                + ("|action1oldid=" + str(oldid) if oldid else "")
                + "}}"
            )
            snapshots.append({
                "page_id": identifier,
                "title": title,
                "talk_page_id": identifier + 200,
                "revision_id": identifier + 300,
                "observed_at": _NOW,
                "contract": "multiple-history-fixture",
                "events": extract_events(
                    text,
                    site,
                    ("Template:Article history", "Template:ArticleHistory"),
                    (),
                ),
            })
        members = collector.prepare_membership(
            pl.DataFrame(memberships),
            pl.DataFrame(snapshots, schema=history_store.SCHEMA),
            _NOW,
        ).sort("page_id")
        assert members["listed_date"].to_list() == [row[4] for row in cases]
        assert members["listed_oldid"].to_list() == [row[3] for row in cases]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store.checkpoint(root, members=members, observed_at=_NOW)
            assert store.read_quality_analysis(root)["coverage"]["dated"] == 3

    @staticmethod
    def test_independent_histories_use_chronological_order() -> None:
        """Compare independent tails with honest ambiguous outcomes."""
        row = _history().row(0, named=True)
        event = row["events"][0]
        for events, expected, oldid in (
            (
                [
                    {
                        **event,
                        "parameter": "action8",
                        "date": dt.date(2020, 1, 1),
                    },
                    {
                        **event,
                        "parameter": "history2_action1",
                        "date": dt.date(2024, 1, 1),
                    },
                ],
                dt.date(2024, 1, 1),
                50,
            ),
            (
                [
                    {
                        **event,
                        "parameter": "action8",
                        "date": dt.date(2024, 1, 1),
                    },
                    {
                        **event,
                        "parameter": "history2_action1",
                        "date": dt.date(2020, 1, 1),
                    },
                ],
                dt.date(2024, 1, 1),
                50,
            ),
            (
                [
                    event,
                    {
                        **event,
                        "parameter": "history2_action1",
                        "action": "removed",
                    },
                ],
                None,
                None,
            ),
            (
                [
                    event,
                    {**event, "parameter": "history2_action1", "oldid": 51},
                ],
                _DAY,
                None,
            ),
            (
                [
                    event,
                    {**event, "parameter": "history2_action1", "date": None},
                ],
                None,
                None,
            ),
            (
                [
                    {
                        **event,
                        "parameter": "action1",
                        "date": dt.date(2025, 1, 1),
                    },
                    {**event, "parameter": "action2", "date": None},
                    {
                        **event,
                        "parameter": "action3",
                        "date": dt.date(2020, 1, 1),
                    },
                    {
                        **event,
                        "parameter": "history2_action1",
                        "date": dt.date(2024, 1, 1),
                    },
                ],
                None,
                None,
            ),
        ):
            history = pl.DataFrame(
                [{**row, "events": events}],
                schema=history_store.SCHEMA,
            )
            members = collector.prepare_membership(
                _membership().head(1),
                history,
                _NOW,
            )
            assert members["listed_date"].item() == expected
            assert members["listed_oldid"].item() == oldid
