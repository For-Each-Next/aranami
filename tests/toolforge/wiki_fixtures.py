"""Use real Pywikibot title and URL rules without source requests."""

from __future__ import annotations

from pywikibot.site import BaseSite, Namespace

from shared.support.titles import page_url


class OfflineSite(BaseSite):
    """Use fixed wiki namespace metadata for isolated source tests."""

    articlepath = "/wiki/{}"

    def dbName(self) -> str:  # ruff: ignore[invalid-function-name]
        """Return the real Wikipedia replica identity without an API."""
        return f"{self.lang}wiki"

    @staticmethod
    def interwiki(prefix: str) -> BaseSite:
        """Treat fixture colon prefixes as local titles.

        Raises:
            KeyError: The fixture has no interwiki destinations.
        """
        raise KeyError(prefix)

    def namespace(
        self,
        number: int,
        *,
        all_ns: bool = False,
    ) -> str | Namespace:
        """Return fixed namespace names or complete metadata.

        Args:
            number: Requested namespace identifier.
            all_ns: Whether to return the namespace metadata object.

        Returns:
            Canonical namespace name or its metadata object.
        """
        namespace = self.namespaces[number]
        return namespace if all_ns else namespace[0]


def offline_site() -> OfflineSite:
    """Provide namespace and main-page metadata without API requests.

    Returns:
        A source client suitable for real Pywikibot Page construction.
    """
    site = OfflineSite("zh", "wikipedia")
    site.siteinfo = {
        "mainpage": "Wikipedia:首页",
        "namespaces": {
            "0": {"*": ""},
            "4": {"*": "Wikipedia"},
            "14": {"*": "Category"},
            "100": {"*": "Portal"},
            "-1": {"*": "Special"},
        },
    }
    return site


def article_url(title: str) -> str:
    """Resolve a parsed title with real offline Pywikibot URL handling.

    Args:
        title: Parsed subject or namespace title.

    Returns:
        Encoded canonical Chinese Wikipedia article URL.
    """
    return page_url(offline_site(), title)
