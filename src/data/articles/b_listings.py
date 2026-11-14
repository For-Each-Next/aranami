"""Retain B/BL talk ranges and resumable history in Parquet.

Exact talk/parser keys reuse listing dates across subject edits.
The established a34 report store owns locking and atomic generations.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from uuid import uuid4

import polars as pl

from data.articles.storage import DEFAULT_ROOT
from data.reports import ReportResult, ReportStore

STAMP = pl.Datetime("us", "UTC")
KEY = [
    "page_id",
    "talk_page_id",
    "scanned_talk_rev_id",
    "class",
    "parser_version",
]
MEMBER_SCHEMA = {
    "page_id": pl.Int64,
    "talk_page_id": pl.Int64,
    "scanned_talk_rev_id": pl.Int64,
    "class": pl.String,
    "parser_version": pl.String,
    "title": pl.String,
    "talk_title": pl.String,
    "observed_at": STAMP,
}
PROGRESS_SCHEMA = {
    "banner_entry_talk_rev_id": pl.Int64,
    "banner_entry_timestamp": STAMP,
    "banner_prior_talk_rev_id": pl.Int64,
    "banner_transition_status": pl.String,
    "reason": pl.String,
    "cursor_revision": pl.Int64,
    "cursor_timestamp": STAMP,
    "expected_revision": pl.Int64,
    "attempted_at": STAMP,
}
ANCHOR_SCHEMA = {
    "anchor_talk_rev_id": pl.Int64,
    "anchor_class": pl.String,
    "anchor_entry_talk_rev_id": pl.Int64,
    "anchor_entry_timestamp": STAMP,
    "anchor_prior_talk_rev_id": pl.Int64,
    "anchor_transition_status": pl.String,
}
PROGRESS_SCHEMA = {**PROGRESS_SCHEMA, **ANCHOR_SCHEMA}
INFERENCE_SCHEMA = {**MEMBER_SCHEMA, **PROGRESS_SCHEMA}
_STORE_ROOT = DEFAULT_ROOT
_KEY = "articles/b-listings/zhwiki-video-games"
_MAX_ROWS = 100_000
_MAX_BYTES = 64 * 1024 * 1024


def _validate(frame: pl.DataFrame) -> None:
    """Reject corrupt identities, chronology and incomplete proof.

    Raises:
        ValueError: Range evidence violates its storage contract.
    """
    if (
        frame.schema != pl.Schema(INFERENCE_SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES
    ):
        message = "Invalid B-listing schema or size."
        raise ValueError(message)
    if (
        frame["page_id"].n_unique() != frame.height
        or frame.filter(
            pl.col("page_id").is_null()
            | (pl.col("page_id") <= 0)
            | pl.col("class").is_null()
            | ~pl.col("class").is_in(["乙", "乙级列表"])
            | pl.col("parser_version").is_null()
            | pl.col("observed_at").is_null()
            | pl.col("title").is_null()
            | ~pl.col("title").str.len_bytes().is_between(1, 4096)
            | (pl.col("parser_version").str.len_bytes() == 0)
            | (pl.col("talk_page_id") <= 0)
            | (pl.col("scanned_talk_rev_id") <= 0)
            | (pl.col("banner_entry_talk_rev_id") <= 0)
            | (pl.col("banner_prior_talk_rev_id") <= 0)
            | (
                pl.col("banner_prior_talk_rev_id")
                >= pl.col("banner_entry_talk_rev_id")
            )
            | (
                pl.col("banner_entry_talk_rev_id")
                > pl.col("scanned_talk_rev_id")
            )
            | (
                ~pl.col("banner_transition_status").is_in([
                    "proven",
                    "partial",
                    "unavailable",
                ])
            ).fill_null(value=False)
            | (
                (pl.col("reason") == "earliest_visible")
                & (
                    pl.col("expected_revision").ne(0).fill_null(value=True)
                    | pl.col("banner_entry_talk_rev_id").is_null()
                )
            )
            | (pl.col("banner_entry_timestamp") > pl.col("observed_at"))
            | (
                pl.col("banner_entry_talk_rev_id").is_null()
                != pl.col("banner_entry_timestamp").is_null()
            )
            | (
                (pl.col("banner_transition_status") == "proven")
                & (
                    pl.col("banner_entry_talk_rev_id").is_null()
                    | pl.col("banner_prior_talk_rev_id").is_null()
                )
            )
            | (pl.col("anchor_talk_rev_id") <= 0)
            | (pl.col("anchor_entry_talk_rev_id") <= 0)
            | (
                pl.col("anchor_entry_talk_rev_id")
                > pl.col("anchor_talk_rev_id")
            )
            | (pl.col("anchor_talk_rev_id") >= pl.col("scanned_talk_rev_id"))
            | (
                pl.col("anchor_talk_rev_id").is_not_null()
                & pl.any_horizontal(
                    pl.col(
                        "anchor_class",
                        "anchor_entry_talk_rev_id",
                        "anchor_entry_timestamp",
                        "anchor_transition_status",
                    ).is_null(),
                )
            ),
        ).height
    ):
        message = "Invalid B-listing identity or proof boundary."
        raise ValueError(message)


def _read(store: ReportStore, *, locked: bool = False) -> pl.DataFrame:
    """Read a validated generation or first-use empty state.

    Returns:
        Current talk-range facts with resumable cursors.
    """
    bundle = store.read(_KEY, locked=locked)
    frame = (
        bundle.tables["inference"]
        if bundle
        else pl.DataFrame(schema=INFERENCE_SCHEMA)
    )
    legacy = {
        name: dtype
        for name, dtype in INFERENCE_SCHEMA.items()
        if name not in ANCHOR_SCHEMA
    }
    if frame.schema == pl.Schema(legacy):
        frame = frame.with_columns(
            *(
                pl.lit(None, dtype=dtype).alias(name)
                for name, dtype in ANCHOR_SCHEMA.items()
            ),
        ).select(list(INFERENCE_SCHEMA))
    _validate(frame)
    return frame


def load(runtime: Path = Path()) -> dict[str, pl.DataFrame]:
    """Read saved range facts without source requests.

    Args:
        runtime: Runtime root containing the established report store.

    Returns:
        Validated inference table, or typed first-use emptiness.
    """
    return {"inference": _read(ReportStore(runtime / _STORE_ROOT))}


def _valid_parser_migration(value: object) -> bool:
    """Validate one caller-certified pair of distinct parser hashes.

    Returns:
        Whether the pair contains two complete lowercase SHA-256 keys.
    """
    if not isinstance(value, tuple):
        return False
    try:
        previous, current = value
    except ValueError:
        return False
    return previous != current and all(
        isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
        for item in value
    )


def checkpoint(
    runtime: Path,
    *,
    members: pl.DataFrame | None = None,
    update: pl.DataFrame | None = None,
    completed_parser_migration: tuple[str, str] | None = None,
) -> pl.DataFrame:
    """Atomically retain the cohort or one bounded history attempt.

    Args:
        runtime: Runtime owner for the a34 report store.
        members: Complete current B/BL cohort, or none to retain it.
        update: Partial or completed progress for one exact member.
        completed_parser_migration: Old and new parser hashes whose
            completed proofs the acquisition owner certifies compatible.
            Only matching cohort identities or newer-head anchors reuse
            those proofs; incomplete progress is never migrated.

    Returns:
        Complete selected range state after validation.

    Raises:
        ValueError: A stale attempt or invalid migration was supplied.
    """
    if completed_parser_migration is not None and (
        members is None
        or not _valid_parser_migration(completed_parser_migration)
    ):
        message = (
            "Completed proof migration requires two cohort parser hashes."
        )
        raise ValueError(message)
    store = ReportStore(runtime / _STORE_ROOT)
    with store.locked(_KEY):
        frame = _read(store, locked=True)
        if members is not None:
            previous = frame.select(
                "page_id",
                *(
                    pl.col(name).alias(f"old_{name}")
                    for name in INFERENCE_SCHEMA
                    if name != "page_id"
                ),
            )
            joined = members.join(
                previous,
                on="page_id",
                how="left",
                validate="1:1",
            )
            complete = (pl.col("old_banner_transition_status") == "proven") | (
                pl.col("old_reason") == "earliest_visible"
            )
            compatible = (
                (
                    complete
                    & (
                        pl.col("old_parser_version")
                        == completed_parser_migration[0]
                    )
                    & (
                        pl.col("parser_version")
                        == completed_parser_migration[1]
                    )
                )
                if completed_parser_migration is not None
                else pl.lit(value=False)
            )
            parser_matches = (
                pl.col("parser_version").eq(
                    pl.col("old_parser_version"),
                )
                | compatible
            )
            exact = parser_matches & pl.all_horizontal(
                pl.col(name).eq_missing(pl.col(f"old_{name}"))
                for name in KEY
                if name not in {"page_id", "parser_version"}
            )
            context = (
                pl.col("talk_page_id").is_not_null()
                & pl.col("talk_page_id").eq(pl.col("old_talk_page_id"))
                & parser_matches
                & (
                    pl.col("scanned_talk_rev_id")
                    > pl.col("old_scanned_talk_rev_id")
                )
            )
            anchor_fields = {
                "anchor_talk_rev_id": "scanned_talk_rev_id",
                "anchor_class": "class",
                "anchor_entry_talk_rev_id": "banner_entry_talk_rev_id",
                "anchor_entry_timestamp": "banner_entry_timestamp",
                "anchor_prior_talk_rev_id": "banner_prior_talk_rev_id",
                "anchor_transition_status": "banner_transition_status",
            }
            updates = []
            for name in PROGRESS_SCHEMA:
                expression = pl.when(exact).then(pl.col(f"old_{name}"))
                if name in anchor_fields:
                    expression = (
                        expression
                        .when(context & complete)
                        .then(
                            pl.col(f"old_{anchor_fields[name]}"),
                        )
                        .when(context)
                        .then(pl.col(f"old_{name}"))
                    )
                updates.append(expression.otherwise(None).alias(name))
            current = (
                joined
                .with_columns(updates)
                .select(list(INFERENCE_SCHEMA))
                .cast(INFERENCE_SCHEMA)
            )
            frame = pl.concat([
                frame.join(
                    members.select("page_id"),
                    on="page_id",
                    how="anti",
                ),
                current,
            ])
        if update is not None:
            if update.join(
                frame.select(KEY),
                on=KEY,
                how="anti",
                nulls_equal=True,
            ).height:
                message = (
                    "B listing progress no longer matches its selected cohort."
                )
                raise ValueError(message)
            frame = pl.concat([
                frame.join(update.select("page_id"), on="page_id", how="anti"),
                update,
            ])
        frame = frame.sort("page_id")
        _validate(frame)
        store.write(
            _KEY,
            ReportResult(family="b-listings", tables={"inference": frame}),
            "",
            run_id=uuid4().hex,
            started_at=dt.datetime.now(dt.UTC),
            target={"site": "zhwiki", "title": "B/BL talk listing dates"},
            locked=True,
        )
        return frame
