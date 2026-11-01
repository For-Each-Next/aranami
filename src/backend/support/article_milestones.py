"""Retain all project recognition events and their coverage.

This inventory spans all recorded dates and retains departed members and
older talk revisions. Source credits and computed authorship stay
distinct. Missing evidence prevents completion; exhausted attempts
remain unresolved.
"""

# Public persistence propagates validated file errors.

from __future__ import annotations

import datetime as dt
import fcntl
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from backend.support.article_history import read_versions
from backend.support.article_scope import retain_active
from shared.support.files import staging_directory

SOURCE_SCHEMA = {"title": pl.String, "revision_id": pl.Int64, "url": pl.String}
SCHEMA = {
    "event_id": pl.String,
    "aliases": pl.List(pl.String),
    "page_id": pl.Int64,
    "title": pl.String,
    "talk_page_id": pl.Int64,
    "revision_id": pl.Int64,
    "date": pl.Date,
    "kind": pl.String,
    "grade": pl.String,
    "action": pl.String,
    "parameter": pl.String,
    "nominator": pl.String,
    "main_author": pl.Boolean,
    "nomination_link": pl.String,
    "contributors": pl.List(pl.String),
    "authors": pl.List(pl.String),
    "sources": pl.List(pl.Struct(SOURCE_SCHEMA)),
    "lookup_state": pl.String,
    "reason": pl.String,
    "lookup_cursor": pl.String,
    "assessment_observed_at": pl.Datetime("us", "UTC"),
    "date_status": pl.String,
    "nominator_status": pl.String,
    "authors_status": pl.String,
    "contributors_status": pl.String,
    "nomination_status": pl.String,
    "main_author_status": pl.String,
    "source_status": pl.String,
    "status": pl.String,
    "updated_at": pl.Datetime("us", "UTC"),
    "action_type": pl.String,
    "question": pl.String,
    "question_status": pl.String,
    "occurred_at": pl.Datetime("us", "UTC"),
    "oldid": pl.Int64,
}
_LEGACY_SCHEMA = {
    key: value
    for key, value in SCHEMA.items()
    if key
    not in {
        "action_type",
        "question",
        "question_status",
        "occurred_at",
        "oldid",
    }
}
_FIELDS = (
    "date",
    "nominator",
    "authors",
    "contributors",
    "nomination",
    "source",
    "question",
)
_MAX_ROWS = 200_000
_MAX_BYTES = 128 * 1024 * 1024
_MAX_CURSOR = 2048
_UNKNOWN = ["pending", "blocked", "unresolved"]
_ATTRIBUTION = [
    "nominator",
    "main_author",
    "nomination_link",
    "contributors",
    "authors",
    "question",
]


def identities(events: pl.DataFrame) -> pl.DataFrame:
    """Attach stable identities with distinct undated parameters.

    Args:
        events: Historical event rows with source parameter identities.

    Returns:
        Original rows with stable recognition event IDs.
    """
    events = events.with_columns(
        *(
            pl.lit(None, dtype=SCHEMA[name]).alias(name)
            for name in ("parameter", "grade", "action_type")
            if name not in events.columns
        ),
    )
    return events.with_columns(
        pl.concat_str(
            pl.col("page_id").cast(pl.String),
            "kind",
            pl.col("date").dt.to_string("%Y-%m-%d").fill_null("unknown"),
            pl
            .when(pl.col("kind") == "review")
            .then(pl.col("action_type"))
            .otherwise(pl.col("grade"))
            .fill_null("none"),
            "action",
            pl
            .when(pl.col("date").is_null())
            .then(pl.col("parameter").fill_null("unknown"))
            .otherwise(pl.lit("dated")),
            separator=":",
        ).alias("event_id"),
    )


def _coverage(frame: pl.DataFrame) -> pl.DataFrame:
    """Derive field and record states from evidence.

    Returns:
        Records with recalculated applicable-field coverage.
    """
    frame = frame.with_columns(
        pl.coalesce(
            "action_type",
            pl
            .when(pl.col("kind") == "quality")
            .then(
                pl
                .when(pl.col("action") == "removed")
                .then(
                    pl.col("grade").replace_strict(
                        {
                            "GA": "GAR",
                            "FA": "FAR",
                            "FL": "FLR",
                            "A": "WAR",
                            "AL": "WAR",
                            "Bplus": "BPAR",
                        },
                        default=None,
                    ),
                )
                .otherwise(
                    pl.col("grade").replace_strict(
                        {
                            "GA": "GAN",
                            "FA": "FAC",
                            "FL": "FLC",
                            "A": "WAR",
                            "AL": "WAR",
                            "Bplus": "BPAR",
                        },
                        default=None,
                    ),
                ),
            )
            .otherwise(
                pl.col("kind").replace_strict(
                    {"dyk": "DYK", "news": "ITN", "history": "OTD"},
                    default=None,
                ),
            ),
        ).alias("action_type"),
    )
    added = (pl.col("kind") == "quality") & (pl.col("action") == "added")
    dyk = pl.col("kind") == "dyk"
    nomination = dyk | (added & pl.col("grade").is_in(["FA", "FL", "GA"]))
    rules = {
        "date": (pl.lit(value=True), pl.col("date").is_not_null()),
        "source": (
            pl.lit(value=True),
            (pl.col("sources").list.len() > 0)
            | pl.col("assessment_observed_at").is_not_null(),
        ),
        "nominator": (nomination, pl.col("nominator").is_not_null()),
        "nomination": (nomination, pl.col("nomination_link").is_not_null()),
        "authors": (added, pl.col("authors").list.len() > 0),
        "contributors": (dyk, pl.col("contributors").list.len() > 0),
        "question": (dyk, pl.col("question").str.len_bytes() > 0),
        "main_author": (nomination, pl.col("main_author").is_not_null()),
    }
    frame = frame.with_columns(
        pl
        .when(~required)
        .then(pl.lit("not_applicable"))
        .when(known.fill_null(value=False))
        .then(pl.lit("known"))
        .otherwise(pl.col("lookup_state"))
        .alias(f"{field}_status")
        for field, (required, known) in rules.items()
    )
    return frame.with_columns(
        pl
        .when(
            pl.all_horizontal(
                pl.col(f"{field}_status").is_in(["known", "not_applicable"])
                for field in _FIELDS
            ),
        )
        .then(pl.lit("complete"))
        .otherwise(pl.col("lookup_state"))
        .alias("status"),
    )


def _validate(frame: pl.DataFrame, *, duplicates: bool = False) -> None:
    """Reject corrupt identities, unsafe credits, and false coverage.

    Raises:
        ValueError: A recognition record violates the durable schema.
    """
    valid_schema = frame.schema == pl.Schema(SCHEMA)
    if (
        not valid_schema
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES * 4
    ):
        message = "Invalid recognition schema or size."
        raise ValueError(message)
    if frame.is_empty():
        return
    bad_name = r"[\[\]{}|<>\x00-\x1f\x7f]"
    invalid = frame.filter(
        pl.any_horizontal(
            pl.col(
                "event_id",
                "page_id",
                "title",
                "kind",
                "action",
                "lookup_state",
                "updated_at",
            ).is_null(),
        )
        | (pl.col("page_id") <= 0)
        | ~pl.col("kind").is_in([
            "quality",
            "dyk",
            "news",
            "history",
            "review",
        ])
        | (
            (pl.col("kind") == "review")
            & (
                ~pl.col("action").is_in([
                    "nominated",
                    "passed",
                    "failed",
                    "pending",
                ])
                | pl.col("action_type").is_null()
            )
        )
        | (pl.col("question").str.len_bytes() > 16 * 1024).fill_null(
            value=False,
        )
        | pl
        .col("question")
        .str.contains(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
        .fill_null(value=False)
        | (
            (pl.col("kind") == "quality")
            & (
                ~pl
                .col("grade")
                .is_in(["FA", "FL", "GA", "A", "AL", "Bplus"])
                .fill_null(value=False)
                | ~pl.col("action").is_in(["added", "removed"])
            )
        )
        | (
            (pl.col("kind") == "dyk")
            & (
                (pl.col("action") != "appeared")
                | pl.col("grade").is_not_null()
            )
        )
        | ~pl.col("lookup_state").is_in(_UNKNOWN)
        | ~pl.col("title").str.len_bytes().is_between(1, 1000)
        | pl.col("title").str.contains(bad_name)
        | (
            pl.col("nominator").is_not_null()
            & (
                pl.col("nominator").str.contains(bad_name)
                | ~pl.col("nominator").str.len_bytes().is_between(1, 1000)
            )
        )
        | pl.any_horizontal(
            pl
            .col(field)
            .list.eval(
                pl.element().is_null()
                | pl.element().str.contains(bad_name)
                | ~pl.element().str.len_bytes().is_between(1, 1000),
            )
            .list.any()
            .fill_null(value=False)
            for field in ("authors", "contributors")
        )
        | (
            pl.col("reason").is_not_null()
            & ~pl.col("reason").str.contains(r"^[a-z_]{1,80}$")
        )
        | (
            pl.col("nomination_link").is_not_null()
            & (
                pl.col("nomination_link").str.contains(bad_name)
                | ~pl
                .col("nomination_link")
                .str.len_bytes()
                .is_between(1, 2000)
            )
        )
        | (pl.col("lookup_cursor").str.len_bytes() > _MAX_CURSOR).fill_null(
            value=False,
        ),
    )
    if (
        not invalid.is_empty()
        or (not duplicates and frame["event_id"].n_unique() != frame.height)
        or not _coverage(frame).equals(frame)
        or not identities(frame)["event_id"].equals(frame["event_id"])
    ):
        message = "Invalid recognition evidence or coverage."
        raise ValueError(message)
    aliases = (
        frame
        .select(
            "page_id",
            "kind",
            "grade",
            "action",
            "date",
            "aliases",
            "action_type",
        )
        .explode("aliases", empty_as_null=False)
        .drop_nulls("aliases")
    )
    prefix = (
        pl.concat_str(
            pl.col("page_id").cast(pl.String),
            "kind",
            pl.lit("unknown"),
            pl
            .when(pl.col("kind") == "review")
            .then(pl.col("action_type"))
            .otherwise(pl.col("grade"))
            .fill_null("none"),
            "action",
            separator=":",
        )
        + ":"
    )
    if aliases.filter(
        pl.col("date").is_null() | ~pl.col("aliases").str.starts_with(prefix),
    ).height:
        message = "Invalid recognition resolved-date alias."
        raise ValueError(message)
    sources = (
        frame
        .select("sources")
        .explode("sources", empty_as_null=False)
        .drop_nulls()
        .unnest("sources")
    )
    if not sources.filter(
        pl.any_horizontal(pl.all().is_null())
        | (pl.col("revision_id") <= 0)
        | ~pl.col("url").str.starts_with("https://zh.wikipedia.org/")
        | pl.col("url").str.contains(r"[\x00-\x20<>\[\]{}|]")
        | pl.col("title").str.contains(bad_name),
    ).is_empty():
        message = "Invalid recognition source provenance."
        raise ValueError(message)


def load(path: Path) -> pl.DataFrame:
    """Read the complete durable inventory without source requests.

    Args:
        path: Recognition Parquet file.

    Returns:
        Validated records, or a typed empty first-use frame.

    Raises:
        OSError: A path is unsafe or unreadable.
        ValueError: Saved records are corrupt or oversized.
    """
    if path.is_symlink() or path.parent.is_symlink():
        message = "Recognition data requires regular paths."
        raise OSError(message)
    if not path.exists():
        return pl.DataFrame(schema=SCHEMA)
    if not path.is_file() or path.stat().st_size > _MAX_BYTES:
        message = "Recognition data exceeds its file bound."
        raise ValueError(message)
    frame = pl.read_parquet(path, n_rows=_MAX_ROWS + 1)
    if frame.schema == pl.Schema(_LEGACY_SCHEMA):
        frame = _coverage(
            frame.with_columns(
                pl.lit(None, dtype=dtype).alias(name)
                for name, dtype in SCHEMA.items()
                if name not in frame.columns
            ).select(list(SCHEMA)),
        )
    _validate(frame)
    return frame


def merge_save(path: Path, refreshed: pl.DataFrame) -> pl.DataFrame:
    """Merge evidence under a lock without erasing known fields.

    Args:
        path: Durable recognition destination.
        refreshed: Complete schema rows from inventory or lookups.

    Returns:
        Complete retained inventory with recalculated coverage.

    Raises:
        ValueError: Evidence conflicts or exceeds storage limits.
    """
    _validate(refreshed, duplicates=True)
    load(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(f".{path.name}.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        all_rows = retain_active(
            pl.concat([load(path), refreshed]),
            path,
        ).sort(
            "updated_at",
            maintain_order=True,
        )
        aliases = (
            all_rows
            .select("event_id", "aliases")
            .explode("aliases", empty_as_null=False)
            .drop_nulls()
            .unique()
        )
        if aliases["aliases"].n_unique() != aliases.height:
            message = (
                "Recognition aliases have conflicting resolved identities."
            )
            raise ValueError(message)
        all_rows = (
            all_rows
            .join(
                aliases.rename({
                    "event_id": "_canonical",
                    "aliases": "event_id",
                }),
                on="event_id",
                how="left",
                maintain_order="left",
            )
            .with_columns(
                pl.coalesce("_canonical", "event_id").alias("event_id"),
            )
            .drop("_canonical")
        )
        merged = (
            all_rows
            .group_by("event_id", maintain_order=True)
            .agg(
                *(
                    pl.col(name).drop_nulls().last().alias(name)
                    for name in SCHEMA
                    if name
                    not in {
                        "event_id",
                        "sources",
                        "lookup_state",
                        "reason",
                        "contributors",
                        "aliases",
                    }
                ),
                pl
                .col("aliases")
                .explode(empty_as_null=False)
                .drop_nulls()
                .unique()
                .sort()
                .implode(),
                pl
                .when(pl.col("contributors").is_not_null().any())
                .then(
                    pl
                    .col("contributors")
                    .explode(empty_as_null=False)
                    .drop_nulls()
                    .unique()
                    .sort()
                    .implode(),
                )
                .otherwise(pl.lit(None, dtype=pl.List(pl.String)))
                .alias("contributors"),
                pl
                .col("sources")
                .explode(empty_as_null=False)
                .drop_nulls()
                .unique()
                .implode(),
                pl
                .col("lookup_state")
                .filter(pl.col("lookup_state") != "pending")
                .last()
                .fill_null("pending"),
                pl.col("reason").drop_nulls().last(),
            )
            .select(list(SCHEMA))
        )
        merged = _coverage(merged)
        _validate(merged)
        with NamedTemporaryFile(
            dir=staging_directory(path),
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        try:
            merged.write_parquet(temporary)
            if temporary.stat().st_size > _MAX_BYTES:
                message = "Recognition data exceeds its file bound."
                raise ValueError(message)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return merged


def prepare_events(
    events: pl.DataFrame,
    *,
    previous: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Prepare observed recognition events for one atomic durable merge.

    Args:
        events: All-time events with optional computed authors.
        previous: Validated inventory used to preserve lookup progress.

    Returns:
        Complete event rows with stable identities and honest coverage.
    """
    selected = identities(
        events.filter(
            pl.col("kind").is_in([
                "quality",
                "dyk",
                "news",
                "history",
                "review",
            ]),
        ),
    )
    previous = (
        previous if previous is not None else pl.DataFrame(schema=SCHEMA)
    ).select(
        "event_id",
        pl.col("revision_id").alias("_previous_revision"),
    )
    if "revision_id" in selected.columns:
        selected = (
            selected
            .join(previous, on="event_id", how="left")
            .with_columns(
                pl
                .when(pl.col("revision_id") > pl.col("_previous_revision"))
                .then(pl.lit("{}"))
                .otherwise(
                    pl.col("lookup_cursor")
                    if "lookup_cursor" in selected.columns
                    else pl.lit(None, dtype=pl.String),
                )
                .alias("lookup_cursor"),
            )
            .drop("_previous_revision")
        )
    selected = (
        selected
        .with_columns(
            *(
                pl.lit(None, dtype=dtype).alias(name)
                for name, dtype in SCHEMA.items()
                if name not in selected.columns
            ),
        )
        .select(list(SCHEMA))
        .cast(SCHEMA)
        .with_columns(
            pl.lit("pending").alias("lookup_state"),
            pl.col("aliases").fill_null(pl.lit([], dtype=pl.List(pl.String))),
            pl
            .lit(dt.datetime.now(dt.UTC).isoformat())
            .str.to_datetime(time_zone="UTC")
            .alias("updated_at"),
            pl
            .when(pl.col("sources").is_not_null())
            .then(pl.col("sources"))
            .when(pl.col("revision_id").is_not_null())
            .then(
                pl.concat_list(
                    pl.struct(
                        pl.concat_str(pl.lit("Talk:"), "title").alias("title"),
                        "revision_id",
                        pl.concat_str(
                            pl.lit(
                                "https://zh.wikipedia.org/w/index.php?oldid=",
                            ),
                            pl.col("revision_id").cast(pl.String),
                        ).alias("url"),
                    ),
                ),
            )
            .otherwise(pl.lit([], dtype=SCHEMA["sources"]))
            .alias("sources"),
        )
    )
    return _coverage(selected)


def sync_events(events: pl.DataFrame, path: Path) -> pl.DataFrame:
    """Retain supplied recognition events without date windows.

    Args:
        events: All-time events with optional computed authors.
        path: Durable recognition destination.

    Returns:
        Complete durable inventory, including earlier retained events.
    """
    return merge_save(path, prepare_events(events, previous=load(path)))


def sync_current_assessments(
    membership: pl.DataFrame,
    path: Path,
    *,
    observed_at: dt.datetime,
) -> pl.DataFrame:
    """Retain observed badges with unknown listing dates.

    Args:
        membership: Current project page IDs, full titles, and pa_class.
        path: Durable recognition destination.
        observed_at: Observation time, never a listing date.

    Returns:
        Complete inventory including unknown-date status observations.
    """
    saved = load(path)
    known = saved.filter(
        (pl.col("kind") == "quality") & (pl.col("action") == "added"),
    ).select("page_id", "grade")
    selected = (
        membership
        .select(
            "page_id",
            pl.col("full_title").alias("title"),
            pl
            .col("pa_class")
            .str.to_lowercase()
            .replace_strict(
                {
                    "fa": "FA",
                    "fl": "FL",
                    "ga": "GA",
                    "a": "A",
                    "al": "AL",
                    "bplus": "Bplus",
                    "b+": "Bplus",
                },
                default=None,
            )
            .alias("grade"),
        )
        .filter(pl.col("grade").is_not_null())
        .join(known, on=["page_id", "grade"], how="anti")
        .with_columns(
            pl.lit("quality").alias("kind"),
            pl.lit("added").alias("action"),
            pl.lit("assessment").alias("parameter"),
            pl.lit(None, dtype=pl.Date).alias("date"),
            pl
            .lit(observed_at.isoformat())
            .str.to_datetime(time_zone="UTC")
            .alias("assessment_observed_at"),
        )
    )
    return sync_events(selected, path)


def inventory_retained(history_path: Path) -> pl.DataFrame:
    """Flatten all retained revisions, including departed members.

    Args:
        history_path: Versioned durable talk-history archive.

    Returns:
        All events without issue or membership filtering.
    """
    return (
        read_versions(history_path)
        .filter(pl.col("events").list.len() > 0)
        .explode("events", empty_as_null=False)
        .unnest("events")
    )


def apply_lookup(record: dict, result: dict) -> pl.DataFrame:
    """Prepare one verified source-lookup result for durable merging.

    Args:
        record: Existing complete-schema inventory record.
        result: Explicit source fields and safe lookup outcome.

    Returns:
        One typed record with updated field coverage.
    """
    row = dict(record)
    # Date corrections require review; lookups only fill unknown dates.
    if record["date"] is not None:
        result = {
            name: value for name, value in result.items() if name != "date"
        }
    for name in (*_ATTRIBUTION, "date"):
        if result.get(name) is not None:
            row[name] = result[name]
    row["sources"] = record["sources"] + result.get("sources", [])
    row["lookup_state"] = (
        result.get("status") if result.get("status") in _UNKNOWN else "pending"
    )
    row["reason"] = result.get("reason")
    if "lookup_cursor" in result:
        row["lookup_cursor"] = result["lookup_cursor"] or ""
    row["updated_at"] = dt.datetime.now(dt.UTC)
    frame = identities(pl.DataFrame([row], schema=SCHEMA))
    if frame["event_id"].item() != record["event_id"]:
        frame = frame.with_columns(
            pl
            .concat_list("aliases", pl.lit([record["event_id"]]))
            .list.unique()
            .alias("aliases"),
        )
    return _coverage(frame)


def project(events: pl.DataFrame, records: pl.DataFrame) -> pl.DataFrame:
    """Attach durable credits without filtering selected events.

    Args:
        events: Source events with caller-selected scope.
        records: Complete recognition inventory.

    Returns:
        Original events enriched with retained attribution.
    """
    alias_rows = (
        records
        .select(
            pl.col("aliases").alias("event_id"),
            pl.col("date").alias("_resolved_date"),
            pl.col("title").alias("_canonical_title"),
            *_ATTRIBUTION,
        )
        .explode("event_id", empty_as_null=False)
        .drop_nulls("event_id")
    )
    lookup = pl.concat([
        records.select(
            "event_id",
            pl.col("date").alias("_resolved_date"),
            pl.col("title").alias("_canonical_title"),
            *_ATTRIBUTION,
        ),
        alias_rows,
    ]).unique(subset="event_id", keep="first")
    joined = identities(events).join(
        lookup,
        on="event_id",
        how="left",
        suffix="_retained",
        maintain_order="left",
    )
    for name in _ATTRIBUTION:
        if name in events.columns:
            value = pl.coalesce(f"{name}_retained", name)
            if name == "authors":
                value = (
                    pl
                    .when(pl.col(name).list.len() > 0)
                    .then(pl.col(name))
                    .otherwise(pl.col(f"{name}_retained"))
                )
            joined = joined.with_columns(value.alias(name)).drop(
                f"{name}_retained",
            )
    return joined.with_columns(
        pl.coalesce("_resolved_date", "date").alias("date"),
        pl.coalesce("_canonical_title", "title").alias("title"),
    ).drop("event_id", "_resolved_date", "_canonical_title")


def coverage(records: pl.DataFrame) -> dict[str, int]:
    """Count complete records and explicit missing-field coverage.

    Args:
        records: Validated complete project inventory.

    Returns:
        Actual all-time counts, never substituting retry exhaustion.
    """
    result = records.select(
        pl.len().alias("total"),
        *(
            (pl.col("status") == state).sum().alias(label)
            for state, label in (
                ("complete", "completed"),
                ("pending", "pending"),
                ("blocked", "blocked"),
                ("unresolved", "unresolved"),
            )
        ),
        *(
            pl.col(f"{field}_status").is_in(_UNKNOWN).sum().alias(label)
            for field, label in (
                ("date", "missing_dates"),
                ("authors", "missing_authors"),
                ("nominator", "missing_nominators"),
                ("contributors", "missing_contributors"),
                ("nomination", "missing_nomination_links"),
                ("question", "missing_questions"),
            )
        ),
    ).row(0, named=True)
    return {key: int(value) for key, value in result.items()}


def read_events(
    path: Path,
    membership: pl.DataFrame | None = None,
    *,
    required: bool = False,
) -> pl.DataFrame:
    """Read retained event rows without source requests.

    Args:
        path: Durable milestones file.
        membership: Optional current project pages with full titles.
        required: Reject a missing first-use dataset for consumers.

    Returns:
        Saved events with optional current membership scope and titles.

    Raises:
        FileNotFoundError: A required dataset has not been acquired.
    """
    if required and not path.exists():
        raise FileNotFoundError(path)
    events = load(path)
    if membership is not None:
        titles = membership.select(
            "page_id",
            pl.col("full_title").alias("_current_title"),
        ).unique("page_id")
        events = (
            events
            .join(titles, on="page_id", how="inner")
            .with_columns(
                pl.col("_current_title").alias("title"),
            )
            .drop("_current_title")
        )
    return events
