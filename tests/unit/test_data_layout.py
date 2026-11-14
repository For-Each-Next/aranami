"""Verify migration, completed periods and shared SQL capture."""

# Exercise exact persisted contracts and historical identities.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from wiki_fixtures import OfflineSite

from data import migration, project_data
from data.caching import project_observations
from shared.config import PROJECT_DATA_TASKFORCES
from shared.sources.quarry import freshness
from shared.support.assessment_encoding import decode_assessments
from tasks.project_data import capture

_NOW = dt.datetime(2026, 11, 14, tzinfo=dt.UTC)


class TestDataLayout(TestCase):
    """Preserve historical nulls and confirmed revisions."""

    @staticmethod
    def test_one_sql_captures_parent_and_children() -> None:
        """Fetch related assessments once and save Enum taskforces."""
        columns = (
            "page_id",
            "page_namespace",
            "page_title",
            "page_revision",
            "page_rev_timestamp",
            "page_length",
            "page_is_redirect",
            "talk_page_id",
            "talk_revision",
            "pa_class",
            "pa_importance",
            "membership_project",
        )
        rows = [
            (
                1,
                0,
                "A",
                10,
                "20261113235900",
                100,
                0,
                2,
                20,
                "B",
                "Low",
                "电子游戏",
            ),
            (
                3,
                0,
                "B",
                30,
                "20261113235800",
                100,
                0,
                None,
                None,
                "C",
                None,
                "电子游戏",
            ),
            (
                1,
                0,
                "A",
                10,
                "20261113235900",
                100,
                0,
                2,
                20,
                "C",
                "High",
                "Games/A",
            ),
        ]
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                PROJECT_DATA_TASKFORCES,
                {("zhwiki", "电子游戏"): ("Games/A",)},
                clear=True,
            ),
            patch("tasks.project_data.require_boundary") as ready,
            patch(
                "shared.sources.quarry.query._fetch",
                return_value=(columns, rows),
            ) as fetch,
        ):
            root = Path(directory)
            bundle = capture(
                OfflineSite("zh", "wikipedia"),
                "电子游戏",
                _NOW.date(),
                runtime=root,
                observed_at=_NOW,
            )
            assert bundle.path == root / "data/archive/20261113.parquet"
            physical = pl.read_parquet(bundle.path)
            assert physical["pa_taskforces"].to_list() == [["Games/A"], []]
            assert isinstance(physical.schema["pa_taskforces"].inner, pl.Enum)
            assert physical.schema["page_is_redirect"] == pl.Boolean
            assert physical["page_rev_latest"].to_list() == [10, 30]
            assert physical["talk_page_latest"].to_list() == [20, None]
            assert physical["page_rev_timestamp"].item(
                0,
            ) == _NOW - dt.timedelta(minutes=1)
            child = project_data.read_snapshot(
                "zhwiki",
                "Games/A",
                _NOW.date(),
                runtime=root,
            )
            assert child.tables["members"]["pa_class"].to_list() == ["C"]
            fetch.assert_called_once()
            ready.assert_called_once()
            assert fetch.call_args.args[1].is_select

    @staticmethod
    def test_migration_preserves_unknown_talk_revision() -> None:
        """Migrate an archive with a missing revision field."""
        source = pl.DataFrame({
            "page_id": [1],
            "page_namespace": [0],
            "page_title": ["A"],
            "rev_id": [10],
            "talk_page_rev": [20],
            "talk_page_id": [2],
            "page_is_redirect": [0],
            "page_length": [100],
            "pa_class": ["B"],
            "pa_importance": ["Low"],
        })
        with (
            TemporaryDirectory() as directory,
            patch.dict(PROJECT_DATA_TASKFORCES, {}, clear=True),
        ):
            root = Path(directory)
            current = project_data.ensure_snapshot(
                "zhwiki",
                "电子游戏",
                _NOW.date(),
                runtime=root,
                observed_at=_NOW,
                load_members=lambda _: source,
            )
            encoded = (
                pl
                .read_parquet(current.path)
                .drop(
                    "pa_taskforces",
                    "page_rev_timestamp",
                    "talk_page_latest",
                )
                .rename({"page_rev_latest": "rev_id"})
                .with_columns(
                    pl.lit([], dtype=pl.List(pl.String)).alias("taskforces"),
                )
            )
            manifest = dict(current.manifest)
            manifest["schema"] = 1
            manifest["metadata"]["source_contract"] = "project-membership-1"
            manifest["columns"] = {
                name: str(dtype) for name, dtype in encoded.schema.items()
            }
            manifest["sha256"] = hashlib.sha256(
                decode_assessments(encoded).write_json().encode(),
            ).hexdigest()
            legacy = (
                root
                / "reports/data/projects/zhwiki/电子游戏/2026/20261113.parquet"
            )
            legacy.parent.mkdir(parents=True)
            encoded.write_parquet(
                legacy,
                metadata={"aranami": json.dumps(manifest)},
            )
            original = legacy.read_bytes()
            current.path.unlink()
            receipt = migration.migrate(root)
            assert receipt["complete"]
            assert legacy.read_bytes() == original
            converted = pl.read_parquet(root / "data/archive/20261113.parquet")
            assert converted["talk_page_latest"].to_list() == [None]
            assert converted["page_rev_timestamp"].to_list() == [None]
            assert converted["talk_page_id"].to_list() == [2]
            assert migration.migrate(root) == receipt

    @staticmethod
    def test_hour_labels_name_completed_hour() -> None:
        """Use the preceding completed hour at each boundary."""
        _, midnight = project_observations.cache("zhwiki", "电子游戏", _NOW)
        _, next_hour = project_observations.cache(
            "zhwiki",
            "电子游戏",
            _NOW + dt.timedelta(hours=1),
        )
        assert midnight.name == "2026111323.parquet"
        assert next_hour.name == "2026111400.parquet"

    def test_daily_readiness_waits_for_the_boundary(self) -> None:
        """Reject daily capture before replication reaches midnight."""
        for timestamp, ready in (
            ("2026-11-13T23:59:59", False),
            ("2026-11-14T00:00:00", True),
        ):
            with patch.object(freshness, "Replica") as replica:
                query = replica.return_value.query
                query.return_value.collect.return_value = pl.DataFrame({
                    "last_updated": [timestamp],
                })
                if ready:
                    freshness.require_boundary("zhwiki", _NOW.date())
                else:
                    with self.assertRaises(
                        freshness.ReplicaBehindBoundaryError,
                    ):
                        freshness.require_boundary("zhwiki", _NOW.date())
                statement = replica.return_value.query.call_args.args[0]
                assert statement.is_select
                assert "heartbeat_p.heartbeat" in str(statement)
                assert "meta_p.wiki" not in str(statement)
