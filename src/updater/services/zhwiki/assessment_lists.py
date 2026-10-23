"""Render assessment lists from backend-prepared members and reviews.

Use prepare_report with AssessmentData and the current destination text.
Rendering preserves unmanaged text and retains the same prepared rows.
"""

from __future__ import annotations

# Chinese report typography intentionally uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]
from typing import TYPE_CHECKING

import mwparserfromhell
from mwparserfromhell.nodes import Tag, Template, Wikilink

from backend.support.report_membership import MembershipReport
from shared.reports import ReportResult
from shared.support.regions import replace_by_tag
from shared.support.titles import title_sort_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from backend.services.zhwiki.assessment_lists import (
        AssessmentData,
        AssessmentList,
    )

_REVIEW_ICONS = {
    "acc": "A candidate.svg",
    "bpan": "Bplus candidate.svg",
    "ppr": "Cvg peerreview icon.png",
}


def _review_icon(grade: str) -> str:
    """Select the configured candidate icon for output rendering.

    Args:
        grade: Supported review-grade code.

    Returns:
        Verified icon filename used by the review wrapper.

    Raises:
        ValueError: The requested review grade is unsupported.
    """
    try:
        return _REVIEW_ICONS[grade.strip().casefold()]
    except KeyError as error:
        message = f"Unknown review grade: {grade!r}."
        raise ValueError(message) from error


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
        else _review_icon(config.review_grade)
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
    normalized = sorted(set(titles), key=title_sort_key)
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
    data: AssessmentData,
    original_text: str,
) -> MembershipReport:
    """Render and retain the supplied complete assessment observations.

    Args:
        data: Prepared settings, members, review links, and rows.
        original_text: Destination text whose ranges are replaced.

    Returns:
        Updated content, stable article IDs, and retained report tables.
    """
    config = data.config
    return MembershipReport(
        text=update_text(
            config,
            original_text,
            tuple(data.members),
            data.review_targets,
        ),
        members=data.members,
        result=ReportResult(
            family="assessment-lists",
            tables={"members": data.rows},
            metadata={
                "category": config.category_title,
                "icon_template": config.icon_template,
                "review_grade": config.review_grade,
            },
            provenance={
                "wiki": data.wiki,
                "sources": [
                    "category-membership",
                    "project-assessments",
                    "review-talk-text",
                ],
            },
            calculated_at=data.calculated_at,
        ),
    )
