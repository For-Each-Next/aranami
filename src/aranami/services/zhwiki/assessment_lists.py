"""Transform category-backed video-game assessment list content.

Replica metadata selects articles; preloaded current page text supplies
review headings and preserves content outside the managed sections.
"""

from __future__ import annotations

# Chinese report typography intentionally uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]
import logging
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import mwparserfromhell
from mwparserfromhell.nodes import Tag, Template, Wikilink
from pywikibot import Page

from aranami.sources.quarry.projects import category_members
from aranami.sources.wiki import read_pages
from aranami.support.regions import (
    region_content,
    region_options,
    replace_by_tag,
)
from aranami.support.report_membership import (
    MembershipReport,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pywikibot.site import BaseSite

logger = logging.getLogger(__name__)
_REVIEW_HEADING_LEVEL = 2
_REVIEW_SUFFIXES = (
    r"[評评][審审選选級级]",
    r"(?<![A-Za-z])review(?![A-Za-z])",
    r"(?<![A-Za-z])assessment(?![A-Za-z])",
)


@dataclass(frozen=True, slots=True)
class ReviewInfo:
    """Describe a review's icon and recognized heading prefixes."""

    icon: str
    prefixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssessmentList:
    """Describe one assessment list and its icon or review grade."""

    category_title: str
    icon_template: str | None = None
    review_grade: str | None = None

    def __post_init__(self) -> None:
        """Require exactly one icon source.

        Raises:
            ValueError: If neither or both icon sources are configured.
        """
        if (self.icon_template is None) == (self.review_grade is None):
            message = "Configure exactly one icon template or review grade."
            raise ValueError(message)


REVIEW_INFO = {
    "acc": ReviewInfo(
        "A candidate.svg",
        (r"甲[級级]", r"A[級级]", r"A-Class", r"甲表", r"AL-Class"),
    ),
    "bpan": ReviewInfo(
        "Bplus candidate.svg",
        (
            r"乙上",
            r"Bplus",
            r"乙\+",
            r"乙[級级]",
            r"B[級级]",
            r"B-Class",
            r"乙表",
            r"BL",
        ),
    ),
    "ppr": ReviewInfo("Cvg peerreview icon.png", (r"",)),
}


def _sort_key(title: str) -> tuple[str, str]:
    """Return a stable case-insensitive title ordering.

    Args:
        title: Normalized article title.

    Returns:
        Folded title and exact title for deterministic tie breaking.
    """
    return title.casefold(), title


def _review_info(grade: str) -> ReviewInfo:
    """Resolve a configured review grade.

    Args:
        grade: Review-grade code.

    Returns:
        Matching icon and heading configuration.

    Raises:
        ValueError: If the grade has no supported configuration.
    """
    normalized = grade.strip().casefold()
    try:
        return REVIEW_INFO[normalized]
    except KeyError as error:
        message = f"Unknown review grade: {grade!r}."
        raise ValueError(message) from error


def review_heading(text: str, grade: str) -> str | None:
    """Find the latest matching level-two review heading.

    Args:
        text: Current talk-page wikitext.
        grade: Review-grade code determining recognized headings.

    Returns:
        Last matching heading, or none to link the talk page itself.
    """
    info = _review_info(grade)
    patterns = tuple(
        re.compile(rf".*{prefix}.*{suffix}.*", re.IGNORECASE)
        for prefix in info.prefixes
        for suffix in _REVIEW_SUFFIXES
    )
    headings = mwparserfromhell.parse(text).filter_headings(recursive=False)
    for heading in reversed(headings):
        title = str(heading.title).replace("_", " ").strip()
        if heading.level == _REVIEW_HEADING_LEVEL and any(
            pattern.search(title) for pattern in patterns
        ):
            return title
    return None


def article_members(text: str) -> dict[str, int | None]:
    """Read article titles from the managed list for identity lookup.

    Args:
        text: Report content containing the managed list section.

    Returns:
        Article titles with unknown IDs, excluding review links.

    Raises:
        ValueError: If list markers are ambiguous or missing.
    """
    content = region_content(text, "assessment-list")
    if content is None:
        message = "Missing managed-region markers for 'assessment-list'."
        raise ValueError(message)
    members: dict[str, int | None] = {}
    for line in content.splitlines():
        if line.lstrip().startswith("#"):
            row = mwparserfromhell.parse(line)
            links = row.filter_wikilinks()
            if links:
                title = str(links[0].title).split("#", 1)[0]
                normalized = title.replace("_", " ").lstrip(":").strip()
                members[normalized] = None
    return members


def article_titles(text: str) -> set[str]:
    """Read article links from the managed numbered list.

    Args:
        text: Report content containing the managed list section.

    Returns:
        Unique article titles, excluding subsequent review links.

    Raises:
        ValueError: If list markers are ambiguous or missing.
    """  # ruff: ignore[docstring-extraneous-exception]
    return set(article_members(text))


def _icon(config: AssessmentList) -> Template:
    """Build the configured standard or candidate icon.

    Args:
        config: List configuration with exactly one icon source.

    Returns:
        Editable icon template node.
    """
    template = Template("icon" if config.review_grade is None else "icon3")
    icon = (
        config.icon_template
        if config.review_grade is None
        else _review_info(config.review_grade).icon
    )
    template.add("1", icon or "", showkey=False)
    return template


def _render_list(
    config: AssessmentList,
    titles: Sequence[str],
    review_targets: Mapping[str, str],
) -> str:
    """Render article and review links with parser-created nodes.

    Args:
        config: Standard or review-list configuration.
        titles: Sorted article titles.
        review_targets: Talk-page destinations keyed by article title.

    Returns:
        Numbered list body including a visible empty-list placeholder.
    """
    if not titles:
        return f"\n# {_icon(config)} （無）\n"
    lines: list[str] = []
    for title in titles:
        code = mwparserfromhell.parse("# ")
        code.append(_icon(config))
        code.append(" ")
        code.append(Wikilink(title))
        if config.review_grade is not None:
            review = Wikilink(review_targets[title], "评审")
            code.append(Tag("small", f"〔{review}〕"))
        lines.append(str(code))
    return "\n" + "\n".join(lines) + "\n"


def update_text(
    config: AssessmentList,
    original_text: str,
    titles: Sequence[str],
    review_targets: Mapping[str, str],
) -> str:
    """Update one assessment list in the supplied page content.

    Args:
        config: List-rendering configuration.
        original_text: Preloaded report text with count/list markers.
        titles: Current article titles from the category query.
        review_targets: Review-link destinations by article title.

    Returns:
        Updated wikitext preserving all unmanaged text.

    Raises:
        ValueError: If the count or list markers are invalid.
    """  # ruff: ignore[docstring-extraneous-exception]
    normalized = sorted(set(titles), key=_sort_key)
    text = replace_by_tag(
        "assessment-count",
        str(len(normalized)),
        original_text,
    )
    return replace_by_tag(
        "assessment-list",
        _render_list(
            config,
            normalized,
            review_targets,
        ),
        text,
    )


def prepare_report(
    site: BaseSite,
    config: AssessmentList,
    original_text: str,
) -> MembershipReport:
    """Fetch category members and current review anchors for one list.

    Args:
        site: Chinese Wikipedia site and its replica identity.
        config: Category and list-rendering configuration.
        original_text: Current page content to transform.

    Returns:
        Updated content and stable article IDs for local tracking.

    Raises:
        ValueError: If a configured category title is empty.
    """
    options = region_options(original_text, "assessment-list")
    category = options.get("category", config.category_title).strip()
    if not category:
        message = "Assessment-list category must not be empty."
        raise ValueError(message)
    config = replace(config, category_title=category)
    members = category_members(site, config.category_title, namespace=1)
    member_ids = {
        str(title).replace("_", " ").lstrip(":").strip(): identifier
        for title, identifier in members.select(
            "page_title",
            "article_page_id",
        ).iter_rows()
    }
    titles = sorted(
        member_ids,
        key=_sort_key,
    )
    logger.info("Found %d articles in %s", len(titles), config.category_title)
    targets: dict[str, str] = {}
    if config.review_grade is not None:
        talk_titles = [
            Page(site, title).toggleTalkPage().title() for title in titles
        ]
        for title, page in zip(
            titles,
            read_pages(site, talk_titles),
            strict=True,
        ):
            heading = review_heading(page.text, config.review_grade)
            targets[title] = (
                f"{page.title()}#{heading}" if heading else page.title()
            )
    return MembershipReport(
        text=update_text(config, original_text, titles, targets),
        members=member_ids,
    )


def prepare_text(
    site: BaseSite,
    config: AssessmentList,
    original_text: str,
) -> str:
    """Fetch current members and return clean assessment-list wikitext.

    Args:
        site: Chinese Wikipedia site and its replica identity.
        config: Category and list-rendering configuration.
        original_text: Current page content to transform.

    Returns:
        Updated content with current members and review links.

    Raises:
        ValueError: If a configured category title is empty.
    """  # ruff: ignore[docstring-extraneous-exception]
    return prepare_report(site, config, original_text).text
