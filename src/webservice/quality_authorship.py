"""Prepare the saved quality-author directory for localized HTML tables.

Use this adapter with validated, retained authorship and user activity
observations. It never reads files or contacts source services.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote, urlsplit

from webservice.presentation import (
    Interface,
    display_title,
    relative_units,
    timestamp,
    title_url,
    utc_time,
)

_QUALITY_GRADES = ("FA", "FL", "A", "AL", "GA", "Bplus")
_GRADES = (
    *_QUALITY_GRADES,
    "B",
    "BL",
    "C",
    "CL",
    "Start",
    "List",
    "Stub",
    "SL",
    "Unassessed",
)
_DAY_SECONDS = 24 * 60 * 60


def grade_label(code: str, interface: Interface) -> str:
    """Keep Chinese quality labels to their first two characters.

    Returns:
        A compact Chinese label or the complete English label.
    """
    label = interface.text(f"assessment.class.{code}")
    return label[:2] if interface.locale.startswith("zh") else label


def review_url(article: dict) -> str | None:
    """Link only retained, anchored Chinese Wikipedia review evidence.

    Returns:
        A current talk-section or revision-pinned review URL, or none
        when no verified review anchor was retained.
    """
    link = article.get("nomination_link")
    if not link or link.casefold() == "#top":
        return None
    if link.startswith("#") and len(link) > 1:
        return (
            title_url(f"Talk:{article['title']}")
            + "#"
            + quote(
                link[1:].replace(" ", "_"),
                safe="%:_-.",
            )
        )
    if any(character.isspace() for character in link):
        return None
    try:
        parsed = urlsplit(link)
    except ValueError:
        return None
    if (
        parsed.scheme == "https"
        and parsed.netloc == "zh.wikipedia.org"
        and parsed.fragment.casefold() not in {"", "top"}
        and (parsed.path.startswith("/wiki/") or parsed.path == "/w/index.php")
    ):
        return link
    return None


def _age(days: int, interface: Interface) -> str:
    """Describe a saved date in whole days, weeks, months or years.

    Args:
        days: UTC days since the saved date, negative for future dates.
        interface: Locale for the unparenthesized date annotation.

    Returns:
        A localized whole-unit annotation rounded down using the shared
        relative-time thresholds. Same-day dates use an explicit label.
    """
    if days == 0:
        return interface.text("quality_authorship.today")
    unit, count = relative_units(days * _DAY_SECONDS)
    direction = "ago" if days >= 0 else "ahead"
    return interface.text(
        f"quality_authorship.{unit}_{direction}",
        count=count,
    )


def quality_authorship_context(
    data: dict,
    interface: Interface,
    now: dt.datetime,
) -> dict:
    """Localize author summaries and their contribution dialog rows.

    Args:
        data: Validated author directory with whole article counts.
        interface: Negotiated interface language.
        now: Aware render clock used for every date annotation.

    Returns:
        The quality_authorship context, preserving unavailable evidence
        separately from an observed empty directory.
    """
    today = now.astimezone(dt.UTC).date()
    include_b = bool(data.get("include_b"))
    authors = []
    for index, source in enumerate(data.get("authors", []), 1):
        articles = []
        for number, article in enumerate(source["articles"], 1):
            date = (
                dt.date.fromisoformat(article["date"])
                if article.get("date")
                else None
            )
            coauthors = [
                name
                for name in article.get("coauthors", [])
                if name != source["name"]
            ]
            articles.append({
                **article,
                "source_url": (
                    "https://zh.wikipedia.org/w/index.php?oldid="
                    + str(article["revision_id"])
                    if type(article.get("revision_id")) is int
                    and article["revision_id"] > 0
                    else None
                ),
                "title_label": display_title(
                    article["title"].replace("_", " "),
                    interface.locale,
                ),
                "number": number,
                "url": title_url(article["title"]),
                "date_label": interface.date(article["date"])
                if date
                else None,
                "listing_url": (
                    "https://zh.wikipedia.org/w/index.php?oldid="
                    + str(article["revision_id"])
                    if type(article.get("revision_id")) is int
                    and article["revision_id"] > 0
                    else None
                ),
                "review_url": review_url(article),
                "grade_order": _GRADES.index(article["grade"]),
                "grade_label": grade_label(article["grade"], interface),
                "age": _age((today - date).days, interface) if date else None,
                "coauthors_label": interface.text(
                    "quality_authorship.coauthors",
                    names=", ".join(coauthors),
                )
                if coauthors
                else None,
            })
        active = timestamp(source.get("last_active"))
        total = source["credit"]
        summary = interface.text(
            "quality_authorship.articles_one"
            if source["credit"] == 1
            else "quality_authorship.articles",
            count=total,
        )
        featured = sum(
            source["grades"].get(grade, 0) for grade in ("FA", "FL")
        )
        authors.append({
            "number": index,
            "name": source["name"],
            "url": title_url(f"User:{source['name']}"),
            "talk_url": title_url(f"User talk:{source['name']}"),
            "contributions_url": title_url(
                f"Special:Contributions/{source['name']}",
            ),
            "popup_id": f"quality-author-{index}",
            "featured_popup_id": f"quality-author-{index}-featured",
            "quality_popup_id": f"quality-author-{index}-quality",
            "dialog_title": interface.text(
                "quality_authorship.author_all_articles",
                name=source["name"],
            ),
            "summary": summary,
            "credit": total,
            "quality_summary": interface.text(
                "quality_authorship.high_quality",
                count=source.get("high_quality_credit", total),
            )
            if include_b and source.get("high_quality_credit", total)
            else None,
            "quality_dialog_title": interface.text(
                "quality_authorship.author_articles",
                name=source["name"],
            ),
            "featured_summary": interface.text(
                "quality_authorship.featured",
                count=featured,
            )
            if featured
            else None,
            "featured_dialog_title": interface.text(
                "quality_authorship.author_featured",
                name=source["name"],
            ),
            "last_active": (
                {
                    "value": active.isoformat(),
                    "label": interface.date(active.date().isoformat()),
                    "title": utc_time(active.isoformat(), seconds=True)
                    + " UTC",
                    "age": _age((today - active.date()).days, interface),
                }
                if active
                else None
            ),
            "articles": articles,
            "quality_articles": [
                article
                for article in articles
                if article["grade"] in _QUALITY_GRADES
            ],
            "featured_articles": [
                article
                for article in articles
                if article["grade"] in {"FA", "FL"}
            ],
        })
    observed_at = timestamp(data.get("observed_at"))
    return {
        "quality_authorship": {
            "locale": interface.locale,
            "parenthesis_open": interface.text(
                "quality_authorship.parenthesis_open",
            ),
            "parenthesis_close": interface.text(
                "quality_authorship.parenthesis_close",
            ),
            "wikiwho_url": "https://www.mediawiki.org/wiki/WikiWho",
            "year_reviews_url": title_url(
                "WikiProject:電子遊戲/簡訊/年终回顾导航模板",
            ),
            "earlier_intro_url": title_url(
                "WikiProject:電子遊戲/簡訊/2021-01/專題",
            ),
            "available": bool(data.get("available")),
            "include_b": include_b,
            "b_unavailable": bool(data.get("b_unavailable")),
            "b_coverage": data.get("b_coverage"),
            "b_missing_articles": [
                {
                    "title": display_title(row["title"], interface.locale),
                    "url": title_url(row["title"]),
                    "talk_url": title_url(f"Talk:{row['title']}"),
                }
                for row in data.get("b_missing_articles", [])
            ],
            "b_coverage_label": interface.text(
                "quality_authorship.b_coverage",
                **data["b_coverage"],
            )
            if data.get("b_coverage") is not None
            else None,
            "authors": authors,
            "observed_at": (
                {
                    "value": observed_at.isoformat(),
                    "label": utc_time(observed_at.isoformat()) + " UTC",
                }
                if observed_at
                else None
            ),
        },
    }
