"""Localize independent runtime observations.

Pass a validated envelope to ``status_context``. The presentation does
not infer a successful task or publication from another domain.
"""

from __future__ import annotations

import datetime as dt
import operator
from typing import TYPE_CHECKING

from webservice.dataset_updates import dataset_updates_context
from webservice.job_history import history_context, source_description
from webservice.presentation import (
    catalog,
    duration,
    failure_description,
    readable_size,
    relative_time,
    timestamp,
    title_url,
    utc_time,
    wiki_link,
)

if TYPE_CHECKING:
    from webservice.presentation import Interface, Record


def observed_time(value: object) -> Record:
    """Prepare a known UTC timestamp or its unknown marker.

    Returns:
        Original instant, display label, and availability.
    """
    label = utc_time(value, seconds=True)
    return {"value": value, "label": label, "known": label != "—"}


def task_name(interface: Interface, identifier: str) -> str:
    """Localize a known task name.

    Returns:
        Authored text or the unfamiliar identifier unchanged.
    """
    key = f"routine.{identifier}"
    return interface.text(key) if key in catalog() else identifier


def _state(interface: Interface, domain: str, value: str) -> str:
    return interface.text(f"observability.{domain}.{value}")


def _services(interface: Interface, status: Record) -> list[Record]:
    """Separate lifecycle and heartbeat freshness.

    Returns:
        Service rows with their independently observed schedules.
    """
    rows = []
    for item in status.get("services", {}).values():
        schedules = item.get("schedules", {})
        rows.append({
            "name": interface.text(
                f"observability.service_name.{item['service_id']}",
            )
            if f"observability.service_name.{item['service_id']}" in catalog()
            else item["service_id"],
            "state": _state(interface, "service", item["lifecycle"]),
            "freshness": _state(interface, "freshness", item["freshness"]),
            "fresh": item["freshness"] == "current",
            "expires": (
                timestamp(item["heartbeat_at"]) + dt.timedelta(seconds=90)
            ).isoformat()
            if item["freshness"] == "current"
            and item["lifecycle"] != "stopped"
            else None,
            "observed": observed_time(item.get("heartbeat_at")),
            "started": observed_time(item.get("started_at")),
            "scheduled": [
                {
                    "name": task_name(interface, name),
                    "next": observed_time(
                        task.get("next_run_at")
                        if item["freshness"] == "current"
                        and item["lifecycle"] == "running"
                        and task.get("state") == "scheduled"
                        else None,
                    ),
                }
                for group in schedules.values()
                for name, task in group.items()
            ],
        })
    return rows


def _datasets(interface: Interface, status: Record) -> list[Record]:
    """Describe accepted datasets using their own observations.

    Returns:
        Dataset freshness, observation, storage, and coverage rows.
    """
    return [
        {
            "id": row["dataset_id"],
            "name": task_name(interface, row["name"]),
            "scope": " / ".join(
                filter(None, (row.get("site"), row.get("project"))),
            ),
            "state": _state(interface, "freshness", row["freshness"]),
            "observed": observed_time(row.get("observed_at")),
            "committed": observed_time(row.get("committed_at")),
            "coverage": " \u2013 ".join(
                interface.date(value)
                for value in dict.fromkeys((
                    row.get("coverage_start"),
                    row.get("coverage_end"),
                ))
                if value
            )
            or interface.text("ops.unavailable"),
            "generation": row.get("generation"),
        }
        for row in status.get("datasets", {}).values()
    ]


def _publications(interface: Interface, status: Record) -> list[Record]:
    """Show independent publication attempts and confirmed saves.

    Returns:
        Destination rows with revision links only for confirmed saves.
    """
    rows = []
    for record in status.get("publications", {}).values():
        outcome = _state(interface, "publication", record["outcome"])
        url = title_url(record["title"], record["site"])
        if record["outcome"] == "saved" and record.get("revision_id"):
            outcome = wiki_link(
                url + "?oldid=" + str(record["revision_id"]),
                outcome,
            )
        rows.append({
            "task_id": record["task_id"],
            "title": record["title"],
            "target": wiki_link(url, record["title"]),
            "result": outcome,
            "started": observed_time(record.get("started_at")),
            "saved": observed_time(record.get("last_confirmed_save_at")),
            "duration": duration(record.get("duration_seconds"), interface),
        })
    return rows


def _report_schedule(
    interface: Interface,
    service: Record,
    identifier: str,
) -> Record:
    """Describe a saved report schedule without inventing a next run.

    Returns:
        Next observed check or the reason that its time is unavailable.
    """
    schedule = (
        service
        .get("schedules", {})
        .get("routines", {})
        .get(
            identifier,
            {},
        )
    )
    next_at = None
    state = "missing"
    if service and service.get("freshness") != "current":
        state = "stale"
    elif service.get("lifecycle") in {"stopped", "draining"}:
        state = "stopped"
    elif service.get("lifecycle") == "paused":
        state = "paused"
    elif service.get("lifecycle") == "running":
        state = schedule.get("state", "missing")
        if schedule.get("queued_since"):
            state = "idle"
        elif state == "scheduled":
            next_at = schedule.get("next_run_at")
    return {
        "time": observed_time(next_at),
        "label": interface.text(
            f"ops.schedule_{state if state != 'scheduled' else 'missing'}",
        ),
        "expires": (
            timestamp(service["heartbeat_at"]) + dt.timedelta(seconds=90)
        ).isoformat()
        if next_at
        else None,
    }


def _report_coverage(
    interface: Interface,
    identifier: str,
    runs: list[Record],
) -> str | None:
    """Describe the latest completed check independently of wiki edits.

    Args:
        interface: Selected language for report status text.
        identifier: Configured report routine.
        runs: Retained executions belonging to this routine.

    Returns:
        Confirmed daily report date or completed hourly check, when
        one is available. Later failures do not erase earlier coverage.
    """
    completed = [run for run in runs if run["outcome"] == "succeeded"]
    if identifier == "dyks":
        finished = [
            timestamp(run.get("finished_at"))
            for run in completed
            if timestamp(run.get("finished_at")) is not None
        ]
        if not finished:
            return None
        instant = max(finished)
        return interface.text(
            "reports.updated_hourly",
            date=interface.date(instant.date().isoformat()),
            hour=f"{instant.hour:02d}:00 UTC"
            if interface.locale == "en"
            else instant.hour,
        )
    days = [run["report_date"] for run in runs if run.get("report_date")]
    return (
        interface.text("reports.updated_daily", date=interface.date(max(days)))
        if days
        else None
    )


def _report_activity(
    interface: Interface,
    run: Record,
) -> str:
    """Describe only the current phase recorded by an active execution.

    Args:
        interface: Selected language for activity text.
        run: Active execution and its recorded progress or stages.

    Returns:
        A localized current action with any known progress counters.
    """
    progress = run.get("progress") or {}
    phase = progress.get("phase")
    source = progress.get("source")
    if not phase:
        stages = run.get("stages", [])
        phase = stages[-1]["kind"] if stages else "started"
        source = stages[-1].get("source") if stages else None
    key = f"reports.running.{phase}"
    activity = (
        source_description(source, interface, kind=phase, links=False)
        if source
        and phase
        in {
            "source_read",
            "source_loaded",
            "source_written",
            "source_unchanged",
            "checking",
            "saving",
        }
        else interface.text(
            "reports.running.fetching"
            if phase == "source_read"
            else key
            if key in catalog()
            else "reports.running.started",
            scope="",
        )
    )
    current, total = progress.get("current"), progress.get("total")
    return (
        interface.text(
            "progress.count",
            phase=activity.rstrip(".。…"),
            current=f"{current:,}",
            total=f"{total:,}",
        )
        if current is not None and total is not None
        else activity
    )


def _deferred_status(interface: Interface, run: Record) -> str:
    """Describe an observed deferred date independently of coverage.

    Returns:
        Deferred result with its known attempted date and safe cause.
    """
    failure = run.get("failure") or {}
    day = failure.get("failure_date")
    label = (
        interface.text("reports.deferred_date", date=interface.date(day))
        if day
        else _state(interface, "execution", "deferred")
    )
    reason = failure_description(failure, interface)
    return (
        interface.text("reports.deferred_reason", result=label, reason=reason)
        if reason
        else label
    )


def _collection_activity(interface: Interface, run: Record) -> str:
    """Describe the observed collection phase and its current work.

    Returns:
        Plain localized activity with only known source details/counts.
    """
    progress = run.get("progress") or {}
    phase = progress.get("phase")
    source = progress.get("source")
    work = progress.get("work")
    work_key = f"server.work.{work}"
    if source and source.get("provider") == "mediawiki":
        references = source.get("references", [])
        if len(references) > 1:
            source = {
                **source,
                "references": references[:1],
                "reference_count": source.get(
                    "reference_count",
                    len(references),
                ),
            }
    activity = (
        source_description(source, interface, kind=phase, links=False)
        if source
        and phase
        in {
            "source_read",
            "source_loaded",
            "source_written",
            "source_unchanged",
        }
        else interface.text(f"progress.{phase}")
        if f"progress.{phase}" in catalog()
        else interface.text("server.work_started")
    )
    if work_key in catalog():
        separator = ": " if interface.locale == "en" else "\uff1a"
        activity = interface.text(work_key) + separator + activity
    current, total = progress.get("current"), progress.get("total")
    return (
        interface.text(
            "progress.count",
            phase=activity.rstrip(".。…"),
            current=f"{current:,}",
            total=f"{total:,}",
        )
        if current is not None and total is not None
        else activity
    )


def _current_work(interface: Interface, status: Record) -> Record:
    """Select background work owned by the current scheduler process.

    Returns:
        Active collection rows or an explicit idle/unknown/stale state.
        A fresh web heartbeat cannot establish scheduler activity.
    """
    owner = status.get("services", {}).get("scheduler", {})
    if not owner or not owner.get("boot_id"):
        return {
            "state": "unknown",
            "label": interface.text(
                "server.work_unknown",
            ),
            "items": [],
            "expires": None,
        }
    if owner.get("freshness") != "current":
        state = "stale" if owner.get("freshness") == "stale" else "unknown"
        return {
            "state": state,
            "label": interface.text(f"server.work_{state}"),
            "items": [],
            "expires": None,
        }
    if owner.get("lifecycle") not in {
        "starting",
        "running",
        "paused",
        "draining",
    }:
        return {
            "state": "unknown",
            "label": interface.text(
                "server.work_unknown",
            ),
            "items": [],
            "expires": None,
        }
    rows = [
        row
        for row in status.get("executions", {}).values()
        if row["kind"] == "collection"
        and row["outcome"] == "running"
        and row["boot_id"] == owner["boot_id"]
    ]
    rows.sort(key=operator.itemgetter("started_at"), reverse=True)
    rows.sort(key=lambda row: row["task_id"] != "website")
    expires = (
        timestamp(owner["heartbeat_at"]) + dt.timedelta(seconds=90)
    ).isoformat()
    return {
        "state": "active" if rows else "idle",
        "label": interface.text(
            "server.work_active" if rows else "server.work_idle",
        ),
        "expires": expires,
        "items": [
            {
                "run_id": row["run_id"],
                "task_id": row["task_id"],
                "name": task_name(interface, row["task_id"]),
                "activity": _collection_activity(interface, row),
                "started": observed_time(row["started_at"]),
                "observed": observed_time(row.get("updated_at")),
                "expires": expires,
            }
            for row in rows
        ],
    }


def report_context(
    interface: Interface,
    status: Record,
    definitions: Record,
    now: dt.datetime,
    *,
    history_page: int | None = None,
) -> Record:
    """Group on-wiki reports with independent run and publication facts.

    Args:
        interface: Selected locale and authored messages.
        status: Validated saved runtime observations.
        definitions: Configured on-wiki reports and their destinations.
        now: UTC rendering instant.
        history_page: Ten-row page; zero omits history details.
            None retains all history for direct presentation callers.

    Returns:
        Report cards, on-wiki history and their status dialogs.
        Collection tasks never appear in this report-only selection.
    """
    runs = {
        key: run
        for key, run in status.get("executions", {}).items()
        if run["task_id"] in definitions and run["kind"] == "report"
    }
    selected = {**status, "executions": runs}
    history = history_context(
        interface,
        selected,
        now,
        offset=max(0, (history_page or 0) - 1) * 10,
        limit=None if history_page is None else 10 if history_page else 0,
    )
    dialogs = {row["run_id"]: row for row in history["history_dialogs"]}
    publications = _publications(interface, status)
    service = status.get("services", {}).get("scheduler", {})
    rows = []
    icons = {
        "dyks": "lightbulb",
        "new_pages": "article-add",
        "assessment_lists": "article-check",
        "assessment_log": "history",
        "pageviews": "chart-bar",
        "enwp_key_articles": "language",
        "pexbot": "robot",
    }
    for identifier, definition in definitions.items():
        task_runs = [
            row for row in runs.values() if row["task_id"] == identifier
        ]
        run = max(
            task_runs,
            key=operator.itemgetter("started_at"),
            default=None,
        )
        event = None
        if run is not None:
            event = dialogs.get(run["run_id"])
            if event is None:
                event = history_context(
                    interface,
                    {**status, "executions": {run["run_id"]: run}},
                    now,
                    recent_only=False,
                )["events"][0]
        targets = list(definition.get("pages", {}).values())
        if not targets:
            targets = definition.get("prefixes", [])
        attempts = [
            row for row in publications if row["task_id"] == identifier
        ]
        saves = [
            row["saved"]["value"] for row in attempts if row["saved"]["known"]
        ]
        coverage = _report_coverage(interface, identifier, task_runs)
        rows.append({
            "identifier": identifier,
            "name": task_name(interface, identifier),
            "icon": icons.get(identifier, "article"),
            "targets": [
                wiki_link(
                    title_url(title),
                    chr(ord("a") + index)
                    if len(targets) > 1
                    else task_name(interface, identifier),
                    title,
                )
                for index, title in enumerate(targets)
            ],
            "event": event,
            "coverage": coverage,
            "status": _report_activity(interface, run)
            if run and run["outcome"] == "running"
            else _deferred_status(interface, run)
            if run and run["outcome"] == "deferred"
            else coverage
            if run and run["outcome"] == "succeeded" and coverage
            else interface.text(
                "reports.failed_task",
                task=task_name(interface, identifier),
            )
            if run and run["outcome"] == "failed"
            else event["result"]
            if event
            else interface.text("ops.no_record"),
            "opening": " (" if interface.locale == "en" else "\uff08",
            "closing": ")" if interface.locale == "en" else "\uff09",
            "observed": observed_time(
                run["started_at"]
                if run and run["outcome"] == "running"
                else run.get("finished_at")
                if run
                else None,
            ),
            "running": run is not None and run["outcome"] == "running",
            "deferred": run is not None and run["outcome"] == "deferred",
            "schedule": _report_schedule(interface, service, identifier),
            "saved": observed_time(
                max(saves, key=timestamp) if saves else None,
            ),
            "publications": attempts,
            "popup_id": f"report-details-{identifier}",
            "popup_heading": interface.text(
                "ops.details",
                routine=task_name(interface, identifier),
            ),
        })
    return {
        "routines": rows,
        "events": history["events"],
        "history_dialogs": list(dialogs.values()),
        "history_total": history["history_total"],
        "history_offset": history["history_offset"],
    }


def _resource_rows(
    interface: Interface,
    resources: Record,
    version: str,
    *,
    fresh: bool,
) -> list[Record]:
    """Format actual resource samples and unknown allocation.

    Returns:
        Runtime table rows with expiry markers for live usage.
    """
    unknown = interface.text("ops.unavailable")
    memory = resources.get("memory_bytes") if fresh else None
    limit = resources.get("memory_limit_bytes") if fresh else None
    cpu = resources.get("cpu_percent") if fresh else None
    cpu_limit = resources.get("cpu_limit") if fresh else None
    rows = [
        ("version", version, False),
        (
            "memory",
            (
                f"{readable_size(memory) if memory is not None else unknown}"
                " / "
                f"{readable_size(limit) if limit is not None else unknown}"
            ),
            True,
        ),
        ("cpu", f"{cpu:.1f}%" if cpu is not None else unknown, True),
    ]
    if cpu_limit is not None:
        rows[-1] = (
            "cpu",
            rows[-1][1]
            + " "
            + interface.text(
                "server.cpu_core" if cpu_limit == 1 else "server.cpu_cores",
                count=f"{cpu_limit:g}",
            ),
            True,
        )
    return [
        {
            "label": interface.text(f"server.{key}"),
            "value": value,
            "fresh": usage and fresh,
            "stale": unknown if usage else None,
        }
        for key, value, usage in rows
    ]


def _service_time(
    interface: Interface,
    service: Record,
    now: dt.datetime,
) -> Record:
    """Describe the time corresponding to the saved lifecycle.

    Returns:
        Start, stop request or completion time and localized labels.
        Older stopped records use their terminal observation time.
    """
    lifecycle = service.get("lifecycle")
    key = (
        "stopping"
        if lifecycle == "draining"
        else "stopped"
        if lifecycle == "stopped"
        else "started"
    )
    instant = service.get(f"{key}_at")
    if lifecycle == "stopped" and instant is None:
        instant = service.get("updated_at")
    return {
        "time": observed_time(instant)
        if instant or lifecycle in {"draining", "stopped"}
        else None,
        "time_label": interface.text(f"server.{key}"),
        "time_relative": {
            "label": (relative_time(instant, interface, now) or "").strip(
                "()\uff08\uff09",
            ),
        },
    }


def status_context(  # ruff: ignore[too-many-arguments]
    interface: Interface,
    status: Record,
    version: str,
    now: dt.datetime,
    *,
    built_at: str | None = None,
    history_limit: int | None = None,
) -> Record:
    """Present a validated status envelope with explicit missing facts.

    Args:
        interface: Selected locale and authored messages.
        status: Version-one saved observations read by the data layer.
        version: Running web source version.
        now: UTC rendering instant.
        built_at: Recorded UTC source build time, independent of uptime.
        history_limit: Optional bound on localized execution details;
            zero omits them and none preserves the complete direct view.

    Returns:
        Independent runtime domains and shared footer contexts.
    """
    services = _services(interface, status)
    candidates = list(status.get("services", {}).values())
    service = status.get("services", {}).get("scheduler") or max(
        candidates,
        key=lambda row: row.get("heartbeat_at", ""),
        default={},
    )
    fresh = service.get("freshness") == "current"
    active = fresh and service.get("lifecycle") in {
        "running",
        "paused",
        "starting",
        "draining",
    }
    started = service.get("started_at") if active else None
    instant = timestamp(started)
    heartbeat = timestamp(service.get("heartbeat_at"))
    expires = (
        (heartbeat + dt.timedelta(seconds=90)).isoformat()
        if heartbeat and active
        else None
    )
    footer_unknown = interface.text("footer.uptime_unknown", version=version)
    footer_label = (
        interface.text(
            "footer.uptime",
            version=version,
            duration=duration(
                max(0, int((now - instant).total_seconds())),
                interface,
                running=True,
            ).removesuffix("…"),
            since=utc_time(started),
        )
        if instant
        else footer_unknown
    )
    runtime_rows = _resource_rows(
        interface,
        service.get("resources") or {},
        version,
        fresh=fresh,
    )
    runtime_rows[0]["updated"] = observed_time(built_at) if built_at else None
    runtime_rows[0]["relative"] = {
        "label": (relative_time(built_at, interface, now) or "").strip(
            "()\uff08\uff09",
        ),
    }
    return {
        **history_context(interface, status, now, limit=history_limit),
        "status_availability": _state(
            interface,
            "availability",
            status.get("availability", "missing"),
        ),
        "status_available": status.get("availability") == "available",
        "services": services,
        "datasets": _datasets(interface, status),
        "publications": _publications(interface, status),
        "clock_expires": expires,
        "footer": {
            "label": footer_label,
            "started": started,
            "template": interface.text(
                "footer.uptime",
                version=version,
                duration=interface.text(
                    "duration.days",
                    days="{days}",
                    hours="{hours}",
                    minutes="{minutes}",
                    seconds="{seconds}",
                ),
                since=utc_time(started),
            )
            if started
            else None,
            "compact_template": interface.text(
                "footer.uptime",
                version=version,
                duration="{duration}",
                since=utc_time(started),
            )
            if started
            else None,
            "unknown": footer_unknown,
        },
        "server": {
            "dataset_updates": dataset_updates_context(interface, status, now),
            "work": _current_work(interface, status),
            "state": _state(interface, "service", service["lifecycle"])
            if fresh
            else _state(
                interface,
                "freshness",
                service.get("freshness", "unknown"),
            ),
            "observed": observed_time(service.get("heartbeat_at")),
            "relative": {
                "label": relative_time(
                    service.get("heartbeat_at"),
                    interface,
                    now,
                ),
            },
            "expires": expires is not None,
            **_service_time(interface, service, now),
            "opening": " (" if interface.locale == "en" else "\uff08",
            "separator": ", " if interface.locale == "en" else "\uff0c",
            "closing": ")" if interface.locale == "en" else "\uff09",
        },
        "runtime": runtime_rows,
    }
