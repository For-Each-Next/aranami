"""Present saved quality-article experience and editor activity.

The directory selects the greatest contributors and editors responsible
for at least 33% of eligible listing-time bytes. Each receives one
article count. Collectors retain public edit observations separately.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from data.articles import (
    authorship_corrections,
    authorship_store,
    current_authorship,
)
from data.articles.storage import MILESTONES_PATH
from shared.assessment_profiles import assessment_profile
from shared.support.files import staging_directory
from shared.support.parquet import parquet_metadata, validate_parquet_metadata

GRADES = ("FA", "FL", "A", "AL", "GA", "Bplus")
ACTIVITY_PATH = MILESTONES_PATH.with_name("quality-author-activity.parquet")
ACTIVITY_SCHEMA = {
    "name": pl.String,
    "last_active": pl.Datetime("us", "UTC"),
    "observed_at": pl.Datetime("us", "UTC"),
}
ARTICLE_SCHEMA = {
    "name": pl.String,
    "page_id": pl.Int64,
    "title": pl.String,
    "grade": pl.String,
    "date": pl.Date,
    "nomination_link": pl.String,
    "coauthors": pl.List(pl.String),
    "coauthor_count": pl.UInt32,
    "credit": pl.UInt32,
    "revision_id": pl.Int64,
    "source_url": pl.String,
}
_KEY = ["page_id", "date", "grade", "action"]
_MAX_ROWS = 10_000
_MAX_BYTES = 8 * 1024 * 1024


def prepare_current_quality_articles(
    events: pl.DataFrame,
    evidence: pl.DataFrame,
    assessments: pl.DataFrame,
) -> pl.DataFrame:
    """Gate historical credits by current project quality assessments.

    Args:
        events: Complete retained recognition inventory.
        evidence: Validated listing-time token authorship observations.
        assessments: Latest complete project assessment projection.

    Returns:
        Credits for current main-namespace, nonredirect members assessed
        FA, FL, A, AL, GA or Bplus. Historical listing grades,
        dates and byte attribution retain their original meaning.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    eligible = assessments.filter(
        (pl.col("page_namespace") == 0)
        & (pl.col("page_is_redirect") == 0)
        & profile.expression(
            "pa_class",
            "class",
            preserve_unknown=False,
        ).is_in(GRADES),
    ).select("page_id")
    return prepare_quality_articles(
        events.join(eligible, on="page_id", how="semi"),
        evidence,
    )


def prepare_quality_articles(
    events: pl.DataFrame,
    evidence: pl.DataFrame,
) -> pl.DataFrame:
    """Select primary authors or contributors reaching 33%.

    Args:
        events: Validated complete recognition inventory with titles.
        evidence: Validated listing-time token authorship observations.

    Returns:
        One whole article count per qualified registered editor, using
        the strongest recorded quality grade and latest listing of that
        grade. The denominator includes all retained eligible bytes,
        including anonymous contributions. Nomination credits do not
        establish authorship. Verified nomination links remain nullable.
    """
    listings = (
        events
        .filter(
            (pl.col("kind") == "quality")
            & (pl.col("action") == "added")
            & pl.col("grade").is_in(GRADES)
            & pl.col("date").is_not_null(),
        )
        .sort("updated_at")
        .unique(subset=_KEY, keep="last")
        .select(
            *_KEY,
            "title",
            pl.col("nomination_link")
            if "nomination_link" in events.columns
            else pl.lit(None, dtype=pl.String).alias("nomination_link"),
        )
    )
    if listings.is_empty() or evidence.is_empty():
        return pl.DataFrame(schema=ARTICLE_SCHEMA)
    contributors = (
        evidence
        .filter(pl.col("contract").is_in(authorship_store.CURRENT_CONTRACTS))
        .sort("fetched_at")
        .unique(subset=_KEY, keep="last")
        .select(*_KEY, "contributors", "eligible_bytes")
        .join(listings, on=_KEY, how="inner", validate="1:1")
        .explode("contributors", empty_as_null=False)
        .unnest("contributors")
        .filter(
            (
                pl.col("bytes").cast(pl.UInt64) * 100
                >= pl.col("eligible_bytes").cast(pl.UInt64) * 33
            )
            | (pl.col("bytes") == pl.col("bytes").max().over(_KEY)),
        )
        .with_columns(pl.len().over(_KEY).alias("coauthor_count"))
        .filter(
            pl.col("editor").str.contains(r"^[1-9][0-9]*$")
            & pl.col("name").is_not_null(),
        )
        .with_columns(
            pl
            .col("name")
            .unique()
            .sort()
            .implode()
            .over(_KEY)
            .alias("coauthors"),
        )
    )
    profile = assessment_profile("zhwiki", "电子游戏")
    return (
        contributors
        .with_columns(
            profile.expression(
                "grade",
                "class",
                field="order",
                preserve_unknown=False,
            ).alias("_grade_order"),
            pl.lit(1, dtype=pl.UInt32).alias("credit"),
        )
        .sort(
            ["_grade_order", "date", "title", "page_id"],
            descending=[False, True, False, False],
        )
        .unique(subset=["name", "page_id"], keep="first", maintain_order=True)
        .sort(["date", "title", "page_id"], descending=[True, False, False])
        .with_columns(
            pl.lit(None, dtype=pl.Int64).alias("revision_id"),
            pl.lit(None, dtype=pl.String).alias("source_url"),
        )
        .select(list(ARTICLE_SCHEMA))
        .cast(ARTICLE_SCHEMA)
    )


def prepare_current_b_articles(
    tables: dict[str, pl.DataFrame],
    assessments: pl.DataFrame,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Prepare B/BL listing-time credits and exact-revision coverage.

    Args:
        tables: Validated durable current attribution tables.
        assessments: Latest complete current project assessment state.

    Returns:
        Whole registered-editor credits and collected/total page counts.
        Missing or stale revision evidence contributes no asserted
        author.
        Current B/BL assessment membership is authoritative.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    eligible = (
        assessments
        .with_columns(
            profile.expression(
                "pa_class",
                "class",
                preserve_unknown=False,
            ).alias("grade"),
        )
        .filter(
            (pl.col("page_namespace") == 0)
            & (pl.col("page_is_redirect") == 0)
            & pl.col("grade").is_in(current_authorship.GRADES),
        )
        .select("page_id", "grade")
    )
    members = tables["members"].join(
        eligible,
        on=["page_id", "grade"],
        how="inner",
        validate="1:1",
    )
    observed = members.join(
        tables["evidence"],
        on=current_authorship.KEY,
        how="inner",
        validate="1:1",
    )
    observed = observed.filter(
        pl.col("listing_timestamp").is_not_null()
        & (pl.col("revision_timestamp") <= pl.col("listing_timestamp")),
    )
    coverage = {"collected": observed.height, "total": eligible.height}
    if observed.is_empty():
        return pl.DataFrame(schema=ARTICLE_SCHEMA), coverage
    key = current_authorship.KEY
    return (
        observed
        .explode("contributors", empty_as_null=False)
        .unnest("contributors")
        .filter(
            (
                pl.col("bytes").cast(pl.UInt64) * 100
                >= pl.col("eligible_bytes").cast(pl.UInt64) * 33
            )
            | (pl.col("bytes") == pl.col("bytes").max().over(key)),
        )
        .with_columns(pl.len().over(key).alias("coauthor_count"))
        .filter(
            pl.col("editor").str.contains(r"^[1-9][0-9]*$")
            & pl.col("name").is_not_null(),
        )
        .with_columns(
            pl
            .col("name")
            .unique()
            .sort()
            .implode()
            .over(key)
            .alias("coauthors"),
            pl.col("revision_timestamp").dt.date().alias("date"),
            pl.format(
                "https://zh.wikipedia.org/w/index.php?oldid={}#top",
                pl.col("revision_id"),
            ).alias("nomination_link"),
            pl.lit(1, dtype=pl.UInt32).alias("credit"),
            pl.format(
                "https://zh.wikipedia.org/w/index.php?oldid={}",
                pl.col("revision_id"),
            ).alias("source_url"),
        )
        .unique(subset=["name", "page_id"], maintain_order=True)
        .select(list(ARTICLE_SCHEMA))
        .cast(ARTICLE_SCHEMA),
        coverage,
    )


def _validate_activity(frame: pl.DataFrame) -> None:
    """Reject malformed identities, duplicates and impossible times.

    Raises:
        ValueError: Activity violates its storage contract.
    """
    if (
        frame.schema != pl.Schema(ACTIVITY_SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES * 4
        or frame["name"].n_unique() != frame.height
        or frame.filter(
            pl.col("name").is_null()
            | ~pl.col("name").str.len_bytes().is_between(1, 1000)
            | pl.col("name").str.strip_chars().eq("")
            | pl.col("name").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]")
            | pl.col("observed_at").is_null()
            | (pl.col("last_active") > pl.col("observed_at")).fill_null(
                value=False,
            ),
        ).height
    ):
        message = "Invalid quality-author activity observations."
        raise ValueError(message)


def load_activity(path: Path) -> pl.DataFrame:
    """Read bounded saved latest-public-edit observations.

    Args:
        path: Activity Parquet destination outside source generations.

    Returns:
        Validated activity, or a typed empty frame before collection.

    Raises:
        OSError: The path is unsafe or unreadable.
        ValueError: The saved activity is corrupt or exceeds its bounds.
    """
    if path.is_symlink() or path.parent.is_symlink():
        message = "Quality-author activity requires regular paths."
        raise OSError(message)
    if not path.exists():
        return pl.DataFrame(schema=ACTIVITY_SCHEMA)
    if not path.is_file() or path.stat().st_size > _MAX_BYTES:
        message = "Quality-author activity exceeds its file bound."
        raise ValueError(message)
    try:
        with path.open("rb") as stream:
            metadata = pl.read_parquet_metadata(stream)
            stream.seek(0)
            frame = pl.read_parquet(stream, n_rows=_MAX_ROWS + 1)
        validate_parquet_metadata(
            metadata,
            frame,
            contract="quality-author-activity",
            allow_legacy=False,
        )
    except pl.exceptions.PolarsError as error:
        message = "Quality-author activity is not readable Parquet."
        raise ValueError(message) from error
    _validate_activity(frame)
    return frame


def save_activity(path: Path, refreshed: pl.DataFrame) -> None:
    """Retain newest activity observations with atomic, locked writes.

    Args:
        path: Durable activity destination.
        refreshed: Source observations with nullable edit times.

    Raises:
        OSError: Storage is unsafe or unavailable.
        ValueError: Observations or their encoded file exceed bounds.
    """
    _validate_activity(refreshed)
    if path.is_symlink() or path.parent.is_symlink():
        message = "Quality-author activity requires regular paths."
        raise OSError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(f".{path.name}.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        merged = (
            pl
            .concat([load_activity(path), refreshed])
            .sort("observed_at")
            .unique(subset="name", keep="last", maintain_order=True)
        )
        _validate_activity(merged)
        with NamedTemporaryFile(
            dir=staging_directory(path),
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        try:
            merged.write_parquet(
                temporary,
                metadata=parquet_metadata(
                    merged,
                    contract="quality-author-activity",
                ),
            )
            if temporary.stat().st_size > _MAX_BYTES:
                message = "Quality-author activity exceeds its file bound."
                raise ValueError(message)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            temporary.replace(path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def apply_b_proofs(
    tables: dict[str, pl.DataFrame],
    proofs: pl.DataFrame,
) -> dict[str, pl.DataFrame]:
    """Prepare a derived B evidence view without changing raw tables.

    Args:
        tables: Current B membership and raw revision evidence.
        proofs: Saved provenance ledger loaded by this reader's caller.

    Returns:
        Original tables when no proofs exist, otherwise a new mapping
        with exact-revision corrected evidence and coverage metadata.
    """
    if proofs.is_empty():
        return tables
    return {
        **tables,
        "evidence": authorship_corrections.apply(
            tables["evidence"],
            proofs,
            revision_column="revision_id",
        ),
    }
