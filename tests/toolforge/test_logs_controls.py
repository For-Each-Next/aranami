"""Verify invocation-history controls and separate diagnostic access."""

from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from backend.public import create_app
from webservice.presentation import Interface

# Exact event counts and status codes describe the public interface.
# ruff: file-ignore[magic-value-comparison]


class LogControlsTests(unittest.TestCase):
    """Exercise HTTP pages over bounded saved job invocations."""

    def setUp(self) -> None:
        """Provide sixty invocations and separate diagnostic files."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runtime = Path(temporary.name)
        logs = runtime / "logs"
        logs.mkdir()
        (logs / "aranami.2026-10-23.log").write_text("diagnostic details\n")
        self.events = [
            {
                "schema": 1,
                "time": (
                    dt.datetime(2026, 10, 23, tzinfo=dt.UTC)
                    + dt.timedelta(seconds=index)
                ).isoformat(),
                "kind": "routine_finished",
                "routine": "pageviews",
                "dry": False,
                "status": "failed" if index % 2 else "updated",
                "updated": None if index % 2 else 1,
                "duration_seconds": 6.83,
            }
            for index in range(60)
        ]
        (logs / "events.2026-10-23.jsonl").write_text(
            "\n".join(json.dumps(event) for event in self.events) + "\n",
        )
        state = runtime / "state"
        state.mkdir()
        (state / "job-history.json").write_text(
            json.dumps({
                "schema": 1,
                "runs": [
                    {
                        "run_id": f"{index:032x}",
                        "routine": event["routine"],
                        "started_at": event["time"],
                        "finished_at": event["time"],
                        "status": event["status"],
                        "duration_seconds": event["duration_seconds"],
                        "truncated": False,
                        "stages": [
                            {
                                "kind": "started",
                                "time": event["time"],
                                "title": "",
                            },
                            {
                                "kind": "finished",
                                "time": event["time"],
                                "title": "",
                            },
                        ],
                    }
                    for index, event in enumerate(self.events)
                ],
            }),
        )
        self.runtime = runtime
        self.client = TestClient(create_app(runtime))
        self.addCleanup(self.client.close)

    @staticmethod
    def _listing(output: str) -> str:
        """Extract event rows without matching status-panel timestamps.

        Returns:
            The visible event rows inside the report table body.
        """
        match = re.search(r"<tbody>(.*?)</tbody>", output, re.DOTALL)
        assert match is not None
        return match[1]

    def test_newest_default_and_chronology_keep_downloads_reachable(
        self,
    ) -> None:
        """Keep chronology and downloads in each locale."""
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                interface = Interface(locale)
                response = self.client.get(
                    "/reports/",
                    headers={"Accept-Language": locale},
                )
                assert response.status_code == 200
                output = response.text
                listing = self._listing(output)
                assert listing.count("<tr>") == 25
                assert listing.count("<td>") == 100
                assert output.count('<th scope="col">') == 4
                assert listing.index(self.events[-1]["time"]) < listing.index(
                    self.events[-2]["time"],
                )
                assert "60" in output
                assert interface.text("logs.download") not in output
                assert " download>" not in output
                assert "job-history-" in listing
                archive_output = self.client.get(
                    "/logs/",
                    headers={"Accept-Language": locale},
                ).text
                archive = archive_output.split(
                    '<table class="logs-table">',
                    1,
                )[1].split(
                    "</table>",
                    1,
                )[0]
                assert archive.count('<th scope="col">') == 3
                assert archive.count("<td") == 3
                assert (
                    '<td class="log-download"><a class="filename" href="'
                    in archive
                )
                assert "download>aranami.2026-10-23.log</a>" in archive
                assert interface.text("logs.download") not in archive
                assert 'class="log-query"' not in archive_output
                assert 'id="recent-events"' not in archive_output
                assert 'class="system-details"' in archive_output
                assert 'class="system-details"' not in output
                chronology = self.client.get(
                    "/reports/?order=asc",
                    headers={"Accept-Language": locale},
                ).text
                assert self._listing(chronology).index(
                    self.events[0]["time"],
                ) < self._listing(chronology).index(self.events[1]["time"])

    def test_status_search_and_pagination_retain_the_current_scope(
        self,
    ) -> None:
        """Retain controls and expose their matching count."""
        parameters = {
            "date": "2026-10-23",
            "q": "Pageview",
            "status": "failed",
            "order": "asc",
            "page": 2,
        }
        response = self.client.get(
            "/reports/?" + urlencode(parameters),
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 200
        output = response.text
        assert self._listing(output).count("<tr>") == 5
        assert "Matching job invocations for 2026-10-23: 30." in output
        assert 'value="failed" selected' in output
        assert 'value="asc" selected' in output
        assert (
            "q=Pageview&amp;status=failed&amp;order=asc&amp;lang=en&amp;page=1"
            in output
        )
        assert "Clear filters" in output
        assert "Page 2 of 2" in output
        assert 'href="/reports/?date=2026-10-23' in output
        assert (
            self.client.get(
                "/logs/?date=2026-10-23&download=1",
            ).text
            == "diagnostic details\n"
        )

    def test_empty_filters_and_invalid_controls_are_explained(self) -> None:
        """Localize empty search and reject invalid controls."""
        for locale in ("en", "zh-hans", "zh-hant"):
            response = self.client.get(
                "/reports/?q=never-match",
                headers={"Accept-Language": locale},
            )
            assert response.status_code == 200
            assert Interface(locale).text("logs.no_matches") in response.text
            assert "download=1" not in response.text
            assert " download>" not in response.text
        for query in (
            "order=sideways",
            "page=0",
            "page=5",
            "status=private-code",
            "order=asc&order=desc",
            "page=-1",
        ):
            with self.subTest(query=query):
                response = self.client.get("/reports/?" + query)
                assert response.status_code == 400
        assert self.client.get("/api/logs?order=asc").status_code == 400

    def test_history_is_independent_of_diagnostic_files(self) -> None:
        """Show saved invocations after diagnostic files are removed."""
        for path in (self.runtime / "logs").iterdir():
            path.unlink()
        response = self.client.get("/reports/")
        assert response.status_code == 200
        assert self._listing(response.text).count("<tr>") == 25
        assert self.client.get("/logs/").status_code == 200
        (self.runtime / "state" / "job-history.json").write_text("{}")
        response = self.client.get("/reports/")
        assert response.status_code == 200
        assert "job-history-" not in response.text


if __name__ == "__main__":
    unittest.main()
