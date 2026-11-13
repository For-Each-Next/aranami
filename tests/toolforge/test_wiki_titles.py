"""Check wiki title rules using Pywikibot without network requests."""

from unittest import TestCase
from unittest.mock import patch

from wiki_fixtures import offline_site

from shared.support.titles import page_title, page_url


class TestWikiTitles(TestCase):
    """Use existing wiki clients for entity and namespace handling."""

    @staticmethod
    def test_urls_use_real_title_rules_without_creating_a_site() -> None:
        """Handle entities, namespaces, and local colon titles."""
        site = offline_site()
        cases = (
            ("A &amp; B", "A & B", "A_%26_B"),
            (
                "category:Game_list",
                "Category:Game list",
                "Category%3AGame_list",
            ),
            ("A:Ordinary_title", "A:Ordinary title", "A%3AOrdinary_title"),
        )
        with patch("pywikibot.Site", side_effect=AssertionError("New client")):
            for source, title, encoded in cases:
                assert page_title(site, source) == title
                assert page_url(site, source) == (
                    "https://zh.wikipedia.org/wiki/" + encoded
                )
