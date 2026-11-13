"""Verify language choices and assessment fallbacks."""

from __future__ import annotations

import re
import tempfile
import unittest
from html import unescape
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from starlette.responses import Response
from starlette.testclient import TestClient
from status_fixtures import status_snapshot

from shared.config import routine_configuration
from webservice.pages import (
    _Page,
    render_dashboard,
    render_reports,
)
from webservice.presentation import (
    Interface,
    article_extract,
    assessment_code,
    title_url,
)
from webservice.server import create_app

# Exact HTTP codes and authored labels define these contracts.
# ruff: file-ignore[magic-value-comparison, import-private-name]


class InterfaceLocaleTests(unittest.TestCase):
    """Keep explicit preferences independent of negotiated defaults."""

    def setUp(self) -> None:
        """Create an isolated application without live sources."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.client = TestClient(create_app(self.runtime))
        self.addCleanup(self.client.close)

    def test_query_cookie_and_header_precedence(self) -> None:
        """Persist a locale across routes and allow overrides."""
        for locale, tag in (
            ("en", "en"),
            ("zh-hans", "zh-Hans"),
            ("zh-hant", "zh-Hant-TW"),
        ):
            self.client.cookies.clear()
            response = self.client.get(
                f"/about/?lang={locale}",
                headers={"Accept-Language": "fr,zh-TW;q=0.8"},
            )
            assert response.status_code == 200
            assert response.headers["Content-Language"] == tag
            assert f'<html lang="{tag}">' in response.text
            cookie = response.headers["set-cookie"]
            assert "Max-Age=31536000" in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=lax" in cookie
            assert "Cookie" in response.headers["Vary"]
            for route in ("/", "/logs/", "/about/", "/api/live"):
                next_page = self.client.get(
                    route,
                    headers={"Accept-Language": "en"},
                )
                assert next_page.headers["Content-Language"] == (
                    "zh-Hant" if locale == "zh-hant" else tag
                )
            changed = self.client.get("/about/?lang=en")
            assert changed.headers["Content-Language"] == "en"
            assert (
                self.client.get("/logs/").headers["Content-Language"] == "en"
            )
        self.client.cookies.clear()
        negotiated = self.client.get(
            "/about/",
            headers={"Accept-Language": "zh-TW"},
        )
        assert negotiated.headers["Content-Language"] == "zh-Hant-TW"
        assert "set-cookie" not in negotiated.headers

    def test_regional_chinese_keeps_native_ui_and_converts_content(
        self,
    ) -> None:
        """Retain Taiwan and Hong Kong rules behind script choices."""
        for browser, tag, variant, expected in (
            ("zh-Hant-TW", "zh-Hant-TW", "zh-tw", "裡"),
            ("zh-Hant-HK", "zh-Hant-HK", "zh-hk", "裏"),
            ("zh-HK", "zh-Hant-HK", "zh-hk", "裏"),
        ):
            with self.subTest(browser=browser):
                self.client.cookies.clear()
                page = self.client.get(
                    "/about/?lang=zh-hant",
                    headers={"Accept-Language": browser},
                )
                assert page.headers["Content-Language"] == tag
                assert re.search(
                    r'href="/assets/icons/report-icons\.svg\?v=[a-f0-9]{64}#language"',
                    page.text,
                )
                assert "繁体中文" in page.text
                language, text = article_extract(
                    {"extract_hant": "裏"},
                    variant,
                )
                assert language == variant
                assert text == expected
                saved = {
                    "extract_hant": "通用文字",
                    "extract_tw": "臺灣用語",
                    "extract_hk": "香港用語",
                }
                assert article_extract(saved, variant)[1] == (
                    "臺灣用語" if variant == "zh-tw" else "香港用語"
                )

    def test_language_links_retain_route_and_query(self) -> None:
        """Keep filters and page number when changing script."""
        params = (
            "task=references&sort=title&order=desc&page=2&q=Game&quality=B"
        )
        with patch(
            "webservice.server._PublicViews._maintenance_page",
        ) as render:
            # Obtain navigation without reading runtime data.
            render.return_value = Response("fixture")
            self.client.get("/maintenance/?" + params + "&lang=en")
            navigation = render.call_args.args[3]
        assert navigation["path"] == "/maintenance/"
        for link in navigation["languages"]:
            target = urlsplit(link["url"])
            assert target.path == "/maintenance/"
            values = parse_qs(target.query)
            assert values["page"] == ["2"]
            assert values["q"] == ["Game"]
            assert values["quality"] == ["B"]
            assert values["order"] == ["desc"]
            assert len(values["lang"]) == 1
        output = self.client.get("/about/?path=unused&lang=zh-Hant").text
        links = re.findall(
            r'<a href="([^"]+)" lang="(?:en|zh-Hans|zh-Hant)"',
            output,
        )
        assert len(links) == 3
        assert all(
            urlsplit(unescape(link)).path == "/about/" for link in links
        )
        assert 'aria-current="page">' in output

    def test_invalid_choices_do_not_poison_preferences(self) -> None:
        """Ignore unsupported or repeated explicit language tags."""
        for query in (
            "lang=invalid",
            "lang=en&lang=zh-hant",
            "lang=%3Cscript%3E",
        ):
            response = self.client.get(
                "/about/?" + query,
                headers={"Accept-Language": "zh-CN"},
            )
            assert response.headers["Content-Language"] == "zh-Hans"
            assert "set-cookie" not in response.headers
        assert not any(self.runtime.iterdir())


class AssessmentFallbackTests(unittest.TestCase):
    """Translate grades while retaining Chinese source titles."""

    @staticmethod
    def test_publication_content_shows_only_its_destination() -> None:
        """Keep page titles free of redundant task subtitles."""
        definitions = routine_configuration()["routines"]
        template = status_snapshot()["publications"]["target"]
        records = {
            title: {**template, "task_id": routine, "title": title}
            for routine in ("assessment_lists", "enwp_key_articles")
            for title in definitions[routine]["pages"].values()
        }
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            empty = render_reports({}, locale)
            assert interface.text("observability.no_publications") in empty
            output = render_reports({"publications": records}, locale)
            for routine in ("assessment_lists", "enwp_key_articles"):
                publications = output.split(
                    f'<dialog id="report-details-{routine}"',
                )[1].split("</dialog>")[0]
                for title in definitions[routine]["pages"].values():
                    assert (
                        publications.count(
                            f'href="{title_url(title)}"',
                        )
                        == 1
                    )
                    assert f">{title}</a>" in publications
                assert "<small" not in publications.split("</table>")[0]

    @staticmethod
    def test_fallbacks_and_aliases_in_all_locales() -> None:
        """Distinguish absent quality from unrecognized saved values."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            for value, code in (
                (None, "Unassessed"),
                ("未评", "Unassessed"),
                ("未知", "Unknown"),
                ("new grade", "Unknown"),
            ):
                snapshot = {
                    "homepage": {
                        "items": [
                            {
                                "title": "源条目",
                                "url": "https://zh.wikipedia.org/wiki/Test",
                                "assessment_class": value,
                                "assessment_importance": "未知",
                                "kind": "ga",
                                "date": None,
                                "extract": "",
                            },
                        ],
                    },
                }
                # Exercise the normal article context.
                card = _Page(locale).article(snapshot["homepage"]["items"][0])
                assert card["assessments"][0]["label"] == interface.text(
                    f"assessment.class.{code}",
                )
                assert card["assessments"][1]["label"] == interface.text(
                    "assessment.importance.Unknown",
                )
                expected_title = "源條目" if locale == "zh-hant" else "源条目"
                assert expected_title in str(card["link"])
            assert assessment_code("未評", "class") == "Unassessed"
            assert assessment_code("未知", "importance") == "Unknown"
            output = render_dashboard(None, None, None, locale)
            assert interface.text("nav.skip") in output
            assert 'class="assessment-guide"' not in output
