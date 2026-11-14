"""Verify compact pages and atomic quality-resource reads."""

# Counts and URL segments describe explicit protocol fixtures.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt
import json
import unittest
from hashlib import sha256
from html import unescape
from http import HTTPStatus
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from starlette.testclient import TestClient
from test_quality_analysis import analysis_fixture
from test_quality_authorship import (
    _directory,  # ruff: ignore[import-private-name] - Reuse the hostile-source fixture.
)

from data.articles import quality_pages
from data.articles.quality_selection import quartiles
from webservice.http import encoded_response
from webservice.pages import render_quality_analysis, render_quality_authorship
from webservice.presentation import Interface
from webservice.quality_analysis import (
    main_author_directory,
    quality_analysis_context,
)
from webservice.server import create_app

_NOW = dt.datetime(2026, 11, 14, 12, tzinfo=dt.UTC)
_PREFIX = "/quality-analysis/resources/en/0/current/"


def _catalog_fixture() -> dict:
    """Provide a larger cohort with saved attribution.

    Returns:
        Saved summaries, matrix cells and article/author observations.
    """
    analysis = analysis_fixture()
    articles = [
        {
            **analysis["articles"][0],
            "page_id": index,
            "title": f"Catalog-only article {index} " + "Long title " * 20,
        }
        for index in range(1, 101)
    ]
    defaults = {
        **analysis,
        "articles": articles,
        "coverage": [{**analysis["coverage"], "total": len(articles)}],
        "years": [{"year": 2026, "pages": len(articles), "FA": 100}],
    }
    authorship = _directory()
    for article in articles[:2]:
        article.update(
            authorship_available=True,
            authorship_editors=[
                {
                    "editor": "1",
                    "name": authorship["authors"][0]["name"],
                    "share": 0.6,
                    "bytes": 60,
                },
            ],
        )
    return {
        "analysis": {
            **analysis,
            "selection": {
                "articles": articles,
                "authors": authorship["authors"],
                "defaults": [defaults, defaults, defaults],
                "cells": [],
                "last_year": 2026,
            },
        },
        "authorship": authorship,
    }


def _resource(content: str) -> str:
    """Name one immutable catalog fixture.

    Returns:
        The exact URL basename for these content bytes.
    """
    return f"catalog-{sha256(content.encode()).hexdigest()}.json"


class QualityResourcePresentationTests(unittest.TestCase):
    """Keep saved results while deferring optional payloads."""

    @staticmethod
    def test_missing_listing_dates_are_unlinked_in_every_directory() -> None:
        """Never replace a missing rating date with a revision link."""
        data = _catalog_fixture()
        data["analysis"]["selection"]["articles"][0]["listed_date"] = None
        data["authorship"]["authors"][0]["articles"][0]["date"] = None
        for locale, label in (
            ("en", "Date not obtained"),
            ("zh-hans", "未取得日期"),
            ("zh-hant", "未取得日期"),
        ):
            resources = {}
            output = render_quality_analysis(
                data,
                None,
                None,
                locale,
                _NOW,
                resources=resources,
            )
            detail = next(
                content
                for name, content in resources.items()
                if name.startswith("author-quality-author-1-")
            )
            legacy = render_quality_authorship(
                data["authorship"],
                None,
                None,
                locale,
                _NOW,
            )
            missing_cell = (
                '<td data-sort=""><span class="context">' + label + "</span>"
            )
            assert missing_cell in output
            assert missing_cell in detail
            assert missing_cell in legacy
            assert 'oldid=777"' not in detail
            catalog = next(
                json.loads(content)
                for name, content in resources.items()
                if name.endswith(".json")
            )
            assert catalog["labels"]["unknown_listing"] == label

    @staticmethod
    def test_catalog_and_author_details_leave_the_initial_document() -> None:
        """Retain twenty rows and native links in every locale."""
        data = _catalog_fixture()
        for locale in ("en", "zh-hans", "zh-hant"):
            resources = {}
            prefix = f"/quality-analysis/resources/{locale}/0/current/"
            output = render_quality_analysis(
                data,
                None,
                None,
                locale,
                _NOW,
                resources=resources,
                resource_prefix=prefix,
            )
            legacy = render_quality_analysis(
                data,
                None,
                None,
                locale,
                _NOW,
            )
            assert len(output.encode()) < len(legacy.encode()) * 0.75
            assert "data-metadata='" not in output
            assert f'data-metadata-url="{prefix}catalog-' in output
            assert output.count("<tr data-analysis-row ") == 20
            assert "Catalog-only article 100 " not in output
            assert '<dialog id="quality-author-1"' not in output
            assert 'data-quality-detail="quality-author-1"' in output
            assert "data-page-ids='[1, 2]'" in output
            assert "data-quality-load-status" in output
            catalog = next(
                json.loads(content)
                for name, content in resources.items()
                if name.endswith(".json")
            )
            assert len(catalog["extra"]) == 80
            assert catalog["extra"][-1][0] == 100
            detail_name, detail = next(
                (name, content)
                for name, content in resources.items()
                if name.startswith("author-quality-author-1-")
            )
            assert f"{prefix}{detail_name}#quality-author-1" in output
            assert '<dialog id="quality-author-1"' in detail
            assert 'aria-labelledby="quality-author-1-heading" open>' in detail
            assert "Game &lt;script&gt; &amp; title" in detail
            assert 'href="/quality-analysis/?lang=' in detail
            assert '#quality-authors"' in detail
            assert '<script src="/live.js?' in detail
            assert "<script>" not in detail
            assert 'data-matrix-page="1"' in detail
            assert f"lang={locale}" in unescape(detail)

    @staticmethod
    def test_initial_author_counts_and_details_use_the_default_cohort() -> (
        None
    ):
        """Keep counts accurate before the optional catalog arrives."""
        data = _catalog_fixture()
        selection = data["analysis"]["selection"]
        selection["defaults"][0]["articles"] = [selection["articles"][0]]
        resources = {}
        output = render_quality_analysis(
            data,
            None,
            None,
            "en",
            _NOW,
            resources=resources,
        )
        assert "data-selected-page-ids='[1]'" in output
        assert "data-page-ids='[1, 2]'" in output
        assert 'data-sort="1"><a href="/quality-analysis/resources/' in output
        assert ">1 article</a>" in output
        assert 'data-matrix-author="Unknown activity"' not in output
        detail = next(
            content
            for name, content in resources.items()
            if name.startswith("author-quality-author-1-")
        )
        assert 'data-matrix-page="2" hidden data-cohort="0"' in detail
        assert 'data-matrix-page="1"><td' in detail

    @staticmethod
    def test_missing_authorship_differs_from_observed_empty_and_boundary() -> (
        None
    ):
        """Keep missing data distinct from no share reaching 10%."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            for available, share, expected in (
                (False, None, "authorship_unavailable"),
                (True, None, "no_top_editors"),
                (True, 0.1, None),
                (True, 0.10001, None),
            ):
                data = analysis_fixture()
                article = data["articles"][0]
                article["authorship_available"] = available
                article["authorship_editors"] = (
                    [
                        {
                            "editor": "1",
                            "name": "Detail editor",
                            "share": share,
                            "bytes": 10,
                        },
                    ]
                    if share is not None
                    else []
                )
                data["articles"] = [article]
                context = quality_analysis_context(data, interface)
                proof = context["quality_analysis"]["articles"][0][
                    "authorship"
                ]
                assert proof["available"] == available
                assert bool(proof["editors"]) == (
                    share is not None and share >= 0.1
                )
                output = render_quality_analysis(
                    data,
                    None,
                    None,
                    locale,
                    _NOW,
                )
                for key in ("authorship_unavailable", "no_top_editors"):
                    assert (
                        interface.text(f"quality_analysis.{key}") in output
                    ) == (key == expected)

    @staticmethod
    def test_contribution_types_use_main_names_and_observed_major_shares() -> (
        None
    ):
        """Exclude rank-only and minor editors from filters."""
        data = _catalog_fixture()
        articles = data["analysis"]["selection"]["articles"]
        proof = {
            "authors": ["Main author"],
            "author_ranks": [
                {"name": "Minor editor", "rank": 1},
                {"name": "Rank-only editor", "rank": 2},
            ],
            "authorship_available": True,
            "authorship_editors": [
                {"editor": str(index), "name": name, "share": share}
                for index, (name, share) in enumerate(
                    (
                        ("Main author", 0.6),
                        ("Significant author", 0.2),
                        ("Boundary editor", 0.1),
                        ("Minor editor", 0.05),
                    ),
                    1,
                )
            ],
        }
        articles[0].update(proof)
        articles[20].update(proof)
        articles[1].update({**proof, "authorship_available": False})
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            context = quality_analysis_context(
                {**data["analysis"], "articles": articles},
                interface,
            )["quality_analysis"]
            first, missing = context["articles"][:2]
            assert first["main_authors"] == ["Main author"]
            assert first["significant_authors"] == [
                "Main author",
                "Significant author",
                "Boundary editor",
            ]
            assert missing["main_authors"] == []
            assert missing["significant_authors"] == []
            assert not missing["authorship"]["available"]
            resources = {}
            output = render_quality_analysis(
                data,
                None,
                None,
                locale,
                _NOW,
                resources=resources,
                resource_prefix=(
                    f"/quality-analysis/resources/{locale}/0/current/"
                ),
            )
            select = output.split("<select data-analysis-author-role>", 1)[1]
            select = select.split("</select>", 1)[0]
            assert select.count("<option ") == 2
            assert '<option value="main">' in select
            assert '<option value="significant">' in select
            for key in ("main_contributors", "significant_contributors"):
                assert interface.text(f"quality_analysis.{key}") in select
            assert interface.text("quality_analysis.author_role") in output
            assert "data-main-authors='[\"Main author\"]'" in output
            assert "data-significant-authors" in output
            assert "data-author-ranks" not in output
            assert "data-analysis-author-rank" not in output
            assert "data-analysis-filter-note" not in output
            assert "Rank-only editor" not in output
            assert "Minor editor" not in output
            catalog = next(
                json.loads(content)
                for name, content in resources.items()
                if name.endswith(".json")
            )
            extra = catalog["extra"][0]
            assert extra[8] == ["Main author"]
            assert extra[9] is None
            assert extra[12]["available"]
            assert [editor["name"] for editor in extra[12]["editors"]] == [
                "Main author",
                "Significant author",
                "Boundary editor",
            ]

    @staticmethod
    def test_main_threshold_anonymous_and_directory_use_the_same_proof() -> (
        None
    ):
        """Distinguish above-33% attribution from empty data."""
        data = analysis_fixture()
        row = data["articles"][0]
        row.update(
            authors=["Primary at 25", "Boundary at 33", "Above 33"],
            authorship_available=True,
            authorship_editors=[
                {"editor": str(index), "name": name, "share": share}
                for index, (name, share) in enumerate(
                    (
                        ("Primary at 25", 0.25),
                        ("Boundary at 33", 0.33),
                        ("Above 33", 0.33001),
                    ),
                    1,
                )
            ]
            + [{"editor": "0|192.0.2.1", "name": None, "share": 0.45}],
        )
        candidates = [
            {
                "name": name,
                "credit": 1,
                "grades": {"FA": 1},
                "articles": [{"page_id": row["page_id"], "grade": "FA"}],
            }
            for name in (
                "Primary at 25",
                "Boundary at 33",
                "Above 33",
                "192.0.2.1",
            )
        ]
        directory = main_author_directory([row], candidates)
        assert [author["name"] for author in directory] == ["Above 33"]
        assert directory[0]["credit"] == 1
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            first = quality_analysis_context(data, interface)[
                "quality_analysis"
            ]["articles"][0]
            assert first["main_authors"] == ["Above 33", "192.0.2.1"]
            assert first["contributors"][1] == {
                "name": "192.0.2.1",
                "label": interface.text("quality_analysis.anonymous_user"),
                "url": None,
            }
            assert "Boundary at 33" in first["significant_authors"]
            output = render_quality_analysis(data, None, None, locale, _NOW)
            cell = output.split(
                '<td data-sort="Above 33, '
                + interface.text("quality_analysis.anonymous_user")
                + '">',
                1,
            )[1]
            cell = cell.split("</td>", 1)[0]
            assert (
                "<span>"
                + interface.text("quality_analysis.anonymous_user")
                + "</span>"
                in cell
            )
            assert "192.0.2.1" not in cell
            assert ">Boundary at 33</a>" not in cell
            row["authorship_editors"] = []
            empty = quality_analysis_context(data, interface)[
                "quality_analysis"
            ]["articles"][0]
            assert empty["authorship"]["available"]
            assert empty["contributors"] == []
            empty_output = render_quality_analysis(
                data,
                None,
                None,
                locale,
                _NOW,
            )
            assert (
                '<span class="context">'
                + interface.text("quality_analysis.no_main_contributors")
                + "</span>"
                in empty_output
            )
            row["authorship_available"] = False
            assert main_author_directory([row], candidates) == []
            missing_output = render_quality_analysis(
                data,
                None,
                None,
                locale,
                _NOW,
            )
            assert (
                '<span class="context">'
                + interface.text("quality_analysis.main_unavailable")
                + "</span>"
                in missing_output
            )
            row.update(
                authorship_available=True,
                authorship_editors=[
                    {"editor": "1", "name": "Primary at 25", "share": 0.25},
                    {"editor": "2", "name": "Boundary at 33", "share": 0.33},
                    {"editor": "3", "name": "Above 33", "share": 0.33001},
                    {"editor": "0|192.0.2.1", "name": None, "share": 0.45},
                ],
            )

    @staticmethod
    def test_annual_legend_and_columns_follow_selected_grades() -> None:
        """Keep bar legends and selected zero-count columns exact."""
        data = _catalog_fixture()
        selection = data["analysis"]["selection"]
        for defaults in selection["defaults"]:
            defaults["years"] = [
                {"year": 2025, "pages": 0, "FA": 0, "GA": 0},
                {"year": 2026, "pages": 2, "FA": 2, "GA": 0},
            ]
            defaults["grade_totals"] = [
                {"grade": "FA", "pages": 2},
                {"grade": "GA", "pages": 0},
            ]
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_quality_analysis(
                data,
                None,
                None,
                locale,
                _NOW,
                resources={},
            )
            legend = output.split('<ul class="analysis-legend"', 1)[1]
            legend = legend.split("</ul>", 1)[0]
            assert 'class="analysis-grade-FA"' in legend
            assert 'class="analysis-grade-GA"' not in legend
            assert 'class="analysis-grade-B"' not in legend
            assert legend.count("<li>") == 1
            color = quality_analysis_context(
                analysis_fixture(),
                Interface(locale),
            )["quality_analysis"]["years"][1]["segments"][0]["color"]
            assert f'fill="{color}"' in legend
            table = output.split('<table class="analysis-year-data">', 1)[1]
            table = table.split("</table>", 1)[0]
            assert '<th scope="row">2025</th>' in table
            assert '<th scope="row">2026</th>' in table
            assert '<td class="analysis-total-column">0</td>' in table
            assert '<td class="analysis-total-column">2</td>' in table
            assert table.count('<th scope="col"') == 6
            assert 'class="analysis-year-scroll"' in output
            assert "Scroll horizontally to inspect more years" not in output
            header = table.split("</thead>", 1)[0]
            assert (
                '<th scope="col">'
                + Interface(locale).text("assessment.class.B")
                + "</th>"
            ) not in header
            context = quality_analysis_context(
                data["analysis"],
                Interface(locale),
            )["quality_analysis"]
            assert [
                grade["value"] for grade in context["year_table_grades"]
            ] == [
                "FA",
                "FL",
                "GA",
                "Bplus",
            ]
            assert "analysis-grade-B" not in legend

    @staticmethod
    def test_quartiles_use_weighted_selected_histograms_and_keep_zero() -> (
        None
    ):
        """Keep exact extrema, weighted quartiles and missing data."""
        cells = [
            {
                "grade": "FA",
                "importance": "High",
                "year": 2026,
                "histogram": [
                    {"value": value, "count": 1} for value in (0, 10, 20, 40)
                ],
            },
            {
                "grade": "B",
                "importance": "High",
                "year": 2026,
                "histogram": [{"value": 1000, "count": 100}],
            },
            {
                "grade": "FA",
                "importance": "High",
                "year": 1999,
                "histogram": [{"value": 1000, "count": 100}],
            },
            {
                "grade": "GA",
                "importance": "Low",
                "year": None,
                "histogram": [{"value": 0, "count": 5}],
            },
            {
                "grade": "FL",
                "importance": "Unknown",
                "year": 2026,
                "histogram": [],
            },
        ]
        observed = {
            (row["group"], row[row["group"]]): (
                row["minimum"],
                row["p25"],
                row["p75"],
                row["maximum"],
            )
            for row in quartiles(cells, ["FA", "GA", "FL"], 2026)
        }
        assert observed["grade", "FA"] == (0, 7.5, 25.0, 40)
        assert observed["importance", "High"] == (0, 7.5, 25.0, 40)
        assert observed["grade", "GA"] == (0, 0.0, 0.0, 0)
        assert ("grade", "FL") not in observed
        assert ("grade", "B") not in observed
        data = analysis_fixture()
        data["selection"] = {
            "cells": cells,
            "last_year": 2026,
            "articles": data["articles"],
            "defaults": [{"articles": data["articles"]}],
            "authors": [],
        }
        data["lengths"] = [
            {
                "group": "grade",
                "grade": "FA",
                "importance": None,
                "pages": 5,
                "measured": 4,
                "median_characters": 15.0,
                "central_95_high": 35,
                "density": [
                    {"position": 5, "density": 0.2},
                    {"position": 80, "density": 0.1},
                ],
            },
        ]
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            result = quality_analysis_context(data, interface)
            length = result["quality_analysis"]["lengths"][0]
            assert length["quartiles"] == [
                {"percentile": 25, "value": 7.5, "x": 93.75},
                {"percentile": 75, "value": 25.0, "x": 312.5},
            ]
            assert isclose(length["median_x"], 187.5)
            assert length["summary"] == interface.text(
                "quality_analysis.violin_summary",
                pages="5",
                measured="4",
                minimum="0",
                p25="8",
                p50="15",
                p75="25",
                maximum="40",
            )
            data["selection"]["defaults"][0].update(
                {
                    **{
                        key: value
                        for key, value in data.items()
                        if key != "selection"
                    },
                    "coverage": [data["coverage"]],
                    "years": [],
                },
            )
            output = render_quality_analysis(data, None, None, locale, _NOW)
            assert 'data-percentile="25"' in output
            assert 'data-percentile="75"' in output
            assert 'x1="187.5" x2="187.5"' in output
            assert length["summary"] in output

    @staticmethod
    def test_length_labels_round_half_up_without_changing_quantiles() -> None:
        """Round labels and retain exact marker observations."""
        for lower in (0, 75):
            data = analysis_fixture()
            data["selection"] = {
                "cells": [
                    {
                        "grade": "FA",
                        "importance": "High",
                        "year": 2026,
                        "histogram": [
                            {"value": lower, "count": 1},
                            {"value": lower + 1, "count": 1},
                        ],
                    },
                ],
                "last_year": 2026,
                "articles": data["articles"],
                "defaults": [{"articles": data["articles"]}],
                "authors": [],
            }
            data["lengths"] = [
                {
                    "group": "grade",
                    "grade": "FA",
                    "importance": None,
                    "pages": 3,
                    "measured": 2,
                    "median_characters": lower + 0.5,
                    "central_95_high": lower + 1,
                },
            ]
            for locale in ("en", "zh-hans", "zh-hant"):
                interface = Interface(locale)
                result = quality_analysis_context(data, interface)
                length = result["quality_analysis"]["lengths"][0]
                assert [point["value"] for point in length["quartiles"]] == [
                    lower + 0.25,
                    lower + 0.75,
                ]
                assert isclose(length["median_characters"], lower + 0.5)
                assert length["summary"] == interface.text(
                    "quality_analysis.violin_summary",
                    pages="3",
                    measured="2",
                    minimum=str(lower),
                    p25=str(lower),
                    p50=str(lower + 1),
                    p75=str(lower + 1),
                    maximum=str(lower + 1),
                )

    @staticmethod
    def test_five_number_summary_formats_zero_unknown_and_thousands() -> None:
        """Distinguish unknown extrema, exact zero and known lengths."""
        for value, label in (
            (None, "—"),
            (0, "0"),
            (654, "650"),
            (1234, "1,200"),
            (15412, "15,000"),
        ):
            measured = 654 if value is not None else 0
            data = analysis_fixture()
            data["selection"] = {
                "cells": [
                    {
                        "grade": "FA",
                        "importance": "High",
                        "year": 2026,
                        "histogram": [{"value": value, "count": measured}]
                        if value is not None
                        else [],
                    },
                ],
                "last_year": 2026,
                "articles": data["articles"],
                "defaults": [{"articles": data["articles"]}],
                "authors": [],
            }
            data["lengths"] = [
                {
                    "group": "grade",
                    "grade": "FA",
                    "importance": None,
                    "pages": 15412,
                    "measured": measured,
                    "median_characters": value,
                },
            ]
            for locale in ("en", "zh-hans", "zh-hant"):
                interface = Interface(locale)
                result = quality_analysis_context(data, interface)
                summary = result["quality_analysis"]["lengths"][0]["summary"]
                assert summary == interface.text(
                    "quality_analysis.violin_summary",
                    pages="15,412",
                    measured=str(measured),
                    minimum=label,
                    p25=label,
                    p50=label,
                    p75=label,
                    maximum=label,
                )


class QualityResourceHTTPTests(unittest.TestCase):
    """Serve saved generations without source reads or writes."""

    @staticmethod
    def test_get_head_etag_and_gzip_reuse() -> None:
        """Keep resource locale independent of request cookies."""
        content = json.dumps({"extra": ["saved article"] * 200})
        name = _resource(content)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            preset = quality_pages.path_for(
                root,
                "en",
                include_b=False,
                basis="current",
            )
            quality_pages.save(
                preset,
                "<main>Saved overview</main>",
                (1,),
                resources={name: content},
            )
            before = {
                path: path.stat().st_mtime_ns
                for path in root.rglob("*")
                if path.is_file()
            }
            client = TestClient(create_app(root))
            with patch(
                "webservice.quality_cache.encoded_response",
                wraps=encoded_response,
            ) as compress:
                response = client.get(
                    _PREFIX + name,
                    headers={
                        "Accept-Encoding": "gzip",
                        "Accept-Language": "zh-Hant",
                        "Cookie": "aranami-language=zh-hans",
                    },
                )
                assert response.status_code == HTTPStatus.OK
                assert response.text == content
                assert response.headers["Content-Encoding"] == "gzip"
                assert response.headers["Content-Language"] == "en"
                assert response.headers["Content-Type"].startswith(
                    "application/json",
                )
                assert "immutable" in response.headers["Cache-Control"]
                assert response.headers["Vary"] == "Accept-Encoding"
                assert "Set-Cookie" not in response.headers
                head = client.head(
                    _PREFIX + name,
                    headers={"Accept-Encoding": "gzip"},
                )
                assert head.status_code == HTTPStatus.OK
                assert not head.content
                assert (
                    head.headers["Content-Length"]
                    == response.headers["Content-Length"]
                )
                plain = client.get(
                    _PREFIX + name,
                    headers={"Accept-Encoding": "identity"},
                )
                assert plain.text == content
                assert "Content-Encoding" not in plain.headers
                unchanged = client.get(
                    _PREFIX + name,
                    headers={"If-None-Match": response.headers["ETag"]},
                )
                assert unchanged.status_code == HTTPStatus.NOT_MODIFIED
                assert not unchanged.content
                compress.assert_called_once()
            assert before == {
                path: path.stat().st_mtime_ns
                for path in root.rglob("*")
                if path.is_file()
            }
            assert (
                client.get("/quality-analysis/?lang=en").headers[
                    "Cache-Control"
                ]
                == "no-store"
            )
            wrong_locale = client.get(
                _PREFIX.replace("/en/", "/zh-hant/") + name,
            )
            assert wrong_locale.status_code == HTTPStatus.NOT_FOUND
            assert wrong_locale.headers["Cache-Control"] == "no-store"

    @staticmethod
    def test_previous_generation_survives_refresh_without_mixing_content() -> (
        None
    ):
        """Retain in-flight URLs and expire older cached entries."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = quality_pages.path_for(
                root,
                "en",
                include_b=False,
                basis="current",
            )
            client = TestClient(create_app(root))
            old = _resource('{"generation":1}')
            for generation in (1, 2, 2, 3):
                content = json.dumps(
                    {"generation": generation},
                    separators=(",", ":"),
                )
                name = _resource(content)
                quality_pages.save(
                    path,
                    f"<main>{generation}</main>",
                    (generation,),
                    resources={name: content},
                )
                response = client.get(_PREFIX + name)
                assert response.status_code == HTTPStatus.OK
                assert response.json() == {"generation": generation}
                previous = client.get(_PREFIX + old)
                if generation < 3:
                    assert previous.status_code == HTTPStatus.OK
                    assert previous.json() == {"generation": 1}
                else:
                    assert previous.status_code == HTTPStatus.NOT_FOUND
                    assert previous.headers["Cache-Control"] == "no-store"

    @staticmethod
    def test_invalid_digest_legacy_and_missing_resources_fail_closed() -> None:
        """Reject damaged resources without caching them."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = quality_pages.path_for(
                root,
                "en",
                include_b=False,
                basis="current",
            )
            client = TestClient(create_app(root))
            content = '{"saved":true}'
            name = _resource(content)
            quality_pages.save(path, "<main>Legacy</main>", (1,))
            assert quality_pages.read(path) == "<main>Legacy</main>"
            assert (
                client.get(_PREFIX + name).status_code == HTTPStatus.NOT_FOUND
            )
            quality_pages.save(
                path,
                "<main>Damaged resource</main>",
                (2,),
                resources={name: '{"saved":false}'},
            )
            response = client.get(_PREFIX + name)
            assert response.status_code == HTTPStatus.NOT_FOUND
            assert response.headers["Cache-Control"] == "no-store"
            for suffix in ("catalog-no-digest.json", name + "?lang=en"):
                assert (
                    client.get(_PREFIX + suffix).status_code
                    == HTTPStatus.NOT_FOUND
                )
