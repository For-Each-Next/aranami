"""Accept supplied milestone metadata into canonical stores."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import os
from typing import TYPE_CHECKING

import polars as pl

from data.articles import milestone_store as store
from data.articles.storage import MILESTONES_PATH
from shared.support.files import atomic_json

if TYPE_CHECKING:
    from pathlib import Path

_RECEIPT_LIMIT = 1024 * 1024

_FACT_KEYS = ("page_id", "date", "kind", "grade", "action")


def temporary_register_path(path: Path = MILESTONES_PATH) -> Path:
    """Locate temporary metadata beside its formal runtime data.

    Args:
        path: Formal milestone destination, including preview paths.

    Returns:
        A legacy path inside the same runtime or test directory.
    """
    root = (
        path.parent.parent if path.parent.name == "articles" else path.parent
    )
    return root / "legacy/quality-register.parquet"


def _sync_directory(path: Path) -> None:
    """Flush directory changes before retiring accepted source data."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def import_milestone_metadata(
    temporary: Path,
    path: Path = MILESTONES_PATH,
) -> pl.DataFrame:
    """Merge validated temporary facts and retire accepted staging.

    Args:
        temporary: Provider metadata using the canonical schema.
        path: Normal milestone inventory receiving those observations.

    Returns:
        Verified formal inventory. Missing staging is inert.

    Raises:
        ValueError: Staging aliases its destination or readback differs.
        OSError: Persistence, receipt or retirement fails.
    """  # ruff: ignore[docstring-extraneous-exception]
    if temporary.absolute() == path.absolute():
        message = "Legacy metadata cannot replace its own staging path."
        raise ValueError(message)
    if not temporary.exists() and not temporary.is_symlink():
        return store.load(path)
    # Loading first rejects unsafe files before opening the existing
    # provider lock. This is the same lock used by its normal writer.
    store.load(temporary)
    descriptor = os.open(
        temporary.with_name(f".{temporary.name}.lock"),
        os.O_RDWR | os.O_NOFOLLOW,
    )
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not temporary.exists():
            return store.load(path)
        staged = store.load(temporary)
        with temporary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        merged = store.merge_save(path, staged)
        accepted = store.load(path)
        if not accepted.equals(merged):
            message = "Milestone import readback differs; staging retained."
            raise ValueError(message)
        _sync_directory(path.parent)
        atomic_json(
            temporary.with_name("quality-register-import.json"),
            {
                "schema": 1,
                "source_sha256": digest,
                "rows": staged.height,
                "destination": str(path.absolute()),
                "facts": staged
                .select(
                    *_FACT_KEYS,
                    "sources",
                )
                .with_columns(pl.col("date").dt.to_string())
                .to_dicts(),
                "accepted_at": dt.datetime.now(dt.UTC).isoformat(),
                "metadata_contract": "article-milestones",
            },
            max_bytes=_RECEIPT_LIMIT,
        )
        temporary.unlink()
        _sync_directory(temporary.parent)
        return accepted
