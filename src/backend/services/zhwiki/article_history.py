"""Extract Chinese article milestones for retained newsletter history.

Call ``extract_events`` with talk text and verified template aliases.
Categories select candidates; template fields supply events.
Unknown dates stay null. Current status never erases older milestones.
OTD notices supplement daily display evidence. Actual coverage requires
the daily source revision after its UTC day ends.

The local vocabulary and categories follow Module:Article history/config
revision 92259335, checked on 31 October 2026. DYKtalk follows revision
83226002. ITNtalk follows revision 76273693, including six named dates
or positional date pairs. Source URLs:
https://zh.wikipedia.org/w/index.php?oldid=92259335
https://zh.wikipedia.org/w/index.php?oldid=83226002
https://zh.wikipedia.org/w/index.php?oldid=76273693

GA/FA/FL attribution follows the local ``actionNlink`` archive,
verified against Talk:BanG Dream! 少女樂團派對 revision 94343077,
Talk:终末之诗 revision 94404604, and Talk:踩地雷 revision 94391242.
Nominators are identified by account links after the explicit nomination
label. Authorship stays unknown without a declaration by that nominator.

DYK contributor attribution follows DYKEntry/doc revision 62936050 and
DYKEntry/archive revision 88772603. Their author field identifies one
main editor; blank means multiple or anonymous authors. Match one inline
entry to one dated appearance without borrowing nomination identities.
https://zh.wikipedia.org/w/index.php?oldid=62936050
https://zh.wikipedia.org/w/index.php?oldid=88772603
"""

from __future__ import annotations

import re
from html import unescape
from ipaddress import ip_address
from typing import TYPE_CHECKING
from urllib.parse import unquote

import mwparserfromhell
from mwparserfromhell.nodes import Tag, Template, Text, Wikilink

from shared.support.dates import parse_complete_date, parse_date
from shared.support.templates import (
    normalize_oldid,
    normalize_param_name,
    template_get_param,
    template_get_raw_param,
    template_page,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

CATEGORY_CANDIDATES = (
    "Category:典範條目討論",
    "Category:特色列表討論",
    "Category:優良條目討論",
    "Category:已撤銷的典範條目",
    "Category:已撤銷的特色列表",
    "Category:已撤銷的優良條目",
    "Category:典範條目落選",
    "Category:特色列表落選",
    "Category:優良條目落選",
    "Category:通過甲級評審的條目",
    "Category:通過乙上級評審的條目",
    "Category:过去的新闻动态条目",
    "Category:推薦的新條目",
    "Category:Selected anniversaries articles",
    "Category:歷史上的今天條目",
)
_ACTION_ALIASES = {
    "FAN": "FAC",
    "FARC": "FAR",
    "FLN": "FLC",
    "FLRC": "FLR",
    "GAC": "GAN",
}
_GRADES = {
    "FAC": "FA",
    "FAR": "FA",
    "FLC": "FL",
    "FLR": "FL",
    "GAN": "GA",
    "GAR": "GA",
    "WAR": "A",
    "BPAR": "Bplus",
}
_PROMOTED = {"promoted", "pass", "passed", "入選", "入选"}
_REMOVED = {"fail", "failed", "remove", "removed", "撤銷", "撤销"}
_IMMEDIATE = {"immediate failures", "immfail"}
_REVIEW_FAILURES = {"fail", "failed", "not promoted", "落選", "落选"}
_ADDITIONS = {
    "FAC": _PROMOTED,
    "FLC": _PROMOTED,
    "GAN": _PROMOTED | {"listed"},
    "WAR": {"approved", "pass", "passed"},
    "BPAR": {"approved", "pass", "passed"},
}
_REMOVALS = {
    "FAC": _IMMEDIATE,
    "FLC": _IMMEDIATE,
    "GAN": _IMMEDIATE,
    "FAR": _REMOVED | _IMMEDIATE | {"demoted", "merged", "merge"},
    "FLR": _REMOVED | _IMMEDIATE | {"demoted", "merged", "merge", "合併"},
    "GAR": _REMOVED | _IMMEDIATE | {"delisted"},
    "WAR": {"demoted", "demote"},
    "BPAR": {"demoted", "demote"},
}
_NOTICE_KINDS = {"dyk": "dyk", "itn": "news", "otd": "history"}
_ITN_SLOTS = 6
_MAX_USER_BYTES = 1000
_SIGNATURE = re.compile(
    r"\d{4}年\s*\d{1,2}月\s*\d{1,2}日\s*"
    r"(?:\([^()]*\)\s*)?\d{1,2}:\d{2}\s*\(UTC\)",
)
_NOMINATOR = re.compile(r"提名[人者]\s*[:\uff1a]")
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
_NOT_AUTHOR = re.compile(
    r"非(?:主要)?(?:作者|主[編编])|"
    r"(?:我|本人|在下)?(?:並|并)?不是(?:本[條条]目|本文)?(?:的)?"
    r"(?:主[編编]|主要作者)",
)
_IS_AUTHOR = re.compile(
    r"(?:我|本人|在下)(?:是|為|为)(?:本[條条]目|本文)?(?:的)?"
    r"(?:主[編编]|主要作者)|主[編编]自[薦荐]",
)


def _discussion_text(code: Wikicode) -> str:
    """Retain account links and prose outside quotes and templates.

    Returns:
        Nomination text without comments, quoted examples, or HTML tags.
    """
    parts = []
    for node in code.nodes:
        if isinstance(node, Text | Wikilink):
            parts.append(str(node))
        elif (
            isinstance(node, Tag)
            and str(node.tag).strip().casefold() not in _QUOTED_TAGS
            and node.contents
        ):
            parts.append(_discussion_text(node.contents))
    return "".join(parts)


def _signature_user(text: str) -> str | None:
    """Identify a signature's account independently of its label.

    Returns:
        A normalized user or talk account, or none without one.
    """
    for link in reversed(mwparserfromhell.parse(text).filter_wikilinks()):
        target = str(link.title).strip().lstrip(":").split("#", 1)[0]
        namespace, separator, user = target.partition(":")
        if separator and namespace.replace("_", " ").casefold() in (
            _USER_NAMESPACES
        ):
            user = unescape(user).replace("_", " ").split("/", 1)[0].strip()
            if user and not re.search(r"[\[\]{}|<>\x00-\x1f\x7f]", user):
                return user[:1].upper() + user[1:]
    return None


def nomination_attribution(
    code: Wikicode,
    link: str | None,
) -> dict[str, object]:
    """Read an exact local nomination section without borrowing a round.

    Args:
        code: Parsed discussion containing the referenced section.
        link: Exact local section anchor recorded by article history.

    Returns:
        Nominator, explicit authorship, and local source anchor.
        Missing, external, or ambiguous sections stay unknown.
    """
    result = {"nominator": None, "main_author": None, "nomination_link": None}
    if not link or not link.startswith("#"):
        return result
    anchor = unquote(link[1:]).replace("_", " ").strip()
    sections = [
        section
        for section in code.get_sections(include_lead=False, flat=True)
        if section.filter_headings()[0].title.strip_code().strip() == anchor
    ]
    if len(sections) != 1:
        return result
    result["nomination_link"] = link
    text = _discussion_text(sections[0])
    opening = _NOMINATOR.search(text)
    if opening is None:
        return result
    line = text[opening.end() :].split("\n", 1)[0]
    stamp = _SIGNATURE.search(line)
    user = _signature_user(line[: stamp.start()]) if stamp else None
    if user is None:
        return result
    result["nominator"] = user
    declarations = set()
    for line in text[opening.start() :].splitlines():
        stamp = _SIGNATURE.search(line)
        if stamp and _signature_user(line[: stamp.start()]) == user:
            statement = mwparserfromhell.parse(
                line[: stamp.start()],
            ).strip_code()
            if _NOT_AUTHOR.search(statement):
                declarations.add("not_author")
            elif _IS_AUTHOR.search(statement):
                declarations.add("author")
    if len(declarations) == 1:
        result["main_author"] = declarations.pop() == "author"
    return result


def _date(value: str | None) -> date | None:
    """Parse a complete recorded date without inventing calendar parts.

    Returns:
        An explicit date, including complete timestamps, or null.
    """
    if value and re.fullmatch(r"(?:\d{8}(?:\d{2}){0,3}|@[0-9.]+)", value):
        return parse_date(value)
    return parse_complete_date(value)


def _event(
    template: Template,
    parameter: str,
    kind: str,
    action: str,
    grade: str | None = None,
) -> dict[str, object]:
    """Retain one explicit milestone and its optional article revision.

    Returns:
        A scalar record suitable for a typed Parquet event column.
    """
    oldid = normalize_oldid(template_get_param(template, parameter + "oldid"))
    return {
        "kind": kind,
        "date": _date(template_get_param(template, parameter + "date")),
        "grade": grade,
        "action": action,
        "parameter": parameter,
        "oldid": int(oldid) if oldid is not None else None,
    }


def _quality_events(
    template: Template,
    code: Wikicode,
    *,
    include_reviews: bool = False,
) -> list[dict[str, object]]:
    """Read status changes using the Chinese action/result vocabulary.

    Returns:
        Changes in action order, excluding failed nominations,
        retained statuses, and ignored actions.
    """
    indices = sorted({
        int(match[1])
        for parameter in template.params
        if (
            match := re.fullmatch(
                r"action([1-9]\d*)",
                normalize_param_name(parameter.name),
            )
        )
    })
    events = []
    for index in indices:
        prefix = f"action{index}"
        if (
            template_get_param(template, prefix + "ignore") or ""
        ).casefold() == "yes":
            continue
        action_code = (template_get_param(template, prefix) or "").upper()
        action_code = _ACTION_ALIASES.get(action_code, action_code)
        result = (
            template_get_param(template, prefix + "result") or ""
        ).casefold()
        if include_reviews and action_code in {"FAC", "FLC", "GAN"}:
            outcome = (
                "passed"
                if result in _ADDITIONS[action_code]
                else "failed"
                if result in _REVIEW_FAILURES | _IMMEDIATE
                or (action_code == "GAN" and result == "not listed")
                else None
            )
            if outcome is not None:
                review = _event(
                    template,
                    prefix,
                    "review",
                    outcome,
                    _GRADES[action_code],
                )
                review["action_type"] = action_code
                review.update(
                    nomination_attribution(
                        code,
                        str(
                            template_get_raw_param(template, prefix + "link")
                            or "",
                        ).strip(),
                    ),
                )
                events.append(review)
        action = (
            "added"
            if result in _ADDITIONS.get(action_code, ())
            else "removed"
            if result in _REMOVALS.get(action_code, ())
            else None
        )
        if action is not None:
            event = _event(
                template,
                prefix,
                "quality",
                action,
                _GRADES[action_code],
            )
            if action == "added" and event["grade"] in {"FA", "GA", "FL"}:
                event.update(
                    nomination_attribution(
                        code,
                        str(
                            template_get_raw_param(template, prefix + "link")
                            or "",
                        ).strip(),
                    ),
                )
            events.append(event)
    return events


def _notice_events(template: Template) -> list[dict[str, object]]:
    """Read explicit homepage notices, retaining unknown dates.

    Returns:
        DYK, ITN, and supplemental OTD records in parameter order.
    """
    events = []
    for parameter in template.params:
        match = re.fullmatch(
            r"(dyk|itn|otd)([1-9]\d*)?date",
            normalize_param_name(parameter.name),
        )
        if match and str(parameter.value).strip():
            prefix = match[1] + (match[2] or "")
            events.append(
                _event(template, prefix, _NOTICE_KINDS[match[1]], "appeared"),
            )
    return events


def extract_events(  # ruff: ignore[too-many-arguments] - Preserve existing alias parameters and optional article identity.
    text: str,
    site: BaseSite,
    history_aliases: Iterable[str],
    dyk_aliases: Iterable[str],
    itn_aliases: Iterable[str] = (),
    *,
    article_title: str | None = None,
    include_reviews: bool = False,
) -> list[dict[str, object]]:
    """Extract Chinese quality and homepage milestones from talk text.

    Args:
        text: Exact preloaded talk-page revision text.
        site: Chinese Wikipedia namespace and title metadata.
        history_aliases: Verified Article history template names.
        dyk_aliases: Verified DYKtalk template names.
        itn_aliases: Verified ITNtalk template names.
        article_title: Exact article title for associating a retained
            DYK entry's explicit author with its dated appearance.
        include_reviews: Retain passed and failed nomination actions
            alongside quality-status changes for the milestone database.

    Returns:
        All supported events from the first Article history template and
        each DYKtalk and ITNtalk. Dates and article oldids may be null.
        Callers add talk provenance and deduplicate equivalent events.

    Raises:
        ValueError: The site does not use the Chinese template protocol.
    """
    if site.dbName() != "zhwiki":
        message = "Article-history extraction requires zhwiki."
        raise ValueError(message)
    histories = {template_page(name, site) for name in history_aliases}
    dyks = {template_page(name, site) for name in dyk_aliases}
    itns = {template_page(name, site) for name in itn_aliases}
    events = []
    history_seen = False
    dyk_index = 0
    itn_index = 0
    code = mwparserfromhell.parse(text)
    for template in code.filter_templates():
        if (
            str(template.name)
            .strip()
            .casefold()
            .startswith(
                ("#", "subst:", "safesubst:"),
            )
        ):
            continue
        name = template_page(template.name, site)
        if name is None:
            continue
        if name in histories and not history_seen:
            history_seen = True
            events.extend(
                _quality_events(
                    template,
                    code,
                    include_reviews=include_reviews,
                ),
            )
            events.extend(_notice_events(template))
        if name in dyks:
            dyk_index += 1
            day = _date(template_get_param(template, "date")) or _date(
                (template_get_param(template, "1") or "")
                + (template_get_param(template, "2") or ""),
            )
            events.append({
                "kind": "dyk",
                "date": day,
                "grade": None,
                "action": "appeared",
                "parameter": f"dyktalk{dyk_index}",
                "oldid": None,
            })
        if name in itns:
            itn_index += 1
            events.extend(_itntalk_events(template, itn_index))
    _attribute_events(code, events, article_title, site)
    return events


def _attribute_events(
    code: Wikicode,
    events: list[dict[str, object]],
    article_title: str | None,
    site: BaseSite,
) -> None:
    """Initialize nullable credits and attach explicit DYK authors."""
    contributors = _dyk_contributors(code, events, article_title, site)
    for event in events:
        for field in (
            "nominator",
            "main_author",
            "nomination_link",
            "contributors",
        ):
            event.setdefault(field, None)
        if event["kind"] == "dyk":
            event["contributors"] = contributors


def _inline_templates(code: Wikicode) -> Iterable[Template]:
    """Inspect direct templates while excluding quoted example content.

    Yields:
        Templates outside other templates and explicitly quoted tags.
    """
    for node in code.nodes:
        if isinstance(node, Template) and not str(
            node.name,
        ).strip().casefold().startswith(
            ("#", "subst:", "safesubst:"),
        ):
            yield node
        elif (
            isinstance(node, Tag)
            and str(node.tag).strip().casefold() not in _QUOTED_TAGS
            and node.contents
        ):
            yield from _inline_templates(node.contents)


def _raw_field(template: Template, field: str) -> str:
    """Read a plain source field without rendering nested markup.

    Returns:
        Trimmed raw parameter text, or an empty string.
    """
    return str(template_get_raw_param(template, field) or "").strip()


def _dyk_contributors(
    code: Wikicode,
    events: list[dict[str, object]],
    article_title: str | None,
    site: BaseSite,
) -> list[str] | None:
    """Associate one explicit author with one unambiguous appearance.

    Returns:
        One recorded contributor, or none when identity, timing, or
        nomination-round association cannot be established uniquely.
    """
    days = {event["date"] for event in events if event["kind"] == "dyk"}
    if not article_title or len(days) != 1 or None in days:
        return None
    day = next(iter(days))
    names = {
        template_page("Template:DYKEntry", site),
        template_page("Template:DYKEntry/archive", site),
    }
    title = article_title.replace("_", " ").strip()
    candidates = [
        template
        for template in _inline_templates(code)
        if template_page(template.name, site) in names
        and _raw_field(template, "article").replace("_", " ") == title
        and _raw_field(template, "result") not in {"-", "!"}
    ]
    if len(candidates) != 1:
        return None
    entry = candidates[0]
    author = _raw_field(entry, "author").replace("_", " ")
    stamp = _raw_field(entry, "timestamp")
    if (
        not author.strip()
        or len(author.encode()) > _MAX_USER_BYTES
        or re.search(r"[:#\[\]{}|<>\x00-\x1f\x7f]", author)
        or not re.fullmatch(r"[1-9][0-9]{0,11}", stamp)
    ):
        return None
    nominated = _date("@" + stamp)
    if nominated is None or nominated > day:
        return None
    try:
        ip_address(author)
    except ValueError:
        return [author[:1].upper() + author[1:]]
    return None


def _itntalk_events(template: Template, index: int) -> list[dict[str, object]]:
    """Read ITNtalk's six explicit dates and optional article revisions.

    Named parameters take precedence even when blank, matching the wiki
    template's default expansion. An entirely undated notice retains one
    unknown event instead of inventing an appearance date.

    Returns:
        Recorded news notices with stable template/slot identities.
    """
    events = []
    parameters = {
        normalize_param_name(parameter.name): str(parameter.value).strip()
        for parameter in template.params
    }
    for slot in range(1, _ITN_SLOTS + 1):
        date_names = (f"date{slot}", "date") if slot == 1 else (f"date{slot}",)
        value = next(
            (parameters[name] for name in date_names if name in parameters),
            None,
        )
        if value is None:
            value = (template_get_param(template, str(2 * slot - 1)) or "") + (
                template_get_param(template, str(2 * slot)) or ""
            )
        if not value and slot > 1:
            continue
        oldid_names = (
            (f"oldid{slot}", "oldid") if slot == 1 else (f"oldid{slot}",)
        )
        oldid = normalize_oldid(
            next(
                (
                    parameters[name]
                    for name in oldid_names
                    if name in parameters
                ),
                None,
            ),
        )
        events.append({
            "kind": "news",
            "date": _date(value),
            "grade": None,
            "action": "appeared",
            "parameter": f"itntalk{index}date{slot}",
            "oldid": int(oldid) if oldid is not None else None,
        })
    return events
