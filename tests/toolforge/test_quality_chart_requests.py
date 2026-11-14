"""Verify bounded Matplotlib chart selections and stale replies."""

# HTTP statuses and bounds describe the regression fixtures.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient

from webservice import quality_chart_requests as charts
from webservice.server import create_app

_SVG = '<svg xmlns="http://www.w3.org/2000/svg" data-renderer="matplotlib"/>'
_RESULT = {
    "grades_svg": _SVG,
    "importance_svg": _SVG,
    "years_svg": _SVG,
    "lengths": {},
}


def _saved() -> dict:
    """Provide three saved observations with known and unknown dates.

    Returns:
        A complete catalog selection independent of source access.
    """
    rows = [
        {
            "page_id": 1,
            "grade": "FA",
            "importance": "High",
            "listed_date": "2026-01-01",
            "prose_characters": 10,
        },
        {
            "page_id": 2,
            "grade": "GA",
            "importance": "Unknown",
            "listed_date": None,
            "prose_characters": 0,
        },
        {
            "page_id": 3,
            "grade": "B",
            "importance": "Low",
            "listed_date": "2025-01-01",
            "prose_characters": 100_000,
        },
    ]
    return {
        "analysis": {
            "selection": {
                "articles": rows,
                "cells": [{"histogram": [{"value": 100_000, "count": 1}]}],
                "last_year": 2026,
            },
        },
    }


class QualityChartRequestTests(TestCase):
    """Keep filter rendering tied to one immutable source generation."""

    def setUp(self) -> None:
        """Exercise the HTTP route with saved readers."""
        self.runtime = Path(self.enterContext(TemporaryDirectory()))
        self.identity = self.enterContext(
            patch.object(
                charts,
                "current_quality_identity",
                return_value=(1,),
            ),
        )
        self.saved = self.enterContext(
            patch.object(
                charts,
                "read_current_quality",
                return_value=_saved(),
            ),
        )
        self.render = self.enterContext(
            patch.object(
                charts,
                "render_charts",
                return_value=_RESULT,
            ),
        )
        self.client = TestClient(create_app(self.runtime))
        self.addCleanup(self.client.close)
        self.controls = {
            "generation": charts.chart_generation((1,)),
            "basis": "current",
            "cells": "FA:High,GA:Unknown",
            "from": "2026",
            "to": "2026",
            "unknown": "1",
            "lang": "en",
        }

    def test_saved_selection_preserves_zero_and_makes_no_writes(self) -> None:
        """Filter saved identities and retain the full catalog scale."""
        before = sorted(self.runtime.rglob("*"))
        with patch("pywikibot.Site", side_effect=AssertionError("sources")):
            response = self.client.get(
                "/quality-analysis/charts/",
                params=self.controls,
            )
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {
            "generation": self.controls["generation"],
            **_RESULT,
        }
        rendered = self.render.call_args.args[0]
        assert [row["page_id"] for row in rendered["articles"]] == [1, 2]
        assert rendered["articles"][1]["prose_characters"] == 0
        assert rendered["length_scale_maximum"] == 100_000
        assert sorted(self.runtime.rglob("*")) == before
        self.controls["unknown"] = "0"
        self.client.get("/quality-analysis/charts/", params=self.controls)
        assert [
            row["page_id"] for row in self.render.call_args.args[0]["articles"]
        ] == [1]
        self.controls["cells"] = "Stub:Top"
        response = self.client.get(
            "/quality-analysis/charts/",
            params=self.controls,
        )
        assert response.status_code == HTTPStatus.OK
        assert self.render.call_args.args[0]["articles"] == []

    def test_invalid_controls_never_invoke_matplotlib(self) -> None:
        """Reject invalid controls before chart rendering."""
        for name, value in (
            ("cells", "FA:High,FA:High"),
            ("cells", "FA:Private"),
            ("from", "2001"),
            ("to", "2027"),
            ("unknown", "yes"),
            ("basis", "private"),
            ("lang", "private"),
        ):
            with self.subTest(name=name, value=value):
                response = self.client.get(
                    "/quality-analysis/charts/",
                    params={**self.controls, name: value},
                )
                assert response.status_code == HTTPStatus.BAD_REQUEST
        self.render.assert_not_called()

    def test_replaced_generation_rejects_stale_results(self) -> None:
        """Reject sources replaced during rendering."""
        self.identity.return_value = (2,)
        response = self.client.get(
            "/quality-analysis/charts/",
            params=self.controls,
        )
        assert response.status_code == HTTPStatus.CONFLICT
        self.saved.assert_not_called()
        self.render.assert_not_called()
        self.identity.side_effect = [(1,), (2,)]
        response = self.client.get(
            "/quality-analysis/charts/",
            params=self.controls,
        )
        assert response.status_code == HTTPStatus.CONFLICT
        self.render.assert_called_once()

    def test_missing_or_corrupt_saved_catalog_is_unavailable(self) -> None:
        """Distinguish corrupt data from invalid controls."""
        self.saved.side_effect = ValueError("damaged")
        response = self.client.get(
            "/quality-analysis/charts/",
            params=self.controls,
        )
        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        self.saved.side_effect = None
        self.identity.return_value = (None,)
        response = self.client.get(
            "/quality-analysis/charts/",
            params=self.controls,
        )
        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        self.render.assert_not_called()
