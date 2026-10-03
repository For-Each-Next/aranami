"""Interpret zhwiki Article history promotion events.

The action/result vocabulary follows Module:Article history/config
revision 92259335.
Only events that grant a quality status supply a listing date.

Reference:
    https://zh.wikipedia.org/w/index.php?oldid=92259335
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from aranami.services.quality_milestones import (
    QualityMilestone,
    QualityProfile,
    QualityTarget,
    extract_quality_milestone_from_wikitext,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pywikibot.site import BaseSite

QUALITY_TARGETS = {
    "FA": QualityTarget("FA", ("FAC",)),
    "FL": QualityTarget("FL", ("FLC",)),
    "GA": QualityTarget("GA", ("GAN",)),
}
PROFILE = QualityProfile(
    action_aliases={"FAC": ("FAN",), "FLC": ("FLN",), "GAN": ("GAC",)},
    success_results={
        "FAC": {"promoted", "pass", "passed", "入選", "入选"},
        "FLC": {"promoted", "pass", "passed", "入選", "入选"},
        "GAN": {"listed", "pass", "passed", "promoted", "入選", "入选"},
    },
    history_names={"Template:Article history"},
    uses_current_status=False,
    skips_ignored_actions=True,
)


def extract_milestone(
    text: str,
    status: str,
    *,
    site: BaseSite,
    most_recent: bool = True,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> QualityMilestone | None:
    """Extract a successful milestone using this site's templates.

    Args:
        text: Current talk-page wikitext.
        status: FA, FL, or GA classification.
        site: Site owning this language-specific template protocol.
        most_recent: Select the latest successful promotion action.
        aliases: Verified titles keyed by history and, on enwiki, ga.

    Returns:
        The best supported milestone, or None for an unresolved date.

    Raises:
        ValueError: If the site does not match this module's protocol.
    """
    aliases = aliases or {}
    if site.dbName() != "zhwiki":
        message = "This quality-date protocol requires zhwiki."
        raise ValueError(message)
    return extract_quality_milestone_from_wikitext(
        text,
        QUALITY_TARGETS[status],
        most_recent=most_recent,
        site=site,
        profile=replace(
            PROFILE,
            history_names=set(
                aliases.get("history", ("Template:Article history",)),
            ),
        ),
    )
