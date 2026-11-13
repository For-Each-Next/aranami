"""Prepare verified Article history parameters without editing a wiki.

Pass the exact checked talk text, verified template aliases and complete
homepage observations. Retain populated slots and fill companions for
the same observed date. FA/GA use the latest active date and retain
earlier dates in a date-only comment in the same field.
DYK, ITN and OTD use numbered slots. Article history/config 92259335,
DYKtalk 83226002 and DYKEntry/archive 88772603. Hooks come from
historical display text or an unambiguously dated archived discussion.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, replace
from functools import cache
from typing import TYPE_CHECKING

import mwparserfromhell
from mwparserfromhell.nodes import Comment, Tag, Template, Text
from opencc import OpenCC
from pywikibot import Page
from pywikibot.exceptions import InvalidTitleError
from pywikibot.site import Namespace

from shared.support.dates import parse_complete_date
from shared.support.wikitext import clean_value
from tasks.zhwiki import homepage

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

_FIELDS = ("maindateFA", "maindateGA", "itndate", "dykdate", "otddate")
_COMPANIONS = {"itn": ("link",), "dyk": ("entry",), "otd": ("oldid", "link")}
_QUOTED_TAGS = {
    "nowiki",
    "pre",
    "source",
    "syntaxhighlight",
    "code",
    "ref",
    "gallery",
    "math",
    "templatedata",
    "blockquote",
    "q",
}
# Audited local banner sources on November 14, 2026. The collector
# resolves redirects and script variants for these identities.
HISTORY_BANNER_TEMPLATES = {
    **dict.fromkeys(
        (
            "Featured article candidates",
            "Featured article review",
            "Featured list candidates",
            "GA nominee",
            "GA reassessment",
            "Peer review",
            "DYK Invite",
            "跳至討論",
            "跳至頁尾",
            "Talk header",
            "Notice",
            "BLP",
            "Warning",
            "Censor",
            "Controversial",
            "Controversial-issues",
            "FAQ",
            "Not a forum",
            "Not-fansite",
            "Policy-civil",
            "NoteTA",
        ),
        -1,
    ),
    **dict.fromkeys(
        (
            "WikiProject banner shell",
            "WikiProject Video games",
            "Image requested",
            "To do",
            "Consensus",
            "Translated page",
            "VRT-talk",
            "Archive box",
            "Archive box collapsible",
            "Archives",
            "Archive auto search",
            "Talk archive",
        ),
        1,
    ),
}
_MAX_OLDID = 2**63 - 1
_MAX_OLDID_DIGITS = len(str(_MAX_OLDID))
_DATE_SEPARATOR = r"[,\uff0c\u3001;\uff1b]"
_LITERAL_DATE = re.compile(
    r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{4}年\s*\d{1,2}月\s*\d{1,2}日)",
)


@dataclass(frozen=True, slots=True)
class HomepageAppearance:
    """Carry a proven appearance and independently known companions.

    Attributes:
        field: Verified base date parameter for this appearance kind.
        date: Complete actual display day.
        entry: Historical DYK question, retaining nested wikitext.
        article_revision: Actual article revision, not a source oldid.
        link: Proven local display or discussion link, when supplied.
    """

    field: str
    date: dt.date
    entry: str | None = None
    article_revision: int | None = None
    link: str | None = None

    def __post_init__(self) -> None:
        """Require a supported field and a complete date.

        Raises:
            ValueError: The date field or complete day is unsupported.
        """
        if self.field not in _FIELDS or type(self.date) is not dt.date:
            message = "Homepage appearances require a verified field and day."
            raise ValueError(message)


class AmbiguousArticleHistoryError(ValueError):
    """Withhold changes when more than one active history call exists.

    Attributes:
        template_count: Exact number of active destination templates.
        talk_appearances: Actual dated facts from those calls and any
            parsed legacy notices, retained without selecting a target.
    """

    def __init__(
        self,
        template_count: int,
        talk_appearances: tuple[HomepageAppearance, ...],
    ) -> None:
        """Retain observed facts without preparing parameter updates.

        Args:
            template_count: Actual number of active history calls.
            talk_appearances: Dated current metadata, not suggestions.
        """
        super().__init__(
            "Multiple active Article history templates are ambiguous.",
        )
        self.template_count = template_count
        self.talk_appearances = talk_appearances


@dataclass(frozen=True, slots=True)
class HomepageSuggestions:
    """Retain parameter changes, proofs and conversion facts.

    Attributes:
        parameters: Ordered values requiring insertion or correction.
        article_history_present: Whether an active history call exists.
        legacy_dyk_template_count: Exact active standalone DYK count.
        removable_dyk_template_count: Count safe to remove,
            or zero when legacy evidence could not be preserved safely.
        parameter_appearances: Observation for each suggested key.
        legacy_appearances: Dates and hooks read from legacy calls.
        talk_appearances: Actual dated history slots and legacy calls,
            excluding generated suggestions and external source facts.
        obsolete_parameters: Old unnumbered keys copied completely to
            a numbered group, safe to remove when applying the update.
    """

    parameters: dict[str, str]
    article_history_present: bool
    legacy_dyk_template_count: int = 0
    removable_dyk_template_count: int = 0
    parameter_appearances: dict[str, HomepageAppearance] = field(
        default_factory=dict,
    )
    legacy_appearances: tuple[HomepageAppearance, ...] = ()
    talk_appearances: tuple[HomepageAppearance, ...] = ()
    obsolete_parameters: tuple[str, ...] = ()


def _active_code(text: str) -> Wikicode:
    """Remove inert quoted source and comments from a detached parse.

    Returns:
        Active wikitext without changing the caller's original text.
    """
    code = mwparserfromhell.parse(text)
    for comment in reversed(code.filter_comments(recursive=True)):
        code.remove(comment, recursive=True)
    for tag in reversed(code.filter_tags(recursive=True)):
        if str(tag.tag).strip().casefold() in _QUOTED_TAGS:
            code.remove(tag, recursive=True)
    return code


def _matching_templates(
    code: Wikicode,
    aliases: Iterable[str],
    site: BaseSite,
) -> list[Template]:
    """Select calls only through caller-verified template identities.

    Returns:
        Matching active template calls in source order.
    """
    names = {_template_identity(name, site) for name in aliases} - {None}
    return [
        template
        for template in _active_templates(code)
        if not str(template.name)
        .strip()
        .startswith(("#", "subst:", "safesubst:"))
        and _template_identity(str(template.name), site) in names
    ]


def _template_identity(value: str, site: BaseSite) -> str | None:
    """Compare local aliases without resolving foreign-wiki prefixes.

    Returns:
        A normalized literal title, or none for another namespace.
    """
    original = value.strip()
    literal = original.lstrip(" :")
    prefix, separator, suffix = literal.partition(":")
    namespace = site.namespaces.lookup_name(prefix) if separator else None
    if namespace == Namespace.TEMPLATE:
        literal = suffix.strip()
    elif namespace is not None or original.startswith(":"):
        return None
    try:
        return Page(site, "::" + literal).title(with_section=False)
    except InvalidTitleError:
        return None


def _active_templates(code: Wikicode) -> Iterator[Template]:
    """Walk active calls while preserving their parameter markup.

    Yields:
        Template calls outside comments and quoted source contexts.
    """
    for node in code.nodes:
        if isinstance(node, Template):
            yield node
            for parameter in node.params:
                yield from _active_templates(parameter.value)
        elif (
            isinstance(node, Tag)
            and str(node.tag).strip().casefold() not in _QUOTED_TAGS
            and node.contents is not None
        ):
            yield from _active_templates(node.contents)


def _parameters(template: Template | None) -> dict[str, str]:
    """Read effective last values while retaining hook markup.

    Returns:
        Exact stripped values, including all populated companion fields.
    """
    return (
        {
            str(item.name).strip(): str(item.value).strip()
            for item in template.params
        }
        if template is not None
        else {}
    )


def _require_site(site: BaseSite) -> None:
    """Keep Chinese template semantics within their verified wiki.

    Raises:
        ValueError: The caller supplied another wiki's title rules.
    """
    if site.dbName() != "zhwiki":
        message = "Homepage parameter suggestions require zhwiki."
        raise ValueError(message)


def _date(value: str | None) -> dt.date | None:
    """Read one complete date while rejecting ambiguous date lists.

    Returns:
        One complete date, or none without a supported observation.
    """
    text = clean_value(value)
    if (
        not text
        or len(re.findall(r"\d{4}(?:-|年)\d{1,2}(?:-|月)\d{1,2}", text)) > 1
    ):
        return None
    return parse_complete_date(text)


def _homepage_value(value: str | None) -> tuple[tuple[dt.date, ...], bool]:
    """Read declared dates and whether formatting can preserve all text.

    Only literal dates in top-level comments establish older displays.
    Editorial comments remain untouched, while their active date can
    still establish an appearance. Unsupported active components retain
    the previous all-or-nothing date-list semantics.

    Returns:
        Distinct dates and whether the whole value is safe to format.
    """
    code = mwparserfromhell.parse(value or "")
    historical: set[dt.date] = set()
    safe = True
    for comment in code.filter_comments(recursive=False):
        text = str(comment.contents).strip()
        parts = re.split(_DATE_SEPARATOR, text)
        if parts and not parts[-1].strip():
            parts.pop()
        dates = [
            _date(part) if _LITERAL_DATE.fullmatch(part.strip()) else None
            for part in parts
        ]
        if dates and all(dates):
            historical.update(day for day in dates if day is not None)
        else:
            safe = False
        code.remove(comment, recursive=False)
    text = clean_value(str(code))
    parts = re.split(_DATE_SEPARATOR, text) if text else []
    dates = [_date(part) for part in parts]
    if not all(dates):
        return (), False
    safe = safe and all(
        _LITERAL_DATE.fullmatch(part.strip()) for part in parts
    )
    safe = safe and str(code).strip() == (text or "")
    return tuple(sorted(historical.union(day for day in dates if day))), safe


def homepage_dates(value: str | None) -> tuple[dt.date, ...]:
    """Read display dates from legacy lists and date-only comments.

    Args:
        value: One FA/GA history field, potentially containing repeats.

    Returns:
        Distinct dates in chronological order. Editorial comments do
        not establish dates. Unsupported active list components return
        no dates, preserving the original manual text during updates.
    """
    return _homepage_value(value)[0]


def _homepage_suggestion(
    value: str | None,
    observations: Sequence[HomepageAppearance],
    name: str,
) -> tuple[str, HomepageAppearance] | None:
    """Keep one template-supported active date and all earlier facts.

    Returns:
        A changed complete value and its latest known appearance, or
        none when the value is already correct or cannot be preserved.
    """
    current, safe = _homepage_value(value)
    wanted = sorted({*current, *(item.date for item in observations)})
    if not safe or not wanted:
        return None
    latest = wanted[-1]
    formatted = latest.isoformat()
    if len(wanted) > 1:
        older = ", ".join(day.isoformat() for day in wanted[:-1])
        formatted = f"<!-- {older} --> {formatted}"
    if set(current) == set(wanted) and (
        len(wanted) == 1 or (value or "").strip() == formatted
    ):
        return None
    proof = next(
        (item for item in observations if item.date == latest),
        HomepageAppearance(name, latest),
    )
    return formatted, proof


def _companions(appearance: HomepageAppearance) -> dict[str, str]:
    """Retain only supplied, safe values supported by the notice kind.

    Returns:
        Known companions without placeholders for unknown facts.
    """
    values = {}
    if appearance.field == "dykdate" and appearance.entry:
        values["entry"] = appearance.entry.strip()
    if appearance.field in {"itndate", "otddate"} and appearance.link:
        link = appearance.link.strip()
        if link and not re.search(r"[\x00-\x1f\x7f\[\]{}|]|://", link):
            values["link"] = link
    oldid = appearance.article_revision
    if (
        appearance.field == "otddate"
        and type(oldid) is int
        and 0 < oldid <= _MAX_OLDID
    ):
        values["oldid"] = str(oldid)
    return values


def _notice_slots(
    parameters: Mapping[str, str],
    prefix: str,
) -> dict[str, dict[str, str]]:
    """Reserve every populated notice slot, including incomplete ones.

    Returns:
        Slot suffixes mapped to their existing fields.
    """
    slots: dict[str, dict[str, str]] = {}
    for name, value in parameters.items():
        match = re.fullmatch(
            re.escape(prefix) + r"([1-9]\d*)?(date|entry|link|oldid)",
            name,
        )
        if match and value:
            slots.setdefault(match[1] or "", {})[match[2]] = value
    return slots


def _talk_appearances(
    parameters: Mapping[str, str],
) -> tuple[HomepageAppearance, ...]:
    """Read dated current metadata independently of suggested changes.

    Companion-only slots and malformed dates remain with the caller's
    complete talk snapshot; they cannot establish an appearance day.

    Returns:
        Actual scalar and numbered dated facts in their existing order.
    """
    appearances = []
    for name in _FIELDS:
        if name.startswith("maindate"):
            appearances.extend(
                HomepageAppearance(name, day)
                for day in homepage_dates(parameters.get(name))
            )
            continue
        for values in _notice_slots(parameters, name[:-4]).values():
            day = _date(values.get("date"))
            if day is None:
                continue
            oldid = values.get("oldid", "")
            article_revision = (
                int(oldid)
                if oldid.isascii()
                and oldid.isdecimal()
                and len(oldid) <= _MAX_OLDID_DIGITS
                and 0 < int(oldid) <= _MAX_OLDID
                else None
            )
            appearances.append(
                HomepageAppearance(
                    name,
                    day,
                    entry=values.get("entry") if name == "dykdate" else None,
                    article_revision=article_revision
                    if name == "otddate"
                    else None,
                    link=values.get("link")
                    if name in {"itndate", "otddate"}
                    else None,
                ),
            )
    return tuple(appearances)


def _free_slot(slots: Mapping[str, object]) -> str:
    """Choose an unused slot without shifting existing numbering.

    Returns:
        First free explicit number, independent of an unnumbered slot.
    """
    index = 1
    while str(index) in slots:
        index += 1
    return str(index)


def _migratable_notice(prefix: str, values: Mapping[str, str]) -> bool:
    """Require every populated slot field to have a known destination.

    Returns:
        Whether moving the whole old slot preserves valid companions.
    """
    if not values.keys() <= {"date", *_COMPANIONS[prefix]}:
        return False
    oldid = values.get("oldid")
    return oldid is None or (
        oldid.isascii()
        and oldid.isdecimal()
        and len(oldid) <= _MAX_OLDID_DIGITS
        and 0 < int(oldid) <= _MAX_OLDID
    )


# Keep a notice's migration and its source proof in one transaction.
def _suggestions(  # ruff: ignore[complex-structure, too-many-branches]
    parameters: Mapping[str, str],
    appearances: Sequence[HomepageAppearance],
) -> tuple[dict[str, str], dict[str, HomepageAppearance], tuple[str, ...]]:
    """Prepare updates and associate each with its observation.

    Returns:
        Changes, their proofs, and old keys requiring removal after a
        complete migration to a numbered notice group.
    """
    result: dict[str, str] = {}
    proofs: dict[str, HomepageAppearance] = {}
    obsolete = []
    for name in _FIELDS:
        observations = sorted(
            (item for item in appearances if item.field == name),
            key=lambda item: item.date,
        )
        if name.startswith("maindate"):
            suggestion = _homepage_suggestion(
                parameters.get(name),
                observations,
                name,
            )
            if suggestion is not None:
                result[name], proofs[name] = suggestion
            continue
        prefix = name[:-4]
        slots = _notice_slots(parameters, prefix)
        for appearance in observations:
            matches = [
                number
                for number, values in slots.items()
                if _date(values.get("date")) == appearance.date
            ]
            if len(matches) > 1:
                continue
            suffix = matches[0] if matches else None
            if suffix is None:
                suffix = _free_slot(slots)
                slots[suffix] = {"date": appearance.date.isoformat()}
                key = prefix + suffix + "date"
                result[key] = appearance.date.isoformat()
                proofs[key] = appearance
            values = slots[suffix]
            additions = {
                companion: value
                for companion, value in _companions(appearance).items()
                if not values.get(companion)
            }
            if additions and not suffix:
                if not _migratable_notice(prefix, values):
                    continue
                suffix = _free_slot(slots)
                copied = {
                    **values,
                    **additions,
                    "date": appearance.date.isoformat(),
                }
                for field in ("date", *_COMPANIONS[prefix]):
                    if values.get(field):
                        obsolete.append(prefix + field)
                    if copied.get(field):
                        key = prefix + suffix + field
                        result[key] = copied[field]
                        proofs[key] = appearance
                slots[suffix] = copied
                del slots[""]
            else:
                for companion, value in additions.items():
                    key = prefix + suffix + companion
                    values[companion] = result[key] = value
                    proofs[key] = appearance
    return result, proofs, tuple(obsolete)


def build_parameter_suggestions(
    talk_text: str,
    appearances: Sequence[HomepageAppearance],
    *,
    aliases: Iterable[str],
    site: BaseSite,
) -> dict[str, str]:
    """Fill missing history facts while preserving populated slots.

    Args:
        talk_text: Exact successfully checked talk-page source.
        appearances: Proven dates and independently known companions.
        aliases: Caller-verified Article history names.
        site: Existing wiki title and namespace rules.

    Returns:
        Ordered parameter changes, without modifying the original page.

    Raises:
        AmbiguousArticleHistoryError: Multiple active history calls
            prevent selecting a safe destination.
    """
    _require_site(site)
    histories = _matching_templates(
        mwparserfromhell.parse(talk_text),
        aliases,
        site,
    )
    if len(histories) > 1:
        raise AmbiguousArticleHistoryError(
            len(histories),
            tuple(
                appearance
                for history in histories
                for appearance in _talk_appearances(_parameters(history))
            ),
        )
    return _suggestions(
        _parameters(histories[0] if histories else None),
        appearances,
    )[0]


def prepare_history_suggestions(  # ruff: ignore[too-many-arguments]
    talk_text: str,
    appearances: Sequence[HomepageAppearance],
    *,
    aliases: Iterable[str],
    site: BaseSite,
    legacy_dyk_aliases: Iterable[str] = (),
    article_title: str | None = None,
    dyk_archive_aliases: Iterable[str] = (),
) -> HomepageSuggestions:
    """Prepare history updates and safe standalone-DYK conversion.

    Args:
        talk_text: Exact checked talk-page source.
        appearances: Proven homepage facts, including historical hooks.
        aliases: Verified Article history template names.
        site: Existing Chinese wiki title rules.
        legacy_dyk_aliases: Verified standalone DYKtalk aliases.
        article_title: Current subject title for an inline DYK archive.
        dyk_archive_aliases: Source-verified DYKEntry/archive names.

    Returns:
        Suggested values and exact active legacy counts. Removal is
        suggested only if all populated legacy fields are understood
        and their dates and hooks are preserved successfully.

    Raises:
        AmbiguousArticleHistoryError: Multiple active history calls
            prevent selecting a safe destination.
    """
    _require_site(site)
    code = mwparserfromhell.parse(talk_text)
    histories = _matching_templates(code, aliases, site)
    legacy = _matching_templates(code, legacy_dyk_aliases, site)
    retained = []
    complete = True
    for template in legacy:
        values = _parameters(template)
        if any(
            str(parameter.value).strip()
            and str(parameter.name).strip() not in {"date", "1", "2", "entry"}
            for parameter in template.params
        ):
            complete = False
        day = _date(values.get("date")) or _date(
            values.get("1", "") + values.get("2", ""),
        )
        if day is None:
            complete = False
            continue
        retained.append(
            HomepageAppearance(
                "dykdate",
                day,
                entry=values.get("entry") or None,
            ),
        )
    if len(histories) > 1:
        raise AmbiguousArticleHistoryError(
            len(histories),
            (
                *(
                    appearance
                    for history in histories
                    for appearance in _talk_appearances(_parameters(history))
                ),
                *retained,
            ),
        )
    existing = _parameters(histories[0] if histories else None)
    talk_appearances = (*_talk_appearances(existing), *retained)
    hooks_by_day: dict[dt.date, set[str]] = {}
    for appearance in talk_appearances:
        if appearance.field == "dykdate" and appearance.entry:
            hooks_by_day.setdefault(appearance.date, set()).add(
                appearance.entry,
            )
    complete = complete and all(
        len(hooks) <= 1 for hooks in hooks_by_day.values()
    )
    archived = _dated_archive_hook(
        talk_text,
        talk_appearances,
        title=article_title,
        aliases=dyk_archive_aliases,
        site=site,
    )
    if archived is not None:
        talk_appearances = tuple(
            replace(item, entry=archived.entry)
            if item.field == "dykdate"
            and item.date == archived.date
            and not item.entry
            else item
            for item in talk_appearances
        )
    changes, proofs, obsolete = _suggestions(
        existing,
        [*retained, *appearances, *([archived] if archived else [])],
    )
    converted = _notice_slots({**existing, **changes}, "dyk")
    complete = complete and all(
        any(
            _date(values.get("date")) == appearance.date
            and (
                not appearance.entry or values.get("entry") == appearance.entry
            )
            for values in converted.values()
        )
        for appearance in retained
    )
    return HomepageSuggestions(
        changes,
        bool(histories),
        len(legacy),
        len(legacy) if complete else 0,
        proofs,
        tuple(retained),
        talk_appearances,
        obsolete,
    )


def prepare_section0_repair(  # ruff: ignore[too-many-arguments]
    talk_text: str,
    suggestions: HomepageSuggestions,
    *,
    aliases: Iterable[str],
    legacy_dyk_aliases: Iterable[str],
    site: BaseSite,
    banner_aliases: Mapping[str, Iterable[str]] | None = None,
) -> dict | None:
    """Bind prepared suggestions to literal targets in the exact lead.

    Args:
        talk_text: Complete checked talk-page source.
        suggestions: Suggestions prepared from that complete source.
        aliases: Source-verified Article history template identities.
        legacy_dyk_aliases: Source-verified standalone DYK identities.
        site: Existing local title rules, without source acquisition.
        banner_aliases: Verified local aliases for the audited banner
            identities, including simplified and traditional variants.

    Returns:
        Exact lead and nonoverlapping top-level target spans, or none
        when targets occur below a heading, are nested, ambiguous, or
        any legacy DYK evidence cannot be preserved. Quoted headings
        remain part of the lead. No fixed wikitext is generated here.
    """
    _require_site(site)
    if (
        suggestions.legacy_dyk_template_count
        != suggestions.removable_dyk_template_count
        or not (
            suggestions.parameters
            or suggestions.obsolete_parameters
            or suggestions.removable_dyk_template_count
        )
    ):
        return None
    code = mwparserfromhell.parse(talk_text)
    sections = code.get_sections(include_lead=True, flat=True)
    original = str(sections[0]) if sections else talk_text
    histories = _matching_templates(code, aliases, site)
    legacy = _matching_templates(code, legacy_dyk_aliases, site)
    if (
        len(histories) != int(suggestions.article_history_present)
        or len(legacy) != suggestions.removable_dyk_template_count
    ):
        return None
    wanted = {id(template) for template in [*histories, *legacy]}
    targets = {}
    offset = 0
    for node in sections[0].nodes if sections else code.nodes:
        text = str(node)
        if id(node) in wanted:
            targets[id(node)] = {
                "start": offset,
                "end": offset + len(text),
                "text": text,
            }
        offset += len(text)
    if set(targets) != wanted:
        return None
    insertion = _history_insertion(
        sections[0] if sections else code,
        banner_aliases or {},
        site,
    )
    if insertion is None:
        return None
    return {
        "history_insertion": insertion,
        "original_text": original,
        "history_template": targets[id(histories[0])] if histories else None,
        "legacy_dyk_templates": [targets[id(template)] for template in legacy],
        "parameters": dict(suggestions.parameters),
        "obsolete_parameters": list(suggestions.obsolete_parameters),
    }


@cache
def _banner_converter() -> OpenCC:
    """Reuse character conversion for mixed-script banner names.

    Returns:
        The simplified-character converter, without source acquisition.
    """
    return OpenCC("t2s")


def _banner_identity(value: str, site: BaseSite) -> str | None:
    """Compare verified banner aliases across mixed Chinese scripts.

    Returns:
        A local identity with traditional characters simplified.
    """
    identity = _template_identity(value, site)
    return _banner_converter().convert(identity) if identity else None


def _history_insertion(
    code: Wikicode,
    aliases: Mapping[str, Iterable[str]],
    site: BaseSite,
) -> int | None:
    """Place history after notices and before projects or archives.

    Unknown calls and quoted examples retain their relative order.
    Conflicting known banner groups withhold the automatic replacement.

    Returns:
        A top-level source boundary, or none for conflicting groups.
    """
    identities = {
        _banner_identity(alias, site): position
        for name, position in HISTORY_BANNER_TEMPLATES.items()
        for alias in aliases.get(name, (name,))
    }
    before, after = 0, len(str(code))
    offset = 0
    leading = True
    for node in code.nodes:
        end = offset + len(str(node))
        if isinstance(node, Comment) or (
            isinstance(node, Text) and not str(node).strip()
        ):
            if leading:
                before = end
        else:
            leading = False
        if isinstance(node, Template):
            position = identities.get(_banner_identity(str(node.name), site))
            if position == -1:
                before = end
            elif position == 1:
                after = min(after, offset)
        offset = end
    if before > after:
        return None
    # Start of the interval also precedes unfamiliar project banners.
    return before


def extract_dyk_archive_entry(
    source_text: str,
    *,
    title: str,
    aliases: Iterable[str],
    site: BaseSite,
) -> str | None:
    """Read one subject's raw question from verified DYK archives.

    Args:
        source_text: Already observed discussion/archive source text.
        title: Explicit article identity required in the archive call.
        aliases: Verified DYKEntry/archive template names.
        site: Existing local title rules, without source acquisition.

    Returns:
        One unambiguous nonfailed question, preserving nested wikitext.
        No display day is inferred from nomination timestamps.
    """
    _require_site(site)
    try:
        wanted = Page(site, "::" + title.lstrip(" :")).title(
            with_section=False,
        )
    except InvalidTitleError:
        return None
    hooks = set()
    for template in _matching_templates(
        mwparserfromhell.parse(source_text),
        aliases,
        site,
    ):
        values = _parameters(template)
        if values.get("result") in {"-", "!"}:
            continue
        try:
            subject = Page(site, "::" + values.get("article", "")).title(
                with_section=False,
            )
        except InvalidTitleError:
            continue
        question = values.get("question", "")
        if subject == wanted and str(_active_code(question)).strip():
            hooks.add(question)
    return next(iter(hooks)) if len(hooks) == 1 else None


def _dated_archive_hook(
    text: str,
    appearances: Sequence[HomepageAppearance],
    *,
    title: str | None,
    aliases: Iterable[str],
    site: BaseSite,
) -> HomepageAppearance | None:
    """Associate an inline archive with one actual talk-notice day.

    Returns:
        A dated hook only when current talk metadata identifies one DYK
        day and its existing questions do not contradict the archive.
    """
    days = {item.date for item in appearances if item.field == "dykdate"}
    if not title or len(days) != 1:
        return None
    question = extract_dyk_archive_entry(
        text,
        title=title,
        aliases=aliases,
        site=site,
    )
    known = {
        item.entry
        for item in appearances
        if item.field == "dykdate" and item.entry
    }
    if not question or known - {question}:
        return None
    return HomepageAppearance("dykdate", next(iter(days)), entry=question)


def extract_dyk_entry(
    source_text: str,
    *,
    title: str,
    site: BaseSite,
) -> str | None:
    """Read a complete DYK hook from its historical display source.

    Args:
        source_text: Exact related Template:Dyk revision wikitext.
        title: Verified primary subject spelling from that source.
        site: Existing local title rules; no source requests are made.

    Returns:
        Complete hook wikitext for one unambiguous primary subject.
        Missing, incidental or ambiguous matches remain unknown.
    """
    _require_site(site)
    try:
        wanted = Page(site, "::" + title.lstrip(" :")).title(
            with_section=False,
        )
    except InvalidTitleError:
        return None
    displays = _matching_templates(
        mwparserfromhell.parse(source_text),
        ("Dyk/auto",),
        site,
    )
    hooks = set()
    for template in displays:
        for index in range(6):
            if not template.has(str(index)):
                continue
            question = template.get(str(index)).value
            for subject in homepage._bold_titles(_active_code(str(question))):  # ruff: ignore[private-member-access]
                try:
                    canonical = Page(
                        site,
                        "::" + subject.strip().lstrip(" :"),
                    ).title(with_section=False)
                except InvalidTitleError:
                    continue
                if canonical == wanted:
                    hooks.add(str(question).strip())
    return next(iter(hooks)) if len(hooks) == 1 else None
