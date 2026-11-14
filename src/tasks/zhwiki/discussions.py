"""Collect recently signed topics on every project-associated talk page.

Level-two topics qualify through an identified comment within the last
30 days. Banner edits and template-contained archived DYK votes
cannot create activity. All subject namespaces participate alongside
the central project talk page. Each saved finding retains its source
revision and exact comment anchor.
"""

from __future__ import annotations

import datetime as dt
import operator
from functools import partial
from itertools import batched
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from pywikibot import Page

from data.website import read_timestamp
from shared.assessment_profiles import assessment_profile
from shared.sources.discussions import read_comments
from shared.sources.quarry.revisions import page_info
from shared.sources.revisions import has_revision_cache
from shared.support.titles import page_url
from tasks.zhwiki.review_cache import ITEM_SCHEMA, ReviewCache
from tasks.zhwiki.reviews import (
    _activity,
    _plain_heading,
    _row,
    _section_anchors,
    _signed_comments,
    _url,
    _username,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pywikibot.site import BaseSite

_PROJECT_TALK = "WikiProject talk:电子游戏"
_ASSESSMENTS = assessment_profile("zhwiki", "电子游戏")


def activity_start(now: dt.datetime) -> dt.datetime:
    """Return the inclusive boundary of the preceding 30 days.

    Args:
        now: Aware collection time.

    Returns:
        The same instant exactly 30 days earlier, independent of month
        length and month-end dates.
    """
    return now - dt.timedelta(days=30)


def talk_topics(
    text: str,
    *,
    talk: str,
    url: Callable[[str], str],
) -> list[dict]:
    """Extract signed level-two discussions without template archives.

    Args:
        text: Current source wikitext at a verified revision.
        talk: Canonical talk-page title.
        url: URL builder bound to the same wiki client.

    Returns:
        Topic identities and observed activity. Opening prose remains
        unknown until the same revision's rendered body is acquired.
    """
    code = mwparserfromhell.parse(text)
    anchors = _section_anchors(code)
    result = []
    for section in code.get_sections(
        levels=[2],
        include_lead=False,
    ):
        comments = _signed_comments(section)
        opening = min(comments, key=operator.itemgetter(1), default=None)
        if opening is None:
            continue
        heading = _plain_heading(section)
        activity = _activity(section, opening[1], nominator=opening[0])
        activity["section_bytes"] = len(str(section).encode("utf-8"))
        latest = max(comments, key=operator.itemgetter(1))
        activity.update(replied_at=latest[1], replied_by=latest[0])
        result.append(
            _row(
                heading,
                "discussion",
                _url(
                    talk,
                    anchors[id(section.filter_headings(recursive=False)[0])],
                    url,
                ),
                opening[1],
                activity,
            ),
        )
    return result


def _opening_summary(text: str) -> str | None:
    """Keep opening prose and mark only an omitted continuation.

    Returns:
        Up to two opening sentences or a bounded fragment, if present.
    """
    text = text.strip()
    if not text:
        return None
    endings = [
        index + 1
        for index, character in enumerate(text)
        if character in "。\uff01\uff1f"
    ]
    end = min(
        endings[1]
        if len(endings) > 1
        else endings[0]
        if endings
        else len(text),
        240,
    )
    summary = text[:end].rstrip()
    if end == len(text) or summary.endswith(("…", "...")):
        return summary
    return summary + "…"


def _with_subjects(
    public: pl.DataFrame,
    membership: pl.DataFrame,
    site: BaseSite,
) -> pl.DataFrame:
    """Attach each topic's parent identity and project assessment.

    Args:
        public: Parsed topics with canonical talk-page identities.
        membership: Current project assessments for all namespaces.
        site: Wiki namespace rules used to identify each subject page.

    Returns:
        Topics with subject links and matching observed assessments.
    """
    subjects = []
    for talk in public["source_title"].unique().to_list():
        page = Page(site, talk)
        subject = page.toggleTalkPage() if page.isTalkPage() else page
        subjects.append({
            "source_title": talk,
            "subject_title": subject.title(),
            "subject_url": page_url(site, subject.title()),
        })
    identities = pl.DataFrame(
        subjects,
        schema=dict.fromkeys(
            ("source_title", "subject_title", "subject_url"),
            pl.String,
        ),
    )
    metadata = (
        membership
        .select(
            pl.col("full_title").alias("subject_title"),
            *[
                (
                    pl.col(source).cast(pl.String)
                    if source in membership.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias(target)
                for source, target in (
                    ("pa_class", "assessment_class"),
                    ("pa_importance", "assessment_importance"),
                )
            ],
        )
        .unique()
        .unique(subset="subject_title", keep="none")
    )
    return public.join(
        identities,
        on="source_title",
        how="left",
        maintain_order="left",
    ).join(
        metadata,
        on="subject_title",
        how="left",
        maintain_order="left",
    )


def _ordered_topics(public: pl.DataFrame, site: BaseSite) -> pl.DataFrame:
    """Order topics by article grade, nonarticle type and comment time.

    Args:
        public: Topics enriched with subject identities and assessments.
        site: Existing namespace rules for unclassified subject pages.

    Returns:
        Unchanged observations in project grade order. Unclassified main
        namespace subjects join unassessed articles. Nonarticles start
        with set indices, disambiguation pages and drafts, then retain
        the profile's remaining type order. Within a class, newest
        comments lead, followed by stable subject/topic identities.
    """
    main_namespace = {
        title: Page(site, title).namespace() == 0
        for title in public["subject_title"].unique().to_list()
    }
    classification = _ASSESSMENTS.expression(
        "assessment_class",
        "class",
        field="is_article",
        preserve_unknown=False,
    ).fill_null(
        pl.col("subject_title").replace_strict(
            main_namespace,
            default=False,
            return_dtype=pl.Boolean,
        ),
    )
    leading = [
        _ASSESSMENTS.code(value, "class", preserve_unknown=False)
        for value in ("同类索引", "消歧义", "草稿")
    ]
    nonarticles = leading + [
        label.code
        for label in _ASSESSMENTS.labels("class")
        if not label.is_article and label.code not in leading
    ]
    order = (
        pl
        .when(classification)
        .then(
            _ASSESSMENTS.expression(
                "assessment_class",
                "class",
                field="order",
                preserve_unknown=False,
            ).fill_null(_ASSESSMENTS.resolve("Unassessed", "class").order),
        )
        .otherwise(
            _ASSESSMENTS.expression(
                "assessment_class",
                "class",
                preserve_unknown=False,
            ).replace_strict(
                dict(zip(nonarticles, range(len(nonarticles)), strict=True)),
                default=len(nonarticles),
                return_dtype=pl.Int64,
            ),
        )
    )
    return public.sort(
        [
            ~classification,
            order,
            pl.col("replied_at"),
            pl.col("subject_title"),
            pl.col("title"),
            pl.col("review_url"),
        ],
        descending=[False, False, True, False, False, False],
        nulls_last=True,
    )


def _rendered_topics(
    source: str,
    revision: int,
    rows: list[dict],
    *,
    site: BaseSite,
    active_since: dt.datetime | None = None,
) -> list[dict]:
    """Enrich topics only from their revision's rendered comment bodies.

    Args:
        source: Canonical talk-page title supplying the source revision.
        revision: Exact revision checked and parsed by the cache owner.
        rows: Source topic identities, signatures and byte sizes.
        site: Existing read-only wiki client.
        active_since: Optional earliest activity needing rendered text.

    Returns:
        Topics with rendered opening excerpts and verified reply URLs.
        Missing or ambiguous comment identities retain unknown values.
        Source failures propagate before the cache or dataset advances.

    Raises:
        ValueError: A rendered comment reports a different revision.
    """
    active = (
        [
            row
            for row in rows
            if row["replied_at"] >= active_since.isoformat(timespec="seconds")
        ]
        if active_since is not None
        else rows
    )
    if not active:
        return [{**row, "extract": None, "replied_url": None} for row in rows]
    comments = read_comments(site, revision)
    identities = {}
    for comment in comments:
        if comment.revision_id != revision:
            message = "Rendered opening revision differs from its source."
            raise ValueError(message)
        key = (_username(comment.author), comment.timestamp)
        identities.setdefault(key, []).append(comment)
    enriched = []
    for original in rows:
        row = {**original, "extract": None, "replied_url": None}
        opening = identities.get((row["nominator"], row["started_at"]), [])
        if len(opening) == 1:
            row["extract"] = _opening_summary(opening[0].text)
        latest = identities.get((row["replied_by"], row["replied_at"]), [])
        if len(latest) == 1:
            row["replied_url"] = _url(
                source,
                latest[0].comment_id,
                partial(page_url, site),
            )
        enriched.append(row)
    return enriched


def collect_discussions(
    site: BaseSite,
    membership: pl.DataFrame,
    *,
    now: dt.datetime,
    cache_directory: Path | None = None,
) -> list[dict]:
    """Collect active topics across all current project namespaces.

    Args:
        site: Existing Chinese Wikipedia source client.
        membership: Complete project subject and talk-page identities.
        now: Actual aware UTC observation time.
        cache_directory: Disposable parser cache root, or none.

    Returns:
        Complete topics active within 30 days, ordered by grade and
        then newest comment, retaining each source revision and
        verified comment URL.
        Unchanged source parses are reused only after a successful
        revision check.

    Raises:
        ValueError: The source omits a requested page observation.
    """
    talks = []
    for title in membership["full_title"].unique().to_list():
        page = Page(site, title)
        talk = page if page.isTalkPage() else page.toggleTalkPage()
        talks.append(talk.title())
    talks.append(Page(site, _PROJECT_TALK).title())
    boundary = activity_start(now)
    recent = []
    for batch in batched(dict.fromkeys(talks), 50, strict=False):
        response = (
            {"query": {"pages": page_info(site, batch)}}
            if has_revision_cache()
            else site.simple_request(
                action="query",
                formatversion=2,
                prop="info",
                titles=list(batch),
            ).submit()
        )
        pages = response.get("query", {}).get("pages")
        if (
            "error" in response
            or not isinstance(pages, list)
            or len(pages) != len(batch)
            or {page.get("title") for page in pages} != set(batch)
        ):
            message = "Active discussion source omitted a talk page."
            raise ValueError(message)
        for page in pages:
            if page.get("missing") is True:
                continue
            stamp = read_timestamp(page.get("touched"))
            if stamp is None:
                message = "Active discussion source omitted a touch time."
                raise ValueError(message)
            if stamp >= boundary:
                recent.append(page["title"])
    # Calendar-month caches may lack rendered February observations.
    cache = ReviewCache(
        site,
        cache_directory / "discussion-sources-30d"
        if cache_directory
        else None,
        now,
    )
    rows = cache.read(
        {
            (talk, "discussion"): partial(
                talk_topics,
                talk=talk,
                url=partial(page_url, site),
            )
            for talk in recent
        },
        required=False,
        include_sources=True,
        enrich=partial(_rendered_topics, site=site, active_since=boundary),
    )
    frame = pl.DataFrame(
        rows,
        schema=ITEM_SCHEMA
        | {"source_title": pl.String, "source_revision": pl.Int64},
    ).filter(
        pl.col("replied_at").is_between(
            pl.lit(boundary.isoformat(timespec="seconds")),
            pl.lit(now.isoformat(timespec="seconds")),
        ),
    )
    public = _ordered_topics(_with_subjects(frame, membership, site), site)
    cache.save()
    return [{**row, "url": row["review_url"]} for row in public.to_dicts()]
