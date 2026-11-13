"""Verify automatic UTC selection and the native issue menu."""

from __future__ import annotations

import datetime as dt
import re
import tempfile
import unittest
from html.parser import HTMLParser
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from data.newsletter.archive import DEFAULT_PATH, NewsletterDraftArchive
from data.newsletter.observations import OBSERVATIONS_PATH, save_observation
from data.newsletter.publication import PUBLICATION_PAGE
from data.newsletter.schema import valid_newsletter
from newsletter.test_newsletter_data import Reader
from tasks.zhwiki.newsletter.collection import collect_source_facts
from webservice.server import create_app


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

    @property
    def available(self) -> set[str | None]:
        """The selectable issues, excluding an archived placeholder."""
        return {
            option["value"]
            for option in self.options
            if "disabled" not in option
        }


class NewsletterIssueControlsTests(unittest.TestCase):
    """Keep the issue current across UTC quarter boundaries."""

    def setUp(self) -> None:
        """Use a socket-free app without source files."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.client = TestClient(
            create_app(self.runtime),
            raise_server_exceptions=False,
        )
        self.addCleanup(self.client.close)

    def _save_drafts(self) -> dict:
        """Save November's offline observations and publication states.

        Returns:
            Facts for the overdue October and upcoming January issues.
        """
        reader = Reader()
        reader.existing[PUBLICATION_PAGE] = (
            'return {{id="2026-07", date={2026,7,2}},'
            '{id="2026-10", date={2026,10,nil}}}'
        )
        facts = collect_source_facts(
            reader,
            now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        )
        save_observation(
            self.runtime / OBSERVATIONS_PATH,
            facts,
            valid_newsletter,
        )
        return facts

    def test_next_scheduled_issue_keeps_overdue_unpublished_choices(
        self,
    ) -> None:
        """Default November to January while keeping October's draft."""
        facts = self._save_drafts()
        assert facts["lifecycle"]["default_issue"] == "2026-10"
        _Clock.instant = dt.datetime(
            2026,
            11,
            2,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        with patch("webservice.server.dt.datetime", _Clock):
            for locale in ("en", "zh-hans", "zh-hant"):
                response = self.client.get(f"/newsletter/?lang={locale}")
                assert response.status_code == HTTPStatus.OK
                menu = _IssueMenu(response.text)
                assert menu.selected == ["2027-01"]
                assert menu.available == {"2026-10", "2027-01"}
            previous = self.client.get("/newsletter/?issue=2026-10&lang=en")
        assert _IssueMenu(previous.text).selected == ["2026-10"]
        assert _IssueMenu(previous.text).available == {"2026-10", "2027-01"}

    def test_publication_refresh_hides_options_but_preserves_archives(
        self,
    ) -> None:
        """Refresh choices without changing saved dated output."""
        facts = self._save_drafts()
        NewsletterDraftArchive(self.runtime / DEFAULT_PATH).save(
            facts,
            generated_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        )
        url = "/newsletter/?issue=2026-10&date=2026-09-30&lang=en"
        _Clock.instant = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
        with patch("webservice.server.dt.datetime", _Clock):
            before = self.client.get(url)
            assert _IssueMenu(before.text).available == {"2026-10", "2027-01"}
            facts["lifecycle"].update(
                active=["2027-01"],
                default_issue="2027-01",
            )
            facts["lifecycle"]["states"]["2026-10"] = "published"
            save_observation(
                self.runtime / OBSERVATIONS_PATH,
                facts,
                valid_newsletter,
            )
            files = {
                path: path.read_bytes()
                for path in self.runtime.rglob("*")
                if path.is_file()
            }
            with patch("pywikibot.Site") as site:
                after = self.client.get(url)
                latest = self.client.get("/newsletter/?issue=2026-10&lang=en")
            site.assert_not_called()
        for response in (after, latest):
            assert response.status_code == HTTPStatus.OK
            menu = _IssueMenu(response.text)
            assert menu.selected == ["2026-10"]
            assert menu.available == {"2027-01"}
            placeholder = next(
                option for option in menu.options if "selected" in option
            )
            assert "disabled" in placeholder
            assert "hidden" in placeholder
            assert "2026-10 (Archived issue)" in response.text
            assert "data-newsletter-wikitext" in response.text
        assert 'name="date" type="date" value="2026-09-30"' in after.text
        assert {
            path: path.read_bytes()
            for path in self.runtime.rglob("*")
            if path.is_file()
        } == files
        (self.runtime / OBSERVATIONS_PATH / "current.json").write_text("{")
        archived = self.client.get(url)
        assert archived.status_code == HTTPStatus.OK
        assert "data-newsletter-wikitext" in archived.text

    def test_closed_upcoming_issue_advances_the_default(self) -> None:
        """Avoid published or cancelled issues for the default."""
        facts = self._save_drafts()
        facts["lifecycle"]["active"] = ["2026-10"]
        facts["lifecycle"]["states"].update({
            "2027-01": "published",
            "2027-04": "cancelled",
        })
        save_observation(
            self.runtime / OBSERVATIONS_PATH,
            facts,
            valid_newsletter,
        )
        _Clock.instant = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
        with patch("webservice.server.dt.datetime", _Clock):
            response = self.client.get("/newsletter/?lang=en")
        menu = _IssueMenu(response.text)
        assert menu.selected == ["2027-07"]
        assert menu.available == {"2026-10", "2027-07"}

    def test_midnight_replaces_fresh_cached_default(
        self,
    ) -> None:
        """Switch from January to April at the exact UTC boundary."""
        _Clock.instant = dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=dt.UTC)
        with patch("webservice.server.dt.datetime", _Clock):
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
        with patch("webservice.server.dt.datetime", _Clock):
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
        with patch("webservice.server.dt.datetime", _Clock):
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
                assert re.search(
                    r'<label\b[^>]*\bfor="newsletter-issue"',
                    response.text,
                )
                assert menu.attributes["id"] == "newsletter-issue"
                assert 'type="radio"' not in response.text
                assert "hidden data-newsletter-issue-form" not in response.text
                assert 'type="submit"' in response.text

    def test_explicit_older_issue_is_kept_as_a_selected_option(self) -> None:
        """Retain valid bookmarked issue choices in the native form."""
        _Clock.instant = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
        with patch("webservice.server.dt.datetime", _Clock):
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
