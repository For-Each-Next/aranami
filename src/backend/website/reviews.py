"""Collect current video-game nominations and talk-page review requests.

Community candidates come from proposal headings or DYKEntry, regardless
of talk-page nomination banners. Project categories identify ACC, BPAN,
PPR, and reassessment requests. Dates come only from the corresponding
discussion or explicit DYK nomination timestamp. Reply times require a
signed comment from someone other than the nominator. Counts include
distinct identified signatories and explicit nominators.

Contracts checked on 25 October 2026: Module:PJ:VG/RVW (92100885), FAC
proposals (95249932), FLC proposals (95117299), Preload FLN/auto
(92992079), GAN proposals (95257581), PR proposals (95231065), DYK
candidates (95258998), Module:DYKEntry (94743983), and WikiProject
Video games (92848131). DYKEntry's nominator parameter follows its
documentation (62936050), checked on 31 October 2026.
Talk:豐川祥子 (93157031) places the opening in a nested section and an
unrelated DYKEntry/archive discussion after the project review.
See https://zh.wikipedia.org/w/index.php?oldid=REVISION.
"""

from __future__ import annotations

import datetime as dt
import operator
import re
from contextlib import suppress
from functools import partial
from html import unescape
from typing import TYPE_CHECKING
from urllib.parse import quote

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Heading, Tag, Template, Text, Wikilink
from pywikibot import Page

from backend.services.zhwiki.article_milestones import (
    explicit_account,
    explicit_question,
)
from backend.services.zhwiki.assessment_lists import review_heading
from backend.website.review_cache import ITEM_SCHEMA, ReviewCache
from shared.sources.quarry.projects import category_members

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

REVIEW_TYPES = (
    "fac",
    "flc",
    "acc",
    "gan",
    "bpan",
    "pr",
    "ppr",
    "dykc",
    "reassess",
)
COMMUNITY_SOURCES = {
    "fac": "Wikipedia:典范条目评选/提名区",
    "flc": "Wikipedia:特色列表评选/提名区",
    "gan": "Wikipedia:優良條目評選/提名區",
    "pr": "Wikipedia:同行评审/提案区",
    "dykc": "Wikipedia:新条目推荐/候选",
}
PROJECT_CATEGORIES = {
    "acc": "Category:请求甲级评审的电子游戏条目",
    "bpan": "Category:请求乙上级评审的电子游戏条目",
    "ppr": "Category:请求专题同侪评审的电子游戏条目",
    "reassess": "Category:请求重新评级的电子游戏条目",
}
_SIGNATURE = re.compile(
    r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*"
    r"(?:\([^()]*\)\s*)?(\d{1,2}):(\d{2})\s*\(UTC\)",
)
_PERIOD = re.compile(
    r"(?:投票期|[評评][審审]期|(?:基[礎础])?[評评][選选]期)[\uff1a:]\s*",
)
_PROJECT_HEADINGS = {
    "ppr": r"(?:[專专][題题]|同[儕侪行]).*[評评][審审]|peer review",
    "reassess": r"重新[評评][級级估]|重[評评]|reassessment",
}
_USER_NAMESPACES = {
    "u",
    "ut",
    "user",
    "user talk",
    "用户",
    "用户讨论",
    "用戶",
    "用戶討論",
    "使用者",
    "使用者討論",
    "使用者讨论",
}
_CONTRIBUTIONS = {
    "special:contributions",
    "special:contribs",
    "特殊:用户贡献",
    "特殊:用戶貢獻",
    "特殊:使用者貢獻",
    "特殊:使用者贡献",
}
_QUOTED_TAGS = {
    "blockquote",
    "q",
    "nowiki",
    "pre",
    "source",
    "syntaxhighlight",
    "ref",
    "gallery",
    "math",
    "code",
}
_DYK_DATE_LEVEL = 3
_NOMINATOR = re.compile(r"提名[人者]\s*[:\uff1a]")


def _url(title: str, heading: str | None = None) -> str:
    """Encode the actual source heading independently of the page title.

    Returns:
        A public article or discussion-section URL.
    """
    base = "https://zh.wikipedia.org/wiki/" + quote(
        title.replace(" ", "_"),
        safe="/:",
    )
    return base + (
        "#" + quote(heading.replace(" ", "_"), safe="") if heading else ""
    )


def _plain_heading(section: Wikicode) -> str:
    """Return a rendered section heading for a discussion link."""
    return (
        section.filter_headings(recursive=False)[0].title.strip_code().strip()
    )


def _started(section: Wikicode) -> str | None:
    """Read the explicit review period, then the opening UTC signature.

    Returns:
        An aware UTC instant, or none without a complete timestamp.
        Comments, quoted templates, and nested subsections cannot supply
        a different discussion's start date.
    """
    lead = section.get_sections(
        include_lead=False,
        include_headings=False,
        flat=True,
    )[0]
    text = lead.strip_code()
    period = _PERIOD.search(text)
    match = _SIGNATURE.search(text, period.end() if period else 0)
    if match is None:
        return None
    return _timestamp(match)


def _timestamp(match: re.Match[str]) -> str | None:
    """Return a valid UTC instant, retaining malformed unknowns."""
    try:
        return dt.datetime(
            *(int(part) for part in match.groups()),
            tzinfo=dt.UTC,
        ).isoformat(timespec="seconds")
    except ValueError:
        return None


def _discussion_text(code: Wikicode) -> str:
    """Keep signature links outside quotes, templates, and comments.

    Returns:
        Text without inline HTML, keeping signature dates and links
        adjacent without reading quoted examples.
    """
    parts = []
    archived_dyk = False
    for node in code.nodes:
        if isinstance(node, Template):
            name = str(node.name).strip().removeprefix("Template:").casefold()
            if name == "dykentry/archive":
                archived_dyk = True
            elif name == "dykvotef":
                archived_dyk = False
            continue
        if archived_dyk:
            continue
        if isinstance(node, Text | Wikilink):
            parts.append(str(node))
        elif isinstance(node, Tag):
            if str(node.tag).strip().casefold() not in _QUOTED_TAGS:
                parts.append(
                    _discussion_text(node.contents) if node.contents else "",
                )
        elif isinstance(node, Heading):
            parts.append("\n")
    return "".join(parts)


def _username(value: str) -> str:
    """Normalize wiki username spacing and its first letter.

    Returns:
        An account name with spaces and an uppercase first letter.
    """
    value = unescape(value).replace("_", " ").strip()
    return value[:1].upper() + value[1:]


def _signature_user(text: str) -> str | None:
    """Read the nearest signature's user, talk, or contributions link.

    Returns:
        The account identity from the timestamp's own line, or none when
        its author is unknown. Display labels do not identify users.
    """
    links = mwparserfromhell.parse(text.rsplit("\n", 1)[-1]).filter_wikilinks()
    for link in reversed(links):
        target = str(link.title).strip().lstrip(":").split("#", 1)[0]
        namespace, separator, name = target.partition(":")
        if (
            separator
            and namespace.replace("_", " ").casefold() in _USER_NAMESPACES
        ):
            return _username(name.split("/", 1)[0]) or None
        prefix, separator, name = target.partition("/")
        if separator and prefix.replace("_", " ").casefold() in _CONTRIBUTIONS:
            return _username(name) or None
    return None


def _signed_comments(code: Wikicode) -> list[tuple[str, str]]:
    """Read signature authors and valid UTC timestamps in source order.

    Returns:
        Signature pairs, ignoring unidentifiable authors, invalid dates,
        and date ranges without a user signature on that line.
    """
    text = _discussion_text(code)
    comments = []
    previous = 0
    for match in _SIGNATURE.finditer(text):
        user = _signature_user(text[previous : match.start()])
        timestamp = _timestamp(match)
        if user is not None and timestamp is not None:
            comments.append((user, timestamp))
        previous = match.end()
    return comments


def _opening_nominator(code: Wikicode, started: str) -> str | None:
    """Identify the opening signatory from nomination evidence.

    Returns:
        The first opening author at or before the nomination start. An
        explicit nominator label permits a slightly later signature.
    """
    text = _discussion_text(code)
    for match in _SIGNATURE.finditer(text):
        prefix = text[: match.start()].rsplit("\n", 1)[-1]
        user, timestamp = _signature_user(prefix), _timestamp(match)
        if user is not None and timestamp is not None:
            return (
                user
                if timestamp <= started or _NOMINATOR.search(prefix)
                else None
            )
    return None


def _activity(
    code: Wikicode,
    started: str | None,
    *,
    nominator: str | None = None,
) -> dict[str, object]:
    """Count participants and find the latest reply.

    Args:
        code: Only this nomination's discussion, including subsections.
        started: Observed nomination start, or none when undated.
        nominator: DYK's explicit account, otherwise the opening author.

    Returns:
        Nullable participant count and response time. Count each known
        author and explicit nominator once. Replies require a known
        nominator and someone else's signature after the start. Without
        identified participants, the count stays unknown.
    """
    comments = _signed_comments(code)
    participants = {user for user, _ in comments}
    if nominator is None and started is not None:
        lead = code.get_sections(
            include_lead=False,
            include_headings=False,
            flat=True,
        )[0]
        nominator = _opening_nominator(lead, started)
    if nominator:
        nominator = _username(nominator)
        participants.add(nominator)
    replied = (
        max(
            (
                timestamp
                for user, timestamp in comments
                if user != nominator and timestamp >= started
            ),
            default=None,
        )
        if started is not None and nominator
        else None
    )
    return {
        "replied_at": replied,
        "participant_count": len(participants) if participants else None,
        "nominator": nominator,
    }


def _row(
    title: str,
    kind: str,
    target: str,
    started: str | None,
    activity: dict[str, object] | None = None,
) -> dict:
    """Return a review fact with nullable activity observations."""
    activity = activity or {}
    contributors = [
        name
        for value in activity.get("contributors") or []
        if (name := explicit_account(value))
    ]
    return {
        "title": title.replace("_", " ").strip(),
        "review_type": kind,
        "review_url": target,
        "started_at": started,
        "replied_at": activity.get("replied_at"),
        "participant_count": activity.get("participant_count"),
        "nominator": explicit_account(activity.get("nominator") or ""),
        "contributors": contributors or None,
        "question": explicit_question(activity.get("question") or ""),
    }


def community_reviews(text: str, kind: str) -> list[dict[str, object]]:
    """Extract primary nominees from the current community source.

    Args:
        text: Current proposal page, with no archive expansion.
        kind: FAC, FLC, GAN, PR, or DYKC in lowercase.

    Returns:
        Nominee titles, actual discussion links, and observed starts.
        Closed DYK results are excluded even while awaiting removal.
    """
    code = mwparserfromhell.parse(text)
    if kind == "dykc":
        return _dyk_reviews(code)
    result = []
    for section in code.get_sections(levels=[2], include_lead=False):
        heading = section.filter_headings(recursive=False)[0].title
        links = heading.filter_wikilinks()
        title = str(links[0].title) if links else heading.strip_code()
        started = _started(section)
        result.append(
            _row(
                title.strip().lstrip(":"),
                kind,
                _url(
                    COMMUNITY_SOURCES[kind].rsplit("/", 1)[0],
                    _plain_heading(section),
                ),
                started,
                _activity(section, started),
            ),
        )
    return result


def _dyk_reviews(code: Wikicode) -> list[dict[str, object]]:
    """Read active DYKEntry subjects, timestamps, and article anchors.

    Returns:
        Current candidates, retaining an unknown malformed start date.
    """
    result = []
    for index, template in enumerate(code.nodes):
        if not isinstance(template, Template):
            continue
        if (
            str(template.name).strip().removeprefix("Template:").casefold()
            != "dykentry"
        ):
            continue
        values = {
            str(p.name).strip(): str(p.value).strip() for p in template.params
        }
        title = values.get("article", "")
        if not title or values.get("result") in {"+", "^", "-"}:
            continue
        started = None
        raw = values.get("timestamp", "")
        if re.fullmatch(r"[0-9]{1,12}", raw):
            with suppress(ValueError, OSError, OverflowError):
                started = dt.datetime.fromtimestamp(
                    int(raw),
                    dt.UTC,
                ).isoformat(timespec="seconds")
        result.append(
            _row(
                title,
                "dykc",
                _url(COMMUNITY_SOURCES["dykc"], title),
                started,
                _activity(
                    _dyk_discussion(code, index),
                    started,
                    nominator=_username(values.get("nominator", "")),
                )
                | {
                    "nominator": values.get("nominator") or None,
                    "contributors": [_username(values["author"])]
                    if _username(values.get("author", ""))
                    else None,
                    "question": values.get("question") or None,
                },
            ),
        )
    return result


def _dyk_discussion(code: Wikicode, index: int) -> Wikicode:
    """Bound DYK comments before the next candidate or date.

    Returns:
        Discussion nodes, preserving nested comment sections.
    """
    nodes = []
    for node in code.nodes[index + 1 :]:
        if isinstance(node, Heading) and node.level <= _DYK_DATE_LEVEL:
            break
        if isinstance(node, Template) and (
            str(node.name).strip().removeprefix("Template:").casefold()
            == "dykentry"
        ):
            break
        nodes.append(node)
    return mwparserfromhell.wikicode.Wikicode(nodes)


def talk_review(text: str, kind: str, *, title: str, talk: str) -> list[dict]:
    """Link the relevant discussion, leaving unobserved starts null.

    Args:
        text: Current talk-page wikitext, or empty for a missing page.
        kind: Project review or reassessment type.
        title: Canonical subject title from its talk-page identity.
        talk: Canonical talk-page title using wiki namespace rules.

    Returns:
        One category-backed request, even without a matching discussion.
        The earliest signed comment in the selected section, including
        subsections, establishes its start; newer lead followups do not
        replace that opening. An unsigned lead date remains a fallback.
    """
    heading = (
        review_heading(text, kind) if kind not in _PROJECT_HEADINGS else None
    )
    if heading is not None:
        heading = mwparserfromhell.parse(heading).strip_code().strip()
    sections = mwparserfromhell.parse(text).get_sections(
        levels=[2],
        include_lead=False,
    )
    if kind in _PROJECT_HEADINGS:
        heading = next(
            (
                _plain_heading(section)
                for section in reversed(sections)
                if re.search(
                    _PROJECT_HEADINGS[kind],
                    _plain_heading(section),
                    re.IGNORECASE,
                )
            ),
            None,
        )
    section = next(
        (s for s in reversed(sections) if _plain_heading(s) == heading),
        None,
    )
    comments = _signed_comments(section) if section is not None else []
    opening = min(comments, key=operator.itemgetter(1), default=None)
    started = (
        opening[1]
        if opening is not None
        else _started(section)
        if section is not None
        else None
    )
    return [
        _row(
            title,
            kind,
            _url(talk, heading),
            started,
            _activity(
                section,
                started,
                nominator=opening[0] if opening else None,
            )
            if section is not None
            else None,
        ),
    ]


def collect_reviews(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    members: set[str],
    *,
    resolve: Callable[[str], str],
    resolve_many: Callable[[Iterable[str]], None],
    cache_directory: Path | None = None,
    now: dt.datetime,
    retain: Callable[[pl.DataFrame], None] | None = None,
) -> dict[str, object]:
    """Collect all current nominations with revision-aware source reuse.

    Args:
        site: Existing Chinese Wikipedia client.
        members: Current mainspace project titles.
        resolve: Shared canonical article-title resolver.
        resolve_many: Batch primer for the resolver.
        cache_directory: Disposable runtime cache root, or none.
        now: Actual UTC collection time, never an inferred start date.
        retain: Save complete source evidence before public projection.

    Returns:
        Complete review rows ordered by type, oldest start, then title.
        Undated requests sort last within their type.
    """
    cache = ReviewCache(
        site,
        cache_directory / "review-sources.parquet"
        if cache_directory
        else None,
        now,
    )
    requests = {
        (Page(site, source).title(), kind): partial(
            community_reviews,
            kind=kind,
        )
        for kind, source in COMMUNITY_SOURCES.items()
    }
    rows = cache.read(requests, include_sources=True)
    resolve_many(dict.fromkeys(row["title"] for row in rows))
    rows = [{**row, "title": resolve(row["title"])} for row in rows]
    requests = {}
    for kind, category in PROJECT_CATEGORIES.items():
        candidates = category_members(site, category, namespace=1).filter(
            pl
            .col("page_title")
            .str.replace_all("_", " ", literal=True)
            .is_in(members),
        )
        for name in candidates["page_title"]:
            subject = Page(site, name.replace("_", " "))
            talk = subject.toggleTalkPage().title()
            requests[talk, kind] = partial(
                talk_review,
                kind=kind,
                title=subject.title(),
                talk=talk,
            )
    rows.extend(cache.read(requests, required=False, include_sources=True))
    frame = pl.DataFrame(
        rows,
        schema=ITEM_SCHEMA
        | {
            "source_title": pl.String,
            "source_revision": pl.Int64,
        },
    ).filter(
        pl.col("title").is_in(members),
    )
    ordered = (
        frame
        .with_columns(
            pl
            .col("review_type")
            .replace_strict(
                dict(zip(REVIEW_TYPES, range(len(REVIEW_TYPES)), strict=True)),
            )
            .alias("_order"),
            pl
            .when(pl.col("started_at") > now.isoformat(timespec="seconds"))
            .then(None)
            .otherwise(pl.col("started_at"))
            .alias("started_at"),
            pl
            .when(
                (pl.col("replied_at") > now.isoformat(timespec="seconds"))
                | (pl.col("replied_at") < pl.col("started_at")),
            )
            .then(None)
            .otherwise(pl.col("replied_at"))
            .alias("replied_at"),
        )
        .unique(["review_type", "title", "review_url"], keep="first")
        .sort(["_order", "started_at", "title", "review_url"], nulls_last=True)
        .drop("_order")
    )
    cache.save()
    if retain is not None:
        retain(ordered)
    public = ordered.select(
        "title",
        "review_type",
        "review_url",
        "started_at",
        "replied_at",
        "participant_count",
    )
    return {
        "items": [
            {**row, "url": _url(row["title"])} for row in public.to_dicts()
        ],
    }
