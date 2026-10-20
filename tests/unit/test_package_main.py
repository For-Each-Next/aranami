"""Verify the backend command configures the shared update hub."""

# Keep unittest exception assertions consistent with the existing tests.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import io
from contextlib import redirect_stderr
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend import __main__ as package_main, hub


class TestPackageMain(TestCase):
    """Verify runtime settings reach the common backend entry point."""

    @staticmethod
    def test_module_entry_point_uses_the_shared_hub() -> None:
        """Start both live outputs with the documented defaults."""
        assert package_main.main is hub.main
        with patch.object(hub, "run") as run:
            package_main.main([])
        run.assert_called_once()
        config = run.call_args.args[0]
        assert not config.dry
        assert config.run_immediately
        assert config.output == Path("cache/toolforge/dashboard.json")
        assert config.interval == 3600  # ruff: ignore[magic-value-comparison]

    def test_cli_options_select_output_mode_and_startup_timing(self) -> None:
        """Forward positive and negative options to the hub."""
        for dry in (False, True):
            for run_immediately in (False, True):
                with (
                    self.subTest(dry=dry, run_immediately=run_immediately),
                    patch.object(hub, "run") as run,
                ):
                    package_main.main([
                        "--dry" if dry else "--no-dry",
                        "--run-immediately"
                        if run_immediately
                        else "--no-run-immediately",
                    ])
                config = run.call_args.args[0]
                assert config.dry is dry
                assert config.run_immediately is run_immediately

    @staticmethod
    def test_website_options_and_independent_refresh_periods() -> None:
        """Retain collection and local HTTP configuration options."""
        with patch.object(hub, "run") as run:
            package_main.main([
                "--output",
                "cache/custom.json",
                "--log-dir",
                "logs/custom",
                "--interval",
                "600",
                "--days",
                "30",
                "--host",
                "127.0.0.2",
                "--port",
                "9000",
                "--recognized-interval-hours",
                "12",
                "--dyk-interval-hours",
                "6",
                "--pageviews-interval-hours",
                "2",
                "--news-history-interval-hours",
                "3",
                "--backlog-interval-hours",
                "4",
            ])
        config = run.call_args.args[0]
        assert config.output == Path("cache/custom.json")
        assert config.log_dir == Path("logs/custom")
        assert config.interval == 600  # ruff: ignore[magic-value-comparison]
        assert config.days == 30  # ruff: ignore[magic-value-comparison]
        assert (config.host, config.port) == ("127.0.0.2", 9000)
        assert config.refresh_intervals == {
            "recognized": 12 * 3600,
            "dyk": 6 * 3600,
            "pageviews": 2 * 3600,
            "news_history": 3 * 3600,
            "backlog": 4 * 3600,
        }

    def test_invalid_settings_fail_before_startup(self) -> None:
        """Reject invalid settings before starting outputs."""
        for options in (
            ["--interval", "0"],
            ["--days", "366"],
            ["--port", "65536"],
            ["--recognized-interval-hours", "-1"],
        ):
            with (
                self.subTest(options=options),
                patch.object(hub, "run") as run,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                package_main.main(options)
            run.assert_not_called()
