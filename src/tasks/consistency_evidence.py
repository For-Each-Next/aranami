"""Build consistency evidence from already observed wiki source pages.

Use the collector's existing client for title and namespace rules. Saved
records and their inert validation remain in ``data.consistency``.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

from pywikibot import Page

from data.consistency import Evidence
from shared.support.titles import page_title, page_url

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

    from shared.sources.wiki import WikiReader


def source_evidence(read: WikiReader, title: str) -> Evidence:
    """Describe a revision already obtained by a source client.

    Args:
        read: Reader that obtained this source text.
        title: Source page whose revision evidence is needed.

    Returns:
        Source identity with unknown revisions and timestamps left null.
    """
    metadata = read.sources.get(title, {})
    title = page_title(read.site, title)
    url = metadata.get("url") or page_url(read.site, title)
    match = re.fullmatch(
        r"https://zh\.wikipedia\.org/w/index\.php\?oldid=([1-9][0-9]*)",
        url,
    )
    return Evidence(title, url, int(match[1]) if match else None, None)


def issue(  # ruff: ignore[too-many-arguments]
    *,
    site: BaseSite,
    kind: str,
    check_id: str,
    title: str,
    page_id: int,
    source: Evidence,
    checked: Evidence,
    observed_at: str,
    expected_template: str = "",
    expected_field: str | None = None,
    event_date: str | None = None,
    assessment_class: str | None = None,
    assessment_importance: str | None = None,
) -> dict[str, object]:
    """Build one finding while retaining independent source evidence.

    Args:
        site: Existing client supplying title and namespace rules.
        kind: Supported disagreement category.
        check_id: Stable identity of the verified check.
        title: Current canonical article title.
        page_id: Stable identity of the project article.
        source: Evidence establishing the expected observation.
        checked: Page checked for the expected metadata or membership.
        observed_at: Actual aware UTC collection time.
        expected_template: Verified template, or empty for list checks.
        expected_field: Verified missing history field, when applicable.
        event_date: Proven appearance date, never an inferred date.
        assessment_class: Raw project class, or none if unknown.
        assessment_importance: Raw importance, or none if unknown.

    Returns:
        Serializable finding with stable identity and explicit unknowns.
    """
    page = Page(site, title)
    title = page.title()
    identity = json.dumps(
        [
            kind,
            check_id,
            page_id,
            event_date,
            expected_template,
            expected_field,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "id": hashlib.sha256(identity.encode()).hexdigest(),
        "kind": kind,
        "check_id": check_id,
        "title": title,
        "page_id": page_id,
        "url": page_url(site, title),
        "talk_url": page.toggleTalkPage().full_url(),
        "source_title": source.title,
        "source_url": source.url,
        "source_revision": source.revision,
        "source_timestamp": source.timestamp,
        "checked_title": checked.title,
        "checked_url": checked.url,
        "checked_revision": checked.revision,
        "checked_timestamp": checked.timestamp,
        "observed_at": observed_at,
        "expected_template": expected_template,
        "expected_field": expected_field,
        "event_date": event_date,
        "assessment_class": assessment_class,
        "assessment_importance": assessment_importance,
    }
