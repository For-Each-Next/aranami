"""Checkpoint bounded exact-source attribution correction audits.

Call after historical or current authorship collection with explicitly
scoped raw observations. Successful raw WikiWho facts remain untouched;
each origin review checkpoints durable proof facts and transfers.
Unverified sources keep their original attribution and coverage.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import time
import urllib.error
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from data.articles import authorship_corrections as store, authorship_store
from data.articles.authorship_store import CONTRIBUTOR_SCHEMA
from data.articles.storage import AUTHORSHIP_PATH
from shared.assessment_profiles import assessment_profile
from shared.sources import wikiwho
from tasks.articles.authorship_proof import (
    CleanupProof,
    build_cleanup_proof,
    cleanup_alignment,
    project_cleanup,
)
from tasks.articles.authorship_text import eligible_bytes, score_tokens

if TYPE_CHECKING:
    from collections.abc import Callable

    from pywikibot.site import BaseSite

OBSERVATION_SCHEMA = store.OBSERVATION_SCHEMA
_LOG = logging.getLogger(__name__)
_QUALITY_GRADES = ("FA", "FL", "A", "AL", "GA", "Bplus")
_EVENT_KEY = ("page_id", "date", "grade", "action")


def prepare_correction_observations(
    events: pl.DataFrame,
    membership: pl.DataFrame,
    evidence: pl.DataFrame,
    *,
    current_members: pl.DataFrame | None = None,
    current_evidence: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Select raw observations using complete current page identities.

    Args:
        events: Retained quality listings with their original dates.
        membership: Complete current project rows and raw assessments.
        evidence: Validated listing-time authorship observations.
        current_members: Optional exact current B/BL revision selector.
        current_evidence: Optional retained current-revision successes.

    Returns:
        Scoped raw bindings, including authors not currently qualified.
        B/BL requires agreement with actual current membership revision.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    members = membership.with_columns(
        profile.expression(
            "pa_class",
            "class",
            preserve_unknown=False,
        ).alias("_grade"),
    ).filter(
        (pl.col("page_namespace") == 0) & (pl.col("page_is_redirect") == 0),
    )
    frames = []
    if events.height and evidence.height:
        listings = (
            events
            .filter(
                (pl.col("kind") == "quality")
                & (pl.col("action") == "added")
                & pl.col("grade").is_in(_QUALITY_GRADES)
                & pl.col("date").is_not_null(),
            )
            .unique(subset=_EVENT_KEY, keep="last")
            .join(
                members.filter(pl.col("_grade").is_in(_QUALITY_GRADES)).select(
                    "page_id",
                    pl.col("full_title").alias("title"),
                ),
                on="page_id",
                how="inner",
                validate="m:1",
                suffix="_current",
            )
            .select(
                *_EVENT_KEY,
                pl.col("title_current").alias("title")
                if "title" in events.columns
                else pl.col("title"),
            )
        )
        frames.append(
            evidence
            .filter(
                pl.col("contract").is_in(authorship_store.CURRENT_CONTRACTS),
            )
            .join(listings, on=_EVENT_KEY, how="inner", validate="m:1")
            .select(
                "page_id",
                "title",
                pl.col("listing_revision").alias("revision_id"),
                pl.col("contract").alias("raw_contract"),
                pl.col("fetched_at").alias("raw_fetched_at"),
                "eligible_bytes",
                "contributors",
            ),
        )
    if (
        current_members is not None
        and current_evidence is not None
        and current_members.height
        and current_evidence.height
        and "page_revision" in membership.columns
    ):
        current = (
            members
            .filter(pl.col("_grade").is_in(["B", "BL"]))
            .select(
                "page_id",
                pl.col("page_revision").alias("revision_id"),
                pl.col("full_title").alias("title"),
            )
            .join(
                current_members.select("page_id", "revision_id"),
                on=["page_id", "revision_id"],
                how="semi",
            )
        )
        frames.append(
            current_evidence.join(
                current,
                on=["page_id", "revision_id"],
                how="inner",
                validate="m:1",
            ).select(
                "page_id",
                "title",
                "revision_id",
                pl.col("contract").alias("raw_contract"),
                pl.col("fetched_at").alias("raw_fetched_at"),
                "eligible_bytes",
                "contributors",
            ),
        )
    if not frames:
        return pl.DataFrame(schema=OBSERVATION_SCHEMA)
    return (
        pl
        .concat([frame.cast(OBSERVATION_SCHEMA) for frame in frames])
        .select(list(OBSERVATION_SCHEMA))
        .cast(OBSERVATION_SCHEMA)
        .unique(subset=store.KEY, keep="last")
    )


def _current_tables(runtime: Path) -> dict[str, pl.DataFrame] | None:
    """Read optional B evidence without failing independent HQ work.

    Returns:
        Current B tables, or none for unavailable optional storage.
    """
    # Optional B storage is needed only by the runtime scope wrapper.
    from data.articles import (  # ruff: ignore[import-outside-top-level]
        current_authorship,
    )

    try:
        return current_authorship.load(runtime)
    except (OSError, ValueError, pl.exceptions.PolarsError) as error:
        _LOG.warning(
            "Current B provenance scope is unavailable (%s).",
            type(error).__name__,
        )
        return None


def refresh_current_corrections(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    events: pl.DataFrame,
    membership: pl.DataFrame,
    *,
    runtime: Path = Path(),
    max_origins: int = 2,
    max_seconds: float = 30,
    should_yield: Callable[[], bool] | None = None,
) -> pl.DataFrame:
    """Audit currently eligible HQ and exact B/BL raw revision bindings.

    Args:
        site: Existing Chinese Wikipedia source client.
        events: Retained complete quality listing inventory.
        membership: Actual complete current membership and revisions.
        runtime: Toolforge runtime root containing both raw stores.
        max_origins: Maximum origin reviews for this background pass.
        max_seconds: Cooperative source-request budget.
        should_yield: Optional ordinary-work and shutdown priority hook.

    Returns:
        Durable proof coverage, preserving every existing raw success.
        Zero origins performs no external requests.
    """
    if max_origins == 0 or max_seconds == 0:
        return store.load(runtime / store.DEFAULT_PATH)
    current = _current_tables(runtime)
    observations = prepare_correction_observations(
        events,
        membership,
        authorship_store.load(runtime / AUTHORSHIP_PATH),
        current_members=current["members"] if current is not None else None,
        current_evidence=current["evidence"] if current is not None else None,
    )
    return refresh_corrections(
        site,
        observations,
        path=runtime / store.DEFAULT_PATH,
        max_origins=max_origins,
        max_seconds=max_seconds,
        should_yield=should_yield,
    )


class _YieldReviewError(Exception):
    """Stop between source requests when collection must yield."""


class _Read:
    """Pace exact-source requests within a cooperative audit budget."""

    def __init__(
        self,
        site: BaseSite,
        row: dict,
        deadline: float,
        should_yield: Callable[[], bool] | None,
        last_started: float,
    ) -> None:
        """Bind one raw observation without making source requests."""
        self.site = site
        self.row = row
        self.deadline = deadline
        self.should_yield = should_yield
        self.last_started = last_started
        self.reviewed = 0
        self.sources: dict[int, wikiwho.RevisionSource] = {}
        self.snapshots: dict[int, pl.DataFrame] = {}

    def call(self, function: Callable, *args: object) -> object:
        """Run one paced request only while the caller's budget permits.

        Returns:
            The source adapter's unchanged result.

        Raises:
            _YieldReviewError: The deadline or work callback is due.
        """
        delay = max(0.0, self.last_started + 1 - time.monotonic())
        if time.monotonic() + delay >= self.deadline or (
            self.should_yield is not None and self.should_yield()
        ):
            raise _YieldReviewError
        if delay:
            time.sleep(delay)
        self.last_started = time.monotonic()
        return function(*args)

    def source(self, revision_id: int) -> wikiwho.RevisionSource:
        """Read an exact source once within this observation's audit.

        Returns:
            Source text and its independently observed actual parent.
        """
        if revision_id not in self.sources:
            self.sources[revision_id] = self.call(
                wikiwho.revision_source,
                self.site,
                self.row["title"],
                self.row["page_id"],
                revision_id,
            )
        return self.sources[revision_id]

    def tokens(self, revision_id: int) -> pl.DataFrame:
        """Read one stable-ID snapshot within this observation's audit.

        Returns:
            Complete ordered raw token attribution for the revision.
        """
        if revision_id not in self.snapshots:
            self.snapshots[revision_id] = self.call(
                wikiwho.revision_tokens,
                self.row["page_id"],
                revision_id,
            )
        return self.snapshots[revision_id]


def _target(read: _Read) -> tuple[str, pl.DataFrame, list[int]]:
    """Bind a fresh exact snapshot to retained raw contributor totals.

    Returns:
        Exact source, stable tokens and eligible origins by byte weight.

    Raises:
        ValueError: New source observations disagree with saved facts.
    """
    row = read.row
    source = read.source(row["revision_id"])
    tokens = read.tokens(row["revision_id"])
    if tokens.filter(pl.col("origin") > source.revision_id).height:
        message = "Nonmonotonic scored origins withhold automatic repair."
        raise ValueError(message)
    scored = score_tokens(tokens, source.text)
    counts = (
        scored
        .group_by("editor")
        .agg(
            pl.len().cast(pl.UInt32).alias("tokens"),
            pl.col("bytes").sum(),
        )
        .sort("editor")
    )
    expected = pl.DataFrame(row["contributors"], schema=CONTRIBUTOR_SCHEMA)
    if eligible_bytes(source.text) != row[
        "eligible_bytes"
    ] or not counts.equals(
        expected.select("editor", "tokens", "bytes").sort("editor"),
    ):
        message = "Fresh provenance differs from the exact saved raw scores."
        raise ValueError(message)
    origins = (
        scored
        .filter(pl.col("bytes") > 0)
        .group_by("origin")
        .agg(pl.col("bytes").sum())
        .sort("bytes", "origin", descending=[True, False])["origin"]
        .to_list()
    )
    return source.text, tokens, origins


def _proof(read: _Read, origin: int) -> CleanupProof | None:
    """Acquire extra tokens only after source can prove a cleanup.

    Returns:
        Verified actual parent proof, or none outside its strict rule.
    """
    after = read.source(origin)
    if after.parent_revision <= 0:
        return None
    before = read.source(after.parent_revision)
    if cleanup_alignment(before.text, after.text) is None:
        return None
    return build_cleanup_proof(
        before,
        read.tokens(before.revision_id),
        after,
        read.tokens(after.revision_id),
    )


def _record(row: dict, previous: dict | None) -> dict:
    """Preserve successful facts while preparing an attempt checkpoint.

    Returns:
        Mutable attempt record bound to one unchanged raw observation.
    """
    return {
        **{key: row[key] for key in store.KEY},
        "observed_at": dt.datetime.now(dt.UTC),
        "status": "partial",
        "checked_origins": list(previous["checked_origins"])
        if previous
        else [],
        "proofs": list(previous["proofs"]) if previous else [],
        "transfers": list(previous["transfers"]) if previous else [],
    }


def _review(
    read: _Read,
    record: dict,
    maximum: int,
    path: Path,
) -> tuple[pl.DataFrame, str]:
    """Checkpoint each proven or nonqualifying origin independently.

    Returns:
        Retained ledger and this observation's actual coverage status.
    """
    text, tokens, origins = _target(read)
    checked = set(record["checked_origins"])
    ledger = store.load(path)
    outcome = "partial"
    for origin in (value for value in origins if value not in checked):
        if read.reviewed >= maximum:
            break
        read.reviewed += 1
        proof = _proof(read, origin)
        if proof is not None:
            transfers = project_cleanup(proof, text, tokens)
            names = {
                value["editor"]: value["name"]
                for value in read.row["contributors"]
                if value["name"] is not None
            }
            unknown = (
                transfers
                .filter(
                    ~pl.col("editor").is_in(list(names)),
                )["editor"]
                .unique()
                .to_list()
            )
            if unknown:
                names.update(read.call(wikiwho.usernames, read.site, unknown))
            transfers = transfers.with_columns(
                pl
                .col("editor")
                .replace_strict(names, default=None)
                .alias("name"),
            )
            record["proofs"].append(proof.record)
            record["transfers"].extend(transfers.to_dicts())
        checked.add(origin)
        record["checked_origins"] = sorted(checked)
        outcome = "complete" if checked.issuperset(origins) else "partial"
        record.update(status=outcome, observed_at=dt.datetime.now(dt.UTC))
        ledger = store.merge_save(
            path,
            pl.DataFrame([record], schema=store.SCHEMA),
        )
    return ledger, outcome


def _defer_review(
    read: _Read,
    record: dict,
    path: Path,
    ledger: pl.DataFrame,
    observe: Callable[[dict, str], None] | None,
) -> pl.DataFrame:
    """Checkpoint actual partial reads without inventing untried work.

    Returns:
        Updated retry ordering with prior proof facts preserved, or the
        unchanged ledger when the first request was never started.
    """
    if not read.sources and not read.snapshots:
        return ledger
    record.update(status="partial", observed_at=dt.datetime.now(dt.UTC))
    retained = store.merge_save(
        path,
        pl.DataFrame([record], schema=store.SCHEMA),
    )
    if observe is not None:
        observe(read.row, "partial")
    return retained


def refresh_corrections(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    observations: pl.DataFrame,
    *,
    path: Path = store.DEFAULT_PATH,
    max_origins: int = 2,
    max_seconds: float = 30,
    should_yield: Callable[[], bool] | None = None,
    observe: Callable[[dict, str], None] | None = None,
) -> pl.DataFrame:
    """Review bounded raw origins and checkpoint completed attempts.

    Args:
        site: Existing Chinese Wikipedia read-only source client.
        observations: Scoped rows matching OBSERVATION_SCHEMA.
            Normalize historical/current revision and contract names in
            the caller. Only non-reference byte contracts are audited.
        path: Durable correction ledger outside source generations.
        max_origins: Maximum origin reviews across all supplied rows.
        max_seconds: Cooperative budget checked before every request.
            An active adapter request keeps its own timeout bound.
        should_yield: Optional callback protecting ordinary work/drain.
        observe: Optional callback with the binding and complete,
            partial, unavailable or throttled attempt outcome.

    Returns:
        Complete retained ledger. Source failures never erase verified
        proofs. Missing or unfinished audits remain explicitly pending
        to readers; original raw contributor observations are unchanged.

    Raises:
        ValueError: Bounds or supplied raw observations are invalid.
    """
    if (
        type(max_origins) is not int
        or max_origins < 0
        or not math.isfinite(max_seconds)
        or max_seconds < 0
    ):
        message = "Provenance audit bounds cannot be negative."
        raise ValueError(message)
    ledger = store.load(path)
    if max_origins == 0 or max_seconds == 0 or observations.is_empty():
        return ledger
    selected = observations.select(list(OBSERVATION_SCHEMA)).cast(
        OBSERVATION_SCHEMA,
    )
    if selected.filter(
        pl.any_horizontal(pl.all().is_null())
        | (pl.col("page_id") <= 0)
        | (pl.col("revision_id") <= 0)
        | (pl.col("eligible_bytes") <= 0),
    ).height:
        message = "Provenance audits require complete positive raw bindings."
        raise ValueError(message)
    pending = (
        selected
        .filter(pl.col("raw_contract").str.contains("nonref-bytes"))
        .unique(subset=store.KEY, keep="last")
        .join(
            ledger.select(*store.KEY, "status", "observed_at"),
            on=store.KEY,
            how="left",
            validate="1:1",
        )
        .filter(pl.col("status").is_null() | (pl.col("status") != "complete"))
        .sort("observed_at", "raw_fetched_at", nulls_last=False)
    )
    deadline = time.monotonic() + max_seconds
    reviewed = 0
    last_started = -1.0
    for row in pending.iter_rows(named=True):
        prior = ledger.join(
            pl.DataFrame(
                [{key: row[key] for key in store.KEY}],
                schema={key: store.SCHEMA[key] for key in store.KEY},
            ),
            on=store.KEY,
            how="semi",
        )
        record = _record(row, prior.to_dicts()[0] if prior.height else None)
        read = _Read(site, row, deadline, should_yield, last_started)
        outcome = "partial"
        try:
            ledger, outcome = _review(
                read,
                record,
                max_origins - reviewed,
                path,
            )
        except _YieldReviewError:
            ledger = _defer_review(read, record, path, ledger, observe)
            break
        except Exception as error:  # ruff: ignore[blind-except] - Optional source failure retains raw evidence and prior proofs.
            outcome = (
                "throttled"
                if isinstance(error, wikiwho.DeferredError)
                or (
                    isinstance(error, urllib.error.HTTPError)
                    and error.code in {429, 503}
                )
                else "unavailable"
            )
            _LOG.info(
                "Provenance audit unavailable for page %s (%s).",
                row["page_id"],
                type(error).__name__,
            )
            record.update(
                status="unavailable",
                observed_at=dt.datetime.now(dt.UTC),
            )
            ledger = store.merge_save(
                path,
                pl.DataFrame([record], schema=store.SCHEMA),
            )
        reviewed += max(1, read.reviewed)
        last_started = read.last_started
        if observe is not None:
            observe(row, outcome)
        if outcome == "throttled" or reviewed >= max_origins:
            break
    return ledger
