"""Verify Python pages and shared presentation contracts offline."""

# Keep unittest exceptions and expected fixture counts.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from shared.config import routine_configuration
from webservice.pages import (
    render_about,
    render_dashboard,
    render_error,
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
    element,
    event_duration,
    fragment,
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
            element("p", '<script>"&', element("b", "bold"), title='"data'),
        )
        assert (
            rendered
            == '<p title="&quot;data">&lt;script&gt;&quot;&amp;<b>bold</b></p>'
        )
        assert str(fragment(None, "<unsafe>")) == "&lt;unsafe&gt;"
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
        assert duration(0.25, self.en) == "0.25 sec"
        assert duration(59.999, self.en) == "1 min 0 sec"
        assert duration(65.1, self.en) == "1 min 5 sec"
        assert duration(5, self.en, exact=True) == "5.000 sec"
        assert duration(None, self.en) == "—"
        assert (
            event_duration(
                {"status": "running", "started_at": "2026-10-18T11:54:15Z"},
                self.en,
                self.now,
            )
            == "6 min 15 sec…"
        )
        assert (
            event_duration(
                {"status": "running", "started_at": "2026-10-18T13:00:00Z"},
                self.en,
                self.now,
            )
            == "0 sec…"
        )
        assert readable_size(1024) == "1.0 KiB"
        assert readable_size(10) == "10 B"

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
        sections = {
            row["key"]: row["items"]
            for row in homepage_sections(self.data["snapshot"])
        }
        assert sections["recognized"][0]["title"] == "Scheduled game"
        assert len(sections["dyk"]) == 12
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
        """Build escaped pages with native accessible details."""
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
            assert "<script" not in output
            assert "DYK game 13" not in output
            assert "News game 7" in output
            assert output.count('class="article-extract"') == 24
            assert output.count('class="extract-attribution"') == 4
            assert output.count('scope="col"') == 5
            assert '<details class="page-updates">' in output
            assert '<summary class="status-link">' in output
            assert "5.000" in output
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
        """Share navigation and escape diagnostics in every locale."""
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
                    "lines": ["<script>never execute</script>"],
                    "truncated": True,
                },
                locale,
            )
            assert "<script" not in output
            assert "&lt;script&gt;never execute&lt;/script&gt;" in output
            assert "1.0 KiB" in output
            assert "/logs/?date=2026-10-18&amp;download=1" in output
            assert 'class="empty"' in render_logs({"files": []}, locale)

    @staticmethod
    def test_shared_css_preserves_responsive_contracts() -> None:
        """Keep narrow cards, counters, and scrollable details."""
        root = Path(__file__).resolve().parents[2] / "src" / "webservice"
        styles = (root / "public_html" / "styles.css").read_text()
        assert "repeat(auto-fill, minmax(min(18em, 100%), 1fr))" in styles
        assert "list-style: decimal inside" in styles
        assert "overflow-x: auto" in styles
        assert "margin-top: auto" in styles
        assert not list(root.rglob("*.php"))
        assert not list(root.rglob("*.js"))
        assert not list(root.rglob("*.mjs"))


if __name__ == "__main__":
    unittest.main()
