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
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import mwparserfromhell

from shared.support.dates import parse_complete_date, parse_date
from shared.support.templates import (
    normalize_oldid,
    normalize_param_name,
    template_get_param,
    template_page,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from mwparserfromhell.nodes import Template
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


def _quality_events(template: Template) -> list[dict[str, object]]:
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
        code = (template_get_param(template, prefix) or "").upper()
        code = _ACTION_ALIASES.get(code, code)
        result = (
            template_get_param(template, prefix + "result") or ""
        ).casefold()
        action = (
            "added"
            if result in _ADDITIONS.get(code, ())
            else "removed"
            if result in _REMOVALS.get(code, ())
            else None
        )
        if action is not None:
            events.append(
                _event(template, prefix, "quality", action, _GRADES[code]),
            )
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


def extract_events(
    text: str,
    site: BaseSite,
    history_aliases: Iterable[str],
    dyk_aliases: Iterable[str],
    itn_aliases: Iterable[str] = (),
) -> list[dict[str, object]]:
    """Extract Chinese quality and homepage milestones from talk text.

    Args:
        text: Exact preloaded talk-page revision text.
        site: Chinese Wikipedia namespace and title metadata.
        history_aliases: Verified Article history template names.
        dyk_aliases: Verified DYKtalk template names.
        itn_aliases: Verified ITNtalk template names.

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
    for template in mwparserfromhell.parse(text).filter_templates():
        name = template_page(template.name, site)
        if name is None:
            continue
        if name in histories and not history_seen:
            history_seen = True
            events.extend(_quality_events(template))
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
    return events


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
