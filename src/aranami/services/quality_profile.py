"""Describe wiki-specific inputs for shared quality-content analysis.

Per-wiki services supply a profile to the shared workflow. The profile
keeps project selection, assessment names, template contracts, and prose
rules together without exposing them to source adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pywikibot.site import BaseSite

    from aranami.services.quality_milestones import QualityMilestone
    from aranami.support.prose import ProseProfile


class MilestoneExtractor(Protocol):
    """Specify a site's callable promotion-date extraction contract."""

    def __call__(
        self,
        text: str,
        status: str,
        *,
        site: BaseSite,
        most_recent: bool = True,
        aliases: Mapping[str, Sequence[str]] | None = None,
    ) -> QualityMilestone | None:
        """Extract a successful listing date from a talk page.

        Args:
            text: Current talk-page wikitext.
            status: Normalized quality classification.
            site: Site providing title and template rules.
            most_recent: Select the latest successful listing event.
            aliases: Verified template titles keyed by profile role.

        Returns:
            The selected milestone, or None when unresolved.
        """
        ...


@dataclass(frozen=True, slots=True)
class QualityAnalysisProfile:
    """Inject wiki-specific metadata and content rules into analysis.

    Attributes:
        project_title: Project name stored in replica assessments.
        classes: Assessment classes selected from the source database.
        quality_names: Source quality names mapped to shared classes.
        importance_names: Source importance names mapped to shared
            names.
        templates: Canonical template titles keyed by extraction role.
        extract_milestone: This site's promotion-event interpreter.
        prose: This site's readable wikitext extraction rules.
        template_value_classes: Normalized quality classes that include
            all readable template values in prose measurements.
        language: Language used for word counting.
    """

    project_title: str
    classes: tuple[str, ...]
    quality_names: Mapping[str, str]
    importance_names: Mapping[str, str]
    templates: Mapping[str, str]
    extract_milestone: MilestoneExtractor
    prose: ProseProfile
    template_value_classes: frozenset[str]
    language: str
