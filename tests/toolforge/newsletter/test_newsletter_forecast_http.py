"""Verify current newsletter requests reuse saved homepage forecasts."""

# Private fixture helpers and exact HTTP values are protocol assertions.
# ruff: file-ignore[private-member-access,magic-value-comparison]

from __future__ import annotations

import datetime as dt
from html import unescape
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient

from data.newsletter.archive import DEFAULT_PATH, NewsletterDraftArchive
from data.newsletter.observations import OBSERVATIONS_PATH, save_observation
from data.newsletter.schema import valid_newsletter
from newsletter.test_newsletter_forecasts import _NOW, _dashboard, _facts
from newsletter.test_newsletter_historical_http import _files
from webservice import server


class _Clock(dt.datetime):
    """Use the fixture's preview clock independently of the real day."""

    @classmethod
    def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
        """Return the stable fixture instant in the requested zone.

        Returns:
            A timezone-aware fixture clock, or its naive local form.
        """
        return (
            _NOW.astimezone(tz)
            if tz is not None
            else _NOW.replace(tzinfo=None)
        )


class TestNewsletterForecastRequests(TestCase):
    """Reuse current forecasts without changing saved facts."""

    @staticmethod
    def test_current_preview_reuses_saved_forecasts_read_only() -> None:
        """Render forecasts from the existing saved dashboard."""
        data = {"issues": {"2027-01": _facts()}}
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            save_observation(
                runtime / OBSERVATIONS_PATH,
                data,
                valid_newsletter,
            )
            app = server.create_app(runtime)
            with (
                TestClient(app) as client,
                patch.object(server.dt, "datetime", _Clock),
                patch.object(
                    server._PublicViews,
                    "_dashboard",
                    return_value=_dashboard(),
                ) as saved,
                patch("pywikibot.Site", side_effect=AssertionError("source")),
                patch(
                    "pywikibot.Page.__init__",
                    side_effect=AssertionError("page"),
                ),
                patch.object(
                    Path,
                    "mkdir",
                    side_effect=AssertionError("write"),
                ),
            ):
                original = _files(runtime)
                response = client.get("/newsletter/?issue=2027-01&lang=en")
                assert response.status_code == 200
                text = unescape(response.text)
                assert "FutureFA" in text
                assert "FutureDYK" in text
                assert "預計" in text
                assert "TooLate" not in text
                assert saved.call_count == 1
                assert _files(runtime) == original

    @staticmethod
    def test_dated_archive_does_not_receive_current_forecasts() -> None:
        """Preserve an archived issue when today's homepage changes."""
        data = {"issues": {"2027-01": _facts()}}
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            NewsletterDraftArchive(runtime / DEFAULT_PATH).save(
                data,
                generated_at=_NOW,
            )
            with (
                TestClient(server.create_app(runtime)) as client,
                patch.object(server.dt, "datetime", _Clock),
                patch.object(
                    server._PublicViews,
                    "_dashboard",
                    return_value=_dashboard(),
                ) as saved,
                patch("pywikibot.Site", side_effect=AssertionError("source")),
                patch(
                    "pywikibot.Page.__init__",
                    side_effect=AssertionError("page"),
                ),
            ):
                response = client.get(
                    "/newsletter/?issue=2027-01&date=2026-11-14&lang=en",
                )
                assert response.status_code == 200
                assert "FutureFA" not in response.text
                saved.assert_not_called()
