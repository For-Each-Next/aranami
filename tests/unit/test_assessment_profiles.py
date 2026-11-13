"""Verify assessment labels, classification, and project ranking."""

# Test the renderer's direct label projection without wiki clients.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

from dataclasses import asdict
from unittest import TestCase

import polars as pl

from data.project_assessments import summary_totals
from shared import assessment_profiles
from shared.assessment_profiles import (
    OBSERVED_PROFILE,
    ZHWIKI_VIDEO_GAMES,
    AssessmentProfile,
    ClassAssessmentLabel,
    assessment_profile,
)
from shared.config import routine_configuration
from updater.services.zhwiki import assessment_log


class TestAssessmentProfiles(TestCase):
    """Keep raw labels, codes, full names, and short names distinct."""

    @staticmethod
    def test_required_class_examples_are_explicit() -> None:
        """Preserve the requested FA and BL display contracts."""
        featured = ZHWIKI_VIDEO_GAMES.class_label("典范")
        assert featured is not None
        assert {
            "raw_value": "典范",
            "code": "FA",
            "long_name": "典范级",
            "short_name": "典范",
            "is_article": True,
        }.items() <= asdict(featured).items()
        assert (featured.article_group, featured.rank) == ("FA", 10)
        listed = ZHWIKI_VIDEO_GAMES.class_label("乙级列表")
        assert listed is not None
        assert {
            "raw_value": "乙级列表",
            "code": "BL",
            "long_name": "乙级列表级",
            "short_name": "乙表",
            "is_article": True,
        }.items() <= asdict(listed).items()
        assert (listed.article_group, listed.rank) == ("B", 6)
        for raw, code, long_name, short_name in (
            ("特色列表", "FL", "特色列表级", "特表"),
            ("小作品", "Stub", "小作品级", "小级"),
            ("小列表", "SL", "小列表级", "小表"),
        ):
            label = ZHWIKI_VIDEO_GAMES.class_label(raw)
            assert (label.code, label.long_name, label.short_name) == (
                code,
                long_name,
                short_name,
            )
        assert ZHWIKI_VIDEO_GAMES.code("典表", "class") == "FL"

    @staticmethod
    def test_article_classification_uses_exact_project_order() -> None:
        """Classify grades independently of page namespaces."""
        assert ZHWIKI_VIDEO_GAMES.article_class_values == (
            "典范",
            "特色列表",
            "甲",
            "甲级列表",
            "优良",
            "乙上",
            "乙",
            "乙级列表",
            "丙",
            "丙级列表",
            "初",
            "列表",
            "小作品",
            "小列表",
            "未评",
        )
        assert ZHWIKI_VIDEO_GAMES.is_article_class("消歧义") is False
        assert ZHWIKI_VIDEO_GAMES.is_article_class("同类索引") is False
        assert ZHWIKI_VIDEO_GAMES.is_article_class("特色列表") is True
        assert ZHWIKI_VIDEO_GAMES.is_article_class("未来评级") is None
        assert ZHWIKI_VIDEO_GAMES.is_article_class(None) is None
        assert "丁" not in ZHWIKI_VIDEO_GAMES.class_values
        assert "小小作品" not in ZHWIKI_VIDEO_GAMES.class_values

    @staticmethod
    def test_unknown_raw_values_remain_unclassified() -> None:
        """Keep codes and future values distinct from raw labels."""
        assert ZHWIKI_VIDEO_GAMES.class_label("FA") is None
        assert ZHWIKI_VIDEO_GAMES.class_label("未来评级") is None
        assert ZHWIKI_VIDEO_GAMES.importance_label("Top") is None
        label = ZHWIKI_VIDEO_GAMES.importance_label("极高")
        assert label is not None
        assert (label.code, label.long_name, label.short_name) == (
            "Top",
            "极高重要度",
            "极高",
        )

    @staticmethod
    def test_mainspace_nonarticle_classes_are_excluded() -> None:
        """Count all 15 article grades while excluding other classes."""
        classes = list(ZHWIKI_VIDEO_GAMES.class_values)
        source = pl.DataFrame({
            "pa_class": classes,
            "pa_importance": ["低"] * len(classes),
            "page_namespace": [0] * len(classes),
            "page_is_redirect": [0] * len(classes),
        })
        totals = summary_totals(source)
        assert totals is not None
        assert totals["count"] == len(ZHWIKI_VIDEO_GAMES.article_class_values)
        assert {entry["value"] for entry in totals["class"]} == set(
            ZHWIKI_VIDEO_GAMES.article_class_values,
        )
        excluded = {entry["value"] for entry in totals["nonarticles"]["class"]}
        assert excluded == {
            label.raw_value
            for label in ZHWIKI_VIDEO_GAMES.classes
            if not label.is_article
        }

    @staticmethod
    def test_configuration_exposes_exact_project_labels() -> None:
        """Publish reusable profile records through the API config."""
        configured = routine_configuration()["assessment_profiles"]
        project = next(
            entry
            for entry in configured
            if entry["wiki"] == "zhwiki" and entry["project"] == "电子游戏"
        )
        assert project["profile_id"] == "zhwiki-video-games:1"
        assert project["article_class_values"] == list(
            ZHWIKI_VIDEO_GAMES.article_class_values,
        )
        assert project["classes"] == [
            {**asdict(label), "color_variants": dict(label.color_variants)}
            for label in ZHWIKI_VIDEO_GAMES.classes
        ]

    @staticmethod
    def test_native_expression_preserves_raw_unknowns_and_nulls() -> None:
        """Resolve aliases without overwriting source observations."""
        raw = ["典範級", " 乙表 ", "未来评级", "", None]
        frame = pl.DataFrame({"raw": raw}).with_columns(
            ZHWIKI_VIDEO_GAMES.expression("raw", "class").alias("code"),
            ZHWIKI_VIDEO_GAMES.expression(
                "raw",
                "class",
                field="rank",
                preserve_unknown=False,
            ).alias("rank"),
        )
        assert frame["raw"].to_list() == raw
        assert frame["code"].to_list() == ["FA", "BL", "未来评级", "", None]
        assert frame["rank"].to_list() == [10, 6, None, None, None]
        assert [ZHWIKI_VIDEO_GAMES.code(item, "class") for item in raw] == (
            frame["code"].to_list()
        )

    @staticmethod
    def test_unaudited_wiki_uses_lossless_observed_profile() -> None:
        """Keep unknown wiki grades independent of other sites."""
        profile = assessment_profile("unconfiguredwiki", "电子游戏")
        assert profile is OBSERVED_PROFILE
        assert profile.class_values == ()
        assert profile.code(" 典范 ", "class") == " 典范 "
        assert profile.resolve("FA", "class") is None
        assert pl.DataFrame({"raw": ["未来", None]}).select(
            profile.expression("raw", "class"),
        )["raw"].to_list() == ["未来", None]

    @staticmethod
    def test_project_override_precedes_audited_site_standard() -> None:
        """Prefer exact projects over their site's general policy."""
        project = assessment_profile("zhwiki", "电子游戏")
        general = assessment_profile("zhwiki", "Another project")
        assert project is ZHWIKI_VIDEO_GAMES
        assert general.identifier == "zhwiki-general:1"
        assert general.class_label("丁") is not None
        assert project.class_label("丁") is None
        assert general.code("同类索引", "class") == "SIA"
        assert project.code("同类索引", "class") == "Setindex"
        assert general.code("極高", "importance") == "Top"

    @staticmethod
    def test_english_general_profile_does_not_inherit_chinese_policy() -> None:
        """Keep raw English empty grades and future values explicit."""
        profile = assessment_profile("enwiki", "Video games")
        assert profile.identifier == "enwiki-general:1"
        assert profile.class_label("A").is_article is True
        assert profile.class_label("Help").is_article is False
        assert profile.code("乙上", "class") == "乙上"
        assert profile.code("YStart", "class") == "YStart"
        frame = pl.DataFrame({"raw": ["", None, "YStart", "FA", "乙上"]})
        assert frame.select(profile.expression("raw", "class"))[
            "raw"
        ].to_list() == ["Unassessed", None, "YStart", "FA", "乙上"]
        assert profile.code("Top", "importance") == "Top"

    @staticmethod
    def test_profile_semantics_are_scoped_to_the_selected_project() -> None:
        """Allow identical source labels to carry different policies."""
        custom = AssessmentProfile(
            "test-project:1",
            (
                ClassAssessmentLabel(
                    "乙",
                    "Custom",
                    "Custom",
                    "Custom",
                    is_article=False,
                    rank=None,
                ),
            ),
            (),
        )
        assert custom.code("乙", "class") == "Custom"
        assert custom.is_article_class("乙") is False
        assert ZHWIKI_VIDEO_GAMES.code("乙", "class") == "B"
        assert ZHWIKI_VIDEO_GAMES.is_article_class("乙") is True
        assert custom.sort_ranks("class") == {}
        assert (
            ZHWIKI_VIDEO_GAMES.sort_ranks("class")["FA"]
            > (ZHWIKI_VIDEO_GAMES.sort_ranks("class")["FL"])
        )

    def test_conflicting_metadata_fails_before_use(self) -> None:
        """Reject ambiguous aliases and malformed article policies."""
        label = {
            "raw_value": "one",
            "code": "One",
            "long_name": "One",
            "short_name": "One",
            "is_article": True,
        }
        cases = [
            [{**label, "rank": True}],
            [{**label, "is_article": "yes"}],
            [{**label, "aliases": "one"}],
            [{**label, "color": "url(evil)"}],
            [{**label, "color_variants": {"pie": "#bad"}}],
            [label, {**label, "raw_value": "two"}],
            [label, {**label, "code": "Two", "raw_value": "one"}],
        ]
        for classes in cases:
            with self.subTest(classes=classes), self.assertRaises(ValueError):
                assessment_profiles._profile({
                    "identifier": "invalid:1",
                    "project": "Example",
                    "classes": classes,
                })

    @staticmethod
    def test_assessment_renderer_consumes_profile_long_names() -> None:
        """Render declared long labels and preserve custom values."""
        assert (
            assessment_log._label(
                "乙级列表",
                "class",
                profile=ZHWIKI_VIDEO_GAMES,
            )
            == "'''乙级列表级'''"
        )
        assert (
            assessment_log._label(
                "典范",
                "class",
                profile=ZHWIKI_VIDEO_GAMES,
            )
            == "'''典范级'''"
        )
        assert (
            assessment_log._label(
                "未来评级",
                "class",
                profile=ZHWIKI_VIDEO_GAMES,
            )
            == "'''未来评级'''"
        )
