"""Verify attribution cards and licenses in the shared page layout."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from unittest import TestCase
from urllib.parse import quote

from webservice.pages import render_attribution
from webservice.presentation import Interface

_ROOT = Path(__file__).resolve().parents[2]
_PATHS = {
    "content": "/assets/icons/credits.html",
    "maintenance": "/assets/icons/maintenance/credits.html",
    "codex": "/codex-license.html",
    "gpl": "/assets/icons/gpl-2.0.html",
    "lgpl": "/assets/icons/lgpl-2.1.html",
}


class TestAttributionPresentation(TestCase):
    """Keep credit details usable without duplicating the site shell."""

    @staticmethod
    def test_every_document_uses_localized_navigation_and_shared_layout() -> (
        None
    ):
        """Keep public URLs inside the dashboard page structure."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            for name, path in _PATHS.items():
                output = render_attribution(path, locale)
                assert interface.text(f"credits.title.{name}") in output
                assert 'class="contents-panel"' in output
                assert 'class="global-navigation"' in output
                assert 'class="footer-links"' in output
                assert output.count("<h1>") == 1
                assert 'href="/about/#credits"' in output
                assert "/assets/images/aranami.svg" not in output

    @staticmethod
    def test_credit_cards_keep_authors_sources_and_thumbnail_art() -> None:
        """Display every credited image with all attribution fields."""
        for kind, count in (("content", 13), ("maintenance", 23)):
            output = render_attribution(_PATHS[kind], "en")
            assert output.count('class="ui-card attribution-card"') == count
            assert output.count('class="attribution-details ') == count
            assert output.count('attribution-thumbnail">') == count
            assert "Original file download" in output
            assert "LGPL-2.1-or-later" in output
            assert 'src="/assets/icons/' in output
            assert 'class="credit-table"' not in output

    @staticmethod
    def test_maintenance_categories_match_configured_task_rules() -> None:
        """Link the configured categories for every template task."""
        output = render_attribution(_PATHS["maintenance"], "en")
        config = json.loads(
            (_ROOT / "src/tasks/website/maintenance.json").read_text(),
        )
        tasks = {
            task["key"]: task
            for section in config["sections"]
            for task in section["tasks"]
        }
        rows = re.findall(
            r'<tr><th scope="row"><code>([^<]+)</code></th>(.*?)</tr>',
            output,
            re.DOTALL,
        )
        assert rows
        for key, row in rows:
            for rule in tasks[key]["rules"]:
                if rule["kind"] == "category":
                    category = quote(
                        "Category:" + rule["name"].replace(" ", "_"),
                        safe=":",
                    )
                    assert f'/wiki/{category}"' in row

    @staticmethod
    def test_original_license_bodies_remain_unchanged() -> None:
        """Preserve the GNU and MIT license text exactly."""
        digests = {
            "codex": (
                "10aeeaa8da381d0bd3b7a64a9f29d478"
                "7d7d6a6a968d85c7db7e89b48c9733e8"
            ),
            "gpl": (
                "ea31bcca899c5e1d8e33d3ada5b2e6fb"
                "251aa8b41dbd88097f6fc3c0dd66be29"
            ),
            "lgpl": (
                "7c0b0c73efa0cae816a1ba7972677fee"
                "1cfe276c02b1b28a74f4bee10b098e28"
            ),
        }
        for kind, expected in digests.items():
            output = render_attribution(_PATHS[kind], "en")
            body = re.search(r"<pre>(.*?)</pre>", output, re.DOTALL)[1]
            assert sha256(body.encode()).hexdigest() == expected

    def test_unknown_document_cannot_select_arbitrary_template(self) -> None:
        """Reject paths outside the five authored public documents."""
        with self.assertRaises(LookupError):  # ruff: ignore[pytest-unittest-raises-assertion]
            render_attribution("../../private.html", "en")
