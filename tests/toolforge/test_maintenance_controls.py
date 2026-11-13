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
from urllib.parse import parse_qs, urlencode, urlsplit

from starlette.testclient import TestClient

from webservice.pages import render_maintenance, render_maintenance_overview
from webservice.presentation import (
    Interface,
    maintenance_filter,
    maintenance_items,
    maintenance_selection,
)
from webservice.server import create_app


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


def _sort_controls(output: str, column: str) -> dict[str, str]:
    """Read a native sortable header's submitted query fields.

    Returns:
        Hidden controls and the sort button's direction value.

    Raises:
        AssertionError: The column has no native sort form.
    """
    for form in re.findall(r"<form\b[^>]*>(.*?)</form>", output, re.DOTALL):
        values = {
            name: unescape(value)
            for name, value in re.findall(
                r'name="([^"]+)" value="([^"]*)"',
                form,
            )
        }
        if values.get("sort") == column and "__sort-button" in form:
            return values
    message = f"Missing sortable header: {column}"
    raise AssertionError(message)


class TestMaintenanceControls(unittest.TestCase):
    """Preserve native table controls and compatible saved queries."""

    @staticmethod
    def test_unlimited_ranges_keep_list_genre_and_size() -> None:
        """Combine open endpoints, list grades, and byte size."""
        rows = _snapshot()["backlog"]["groups"][0]["items"][:4]
        for row, grade, length in zip(
            rows,
            ("FA", "FL", "CL", None),
            (100, 300, 200, None),
            strict=True,
        ):
            row.update(assessment_class=grade, page_length=length)
        selected = maintenance_selection({
            "task": ["references"],
            "quality_op": ["range"],
            "quality_to": ["C"],
            "genre": ["list"],
            "sort": ["size"],
            "order": ["desc"],
        })
        assert (
            maintenance_items(
                maintenance_filter(rows, selected),
                "size",
                "desc",
            )
            == rows[1:3]
        )
        assert (
            maintenance_filter(rows, {"quality": "C", "quality_op": "range"})
            == rows[2:]
        )
        assert (
            maintenance_filter(
                rows,
                {"quality_op": "range", "importance_op": "range"},
            )
            == rows
        )
        output = render_maintenance(
            _snapshot(),
            {"task": ["references"]},
            "zh-hant",
        )
        assert '<option value="D"' not in output
        assert '<option value="FL"' not in output
        assert '<option value="Substub"' not in output
        assert 'class="maintenance-controls"' not in output
        assert 'id="maintenance-quality-to"' not in output
        assert 'name="genre" value="list"' not in output
        assert output.count('class="ui-table-pager ') == 1
        assert output.count('class="source-updated"') == 1

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

    def test_regex_and_shared_grade_ranges(self) -> None:
        """Filter a complete list by safe regex and inclusive ranks."""
        rows = _snapshot()["backlog"]["groups"][0]["items"][:6]
        for row, quality, importance in zip(
            rows,
            ("C", "乙", "A", "GA", None, "invented"),
            ("Low", "High", "Top", "Mid", None, "invented"),
            strict=True,
        ):
            row["assessment_class"] = quality
            row["assessment_importance"] = importance
        selection = maintenance_selection({
            "task": ["references"],
            "q": [r"^GAME 00[1-4]$"],
            "regex": ["1"],
            "quality": ["C"],
            "quality_op": ["range"],
            "quality_to": ["A"],
            "importance": ["High"],
            "importance_op": ["gte"],
        })
        assert maintenance_filter(rows, selection) == rows[1:3]
        selection.update(quality="A", quality_to="C")
        assert maintenance_filter(rows, selection) == rows[1:3]
        assert (
            maintenance_filter(
                rows,
                {
                    "q": r"^(?-i:Game) \d+$",
                    "regex": "1",
                },
            )
            == rows
        )
        assert (
            maintenance_filter(
                rows,
                {
                    "q": r"^(?-i:GAME) \d+$",
                    "regex": "1",
                },
            )
            == []
        )
        assert maintenance_filter(
            rows,
            {
                "importance": "Mid",
                "importance_op": "lte",
            },
        ) == [rows[0], rows[3], rows[4], rows[5]]
        for invalid in (
            {"q": ["["], "regex": ["1"]},
            {"q": [r"(a)\1"], "regex": ["1"]},
            {"quality": ["Unknown"], "quality_op": ["gte"]},
        ):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(ValueError),
            ):
                maintenance_selection({"task": ["references"], **invalid})

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
        assert output.count('class="ui-table-pager ') == 1
        assert output.count('class="page-jump"') == 0
        assert "Articles 11\u201320 of 103" in output
        assert ">11</td>" in output
        assert "Game 011" in output
        assert "Game 010" not in output
        assert 'aria-sort="ascending"' in output
        assert _sort_controls(output, "title") == {
            "task": "references",
            "q": "Game",
            "quality": "B",
            "importance": "Low",
            "sort": "title",
            "order": "desc",
        }
        assert _sort_controls(output, "class")["order"] == "desc"
        links = re.findall(r'href="([^"]+)"[^>]*rel="(?:prev|next)"', output)
        assert len(links) == 2
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
                assert (
                    '<table class="maintenance-table ui-table__table"'
                    in output
                )
                assert f'data-label="{heading}"' in output
                assert 'scope="col"' in output
                assert 'scope="row"' in output
                assert 'class="navigation-tools maintenance-menu"' in output
                assert 'class="maintenance-search" role="search"' in output
                assert "<caption>" in output
                output = render_maintenance(
                    _snapshot(),
                    {"task": ["references"], "q": ['<script>"']},
                    locale,
                )
                assert '<script>"' not in output
                assert "&lt;script&gt;&#34;" in output
                assert Interface(locale).text("backlog.empty") in output


class TestMaintenanceTemplateIcons(unittest.TestCase):
    """Keep tasks without verified images consistent with icon cards."""

    @staticmethod
    def test_text_only_templates_use_a_decorative_svg_icon() -> None:
        """Replace bare source with the shared template symbol."""
        snapshot = _snapshot()
        snapshot["backlog"]["total"] = 103
        snapshot["backlog"]["groups"] = [
            {**snapshot["backlog"]["groups"][0], "key": "citation_needed"},
        ]
        output = render_maintenance_overview(snapshot, {}, {}, "en")
        assert re.search(
            r'<svg class="maintenance-icon ui-card__icon"[^>]*'
            r'aria-hidden="true"[^>]*><use href="[^"]+#template">',
            output,
        )
        assert "{{...}}" not in output
        assert "maintenance-template-icon" not in output


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
            "webservice.server._PublicViews._dashboard",
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
            assert len(expected) == 10
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

    def test_header_sort_orders_full_category_and_resets_page(self) -> None:
        """Follow header sorting across pages and directions."""
        snapshot = _snapshot()
        rows = snapshot["backlog"]["groups"][0]["items"]
        for row, grade in zip(
            rows[-6:],
            ("Bplus", "GA", "AL", "A", "FL", "FA"),
            strict=True,
        ):
            row["assessment_class"] = grade
        with patch(
            "webservice.server._PublicViews._dashboard",
            return_value=snapshot,
        ):
            initial = self.client.get(
                "/maintenance/?task=references&q=Game&page=2&limit=20",
            )
            controls = _sort_controls(initial.text, "class")
            assert "page" not in controls
            assert controls["limit"] == "20"
            assert controls["q"] == "Game"
            descending = self.client.get(
                "/maintenance/?" + urlencode(controls),
            )
            assert descending.status_code == 200
            titles = re.findall(
                r'<th scope="row".*?>(Game \d+)</a>',
                descending.text,
            )
            assert titles[:6] == [
                "Game 103",
                "Game 102",
                "Game 101",
                "Game 100",
                "Game 099",
                "Game 098",
            ]
            assert 'aria-sort="descending"' in descending.text
            reverse = _sort_controls(descending.text, "class")
            assert reverse["order"] == "asc"
            ascending = self.client.get("/maintenance/?" + urlencode(reverse))
            assert ascending.status_code == 200
            assert 'aria-sort="ascending"' in ascending.text
            titles = re.findall(
                r'<th scope="row".*?>(Game \d+)</a>',
                ascending.text,
            )
            assert titles[0] == "Game 001"
            assert "Game 103" not in titles

    def test_actual_language_switch_keeps_filtered_second_page(self) -> None:
        """Keep the selected rows and numbering across scripts."""
        original = (
            "/maintenance/?task=references&q=Game&quality=B"
            "&importance=Low&sort=title&order=asc&page=2"
            "&seed=0123456789abcdef&lang=en"
        )
        response = self.client.get(original)
        assert response.status_code == 200
        assert "Game 011" in response.text
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
                assert "Game 011" in changed.text
                assert "Game 010" not in changed.text
                assert ">11</td>" in changed.text
                next_link = re.search(
                    r'<a[^>]+href="([^"]+)" rel="next"[^>]*>',
                    changed.text,
                )
                assert next_link is not None
                next_page = self.client.get(unescape(next_link[1]))
                assert next_page.status_code == 200
                assert next_page.headers["Content-Language"] == tag
                assert "Game 021" in next_page.text
                assert "Game 020" not in next_page.text

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
        assert Interface("en").text("backlog.empty") in output
        assert "Articles 0\u20130 of 0" in output
