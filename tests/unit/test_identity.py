"""Verify source requests consistently identify the application."""

import json
from io import BytesIO
from unittest import TestCase
from unittest.mock import Mock, patch

from pywikibot import config
from pywikibot.comms import http

from shared.identity import USER_AGENT, configure_pywikibot
from shared.sources import summaries, wikiwho
from updater.jobs import pexbot


class TestApplicationIdentity(TestCase):
    """Preserve one contact and Pywikibot's framework product."""

    @staticmethod
    def test_pywikibot_retains_its_framework_identification() -> None:
        """Preserve the framework format in configured requests."""
        with patch.object(config, "user_agent_description", None):
            configure_pywikibot()
            agent = http.user_agent()
        assert USER_AGENT in agent
        assert "Pywikibot/" in agent

    @staticmethod
    def test_custom_requests_share_version_and_contact() -> None:
        """Check outgoing headers at custom transport boundaries."""
        summary = {
            "type": "standard",
            "title": "Game",
            "pageid": 1,
            "namespace": {"id": 0},
            "extract": "Game summary.",
        }
        with patch.object(
            summaries.urllib.request,
            "urlopen",
            return_value=BytesIO(json.dumps(summary).encode()),
        ) as fetch:
            summaries.fetch_summary("Game")
        assert fetch.call_args.args[0].get_header("User-agent") == USER_AGENT
        with patch.object(
            wikiwho.urllib.request,
            "urlopen",
            return_value=BytesIO(
                b'{"success":true,"page_id":1,"revisions":[]}',
            ),
        ) as fetch:
            wikiwho.revisions(1)
        assert fetch.call_args.args[0].get_header("User-agent") == USER_AGENT
        site = Mock()
        site.dbName.return_value = "zhwiki"
        with patch.object(
            pexbot.urllib.request,
            "urlopen",
            return_value=BytesIO(b'data: {"code":"end"}\n'),
        ) as fetch:
            pexbot._refresh(site, "Wikipedia:Report")  # ruff: ignore[private-member-access]
        assert fetch.call_args.args[0].get_header("User-agent") == USER_AGENT
