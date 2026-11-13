"""Build complete observability records for storage contract tests."""

import datetime as dt


def execution_record(index: int = 1, *, outcome: str = "running") -> dict:
    """Build a distinct execution with actual terminal times when known.

    Returns:
        A complete owner record accepted by the saved-status contract.
    """
    start = dt.datetime(2026, 11, 3, tzinfo=dt.UTC) + dt.timedelta(
        seconds=index,
    )
    terminal = outcome not in {"running", "interrupted"}
    return {
        "updated_at": start.isoformat(),
        "sequence": 1,
        "run_id": f"{index:032x}",
        "boot_id": "a" * 32,
        "task_id": "dyks",
        "kind": "report",
        "trigger": "scheduled",
        "started_at": start.isoformat(),
        "finished_at": start.isoformat() if terminal else None,
        "detected_at": start.isoformat() if outcome == "interrupted" else None,
        "duration_seconds": 0 if terminal else None,
        "outcome": outcome,
        "progress": None,
        "failure": None,
        "stages": [
            {
                "time": start.isoformat(),
                "kind": "started",
                "title": "",
            },
        ],
        "stages_truncated": False,
        "report_date": "2026-11-02",
        "updated_count": None,
    }


def publication_record(index: int = 1, *, outcome: str = "running") -> dict:
    """Build an attempt with explicit destination confirmation.

    Returns:
        A publication record whose identity is separate from its run.
    """
    execution = execution_record(index, outcome="succeeded")
    terminal = outcome not in {"queued", "running"}
    return {
        "updated_at": execution["updated_at"],
        "sequence": 1,
        "attempt_id": f"{index:032x}",
        "run_id": execution["run_id"],
        "boot_id": execution["boot_id"],
        "task_id": execution["task_id"],
        "site": "zhwiki",
        "title": "WikiProject:电子游戏/测试报告",
        "registered_at": execution["started_at"],
        "started_at": execution["started_at"],
        "finished_at": execution["finished_at"] if terminal else None,
        "detected_at": None,
        "duration_seconds": 0 if terminal else None,
        "outcome": outcome,
        "confirmed_edits": (
            1 if outcome == "saved" else 0 if outcome == "unchanged" else None
        ),
        "revision_id": 123 if outcome == "saved" else None,
        "last_confirmed_save_at": None,
        "failure": None,
        "generation": None,
        "report_date": "2026-11-02",
    }
