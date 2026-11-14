"""Build saved quality metrics for responsive browser checks."""

from __future__ import annotations

import datetime as dt
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from data.articles import (
    authorship_store,
    current_authorship,
    current_quality,
    quality_analysis as store,
)
from data.articles.storage import AUTHORSHIP_PATH


def saved_analysis_fixture(
    now: dt.datetime,
    *,
    all_grades: bool = False,
    runtime: Path | None = None,
) -> dict:
    """Prepare real Parquet summaries with long, zero and unknown rows.

    Args:
        now: Shared aware UTC observation clock for these fixtures.
        all_grades: Select the complete universal-grade report preset.
        runtime: Retain fixture sources here for local chart requests;
            omit to remove them when this function returns.

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
            else f"分析条目_Analysis_article_{index}",
            "grade": grades[(index - 1) % len(grades)],
            "importance": importance[(index - 1) % len(importance)],
            "article_revision_id": 1000 + index,
            "talk_page_id": 2000 + index,
            "talk_revision_id": 3000 + index,
            "source_bytes": words * 10 + 1000,
            "listed_date": listed if index % 5 else None,
            "listed_oldid": 4000 + index if index % 5 else None,
            "listed_review_link": "Wikipedia:优良条目候选#Browser fixture"
            if index % 3 == 0
            else None,
            "observed_at": now,
            "measurement_contract": "responsive-fixture:1",
            "prose_words": words if measured else None,
            "prose_characters": words if measured else None,
            "prose_bytes": words * 4 if measured else None,
            "measured_at": now if measured else None,
            "attempted_at": now,
        })
    context = TemporaryDirectory() if runtime is None else nullcontext(runtime)
    with context as directory:
        runtime = Path(directory)
        frame = pl.DataFrame(rows, schema=store.ARTICLE_SCHEMA)
        store.checkpoint(
            runtime,
            members=frame,
            updates=frame,
            observed_at=now,
        )
        evidence, b_members, b_evidence = _authorship_rows(rows, now)
        authorship_store.save(
            runtime / AUTHORSHIP_PATH,
            pl.DataFrame(evidence, schema=authorship_store.SCHEMA),
        )
        current_authorship.checkpoint(
            runtime,
            members=pl.DataFrame(
                b_members,
                schema=current_authorship.MEMBER_SCHEMA,
            ),
            evidence=pl.DataFrame(
                b_evidence,
                schema=current_authorship.EVIDENCE_SCHEMA,
            ),
        )
        assert current_quality.refresh_saved_quality(runtime)
        return current_quality.read_current_quality(
            runtime,
            all_grades=all_grades,
        )


def _authorship_rows(
    rows: list[dict],
    now: dt.datetime,
) -> tuple[list, list, list]:
    """Prepare saved high-quality and B-Class attribution fixtures.

    Returns:
        Listing evidence and current B/BL membership/evidence rows.
    """
    high, b_members, b_evidence = [], [], []
    for row in rows:
        if row["listed_date"] is None:
            continue
        primary = "B_Editor" if row["grade"] in {"B", "BL"} else "Author_A"
        contributors = [
            {
                "editor": "1",
                "name": primary,
                "tokens": 50,
                "bytes": 500,
                "share": 0.50,
            },
            {
                "editor": "2",
                "name": "Author_B",
                "tokens": 25,
                "bytes": 250,
                "share": 0.25,
            },
            {
                "editor": "3",
                "name": "Boundary_Editor",
                "tokens": 10,
                "bytes": 100,
                "share": 0.10,
            },
            {
                "editor": "0|192.0.2.1",
                "name": None,
                "tokens": 15,
                "bytes": 150,
                "share": 0.15,
            },
        ]
        cutoff = dt.datetime.combine(
            row["listed_date"] + dt.timedelta(days=1),
            dt.time(tzinfo=dt.UTC),
        )
        creation = cutoff - dt.timedelta(days=30)
        common = {
            "page_id": row["page_id"],
            "eligible_bytes": 1000,
            "creation_revision": 500 + row["page_id"],
            "fetched_at": now,
            "contributors": contributors,
        }
        if row["grade"] in store.QUALITY_GRADES:
            high.append({
                **common,
                "date": row["listed_date"],
                "grade": row["grade"],
                "action": "added",
                "contract": authorship_store.CONTRACT,
                "listing_revision": row["article_revision_id"],
                "cutoff": cutoff,
                "creation": creation,
                "authors": [primary],
            })
            continue
        b_members.append({
            "page_id": row["page_id"],
            "revision_id": row["article_revision_id"],
            "title": row["title"],
            "grade": row["grade"],
            "observed_at": now,
            "article_timestamp": cutoff,
            "banner_contract": "browser-fixture:1",
            "talk_page_id": row["talk_page_id"],
            "talk_revision": row["talk_revision_id"],
            "listing_talk_revision": 1900 + row["page_id"],
            "listing_timestamp": cutoff,
        })
        b_evidence.append({
            **common,
            "revision_id": row["article_revision_id"],
            "contract": current_authorship.CONTRACT,
            "revision_timestamp": cutoff,
            "creation_timestamp": creation,
        })
    return high, b_members, b_evidence
