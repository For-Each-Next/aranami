"""Verify independent dataset-update lists and their live fragments."""

# Displayed table dimensions use multiplication signs.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import copy
import datetime as dt
import re
from unittest import TestCase
from unittest.mock import patch

from status_fixtures import status_snapshot

from webservice.pages import render_about, render_live_status
from webservice.presentation import Interface
from webservice.status_view import status_context

_NOW = dt.datetime(2026, 11, 14, 12, 0, 30, tzinfo=dt.UTC)


def _updates() -> dict:
    """Provide completed, failed and active files with saved evidence.

    Returns:
        The presentation contract, independent of storage acquisition.
    """
    started = {
        "known": True,
        "value": "2026-11-14T12:00:00Z",
        "label": "2026-11-14 12:00:00",
    }
    baseline = {
        "filename": "old.parquet",
        "url": "/data/?file=data/raw/old.parquet&page=1&lang=en",
        "shape": "12 × 3",
    }
    completed = {
        "key": "previous-file",
        "filename": "new<unsafe>.parquet",
        "url": "/data/?file=data/raw/new.parquet&page=1&lang=en",
        "state": "succeeded",
        "started": started,
        "finished": {**started, "label": "2026-11-14 12:00:15"},
        "baseline": baseline,
        "shape": "14 × 4",
        "reports": [{"name": "Report <unsafe>", "url": "/reports/"}],
    }
    return {
        "previous": {
            "items": [
                completed,
                {
                    **completed,
                    "key": "failed-file",
                    "filename": "failed.parquet",
                    "state": "failed",
                    "shape": None,
                },
            ],
        },
        "current": {
            "state": "running",
            "scheduled": started,
            "items": [
                {
                    "key": "next-file",
                    "filename": "next.parquet",
                    "state": "running",
                    "started": started,
                    "baseline": baseline,
                    "elapsed": {
                        "running": True,
                        "started": started["value"],
                        "label": "0:30.00",
                    },
                    "activity": "Reading <source>",
                    "reports": [{"name": "Report <unsafe>", "url": None}],
                },
            ],
        },
        "expires": "2026-11-14T12:01:30Z",
    }


def _render_updates(data: dict, locale: str, *, live: bool = False) -> str:
    """Render controlled update evidence through page templates.

    Returns:
        A full About document or the WebSocket HTML fragments.
    """

    def context(*args: object, **kwargs: object) -> dict:
        """Inject the dataset presentation contract.

        Returns:
            Ordinary status with controlled dataset evidence.
        """
        result = status_context(*args, **kwargs)
        result["server"]["dataset_updates"] = copy.deepcopy(data)
        return result

    config = {"version": "0.9.15"}
    with patch("webservice.pages.status_context", side_effect=context):
        if live:
            return render_live_status(status_snapshot(), config, locale, _NOW)
        return render_about(config, locale, status=status_snapshot(), now=_NOW)


class DatasetUpdatePresentationTests(TestCase):
    """Keep dataset evidence in separate semantic sections."""

    @staticmethod
    def test_lists_and_fragments_match_in_all_locales() -> None:
        """Use the same escaped evidence in full and pushed pages."""
        for locale in ("en", "zh-hans", "zh-hant"):
            full = _render_updates(_updates(), locale)
            live = _render_updates(_updates(), locale, live=True)
            for kind in ("previous", "current"):
                pattern = rf'<section id="dataset-updates-{kind}".*?</section>'
                first = re.search(pattern, full, re.DOTALL)
                second = re.search(pattern, live, re.DOTALL)
                assert first is not None
                assert second is not None
                assert first[0] == second[0]
                assert "<h2 " in first[0]
            assert 'id="server-work"' not in full
            assert "new&lt;unsafe&gt;.parquet</a></strong>" in full
            assert "new<unsafe>.parquet" not in full
            assert "Report &lt;unsafe&gt;" in full
            assert "12 × 3" in full
            assert "14 × 4" in full
            for icon in ("✅", "❌", "⌛"):
                assert icon in full
            assert "<strong>next.parquet</strong>" in full
            assert "data-dataset-link" in full
            assert 'data-clock="dataset-elapsed"' in full
            assert "Reading &lt;source&gt;" in full
            assert "2026-11-14 12:00:15</time> UTC" in full
            assert Interface(locale).text("dataset_updates.running") in full

    @staticmethod
    def test_plans_do_not_claim_running_or_start_a_clock() -> None:
        """Keep future filenames unlinked without elapsed activity."""
        data = _updates()
        data["current"]["state"] = "planned"
        item = data["current"]["items"][0]
        item.update(state="pending", started=None, elapsed=None, activity=None)
        output = _render_updates(data, "zh-hans")
        assert "计划更新资料</h2>" in output
        assert "正在更新资料" not in output
        assert 'data-clock="dataset-elapsed"' not in output
        assert "<strong>next.parquet</strong>" in output

    @staticmethod
    def test_empty_evidence_is_explicit() -> None:
        """Render missing records without inventing successful files."""
        output = _render_updates({}, "en")
        assert Interface("en").text("dataset_updates.no_previous") in output
        assert Interface("en").text("dataset_updates.no_planned") in output
        assert 'class="dataset-update-files"' not in output
