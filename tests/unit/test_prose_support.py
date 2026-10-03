"""Test configurable wikitext prose extraction and word counting."""

from __future__ import annotations

import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from wiki_fixtures import OfflineSite

from aranami.services.enwiki.prose import PROFILE as EN_PROFILE
from aranami.services.zhwiki.prose import PROFILE as ZH_PROFILE
from aranami.support import words
from aranami.support.prose import ProseProfile, extract_prose


class TestProseSupport(TestCase):
    """Check extraction with caller-owned section and template rules."""

    @staticmethod
    def test_profile_defines_trailing_sections() -> None:
        """Truncate only sections identified by the caller."""
        text = "Opening prose.\n== Archive ==\nOther content."
        site = OfflineSite("en", "wikipedia")
        customized = extract_prose(
            text,
            site=site,
            profile=ProseProfile(non_prose_headings=frozenset({"archive"})),
        )
        unrestricted = extract_prose(text, site=site, profile=ProseProfile())
        assert customized == "Opening prose."
        assert "Other content." in unrestricted

    @staticmethod
    def test_template_identity_and_parameter_rule_are_configurable() -> None:
        """Extract a configured template through a namespace alias."""
        profile = ProseProfile(
            prose_templates=frozenset({"Episode list"}),
            prose_parameters=re.compile(r"summary\d*", re.IGNORECASE),
        )
        text = (
            "Introduction. {{模板:episode_list"
            "|summary 2=More [[prose]]|other=Hidden text}}"
        )
        assert (
            extract_prose(
                text,
                site=OfflineSite("zh", "wikipedia"),
                profile=profile,
            )
            == "Introduction. More prose"
        )

    @staticmethod
    def test_template_values_are_opt_in_and_fragments_not_duplicated() -> None:
        """Include extra values without doubling summaries."""
        text = (
            "Introduction. {{Episode list|ShortSummary=Shared prose}}"
            "{{Custom|label=Additional value}}"
        )
        site = OfflineSite("en", "wikipedia")
        standard = extract_prose(text, site=site, profile=EN_PROFILE)
        expanded = extract_prose(
            text,
            site=site,
            profile=EN_PROFILE,
            include_template_values=True,
        )
        assert standard == "Introduction. Shared prose"
        assert expanded == "Introduction. Shared prose Additional value"

    @staticmethod
    def test_chinese_profile_preserves_summaries() -> None:
        """Apply translated episode fields and section headings."""
        text = (
            "故事開始。{{劇集列表|劇情=接著發生。}}\n== 參考資料 ==\n不計算。"
        )
        assert (
            extract_prose(
                text,
                site=OfflineSite("zh", "wikipedia"),
                profile=ZH_PROFILE,
            )
            == "故事開始。 接著發生。"
        )


class TestWordCounting(TestCase):
    """Check language rules and caller-relative tokenizer caches."""

    @staticmethod
    def test_english_words_preserve_internal_apostrophes_and_hyphens() -> None:
        """Count compound words and numbers, excluding punctuation."""
        text = "It's a turn-based game, version 2.0!"
        expected = 7
        assert words.count_words(text, language="en") == expected

    @staticmethod
    def test_chinese_counting_uses_precise_hmm_and_caller_cache() -> None:
        """Exclude punctuation and whitespace from useful tokens."""
        tokenizer = Mock()
        tokenizer.cut.return_value = ["中文", " ", "游戏", "!", "2024", "。"]
        with (
            TemporaryDirectory() as directory,
            patch.object(words.Path, "cwd", return_value=Path(directory)),
            patch.object(
                words,
                "_tokenizer",
                return_value=tokenizer,
            ) as factory,
        ):
            count = words.count_words("中文游戏! 2024。", language="zh")
        expected = 3
        assert count == expected
        factory.assert_called_once_with(Path(directory) / "cache" / "words")
        tokenizer.cut.assert_called_once_with(
            "中文游戏! 2024。",
            cut_all=False,
            HMM=True,
        )

    @staticmethod
    def test_tokenizer_cache_is_keyed_by_directory() -> None:
        """Reuse one tokenizer per directory without sharing files."""
        words._tokenizer.cache_clear()  # ruff: ignore[private-member-access]
        first, second = Mock(), Mock()
        with (
            TemporaryDirectory() as directory,
            patch.object(
                words.jieba,
                "Tokenizer",
                side_effect=[first, second],
            ),
        ):
            first_path = Path(directory) / "first" / "cache" / "words"
            second_path = Path(directory) / "second" / "cache" / "words"
            assert words._tokenizer(first_path) is first  # ruff: ignore[private-member-access]
            assert words._tokenizer(first_path) is first  # ruff: ignore[private-member-access]
            assert words._tokenizer(second_path) is second  # ruff: ignore[private-member-access]
            assert first_path.is_dir()
            assert second_path.is_dir()
            assert first.tmp_dir == str(first_path)
            assert second.tmp_dir == str(second_path)
        words._tokenizer.cache_clear()  # ruff: ignore[private-member-access]

    def test_unknown_language_fails_without_creating_cache(self) -> None:
        """Reject unsupported rules before creating tokenizer state."""
        with (
            patch.object(words, "_tokenizer") as factory,
            self.assertRaisesRegex(ValueError, "unsupported"),  # ruff: ignore[pytest-unittest-raises-assertion]
        ):
            words.count_words("sample", language="unsupported")
        factory.assert_not_called()
