"""Provide network-free Pywikibot sites for language protocol tests."""

from pywikibot.site import BaseSite, Namespace


class OfflineSite(BaseSite):
    """Use real title parsing with localized namespace fixtures."""

    @staticmethod
    def _build_namespaces() -> dict[int, Namespace]:
        """Build namespace names without querying a wiki.

        Returns:
            Localized template names and WikiProject aliases.
        """
        namespaces = Namespace.builtin_namespaces()
        namespaces[10] = Namespace(
            10,
            canonical_name="Template",
            custom_name="模板",
            aliases=["模版", "樣板", "样板"],
            case="first-letter",
        )
        namespaces[102] = Namespace(
            102,
            canonical_name="WikiProject",
            custom_name="WikiProject",
            aliases=["PJ", "维基专题"],
            case="first-letter",
        )
        return namespaces

    @staticmethod
    def encodings() -> tuple[str, ...]:
        """Return the character encodings used in title parsing."""
        return ("utf-8",)

    def dbName(self) -> str:  # ruff: ignore[invalid-function-name]
        """Return the replica identifier for this fixture."""
        return f"{self.lang}wiki"

    def namespace(
        self,
        number: int,
        *,
        all_ns: bool = False,
    ) -> str | Namespace:
        """Return a namespace name or its metadata.

        Args:
            number: Requested namespace identifier.
            all_ns: Whether to return the complete namespace object.

        Returns:
            Local namespace metadata or its preferred name.
        """
        namespace = self.namespaces[number]
        return namespace if all_ns else namespace[0]
