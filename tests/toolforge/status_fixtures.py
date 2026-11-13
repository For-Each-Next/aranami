"""Verify the saved status and log presentation contracts."""

from __future__ import annotations

import copy


def status_snapshot() -> dict:
    """Return independent runtime observation domains."""
    stamp = "2026-10-18T12:00:00Z"
    run_id = "0" * 32
    boot_id = "1" * 32
    return {
        "schema": 1,
        "store_id": "2" * 32,
        "revision": 4,
        "written_at": stamp,
        "as_of": stamp,
        "availability": "available",
        "services": {
            "scheduler": {
                "service_id": "scheduler",
                "boot_id": boot_id,
                "lifecycle": "running",
                "started_at": "2026-10-16T08:56:00Z",
                "heartbeat_at": stamp,
                "updated_at": stamp,
                "sequence": 1,
                "config_id": "b" * 64,
                "freshness": "current",
                "schedules": {
                    "routines": {
                        "pageviews": {
                            "state": "scheduled",
                            "next_run_at": "2026-10-18T19:00:00Z",
                            "hour": 19,
                            "minute": 0,
                            "pages": {},
                            "prefixes": [],
                            "idle": False,
                            "queued_since": None,
                        },
                    },
                    "collections": {},
                },
                "resources": {
                    "python": "3.15.0",
                    "system": "Linux",
                    "machine": "x86_64",
                    "aranami_version": "1.0.0a1",
                    "memory_bytes": 104857600,
                    "memory_limit_bytes": 4294967296,
                    "cpu_percent": 12.5,
                    "cpu_limit": 2,
                },
            },
        },
        "executions": {
            run_id: {
                "task_id": "pageviews",
                "kind": "report",
                "boot_id": boot_id,
                "run_id": run_id,
                "started_at": "2026-10-18T11:59:47Z",
                "finished_at": stamp,
                "duration_seconds": 13,
                "outcome": "succeeded",
                "progress": None,
                "failure": None,
                "stages": [
                    {
                        "time": "2026-10-18T11:59:47Z",
                        "kind": "started",
                        "title": "",
                    },
                    {
                        "time": "2026-10-18T11:59:50Z",
                        "kind": "database_updated",
                        "title": "WikiProject:电子游戏/热门条目",
                    },
                    {"time": stamp, "kind": "finished", "title": ""},
                ],
                "stages_truncated": False,
                "report_date": "2026-10-17",
                "trigger": "scheduled",
                "updated_at": stamp,
                "sequence": 2,
                "detected_at": None,
                "updated_count": 1,
            },
        },
        "datasets": {
            "pageviews": {
                "dataset_id": "pageviews",
                "name": "pageviews",
                "family": "pageviews",
                "site": "zhwiki",
                "project": "电子游戏",
                "run_id": run_id,
                "generation": "3" * 32,
                "digest": "a" * 64,
                "observed_at": "2026-10-18T11:59:50Z",
                "committed_at": stamp,
                "coverage_start": "2026-09-18",
                "coverage_end": "2026-10-17",
                "policy": "pageviews19",
                "max_age_seconds": None,
                "updated_at": stamp,
                "sequence": 3,
                "freshness": "current",
            },
        },
        "publications": {
            "target": {
                "attempt_id": "4" * 32,
                "run_id": run_id,
                "boot_id": boot_id,
                "task_id": "pageviews",
                "site": "zhwiki",
                "title": "WikiProject:电子游戏/热门条目 <Latest & safe>",
                "outcome": "saved",
                "registered_at": "2026-10-18T11:59:54Z",
                "started_at": "2026-10-18T11:59:55Z",
                "finished_at": stamp,
                "duration_seconds": 5,
                "confirmed_edits": 1,
                "revision_id": 321,
                "last_confirmed_save_at": stamp,
                "failure": None,
                "updated_at": stamp,
                "sequence": 4,
                "detected_at": None,
                "generation": None,
                "report_date": "2026-10-17",
            },
        },
    }


def executions(count: int) -> dict:
    """Return completed runs for history limits and ordering."""
    sample = next(iter(status_snapshot()["executions"].values()))
    return {
        f"{index:032x}": {**copy.deepcopy(sample), "run_id": f"{index:032x}"}
        for index in range(count)
    }


def report_card_snapshot() -> dict:
    """Exercise daily, hourly, unchanged and active report cards.

    Returns:
        Synthetic saved observations for browser layout verification.
    """
    status = status_snapshot()
    sample = next(iter(status["executions"].values()))
    for index, identifier in enumerate(
        ("assessment_log", "assessment_lists", "dyks", "new_pages"),
        start=1,
    ):
        run = {
            **copy.deepcopy(sample),
            "run_id": f"{index:032x}",
            "task_id": identifier,
            "updated_count": 0,
            "outcome": "succeeded",
            "report_date": "2026-10-17",
        }
        if identifier == "dyks":
            run.update(
                started_at="2026-10-18T11:00:00Z",
                finished_at="2026-10-18T11:01:23Z",
                duration_seconds=83,
                report_date="2026-10-18",
            )
        elif identifier == "new_pages":
            source = {
                "provider": "mediawiki",
                "dataset": "page_history",
                "wiki": "zhwiki",
                "target": "含有較長中文名稱的遊戲條目" * 4,
                "date": "2026-10-17",
            }
            run.update(
                outcome="running",
                started_at="2026-10-18T11:58:00Z",
                finished_at=None,
                duration_seconds=None,
                report_date=None,
                progress={
                    "phase": "source_read",
                    "current": 3,
                    "total": 12,
                    "source": source,
                },
                stages=[
                    {
                        "time": "2026-10-18T11:58:00Z",
                        "kind": "started",
                        "title": "",
                    },
                    {
                        "time": "2026-10-18T11:59:45Z",
                        "kind": "source_read",
                        "title": "",
                        "source": source,
                    },
                ],
            )
        status["executions"][run["run_id"]] = run
    status["services"]["scheduler"]["schedules"]["routines"].update({
        "dyks": {
            "state": "scheduled",
            "next_run_at": "2026-10-18T13:00:00Z",
        },
        "assessment_log": {
            "state": "scheduled",
            "next_run_at": "2026-10-19T00:00:00Z",
        },
        "assessment_lists": {
            "state": "scheduled",
            "next_run_at": "2026-10-19T00:00:00Z",
        },
    })
    return status


def active_collection_record(task_id: str, index: int) -> dict:
    """Return one current source read owned by the fixture scheduler.

    Args:
        task_id: Background collection identity.
        index: Unique execution identifier.

    Returns:
        An active collection with observed source and counters.
    """
    sample = copy.deepcopy(
        next(iter(status_snapshot()["executions"].values())),
    )
    sample.update(
        task_id=task_id,
        kind="collection",
        run_id=f"{index:032x}",
        started_at="2026-10-18T11:59:50Z",
        finished_at=None,
        duration_seconds=None,
        outcome="running",
        updated_at="2026-10-18T12:00:20Z",
        updated_count=None,
        stages=[],
        progress={
            "phase": "source_read",
            "report_date": None,
            "current": 3,
            "total": 12,
            "source": {
                "provider": "mediawiki",
                "dataset": "page_text",
                "wiki": "zhwiki",
                "target": "Current source <unsafe>",
                "date": "",
                "references": [
                    {"kind": "page", "value": "Current source <unsafe>"},
                ],
                "reference_count": 1,
            },
        },
    )
    return sample
