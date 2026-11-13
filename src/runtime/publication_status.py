"""Measure destination attempts independently of task outcomes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from data.runtime.events import event_extra, logging_context
from data.runtime.status import get_boot_id, save_status, target_key, utc_now

if TYPE_CHECKING:
    import datetime as dt

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PublicationAttempt:
    """Own one target's queued, active, and confirmed publication facts.

    Attributes:
        site: Destination wiki database identity.
        title: Canonical target title.
        run_id: Parent execution identity.
        task_id: Report task responsible for this attempt.
        dry: Keep all observations in isolated preview state.
    """

    site: str
    title: str
    run_id: str
    task_id: str
    dry: bool = False
    attempt_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    registered_at: str = field(default_factory=utc_now, init=False)
    started_at: str | None = field(default=None, init=False)
    finished: bool = field(default=False, init=False)
    attempt_started: bool = field(default=False, init=False)
    generation: str | None = field(default=None, init=False)
    report_date: str | None = field(default=None, init=False)
    _clock: float = field(default_factory=perf_counter, init=False)
    _sequence: int = field(default=0, init=False)

    def _record(self) -> dict:
        """Build the complete current observation for this owner.

        Returns:
            An unpersisted record with unknown completion fields.
        """
        self._sequence += 1
        return {
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "boot_id": get_boot_id(),
            "task_id": self.task_id,
            "site": self.site,
            "title": self.title,
            "registered_at": self.registered_at,
            "started_at": self.started_at,
            "finished_at": None,
            "detected_at": None,
            "duration_seconds": None,
            "outcome": "running" if self.attempt_started else "queued",
            "confirmed_edits": None,
            "revision_id": None,
            "last_confirmed_save_at": None,
            "failure": None,
            "generation": self.generation,
            "report_date": self.report_date,
            "updated_at": utc_now(),
            "sequence": self._sequence,
        }

    def start(self) -> None:
        """Retain the queued or active target observation."""
        save_status(
            "publications",
            target_key(self.site, self.title),
            self._record(),
            dry=self.dry,
        )

    def start_attempt(self) -> None:
        """Start measuring target work after shared preparation."""
        if self.attempt_started or self.finished:
            return
        self.started_at = utc_now()
        self._clock = perf_counter()
        self.attempt_started = True
        self.start()
        with logging_context(run_id=self.run_id, attempt_id=self.attempt_id):
            _LOGGER.info(
                "Publication started: %s.",
                self.title,
                extra=event_extra(
                    "publication_started",
                    task_id=self.task_id,
                    site=self.site,
                    title=self.title,
                    dry=self.dry,
                ),
            )

    def finish(  # ruff: ignore[too-many-arguments]
        self,
        outcome: str,
        confirmed_edits: int | None,
        *,
        failure_reason: str | None = None,
        failure_date: dt.date | None = None,
        failure_context: dict[str, int] | None = None,
        revision_id: int | None = None,
    ) -> None:
        """Retain an actual publication result and measured duration.

        Args:
            outcome: Saved, unchanged, failed, deferred, or unconfirmed.
            confirmed_edits: Confirmed saves, including zero or unknown.
            failure_reason: Safe failure identifier, excluding messages.
            failure_date: Actually attempted report date, when known.
            failure_context: Bounded observed failure counts.
            revision_id: Actual revision returned by a confirmed save.
        """
        if self.finished:
            return
        record = self._record()
        record.update(
            outcome=outcome,
            finished_at=utc_now(),
            duration_seconds=max(0.0, perf_counter() - self._clock)
            if self.attempt_started
            else None,
            confirmed_edits=confirmed_edits,
            revision_id=revision_id,
        )
        failure = {
            key: value
            for key, value in (
                ("failure_reason", failure_reason),
                (
                    "failure_date",
                    failure_date.isoformat() if failure_date else None,
                ),
                ("failure_context", failure_context),
            )
            if value is not None
        }
        record["failure"] = failure or None
        save_status(
            "publications",
            target_key(self.site, self.title),
            record,
            dry=self.dry,
        )
        self.finished = True
        with logging_context(run_id=self.run_id, attempt_id=self.attempt_id):
            _LOGGER.info(
                "Publication finished: %s (%s).",
                self.title,
                outcome,
                extra=event_extra(
                    "publication_finished",
                    task_id=self.task_id,
                    site=self.site,
                    title=self.title,
                    outcome=outcome,
                    confirmed_edits=confirmed_edits,
                    revision_id=revision_id,
                    duration_seconds=record["duration_seconds"],
                    dry=self.dry,
                ),
            )
