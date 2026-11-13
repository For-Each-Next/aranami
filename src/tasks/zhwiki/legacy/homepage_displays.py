"""Read audited historical Chinese homepage display formats.

Early Chinese good-article archives store introduction prose rather than
an introduction-page transclusion. Their primary subject is the first
article-bearing element, explicitly bolded, after optional image,
conversion metadata or introductory plain text. Later bold links and
image captions do not identify additional subjects. Named introductions,
summary wrappers, introduction redirects and bounded reuse of another
daily source retain the outer display date in the caller.

This contract was verified on 9 November 2026 against
Wikipedia:优良条目/2006年9月12日 (89356884), /2006年9月19日
(19446787), /2006年9月21日 (8968653), and /2009年8月14日 (10868644).
The delinked bold subject at /2010年12月24日 (92425867) is corroborated
by its display-day source (15181986) and predecessor (92383653).
Summary parameter 1 was checked against Wikipedia:典范条目/摘要
(52777241) and Wikipedia:优良条目/摘要 (31145059);
Image/File2 (70152701/90512748), NoteTA/module
(52804175/95445558) and 單雙書名號轉換 (90570034, documentation
89363314) establish the supported image, conversion and title wrappers.
Revision links use
https://zh.wikipedia.org/w/index.php?oldid=REVISION.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import mwparserfromhell
import pywikibot
from mwparserfromhell.nodes import Comment, Tag, Template, Text, Wikilink
from pywikibot.site import Namespace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from mwparserfromhell.nodes import Node
    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

_MAX_INDIRECTIONS = 8
_DAILY = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日")
_HIDDEN_CONVERSION = re.compile(r"-\{H\|(?:(?!-\{|\}-).)*\}-", re.DOTALL)
_SUMMARY_ROOTS = {
    "Wikipedia:特色条目/",
    "Wikipedia:特色條目/",
    "Wikipedia:优良条目/",
    "Wikipedia:優良條目/",
}


def _article_title(value: str, site: BaseSite) -> str:
    """Normalize an explicitly identified local article title.

    Returns:
        Canonical main-namespace target without a section.

    Raises:
        ValueError: The title identifies another namespace or wiki.
    """
    target = value.strip().lstrip(":").partition("#")[0]
    page = pywikibot.Page(site, target)
    if page.site != site or page.namespace() != Namespace.MAIN:
        message = "A historical homepage subject is not a local article."
        raise ValueError(message)
    return page.title()


def _template_name(template: Template, site: BaseSite) -> str:
    """Normalize a local template's title without following redirects.

    Returns:
        The unprefixed local template name, or an empty unrelated name.
    """
    page = pywikibot.Page(
        site,
        str(template.name).strip(),
        ns=Namespace.TEMPLATE,
    )
    if page.site != site or page.namespace() != Namespace.TEMPLATE:
        return ""
    return page.title(with_ns=False)


def _prefix(node: Node, site: BaseSite, depth: int = 0) -> bool:
    """Recognize non-subject material before an introductory bold link.

    Returns:
        Whether text or verified non-subject markup can precede the
        first article-bearing element without supplying evidence.
    """
    if isinstance(node, Comment):
        return True
    if isinstance(node, Text):
        return True
    if isinstance(node, Template):
        return _template_name(node, site) in {"Image", "NoteTA"}
    if isinstance(node, Tag):
        name = str(node.tag).casefold()
        return name == "noinclude" or (
            name in {"div", "span"}
            and depth < _MAX_INDIRECTIONS
            and all(
                _prefix(child, site, depth + 1)
                for child in node.contents.nodes
            )
        )
    if isinstance(node, Wikilink):
        target = str(node.title).strip()
        return (
            not target.startswith(":")
            and pywikibot.Page(site, target).namespace() == Namespace.FILE
        )
    return False


def _lead_link(node: Node) -> Wikilink | None:
    """Find the explicitly bold article link in a lead subject node.

    Returns:
        One bold subject link, or none for ambiguous or unbolded prose.
    """
    if isinstance(node, Tag) and str(node.tag).casefold() in {"b", "strong"}:
        links = node.contents.filter_wikilinks(recursive=False)
        return links[0] if len(links) == 1 else None
    if (
        isinstance(node, Wikilink)
        and node.text is not None
        and any(
            str(tag.tag).casefold() in {"b", "strong"}
            for tag in node.text.filter_tags(recursive=False)
        )
    ):
        return node
    return None


def _introduction_subject(code: Wikicode, site: BaseSite, depth: int) -> str:
    """Read the first explicitly bold subject with bounded wrappers.

    Args:
        code: Parsed daily display source.
        site: Existing wiki site used for title normalization.
        depth: Number of nested title wrappers already followed.

    Returns:
        The article explicitly named by the introductory bold subject.

    Raises:
        ValueError: The lead or its wrapper depth is unsupported.
    """
    code = mwparserfromhell.parse(_HIDDEN_CONVERSION.sub("", str(code)))
    for tag in list(code.filter_tags(recursive=False)):
        if (
            str(tag.tag).casefold() == "b"
            and "\n" in str(tag.contents)
            and not str(tag.contents).strip()
        ):
            # MediaWiki resets apostrophe bold state at line boundaries.
            code.replace(tag, "\n'''")
    code = mwparserfromhell.parse(str(code))
    for node in code.nodes:
        if _prefix(node, site):
            continue
        if (
            isinstance(node, Template)
            and _template_name(node, site) == "單雙書名號轉換"
            and node.has(1)
        ):
            if depth >= _MAX_INDIRECTIONS:
                message = "Homepage title wrappers exceed their depth bound."
                raise ValueError(message)
            return _introduction_subject(node.get(1).value, site, depth + 1)
        link = _lead_link(node)
        if link is not None:
            return _article_title(str(link.title), site)
        if (
            isinstance(node, Tag)
            and str(node.tag).casefold() in {"b", "strong"}
            and all(isinstance(child, Text) for child in node.contents.nodes)
            and str(node.contents).strip()
        ):
            return _article_title(str(node.contents), site)
        break
    message = "The historical homepage introduction has no unique subject."
    raise ValueError(message)


def introduction_subject(code: Wikicode, site: BaseSite) -> str:
    """Read the explicitly bold subject of a historical introduction.

    Args:
        code: Parsed daily display source, without template expansion.
        site: Existing wiki site used for title normalization.

    Returns:
        The primary article identified by a bold title or link.
    """
    return _introduction_subject(code, site, 0)


def _introduction(
    value: str,
    roots: Iterable[str],
    site: BaseSite,
) -> tuple[str, str] | None:
    """Recognize a verified local introduction or dated display title.

    Returns:
        Canonical source root and its complete suffix, or none.
    """
    page = pywikibot.Page(site, value.strip(), ns=Namespace.TEMPLATE)
    if page.site != site or page.namespace() != Namespace.PROJECT:
        return None
    name = page.title(with_section=False)
    return next(
        (
            (root, name.removeprefix(root))
            for root in roots
            if name.startswith(root)
        ),
        None,
    )


def _summary_subject(
    root: str,
    template: Template | None,
    site: BaseSite,
) -> str:
    """Read a verified summary wrapper's single positional subject.

    Returns:
        The article title supplied as parameter 1.

    Raises:
        ValueError: The wrapper or required parameter is unsupported.
    """
    parameters = (
        [
            parameter
            for parameter in template.params
            if str(parameter.name).strip() == "1"
        ]
        if template is not None
        else []
    )
    if root not in _SUMMARY_ROOTS or len(parameters) != 1:
        message = "A homepage summary has no unique subject parameter."
        raise ValueError(message)
    return _article_title(str(parameters[0].value), site)


def _display_subject(
    code: Wikicode,
    roots: Iterable[str],
    site: BaseSite,
    read_daily: Callable[[str], str] | None,
    seen: tuple[str, ...],
) -> str:
    """Resolve explicit subjects while bounding daily-source reuse.

    Returns:
        The article named by the display's verified source format.

    Raises:
        ValueError: The source is ambiguous, cyclic or unsupported.
    """
    candidates = [
        (template, target)
        for template in code.filter_templates(recursive=False)
        if (target := _introduction(str(template.name), roots, site))
        is not None
    ]
    if len(candidates) > 1:
        message = "A historical display names multiple introductions."
        raise ValueError(message)
    target = None
    template = None
    if candidates:
        template, target = candidates[0]
    else:
        for comment in list(code.filter_comments()):
            code.remove(comment)
        redirect = site.redirect_regex.match(str(code))
        if redirect is not None:
            target = _introduction(redirect[1], roots, site)
            if target is None:
                message = "A homepage redirect has no verified introduction."
                raise ValueError(message)
    if target is None:
        return introduction_subject(code, site)
    root, suffix = target
    if suffix in {"s", "摘要"}:
        return _summary_subject(root, template, site)
    if _DAILY.fullmatch(suffix):
        title = root + suffix
        if (
            read_daily is None
            or title in seen
            or len(seen) >= _MAX_INDIRECTIONS
        ):
            message = "Daily homepage source reuse is unavailable or cyclic."
            raise ValueError(message)
        return _display_subject(
            mwparserfromhell.parse(read_daily(title)),
            roots,
            site,
            read_daily,
            (*seen, title),
        )
    return _article_title(suffix, site)


def display_subject(
    code: Wikicode,
    roots: Iterable[str],
    site: BaseSite,
    *,
    read_daily: Callable[[str], str] | None = None,
) -> str:
    """Read one explicit primary subject from a historical display.

    Args:
        code: Parsed exact source revision.
        roots: Verified introduction roots, including old spellings.
        site: Existing wiki site for title and redirect normalization.
        read_daily: Optional text reader for another dated source.

    Returns:
        The primary article, independent of its current award status.
        The caller retains the original daily source date and revision.
    """
    return _display_subject(code, tuple(roots), site, read_daily, ())
