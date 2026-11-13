"""Reuse validated template aliases without downloading shell content.

``read_aliases`` reads a bounded local Parquet observation for up to
twenty-four hours, then refreshes canonical and redirect metadata from
Wiki Replicas. Template and module bodies are not needed by this lookup.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import polars as pl
from opencc import OpenCC
from pywikibot import Page
from pywikibot.exceptions import InvalidTitleError
from pywikibot.site import Namespace

from shared.sources.quarry.templates import (
    MAX_ALIASES,
    template_alias_metadata,
)
from shared.support.activity import report_activity
from shared.support.cache import ParquetCache, latest_cache_path

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_MAX_TITLE = 1000
_MAX_ENCODED_FILENAME = 220
_FILENAME_PREFIX = 150
_TTL = dt.timedelta(hours=24)
_DEFAULT_DIRECTORY = Path("cache/template-aliases")
_DIRECTORY: ContextVar[Path | None] = ContextVar(
    "template_alias_cache_directory",
    default=_DEFAULT_DIRECTORY,
)
_SCHEMA = {
    "wiki": pl.String,
    "requested_title": pl.String,
    "canonical_title": pl.String,
    "canonical_revision": pl.Int64,
    "aliases": pl.List(pl.String),
    "started_at": pl.Datetime("us", "UTC"),
    "checked_at": pl.Datetime("us", "UTC"),
}
_CACHE = ParquetCache(
    schema=_SCHEMA,
    contract="template-alias-metadata",
    max_rows=1,
    max_bytes=8 * 1024 * 1024,
    observe=False,
)


@contextmanager
def template_alias_cache(directory: Path | None) -> Iterator[None]:
    """Choose an isolated cache directory for one caller context.

    Args:
        directory: Production or preview cache root, or none to disable
            persistent cache reads and writes for this context.

    Yields:
        Control without creating directories or querying a source.
    """
    token = _DIRECTORY.set(directory)
    try:
        yield
    finally:
        _DIRECTORY.reset(token)


def _utc_now() -> dt.datetime:
    """Read actual UTC time independently of report anchors.

    Returns:
        Current aware instant used for metadata freshness.
    """
    return dt.datetime.now(dt.UTC)


def _template_title(site: BaseSite, title: str) -> str:
    """Normalize a local template without resolving foreign prefixes.

    Returns:
        Full normalized title on the existing local site.

    Raises:
        ValueError: The identity is external, non-template or invalid.
    """
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > _MAX_TITLE
    ):
        message = "Template aliases require a bounded template title."
        raise ValueError(message)
    prefix, separator, _ = title.strip().partition(":")
    if separator and site.namespaces.lookup_name(prefix) != Namespace.TEMPLATE:
        message = "Template aliases require a local template namespace."
        raise ValueError(message)
    try:
        page = Page(site, title, Namespace.TEMPLATE)
        page.title()
    except InvalidTitleError as error:
        message = "Template aliases require a valid local title."
        raise ValueError(message) from error
    if (
        page.site != site
        or page.namespace() != Namespace.TEMPLATE
        or page.section() is not None
    ):
        message = "Template aliases require a local template identity."
        raise ValueError(message)
    return page.title()


@cache
def _converter(config: str) -> OpenCC:
    """Reuse a configured Chinese script converter.

    Returns:
        An inert local converter with no source requests.
    """
    return OpenCC(config)


def _variants(site: BaseSite, aliases: tuple[str, ...]) -> tuple[str, ...]:
    """Preserve canonical-first ordering and local script variants.

    Returns:
        Unique titles accepted by existing caller alias digests.
    """
    if site.lang != "zh":
        return aliases
    return tuple(
        dict.fromkeys(
            variant
            for title in aliases
            for variant in (
                title,
                _converter("t2s").convert(title),
                _converter("s2t").convert(title),
            )
        ),
    )


def _cache_path(site: BaseSite, requested: str) -> Path | None:
    """Choose an isolated template directory beneath the caller's cache.

    Returns:
        A template-specific path, or none when persistence is disabled.

    Raises:
        ValueError: A wiki identity cannot safely identify a directory.
    """
    directory = _DIRECTORY.get()
    if directory is None:
        return None
    database = site.dbName()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", database):
        message = "Template alias caches require a bounded wiki identity."
        raise ValueError(message)
    name = Page(site, requested).title(with_ns=False).replace(" ", "_")
    encoded = quote(name, safe="")
    if len(encoded) > _MAX_ENCODED_FILENAME:
        encoded = (
            encoded[:_FILENAME_PREFIX]
            + "-"
            + hashlib.sha256(name.encode()).hexdigest()
        )
    return directory / database / encoded / "aliases"


def _cached(
    path: Path | None,
    site: BaseSite,
    requested: str,
    now: dt.datetime,
) -> dict | None:
    """Accept a complete observation within its freshness window.

    Returns:
        Detached cache facts, or none for expired or invalid content.
    """
    frame = _CACHE.read(path)
    if frame.height != 1:
        return None
    row = frame.row(0, named=True)
    aliases = row["aliases"]
    try:
        valid = (
            row["wiki"] == site.dbName()
            and row["requested_title"] == requested
            and type(row["canonical_revision"]) is int
            and row["canonical_revision"] > 0
            and isinstance(row["started_at"], dt.datetime)
            and isinstance(row["checked_at"], dt.datetime)
            and row["started_at"] <= row["checked_at"] <= now
            and now - row["checked_at"] < _TTL
            and isinstance(aliases, list)
            and 0 < len(aliases) <= MAX_ALIASES
            and len(aliases) == len(set(aliases))
            and aliases[0] == row["canonical_title"]
            and requested in aliases
            and _template_title(site, row["canonical_title"])
            == row["canonical_title"]
            and all(_template_title(site, alias) == alias for alias in aliases)
        )
    except TypeError, ValueError:
        return None
    return row if valid else None


def _report_cached(path: Path, requested: str, row: dict) -> None:
    """Expose actual identity and retained observation times."""
    if path.is_absolute():
        try:
            filename = str(path.relative_to(Path.cwd()))
        except ValueError:
            filename = path.name
    else:
        filename = path.as_posix()
    report_activity(
        "saved",
        "template_aliases",
        wiki=row["wiki"],
        target=row["canonical_title"],
        references=(("page", row["canonical_title"]),),
        metadata={
            "file": filename,
            "observed_at": row["started_at"].isoformat(),
            "finished_at": row["checked_at"].isoformat(),
        },
        purpose="template_aliases",
        scope={"template": requested},
        kind="source_loaded",
    )


def read_aliases(site: BaseSite, title: str) -> tuple[str, ...]:
    """Reuse fresh aliases or query canonical and redirect metadata.

    Args:
        site: Existing source site defining namespace and script rules.
        title: Local template name with an optional template namespace.

    Returns:
        Canonical-first aliases including Chinese script variants.

    Raises:
        ValueError: The title, query result or aliases are invalid.
    """
    requested = _template_title(site, title)
    path = _cache_path(site, requested)
    started = _utc_now()
    try:
        selected = latest_cache_path(path) if path is not None else None
    except OSError:
        _LOGGER.warning("Could not inspect template alias cache: %s", path)
        selected = None
    cached = _cached(selected, site, requested, started)
    if cached is not None:
        _report_cached(selected, requested, cached)
        return tuple(cached["aliases"])
    metadata = template_alias_metadata(site, requested)
    aliases = _variants(site, metadata.aliases)
    if (
        not aliases
        or aliases[0] != metadata.canonical_title
        or len(aliases) > MAX_ALIASES
        or any(_template_title(site, alias) != alias for alias in aliases)
    ):
        message = f"Template aliases exceed their contract: {requested}."
        raise ValueError(message)
    if path is not None:
        frame = pl.DataFrame(
            [
                {
                    "wiki": site.dbName(),
                    "requested_title": requested,
                    "canonical_title": metadata.canonical_title,
                    "canonical_revision": metadata.canonical_revision,
                    "aliases": list(aliases),
                    "started_at": started,
                    "checked_at": _utc_now(),
                },
            ],
            schema=_SCHEMA,
        )
        try:
            _CACHE.write(path, frame)
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Could not save template alias metadata.")
    return aliases
