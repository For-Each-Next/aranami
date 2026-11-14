"""Read newsletter figures through interchangeable audited sources.

The audited metadata provider preserves aggregate observations from the
former implementation. Explicit imports materialize canonical Parquet
without manufacturing page histories. Reads and issue rendering never
initialize or mutate runtime storage.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

import polars as pl

from data.reports import ReportBundle, ReportResult, ReportStore
from shared.support.files import atomic_json, encode_json, read_json_object

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

SCHEMA = {
    "kind": pl.String,
    "date": pl.Date,
    "grade": pl.String,
    "count": pl.Int64,
    "source_title": pl.String,
    "source_revision": pl.Int64,
    "source_url": pl.String,
    "license": pl.String,
    "coverage": pl.String,
    "audited_on": pl.Date,
    "source_note": pl.String,
}
_KEY = "audited-aggregates"
_MAX_BYTES = 256 * 1024
_MAX_ROWS = 4096
_ACTIVE_SOURCE: ContextVar[NewsletterFigureSource | None] = ContextVar(
    "newsletter_figure_source",
    default=None,
)
_SOURCE_PROVIDERS: ContextVar[Mapping[str, NewsletterFigureSource] | None] = (
    ContextVar("newsletter_figure_providers", default=None)
)
# The same provider interface can replace this issue's temporary source
# once formal observations cover its required historical dates.
ISSUE_FIGURE_SOURCES = {"2027-01": ("canonical", "audited-metadata")}
_DEFAULT_SOURCES = ("canonical", "audited-metadata")


class NewsletterFigureSource(Protocol):
    """Supply typed aggregate observations with explicit coverage.

    Attributes:
        identity: Stable source identifier retained by the issue.
    """

    identity: str

    def read(self, metric: str, through: dt.date) -> pl.DataFrame:
        """Return covered metric rows through an inclusive date.

        Args:
            metric: Aggregate metric identifier.
            through: Inclusive latest supported data date.

        Returns:
            Typed rows with counts, dates, coverage and provenance.
        """
        ...


@dataclass(frozen=True, slots=True)
class FrameFigureSource:
    """Expose metadata or formal observations through one contract.

    Attributes:
        identity: Stable provider identity retained in prepared facts.
        facts: Validated rows with provenance and explicit dates.
    """

    identity: str
    facts: pl.DataFrame

    def read(self, metric: str, through: dt.date) -> pl.DataFrame:
        """Return covered rows without inventing absent-period zeroes.

        Args:
            metric: Aggregate metric identifier.
            through: Inclusive latest supported data date.

        Returns:
            Matching typed observations in date order.
        """
        _validate(self.facts)
        return self.facts.filter(
            (pl.col("kind") == metric) & (pl.col("date") <= through),
        ).sort("date", "grade")


def _validate(frame: pl.DataFrame) -> None:
    """Reject malformed or duplicate aggregate observations.

    Raises:
        ValueError: Dates, values, identities or schemas are invalid.
    """
    if (
        frame.schema != pl.Schema(SCHEMA)
        or frame.is_empty()
        or frame.height > _MAX_ROWS
        or frame.select("kind", "date", "grade").is_duplicated().any()
        or frame.filter(
            pl.any_horizontal(
                pl.col("kind", "date", "count", "coverage").is_null(),
            )
            | ~pl.col("kind").is_in(["quality_stock", "dyk_cumulative"])
            | (pl.col("count") < 0)
            | ((pl.col("kind") == "quality_stock") & pl.col("grade").is_null())
            | (
                (pl.col("kind") == "dyk_cumulative")
                & pl.col("grade").is_not_null()
            )
            | (pl.col("source_revision") <= 0),
        ).height
    ):
        message = "Invalid newsletter aggregate observations."
        raise ValueError(message)


def decode_import(value: dict) -> tuple[dict, pl.DataFrame]:
    """Validate supplied audit metadata through the canonical schema.

    Args:
        value: Bounded original metadata supplied by an import task.

    Returns:
        Original import metadata and validated aggregate facts.

    Raises:
        ValueError: Packaged metadata has the wrong identity.
    """
    if (
        value.get("schema") != 1
        or value.get("dataset") != "newsletter-audited-historical-aggregates"
    ):
        message = "Invalid historical newsletter metadata identity."
        raise ValueError(message)
    frame = (
        pl
        .DataFrame(value["rows"])
        .with_columns(
            pl.col("date", "audited_on").str.to_date(),
        )
        .cast(SCHEMA)
        .select(*SCHEMA)
        .sort("kind", "date", "grade")
    )
    _validate(frame)
    return value, frame


def _store(runtime: Path | None, *, dry: bool) -> ReportStore:
    """Locate the durable dataset outside immutable source.

    Returns:
        The isolated production or preview storage owner.
    """
    root = runtime or Path.cwd()
    return ReportStore(
        root
        / (
            "dry-run/data/daily/newsletter/historical"
            if dry
            else "data/daily/newsletter/historical"
        ),
        dry=dry,
    )


def read_historical_data(
    runtime: Path | None = None,
    *,
    dry: bool = False,
) -> ReportBundle | None:
    """Read canonical figures without creating paths or collecting data.

    Args:
        runtime: Runtime home, defaulting to the working directory.
        dry: Read only the isolated preview dataset.

    Returns:
        Validated retained dataset, or none before its explicit import.
    """
    store = _store(runtime, dry=dry)
    if not (store.root / _KEY).exists():
        return None
    descriptor = os.open(
        store.lock_root / _KEY / ".lock",
        os.O_RDONLY | os.O_NOFOLLOW,
    )
    with os.fdopen(descriptor, "rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        bundle = store.read(_KEY, locked=True)
    if bundle is not None:
        _validate_bundle(bundle)
    return bundle


def _validate_bundle(bundle: ReportBundle) -> None:
    """Require canonical identities and facts after hash checks.

    Raises:
        ValueError: Stored family, tables or row counts differ.
    """
    if bundle.manifest["family"] != "newsletter-historical" or set(
        bundle.tables,
    ) != {"figures"}:
        message = "Invalid historical newsletter dataset."
        raise ValueError(message)
    _validate(bundle.tables["figures"])
    if bundle.tables["figures"].height != bundle.manifest["metadata"]["rows"]:
        message = "Historical newsletter import row count changed."
        raise ValueError(message)


def import_historical_data(
    runtime: Path | None = None,
    *,
    dry: bool = False,
    seed: dict | None = None,
) -> ReportBundle:
    """Accept audited metadata and retire validated temporary staging.

    Args:
        runtime: Runtime home, defaulting to the working directory.
        dry: Import only into isolated preview storage.
        seed: Audited metadata supplied explicitly by an import task.

    Returns:
        The first validated canonical generation. Its receipt records
        original metadata digest, count and coverage; retries are inert.

    Raises:
        ValueError: Canonical data or staged metadata is corrupt.
    """
    store = _store(runtime, dry=dry)
    with store.locked(_KEY):
        bundle = store.read(_KEY, locked=True)
        staged = store.root / "temporary-import.json"
        receipt = store.root / "import-receipt.json"
        if bundle is None:
            if seed is None:
                message = "Historical import requires supplied audit metadata."
                raise ValueError(message)
            value, frame = decode_import(seed)
            store.root.mkdir(parents=True, exist_ok=True)
            if staged.exists() or staged.is_symlink():
                if read_json_object(staged, _MAX_BYTES) != value:
                    message = (
                        "Historical metadata staging differs from its audit."
                    )
                    raise ValueError(message)
            else:
                atomic_json(staged, value, max_bytes=_MAX_BYTES)
            now = dt.datetime.now(dt.UTC)
            bundle = store.write(
                _KEY,
                ReportResult(
                    family="newsletter-historical",
                    tables={"figures": frame},
                    metadata={
                        "seed_sha256": hashlib.sha256(
                            encode_json(value),
                        ).hexdigest(),
                        "rows": frame.height,
                        "coverage_start": frame["date"].min().isoformat(),
                        "coverage_end": frame["date"].max().isoformat(),
                        "source": "audited-metadata",
                    },
                    provenance={
                        "aggregate_only": True,
                        "invented_page_history": False,
                    },
                    calculated_at=now,
                ),
                "",
                run_id=uuid4().hex,
                started_at=now,
                target={
                    "site": "zhwiki",
                    "title": "newsletter historical figures",
                },
                locked=True,
            )
        _validate_bundle(bundle)
        expected = {
            "schema": 1,
            "generation": bundle.manifest["generation"],
            **bundle.manifest["metadata"],
        }
        if receipt.exists() or receipt.is_symlink():
            if read_json_object(receipt, _MAX_BYTES) != expected:
                message = "Historical newsletter import receipt changed."
                raise ValueError(message)
        else:
            atomic_json(receipt, expected, max_bytes=_MAX_BYTES)
        if staged.exists() or staged.is_symlink():
            value = read_json_object(staged, _MAX_BYTES)
            if (
                hashlib.sha256(encode_json(value)).hexdigest()
                != expected["seed_sha256"]
            ):
                message = "Cannot retire unrecognized historical staging."
                raise ValueError(message)
            staged.unlink()
        return bundle


def source_for_issue(
    issue: str,
    *,
    fallback: NewsletterFigureSource | None = None,
) -> NewsletterFigureSource:
    """Select the configured saved or audited metadata figure provider.

    The caller or active registry supplies temporary providers.
    Corrupt canonical data never falls back to source files.

    Args:
        issue: Explicit issue identity selecting its provider policy.
        fallback: Explicit temporary provider supplied by a task.

    Returns:
        One provider implementing the normal figure-source interface.

    Raises:
        ValueError: A provider is unknown or configured data is absent.
    """
    active = _ACTIVE_SOURCE.get()
    if active is not None:
        return active
    providers = _SOURCE_PROVIDERS.get()
    for name in ISSUE_FIGURE_SOURCES.get(issue, _DEFAULT_SOURCES):
        if providers is not None and name in providers:
            return providers[name]
        if name == "canonical":
            if providers is not None:
                # An explicit runtime registry owns canonical selection;
                # preview scopes must not inspect production data.
                continue
            bundle = read_historical_data()
            if bundle is not None:
                return FrameFigureSource(
                    "canonical-historical",
                    bundle.tables["figures"],
                )
        elif name == "audited-metadata":
            if fallback is not None:
                return fallback
        else:
            message = f"Unknown newsletter figure provider: {name}"
            raise ValueError(message)
    message = f"Newsletter figure source unavailable for {issue}."
    raise ValueError(message)


@contextmanager
def using_sources(
    providers: Mapping[str, NewsletterFigureSource] | None,
) -> Iterator[None]:
    """Register runtime providers while preserving each issue's policy.

    Args:
        providers: Named saved providers for this runtime, or none for
            normal read-only lookup. A supplied registry prevents any
            implicit production canonical lookup, including previews.

    Yields:
        No value; each issue selects its own configured provider.
    """
    token = _SOURCE_PROVIDERS.set(
        dict(providers) if providers is not None else None,
    )
    try:
        yield
    finally:
        _SOURCE_PROVIDERS.reset(token)


@contextmanager
def using_source(source: NewsletterFigureSource) -> Iterator[None]:
    """Override figure selection for an explicit caller or offline test.

    Args:
        source: Configured metadata or formal-observation provider.

    Yields:
        No value; nested collection reads use this provider.
    """
    token = _ACTIVE_SOURCE.set(source)
    try:
        yield
    finally:
        _ACTIVE_SOURCE.reset(token)


def prepare_dyk_series(
    review: dict,
    year: int,
    *,
    source: NewsletterFigureSource | None = None,
) -> list[dict]:
    """Combine covered aggregate history with adjacent observed counts.

    Args:
        review: Prepared annual event counts and current observation.
        year: Calendar year covered by the January issue.
        source: Saved provider, or the issue's configured source.

    Returns:
        Explicit dated counts, preserving the original source baseline.
        Nonadjacent history is never bridged with invented appearances.
    """
    source = (
        source if source is not None else source_for_issue(f"{year + 1}-01")
    )
    facts = source.read("dyk_cumulative", dt.date(year - 1, 12, 31))
    rows = facts.select(
        pl.col("date").dt.to_string().alias("date"),
        "count",
    ).to_dicts()
    if rows and facts["date"].max() == dt.date(year - 1, 12, 31):
        baseline = rows[-1]["count"]
        rows.extend(
            {"date": row["date"], "count": baseline + row["count"]}
            for row in review.get("dyk_new_quarters") or []
        )
        current = review.get("dyk_current")
        if current is not None and current["date"] > rows[-1]["date"]:
            rows.append({
                "date": current["date"],
                "count": baseline + current["count"],
            })
    return rows
