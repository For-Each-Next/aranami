"""Retain detailed daily Pageviews for independent saved-data consumers.

Read the selected bundle without clients. Writers retain actual daily
measurements, project identities, and explicit request coverage before
publication. Missing dates remain absent; report-only zeroes never enter
this store. Atomic report-store selection preserves the last healthy
generation when validation or storage fails.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl

from data.caching.pageviews import SCHEMA as CACHE_SCHEMA
from data.paths import read_storage_path
from data.reports import ReportBundle, ReportResult, ReportStore, report_key
from data.runtime.status import record_dataset

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DAILY_SCHEMA = {
    "page_id": pl.Int64,
    "title": pl.String,
    "date": pl.Date,
    "pageview": pl.Int64,
    "observed": pl.Boolean,
}
MEMBER_SCHEMA = {
    "page_id": pl.Int64,
    "title": pl.String,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "taskforces": pl.List(pl.String),
}
COVERAGE_SCHEMA = {
    "title": pl.String,
    "start": pl.Date,
    "stop": pl.Date,
    "request_succeeded": pl.Boolean,
    "observed_days": pl.Int64,
    "complete": pl.Boolean,
}
_MAX_DAILY_ROWS = 20_000_000


def _store(runtime: Path | None, *, dry: bool) -> ReportStore:
    """Select isolated durable source storage.

    Returns:
        Store using atomic generations and selectors.
    """
    root = (runtime or Path.cwd()) / (
        "dry-run/data/daily" if dry else "data/daily"
    )
    return ReportStore(root, dry=dry)


def _validate(tables: Mapping[str, pl.DataFrame]) -> None:
    """Reject malformed durable facts without inventing empty data.

    Raises:
        ValueError: Stored schemas, measurements, or coverage conflict.
    """
    for name, schema in (
        ("daily", DAILY_SCHEMA),
        ("members", MEMBER_SCHEMA),
        ("coverage", COVERAGE_SCHEMA),
    ):
        if name not in tables or tables[name].schema != pl.Schema(schema):
            message = "Invalid durable Pageviews table schema."
            raise ValueError(message)
    daily, members, coverage = (
        tables[name] for name in ("daily", "members", "coverage")
    )
    invalid = (
        daily.height > _MAX_DAILY_ROWS
        or members.is_empty()
        or members["title"].null_count()
        or members.filter(pl.col("title").str.len_chars() == 0).height
        or members["title"].n_unique() != members.height
        or coverage["title"].n_unique() != coverage.height
        or coverage.height != members.height
        or coverage.join(members, on="title", how="anti").height
        or daily.filter(
            pl.any_horizontal(pl.col("title", "date", "pageview").is_null())
            | (pl.col("pageview") < 0)
            | ~pl.col("observed").fill_null(value=False),
        ).height
        or coverage.filter(
            pl.any_horizontal(pl.all().is_null())
            | (pl.col("start") >= pl.col("stop"))
            | (pl.col("observed_days") < 0),
        ).height
    )
    counts = daily.group_by("title").agg(
        pl.len().alias("actual_days"),
        pl.col("date").n_unique().alias("distinct_days"),
        pl.col("date").min().alias("first_date"),
        pl.col("date").max().alias("last_date"),
    )
    checked = counts.join(coverage, on="title", how="left", validate="1:1")
    if (
        invalid
        or checked.filter(
            pl.col("start").is_null()
            | ~pl.col("request_succeeded")
            | (pl.col("actual_days") != pl.col("distinct_days"))
            | (pl.col("first_date") < pl.col("start"))
            | (pl.col("last_date") >= pl.col("stop")),
        ).height
    ):
        message = "Invalid durable Pageviews observations or coverage."
        raise ValueError(message)
    if (
        coverage
        .join(counts.select("title", "actual_days"), on="title", how="left")
        .with_columns(pl.col("actual_days").fill_null(0))
        .filter(
            (pl.col("observed_days") != pl.col("actual_days"))
            | (
                pl.col("complete")
                != (
                    pl.col("request_succeeded")
                    & (
                        pl.col("actual_days")
                        == (pl.col("stop") - pl.col("start")).dt.total_days()
                    )
                )
            ),
        )
        .height
    ):
        message = "Durable Pageviews completeness does not match daily rows."
        raise ValueError(message)


def read_snapshot(
    site_name: str,
    project: str,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> ReportBundle | None:
    """Read the latest detailed observations without source requests.

    Args:
        site_name: Exact source wiki database name.
        project: Exact WikiProject assessment name.
        runtime: Runtime home, or the caller's working directory.
        dry: Read isolated preview observations.

    Returns:
        Validated selected bundle, or none before the first capture.

    Raises:
        ValueError: Retained tables or identities are damaged.
    """
    store = _store(runtime, dry=dry)
    key = report_key("pageviews", site_name, project)
    if not store.data_path(key).exists():
        return None
    # Writers create the lock before selecting data. Readers open it
    # read-only so HTTP consumers never create runtime directories.
    descriptor = os.open(
        read_storage_path(store.lock_root, key + "/.lock"),
        os.O_RDONLY | os.O_NOFOLLOW,
    )
    with os.fdopen(descriptor, "rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        bundle = store.read(key, locked=True)
    if bundle is not None:
        if bundle.manifest["family"] != "pageview-data" or bundle.manifest[
            "target"
        ] != {"site": site_name, "title": project}:
            message = "Invalid durable Pageviews identity."
            raise ValueError(message)
        _validate(bundle.tables)
    return bundle


def as_cache_frame(bundle: ReportBundle) -> pl.DataFrame:
    """Project durable measured rows into the historical reader schema.

    Args:
        bundle: Validated detailed Pageviews observations.

    Returns:
        Measured rows and successful-request coverage markers. Failed
        requests and missing dates never become measured zeroes.
    """
    _validate(bundle.tables)
    coverage = (
        bundle
        .tables["coverage"]
        .filter(pl.col("request_succeeded"))
        .select(
            pl.col("title").alias("page"),
            pl.col("start").alias("coverage_start"),
            pl.col("stop").alias("coverage_stop"),
        )
    )
    markers = coverage.with_columns(
        pl.lit(None, dtype=pl.Date).alias("date"),
        pl.lit(None, dtype=pl.Int64).alias("pageview"),
    ).select(*CACHE_SCHEMA)
    daily = (
        bundle
        .tables["daily"]
        .select(pl.col("title").alias("page"), "date", "pageview")
        .join(coverage, on="page", how="left", validate="m:1")
        .select(*CACHE_SCHEMA)
    )
    return pl.concat([markers, daily]).sort("page", "date", nulls_last=False)


def save_snapshot(  # ruff: ignore[too-many-arguments]
    site_name: str,
    project: str,
    articles: pl.DataFrame,
    observations: Mapping[str, tuple[dt.date, dt.date, pl.DataFrame | None]],
    *,
    started_at: dt.datetime,
    observed_at: dt.datetime,
    hostname: str,
    project_snapshots: Sequence[dict[str, str]] = (),
    runtime: Path | None = None,
    dry: bool = False,
) -> ReportBundle:
    """Save accepted actual measurements before preparing publication.

    Args:
        site_name: Exact Wiki Replica database identity.
        project: Exact WikiProject assessment name.
        articles: Complete population with titles and assessments.
        observations: Actual requested intervals and measured responses;
            none denotes a failed request, never zero traffic.
        started_at: Actual acquisition start used to order writers.
        observed_at: Actual accepted-data preparation time.
        hostname: Wikimedia hostname used by the traffic API.
        project_snapshots: Daily membership source identities.
        runtime: Runtime home, or the caller's working directory.
        dry: Keep preview data separate from production.

    Returns:
        Selected bundle, retaining its predecessor for recovery.

    Raises:
        ValueError: Sources are malformed or clocks are inconsistent.
    """
    if (
        started_at.tzinfo is None
        or observed_at.tzinfo is None
        or observed_at < started_at
    ):
        message = "Pageviews acquisition requires ordered aware clocks."
        raise ValueError(message)
    members = articles.select(
        pl.col("page_id")
        if "page_id" in articles.columns
        else pl.lit(None, dtype=pl.Int64).alias("page_id"),
        "title",
        pl.col("class").alias("pa_class"),
        pl.col("importance").alias("pa_importance"),
        pl.col("taskforces")
        if "taskforces" in articles.columns
        else pl.lit(None, dtype=pl.List(pl.String)).alias("taskforces"),
    ).cast(MEMBER_SCHEMA)
    frames = []
    coverage_rows = []
    for title in members["title"]:
        start, stop, raw = observations[title]
        rows = (
            raw.select("date", "pageview")
            if raw is not None
            else pl.DataFrame(schema={"date": pl.Date, "pageview": pl.Int64})
        )
        coverage_rows.append((
            title,
            start,
            stop,
            raw is not None,
            rows.height,
            raw is not None and rows.height == (stop - start).days,
        ))
        frames.append(rows.with_columns(pl.lit(title).alias("title")))
    daily = (
        pl
        .concat(frames)
        .join(members.select("title", "page_id"), on="title", how="left")
        .with_columns(pl.lit(value=True).alias("observed"))
        .select(*DAILY_SCHEMA)
        .cast(DAILY_SCHEMA)
        .sort("title", "date")
    )
    coverage = pl.DataFrame(
        coverage_rows,
        schema=COVERAGE_SCHEMA,
        orient="row",
    ).sort("title")
    tables = {"daily": daily, "members": members, "coverage": coverage}
    _validate(tables)
    result = ReportResult(
        family="pageview-data",
        tables=tables,
        metadata={
            "schema": 1,
            "project": project,
            "hostname": hostname,
            "coverage_start": coverage["start"].min().isoformat(),
            "coverage_stop": coverage["stop"].max().isoformat(),
            "observed_at": observed_at.astimezone(dt.UTC).isoformat(),
        },
        provenance={
            "project_snapshots": list(project_snapshots),
            "source": "Wikimedia Pageviews daily observations",
            "missing_dates": "unknown",
        },
        calculated_at=observed_at,
    )
    store = _store(runtime, dry=dry)
    key = report_key("pageviews", site_name, project)
    with store.locked(key):
        previous = store.read(key, locked=True)
        if (
            previous is not None
            and coverage.filter(
                ~pl.col("request_succeeded"),
            ).height
        ):
            return previous
        if (
            previous is not None
            and previous.manifest["metadata"]["coverage_stop"]
            > result.metadata["coverage_stop"]
        ):
            return previous
        # The generic writer revalidates the selected generation under
        # this lock. Release its large daily table before that read.
        del previous
        bundle = store.write(
            key,
            result,
            "",
            run_id=uuid4().hex,
            started_at=started_at,
            target={"site": site_name, "title": project},
            locked=True,
        )
        if bundle.selected:
            record_dataset(
                bundle.manifest
                if coverage["complete"].all()
                else {
                    **bundle.manifest,
                    "metadata": {
                        **bundle.manifest["metadata"],
                        "coverage_stop": None,
                    },
                },
                runtime=runtime,
                dry=dry,
                policy="pageviews19",
            )
        return bundle
