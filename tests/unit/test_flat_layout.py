"""Verify flat report publication and predecessor retention."""

# Small fixture cardinalities deliberately describe two generations.
# ruff: file-ignore[magic-value-comparison]

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import polars as pl

from data.reports import ReportResult, ReportStore


class FlatLayoutTests(TestCase):
    """Preserve complete flat generations across publication."""

    @staticmethod
    def test_flat_publication_preserves_readable_generations() -> None:
        """Keep the latest and previous facts after each publication."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReportStore(root / "data/parsed")
            key = "articles/example/zhwiki-video-games"
            start = dt.datetime(2026, 11, 14, tzinfo=dt.UTC)
            for offset in range(2):
                store.write(
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
