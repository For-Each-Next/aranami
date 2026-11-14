"""Verify newsletter traffic readers only consume prepared metadata."""

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from data.newsletter import traffic


class TestNewsletterTraffic(TestCase):
    """Keep cutoffs and preview traffic independent of raw sources."""

    @staticmethod
    def test_prepared_values_are_read_without_raw_traffic() -> None:
        """Use prepared totals and exclude periods after the cutoff."""
        annual = {"end": "2026-02-28", "total_views": None}
        months = [
            {"month": 1, "days": 31, "title": "A", "views": 0},
            {"month": 2, "days": 28, "title": "B", "views": 10},
        ]
        with (
            TemporaryDirectory() as directory,
            patch(
                "data.pageviews.read_snapshot",
                side_effect=AssertionError("Raw traffic scan"),
            ),
        ):
            root = Path(directory)
            traffic.save(
                {"2026": {"annual": annual, "monthly": months}},
                runtime=root,
            )
            assert (
                traffic.read_annual(
                    2026,
                    dt.date(2026, 3, 1),
                    runtime=root,
                )
                == annual
            )
            assert (
                traffic.read_annual(
                    2026,
                    dt.date(2026, 2, 28),
                    runtime=root,
                )
                is None
            )
            assert (
                traffic.read_monthly(
                    2026,
                    dt.date(2026, 2, 1),
                    runtime=root,
                )
                == months[:1]
            )
            assert (
                traffic.read_monthly(
                    2026,
                    dt.date(2026, 3, 1),
                    runtime=root,
                    dry=True,
                )
                == []
            )
            assert (
                traffic.read_annual(
                    2025,
                    dt.date(2026, 1, 1),
                    runtime=root,
                )
                is None
            )
