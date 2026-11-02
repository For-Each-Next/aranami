"""Verify retained assessment summaries and safe format migration."""

# Generated Chinese report punctuation is intentionally full-width.
# ruff: file-ignore[ambiguous-unicode-character-string]
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from backend.services.zhwiki import assessment_log as source
from shared.reports import ReportStore, report_key
from updater.services.zhwiki.assessment_log import update_text

_NOW = dt.datetime(2026, 11, 2, 0, 5, tzinfo=dt.UTC)
_DAY = dt.date(2026, 11, 1)
_TITLE = "WikiProject:电子游戏/评级/日志"
_TOTALS = "丙3 低重要度3 条目3 非条目0 重定向0 专题页面3"
_MARKER = "<!-- aranami-assessment-totals:v2 "


def _text(summary: str, *, changes: bool = False) -> str:
    """Wrap one summary in a complete managed daily report.

    Returns:
        Source text with surrounding content and dated history.
    """
    body = (
        "==== 新增評級 ====\n\n* '''[[:Example]]'''已評級。"
        if changes
        else "無變更"
    )
    return (
        'Introduction\n<!-- aranami begin="assessment-log" -->\n'
        f"=== 2026年11月1日 ===\n\n{summary}\n\n{body}\n\n"
        '<!-- aranami end="assessment-log" -->\nFooter'
    )


class TestAssessmentSummaryHistory(TestCase):
    """Read compact summaries and preserve original comparison facts."""

    @staticmethod
    def test_v2_accepts_optional_categories_and_comment_only() -> None:
        """Accept summaries without a project delta."""
        for visible in (
            "专题页面+3：条目+3（丙+3 ｜ 低重要度+3）",
            "条目（优良+1 · 丙-1）",
            "非条目-1",
            "重定向+4",
            "",
        ):
            text = _text(visible + _MARKER + _TOTALS + " -->")
            assert source.latest_report_date(text, through=_DAY) == _DAY

    def test_v2_rejects_unrecognized_or_unclosed_summary_content(self) -> None:
        """Reject arbitrary markup and incomplete summaries."""
        for summary in (
            "Unexpected text" + _MARKER + _TOTALS + " -->",
            "条目+3" + _MARKER + "<b>counts</b> -->",
            "条目+3" + _MARKER + _TOTALS,
        ):
            with self.subTest(summary=summary), self.assertRaises(ValueError):
                source.latest_report_date(_text(summary), through=_DAY)

    def test_legacy_changes_survive_missing_prior_and_repeated_retry(
        self,
    ) -> None:
        """Keep original deltas after the earlier dataset is gone."""
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        )
        site = OfflineSite("zh", "wikipedia")
        members = pl.DataFrame({
            "page_id": [1, 2, 3],
            "pa_class": ["C"] * 3,
            "pa_importance": ["Low"] * 3,
        })
        snapshot = Mock(
            tables={"members": members},
            manifest={
                "calculated_at": _NOW.isoformat(),
                "report_key": "data/zhwiki/电子游戏",
                "generation": "1" * 32,
                "metadata": {
                    "article_totals": {
                        "count": 3,
                        "class": [{"value": "C", "count": 3}],
                        "importance": [{"value": "Low", "count": 3}],
                        "nonarticles": {"count": 0, "class": []},
                        "redirects": 0,
                    },
                },
            },
        )
        old = _text(
            "专题页面+3：条目+3（丙+3 | 低重要度+3）"
            " · 非条目+0 · 重定向+0<!-- " + _TOTALS + " -->",
            changes=True,
        )
        self.enterContext(
            patch.object(source, "get_snapshot", return_value=snapshot),
        )
        self.enterContext(
            patch.object(source, "read_previous_snapshot", return_value=None),
        )
        prepared = source.prepare_data(
            site,
            "电子游戏",
            _TITLE,
            old,
            observed_at=_NOW,
        )
        migration = prepared.summary_migrations[_DAY]
        assert migration.totals == _TOTALS
        assert migration.changes[1].dimensions == (
            (("丙", 3),),
            (("低重要度", 3),),
        )
        rendered = update_text(old, prepared)
        assert "专题页面+3：条目+3（丙+3 ｜ 低重要度+3）" in rendered
        assert "+0" not in rendered
        assert _MARKER + _TOTALS + " -->" in rendered
        store = ReportStore()
        bundle = store.write(
            report_key("assessment-log", site.dbName(), _TITLE),
            prepared.result,
            rendered,
            run_id="2" * 32,
            started_at=_NOW,
            target={"site": site.dbName(), "title": _TITLE},
        )
        store.set_publication(bundle, "saved")
        retry = source.prepare_data(
            site,
            "电子游戏",
            _TITLE,
            rendered,
            observed_at=_NOW,
        )
        assert retry.previous_totals is None
        assert retry.summary_migrations == {}
        assert update_text(rendered, retry) == rendered

    def test_unknown_and_zero_line_becomes_comment_across_failed_retry(
        self,
    ) -> None:
        """Retain totals while hiding unknown and zero deltas."""
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(
            patch("pathlib.Path.cwd", return_value=Path(directory)),
        )
        site = OfflineSite("zh", "wikipedia")
        snapshot = Mock(
            tables={
                "members": pl.DataFrame({
                    "page_id": [1],
                    "pa_class": ["C"],
                    "pa_importance": ["Low"],
                }),
            },
            manifest={
                "calculated_at": _NOW.isoformat(),
                "report_key": "data/zhwiki/电子游戏",
                "generation": "1" * 32,
                "metadata": {},
            },
        )
        totals = "条目13,062 非条目12,007 重定向68 专题页面25,069"
        old = _text(
            "专题页面25,069（无可比记录）：条目+0"
            " · 非条目12,007（无可比记录） · 重定向68（无可比记录）"
            "<!-- " + totals + " -->",
        )
        self.enterContext(
            patch.object(source, "get_snapshot", return_value=snapshot),
        )
        self.enterContext(
            patch.object(source, "read_previous_snapshot", return_value=None),
        )
        prepared = source.prepare_data(
            site,
            "电子游戏",
            _TITLE,
            old,
            observed_at=_NOW,
        )
        assert [
            group.change for group in prepared.summary_migrations[_DAY].changes
        ] == [None, 0, None, None]
        rendered = update_text(old, prepared)
        assert rendered == _text(_MARKER + totals + " -->")
        store = ReportStore()
        bundle = store.write(
            report_key("assessment-log", site.dbName(), _TITLE),
            prepared.result,
            rendered,
            run_id="3" * 32,
            started_at=_NOW,
            target={"site": site.dbName(), "title": _TITLE},
        )
        store.set_publication(bundle, "failed")
        retry = source.prepare_data(
            site,
            "电子游戏",
            _TITLE,
            old,
            observed_at=_NOW,
        )
        assert retry.pending
        assert retry.summary_migrations == {}
        assert update_text(old, retry) == rendered
