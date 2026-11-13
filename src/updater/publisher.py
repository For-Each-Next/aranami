"""Save prepared wiki output after checking its original text."""

from __future__ import annotations

import logging

import pywikibot

from data.publication import ProposedEdit, PublicationOutcome
from shared.support.activity import report_activity

_LOGGER = logging.getLogger(__name__)


def save_edit(edit: ProposedEdit) -> str | PublicationOutcome:
    """Save a proposal after checking its original wiki content.

    Args:
        edit: Complete proposal, durably stored when structured.

    Returns:
        Confirmed ``saved`` or ``unchanged`` publication outcome.

    Raises:
        RuntimeError: Destination changed after report construction.
    """
    report_activity(
        "mediawiki",
        "page_text",
        wiki=edit.site.dbName(),
        target=edit.title,
        references=(("page", edit.title),),
        kind="checking",
    )
    page = pywikibot.Page(edit.site, edit.title)
    try:
        current = page.get(force=True)
    except pywikibot.exceptions.NoPageError:
        current = ""
    if current == edit.text:
        _LOGGER.info(
            "No changes needed for %s on %s.",
            edit.title,
            edit.site.dbName(),
        )
        return "unchanged"
    if edit.original_text is not None and current != edit.original_text:
        msg = f"Page changed during report construction: {edit.title}."
        raise RuntimeError(msg)
    _LOGGER.info(
        "Updating %s on %s: %s",
        edit.title,
        edit.site.dbName(),
        edit.summary,
    )
    page.text = edit.text
    report_activity(
        "mediawiki",
        "page_text",
        wiki=edit.site.dbName(),
        target=edit.title,
        references=(("page", edit.title),),
        kind="saving",
    )
    page.save(summary=edit.summary)
    _LOGGER.info("Saved %s on %s.", edit.title, edit.site.dbName())
    revision = page.latest_revision_id
    return PublicationOutcome(
        "saved",
        revision if type(revision) is int and revision > 0 else None,
    )
