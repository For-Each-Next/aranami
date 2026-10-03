"""Verify configurable HTML-comment regions and legacy migration."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from unittest import TestCase

from aranami.support.wikitext import (
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
    def test_nested_legacy_counter_is_migrated() -> None:
        """Migrate a legacy repeated-begin counter inside a template."""
        source = (
            '{{Box|count=<!-- update begin="total" -->1'
            '<!-- update begin="total" -->|other=keep}}'
        )
        updated = replace_by_tag(
            "total",
            "2",
            source,
            legacy_begin="begin",
            allow_repeated_begin=True,
        )
        assert 'aranami begin="total"' in updated
        assert 'aranami end="total"' in updated
        assert "|other=keep" in updated
        assert region_content(updated, "total") == "2"

    def test_missing_and_ambiguous_boundaries_are_rejected(self) -> None:
        """Avoid broad edits when comments are absent or duplicated."""
        for source in (
            "unmanaged",
            '<!-- aranami begin="x" -->',
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
