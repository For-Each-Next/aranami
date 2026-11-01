"""Verify automatic UTC selection and the native issue menu."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from html.parser import HTMLParser
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.public import create_app


class _Clock(dt.datetime):
    """Provide an aware, movable request clock."""

    instant = dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=dt.UTC)

    @classmethod
    def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
        """Return the selected instant in the requested timezone."""
        return (
            cls.instant.astimezone(tz)
            if tz is not None
            else cls.instant.astimezone().replace(tzinfo=None)
        )


class _IssueMenu(HTMLParser):
    """Read native issue options from the rendered document."""

    def __init__(self, document: str) -> None:
        """Collect issue controls from a complete HTML response."""
        super().__init__()
        self.attributes: dict[str, str | None] = {}
        self.options: list[dict[str, str | None]] = []
        self._inside = False
        self.feed(document)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Retain only options belonging to the native issue menu."""
        attributes = dict(attrs)
        if tag == "select" and attributes.get("name") == "issue":
            self.attributes = attributes
            self._inside = True
        elif tag == "option" and self._inside:
            self.options.append(attributes)

    def handle_endtag(self, tag: str) -> None:
        """Exclude options after the issue menu has closed."""
        if tag == "select":
            self._inside = False

    @property
    def selected(self) -> list[str | None]:
        """The explicitly selected values in the issue menu."""
        return [
            option["value"] for option in self.options if "selected" in option
        ]


class NewsletterIssueControlsTests(unittest.TestCase):
    """Keep the issue current across UTC quarter boundaries."""

    def setUp(self) -> None:
        """Use a socket-free app without source files."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.client = TestClient(
            create_app(Path(temporary.name)),
            raise_server_exceptions=False,
        )
        self.addCleanup(self.client.close)

    def test_midnight_replaces_fresh_cached_default(
        self,
    ) -> None:
        """Switch from January to April at the exact UTC boundary."""
        _Clock.instant = dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=dt.UTC)
        with patch("backend.public.dt.datetime", _Clock):
            before = self.client.get("/newsletter/?lang=zh-hant")
            _Clock.instant = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
            after = self.client.get("/newsletter/?lang=zh-hant")
        assert before.status_code == after.status_code == HTTPStatus.OK
        assert _IssueMenu(before.text).selected == ["2027-01"]
        menu = _IssueMenu(after.text)
        assert menu.selected == ["2027-04"]
        assert {option["value"] for option in menu.options} == {
            "2027-01",
            "2027-04",
        }
        assert "00:00 UTC" not in after.text
        assert 'type="month"' not in after.text

    def test_automatic_choice_uses_utc_instead_of_local_midnight(self) -> None:
        """Keep January selected until UTC reaches the new quarter."""
        local = dt.timezone(dt.timedelta(hours=8))
        _Clock.instant = dt.datetime(2027, 1, 1, tzinfo=local)
        with patch("backend.public.dt.datetime", _Clock):
            before = self.client.get("/newsletter/?lang=en")
            _Clock.instant = dt.datetime(2027, 1, 1, 8, tzinfo=local)
            after = self.client.get("/newsletter/?lang=en")
        assert _IssueMenu(before.text).selected == ["2027-01"]
        assert _IssueMenu(after.text).selected == ["2027-04"]

    def test_explicit_previous_issue_survives_rollover_and_languages(
        self,
    ) -> None:
        """Allow prior-issue inspection after the default advances."""
        _Clock.instant = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
        with patch("backend.public.dt.datetime", _Clock):
            for locale in ("en", "zh-hans", "zh-hant"):
                response = self.client.get(
                    f"/newsletter/?issue=2027-01&lang={locale}",
                )
                assert response.status_code == HTTPStatus.OK
                menu = _IssueMenu(response.text)
                assert menu.selected == ["2027-01"]
                assert len(menu.options) == 2  # ruff: ignore[magic-value-comparison]
                assert "required" in menu.attributes
                assert "issue=2027-01" in response.text
                assert '<label for="newsletter-issue">' in response.text
                assert menu.attributes["id"] == "newsletter-issue"
                assert 'type="radio"' not in response.text
                assert "hidden data-newsletter-issue-form" not in response.text
                assert 'type="submit"' in response.text

    def test_explicit_older_issue_is_kept_as_a_selected_option(self) -> None:
        """Retain valid bookmarked issue choices in the native form."""
        _Clock.instant = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
        with patch("backend.public.dt.datetime", _Clock):
            response = self.client.get(
                "/newsletter/?issue=2026-07&lang=en",
            )
        menu = _IssueMenu(response.text)
        assert menu.selected == ["2026-07"]
        assert {option["value"] for option in menu.options} == {
            "2026-07",
            "2027-01",
            "2027-04",
        }


if __name__ == "__main__":
    unittest.main()
