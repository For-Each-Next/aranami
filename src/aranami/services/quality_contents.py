"""Analyze quality content manually without publishing reports.

Call ``analyze_quality_contents(site)`` for English or Chinese
Wikipedia.
The returned Polars frame can be inspected or exported by the caller;
``length_statistics`` and ``year_statistics`` derive summary frames.
"""

from __future__ import annotations

from itertools import batched
from typing import TYPE_CHECKING

import polars as pl

from aranami.services.enwiki.quality_contents import PROFILE as EN_PROFILE
from aranami.services.zhwiki.quality_contents import PROFILE as ZH_PROFILE
from aranami.sources.quarry.quality import fetch_quality_articles
from aranami.sources.wiki import read_pages, template_aliases
from aranami.support.prose import extract_prose
from aranami.support.words import count_words

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_SCHEMA = {
    "quality_status": pl.String,
    "importance": pl.String,
    "article_title": pl.String,
    "article_display_title": pl.String,
    "article_sort_key": pl.String,
    "listed_date": pl.String,
    "listed_oldid": pl.String,
    "date_source": pl.String,
    "prose_words": pl.Int64,
    "prose_bytes": pl.Int64,
    "source_bytes": pl.Int64,
    "talk_revision_id": pl.Int64,
    "article_revision_id": pl.Int64,
}
_PROFILES = {"enwiki": EN_PROFILE, "zhwiki": ZH_PROFILE}
_BATCH_SIZE = 50


def analyze_quality_contents(
    site: BaseSite,
    *,
    most_recent: bool = True,
) -> pl.DataFrame:
    """Analyze all current FA, FL, and GA video-game articles once.

    Replica metadata selects articles. Current article/talk wikitext is
    read in preloaded batches, then analyzed without saving wiki pages.
    Missing promotion dates remain null rather than being guessed.

    Args:
        site: English or Chinese Wikipedia site supplied by the caller.
        most_recent: Select the latest successful history action; false
            selects the earliest, following the source notebooks.

    Returns:
        Typed article metrics and milestones, ordered by newest date,
        quality status, and article sort key.

    Raises:
        ValueError: If the site's database has no analysis profile.
    """
    try:
        profile = _PROFILES[site.dbName()]
    except KeyError as error:
        message = f"Unsupported quality-analysis project: {site.dbName()}."
        raise ValueError(message) from error
    metadata = fetch_quality_articles(
        site,
        project_title=profile.project_title,
        classes=profile.classes,
    )
    if metadata.is_empty():
        return pl.DataFrame(schema=_SCHEMA)
    metadata = metadata.with_columns(
        pl.col("article_title").str.replace_all("_", " ", literal=True),
        pl.col("quality_status").replace(profile.quality_names),
        pl
        .col("importance")
        .replace(profile.importance_names)
        .fill_null("Unknown"),
    ).unique("article_title", keep="first", maintain_order=True)
    aliases = {
        role: template_aliases(site, title)
        for role, title in profile.templates.items()
    }
    records = []
    for batch in batched(metadata.iter_rows(named=True), _BATCH_SIZE):
        article_titles = [str(row["article_title"]) for row in batch]
        talk_titles = [
            f"{site.namespace(1)}:{title}" for title in article_titles
        ]
        articles = read_pages(site, article_titles)
        talks = read_pages(site, talk_titles)
        for row, article, talk in zip(batch, articles, talks, strict=True):
            milestone = profile.extract_milestone(
                talk.text,
                row["quality_status"],
                most_recent=most_recent,
                aliases=aliases,
                site=site,
            )
            prose = extract_prose(
                article.text,
                site=site,
                profile=profile.prose,
                include_template_values=row["quality_status"]
                in profile.template_value_classes,
            )
            records.append({
                **row,
                "article_display_title": row["article_display_title"]
                or row["article_title"],
                "article_sort_key": row["article_sort_key"]
                or row["article_title"],
                "listed_date": milestone.date if milestone else None,
                "listed_oldid": milestone.oldid if milestone else None,
                "date_source": milestone.source if milestone else None,
                "prose_words": count_words(prose, language=profile.language),
                "prose_bytes": len(prose.encode("utf-8")),
                "source_bytes": len(article.text.encode("utf-8")),
                "talk_revision_id": talk.latest_revision_id
                if talk.exists()
                else None,
                "article_revision_id": article.latest_revision_id,
            })
    return pl.DataFrame(records, schema=_SCHEMA).sort(
        "listed_date",
        "quality_status",
        "article_sort_key",
        descending=[True, False, False],
        nulls_last=True,
    )


def length_statistics(frame: pl.DataFrame) -> pl.DataFrame:
    """Summarize prose lengths by quality status with linear quantiles.

    Args:
        frame: Article analysis containing quality status and prose
            words.

    Returns:
        Page/measurement counts, median words, and central 68%/95%
        bounds.
    """
    return (
        frame
        .group_by("quality_status")
        .agg(
            pl.len().alias("pages"),
            pl.col("prose_words").count().alias("measured"),
            pl.col("prose_words").median().alias("median_words"),
            pl
            .col("prose_words")
            .quantile(0.16, interpolation="linear")
            .alias("central_68_low"),
            pl
            .col("prose_words")
            .quantile(0.84, interpolation="linear")
            .alias("central_68_high"),
            pl
            .col("prose_words")
            .quantile(0.025, interpolation="linear")
            .alias("central_95_low"),
            pl
            .col("prose_words")
            .quantile(0.975, interpolation="linear")
            .alias("central_95_high"),
        )
        .sort("quality_status")
    )


def year_statistics(frame: pl.DataFrame) -> pl.DataFrame:
    """Count listings by year, quality status, and importance.

    Args:
        frame: Article analysis containing listing dates and
            assessments.

    Returns:
        Dated article counts; articles with unresolved dates are
        omitted.
    """
    return (
        frame
        .filter(pl.col("listed_date").is_not_null())
        .with_columns(
            pl.col("listed_date").str.slice(0, 4).cast(pl.Int32).alias("year"),
        )
        .group_by("year", "quality_status", "importance")
        .agg(pl.len().alias("articles"))
        .sort("year", "quality_status", "importance")
    )
