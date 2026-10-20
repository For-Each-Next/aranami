"""Verify project maintenance categories and complete article lists."""

# The project uses unittest for source-failure assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from pywikibot.site import BaseSite, Namespace

from backend.website.backlog import collect_backlog

_MEMBER_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "full_title": pl.String,
}
_CATEGORY_SCHEMA = {"page_id": pl.Int64, "name": pl.String}


class _OfflineSite(BaseSite):
    """Resolve namespace identities without contacting Wikipedia."""

    def namespace(
        self,
        number: int,
        *,
        all_ns: bool = False,
    ) -> str | Namespace:
        """Return the builtin namespace name or metadata.

        Args:
            number: Requested namespace identifier.
            all_ns: Whether to return the complete namespace object.

        Returns:
            The namespace metadata or preferred title prefix.
        """
        namespace = self.namespaces[number]
        return namespace if all_ns else namespace[0]


class TestMaintenanceBacklog(TestCase):
    """Preserve measured category workloads within project scope."""

    @staticmethod
    def test_dated_categories_deduplicate_and_keep_project_scope() -> None:
        """Count each stable article once per group and once overall."""
        site = _OfflineSite("zh", "wikipedia")
        members = pl.DataFrame(
            [
                (1, 0, "Z Game"),
                (2, 0, "A Game"),
                (3, 0, "星空 & 海洋"),
                (4, 14, "Category:Games"),
                (1, 0, "Z Game"),
            ],
            schema=_MEMBER_SCHEMA,
            orient="row",
        )
        categories = pl.DataFrame(
            [
                (1, "自2026年10月缺少来源的条目"),
                (1, "缺少来源的条目"),
                (1, "自2020年1月缺少来源的条目"),
                (1, "需补充来源的条目"),
                (2, "自2026年9月需补充来源的条目"),
                (2, "自2026年9月需补充来源的条目"),
                (3, "所有主題不滿足收錄標準的條目"),
                (3, "自2026年10月主題不滿足收錄標準的條目"),
                (4, "缺少来源的条目"),
                (99, "缺少来源的条目"),
                (2, "缺少来源的条目的子分类"),
                (2, "自2026年10月缺少来源的条目/沙盒"),
                (2, "自2026年10月电子游戏"),
                (2, "自2026年10月所有主題不滿足收錄標準的條目"),
            ],
            schema=_CATEGORY_SCHEMA,
            orient="row",
        )
        with patch(
            "backend.website.backlog.page_categories",
            return_value=categories,
        ) as read:
            result = collect_backlog(site, members)
        read.assert_called_once_with(site, [1, 2, 3])
        assert result["total"] == 3  # ruff: ignore[magic-value-comparison]
        groups = {group["key"]: group for group in result["groups"]}
        assert groups["unreferenced"]["count"] == 1
        assert groups["unreferenced"]["items"] == [
            {
                "page_id": 1,
                "title": "Z Game",
                "assessment_class": None,
                "assessment_importance": None,
                "issues": ["unreferenced", "references"],
                "oldest_issue_month": "2020-01",
                "oldest_issue_category": "自2020年1月缺少来源的条目",
                "url": "https://zh.wikipedia.org/wiki/Z_Game",
            },
        ]
        assert groups["references"]["count"] == 2  # ruff: ignore[magic-value-comparison]
        assert groups["references"]["items"][1]["oldest_issue_category"] == (
            "自2020年1月缺少来源的条目"
        )
        assert [item["title"] for item in groups["references"]["items"]] == [
            "A Game",
            "Z Game",
        ]
        assert groups["notability"]["count"] == 1
        assert groups["notability"]["items"][0]["url"] == (
            "https://zh.wikipedia.org/wiki/"
            "%E6%98%9F%E7%A9%BA_%26_%E6%B5%B7%E6%B4%8B"
        )
        assert groups["cleanup"]["count"] == 0
        assert groups["cleanup"]["items"] == []
        assert (
            dt.datetime.fromisoformat(result["observed_at"]).utcoffset()
            == dt.timedelta()
        )

    @staticmethod
    def test_all_verified_category_roots_accept_their_monthly_forms() -> None:
        """Retain the checked Chinese message-box category contract."""
        roots = {
            "unreferenced": "缺少来源的条目",
            "references": "需补充来源的条目",
            "footnotes": "缺少注脚的条目",
            "unreliable": "可能有不可靠來源的條目",
            "cleanup": "需要清理的条目",
            "wikify": "需要维基化的页面",
            "update": "待更新条目",
            "dead_links": "带有失效链接的条目",
            "notability": "主題不滿足收錄標準的條目",
            "neutrality": "中立性有争议的作品",
            "advert": "需要清理的疑似广告条目",
            "translation": "粗劣翻译",
        }
        members = pl.DataFrame(
            [(1, 0, "Game")],
            schema=_MEMBER_SCHEMA,
            orient="row",
        )
        categories = pl.DataFrame(
            [(1, "自1999年1月" + category) for category in roots.values()],
            schema=_CATEGORY_SCHEMA,
            orient="row",
        )
        with patch(
            "backend.website.backlog.page_categories",
            return_value=categories,
        ):
            result = collect_backlog(_OfflineSite("zh", "wikipedia"), members)
        assert result["total"] == 1
        old_groups = [
            group for group in result["groups"] if group["key"] in roots
        ]
        assert {
            group["key"]: group["category"] for group in old_groups
        } == roots
        assert all(group["count"] == 1 for group in old_groups)
        assert all(len(group["items"]) == 1 for group in old_groups)
        assert all(
            group["source_url"].startswith(
                "https://zh.wikipedia.org/wiki/Category:",
            )
            for group in result["groups"]
        )

    @staticmethod
    def test_detailed_taxonomy_uses_verified_chinese_category_forms() -> None:
        """Expose detailed tasks using actual Chinese tags."""
        roots = {
            "unsourced": "有未列明来源语句的条目",
            "blp_sources": "缺少可靠来源的条目",
            "copyedit": "需要校對的頁面",
            "rewrite": "维基百科需要重写的条目",
            "expand": "扩充中的条目",
            "lead": "需要清理導言的頁面",
            "style": "维基百科需要风格编辑的条目",
            "trivia": "需要清理的內容瑣碎條目",
            "fiction": "需要區分事實與虛構的條目",
            "original_research": "可能带有原创研究的条目",
            "globalize": "部分地区的观点",
            "coi": "可能存在利益衝突的維基百科條目",
            "weasel": "有非常模棱两可或者十分空泛语句的条目",
            "orphan": "缺少链入页面的条目",
            "underlinked": "需要更多内部链接的条目",
            "uncategorized": "缺少頁面分類的頁面",
            "merge": "需要合併的條目",
            "split": "需要拆分的条目",
            "accuracy": "准确性有争议的作品",
        }
        undated = {
            "verify": "需要查證來源的維基百科條目",
            "selfpromotion": "含有自我宣傳內容的作品",
            "expert": "所有需要專家關注的頁面",
        }
        categories = pl.DataFrame(
            [
                *((1, "自2025年3月" + root) for root in roots.values()),
                *((1, root) for root in undated.values()),
            ],
            schema=_CATEGORY_SCHEMA,
            orient="row",
        )
        with patch(
            "backend.website.backlog.page_categories",
            return_value=categories,
        ):
            result = collect_backlog(
                _OfflineSite("zh", "wikipedia"),
                pl.DataFrame(
                    [(1, 0, "Game")],
                    schema=_MEMBER_SCHEMA,
                    orient="row",
                ),
            )
        groups = {group["key"]: group for group in result["groups"]}
        expected = {**roots, **undated}
        assert {key: groups[key]["category"] for key in expected} == expected
        assert all(groups[key]["count"] == 1 for key in expected)
        assert result["total"] == 1
        assert groups["verify"]["items"][0]["oldest_issue_month"] == "2025-03"
        assert set(groups["expert"]["items"][0]["issues"]) == set(expected)

    @staticmethod
    def test_verified_aliases_do_not_invent_dates() -> None:
        """Reject fabricated monthly aggregate categories."""
        categories = pl.DataFrame(
            [
                (1, "所有扩充中的条目"),
                (1, "扩充中的条目"),
                (1, "缺失序言章节的条目"),
                (1, "全部需要合併的條目頁面"),
                (1, "其他需要專家關注的頁面"),
                (1, "所有需要專家關注的頁面"),
                (1, "需要查證來源的維基百科條目"),
                (1, "含有自我宣傳內容的作品"),
                (2, "自2026年9月所有扩充中的条目"),
                (2, "自2026年9月缺失序言章节的条目"),
                (2, "自2026年9月全部需要合併的條目頁面"),
                (2, "自2026年9月所有需要專家關注的頁面"),
                (2, "自2026年9月需要查證來源的維基百科條目"),
                (2, "自2026年9月含有自我宣傳內容的作品"),
                (2, "自2026年0月扩充中的条目"),
                (2, "自2026年13月扩充中的条目"),
                (2, "自0000年1月扩充中的条目"),
                (2, "自2026年9月扩充中的条目/沙盒"),
            ],
            schema=_CATEGORY_SCHEMA,
            orient="row",
        )
        with patch(
            "backend.website.backlog.page_categories",
            return_value=categories,
        ):
            result = collect_backlog(
                _OfflineSite("zh", "wikipedia"),
                pl.DataFrame(
                    [(1, 0, "Tagged"), (2, 0, "Unmatched")],
                    schema=_MEMBER_SCHEMA,
                    orient="row",
                ),
            )
        assert result["total"] == 1
        matched = [group for group in result["groups"] if group["count"]]
        assert {group["key"] for group in matched} == {
            "expand",
            "lead",
            "merge",
            "expert",
            "verify",
            "selfpromotion",
        }
        assert all(group["count"] == 1 for group in matched)
        assert all(
            group["items"][0]["oldest_issue_month"] is None
            for group in matched
        )
        assert all(
            group["items"][0]["oldest_issue_category"] is None
            for group in matched
        )

    @staticmethod
    def test_detailed_rows_preserve_assessments_and_all_issues() -> None:
        """Join assessments with the earliest known marker month."""
        members = pl.DataFrame(
            [
                (1, 0, "Game", "初", "低"),
                (1, 0, "Game", None, None),
                (2, 0, "Conflicting", "乙", "高"),
                (2, 0, "Conflicting", "丙", "高"),
                (3, 0, "Unknown", "", None),
            ],
            schema={
                **_MEMBER_SCHEMA,
                "pa_class": pl.String,
                "pa_importance": pl.String,
            },
            orient="row",
        )
        categories = pl.DataFrame(
            [
                (1, "自2026年10月扩充中的条目"),
                (1, "所有扩充中的条目"),
                (1, "自2020年12月有未列明来源语句的条目"),
                (1, "自2020年2月需要专业人士关注的页面"),
                (1, "所有需要專家關注的頁面"),
                (1, "自1999年1月未知维护分类"),
                (2, "有未列明来源语句的条目"),
                (3, "扩充中的条目"),
            ],
            schema=_CATEGORY_SCHEMA,
            orient="row",
        )
        with patch(
            "backend.website.backlog.page_categories",
            return_value=categories,
        ):
            result = collect_backlog(_OfflineSite("zh", "wikipedia"), members)
        groups = {group["key"]: group for group in result["groups"]}
        item = next(
            row for row in groups["expand"]["items"] if row["page_id"] == 1
        )
        assert item["assessment_class"] == "初"
        assert item["assessment_importance"] == "低"
        assert item["issues"] == ["unsourced", "expand", "expert"]
        assert item["oldest_issue_month"] == "2020-02"
        assert item == groups["expert"]["items"][0]
        conflicting = groups["unsourced"]["items"][0]
        assert conflicting["assessment_class"] is None
        assert conflicting["assessment_importance"] == "高"
        assert conflicting["oldest_issue_month"] is None
        unknown = groups["expand"]["items"][1]
        assert unknown["assessment_class"] is None
        assert unknown["assessment_importance"] is None

    @staticmethod
    def test_chinese_translation_worklists_keep_real_category_dates() -> None:
        """Collect expansion and translation proofreading tags."""
        categories = pl.DataFrame(
            [
                (1, "需要從英語維基百科翻譯的條目"),
                (1, "自2024年7月需要從英語維基百科翻譯的條目"),
                (1, "需要校對的翻譯頁面"),
                (2, "自2026年10月需要從日語維基百科翻譯的條目"),
                (3, "需要校對的翻譯頁面"),
                (3, "自1999年1月需要校對的翻譯頁面"),
                (4, "需要從英語維基百科翻譯的條目的子分類"),
            ],
            schema=_CATEGORY_SCHEMA,
            orient="row",
        )
        with patch(
            "backend.website.backlog.page_categories",
            return_value=categories,
        ):
            result = collect_backlog(
                _OfflineSite("zh", "wikipedia"),
                pl.DataFrame(
                    [
                        (1, 0, "English"),
                        (2, 0, "Japanese"),
                        (3, 0, "Proofread"),
                        (4, 0, "Unmatched"),
                    ],
                    schema=_MEMBER_SCHEMA,
                    orient="row",
                ),
            )
        groups = {group["key"]: group for group in result["groups"]}
        english = groups["expand_english"]["items"][0]
        japanese = groups["expand_japanese"]["items"][0]
        proofreading = groups["proofread_translation"]["items"][1]
        assert groups["expand_english"]["count"] == 1
        assert groups["expand_japanese"]["count"] == 1
        assert english["oldest_issue_month"] == "2024-07"
        assert english["issues"] == ["expand_english", "proofread_translation"]
        assert japanese["oldest_issue_month"] == "2026-10"
        assert proofreading["oldest_issue_month"] is None
        assert proofreading["issues"] == ["proofread_translation"]
        assert result["total"] == 3  # ruff: ignore[magic-value-comparison]

    @staticmethod
    def test_successful_empty_membership_retains_zero_groups() -> None:
        """Keep successful absence separate from collection failures."""
        with patch(
            "backend.website.backlog.page_categories",
            return_value=pl.DataFrame(schema=_CATEGORY_SCHEMA),
        ) as read:
            site = _OfflineSite("zh", "wikipedia")
            result = collect_backlog(
                site,
                pl.DataFrame(schema=_MEMBER_SCHEMA),
            )
        read.assert_called_once_with(site, [])
        assert result["total"] == 0
        assert result["groups"]
        assert all(group["count"] == 0 for group in result["groups"])
        assert all(group["items"] == [] for group in result["groups"])

    @staticmethod
    def test_complete_lists_are_not_truncated_to_a_preview() -> None:
        """Preserve every matching article for in-place continuation."""
        size = 75
        members = pl.DataFrame(
            [
                (number, 0, f"Game {number:02}")
                for number in range(1, size + 1)
            ],
            schema=_MEMBER_SCHEMA,
            orient="row",
        )
        categories = pl.DataFrame(
            [
                (number, "自2026年10月需要清理的条目")
                for number in range(1, size + 1)
            ],
            schema=_CATEGORY_SCHEMA,
            orient="row",
        )
        with patch(
            "backend.website.backlog.page_categories",
            return_value=categories,
        ):
            result = collect_backlog(_OfflineSite("zh", "wikipedia"), members)
        cleanup = next(
            group for group in result["groups"] if group["key"] == "cleanup"
        )
        assert result["total"] == cleanup["count"] == size
        assert [item["page_id"] for item in cleanup["items"]] == list(
            range(1, size + 1),
        )

    def test_failed_replica_is_not_a_measured_zero(self) -> None:
        """Propagate source failure so orchestration can mark null."""
        with (
            patch(
                "backend.website.backlog.page_categories",
                side_effect=RuntimeError("Replica unavailable."),
            ),
            self.assertRaisesRegex(RuntimeError, "Replica unavailable"),
        ):
            collect_backlog(
                _OfflineSite("zh", "wikipedia"),
                pl.DataFrame(schema=_MEMBER_SCHEMA),
            )

    def test_oversized_group_fails_without_truncating_observations(
        self,
    ) -> None:
        """Reject groups above the shared snapshot record bound."""
        members = pl.DataFrame(
            [(1, 0, "First"), (2, 0, "Second")],
            schema=_MEMBER_SCHEMA,
            orient="row",
        )
        with (
            patch(
                "backend.website.backlog.page_categories",
                return_value=pl.DataFrame(
                    [(1, "需要清理的条目"), (2, "需要清理的条目")],
                    schema=_CATEGORY_SCHEMA,
                    orient="row",
                ),
            ),
            patch("backend.website.backlog._MAX_GROUP_ITEMS", 1),
            self.assertRaisesRegex(ValueError, "snapshot record limit"),
        ):
            collect_backlog(_OfflineSite("zh", "wikipedia"), members)


class TestConfiguredMaintenance(TestCase):
    """Apply local category/template rules to article and talk tags."""

    @staticmethod
    def test_explicit_dated_category_matches_only_its_exact_month() -> None:
        """Match exact category names without monthly expansion."""
        name = "自2020年1月需补充来源的条目"
        config = {
            "schema": 1,
            "sections": [
                {
                    "key": "section",
                    "label": "分组",
                    "tasks": [
                        {
                            "key": "task",
                            "label": "指定月份",
                            "rules": [{"kind": "category", "name": name}],
                        },
                    ],
                },
            ],
        }
        members = pl.DataFrame(
            [(1, 0, "First"), (2, 0, "Second"), (3, 0, "Third")],
            schema=_MEMBER_SCHEMA,
            orient="row",
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch(
                "backend.website.backlog.page_categories",
                return_value=pl.DataFrame(
                    [
                        (1, name),
                        (2, "自2020年2月需补充来源的条目"),
                        (3, name + "/沙盒"),
                    ],
                    schema=_CATEGORY_SCHEMA,
                    orient="row",
                ),
            ):
                result = collect_backlog(
                    _OfflineSite("zh", "wikipedia"),
                    members,
                    config_path=path,
                )
        group = result["groups"][0]
        assert group["count"] == result["total"] == 1
        assert group["items"][0]["page_id"] == 1
        assert group["items"][0]["oldest_issue_month"] == "2020-01"

    @staticmethod
    def test_custom_rules_keep_scope_and_configured_labels() -> None:
        """Match tag scopes and combine their article issues."""
        labels = {"en": "Sources", "zh-hans": "来源"}
        config = {
            "schema": 1,
            "sections": [
                {
                    "key": "custom_section",
                    "label": "自定义分组",
                    "tasks": [
                        {
                            "key": "custom_sources",
                            "label": labels,
                            "rules": [
                                {
                                    "kind": "category",
                                    "name": "Category:Needs sources",
                                    "monthly": True,
                                },
                                {
                                    "kind": "template",
                                    "name": "Template:Needs review",
                                    "talk_page": True,
                                },
                            ],
                        },
                        {
                            "key": "custom_template",
                            "label": "模板维护",
                            "rules": [
                                {
                                    "kind": "template",
                                    "name": "Template:Article warning",
                                },
                            ],
                        },
                        {
                            "key": "talk_category",
                            "label": "讨论页维护",
                            "rules": [
                                {
                                    "kind": "category",
                                    "name": "Talk warning",
                                    "talk_page": True,
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        members = pl.DataFrame(
            [(1, 0, "First"), (2, 0, "Second"), (3, 14, "Category:Other")],
            schema=_MEMBER_SCHEMA,
            orient="row",
        )
        scopes = {
            ("template", True): [(2, "Needs_review"), (99, "Needs_review")],
            ("template", False): [(1, "Article_warning"), (2, "Unrelated")],
            ("category", True): [(1, "Talk_warning"), (2, "Needs_sources")],
        }

        def tags(
            _site: BaseSite,
            _ids: list[int],
            *,
            kind: str,
            talk_page: bool,
        ) -> pl.DataFrame:
            """Return independent source scopes keyed by subject ID."""
            return pl.DataFrame(
                scopes[kind, talk_page],
                schema=_CATEGORY_SCHEMA,
                orient="row",
            )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with (
                patch(
                    "backend.website.backlog.page_categories",
                    return_value=pl.DataFrame(
                        [
                            (1, "自2024年5月Needs_sources"),
                            (99, "Needs_sources"),
                        ],
                        schema=_CATEGORY_SCHEMA,
                        orient="row",
                    ),
                ) as categories,
                patch(
                    "backend.website.backlog.maintenance_tags",
                    side_effect=tags,
                ) as read,
            ):
                site = _OfflineSite("zh", "wikipedia")
                result = collect_backlog(site, members, config_path=path)
        groups = {group["key"]: group for group in result["groups"]}
        categories.assert_called_once_with(site, [1, 2])
        assert read.call_count == 3  # ruff: ignore[magic-value-comparison]
        assert all(call.args[1] == [1, 2] for call in read.call_args_list)
        assert list(groups) == [
            "custom_sources",
            "custom_template",
            "talk_category",
        ]
        sources = groups["custom_sources"]
        assert sources["label"] == labels
        assert sources["section_key"] == "custom_section"
        assert sources["section_label"] == "自定义分组"
        assert sources["category"] == "Needs sources"
        assert sources["count"] == 2  # ruff: ignore[magic-value-comparison]
        assert sources["items"][0]["issues"] == [
            "custom_sources",
            "custom_template",
            "talk_category",
        ]
        assert sources["items"][0]["oldest_issue_month"] == "2024-05"
        assert sources["items"][1]["oldest_issue_month"] is None
        assert groups["custom_template"]["source_url"] == (
            "https://zh.wikipedia.org/wiki/Template:Article_warning"
        )
        assert result["total"] == 2  # ruff: ignore[magic-value-comparison]

    def test_missing_or_invalid_configuration_never_queries_sources(
        self,
    ) -> None:
        """Fail collection instead of silently substituting defaults."""
        members = pl.DataFrame(schema=_MEMBER_SCHEMA)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance.json"
            with (
                patch("backend.website.backlog.page_categories") as categories,
                patch("backend.website.backlog.maintenance_tags") as tags,
                self.assertRaises(OSError),
            ):
                collect_backlog(
                    _OfflineSite("zh", "wikipedia"),
                    members,
                    config_path=path,
                )
            path.write_text('{"schema": 2}', encoding="utf-8")
            with self.assertRaises(ValueError):
                collect_backlog(
                    _OfflineSite("zh", "wikipedia"),
                    members,
                    config_path=path,
                )
        categories.assert_not_called()
        tags.assert_not_called()
