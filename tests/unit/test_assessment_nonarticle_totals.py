"""Verify assessment delta summaries and nonarticle classifications."""

# Chinese report typography intentionally uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase

import polars as pl

from data import project_assessments as snapshots
from updater.services.zhwiki.assessment_log import update_text


def _source(*classes: str) -> pl.DataFrame:
    """Build main-namespace source facts with nonredirect flags.

    Returns:
        Typed rows with different assessment grades.
    """
    return pl.DataFrame(
        {
            "page_id": range(1, len(classes) + 1),
            "page_namespace": [0] * len(classes),
            "page_is_redirect": [0] * len(classes),
            "pa_class": classes,
            "pa_importance": ["High"] * len(classes),
        },
        schema={
            "page_id": pl.Int64,
            "page_namespace": pl.Int64,
            "page_is_redirect": pl.Int64,
            "pa_class": pl.String,
            "pa_importance": pl.String,
        },
    )


def _data(current: dict, previous: dict | None) -> SimpleNamespace:
    """Provide isolated saved aggregate evidence for the renderer.

    Returns:
        Prepared-data attributes required by the summary projection.
    """
    return SimpleNamespace(
        result=SimpleNamespace(
            metadata={"article_totals": current, "project": "电子游戏"},
            provenance={"wiki": "zhwiki"},
            tables={},
        ),
        previous_totals=previous,
        previous_source=None,
        report_date=dt.date(2026, 11, 1),
        unchanged=True,
        changes=pl.DataFrame(),
        days={},
        summaries={},
        pruned=False,
        pending=False,
        removed_links={},
        summary_migrations={},
        prefix="",
    )


def render_totals(data: SimpleNamespace) -> str:
    """Render a new observed day through the public log projection.

    Returns:
        Its complete daily summary line and hidden current totals.
    """
    rendered = update_text(
        '<!-- aranami begin="assessment-log" -->\n'
        '<!-- aranami end="assessment-log" -->',
        data,
    )
    return next(
        line
        for line in rendered.splitlines()
        if "aranami-assessment-totals schema=2" in line
    )


class TestAssessmentNonarticleTotals(TestCase):
    """Keep main-namespace nonarticles out of article totals."""

    @staticmethod
    def test_partially_known_redirects_do_not_invent_complete_totals() -> None:
        """Keep mixed known and unknown exclusions unavailable."""
        source = _source("B", "B").with_columns(
            pl.Series("page_is_redirect", [1, None], dtype=pl.Int64),
        )
        assert snapshots.summary_totals(source) is None
        assert snapshots._totals(source) is None  # ruff: ignore[private-member-access]

    @staticmethod
    def test_nonarticle_grade_aliases_and_actual_redirects() -> None:
        """Classify aliases as pages and exclude actual redirects."""
        redirect_id = 10
        nonarticle_count = 8
        source = _source(
            "B",
            "Redirect",
            "重定向",
            "Setindex",
            "同类索引",
            " 同類索引級 ",
            "Disambig",
            "消歧义",
            "消歧義",
            "FA",
        ).with_columns(
            pl
            .when(pl.col("page_id") == redirect_id)
            .then(1)
            .otherwise(0)
            .cast(pl.Int64)
            .alias("page_is_redirect"),
        )
        totals = snapshots.summary_totals(source)
        assert totals["count"] == 1
        assert totals["class"] == [{"value": "B", "count": 1}]
        assert totals["importance"] == [{"value": "High", "count": 1}]
        assert totals["nonarticles"]["count"] == nonarticle_count
        assert totals["redirects"] == 1

    @staticmethod
    def test_delta_summary_matches_requested_order_and_hidden_totals() -> None:
        """Show deltas and retain complete counts in one comment."""
        previous = {
            "count": 20,
            "class": [
                {"value": "FA", "count": 11},
                {"value": "GA", "count": 2},
                {"value": "Stub", "count": 7},
            ],
            "importance": [{"value": "Top", "count": 20}],
            "nonarticles": {
                "count": 2,
                "class": [{"value": "Setindex", "count": 2}],
            },
            "redirects": 3,
        }
        current = {
            "count": 24,
            "class": [
                {"value": "FA", "count": 12},
                {"value": "GA", "count": 7},
                {"value": "Stub", "count": 5},
            ],
            "importance": [{"value": "Top", "count": 24}],
            "nonarticles": {
                "count": 1,
                "class": [{"value": "Setindex", "count": 1}],
            },
            "redirects": 7,
        }
        rendered = render_totals(_data(current, previous))
        assert rendered.split("<!--", 1)[0] == (
            "专题页面数+7：条目页+4（典范级+1、优良级+5、小作品级-2，极高重要度+4）"
            "；非条目页-1（同类索引级-1）；重定向页+4。"
        )
        assert (
            "<!-- aranami-assessment-totals schema=2 "
            "合计32：典范12、特色列表0、甲0、甲级列表0、优良7"
        ) in rendered
        assert "极高重要度24" in rendered
        assert "重定向级7；极高重要度24" in rendered
        assert "不适用重要度8" in rendered

    @staticmethod
    def test_vanished_grades_remain_visible_without_zero_totals() -> None:
        """Retain changed grades and omit unchanged page totals."""
        previous = snapshots.summary_totals(_source("B", "Setindex"))
        current = snapshots.summary_totals(_source("GA", "Disambig"))
        rendered = render_totals(_data(current, previous))
        assert rendered.startswith(
            "条目页（优良级+1、乙级-1）；非条目页（",
        )
        assert "同类索引级-1" in rendered
        assert "+0" not in rendered
        assert "±0" not in rendered
        identical = render_totals(_data(current, current))
        assert identical.startswith(
            "<!-- aranami-assessment-totals schema=2 ",
        )

    def test_redirect_only_changes_omit_unchanged_groups(self) -> None:
        """Show signed redirect changes without zero article deltas."""
        previous = snapshots.summary_totals(_source("B", "Setindex"))
        previous["redirects"] = 13
        for change in (13, -13):
            with self.subTest(change=change):
                current = deepcopy(previous)
                current["redirects"] += change
                rendered = render_totals(_data(current, previous))
                assert rendered.split("<!--", 1)[0] == (
                    f"专题页面数{change:+}：重定向页{change:+}。"
                )

    @staticmethod
    def test_retained_zero_groups_are_compacted_without_recomputing() -> None:
        """Preserve hidden totals while removing visible zero groups."""
        data = _data(snapshots.summary_totals(_source("B")), None)
        hidden = "<!-- aranami-assessment-totals schema=2 original counts -->"
        summary = "专题页面数+13：条目页±0；非条目页±0；重定向页+13。" + hidden
        data.summaries = {data.report_date: summary}
        data.days = {data.report_date: {}}
        data.unchanged = False
        original = (
            '<!-- aranami begin="assessment-log" -->\n'
            f"=== 2026年11月1日 ===\n\n{summary}\n\n無變更\n"
            '<!-- aranami end="assessment-log" -->'
        )
        rendered = update_text(original, data)
        assert "专题页面数+13：重定向页+13。" + hidden in rendered
        assert "±0" not in rendered

    @staticmethod
    def test_legacy_counts_preserve_unknown_importance() -> None:
        """Reclassify saved grades without inventing joint facts."""
        legacy = {
            "count": 3,
            "class": [
                {"value": "B", "count": 1},
                {"value": "重定向", "count": 1},
                {"value": "同类索引", "count": 1},
            ],
            "importance": [{"value": "High", "count": 3}],
            "nonarticles": {"count": 0, "class": []},
            "redirects": 0,
        }
        original = deepcopy(legacy)
        current = snapshots.summary_totals(_source("B", "重定向", "同类索引"))
        rendered = render_totals(_data(current, legacy))
        assert rendered.startswith(
            "<!-- aranami-assessment-totals schema=2 ",
        )
        assert legacy == original
        old = render_totals(_data(legacy, legacy))
        assert "重要度未知" in old
        assert "合计3：" in old
        assert "同类索引级1" in old
        assert "重定向级1" in old

    @staticmethod
    def test_known_chinese_nonarticle_labels_keep_the_grade_suffix() -> None:
        """Render canonical Chinese source labels like their aliases."""
        source = _source("模板", "草稿").with_columns(
            pl.lit(10, dtype=pl.Int64).alias("page_namespace"),
        )
        totals = snapshots.summary_totals(source)
        rendered = render_totals(_data(totals, totals))
        assert "模板级1" in rendered
        assert "草稿级1" in rendered

    @staticmethod
    def test_unknown_prior_counts_are_not_zero() -> None:
        """Keep hidden totals without a comparable prior capture."""
        rendered = render_totals(
            _data(snapshots.summary_totals(_source("B")), None),
        )
        assert rendered.startswith("<!-- aranami-assessment-totals schema=2 ")
        assert "合计1：" in rendered
        assert "无可比记录" not in rendered
        assert "+" not in rendered

    @staticmethod
    def test_unrecognized_legacy_summary_is_not_recomputed() -> None:
        """Preserve custom labels without a parsed migration."""
        data = _data(snapshots.summary_totals(_source("B")), None)
        summary = (
            "专题页面+1：条目+1（自定义（名称）+1）<!-- 条目1 专题页面1 -->"
        )
        data.days = {data.report_date: {}}
        data.summaries = {data.report_date: summary}
        data.unchanged = False
        original = (
            '<!-- aranami begin="assessment-log" -->\n'
            f"=== 2026年11月1日 ===\n\n{summary}\n\n無變更\n"
            '<!-- aranami end="assessment-log" -->'
        )
        assert update_text(original, data) == original

    @staticmethod
    def test_complete_page_facts_recover_legacy_importance() -> None:
        """Use saved page evidence to recover the joint distribution."""
        source = _source("B", "Setindex")
        legacy = {
            "count": 2,
            "class": [
                {"value": "B", "count": 1},
                {"value": "Setindex", "count": 1},
            ],
            "importance": [{"value": "High", "count": 2}],
            "nonarticles": {"count": 0, "class": []},
            "redirects": 0,
        }
        totals = snapshots.summary_totals(source, saved=legacy)
        assert totals["count"] == 1
        assert totals["importance"] == [{"value": "High", "count": 1}]
        assert totals["nonarticles"]["count"] == 1

    @staticmethod
    def test_missing_redirect_flags_preserve_better_saved_facts() -> None:
        """Do not replace known exclusions with legacy missing flags."""
        redirect_id = 2
        source = _source("B", "B").with_columns(
            (pl.col("page_id") == redirect_id)
            .cast(pl.Int64)
            .alias("page_is_redirect"),
        )
        saved = snapshots.summary_totals(source)
        assert (
            snapshots.summary_totals(
                source.drop("page_is_redirect"),
                saved=saved,
            )
            == saved
        )
        unknown = source.with_columns(
            pl.lit(None, dtype=pl.Int64).alias("page_is_redirect"),
        )
        assert snapshots.summary_totals(unknown, saved=saved) == saved
        assert snapshots.summary_totals(unknown) is None

    @staticmethod
    def test_hidden_unknown_labels_cannot_close_the_comment() -> None:
        """Escape comment delimiters in custom grade source text."""
        totals = snapshots.summary_totals(_source("custom--><b>"))
        rendered = render_totals(_data(totals, None))
        assert rendered.count("<!--") == 1
        assert rendered.count("-->") == 1
        assert "custom&#45;&#45;&gt;&lt;b&gt;1" in rendered
