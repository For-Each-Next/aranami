"""Verify the manual character report without Wikimedia requests."""

from datetime import date, timedelta
from unittest import TestCase
from unittest.mock import Mock, patch

import mwparserfromhell
import polars as pl
import pywikibot

from aranami.services.zhwiki import characters
from aranami.sources import wikidata


class TestCharacterFranchises(TestCase):
    """Verify truthy franchise relationships and multilingual names."""

    @staticmethod
    def test_best_rank_claims_use_only_concrete_item_targets() -> None:
        """Prefer best-rank values and consistently order targets."""
        item = Mock(spec=pywikibot.ItemPage)
        targets = [Mock(spec=pywikibot.ItemPage) for _ in range(4)]
        for target, qid in zip(
            targets,
            ("Q30", "Q20", "Q10", "Q5"),
            strict=True,
        ):
            target.id = qid
        claims = [
            Mock(
                rank=rank,
                snaktype="value",
                getTarget=Mock(return_value=target),
            )
            for rank, target in zip(
                ("normal", "preferred", "preferred", "deprecated"),
                targets,
                strict=True,
            )
        ]
        claims.append(Mock(rank="preferred", snaktype="somevalue"))
        item.claims = {"P8345": claims}
        assert wikidata._franchise_targets(item) == [10, 20]  # ruff: ignore[private-member-access]
        claims[-1].getTarget.assert_not_called()

    @staticmethod
    def test_chinese_titles_precede_labels_and_english_fallbacks() -> None:
        """Preserve the title, label, and item-ID fallback order."""
        chinese_titles = pl.DataFrame({"item_id": [1], "zh_title": ["中文頁"]})
        english_titles = pl.DataFrame(
            {"item_id": [1, 2, 3], "zh_title": ["Page", "Page2", "Page3"]},
        )
        chinese_labels = pl.DataFrame(
            {"item_id": [1, 2], "label": ["中文名", "中文名2"]},
        )
        english_labels = pl.DataFrame(
            {
                "item_id": [1, 2, 3, 4],
                "label": ["Name", "Name2", "Name3", "Name4"],
            },
        )
        with (
            patch(
                "aranami.services.zhwiki.characters.fetch_wikidata_sitelinks",
                side_effect=[chinese_titles, english_titles],
            ),
            patch(
                "aranami.services.zhwiki.characters.fetch_wikidata_labels",
                side_effect=[chinese_labels, english_labels],
            ),
        ):
            names = characters._franchise_names([1, 2, 3, 4, 5])  # ruff: ignore[private-member-access]
        assert names["franchise"].to_list() == [
            "中文頁",
            "中文名2",
            "Page3",
            "Name4",
            "d:Q5",
        ]

    @staticmethod
    def test_empty_character_ids_do_not_open_wikidata() -> None:
        """Avoid repository access when there are no linked items."""
        site = Mock()
        assert wikidata.fetch_franchises(site, []).is_empty()
        site.data_repository.assert_not_called()


class TestCharacterReport(TestCase):
    """Verify project intersection, class filtering, and report data."""

    @staticmethod
    def test_builder_preserves_metadata_and_uses_sixty_complete_days() -> None:
        """Build only eligible project members and retain metadata."""
        site = Mock()
        today = date(2026, 10, 3)
        members = pl.DataFrame(
            {
                "page_id": [1, 2, 3],
                "full_title": ["角色甲", "角色乙", "角色丙"],
                "page_title": ["角色甲", "角色乙", "角色丙"],
                "pa_class": ["优良", "小作品", "重定向"],
                "pa_importance": ["高", "低", "低"],
                "page_len": [1000, 2000, 3000],
                "latest_timestamp": ["20260101120000"] * 3,
                "wikibase_qid": [10, 20, 30],
                "defaultsort": ["A", "B", "C"],
            },
        )
        with (
            patch(
                "aranami.services.zhwiki.characters.query_pages_by_wikiproject",
                side_effect=[pl.DataFrame({"page_id": [1, 3]}), members],
            ),
            patch(
                "aranami.services.zhwiki.characters.query_tags",
                side_effect=lambda _site, frame, _tags: frame.with_columns(
                    pl.lit(["FAC", "PR"]).alias("tags"),
                ),
            ),
            patch(
                "aranami.services.zhwiki.characters.aggregate_views",
                return_value=pl.DataFrame({
                    "title": ["角色甲"],
                    "views": [100],
                }),
            ) as views,
            patch(
                "aranami.services.zhwiki.characters.fetch_franchises",
                return_value=pl.DataFrame(
                    {"wikibase_qid": [10], "franchise_qid": [100]},
                ),
            ) as franchises,
            patch(
                "aranami.services.zhwiki.characters._franchise_names",
                return_value=pl.DataFrame(
                    {"franchise_qid": [100], "franchise": ["作品系列"]},
                ),
            ),
        ):
            report, summary = characters.build_report(site, today)
        views.assert_called_once_with(
            site,
            ["角色甲"],
            [(today - timedelta(days=60), today)],
        )
        franchises.assert_called_once_with(site, [10])
        templates = mwparserfromhell.parse(report).filter_templates()
        assert [str(template.name) for template in templates] == [
            "PJ:VG/CHAR/header",
            "PJ:VG/CHAR/item",
            "PJ:VG/CHAR/footer",
        ]
        item = templates[1]
        assert str(item.get("1").value) == "角色甲"
        assert str(item.get("tags").value) == "FAC, PR"
        assert str(item.get("views").value) == "100"
        assert str(item.get("qid").value) == "10"
        assert str(item.get("franchise").value) == "作品系列"
        assert summary == "{{PJ:VG/CHAR/summary|优良=1}}"
