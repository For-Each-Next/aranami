"""Recover audited historical HOT formats with explicit evidence."""

# Source periods, counts, and revision IDs are exact audited evidence.
# Preserve original single-line wikitext and Chinese punctuation.
# ruff: file-ignore[magic-value-comparison, ambiguous-unicode-character-string, line-too-long]

import datetime as dt
from unittest import TestCase

from shared.newsletter import Issue
from tasks.zhwiki.legacy.newsletter_popularity import (
    collect_legacy_popularity,
    parse_popularity,
)

_OLD = """== 条目浏览量 ==
<div class="pageview_group"><div>
=== 月浏览量 ===
<div class="pageviews" period="1 month" limit="100">
: '''統計期間：'''2024年1月1日 — 2024年1月31日<small>（計31日）</small>
: '''總瀏覽量：'''7,022,332次<small>（日均226,526次）</small>
# {{al-iw | 1 = 劉偉健 | c = Start | i = Low }} — 209,157 <small>(6,747)</small>
# {{al-iw | 1 = 幻獸帕魯 | c = C | i = Unknown }} — 177,862 <small>(5,737)</small>
</div></div></div>
"""
_PRINTED = """=== 月瀏覽量 ===
<div class="hlist report_header">
* '''統計期間：'''2025年11月1日—2025年11月30日<small>（計30日）</small>
* '''總瀏覽量：'''12,441個頁面，7,831,656次瀏覽<small>（日均261,055次）</small>
</div>
<div class="report_body report_body_vg report_body_top_100">
# {{PJ:VG/HOT/item|英雄联盟2025赛季全球总决赛|初|低|start=2025-11-01|end=2025-11-30|views=197923|rank=1}}
# {{PJ:VG/HOT/item|Faker|丙|高|start=2025-11-01|end=2025-11-30|views=190007|rank=2}}
</div>
"""
_PLAIN = """== 条目浏览量 ==
=== 月瀏覽量 ===
* '''統計期間：'''2024年9月1日—2024年9月30日<small>（計30日）</small>
* '''總瀏覽量：'''6,406,461次<small>（日均213,548次）</small>
# {{PJ:VG/HOT/item|黑神话：悟空|丙|中|start=2024-09-01|end=2024-09-30|views=209457|rank=1|old_rank=1}}
# {{PJ:VG/HOT/item|英雄联盟2024赛季全球总决赛|小作品|低|start=2024-09-01|end=2024-09-30|views=80377|rank=2|old_rank=56}}
"""


class LegacyPopularityFormatTests(TestCase):
    """Preserve source scope, intervals, ranking, and missing counts."""

    @staticmethod
    def test_nested_2024_rows_preserve_unknown_population() -> None:
        """Read oldid80762498's nested block without a page count."""
        result = parse_popularity(_OLD, "monthly")
        assert result["start"] == "2024-01-01"
        assert result["end"] == "2024-01-31"
        assert result["article_count"] is None
        assert result["total_views"] == 7022332
        assert result["items"][0] == {"title": "劉偉健", "views": 209157}
        assert parse_popularity(_OLD, "yearly") is None

    @staticmethod
    def test_printed_2025_header_retains_counts_and_tied_ranks() -> None:
        """Read oldid90417271's prose header and competition ranks."""
        source = _PRINTED.replace("views=190007|rank=2", "views=197923|rank=1")
        result = parse_popularity(source, "monthly")
        assert result["article_count"] == 12441
        assert result["total_views"] == 7831656
        assert len(result["items"]) == 2
        assert result["items"][1]["views"] == 197923

    def test_plain_project_month_retains_evidence_and_revision(self) -> None:
        """Recover oldid84442851 and its optional content wrapper."""
        for source in (
            _PLAIN,
            _PLAIN.replace(
                "=== 月瀏覽量 ===",
                '=== 月瀏覽量 ===\n<div class="content">',
            )
            + "</div>\n",
        ):
            with self.subTest(source=source):
                parsed = parse_popularity(source, "monthly")
                assert parsed["start"] == "2024-09-01"
                assert parsed["end"] == "2024-09-30"
                assert parsed["article_count"] is None
                assert parsed["total_views"] == 6406461
                assert len(parsed["items"]) == 2
                result = collect_legacy_popularity(
                    lambda *_args: "",
                    Issue(2025, 1),
                    dt.datetime(2026, 11, 2, tzinfo=dt.UTC),
                    revision_history=lambda *_args, source=source: [
                        {
                            "text": source,
                            "revision": 84442851,
                            "timestamp": dt.datetime(
                                2024,
                                10,
                                3,
                                0,
                                1,
                                19,
                                tzinfo=dt.UTC,
                            ),
                        },
                    ],
                    months_needed={9},
                    include_annual=False,
                )
                assert result["monthly_popularity"] == [
                    {
                        "month": 9,
                        "days": 30,
                        "title": "黑神话：悟空",
                        "views": 209457,
                        "revision_id": 84442851,
                    },
                ]

    def test_plain_fallback_requires_scope_and_complete_valid_rows(
        self,
    ) -> None:
        """Reject wrong scope, malformed rows, and conflicting tags."""
        for source in (
            _PLAIN.replace("条目浏览量", "工作組月瀏覽量"),
            _PLAIN.replace("月瀏覽量", "史克威爾艾尼克斯月瀏覽量"),
            _PLAIN.replace("|end=2024-09-30", "|end=2024-09-29"),
            _PLAIN.replace("|rank=2", "|rank=9"),
            _PLAIN + "# {{unrelated|unparsed ranking row}}\n",
            "== 条目浏览量 ==\n"
            + _PRINTED.replace("report_body_vg", "report_body_se"),
        ):
            with self.subTest(source=source):
                assert parse_popularity(source, "monthly") is None

    def test_scope_period_and_complete_rows_are_required(self) -> None:
        """Reject taskforces, conflicting dates, and partial parsing."""
        for source in (
            _PRINTED.replace("report_body_vg", "report_body_se"),
            _PRINTED.replace("計30日", "計31日"),
            _PRINTED.replace("12,441個頁面，7,831,656", "unknown"),
            _PRINTED.replace("start=2025-11-01", "start=2025-11-02"),
            _PRINTED.replace("|rank=2", "|rank=9"),
            _PRINTED.replace("|views=190007", "|views=unknown"),
            _OLD.replace("条目浏览量", "工作組月瀏覽量"),
            _OLD.replace("177,862 <small>", "unknown <small>"),
        ):
            with self.subTest(source=source):
                assert parse_popularity(source, "monthly") is None

    @staticmethod
    def test_year_end_prose_requires_observed_population() -> None:
        """Retain audited year-end totals and unknown observations."""
        source = (
            _PRINTED
            .replace("月瀏覽量", "年瀏覽量")
            .replace("2025年11月1日", "2024年1月1日")
            .replace("2025年11月30日", "2024年12月31日")
            .replace("計30日", "計366日")
            .replace("2025-11-01", "2024-01-01")
            .replace("2025-11-30", "2024-12-31")
            .replace("12,441", "11,952")
            .replace("7,831,656", "82,433,384")
            .replace("英雄联盟2025赛季全球总决赛", "黑神话：悟空")
            .replace("197923", "1186298")
            .replace("Faker", "英伟达")
            .replace("190007", "656176")
        )
        result = parse_popularity(source, "yearly")
        assert result["start"] == "2024-01-01"
        assert result["end"] == "2024-12-31"
        assert result["article_count"] == 11952
        assert result["total_views"] == 82433384
        assert result["items"][0] == {
            "title": "黑神话：悟空",
            "views": 1186298,
        }
        assert (
            parse_popularity(
                source.replace("11,952個頁面，", ""),
                "yearly",
            )
            is None
        )

    @staticmethod
    def test_exact_month_import_keeps_original_revision() -> None:
        """Import the old source with its actual revision."""
        now = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
        rows = [
            {
                "text": _PRINTED,
                "revision": 90417271,
                "timestamp": dt.datetime(
                    2025,
                    12,
                    1,
                    23,
                    20,
                    46,
                    tzinfo=dt.UTC,
                ),
            },
        ]
        result = collect_legacy_popularity(
            lambda *_args: "",
            Issue(2026, 1),
            now,
            revision_history=lambda *_args: rows,
            months_needed={11},
            include_annual=False,
        )
        assert result["monthly_popularity"] == [
            {
                "month": 11,
                "days": 30,
                "title": "英雄联盟2025赛季全球总决赛",
                "views": 197923,
                "revision_id": 90417271,
            },
        ]
