"""Verify alpha source versions remain readable in durable reports."""

# Reuse real report writes rather than duplicating their validation.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import test_report_storage as fixtures

from data.reports import ReportStore
from shared import deployment
from shared.deployment import project_version


class AlphaVersionTests(TestCase):
    """Keep alpha and previous released observations compatible."""

    @staticmethod
    def test_recorded_build_time_survives_process_restarts() -> None:
        """Read build metadata and preserve unknown old timestamps."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                deployment,
                "__file__",
                str(root / "src/shared/deployment.py"),
            ):
                assert deployment.project_build_time() is None
                (root / "deployment.json").write_text(
                    '{"built_at":"2026-11-10T12:00:00Z"}',
                )
                assert (
                    deployment.project_build_time() == "2026-11-10T12:00:00Z"
                )

    def test_source_and_report_versions_accept_alpha_and_old_builds(
        self,
    ) -> None:
        """Round-trip valid versions without losing prior formats."""
        for version in ("1.0.0a1", "1.0.0a12", "0.8.8", "0.8.9.post2"):
            with (
                self.subTest(version=version),
                TemporaryDirectory() as directory,
                patch(
                    "shared.deployment.tomllib.load",
                    return_value={"project": {"version": version}},
                ),
            ):
                assert project_version() == version
                store = ReportStore(Path(directory))
                fixtures._write(store, 1)
                selected = store.read("test-zhwiki-main")
                assert selected.manifest["application_version"] == version

    def test_invalid_alpha_versions_are_rejected(self) -> None:
        """Require a positive canonical alpha sequence number."""
        for version in ("1.0.0a0", "1.0.0a01", "1.0.0alpha1", "1.0.0a"):
            with (
                self.subTest(version=version),
                patch(
                    "shared.deployment.tomllib.load",
                    return_value={"project": {"version": version}},
                ),
                self.assertRaises(ValueError),
            ):
                project_version()
