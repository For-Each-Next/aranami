"""Build saved quality metrics for responsive browser checks."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from data.articles import quality_analysis as store


def saved_analysis_fixture(now: dt.datetime) -> dict:
    """Prepare real Parquet summaries with long, zero and unknown rows.

    Args:
        now: Shared aware UTC observation clock for these fixtures.

    Returns:
        A complete saved-data report with enough rows for pagination.
    """
    grades = store.GRADES
    importance = ("Top", "High", "Mid", "Low", "Unknown")
    rows = []
    for index in range(1, 49):
        measured = index % 7 != 0
        words = index * 131 if index % 9 else 0
        listed = now.date() - dt.timedelta(days=index * 19)
        rows.append({
            "page_id": index,
            "title": "LongUnbrokenAnalysisArticleIdentifier" * 5
            if index == 1
            else f"分析条目 Analysis article {index}",
            "grade": grades[(index - 1) % len(grades)],
            "importance": importance[(index - 1) % len(importance)],
            "article_revision_id": 1000 + index,
            "talk_page_id": 2000 + index,
            "talk_revision_id": 3000 + index,
            "source_bytes": words * 10 + 1000,
            "listed_date": listed if index % 5 else None,
            "listed_oldid": 4000 + index if index % 5 else None,
            "observed_at": now,
            "measurement_contract": "responsive-fixture:1",
            "prose_words": words if measured else None,
            "prose_bytes": words * 4 if measured else None,
            "measured_at": now if measured else None,
            "attempted_at": now,
        })
    with TemporaryDirectory() as directory:
        runtime = Path(directory)
        frame = pl.DataFrame(rows, schema=store.ARTICLE_SCHEMA)
        store.checkpoint(
            runtime,
            members=frame,
            updates=frame,
            observed_at=now,
        )
        return store.read_quality_analysis(runtime)
