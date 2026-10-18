"""Build detailed Chinese video-game maintenance worklists.

The background collector passes its current project membership. Read
direct category links for those article IDs, group verified maintenance
categories, and retain every matched article for the saved presentation.
Each record includes the available project assessments, all recognized
maintenance issues, and the oldest known category month. That month
dates a maintenance marker, not article creation or observation.
Successful empty groups contain zero counts. Source failures reach the
collector so that unavailable observations remain null.
The deployed ``maintenance.json`` supplies groups and exact category or
template rules, optionally on each article's associated talk page.

Chinese message boxes normally use dated monthly categories rather than
also adding the undated parent. The category contract was checked on
18 October 2026 against Module:Message box (90800240), including live
dated and undated template expansions. Only the verified roots and their
monthly forms count; unrelated descendants do not imply maintenance.
Source revision: https://zh.wikipedia.org/w/index.php?oldid=90800240.
Checked templates (revision IDs): Unreferenced (76746706), Refimprove
(76746745), More footnotes needed (82945681), Unreliable sources
(87368286), Cleanup (81692788), Wikify (74353507), Update (89149409),
Dead link (85833788), Notability (85825857), POV (94173003), Advert
(91346695), and Rough translation (92981024).

The taxonomy follows English WikiProject Video games/Backlog and
CleanupWorklistBot's category worklists while retaining Chinese articles
and Chinese source contracts. Additional templates checked on the same
date: Citation needed (88702012), Verify source (92336045), Mark I
(88866296), BLP sources (88386927), Copy edit (91880610),
Cleanup rewrite (75757372), Expand section (92327407),
Lead too short (81722215), Lead
missing (92342441), Overly detailed (92617016), Trivia (86392310),
In-universe (92327475), Original research (78915053),
Globalize (77171605), COI (90825337), Weasel (70357909),
Peacock (92617022), Orphan (41660919),
Underlinked (92617100), Dead end (78387088), Uncategorized (85286509),
Merge (94465333), Split (77171957), Expert needed (76725756), and
Disputed (78159585). Their source revisions are available at
https://zh.wikipedia.org/w/index.php?oldid=<revision>.
Dated and undated expansions additionally verified the inline, merging,
uncategorized, expert, lead, expansion, fiction, and promotional forms.
Only explicitly emitted aggregate aliases are included; they never imply
that an arbitrary descendant is an article maintenance tag.

Chinese translation tasks were also checked against Expand English
(92616959), Expand Japanese (92327378), Expand language (92616975),
their documentation (84312754, 40229369, 51743875), and the Copyedit
translation redirect to Proofreader needed (92617024, doc 77913356).
Live expansions verified dated English/Japanese roots and the undated
proofreading aggregate, which does not preserve a marker month.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING
from urllib.parse import quote

import polars as pl
import pywikibot
from pywikibot.site import Namespace

from aranami.sources.quarry.projects import maintenance_tags, page_categories

from .backlog_config import backlog_tasks, load_backlog_config

if TYPE_CHECKING:
    from pathlib import Path

    from pywikibot.site import BaseSite

_DATED_CATEGORY = r"^自([0-9]{4})年(0?[1-9]|1[0-2])月(.+)$"
_MAX_GROUP_ITEMS = 10000


def _article_url(title: str) -> str:
    """Encode a Chinese Wikipedia title for the saved presentation.

    Args:
        title: Canonical title, including its namespace when applicable.

    Returns:
        Public Chinese Wikipedia URL with reserved characters encoded.
    """
    return "https://zh.wikipedia.org/wiki/" + quote(
        title.replace(" ", "_"),
        safe="/:",
    )


def _rule_title(site: BaseSite, rule: dict[str, object]) -> pywikibot.Page:
    """Resolve a configured tag using the source wiki's namespace rules.

    Args:
        site: Wiki whose namespace aliases apply.
        rule: Validated category or template matching rule.

    Returns:
        A canonical page identity without fetching its contents.

    Raises:
        ValueError: An explicit title namespace conflicts with its kind.
    """
    namespace = (
        Namespace.CATEGORY
        if rule["kind"] == "category"
        else Namespace.TEMPLATE
    )
    page = pywikibot.Page(site, rule["name"], namespace)
    if page.namespace() != namespace:
        message = "Maintenance rule title does not match its tag kind."
        raise ValueError(message)
    return page


def _configured_rules(
    site: BaseSite,
    tasks: list[dict[str, object]],
) -> tuple[pl.DataFrame, dict[str, pywikibot.Page]]:
    """Resolve local rules and first-source links for each task.

    Args:
        site: Wiki whose category and template titles are normalized.
        tasks: Validated tasks in their configured display order.

    Returns:
        Typed matching rules and one source page per task.
    """
    rules = []
    sources = {}
    for priority, task in enumerate(tasks):
        for rule in task["rules"]:
            page = _rule_title(site, rule)
            sources.setdefault(task["key"], page)
            rules.append({
                "key": task["key"],
                "tag": page.title(with_ns=False),
                "kind": rule["kind"],
                "talk_page": rule["talk_page"],
                "dated": rule["monthly"],
                "priority": priority,
            })
    definitions = pl.DataFrame(
        rules,
        schema={
            "key": pl.String,
            "tag": pl.String,
            "kind": pl.String,
            "talk_page": pl.Boolean,
            "dated": pl.Boolean,
            "priority": pl.Int64,
        },
    )
    return definitions, sources


def _matching_tags(
    site: BaseSite,
    page_ids: list[int],
    definitions: pl.DataFrame,
) -> pl.DataFrame:
    """Read configured scopes and match exact or dated category tags.

    Args:
        site: Wiki containing current article and talk-page tags.
        page_ids: Exact subject article identities permitted in results.
        definitions: Normalized rules with stable task priorities.

    Returns:
        Distinct matching task/article/month records. Exact names always
        match; monthly rules additionally accept Chinese dated roots.
    """
    frames = []
    for kind, talk_page in (
        definitions.select("kind", "talk_page").unique().iter_rows()
    ):
        tags = (
            page_categories(site, page_ids)
            if kind == "category" and not talk_page
            else maintenance_tags(
                site,
                page_ids,
                kind=kind,
                talk_page=talk_page,
            )
        )
        frames.append(
            tags.with_columns(
                pl.lit(kind).alias("kind"),
                pl.lit(talk_page).alias("talk_page"),
            ),
        )
    observed = (
        pl
        .concat(frames)
        .with_columns(
            pl.col("name").str.replace_all("_", " ", literal=True),
        )
        .with_columns(
            pl
            .when(pl.col("kind") == "category")
            .then(pl.col("name").str.extract(_DATED_CATEGORY, 1))
            .otherwise(None)
            .alias("year"),
            pl
            .when(pl.col("kind") == "category")
            .then(pl.col("name").str.extract(_DATED_CATEGORY, 2))
            .otherwise(None)
            .alias("month"),
            pl
            .when(pl.col("kind") == "category")
            .then(pl.col("name").str.extract(_DATED_CATEGORY, 3))
            .otherwise(None)
            .alias("tag"),
        )
    )
    match_columns = ("key", "page_id", "priority", "year", "month")
    matches = (
        pl
        .concat([
            observed.join(
                definitions,
                left_on=["kind", "talk_page", "name"],
                right_on=["kind", "talk_page", "tag"],
                how="inner",
            ).select(*match_columns),
            observed
            .filter(
                pl.col("year").is_not_null() & (pl.col("year") != "0000"),
            )
            .join(
                definitions.filter(pl.col("dated")),
                on=["kind", "talk_page", "tag"],
                how="inner",
            )
            .select(*match_columns),
        ])
        .filter(pl.col("page_id").is_in(page_ids))
        .with_columns(
            pl
            .when(pl.col("year") != "0000")
            .then(
                pl.concat_str(
                    "year",
                    pl.col("month").str.pad_start(2, "0"),
                    separator="-",
                ),
            )
            .otherwise(None)
            .alias("issue_month"),
        )
        .select("key", "page_id", "priority", "issue_month")
        .unique()
    )
    return matches


def collect_backlog(
    site: BaseSite,
    membership: pl.DataFrame,
    *,
    config_path: Path | None = None,
) -> dict[str, object]:
    """Collect complete maintenance lists for current project articles.

    Args:
        site: Chinese Wikipedia with configured replica access.
        membership: Metadata from the shared project membership query.
        config_path: Optional local JSON configuration. The deployed
            ``maintenance.json`` beside this module is used by default.

    Returns:
        UTC observation time, distinct article total, and configured
        groups with exact counts and complete article lists.
        Article identities appear once within each group and may occur
        in several different groups. Assessments are null when absent or
        conflicting. Issue keys follow catalogue order, and the oldest
        issue month is null without a recognized dated category.

    Raises:
        OSError: The configuration file cannot be read.
        ValueError: The configuration is invalid, a rule uses the wrong
            namespace, or a group exceeds the snapshot's record bound.
    """  # ruff: ignore[docstring-extraneous-exception] - Config reads propagate OSError.
    tasks = backlog_tasks(load_backlog_config(config_path))
    definitions, sources = _configured_rules(site, tasks)
    assessments = ("assessment_class", "assessment_importance")
    pages = (
        membership
        .filter(pl.col("page_namespace") == 0)
        .select(
            "page_id",
            pl.col("full_title").alias("title"),
            *(
                (
                    pl.col(source).cast(pl.String).str.strip_chars()
                    if source in membership.columns
                    else pl.lit(None, dtype=pl.String)
                )
                .replace("", None)
                .alias(target)
                for source, target in zip(
                    ("pa_class", "pa_importance"),
                    assessments,
                    strict=True,
                )
            ),
        )
        .group_by("page_id", maintain_order=True)
        .agg(
            pl.col("title").first(),
            *(
                pl
                .when(pl.col(column).drop_nulls().n_unique() == 1)
                .then(pl.col(column).drop_nulls().first())
                .otherwise(None)
                .alias(column)
                for column in assessments
            ),
        )
    )
    matches = _matching_tags(site, pages["page_id"].to_list(), definitions)
    issues = (
        matches
        .sort("priority")
        .group_by("page_id")
        .agg(
            pl.col("key").unique(maintain_order=True).alias("issues"),
            pl.col("issue_month").min().alias("oldest_issue_month"),
        )
    )
    detailed = (
        matches
        .select("key", "page_id")
        .unique(subset=["key", "page_id"])
        .join(pages, on="page_id", how="inner")
        .join(issues, on="page_id", how="inner")
        .sort("key", "title", "page_id")
    )
    grouped = detailed.group_by("key").agg(
        pl.len().alias("count"),
        pl.struct(
            "page_id",
            "title",
            *assessments,
            "issues",
            "oldest_issue_month",
        ).alias("items"),
    )
    if grouped.filter(pl.col("count") > _MAX_GROUP_ITEMS).height:
        message = "A maintenance group exceeds the snapshot record limit."
        raise ValueError(message)
    by_key = {row["key"]: row for row in grouped.to_dicts()}
    groups = []
    for task in tasks:
        key = task["key"]
        source = sources[key]
        group = by_key.get(key, {"count": 0, "items": []})
        groups.append({
            "key": key,
            "label": task["label"],
            "section_key": task["section_key"],
            "section_label": task["section_label"],
            "category": source.title(with_ns=False),
            "source_url": _article_url(source.title()),
            "count": group["count"],
            "items": [
                {**item, "url": _article_url(item["title"])}
                for item in group["items"]
            ],
        })
    return {
        "observed_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "total": matches["page_id"].n_unique(),
        "groups": groups,
    }
