"""Locate shared article milestones and migrate retained evidence."""

# Lazy imports keep path constants independent of store initialization.
# ruff: file-ignore[import-outside-top-level]

import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from shared.support.files import read_json_object

if TYPE_CHECKING:
    from collections.abc import Callable

DATA_VERSION = 1
DEFAULT_ROOT = Path("reports/data/articles")
HISTORY_PATH = DEFAULT_ROOT / "history.parquet"
AUTHORSHIP_PATH = DEFAULT_ROOT / "authorship.parquet"
MILESTONES_PATH = DEFAULT_ROOT / "milestones.parquet"


def _migrate(
    source: Path,
    target: Path,
    operation: Callable[[], None],
    validate: Callable[[], object],
) -> None:
    """Checkpoint migration without editing legacy bytes."""
    if not source.exists() and not source.is_symlink():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    reports = next(
        (parent for parent in target.parents if parent.name == "reports"),
        None,
    )
    runtime = reports.parent if reports is not None else target.parent.parent
    checkpoints = runtime / "state/report-layout"
    checkpoints.mkdir(parents=True, exist_ok=True)
    marker = checkpoints / "articles.json"
    descriptor = os.open(
        checkpoints / "articles.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "r+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        receipts = (
            read_json_object(marker, 1024 * 1024)
            if marker.exists() or marker.is_symlink()
            else {}
        )
        identity = f"{source.resolve()} -> {target.resolve()}"
        if receipts.get(identity) == "complete":
            validate()
            return
        operation()
        validate()
        receipts[identity] = "complete"
        with NamedTemporaryFile(dir=checkpoints, delete=False) as temporary:
            path = Path(temporary.name)
            try:
                temporary.write(
                    json.dumps(receipts, ensure_ascii=False).encode(),
                )
                temporary.flush()
                os.fsync(temporary.fileno())
                path.replace(marker)
                directory = os.open(checkpoints, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                path.unlink(missing_ok=True)


def migrate_history(
    source: Path = Path("reports/newsletter-data-v2/history.parquet"),
    target: Path = HISTORY_PATH,
) -> None:
    """Merge all retained talk revisions while preserving the source."""
    from backend.support.article_history import merge_save, read_versions

    if not source.exists() and not source.is_symlink():
        return
    observations = read_versions(source)
    _migrate(
        source,
        target,
        lambda: merge_save(target, observations),
        lambda: read_versions(target),
    )


def migrate_authorship(source: Path, target: Path = AUTHORSHIP_PATH) -> None:
    """Merge historical authorship while preserving the source file."""
    from backend.support.article_authorship import load, save

    if not source.exists() and not source.is_symlink():
        return
    observations = load(source)
    _migrate(
        source,
        target,
        lambda: save(target, observations),
        lambda: load(target),
    )


def migrate_milestones(source: Path, target: Path = MILESTONES_PATH) -> None:
    """Merge legacy recognition once and preserve original bytes."""
    from backend.support.article_milestones import load, merge_save

    if not source.exists() and not source.is_symlink():
        return
    observations = load(source)
    _migrate(
        source,
        target,
        lambda: merge_save(target, observations),
        lambda: load(target),
    )


def migrate_legacy(runtime: Path = Path()) -> None:
    """Adopt all legacy project evidence in an explicit migration stage.

    Args:
        runtime: Toolforge runtime root containing retained reports.
    """
    from backend.support.article_scope import load as load_scope

    old_root = runtime / "reports/article-milestones-v1"
    new_root = runtime / DEFAULT_ROOT
    old_scope = old_root / "scope.parquet"
    if old_scope.exists() or old_scope.is_symlink():
        # Select tombstones before merging any legacy evidence.
        scope = load_scope(old_root)

        def retain_scope() -> None:
            """Copy validated tombstones before importing evidence."""
            from backend.support.article_scope import _write

            if not (new_root / "scope.parquet").exists():
                _write(new_root, scope)

        _migrate(
            old_scope,
            new_root / "scope.parquet",
            retain_scope,
            lambda: load_scope(new_root),
        )
    migrate_history(old_root / "history.parquet", runtime / HISTORY_PATH)
    migrate_authorship(
        old_root / "authorship.parquet",
        runtime / AUTHORSHIP_PATH,
    )
    migrate_milestones(
        old_root / "milestones.parquet",
        runtime / MILESTONES_PATH,
    )
    migrate_history(
        runtime / "reports/newsletter-history-v1.parquet",
        runtime / HISTORY_PATH,
    )
    migrate_history(
        runtime / "reports/newsletter-data-v2/history.parquet",
        runtime / HISTORY_PATH,
    )
    migrate_authorship(
        runtime / "reports/newsletter-data-v2/authorship.parquet",
        runtime / AUTHORSHIP_PATH,
    )
    migrate_milestones(
        runtime / "reports/project-recognition-v1/recognition.parquet",
        runtime / MILESTONES_PATH,
    )
