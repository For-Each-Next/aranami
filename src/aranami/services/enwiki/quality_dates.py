"""Interpret enwiki Article history promotion events.

The action/result vocabulary follows Module:Article history/config
revision 1375495984.
Only events that grant a quality status supply a listing date.

Reference:
    https://en.wikipedia.org/w/index.php?oldid=1375495984
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import mwparserfromhell

from aranami.services.quality_milestones import (
    QualityMilestone,
    QualityProfile,
    QualityTarget,
    choose_milestone,
    extract_quality_milestone_from_wikitext,
)
from aranami.support.dates import parse_complete_date
from aranami.support.templates import (
    normalize_oldid,
    template_get_param,
    template_page,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pywikibot.site import BaseSite

QUALITY_TARGETS = {
    "FA": QualityTarget("FA", ("FAC",)),
    "FL": QualityTarget("FL", ("FLC",)),
    "GA": QualityTarget("GA", ("GAN", "GAR")),
}
PROFILE = QualityProfile(
    action_aliases={"GAN": ("GAC",)},
    success_results={
        "FAC": {"promoted", "pass", "passed"},
        "FLC": {"promoted", "pass", "passed"},
        "GAN": {"listed", "pass", "passed", "promoted"},
        "GAR": {"listed"},
    },
    history_names={"Template:Article history"},
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
    if site.dbName() != "enwiki":
        message = "This quality-date protocol requires enwiki."
        raise ValueError(message)
    milestone = extract_quality_milestone_from_wikitext(
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
    if milestone is not None or status != "GA":
        return milestone
    return _ga_template_milestone(
        text,
        site,
        aliases.get("ga", ("Template:GA",)),
        most_recent=most_recent,
    )


def _ga_template_milestone(
    text: str,
    site: BaseSite,
    aliases: Sequence[str],
    *,
    most_recent: bool,
) -> QualityMilestone | None:
    """Read the date and oldid supported by English Template:GA.

    Args:
        text: Current talk-page wikitext.
        site: English Wikipedia site providing title normalization.
        aliases: Canonical GA title and verified redirects.
        most_recent: Select the latest dated GA template.

    Returns:
        A dated GA promotion, or None when no complete date is present.
    """
    pages = {template_page(alias, site) for alias in aliases}
    milestones = []
    for template in mwparserfromhell.parse(text).filter_templates():
        if template_page(template.name, site) not in pages:
            continue
        parameter = "1" if template.has("1") else "date"
        if not template.has(parameter):
            continue
        date = parse_complete_date(template_get_param(template, parameter))
        if date is None:
            continue
        oldid = normalize_oldid(
            template_get_param(template, "oldid"),
        )
        milestones.append(
            QualityMilestone(
                date=date.isoformat(),
                oldid=oldid,
                source="status_template",
            ),
        )
    if milestones:
        return choose_milestone(milestones, most_recent=most_recent)
    return None
