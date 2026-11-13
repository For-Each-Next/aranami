"""Publish rendered report proposals through runtime execution state.

Jobs read their destination pages and render runtime-prepared metadata.
The runtime owns task state, artifacts, logs, and data retention;
this module performs only the destination save and summary attribution.
"""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

import pywikibot

from data.publication import ProposedEdit
from data.runtime.logs import open_run_log
from runtime.execution import RuntimeContext, routine_run
from shared.identity import configure_pywikibot
from updater.edit_summary import EditSummary
from updater.publisher import save_edit

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOGGER = logging.getLogger(__name__)


class JobContext(RuntimeContext[ProposedEdit]):
    """Publish destination edits using runtime-owned lifecycle state."""

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
        return self.publish_proposal(edit, save_edit)


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
        Destination publisher backed by the runtime runtime.
    """
    if context is None:
        configure_pywikibot()
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
                        f"Dry run: executing runtime.jobs.{name}.run()",
                        flush=True,
                    )
                yield active
        finally:
            if context is None:
                active.write_report()
