"""Retain proved corrections beside unchanged WikiWho observations.

Proofs bind an exact raw observation and identify nonoverlapping
character
segments in its scored token IDs. Derived byte overlays preserve the raw
token counts and denominator. Only acquisition code establishes the
source
proof; saved-data readers never fetch or infer a correction themselves.
"""

# Filesystem failures propagate from bounded shared readers.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from data.articles.authorship_store import CONTRIBUTOR_SCHEMA
from data.articles.storage import DEFAULT_ROOT
from shared.support.files import open_regular, staging_directory
from shared.support.parquet import parquet_metadata, validate_parquet_metadata

DEFAULT_PATH = DEFAULT_ROOT / "authorship-corrections.parquet"
PROOF_CONTRACT = "wikiwho-zh:reference-cleanup-single-insertion-v1"
OBSERVATION_SCHEMA = {
    "page_id": pl.Int64,
    "title": pl.String,
    "revision_id": pl.Int64,
    "raw_contract": pl.String,
    "raw_fetched_at": pl.Datetime("us", "UTC"),
    "eligible_bytes": pl.UInt32,
    "contributors": pl.List(pl.Struct(CONTRIBUTOR_SCHEMA)),
}
KEY = [
    "page_id",
    "revision_id",
    "raw_contract",
    "raw_fetched_at",
    "eligible_bytes",
]
PROOF_SCHEMA = {
    "proof_id": pl.String,
    "proof_contract": pl.String,
    "origin_revision": pl.Int64,
    "parent_revision": pl.Int64,
    "before_source_sha256": pl.String,
    "after_source_sha256": pl.String,
    "before_tokens_sha256": pl.String,
    "after_tokens_sha256": pl.String,
    "before_characters": pl.UInt32,
    "after_characters": pl.UInt32,
    "matched_characters": pl.UInt32,
    "added_characters": pl.UInt32,
    "removed_reference_bytes": pl.UInt32,
}
TRANSFER_SCHEMA = {
    "proof_id": pl.String,
    "token_id": pl.String,
    "token_start": pl.UInt32,
    "token_stop": pl.UInt32,
    "raw_editor": pl.String,
    "editor": pl.String,
    "name": pl.String,
    "origin": pl.Int64,
    "bytes": pl.UInt32,
    "characters": pl.UInt32,
}
SCHEMA = {
    "page_id": pl.Int64,
    "revision_id": pl.Int64,
    "raw_contract": pl.String,
    "raw_fetched_at": pl.Datetime("us", "UTC"),
    "eligible_bytes": pl.UInt32,
    "observed_at": pl.Datetime("us", "UTC"),
    "status": pl.String,
    "checked_origins": pl.List(pl.Int64),
    "proofs": pl.List(pl.Struct(PROOF_SCHEMA)),
    "transfers": pl.List(pl.Struct(TRANSFER_SCHEMA)),
}
_CONTRACT = "article-authorship-corrections:1"
_MAX_ROWS = 100_000
_MAX_BYTES = 128 * 1024 * 1024
_MAX_PROOFS = 100_000
_MAX_SEGMENTS = 1_000_000
_HASH = r"^[0-9a-f]{64}$"
_EDITOR = r"^([1-9][0-9]*|0\|[^\x00-\x20]+)$"
_METADATA = (
    "correction_status",
    "correction_observed_at",
    "correction_proof_count",
    "correction_bytes",
    "correction_checked_origins",
)


def proof_digest(proof: dict) -> str:
    """Identify the exact source and token facts of a provenance proof.

    Args:
        proof: Proof fields, optionally including an ignored proof_id.

    Returns:
        SHA-256 of canonical JSON for the declared proof fields.
    """
    fields = {name: proof[name] for name in PROOF_SCHEMA if name != "proof_id"}
    return hashlib.sha256(
        json.dumps(
            fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode(),
    ).hexdigest()


def _flatten(frame: pl.DataFrame, name: str) -> pl.DataFrame:
    """Expand a nested ledger while retaining its exact raw binding.

    Returns:
        Nonempty nested records with their parent observation identity.
    """
    return (
        frame
        .select(*KEY, name)
        .explode(name, empty_as_null=False)
        .unnest(name)
    )


def _validate(frame: pl.DataFrame) -> None:
    """Reject unsafe identities, invalid proofs and overlapping spans.

    Raises:
        ValueError: Saved correction facts violate the bounded contract.
    """
    if (
        frame.schema != pl.Schema(SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES * 4
        or frame.select(KEY).n_unique() != frame.height
        or frame.filter(
            pl.any_horizontal(pl.all().is_null())
            | (pl.col("page_id") <= 0)
            | (pl.col("revision_id") <= 0)
            | (pl.col("eligible_bytes") <= 0)
            | ~pl.col("raw_contract").str.len_bytes().is_between(1, 256)
            | pl.col("raw_contract").str.contains(r"[\x00-\x20\x7f]")
            | (pl.col("observed_at") < pl.col("raw_fetched_at"))
            | ~pl.col("status").is_in(["partial", "complete", "unavailable"])
            | pl
            .col("checked_origins")
            .list.eval(
                pl.element().is_null() | (pl.element() <= 0),
            )
            .list.any()
            | pl.col("checked_origins").ne_missing(
                pl.col("checked_origins").list.unique().list.sort(),
            ),
        ).height
    ):
        message = "Invalid authorship correction identity or schema."
        raise ValueError(message)
    proofs, transfers = _flatten(frame, "proofs"), _flatten(frame, "transfers")
    if proofs.height > _MAX_PROOFS or transfers.height > _MAX_SEGMENTS:
        message = "Authorship corrections exceed their ledger bounds."
        raise ValueError(message)
    invalid_proofs = proofs.filter(
        pl.any_horizontal(pl.exclude(KEY).is_null())
        | ~pl.all_horizontal(
            pl.col(
                "proof_id",
                "before_source_sha256",
                "after_source_sha256",
                "before_tokens_sha256",
                "after_tokens_sha256",
            ).str.contains(_HASH),
        )
        | (pl.col("origin_revision") <= 0)
        | (pl.col("parent_revision") <= 0)
        | (pl.col("origin_revision") == pl.col("parent_revision"))
        | (pl.col("proof_contract") != PROOF_CONTRACT)
        | (pl.col("matched_characters") != pl.col("before_characters"))
        | (
            pl.col("matched_characters").cast(pl.UInt64)
            + pl.col("added_characters").cast(pl.UInt64)
            != pl.col("after_characters")
        )
        | pl.struct(*KEY, "proof_id").is_duplicated(),
    )
    checked = frame.select(*KEY, "checked_origins").explode(
        "checked_origins",
        empty_as_null=False,
    )
    unchecked = proofs.join(
        checked,
        left_on=[*KEY, "origin_revision"],
        right_on=[*KEY, "checked_origins"],
        how="anti",
    )
    invalid_transfers = transfers.filter(
        pl.any_horizontal(pl.exclude(*KEY, "name").is_null())
        | ~pl.col("proof_id").str.contains(_HASH)
        | ~pl.col("token_id").str.len_bytes().is_between(1, 200)
        | pl.col("token_id").str.contains(r"[\x00-\x20\x7f]")
        | ~pl.col("raw_editor").str.contains(_EDITOR)
        | ~pl.col("editor").str.contains(_EDITOR)
        | (pl.col("raw_editor") == pl.col("editor"))
        | (pl.col("origin") <= 0)
        | (pl.col("token_stop") <= pl.col("token_start"))
        | (
            pl.col("characters")
            != pl.col("token_stop") - pl.col("token_start")
        )
        | (pl.col("bytes") < pl.col("characters"))
        | (
            pl.col("bytes").cast(pl.UInt64)
            > pl.col("characters").cast(pl.UInt64) * 4
        )
        | (
            pl.col("name").is_not_null()
            & (
                ~pl.col("name").str.len_bytes().is_between(1, 1000)
                | pl.col("name").str.strip_chars().eq("")
                | pl.col("name").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]")
            )
        ),
    )
    orphaned = transfers.join(
        proofs.select(*KEY, "proof_id"),
        on=[*KEY, "proof_id"],
        how="anti",
    )
    overlap = (
        transfers
        .sort([*KEY, "token_id", "token_start", "token_stop"])
        .with_columns(
            pl
            .col("token_stop")
            .cum_max()
            .shift(1)
            .over([*KEY, "token_id"])
            .alias("_previous_stop"),
        )
        .filter(pl.col("token_start") < pl.col("_previous_stop"))
    )
    if any(
        not result.is_empty()
        for result in (
            invalid_proofs,
            unchecked,
            invalid_transfers,
            orphaned,
            overlap,
        )
    ):
        message = "Invalid or overlapping authorship correction proof."
        raise ValueError(message)
    # Cryptographic record identity is a scalar serialization boundary;
    # joins, validation and byte aggregation remain native above.
    if any(
        row["proof_id"] != proof_digest(row)
        for row in proofs.select(list(PROOF_SCHEMA)).iter_rows(named=True)
    ):
        message = "Authorship proof digest does not match its source facts."
        raise ValueError(message)


def load(path: Path = DEFAULT_PATH) -> pl.DataFrame:
    """Read bounded durable proofs without contacting a source service.

    Args:
        path: Durable correction ledger outside disposable caches.

    Returns:
        Validated proof records or a typed empty first-use frame.

    Raises:
        OSError: Storage is unsafe or unavailable.
        ValueError: Existing saved evidence is corrupt or oversized.
    """
    try:
        with open_regular(path, _MAX_BYTES) as stream:
            metadata = pl.read_parquet_metadata(stream)
            stream.seek(0)
            frame = pl.read_parquet(stream, n_rows=_MAX_ROWS + 1)
    except FileNotFoundError:
        return pl.DataFrame(schema=SCHEMA)
    except pl.exceptions.PolarsError as error:
        message = "Authorship corrections are not readable Parquet."
        raise ValueError(message) from error
    validate_parquet_metadata(
        metadata,
        frame,
        contract=_CONTRACT,
        allow_legacy=False,
    )
    _validate(frame)
    return frame


def _merge(previous: pl.DataFrame, refreshed: pl.DataFrame) -> pl.DataFrame:
    """Union incremental proof facts without replacing earlier evidence.

    Returns:
        Complete raw bindings with monotonic proof coverage.
    """
    combined = pl.concat([previous, refreshed])
    base = combined.group_by(KEY, maintain_order=True).agg(
        pl.col("observed_at").max(),
        pl
        .when((pl.col("status") == "complete").any())
        .then(pl.lit("complete"))
        .when((pl.col("status") == "partial").any())
        .then(pl.lit("partial"))
        .otherwise(pl.lit("unavailable"))
        .alias("status"),
        pl
        .col("checked_origins")
        .list.explode(
            keep_nulls=False,
            empty_as_null=False,
        )
        .unique()
        .sort(),
    )
    for name, schema in (
        ("proofs", PROOF_SCHEMA),
        ("transfers", TRANSFER_SCHEMA),
    ):
        records = _flatten(combined, name).unique(maintain_order=True)
        nested = records.group_by(KEY, maintain_order=True).agg(
            pl.struct(list(schema)).alias(name),
        )
        base = base.join(
            nested,
            on=KEY,
            how="left",
            validate="1:1",
        ).with_columns(
            pl.col(name).fill_null(pl.lit([], dtype=SCHEMA[name])),
        )
    result = base.select(list(SCHEMA)).cast(SCHEMA)
    _validate(result)
    return result


def merge_save(path: Path, refreshed: pl.DataFrame) -> pl.DataFrame:
    """Atomically retain every proof and its exact raw binding.

    Args:
        path: Durable correction ledger outside disposable caches.
        refreshed: Validated partial, complete or unavailable
            observations.

    Returns:
        Complete merged ledger. Empty input reads without creating
        files.

    Raises:
        OSError: Storage is unsafe or unavailable.
        ValueError: Proofs conflict or exceed the storage bounds.
    """
    _validate(refreshed)
    if refreshed.is_empty():
        return load(path)
    if path.is_symlink() or path.parent.is_symlink():
        message = "Authorship corrections require regular storage paths."
        raise OSError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(f".{path.name}.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        merged = _merge(load(path), refreshed)
        with NamedTemporaryFile(
            dir=staging_directory(path),
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        try:
            merged.write_parquet(
                temporary,
                metadata=parquet_metadata(merged, contract=_CONTRACT),
            )
            if temporary.stat().st_size > _MAX_BYTES:
                message = "Authorship corrections exceed their file bound."
                raise ValueError(message)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            temporary.replace(path)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)
        return merged


def _annotations(bindings: pl.DataFrame) -> pl.DataFrame:
    """Describe proof coverage independently from credited authors.

    Returns:
        Per-input-row status, proof counts and proved transferred bytes.
    """
    return bindings.select(
        "_correction_row",
        pl.col("status").fill_null("pending").alias("correction_status"),
        pl.col("observed_at").alias("correction_observed_at"),
        pl
        .col("proofs")
        .list.len()
        .fill_null(0)
        .alias("correction_proof_count"),
        pl
        .col("transfers")
        .list.eval(
            pl.element().struct.field("bytes").cast(pl.UInt64),
        )
        .list.sum()
        .fill_null(0)
        .alias("correction_bytes"),
        pl
        .col("checked_origins")
        .list.len()
        .fill_null(0)
        .alias("correction_checked_origins"),
    )


def _overlay_contributors(changes: pl.DataFrame) -> pl.DataFrame:
    """Conserve byte totals while retaining original raw token counts.

    Returns:
        Corrected contributor lists and nullable tied principal authors.

    Raises:
        ValueError: A proof overdraws raw ownership or names conflict.
    """
    raw = (
        changes
        .select(
            "_correction_row",
            "eligible_bytes",
            "contributors",
        )
        .explode("contributors")
        .unnest("contributors")
    )
    transfers = (
        changes
        .select("_correction_row", "transfers")
        .explode(
            "transfers",
            empty_as_null=False,
        )
        .unnest("transfers")
    )
    outgoing = (
        transfers
        .group_by("_correction_row", "raw_editor")
        .agg(
            pl.col("bytes").cast(pl.Int64).sum().alias("_outgoing"),
        )
        .rename({"raw_editor": "editor"})
    )
    incoming = transfers.group_by("_correction_row", "editor").agg(
        pl.col("bytes").cast(pl.Int64).sum().alias("_incoming"),
        pl.col("name").drop_nulls().first().alias("_incoming_name"),
        pl.col("name").drop_nulls().n_unique().alias("_name_count"),
    )
    overdrawn = outgoing.join(
        raw.select("_correction_row", "editor", "bytes"),
        on=["_correction_row", "editor"],
        how="left",
        validate="1:1",
    ).filter(
        pl.col("bytes").is_null() | (pl.col("_outgoing") > pl.col("bytes")),
    )
    if (
        raw.select("_correction_row", "editor").n_unique() != raw.height
        or incoming.filter(pl.col("_name_count") > 1).height
        or not overdrawn.is_empty()
    ):
        message = "Correction transfers exceed exact raw contributor bytes."
        raise ValueError(message)
    editors = pl.concat([
        raw.select("_correction_row", "editor"),
        incoming.select("_correction_row", "editor"),
    ]).unique()
    corrected = (
        editors
        .join(
            raw.drop("eligible_bytes").rename({"bytes": "_raw_bytes"}),
            on=["_correction_row", "editor"],
            how="left",
            validate="1:1",
        )
        .join(
            incoming,
            on=["_correction_row", "editor"],
            how="left",
            validate="1:1",
        )
        .join(
            outgoing,
            on=["_correction_row", "editor"],
            how="left",
            validate="1:1",
        )
        .join(
            changes.select("_correction_row", "eligible_bytes"),
            on="_correction_row",
            how="left",
            validate="m:1",
        )
        .with_columns(
            (
                pl.col("_raw_bytes").cast(pl.Int64).fill_null(0)
                - pl.col("_outgoing").fill_null(0)
                + pl.col("_incoming").fill_null(0)
            ).alias("bytes"),
            pl.coalesce("name", "_incoming_name").alias("name"),
            pl.col("tokens").fill_null(0),
        )
    )
    original_totals = raw.group_by("_correction_row").agg(
        pl.col("bytes").cast(pl.Int64).sum().alias("_original_total"),
    )
    invalid = corrected.join(original_totals, on="_correction_row").filter(
        ~pl.col("bytes").is_between(0, 2**32 - 1)
        | (
            pl.col("bytes").sum().over("_correction_row")
            != pl.col("_original_total")
        )
        | (pl.col("_original_total") > pl.col("eligible_bytes"))
        | (pl.col("_original_total") <= 0),
    )
    if not invalid.is_empty():
        message = "Correction overlay does not conserve eligible raw bytes."
        raise ValueError(message)
    corrected = corrected.with_columns(
        pl.col("bytes").cast(pl.UInt32),
        (pl.col("bytes") / pl.col("eligible_bytes")).alias("share"),
    ).sort("_correction_row", "editor")
    principals = (
        corrected
        .filter(
            pl.col("bytes") == pl.col("bytes").max().over("_correction_row"),
        )
        .group_by("_correction_row")
        .agg(
            pl.col("name").unique().sort().alias("_names"),
            pl.col("name").is_null().any().alias("_unknown"),
        )
        .with_columns(
            pl
            .when(pl.col("_unknown"))
            .then(pl.lit(None, dtype=pl.List(pl.String)))
            .otherwise(pl.col("_names"))
            .alias("_corrected_authors"),
        )
        .select("_correction_row", "_corrected_authors")
    )
    return (
        corrected
        .group_by("_correction_row")
        .agg(
            pl.struct(list(CONTRIBUTOR_SCHEMA)).alias(
                "_corrected_contributors",
            ),
        )
        .join(principals, on="_correction_row", validate="1:1")
        .with_columns(
            pl.lit(value=True).alias("_corrected"),
        )
    )


def apply(
    evidence: pl.DataFrame,
    corrections: pl.DataFrame,
    *,
    revision_column: str = "listing_revision",
) -> pl.DataFrame:
    """Apply proved byte transfers to a separate derived evidence view.

    Args:
        evidence: Validated raw historical or current byte observations.
        corrections: Validated durable proof records.
        revision_column: Exact scored revision field in the raw
            evidence.

    Returns:
        Original rows and columns with derived contributor bytes, shares
        and principal authors, plus correction coverage metadata. Raw
        token counts remain unchanged; introduced editors have zero raw
        tokens. Missing proofs are pending, never implicitly complete.
        Never save this derived view into a raw authorship store.

    Raises:
        ValueError: Evidence is already derived or a proof overdraws
            bytes.
    """
    _validate(corrections)
    if "_correction_row" in evidence.columns or set(_METADATA) & set(
        evidence.columns,
    ):
        message = "Correction overlays require original raw evidence."
        raise ValueError(message)
    original_columns = evidence.columns
    base = evidence.with_row_index("_correction_row")
    bindings = base.select(
        "_correction_row",
        "page_id",
        "eligible_bytes",
        pl.col(revision_column).alias("revision_id"),
        pl.col("contract").alias("raw_contract"),
        pl.col("fetched_at").alias("raw_fetched_at"),
    ).join(corrections, on=KEY, how="left", validate="m:1")
    annotated = base.join(
        _annotations(bindings),
        on="_correction_row",
        validate="1:1",
    )
    changes = (
        bindings
        .filter(pl.col("transfers").list.len() > 0)
        .select(
            "_correction_row",
            "eligible_bytes",
            "transfers",
        )
        .join(
            base.select("_correction_row", "contributors"),
            on="_correction_row",
            validate="1:1",
        )
    )
    if changes.is_empty():
        return annotated.sort("_correction_row").select(
            *original_columns,
            *_METADATA,
        )
    result = annotated.join(
        _overlay_contributors(changes),
        on="_correction_row",
        how="left",
        validate="1:1",
    )
    replacements = [
        pl.coalesce("_corrected_contributors", "contributors").alias(
            "contributors",
        ),
    ]
    if "authors" in original_columns:
        replacements.append(
            pl
            .when(pl.col("_corrected").fill_null(value=False))
            .then(pl.col("_corrected_authors"))
            .otherwise(pl.col("authors"))
            .alias("authors"),
        )
    return (
        result
        .with_columns(*replacements)
        .sort("_correction_row")
        .select(*original_columns, *_METADATA)
    )
