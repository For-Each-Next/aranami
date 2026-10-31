"""Verify localized log controls and download access."""

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
    """Exercise HTTP pages over bounded saved events."""

    def setUp(self) -> None:
        """Provide sixty saved events and one downloadable daily log."""
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
        self.client = TestClient(create_app(runtime))
        self.addCleanup(self.client.close)

    @staticmethod
    def _listing(output: str) -> str:
        """Extract the event list without matching unrelated timestamps.

        Returns:
            The visible event records inside their semantic container.
        """
        match = re.search(r'<div class="major-events">(.*?)</div>', output)
        assert match is not None
        return match[1]

    def test_newest_default_and_chronology_keep_downloads_reachable(
        self,
    ) -> None:
        """Keep archive columns and downloads in every locale."""
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                interface = Interface(locale)
                response = self.client.get(
                    "/logs/",
                    headers={"Accept-Language": locale},
                )
                assert response.status_code == 200
                output = response.text
                listing = self._listing(output)
                assert listing.count('class="log-event"') == 25
                assert listing.index(self.events[-1]["time"]) < listing.index(
                    self.events[-2]["time"],
                )
                assert "60" in output
                assert interface.text("logs.download") in output
                assert output.index(" download>") < output.index(
                    'class="major-events"',
                )
                archive = output.split('<table class="logs-table">', 1)[
                    1
                ].split(
                    "</table>",
                    1,
                )[0]
                assert archive.count('<th scope="col">') == 3
                assert archive.count("<td") == 3
                assert (
                    '<td class="log-download"><span class="filename">'
                    in archive
                )
                assert '</span> <a href="' in archive
                assert '<span lang="zh">' in listing
                assert '<details class="system-details">' in output
                chronology = self.client.get(
                    "/logs/?order=asc",
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
            "/logs/?" + urlencode(parameters),
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 200
        output = response.text
        assert self._listing(output).count('class="log-event"') == 5
        assert "30 matching events" in output
        assert 'value="failed" selected' in output
        assert 'value="asc" selected' in output
        assert (
            "q=Pageview&amp;order=asc&amp;status=failed&amp;page=1" in output
        )
        assert "Clear filters" in output
        assert "Page 2 of 2" in output
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
                "/logs/?q=never-match",
                headers={"Accept-Language": locale},
            )
            assert response.status_code == 200
            assert Interface(locale).text("logs.no_matches") in response.text
            assert 'class="log-download"' in response.text
        for query in (
            "order=sideways",
            "page=0",
            "page=5",
            "status=private-code",
            "order=asc&order=desc",
            "page=-1",
        ):
            with self.subTest(query=query):
                assert self.client.get("/logs/?" + query).status_code == 400
        assert self.client.get("/api/logs?order=asc").status_code == 400


if __name__ == "__main__":
    unittest.main()
