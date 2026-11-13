"""Collect current video-game nominations and talk-page review requests.

Community candidates come from proposal headings or DYKEntry, regardless
of talk-page nomination banners. Project categories identify ACC, BPAN,
PPR, and reassessment requests. Dates come only from the corresponding
discussion or explicit DYK nomination timestamp. Reply times require a
signed comment from someone other than the nominator. Counts include
distinct identified signatories and explicit nominators.
Latest-comment facts include the opening and nominator followups without
changing that response contract. Section lengths count exact UTF-8
source bytes, including headings, nested sections and media markup.

Contracts checked on 25 October 2026: Module:PJ:VG/RVW (92100885), FAC
proposals (95249932), FLC proposals (95117299), Preload FLN/auto
(92992079), GAN proposals (95257581), PR proposals (95231065), DYK
candidates (95258998), Module:DYKEntry (94743983), and WikiProject
Video games (92848131). DYKEntry's nominator parameter follows its
documentation (62936050), checked on 31 October 2026.
DYK candidates (95512115), Module:DYKEntry (94743983), and DYKvoteH
(62537713) distinguish bot qualification from explicit closing outcomes.
Closed entries remain visible while present on the candidate page.
Talk:豐川祥子 (93157031) places the opening in a nested section and an
unrelated DYKEntry/archive discussion after the project review.
See https://zh.wikipedia.org/w/index.php?oldid=REVISION.
"""

from __future__ import annotations

import datetime as dt
import logging
import operator
import re
from contextlib import suppress
from functools import partial
from html import unescape
from typing import TYPE_CHECKING
from urllib.parse import quote, quote_plus

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Heading, Tag, Template, Text, Wikilink
from pywikibot import Page

from data.website import REVIEW_TYPES
from shared.sources.quarry.projects import category_members
from shared.support.titles import page_url
from tasks.zhwiki.article_milestones import explicit_account, explicit_question
from tasks.zhwiki.assessment_lists import review_heading
from tasks.zhwiki.review_cache import ITEM_SCHEMA, ReviewCache

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)

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
# Voting rules and conditional extensions checked on 10 November 2026:
# https://zh.wikipedia.org/wiki/Wikipedia:新条目推荐/候选#獲選標準
# https://zh.wikipedia.org/wiki/Wikipedia:特色列表评选#存檔程序
# https://zh.wikipedia.org/wiki/Wikipedia:優良條目評選
# https://zh.wikipedia.org/wiki/Wikipedia:典范条目评选
_REVIEW_DAYS = {"fac": 14, "gan": 7, "flc": 28, "dykc": 7}
_DYK_PASSED = {"通過", "通过", "入選", "入选"}
_DYK_FAILED = {
    "未獲通過",
    "未获通过",
    "未通過",
    "未通过",
    "不通過",
    "不通过",
    "落選",
    "落选",
}


def _review_end(
    kind: str,
    started: str | None,
    section: Wikicode | None = None,
) -> str | None:
    """Read an explicit base deadline or the last possible voting day.

    Returns:
        A UTC deadline, or none without a known start. DYK may extend
        from four to seven days and FLC from fourteen to twenty-eight;
        absent a verified closure, their full windows avoid treating an
        optional printed extension as proof of its activation. FAC and
        GAN use explicit period ends when provided, including pauses.
    """
    if started is None or kind not in _REVIEW_DAYS:
        return None
    start = dt.datetime.fromisoformat(started)
    try:
        end = start + dt.timedelta(days=_REVIEW_DAYS[kind])
    except OverflowError:
        return None
    if section is not None:
        lead = section.get_sections(
            include_lead=False,
            include_headings=False,
            flat=True,
        )[0].strip_code()
        period = _PERIOD.search(lead)
        if period is not None:
            line = lead[period.end() :].partition("\n")[0]
            dates = list(_SIGNATURE.finditer(line))
            if len(dates) > 1 and (raw := _timestamp(dates[1])) is not None:
                explicit = dt.datetime.fromisoformat(raw)
                if explicit > start:
                    try:
                        end = explicit + dt.timedelta(
                            days=14 if kind == "flc" else 0,
                        )
                    except OverflowError:
                        return None
    return end.isoformat(timespec="seconds")


def _dyk_result(values: dict[str, str], discussion: Wikicode) -> str | None:
    """Read an explicit DYK closure without promoting bot qualification.

    Returns:
        Passed, failed, ended pending disposition, or an unknown result.
        DYKvoteH takes precedence over earlier bot result icons. Icon
        evidence must satisfy Module:DYKEntry's hash and epoch guards.
    """
    for node in discussion.filter_templates(recursive=False):
        if (
            str(node.name).strip().removeprefix("Template:").casefold()
            != "dykvoteh"
        ):
            continue
        outcome = str(node.get("1").value).strip() if node.has("1") else ""
        if outcome in _DYK_PASSED:
            return "passed"
        return "failed" if outcome in _DYK_FAILED else "ended"
    raw = values.get("2", "")
    if (
        values.get("hash")
        and values.get("1") != values["hash"]
        and re.fullmatch(r"[0-9]{1,12}", raw)
    ):
        with suppress(ValueError, OSError, OverflowError):
            dt.datetime.fromtimestamp(int(raw), dt.UTC)
            return {"+": "passed", "-": "failed"}.get(values.get("result"))
    return None


def _url(
    title: str,
    heading: str | None,
    url: Callable[[str], str],
) -> str:
    """Encode the actual source heading independently of the page title.

    Returns:
        A public article or discussion-section URL.
    """
    base = url(title)
    return base + (
        "#" + quote(heading.replace(" ", "_"), safe="") if heading else ""
    )


def _section_anchors(code: Wikicode) -> dict[int, str]:
    """Assign collision-aware anchors across all structural headings.

    MediaWiki's Parser::formatHeadings reserves primary and legacy IDs
    in source order across all heading levels. ASCII case is ignored
    for collision checks, and an occupied suffix advances to the next
    free number. The contract was checked against MediaWiki's Parser
    and Sanitizer on 9 November 2026:
    https://github.com/wikimedia/mediawiki/blob/master/includes/Parser/Parser.php
    https://github.com/wikimedia/mediawiki/blob/master/includes/Parser/Sanitizer.php

    Args:
        code: Parsed page containing every structural heading.

    Returns:
        Heading-node identities mapped to unique primary fragment IDs.
    """
    anchors = {}
    used = set()
    for heading in code.filter_headings(recursive=False):
        text = re.sub(r"[ _]+", " ", heading.title.strip_code()).strip()[:1024]
        primary = re.sub(r"[\t\n\f\r ]", "_", text)
        anchors[id(heading)] = _reserve_anchor(primary, used)
        fallback = (
            quote_plus(text.replace(" ", "_"), safe="")
            .replace("~", "%7E")
            .replace("%3A", ":")
            .replace("%", ".")
        )
        if fallback != primary:
            _reserve_anchor(fallback, used)
    return anchors


def _reserve_anchor(base: str, used: set[bytes]) -> str:
    """Reserve the first available MediaWiki heading ID.

    Args:
        base: Unsuffixed primary or fallback fragment ID.
        used: Already reserved ASCII-folded UTF-8 IDs, updated in place.

    Returns:
        The original ID or its first available numbered suffix.
    """
    candidate = base
    suffix = 2
    while candidate.encode().lower() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.encode().lower())
    return candidate


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
    """Count participants and observe comments separately from replies.

    Args:
        code: Only this nomination's discussion, including subsections.
        started: Observed nomination start, or none when undated.
        nominator: DYK's explicit account, otherwise the opening author.

    Returns:
        Counts include each known author and explicit nominator once.
        A reply requires a known nominator and another author signing
        at or after the start. Latest comments include any identified
        signed comment. Missing participants leave the count unknown.
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
    replies = (
        [
            (user, timestamp)
            for user, timestamp in comments
            if user != nominator and timestamp >= started
        ]
        if started is not None and nominator
        else []
    )
    latest = max(replies, key=operator.itemgetter(1), default=None)
    comment = max(reversed(comments), key=operator.itemgetter(1), default=None)
    return {
        "replied_at": latest[1] if latest else None,
        "replied_by": latest[0] if latest else None,
        "latest_comment_at": comment[1] if comment else None,
        "latest_comment_by": comment[0] if comment else None,
        "comment_count": len(comments) if comments else None,
        "section_bytes": len(str(code).encode("utf-8")),
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
        "ends_at": activity.get("ends_at"),
        "result": activity.get("result"),
        "replied_at": activity.get("replied_at"),
        "replied_by": activity.get("replied_by"),
        "replied_url": activity.get("replied_url"),
        "latest_comment_at": activity.get("latest_comment_at"),
        "latest_comment_by": activity.get("latest_comment_by"),
        "latest_comment_url": activity.get("latest_comment_url"),
        "comment_count": activity.get("comment_count"),
        "section_bytes": activity.get("section_bytes"),
        "extract": activity.get("extract"),
        "participant_count": activity.get("participant_count"),
        "nominator": explicit_account(activity.get("nominator") or ""),
        "contributors": contributors or None,
        "question": explicit_question(activity.get("question") or ""),
    }


def community_reviews(
    text: str,
    kind: str,
    *,
    url: Callable[[str], str],
) -> list[dict[str, object]]:
    """Extract primary nominees from the current community source.

    Args:
        text: Current proposal page, with no archive expansion.
        kind: FAC, FLC, GAN, PR, or DYKC in lowercase.
        url: URL builder bound to the collector's existing wiki client.

    Returns:
        Nominee titles, actual discussion links, and observed starts.
        Explicit outcomes and deadlines remain separate observations.
        Closed DYK entries stay visible until removed from this source.
    """
    code = mwparserfromhell.parse(text)
    if kind == "dykc":
        return _dyk_reviews(code, url=url)
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
                    url,
                ),
                started,
                _activity(section, started)
                | {"ends_at": _review_end(kind, started, section)},
            ),
        )
    return result


def _dyk_reviews(
    code: Wikicode,
    *,
    url: Callable[[str], str],
) -> list[dict[str, object]]:
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
        if not title:
            continue
        started = None
        raw = values.get("timestamp", "")
        if re.fullmatch(r"[0-9]{1,12}", raw):
            with suppress(ValueError, OSError, OverflowError):
                started = dt.datetime.fromtimestamp(
                    int(raw),
                    dt.UTC,
                ).isoformat(timespec="seconds")
        discussion = _dyk_discussion(code, index)
        result.append(
            _row(
                title,
                "dykc",
                _url(COMMUNITY_SOURCES["dykc"], title, url),
                started,
                _activity(
                    discussion,
                    started,
                    nominator=_username(values.get("nominator", "")),
                )
                | {
                    "ends_at": _review_end("dykc", started),
                    "result": _dyk_result(values, discussion),
                    "section_bytes": len(
                        (str(template) + str(discussion)).encode("utf-8"),
                    ),
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


def talk_review(
    text: str,
    kind: str,
    *,
    title: str,
    talk: str,
    url: Callable[[str], str],
) -> list[dict]:
    """Link the relevant discussion, leaving unobserved starts null.

    Args:
        text: Current talk-page wikitext, or empty for a missing page.
        kind: Project review or reassessment type.
        title: Canonical subject title from its talk-page identity.
        talk: Canonical talk-page title using wiki namespace rules.
        url: URL builder bound to the collector's existing wiki client.

    Returns:
        One category-backed request, even without a matching discussion.
        The earliest signed comment in the selected section, including
        subsections, establishes its start; newer lead followups do not
        replace that opening. An unsigned lead date remains a fallback.
        Repeated headings retain the selected section's exact anchor.
    """
    heading = (
        review_heading(text, kind) if kind not in _PROJECT_HEADINGS else None
    )
    if heading is not None:
        heading = mwparserfromhell.parse(heading).strip_code().strip()
    code = mwparserfromhell.parse(text)
    sections = code.get_sections(levels=[2], include_lead=False)
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
    anchor = (
        _section_anchors(code)[id(section.filter_headings(recursive=False)[0])]
        if section is not None
        else None
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
            _url(talk, anchor, url),
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
    # Older parses may link a repeated review to the first section.
    cache = ReviewCache(
        site,
        cache_directory / "review-sources-anchors"
        if cache_directory
        else None,
        now,
    )
    requests = {
        (Page(site, source).title(), kind): partial(
            community_reviews,
            kind=kind,
            url=partial(page_url, site),
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
            subject = Page(site, name)
            talk = subject.toggleTalkPage().title()
            requests[talk, kind] = partial(
                talk_review,
                kind=kind,
                title=subject.title(),
                talk=talk,
                url=partial(page_url, site),
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
            *(
                pl
                .when(
                    pl.col("latest_comment_at")
                    > now.isoformat(timespec="seconds"),
                )
                .then(None)
                .otherwise(pl.col(field))
                .alias(field)
                for field in (
                    "latest_comment_at",
                    "latest_comment_by",
                    "latest_comment_url",
                )
            ),
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
        "ends_at",
        "result",
        "replied_at",
        "participant_count",
        "nominator",
        "replied_by",
        "replied_url",
        "latest_comment_at",
        "latest_comment_by",
        "latest_comment_url",
        "comment_count",
        "section_bytes",
    )
    public = _reply_links(site, public, ordered)
    return {
        "items": [
            {**row, "url": page_url(site, row["title"])}
            for row in public.to_dicts()
        ],
    }


def _reply_links(
    site: BaseSite,
    public: pl.DataFrame,
    sources: pl.DataFrame,
) -> pl.DataFrame:
    """Attach reply and latest-comment IDs from saved revisions.

    Args:
        site: Existing source client.
        public: Public rows with observed reply authors and timestamps.
        sources: Corresponding rows with exact source revisions.

    Returns:
        Verified reply and latest-comment links from the same lookup.
        No additional request is made only for an opening/followup link;
        failed or ambiguous lookups retain null links.
    """
    targets = {}
    for source in (
        sources
        .filter(pl.col("replied_at").is_not_null())
        .select(
            "source_title",
            "source_revision",
        )
        .unique()
        .to_dicts()
    ):
        revision = source["source_revision"]
        try:
            result = site.simple_request(
                action="discussiontoolspageinfo",
                oldid=revision,
                prop="threaditemshtml",
                formatversion=2,
            ).submit()
            threads = result["discussiontoolspageinfo"]["threaditemshtml"]
            targets[revision] = (
                _thread_targets(threads) if isinstance(threads, list) else {}
            )
        except Exception as error:  # ruff: ignore[blind-except]
            _LOGGER.warning(
                "Last-comment links unavailable for %s (%s).",
                source["source_title"],
                type(error).__name__,
            )
    rows = []
    for row, source in zip(public.to_dicts(), sources.to_dicts(), strict=True):
        for prefix in ("replied", "latest_comment"):
            key = (row.get(f"{prefix}_by"), row.get(f"{prefix}_at"))
            ids = targets.get(source["source_revision"], {}).get(key, [])
            if len(ids) == 1:
                row[f"{prefix}_url"] = _url(
                    source["source_title"],
                    ids[0],
                    partial(page_url, site),
                )
        rows.append(row)
    return pl.DataFrame(rows, schema=public.schema)


def _thread_targets(items: list[dict]) -> dict[tuple[str, str], list[str]]:
    """Index verified comment anchors without exposing downloaded HTML.

    Args:
        items: DiscussionTools' nested serialized thread items.

    Returns:
        Author/time keys mapped to IDs for ambiguity checks.
    """
    result = {}
    for item in items:
        author, stamp, anchor = (
            item.get("author"),
            item.get("timestamp"),
            item.get("id"),
        )
        if (
            isinstance(author, str)
            and isinstance(stamp, str)
            and isinstance(anchor, str)
        ):
            with suppress(ValueError):
                timestamp = dt.datetime.fromisoformat(
                    stamp,
                ).isoformat(timespec="seconds")
                result.setdefault((_username(author), timestamp), []).append(
                    anchor,
                )
        for key, values in _thread_targets(item.get("replies", [])).items():
            result.setdefault(key, []).extend(values)
    return {key: list(dict.fromkeys(values)) for key, values in result.items()}
