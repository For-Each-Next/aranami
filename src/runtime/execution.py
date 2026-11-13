"""Own report execution state, durable output, previews, and status.

The runtime tracks routine lifecycles and stores complete generations
before a frontend saves an edit. Publication callbacks contain the wiki
write; all runtime files and outcome snapshots are managed here.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from data.observations import project_snapshot_references
from data.publication import PublicationOutcome
from data.reports import ReportStore
from data.runtime.events import (
    current_log_context,
    event_extra,
    logging_context,
)
from data.runtime.failure_status import exception_failure_details
from data.runtime.logs import open_run_log
from data.runtime.progress import (
    progress_updates,
    report_progress,
    validate_progress,
)
from data.runtime.status import (
    MAX_STAGES,
    get_boot_id,
    record_dataset,
    save_status,
    target_key,
    utc_now,
)
from runtime.interrupted_runs import execution_lease
from runtime.publication_status import PublicationAttempt
from shared.sources.template_aliases import template_alias_cache
from shared.support.activity import activity_observer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from pywikibot.site import BaseSite

    from data.reports import ReportResult

_LOGGER = logging.getLogger(__name__)
_TRIGGER: ContextVar[str] = ContextVar("execution_trigger", default="manual")


@contextmanager
def execution_trigger(trigger: str) -> Iterator[None]:
    """Bind the actual invocation cause for newly started report work.

    Args:
        trigger: Scheduled, manual, recovery, or unknown.

    Yields:
        Control while nested report executions inherit the cause.
    """
    token = _TRIGGER.set(trigger)
    try:
        yield
    finally:
        _TRIGGER.reset(token)


def _source_result(result: ReportResult) -> ReportResult:
    """Attach consumed daily source identities before durable storage.

    Retained results with explicit snapshot provenance preserve it when
    republished. Newly prepared results inherit only observations read
    by the active pass, including exact hashes and actual source times.

    Returns:
        Original result or a copy with daily membership provenance.
    """
    references = project_snapshot_references()
    if not references or "project_snapshots" in result.provenance:
        return result
    return replace(
        result,
        provenance={
            **result.provenance,
            "project_snapshots": references,
            "membership_policy": "current-daily-cohort",
        },
    )


class Publication(Protocol):
    """Describe rendered output accepted by runtime execution storage.

    Attributes:
        site: Destination wiki used only for its identity.
        title: Destination title selected from configuration.
        text: Complete proposed destination content.
        summary: Formatted publication summary.
        tags: Report identifiers attached to the proposal.
        original_text: Destination content observed before rendering.
        report_key: Stable key for retaining complete report data.
        result: Complete prepared tables and source metadata.
    """

    site: BaseSite
    title: str
    text: str
    summary: str
    tags: tuple[str, ...]
    original_text: str | None
    report_key: str | None
    result: ReportResult | None


def _monotonic_time() -> float:
    """Read the current monotonic clock when a record is created.

    Returns:
        Current timestamp for measuring execution durations.
    """
    return perf_counter()


@dataclass(slots=True)
class _TaskResult:
    """Record one routine's lifecycle and local output references.

    Attributes:
        name: Independently runnable routine name.
        started_at: UTC time when the routine entered its lifecycle.
        finished_at: UTC completion time, including failed routines.
        status: Routine outcome, independent of its proposal count.
        elapsed_seconds: Duration measured with the monotonic clock.
        edit_indices: Positions of proposals in the parent edit list.
        notes: Routine-specific delegated actions or diagnostic notes.
        updated: Successful saves, or unknown after delegated writes.
        started_monotonic: Clock start shared with publication timing.
        run_id: Unique identity guarding the latest routine status.
        progress: Optional bounded activity for this running routine.
        report_date: Latest daily report confirmed saved or unchanged.
        failure_reason: Safe identifier for failed or deferred work.
        failure_date: Observed report date attempted before stopping.
        failure_context: Optional observed missing-data counts.
        history: Explicit stage facts for this invocation's history.
        history_truncated: Whether intermediate stages were omitted.
    """

    name: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    status: str = "running"
    elapsed_seconds: float = 0
    edit_indices: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    updated: int | None = 0
    started_monotonic: float = field(
        default_factory=_monotonic_time,
        init=False,
        repr=False,
    )
    run_id: str = field(default_factory=lambda: uuid4().hex)
    report_date: dt.date | None = None
    failure_reason: str | None = None
    failure_date: dt.date | None = None
    failure_context: dict[str, int] | None = None
    progress: dict[str, object] | None = field(
        default_factory=lambda: {
            "phase": "preparing",
            "report_date": None,
            "current": None,
            "total": None,
        },
    )
    history: list[dict[str, object]] = field(default_factory=list)
    history_truncated: bool = False
    sequence: int = 0
    trigger: str = "unknown"
    source_activity: tuple[str, str, str, str, str] | None = None
    source_progress_at: float = 0


def _routine_result(task: _TaskResult, *, dry: bool) -> dict[str, object]:
    """Describe publication outcomes without counting proposals.

    Args:
        task: Completed routine with its confirmed save count.
        dry: Whether this execution suppressed intentional writes.

    Returns:
        Versioned diagnostic record of actual publication outcomes.
    """
    if task.status in {"failed", "deferred"}:
        status = task.status
    elif task.status != "success" or task.updated is None:
        status = "unknown"
    else:
        status = "updated" if task.updated else "no_update"
    return {
        "schema": 1,
        "routine": task.name,
        "dry": dry,
        "status": status,
        "updated": task.updated,
        "duration_seconds": round(task.elapsed_seconds, 3),
    }


def _save_task_status(task: _TaskResult, *, dry: bool) -> None:
    """Persist a live lifecycle state without changing job execution.

    Args:
        task: Current routine state with its unique execution identity.
        dry: Keep preview observations separate from production.
    """
    task.sequence += 1
    failure = None
    if task.failure_reason is not None:
        failure = {"failure_reason": task.failure_reason}
        if task.failure_date is not None:
            failure["failure_date"] = task.failure_date.isoformat()
        if task.failure_context is not None:
            failure["failure_context"] = dict(task.failure_context)
    save_status(
        "executions",
        task.run_id,
        {
            "run_id": task.run_id,
            "boot_id": get_boot_id(),
            "task_id": task.name,
            "kind": "report",
            "trigger": task.trigger,
            "started_at": task.started_at.isoformat(),
            "finished_at": task.finished_at.isoformat()
            if task.finished_at
            else None,
            "detected_at": None,
            "duration_seconds": round(task.elapsed_seconds, 3)
            if task.finished_at
            else None,
            "outcome": "succeeded"
            if task.status == "success"
            else task.status,
            "progress": task.progress if task.finished_at is None else None,
            "failure": failure,
            "stages": list(task.history),
            "stages_truncated": task.history_truncated,
            "report_date": task.report_date.isoformat()
            if task.report_date
            else None,
            "updated_count": task.updated if task.finished_at else None,
            "updated_at": utc_now(),
            "sequence": task.sequence,
        },
        dry=dry,
    )


def _save_progress(
    task: _TaskResult,
    progress: dict[str, object],
    *,
    dry: bool,
) -> None:
    """Update activity without changing a run's lifecycle identity.

    Args:
        task: Exact routine whose execution installed this callback.
        progress: Bounded stage and optional report/counter update.
        dry: Whether the execution must omit live state writes.
    """
    if task.finished_at is not None or task.status != "running":
        return
    if progress["report_date"] is None and task.progress is not None:
        progress = {**progress, "report_date": task.progress["report_date"]}
    try:
        progress = validate_progress(progress)
    except ValueError:
        _LOGGER.warning("Ignoring invalid routine activity", exc_info=True)
        return
    if progress == task.progress:
        return
    if progress["phase"] in {"preparing", "publishing"}:
        task.source_activity = None
    previous = task.progress
    task.progress = progress
    if progress["phase"].startswith("pageviews_"):
        _history_stage(
            task,
            progress["phase"],
            dry=dry,
        )
    elif progress["phase"] not in {
        "publishing",
        "source_read",
        "source_loaded",
        "checking",
        "saving",
    } and (previous is None or progress["phase"] != previous["phase"]):
        _history_stage(
            task,
            "preparing",
            dry=dry,
        )
    _save_task_status(task, dry=dry)


def _history_stage(
    task: _TaskResult,
    kind: str,
    *,
    dry: bool,
    title: str = "",
    source: dict[str, object] | None = None,
) -> None:
    """Record a real execution boundary independently of diagnostics.

    Args:
        task: Invocation that owns the observation.
        kind: Explicit stage identifier localized by website renderers.
        dry: Whether this preview must omit live history.
        title: Optional destination title, never diagnostic error text.
        source: Public source identity for an actual read or save.
    """
    observed = (
        task.started_at
        if kind == "started"
        else (task.finished_at or dt.datetime.now(dt.UTC))
    )
    progress = (
        task.progress
        if kind.startswith("pageviews_")
        and task.progress is not None
        and task.progress["phase"] == kind
        else None
    )
    entry = {
        "time": observed.isoformat(),
        "kind": kind,
        "title": title[:512],
        **({"source": source} if source is not None else {}),
        **({"progress": dict(progress)} if progress is not None else {}),
    }
    if kind == "pageviews_aggregate" and progress and task.history:
        previous = task.history[-1]
        previous_progress = previous.get("progress") or {}
        if (
            previous["kind"] == kind
            and previous_progress.get("report_date") == progress["report_date"]
            and previous_progress.get("total") == progress["total"]
        ):
            task.history[-1] = entry
            _save_task_status(task, dry=dry)
            return
    task.history.append(entry)
    if len(task.history) > MAX_STAGES:
        del task.history[1]
        task.history_truncated = True
    _save_task_status(task, dry=dry)
    _LOGGER.info(
        "Execution stage: %s.",
        kind,
        extra=event_extra(
            "routine_stage",
            routine=task.name,
            stage=kind,
            title=title,
            dry=dry,
        ),
    )


def _publication_date(edit: Publication) -> dt.date | None:
    """Read a report date without trusting arbitrary result metadata.

    Args:
        edit: Proposal whose prepared result may identify a report date.

    Returns:
        The calendar date, or none when absent or invalid.
    """
    if edit.result is None:
        return None
    value = edit.result.metadata.get("data_date")
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _observe_activity(
    task: _TaskResult,
    kind: str,
    source: dict[str, object],
    *,
    dry: bool,
) -> None:
    """Retain an actual source operation in progress and history.

    Args:
        task: Invocation owning the actual operation.
        kind: Read, destination check, or imminent save boundary.
        source: Bounded public source facts from the adapter.
        dry: Isolate preview status.
    """
    phase = (task.progress or {}).get("phase", "")
    pageviews_phase = phase.startswith("pageviews_")
    if (
        pageviews_phase
        and phase in {"pageviews_download", "pageviews_aggregate"}
        and kind == "source_read"
        and source["provider"] == "pageviews"
        and source["dataset"] == "article_traffic"
    ):
        # The task reports cumulative batches instead of per-page reads.
        return
    identity = (
        source["provider"],
        source["dataset"],
        source["wiki"],
        source.get("purpose", ""),
        json.dumps(source.get("scope", {}), sort_keys=True),
    )
    repeated = kind == "source_read" and task.source_activity == identity
    now = perf_counter()
    if repeated and now - task.source_progress_at < 1:
        return
    task.source_activity = identity if kind == "source_read" else None
    task.source_progress_at = now
    if not pageviews_phase:
        _save_progress(
            task,
            {
                "phase": kind,
                "report_date": None,
                "current": None,
                "total": None,
                "source": source,
            },
            dry=dry,
        )
    if not repeated:
        _history_stage(task, kind, dry=dry, source=source)


def _markdown_value(value: str) -> str:
    """Keep metadata on one Markdown line without changing artifacts.

    Args:
        value: Untrusted page metadata or a diagnostic message.

    Returns:
        Escaped, whitespace-normalized Markdown text.
    """
    value = " ".join(value.split())
    for character in ("\\", "`", "*", "_", "[", "]", "<", ">"):
        value = value.replace(character, f"\\{character}")
    return value


def _utc_time(value: dt.datetime) -> str:
    """Format an aware timestamp as explicit UTC report metadata.

    Args:
        value: Recorded run or routine timestamp.

    Returns:
        Human-readable UTC timestamp.
    """
    return value.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass(slots=True)
class RuntimeContext[EditT: Publication]:
    """Collect one run's outputs and select its publication behavior.

    Attributes:
        site: Existing authenticated Chinese Wikipedia site.
        today: UTC date captured at the start of the run.
        dry: Whether edits are recorded locally instead of saved.
        edits: Ordered proposed edits, including unchanged proposals.
        notes: Delegated actions, failures, and other execution notes.
        started_at: UTC timestamp captured before routine execution.
        finished_at: UTC timestamp when run output is finalized.
        tasks: Ordered routine outcomes, including failed jobs.
    """

    site: BaseSite
    today: dt.date
    dry: bool
    edits: list[EditT] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    started_at: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(dt.UTC),
    )
    finished_at: dt.datetime | None = None
    tasks: list[_TaskResult] = field(default_factory=list)
    _active_task: _TaskResult | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _task_notes_from: int = field(default=0, init=False, repr=False)
    _run_id: str = field(
        default_factory=lambda: (
            current_log_context().get("run_id") or uuid4().hex
        ),
        init=False,
    )
    _report_updates: dict[str, PublicationAttempt] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _report_generations: dict[tuple[str, str], str] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @property
    def report_run_id(self) -> str:
        """Identify the routine shared by its report generations."""
        return self._active_task.run_id if self._active_task else self._run_id

    def register_targets(self, titles: Iterable[str]) -> None:
        """Queue each destination before reading or preparing its data.

        Args:
            titles: Configured destinations attempted by this routine.
        """
        for title in titles:
            self._report_update(self.site.dbName(), title)

    def _report_update(self, site: str, title: str) -> PublicationAttempt:
        """Register or retrieve a destination's current lifecycle.

        Returns:
            Runtime-owned update handle for this routine and target.
        """
        key = target_key(site, title)
        update = self._report_updates.get(key)
        if update is None or update.run_id != self.report_run_id:
            update = PublicationAttempt(
                site,
                title,
                self.report_run_id,
                self._active_task.name if self._active_task else "manual",
                self.dry,
            )
            self._report_updates[key] = update
            update.start()
        return update

    @contextmanager
    def report_attempt(self, title: str) -> Iterator[None]:
        """Measure one destination's preparation and publication.

        Start after shared preparation or another destination's work.
        Preserve explicit publication outcomes when later cleanup fails;
        otherwise record preparation failures when they occur.

        Args:
            title: Registered destination on this context's wiki.

        Yields:
            Control while this destination is prepared and published.
        """
        update = self._report_update(self.site.dbName(), title)
        update.start_attempt()
        report_progress("preparing")
        try:
            with logging_context(attempt_id=update.attempt_id):
                yield
        except BaseException as error:
            if not update.finished:
                update.finish(
                    "failed",
                    confirmed_edits=0,
                    failure_date=self._attempted_report_date(),
                    **exception_failure_details(error),
                )
            raise

    def _attempted_report_date(self) -> dt.date | None:
        """Read an attempted date independently of confirmed saves.

        Returns:
            Date from active progress, or none when unknown.
        """
        task = self._active_task
        day = (
            task.progress.get("report_date")
            if task and task.progress
            else None
        )
        return dt.date.fromisoformat(day) if isinstance(day, str) else None

    def finish_report(
        self,
        title: str,
        outcome: str,
        *,
        confirmed_edits: int | None,
        error: BaseException | None = None,
    ) -> None:
        """Record a runtime check or externally delegated outcome.

        Args:
            title: Registered destination on this context's wiki.
            outcome: Actual status, independent of diagnostic logs.
            confirmed_edits: Saved count, or null for delegated writes.
            error: Cause whose safe facts accompany a failed outcome.
        """
        self._report_update(self.site.dbName(), title).finish(
            outcome,
            confirmed_edits,
            **(exception_failure_details(error) if error is not None else {}),
        )
        self.record_stage(
            {
                "saved": "saved",
                "unchanged": "no_update",
                "failed": "save_failed",
                "unconfirmed": "unconfirmed",
            }.get(outcome, "deferred"),
            title=title,
        )

    def record_stage(self, kind: str, *, title: str = "") -> None:
        """Retain an observed stage for the active invocation.

        Args:
            kind: Explicit stage from the runtime history contract.
            title: Optional destination being processed.
        """
        if self._active_task is not None:
            _history_stage(self._active_task, kind, dry=self.dry, title=title)

    def confirm_report_date(self, day: dt.date | None) -> None:
        """Retain this routine's latest verified daily report date.

        Call only after a save or an unchanged-report check succeeds.
        Failed attempts and preparation progress cannot establish it.

        Args:
            day: Actual report date, or none when no date is known.
        """
        task = self._active_task
        if day is not None and task is not None:
            task.report_date = max(day, task.report_date or day)

    def start_task(self, name: str) -> _TaskResult:
        """Start one independently executed routine in this run.

        Args:
            name: Routine identifier for logs and summary output.

        Returns:
            Mutable result finalized by the routine lifecycle.

        Raises:
            RuntimeError: A routine is already active in this context.
        """
        if self._active_task is not None:
            msg = "Routine lifecycles cannot overlap in one run context."
            raise RuntimeError(msg)
        task = _TaskResult(
            name,
            dt.datetime.now(dt.UTC),
            trigger=_TRIGGER.get(),
        )
        self.tasks.append(task)
        self._active_task = task
        self._task_notes_from = len(self.notes)
        return task

    def finish_task(self, elapsed_seconds: float) -> None:
        """Finalize active routine timing and associate its notes.

        Args:
            elapsed_seconds: Duration measured by the lifecycle clock.

        Raises:
            RuntimeError: No routine lifecycle is currently active.
        """
        if self._active_task is None:
            msg = "A completed result requires an active routine."
            raise RuntimeError(msg)
        self._active_task.finished_at = dt.datetime.now(dt.UTC)
        if self._active_task.status in {"failed", "deferred"}:
            self._active_task.failure_date = self._attempted_report_date()
        self._active_task.progress = None
        self._active_task.elapsed_seconds = elapsed_seconds
        self._active_task.notes.extend(self.notes[self._task_notes_from :])
        for update in self._report_updates.values():
            if (
                update.run_id == self._active_task.run_id
                and not update.finished
            ):
                update.finish(
                    {
                        "failed": "failed",
                        "deferred": "deferred",
                    }.get(
                        self._active_task.status,
                        "unconfirmed"
                        if update.attempt_started
                        else "deferred",
                    ),
                    confirmed_edits=0 if not update.attempt_started else None,
                    failure_reason=self._active_task.failure_reason,
                    failure_date=self._active_task.failure_date,
                    failure_context=self._active_task.failure_context,
                )
        self._active_task = None

    def defer(
        self,
        reason: str,
        *,
        error: BaseException | None = None,
    ) -> None:
        """Defer the active routine while allowing later jobs to run.

        Args:
            reason: Concise explanation recorded under the routine.
            error: Optional cause retained as safe public reason facts.

        Raises:
            RuntimeError: No routine lifecycle is currently active.
        """
        if self._active_task is None:
            msg = "A deferred result requires an active routine."
            raise RuntimeError(msg)
        self._active_task.status = "deferred"
        if error is not None:
            details = exception_failure_details(error)
            self._active_task.failure_reason = details["failure_reason"]
            self._active_task.failure_context = details.get("failure_context")
        self.notes.append(reason)

    def mark_updates_unknown(self) -> None:
        """Mark delegated writes whose actual edit count is unconfirmed.

        Call before a delegated request that may write even if its
        response fails. Outside an active routine there is no lifecycle
        result to change. Confirmed direct saves cannot establish a
        complete count once delegated writes make the total unknown.
        """
        if self._active_task is not None:
            self._active_task.updated = None

    def routine_results(self) -> tuple[dict[str, object], ...]:
        """Expose recorded outcomes without reading diagnostic logs.

        Returns:
            Structured terminal outcomes in this run's execution order.
        """
        return tuple(
            _routine_result(task, dry=self.dry) for task in self.tasks
        )

    def _proposal_lines(self, index: int, path: Path) -> list[str]:
        """Write exact proposal text and return its concise metadata.

        Args:
            index: Zero-based proposal position in the shared edit list.
            path: Markdown report whose unique stem names the companion.

        Returns:
            Nested Markdown bullets linking the flat wikitext file.
        """
        edit = self.edits[index]
        artifact = path.with_name(f"{path.stem}-{index + 1:03d}.wikitext")
        artifact.write_text(edit.text, encoding="utf-8", newline="")
        tags = _markdown_value(", ".join(edit.tags) or "proposed")
        return [
            f"    - proposal {index + 1}: {_markdown_value(edit.title)}",
            f"      - site: {_markdown_value(edit.site.dbName())}",
            f"      - summary: {_markdown_value(edit.summary)}",
            f"      - tags: {tags}",
            f"      - wikitext: [{artifact.name}]({artifact.name})",
        ]

    def _task_lines(
        self,
        task: _TaskResult,
        index: int,
        path: Path,
    ) -> list[str]:
        """Render a routine outcome and all of its output links.

        Args:
            task: Completed routine lifecycle and proposal references.
            index: One-based routine order within the complete run.
            path: Markdown report receiving the routine summary.

        Returns:
            Nested routine, timing, proposal, and diagnostic bullets.
        """
        lines = [
            f"  - task {index}: {_markdown_value(task.name)} — {task.status}",
            f"    - start: {_utc_time(task.started_at)}",
            f"    - end: {_utc_time(task.finished_at or self.started_at)}",
            f"    - elapsed: {task.elapsed_seconds:.3f} seconds",
        ]
        for edit_index in task.edit_indices:
            lines.extend(self._proposal_lines(edit_index, path))
        lines.extend(
            f"    - note: {_markdown_value(note)}" for note in task.notes
        )
        return lines

    def write_report(self) -> Path | None:
        """Write a concise routine summary and exact linked wikitext.

        All files are UTF-8 and live directly under ``dry-run/`` in the
        current directory. Wikitext filenames share the report's unique
        stem and use an edit number instead of untrusted titles.

        Returns:
            Artifact path, or ``None`` for a live run.
        """
        if not self.dry:
            return None
        self.finished_at = self.finished_at or dt.datetime.now(dt.UTC)
        directory = Path.cwd() / "dry-run"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"aranami-{timestamp}-{uuid4().hex[:8]}.md"
        tasks = list(self.tasks)
        assigned = {index for task in tasks for index in task.edit_indices}
        remaining = sorted(set(range(len(self.edits))) - assigned)
        if remaining or not tasks:
            tasks.append(
                _TaskResult(
                    "proposed edits",
                    self.started_at,
                    self.finished_at,
                    "success",
                    (self.finished_at - self.started_at).total_seconds(),
                    remaining,
                    self.notes if not self.tasks else [],
                ),
            )
        failures = sum(task.status in {"failed", "deferred"} for task in tasks)
        status = "success"
        if failures:
            status = "failed" if failures == len(tasks) else "partly success"
        lines = [
            f"# Aranami dry run — {self.today} UTC",
            "",
            f"- start: {_utc_time(self.started_at)}",
            f"- end: {_utc_time(self.finished_at)}",
            f"- status: {status}",
        ]
        for index, task in enumerate(tasks, start=1):
            lines.extend(self._task_lines(task, index, path))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _LOGGER.info("Dry-run report: %s.", path)
        return path

    def publish_proposal(
        self,
        edit: EditT,
        save: Callable[[EditT], str | PublicationOutcome],
    ) -> bool:
        """Retain a complete proposal before delegating its publication.

        Args:
            edit: Rendered proposal and its complete prepared data.
            save: Updater callback that saves only this destination.

        Returns:
            Whether a live result was saved or verified unchanged.

        """
        update = self._report_update(edit.site.dbName(), edit.title)
        if not update.attempt_started:
            update.start_attempt()
        try:
            confirmed = self._store_and_publish(edit, save)
        except BaseException as error:
            if not update.finished:
                update.finish(
                    "failed",
                    confirmed_edits=0,
                    failure_date=self._attempted_report_date(),
                    **exception_failure_details(error),
                )
            raise
        if not update.finished:
            update.finish(
                "deferred",
                confirmed_edits=0,
            )
        return confirmed

    def _store_and_publish(
        self,
        edit: EditT,
        save: Callable[[EditT], str | PublicationOutcome],
    ) -> bool:
        """Persist required data before entering the save callback.

        Returns:
            Whether publication was confirmed or verified unchanged.

        Raises:
            ValueError: Prepared report data has no stable storage key.
        """
        report_progress("publishing", report_date=_publication_date(edit))
        self.edits.append(edit)
        if self._active_task is not None:
            self._active_task.edit_indices.append(len(self.edits) - 1)
        if edit.result is not None:
            if edit.report_key is None:
                msg = "Structured report publication requires an internal key."
                raise ValueError(msg)
            store = ReportStore(dry=self.dry)
            generation_key = self.report_run_id, edit.report_key
            with store.locked(edit.report_key):
                bundle = store.write(
                    edit.report_key,
                    _source_result(edit.result),
                    edit.text,
                    run_id=self.report_run_id,
                    started_at=self.started_at,
                    target={"site": edit.site.dbName(), "title": edit.title},
                    locked=True,
                    previous_generation=self._report_generations.get(
                        generation_key,
                    ),
                )
                if not bundle.selected:
                    self.notes.append(
                        f"Superseded report generation: {edit.title}.",
                    )
                    return False
                self._report_generations[generation_key] = str(
                    bundle.manifest["generation"],
                )
                record_dataset(bundle.manifest, dry=self.dry)
                update = self._report_update(edit.site.dbName(), edit.title)
                update.generation = str(bundle.manifest["generation"])
                day = _publication_date(edit)
                update.report_date = day.isoformat() if day else None
                update.start()
                self.record_stage("database_updated", title=edit.title)
                if self.dry:
                    _LOGGER.info(
                        "Would update %s on %s: %s",
                        edit.title,
                        edit.site.dbName(),
                        edit.summary,
                    )
                    return False
                store.set_publication(
                    bundle,
                    "unconfirmed",
                    confirmed_edits=None,
                    locked=True,
                )
                try:
                    outcome = self._publish_edit(edit, save)
                except Exception as error:
                    try:
                        store.set_publication(
                            bundle,
                            "unconfirmed",
                            error=f"{type(error).__name__}: {error}",
                            confirmed_edits=None,
                            locked=True,
                        )
                    except Exception:
                        _LOGGER.exception(
                            "Could not record failed report publication.",
                        )
                    raise
                store.set_publication(
                    bundle,
                    outcome,
                    confirmed_edits=int(outcome == "saved"),
                    locked=True,
                )
            return True
        if self.dry:
            _LOGGER.info(
                "Would update %s on %s: %s",
                edit.title,
                edit.site.dbName(),
                edit.summary,
            )
            return False
        self._publish_edit(edit, save)
        return True

    def _publish_edit(
        self,
        edit: EditT,
        save: Callable[[EditT], str | PublicationOutcome],
    ) -> str:
        """Count a confirmed save returned by the updater callback.

        Args:
            edit: Complete proposal being published.
            save: Callback that returns a confirmed publication outcome.

        Returns:
            Confirmed saved or unchanged outcome.
        """
        update = self._report_update(edit.site.dbName(), edit.title)
        try:
            with logging_context(attempt_id=update.attempt_id):
                observed = save(edit)
            outcome = (
                observed.outcome
                if isinstance(observed, PublicationOutcome)
                else observed
            )
        except BaseException as error:
            self.mark_updates_unknown()
            self.record_stage("save_failed", title=edit.title)
            update.finish(
                "failed",
                confirmed_edits=None,
                failure_date=self._attempted_report_date(),
                **exception_failure_details(error),
            )
            raise
        self.record_stage(
            "saved" if outcome == "saved" else "no_update",
            title=edit.title,
        )
        update.finish(
            outcome,
            confirmed_edits=int(outcome == "saved"),
            revision_id=observed.revision_id
            if isinstance(observed, PublicationOutcome)
            else None,
        )
        self.confirm_report_date(_publication_date(edit))
        if (
            outcome == "saved"
            and self._active_task is not None
            and self._active_task.updated is not None
        ):
            self._active_task.updated += 1
        return outcome


@contextmanager
def routine_run[EditT: Publication](
    name: str,
    active: RuntimeContext[EditT],
) -> Iterator[RuntimeContext[EditT]]:
    """Record one routine's lifecycle in logs and structured status.

    Args:
        name: Routine name included in the shared daily log.
        active: Runtime context holding this routine's state.

    Yields:
        Context through which the job publishes proposed edits.
    """
    with open_run_log(), ExitStack() as resources:
        logger = _LOGGER
        task = active.start_task(name)
        resources.enter_context(execution_lease(task.run_id, dry=active.dry))
        resources.enter_context(
            template_alias_cache(
                Path.cwd()
                / ("dry-run/cache" if active.dry else "cache")
                / "template-aliases",
            ),
        )
        resources.enter_context(
            logging_context(
                run_id=task.run_id,
                parent_run_id=current_log_context().get("run_id"),
            ),
        )
        _history_stage(task, "started", dry=active.dry)
        started = task.started_monotonic
        logger.info(
            "Started %s%s.",
            name,
            " preview" if active.dry else "",
            extra=event_extra("routine_started", routine=name, dry=active.dry),
        )
        _save_task_status(task, dry=active.dry)
        try:
            with (
                activity_observer(
                    lambda kind, source: _observe_activity(
                        task,
                        kind,
                        source,
                        dry=active.dry,
                    ),
                ),
                progress_updates(
                    lambda progress: _save_progress(
                        task,
                        progress,
                        dry=active.dry,
                    ),
                ),
            ):
                yield active
        except BaseException as error:
            task.status = "failed"
            details = exception_failure_details(error)
            task.failure_reason = details["failure_reason"]
            task.failure_context = details.get("failure_context")
            active.notes.append(
                f"Routine {name} failed: {type(error).__name__}: {error}. "
                "See the daily log.",
            )
            raise
        else:
            if task.status == "running":
                task.status = "success"
        finally:
            active.finish_task(perf_counter() - started)
            _history_stage(task, "finished", dry=active.dry)
            _save_task_status(task, dry=active.dry)
            result = _routine_result(task, dry=active.dry)
            logger.log(
                {
                    "failed": logging.ERROR,
                    "deferred": logging.WARNING,
                }.get(task.status, logging.INFO),
                "Finished %s%s: %s. Confirmed edits: %s. "
                "Duration: %.3f seconds.",
                name,
                " preview" if active.dry else "",
                {
                    "updated": "updated",
                    "no_update": "no changes needed",
                    "failed": "failed",
                    "deferred": "waiting to retry",
                    "unknown": "outcome unconfirmed",
                }[result["status"]],
                result["updated"]
                if result["updated"] is not None
                else "unknown",
                result["duration_seconds"],
                extra={
                    "routine_result": result,
                    "run_id": task.run_id,
                    **event_extra(
                        "routine_finished",
                        routine=name,
                        dry=active.dry,
                        outcome="succeeded"
                        if task.status == "success"
                        else task.status,
                        updated=result["updated"],
                        duration_seconds=result["duration_seconds"],
                    ),
                },
                exc_info=task.status == "failed",
            )
