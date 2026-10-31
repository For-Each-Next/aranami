"""Verify maintenance filters, paging, and responsive field labels."""

# The repository uses unittest and fixed observation counts.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import re
import tempfile
import unittest
from html import unescape
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from backend.public import create_app
from webservice.pages import render_maintenance
from webservice.presentation import (
    Interface,
    maintenance_filter,
    maintenance_selection,
)


def _snapshot() -> dict[str, object]:
    """Provide enough saved articles for three pages without collection.

    Returns:
        Independent maintenance rows, tasks, and source metadata.
    """
    rows = [
        {
            "page_id": number,
            "title": f"Game {number:03}",
            "url": f"https://zh.wikipedia.org/wiki/Game_{number}",
            "assessment_class": "B",
            "assessment_importance": "Low",
            "issues": ["references"],
            "oldest_issue_month": "2020-03",
            "oldest_issue_category": None,
        }
        for number in range(1, 104)
    ]
    group = {
        "key": "references",
        "category": "缺少来源的条目",
        "label": {
            "en": "Add citations",
            "zh-hans": "补充来源",
            "zh-hant": "補充來源",
        },
        "source_url": "https://zh.wikipedia.org/wiki/Category:References",
        "count": len(rows),
        "items": rows,
    }
    return {
        "backlog": {
            "observed_at": "2026-10-18T12:00:00Z",
            "groups": [group, {**group, "key": "cleanup"}],
        },
    }


class TestMaintenanceControls(unittest.TestCase):
    """Preserve filter state and native GET controls across pages."""

    @staticmethod
    def test_title_and_assessment_filters() -> None:
        """Match literals, aliases, missing grades, and unknowns."""
        rows = _snapshot()["backlog"]["groups"][0]["items"]
        rows[0]["title"] = "Literal <&> [Game]"
        rows[0]["assessment_class"] = "乙"
        rows[1]["assessment_class"] = None
        rows[2]["assessment_class"] = "invented"
        rows[2]["assessment_importance"] = "invented"
        assert maintenance_filter(rows, {"q": "[gAME]", "quality": "B"}) == [
            rows[0],
        ]
        assert maintenance_filter(rows, {"quality": "Unassessed"}) == [
            rows[1],
        ]
        assert maintenance_filter(rows, {"quality": "Unknown"}) == [rows[2]]
        assert maintenance_filter(rows, {"importance": "Unknown"}) == [
            rows[2],
        ]
        assert maintenance_filter(rows, {"q": "not present"}) == []
        assert maintenance_filter([], {"q": "anything"}) == []
        assert maintenance_filter(rows, {}) is rows

    def test_controls_validate_and_allow_empty_filter_fields(self) -> None:
        """Reject unsupported values and bound literal search input."""
        assert maintenance_selection({
            "task": ["references"],
            "q": [""],
            "quality": [""],
        }) == {
            "task": "references",
            "page": "1",
            "sort": "oldest",
            "order": "asc",
        }
        for parameters in (
            {"task": ["references"], "q": ["a" * 201]},
            {"task": ["references"], "q": ["line\nbreak"]},
            {"task": ["references"], "quality": ["invented"]},
            {"task": ["references"], "importance": ["invented"]},
            {"task": ["references"], "q": ["a", "b"]},
        ):
            with (
                self.subTest(parameters=parameters),
                self.assertRaises(ValueError),
            ):
                maintenance_selection(parameters)

    @staticmethod
    def test_pagination_and_sort_keep_filter_state() -> None:
        """Show the second range with links and both jump forms."""
        output = render_maintenance(
            _snapshot(),
            parse_qs(
                "task=references&q=Game&quality=B&importance=Low"
                "&sort=title&order=asc&page=2",
            ),
            "en",
        )
        assert output.count('class="pagination"') == 2
        assert output.count('class="page-jump"') == 2
        assert "Articles 51\u2013100 of 103" in output
        assert ">51</td>" in output
        assert "Game 051" in output
        assert "Game 050" not in output
        assert 'aria-current="true">Article name</a>' in output
        links = re.findall(r'href="([^"]+)"[^>]*rel="(?:prev|next)"', output)
        assert len(links) == 4
        for link in links:
            values = parse_qs(urlsplit(unescape(link)).query)
            assert values["q"] == ["Game"]
            assert values["quality"] == ["B"]
            assert values["importance"] == ["Low"]
            assert values["sort"] == ["title"]
            assert values["order"] == ["asc"]

    def test_three_locales_keep_labeled_rows_and_escape_search(self) -> None:
        """Escape input and expose labeled fields and empty results."""
        for locale, heading in (
            ("en", "First tagged"),
            ("zh-hans", "首次标记时间"),
            ("zh-hant", "首次標記時間"),
        ):
            with self.subTest(locale=locale):
                output = render_maintenance(
                    _snapshot(),
                    {"task": ["references"]},
                    locale,
                )
                assert 'class="maintenance-table"' in output
                assert f'data-label="{heading}"' in output
                assert 'scope="col"' in output
                assert 'scope="row"' in output
                assert 'id="maintenance-task"' in output
                output = render_maintenance(
                    _snapshot(),
                    {"task": ["references"], "q": ['<script>"']},
                    locale,
                )
                assert '<script>"' not in output
                assert "&lt;script&gt;&#34;" in output
                assert 'class="empty"' in output


class TestMaintenanceHttpControls(unittest.TestCase):
    """Verify real query parsing and locale changes over saved rows."""

    def setUp(self) -> None:
        """Create a client with synthetic dashboard observations."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runtime = Path(temporary.name)
        self.client = TestClient(create_app(runtime))
        self.addCleanup(self.client.close)
        saved = patch(
            "backend.public._PublicViews._dashboard",
            return_value=_snapshot(),
        )
        saved.start()
        self.addCleanup(saved.stop)

    def test_generated_random_seed_survives_language_switches(self) -> None:
        """Keep initial random ordering when choosing a language."""
        with patch(
            "webservice.pages.secrets.token_hex",
            return_value="0123456789abcdef",
        ) as generated:
            response = self.client.get(
                "/maintenance/?task=references&sort=random&q=Game&page=2",
            )
            assert response.status_code == 200
            row_pattern = r'<th scope="row".*?>(Game \d+)</a>'
            expected = re.findall(row_pattern, response.text)
            assert len(expected) == 50
            links = re.findall(
                r'<a href="([^"]+)" lang="(?:en|zh-Hans|zh-Hant)" hreflang=',
                response.text,
            )
            assert len(links) == 3
            for link in links:
                target = unescape(link)
                query = parse_qs(urlsplit(target).query)
                assert query["seed"] == ["0123456789abcdef"]
                assert query["page"] == ["2"]
                assert query["q"] == ["Game"]
                assert query["sort"] == ["random"]
                translated = self.client.get(target)
                assert translated.status_code == 200
                assert re.findall(row_pattern, translated.text) == expected
            generated.assert_called_once_with(8)

    def test_actual_language_switch_keeps_filtered_second_page(self) -> None:
        """Keep the selected rows and numbering across scripts."""
        original = (
            "/maintenance/?task=references&q=Game&quality=B"
            "&importance=Low&sort=title&order=asc&page=2"
            "&seed=0123456789abcdef&lang=en"
        )
        response = self.client.get(original)
        assert response.status_code == 200
        assert "Game 051" in response.text
        assert Interface("en").text("backlog.clear") in response.text
        for locale, tag in (
            ("en", "en"),
            ("zh-hans", "zh-Hans"),
            ("zh-hant", "zh-Hant"),
        ):
            with self.subTest(locale=locale):
                match = re.search(
                    rf'<a href="([^"]+)" lang="{tag}" hreflang=',
                    response.text,
                )
                assert match is not None
                target = unescape(match[1])
                query = parse_qs(urlsplit(target).query)
                assert query["lang"] == [locale]
                assert query["page"] == ["2"]
                assert query["q"] == ["Game"]
                assert query["quality"] == ["B"]
                assert query["importance"] == ["Low"]
                assert query["seed"] == ["0123456789abcdef"]
                changed = self.client.get(target)
                assert changed.status_code == 200
                assert changed.headers["Content-Language"] == tag
                assert f'<html lang="{tag}">' in changed.text
                assert "Game 051" in changed.text
                assert "Game 050" not in changed.text
                assert ">51</td>" in changed.text
                next_link = re.search(
                    r'<a href="([^"]+)" rel="next">',
                    changed.text,
                )
                assert next_link is not None
                next_page = self.client.get(unescape(next_link[1]))
                assert next_page.status_code == 200
                assert next_page.headers["Content-Language"] == tag
                assert "Game 101" in next_page.text
                assert "Game 100" not in next_page.text

    def test_unknown_empty_and_malformed_filters_over_http(self) -> None:
        """Accept empty filters and reject malformed input."""
        for suffix, status in (
            ("q=&quality=&importance=", 200),
            ("q=no-match&quality=B&importance=Low", 200),
            ("quality=Unknown", 200),
            ("quality=invented", 400),
            ("q=one&q=two", 400),
            ("q=%FF", 400),
        ):
            with self.subTest(suffix=suffix):
                result = self.client.get(
                    "/maintenance/?task=references&" + suffix,
                )
                assert result.status_code == status
        output = self.client.get(
            "/maintenance/?task=references&q=no-match&page=999",
        ).text
        assert 'class="empty"' in output
        assert "Articles 0\u20130 of 0" in output
