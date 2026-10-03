"""Verify scalar wikitext values and editable template parameters."""

from unittest import TestCase

import mwparserfromhell

from aranami.support.templates import (
    normalize_oldid,
    normalize_param_name,
    template_get_param,
    template_get_raw_param,
)
from aranami.support.wikitext import clean_value


class TestScalarWikitext(TestCase):
    """Clean scalar observations without retaining reference text."""

    @staticmethod
    def test_clean_value_uses_display_text_and_discards_references() -> None:
        """Use visible link labels and remove reference contents."""
        value = (
            "[[Some event|29 February 2024]]<!-- private note -->"
            "<ref name='source'>A reference dated 2020-01-01</ref>"
            "<ref name='source'/> <span>UTC</span>"
        )
        assert clean_value(value) == "29 February 2024 UTC"

    @staticmethod
    def test_empty_markup_has_no_observation() -> None:
        """Keep missing observations distinct from scalar zeroes."""
        for value in (None, "  ", "<!-- hidden -->", "<ref>only ref</ref>"):
            assert clean_value(value) is None
        assert clean_value("<b>0</b>") == "0"

    @staticmethod
    def test_reading_parser_values_does_not_modify_original_wikitext() -> None:
        """Return plain text without mutating the editable source."""
        code = mwparserfromhell.parse(
            "{{Example| Action_1 = <b>value</b><!-- retain in source -->}}",
        )
        original = str(code)
        template = code.filter_templates()[0]
        assert normalize_param_name(" Action_1 ") == "action 1"
        raw = template_get_raw_param(template, "action 1")
        assert raw is template.params[0].value
        assert template_get_param(template, "ACTION_1") == "value"
        assert str(code) == original
        assert template_get_param(template, "absent") is None
        assert raw is not None
        raw.append(" after")
        assert template_get_param(template, "action 1") == "value after"

    @staticmethod
    def test_revision_identifier_normalization() -> None:
        """Accept positive revision IDs and reject missing IDs."""
        assert normalize_oldid("<span>00123</span><!-- ignored -->") == "123"
        for value in (None, "0", "-1", "1.5", "unknown", "{{number|123}}"):
            assert normalize_oldid(value) is None
