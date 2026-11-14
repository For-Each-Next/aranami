"""Verify Matplotlib samples and interactive SVG semantics."""

# Quantities and locally generated SVG are deliberate fixtures.
# ruff: file-ignore[magic-value-comparison, private-member-access, suspicious-xml-etree-import, suspicious-xml-element-tree-usage, pytest-unittest-raises-assertion]

from __future__ import annotations

from typing import override
from unittest import TestCase
from unittest.mock import patch
from xml.etree import ElementTree as ET

import numpy as np
from matplotlib.axes import Axes

from webservice import quality_charts as charts
from webservice.presentation import Interface


def _data(values: list) -> dict:
    """Prepare exact character samples with nullable saved measurements.

    Returns:
        One bounded cohort with a shared catalog-wide scale.
    """
    return {
        "articles": [
            {
                "grade": "FA",
                "importance": "High",
                "prose_characters": value,
                "listed_date": "2026-01-01",
            }
            for value in values
        ],
        "length_scale_maximum": 100_000,
        "year_from": 2025,
        "year_to": 2026,
        "grade_codes": ["FA"],
    }


class MatplotlibChartTests(TestCase):
    """Keep actual statistical artists and accessible SVG semantics."""

    @override
    def setUp(self) -> None:
        """Prevent render caches from hiding Matplotlib calls."""
        charts._pie.cache_clear()
        charts._violin.cache_clear()
        charts._years.cache_clear()

    @staticmethod
    def test_real_matplotlib_artists_receive_exact_saved_samples() -> None:
        """Use violinplot, pie and bar rather than manual geometry."""
        with (
            patch.object(
                Axes,
                "violinplot",
                autospec=True,
                side_effect=Axes.violinplot,
            ) as violin,
            patch.object(
                Axes,
                "pie",
                autospec=True,
                side_effect=Axes.pie,
            ) as pie,
            patch.object(
                Axes,
                "bar",
                autospec=True,
                side_effect=Axes.bar,
            ) as bar,
        ):
            result = charts.render_charts(
                _data([100_000, 0, 100, 10, None]),
                Interface("en"),
            )
        assert violin.call_count == 2
        assert violin.call_args_list[0].args[1] == (0, 10, 100, 100_000)
        assert violin.call_args_list[0].kwargs["bw_method"] == "scott"
        assert pie.call_count == 2
        bar.assert_called_once()
        svg = ET.fromstring(result["lengths"]["grade:FA"])
        statistics = {
            element.get("data-statistic"): element
            for element in svg.iter()
            if element.get("data-statistic")
        }
        expected = {
            "minimum": 0,
            "p25": 7.5,
            "p50": 55,
            "p75": 25_075,
            "maximum": 100_000,
            "mean": 25_027.5,
        }
        assert {
            kind: float(element.get("data-value"))
            for kind, element in statistics.items()
        } == expected
        assert all(
            element.find(f"{{{charts._SVG}}}title") is not None
            for element in statistics.values()
        )
        assert "lengths collected for 4 articles" in svg.get("aria-label")
        assert "#9c3e32" in result["lengths"]["grade:FA"]
        for serialized in [
            result["grades_svg"],
            result["importance_svg"],
            result["years_svg"],
            *result["lengths"].values(),
        ]:
            root = ET.fromstring(serialized)
            assert root.get("data-renderer") == "matplotlib"
            assert all(
                "style" not in element.attrib for element in root.iter()
            )
            assert not root.findall(f".//{{{charts._SVG}}}style")
            assert any(
                element.get("href") == "#analysis-articles"
                for element in root.iter()
            )

    @staticmethod
    def test_empty_unknown_and_constant_lengths_are_distinct() -> None:
        """Preserve zero, constant and unknown measurements."""
        for values in ([0], [100, 100], [None]):
            result = charts.render_charts(_data(values), Interface("zh-hans"))
            root = ET.fromstring(result["lengths"]["grade:FA"])
            marks = [
                element
                for element in root.iter()
                if element.get("data-statistic")
            ]
            assert len(marks) == (0 if values == [None] else 6)
            assert "nan" not in result["lengths"]["grade:FA"].lower()
        empty = charts.render_charts(_data([]), Interface("en"))
        assert empty["lengths"] == {}
        assert (
            ET.fromstring(empty["grades_svg"]).get("data-renderer")
            == "matplotlib"
        )

    @staticmethod
    def test_svg_ids_are_independent_and_scale_round_trips() -> None:
        """Avoid inline definition collisions across plot instances."""
        result = charts.render_charts(_data([0, 50_000]), Interface("en"))
        identifiers = []
        for serialized in [
            result["grades_svg"],
            result["importance_svg"],
            result["years_svg"],
            *result["lengths"].values(),
        ]:
            identifiers.extend(
                element.get("id")
                for element in ET.fromstring(serialized).iter()
                if element.get("id")
            )
        assert len(identifiers) == len(set(identifiers))
        samples = np.array([0, 50, 500, 5_000, 25_000, 50_000, 100_000])
        np.testing.assert_allclose(
            charts._inverse_scale(charts._scale(samples, 100_000), 100_000),
            samples,
        )

    def test_plot_bounds_reject_unbounded_requests(self) -> None:
        """Reject excessive years and unsafe character measurements."""
        for data in (
            {**_data([1]), "year_to": 9999},
            _data([float("inf")]),
            _data([-1]),
        ):
            with self.assertRaises(ValueError):
                charts.render_charts(data, Interface("en"))
