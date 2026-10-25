"""Capture and reuse complete daily WikiProject assessment observations.

Call ``get_snapshot`` before preparing reports. The first valid
daily observation remains immutable across publication outcomes.
``read_snapshot`` retrieves saved facts without collecting source data.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl

from shared.reports import ReportResult, ReportStore, report_key
from shared.sources.quarry.projects import query_pages_by_wikiproject

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

    from shared.reports import ReportBundle

_FAMILY = "project-assessments"
_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "talk_page_id": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "full_title": pl.String,
}
_REFERENCE_SCHEMA = {
    "page_revision": pl.Int64,
    "talk_revision": pl.Int64,
    "talk_title": pl.String,
}


def snapshot_key(site_name: str, project: str, day: dt.date) -> str:
    """Identify one project's immutable UTC-date observation.

    Args:
        site_name: Wiki database name without the replica suffix.
        project: Exact PageAssessments project name.
        day: UTC date of the actual observation.

    Returns:
        Shared-report key identifying one site's project and date.

    Raises:
        ValueError: The project name is empty.
    """
    if not project.strip():
        message = "A project assessment snapshot requires a project name."
        raise ValueError(message)
    return report_key(_FAMILY, site_name, project, variant=day.isoformat())


def _validate_members(frame: pl.DataFrame) -> None:
    """Reject incomplete source schemas or ambiguous page identities.

    Args:
        frame: Full source table, including nullable assessment fields.

    Raises:
        ValueError: The table is empty or has invalid source facts.
    """
    schema = _SCHEMA | {
        name: dtype
        for name, dtype in _REFERENCE_SCHEMA.items()
        if name in frame.columns
    }
    if frame.is_empty() or any(
        frame.schema.get(name) != dtype for name, dtype in schema.items()
    ):
        message = "Invalid project assessment snapshot schema or empty result."
        raise ValueError(message)
    invalid_title = (
        pl.col("full_title").is_null()
        | (pl.col("full_title").str.strip_chars().str.len_chars() == 0)
        | pl.col("full_title").str.contains(r"[\x00-\x1f\x7f<>\[\]{}|#]")
        | pl.col("full_title").is_duplicated()
    )
    invalid_identity = (
        pl.col("page_id").is_null()
        | (pl.col("page_id") <= 0)
        | pl.col("page_id").is_duplicated()
        | pl.col("page_namespace").is_null()
        | (pl.col("page_namespace") < 0)
        | pl.col("page_title").is_null()
        | (pl.col("page_title").str.strip_chars().str.len_chars() == 0)
        | (
            pl.col("talk_page_id").is_not_null()
            & (
                (pl.col("talk_page_id") <= 0)
                | pl.col("talk_page_id").is_duplicated()
            )
        )
    )
    invalid_references = []
    if "page_revision" in frame.columns:
        invalid_references.append(
            pl.col("page_revision").is_null() | (pl.col("page_revision") <= 0),
        )
    if "talk_revision" in frame.columns:
        invalid_references.append(pl.col("talk_revision") <= 0)
    if "talk_title" in frame.columns:
        invalid_references.append(
            (pl.col("talk_title").str.strip_chars().str.len_chars() == 0)
            | pl.col("talk_title").str.contains(
                r"[\x00-\x1f\x7f<>\[\]{}|#]",
            ),
        )
    if not frame.filter(
        pl.any_horizontal(
            invalid_title,
            invalid_identity,
            *invalid_references,
        ),
    ).is_empty():
        message = "Invalid project assessment snapshot identities or titles."
        raise ValueError(message)


def _metadata(project: str, day: dt.date) -> dict[str, str]:
    """Describe the daily boundary separately from observation time.

    Args:
        project: Exact source project name.
        day: UTC observation date.

    Returns:
        Stable source metadata for every reader of this daily snapshot.
    """
    return {
        "project": project,
        "snapshot_date": day.isoformat(),
        "scheduled_for": dt.datetime.combine(
            day,
            dt.time(),
            dt.UTC,
        ).isoformat(),
    }


def _validate_bundle(
    bundle: ReportBundle | None,
    site_name: str,
    project: str,
    day: dt.date,
) -> ReportBundle | None:
    """Validate source identity in addition to shared storage integrity.

    Args:
        bundle: Selected complete source generation, if one exists.
        site_name: Expected source wiki database.
        project: Expected source project name.
        day: Expected UTC observation date.

    Returns:
        The original validated bundle, or null if none was retained.

    Raises:
        ValueError: The bundle does not describe this daily source.
    """
    if bundle is None:
        return None
    manifest = bundle.manifest
    if (
        manifest["family"] != _FAMILY
        or manifest["target"] != {"site": site_name, "title": project}
        or manifest["metadata"] != _metadata(project, day)
        or "members" not in bundle.tables
        or not isinstance(manifest.get("calculated_at"), str)
    ):
        message = "Invalid project assessment snapshot source identity."
        raise ValueError(message)
    calculated_at = dt.datetime.fromisoformat(manifest["calculated_at"])
    if calculated_at.date() != day:
        message = "Project assessment snapshot has the wrong observation date."
        raise ValueError(message)
    _validate_members(bundle.tables["members"])
    return bundle


def read_snapshot(
    site: BaseSite,
    project: str,
    day: dt.date,
) -> ReportBundle | None:
    """Read a retained production observation without querying the wiki.

    Args:
        site: Source wiki whose database identifies the observation.
        project: Exact PageAssessments project name.
        day: UTC date to retrieve; missing dates are never fabricated.

    Returns:
        Complete source bundle, or null for an unobserved date.
    """
    site_name = site.dbName()
    bundle = ReportStore().read(snapshot_key(site_name, project, day))
    return _validate_bundle(bundle, site_name, project, day)


def get_snapshot(
    site: BaseSite,
    project: str,
    *,
    observed_at: dt.datetime,
    dry: bool = False,
) -> ReportBundle:
    """Reuse or capture the first complete observation of a UTC date.

    Previews reuse existing production data. Otherwise they only create
    preview data. Live calls create production data. Calculation time
    records the actual observation, including captures after midnight.
    Capture remains independent of subsequent wiki publication.

    Args:
        site: Source wiki whose replica supplies complete assessments.
        project: Exact PageAssessments project name.
        observed_at: Actual timezone-aware observation start.
        dry: Use isolated preview storage if production data is absent.

    Returns:
        Immutable full source data in ``tables['members']``.

    Raises:
        ValueError: Time or source facts are invalid.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        message = "Project assessment observations require an aware timestamp."
        raise ValueError(message)
    observed_at = observed_at.astimezone(dt.UTC)
    day = observed_at.date()
    site_name = site.dbName()
    key = snapshot_key(site_name, project, day)
    production = ReportStore()
    existing = _validate_bundle(production.read(key), site_name, project, day)
    if existing is not None:
        return existing
    store = ReportStore(dry=True) if dry else production
    with store.locked(key):
        if dry:
            existing = _validate_bundle(
                production.read(key),
                site_name,
                project,
                day,
            )
            if existing is not None:
                return existing
        existing = _validate_bundle(
            store.read(key, locked=True),
            site_name,
            project,
            day,
        )
        if existing is not None:
            return existing
        members = query_pages_by_wikiproject(site, project)
        _validate_members(members)
        result = ReportResult(
            family=_FAMILY,
            tables={"members": members},
            metadata=_metadata(project, day),
            provenance={
                "source": "wiki-replica-pageassessments",
                "site": site_name,
                "project": project,
                "observed_at": observed_at.isoformat(),
            },
            calculated_at=observed_at,
        )
        return store.write(
            key,
            result,
            "",
            run_id=uuid4().hex,
            started_at=observed_at,
            target={"site": site_name, "title": project},
            locked=True,
        )
