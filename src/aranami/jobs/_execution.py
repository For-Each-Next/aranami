"""Share output and execution context between independent report jobs.

Each job exposes ``run(dry=...)``. The package runner supplies one
context so all jobs contribute to one dry-run report and daily log, with
plain wikitext files for the proposed edits.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

import pywikibot

from aranami.support.edit_summary import EditSummary
from aranami.support.logs import log_project_event, open_run_log
from aranami.support.routine_status import write_routine_status

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pywikibot.site import BaseSite

    from aranami.jobs._edits import ProposedEdit

_LOGGER = logging.getLogger(__name__)


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
        dry: Whether public status must omit this preview entirely.
    """
    if dry:
        return
    record = _routine_result(task, dry=False)
    record.pop("schema")
    record.update({
        "started_at": task.started_at.isoformat(),
        "finished_at": (
            task.finished_at.isoformat() if task.finished_at else None
        ),
        "run_id": task.run_id,
    })
    if task.finished_at is None:
        record.update({
            "status": "running",
            "updated": None,
            "duration_seconds": None,
        })
    try:
        write_routine_status(record)
    except Exception as error:
        _LOGGER.warning(
            "Routine status persistence failed (%s).",
            type(error).__name__,
            exc_info=True,
        )


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
class JobContext:
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
    edits: list[ProposedEdit] = field(default_factory=list)
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
    _started_monotonic: float = field(
        default_factory=_monotonic_time,
        init=False,
        repr=False,
    )

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
        task = _TaskResult(name, dt.datetime.now(dt.UTC))
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
        self._active_task.elapsed_seconds = elapsed_seconds
        self._active_task.notes.extend(self.notes[self._task_notes_from :])
        self._active_task = None

    def defer(self, reason: str) -> None:
        """Defer the active routine while allowing later jobs to run.

        Args:
            reason: Concise explanation recorded under the routine.

        Raises:
            RuntimeError: No routine lifecycle is currently active.
        """
        if self._active_task is None:
            msg = "A deferred result requires an active routine."
            raise RuntimeError(msg)
        self._active_task.status = "deferred"
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

    def publish(self, edit: ProposedEdit) -> None:
        """Record a proposal and save changed text during live runs.

        Append elapsed time from the routine's start until this proposal
        is ready, before checking or saving its target. Publications
        outside a routine use the context's creation time. Live edits
        and dry-run proposals carry the same final summary.

        Args:
            edit: Proposal to record or intentionally publish.

        Raises:
            RuntimeError: The target changed after report construction.
        """
        started = (
            self._active_task.started_monotonic
            if self._active_task is not None
            else self._started_monotonic
        )
        edit = replace(
            edit,
            summary=EditSummary.with_execution_time(
                edit.summary,
                perf_counter() - started,
            ),
        )
        self.edits.append(edit)
        if self._active_task is not None:
            self._active_task.edit_indices.append(len(self.edits) - 1)
        if self.dry:
            _LOGGER.info("Proposed edit: %s (%s).", edit.title, edit.tags)
            return
        if edit.original_text == edit.text:
            _LOGGER.info("Unchanged: %s.", edit.title)
            return
        page = pywikibot.Page(edit.site, edit.title)
        current = page.text
        if edit.original_text is not None and current != edit.original_text:
            msg = f"Page changed during report construction: {edit.title}."
            raise RuntimeError(msg)
        if current == edit.text:
            _LOGGER.info("Unchanged: %s.", edit.title)
            return
        page.text = edit.text
        page.save(summary=edit.summary)
        if (
            self._active_task is not None
            and self._active_task.updated is not None
        ):
            self._active_task.updated += 1
        _LOGGER.info("Saved: %s.", edit.title)

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


@contextmanager
def job_run(
    name: str,
    *,
    dry: bool = False,
    context: JobContext | None = None,
) -> Iterator[JobContext]:
    """Log one job and share its context or own a standalone run.

    Dry runs print the routine's entry point before execution so callers
    can follow progress in a terminal or notebook.

    Args:
        name: Routine name included in the shared daily log.
        dry: Output mode for a standalone invocation.
        context: Parent run context, whose mode overrides ``dry``.

    Yields:
        Context through which the job publishes proposed edits.
    """
    with open_run_log():
        active = context or JobContext(
            pywikibot.Site("zh", "wikipedia"),
            dt.datetime.now(dt.UTC).date(),
            dry,
        )
        logger = logging.getLogger(f"aranami.jobs.{name}")
        task = active.start_task(name)
        started = task.started_monotonic
        logger.info("Routine started (dry=%s).", active.dry)
        log_project_event(
            "aranami",
            "routine.started",
            routine=name,
            dry=active.dry,
        )
        _save_task_status(task, dry=active.dry)
        try:
            if active.dry:
                print(  # ruff: ignore[print]
                    f"Dry run: executing aranami.jobs.{name}.run()",
                    flush=True,
                )
            yield active
        except Exception as error:
            task.status = "failed"
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
            _save_task_status(task, dry=active.dry)
            result = _routine_result(task, dry=active.dry)
            logger.log(
                {
                    "failed": logging.ERROR,
                    "deferred": logging.WARNING,
                }.get(task.status, logging.INFO),
                "Routine result: %s",
                json.dumps(
                    result,
                    allow_nan=False,
                ),
                exc_info=task.status == "failed",
            )
            log_project_event(
                "aranami",
                "routine.result",
                **{
                    key: value
                    for key, value in result.items()
                    if key != "schema"
                },
            )
            if context is None:
                active.write_report()
