"""Verify the merged quality page and former author-page redirect."""

# Query counts are explicit cache-identity fixtures.

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from starlette.testclient import TestClient
from test_quality_analysis import analysis_fixture

from data.articles import quality_pages
from webservice.presentation import Interface
from webservice.quality_pages import prepare_quality_pages
from webservice.server import create_app


class QualityAuthorshipHTTPTests(TestCase):
    """Preserve bookmarked controls on one current-cohort page."""

    @staticmethod
    def test_legacy_redirect_preserves_controls_without_reads() -> None:
        """Redirect old links before loading or calculating evidence."""
        with (
            TemporaryDirectory() as directory,
            patch("webservice.server.quality_pages.read") as read,
        ):
            runtime = Path(directory) / "absent"
            client = TestClient(create_app(runtime))
            for locale in ("en", "zh-hans", "zh-hant"):
                response = client.get(
                    f"/quality-authorship/?lang={locale}&count_b=1",
                    follow_redirects=False,
                )
                assert response.status_code == HTTPStatus.MOVED_PERMANENTLY
                target = urlsplit(response.headers["Location"])
                assert target.path == "/quality-analysis/"
                assert parse_qs(target.query) == {
                    "lang": [locale],
                    "count_b": ["1"],
                }
            read.assert_not_called()
            assert not runtime.exists()

    @staticmethod
    def test_invalid_filters_do_not_read_evidence() -> None:
        """Reject invalid controls before loading saved evidence."""
        with (
            TemporaryDirectory() as directory,
            patch("webservice.server.quality_pages.read") as read,
        ):
            client = TestClient(create_app(Path(directory)))
            for route in ("quality-authorship", "quality-analysis"):
                for query in (
                    "user=Someone",
                    "count_b=",
                    "count_b=yes",
                    "count_b=1&count_b=0",
                ):
                    response = client.get(f"/{route}/?{query}")
                    assert response.status_code == HTTPStatus.BAD_REQUEST
            read.assert_not_called()

    @staticmethod
    def test_global_toggle_selects_a_saved_cohort() -> None:
        """Use one saved variant for all three sections."""

        def saved(
            _runtime: Path,
            *,
            include_b: bool,
            authorship_basis: str,
            all_grades: bool = False,
        ) -> dict:
            """Provide a prepared cohort.

            Returns:
                Saved analysis and an observed empty author directory.
            """
            assert authorship_basis in {"current", "listed"}
            return {
                "include_b": include_b,
                "analysis": {
                    **analysis_fixture(),
                    "include_b": include_b,
                    "all_grades": all_grades,
                },
                "authorship": {
                    "available": True,
                    "authors": [],
                    "include_b": include_b,
                },
            }

        with (
            TemporaryDirectory() as directory,
            patch(
                "webservice.quality_pages.read_current_quality",
                side_effect=saved,
            ),
            patch(
                "webservice.quality_pages.current_quality_identity",
                return_value=(1,),
            ),
        ):
            runtime = Path(directory)
            prepare_quality_pages(runtime)
            client = TestClient(create_app(runtime))
            for query, included in (
                ("", False),
                ("?count_b=0", False),
                ("?count_b=1", True),
            ):
                response = client.get("/quality-analysis/" + query)
                assert response.status_code == HTTPStatus.OK
                assert (
                    'name="count_b" value="1" checked' in response.text
                ) == included
                assert 'id="analysis-overview"' in response.text
                assert 'id="analysis-articles"' in response.text
                assert 'id="quality-authors"' in response.text
                if included:
                    assert "count_b=1&amp;lang=zh-hans" in response.text
            assert quality_pages.path_for(
                runtime,
                "zh-hans",
                include_b=True,
                basis="current",
            ).is_file()

    @staticmethod
    def test_corrupt_saved_variant_is_unavailable() -> None:
        """Preserve the unavailable state for corrupt saved data."""
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

    @staticmethod
    def test_post_is_rejected_and_head_keeps_redirect_without_a_body() -> None:
        """Keep both routes limited to safe read methods."""
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(Path(directory)))
            for route in ("quality-analysis", "quality-authorship"):
                assert (
                    client.post(f"/{route}/").status_code
                    == HTTPStatus.METHOD_NOT_ALLOWED
                )
            response = client.head(
                "/quality-authorship/?lang=en",
                follow_redirects=False,
            )
            assert response.status_code == HTTPStatus.MOVED_PERMANENTLY
            assert not response.content
