"""Select durable daily membership or temporary hourly observations.

Wrap background work in ``project_cycle``. Midnight work and ordinary
daily reports consume today's first complete project dataset. Dated
consumers select an exact saved boundary; later hourly passes use
temporary source reads.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

import polars as pl

from data import project_data
from data.articles import assessment_comparison, current_assessments
from data.caching import project_observations as hourly_cache
from data.observations import (
    observation_scope,
    record_page_inputs,
    record_project_snapshot,
)
from data.revisions import RevisionStore
from shared.config import ASSESSMENT_CATEGORIES
from shared.sources.cycle import source_cycle
from shared.sources.quarry.projects import (
    category_observations,
    query_pages_by_wikiproject,
)
from shared.sources.revisions import RevisionTextCache, revision_text_cache
from shared.support.cache import cache_key
from shared.support.dataset_activity import dataset_activity
from tasks import project_data as project_capture

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pywikibot.site import BaseSite

DAILY_REPORTS = frozenset({
    "assessment_log",
    "assessment_lists",
    "enwp_key_articles",
    "new_pages",
    "pageviews",
})
_HOURLY_LOCK = RLock()


def collect_project_categories(
    site: BaseSite,
    memberships: list[pl.DataFrame],
    *,
    now: dt.datetime,
    assessment_members: pl.DataFrame | None = None,
) -> None:
    """Collect all category links and configured lists in one SQL query.

    Args:
        site: Wiki supplying replica category and revision metadata.
        memberships: Completed project lists from the hourly phase.
        now: Actual UTC collection time selecting the cache contract.
        assessment_members: Chinese Video games membership for a
            bidirectional grade comparison, absent for other projects.
    """
    cache, path = hourly_cache.category_cache(site.dbName(), now)
    with _HOURLY_LOCK:
        if not cache.read(path).is_empty():
            return
        ids = (
            pl
            .concat([
                frame.select("page_id")
                for members in memberships
                for frame in (
                    members.select("page_id"),
                    members.select(pl.col("talk_page_id").alias("page_id")),
                )
            ])
            .drop_nulls()
            .unique()["page_id"]
            .to_list()
        )
        previous = hourly_cache.category_cache(
            site.dbName(),
            now - dt.timedelta(hours=1),
        )[1]
        dataset_activity("begin", path, baseline=previous)
        frame = category_observations(site, ASSESSMENT_CATEGORIES, ids)
        if assessment_members is not None and site.dbName() == "zhwiki":
            assessment_comparison.save(
                assessment_members,
                frame,
                category_observed_at=dt.datetime.now(dt.UTC),
                pageassessments_observed_at=current_assessments.load(
                    current_assessments.DEFAULT_PATH,
                )["observed_at"].max(),
            )
        cache.write(path, frame.select(list(hourly_cache.CATEGORY_SCHEMA)))


def _hourly_categories(
    database: str,
    *,
    now: dt.datetime,
    dry: bool,
) -> tuple[pl.DataFrame, frozenset[str]] | None:
    """Load a completed central category table without querying sources.

    Returns:
        Current-hour page/category coverage and complete category names,
        or none when central acquisition has not completed successfully.
    """
    cache, path = hourly_cache.category_cache(database, now, dry=dry)
    frame = cache.read(path)
    if frame.is_empty():
        return None
    return frame, frozenset(
        name.replace(" ", "_") for name in ASSESSMENT_CATEGORIES
    )


def collect_project_membership(
    site: BaseSite,
    project: str,
    *,
    now: dt.datetime,
    dry: bool = False,
) -> pl.DataFrame:
    """Read or collect the hour's shared membership and revision IDs.

    Midnight starts from the committed daily file. Later hours query
    replicas once and save disposable Parquet before consumers run.
    Missing caches rebuild under a process lock; failed reads propagate.
    Fresh Chinese Video games observations retain current eligibility
    independently of disposable caches. Missing retained
    eligibility forces a fresh query before reusing an hourly cache.

    Returns:
        Source-native membership rows with subject and talk page_latest.

    Raises:
        ValueError: Fresh project membership is empty or incomplete.
    """
    cache, path = hourly_cache.cache(site.dbName(), project, now, dry=dry)
    retain_assessments = (
        not dry and site.dbName() == "zhwiki" and project == "电子游戏"
    )
    with _HOURLY_LOCK:
        frame = cache.read(path)
        if not frame.is_empty() and (
            not retain_assessments or current_assessments.DEFAULT_PATH.exists()
        ):
            restored = hourly_cache.decode_members(
                frame,
                site.dbName(),
                project,
            )
            record_page_inputs(site.dbName(), project, restored)
            return restored
        previous = hourly_cache.cache(
            site.dbName(),
            project,
            now - dt.timedelta(hours=1),
            dry=dry,
        )[1]
        dataset_activity("begin", path, baseline=previous)
        reader = (
            partial(_daily_membership, now=now, dry=dry)
            if now.hour == 0 and now.date() >= project_data.ACTIVATION_DATE
            else None
        )
        with source_cycle(membership_reader=reader, share_project_groups=True):
            frame = query_pages_by_wikiproject(site, project)
        if frame.is_empty():
            message = "Hourly membership has no complete project rows."
            raise ValueError(message)
        if retain_assessments:
            observed_at = dt.datetime.now(dt.UTC)
            if reader is not None:
                snapshot = project_data.read_snapshot(
                    site.dbName(),
                    project,
                    now.date(),
                )
                if snapshot is None:
                    message = "Current assessments require the saved capture."
                    raise ValueError(message)
                observed_at = dt.datetime.fromisoformat(
                    snapshot.manifest["calculated_at"],
                )
            current_assessments.save(
                current_assessments.DEFAULT_PATH,
                frame,
                observed_at=observed_at,
            )
        encoded = hourly_cache.encode_members(frame, site.dbName(), project)
        cache.write(path, encoded)
        restored = hourly_cache.decode_members(encoded, site.dbName(), project)
        record_page_inputs(site.dbName(), project, restored)
        return restored


def _daily_membership(
    site: BaseSite,
    project: str,
    *,
    now: dt.datetime,
    dry: bool,
    boundary_date: dt.date | None = None,
) -> pl.DataFrame:
    """Read or capture one boundary without recursively selecting it.

    Returns:
        Durable membership in the source adapter's native column names.
    """
    with source_cycle():
        snapshot = project_capture.capture(
            site,
            project,
            boundary_date if boundary_date is not None else now.date(),
            dry=dry,
        )
    manifest = snapshot.manifest
    record_project_snapshot(
        site.dbName(),
        project,
        {
            "wiki": site.dbName(),
            "project": project,
            "snapshot_date": manifest["metadata"]["snapshot_date"],
            "boundary_date": manifest["metadata"]["boundary_date"],
            "sha256": manifest["sha256"],
            "generation": manifest["generation"],
            "observed_at": manifest["calculated_at"],
            "scheduled_for": manifest["metadata"]["scheduled_for"],
        },
    )
    frame = snapshot.tables["members"].rename({
        "rev_id": "page_revision",
        "talk_page_rev": "talk_revision",
    })
    record_page_inputs(site.dbName(), project, frame)
    return frame


@contextmanager
def project_cycle(
    *,
    now: dt.datetime | None = None,
    daily: bool = False,
    dry: bool = False,
    boundary_date: dt.date | None = None,
) -> Iterator[None]:
    """Pin daily consumers while keeping hourly observations temporary.

    Ordinary selection uses actual UTC time. A dated consumer can select
    its exact saved boundary without inventing historical captures.
    Failed daily acquisition propagates; current replica rows cannot
    replace it.

    Args:
        now: Actual aware execution time, or the current UTC clock.
        daily: Require today's durable membership at any execution hour.
        dry: Isolate newly captured observations from production data.
        boundary_date: Exact saved midnight boundary required by a dated
            consumer, or none for ordinary actual-day selection.

    Yields:
        Control with isolated source observations for this pass.

    Raises:
        ValueError: The clock is naive or the boundary is in the future.
    """
    instant = now if now is not None else dt.datetime.now(dt.UTC)
    if instant.tzinfo is None:
        message = "Project collection requires an aware execution clock."
        raise ValueError(message)
    instant = instant.astimezone(dt.UTC)
    boundary = boundary_date if boundary_date is not None else instant.date()
    if boundary > instant.date():
        message = "Project membership cannot select a future boundary."
        raise ValueError(message)
    selected = boundary_date is not None or (
        boundary >= project_data.ACTIVATION_DATE
        and (daily or instant.hour == 0)
    )
    reader = (
        partial(
            _daily_membership,
            now=instant,
            dry=dry,
            boundary_date=boundary,
        )
        if selected
        else partial(collect_project_membership, now=instant, dry=dry)
    )

    def revision_store(wiki: str) -> RevisionStore | RevisionTextCache:
        """Select shared Chinese files or another wiki's isolated cache.

        Returns:
            The caller's production or preview revision store.
        """
        if wiki == "zhwiki":
            return RevisionStore(
                Path("dry-run/data/revisions" if dry else "data/revisions"),
            )
        return RevisionTextCache(
            Path("dry-run/cache" if dry else "cache")
            / "revision-contents"
            / cache_key("revision-text", wiki),
            instant,
        )

    with (
        observation_scope(),
        revision_text_cache(
            Path("dry-run/cache" if dry else "cache") / "revision-contents",
            factory=revision_store,
        ),
        source_cycle(
            membership_reader=reader,
            category_reader=partial(_hourly_categories, now=instant, dry=dry),
        ),
    ):
        yield
