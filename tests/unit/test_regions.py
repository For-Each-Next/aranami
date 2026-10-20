"""Verify configurable canonical HTML-comment regions."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from unittest import TestCase

from shared.support.regions import (
    integer_option,
    managed_region,
    region_content,
    region_options,
    replace_by_tag,
)


class TestManagedRegions(TestCase):
    """Test managed updates that preserve unrelated text."""

    @staticmethod
    def test_configuration_survives_literal_replacement() -> None:
        """Preserve settings and literal replacement characters."""
        source = (
            'before<!-- aranami begin="new-pages" days="100" / -->old'
            '<!-- aranami end="new-pages" / -->after'
        )
        content = r"{{example|text=\1}}"
        updated = replace_by_tag("new-pages", content, source)
        assert region_content(updated, "new-pages") == content
        assert region_options(updated, "new-pages") == {"days": "100"}
        assert updated.startswith("before")
        assert updated.endswith("after")

    @staticmethod
    def test_nested_counter_preserves_surroundings() -> None:
        """Update canonical comments inside a template parameter."""
        source = (
            '{{Box|count=<!-- aranami begin="total" -->1'
            '<!-- aranami end="total" -->|other=keep}}'
        )
        updated = replace_by_tag(
            "total",
            "2",
            source,
        )
        assert 'aranami begin="total"' in updated
        assert 'aranami end="total"' in updated
        assert "|other=keep" in updated
        assert region_content(updated, "total") == "2"

    def test_obsolete_markers_are_not_managed(self) -> None:
        """Ignore old update dialects and refuse to rewrite them."""
        for opening, closing in (
            ("start", "end"),
            ("begin", "end"),
            ("begin", "begin"),
        ):
            source = (
                f'<!-- update {opening}="x" -->old'
                f'<!-- update {closing}="x" -->'
            )
            with self.subTest(opening=opening, closing=closing):
                assert region_content(source, "x") is None
                assert region_options(source, "x") == {}
                with self.assertRaises(ValueError):
                    replace_by_tag("x", "replacement", source)

    @staticmethod
    def test_obsolete_comments_outside_canonical_range_are_preserved() -> None:
        """Treat old comments as ordinary surrounding page content."""
        outside = '<!-- update start="x" -->keep<!-- update end="x" -->'
        source = outside + managed_region("x", "old")
        assert replace_by_tag("x", "new", source) == (
            outside + managed_region("x", "new")
        )

    def test_missing_and_ambiguous_boundaries_are_rejected(self) -> None:
        """Avoid broad edits when comments are absent or duplicated."""
        for source in (
            "unmanaged",
            '<!-- aranami begin="x" -->',
            '<!-- aranami begin="x" --><!-- aranami begin="x" -->',
            '<!-- aranami begin="x" --><!-- update end="x" -->',
            managed_region("x", "a") + managed_region("x", "b"),
            '<!-- aranami end="x" --><!-- aranami begin="x" -->',
            (
                '<!-- aranami begin="x" days="1" days="2" -->'
                '<!-- aranami end="x" -->'
            ),
            "{{Box|"
            + managed_region("x", "nested")
            + "}}"
            + managed_region("x", "outside"),
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                replace_by_tag("x", "replacement", source)

    def test_bounded_integer_options(self) -> None:
        """Validate settings rather than accepting malformed values."""
        default, override = 100, 3
        assert integer_option({}, "days", default) == default
        assert (
            integer_option({"days": str(override)}, "days", default)
            == override
        )
        for value in ("0", "bad", "101"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                integer_option({"days": value}, "days", 100, maximum=100)

    @staticmethod
    def test_whitespace_and_quoted_configuration_round_trip() -> None:
        """Keep Unicode and escaped quotes in begin attributes."""
        options = {"category": '分类:游戏 "A"', "history-days": "800"}
        source = managed_region("report", "old", options)
        source = source.replace("aranami begin", "aranami\tbegin")
        assert region_options(source, "report") == options
        updated = replace_by_tag("report", "new", source)
        assert region_options(updated, "report") == options
        assert region_content(updated, "report") == "new"

    def test_builder_rejects_config_delimiters_and_reserved_fields(
        self,
    ) -> None:
        """Reject attributes that could corrupt generated comments."""
        for name in ('bad"name', "bad--name", "bad\\name", "bad\nname"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                managed_region(name, "content")
        for options in (
            {"begin": "other"},
            {"end": "other"},
            {"bad key": "value"},
            {"category": "invalid-->comment"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                managed_region("report", "content", options)

    def test_reader_rejects_incomplete_or_invalid_attributes(self) -> None:
        """Require valid attributes before constructing reports."""
        for begin in (
            "aranami",
            'aranami begin="x" days',
            'aranami begin="x" days="unterminated',
            'aranami begin="x" category="bad--comment"',
        ):
            source = f'<!-- {begin} -->old<!-- aranami end="x" -->'
            with self.subTest(begin=begin), self.assertRaises(ValueError):
                region_options(source, "x")
