"""Render Django pages from validated, saved runtime observations.

The public application validates facts before calling these renderers.
Templates own HTML structure and escaping; context preparation localizes
observations without reading runtime files or starting source clients.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from functools import lru_cache
from hashlib import sha256
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlencode, urlsplit

import polars as pl
from django.template import engines
from markupsafe import Markup, escape

from shared.assessment_profiles import assessment_profile
from shared.config import routine_configuration
from shared.deployment import (
    project_build_time,
    project_version,
    resource_root,
)
from shared.newsletter import Issue
from webservice.consistency import (
    consistency_groups,
    coverage_status,
    finding_details,
    latest_history_first,
)
from webservice.django_setup import setup_django
from webservice.presentation import (
    Interface,
    article_extract,
    assessment_code,
    catalog,
    display_title,
    homepage_sections,
    local_url,
    maintenance_filter,
    maintenance_items,
    maintenance_page,
    maintenance_sections,
    maintenance_selection,
    pageviews_url,
    readable_size,
    relative_time,
    relative_units,
    timestamp,
    title_url,
    utc_time,
    wiki_link,
)
from webservice.quality_analysis import quality_analysis_context
from webservice.quality_authorship import quality_authorship_context
from webservice.redirects import redirect_selection
from webservice.status_view import report_context, status_context

if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.template.backends.jinja2 import Jinja2

    from webservice.presentation import Record

# Module:PJ:VG/RVW, revision 92100885, with credited vector equivalents.
# Reassessment uses Codex history.
_REVIEW_ICONS = {
    "fac": ("featured-candidate.svg", "Cscr-candidate.svg"),
    "flc": ("featured-candidate.svg", "Cscr-candidate.svg"),
    "acc": ("a-candidate.svg", "A_candidate.svg"),
    "gan": ("ga-candidate.svg", "GA_candidate.svg"),
    "bpan": ("bplus-candidate.svg", "Bplus_candidate.svg"),
    "pr": ("peer-review.svg", "Nuvola_apps_kedit.svg"),
    "ppr": ("project-peer-review.svg", "Cvg_peerreview_icon.png"),
    "dykc": ("dyk-candidate.svg", "DYK_candidate.svg"),
}
_HIGHLIGHT_ICONS = {
    "featured": ("featured.svg", "Cscr-featured.svg"),
    "good": ("good.svg", "Symbol_support_vote.svg"),
    "dyk": ("dyk.svg", "Symbol_question.svg"),
}
_SHOWCASE_TYPES = {
    "fa": "TFA",
    "fl": "TFL",
    "ga": "TGA",
    "dyk": "DYK",
    "news": "ITN",
    "history": "OTD",
}


def _review_link_key(item: Record, now: dt.datetime) -> str:
    """Separate observed nomination outcomes from elapsed voting time.

    Returns:
        The localized link key. Unscheduled review types keep their
        ordinary link. A deadline establishes only that voting ended.
    """
    if item["review_type"] not in {"dykc", "gan", "fac", "flc"}:
        return "reviews.link"
    result = item.get("result")
    if result in {"passed", "failed", "ended"}:
        return f"reviews.{result}"
    ends = item.get("ends_at")
    if ends is not None:
        try:
            deadline = dt.datetime.fromisoformat(ends)
        except ValueError:
            deadline = None
        if (
            deadline is not None
            and deadline.tzinfo is not None
            and now >= deadline
        ):
            return "reviews.ended"
    return "reviews.ongoing"


# Known Chinese Wikipedia talk namespaces, verified against siteinfo on
# 9 November 2026. Legacy saved titles are canonical; local aliases also
# resolve without creating a source client during page rendering.
_DISCUSSION_NAMESPACE_PAIRS = (
    ("", ("Talk", "对话", "對話", "討論", "讨论")),
    (
        "User",
        (
            "User talk",
            "UT",
            "使用者对话",
            "使用者對話",
            "使用者討論",
            "使用者讨论",
            "用戶對話",
            "用戶討論",
            "用户对话",
            "用户讨论",
        ),
    ),
    (
        "Wikipedia",
        (
            "Wikipedia talk",
            "Project talk",
            "WT",
            "Wikipedia对话",
            "Wikipedia對話",
            "Wikipedia討論",
            "Wikipedia讨论",
            "专案讨论",
            "專案討論",
            "維基百科對話",
            "維基百科討論",
            "维基百科对话",
            "维基百科讨论",
        ),
    ),
    (
        "File",
        (
            "File talk",
            "Image talk",
            "图像对话",
            "图像讨论",
            "图片讨论",
            "圖像對話",
            "圖像討論",
            "圖片討論",
            "文件对话",
            "文件對話",
            "文件討論",
            "文件讨论",
            "档案对话",
            "档案讨论",
            "檔案對話",
            "檔案討論",
        ),
    ),
    ("MediaWiki", ("MediaWiki talk", "MediaWiki討論", "MediaWiki讨论")),
    (
        "Template",
        (
            "Template talk",
            "样板对话",
            "样板讨论",
            "模板对话",
            "模板對話",
            "模板討論",
            "模板讨论",
            "樣板對話",
            "樣板討論",
        ),
    ),
    (
        "Help",
        (
            "Help talk",
            "使用說明討論",
            "使用说明讨论",
            "帮助对话",
            "帮助讨论",
            "幫助對話",
            "幫助討論",
            "說明討論",
            "说明讨论",
        ),
    ),
    (
        "Category",
        ("Category talk", "分类对话", "分类讨论", "分類對話", "分類討論"),
    ),
    (
        "Portal",
        ("Portal talk", "主題對話", "主題討論", "主题对话", "主题讨论"),
    ),
    (
        "WikiProject",
        (
            "WikiProject talk",
            "PJT",
            "WPJT",
            "专题对话",
            "专题讨论",
            "專題對話",
            "專題討論",
            "維基專題對話",
            "維基專題討論",
            "维基专题对话",
            "维基专题讨论",
        ),
    ),
    ("Draft", ("Draft talk", "草稿討論", "草稿讨论")),
    ("MOS", ("MOS talk",)),
    ("TimedText", ("TimedText talk",)),
    (
        "Module",
        (
            "Module talk",
            "模块对话",
            "模块讨论",
            "模塊對話",
            "模塊討論",
            "模組對話",
            "模組討論",
            "模组对话",
            "模组讨论",
        ),
    ),
    ("Event", ("Event talk", "活动讨论", "活動討論")),
)
_DISCUSSION_SUBJECT_PREFIXES = {
    alias.casefold(): subject
    for subject, aliases in _DISCUSSION_NAMESPACE_PAIRS
    for alias in aliases
}
_DISCUSSION_GROUPS = (
    "week-1",
    "week-2",
    "week-3",
    "week-4",
    "drafts",
    "projects",
    "categories-templates",
    "other",
)
# Chinese Wikipedia template images; provenance is in the credits.
_MAINTENANCE_ICONS = {
    "afd": "icono-aviso-borrar.svg",
    "csd": "icono-archivo-borrar.png",
    "copyvio": "copyright-problem.svg",
    "notability": "ambox-important.svg",
    "unreferenced": "tango-nosources.svg",
    "references": "tango-nosources.svg",
    "footnotes": "text-document-with-red-question-mark.svg",
    "unreliable": "text-document-with-red-question-mark.svg",
    "blp_sources": "ambox-important.svg",
    "accuracy": "system-search.svg",
    "neutrality": "emblem-scales.svg",
    "update": "ambox-outdated-serious.svg",
    "original_research": "ambox-important.svg",
    "fiction": "ambox-important.svg",
    "globalize": "ambox-globe-content.svg",
    "trivia": "edit-clear.svg",
    "expand_english": "translation-to-zh-arrow.svg",
    "expand_japanese": "translation-to-zh-arrow.svg",
    "translation": "translation-to-zh-arrow.svg",
    "proofread_translation": "translation-to-zh-arrow.svg",
    "orphan": "arrows-orphan.svg",
    "underlinked": "ambox-wikify.svg",
    "expand": "antistub.svg",
    "lead": "edit-clear.svg",
    "merge": "merge-arrows.svg",
    "split": "split-arrows.svg",
    "copyedit": "ambox-spelling.svg",
    "rewrite": "ambox-rewrite.svg",
    "style": "edit-clear.svg",
    "weasel": "ambox-important.svg",
    "advert": "ambox-pr-orange.svg",
    "selfpromotion": "ambox-important.svg",
    "coi": "ambox-scales.svg",
    "cleanup": "edit-clear.svg",
    "wikify": "ambox-wikify.svg",
    "uncategorized": "wiki-letter-w.svg",
    "expert": "ambox-important.svg",
}
_CONSISTENCY_HISTORY_PREVIEW = 10


@lru_cache(maxsize=64)
def _asset_url(name: str) -> str:
    """Identify an authored source asset by its immutable content.

    Args:
        name: Relative asset path supplied by a template or renderer.

    Returns:
        A local URL whose digest permits safe browser caching.
    """
    asset = resource_root("webservice") / "public_html" / name
    return f"/{name}?v={sha256(asset.read_bytes()).hexdigest()}"


@lru_cache(maxsize=1)
def _templates() -> Jinja2:
    """Load cached presentation templates with HTML autoescaping.

    Returns:
        A Django template backend rooted in the source directory.
    """
    setup_django()
    backend = engines["jinja2"]
    backend.env.globals["asset_url"] = _asset_url
    return backend


def _render(name: str, context: Record) -> str:
    """Render plain context values through an autoescaping template.

    Returns:
        A complete document or live fragments selected by the caller.
    """
    return _templates().get_template(name).render(context)


@lru_cache(maxsize=4)
def _messages(locale: str) -> Record:
    """Expose authored message namespaces to Jinja templates.

    Returns:
        Nested message keys containing localized plain text.
    """
    interface = Interface(locale)
    result = {}
    for key in catalog():
        namespace = result
        *parents, leaf = key.split(".")
        for parent in parents:
            if isinstance(namespace.get(parent), str):
                namespace[parent] = {"heading": namespace[parent]}
            namespace = namespace.setdefault(parent, {})
        if isinstance(namespace.get(leaf), dict):
            namespace[leaf]["heading"] = interface.text(key)
        else:
            namespace[leaf] = interface.text(key)
    return result


def _time(value: object, *, seconds: bool = False) -> Record:
    """Prepare an actual UTC observation or its unknown marker.

    Returns:
        The original timestamp, its label, and whether it is known.
    """
    label = utc_time(value, seconds=seconds)
    return {"value": value, "label": label, "known": label != "—"}


def _english_ordinal(value: int) -> str:
    """Format an English rank with grouped digits and an ordinal suffix.

    Args:
        value: Observed positive rank.

    Returns:
        A rank such as ``1st``, ``12th``, or ``1,000th``.
    """
    suffix = (
        "th"
        if value % 100 in {11, 12, 13}
        else {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    )
    return f"{value:,}{suffix}"


_SECTION_SIZE_INTEGER_BYTES = 10_000
_DISCUSSION_HOT_PARTICIPANTS = 3
_DISCUSSION_HOT_COMMENTS = 10


def _section_size(value: object, interface: Interface) -> Record | None:
    """Format observed topic bytes as a decimal-kilobyte label.

    Returns:
        The SI size label and exact source-byte tooltip, if known.
    """
    if type(value) is not int or value <= 0:
        return None
    kilobytes = value / 1000
    digits = (
        0
        if value >= _SECTION_SIZE_INTEGER_BYTES
        else 1
        if kilobytes >= 1
        else 2
    )
    return {
        "label": f"{kilobytes:,.{digits}f}\u00a0kB",
        "description": interface.text(
            "reviews.section_size",
            bytes=f"{value:,}",
        ),
    }


def _discussion_subject(item: Record) -> tuple[str | None, str | None]:
    """Resolve a saved topic's subject using known wiki namespaces.

    Args:
        item: Topic observations, with optional saved subject metadata.

    Returns:
        The subject title and URL, or unknown values without identity.
        Unrecognized colon prefixes remain literal page-title content.
    """
    title = item.get("subject_title") or item.get("source_title")
    if not isinstance(title, str) or not title:
        return None, None
    prefix, separator, name = title.partition(":")
    subject = _DISCUSSION_SUBJECT_PREFIXES.get(
        prefix.replace("_", " ").strip().casefold(),
    )
    if separator and name and subject is not None:
        title = f"{subject}:{name}" if subject else name
        return title, title_url(title)
    return title, item.get("subject_url") or title_url(title)


def _discussion_activity(item: Record, now: dt.datetime) -> str:
    """Classify saved section totals against the latest comment time.

    Args:
        item: Observed topic timestamps and whole-section counts.
        now: Aware render clock, shared with the displayed comment ages.

    Returns:
        The ordinary, recent, hot, or combined icon state. Missing or
        future timestamps never establish activity. Counts describe the
        whole section rather than a time series.
    """
    latest = timestamp(item.get("replied_at"))
    if latest is None or latest > now:
        return "ordinary"
    age = now - latest
    comments = item.get("comment_count")
    participants = item.get("participant_count")
    recent = (
        type(comments) is int
        and comments > 1
        and age <= dt.timedelta(hours=24)
    )
    hot = (
        type(comments) is int
        and comments >= _DISCUSSION_HOT_COMMENTS
        and type(participants) is int
        and participants >= _DISCUSSION_HOT_PARTICIPANTS
        and age <= dt.timedelta(days=7)
    )
    if recent:
        return "recent-hot" if hot else "recent"
    return "hot" if hot else "ordinary"


def _discussion_key(value: object) -> str | None:
    """Normalize a saved section or comment URL for exact identity.

    Args:
        value: Saved wiki URL, possibly absent or without a fragment.

    Returns:
        A host, page and fragment identity, or none for a bare page.
        Percent escapes and equivalent wiki spaces compare equally;
        a review of one section never excludes its entire talk page.
    """
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if not parsed.fragment:
        return None
    return json.dumps([
        parsed.hostname,
        unquote(parsed.path).replace("_", " "),
        unquote(parsed.fragment).replace("_", " "),
    ])


def _discussion_selection(
    items: list[Record],
    now: dt.datetime,
    *,
    days: int,
    reviews: list[Record],
) -> pl.DataFrame:
    """Select dated saved topics and assign exclusive display groups.

    Args:
        items: Saved general discussions with optional subject metadata.
        now: Shared aware UTC render clock.
        days: Inclusive elapsed-day activity window.
        reviews: Recognized reviews excluded by section or comment.

    Returns:
        Source row indices, normalized comment instants and group keys,
        ordered by page type and newest comment with stable subject
        and topic ties.
        Assessments determine content type; missing class observations
        fall back to known subject namespaces. Unknown dates cannot
        establish activity, and future dates never qualify.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    review_keys = [
        key
        for review in reviews
        for field in ("review_url", "latest_comment_url", "replied_url")
        if (key := _discussion_key(review.get(field))) is not None
    ]
    frame = (
        pl
        .DataFrame(
            [
                {
                    "index": index,
                    "subject": _discussion_subject(item)[0],
                    "assessment_class": item.get("assessment_class"),
                    "latest": item.get("replied_at"),
                    "title": item["title"],
                    "review_url": item["review_url"],
                    "topic_key": _discussion_key(item["review_url"]),
                    "comment_key": _discussion_key(item.get("replied_url")),
                }
                for index, item in enumerate(items)
            ],
            schema={
                "index": pl.UInt32,
                "subject": pl.String,
                "assessment_class": pl.String,
                "latest": pl.String,
                "title": pl.String,
                "review_url": pl.String,
                "topic_key": pl.String,
                "comment_key": pl.String,
            },
        )
        .with_columns(
            pl.col("latest").str.to_datetime(
                format="%+",
                time_zone="UTC",
                strict=False,
            ),
            profile.expression(
                "assessment_class",
                "class",
                preserve_unknown=False,
            ).alias("class"),
            profile.expression(
                "assessment_class",
                "class",
                field="is_article",
                preserve_unknown=False,
            ).alias("article"),
            pl.col("subject").str.extract(r"^([^:]+):").alias("namespace"),
        )
        .filter(
            pl.col("latest").is_between(now - dt.timedelta(days=days), now),
            ~pl.col("topic_key").is_in(review_keys).fill_null(value=False),
            ~pl.col("comment_key").is_in(review_keys).fill_null(value=False),
        )
    )
    namespace_group = (
        pl
        .when(pl.col("namespace") == "Draft")
        .then(pl.lit("drafts"))
        .when(pl.col("namespace").is_in(["WikiProject", "Portal"]))
        .then(pl.lit("projects"))
        .when(pl.col("namespace").is_in(["Category", "Template"]))
        .then(pl.lit("categories-templates"))
        .when(
            pl.col("namespace").is_in(
                [
                    name
                    for name, _aliases in _DISCUSSION_NAMESPACE_PAIRS
                    if name
                ],
            ),
        )
        .then(pl.lit("other"))
        .otherwise(pl.lit("article"))
    )
    kind = (
        pl
        .when(pl.col("class") == "Draft")
        .then(pl.lit("drafts"))
        .when(pl.col("class").is_in(["Project", "Portal"]))
        .then(pl.lit("projects"))
        .when(pl.col("class").is_in(["Category", "Template"]))
        .then(pl.lit("categories-templates"))
        .when(pl.col("article"))
        .then(pl.lit("article"))
        .when(pl.col("class").is_not_null() & (pl.col("class") != "Unknown"))
        .then(pl.lit("other"))
        .otherwise(namespace_group)
    )
    week = (
        pl
        .when(pl.col("latest") >= now - dt.timedelta(days=7))
        .then(pl.lit("week-1"))
        .when(pl.col("latest") >= now - dt.timedelta(days=14))
        .then(pl.lit("week-2"))
        .when(pl.col("latest") >= now - dt.timedelta(days=21))
        .then(pl.lit("week-3"))
        .otherwise(pl.lit("week-4"))
    )
    return frame.with_columns(
        pl
        .when(kind == "article")
        .then(week)
        .otherwise(kind)
        .cast(pl.Enum(_DISCUSSION_GROUPS))
        .alias("group"),
    ).sort(
        ["group", "latest", "subject", "title", "review_url"],
        descending=[False, True, False, False, False],
        nulls_last=True,
    )


class _Page:
    """Prepare localized template values from unchanged source facts."""

    def __init__(self, locale: str) -> None:
        """Use one negotiated locale throughout a complete response."""
        self.interface = Interface(locale)
        self.text = self.interface.text

    def context(self, heading: str, *, live: bool = False) -> Record:
        """Prepare metadata and shared navigation for a document.

        Returns:
            Plain context values and an explicitly validated wiki link.
        """
        before, after = self.text("extracts.attribution").split("{license}")
        return {
            "heading": heading,
            "language_tag": self.interface.language_tag,
            "t": _messages(self.interface.locale),
            "live": live,
            "footer": {
                "label": self.text(
                    "footer.uptime_unknown",
                    version=project_version(),
                ),
                "unknown": self.text(
                    "footer.uptime_unknown",
                    version=project_version(),
                ),
            },
            "clock": self.clock({}, dt.datetime.now(dt.UTC)),
            "extract_attribution": {"before": before, "after": after},
            "project_link": wiki_link(
                "https://zh.wikipedia.org/wiki/WikiProject:电子游戏",
                self.text("nav.project"),
            ),
        }

    def clock(self, schedule: Record, now: dt.datetime) -> Record:
        """Share server clock anchors between status views.

        Returns:
            Plain values for browser clocks and observed state expiry.
        """
        return {
            "server_time": now.isoformat(),
            "expires": schedule.get("expires_at"),
            "stale": self.text("ops.schedule_stale"),
            "messages": json.dumps({
                key: self.text(
                    key,
                    count="{count}",
                    seconds="{seconds}",
                    minutes="{minutes}",
                    hours="{hours}",
                    days="{days}",
                )
                for key in (
                    "duration.seconds",
                    "duration.minutes",
                    "duration.hours",
                    "duration.days",
                    "time.months_ago",
                    "time.months_ahead",
                    "time.years_ago",
                    "time.years_ahead",
                    "time.weeks_ago",
                    "time.weeks_ahead",
                    "time.minutes_ago",
                    "time.hours_ago",
                    "time.days_ago",
                    "time.minutes_ahead",
                    "time.hours_ahead",
                    "time.days_ahead",
                    "time.soon",
                    "time.just_now",
                    "time.minutes_ago_one",
                    "time.minutes_ahead_one",
                    "time.hours_ago_one",
                    "time.hours_ahead_one",
                    "time.days_ago_one",
                    "time.days_ahead_one",
                    "time.months_ago_one",
                    "time.months_ahead_one",
                    "time.years_ago_one",
                    "time.years_ahead_one",
                    "time.weeks_ago_one",
                    "time.weeks_ahead_one",
                )
            }),
        }

    def article(self, item: Record) -> Record:
        """Prepare a title, assessment chips, and saved introduction.

        Returns:
            Localized plain observations and a safe upstream title link.
        """
        assessments = []
        for dimension in ("class", "importance"):
            value = item.get(f"assessment_{dimension}")
            code = assessment_code(value, dimension) or (
                "Unassessed"
                if dimension == "class"
                and (not isinstance(value, str) or not value.strip())
                else "Unknown"
            )
            label = self.text(f"assessment.{dimension}.{code}")
            dimension_label = self.text(f"assessment.dimension.{dimension}")
            assessments.append({
                "dimension": dimension,
                "label": label,
                "dimension_label": dimension_label,
                "description": self.text(
                    f"assessment.description.{dimension}.{code}",
                ),
                "tooltip": self.text(
                    f"assessment.tooltip.{dimension}",
                    grade=self.text(
                        f"assessment.description.{dimension}.{code}",
                    ),
                ),
            })
        language, extract = article_extract(item, self.interface.locale)
        title = display_title(item["title"], self.interface.locale)
        return {
            "link": wiki_link(item["url"], title, title),
            "assessments": assessments,
            "extract": extract,
            "extract_language": Interface(language).language_tag,
        }

    def article_card(
        self,
        item: Record,
        pageviews: Record | None,
        section: str,
        now: dt.datetime,
    ) -> Record:
        """Prepare date, appearance, and popularity observations.

        Args:
            item: Saved observations, including the appearance kind.
            pageviews: Observed metadata for the popularity section.
            section: Source section for DYK or showcase appearances.
            now: Dashboard clock used to identify the current year.

        Returns:
            An article card preserving each observation's meaning.
        """
        card = self.article(item)
        source_type = (
            _SHOWCASE_TYPES.get(item.get("kind"))
            if section == "recognized"
            else None
        )
        card["showcase_type"] = (
            {
                "code": source_type,
                "description": self.text(
                    f"homepage.scheduled_type.{source_type.lower()}"
                    if item.get("date_kind") in {"scheduled", "estimated"}
                    and source_type in {"TFA", "TFL", "TGA"}
                    else f"homepage.type.{source_type.lower()}",
                ),
            }
            if source_type
            else None
        )
        appearance = (
            "dyk"
            if section == "dyk" or item.get("kind") == "dyk"
            else {
                "fa": "featured",
                "fl": "featured",
                "featured": "featured",
                "ga": "good",
            }.get(item.get("kind"))
            if section == "recognized"
            else None
        )
        if appearance:
            icon, source = _HIGHLIGHT_ICONS[appearance]
            card["highlight_icon"] = {
                "url": _asset_url(f"assets/icons/{icon}"),
                "source": "https://commons.wikimedia.org/wiki/File:" + source,
                "label": self.text(
                    "homepage.dyk"
                    if appearance == "dyk"
                    else f"appearance.{appearance}",
                ),
            }
        if pageviews:
            popularity_day = dt.date.fromisoformat(pageviews["date"])
            card.update(
                rank=item["rank"],
                date={
                    "value": pageviews["date"],
                    "label": self.interface.date(
                        pageviews["date"],
                        compact=True,
                        reference_year=popularity_day.year,
                    )
                    if self.interface.locale == "en"
                    else f"{popularity_day.month}月{popularity_day.day}日",
                },
                pageviews_url=pageviews_url(item, pageviews["date"]),
                views=self.text(
                    "pageviews.views_one"
                    if self.interface.locale == "en" and item["views"] == 1
                    else "pageviews.views",
                    count=f"{item['views']:,}",
                ),
                site_rank=self.text(
                    "pageviews.site_rank",
                    rank=_english_ordinal(item["site_rank"])
                    if self.interface.locale == "en"
                    else f"{item['site_rank']:,}",
                )
                if item.get("site_rank") is not None
                else None,
                site_rank_url=local_url(
                    "https://pageviews.wmcloud.org/topviews/",
                    project="zh.wikipedia.org",
                    date=pageviews["date"],
                    search=item["title"],
                ).replace("+", "%20"),
            )
            return card
        date = item.get("date")
        kind = {
            "fa": "appearance.featured",
            "fl": "appearance.featured",
            "featured": "appearance.featured",
            "ga": "appearance.good",
            "news": "homepage.news",
            "history": "homepage.history",
        }.get(item.get("kind"))
        note = {
            "window_start": "date.window_start",
            "observed": "date.observed",
        }.get(item.get("date_kind"))
        card.update(
            date={
                "value": date,
                "label": self.interface.date(
                    date,
                    compact=True,
                    reference_year=now.year
                    if section != "recognized"
                    else None,
                ),
            }
            if date is not None
            else None,
            scheduled=(
                f"{self.text('date.scheduled')} "
                if self.interface.locale == "en"
                else self.text("date.scheduled")
            )
            if date is not None
            and item.get("date_kind") in {"scheduled", "estimated"}
            else "",
            kind=self.text(kind) if kind else None,
            date_note=self.text(note) if note else None,
        )
        return card

    @staticmethod
    def source_updated(value: object) -> Record | None:
        """Prepare the section's observed source time.

        Args:
            value: Saved source observation timestamp, when available.

        Returns:
            Plain source metadata, or none without a saved observation.
        """
        return {"time": _time(value)} if value is not None else None

    def _review_elapsed(
        self,
        instant: dt.datetime,
        now: dt.datetime,
        event: str,
    ) -> Record:
        """Describe compact and complete elapsed review ages.

        Returns:
            Localized compact age and its complete event description.
            Shared whole-unit thresholds match other relative clocks.
        """
        unit, count = relative_units((now - instant).total_seconds())
        if count == 0:
            return {
                "elapsed": self.text("reviews.age_just_now"),
                "description": self.text(f"reviews.{event}_just_now"),
            }
        return {
            "elapsed": self.text(f"reviews.age_{unit}", count=count),
            "description": self.text(f"reviews.{event}_{unit}", count=count),
        }

    def _review_counts(self, item: Record) -> Record | None:
        """Describe known participant and comment totals.

        Args:
            item: Saved counts, with unknown observations left unset.

        Returns:
            Known counts, even without a latest-comment timestamp.
        """
        participants = (
            f"{item['participant_count']:,}"
            if item.get("participant_count") is not None
            else None
        )
        comments = (
            f"{item['comment_count']:,}"
            if item.get("comment_count") is not None
            else None
        )
        if participants is None and comments is None:
            return None
        description = (
            self.text(
                "reviews.participants_comments"
                if comments is not None
                else "reviews.participants",
                count=participants,
                comments=comments,
            )
            if participants is not None
            else self.text("reviews.comments", count=comments)
        )
        return {"participant_count": participants, "description": description}

    def _review_dates(
        self,
        item: Record,
        now: dt.datetime,
        *,
        discussion: bool,
        counts: Record | None,
    ) -> list[Record]:
        """Keep nomination and latest signed comment facts separate.

        Args:
            item: Saved review or discussion observations.
            now: Aware clock for elapsed labels and future-time guards.
            discussion: Use the discussion's existing comment fields.
            counts: Independent known section totals.

        Returns:
            Valid start and latest-comment indicators. Legacy reviews
            use response facts only when comment fields are absent.
            Explicit unknown latest comments never fall back.
        """
        latest = (
            "latest_comment"
            if not discussion and "latest_comment_at" in item
            else "replied"
        )
        dates = []
        for field, event, author in (
            ("started_at", "started", "nominator"),
            (f"{latest}_at", "replied", f"{latest}_by"),
        ):
            instant = timestamp(item.get(field))
            if instant is None or instant > now:
                continue
            age = self._review_elapsed(instant, now, event)
            dates.append({
                "value": item[field],
                "label": self.text(
                    "reviews.author_details",
                    event=relative_time(item[field], self.interface, now),
                    time=utc_time(item[field]),
                    user=item[author],
                    action=self.text(
                        "reviews.started_action"
                        if event == "started"
                        else "reviews.replied_action",
                    ),
                )
                if item.get(author)
                else self.text(
                    "reviews.time_details",
                    event=age["description"],
                    time=utc_time(item[field]),
                ),
                "marker": "+" if event == "started" else "\u203a",
                "url": item.get(f"{latest}_url")
                if event == "replied"
                else None,
                **age,
                "participant_count": counts["participant_count"]
                if counts is not None and event == "replied"
                else None,
                "participants": counts["description"]
                if counts is not None
                and counts["participant_count"] is not None
                and event == "replied"
                else None,
            })
        return dates

    def reviews(
        self,
        snapshot: Record,
        now: dt.datetime,
        *,
        discussion: bool = False,
        days: int | None = None,
    ) -> Record | None:
        """Prepare complete review cards with observed elapsed time.

        Args:
            snapshot: Saved dashboard facts, possibly without reviews.
            now: Aware server time for duration calculations.
            discussion: Select saved active discussion topics.
            days: Optional inclusive activity window for discussions.

        Returns:
            The review section, or none for a confirmed empty list.
        """
        data = snapshot.get("reviews")
        if discussion:
            data = (
                {"items": data["discussions"]}
                if data is not None and "discussions" in data
                else None
            )
            if data is not None and days is not None:
                items = data["items"]
                data = {
                    "items": [
                        items[index]
                        for index in _discussion_selection(
                            items,
                            now,
                            days=days,
                            reviews=(snapshot.get("reviews") or {}).get(
                                "items",
                                [],
                            ),
                        )["index"]
                    ],
                }
        if data is not None and not data["items"]:
            return None
        cards = []
        for item in (data or {}).get("items", []):
            subject, subject_url = (
                _discussion_subject(item) if discussion else (None, None)
            )
            card = self.article(
                {
                    **item,
                    "title": subject,
                    "url": subject_url,
                }
                if discussion and subject
                else item,
            )
            review_type = item["review_type"]
            review_label = self.text(f"reviews.{review_type}")
            activity = _discussion_activity(item, now) if discussion else None
            icon = _REVIEW_ICONS.get(review_type)
            icon_source = icon[1] if icon else "Codex_icon_history_(v2.6).svg"
            counts = self._review_counts(item)
            dates = self._review_dates(
                item,
                now,
                discussion=discussion,
                counts=counts,
            )
            card.update(
                review_icon=_asset_url(f"assets/icons/{icon[0]}")
                if icon
                else None,
                review_icon_symbol=(
                    "speech-bubbles"
                    if activity == "ordinary"
                    else f"speech-bubbles-{activity}"
                )
                if discussion
                else None
                if icon
                else "history",
                review_icon_label=review_label,
                review_activity=activity,
                review_activity_label=self.text(
                    "reviews.activity_details",
                    activity=self.text(
                        f"reviews.activity_{activity.replace('-', '_')}",
                    ),
                )
                if activity is not None
                else None,
                review_icon_source="https://doc.wikimedia.org/codex/latest/icons/overview.html"
                if discussion
                else (
                    "https://commons.wikimedia.org/wiki/File:" + icon_source
                ),
                review_link=wiki_link(
                    title_url(item["source_title"])
                    if discussion and item.get("source_title")
                    else item["review_url"],
                    self.text(_review_link_key(item, now)),
                ),
                review_dates=dates,
                review_counts=counts,
                discussion_link=wiki_link(
                    item["review_url"],
                    display_title(item["title"], self.interface.locale),
                    display_title(item["title"], self.interface.locale),
                )
                if discussion
                else None,
                review_section_size=_section_size(
                    item.get("section_bytes"),
                    self.interface,
                ),
            )
            cards.append(card)
        return {
            "cards": cards,
            "is_review": True,
            "key": "discussions" if discussion else "reviews",
            "empty": self.text("empty.section") if data is None else None,
            "updated": self.source_updated(
                snapshot
                .get("reports", {})
                .get("reviews", {})
                .get("last_success_at"),
            ),
        }

    def highlights(self, snapshot: Record, now: dt.datetime) -> list[Record]:
        """Prepare visible article sections, omitting confirmed empties.

        Args:
            snapshot: Saved dashboard observations.
            now: Dashboard clock used to label current-year dates.

        Returns:
            Ordered sections retaining unavailable and empty states.
        """
        sections = []
        for section in homepage_sections(snapshot, now):
            key = section["key"]
            views = snapshot.get("pageviews") if key == "pageviews" else None
            updated = (
                snapshot.get("reports", {}).get(key, {}).get("last_success_at")
            )
            sections.append({
                "key": key,
                "title": self.text(
                    "pageviews.heading"
                    if key == "pageviews"
                    else f"homepage.{key}",
                ),
                "description": self.text(f"homepage.{key}_description")
                if key not in {"pageviews", "recognized", "dyk"}
                else None,
                "empty": self.text("pageviews.unavailable")
                if key == "pageviews" and views is None
                else self.text("empty.section")
                if section["items"] is None
                else self.text("empty.period")
                if not section["items"]
                else None,
                "cards": [
                    self.article_card(item, views, key, now)
                    for item in section["items"] or []
                ],
                "updated": self.source_updated(updated),
            })
        return sections

    def backlog(self, backlog: Record | None) -> Record | None:
        """Prepare nonempty maintenance tasks in their configured order.

        Returns:
            Ordered sections, unavailable state, or none when empty.
        """
        if backlog is not None and backlog["total"] == 0:
            return None
        if backlog is None:
            return {"unavailable": True}
        groups = [group for group in backlog["groups"] if group["count"] > 0]
        return {
            "sections": [
                {
                    "key": key,
                    "label": self.interface.label(
                        tasks[0].get("section_label"),
                        self.text("backlog.heading"),
                    ),
                    "tasks": [
                        {
                            "label": self.interface.label(
                                group.get("label"),
                                group["category"],
                            ),
                            "count": self.text(
                                "backlog.count_one"
                                if group["count"] == 1
                                else "backlog.count",
                                count=f"{group['count']:,}",
                            ),
                            "image": (
                                "assets/icons/maintenance/"
                                + _MAINTENANCE_ICONS[group["key"]]
                                if group["key"] in _MAINTENANCE_ICONS
                                else None
                            ),
                            "url": local_url(
                                "/maintenance/",
                                task=group["key"],
                                page=1,
                            ),
                        }
                        for group in tasks
                    ],
                }
                for key, tasks in maintenance_sections(groups).items()
            ],
            "updated": self.source_updated(backlog["observed_at"]),
        }

    def live_status(
        self,
        status: Record,
        config: Record,
        now: dt.datetime,
        *,
        history_page: int = 0,
    ) -> Record:
        """Prepare saved observations and shared live clocks.

        Args:
            status: Validated independent runtime observations.
            config: Current source and configured report definitions.
            now: Actual rendering clock for freshness and durations.
            history_page: History page, or zero for report cards only.

        Returns:
            Localized status contexts without reading runtime files.
        """
        context = status_context(
            self.interface,
            status,
            config.get("version") or self.text("ops.unavailable"),
            now,
            built_at=project_build_time(),
            history_limit=0,
        )
        context.update(
            report_context(
                self.interface,
                status,
                (config.get("routines") or routine_configuration())[
                    "routines"
                ],
                now,
                history_page=history_page,
            ),
        )
        context["clock"] = self.clock(
            {"expires_at": context.pop("clock_expires")},
            now,
        )
        return context

    def worklist_card(
        self,
        item: Record,
        groups: Record,
        task: str = "",
    ) -> Record:
        """Prepare exact issue sources and the earliest observed month.

        Returns:
            An article summary with localized issue links and dates.
        """
        card = self.article(item)
        if item.get("consistency"):
            card.update(
                consistency=finding_details(item, self.interface),
            )
            return card
        month = item.get("oldest_issue_month")
        card.update(
            issues=[
                wiki_link(
                    groups[key]["source_url"],
                    self.interface.label(
                        groups[key].get("label"),
                        groups[key]["category"],
                    ),
                )
                for key in item.get("issues", [])
                if key in groups and key != task
            ],
            month={
                "value": month,
                "label": self.interface.date(month, month_only=True),
                "url": title_url("Category:" + item["oldest_issue_category"])
                if item.get("oldest_issue_category")
                else None,
            }
            if month
            else None,
        )
        return card


def render_dashboard(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
    *,
    navigation: Record | None = None,
) -> str:
    """Render the dashboard from independent saved observations.

    Args:
        snapshot: Validated website snapshot, or unavailable data.
        status: Validated routine, schedule, and page observations.
        config: Current source version and routine definitions.
        locale: Negotiated interface language.
        now: Optional aware clock for deterministic elapsed time.
        navigation: Request route and language choices, when available.

    Returns:
        Complete escaped HTML document, including unavailable states.
    """
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    page = _Page(locale)
    context = page.context(page.text("page.heading"), live=True)
    context["navigation"] = navigation
    context.update(
        page.live_status(
            status or {},
            config or {},
            now,
        ),
    )
    context.update(
        hide_assessment_chips=True,
        notice=page.text("notice.preparing")
        if snapshot is None
        else page.text("notice.partial")
        if snapshot.get("errors")
        else None,
        sections=page.highlights(snapshot or {}, now),
        reviews=page.reviews(snapshot or {}, now),
        discussions=page.reviews(snapshot or {}, now, discussion=True, days=7),
        discussions_url=local_url("/discussions/", lang=locale),
    )
    return _render("dashboard.html", context)


def render_discussions(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
    *,
    navigation: Record | None = None,
) -> str:
    """Render four weeks of general discussions in exclusive groups.

    Args:
        snapshot: Validated saved dashboard data, or unavailable data.
        status: Saved runtime observations for the shared page footer.
        config: Current routine configuration.
        locale: Negotiated interface language.
        now: Optional aware clock for deterministic activity windows.
        navigation: Current route and localized language links.

    Returns:
        Complete HTML with four article age groups and four nonarticle
        groups ordered by newest comment with stable identity ties.
        Each article group spans seven days, through the inclusive
        twenty-eight-day boundary. Missing saved observations remain
        distinct from confirmed empty groups.
    """
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    page = _Page(locale)
    context = page.context(page.text("discussions.heading"), live=True)
    context.update(page.live_status(status or {}, config or {}, now))
    data = (snapshot or {}).get("reviews")
    items = (data or {}).get("discussions")
    groups = []
    if items is not None:
        selected = _discussion_selection(
            items,
            now,
            days=28,
            reviews=data.get("items", []),
        )
        indices = dict(
            selected
            .group_by("group", maintain_order=True)
            .agg("index")
            .iter_rows(),
        )
        for key in _DISCUSSION_GROUPS:
            section = page.reviews(
                {
                    **(snapshot or {}),
                    "reviews": {
                        "discussions": [
                            items[index] for index in indices.get(key, [])
                        ],
                    },
                },
                now,
                discussion=True,
            ) or {"cards": [], "empty": page.text("empty.period")}
            groups.append({
                **section,
                "key": f"discussion-{key}",
                "is_discussion": True,
                "is_review": True,
                "title": page.text(f"discussions.{key.replace('-', '_')}"),
            })
    context.update(
        navigation=navigation,
        hide_assessment_chips=True,
        groups=groups,
        unavailable=page.text("empty.section") if items is None else None,
        updated=page.source_updated(
            (snapshot or {})
            .get("reports", {})
            .get("reviews", {})
            .get("last_success_at"),
        ),
    )
    return _render("discussions.html", context)


def render_quality_analysis(  # ruff: ignore[too-many-arguments] - Shared page-renderer contract.
    data: Record,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
    *,
    navigation: Record | None = None,
) -> str:
    """Render precomputed article metrics and complete native tables.

    Args:
        data: Saved current article analysis and authorship variants.
        status: Saved runtime observations for the shared footer.
        config: Current version and routine definitions.
        locale: Negotiated interface language.
        now: Optional aware clock for deterministic status display.
        navigation: Current route and localized language links.

    Returns:
        Escaped charts, measurement coverage and all article rows.
    """
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    page = _Page(locale)
    context = page.context(page.text("quality_analysis.heading"), live=True)
    context.update(page.live_status(status or {}, config or {}, now))
    analysis = data.get("analysis", data)
    analysis = {
        **analysis,
        "include_b": bool(data.get("include_b", analysis.get("include_b"))),
    }
    authorship = {
        **data.get("authorship", {}),
        "include_b": bool(analysis.get("include_b")),
    }
    selection = analysis.get("selection")
    if selection:
        defaults = selection["defaults"][
            2 if analysis.get("all_grades") else int(analysis["include_b"])
        ]
        analysis = {
            **analysis,
            **defaults,
            "coverage": defaults["coverage"][0],
            "years": [
                {"year": row["year"], "pages": row["pages"], "grades": row}
                for row in defaults["years"]
            ],
            "articles": selection["articles"],
        }
        authorship = {**authorship, "authors": selection["authors"]}
    context.update(quality_analysis_context(analysis, page.interface))
    context.update(
        quality_authorship_context(authorship, page.interface, now),
    )
    context["navigation"] = navigation
    return _render("quality_analysis.html", context)


def render_quality_authorship(  # ruff: ignore[too-many-arguments]
    data: Record,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
    *,
    navigation: Record | None = None,
) -> str:
    """Render estimated quality authors with saved article evidence.

    Args:
        data: Saved authorship and last-public-edit observations.
        status: Saved runtime observations for the shared footer.
        config: Current source version and routine definitions.
        locale: Negotiated interface language.
        now: Optional aware clock for deterministic elapsed labels.
        navigation: Current route and localized language links.

    Returns:
        Escaped HTML with fractional credits and article detail dialogs.
    """
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    page = _Page(locale)
    context = page.context(page.text("quality_authorship.heading"), live=True)
    context.update(page.live_status(status or {}, config or {}, now))
    context.update(quality_authorship_context(data, page.interface, now))
    context["navigation"] = navigation
    return _render("quality_authorship.html", context)


def render_live_status(
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
) -> str:
    """Render status fragments without loading website datasets.

    Args:
        status: Validated routine, schedule, and page observations.
        config: Current source version and routine definitions.
        locale: The language of the existing page.
        now: Optional aware server clock for deterministic elapsed time.

    Returns:
        Escaped fragments with the same identities as the full page.
    """
    page = _Page(locale)
    context = {"t": _messages(locale)}
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    return _render("live.html", context)


def render_maintenance_overview(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
    *,
    navigation: Record | None = None,
) -> str:
    """Render saved maintenance categories on their own overview page.

    Args:
        snapshot: Validated website snapshot containing the catalogue.
        status: Validated routine, schedule, and page observations.
        config: Current source version and routine definitions.
        locale: Negotiated interface language.
        now: Optional aware clock for deterministic elapsed time.
        navigation: Request route and language choices, when available.

    Returns:
        Complete escaped HTML with category links and source timestamps,
        preserving unavailable and confirmed-empty observations.
    """
    page = _Page(locale)
    context = page.context(page.text("backlog.heading"), live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    backlog = (snapshot or {}).get("backlog")
    context.update(
        navigation=navigation,
        backlog=page.backlog(backlog),
        updated=page.source_updated(backlog["observed_at"])
        if backlog is not None
        else None,
    )
    return _render("backlog.html", context)


def render_consistency_checks(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    status: Record | None,
    config: Record | None,
    locale: str,
    *,
    navigation: Record | None = None,
    parameters: dict[str, list[str]] | None = None,
) -> str:
    """Render independent maintenance checks grouped by issue type.

    Args:
        snapshot: Validated saved website observations, when available.
        status: Saved runtime observations for the shared footer.
        config: Application version and routine configuration.
        locale: Negotiated interface language.
        navigation: Request route and language choices.
        parameters: Optional native page selection for homepage history.

    Returns:
        Escaped issue lists with evidence links, coverage and update
        time, independent of maintenance-category counts.

    Raises:
        ValueError: The page selection is duplicated or malformed.
    """
    parameters = parameters or {}
    if set(parameters) - {"page"}:
        message = "Invalid consistency page query."
        raise ValueError(message)
    selection = maintenance_selection({
        "task": ["consistency_history"],
        **parameters,
    })
    page = _Page(locale)
    context = page.context(page.text("consistency.heading"), live=True)
    context.update(
        page.live_status(status or {}, config or {}, dt.datetime.now(dt.UTC)),
    )
    coverage = coverage_status(snapshot, page.interface)
    coverage["updated"] = page.source_updated(coverage.get("observed_at"))
    sections = []
    for group in consistency_groups(snapshot, page.interface):
        if not group["items"]:
            continue
        portion = maintenance_page(
            group["items"],
            selection["page"] if group["ordered"] else "1",
            _CONSISTENCY_HISTORY_PREVIEW
            if group["ordered"]
            else len(group["items"]),
        )
        sections.append({
            "key": group["key"],
            "label": group["label"],
            "ordered": group["ordered"],
            "total_count": group["count"] if group["ordered"] else None,
            "offset": portion["offset"],
            "pagination": {
                "label": page.text(
                    "backlog.range",
                    start=portion["offset"] + 1,
                    end=portion["offset"] + len(portion["items"]),
                    count=f"{group['count']:,}",
                ),
                **{
                    name: local_url(
                        "/checks/",
                        page=number,
                        lang=page.interface.locale,
                    )
                    + "#"
                    + group["key"]
                    if available
                    else None
                    for name, number, available in (
                        (
                            "previous",
                            portion["page"] - 1,
                            portion["page"] > 1,
                        ),
                        (
                            "next",
                            portion["page"] + 1,
                            portion["page"] < portion["pages"],
                        ),
                    )
                },
            }
            if group["ordered"] and portion["pages"] > 1
            else None,
            "findings": [
                finding
                for item in portion["items"]
                for finding in finding_details(item, page.interface)
            ],
        })
    context.update(
        navigation=navigation,
        consistency_status=coverage,
        consistency_sections=sections,
    )
    return _render("consistency.html", context)


def render_redirects(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    parameters: dict[str, list[str]],
    locale: str,
    *,
    navigation: Record | None = None,
    status: Record | None = None,
    config: Record | None = None,
    unavailable: bool = False,
) -> str:
    """Present redirect findings with exact source revisions.

    Args:
        snapshot: Validated saved scanner observations, when available.
        parameters: Native page and page-size selections.
        locale: Negotiated interface language.
        navigation: Request route and language choices.
        status: Saved service observations for the shared footer.
        config: Application version and routine configuration.
        unavailable: Saved data could not be read or validated.

    Returns:
        Escaped findings and explicit bounded-history scan coverage.
    """
    page = _Page(locale)
    context = page.context(page.text("redirects.heading"), live=True)
    context.update(
        page.live_status(status or {}, config or {}, dt.datetime.now(dt.UTC)),
    )
    selection = redirect_selection(snapshot, parameters, locale)
    state = (
        "unavailable"
        if unavailable
        else (snapshot or {}).get(
            "status",
            "missing",
        )
    )
    context.update(
        navigation=navigation,
        redirects=selection,
        redirect_scan=snapshot,
        redirect_state=page.text(f"redirects.status.{state}"),
        redirect_updated=_time((snapshot or {}).get("updated_at")),
        redirect_range=page.text(
            "redirects.range",
            first=selection["first"],
            last=selection["last"],
            total=selection["total"],
        ),
        redirect_progress=page.text(
            "redirects.progress",
            checked=(snapshot or {}).get("checked_count", 0),
            discovered=(snapshot or {}).get("discovered_count", 0),
        ),
        redirect_identity=sha256(
            json.dumps(snapshot, sort_keys=True).encode(),
        ).hexdigest(),
    )
    return _render("redirects.html", context)


def render_maintenance(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    params: Mapping[str, list[str]],
    locale: str,
    *,
    navigation: Record | None = None,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Render a maintenance list after sorting all saved observations.

    Args:
        snapshot: Validated website snapshot containing the catalogue.
        params: Parsed controls with repeated values retained.
        locale: Negotiated interface language.
        navigation: Request-specific language links and active location.
        status: Saved service state for the shared uptime footer.
        config: Source version and routine definitions.
        now: Optional clock for deterministic uptime.

    Returns:
        Complete escaped HTML with stable sorting and pagination links.

    Raises:
        LookupError: The selected maintenance task does not exist.
        RuntimeError: Maintenance observations are unavailable.

    Invalid controls propagate ValueError from maintenance_selection.
    """
    selection = maintenance_selection(params)
    page = _Page(locale)
    consistency = selection["task"].startswith("consistency_")
    worklist_path = "/checks/" if consistency else "/maintenance/"
    backlog = (snapshot or {}).get("consistency" if consistency else "backlog")
    if backlog is None:
        message = "Maintenance data is unavailable."
        raise RuntimeError(message)
    groups = {
        group["key"]: group
        for group in (
            consistency_groups(snapshot, page.interface)
            if consistency
            else backlog["groups"]
        )
    }
    if selection["task"] not in groups:
        message = "Maintenance task not found."
        raise LookupError(message)
    if selection["sort"] == "random" and "seed" not in selection:
        selection["seed"] = secrets.token_hex(8)
    selected = groups[selection["task"]]
    items = maintenance_items(
        maintenance_filter(selected["items"], selection),
        selection["sort"],
        selection["order"],
        selection.get("seed", ""),
    )
    if selection["task"] == "consistency_history":
        items = latest_history_first(items)
    portion = maintenance_page(
        items,
        selection["page"],
        int(selection.get("limit", "10")),
    )
    if navigation:
        navigation = {
            **navigation,
            "languages": [
                {
                    **language,
                    "url": local_url(
                        worklist_path,
                        **selection,
                        lang=language["tag"].lower(),
                    ),
                }
                for language in navigation["languages"]
            ],
        }
    heading = page.interface.label(selected.get("label"), selected["category"])
    context = page.context(
        page.text("nav.checks" if consistency else "nav.backlog"),
        live=True,
    )
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    context.update(
        navigation=navigation,
        worklist_path=worklist_path,
        selection=selection,
        consistency=selected.get("consistency", False),
        consistency_ordered=selected.get("ordered", False),
        consistency_status=coverage_status(snapshot, page.interface),
        category_heading=heading,
        task_choices=[
            {
                "url": local_url(
                    worklist_path,
                    **{**selection, "task": key, "page": "1"},
                ),
                "label": page.interface.label(
                    group.get("label"),
                    group["category"],
                ),
                "current": key == selection["task"],
            }
            for key, group in groups.items()
        ],
        clear_url=local_url(
            worklist_path,
            task=selection["task"],
        ),
        range_label=page.text(
            "consistency.page_range"
            if selected.get("target_type") == "project"
            else "backlog.range",
            start=portion["offset"] + 1 if items else 0,
            end=portion["offset"] + len(portion["items"]),
            count=f"{len(items):,}",
        ),
        updated=page.source_updated(
            selected.get("observed_at") or backlog.get("observed_at"),
        ),
        sort_choices=[
            {
                "label": page.text(
                    "consistency.observed"
                    if sort == "oldest" and selected.get("consistency")
                    else "consistency.page_title"
                    if sort == "title"
                    and selected.get("target_type") == "project"
                    else key,
                ),
                "value": sort,
                "current": selection["order"]
                if sort == selection["sort"]
                else None,
                "order": ("desc" if selection["order"] == "asc" else "asc")
                if sort == selection["sort"]
                else "desc"
                if sort in {"class", "importance"}
                else "asc",
            }
            for sort, key in (
                ("title", "backlog.sort.title"),
                ("class", "backlog.sort.class"),
                ("importance", "backlog.column.importance"),
                ("oldest", "backlog.sort.time"),
            )
            if selected.get("target_type") != "project"
            or sort not in {"class", "importance"}
        ],
        cards=[
            page.worklist_card(item, groups, selection["task"])
            for item in portion["items"]
        ],
        start=portion["offset"] + 1,
        first=local_url(
            worklist_path,
            **{**selection, "page": 1},
        )
        if portion["page"] > 1
        else None,
        previous=local_url(
            worklist_path,
            **{**selection, "page": portion["page"] - 1},
        )
        if portion["page"] > 1
        else None,
        following=local_url(
            worklist_path,
            **{**selection, "page": portion["page"] + 1},
        )
        if portion["page"] < portion["pages"]
        else None,
        last=local_url(
            worklist_path,
            **{**selection, "page": portion["pages"]},
        )
        if portion["page"] < portion["pages"]
        else None,
        source_link=wiki_link(selected["source_url"], selected["category"]),
    )
    if consistency:
        context["consistency_findings"] = [
            finding
            for card in context["cards"]
            for finding in card["consistency"]
        ]
        context["consistency_status"]["updated"] = context["updated"]
    return _render("maintenance.html", context)


def _newsletter_edit_url(title: str, data_through: str | None) -> str:
    """Link to the wiki editor with dated Chinese source attribution.

    Args:
        title: Newsletter page receiving the reviewed draft.
        data_through: Inclusive date of the draft's saved observations.

    Returns:
        Edit-form URL with an attributed summary for manual review.
    """
    day = dt.date.fromisoformat(data_through) if data_through else None
    label = f"{day.year}年{day.month}月{day.day}日资料" if day else "简讯资料"
    query = urlencode({
        "action": "edit",
        "summary": f"[[toolforge:aranami/newsletter/|{label}]]",
    })
    return f"{title_url(title)}?{query}"


def render_newsletter(  # ruff: ignore[too-many-arguments]
    value: Record,
    locale: str,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
    navigation: Record | None = None,
) -> str:
    """Present saved newsletter drafts by issue and data date.

    Args:
        value: Issue, reporting period, drafts, source observations,
            and optional daily draft history.
        locale: Negotiated interface language; wikitext stays Chinese.
        status: Saved monitor observations for the shared footer.
        config: Current application version and routine definitions.
        now: Optional server clock for observed uptime.
        navigation: Request route and language choices, when available.

    Returns:
        Escaped HTML with native issue/date selection and copy controls.
    """
    page = _Page(locale)
    context = page.context(page.text("newsletter.heading"), live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    labels = {
        "主頁": "main",
        "特優內容": "quality",
        "宣告": "announcements",
        "專題": "feature",
        "訪談": "interview",
    }
    automatic = value.get("automatic_issue", value["issue"])
    previous = Issue.parse(str(automatic)).previous
    choices = value.get("issue_choices", [previous, automatic])
    history = value.get("draft_history") or {}
    issue = Issue.parse(str(value["issue"]))
    dates = sorted(history.get("dates") or [])
    data_through = history.get("selected_date") or value.get("data_through")
    context.update(
        introduction=escape(page.text("newsletter.intro")).format(
            newsletter=wiki_link(
                title_url("WikiProject:电子游戏/简讯"),
                page.text("newsletter.name"),
            ),
        ),
        navigation=navigation,
        available=value["available"],
        locale=page.interface.locale,
        selected_issue=value["issue"],
        archived_issue=value["issue"] not in choices,
        draft_history={
            "dates": dates,
            "selected": history.get("selected_date"),
            "form_date": history.get("selected_date")
            or (dates[-1] if dates else ""),
            "minimum": f"{issue.year - 1}-01-01"
            if issue.month == 1
            else issue.start.isoformat(),
            "maximum": (issue.end - dt.timedelta(days=1)).isoformat(),
            "generated": _time(history.get("generated_at"), seconds=True),
            "missing": history.get("missing", False),
        },
        issue_choices=[
            {
                "value": choice,
                "checked": choice == value["issue"],
                "label": page.text(
                    "newsletter.choice.current"
                    if choice == automatic
                    else "newsletter.choice.previous"
                    if choice == previous
                    else "newsletter.choice.annual"
                    if str(choice).endswith("-01") and choice != value["issue"]
                    else "newsletter.choice.selected",
                ),
            }
            for choice in choices
        ],
        drafts=[
            {
                **draft,
                "heading": page.text(
                    "newsletter.page."
                    + labels[draft["label"].lstrip("/") or "主頁"],
                ),
                "edit_url": _newsletter_edit_url(
                    draft["title"],
                    draft.get("data_through") or data_through,
                ),
            }
            for draft in value["pages"]
            if value["available"]
        ],
    )
    return _render("newsletter.html", context)


def render_about(
    config: Record | None,
    locale: str,
    *,
    status: Record | None = None,
    now: dt.datetime | None = None,
    navigation: Record | None = None,
) -> str:
    """Explain the tool and credit its sources and artwork.

    Args:
        config: Current application version and routine configuration.
        locale: Negotiated interface language.
        status: Validated saved monitor and resource observations.
        now: Optional clock for deterministic freshness checks.
        navigation: Request route and language choices, when available.

    Returns:
        An informative About page with a live footer uptime.
    """
    page = _Page(locale)
    context = page.context(page.text("nav.about"), live=True)
    context["navigation"] = navigation
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    context.update(
        introduction=escape(page.text("about.intro")).format(
            name="荒波",
            wikipedia=wiki_link(
                title_url("中文维基百科"),
                page.text("about.wikipedia"),
            ),
            project=wiki_link(
                title_url("WikiProject:电子游戏"),
                page.text("about.project"),
            ),
        ),
        contact=escape(page.text("about.contact")).format(
            source=Markup(
                '<a href="https://github.com/For-Each-Next/aranami-old">GitHub</a>',
            ),
            user=wiki_link(
                "https://zh.wikipedia.org/wiki/User_talk:For_Each_..._Next",
                "For Each ... Next",
            ),
        ),
    )
    return _render("about.html", context)


def render_attribution(  # ruff: ignore[too-many-arguments]
    path: str,
    locale: str = "zh-hant",
    navigation: Record | None = None,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Present credits and unchanged licenses in the shared page shell.

    Args:
        path: One established public attribution or license URL.
        locale: Negotiated interface language for navigation and labels.
        navigation: Request route and language choices, when available.
        status: Saved service observations for the shared uptime footer.
        config: Application version and routine configuration.
        now: Optional clock for deterministic freshness checks.

    Returns:
        Complete HTML with original attribution and license wording.

    Raises:
        LookupError: The URL is not an authored attribution document.
    """
    documents = {
        "/assets/icons/credits.html": "content",
        "/assets/icons/maintenance/credits.html": "maintenance",
        "/codex-license.html": "codex",
        "/assets/icons/gpl-2.0.html": "gpl",
        "/assets/icons/lgpl-2.1.html": "lgpl",
    }
    if path not in documents:
        message = "Attribution document not found."
        raise LookupError(message)
    document = documents[path]
    page = _Page(locale)
    context = page.context(page.text(f"credits.title.{document}"), live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    context.update(
        navigation=navigation,
        document=document,
        document_template=f"attribution/{document}.html",
    )
    return _render("attribution.html", context)


def render_dataset(
    data: Record | None,
    locale: str,
    *,
    navigation: Record | None = None,
    status: Record | None = None,
    config: Record | None = None,
) -> str:
    """Present original snapshot metadata and a bounded member preview.

    Args:
        data: Validated detail facts, or none for selection help.
        locale: Selected interface language.
        navigation: Current route and language choices.
        status: Saved observations for the common page footer.
        config: Public source version and routine configuration.

    Returns:
        Escaped metadata and up to fifty rows without source requests.
    """
    page = _Page(locale)
    context = page.context(page.text("data.heading"), live=True)
    context.update(
        page.live_status(status or {}, config or {}, dt.datetime.now(dt.UTC)),
    )
    value = None
    if data is not None:
        manifest, storage = data.get("manifest", {}), data["storage"] or {}
        metadata = manifest.get("metadata", {})
        fields = [
            ("file", data["file"]),
            ("contract", storage.get("contract")),
            (
                "written_at",
                utc_time(storage["written_at"], seconds=True)
                if storage.get("written_at")
                else None,
            ),
            ("schema", manifest.get("schema", storage.get("schema"))),
            (
                "generation",
                manifest.get("generation", storage.get("generation")),
            ),
            ("row_count", f"{data['row_count']:,}"),
        ]
        if manifest:
            fields.extend([
                ("wiki", data["wiki"]),
                ("project", data["project"]),
                (
                    "snapshot_date",
                    page.interface.date(metadata["snapshot_date"]),
                ),
                (
                    "boundary_date",
                    page.interface.date(metadata["boundary_date"]),
                ),
                ("started_at", utc_time(manifest["started_at"], seconds=True)),
                (
                    "finished_at",
                    utc_time(manifest["finished_at"], seconds=True),
                ),
                ("application_version", manifest.get("application_version")),
                ("sha256", manifest["sha256"]),
            ])
        if data.get("size_bytes") is not None:
            fields.append(("size_bytes", f"{data['size_bytes']:,}"))
        current = data.get("page", 1)
        page_count = data.get("page_count", 1)
        offset = (current - 1) * data["preview_limit"]
        value = {
            **data,
            "facts": [
                {"label": page.text(f"data.field.{key}"), "value": item}
                for key, item in fields
            ],
            "preview_note": page.text(
                "data.preview_note",
                start=f"{offset + 1 if data['rows'] else 0:,}",
                end=f"{offset + len(data['rows']):,}",
                total=f"{data['row_count']:,}",
            ),
            "page": current,
            "page_count": page_count,
            "previous": local_url(
                "/data/",
                file=data["file"],
                page=current - 1,
                lang=locale,
            )
            if current > 1
            else None,
            "next": local_url(
                "/data/",
                file=data["file"],
                page=current + 1,
                lang=locale,
            )
            if current < page_count
            else None,
            "cells": [
                [
                    {
                        "value": json.dumps(
                            item,
                            ensure_ascii=False,
                            default=str,
                        )
                        if isinstance(item, list | dict)
                        else item,
                    }
                    for item in (row[name] for name in data["columns"])
                ]
                for row in data["rows"]
            ],
        }
    context.update(navigation=navigation, dataset=value)
    return _render("dataset.html", context)


def render_logs(  # ruff: ignore[too-many-arguments]
    data: Record,
    locale: str,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
    navigation: Record | None = None,
) -> str:
    """Render log archive links for online reading and activity filters.

    Args:
        data: Validated daily-file listing and selection.
        locale: Negotiated interface language.
        status: Saved monitor observations for the shared footer.
        config: Current source version and routine definitions.
        now: Optional clock for deterministic uptime.
        navigation: Request route and language choices.

    Returns:
        A file archive without interpreting diagnostic text.
    """
    page = _Page(locale)
    context = page.context(page.text("logs.heading"), live=True)
    context.update(navigation=navigation)
    context.update(_log_archive(data, page.interface))
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    return _render("logs.html", context)


def render_reports(  # ruff: ignore[too-many-arguments]
    data: Record,
    locale: str,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
    navigation: Record | None = None,
    history_page: int = 1,
) -> str:
    """Render saved update status and per-invocation report history.

    Args:
        data: Validated independent invocations.
        locale: Negotiated interface language.
        status: Validated routine and scheduler observations.
        config: Current source version and routine definitions.
        now: Optional clock for deterministic freshness checks.
        navigation: Request route and language choices.
        history_page: One-based page of ten execution records.

    Returns:
        A status panel and recent invocation history with stage dialogs.
    """
    page = _Page(locale)
    context = page.context(page.text("reports.heading"), live=True)
    context.update(navigation=navigation)
    context.update(
        page.live_status(
            status or data,
            config or {},
            now or dt.datetime.now(dt.UTC),
            history_page=max(1, history_page),
        ),
    )
    events = context["events"]
    count = context["history_total"]
    last = max(1, (count + 9) // 10)
    current = context["history_offset"] // 10 + 1
    context.update(
        history_dialogs=events,
        history_pagination={
            "page": current,
            "total": count,
            "url": local_url("/reports/", lang=locale),
            "label": f"{current} / {last}",
            "previous": local_url("/reports/", page=current - 1, lang=locale)
            + "#recent-events"
            if current > 1
            else None,
            "next": local_url("/reports/", page=current + 1, lang=locale)
            + "#recent-events"
            if current < last
            else None,
        },
    )
    return _render("reports.html", context)


def _log_archive(data: Record, interface: Interface) -> Record:
    """Prepare structured activity and safe per-segment viewing links.

    Returns:
        Validated selection, escaped event fields, and file metadata.
    """
    files = data.get("files", [])
    selected_date = data.get("date")
    return {
        "files": [
            {
                **row,
                "size_label": readable_size(row["size"]),
                "stream_label": interface.text(f"logs.stream_{row['stream']}"),
                "view_url": local_url(
                    "/logs/",
                    date=row["date"],
                    stream=row["stream"],
                    file=row["name"],
                ),
            }
            for row in files
        ],
        "selected_date": selected_date or "",
        "query": data.get("query", ""),
        "run_id": data.get("run_id") or "",
        "log_availability": data.get("availability", "missing"),
        "log_truncated": data.get("truncated", False),
        "activity": [
            {
                "time": _time(event["time"], seconds=True),
                "kind": interface.text(f"logs.kind.{event['kind']}")
                if f"logs.kind.{event['kind']}" in catalog()
                else event["kind"],
                "level": event["level"],
                "fields": [
                    (key, value)
                    for key, value in event.items()
                    if key not in {"schema", "time", "level"}
                    and value is not None
                ],
            }
            for event in data.get("events", [])
        ],
    }


def render_error(message_key: str, locale: str) -> str:
    """Show a localized failure with working site navigation.

    Returns:
        An escaped HTML document with a localized alert.
    """
    page = _Page(locale)
    context = page.context(page.text("page.heading"))
    context["message"] = page.text(message_key)
    return _render("error.html", context)
