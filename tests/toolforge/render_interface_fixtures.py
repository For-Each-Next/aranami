"""Write synthetic localized pages for offline browser interface checks.

Use the repository Python environment and PYTHONPATH=src. Its disposable
output contains no runtime data and performs no requests.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

# Reuse the offline suites' synthetic observations.
from test_maintenance_controls import (
    _snapshot,  # ruff: ignore[import-private-name]
)
from test_presentation import _fixtures  # ruff: ignore[import-private-name]

from shared.config import routine_configuration
from webservice.pages import (
    render_about,
    render_dashboard,
    render_error,
    render_logs,
    render_maintenance,
)


def main() -> None:
    """Write five page surfaces in each supported language."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    destination = parser.parse_args().destination
    fixture = _fixtures()
    fixture["config"]["routines"] = routine_configuration()
    snapshot = fixture["snapshot"]
    maintenance = _snapshot()
    article = snapshot["pageviews"]["items"][0]
    article["title"] = "LongUnbrokenArticleIdentifier" * 7
    article["assessment_class"] = None
    article["assessment_importance"] = "unrecognized value"
    article["extract"] = "A longer source introduction with context. " * 20
    article["extract_zh"] = "保留中文源文与标点的简介。" * 30
    article["extract_hant"] = "保留中文來源與標點的簡介。" * 30
    maintenance_rows = maintenance["backlog"]["groups"][0]["items"]
    maintenance_rows[0]["title"] = "LongUnbrokenArticleIdentifier" * 7
    maintenance_rows[1]["title"] = "含有較長中文名稱的遊戲條目" * 5
    maintenance_rows[1]["oldest_issue_month"] = "2019-01"
    fixture["status"]["reports"]["reports"]["first"]["title"] = (
        "WikiProject:电子游戏/" + "LongReportIdentifier" * 9
    )
    now = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)
    events = [
        {
            "schema": 1,
            "kind": "routine_finished",
            "time": "2026-10-18T12:00:02Z",
            "routine": "pageviews",
            "dry": False,
            "status": "updated",
            "updated": 1,
            "duration_seconds": 73.7,
        },
        {
            "schema": 1,
            "kind": "routine_finished",
            "time": "2026-10-18T11:00:02Z",
            "routine": "pageviews",
            "dry": False,
            "status": "unknown",
            "updated": None,
            "duration_seconds": 6.83,
        },
    ]
    logs = {
        "files": [
            {
                "date": "2026-10-18",
                "name": "aranami.2026-10-18.log",
                "size": 1024,
            },
        ],
        "date": "2026-10-18",
        "events": events,
        "query": "",
    }
    for locale in ("en", "zh-hans", "zh-hant"):
        directory = destination / locale
        directory.mkdir(parents=True, exist_ok=True)
        documents = {
            "home": render_dashboard(
                snapshot,
                fixture["status"],
                fixture["config"],
                locale,
                now,
            ),
            "maintenance": render_maintenance(
                maintenance,
                {"task": ["references"]},
                locale,
            ),
            "logs": render_logs(
                logs,
                locale,
                status=fixture["status"],
                config=fixture["config"],
                now=now,
            ),
            "about": render_about(
                fixture["config"],
                locale,
                status=fixture["status"],
                now=now,
            ),
            "error": render_error("backlog.not_found", locale),
        }
        for surface, document in documents.items():
            (directory / f"{surface}.html").write_text(document)


if __name__ == "__main__":
    main()
