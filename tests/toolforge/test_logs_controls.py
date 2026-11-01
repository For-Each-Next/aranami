"""Verify recent invocation history and separate diagnostic access."""

from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.public import create_app
from webservice.presentation import Interface

# Exact event counts and status codes describe the public interface.
# ruff: file-ignore[magic-value-comparison]


class LogControlsTests(unittest.TestCase):
    """Exercise HTTP pages over bounded saved job invocations."""

    def setUp(self) -> None:
        """Provide one hundred invocations and diagnostic files."""
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
            for index in range(100)
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

    def test_newest_history_keeps_diagnostic_downloads_reachable(
        self,
    ) -> None:
        """Show invocations and separate downloads in every locale."""
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
                assert listing.count("<tr>") == 20
                assert listing.count("<td>") == 80
                assert output.count('<th scope="col">') == 4
                assert re.findall(r'<time datetime="([^"]+)"', listing) == [
                    event["time"] for event in reversed(self.events[-20:])
                ]
                assert interface.text("logs.result.updated") in listing
                assert interface.text("logs.result.failed") in listing
                assert '<form action="/reports/"' not in output
                assert 'class="log-query"' not in output
                assert 'class="log-match-count"' not in output
                assert 'class="pagination"' not in output
                assert interface.text("logs.download") not in output
                assert " download>" not in output
                assert "job-history-" in listing
                assert listing.count('<a class="status-link"') == 20
                assert listing.count('aria-haspopup="dialog"') == 20
                assert "<button" not in listing
                for index in range(80, 100):
                    assert f'href="#job-history-{index:032x}"' in listing
                assert f'href="#job-history-{79:032x}"' not in listing
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
                assert (
                    self.client.get(
                        "/logs/?date=2026-10-23&download=1",
                    ).text
                    == "diagnostic details\n"
                )

    def test_obsolete_controls_leave_recent_history_visible(self) -> None:
        """Ignore retired history controls without changing the rows."""
        for locale in ("en", "zh-hans", "zh-hant"):
            headers = {"Accept-Language": locale}
            expected = self._listing(
                self.client.get("/reports/", headers=headers).text,
            )
            for query in (
                "date=2026-10-23&q=Pageview&status=failed&order=asc&page=2",
                "q=never-match",
                "date=2026-01-01",
                "order=sideways",
                "page=0",
                "page=5",
                "status=private-code",
                "order=asc&order=desc",
                "page=-1",
            ):
                with self.subTest(locale=locale, query=query):
                    response = self.client.get(
                        "/reports/?" + query,
                        headers=headers,
                    )
                    assert response.status_code == 200
                    assert self._listing(response.text) == expected
                    assert 'class="log-query"' not in response.text
        assert self.client.get("/api/logs?order=asc").status_code == 400

    def test_empty_history_remains_localized_without_controls(self) -> None:
        """Describe empty history in every supported locale."""
        (self.runtime / "state" / "job-history.json").write_text(
            json.dumps({"schema": 1, "runs": []}),
        )
        for locale in ("en", "zh-hans", "zh-hant"):
            response = self.client.get(
                "/reports/",
                headers={"Accept-Language": locale},
            )
            assert response.status_code == 200
            assert Interface(locale).text("reports.empty") in response.text
            assert "download=1" not in response.text
            assert " download>" not in response.text
            assert '<table class="logs-table">' not in response.text
            assert 'class="log-query"' not in response.text

    def test_history_is_independent_of_diagnostic_files(self) -> None:
        """Show saved invocations after diagnostic files are removed."""
        for path in (self.runtime / "logs").iterdir():
            path.unlink()
        response = self.client.get("/reports/")
        assert response.status_code == 200
        assert self._listing(response.text).count("<tr>") == 20
        assert self.client.get("/logs/").status_code == 200
        (self.runtime / "state" / "job-history.json").write_text("{}")
        response = self.client.get("/reports/")
        assert response.status_code == 200
        assert "job-history-" not in response.text


if __name__ == "__main__":
    unittest.main()
