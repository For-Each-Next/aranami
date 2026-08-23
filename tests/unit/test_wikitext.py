"""Test site-aware MediaWiki template lookup."""

from unittest import TestCase

import mwparserfromhell
from pywikibot.exceptions import InvalidTitleError
from pywikibot.site import BaseSite, Namespace

from aranami.support import get_templates


class _OfflineSite(BaseSite):
    """Provide deterministic namespace metadata without API access."""

    @staticmethod
    def _build_namespaces() -> dict[int, Namespace]:
        """Build namespaces with localized User and Template names.

        Returns:
            Network-free namespace metadata.
        """
        namespaces = Namespace.builtin_namespaces()
        namespaces[Namespace.USER] = Namespace(
            Namespace.USER,
            canonical_name="User",
            custom_name="用户",
            aliases=["使用者"],
            case="first-letter",
        )
        namespaces[Namespace.TEMPLATE] = Namespace(
            Namespace.TEMPLATE,
            canonical_name="Template",
            custom_name="模板",
            aliases=["模版"],
            case="first-letter",
        )
        return namespaces

    @staticmethod
    def encodings() -> tuple[str, ...]:
        """Return the encoding candidates used by title parsing."""
        return ("utf-8",)

    def namespace(
        self,
        number: int,
        *,
        all_ns: bool = False,
    ) -> str | Namespace:
        """Return the localized name or metadata for one namespace.

        Args:
            number: Namespace identifier.
            all_ns: Whether to return the complete namespace object.

        Returns:
            The namespace object or its preferred localized name.
        """
        namespace = self.namespaces[number]
        return namespace if all_ns else namespace[0]


def _site() -> BaseSite:
    """Build a network-free site with localized namespaces.

    Returns:
        A site fixture using real Pywikibot title comparison.

    """
    return _OfflineSite("en", "wikipedia")


class TestGetTemplates(TestCase):
    """Test recursive matching with MediaWiki title semantics."""

    @staticmethod
    def test_accepts_text_or_wikicode_and_returns_original_nodes() -> None:
        """Return editable original nodes from existing Wikicode."""
        code = mwparserfromhell.parse(
            "{{E}}{{Example__name|value=before}}",
        )
        original = str(code)

        from_text = get_templates(
            text=original,
            template="example name",
            site=_site(),
        )
        from_code = get_templates(code, "example name", _site())

        assert [str(template.name) for template in from_text] == [
            "Example__name",
        ]
        assert len(from_code) == 1
        assert from_code[0] is code.filter_templates(recursive=True)[1]
        assert str(code) == original

        from_code[0].add("value", "after")

        assert str(code) == "{{E}}{{Example__name|value=after}}"

    @staticmethod
    def test_iterable_names_preserve_recursive_order_and_duplicates() -> None:
        """Materialize iterables and retain all source-order matches."""
        code = mwparserfromhell.parse(
            "{{Outer|{{Inner}}}}{{Other}}{{Inner}}",
        )
        names = (name for name in ("Outer", "Inner", "Other"))

        templates = get_templates(code, names, _site())

        assert [str(template.name) for template in templates] == [
            "Outer",
            "Inner",
            "Other",
            "Inner",
        ]
        assert templates == code.filter_templates(recursive=True)

    @staticmethod
    def test_uses_site_aware_page_titles() -> None:
        """Treat namespace-like prefixes as literal main-page text."""
        code = mwparserfromhell.parse(
            "{{Sandbox}}{{Template:Sandbox}}{{模板:Sandbox}}"
            "{{模版:Sandbox}}{{User:Sandbox}}{{用户:Sandbox}}",
        )
        site = _site()

        bare = get_templates(code, "Sandbox", site)
        template = get_templates(code, "Template:Sandbox", site)
        localized_template = get_templates(code, "模板:Sandbox", site)
        user = get_templates(code, "User:Sandbox", site)
        localized_user = get_templates(code, "用户:Sandbox", site)

        assert [str(template.name) for template in bare] == ["Sandbox"]
        assert [str(node.name) for node in template] == ["Template:Sandbox"]
        assert [str(node.name) for node in localized_template] == [
            "模板:Sandbox",
        ]
        assert [str(template.name) for template in user] == [
            "User:Sandbox",
        ]
        assert [str(template.name) for template in localized_user] == [
            "用户:Sandbox",
        ]

    @staticmethod
    def test_normalizes_delimiters_and_only_the_first_letter_case() -> None:
        """Normalize title separators without folding later letters."""
        templates = get_templates(
            "{{Example__name}}{{example   name}}{{EXample name}}"
            "{{Example_Name}}",
            "example_name",
            _site(),
        )

        assert [str(template.name) for template in templates] == [
            "Example__name",
            "example   name",
        ]

    @staticmethod
    def test_ignores_leading_spaces_and_colons() -> None:
        """Collapse ordinary and colon-prefixed names to one title."""
        code = mwparserfromhell.parse(
            "{{Foo}}{{ :Foo }}{{::Foo}}{{   :::Foo}}",
        )
        templates = get_templates(code, " ::Foo", _site())

        assert [str(template.name) for template in templates] == [
            "Foo",
            " :Foo ",
            "::Foo",
            "   :::Foo",
        ]

    @staticmethod
    def test_skips_invalid_candidates_and_keeps_valid_matches() -> None:
        """Continue past non-page nodes and keep nested matches."""
        templates = get_templates(
            "{{#if:1|{{Wanted}}}}{{{{Name}}}}"
            "{{Wanted<!--comment-->}}{{Wanted}}",
            "Wanted",
            _site(),
        )

        assert [str(template.name) for template in templates] == [
            "Wanted",
            "Wanted",
        ]

    def test_rejects_invalid_requested_titles_before_scanning(self) -> None:
        """Validate requested titles even when the source is empty."""
        with self.assertRaises(  # ruff: ignore[pytest-unittest-raises-assertion]
            InvalidTitleError,
        ):
            get_templates("", "#if:1", _site())

    @staticmethod
    def test_empty_title_iterable_returns_no_matches() -> None:
        """Return immediately when the caller requests no titles."""
        assert not get_templates("{{#if:1|yes|no}}", (), _site())
