"""Keep retired category maintenance absent while preserving checks."""

from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from starlette.testclient import TestClient

from data.website import REPORT_KEYS
from shared.config import WEBSITE_REFRESH_INTERVALS
from tasks.website.collect import report_intervals
from webservice.server import create_app


class RetiredMaintenanceTests(TestCase):
    """Verify removal of routes and background refresh settings."""

    @staticmethod
    def test_routes_and_browser_module_are_absent() -> None:
        """Keep retired routes missing and checks available."""
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(Path(directory)))
            for path in (
                "/maintenance/",
                "/maintenance/checks/",
                "/maintenance/?task=references",
                "/api/backlog",
                "/api/maintenance",
                "/maintenance.js",
            ):
                assert client.get(path).status_code == HTTPStatus.NOT_FOUND
            assert client.get("/checks/").status_code == HTTPStatus.OK

    @staticmethod
    def test_report_and_refresh_setting_are_absent() -> None:
        """Expose only the remaining independent collection keys."""
        assert "backlog" not in REPORT_KEYS
        assert "backlog" not in WEBSITE_REFRESH_INTERVALS
        assert "backlog" not in report_intervals()
