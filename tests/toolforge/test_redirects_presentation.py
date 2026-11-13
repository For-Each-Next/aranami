"""Verify redirect evidence, paging and read-only HTTP."""

# Keep unittest assertions for this repository's offline runner.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import tempfile
from http import HTTPStatus
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from redirect_fixtures import redirect_snapshot
from starlette.testclient import TestClient

from webservice.pages import render_redirects
from webservice.presentation import Interface
from webservice.redirects import redirect_selection
from webservice.server import create_app


class TestRedirectPresentation(TestCase):
    """Retain exact revisions and distinguish incomplete saved scans."""

    @staticmethod
    def test_localized_evidence_and_escaped_previews() -> None:
        """Link evidence revisions and escape source text."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = render_redirects(redirect_snapshot(), {}, locale)
            assert interface.text("redirects.heading") in output
            assert interface.text("redirects.status.scanning") in output
            assert (
                'data-banner-aliases=\'["Template:WikiProject Video games", '
                '"Template:VG"]\''
            ) in output
            assert 'data-talk-id="101"' in output
            assert interface.text("redirects.sampled_preview") in output
            assert (
                '<strong><a href="https://zh.wikipedia.org/w/index.php?'
                f'oldid=2001&amp;wpvg-assessor=1">{interface.text("redirects.sampled_preview")}'
                "</a>:</strong>"
            ) in output
            assert (
                'href="https://zh.wikipedia.org/wiki/Game_1?redirect=no&amp;wpvg-assessor=1"'
            ) in output
            for revision in (1001, 2001, 3001):
                assert (
                    f'href="https://zh.wikipedia.org/w/index.php?oldid={revision}"'
                ) in output
            assert "&lt;script&gt;never execute&lt;/script&gt;" in output
            assert "<script>never execute</script>" not in output
            assert output.count('data-redirect-id="') == len(
                redirect_selection(redirect_snapshot(), {}, locale)["items"],
            )
            assert 'rel="next"' in output
            assert 'method="get"' in output

    @staticmethod
    def test_paging_retains_all_saved_evidence() -> None:
        """Select twenty-row pages without losing the remainder."""
        data = redirect_snapshot()
        selection = redirect_selection(
            data,
            {"page": ["2"], "limit": ["20"]},
            "zh-hant",
        )
        assert [row["page_id"] for row in selection["items"]] == [21, 22, 23]
        assert selection["following"] is None
        assert "lang=zh-hant" in selection["previous"]

    @staticmethod
    def test_assessor_link_preserves_existing_query_and_fragment() -> None:
        """Preserve URL parameters when adding the gadget switch."""
        data = redirect_snapshot(1)
        data["items"][0]["url"] = (
            "https://zh.wikipedia.org/w/index.php?title=Game&redirect=no#section"
        )
        selection = redirect_selection(data, {}, "en")
        assert selection["items"][0]["assessor_url"] == (
            "https://zh.wikipedia.org/w/index.php?"
            "title=Game&redirect=no&wpvg-assessor=1#section"
        )
        assert "wpvg-assessor" not in data["items"][0]["url"]

    @staticmethod
    def test_unresolved_history_preserves_positive_banner_evidence() -> None:
        """Keep proven banners visible without article text."""
        data = redirect_snapshot(1)
        data["items"][0].update(
            content_revision=None,
            content_url=None,
            content_timestamp=None,
            preview="",
        )
        output = render_redirects(data, {}, "en")
        interface = Interface("en")
        assert interface.text("redirects.incomplete", count=1) in output
        assert interface.text("redirects.no_preview") in output
        assert (
            'href="https://zh.wikipedia.org/w/index.php?oldid=3001"'
        ) in output
        assert "Revision None" not in output

    @staticmethod
    def test_missing_unavailable_and_empty_are_distinct() -> None:
        """Never present unreadable data as a completed empty search."""
        interface = Interface("en")
        missing = render_redirects(None, {}, "en")
        unavailable = render_redirects(None, {}, "en", unavailable=True)
        empty = render_redirects(redirect_snapshot(0), {}, "en")
        assert interface.text("redirects.status.missing") in missing
        assert interface.text("redirects.status.unavailable") in unavailable
        assert interface.text("redirects.empty") not in unavailable
        assert interface.text("redirects.empty") in empty

    def test_invalid_selections_are_rejected(self) -> None:
        """Bound pagination and reject repeat or unrelated controls."""
        for parameters in (
            {"page": ["0"]},
            {"limit": ["50"]},
            {"page": ["1", "2"]},
            {"q": ["Game"]},
            {"page": ["\uff19"]},
        ):
            with (
                self.subTest(parameters=parameters),
                self.assertRaises(ValueError),
            ):
                redirect_selection(redirect_snapshot(), parameters, "en")

    @staticmethod
    def test_http_reads_saved_results_on_each_poll() -> None:
        """Read new checkpoints without writing or source clients."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("webservice.server.read_redirects") as read,
            TestClient(create_app(Path(directory))) as client,
        ):
            read.return_value = redirect_snapshot(1)
            first = client.get("/redirects/?lang=en")
            read.return_value = redirect_snapshot(2)
            second = client.get("/redirects/?lang=en")
            assert first.status_code == second.status_code == HTTPStatus.OK
            assert 'data-redirect-id="2"' not in first.text
            assert 'data-redirect-id="2"' in second.text
            assert second.headers["cache-control"] == "no-store"
            assert (
                "https://zh.wikipedia.org"
                in second.headers["content-security-policy"]
            )
            assert (
                "https://zh.wikipedia.org"
                not in client.get("/").headers["content-security-policy"]
            )
            assert (
                client.post("/redirects/").status_code
                == HTTPStatus.METHOD_NOT_ALLOWED
            )
            assert (
                client.get("/redirects/?page=0").status_code
                == HTTPStatus.BAD_REQUEST
            )
            assert client.head("/redirects/").content == b""
