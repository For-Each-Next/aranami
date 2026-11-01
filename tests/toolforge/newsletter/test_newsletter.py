"""Verify quarterly newsletter drafts and read-only public requests."""

# Chinese source fixtures preserve their original punctuation.
# This suite uses unittest exception assertions.
# ruff: file-ignore[ambiguous-unicode-character-string, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
import unittest
from copy import deepcopy
from html import unescape
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi.testclient import TestClient

from backend.newsletter.archive import (
    DEFAULT_PATH as DRAFT_ARCHIVE_PATH,
    NewsletterDraftArchive,
)
from backend.newsletter.layout import prepare_main_layout
from backend.newsletter.observations import OBSERVATIONS_PATH, save_observation
from backend.newsletter.schema import valid_newsletter
from backend.public import create_app
from backend.website.collect import REPORT_KEYS, validate_dashboard_snapshot
from newsletter.fixtures import subpage_layouts
from shared.newsletter import ROOT, SUFFIXES, Issue
from updater.services.newsletter import render_issue


def _main_layout() -> dict:
    """Prepare the actual previous issue as an offline layout fixture.

    Returns:
        Source markup with generated fields replaced by slots.
    """
    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "newsletter-main-2026-10.wikitext"
    )
    layout = prepare_main_layout(
        fixture.read_text(encoding="utf-8"),
        Issue.parse("2026-10"),
    )
    assert layout is not None
    return layout


class NewsletterIssueTests(unittest.TestCase):
    """Keep quarterly periods independent of clocks."""

    @staticmethod
    def test_january_rollover_and_verified_sequence() -> None:
        """Make January 2027 issue 29 cover the preceding quarter."""
        issue = Issue.parse("2027-01")
        assert issue.start == dt.date(2026, 10, 1)
        assert issue.end == dt.date(2027, 1, 1)
        assert issue.previous == "2026-10"
        assert issue.next == "2027-04"
        assert issue.number == 29  # ruff: ignore[magic-value-comparison]
        assert issue.quarter_label == "2026年第四季"
        assert Issue.parse("2026-10").next == "2027-01"

    @staticmethod
    def test_leap_february_remains_inside_the_first_quarter() -> None:
        """Include February 29 inside the reporting quarter."""
        issue = Issue.parse("2028-04")
        assert issue.start == dt.date(2028, 1, 1)
        assert issue.start <= dt.date(2028, 2, 29) < issue.end
        assert issue.end == dt.date(2028, 4, 1)
        assert (issue.end - issue.start).days == 91  # ruff: ignore[magic-value-comparison]
        assert render_issue(issue)["period"].endswith("2028-03-31")

    @staticmethod
    def test_upcoming_issue_tracks_current_quarter_and_year() -> None:
        """Select the next publication throughout the quarter."""
        for day, expected in (
            (dt.date(2026, 10, 31), "2027-01"),
            (dt.date(2026, 12, 31), "2027-01"),
            (dt.date(2027, 1, 1), "2027-04"),
            (dt.date(2028, 2, 29), "2028-04"),
            (dt.date(2028, 4, 1), "2028-07"),
        ):
            assert Issue.upcoming(day).value == expected

    def test_invalid_or_nonquarterly_issue_is_rejected(self) -> None:
        """Reject malformed and unsupported publication months."""
        for value in (
            "",
            "2027-02",
            "2027-1",
            "2027-01-01",
            "2019-10",
            "2100-01",
            " 2027-01",
            "2027-01\n",
            "../2027-01",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Issue.parse(value)


class NewsletterWikitextTests(unittest.TestCase):
    """Keep verified facts distinct from editorial work still to do."""

    @staticmethod
    def test_january_destinations_and_quarter_navigation() -> None:
        """Generate January's combined feature with matching URLs."""
        output = render_issue(
            Issue.parse("2027-01"),
            {
                "issue": "2027-01",
                "available": True,
                "overview": {"total": 1},
                "main_layout": _main_layout(),
                "subpage_layouts": subpage_layouts(),
            },
        )
        expected = [
            f"{ROOT}/2027-01" + (f"/{suffix}" if suffix else "")
            for suffix in Issue.parse("2027-01").draft_suffixes
        ]
        assert [page["title"] for page in output["pages"]] == expected
        for page in output["pages"]:
            assert (
                unquote(urlsplit(page["url"]).path) == "/wiki/" + page["title"]
            )
        main = output["pages"][0]["text"]
        assert "第廿九號" in main
        assert "2026年第四季" in main
        assert "/2027-01/訪談" not in main
        assert "專題報導兼訪談：2026年年終回顧" in main
        assert "[[維基專題:電子遊戲/簡訊/2026-10|上一期]]" in main
        assert "[[維基專題:電子遊戲/簡訊/2027-04|下一期]]" in main

    @staticmethod
    def test_missing_statistics_are_not_measured_zeroes() -> None:
        """Distinguish unknown totals from an explicitly saved zero."""
        issue = Issue.parse("2027-01")
        unavailable = render_issue(issue)
        assert not unavailable["available"]
        assert unavailable["pages"] == []
        available = render_issue(
            issue,
            {
                "issue": issue.value,
                "available": True,
                "overview": {"total": 0},
                "dyk": [],
                "main_layout": _main_layout(),
                "subpage_layouts": subpage_layouts(),
            },
        )
        assert "* 0篇條目" in available["pages"][0]["text"]
        dyk = (
            available["pages"][2]["text"]
            .partition(
                "==== 新條目推薦 ====",
            )[2]
            .partition("==== 優良條目 ====")[0]
        )
        assert "以下條目曾登上首頁的「新條目推薦」區塊。" in dyk
        assert "{{div col|colwidth=27em}}\n（無）\n{{div col end}}" in dyk

    @staticmethod
    def test_editorial_templates_do_not_reuse_authored_prose() -> None:
        """Generate blank editorial drafts without copying old prose."""
        existing = {
            "專題": "== 手工專題 ==\n作者：[[User:編輯]]\n{{例子|原文}}\n",
            "訪談": "== 訪談 ==\n;問：為何？\n:答：保留原文。\n",
        }
        output = render_issue(
            Issue.parse("2027-01"),
            {
                "issue": "2027-01",
                "available": True,
                "overview": {"total": 1},
                "existing": existing,
                "main_layout": _main_layout(),
                "subpage_layouts": subpage_layouts(),
            },
        )
        assert len(output["pages"]) == len(
            Issue.parse("2027-01").draft_suffixes,
        )
        assert output["pages"][-1]["title"].endswith("/專題")
        assert all("/訪談" not in page["title"] for page in output["pages"])
        assert "手工專題" not in "\n".join(
            page["text"] for page in output["pages"]
        )

    def test_wrong_issue_facts_cannot_be_reused(self) -> None:
        """Reject old editorial text and statistics as current facts."""
        with self.assertRaises(ValueError):
            render_issue(
                Issue.parse("2027-01"),
                {
                    "issue": "2026-10",
                    "overview": {"total": 123},
                    "existing": {"專題": "上期專題"},
                },
            )


class NewsletterRouteTests(unittest.TestCase):
    """Exercise issue selection through real HTTP requests."""

    def setUp(self) -> None:
        """Create a temporary runtime without source data."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.client = TestClient(create_app(self.runtime))
        self.addCleanup(self.client.close)

    def _save_issue(
        self,
        facts: dict[str, object],
        *,
        durable: bool = True,
    ) -> Path:
        """Write a validated offline fixture before HTTP requests.

        Returns:
            The saved dashboard path for checking unchanged contents.
        """
        stamp = "2027-01-02T00:00:00+00:00"
        counts = dict.fromkeys((
            "total",
            "fa",
            "fl",
            "ga",
            "dyk",
            "bplus",
            "topics",
        ))
        complete = {
            "issue": "2027-01",
            "period": "2026-10-01/2027-01-01",
            "updated_at": stamp,
            "available": True,
            "observation_end": "2027-01-01",
            "complete": True,
            "overview": counts,
            "previous_overview": counts,
            "quality": None,
            "new_pages": None,
            "announcements": None,
            "dyk": None,
            "awards": None,
            "newcomers": None,
            "existing": dict.fromkeys(SUFFIXES),
            "main_layout": _main_layout(),
            "subpage_layouts": subpage_layouts(),
            "sources": [],
            **facts,
        }
        complete["existing"] = dict.fromkeys(SUFFIXES) | facts.get(
            "existing",
            {},
        )
        complete["overview"] = counts | facts.get("overview", {})
        snapshot = {
            "schema": 2,
            "period": {
                "label": "Fixture",
                "start": "2027-01-01",
                "end": "2027-01-02",
            },
            "updated_at": stamp,
            "reports": {
                name: {
                    "config_id": "a" * 64,
                    "source_config_id": "a" * 64,
                    "refresh_interval_seconds": 86400,
                    "last_success_at": stamp,
                    "last_attempt_at": stamp,
                    "failed": False,
                }
                for name in REPORT_KEYS
            },
            "homepage": dict.fromkeys((
                "featured",
                "good",
                "upcoming",
                "dyk",
                "news",
                "history",
            )),
            "reviews": None,
            "pageviews": None,
            "backlog": None,
            "newsletter": {"issues": {"2027-01": complete}},
            "sources": [],
            "errors": [],
        }
        if durable:
            save_observation(
                self.runtime / OBSERVATIONS_PATH,
                snapshot["newsletter"],
                valid_newsletter,
            )
        assert validate_dashboard_snapshot(snapshot)
        path = self.runtime / "cache" / "toolforge" / "dashboard.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return path

    def test_methods_without_sources_or_writes(self) -> None:
        """Serve defaults without creating files or source clients."""
        with (
            patch("pywikibot.Site") as site,
            patch("backend.website.collect.collect_dashboard") as collect,
        ):
            response = self.client.get("/newsletter/?issue=2027-01&lang=en")
            head = self.client.head("/newsletter/?issue=2027-01&lang=en")
            post = self.client.post("/newsletter/?issue=2027-01")
        assert response.status_code == HTTPStatus.OK
        assert head.status_code == HTTPStatus.OK
        assert head.content == b""
        assert (
            head.headers["content-length"]
            == response.headers["content-length"]
        )
        assert post.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        assert 'value="2027-01"' in response.text
        assert "<select" in response.text
        assert 'id="newsletter-issue"' in response.text
        assert 'type="radio"' not in response.text
        assert 'name="issue"' in response.text
        assert response.headers["Content-Language"] == "en"
        assert "<textarea " not in response.text
        assert not list(self.runtime.iterdir())
        site.assert_not_called()
        collect.assert_not_called()

    def test_old_newsletter_does_not_hide_other_saved_reports(self) -> None:
        """Preserve valid reports during newsletter migration."""
        path = self._save_issue({"overview": {"total": 13025}})
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        facts = snapshot["newsletter"]["issues"]["2027-01"]
        del facts["observation_end"]
        del facts["complete"]
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        response = self.client.get("/api/dashboard")
        assert response.status_code == HTTPStatus.OK
        assert response.json()["newsletter"] is None
        assert response.json()["updated_at"] == snapshot["updated_at"]
        assert path.read_text(encoding="utf-8") == json.dumps(snapshot)

    def test_invalid_or_repeated_issue_returns_localized_error(self) -> None:
        """Reject duplicate controls and unsupported months."""
        for query in (
            "issue=2027-02",
            "issue=",
            "issue=2019-10",
            "issue=2100-01",
            "issue=2027-01&issue=2027-04",
            "issue=2027-01&unexpected=value",
        ):
            with self.subTest(query=query):
                response = self.client.get(
                    f"/newsletter/?{query}&lang=zh-hant",
                )
                assert response.status_code == HTTPStatus.BAD_REQUEST
                assert "請選擇可用的刊號。" in response.text

    def test_default_issue_shows_automatically_collected_statistics(
        self,
    ) -> None:
        """Display saved data for the automatically selected issue."""
        self._save_issue({"overview": {"total": 13025}})
        with patch(
            "backend.public.Issue.upcoming",
            return_value=Issue.parse("2027-01"),
        ) as upcoming:
            response = self.client.get("/newsletter/?lang=en")
        assert response.status_code == HTTPStatus.OK
        assert response.text.count("data-newsletter-wikitext") == len(
            Issue.parse("2027-01").draft_suffixes,
        )
        assert "13,025篇條目" in response.text
        assert "待補" not in response.text
        upcoming.assert_called_once()

    def test_language_links_retain_selected_issue(self) -> None:
        """Keep the selected issue when switching language."""
        output = self.client.get("/newsletter/?issue=2028-04&lang=en").text
        links = re.findall(
            r'<a href="([^"]+)" lang="(?:en|zh-Hans|zh-Hant)"',
            output,
        )
        assert len(links) == 3  # ruff: ignore[magic-value-comparison]
        for href in links:
            target = urlsplit(unescape(href))
            assert target.path == "/newsletter/"
            assert parse_qs(target.query)["issue"] == ["2028-04"]

    def test_source_and_editorial_text_are_escaped(self) -> None:
        """Keep saved wiki text inside escaped source displays."""
        dangerous = "</code></pre><script>alert('saved')</script>"
        facts = {
            "issue": "2027-01",
            "announcements": dangerous,
            "sources": [
                {
                    "label": "<img src=x onerror=alert(1)>",
                    "url": "https://zh.wikipedia.org/w/index.php?oldid=123",
                },
            ],
        }
        path = self._save_issue(facts)
        original = path.read_bytes()
        before = {
            saved: saved.read_bytes()
            for saved in self.runtime.rglob("*")
            if saved.is_file()
        }
        with (
            patch("pywikibot.Site") as site,
            patch("backend.website.collect.collect_dashboard") as collect,
        ):
            response = self.client.get("/newsletter/?issue=2027-01")
        assert response.status_code == HTTPStatus.OK
        assert dangerous not in response.text
        assert "&lt;/code&gt;&lt;/pre&gt;&lt;script&gt;" in response.text
        assert "onerror=alert(1)" not in response.text
        assert "newsletter-sources" not in response.text
        assert path.read_bytes() == original
        assert {
            saved: saved.read_bytes()
            for saved in self.runtime.rglob("*")
            if saved.is_file()
        } == before
        site.assert_not_called()
        collect.assert_not_called()

    def test_saved_notices_are_not_copied_to_another_issue(self) -> None:
        """Use collected source facts only for their exact issue."""
        self._save_issue({"announcements": "本期已寫好的獨有公告"})
        selected = self.client.get("/newsletter/?issue=2027-01")
        different = self.client.get("/newsletter/?issue=2027-04")
        assert "本期已寫好的獨有公告" in selected.text
        assert "本期已寫好的獨有公告" not in different.text
        assert "<textarea " not in different.text

    def _archive_issue(
        self,
        day: str,
        total: int,
    ) -> dict:
        """Save synthetic daily facts with their actual capture time.

        Returns:
            The validated archive envelope for the selected day.
        """
        boundary = dt.date.fromisoformat(day) + dt.timedelta(days=1)
        issue = Issue.parse("2027-01")
        path = self._save_issue(
            {
                "updated_at": f"{boundary}T00:00:00+00:00",
                "observation_end": str(boundary),
                "complete": boundary == issue.end,
                "overview": {"total": total},
            },
            durable=False,
        )
        archive = NewsletterDraftArchive(self.runtime / DRAFT_ARCHIVE_PATH)
        archive.save(
            json.loads(path.read_text())["newsletter"],
            generated_at=dt.datetime.combine(
                boundary,
                dt.time(0, 4, 5),
                dt.UTC,
            ),
        )
        entry = archive.read_entry(issue, dt.date.fromisoformat(day))
        assert entry is not None
        return entry

    def test_daily_facts_use_current_renderer_without_newer_data(self) -> None:
        """Render saved facts while preserving their capture."""
        entry = self._archive_issue("2026-10-30", 13100)
        issue = Issue.parse("2027-01")
        rendered = render_issue(issue, entry["facts"])
        rendered["pages"][0]["text"] += "\nCurrent renderer output"
        self._save_issue({"overview": {"total": 999999}}, durable=False)
        before = {
            path: path.read_bytes()
            for path in self.runtime.rglob("*")
            if path.is_file()
        }
        for selection in ("", "&date=2026-10-30"):
            with (
                self.subTest(selection=selection),
                patch("pywikibot.Site") as site,
                patch(
                    "backend.public.render_issue",
                    return_value=deepcopy(rendered),
                ) as project,
            ):
                response = self.client.get(
                    "/newsletter/?issue=2027-01&lang=en" + selection,
                )
            assert response.status_code == HTTPStatus.OK
            texts = [
                unescape(text)
                for text in re.findall(
                    r"data-newsletter-wikitext>(.*?)</code>",
                    response.text,
                    re.DOTALL,
                )
            ]
            assert texts == [page["text"] for page in rendered["pages"]]
            assert len(texts) == len(Issue.parse("2027-01").draft_suffixes)
            assert "2026年年終回顧" in texts[-1]
            assert "待核對" not in texts[-1]
            assert [
                line
                for line in texts[-1].splitlines()
                if line.startswith("==== ")
            ] == [
                "==== 內容評審 ====",
                "==== 新章推薦 ====",
                "==== 条目报告 ====",
                "==== 榮譽表彰 ====",
            ]
            assert "999,999" not in response.text
            assert "Current renderer output" in response.text
            assert "not part of the original draft" not in response.text
            assert 'newsletter-heading-4">Feature</h2>' in response.text
            assert "00:04:05" in response.text
            site.assert_not_called()
            project.assert_called_once_with(issue, entry["facts"])
        assert all(path.read_bytes() == data for path, data in before.items())

    def test_selected_day_renders_its_facts_without_current_enrichment(
        self,
    ) -> None:
        """Render the selected facts without source reads or writes."""
        entry = self._archive_issue("2026-10-29", 13000)
        self._archive_issue("2026-10-30", 13100)
        before = {
            path: path.read_bytes()
            for path in self.runtime.rglob("*")
            if path.is_file()
        }
        with (
            patch("pywikibot.Site") as site,
            patch(
                "backend.public.render_issue",
                wraps=render_issue,
            ) as project,
        ):
            response = self.client.get(
                "/newsletter/?issue=2027-01&date=2026-10-29&lang=en",
            )
        assert response.status_code == HTTPStatus.OK
        assert "13,000篇條目" in response.text
        assert "13,100篇條目" not in response.text
        assert 'type="date" value="2026-10-29"' in response.text
        assert "00:04:05" in response.text
        assert "2026-10-30" in response.text
        assert all(
            path.read_bytes() == value for path, value in before.items()
        )
        site.assert_not_called()
        project.assert_called_once_with(Issue.parse("2027-01"), entry["facts"])

    def test_missing_daily_draft_never_substitutes_latest_data(self) -> None:
        """Keep missing dates separate from current drafts."""
        self._archive_issue("2026-10-30", 13100)
        response = self.client.get(
            "/newsletter/?issue=2027-01&date=2026-10-29&lang=en",
        )
        assert response.status_code == HTTPStatus.OK
        assert "No saved draft is available for this date." in response.text
        assert "13,100篇條目" not in response.text
        assert "data-newsletter-wikitext" not in response.text

    def test_calendar_rejects_invalid_duplicate_and_wrong_quarter_dates(
        self,
    ) -> None:
        """Reject malformed controls before reading the archive."""
        for query in (
            "date=",
            "date=20261030",
            "date=2026-02-30",
            "date=2027-01-01",
            "date=2025-12-31",
            "date=2026-10-29&feature_date=2026-10-30",
            "date=2026-10-29&date=2026-10-30",
            "date=../../other",
        ):
            with self.subTest(query=query):
                response = self.client.get(
                    f"/newsletter/?issue=2027-01&{query}&lang=zh-hant",
                )
                assert response.status_code == HTTPStatus.BAD_REQUEST
                assert "YYYY-MM-DD" in response.text

    def test_language_links_retain_daily_selection(self) -> None:
        """Carry the selected data date through locale switches."""
        output = self.client.get(
            "/newsletter/?issue=2027-01&date=2026-10-30&lang=en",
        ).text
        links = re.findall(
            r'<a href="([^"]+)" lang="(?:en|zh-Hans|zh-Hant)"',
            output,
        )
        assert links
        for href in links:
            values = parse_qs(urlsplit(unescape(href)).query)
            assert values["issue"] == ["2027-01"]
            assert values["date"] == ["2026-10-30"]

    def test_calendar_cache_tracks_archive_without_dashboard_replacement(
        self,
    ) -> None:
        """Invalidate cached HTML when a new daily entry is captured."""
        entry = self._archive_issue("2026-10-29", 13000)
        url = "/newsletter/?issue=2027-01&lang=en"
        first = self.client.get(url)
        assert "13,000篇條目" in first.text
        facts = entry["facts"]
        facts.update(
            updated_at="2026-10-31T00:00:00+00:00",
            observation_end="2026-10-31",
        )
        facts["overview"]["total"] = 13100
        archive = NewsletterDraftArchive(self.runtime / DRAFT_ARCHIVE_PATH)
        archive.save(
            {"issues": {"2027-01": facts}},
            generated_at=dt.datetime(2026, 10, 31, 0, 4, 5, tzinfo=dt.UTC),
        )
        second = self.client.get(url)
        assert "13,100篇條目" in second.text
        assert '<option value="2026-10-30">' in second.text

    def _save_durable(self, total: int) -> None:
        """Persist complete version-two fixture facts."""
        source = self._save_issue({"overview": {"total": total}})
        facts = json.loads(source.read_text())["newsletter"]
        save_observation(
            self.runtime / OBSERVATIONS_PATH,
            facts,
            valid_newsletter,
        )

    def test_latest_durable_facts_take_precedence_without_sources(
        self,
    ) -> None:
        """Prefer durable facts over older drafts and dashboard data."""
        self._archive_issue("2026-10-30", 13100)
        self._save_durable(14000)
        self._save_issue({"overview": {"total": 999999}}, durable=False)
        before = {
            path: path.read_bytes()
            for path in self.runtime.rglob("*")
            if path.is_file()
        }
        with (
            patch("pywikibot.Site") as site,
            patch("backend.website.collect.collect_dashboard") as collect,
        ):
            response = self.client.get("/newsletter/?issue=2027-01&lang=en")
        assert response.status_code == HTTPStatus.OK
        assert "14,000篇條目" in response.text
        assert "13,100篇條目" not in response.text
        assert "999,999" not in response.text
        assert 'type="date" value="2026-12-31"' not in response.text
        assert 'type="date" value="2026-10-30"' in response.text
        assert 'class="context newsletter-observation"' not in response.text
        destinations = re.findall(
            r'<a class="cdx-button[^"]*" href="([^"]+)"',
            response.text,
        )
        assert destinations
        for view_url, edit_url in zip(
            destinations[::2],
            destinations[1::2],
            strict=True,
        ):
            assert not urlsplit(unescape(view_url)).query
            query = parse_qs(urlsplit(unescape(edit_url)).query)
            assert query["action"] == ["edit"]
            assert query["summary"][0].startswith(
                "[[toolforge:aranami/newsletter|",
            )
        assert "No saved draft is available" not in response.text
        assert all(
            path.read_bytes() == value for path, value in before.items()
        )
        site.assert_not_called()
        collect.assert_not_called()

    def test_explicit_date_ignores_newer_durable_facts(self) -> None:
        """Render the selected day's facts after newer data is saved."""
        entry = self._archive_issue("2026-10-30", 13100)
        self._save_durable(14000)
        with patch("backend.public.read_newsletter_data") as read:
            response = self.client.get(
                "/newsletter/?issue=2027-01&date=2026-10-30&lang=en",
            )
        read.assert_not_called()
        texts = [
            unescape(text)
            for text in re.findall(
                r"data-newsletter-wikitext>(.*?)</code>",
                response.text,
                re.DOTALL,
            )
        ]
        assert texts == [
            page["text"]
            for page in render_issue(
                Issue.parse("2027-01"),
                entry["facts"],
            )["pages"]
        ]
        assert "14,000篇條目" not in response.text

    def test_durable_pointer_invalidates_same_timestamp_page(self) -> None:
        """Refresh enriched data without advancing observation time."""
        self._save_durable(14000)
        url = "/newsletter/?issue=2027-01&lang=en"
        assert "14,000篇條目" in self.client.get(url).text
        directory = self.runtime / OBSERVATIONS_PATH
        facts = json.loads((directory / "current.json").read_text())["facts"]
        facts["issues"]["2027-01"]["overview"]["total"] = 15000
        save_observation(directory, facts, valid_newsletter)
        assert "15,000篇條目" in self.client.get(url).text

    def test_corrupt_durable_data_never_falls_back_to_old_drafts(self) -> None:
        """Report damaged data without substituting old drafts."""
        self._archive_issue("2026-10-30", 13100)
        self._save_durable(14000)
        url = "/newsletter/?issue=2027-01&lang=en"
        assert self.client.get(url).status_code == HTTPStatus.OK
        (self.runtime / OBSERVATIONS_PATH / "current.json").write_text("{}")
        response = self.client.get(url)
        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert "13,100篇條目" not in response.text

    def test_corrupt_daily_catalog_reports_unavailable(self) -> None:
        """Keep unreadable history distinct from an unrecorded date."""
        self._archive_issue("2026-10-30", 13100)
        path = self.runtime / DRAFT_ARCHIVE_PATH / "2027-01" / "catalog.json"
        path.write_text("{}", encoding="utf-8")
        response = self.client.get(
            "/newsletter/?issue=2027-01&date=2026-10-30&lang=en",
        )
        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert "temporarily unavailable" in response.text
        assert "13,100篇條目" not in response.text


if __name__ == "__main__":
    unittest.main()
