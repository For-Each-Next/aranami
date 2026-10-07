"""Check dated English quality sections and importance compatibility."""

from __future__ import annotations

import datetime as dt
from unittest import TestCase

import mwparserfromhell
import polars as pl

from aranami.jobs.enwp_key_articles import REPORT_SPECS
from aranami.services.zhwiki import enwp_key_articles as reports


def _rows() -> pl.DataFrame:
    """Provide dates whose order differs from grade and title order.

    Returns:
        Enriched rows with dated and unresolved quality listings.
    """
    titles = ["Alpha FA", "Zulu GA", "Beta FL", "Unknown GA", "Old FL"]
    return pl.DataFrame(
        {
            "page_id": [1, 2, 3, 4, 5],
            "en_title": titles,
            "en_display_title": titles,
            "sort_value": titles,
            "item_id": [1, 2, 3, 4, 5],
            "en_class": ["FA", "GA", "FL", "GA", "FL"],
            "en_importance": ["Top"] * len(titles),
            "display_title": titles,
            "final_zh_class": ["请求"] * len(titles),
            "final_zh_importance": ["无"] * len(titles),
            "quality_date": [
                dt.date(2026, 9, 2),
                dt.date(2026, 9, 5),
                dt.date(2026, 9, 2),
                None,
                dt.date(2025, 12, 31),
            ],
        },
        schema_overrides={"quality_date": pl.Date},
    )


class TestQualityRendering(TestCase):
    """Verify report ordering and exact managed-region boundaries."""

    @staticmethod
    def test_dates_descend_across_classes_with_unknowns_last() -> None:
        """Mix FA, FL, and GA by date and use titles for date ties."""
        body = reports.render_quality_body(_rows())
        code = mwparserfromhell.parse(body)
        assert [
            str(heading.title).strip() for heading in code.filter_headings()
        ] == [
            "2026年",
            "2025年",
            "年份未知",
        ]
        templates = [
            template
            for template in code.filter_templates()
            if str(template.name) == reports.REPORT_ITEM_TEMPLATE
        ]
        assert [str(template.get("en").value) for template in templates] == [
            "Zulu GA",
            "Alpha FA",
            "Beta FL",
            "Old FL",
            "Unknown GA",
        ]
        assert [
            str(comment.contents).strip() for comment in code.filter_comments()
        ] == [
            "2026-09-05",
            "2026-09-02",
            "2026-09-02",
            "2025-12-31",
        ]
        assert "wd=2}}<!-- 2026-09-05 -->" in body
        section_count = 3
        assert body.count(reports.REPORT_HEADER) == section_count
        assert body.count(reports.REPORT_FOOTER) == section_count

    @staticmethod
    def test_unknown_only_and_empty_results() -> None:
        """Render unknown dates and clear empty managed bodies."""
        rows = _rows().filter(pl.col("quality_date").is_null())
        body = reports.render_quality_body(rows)
        assert body.startswith("== 年份未知 ==\n\n")
        assert "<!--" not in body
        assert not reports.render_quality_body(rows.clear())

    @staticmethod
    def test_quality_preserves_descriptions_and_requires_only_body() -> None:
        """Keep optional counters and manual surroundings intact."""
        quality = REPORT_SPECS[1]
        prefix = 'Description <!-- aranami begin="count_all" -->99'
        prefix += '<!-- aranami end="count_all" -->\n'
        begin = '<!-- aranami begin="body" note="keep" -->'
        end = '<!-- aranami end="body" -->'
        suffix = "\nManual footer"
        original = prefix + begin + "Old body" + end + suffix
        data = reports.ReportData(_rows(), {"quality": {}}, {})
        updated = reports.build_reports(
            {"quality": original},
            data,
            (quality,),
        )
        assert updated["quality"] == (
            prefix
            + begin
            + "\n"
            + reports.render_quality_body(_rows())
            + "\n"
            + end
            + suffix
        )
        assert (
            reports.update_page_text(
                begin + "old" + end,
                "",
                {},
                quality,
            )
            == begin + "\n\n" + end
        )

    @staticmethod
    def test_importance_remains_alphabetical_without_date_comments() -> None:
        """Keep the importance layout even when rows contain dates."""
        important = REPORT_SPECS[0]
        counters = "".join(
            f'<!-- aranami begin="{name}" -->99<!-- aranami end="{name}" -->'
            for name in ("count_all", "count_top", "count_high")
        )
        original = counters + '<!-- aranami begin="body" -->old'
        original += '<!-- aranami end="body" -->'
        data = reports.ReportData(_rows(), {"important": {}}, {})
        updated = reports.build_reports(
            {"important": original},
            data,
            (important,),
        )["important"]
        expected_body = reports.render_body(_rows().drop("quality_date"))
        assert "\n" + expected_body + "\n" in updated
        assert "== A ==" in updated
        assert "== Z ==" in updated
        assert "入选" not in updated
        assert "<!-- 2026" not in updated
        assert '<!-- aranami begin="count_all" -->5' in updated
        assert '<!-- aranami begin="count_top" -->5' in updated
        assert '<!-- aranami begin="count_high" -->0' in updated
