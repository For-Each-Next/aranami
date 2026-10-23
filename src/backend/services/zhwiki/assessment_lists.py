"""Prepare assessment members, reviews, and report input settings.

Use prepare_data with the caller's current destination text and source
configuration. Results preserve complete identities and assessments
without formatting or publishing wiki output.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from pywikibot import Page

from backend.support.report_membership import membership_changes
from shared.sources.quarry.projects import (
    category_members,
    query_pages_by_wikiproject,
)
from shared.sources.wiki import read_pages
from shared.support.regions import region_content, region_options
from shared.support.titles import title_sort_key

if TYPE_CHECKING:
    from collections.abc import Mapping

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
    """Describe a review's recognized heading prefixes."""

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
        (r"甲[級级]", r"A[級级]", r"A-Class", r"甲表", r"AL-Class"),
    ),
    "bpan": ReviewInfo(
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
    "ppr": ReviewInfo((r"",)),
}


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


@dataclass(frozen=True, slots=True)
class AssessmentChanges:
    """Describe identity-aware additions and removals without wording.

    Attributes:
        added: Current titles absent from earlier membership.
        removed: Previous titles absent from current membership.
        count: Complete current member count.
    """

    added: tuple[str, ...]
    removed: tuple[str, ...]
    count: int


@dataclass(frozen=True, slots=True)
class AssessmentData:
    """Retain complete assessment facts and interpreted report settings.

    Attributes:
        config: Configuration with the effective category setting.
        members: Current article titles and stable page IDs.
        previous_members: Original members with available cached IDs.
        preview_members: Original members with enriched identities.
        review_targets: Current talk-page titles and review anchors.
        rows: Complete identities, reviews, and project assessments.
        changes: Identity-aware membership changes for presentation.
        wiki: Source wiki database name.
        calculated_at: Actual UTC calculation time.
    """

    config: AssessmentList
    members: Mapping[str, int | None]
    previous_members: Mapping[str, int | None]
    preview_members: Mapping[str, int | None]
    review_targets: Mapping[str, str]
    rows: pl.DataFrame
    changes: AssessmentChanges
    wiki: str
    calculated_at: datetime


def compare_members(
    original_text: str,
    text: str,
    *,
    previous_members: Mapping[str, int | None] | None = None,
    current_members: Mapping[str, int | None] | None = None,
) -> AssessmentChanges:
    """Compare prepared identities or interpret the supplied list text.

    Args:
        original_text: Previously published list content.
        text: Updated content when current identities are unavailable.
        previous_members: Earlier IDs, or parse original titles.
        current_members: Current IDs, or parse updated titles.

    Returns:
        Additions, removals, and complete current member count.
    """
    previous = (
        article_members(original_text)
        if previous_members is None
        else previous_members
    )
    current = (
        article_members(text) if current_members is None else current_members
    )
    added, removed = membership_changes(previous, current)
    return AssessmentChanges(tuple(added), tuple(removed), len(current))


def read_assessments(site: BaseSite, project: str) -> pl.DataFrame:
    """Read reusable assessment observations for the selected project.

    Args:
        site: Wiki whose replica supplies project assessments.
        project: Concrete project selected by the caller.

    Returns:
        Complete source rows shared by independently prepared lists.
    """
    return query_pages_by_wikiproject(site, project)


def prepare_data(
    site: BaseSite,
    config: AssessmentList,
    original_text: str,
    *,
    assessments: pl.DataFrame | None = None,
    previous_members: Mapping[str, int | None] | None = None,
) -> AssessmentData:
    """Fetch category members and current review anchors for one list.

    Args:
        site: Chinese Wikipedia site and its replica identity.
        config: Category and list-rendering configuration.
        original_text: Destination settings and earlier member text.
        assessments: Project metadata, or null for unknown grades.
        previous_members: Cached IDs matching the destination text.

    Returns:
        Complete rows, review links, settings, and membership changes.

    Raises:
        ValueError: If a configured category title is empty.
    """
    original_members = article_members(original_text)
    previous = {
        title: (previous_members or {}).get(title)
        for title in original_members
    }
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
        key=title_sort_key,
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
    rows = pl.DataFrame(
        {
            "title": titles,
            "page_id": [member_ids[title] for title in titles],
            "review_target": [targets.get(title) for title in titles],
        },
        schema={
            "title": pl.String,
            "page_id": pl.Int64,
            "review_target": pl.String,
        },
    )
    if assessments is not None:
        rows = rows.join(
            assessments.select(
                "page_id",
                pl.col("pa_class").alias("class"),
                pl.col("pa_importance").alias("importance"),
            ),
            on="page_id",
            how="left",
        )
    else:
        rows = rows.with_columns(
            pl.lit(None, dtype=pl.String).alias("class"),
            pl.lit(None, dtype=pl.String).alias("importance"),
        )
    added, removed = membership_changes(previous, member_ids)
    return AssessmentData(
        config=config,
        members=member_ids,
        previous_members=previous,
        preview_members={
            title: identifier or member_ids.get(title)
            for title, identifier in previous.items()
        },
        review_targets=targets,
        rows=rows,
        changes=AssessmentChanges(
            tuple(added),
            tuple(removed),
            len(member_ids),
        ),
        wiki=site.dbName(),
        calculated_at=datetime.now(UTC),
    )
