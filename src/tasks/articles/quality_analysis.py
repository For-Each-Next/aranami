"""Measure changed quality articles for a saved Chinese project report.

Run bounded passes in a short-lived background worker. Existing article
revisions reuse measurements; saved Article history supplies listing
dates, including Bplus, without another talk-page content request.
"""

from __future__ import annotations

import datetime as dt
import gc
import hashlib
import json
import logging
from itertools import batched
from pathlib import Path
from time import monotonic, sleep
from typing import TYPE_CHECKING

import polars as pl

from data.articles import history_store, quality_analysis as store
from data.articles.storage import HISTORY_PATH
from shared.assessment_profiles import assessment_profile
from shared.sources.wiki import read_page_ids
from shared.support.prose import extract_prose
from shared.support.words import count_words
from tasks.prose import PROFILE

if TYPE_CHECKING:
    from collections.abc import Callable

    from pywikibot.site import BaseSite

_LOG = logging.getLogger(__name__)
_BATCH_SIZE = 5
_MAX_TEXT_BYTES = 4 * 1024 * 1024
_CONTRACT = "zh-quality-prose:jieba-precise-hmm:2"


def _contract(*, include_template_values: bool) -> str:
    """Bind saved metrics to prose rules and template-value policy.

    Returns:
        Deterministic extraction identity independent of article titles.
    """
    return hashlib.sha256(
        json.dumps(
            {
                "contract": _CONTRACT,
                "headings": sorted(PROFILE.non_prose_headings),
                "templates": sorted(PROFILE.prose_templates),
                "parameters": PROFILE.prose_parameters.pattern,
                "include_template_values": include_template_values,
            },
            sort_keys=True,
        ).encode(),
    ).hexdigest()


def _listing_dates(
    members: pl.DataFrame,
    history: pl.DataFrame,
) -> pl.DataFrame:
    """Join local action order and independent histories' dated tails.

    Returns:
        Successful current-grade dates, with ambiguous order omitted.
        Action indices order one template only. Independent templates
        require dated, consistent tails; conflicting same-day outcomes
        remain unknown, and conflicting oldids never choose a version.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    # WAR stores the article group A for both article and list passes.
    selected = members.with_columns(
        pl
        .when(pl.col("grade") == "AL")
        .then(profile.expression("grade", "class", field="article_group"))
        .otherwise(pl.col("grade"))
        .alias("_current_grade"),
    )
    return (
        history
        .select(
            "page_id",
            "talk_page_id",
            pl.col("revision_id").alias("talk_revision_id"),
            "events",
        )
        .join(
            selected.select(
                "page_id",
                "talk_page_id",
                "talk_revision_id",
                "_current_grade",
            ),
            on=["page_id", "talk_page_id", "talk_revision_id"],
            how="inner",
            validate="1:1",
        )
        .explode("events", empty_as_null=False)
        .unnest("events")
        .filter(
            (pl.col("kind") == "quality")
            & (pl.col("grade") == pl.col("_current_grade")),
        )
        .with_columns(
            pl
            .col("parameter")
            .str.extract(
                r"^(?:history([1-9]\d*)_)?action([1-9]\d*)$",
                1,
            )
            .fill_null("1")
            .cast(pl.Int64)
            .alias("_history"),
            pl
            .col("parameter")
            .str.extract(
                r"^(?:history([1-9]\d*)_)?action([1-9]\d*)$",
                2,
            )
            .cast(pl.Int64)
            .alias("_action"),
        )
        .sort("page_id", "_history", "_action", maintain_order=True)
        .with_columns(
            (pl.col("date").forward_fill().diff().dt.total_days() < 0)
            .fill_null(value=False)
            .over("page_id", "_history")
            .alias("_reversed_date"),
            pl.col("_history").n_unique().over("page_id").alias("_histories"),
        )
        .with_columns(
            pl
            .col("_reversed_date")
            .any()
            .over("page_id")
            .alias("_contradiction"),
            pl
            .col("_action")
            .is_null()
            .any()
            .over("page_id")
            .alias("_unknown_order"),
        )
        .unique(
            subset=["page_id", "_history"],
            keep="last",
            maintain_order=True,
        )
        .filter(
            ~pl.col("_unknown_order")
            & ~((pl.col("_histories") > 1) & pl.col("_contradiction")),
        )
        .with_columns(
            pl.col("date").max().over("page_id").alias("_latest_date"),
            (
                pl.col("date").count().over("page_id")
                == pl.len().over("page_id")
            ).alias("_known_dates"),
        )
        .filter(
            pl.col("_known_dates")
            & (pl.col("date") == pl.col("_latest_date")),
        )
        .group_by("page_id")
        .agg(
            pl.col("date").first().alias("listed_date"),
            pl.col("action").n_unique().alias("_outcomes"),
            pl.col("action").first(),
            pl.col("oldid").drop_nulls().n_unique().alias("_oldids"),
            pl.col("oldid").drop_nulls().first().alias("listed_oldid"),
        )
        .filter((pl.col("_outcomes") == 1) & (pl.col("action") == "added"))
        .select(
            "page_id",
            "listed_date",
            pl
            .when(pl.col("_oldids") == 1)
            .then(pl.col("listed_oldid"))
            .otherwise(None)
            .alias("listed_oldid"),
        )
    )


def prepare_membership(
    membership: pl.DataFrame,
    history: pl.DataFrame,
    observed_at: dt.datetime,
) -> pl.DataFrame:
    """Prepare all current quality members with exact source identities.

    Args:
        membership: Shared complete project observation from replicas.
        history: Validated latest retained Article history snapshots.
        observed_at: Actual aware UTC time of this cohort observation.

    Returns:
        Complete FA/FL/A/AL/GA/Bplus rows with nullable prose and dates.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    members = (
        membership
        .with_columns(
            profile.expression(
                "pa_class",
                "class",
                preserve_unknown=False,
            ).alias("grade"),
            profile
            .expression("pa_importance", "importance", preserve_unknown=False)
            .fill_null("Unknown")
            .alias("importance"),
        )
        .filter(
            (pl.col("page_namespace") == 0)
            & (pl.col("page_is_redirect") == 0)
            & pl.col("grade").is_in(store.GRADES),
        )
        .select(
            "page_id",
            pl.col("full_title").alias("title"),
            "grade",
            "importance",
            pl.col("page_revision").alias("article_revision_id"),
            "talk_page_id",
            pl.col("talk_revision").alias("talk_revision_id"),
            pl.col("page_length").alias("source_bytes"),
        )
    )
    dates = _listing_dates(members, history)
    return (
        members
        .join(dates, on="page_id", how="left", validate="1:1")
        .with_columns(
            *(
                pl
                .when(pl.col("listed_date") <= pl.lit(observed_at.date()))
                .then(pl.col(name))
                .otherwise(None)
                .alias(name)
                for name in ("listed_date", "listed_oldid")
            ),
            pl.lit(observed_at).alias("observed_at"),
            pl
            .when(pl.col("grade").is_in(["FL", "AL"]))
            .then(pl.lit(_contract(include_template_values=True)))
            .otherwise(pl.lit(_contract(include_template_values=False)))
            .alias("measurement_contract"),
            *(
                pl.lit(None, dtype=store.ARTICLE_SCHEMA[name]).alias(name)
                for name in (
                    "prose_words",
                    "prose_bytes",
                    "measured_at",
                    "attempted_at",
                )
            ),
        )
        .select(list(store.ARTICLE_SCHEMA))
        .cast(store.ARTICLE_SCHEMA)
    )


def _measure(page: object, member: dict, site: BaseSite) -> dict:
    """Measure only the requested visible article revision.

    Returns:
        Complete member row with current exact-revision prose evidence.

    Raises:
        ValueError: Identity, revision or visible text is inconsistent.
    """
    if (
        not page.exists()
        or page.pageid != member["page_id"]
        or page.latest_revision_id != member["article_revision_id"]
    ):
        message = "Quality analysis source differs from selected revision."
        raise ValueError(message)
    text = page.text
    size = len(text.encode())
    if size > _MAX_TEXT_BYTES:
        message = "Quality analysis text exceeds its bounded source size."
        raise ValueError(message)
    prose = extract_prose(
        text,
        site=site,
        profile=PROFILE,
        include_template_values=member["grade"] in {"FL", "AL"},
    )
    return {
        **member,
        "source_bytes": size,
        "prose_words": count_words(prose, language="zh"),
        "prose_bytes": len(prose.encode()),
        "measured_at": dt.datetime.now(dt.UTC),
    }


def _attempt(page: object | None, member: dict, site: BaseSite) -> dict:
    """Isolate one unavailable body while retaining the full cohort.

    Returns:
        New measurements or the unchanged pending member and retry time.
    """
    row = member
    if page is not None:
        try:
            row = _measure(page, member, site)
        except Exception as error:  # ruff: ignore[blind-except] - Preserve independent source coverage.
            _LOG.warning(
                "Quality measurement unavailable for page %s (%s).",
                member["page_id"],
                type(error).__name__,
            )
    return {**row, "attempted_at": dt.datetime.now(dt.UTC)}


def _batch_measurements(site: BaseSite, batch: tuple[dict, ...]) -> list[dict]:
    """Preload only one small batch and release its source references.

    Returns:
        Scalar measurement updates without retained wiki page objects.
    """
    try:
        pages = read_page_ids(site, [row["page_id"] for row in batch])
    except Exception as error:  # ruff: ignore[blind-except] - A source outage changes retry order only.
        _LOG.warning(
            "Quality-analysis source batch unavailable (%s).",
            type(error).__name__,
        )
        pages = []
    indexed = {page.pageid: page for page in pages}
    return [_attempt(indexed.get(row["page_id"]), row, site) for row in batch]


def refresh_quality_analysis(  # ruff: ignore[too-many-arguments] - Source, runtime and bounded admission are explicit.
    site: BaseSite,
    membership: pl.DataFrame,
    *,
    runtime: Path = Path(),
    max_pages: int = 50,
    time_budget: float = 60,
    should_yield: Callable[[], bool] | None = None,
) -> None:
    """Refresh current membership and a bounded set of missing metrics.

    Args:
        site: Existing Chinese Wikipedia client supplied by runtime.
        membership: Complete shared current project observation.
        runtime: Owner of saved Parquet; the worker runs there so the
            tokenizer's disposable cache remains outside source files.
        max_pages: Maximum current article measurements attempted.
        time_budget: Seconds admitting additional preloaded batches.
        should_yield: Foreground priority or cancellation callback.

    Raises:
        ValueError: Scope or saved history violates its source contract.
    """
    if site.dbName() != "zhwiki":
        message = "Quality analysis requires the Chinese Wikipedia project."
        raise ValueError(message)
    with store.collection_lease(runtime) as acquired:
        if not acquired:
            _LOG.info(
                "Quality-analysis collector already active; pass deferred.",
            )
            return
        observed = dt.datetime.now(dt.UTC)
        deadline = monotonic() + time_budget
        history = history_store.load(runtime / HISTORY_PATH)
        members = prepare_membership(membership, history, observed)
        del history
        current = store.checkpoint(
            runtime,
            members=members,
            observed_at=observed,
        )
        pending = (
            current
            .filter(pl.col("prose_words").is_null())
            .sort(
                "attempted_at",
                "page_id",
                nulls_last=False,
            )
            .head(max(0, max_pages))
        )
        del current, members
        for batch in batched(
            pending.iter_rows(named=True),
            _BATCH_SIZE,
            strict=False,
        ):
            if monotonic() >= deadline or (should_yield and should_yield()):
                break
            updates = _batch_measurements(site, batch)
            store.checkpoint(
                runtime,
                updates=pl.DataFrame(updates, schema=store.ARTICLE_SCHEMA),
                observed_at=observed,
            )
            # Release parsed text and SDK cycles at each batch boundary.
            gc.collect()
            sleep(0.2)
