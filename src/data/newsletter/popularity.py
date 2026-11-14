"""Store newsletter traffic periods with their original evidence."""

from __future__ import annotations

import datetime as dt
import fcntl
import os
from calendar import monthrange
from pathlib import Path
from uuid import uuid4

import polars as pl

from data.reports import ReportBundle, ReportResult, ReportStore

POPULAR_PAGE = "WikiProject:电子游戏/热门条目"

SCHEMA = {
    "kind": pl.String,
    "start": pl.Date,
    "end": pl.Date,
    "article_count": pl.Int64,
    "total_views": pl.Int64,
    "items": pl.List(pl.Struct({"title": pl.String, "views": pl.Int64})),
    "source_title": pl.String,
    "source_revision": pl.Int64,
    "source_url": pl.String,
    "observed_at": pl.Datetime("us", "UTC"),
    "provisional": pl.Boolean,
}

MONTHS = 12

_MAX_ITEMS = 5000


def _validate(bundle: ReportBundle, year: int) -> pl.DataFrame:
    """Require a bounded typed dataset after the store's hash checks.

    Returns:
        Validated period observations retaining their source identity.

    Raises:
        ValueError: Saved periods or observations are inconsistent.
    """
    frame = bundle.tables.get("periods")
    if frame is None or frame.schema != SCHEMA:
        message = "Invalid retained popularity schema."
        raise ValueError(message)
    if (
        bundle.manifest["family"] != "newsletter-popularity-history"
        or bundle.manifest["metadata"].get("year") != year
        or frame.height > MONTHS + 1
        or frame.select(pl.struct("kind", "start").n_unique()).item()
        != frame.height
    ):
        message = "Invalid retained popularity dataset."
        raise ValueError(message)
    for row in frame.iter_rows(named=True):
        required = (
            "kind",
            "start",
            "end",
            "items",
            "source_title",
            "observed_at",
            "provisional",
        )
        if any(row[key] is None for key in required):
            message = "Popularity period lacks required observations."
            raise ValueError(message)
        begin, end = row["start"], row["end"]
        interval_valid = all((
            row["kind"] in {"monthly", "yearly"},
            begin.year == year,
            end.year == year,
            begin <= end,
            end < row["observed_at"].date(),
            begin.day == 1,
        ))
        counts_valid = (
            len(row["items"]) <= _MAX_ITEMS
            and all(
                value is None or value >= 0
                for value in (row["article_count"], row["total_views"])
            )
            and all(
                item["title"]
                and item["views"] is not None
                and item["views"] >= 0
                for item in row["items"]
            )
        )
        revision = row["source_revision"]
        source_valid = (
            row["source_title"] == POPULAR_PAGE
            and (revision is None or revision > 0)
            and row["source_url"]
            == (
                f"https://zh.wikipedia.org/w/index.php?oldid={revision}"
                if revision
                else None
            )
        )
        if not interval_valid or not counts_valid or not source_valid:
            message = "Invalid popularity interval or count."
            raise ValueError(message)
        if row["kind"] == "monthly" and (
            end.month != begin.month
            or end.day != monthrange(year, begin.month)[1]
            or len(row["items"]) != 1
            or row["provisional"]
        ):
            message = "Monthly popularity requires a completed exact month."
            raise ValueError(message)
        if row["kind"] == "yearly" and (
            begin.month != 1
            or row["provisional"] != (end != dt.date(year, 12, 31))
            or row["article_count"] is None
            or row["total_views"] is None
        ):
            message = "Annual popularity requires explicit measured totals."
            raise ValueError(message)
    return frame


def import_records(result: dict, year: int, now: dt.datetime) -> pl.DataFrame:
    """Convert verified imported facts to the shared period schema.

    Args:
        result: Prepared monthly and annual historical observations.
        year: Calendar year owning these observations.
        now: Actual UTC observation time for the import.

    Returns:
        Typed monthly winners and annual measured aggregates.
    """
    records = []
    for value in result["monthly_popularity"]:
        start = dt.date(year, value["month"], 1)
        records.append({
            "kind": "monthly",
            "start": start,
            "end": start.replace(day=value["days"]),
            "article_count": None,
            "total_views": None,
            "items": [{"title": value["title"], "views": value["views"]}],
            "source_revision": value.get("revision_id"),
            "provisional": False,
        })
    if (value := result["annual_popularity"]) is not None:
        records.append({
            "kind": "yearly",
            "start": dt.date.fromisoformat(value["start"]),
            "end": dt.date.fromisoformat(value["end"]),
            "article_count": value["article_count"],
            "total_views": value["total_views"],
            "items": value["items"],
            "source_revision": value.get("revision_id"),
            "provisional": value["provisional"],
        })
    for row in records:
        revision = row["source_revision"]
        row.update(
            source_title=POPULAR_PAGE,
            source_url=f"https://zh.wikipedia.org/w/index.php?oldid={revision}"
            if revision
            else None,
            observed_at=now,
        )
    return pl.DataFrame(records, schema=SCHEMA)


def period_facts(frame: pl.DataFrame) -> dict:
    """Expose retained periods through newsletter's fact interface.

    Args:
        frame: Validated canonical period records.

    Returns:
        Monthly winners and the selected annual ranking, if retained.
    """
    result = {"monthly_popularity": [], "annual_popularity": None}
    for row in frame.sort("start").iter_rows(named=True):
        evidence = {key: row[key] for key in ("source_title", "source_url")}
        if row["source_revision"] is not None:
            evidence["revision_id"] = row["source_revision"]
        if row["kind"] == "monthly":
            result["monthly_popularity"].append({
                "month": row["start"].month,
                "days": row["end"].day,
                **row["items"][0],
                **evidence,
            })
        else:
            result["annual_popularity"] = {
                "start": row["start"].isoformat(),
                "end": row["end"].isoformat(),
                "article_count": row["article_count"],
                "total_views": row["total_views"],
                "items": row["items"],
                "provisional": row["provisional"],
                **evidence,
            }
    return result


class PopularitySource:
    """Read and accept historical periods through one metadata contract.

    Args:
        runtime: Runtime home, never immutable source.
        dry: Select the isolated preview data store.
    """

    def __init__(self, runtime: Path, *, dry: bool = False) -> None:
        """Select an existing runtime without creating any files."""
        self.store = ReportStore(
            runtime
            / ("dry-run" if dry else "")
            / "data/daily/newsletter/popularity-history",
            dry=dry,
        )

    def read(self, year: int) -> pl.DataFrame:
        """Read validated saved periods without filesystem mutation.

        Args:
            year: Exact calendar year of measured traffic.

        Returns:
            Saved typed rows, or an empty schema before first import.
        """
        key = str(year)
        if not (self.store.root / key).exists():
            return pl.DataFrame(schema=SCHEMA)
        descriptor = os.open(
            self.store.lock_root / key / ".lock",
            os.O_RDONLY | os.O_NOFOLLOW,
        )
        with os.fdopen(descriptor, "rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            bundle = self.store.read(key, locked=True)
        return (
            _validate(bundle, year) if bundle else pl.DataFrame(schema=SCHEMA)
        )

    def save(
        self,
        year: int,
        frame: pl.DataFrame,
        now: dt.datetime,
    ) -> pl.DataFrame:
        """Merge validated imported periods and verify durable readback.

        Args:
            year: Calendar year covered by the supplied rows.
            frame: Source observations using the normal period schema.
            now: Actual aware UTC import time.

        Returns:
            Accepted complete inventory; repeated imports are inert.

        Raises:
            ValueError: Source metadata is invalid or readback differs.
        """
        key = str(year)
        with self.store.locked(key):
            old = self.store.read(key, locked=True)
            previous = (
                _validate(old, year) if old else pl.DataFrame(schema=SCHEMA)
            )
            merged = (
                pl
                .concat([previous, frame])
                .sort("end", descending=True)
                .unique(
                    ["kind", "start"],
                    keep="first",
                    maintain_order=True,
                )
                .sort("kind", "start")
            )
            candidate = ReportBundle(
                Path(),
                {
                    "family": "newsletter-popularity-history",
                    "metadata": {"year": year},
                },
                {"periods": merged},
                "",
            )
            _validate(candidate, year)
            if merged.equals(previous):
                return previous
            self.store.write(
                key,
                ReportResult(
                    "newsletter-popularity-history",
                    {"periods": merged},
                    metadata={"year": year, "periods": merged.height},
                    calculated_at=now,
                ),
                "",
                run_id=uuid4().hex,
                started_at=now,
                target={"site": "zhwiki", "title": POPULAR_PAGE},
                locked=True,
            )
            accepted = _validate(self.store.read(key, locked=True), year)
            if not accepted.equals(merged):
                message = "Popularity import readback differs."
                raise ValueError(message)
            return accepted
