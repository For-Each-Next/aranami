"""Verify flat report publication, migration and predecessor reads."""

# Small fixture cardinalities deliberately describe two generations.
# ruff: file-ignore[magic-value-comparison]

import datetime as dt
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import polars as pl

from data.layout_migration import migrate_layout
from data.reports import ReportResult, ReportStore, report_storage_key


class FlatLayoutTests(TestCase):
    """Preserve complete generations across the layout migration."""

    @staticmethod
    def test_flat_migration_preserves_readable_generations() -> None:
        """Preserve migrated facts when publishing a new generation."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = ReportStore(root / "legacy")
            key = "articles/example/zhwiki-video-games"
            start = dt.datetime(2026, 11, 14, tzinfo=dt.UTC)
            for offset in range(2):
                old.write(
                    key,
                    ReportResult(
                        "example",
                        {"members": pl.DataFrame({"page_id": [offset + 1]})},
                    ),
                    "saved",
                    run_id=str(offset + 1) * 32,
                    started_at=start + dt.timedelta(seconds=offset),
                    target={"site": "zhwiki", "title": "Fixture"},
                )
            legacy = root / "data/hourly" / report_storage_key(key)
            legacy.parent.mkdir(parents=True)
            shutil.copytree(old.data_path(key), legacy)
            receipt = migrate_layout(
                root,
                lambda source, target: (
                    target.parent.mkdir(parents=True, exist_ok=True),
                    shutil.copyfile(source, target),
                ),
            )
            assert receipt["complete"]
            store = ReportStore(root / "data/parsed")
            assert store.read(key).tables["members"]["page_id"].to_list() == [
                2,
            ]
            assert store.read(key, previous=True).tables["members"][
                "page_id"
            ].to_list() == [1]
            store.write(
                key,
                ReportResult(
                    "example",
                    {"members": pl.DataFrame({"page_id": [3]})},
                ),
                "saved",
                run_id="3" * 32,
                started_at=start + dt.timedelta(seconds=3),
                target={"site": "zhwiki", "title": "Fixture"},
            )
            assert store.read(key).tables["members"]["page_id"].to_list() == [
                3,
            ]
            assert store.read(key, previous=True).tables["members"][
                "page_id"
            ].to_list() == [2]
            assert (
                len(list((root / "data/parsed").glob("*--members.parquet")))
                == 2
            )
            assert all(
                path.is_file() for path in (root / "data/parsed").iterdir()
            )
            assert (root / "data/hourly").is_dir()
            assert not (root / "deployment-backups/data-layout-v3").exists()
            assert (legacy / "latest.json").read_bytes() == (
                old.data_path(key) / "latest.json"
            ).read_bytes()
