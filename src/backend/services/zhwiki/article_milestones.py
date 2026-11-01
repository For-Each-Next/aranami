"""Extract explicit credits from Chinese recognition discussions.

DYK fields follow Template:DYKEntry/doc oldid 62936050 and
Template:DYKEntry/archive oldid 88772603. The candidate-page
instructions archive accepted discussions on article talk pages:
https://zh.wikipedia.org/wiki/Wikipedia:新条目推荐/候选
A surviving single author field identifies a contributor; the separate
nominator field identifies the nominator. Blank fields stay unknown.
The explicit question field retains its documented rich wikitext.
"""

from __future__ import annotations

import datetime as dt
import re
from ipaddress import ip_address
from typing import TYPE_CHECKING

import mwparserfromhell

from backend.services.zhwiki.article_history import (
    _inline_templates,
    extract_events,
    nomination_attribution,
)
from shared.support.templates import template_get_raw_param, template_page

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_MAX_USER_BYTES = 1000
_MAX_QUESTION_BYTES = 16 * 1024


def explicit_question(value: str) -> str | None:
    """Keep bounded explicit question wikitext without rendering it.

    Returns:
        Original markup, or none for blank or invalid source data.
    """
    if (
        not value
        or len(value.encode()) > _MAX_QUESTION_BYTES
        or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value)
    ):
        return None
    return value


def explicit_account(value: str) -> str | None:
    """Normalize one account, rejecting markup and IPs.

    Returns:
        One account, or none when the field cannot identify a user.
    """
    value = value.replace("_", " ").strip()
    if (
        not value
        or len(value.encode()) > _MAX_USER_BYTES
        or re.search(r"[:#\[\]{}|<>\x00-\x1f\x7f]", value)
    ):
        return None
    try:
        ip_address(value)
    except ValueError:
        return value[:1].upper() + value[1:]
    return None


def source_events(
    text: str,
    site: BaseSite,
    title: str,
    aliases: dict | None = None,
) -> list[dict]:
    """Read verified canonical history templates from a source snapshot.

    Returns:
        Historical milestones with nullable explicit attribution.
    """
    aliases = aliases or {}
    code = mwparserfromhell.parse(text)
    templates = list(_inline_templates(code))
    direct = "\n".join(map(str, templates))
    events = extract_events(
        direct,
        site,
        aliases.get("Article history", ("Template:Article history",)),
        aliases.get("DYKtalk", ("Template:DYKtalk",)),
        article_title=title,
        include_reviews=True,
    )
    history_names = {
        template_page(name, site)
        for name in aliases.get(
            "Article history",
            ("Template:Article history",),
        )
    }
    history = next(
        (
            template
            for template in templates
            if template_page(template.name, site) in history_names
        ),
        None,
    )
    for event in events:
        if event.get("kind") in {"quality", "review"} and history is not None:
            link = str(
                template_get_raw_param(history, event["parameter"] + "link")
                or "",
            ).strip()
            event.update(nomination_attribution(code, link))
    return events


def nomination_targets(text: str, event: dict, site: BaseSite) -> set[str]:
    """Read explicit archive anchors for the matching milestone.

    Returns:
        Recorded wiki titles and anchors, never guessed paths.
    """
    if event.get("kind") not in {"quality", "review"}:
        return set()
    matches = {
        row["parameter"]
        for row in source_events(
            text,
            site,
            event["title"],
            event.get("_aliases"),
        )
        if row.get("date") == event.get("date")
        and row.get("grade") == event.get("grade")
        and row.get("action") == event.get("action")
    }
    histories = [
        template
        for template in _inline_templates(mwparserfromhell.parse(text))
        if template_page(template.name, site)
        in {
            template_page(name, site)
            for name in event.get("_aliases", {}).get(
                "Article history",
                ("Template:Article history",),
            )
        }
    ]
    if len(histories) != 1:
        return set()
    return {
        link
        for parameter in matches
        if (
            link := str(
                template_get_raw_param(histories[0], parameter + "link") or "",
            ).strip()
        )
        and "#" in link
        and not link.startswith("#")
        and not re.search(r"[\[\]{}|<>\x00-\x1f\x7f]", link)
    }


def extract_credits(text: str, event: dict, site: BaseSite) -> list[dict]:
    """Find credits without choosing between nomination rounds.

    Args:
        text: Exact source revision wikitext.
        event: Article identity and dated recognition event.
        site: Existing Chinese Wikipedia namespace metadata.

    Returns:
        Candidate identities and explicit fields. Distinct identities
        must remain ambiguous until the caller resolves their evidence.
    """
    day = event.get("date")
    if not isinstance(day, dt.date):
        return []
    code = mwparserfromhell.parse(text)
    if event.get("kind") != "dyk":
        link = event.get("nomination_link")
        identity = (
            f"{day.isoformat()}:{event.get('grade')}:{event.get('action')}"
        )
        if isinstance(link, str) and link.startswith("#"):
            credit = nomination_attribution(code, link)
            if credit.get("nominator"):
                return [{"identity": identity, **credit}]
        return [
            {
                "identity": identity,
                **{
                    field: row.get(field)
                    for field in (
                        "nominator",
                        "main_author",
                        "nomination_link",
                    )
                },
            }
            for row in source_events(
                text,
                site,
                event["title"],
                event.get("_aliases"),
            )
            if row.get("date") == day
            and row.get("grade") == event.get("grade")
            and row.get("action") == event.get("action")
            and row.get("nominator")
        ]
    return _dyk_credits(code, event, site)


def _dyk_credits(
    code: mwparserfromhell.wikicode.Wikicode,
    event: dict,
    site: BaseSite,
) -> list[dict]:
    """Read explicit fields from matching DYK entries.

    Returns:
        Candidates nominated no later than the recorded appearance.
    """
    day = event["date"]
    aliases = event.get("_aliases", {})
    names = {
        template_page(alias, site)
        for name in ("DYKEntry", "DYKEntry/archive")
        for alias in aliases.get(name, ("Template:" + name,))
    }
    result = []
    for template in _inline_templates(code):
        if template_page(template.name, site) not in names:
            continue
        fields = {
            name: str(template_get_raw_param(template, name) or "").strip()
            for name in (
                "article",
                "author",
                "nominator",
                "timestamp",
                "result",
                "question",
            )
        }
        if fields["article"].replace("_", " ") != event["title"].replace(
            "_",
            " ",
        ).strip() or fields["result"] in {"-", "!"}:
            continue
        stamp = fields["timestamp"]
        if not re.fullmatch(r"[1-9][0-9]{0,11}", stamp):
            continue
        try:
            nominated = dt.datetime.fromtimestamp(int(stamp), dt.UTC).date()
        except ValueError, OverflowError, OSError:
            continue
        previous = event.get("previous_date")
        if nominated > day or (previous is not None and nominated <= previous):
            continue
        author = explicit_account(fields["author"])
        nominator = explicit_account(fields["nominator"])
        result.append({
            "identity": stamp,
            "contributors": [author] if author else None,
            "nominator": nominator,
            "question": explicit_question(fields["question"]),
        })
    return result
