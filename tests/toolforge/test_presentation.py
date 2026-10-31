"""Verify Python pages and shared presentation contracts offline."""

# Keep unittest exceptions and expected fixture counts.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import unittest
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from shared.config import routine_configuration
from webservice.pages import (
    render_about,
    render_dashboard,
    render_error,
    render_live_status,
    render_logs,
    render_maintenance,
)
from webservice.presentation import (
    Interface,
    article_extract,
    assessment_code,
    assessment_rank,
    catalog,
    duration,
    event_duration,
    homepage_sections,
    local_url,
    maintenance_items,
    maintenance_page,
    maintenance_sections,
    maintenance_selection,
    merged_items,
    next_run,
    pageviews_url,
    readable_size,
    relative_time,
    schedule_state,
    select_locale,
    timestamp,
    title_url,
    uptime,
    utc_time,
    wiki_link,
    wiki_url,
)


def _fixtures() -> dict[str, object]:
    """Build saved observations with hostile text and all sections.

    Returns:
        Independent website, routine, scheduler, and source fixtures.
    """
    stamp = "2026-10-18T12:00:00Z"
    definition = {
        "pages": {"report": "WikiProject:电子游戏/热门条目"},
        "prefixes": [],
        "hour": "*",
        "minute": 59,
    }
    config = {
        "version": "0.6.0.dev1",
        "routines": {"schema": 1, "routines": {"pageviews": definition}},
    }
    routine = {
        "routine": "pageviews",
        "status": "deferred",
        "dry": False,
        "updated": 0,
        "duration_seconds": 12.5,
        "started_at": "2026-10-18T11:59:47Z",
        "finished_at": stamp,
        "run_id": "a" * 32,
    }
    schedule = {
        "schema": 1,
        "updated_at": stamp,
        "state": "running",
        "config_id": "b" * 64,
        "started_at": "2026-10-16T08:56:00Z",
        "routines": {
            "pageviews": {
                **definition,
                "state": "scheduled",
                "next_run_at": "2026-10-20T18:59:00Z",
            },
        },
    }
    report = {
        "update_id": "c" * 32,
        "run_id": "a" * 32,
        "routine": "pageviews",
        "site": "zhwiki",
        "title": "WikiProject:电子游戏/热门条目 <Latest & safe>",
        "status": "updated",
        "dry": False,
        "started_at": "2026-10-18T11:59:55Z",
        "finished_at": stamp,
        "duration_seconds": 5,
        "confirmed_edits": 1,
    }
    status = {
        "routines": {
            "schema": 1,
            "updated_at": stamp,
            "routines": {"pageviews": routine},
        },
        "schedule": schedule,
        "reports": {
            "schema": 1,
            "updated_at": stamp,
            "reports": {"first": report},
        },
    }
    article = {
        "title": "Game & Characters / 中文",
        "url": "https://zh.wikipedia.org/wiki/Game",
        "extract": "简介 <script>never execute</script> & 中文。第二句。",
        "extract_hant": "簡介 <script>never execute</script> & 中文。第二句。",
        "extract_zh": "原文遊戲与音乐\uff0c保留原始简介。",
        "views": 723,
        "rank": 1,
        "site_rank": 681,
        "assessment_class": "GA",
        "assessment_importance": "High",
        "date": "2026-10-17",
        "kind": "fa",
    }
    maintenance = {
        "page_id": 17,
        "title": "Maintenance <&> article",
        "url": "https://zh.wikipedia.org/wiki/Maintenance",
        "assessment_class": "B",
        "assessment_importance": "High",
        "issues": ["references"],
        "oldest_issue_month": "2020-03",
        "oldest_issue_category": "自2020年03月需补充来源的条目",
    }
    group = {
        "key": "references",
        "category": "缺少来源的条目",
        "source_url": "https://zh.wikipedia.org/wiki/Category:References",
        "section_key": "sources",
        "section_label": {
            "en": "References",
            "zh-hans": "参考来源",
            "zh-hant": "參考來源",
        },
        "label": {
            "en": "Add citations",
            "zh-hans": "补充来源",
            "zh-hant": "補充來源",
        },
        "count": 1,
        "items": [maintenance],
    }
    snapshot = {
        "schema": 2,
        "updated_at": stamp,
        "reports": {
            key: {"last_success_at": stamp}
            for key in (
                "recognized",
                "dyk",
                "pageviews",
                "news_history",
                "backlog",
            )
        },
        "homepage": {
            "featured": [article],
            "good": [{**article, "title": "Good game", "kind": "ga"}],
            "upcoming": [
                {
                    **article,
                    "title": "Scheduled game",
                    "date": "2026-10-20",
                    "date_kind": "scheduled",
                },
            ],
            "dyk": [
                {**article, "title": f"DYK game {index}", "kind": "dyk"}
                for index in range(1, 14)
            ],
            "news": [
                {**article, "title": f"News game {index}", "kind": "news"}
                for index in range(1, 8)
            ],
            "history": [
                {**article, "title": "Historic game", "kind": "history"},
            ],
        },
        "pageviews": {
            "date": "2026-10-17",
            "limit": 10,
            "scope": "site_top_1000",
            "items": [article],
        },
        "backlog": {"observed_at": stamp, "total": 1, "groups": [group]},
        "errors": [],
    }
    return {
        "config": config,
        "status": status,
        "snapshot": snapshot,
        "article": article,
    }


class ReviewPresentationTests(unittest.TestCase):
    """Render review cards from saved observations in every language."""

    def setUp(self) -> None:
        """Create saved observations and a deterministic clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.en = Interface("en")

    def test_review_cards_precede_highlights_in_every_language(self) -> None:
        """Show localized review cards with observed durations."""
        snapshot = self.data["snapshot"]
        snapshot["reports"]["reviews"] = {
            "last_success_at": "2026-10-18T12:00:00Z",
        }
        snapshot["reviews"] = {
            "items": [
                {
                    **self.data["article"],
                    "title": f"Review {index} <&>",
                    "review_type": kind,
                    "review_url": f"https://zh.wikipedia.org/wiki/Talk:Game#Review_{index}",
                    "started_at": "2026-10-07T03:04:00Z"
                    if index == 0
                    else "2026-10-18T10:00:00Z"
                    if index == 1
                    else None,
                }
                for index, kind in enumerate((
                    "fac",
                    "flc",
                    "acc",
                    "gan",
                    "bpan",
                    "pr",
                    "ppr",
                    "dykc",
                    "reassess",
                ))
            ],
        }
        for locale in ("en", "zh-hans", "zh-hant", "zh"):
            with self.subTest(locale=locale):
                interface = Interface(locale)
                rendered = render_dashboard(snapshot, {}, {}, locale, self.now)
                assert rendered.index('id="reviews-heading"') < rendered.index(
                    'id="homepage-heading"',
                )
                section = rendered.split('id="reviews"', 1)[1].split(
                    "</section>",
                    1,
                )[0]
                assert (
                    '<h2 id="reviews-heading">'
                    f"{interface.text('reviews.heading')}</h2>" in section
                )
                assert section.count('class="article-card"') == 9
                assert section.count(interface.text("reviews.started")) == 2
                assert interface.text("reviews.days", count=11) in section
                assert interface.text("reviews.hours", count=2) in section
                assert "Review 0 &lt;&amp;&gt;" in section
                if locale != "zh":
                    assert (
                        "&lt;script&gt;never execute&lt;/script&gt;" in section
                    )
                assert "#Review_0" in section
                assert 'data-assessment="class"' in section
                assert 'data-assessment="importance"' in section
                assert 'href="#reviews"' in rendered

    def test_empty_reviews_hide_section_and_missing_reviews_stay_unknown(
        self,
    ) -> None:
        """Distinguish empty reviews from unavailable data."""
        snapshot = self.data["snapshot"]
        snapshot["reviews"] = {"items": []}
        rendered = render_dashboard(snapshot, {}, {}, "en", self.now)
        assert 'id="reviews-heading"' not in rendered
        assert 'href="#reviews"' not in rendered
        snapshot["reviews"] = None
        rendered = render_dashboard(snapshot, {}, {}, "en", self.now)
        assert 'id="reviews-heading"' in rendered
        section = rendered.split('id="reviews"', 1)[1].split("</section>", 1)[
            0
        ]
        assert self.en.text("empty.section") in section


class PresentationTests(unittest.TestCase):
    """Exercise routes without network or runtime writes."""

    def setUp(self) -> None:
        """Create saved observations and a deterministic clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.en = Interface("en")

    def test_locales_and_catalog(self) -> None:
        """Preserve language weights, scripts, and placeholders."""
        cases = {
            "": "en",
            "fr,de;q=0.9": "en",
            "*": "en",
            "en-US": "en",
            "zh": "zh",
            "zh-CN": "zh-hans",
            "zh-SG": "zh-hans",
            "zh-MY": "zh-hans",
            "zh-TW": "zh-hant",
            "zh-HK": "zh-hant",
            "zh-MO": "zh-hant",
            "zh-Hans-TW": "zh-hans",
            "zh-Hant-CN": "zh-hant",
            "en;q=0.3,zh-TW;q=0.9": "zh-hant",
            "zh-TW;q=0.8,en;q=0.8": "zh-hant",
            "en;q=invalid,zh-TW;q=0.5": "zh-hant",
            "en;q=0.7;q=1,zh-TW;q=0.2": "zh-hant",
            "zh-HK;q=0,en;q=0.001": "en",
            "../../en,zh-TW;q=0.2": "zh-hant",
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                assert select_locale(header) == expected
        assert Interface("zh-hant").language_tag == "zh-Hant"
        assert Interface("zh").text("column.article") == "条目"
        assert self.en.text("backlog.count", count=12) == "12 articles"
        for translations in catalog().values():
            assert list(translations) == ["en", "zh-hans", "zh-hant"]
            parameters = sorted(
                re.findall(r"\{([a-z_]+)\}", translations["en"]),
            )
            for message in translations.values():
                assert message
                assert (
                    sorted(re.findall(r"\{([a-z_]+)\}", message)) == parameters
                )

    def test_safe_html_and_links(self) -> None:
        """Escape data once and reject executable source links."""
        rendered = str(
            wiki_link(
                "https://zh.wikipedia.org/wiki/Test",
                '<script>"&',
                title='"data',
            ),
        )
        assert rendered == (
            '<a href="https://zh.wikipedia.org/wiki/Test" '
            'title="&#34;data" aria-label="&#34;data">'
            "&lt;script&gt;&#34;&amp;</a>"
        )
        for value in (
            "javascript:alert(1)",
            "https://evil.example/wiki/a",
            "//zh.wikipedia.org/wiki/a",
            "https://name:secret@zh.wikipedia.org/wiki/a",
            "https://zh.wikipedia.org:443/wiki/a",
            "https://zh.wikipedia.org\\@evil.example/wiki/a",
            "https://zh.wikipedia.org/other",
            None,
        ):
            assert wiki_url(value) is None
            assert str(wiki_link(value, "<unsafe>")) == "&lt;unsafe&gt;"
        assert (
            wiki_url(self.data["article"]["url"])
            == self.data["article"]["url"]
        )
        assert title_url("A & B") == "https://zh.wikipedia.org/wiki/A_%26_B"
        assert title_url("A", "enwiki") == "https://en.wikipedia.org/wiki/A"
        assert title_url("A", "unknown") is None
        assert local_url("/#backlog", q="a&b") == "/?q=a%26b#backlog"

    def test_dates_and_durations(self) -> None:
        """Retain UTC, measured precision, and unknown observations."""
        assert utc_time("2026-10-18T12:00:00Z") == "2026-10-18 12:00 UTC"
        assert utc_time(None) == "—"
        assert timestamp("2026-02-30T12:00:00Z") is None
        assert timestamp("2026-10-18T12:00:00") is None
        assert self.en.date("2020-03", month_only=True) == "March 2020"
        assert Interface("zh-hant").date("2026-10-18") == "2026年10月18日"
        assert duration(0.25, self.en) == "0.25 s"
        assert duration(59.999, self.en) == "1 min 00.00 s"
        assert duration(65.1, self.en) == "1 min 05.10 s"
        for seconds, expected in (
            (0, "0.00 s"),
            (0.004, "0.00 s"),
            (0.44, "0.44 s"),
            (1, "1.00 s"),
            (5.3, "5.30 s"),
            (32.89, "32.89 s"),
            (32.49, "32.49 s"),
            (59.994, "59.99 s"),
            (60, "1 min 00.00 s"),
            (725.3, "12 min 05.30 s"),
            (3600, "60 min 00.00 s"),
        ):
            for locale in ("en", "zh-hans", "zh-hant"):
                localized = (
                    expected
                    if locale == "en"
                    else expected.replace(" min ", " 分 ").replace(" s", " 秒")
                )
                assert duration(seconds, Interface(locale)) == localized
        for seconds, expected in (
            (0.44, "0 s…"),
            (32.89, "32 s…"),
            (59.999, "59 s…"),
            (60, "1 min 00 s…"),
            (725.3, "12 min 05 s…"),
        ):
            assert duration(seconds, self.en, running=True) == expected
        assert duration(None, self.en) == "—"
        assert (
            event_duration(
                {"status": "running", "started_at": "2026-10-18T11:54:15Z"},
                self.en,
                self.now,
            )
            == "6 min 15 s…"
        )
        assert (
            event_duration(
                {"status": "running", "started_at": "2026-10-18T13:00:00Z"},
                self.en,
                self.now,
            )
            == "0 s…"
        )
        assert readable_size(1024) == "1.0 KiB"
        assert readable_size(10) == "10 B"

    def test_relative_times_use_whole_minutes_hours_or_days(self) -> None:
        """Keep directions, unit boundaries, and timezones exact."""
        for seconds, expected in (
            (0, "(just now)"),
            (-59, "(just now)"),
            (59, "(within 1 min)"),
            (-60, "(1 min ago)"),
            (60, "(in 1 min)"),
            (-3599, "(59 min ago)"),
            (3599, "(in 59 min)"),
            (-3600, "(1 hr ago)"),
            (3600, "(in 1 hr)"),
            (-7199, "(1 hr ago)"),
            (7199, "(in 1 hr)"),
            (-86399, "(23 hr ago)"),
            (86399, "(in 23 hr)"),
            (-86400, "(1 d ago)"),
            (86400, "(in 1 d)"),
            (-172799, "(1 d ago)"),
            (172800, "(in 2 d)"),
        ):
            with self.subTest(seconds=seconds):
                value = (self.now + dt.timedelta(seconds=seconds)).isoformat()
                assert relative_time(value, self.en, self.now) == expected
        assert (
            relative_time(
                "2026-10-18T20:01:30+08:00",
                self.en,
                self.now,
            )
            == "(in 1 min)"
        )
        for invalid in (None, "", "invalid", "2026-10-18T12:00:00"):
            assert not relative_time(invalid, self.en, self.now)
        for locale, past, future, future_days in (
            ("zh", "13分钟前", "3小时后", "2天后"),
            ("zh-hans", "13分钟前", "3小时后", "2天后"),
            ("zh-hant", "13分鐘前", "3小時後", "2天後"),
        ):
            interface = Interface(locale)
            assert (
                relative_time(
                    (self.now - dt.timedelta(minutes=13)).isoformat(),
                    interface,
                    self.now,
                )
                == f"\uff08{past}\uff09"
            )
            assert (
                relative_time(
                    (self.now + dt.timedelta(hours=3)).isoformat(),
                    interface,
                    self.now,
                )
                == f"\uff08{future}\uff09"
            )
            assert (
                relative_time(
                    (self.now + dt.timedelta(days=2)).isoformat(),
                    interface,
                    self.now,
                )
                == f"\uff08{future_days}\uff09"
            )

    def test_assessment_aliases(self) -> None:
        """Rank equivalent grades together and leave unknowns last."""
        levels = (
            ("FA", "FL", "典范", "典範", "特色列表"),
            ("A", "AL", "甲", "甲级列表", "甲級列表"),
            ("GA", "优良", "優良"),
            ("Bplus", "乙上"),
            ("B", "BL", "乙", "乙级列表"),
            ("C", "CL", "丙", "丙級列表"),
            ("D", "丁"),
            ("Start", "List", "初", "列表"),
            ("Stub", "SL", "StubList", "小作品", "小列表"),
            ("Substub", "小小作品"),
        )
        for index, values in enumerate(levels):
            for value in values:
                assert assessment_rank(value, "class") == 10 - index
        for value in (
            None,
            "",
            "Unassessed",
            "未评级",
            "Disambig",
            "NA",
            "不适用",
            "custom",
        ):
            assert assessment_rank(value, "class") is None
            assert assessment_rank(value, "importance") is None
        assert assessment_code(" ga ", "class") == "GA"
        assert self.en.label({"zh-hant": "來源"}, "") == "來源"
        assert self.en.label(None, "Fallback") == "Fallback"

    @staticmethod
    def test_maintenance_sorting() -> None:
        """Keep nulls last, stable ties, and seeded reversal."""
        items = [
            {"page_id": 4, "title": "D", "oldest_issue_month": None},
            {"page_id": 1, "title": "B", "oldest_issue_month": "2020-01"},
            {"page_id": 3, "title": "A", "oldest_issue_month": "2018-05"},
            {"page_id": 2, "title": "A", "oldest_issue_month": "2018-05"},
            {"page_id": 5, "title": "AA"},
        ]
        for sort, order, expected in (
            ("oldest", "asc", [2, 3, 1, 5, 4]),
            ("oldest", "desc", [1, 2, 3, 5, 4]),
            ("title", "asc", [2, 3, 5, 1, 4]),
            ("title", "desc", [4, 1, 5, 2, 3]),
        ):
            assert [
                row["page_id"] for row in maintenance_items(items, sort, order)
            ] == expected
        random = maintenance_items(items, "random", "asc", "0123456789abcdef")
        assert random == maintenance_items(
            list(reversed(items)),
            "random",
            "asc",
            "0123456789abcdef",
        )
        assert list(reversed(random)) == maintenance_items(
            items,
            "random",
            "desc",
            "0123456789abcdef",
        )
        assert random != maintenance_items(
            items,
            "random",
            "asc",
            "fedcba9876543210",
        )
        assert [item["page_id"] for item in items] == [4, 1, 3, 2, 5]
        last_page_id = 101
        many = [
            {
                "page_id": index,
                "title": f"{index:03}",
                "assessment_class": "FA" if index == last_page_id else "Stub",
            }
            for index in range(1, 102)
        ]
        ordered = maintenance_items(many, "class", "desc")
        assert maintenance_page(ordered)["items"][0]["page_id"] == 101
        assert maintenance_page(ordered, "2")["items"][0]["page_id"] == 50
        assert maintenance_page(ordered, "9" * 1000)["page"] == 3
        assert maintenance_page([])["pages"] == 1
        assert list(
            maintenance_sections([
                {"section_key": "a"},
                {"section_key": "b"},
                {"section_key": "a"},
            ]),
        ) == ["a", "b"]

    def test_maintenance_queries_and_rendering(self) -> None:
        """Reject bad controls and retain categories and seeds."""
        for query in (
            "",
            "task=../escape",
            "task=references&page=0",
            "task=references&sort=invalid",
            "task=references&order=invalid",
            "task=references&seed=abc",
            "task=references&task=other",
            "task=references&extra=1",
        ):
            with self.subTest(query=query), self.assertRaises(ValueError):
                maintenance_selection(parse_qs(query))
        selection = parse_qs(
            "task=references&sort=random&seed=0123456789abcdef",
        )
        output = render_maintenance(self.data["snapshot"], selection, "en")
        assert "March 2020" in output
        assert "Category%3A%E8%87%AA2020" in output
        assert "Maintenance &lt;&amp;&gt; article" in output
        assert "seed=0123456789abcdef" in output
        assert "order=desc" in output
        with self.assertRaises(LookupError):
            render_maintenance(
                self.data["snapshot"],
                {"task": ["missing"]},
                "en",
            )
        with self.assertRaises(RuntimeError):
            render_maintenance(None, {"task": ["references"]}, "en")

    def test_highlight_semantics(self) -> None:
        """Keep dates, scripts, and unavailable sections distinct."""
        assert merged_items({}, ("featured", "good"))["items"] is None
        self.data["snapshot"]["pageviews"]["items"] = [
            {
                **self.data["article"],
                "title": f"Popular game {index}",
                "rank": index,
            }
            for index in range(1, 14)
        ]
        sections = {
            row["key"]: row["items"]
            for row in homepage_sections(self.data["snapshot"])
        }
        assert sections["recognized"][0]["title"] == "Scheduled game"
        assert len(sections["pageviews"]) == 6
        assert len(sections["dyk"]) == 6
        assert sections["pageviews"][-1]["title"] == "Popular game 6"
        assert sections["dyk"][-1]["title"] == "DYK game 6"
        assert len(self.data["snapshot"]["pageviews"]["items"]) == 13
        assert len(sections["news_history"]) == 8
        article = self.data["article"]
        assert article_extract(article, "zh")[1] == article["extract_zh"]
        assert article_extract({"extract": "simplified"}, "zh")[1] is None
        assert (
            article_extract(article, "zh-hant")[1] == article["extract_hant"]
        )
        assert article_extract(article, "en")[1] == article["extract"]
        query = parse_qs(urlsplit(pageviews_url(article, "2026-10-17")).query)
        assert query["start"] == ["2026-09-18"]
        assert query["end"] == ["2026-10-17"]
        assert query["pages"] == [article["title"]]

    def test_actual_scheduling_and_uptime(self) -> None:
        """Suppress stale data and retain actual execution times."""
        schedule = self.data["status"]["schedule"]
        definitions = self.data["config"]["routines"]["routines"]
        selected = schedule_state(schedule, definitions, self.now)
        assert (
            next_run(selected, "pageviews", self.en) == "2026-10-20 18:59 UTC"
        )
        assert "uptime: 2d 3h 4m" in uptime(
            selected,
            "0.6.0",
            self.en,
            self.now,
        )
        for change in (
            {"updated_at": "2026-10-18T11:58:29Z"},
            {"updated_at": "2026-10-18T12:01:00Z"},
            {"state": "stopped"},
            {"state": "missing"},
            {"started_at": None},
        ):
            altered = schedule_state(
                {**schedule, **change},
                definitions,
                self.now,
            )
            assert "unavailable" in uptime(altered, "0.6.0", self.en, self.now)
        paused = schedule_state(
            {**schedule, "state": "paused"},
            definitions,
            self.now,
        )
        assert next_run(paused, "pageviews", self.en) == "Scheduler paused"
        assert "2d 3h 4m" in uptime(paused, "0.6.0", self.en, self.now)
        changed = copy.deepcopy(definitions)
        changed["pageviews"]["minute"] = 58
        assert (
            next_run(
                schedule_state(schedule, changed, self.now),
                "pageviews",
                self.en,
            )
            == "Schedule unavailable"
        )

    def test_python_configuration_matches_json_schedule(self) -> None:
        """Keep times when source tuples become JSON prefix arrays."""
        definitions = routine_configuration()["routines"]
        rows = json.loads(json.dumps(definitions))
        for row in rows.values():
            row.update(state="scheduled", next_run_at="2026-10-20T18:59:00Z")
        schedule = {**self.data["status"]["schedule"], "routines": rows}
        selected = schedule_state(schedule, definitions, self.now)
        assert set(selected["routines"]) == set(definitions)
        for key in definitions:
            assert next_run(selected, key, self.en) == "2026-10-20 18:59 UTC"

    def test_all_localized_dashboards(self) -> None:
        """Build escaped pages with native accessible status popups."""
        for locale, deferred, extract in (
            ("en", "Waiting to retry", "简介"),
            ("zh-hans", "待重试", "简介"),
            ("zh-hant", "待重試", "簡介"),
        ):
            output = render_dashboard(
                self.data["snapshot"],
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            )
            assert deferred in output
            assert (
                f"{extract} &lt;script&gt;never execute&lt;/script&gt;"
                " &amp; 中文" in output
            )
            assert output.count('<script src="/live.js" defer></script>') == 1
            assert "DYK game 6" in output
            assert "DYK game 7" not in output
            assert "News game 7" in output
            assert output.count('class="article-extract"') == 18
            assert output.count('class="extract-attribution"') == 4
            assert output.count('scope="col"') == 5
            assert 'class="assessment-guide"' in output
            assert 'commandfor="report-details-pageviews"' in output
            assert '<dialog id="report-details-pageviews"' in output
            assert (
                'aria-labelledby="report-details-pageviews-heading"' in output
            )
            assert 'command="close" autofocus' in output
            assert '<span aria-hidden="true">⏸️</span>' not in output
            assert 'class="routine-status" data-status=' in output
            task = re.search(
                r'<a href="/maintenance/\?task=references&amp;page=1" '
                r'class="backlog-task">(.*?)</a>',
                output,
            )
            assert task is not None
            label = Interface(locale).label(
                self.data["snapshot"]["backlog"]["groups"][0]["label"],
                "",
            )
            count = Interface(locale).text("backlog.count", count=1)
            assert task[1] == (
                f'<span class="backlog-label">{label}</span>'
                f'<span class="backlog-count">{count}</span>'
            )
            assert "5.000" not in output
            assert "&lt;Latest &amp; safe&gt;" in output
            assert "2026-10-20 18:59 UTC" in output
            assert "https://creativecommons.org/licenses/by-sa/4.0/" in output
        generic = render_dashboard(
            self.data["snapshot"],
            self.data["status"],
            self.data["config"],
            "zh",
            self.now,
        )
        assert 'lang="zh">原文遊戲与音乐' in generic
        missing = render_dashboard(None, None, None, "en", self.now)
        assert "Project data is being prepared" in missing
        assert "temporarily unavailable" in missing

    def test_status_line_labels_and_independent_timing(self) -> None:
        """Show finish times and retain durations in details."""
        for locale, finished, next_label, report_duration in (
            ("en", "Finished at ", "Next run scheduled for ", "5.00 s"),
            (
                "zh-hans",
                "执行完毕于",
                "下次执行预定于",
                "5.00 秒",
            ),
            (
                "zh-hant",
                "執行完畢於",
                "下次執行預定於",
                "5.00 秒",
            ),
        ):
            output = render_dashboard(
                self.data["snapshot"],
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            )
            line = re.search(
                r'<li class="routine-status"[^>]*>(.*?)</li>',
                output,
            )
            assert line is not None
            assert line[1].endswith("</button>")
            assert "data-clock" not in line[1]
            assert "(" not in line[1]
            assert "\uff08" not in line[1]
            assert '<button type="button" class="status-link"' in line[1]
            assert "<div" not in line[1]
            assert "<br" not in line[1]
            assert finished in output
            assert next_label in output
            assert f">{report_duration}</td>" in output
            assert "5.000" not in output
        self.data["status"]["reports"] = None
        output = render_dashboard(
            self.data["snapshot"],
            self.data["status"],
            self.data["config"],
            "en",
            self.now,
        )
        assert '<dialog id="report-details-pageviews"' in output
        assert '<p class="empty">No record</p>' in output

    def test_queued_reports_wait_until_individual_execution_starts(
        self,
    ) -> None:
        """Hide registration clocks until each report actually runs."""
        status = self.data["status"]
        report = status["reports"]["reports"]["first"]
        routine = status["routines"]["routines"]["pageviews"]
        routine.update(
            status="running",
            started_at=self.now.isoformat(),
            finished_at=None,
            duration_seconds=None,
        )
        for locale, waiting, running, completed in (
            ("en", "Waiting to run…", "2 s…", "5.00 s"),
            (
                "zh-hans",
                "等待执行…",
                "2 秒…",
                "5.00 秒",
            ),
            (
                "zh-hant",
                "等待執行…",
                "2 秒…",
                "5.00 秒",
            ),
        ):
            report.update(
                status="queued",
                started_at=self.now.isoformat(),
                finished_at=None,
                duration_seconds=None,
                confirmed_edits=None,
            )
            later = self.now + dt.timedelta(seconds=30)
            for now in (self.now, later):
                output = render_live_status(
                    status,
                    self.data["config"],
                    locale,
                    now,
                )
                row = re.search(
                    r'<tr data-key="[^"]*"[^>]*>(.*?)</tr>',
                    output,
                )
                assert row is not None
                assert row[1].count(waiting) == 2
                assert "<time" not in row[1]
            assert 'data-clock="elapsed"' not in output
            page = render_dashboard(
                self.data["snapshot"],
                status,
                self.data["config"],
                locale,
                later,
            )
            assert row[0] in page
            report.update(status="running", started_at=later.isoformat())
            output = render_live_status(
                status,
                self.data["config"],
                locale,
                later + dt.timedelta(seconds=2),
            )
            assert waiting not in output
            assert f">{running}</td>" in output
            assert 'data-clock="elapsed"' in output
            assert "2026-10-18 12:01:00 UTC" in output
            report.update(
                status="updated",
                finished_at=(later + dt.timedelta(seconds=5)).isoformat(),
                duration_seconds=5,
                confirmed_edits=1,
            )
            output = render_live_status(
                status,
                self.data["config"],
                locale,
                later + dt.timedelta(seconds=30),
            )
            assert f">{completed}</td>" in output

    def test_live_fragments_advance_timers_and_expire_schedules(self) -> None:
        """Refresh clocks and observed states with the server's time."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event.update(
            status="running",
            started_at=self.now.isoformat(),
            finished_at=None,
            duration_seconds=None,
        )
        status["reports"]["reports"]["first"].update(
            status="running",
            started_at=self.now.isoformat(),
            finished_at=None,
            duration_seconds=None,
        )
        for locale, elapsed in (
            ("en", "1 s…"),
            ("zh-hans", "1 秒…"),
            ("zh-hant", "1 秒…"),
        ):
            now = self.now + dt.timedelta(seconds=1)
            live = render_live_status(status, self.data["config"], locale, now)
            assert f">{elapsed}</td>" in live
            assert "&lt;Latest &amp; safe&gt;" in live
            assert 'id="tool-status"' in live
            assert 'id="live-uptime"' in live
            assert f'data-server-time="{now.isoformat()}"' in live
            assert 'data-schedule-expires="2026-10-18T12:02:00+00:00"' in live
            assert 'data-clock="uptime"' in live
            assert "<script" not in live
            assert "<html" not in live
        fresh = render_live_status(
            status,
            self.data["config"],
            "en",
            self.now + dt.timedelta(seconds=1),
        )
        assert "2d 3h 4m 31s" in fresh
        stale = render_live_status(
            status,
            self.data["config"],
            "en",
            self.now + dt.timedelta(seconds=121),
        )
        assert "uptime: unavailable" in stale
        assert "2026-10-20 18:59 UTC" not in stale
        assert "data-schedule-expires" not in stale
        assert 'data-clock="uptime"' not in stale

    def test_only_active_observations_receive_timer_anchors(self) -> None:
        """Keep completed and queued times fixed between syncs."""
        status = self.data["status"]
        report = status["reports"]["reports"]["first"]
        for state in ("updated", "queued", "running"):
            report["status"] = state
            output = render_live_status(
                status,
                self.data["config"],
                "en",
                self.now,
            )
            assert ('data-clock="elapsed"' in output) == (state == "running")
            assert "Status syncs automatically" not in output
        report["started_at"] = None
        output = render_live_status(
            status,
            self.data["config"],
            "en",
            self.now,
        )
        assert 'data-clock="elapsed"' not in output

    def test_routine_time_annotations_preserve_absolute_observations(
        self,
    ) -> None:
        """Append small labels only to observed timestamps."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["finished_at"] = (
            self.now - dt.timedelta(hours=3, minutes=35)
        ).isoformat()
        schedule = status["schedule"]
        schedule["routines"]["pageviews"]["next_run_at"] = (
            self.now + dt.timedelta(minutes=23, seconds=40)
        ).isoformat()
        output = render_dashboard(
            self.data["snapshot"],
            status,
            self.data["config"],
            "zh-hans",
            self.now,
        )
        assert (
            '2026-10-18 08:25 UTC</time><small class="relative-time" '
            'data-clock="relative" '
            'data-observed-at="2026-10-18T08:25:30+00:00">'
            "\uff083小时前\uff09</small>" in output
        )
        assert (
            '2026-10-18 12:24 UTC<small class="relative-time" '
            'data-clock="relative" '
            'data-observed-at="2026-10-18T12:24:10+00:00">'
            "\uff0823分钟后\uff09</small>" in output
        )
        assert output.count('class="relative-time"') == 2
        status["routines"] = None
        for change in (
            {"state": "paused"},
            {"state": "stopped"},
            {"updated_at": "2026-10-18T11:00:00Z"},
        ):
            status["schedule"] = {**schedule, **change}
            output = render_dashboard(
                self.data["snapshot"],
                status,
                self.data["config"],
                "zh-hans",
                self.now,
            )
            assert 'class="relative-time"' not in output

    def test_confirmed_empty_sections_are_omitted(self) -> None:
        """Omit empty headings and retain unavailable observations."""
        empty = self.data["snapshot"]
        empty["homepage"] = {key: [] for key in empty["homepage"]}
        empty["pageviews"]["items"] = []
        empty["backlog"]["total"] = 0
        output = render_dashboard(empty, None, None, "en", self.now)
        assert 'id="homepage"' not in output
        assert 'id="backlog"' not in output
        empty["homepage"]["news"] = None
        assert 'id="homepage-news_history"' in render_dashboard(
            empty,
            None,
            None,
            "en",
            self.now,
        )

    def test_about_logs_and_errors(self) -> None:
        """Share navigation and escape log controls in every locale."""
        for locale in ("en", "zh-hans", "zh-hant"):
            assert "0.6.0.dev1" in render_about(self.data["config"], locale)
            assert 'role="alert"' in render_error("backlog.not_found", locale)
            output = render_logs(
                {
                    "files": [
                        {
                            "date": "2026-10-18",
                            "name": "aranami.2026-10-18.log",
                            "size": 1024,
                        },
                    ],
                    "date": "2026-10-18",
                    "query": '"><script>bad</script>',
                    "events": [],
                    "truncated": True,
                },
                locale,
            )
            assert output.count('<script src="/live.js" defer>') == 1
            assert "<script>bad</script>" not in output
            assert "&lt;script&gt;bad&lt;/script&gt;" in output
            assert "<pre" not in output
            assert "1.0 KiB" in output
            assert "/logs/?date=2026-10-18&amp;download=1" in output
            assert 'class="empty"' in render_logs({"files": []}, locale)

    @staticmethod
    def test_interface_assets_are_self_contained() -> None:
        """Ship local assets without a remote browser dependency."""
        root = Path(__file__).resolve().parents[2] / "src" / "webservice"
        for output in (
            render_dashboard(None, None, None, "en"),
            render_logs({"files": []}, "en"),
            render_about(None, "en"),
        ):
            assets = re.findall(
                r'<(?:link|script)\b[^>]*(?:href|src)="([^"]+)"',
                output,
            )
            assert "/styles.css" in assets
            assert all(asset.startswith("/") for asset in assets)
            assert all(
                (root / "public_html" / asset.lstrip("/")).is_file()
                for asset in assets
            )
        assert not list(root.rglob("*.php"))
        assert list(root.rglob("*.js")) == [root / "public_html" / "live.js"]
        assert not list(root.rglob("*.mjs"))


class LiveVisibilityTests(unittest.TestCase):
    """Keep routine schedules and connection hints relevant to state."""

    def setUp(self) -> None:
        """Create saved observations and a deterministic clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)

    def _outputs(self, locale: str) -> tuple[str, str]:
        """Render full and live pages from the same saved observations.

        Args:
            locale: Interface language for both renderers.

        Returns:
            Complete dashboard followed by its live status fragments.
        """
        return (
            render_dashboard(
                self.data["snapshot"],
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            ),
            render_live_status(
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            ),
        )

    @staticmethod
    def _card(output: str, routine: str = "pageviews") -> str:
        """Select one routine card without matching report-popup states.

        Args:
            output: Rendered dashboard or live status fragments.
            routine: Stable routine identifier of the expected card.

        Returns:
            Contents of the selected routine card.
        """
        card = re.search(
            rf'<article[^>]*id="routine-{re.escape(routine)}"[^>]*>'
            r"(.*?)</article>",
            output,
        )
        assert card is not None
        return card[1]

    def test_completed_daily_reports_show_their_confirmed_date(self) -> None:
        """Use dated success labels after verified daily work."""
        status = self.data["status"]
        config = self.data["config"]["routines"]["routines"]
        original = status["routines"]["routines"]["pageviews"]
        for routine in ("pageviews", "new_pages"):
            config[routine] = config["pageviews"]
            status["routines"]["routines"][routine] = original
            for outcome in ("updated", "no_update"):
                original.update(status=outcome, report_date="2026-10-17")
                for locale, label in (
                    ("en", "Updated report for October 17, 2026"),
                    ("zh-hans", "已更新2026年10月17日报告"),
                    ("zh-hant", "已更新2026年10月17日報告"),
                ):
                    with self.subTest(
                        routine=routine,
                        outcome=outcome,
                        locale=locale,
                    ):
                        for output in self._outputs(locale):
                            card = self._card(output, routine)
                            assert label in card
                            assert 'data-status="updated"' in card

    def test_contents_links_only_visible_dashboard_sections(self) -> None:
        """Keep contents links valid when navigation moves."""
        for locale in ("en", "zh-hans", "zh-hant"):
            output = self._outputs(locale)[0]
            header = output.split("</header>")[0]
            contents = re.search(
                r'<nav class="page-toc"[^>]*>(.*?)</nav>',
                output,
                re.DOTALL,
            )
            assert contents is not None
            contents = contents[1]
            assert 'href="/"' not in contents
            assert 'class="brand"' in header
            for anchor in (
                "#homepage-pageviews",
                "#homepage-recognized",
                "#homepage-dyk",
                "#homepage-news_history",
                "#backlog",
                "#tool-status",
            ):
                assert f'href="{anchor}"' in contents
                assert f'id="{anchor[1:]}"' in output
        snapshot = self.data["snapshot"]
        snapshot["pageviews"]["items"] = []
        snapshot["backlog"]["total"] = 0
        output = self._outputs("en")[0]
        contents = re.search(
            r'<nav class="page-toc"[^>]*>(.*?)</nav>',
            output,
            re.DOTALL,
        )
        assert contents is not None
        assert 'href="#homepage-pageviews"' not in contents[1]
        assert 'href="#backlog"' not in contents[1]

    def test_about_monitor_uses_saved_environment_and_fresh_usage(
        self,
    ) -> None:
        """Refresh saved resource facts and hide stale measurements."""
        status = self.data["status"]
        status["schedule"]["runtime"] = {
            "python": "3.15.0",
            "system": "Linux",
            "machine": "x86_64",
            "memory_bytes": 104_857_600,
            "memory_limit_bytes": 4_294_967_296,
            "cpu_percent": 12.5,
            "cpu_limit": 2,
        }
        for locale, memory, cpu, started in (
            ("en", "100.0 MiB / 4.0 GiB", "12.5% (2 cores)", "started at "),
            (
                "zh-hans",
                "100.0 MiB / 4.0 GiB",
                "12.5%\uff082核心\uff09",
                "启动时间",
            ),
            (
                "zh-hant",
                "100.0 MiB / 4.0 GiB",
                "12.5%\uff082核心\uff09",
                "啟動時間",
            ),
        ):
            initial = render_about(
                self.data["config"],
                locale,
                status=status,
                now=self.now,
            )
            live = self._outputs(locale)[1]
            for output in (initial, live):
                assert 'id="server-runtime"' in output
                assert "3.15.0" in output
                assert "Linux · x86_64" in output
                assert memory in output
                assert cpu in output
                assert started in output
                assert "2026-10-16 08:56:00 UTC" in output
                assert "data-relative-inline>2" in output
                assert output.count('id="server-runtime"') == 1
                metrics = re.search(
                    r'<table class="server-metrics">(.*?)</table>',
                    output,
                )
                assert metrics is not None
                assert metrics[1].count("<tr>") == 5
            assert 'src="/live.js"' in initial
            assert 'id="live-server-status"' in initial
        stale = render_about(
            self.data["config"],
            "en",
            status=status,
            now=self.now + dt.timedelta(minutes=3),
        )
        assert "12.5%" not in stale
        assert "100.0 MiB" not in stale
        assert "3.15.0" in stale
        assert "Linux · x86_64" in stale
        assert "2026-10-16 08:56:00 UTC" in stale
        assert "2026-10-18 12:00:00 UTC" in stale
        assert "data-schedule-time" not in stale

    def test_unconfirmed_daily_dates_keep_the_actual_failure_label(
        self,
    ) -> None:
        """Never show a success date for failed or deferred work."""
        event = self.data["status"]["routines"]["routines"]["pageviews"]
        for outcome, label in (
            ("failed", "Failed"),
            ("deferred", "Waiting to retry"),
            ("unknown", "Result unavailable"),
        ):
            event.update(status=outcome, report_date="2026-10-17")
            for output in self._outputs("en"):
                card = self._card(output)
                assert label in card
                assert "Updated report for" not in card
        event.update(status="updated", report_date=None)
        for output in self._outputs("en"):
            card = self._card(output)
            assert "Updated report for" not in card
            assert ">Updated</button>" in card

    def test_unknown_failure_identifiers_never_expose_diagnostic_text(
        self,
    ) -> None:
        """Use localized fallbacks for unknown private details."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        report = status["reports"]["reports"]["first"]
        for record in (event, report):
            record.update(
                status="failed",
                failure_date="2026-10-19",
                failure_reason="TimeoutError <script>unsafe</script> & retry",
            )
        event["report_date"] = "2026-10-17"
        for locale, label in (
            (
                "en",
                (
                    "Record update for October 19, 2026 failed: "
                    "The update could not be completed; "
                    "see run logs for details"
                ),
            ),
            (
                "zh-hans",
                "2026年10月19日记录更新失败\uff1a无法完成更新\uff0c具体原因请查看运行日志",
            ),
            (
                "zh-hant",
                "2026年10月19日紀錄更新失敗\uff1a無法完成更新\uff0c個別原因請查看執行日誌",
            ),
        ):
            with self.subTest(locale=locale):
                for output in self._outputs(locale):
                    card = self._card(output)
                    assert card.count(label) == 2
                    assert 'data-status="failed"' in card
                    assert "<script>unsafe</script>" not in card
                    assert "TimeoutError" not in card
                    assert "Updated report for" not in card
                    assert "2026年10月17日" not in card

    def test_failed_report_details_preserve_unknown_dates_and_reasons(
        self,
    ) -> None:
        """Keep unknown failure dates and reasons unknown."""
        event = self.data["status"]["routines"]["routines"]["pageviews"]
        event.update(status="failed", report_date="2026-10-17")
        for fields, label in (
            (
                {"failure_reason": "TimeoutError"},
                "Failed: The request timed out",
            ),
            (
                {"failure_date": "2026-10-19"},
                "Record update for October 19, 2026 failed",
            ),
            ({}, "Failed"),
        ):
            with self.subTest(fields=fields):
                event.pop("failure_date", None)
                event.pop("failure_reason", None)
                event.update(fields)
                for output in self._outputs("en"):
                    card = self._card(output)
                    assert f">{label}</button>" in card
                    assert "October 17, 2026" not in card
                    assert "October 18, 2026" not in card
        event.update(
            status="updated",
            report_date=None,
            failure_date="2026-10-19",
            failure_reason="TimeoutError",
        )
        for output in self._outputs("en"):
            card = self._card(output)
            assert ">Updated</button>" in card
            assert "TimeoutError" not in card

    def test_http_causes_are_human_readable_in_every_status_view(self) -> None:
        """Localize service outages in initial and live responses."""
        status = self.data["status"]
        for record in (
            status["routines"]["routines"]["pageviews"],
            status["reports"]["reports"]["first"],
        ):
            record.update(status="failed", failure_reason="HTTP503")
        for locale, reason in (
            (
                "en",
                "The external service is temporarily unavailable (HTTP 503)",
            ),
            ("zh-hans", "外部服务暂时不可用\uff08HTTP 503\uff09"),
            ("zh-hant", "外部服務暫時無法使用\uff08HTTP 503\uff09"),
        ):
            for output in self._outputs(locale):
                card = self._card(output)
                assert card.count(reason) == 2
                assert "HTTP503" not in card

    def test_legacy_exception_groups_have_a_readable_fallback(self) -> None:
        """Never present a Python exception-group name to readers."""
        event = self.data["status"]["routines"]["routines"]["pageviews"]
        event.update(status="failed", failure_reason="ExceptionGroup")
        for locale in ("en", "zh-hans", "zh-hant"):
            for output in self._outputs(locale):
                card = self._card(output)
                assert "ExceptionGroup" not in card
                assert 'data-status="failed"' in card

    def test_running_progress_uses_saved_dates_and_counts(self) -> None:
        """Separate dated status labels from phase and counters."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event.update(
            status="running",
            progress={
                "phase": "pageviews_download",
                "report_date": "2026-10-20",
                "current": 2000,
                "total": 13500,
            },
        )
        for locale, label, note, fallback in (
            (
                "en",
                "Updating pageviews for October 20, 2026",
                "Fetching pageviews (2,000/13,500)",
                "Running",
            ),
            (
                "zh-hans",
                "更新2026年10月20日记录",
                "抓取浏览量\uff082,000/13,500\uff09",
                "更新中",
            ),
            (
                "zh-hant",
                "更新2026年10月20日紀錄",
                "擷取瀏覽量\uff082,000/13,500\uff09",
                "更新中",
            ),
        ):
            for output in self._outputs(locale):
                card = self._card(output)
                button = re.search(
                    r'<button[^>]*class="status-link"[^>]*>(.*?)</button>',
                    card,
                )
                line = re.search(
                    r'<li class="routine-progress context"[^>]*>(.*?)</li>',
                    card,
                )
                assert button is not None
                assert button[1] == label
                assert f'data-progress-label="{label}"' in button[0]
                assert f'data-progress-fallback="{fallback}"' in button[0]
                assert line is not None
                assert line[1] == note
                assert "data-progress-active" in line[0]
                assert "2026" not in line[1]
                assert "2,000/13,500" not in button[1]

    def test_progress_phases_and_unknown_counters_remain_explicit(
        self,
    ) -> None:
        """Preserve fallback phases and distinguish zero from null."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["status"] = "running"
        for phase, expected in (
            ("preparing", "Preparing"),
            ("publishing", "Publishing"),
            ("pageviews_readiness", "Checking daily availability"),
            ("pageviews_aggregate", "Calculating rankings"),
            ("unknown", "Preparing"),
        ):
            event["progress"] = {"phase": phase}
            output = render_live_status(
                status,
                self.data["config"],
                "en",
                self.now,
            )
            assert f"data-progress-active>{expected}</li>" in output
            assert ">Updating pageview records</button>" in output
        for current, total, expected in (
            (None, None, "Fetching pageviews"),
            (None, 13500, "Fetching pageviews"),
            (0, None, "Fetching pageviews"),
            (0, 13500, "Fetching pageviews (0/13,500)"),
            (0, 0, "Fetching pageviews (0/0)"),
        ):
            event["progress"] = {
                "phase": "pageviews_download",
                "current": current,
                "total": total,
            }
            for output in self._outputs("en"):
                assert f"data-progress-active>{expected}</li>" in output
        event.pop("progress")
        assert "data-progress-active>Preparing</li>" in render_live_status(
            status,
            self.data["config"],
            "en",
            self.now,
        )

    def test_undated_running_labels_name_each_task(self) -> None:
        """Localize active task labels independently of phase notes."""
        status = self.data["status"]
        config = self.data["config"]["routines"]
        definition = config["routines"]["pageviews"]
        schedule = status["schedule"]["routines"]["pageviews"]
        event = status["routines"]["routines"]["pageviews"]
        for routine, labels in (
            (
                "dyks",
                (
                    "Updating DYK recommendations",
                    "更新新条目推荐",
                    "更新新條目推薦",
                ),
            ),
            (
                "new_pages",
                (
                    "Updating new-page records",
                    "更新新进条目记录",
                    "更新新進條目紀錄",
                ),
            ),
            (
                "assessment_lists",
                (
                    "Updating assessment lists",
                    "更新条目评级列表",
                    "更新條目評級列表",
                ),
            ),
            (
                "assessment_log",
                (
                    "Updating assessment change log",
                    "更新评级变更日志",
                    "更新評級變更日誌",
                ),
            ),
            (
                "pageviews",
                ("Updating pageview records", "更新浏览记录", "更新瀏覽紀錄"),
            ),
            (
                "enwp_key_articles",
                (
                    "Updating English Wikipedia comparisons",
                    "更新英文维基比较报告",
                    "更新英文維基比較報告",
                ),
            ),
            (
                "pexbot",
                (
                    "Updating database reports",
                    "更新数据库报告",
                    "更新資料庫報告",
                ),
            ),
        ):
            config["routines"] = {routine: definition}
            status["schedule"]["routines"] = {routine: schedule}
            status["routines"]["routines"] = {
                routine: {**event, "routine": routine, "status": "running"},
            }
            for locale, label in zip(
                ("en", "zh-hans", "zh-hant"),
                labels,
                strict=True,
            ):
                for output in self._outputs(locale):
                    card = self._card(output, routine)
                    assert f">{label}</button>" in card
                    assert f'data-progress-label="{label}"' in card
                    assert "data-progress-active" in card

    def test_dated_other_routines_keep_generic_report_labels(self) -> None:
        """Keep other report dates on buttons, not phase notes."""
        status = self.data["status"]
        config = self.data["config"]["routines"]
        config["routines"] = {"new_pages": config["routines"]["pageviews"]}
        status["schedule"]["routines"] = {
            "new_pages": status["schedule"]["routines"]["pageviews"],
        }
        event = status["routines"]["routines"]["pageviews"]
        status["routines"]["routines"] = {"new_pages": event}
        event.update(
            routine="new_pages",
            status="running",
            progress={"phase": "publishing", "report_date": "2026-10-20"},
        )
        for locale, label, note in (
            ("en", "Updating report for October 20, 2026", "Publishing"),
            ("zh-hans", "更新2026年10月20日报告", "发布中"),
            ("zh-hant", "更新2026年10月20日報告", "發布中"),
        ):
            for output in self._outputs(locale):
                card = self._card(output, "new_pages")
                assert f">{label}</button>" in card
                assert f"data-progress-active>{note}</li>" in card

    def test_progress_requires_fresh_active_observations(self) -> None:
        """Hide progress for completed tasks and stale server state."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["progress"] = {
            "phase": "pageviews_download",
            "report_date": "2026-10-20",
        }
        for state in ("updated", "no_update", "failed", "deferred"):
            event["status"] = state
            for output in self._outputs("en"):
                card = self._card(output)
                assert "data-progress-active" not in card
                assert "data-progress-label" not in card
                assert "Updating pageviews for" not in card
        event["status"] = "running"
        schedule = copy.deepcopy(status["schedule"])
        for change in (
            {"state": "paused"},
            {"state": "stopped"},
            {"updated_at": "2026-10-18T11:00:00Z"},
            None,
        ):
            status["schedule"] = (
                {**schedule, **change} if change is not None else None
            )
            for locale, fallback in (
                ("en", "Running"),
                ("zh-hans", "更新中"),
                ("zh-hant", "更新中"),
            ):
                for output in self._outputs(locale):
                    card = self._card(output)
                    assert "data-progress-active" not in card
                    assert "data-progress-label" not in card
                    assert "data-progress-fallback" not in card
                    assert f">{fallback}</button>" in card

    def test_progress_requires_the_current_monitor_session(self) -> None:
        """Hide old running records after a monitor restart."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["status"] = "running"
        event["progress"] = {
            "phase": "pageviews_download",
            "report_date": "2026-10-20",
        }
        status["schedule"]["started_at"] = "2026-10-18T11:59:48Z"
        for started_at, visible in (
            ("2026-10-18T11:59:47Z", False),
            ("2026-10-18T11:59:48Z", True),
            ("2026-10-18T11:59:49Z", True),
        ):
            event["started_at"] = started_at
            for output in self._outputs("en"):
                card = self._card(output)
                label = (
                    "Updating pageviews for October 20, 2026"
                    if visible
                    else "Running"
                )
                assert f">{label}</button>" in card
                assert ("data-progress-active" in card) is visible
                assert ("data-progress-label" in card) is visible

    def test_logs_use_current_server_observations(self) -> None:
        """Show compact live state independently of log availability."""
        for locale, labels in (
            (
                "en",
                (
                    "Running",
                    "Running; scheduled work is paused",
                    "Stopped",
                    "Unavailable; the last server observation is stale",
                    "Unavailable; no current server observation",
                ),
            ),
            (
                "zh-hans",
                (
                    "运行中",
                    "运行中\uff0c定时任务已暂停",
                    "已停止",
                    "无法确认\uff0c上次服务器状态记录已过期",
                    "无法确认\uff0c尚无服务器状态记录",
                ),
            ),
            (
                "zh-hant",
                (
                    "運行中",
                    "運行中\uff0c排程任務已暫停",
                    "已停止",
                    "無法確認\uff0c上次伺服器狀態紀錄已過期",
                    "無法確認\uff0c尚無伺服器狀態紀錄",
                ),
            ),
        ):
            for state, label in zip(
                ("running", "paused", "stopped", "stale", "missing"),
                labels,
                strict=True,
            ):
                status = copy.deepcopy(self.data["status"])
                if state == "missing":
                    status["schedule"] = None
                elif state == "stale":
                    status["schedule"]["updated_at"] = (
                        self.now - dt.timedelta(seconds=121)
                    ).isoformat()
                else:
                    status["schedule"]["state"] = state
                output = render_logs(
                    {"files": []},
                    locale,
                    status=status,
                    config=self.data["config"],
                    now=self.now,
                )
                line = re.search(
                    r'<span id="server-state"[^>]*>(.*?)</span>',
                    output,
                )
                assert line is not None
                assert line[1] == label
                assert 'id="live-server-status"' in output
                assert 'id="live-uptime"' in output
                assert 'id="tool-status"' not in output
                assert 'class="routine-card"' not in output
                assert 'src="/live.js"' in output
                assert "0.6.0.dev1" in output
                assert ('data-clock="uptime"' in output) == (
                    state in {"running", "paused"}
                )

    def test_running_routines_hide_next_run_until_finished(self) -> None:
        """Show actual timestamps and hide active schedules."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["progress"] = {
            "phase": "pageviews_download",
            "report_date": "2026-10-20",
        }
        event["finished_at"] = (self.now - dt.timedelta(minutes=2)).isoformat()
        event["started_at"] = (self.now - dt.timedelta(minutes=10)).isoformat()
        status["schedule"]["routines"]["pageviews"]["next_run_at"] = (
            self.now + dt.timedelta(minutes=35)
        ).isoformat()
        scheduled = copy.deepcopy(status["schedule"])
        for locale, started, finished, label, ago, ahead in (
            (
                "en",
                "Started at ",
                "Finished at ",
                "Next run scheduled for ",
                "(2 min ago)",
                "(in 35 min)",
            ),
            (
                "zh-hans",
                "执行开始于",
                "执行完毕于",
                "下次执行预定于",
                "\uff082分钟前\uff09",
                "\uff0835分钟后\uff09",
            ),
            (
                "zh-hant",
                "執行開始於",
                "執行完畢於",
                "下次執行預定於",
                "\uff082分鐘前\uff09",
                "\uff0835分鐘後\uff09",
            ),
        ):
            for state in (
                "running",
                "updated",
                "no_update",
                "failed",
                "deferred",
            ):
                event["status"] = state
                live = render_live_status(
                    status,
                    self.data["config"],
                    locale,
                    self.now,
                )
                card = re.search(
                    r'<article[^>]*id="routine-pageviews"[^>]*>(.*?)</article>',
                    live,
                )
                assert card is not None
                metadata = re.search(
                    r'<ul class="routine-meta">(.*?)</ul>',
                    card[1],
                )
                assert metadata is not None
                assert (label in card[1]) == (state != "running")
                if state == "running":
                    assert started in metadata[1]
                    assert finished not in metadata[1]
                    assert event["started_at"] in metadata[1]
                    assert event["finished_at"] not in metadata[1]
                else:
                    assert finished in metadata[1]
                    assert started not in metadata[1]
                    assert event["finished_at"] in metadata[1]
                    assert event["started_at"] not in metadata[1]
                    assert ago in metadata[1]
                    assert ahead in metadata[1]
                assert status["schedule"] == scheduled

    def test_connection_notice_only_describes_failures(self) -> None:
        """Hide the failure notice until synchronization fails."""
        for locale, removed in (
            ("en", "Timers advance every second."),
            ("zh-hans", "计时每秒更新"),
            ("zh-hant", "計時每秒更新"),
        ):
            live = render_live_status(
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            )
            notice = re.search(
                r'<p id="live-notice"([^>]*)>([^<]+)</p>',
                live,
            )
            assert notice is not None
            assert "hidden" in notice[1]
            assert 'role="status"' in notice[1]
            assert notice[2] == Interface(locale).text("ops.reconnecting")
            assert removed not in live


class ServerPresentationTests(unittest.TestCase):
    """Verify compact saved-server facts and the authored About page."""

    def setUp(self) -> None:
        """Create saved observations and a fixed UTC clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)

    def _live(self, locale: str) -> str:
        """Render saved status without reading runtime data.

        Returns:
            Localized live fragments at the fixed UTC clock.
        """
        return render_live_status(
            self.data["status"],
            self.data["config"],
            locale,
            self.now,
        )

    def test_about_explains_name_purpose_and_contact(self) -> None:
        """Localize the linked name, explanation, and contact."""
        for locale, purpose, contact in (
            (
                "en",
                "updates reports for Chinese Wikipedia",
                "If you have questions, contact ",
            ),
            (
                "zh-hans",
                "为中文维基百科电子游戏专题更新报告",
                "如果有疑问\uff0c联系",
            ),
            (
                "zh-hant",
                "為中文維基百科電子遊戲專題更新報告",
                "如果有疑問\uff0c聯絡",
            ),
        ):
            output = render_about(self.data["config"], locale)
            assert (
                '<span lang="ja"><a href="https://en.wiktionary.org/wiki/'
                '%E8%8D%92%E6%B3%A2">荒波</a></span>' in output
            )
            assert purpose in output
            assert "Toolforge" in output
            assert (
                contact + '<a href="https://zh.wikipedia.org/wiki/'
                'User:For_Each_..._Next">For Each ... Next</a>' in output
            )
            assert 'href="/#backlog"' in output
            for removed in (
                "discover-heading",
                "improve-heading",
                "reports-heading",
                "Lists refresh automatically",
                "列表会自动刷新",
                "列表會自動重新整理",
            ):
                assert removed not in output
        assert 'href="/#backlog"' in render_logs({"files": []}, "en")

    def test_server_resources_keep_zero_and_unknown_allocation(self) -> None:
        """Combine resources without inventing missing measurements."""
        runtime = {
            "python": "3.15.0",
            "system": "Linux",
            "machine": "x86_64",
            "memory_bytes": 0,
            "memory_limit_bytes": None,
            "cpu_percent": 0,
            "cpu_limit": 1,
        }
        self.data["status"]["schedule"]["runtime"] = runtime
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = self._live(locale)
            assert f"0 B / {interface.text('ops.unavailable')}" in output
            cores = interface.text("server.cpu_core", count=1)
            assert f"0.0%{' ' if locale == 'en' else ''}{cores}" in output
        runtime.update(memory_bytes=None, memory_limit_bytes=1_073_741_824)
        runtime.update(cpu_percent=None, cpu_limit=0.5)
        output = self._live("en")
        unknown = Interface("en").text("ops.unavailable")
        assert f"{unknown} / 1.0 GiB" in output
        assert f"{unknown} (0.5 cores)" in output
        assert "0.0%" not in output
        runtime.update(cpu_percent=0, cpu_limit=None)
        output = self._live("en")
        assert "0.0% (core allocation unavailable)" in output

    def test_server_sample_and_start_times_keep_actual_seconds(self) -> None:
        """Retain saved start/sample times and relative clocks."""
        status = self.data["status"]
        status["schedule"]["started_at"] = "2026-10-18T11:45:07Z"
        status["schedule"]["updated_at"] = "2026-10-18T12:00:02Z"
        for locale, prefix, age, recent in (
            ("en", "Last updated at ", "15 min ago", "(just now)"),
            ("zh-hans", "最近更新于", "15分钟前", "\uff08刚刚\uff09"),
            ("zh-hant", "最近更新於", "15分鐘前", "\uff08剛剛\uff09"),
        ):
            output = self._live(locale)
            assert "2026-10-18 11:45:07 UTC" in output
            assert f"data-relative-inline>{age}</small>" in output
            assert (
                prefix + '<time datetime="2026-10-18T12:00:02Z">'
                "2026-10-18 12:00:02 UTC</time>" in output
            )
            assert f">{recent}</small>" in output


class EventPresentationTests(unittest.TestCase):
    """Show major events as localized prose and safe report links."""

    def setUp(self) -> None:
        """Create configured report targets for each test."""
        self.data = _fixtures()

    @staticmethod
    def _event(kind: str, **facts: object) -> dict[str, object]:
        """Prepare one saved event with a fixed exact UTC timestamp.

        Returns:
            A structured major event without diagnostic text.
        """
        return {
            "schema": 1,
            "time": "2026-10-18T12:00:02Z",
            "kind": kind,
            "dry": False,
            **facts,
        }

    def _render(
        self,
        events: list[dict[str, object]],
        locale: str = "en",
        *,
        query: str = "",
        config: dict[str, object] | None = None,
        page: int = 1,
    ) -> str:
        """Render event prose alongside daily download controls.

        Returns:
            A complete localized logs page using only supplied facts.
        """
        return render_logs(
            {
                "files": [
                    {
                        "date": "2026-10-18",
                        "name": "aranami.2026-10-18.log",
                        "size": 1024,
                    },
                ],
                "date": "2026-10-18",
                "query": query,
                "page": page,
                "events": events,
                "truncated": False,
            },
            locale,
            config=self.data["config"] if config is None else config,
        )

    @staticmethod
    def _paragraphs(output: str) -> list[str]:
        """Read visible text from the fixed event-paragraph markup.

        Returns:
            Unescaped prose paragraphs, retaining linked report titles.
        """
        events = re.search(r'<div class="major-events">(.*?)</div>', output)
        if events is None:
            return []
        return [
            unescape(re.sub(r"<[^>]+>", "", paragraph))
            for paragraph in re.findall(r"<p>(.*?)</p>", events[1])
        ]

    def test_major_event_kinds_use_normal_localized_prose(self) -> None:
        """Describe services, tasks, refreshes, and report runs."""
        events = [
            self._event("service_started", service=service)
            for service in ("hub", "scheduler", "website", "web")
        ]
        events.extend([
            self._event("service_stopping", service="hub"),
            self._event("service_stopped", service="hub"),
            self._event("routine_started", routine="pageviews"),
            self._event(
                "routine_finished",
                routine="pageviews",
                status="updated",
                updated=2,
                duration_seconds=5.3,
            ),
            self._event("website_started"),
            self._event(
                "website_finished",
                status="partial",
                failures=1,
                duration_seconds=0.44,
            ),
            self._event("run_started", tasks=6),
            self._event(
                "run_finished",
                tasks=6,
                failures=1,
                deferred=2,
                duration_seconds=65.1,
            ),
        ])
        events.extend(
            self._event("routine_started", routine=routine)
            for routine in routine_configuration()["routines"]
            if routine != "pageviews"
        )
        events.extend({**event, "dry": True} for event in events.copy())
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = "".join(
                self._render(events, locale, page=number)
                for number in range(1, (len(events) + 24) // 25 + 1)
            )
            listing = re.search(
                r'<div class="major-events">(.*)</div>',
                '<div class="major-events">'
                + "".join(
                    re.findall(
                        r'<div class="major-events">(.*?)</div>',
                        output,
                        re.DOTALL,
                    ),
                )
                + "</div>",
                re.DOTALL,
            )
            assert listing is not None
            assert listing[1].count("<p>") == len(events)
            assert "<ul" not in listing[1]
            assert "<li" not in listing[1]
            assert " · " not in listing[1]
            assert (
                '<time datetime="2026-10-18T12:00:02Z">'
                "2026-10-18 12:00:02 UTC</time>" in listing[1]
            )
            for service in ("hub", "scheduler", "website", "web"):
                assert interface.text(f"logs.service.{service}") in listing[1]
            for routine in routine_configuration()["routines"]:
                assert interface.text(f"routine.{routine}") in listing[1]
            assert interface.text("logs.result.partial") in listing[1]
            assert interface.text("logs.edits", count=2) in listing[1]
            assert interface.text("logs.failure", count=1) in listing[1]
            assert (
                interface.text("duration.seconds", seconds="5.30")
                in listing[1]
            )
            assert (
                interface.text(
                    "duration.minutes",
                    minutes=1,
                    seconds="05.10",
                )
                in listing[1]
            )
            assert "https://zh.wikipedia.org/wiki/" in listing[1]
            assert "<pre" not in output
            assert "<code" not in output
            for identifier in (
                "routine_started",
                "service_started",
                "run_finished",
            ):
                assert identifier not in listing[1]

    def test_results_preserve_unknown_counts_and_preview_semantics(
        self,
    ) -> None:
        """Distinguish unavailable results, zero, and previews."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            for outcome in (
                "updated",
                "no_update",
                "failed",
                "deferred",
                "unconfirmed",
            ):
                output = self._render(
                    [
                        self._event(
                            "routine_finished",
                            routine="pageviews",
                            status=outcome,
                            updated=None,
                            duration_seconds=1,
                        ),
                    ],
                    locale,
                )
                assert interface.text(f"logs.result.{outcome}") in output
                assert interface.text("logs.edits", count=0) not in output
            output = self._render(
                [
                    self._event(
                        "routine_finished",
                        routine="pageviews",
                        status="no_update",
                        updated=0,
                        duration_seconds=0,
                    ),
                ],
                locale,
            )
            assert interface.text("logs.edits", count=0) in output
            output = self._render(
                [
                    self._event(
                        "routine_finished",
                        routine="pageviews",
                        status="updated",
                        updated=0,
                        duration_seconds=0,
                        dry=True,
                    ),
                ],
                locale,
            )
            assert interface.text("logs.edits", count=0) not in output
            assert (
                interface.text(
                    "logs.event.preview_routine_finished",
                    routine=interface.text("routine.pageviews"),
                    result=interface.text("logs.result.updated"),
                )
                in output
            )

    def test_search_matches_localized_summaries_and_escaped_target_titles(
        self,
    ) -> None:
        """Find translated words and safely linked report titles."""
        events = [self._event("routine_started", routine="pageviews")]
        for locale, query in (
            ("en", "STARTED"),
            ("zh-hans", "开始执行"),
            ("zh-hant", "開始執行"),
        ):
            output = self._render(events, locale, query=query)
            assert '<div class="major-events">' in output
        config = copy.deepcopy(self.data["config"])
        config["routines"]["routines"]["pageviews"]["pages"] = {
            "report": 'WikiProject:<script>never execute</script> & "title"',
        }
        output = self._render(events, query="never execute", config=config)
        assert '<div class="major-events">' in output
        assert "&lt;script&gt;never execute&lt;/script&gt;" in output
        assert "<script>never execute</script>" not in output
        assert (
            "https://zh.wikipedia.org/wiki/WikiProject%3A%3Cscript%3E"
            in output
        )
        assert '<div class="major-events">' not in self._render(
            events,
            query="no match",
            config=config,
        )
        definition = config["routines"]["routines"]["pageviews"]
        definition["pages"] = {}
        definition["prefixes"] = ["WikiProject:Report prefix/"]
        output = self._render(events, query="Report prefix", config=config)
        assert "WikiProject%3AReport_prefix%2F" in output

    def test_unknown_identifiers_never_expose_diagnostics(self) -> None:
        """Omit unknown kinds and retain generic outcome labels."""
        output = self._render([
            self._event(
                "traceback_secret_diagnostic",
                detail="private message",
            ),
            self._event(
                "routine_finished",
                routine="pageviews",
                status="secret_exception_name",
                updated=None,
                duration_seconds=1,
            ),
        ])
        assert Interface("en").text("logs.result.unknown") in output
        for secret in (
            "traceback_secret_diagnostic",
            "private message",
            "secret_exception_name",
        ):
            assert secret not in output

    def test_event_display_remains_bounded(self) -> None:
        """Keep no more than one hundred saved major events visible."""
        output = self._render([
            self._event("service_started", service="hub") for _ in range(105)
        ])
        listing = re.search(r'<div class="major-events">(.*?)</div>', output)
        assert listing is not None
        assert listing[1].count("<p>") == 25

    def test_failed_attempts_keep_confirmed_edits_in_complete_sentences(
        self,
    ) -> None:
        """Separate confirmed edits and elapsed work from retries."""
        event = self._event(
            "routine_finished",
            routine="pageviews",
            status="failed",
            updated=1,
            duration_seconds=5.3,
        )
        for locale, expected in (
            (
                "en",
                (
                    "At 2026-10-18 12:00:02 UTC, "
                    "the Pageview rankings task failed. "
                    "1 page edit was confirmed. The attempt took 5.30 s. "
                    "Related reports include WikiProject:电子游戏/热门条目."
                ),
            ),
            (
                "zh-hans",
                (
                    "在2026-10-18 12:00:02 UTC\uff0c浏览量排行执行失败。"
                    "已确认完成1次页面编辑。本次执行耗时5.30 秒。"
                    "相关报告包括WikiProject:电子游戏/热门条目。"
                ),
            ),
            (
                "zh-hant",
                (
                    "在2026-10-18 12:00:02 UTC\uff0c瀏覽量排行執行失敗。"
                    "已確認完成1次頁面編輯。本次執行耗時5.30 秒。"
                    "相關報告包括WikiProject:电子游戏/热门条目。"
                ),
            ),
        ):
            with self.subTest(locale=locale):
                assert self._paragraphs(self._render([event], locale)) == [
                    expected,
                ]

    def test_pending_unconfirmed_and_preview_prose_preserves_outcomes(
        self,
    ) -> None:
        """Keep pending, unconfirmed, and preview outcomes distinct."""
        for locale, prefix, duration_sentence, reports, clauses in (
            (
                "en",
                "At 2026-10-18 12:00:02 UTC, ",
                " The attempt took 0.44 s.",
                " Related reports include WikiProject:电子游戏/热门条目.",
                (
                    "the Pageview rankings task was deferred for retry.",
                    (
                        "the Pageview rankings task finished, "
                        "but its result is unconfirmed."
                    ),
                    (
                        "the preview of the Pageview rankings task "
                        "completed successfully."
                    ),
                ),
            ),
            (
                "zh-hans",
                "在2026-10-18 12:00:02 UTC\uff0c",
                "本次执行耗时0.44 秒。",
                "相关报告包括WikiProject:电子游戏/热门条目。",
                (
                    "浏览量排行已推迟执行\uff0c等待重试。",
                    "浏览量排行已结束\uff0c但结果尚未确认。",
                    "浏览量排行预览已完成。",
                ),
            ),
            (
                "zh-hant",
                "在2026-10-18 12:00:02 UTC\uff0c",
                "本次執行耗時0.44 秒。",
                "相關報告包括WikiProject:电子游戏/热门条目。",
                (
                    "瀏覽量排行已延後執行\uff0c等待重試。",
                    "瀏覽量排行已結束\uff0c但結果尚未確認。",
                    "瀏覽量排行預覽已完成。",
                ),
            ),
        ):
            for (status, dry), clause in zip(
                (
                    ("deferred", False),
                    ("unconfirmed", False),
                    ("updated", True),
                ),
                clauses,
                strict=True,
            ):
                event = self._event(
                    "routine_finished",
                    routine="pageviews",
                    status=status,
                    dry=dry,
                    updated=1 if dry else None,
                    duration_seconds=0.44,
                )
                expected = prefix + clause + duration_sentence + reports
                with self.subTest(locale=locale, status=status, dry=dry):
                    assert self._paragraphs(self._render([event], locale)) == [
                        expected,
                    ]
                    if dry:
                        proposed = {
                            **event,
                            "status": "no_update",
                            "updated": 0,
                        }
                        assert self._paragraphs(
                            self._render([proposed], locale),
                        ) == [expected]

    def test_run_counts_and_source_failures_use_sentence_grammar(self) -> None:
        """Keep noun forms correct and source failures separate."""
        events = [
            self._event(
                "run_finished",
                tasks=1,
                failures=0,
                deferred=1,
                duration_seconds=1,
            ),
            self._event(
                "website_finished",
                status="partial",
                failures=1,
                duration_seconds=0.44,
            ),
        ]
        for locale, expected in (
            (
                "en",
                [
                    (
                        "At 2026-10-18 12:00:02 UTC, "
                        "the report run finished after processing 1 task, "
                        "with 0 tasks failing and 1 task deferred for retry. "
                        "The attempt took 1.00 s."
                    ),
                    (
                        "At 2026-10-18 12:00:02 UTC, "
                        "the website data refresh completed with some "
                        "failures. The refresh encountered 1 source failure. "
                        "The attempt took 0.44 s."
                    ),
                ],
            ),
            (
                "zh-hans",
                [
                    (
                        "在2026-10-18 12:00:02 UTC\uff0c报告执行完毕\uff0c"
                        "共处理1项任务\uff0c其中0项任务失败\uff0c"
                        "1项任务等待重试。本次执行耗时1.00 秒。"
                    ),
                    (
                        "在2026-10-18 12:00:02 UTC\uff0c"
                        "网站数据刷新已部分完成。"
                        "刷新时有1项来源获取失败。本次执行耗时0.44 秒。"
                    ),
                ],
            ),
            (
                "zh-hant",
                [
                    (
                        "在2026-10-18 12:00:02 UTC\uff0c報告執行完畢\uff0c"
                        "共處理1項任務\uff0c其中0項任務失敗\uff0c"
                        "1項任務等待重試。本次執行耗時1.00 秒。"
                    ),
                    (
                        "在2026-10-18 12:00:02 UTC\uff0c"
                        "網站資料重新整理已部分完成。"
                        "重新整理時有1項來源取得失敗。本次執行耗時0.44 秒。"
                    ),
                ],
            ),
        ):
            with self.subTest(locale=locale):
                assert (
                    self._paragraphs(self._render(events, locale)) == expected
                )

    def test_full_prose_search_and_related_targets_remain_safe(self) -> None:
        """Search complete prose and retain related targets as links."""
        event = self._event(
            "routine_finished",
            routine="pageviews",
            status="failed",
            updated=0,
            duration_seconds=5.3,
        )
        for query in (
            "0 page edits were confirmed",
            "attempt took 5.30",
            "Related reports include",
            "2026-10-18 12:00:02 UTC",
        ):
            assert (
                len(self._paragraphs(self._render([event], query=query))) == 1
            )
        config = copy.deepcopy(self.data["config"])
        config["routines"]["routines"]["pageviews"]["pages"] = {
            "first": "Report A",
            "second": "Report B",
            "third": "Report <C>",
        }
        output = self._render([event], config=config)
        assert self._paragraphs(output)[0].endswith(
            "0 page edits were confirmed. The attempt took 5.30 s. "
            "Related reports include Report A, Report B and Report <C>.",
        )
        assert "Report &lt;C&gt;" in output
        assert "<C>" not in output


if __name__ == "__main__":
    unittest.main()
