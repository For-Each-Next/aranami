"""Verify quality charts, native rows and read-only HTTP."""

# Fixture identities, coverage and percentile endpoints are explicit.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt
import unittest
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import polars as pl
from starlette.testclient import TestClient

from data.articles import current_quality, quality_analysis as store
from shared.assessment_profiles import assessment_profile
from webservice.pages import render_quality_analysis
from webservice.presentation import Interface
from webservice.quality_analysis import quality_analysis_context
from webservice.quality_pages import prepare_quality_pages
from webservice.server import create_app

_NOW = dt.datetime(2026, 11, 13, 12, tzinfo=dt.UTC)


def analysis_fixture() -> dict:
    """Provide saved summaries and a complete list with unknown values.

    Returns:
        One current cohort with known, zero and pending measurements.
    """
    summary = {
        "pages": 2,
        "measured": 1,
        "dated": 1,
        "median_characters": 1000.0,
        "central_68_low": 1000.0,
        "central_68_high": 1000.0,
        "central_95_low": 1000.0,
        "central_95_high": 1000.0,
    }
    return {
        "available": True,
        "observed_at": _NOW.isoformat(),
        "coverage": {
            "total": 3,
            "measured": 2,
            "dated": 2,
            "median_characters": 500,
        },
        "grade_totals": [
            {"grade": "FA", **summary},
            {"grade": "Bplus", **summary, "pages": 1},
        ],
        "importance_available": True,
        "importance_totals": [
            {"importance": code, "pages": 1, "measured": 1, "dated": 1}
            for code in ("High", "Low", "Unknown")
        ],
        "year_totals": [
            {"year": 2026, "grade": "FA", "pages": 1},
            {"year": 2025, "grade": "Bplus", "pages": 1},
        ],
        "years": [
            {"year": 2026, "pages": 1, "grades": {"FA": 1}},
            {"year": 2025, "pages": 1, "grades": {"Bplus": 1}},
        ],
        "lengths_available": True,
        "lengths": [
            {"group": "grade", "grade": grade, "importance": None, **summary}
            for grade in ("FA", "GA", "Bplus")
        ]
        + [
            {
                "group": "importance",
                "grade": None,
                "importance": importance,
                **summary,
            }
            for importance in ("Top", "High", "Mid", "Low")
        ],
        "articles": [
            {
                "page_id": index,
                "title": title,
                "grade": grade,
                "importance": importance,
                "listed_date": date,
                "listed_oldid": 456 if date else None,
                "source_bytes": 12345,
                "prose_words": words,
                "prose_characters": words,
                "prose_bytes": words * 4 if words is not None else None,
                "article_revision_id": 123 + index,
                "talk_revision_id": 456 + index,
                "measured_at": _NOW.isoformat() if words is not None else None,
            }
            for index, title, grade, importance, date, words in (
                (
                    1,
                    'Game <script> & "name"',
                    "FA",
                    "High",
                    "2026-01-01",
                    1000,
                ),
                (2, "Pending 遊戲", "FA", "Unknown", None, None),
                (3, "Bplus list", "Bplus", "Low", "2025-12-31", 0),
            )
        ],
    }


class QualityAnalysisPresentationTests(unittest.TestCase):
    """Preserve coverage and the complete native article list."""

    @staticmethod
    def test_display_and_listing_links_keep_separate_revision_provenance() -> (
        None
    ):
        """Keep display, listing oldid and proof ranges distinct."""
        source = analysis_fixture()
        article = source["articles"][0]
        article.update(
            title="PlayStation_5",
            listed_review_link="#Review",
            authors=["Main author"],
            authorship_available=True,
            authorship_start_revision=11,
            authorship_end_revision=22,
            authorship_start_timestamp="2000-01-01T00:00:00Z",
            authorship_end_timestamp="2026-01-01T23:59:59Z",
            authorship_eligible_bytes=100,
            authorship_editors=[
                {
                    "editor": "1",
                    "name": "Main author",
                    "share": 0.6,
                    "bytes": 60,
                },
                {
                    "editor": "0|192.0.2.1",
                    "name": None,
                    "share": 0.2,
                    "bytes": 20,
                },
                {"editor": "2", "name": "Boundary", "share": 0.1, "bytes": 10},
                {"editor": "3", "name": "Other", "share": 0.1, "bytes": 10},
            ],
        )
        row = quality_analysis_context(source, Interface("en"))[
            "quality_analysis"
        ]["articles"][0]
        assert row["title_label"] == "PlayStation 5"
        assert "PlayStation_5" in row["url"]
        assert row["listing_url"].endswith("oldid=456")
        assert row["review_url"].endswith("#Review")
        proof = row["authorship"]
        assert proof["range_start"]["url"].endswith("oldid=11")
        assert proof["range_end"]["url"].endswith("oldid=22")
        assert [editor["name"] for editor in proof["editors"]] == [
            "Main author",
            "192.0.2.1",
            "Boundary",
            "Other",
        ]
        assert (
            "Special%3AContributions%2F192.0.2.1" in proof["editors"][1]["url"]
        )

    @staticmethod
    def test_word_only_measurements_are_not_presented_as_characters() -> None:
        """Keep missing characters distinct from word evidence."""
        source = analysis_fixture()
        source["articles"][0].pop("prose_characters")
        source["coverage"].pop("median_characters")
        source["coverage"]["median_words"] = 9876
        result = quality_analysis_context(source, Interface("en"))[
            "quality_analysis"
        ]
        assert result["median"] == "—"
        assert result["articles"][0]["characters_label"] == "—"

    @staticmethod
    def test_context_uses_precomputed_totals_and_retains_unknowns() -> None:
        """Keep zero, missing data and listing evidence distinct."""
        source = analysis_fixture()
        source["coverage"]["median_characters"] = 9876
        context = quality_analysis_context(source, Interface("en"))[
            "quality_analysis"
        ]
        assert context["median"] == "9,876"
        assert context["articles"][1]["characters_label"] == "—"
        assert context["articles"][2]["characters_label"] == "0"
        assert context["articles"][1]["date_label"] is None
        assert context["articles"][0]["listing_url"].endswith("oldid=456")
        assert context["articles"][0]["source_url"].endswith("oldid=124")
        source["articles"][0]["listed_oldid"] = 999999
        evidence = quality_analysis_context(source, Interface("en"))[
            "quality_analysis"
        ]["articles"][0]
        assert evidence["listing_url"].endswith("oldid=999999")
        source["articles"][0]["listed_oldid"] = None
        assert (
            quality_analysis_context(source, Interface("en"))[
                "quality_analysis"
            ]["articles"][0]["listing_url"]
            is None
        )
        assert context["years"][0]["segments"][0]["grade"] == "Bplus"

    @staticmethod
    def test_localized_native_rows_and_charts_are_complete_and_escaped() -> (
        None
    ):
        """Render every source row and Bplus without scripting."""
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_quality_analysis(
                analysis_fixture(),
                None,
                None,
                locale,
                _NOW,
            )
            assert Interface(locale).text("quality_analysis.heading") in output
            assert output.count("data-analysis-row ") == 3
            assert 'id="analysis-method"' not in output
            assert 'href="#analysis-method"' not in output
            assert 'data-grade="Bplus"' in output
            assert "data-analysis-row hidden" not in output
            assert "Game &lt;script&gt;" in output
            assert "Game <script>" not in output
            assert 'data-sort="0" class="analysis-number">0' in output
            assert 'role="img"' in output
            assert 'href="/quality-analysis/"' in output
            assert "quality_analysis.js" in output
            assert "oldid=94091952" not in output
            assert "sandbox analysis" not in output
            assert output.count('data-analysis-pie="') == 2
            assert "data-analysis-filter-note" not in output

    @staticmethod
    def test_vertical_columns_and_inline_prose_distributions() -> None:
        """Render stacks and pooled strata from saved statistics."""
        source = analysis_fixture()
        context = quality_analysis_context(source, Interface("en"))[
            "quality_analysis"
        ]
        assert [row["year"] for row in context["years"]] == [2025, 2026]
        for row in context["years"]:
            assert row["segments"][0]["height"] > 0
            assert row["segments"][0]["y"] < 248
        assert [row["grade"] for row in context["grade_lengths"]] == [
            "FA",
            "GA",
            "Bplus",
        ]
        assert [
            row["importance"] for row in context["importance_lengths"]
        ] == [
            "Top",
            "High",
            "Mid",
            "Low",
        ]
        output = render_quality_analysis(source, None, None, "en", _NOW)
        assert 'class="analysis-year-columns"' in output
        assert output.count("data-analysis-year=") == 2
        assert output.count('class="analysis-mini-violin"') == 4
        assert 'id="analysis-lengths"' not in output
        assert "PlayStation 5" not in output
        assert 'data-analysis-violin="FL"' not in output
        source.update(lengths=[], lengths_available=False)
        pending = render_quality_analysis(source, None, None, "en", _NOW)
        assert 'class="analysis-mini-violin"' not in pending
        assert pending.count("data-analysis-row ") == 3
        assert 'class="analysis-year-columns"' in pending

    @staticmethod
    def test_pies_use_full_counts_and_default_class_palette() -> None:
        """Preserve circular geometry and default class colors."""
        source = analysis_fixture()
        context = quality_analysis_context(source, Interface("en"))[
            "quality_analysis"
        ]
        profile = assessment_profile("zhwiki", "电子游戏")
        assert context["grades"][0]["share_label"] == "66.7%"
        assert context["grades"][0]["path"].startswith("M 130 130 L ")
        assert context["grades"][0]["color"] == profile.color("FA", "class")
        assert context["grades"][0]["color"] != profile.color(
            "FA",
            "class",
            variant="analysis",
        )
        assert context["years"][1]["segments"][0]["color"] == profile.color(
            "FA",
            "class",
        )
        assert [row["importance"] for row in context["importance_totals"]] == [
            "High",
            "Low",
            "Unknown",
        ]
        assert context["articles"][0]["grade_color"] == profile.color(
            "FA",
            "class",
        )
        source["grade_totals"] = [{"grade": "FA", "pages": 3}]
        full = quality_analysis_context(source, Interface("en"))[
            "quality_analysis"
        ]["grades"][0]
        assert full["whole"]
        assert full["path"] is None
        source.update(importance_totals=[], importance_available=False)
        output = render_quality_analysis(source, None, None, "en", _NOW)
        assert (
            Interface("en").text("quality_analysis.importance_pending")
            in output
        )
        assert (
            '<circle cx="130" cy="130" r="112" class="analysis-pie-slice"'
            in output
        )
        assert output.count("data-analysis-row ") == 3

    @staticmethod
    def test_unavailable_and_empty_states_without_method_content() -> None:
        """Distinguish missing analysis from an empty cohort."""
        missing = render_quality_analysis(
            {"available": False},
            None,
            None,
            "en",
            _NOW,
        )
        assert Interface("en").text("quality_analysis.unavailable") in missing
        assert 'href="#analysis-overview"' in missing
        assert 'id="analysis-method"' not in missing
        empty = render_quality_analysis(
            {"available": True},
            None,
            None,
            "en",
            _NOW,
        )
        assert 'class="analysis-mini-violin"' not in empty
        assert 'id="analysis-lengths"' not in empty
        assert 'id="analysis-articles"' in empty
        assert "data-analysis-row " not in empty


class QualityAnalysisHTTPTests(unittest.TestCase):
    """Read immutable report generations without source acquisition."""

    @staticmethod
    def test_missing_store_does_not_create_runtime_files() -> None:
        """Serve an unpopulated report without triggering collection."""
        with TemporaryDirectory() as directory:
            root = Path(directory) / "absent"
            client = TestClient(create_app(root))
            for locale in ("en", "zh-hans", "zh-hant"):
                response = client.get(f"/quality-analysis/?lang={locale}")
                assert response.status_code == HTTPStatus.OK
                assert (
                    Interface(locale).text("quality_analysis.unavailable")
                    in response.text
                )
            assert not root.exists()

    @staticmethod
    def test_real_parquet_generation_invalidates_html_without_writes() -> None:
        """Read saved generations without grouping or lock writes."""
        rows = [
            {
                **source,
                "talk_page_id": source["page_id"] + 100,
                "listed_date": dt.date.fromisoformat(source["listed_date"])
                if source["listed_date"]
                else None,
                "observed_at": _NOW,
                "measurement_contract": "http-fixture:1",
                "measured_at": _NOW
                if source["prose_words"] is not None
                else None,
                "attempted_at": _NOW,
            }
            for source in analysis_fixture()["articles"]
        ]
        frame = pl.DataFrame(rows, schema=store.ARTICLE_SCHEMA)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store.checkpoint(
                root,
                members=frame,
                updates=frame,
                observed_at=_NOW,
            )
            assert current_quality.refresh_saved_quality(root)
            prepare_quality_pages(root)
            client = TestClient(create_app(root))
            with patch(
                "data.articles.quality_analysis._summaries",
                side_effect=AssertionError("HTTP must not aggregate"),
            ):
                before = {
                    path: path.stat().st_mtime_ns
                    for path in root.rglob("*")
                    if path.is_file()
                }
                response = client.get("/quality-analysis/?lang=en")
                assert response.status_code == HTTPStatus.OK
                assert 'data-grade="Bplus"' in response.text
                assert before == {
                    path: path.stat().st_mtime_ns
                    for path in root.rglob("*")
                    if path.is_file()
                }
            changed = frame.with_columns(
                pl.lit("Updated analysis title").alias("title"),
                pl.lit(_NOW + dt.timedelta(minutes=1)).alias("observed_at"),
            )
            store.checkpoint(
                root,
                members=changed,
                observed_at=_NOW + dt.timedelta(minutes=1),
            )
            assert current_quality.refresh_saved_quality(root)
            prepare_quality_pages(root)
            response = client.get("/quality-analysis/?lang=en")
            assert "Updated analysis title" in response.text

    @staticmethod
    def test_cached_generation_reuse_and_invalidation() -> None:
        """Retain bounded HTML without source bodies or DataFrames."""
        with (
            TemporaryDirectory() as directory,
            patch(
                "webservice.server.quality_pages.identity",
                return_value=(1,),
            ) as identity,
            patch(
                "webservice.server.quality_pages.read",
                return_value=render_quality_analysis(
                    analysis_fixture(),
                    None,
                    None,
                    "en",
                    _NOW,
                ),
            ) as read,
            patch(
                "webservice.server.pages.render_quality_analysis",
                side_effect=AssertionError(
                    "HTTP must not render a prepared cohort",
                ),
            ),
        ):
            client = TestClient(create_app(Path(directory)))
            for _ in range(2):
                assert (
                    client.get("/quality-analysis/?lang=en").status_code
                    == HTTPStatus.OK
                )
            read.assert_called_once()
            identity.return_value = (2,)
            assert (
                client.get("/quality-analysis/?lang=en").status_code
                == HTTPStatus.OK
            )
            assert read.call_count == 2
            response = client.get("/quality-analysis/?unexpected=1")
            assert response.status_code == HTTPStatus.BAD_REQUEST
            assert read.call_count == 2

    @staticmethod
    def test_corrupt_data_is_unavailable_and_request_is_read_only() -> None:
        """Keep the route available despite damaged saved data."""
        with (
            TemporaryDirectory() as directory,
            patch(
                "webservice.server.quality_pages.read",
                side_effect=ValueError("damaged"),
            ),
        ):
            response = TestClient(create_app(Path(directory))).get(
                "/quality-analysis/?lang=en",
            )
            assert response.status_code == HTTPStatus.OK
            assert (
                Interface("en").text("quality_analysis.unavailable")
                in response.text
            )
