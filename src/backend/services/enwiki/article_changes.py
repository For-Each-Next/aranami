"""Compare English report membership and quality-class observations.

Call ``compare_articles`` with retained identities and current prepared
rows. Results record additions, removals, and known class transitions
without formatting article links or selecting publication destinations.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    import polars as pl


@dataclass(frozen=True)
class OldArticle:
    """Represent an English article and its available report identity.

    Attributes:
        english_title: English Wikipedia article title.
        item_id: Numeric Wikidata item identifier, when available.
        page_id: Stable English Wikipedia page ID, when known locally.
        en_class: Previously observed English quality class, when known.
    """

    english_title: str
    item_id: int | None
    page_id: int | None = None
    en_class: str | None = None


@dataclass(frozen=True)
class QualityChange:
    """Record a new member or a known English quality-class transition.

    Attributes:
        previous: Matched earlier identity, or none for a new member.
        current: Current identity and normalized English quality class.
    """

    previous: OldArticle | None
    current: OldArticle


@dataclass(frozen=True)
class ArticleChanges:
    """Keep immutable membership and class differences for one report.

    Attributes:
        current_total: Number of distinct current English titles.
        added: Current articles absent from the previous membership.
        removed: Previous articles absent from the current membership.
        quality_changes: New members and matched known-class changes.
            Matched articles with an unknown previous class are omitted.
            Entries use accent-insensitive English title order.
    """

    current_total: int
    added: tuple[OldArticle, ...]
    removed: tuple[OldArticle, ...]
    quality_changes: tuple[QualityChange, ...]


class _ArticleMembership:
    """Match stable page IDs before Wikidata and title fallbacks."""

    def __init__(self, articles: Mapping[str, OldArticle]) -> None:
        """Index available identities without inventing missing IDs.

        Args:
            articles: Report articles keyed by their English title.
        """
        self.articles = articles
        self.by_page_id = {
            article.page_id: article
            for article in articles.values()
            if article.page_id is not None
        }
        self.by_item_id = {
            article.item_id: article
            for article in articles.values()
            if article.item_id is not None
        }
        self.uncached_by_item_id = {
            article.item_id: article
            for article in articles.values()
            if article.page_id is None and article.item_id is not None
        }

    def find(self, article: OldArticle) -> OldArticle | None:
        """Find a matching article without bypassing known ID conflicts.

        Args:
            article: Candidate identity to compare with this membership.

        Returns:
            Matching article, or none when no identity matches. Known
            conflicting page IDs never fall back to a Wikidata ID.
        """
        previous = self.articles.get(article.english_title)
        if article.page_id is not None:
            match = self.by_page_id.get(
                article.page_id,
            ) or self.uncached_by_item_id.get(article.item_id)
        elif article.item_id is not None:
            match = self.by_item_id.get(article.item_id)
        else:
            return previous
        if match is not None:
            return match
        if (
            previous is not None
            and previous.page_id is None
            and previous.item_id is None
        ):
            return previous
        return None


def _title_key(title: str) -> str:
    """Return a case-insensitive English title key without accents.

    Args:
        title: Article title whose stable metadata order is needed.

    Returns:
        Normalized sortable text.
    """
    normalized = unicodedata.normalize("NFKD", title)
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    ).casefold()


def compare_articles(
    old_articles: Mapping[str, OldArticle],
    new_rows: pl.DataFrame,
) -> ArticleChanges:
    """Compare report identities and known English class transitions.

    Stable page IDs recognize renames even when Wikidata items change.
    Missing IDs permit Wikidata or title matching; known conflicting IDs
    preserve additions and removals. Importance and Chinese assessment
    fields do not affect these observations.

    Args:
        old_articles: Previous report articles keyed by English title.
        new_rows: Current rows with English titles and item IDs;
            page IDs and English classes may be absent or null.

    Returns:
        Immutable, title-ordered differences and the current total.
        Duplicate current titles retain their last observation. Unknown
        previous classes do not establish a quality transition.
    """
    new_articles = {
        str(row["en_title"]): OldArticle(
            english_title=str(row["en_title"]),
            item_id=int(row["item_id"]),
            page_id=(
                int(row["page_id"]) if row.get("page_id") is not None else None
            ),
            en_class=(str(row.get("en_class") or "").strip().upper() or None),
        )
        for row in new_rows.iter_rows(named=True)
    }
    old_membership = _ArticleMembership(old_articles)
    new_membership = _ArticleMembership(new_articles)
    added = []
    quality_changes = []
    for title in sorted(new_articles, key=_title_key):
        current = new_articles[title]
        previous = old_membership.find(current)
        if previous is None:
            added.append(current)
        if previous is None or (
            previous.en_class is not None
            and previous.en_class != current.en_class
        ):
            quality_changes.append(QualityChange(previous, current))
    removed = tuple(
        old_articles[title]
        for title in sorted(old_articles, key=_title_key)
        if new_membership.find(old_articles[title]) is None
    )
    return ArticleChanges(
        current_total=len(new_articles),
        added=tuple(added),
        removed=removed,
        quality_changes=tuple(quality_changes),
    )
