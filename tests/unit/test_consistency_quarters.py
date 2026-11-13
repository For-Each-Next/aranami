"""Verify quarterly hints without fabricated historical observations."""

# Numeric expectations describe the audited table and class weights.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
import math
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from tasks.zhwiki import consistency_quarters as quarters

_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures/consistency/challenge-94947356.wikitext"
)
_DAY = dt.date(2026, 9, 30)
_NOW = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)


def _bundle(grades: list[str], importance: str = "高") -> SimpleNamespace:
    """Build an exact saved subject snapshot for aggregate tests.

    Returns:
        A retained data-date, observation timestamp, and digest.
    """
    return SimpleNamespace(
        tables={
            "members": pl.DataFrame(
                {
                    "page_namespace": [0] * len(grades),
                    "page_is_redirect": [0] * len(grades),
                    "pa_class": grades,
                    "pa_importance": [importance] * len(grades),
                },
                schema={
                    "page_namespace": pl.Int64,
                    "page_is_redirect": pl.Int64,
                    "pa_class": pl.String,
                    "pa_importance": pl.String,
                },
            ),
        },
        manifest={
            "metadata": {"snapshot_date": _DAY.isoformat()},
            "calculated_at": "2026-10-01T00:02:00+00:00",
            "sha256": "a" * 64,
        },
    )


def _read() -> Mock:
    """Return an audited source with one missing quarterly row.

    Returns:
        Read-only fixture client with the live today row still present.
    """
    read = Mock()
    read.site = OfflineSite("zh", "wikipedia")
    read.sources = {
        quarters.TITLE: {
            "url": "https://zh.wikipedia.org/w/index.php?oldid=94947356",
        },
    }
    read.source_page_ids = {quarters.TITLE: 1234}
    read.return_value = re.sub(
        r'(?m)^! scope="row" \| 2026年9月30日\n(?:[^\n]*\n)*?\|-\n',
        "",
        _FIXTURE.read_text(),
    )
    return read


class TestQuarterlyChallenge(TestCase):
    """Keep a missing row separate from unavailable historical hints."""

    @staticmethod
    def test_audited_fixture_includes_scope_less_2025_rows() -> None:
        """Read 23 static quarters and exclude the dynamic today row."""
        dates = quarters.parse_quarter_dates(_FIXTURE.read_text())
        assert len(dates) == 23
        assert dates[0] == dt.date(2021, 3, 31)
        assert dates[-1] == _DAY
        assert dt.date(2025, 3, 31) in dates
        assert dt.date(2025, 6, 30) in dates

    def test_broken_dated_rows_fail_the_contract(self) -> None:
        """Reject a blank cell in a static quarterly row."""
        text = _FIXTURE.read_text().replace("| 794\n", "| \n", 1)
        with self.assertRaises(ValueError):
            quarters.parse_quarter_dates(text)

    @staticmethod
    def test_class_families_weights_and_unknowns() -> None:
        """Pool list grades and preserve unknown means for zero rows."""
        values = quarters.snapshot_values(
            _bundle([
                "FA",
                "FL",
                "A",
                "AL",
                "GA",
                "Bplus",
                "B",
                "BL",
                "C",
                "CL",
                "Start",
                "List",
                "Stub",
                "SL",
                "Unassessed",
            ]),
        )
        assert {
            key: values[key] for key in ("fa", "ga", "b", "c", "start", "stub")
        } == {
            "fa": 4,
            "ga": 2,
            "b": 2,
            "c": 2,
            "start": 2,
            "stub": 3,
        }
        assert values["total"] == 15
        assert values["top"] == 0
        assert values["high"] == 15
        assert math.isclose(values["start_percentage"], 80.0)
        assert math.isclose(values["quality_index"], 5.93)
        empty = quarters.snapshot_values(_bundle([]))
        assert empty["total"] == 0
        assert empty["start_percentage"] is None
        assert empty["quality_index"] is None
        assert set(
            quarters.snapshot_values(_bundle(["Unknown"])).values(),
        ) == {None}
        assert set(quarters.snapshot_values(None).values()) == {None}

    @staticmethod
    def test_missing_quarter_reads_exact_snapshot_and_keeps_provenance() -> (
        None
    ):
        """Use quarter-end metadata without current substitutions."""
        read = _read()
        with (
            patch.object(
                quarters.project_assessments,
                "read_snapshot",
                return_value=_bundle(["FA", "Stub"]),
            ) as chinese,
            patch.object(
                quarters.project_data,
                "read_snapshot",
                return_value=_bundle(["C"], "Top"),
            ) as english,
        ):
            items, check = quarters.collect_quarter_check(read, now=_NOW)
        assert check["status"] == "complete"
        assert len(items) == 1
        finding = items[0]
        assert finding["event_date"] == "2026-09-30"
        assert finding["page_id"] == 1234
        assert finding["source_revision"] == 94947356
        assert finding["target_type"] == "project"
        assert finding["suggested_values"]["total"] == 2
        assert finding["suggested_values"]["en_total"] == 1
        assert (
            finding["suggested_snapshot"]["observed_at"]
            == "2026-10-01T00:02:00+00:00"
        )
        assert finding["suggested_english_snapshot"]["sha256"] == "a" * 64
        assert "Talk:WikiProject" not in finding["talk_url"]
        chinese.assert_called_once_with(read.site.dbName(), "电子游戏", _DAY)
        english.assert_called_once_with(
            "enwiki",
            "Video games",
            dt.date(2026, 10, 1),
        )
        read.assert_called_once_with(quarters.TITLE)

    @staticmethod
    def test_missing_history_leaves_hints_unknown() -> None:
        """Show a missing row when old data is unavailable."""
        with (
            patch.object(
                quarters.project_assessments,
                "read_snapshot",
                return_value=None,
            ),
            patch.object(
                quarters.project_data,
                "read_snapshot",
                return_value=None,
            ),
        ):
            items, check = quarters.collect_quarter_check(_read(), now=_NOW)
        assert check["issue_count"] == 1
        assert set(items[0]["suggested_values"].values()) == {None}
        assert items[0]["suggested_snapshot"] is None

    @staticmethod
    def test_english_failure_does_not_erase_chinese_observations() -> None:
        """Keep local data when English observations are unavailable."""
        with (
            patch.object(
                quarters.project_assessments,
                "read_snapshot",
                return_value=_bundle(["GA"]),
            ),
            patch.object(
                quarters.project_data,
                "read_snapshot",
                side_effect=ValueError("corrupt"),
            ),
        ):
            items, _ = quarters.collect_quarter_check(_read(), now=_NOW)
        assert items[0]["suggested_values"]["total"] == 1
        assert items[0]["suggested_values"]["en_total"] is None

    @staticmethod
    def test_wrong_snapshot_day_is_not_a_historical_substitute() -> None:
        """Reject current observations supplied under a prior date."""
        current = _bundle(["FA"])
        current.manifest["metadata"]["snapshot_date"] = "2026-11-01"
        with (
            patch.object(
                quarters.project_assessments,
                "read_snapshot",
                return_value=current,
            ),
            patch.object(
                quarters.project_data,
                "read_snapshot",
                return_value=None,
            ),
        ):
            items, _ = quarters.collect_quarter_check(_read(), now=_NOW)
        assert set(items[0]["suggested_values"].values()) == {None}

    @staticmethod
    def test_unavailable_and_unsupported_sources_are_distinct() -> None:
        """Never turn source failure into an empty challenge table."""
        read = _read()
        read.side_effect = OSError("offline")
        items, check = quarters.collect_quarter_check(read, now=_NOW)
        assert items == []
        assert check["status"] == "unavailable"
        read.side_effect = None
        read.return_value = "unknown table"
        items, check = quarters.collect_quarter_check(read, now=_NOW)
        assert items == []
        assert check["status"] == "unsupported"
