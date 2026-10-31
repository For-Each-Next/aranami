"""Verify automatic UTC selection and native issue radios."""

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
        return cls.instant.astimezone(tz or dt.UTC)


class _Radios(HTMLParser):
    """Read semantic issue radio values from the rendered document."""

    def __init__(self, document: str) -> None:
        """Collect issue controls from a complete HTML response."""
        super().__init__()
        self.controls: list[dict[str, str | None]] = []
        self.feed(document)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Retain only native issue radio controls."""
        attributes = dict(attrs)
        if (
            tag == "input"
            and attributes.get("name") == "issue"
            and attributes.get("type") == "radio"
        ):
            self.controls.append(attributes)

    @property
    def selected(self) -> list[str | None]:
        """The values of checked controls in the issue group."""
        return [
            control["value"]
            for control in self.controls
            if "checked" in control
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
        assert _Radios(before.text).selected == ["2027-01"]
        assert _Radios(after.text).selected == ["2027-04"]
        assert {radio["value"] for radio in _Radios(after.text).controls} == {
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
        assert _Radios(before.text).selected == ["2027-01"]
        assert _Radios(after.text).selected == ["2027-04"]

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
                controls = _Radios(response.text)
                assert controls.selected == ["2027-01"]
                assert len(controls.controls) == 2  # ruff: ignore[magic-value-comparison]
                assert all("required" in radio for radio in controls.controls)
                assert "issue=2027-01" in response.text
                assert "<fieldset" in response.text
                assert "<legend>" in response.text
                assert "hidden data-newsletter-issue-form" in response.text
                assert 'type="submit"' not in response.text
                assert "<noscript>" in response.text
                assert (
                    f'href="/newsletter/?issue=2027-01&amp;lang={locale}"'
                    in response.text
                )

    def test_explicit_older_issue_is_kept_as_a_selected_radio(self) -> None:
        """Retain valid bookmarked issue choices in the native form."""
        _Clock.instant = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
        with patch("backend.public.dt.datetime", _Clock):
            response = self.client.get(
                "/newsletter/?issue=2026-07&lang=en",
            )
        controls = _Radios(response.text)
        assert controls.selected == ["2026-07"]
        assert {radio["value"] for radio in controls.controls} == {
            "2026-07",
            "2027-01",
            "2027-04",
        }


if __name__ == "__main__":
    unittest.main()
