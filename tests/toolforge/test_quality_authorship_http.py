"""Verify quality authorship stays read-only over HTTP."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient

from webservice.presentation import Interface
from webservice.server import create_app


class QualityAuthorshipHTTPTests(TestCase):
    """Keep source acquisition outside author page requests."""

    @staticmethod
    def test_missing_saved_data_is_unavailable_without_writes() -> None:
        """Serve all locales without creating an absent runtime."""
        with TemporaryDirectory() as directory:
            runtime = Path(directory) / "absent"
            client = TestClient(create_app(runtime))
            for locale in ("en", "zh-hans", "zh-hant"):
                response = client.get(f"/quality-authorship/?lang={locale}")
                assert response.status_code == HTTPStatus.OK
                assert (
                    Interface(locale).text(
                        "quality_authorship.heading",
                    )
                    in response.text
                )
                assert 'href="/quality-authorship/"' in response.text
                assert not runtime.exists()

    @staticmethod
    def test_invalid_filters_do_not_read_evidence() -> None:
        """Reject unsupported controls before reading saved data."""
        with (
            TemporaryDirectory() as directory,
            patch("webservice.server.read_quality_authors") as read,
        ):
            client = TestClient(create_app(Path(directory)))
            response = client.get("/quality-authorship/?user=Someone")
            assert response.status_code == HTTPStatus.BAD_REQUEST
            read.assert_not_called()

    @staticmethod
    def test_b_toggle_uses_saved_data_and_preserves_language_links() -> None:
        """Keep native GET state without HTTP source acquisition."""
        with (
            TemporaryDirectory() as directory,
            patch(
                "webservice.server.read_quality_authors",
                return_value={"available": True, "authors": []},
            ) as read,
        ):
            runtime = Path(directory)
            client = TestClient(create_app(runtime))
            for query, included in (
                ("", False),
                ("?count_b=0", False),
                ("?count_b=1", True),
            ):
                response = client.get("/quality-authorship/" + query)
                assert response.status_code == HTTPStatus.OK
                read.assert_called_with(runtime, include_b=included)
                assert (
                    'name="count_b" value="1" checked' in response.text
                ) == included
                if included:
                    assert "count_b=1&amp;lang=zh-hans" in response.text
            read.reset_mock()
            for query in ("count_b=", "count_b=yes", "count_b=1&count_b=0"):
                response = client.get("/quality-authorship/?" + query)
                assert response.status_code == HTTPStatus.BAD_REQUEST
            read.assert_not_called()

    @staticmethod
    def test_unreadable_evidence_keeps_the_page_available() -> None:
        """Keep corrupt saved files distinct from an empty result."""
        with (
            TemporaryDirectory() as directory,
            patch(
                "webservice.server.read_quality_authors",
                side_effect=ValueError("damaged saved evidence"),
            ),
        ):
            response = TestClient(create_app(Path(directory))).get(
                "/quality-authorship/?lang=en",
            )
            assert response.status_code == HTTPStatus.OK
            assert (
                Interface("en").text(
                    "quality_authorship.unavailable",
                )
                in response.text
            )

    @staticmethod
    def test_post_is_rejected_and_head_has_no_body() -> None:
        """Expose the tool through the existing safe read methods."""
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(Path(directory)))
            assert (
                client.post(
                    "/quality-authorship/",
                ).status_code
                == HTTPStatus.METHOD_NOT_ALLOWED
            )
            response = client.head("/quality-authorship/?lang=en")
            assert response.status_code == HTTPStatus.OK
            assert not response.content
