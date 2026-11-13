"""Verify authorship byte scores exclude reference material."""

# Keep the repository's unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import unittest

import polars as pl
from polars.testing import assert_frame_equal

from tasks.articles.authorship_text import eligible_bytes, score_tokens


def _tokens(*values: str) -> pl.DataFrame:
    """Prepare ordered provenance rows with distinct original editors.

    Returns:
        Explicit token input preserving each supplied occurrence.
    """
    return pl.DataFrame({
        "editor": [str(index + 1) for index in range(len(values))],
        "origin": pl.Series(range(1, len(values) + 1), dtype=pl.Int64),
        "text": pl.Series(values, dtype=pl.String),
    })


class AuthorshipTextTests(unittest.TestCase):
    """Keep byte weights aligned to exact final-revision occurrences."""

    @staticmethod
    def test_original_multilingual_utf8_bytes_and_columns() -> None:
        """Count original UTF-8 bytes and preserve provenance."""
        tokens = _tokens("a", "中", "文", "ü")
        result = score_tokens(tokens, "A \t中文\nÜ")
        assert_frame_equal(result.drop("bytes"), tokens)
        assert result["bytes"].dtype == pl.UInt32
        assert result["bytes"].to_list() == [1, 3, 3, 2]

    @staticmethod
    def test_repeated_reference_and_body_strings_remain_distinct() -> None:
        """Mask a reference while preserving identical prose."""
        reference = '<ref name="x">{{cite web|title=Same}}</ref>'
        tokens = _tokens("same", reference.lower(), "same")
        result = score_tokens(tokens, f"Same {reference} Same")
        assert result["bytes"].to_list() == [4, 0, 4]

    @staticmethod
    def test_partial_token_reference_spans_and_self_closing_tags() -> None:
        """Score eligible portions of a token crossing tags."""
        text = 'start<REF name="x">中文</REF>end<ref name="x"/>'
        result = score_tokens(_tokens(text.lower()), text)
        assert result["bytes"].to_list() == [len(b"startend")]

    @staticmethod
    def test_nested_templates_comments_and_reference_lists() -> None:
        """Exclude citations while preserving literal comment source."""
        excluded = (
            "{{reflist|refs=<ref>{{cite web|title={{lang|en|Same}}}}</ref>}}"
            '<references><ref name="x">Same</ref></references>'
            "{{Citation|title=Same}}{{引用书籍|title=Same}}"
        )
        comment = "<!-- Same {{cite news|title=Same}} -->"
        text = "前" + excluded + comment + "後"
        result = score_tokens(_tokens(text.lower()), text)
        attributed = "前<!--Same{{citenews|title=Same}}-->後"
        assert result["bytes"].to_list() == [len(attributed.encode())]
        assert eligible_bytes(text) == len(("前" + comment + "後").encode())

    @staticmethod
    def test_whitespace_counts_without_inventing_its_token_owner() -> None:
        """Retain unowned spaces in the eligible-byte denominator."""
        text = ' \nA\t<ref name="x"> excluded \n</ref> 中文  '
        result = score_tokens(
            _tokens("a", '<refname="x">excluded</ref>', "中文"),
            text,
        )
        assert result["bytes"].to_list() == [1, 0, 6]
        assert eligible_bytes(text) == len(" \nA\t 中文  ".encode())

    @staticmethod
    def test_reference_sections_stop_at_the_next_peer_heading() -> None:
        """Exclude bibliography and nested reference subsections."""
        text = (
            "Before.\n== References ==\nOrdinary [[source]]."
            "\n=== Books ===\nPlain bibliography."
            "\n== External links ==\nAfter."
        )
        result = score_tokens(_tokens(text.lower()), text)
        retained = "Before.==Externallinks==After."
        assert result["bytes"].to_list() == [len(retained.encode())]
        assert eligible_bytes(text) == len(
            b"Before.\n== External links ==\nAfter.",
        )

    @staticmethod
    def test_chinese_headings_and_higher_section_boundaries() -> None:
        """Match audited Chinese headings without substring guesses."""
        text = (
            "== 故事 ==\n劇情。\n=== 參考文獻 ===\n紙本來源。"
            "\n==== 附註 ====\n更多來源。\n== 參考文獻的研究 ==\n正文。"
        )
        result = score_tokens(_tokens(text.lower()), text)
        retained = "==故事==劇情。==參考文獻的研究==正文。"
        assert result["bytes"].to_list() == [len(retained.encode())]

    @staticmethod
    def test_literal_nowiki_reference_text_remains_eligible() -> None:
        """Keep literal examples separate from active references."""
        text = "<nowiki><ref>literal</nowiki>"
        result = score_tokens(_tokens(text.lower()), text)
        assert result["bytes"].to_list() == [len(text.encode())]

    def test_uncertain_alignment_and_malformed_references_fail(self) -> None:
        """Reject uncertain alignment and malformed markup."""
        cases = (
            (_tokens("same"), "same same"),
            (_tokens("second", "first"), "first second"),
            (_tokens("body"), "body<ref>source</ref>"),
            (_tokens("ss"), "ß"),
            (_tokens("broken<ref>"), "broken<ref>"),
            (_tokens("i\u0307"), "İ"),
            (_tokens("ος"), "ΟΣ"),
        )
        for tokens, text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                score_tokens(tokens, text)

    @staticmethod
    def test_empty_whitespace_source_preserves_empty_schema() -> None:
        """Represent empty content without inventing tokens."""
        result = score_tokens(_tokens(), " \n\t")
        assert result.is_empty()
        assert result.schema["bytes"] == pl.UInt32
        assert eligible_bytes(" \n\t") == len(b" \n\t")


if __name__ == "__main__":
    unittest.main()
