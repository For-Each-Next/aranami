"""Extract quality milestones from talk-page wikitext.

Use ``extract_quality_milestone_from_wikitext`` with a quality target
to interpret the profile supplied by each language-specific service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import mwparserfromhell

from aranami.support.dates import parse_complete_date
from aranami.support.templates import (
    normalize_oldid,
    normalize_param_name,
    template_get_param,
    template_page,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pywikibot.site import BaseSite


@dataclass(frozen=True, slots=True)
class QualityTarget:
    """Describe one Wikipedia quality target."""

    code: str
    action_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualityMilestone:
    """Store a successful quality milestone."""

    date: str
    oldid: str | None
    source: str
    action_index: int | None = None


@dataclass(frozen=True, slots=True)
class QualityProfile:
    """Describe one wiki's history-template vocabulary.

    Attributes:
        action_aliases: Canonical action IDs and their accepted aliases.
        success_results: Accepted outcomes for each canonical action.
        history_names: Site-specific ArticleHistory template identities.
        uses_current_status: Honor an explicit currentstatus parameter.
        skips_ignored_actions: Exclude events marked actionXignore=yes.
    """

    action_aliases: Mapping[str, tuple[str, ...]]
    success_results: Mapping[str, set[str]]
    history_names: set[str]
    uses_current_status: bool = True
    skips_ignored_actions: bool = False


def normalize_status_code(value: str) -> str:
    """Normalize a quality status code.

    Args:
        value: Source value to normalize.

    Returns:
        Uppercase status code.
    """
    return value.strip().upper()


def normalize_action_name(value: str, profile: QualityProfile) -> str:
    """Normalize an Article history action name.

    Args:
        value: Source value to normalize.
        profile: Vocabulary and aliases for this wiki.

    Returns:
        Canonical history action name.
    """
    normalized = value.strip().upper()
    for canonical, aliases in profile.action_aliases.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return normalized


def normalize_result(value: str) -> str:
    """Normalize an Article history result.

    Args:
        value: Source value to normalize.

    Returns:
        Lowercase, whitespace-normalized history result.
    """
    return re.sub(r"\s+", " ", value.strip().lower())


def is_target_action(
    action: str,
    target: QualityTarget,
    profile: QualityProfile,
) -> bool:
    """Return whether an action belongs to a target.

    Args:
        action: Recorded history action.
        target: Quality status and its recognized aliases.
        profile: Vocabulary and aliases for this wiki.

    Returns:
        Whether the action belongs to the requested quality class.
    """
    normalized = normalize_action_name(action, profile)
    valid_names = {
        normalize_action_name(name, profile) for name in target.action_names
    }
    return normalized in valid_names


def is_success_result(
    action: str,
    result: str,
    target: QualityTarget,
    profile: QualityProfile,
) -> bool:
    """Return whether an action result grants the target status.

    Args:
        action: Recorded history action.
        result: Recorded result of that action.
        target: Quality status and its recognized aliases.
        profile: Vocabulary and aliases for this wiki.

    Returns:
        Whether the action successfully grants the target status.
    """
    if not is_target_action(action, target, profile):
        return False

    normalized_result = normalize_result(result)
    normalized_action = normalize_action_name(action, profile)
    return normalized_result in profile.success_results.get(
        normalized_action,
        set(),
    )


def choose_milestone(
    milestones: list[QualityMilestone],
    *,
    most_recent: bool,
) -> QualityMilestone:
    """Choose the earliest or latest successful milestone.

    Args:
        milestones: Successful milestones from one extraction method.
        most_recent: Select the latest successful action rather than the
            earliest.

    Returns:
        Earliest or latest milestone in history-action order.
    """
    if all(item.action_index is not None for item in milestones):
        ordered = sorted(
            milestones,
            key=lambda item: int(item.action_index or 0),
        )
    else:
        ordered = sorted(
            milestones,
            key=lambda item: (
                item.date,
                item.action_index if item.action_index is not None else -1,
            ),
        )
    return ordered[-1] if most_recent else ordered[0]


def extract_history_milestones(
    code: mwparserfromhell.wikicode.Wikicode,
    target: QualityTarget,
    site: BaseSite,
    profile: QualityProfile,
) -> list[QualityMilestone]:
    """Extract successful Article history milestones.

    Args:
        code: Parsed talk-page wikitext.
        target: Quality status and its recognized aliases.
        site: Site providing template title and namespace rules.
        profile: Vocabulary and aliases for this wiki.

    Returns:
        Successful history actions compatible with current status.
    """
    milestones: list[QualityMilestone] = []

    for template in code.filter_templates(recursive=True):
        name = template_page(template.name, site)
        if name not in {
            template_page(alias, site) for alias in profile.history_names
        }:
            continue

        current_status = normalize_status_code(
            template_get_param(template, "currentstatus") or "",
        )
        current_statuses = set(re.findall(r"[A-Z]+", current_status))
        if (
            profile.uses_current_status
            and current_statuses
            and target.code not in current_statuses
        ):
            continue

        indices = sorted({
            int(match.group(1))
            for parameter in template.params
            if (
                match := re.fullmatch(
                    r"action([1-9]\d*)",
                    normalize_param_name(parameter.name),
                )
            )
        })
        for index in indices:
            prefix = f"action{index}"
            if (
                profile.skips_ignored_actions
                and (
                    template_get_param(template, f"{prefix}ignore") or ""
                ).casefold()
                == "yes"
            ):
                continue
            action = template_get_param(template, prefix) or ""
            result = template_get_param(template, f"{prefix}result") or ""
            raw_date = template_get_param(template, f"{prefix}date")

            if not raw_date:
                continue

            if not is_success_result(action, result, target, profile):
                continue

            normalized_date = parse_complete_date(raw_date)
            if normalized_date is None:
                continue

            oldid = normalize_oldid(
                template_get_param(template, f"{prefix}oldid"),
            )
            milestones.append(
                QualityMilestone(
                    date=normalized_date.isoformat(),
                    oldid=oldid,
                    source="article_history",
                    action_index=index,
                ),
            )

    return milestones


def extract_quality_milestone_from_wikitext(
    text: str,
    target: QualityTarget,
    *,
    most_recent: bool,
    site: BaseSite,
    profile: QualityProfile,
) -> QualityMilestone | None:
    """Extract a successful quality milestone.

    Args:
        text: Complete source wikitext.
        target: Quality status and its recognized aliases.
        most_recent: Select the latest successful action rather than the
            earliest.
        site: Site providing template title and namespace rules.
        profile: Vocabulary and aliases for this wiki.

    Returns:
        Best supported milestone, or None when unresolved.
    """
    code = mwparserfromhell.parse(text)

    milestones = extract_history_milestones(code, target, site, profile)
    if milestones:
        return choose_milestone(milestones, most_recent=most_recent)
    return None
