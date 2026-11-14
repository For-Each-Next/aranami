"""Read explicit project and inherited header-shell assessment classes.

The Video games banner/class mask (92848131/92848137), WPBannerMeta
(91444890), banner shell (95284439), and PJBSClass modules
(95173400/95173398) define child precedence and header inheritance.
Literal project and shell classes precede complete B criteria.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import mwparserfromhell
from mwparserfromhell.nodes import Template, Text
from pywikibot import Page
from pywikibot.exceptions import InvalidTitleError
from pywikibot.site import Namespace

from shared.assessment_profiles import assessment_profile

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

CONTRACT = (
    "zh-vg-b-listing:92848131:92848137:95173241:91444890:"
    "95284439:95173400:95173398:3"
)
_DEFAULT_SHELL = ("Template:WikiProject banner shell",)


def _legacy_check_suffix(name: Wikicode) -> bool:
    """Recognize the historical Bckeck substitution in a banner name.

    Returns:
        Whether the suffix contains only literal criteria substitutions
        and whitespace, without an arbitrary dynamic template name.
    """
    checks = False
    for node in name.nodes[1:]:
        if isinstance(node, Text) and not str(node).strip():
            continue
        if not isinstance(node, Template):
            return False
        prefix, separator, target = str(node.name).strip().partition(":")
        if (
            not separator
            or prefix.casefold() != "subst"
            or target.strip() != "Bckeck"
            or any(
                str(parameter.name).strip()
                not in {"1", "2", "3", "4", "5", "6"}
                or parameter.value.filter_templates()
                or parameter.value.filter_tags()
                or parameter.value.filter_arguments()
                for parameter in node.params
            )
        ):
            return False
        checks = True
    return checks


def _all_b_criteria(template: Template) -> bool | None:
    """Check all six literal B criteria without overriding a class.

    Returns:
        True for six yes/y values, false for absent or negative flags,
        or none for duplicated or dynamic supplied criteria.
    """
    criteria = {"b1": [], "b2": [], "b3": [], "b4": [], "b5": [], "b6": []}
    for parameter in template.params:
        name = str(parameter.name).strip()
        if name in criteria:
            criteria[name].append(parameter.value)
    if any(not values for values in criteria.values()):
        return False
    unknown = False
    for values in criteria.values():
        if (
            len(values) != 1
            or values[0].filter_templates()
            or values[0].filter_tags()
            or values[0].filter_arguments()
        ):
            unknown = True
        elif str(values[0]).strip().casefold() not in {"yes", "y"}:
            return False
    return None if unknown else True


def _literal_class(
    template: Template,
    *,
    case_insensitive: bool = False,
) -> str | None:
    """Read one literal class, distinguishing missing from unsupported.

    Returns:
        An explicit value, empty for missing, or none for ambiguity.
    """
    values = [
        parameter.value
        for parameter in template.params
        if (
            str(parameter.name).strip().casefold()
            if case_insensitive
            else str(parameter.name).strip()
        )
        == "class"
    ]
    if not values:
        return ""
    if len(values) != 1:
        return None
    value = values[0]
    if (
        value.filter_templates()
        or value.filter_tags()
        or value.filter_arguments()
    ):
        return None
    literal = str(value).strip()
    return "" if literal.casefold() in {"no_input", "¬"} else literal


def _clean_code(text: str) -> Wikicode:
    """Remove comments and nontranscluded markup before reading banners.

    Returns:
        Parsed active source without expansion or external reads.
    """
    code = mwparserfromhell.parse(text)
    for comment in reversed(code.filter_comments()):
        code.remove(comment)
    for tag in reversed(code.filter_tags()):
        if str(tag.tag).strip().casefold() in {
            "nowiki",
            "pre",
            "source",
            "syntaxhighlight",
            "math",
            "ref",
        }:
            code.remove(tag)
    return code


def _matching_templates(
    code: Wikicode,
    site: BaseSite,
    aliases: Iterable[str],
) -> tuple[list[Template], bool]:
    """Match exact template identities and flag dynamic candidate names.

    Returns:
        Literal matching nodes and whether a recognized name is dynamic.
    """
    names = {Page(site, name, ns=10) for name in aliases}
    matches = []
    dynamic = False
    for template in code.filter_templates():
        name = str(template.name).strip()
        literal, separator, _ = name.partition("{{")
        candidate = _template_page(site, literal.strip())
        if candidate in names:
            if separator and not _legacy_check_suffix(template.name):
                dynamic = True
            else:
                matches.append(template)
    return matches, dynamic


def _template_page(site: BaseSite, name: str) -> Page | None:
    """Normalize template names without probing interwiki prefixes.

    Returns:
        Local template identity, or none for functions and modifiers.
    """
    transclusion = name.startswith(":")
    name = name.removeprefix(":").strip()
    prefix, separator, _ = name.partition(":")
    if transclusion and not separator:
        return None
    if separator and site.namespaces.lookup_name(prefix) != Namespace.TEMPLATE:
        return None
    try:
        return Page(site, name, ns=10)
    except InvalidTitleError:
        return None


def _header(code: Wikicode, site: BaseSite) -> Wikicode:
    """Select the header before the first heading or DYK archive block.

    Returns:
        Only the source region used by the verified shell module.
    """
    header = code.get_sections(include_lead=True, include_headings=False)[0]
    archive = Page(site, "Template:DYKEntry/archive")
    nodes = []
    for node in header.nodes:
        if (
            isinstance(node, Template)
            and _template_page(site, str(node.name).strip()) == archive
        ):
            break
        nodes.append(node)
    return mwparserfromhell.wikicode.Wikicode(nodes)


def banner_class(
    text: str | None,
    site: BaseSite,
    aliases: Iterable[str],
    *,
    shell_aliases: Iterable[str] = _DEFAULT_SHELL,
) -> str | None:
    """Read an explicit project class or its literal header inheritance.

    Args:
        text: Exact source text, or none for inaccessible content.
        site: Existing Chinese Wikipedia namespace metadata.
        aliases: Verified project banner and redirect titles.
        shell_aliases: Verified shell template and redirect titles.

    Returns:
        Canonical raw class, not-listed for an absent literal banner,
        or none for dynamic, ambiguous or unsupported evidence. Explicit
        nonblank project classes win; only missing/blank values inherit
        a single literal shell from the page header, wherever placed.
        With both grades missing, six yes/y criteria establish B;
        otherwise the banner is unassessed or remains ambiguous.
    """
    if text is None or site.dbName() != "zhwiki":
        return None
    code = _clean_code(text)
    candidates, dynamic = _matching_templates(code, site, aliases)
    if dynamic or len(candidates) > 1:
        return None
    if not candidates:
        return "not-listed"
    value = _literal_class(candidates[0])
    if value is not None and not value:
        shells, dynamic_shell = _matching_templates(
            _header(code, site),
            site,
            shell_aliases,
        )
        if dynamic_shell or len(shells) > 1:
            return None
        value = (
            _literal_class(shells[0], case_insensitive=True) if shells else ""
        )
        if value is not None and not value:
            criteria = _all_b_criteria(candidates[0])
            if criteria is None:
                return None
            value = "B" if criteria else "Unassessed"
    label = (
        assessment_profile("zhwiki", "电子游戏").resolve(value, "class")
        if value
        else None
    )
    return label.raw_value if label is not None else None
