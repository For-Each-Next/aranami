"""Publish rendered report proposals through backend execution state.

Jobs read their destination pages and render backend-prepared metadata.
The backend owns task state, artifacts, logs, and data retention;
this module performs only the destination save and summary attribution.
"""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

import pywikibot

from backend.support.execution import RuntimeContext, routine_run
from backend.support.logs import open_run_log
from updater.jobs._edits import ProposedEdit
from updater.support.edit_summary import EditSummary

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOGGER = logging.getLogger(__name__)


class JobContext(RuntimeContext[ProposedEdit]):
    """Publish destination edits using backend-owned lifecycle state."""

    def publish(self, edit: ProposedEdit) -> bool:
        """Format attribution and delegate durable publication tracking.

        Args:
            edit: Rendered content for this job's destination page.

        Returns:
            Whether a live edit was saved or verified unchanged.
        """
        edit = replace(
            edit,
            summary=EditSummary.with_attribution(edit.summary),
        )
        return self.publish_proposal(edit, self._save_edit)

    @staticmethod
    def _save_edit(edit: ProposedEdit) -> str:
        """Save a proposal after checking its original wiki content.

        Args:
            edit: Complete proposal, durably stored when structured.

        Returns:
            Confirmed ``saved`` or ``unchanged`` publication outcome.

        Raises:
            RuntimeError: Destination changed after report construction.
        """
        if edit.original_text == edit.text:
            _LOGGER.info(
                "No changes needed for %s on %s.",
                edit.title,
                edit.site.dbName(),
            )
            return "unchanged"
        page = pywikibot.Page(edit.site, edit.title)
        current = page.text
        if edit.original_text is not None and current != edit.original_text:
            msg = f"Page changed during report construction: {edit.title}."
            raise RuntimeError(msg)
        if current == edit.text:
            _LOGGER.info(
                "No changes needed for %s on %s.",
                edit.title,
                edit.site.dbName(),
            )
            return "unchanged"
        _LOGGER.info(
            "Updating %s on %s: %s",
            edit.title,
            edit.site.dbName(),
            edit.summary,
        )
        page.text = edit.text
        page.save(summary=edit.summary)
        _LOGGER.info("Saved %s on %s.", edit.title, edit.site.dbName())
        return "saved"


@contextmanager
def job_run(
    name: str,
    *,
    dry: bool = False,
    context: JobContext | None = None,
) -> Iterator[JobContext]:
    """Delegate a routine lifecycle and publish through its context.

    Args:
        name: Configured routine identifier.
        dry: Suppress writes for a standalone invocation.
        context: Parent execution context, whose mode overrides dry.

    Yields:
        Destination publisher backed by the backend runtime.
    """
    with open_run_log():
        active = context or JobContext(
            pywikibot.Site("zh", "wikipedia"),
            dt.datetime.now(dt.UTC).date(),
            dry,
        )
        try:
            with routine_run(name, active):
                if active.dry:
                    print(  # ruff: ignore[print]
                        f"Dry run: executing updater.jobs.{name}.run()",
                        flush=True,
                    )
                yield active
        finally:
            if context is None:
                active.write_report()
