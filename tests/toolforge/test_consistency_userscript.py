"""Verify saved correction URLs, userscript assets and read-only API."""

# Protocol identities and status codes are intentionally literal.
# ruff: file-ignore[magic-value-comparison,private-member-access]

from __future__ import annotations

import re
from html import unescape
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from starlette.testclient import TestClient
from test_consistency_presentation import consistency_snapshot

from data.consistency import review_repair
from webservice import server
from webservice.consistency_review import repair_links
from webservice.pages import render_consistency_checks, render_maintenance
from webservice.presentation import Interface


class TestConsistencyUserscript(TestCase):
    """Keep correction handoffs exact and dependent on saved results."""

    @staticmethod
    def test_query_links_select_only_the_current_talk_lead() -> None:
        """Put an opaque repair ID in a native section-zero edit URL."""
        snapshot = consistency_snapshot()
        result = snapshot["consistency"]
        repair_id = next(iter(result["section0_repairs"]))
        repair = review_repair(result, repair_id)
        assert repair is not None
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            actions = repair_links(repair, interface)
            assert len(actions) == 1
            action = actions[0]
            target = urlsplit(action["url"])
            assert target.scheme == "https"
            assert target.netloc == "zh.wikipedia.org"
            assert target.path == "/w/index.php"
            assert parse_qs(target.query) == {
                "title": ["Talk:Game 01"],
                "action": ["edit"],
                "section": ["0"],
                "aranami-repair": [repair_id],
            }
            assert action["label"] == interface.text("consistency.review_diff")
        assert repair_links(None, Interface("en")) == []

    @staticmethod
    def test_pages_expose_install_link_and_only_ready_actions() -> None:
        """Offer one native download and one exact ready page action."""
        snapshot = consistency_snapshot()
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_maintenance(
                snapshot,
                {"task": ["consistency_history"]},
                locale,
            )
            interface = Interface(locale)
            assert interface.text("consistency.userscript_note") in unescape(
                output,
            )
            assert 'href="/consistency-repair.user.js?v=' in output
            assert output.count(' consistency-review-action"') == 1
            controls = re.search(
                r'<div class="newsletter-copy-controls '
                r'consistency-copy-controls">(.*?)</div>',
                output,
            )
            assert controls is not None
            assert "data-copy-target=" in controls[1]
            assert "consistency-review-action" in controls[1]
            assert (
                controls[1].index("data-copy-target=")
                < controls[1].index(
                    "consistency-review-action",
                )
                < controls[1].index("data-copy-status")
            )
            assert "aranami-repair=" in output
            assert "wpTextbox1=" not in output
            assert "wpPreview" not in output
            assert 'id="consistency-userscript-code"' not in output
            checks = render_consistency_checks(
                snapshot,
                None,
                None,
                locale,
            )
            assert 'href="/consistency-repair.user.js?v=' in checks
        snapshot["consistency"]["section0_repairs"] = {}
        pending = render_consistency_checks(snapshot, None, None, "en")
        assert "consistency-review-action" not in pending
        assert 'href="/consistency-repair.user.js?v=' in pending

    @staticmethod
    def test_legacy_only_cleanup_keeps_a_standalone_edit_action() -> None:
        """Keep a ready cleanup usable without a parameter snippet."""
        snapshot = consistency_snapshot()
        for finding in snapshot["consistency"]["items"]:
            if finding.get("repair_id"):
                finding.update(
                    expected_field=None,
                    event_date=None,
                    suggested_parameters={},
                    removable_dyk_template_count=1,
                )
        snapshot["consistency"]["items"] = [
            finding
            for finding in snapshot["consistency"]["items"]
            if finding.get("repair_id")
        ]
        output = render_maintenance(
            snapshot,
            {"task": ["consistency_history"]},
            "en",
        )
        fallback = re.search(
            r'<p class="consistency-review-actions">(.*?)</p>',
            output,
        )
        assert fallback is not None
        assert "consistency-review-action" in fallback[1]
        assert Interface("en").text("consistency.review_diff") in fallback[1]

    @staticmethod
    def test_api_returns_only_one_validated_ready_record_without_writes() -> (
        None
    ):
        """Read saved code and guards without creating runtime files."""
        snapshot = consistency_snapshot()
        result = snapshot["consistency"]
        repair_id = next(iter(result["section0_repairs"]))
        expected = review_repair(result, repair_id)
        with TemporaryDirectory() as directory:
            runtime = Path(directory) / "missing"
            with patch.object(
                server._PublicViews,
                "_dashboard",
                return_value=snapshot,
            ) as saved:
                client = TestClient(server.create_app(runtime))
                response = client.get(
                    "/api/consistency-repair",
                    params={"repair": repair_id},
                )
                assert response.status_code == HTTPStatus.OK
                assert response.json() == {"schema": 1, "repair": expected}
                assert saved.call_count == 1
                assert (
                    "form-action 'self'"
                    in response.headers["Content-Security-Policy"]
                )
                head = client.head(
                    "/api/consistency-repair",
                    params={"repair": repair_id},
                )
                assert head.status_code == HTTPStatus.OK
                assert head.content == b""
                denied = client.post(
                    "/api/consistency-repair",
                    params={"repair": repair_id},
                )
                assert denied.status_code == HTTPStatus.METHOD_NOT_ALLOWED
                assert saved.call_count == 2
            assert not runtime.exists()

    @staticmethod
    def test_query_rejects_caller_payloads_and_duplicate_selectors() -> None:
        """Accept one lower-hex identity before reading saved data."""
        repair_id = "a" * 64
        with (
            TemporaryDirectory() as directory,
            patch.object(server._PublicViews, "_dashboard") as saved,
        ):
            client = TestClient(server.create_app(Path(directory)))
            for query in (
                "",
                "repair=",
                "repair=bad",
                "repair=" + "z" * 64,
                f"repair={repair_id}&repair={repair_id}",
                f"repair={repair_id}&text=untrusted",
                f"repair={repair_id}&url=https://example.com",
                f"repair={repair_id}&mode=preview",
                "repair=%FF",
            ):
                response = client.get("/api/consistency-repair?" + query)
                assert response.status_code == HTTPStatus.BAD_REQUEST
            assert saved.call_count == 0

    @staticmethod
    def test_unready_missing_or_invalid_corrections_are_unavailable() -> None:
        """Withhold missing, unfinished or invalid saved corrections."""
        snapshot = consistency_snapshot()
        result = snapshot["consistency"]
        repair_id = next(iter(result["section0_repairs"]))
        with (
            TemporaryDirectory() as directory,
            patch.object(
                server._PublicViews,
                "_dashboard",
                return_value=snapshot,
            ),
        ):
            client = TestClient(server.create_app(Path(directory)))
            unknown = client.get(
                "/api/consistency-repair?repair=" + "a" * 64,
            )
            assert unknown.status_code == HTTPStatus.NOT_FOUND
            result["section0_repairs"][repair_id]["edit_summary"] = ""
            invalid = client.get(
                "/api/consistency-repair?repair=" + repair_id,
            )
            assert invalid.status_code == HTTPStatus.NOT_FOUND
            result["section0_repairs"] = {}
            pending = client.get(
                "/api/consistency-repair?repair=" + repair_id,
            )
            assert pending.status_code == HTTPStatus.NOT_FOUND

    @staticmethod
    def test_install_asset_is_fixed_readable_and_uses_no_save_action() -> None:
        """Serve the same complete userscript from its install URL."""
        with TemporaryDirectory() as directory:
            client = TestClient(server.create_app(Path(directory)))
            response = client.get("/consistency-repair.user.js")
            assert response.status_code == HTTPStatus.OK
            assert response.headers["Content-Type"].startswith(
                "text/javascript",
            )
            assert "// ==UserScript==" in response.text
            assert "@connect      aranami.toolforge.org" in response.text
            assert "controls.diff.click()" in response.text
            assert "wpPreview" not in response.text
            assert "wpSave" not in response.text
            assert (
                "form-action 'self'"
                in response.headers["Content-Security-Policy"]
            )
