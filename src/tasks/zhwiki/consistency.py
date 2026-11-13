"""Find documented metadata disagreements without editing wiki pages.

Checks are scoped to Chinese Wikipedia's video-game project. Nomination
and award templates were verified on November 2, 2026, at revisions
83223665, 88861323, 88661416, 88551970, 70703818, 85932271, 85932258,
and 88404248. Homepage fields follow Article history/doc 88380051 and
Module:Article history/config 92259335, rechecked November 3, 2026:
maindateFA and maindateGA retain repeated dates in one field. Missing
sources produce unavailable checks, never inferred defects.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Text
from pywikibot import Page
from pywikibot.exceptions import InvalidTitleError, NoPageError
from pywikibot.site import Namespace

from data.articles import history_store, homepage_archive, homepage_data
from data.consistency import (
    MAX_REPAIR_INPUT_BYTES,
    MAX_REPAIR_INPUTS,
    Evidence,
    repair_identity,
    valid_repair_input,
)
from shared.assessment_profiles import assessment_profile
from shared.sources.wiki import read_pages, template_aliases
from shared.support.dates import parse_complete_date
from shared.support.titles import page_url
from tasks.consistency_evidence import issue, source_evidence
from tasks.zhwiki import article_history, homepage, homepage_archives, reviews
from tasks.zhwiki.consistency_lists import collect_list_checks
from tasks.zhwiki.homepage_parameters import (
    AmbiguousArticleHistoryError,
    HomepageAppearance,
    HomepageSuggestions,
    extract_dyk_entry,
    homepage_dates,
    prepare_history_suggestions,
    prepare_section0_repair,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from mwparserfromhell.nodes import Template
    from pywikibot.site import BaseSite

    from shared.sources.wiki import WikiReader

_LOGGER = logging.getLogger(__name__)
_NOMINATIONS = {
    "fac": "Featured article candidates",
    "flc": "Featured list candidates",
    "gan": "GA nominee",
    "pr": "Peer review",
    "dykc": "DYK Invite",
}
_AWARDS = {
    "fa": "Featured article",
    "fl": "Featured list",
    "ga": "Good article",
}
_HISTORY_FIELDS = {
    "featured": "maindateFA",
    "good": "maindateGA",
    "dyk": "dykdate",
    "news": "itndate",
    "history": "otddate",
}
_GOOD_REPORT = "Wikipedia:首頁/優良條目展示報告"
_QUOTED_TAGS = {"nowiki", "pre", "source", "syntaxhighlight", "code"}
_GOOD_REPORT_COLUMNS = 6
_MAX_DAYS = 365
_HOMEPAGE_CHECKS = {
    "homepage_featured": "maindateFA",
    "homepage_good": "maindateGA",
    "homepage_dyk": "dykdate",
    "homepage_news": "itndate",
    "homepage_history": "otddate",
}
_PROJECT_ACTIONS = {
    "A": ("A", "WAR"),
    "AL": ("A", "WAR"),
    "Bplus": ("Bplus", "BPAR"),
}


@dataclass(frozen=True, slots=True)
class _Expectation:
    """Retain one source-backed expectation before checking its page."""

    title: str
    check_id: str
    kind: str
    template: str
    source: Evidence
    field: str | None = None
    event_date: str | None = None
    entry: str | None = None
    article_revision: int | None = None
    link: str | None = None


@dataclass(frozen=True, slots=True)
class _Observation:
    """Keep page text and the exact revision supplying it together."""

    evidence: Evidence
    text: str
    page_id: int | None = None


@dataclass(slots=True)
class _Checks:
    """Share source evidence and aliases within one collection pass."""

    read: WikiReader
    members: pl.DataFrame
    now: dt.datetime
    observations: dict[str, _Observation] = field(default_factory=dict)
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    appearances: pl.DataFrame = field(
        default_factory=lambda: pl.DataFrame(schema=homepage_data.SCHEMA),
    )
    unavailable_checks: set[str] = field(default_factory=set)
    repair_inputs: dict[str, dict] = field(default_factory=dict)
    repair_input_bytes: int = 0


def _name(value: str, site: BaseSite) -> str | None:
    """Normalize a template spelling without guessing new aliases.

    Returns:
        Canonical template title, or none for another namespace.
    """
    try:
        page = Page(site, value, ns=Namespace.TEMPLATE)
        return page.title() if page.namespace() == Namespace.TEMPLATE else None
    except InvalidTitleError:
        return None


def _templates(text: str) -> list[Template]:
    """Read active template calls while excluding literal code samples.

    Returns:
        Parsed transclusions outside quoted wikitext.
    """
    code = mwparserfromhell.parse(text)
    for tag in reversed(code.filter_tags()):
        if str(tag.tag).strip().casefold() in _QUOTED_TAGS:
            code.remove(tag)
    return code.filter_templates()


def missing_metadata(  # ruff: ignore[too-many-arguments]
    text: str,
    aliases: Iterable[str],
    *,
    site: BaseSite,
    field: str | None = None,
    equivalents: Iterable[str] = (),
    event_date: dt.date | None = None,
) -> bool:
    """Check a verified banner or documented homepage-date field.

    Args:
        text: Successfully read current page text.
        aliases: Canonical template and source-verified aliases.
        site: Existing wiki client providing namespace and title rules.
        field: Required history field, or none for template presence.
        equivalents: Verified legacy templates retaining the same date
            observation outside Article history.
        event_date: Proven appearance date to find among recorded dates.
            Each proven FA/GA appearance requires its own recorded date.

    Returns:
        Whether the expected metadata is absent. Numbered DYK, ITN, and
        OTD date fields count as recorded observations. Optional link,
        oldid, and question fields never become invented requirements.
    """
    names = {_name(name, site) for name in aliases} - {None}
    legacy = {_name(name, site) for name in equivalents} - {None}
    dates = set()
    for template in _templates(text):
        name = _name(str(template.name), site)
        if name in names:
            if field is None:
                return False
            pattern = (
                re.escape(field[:-4]) + r"(?:[1-9]\d*)?date"
                if field in {"dykdate", "itndate", "otddate"}
                else re.escape(field)
            )
            if field in {"maindateFA", "maindateGA"}:
                dates.update(
                    day
                    for parameter in template.params
                    if str(parameter.name).strip() == field
                    for day in homepage_dates(str(parameter.value))
                )
            else:
                dates.update(_template_dates(template, pattern))
        if name in legacy:
            dates.update(
                _template_dates(template, r"date(?:[1-9]\d*)?", legacy=True),
            )
    if event_date is None:
        return not dates
    return event_date not in dates


def _template_dates(
    template: Template,
    pattern: str,
    *,
    legacy: bool = False,
) -> set[dt.date]:
    """Parse complete dates from documented parameter names and pairs.

    Returns:
        Explicit dates without supplying absent calendar components.
    """
    parameters = {
        str(parameter.name).strip(): parameter.value.strip_code().strip()
        for parameter in template.params
    }
    values = [
        value
        for key, value in parameters.items()
        if re.fullmatch(pattern, key)
    ]
    if legacy:
        # Legacy templates accept dates split across positional pairs.
        values.extend(
            parameters.get(str(first), "") + parameters.get(str(first + 1), "")
            for first in (1, 3, 5, 7, 9)
        )
    return {
        day
        for value in values
        if (day := parse_complete_date(value)) is not None
    }


def _source(read: WikiReader, title: str) -> tuple[str, Evidence]:
    """Read source text and retain its actual saved revision link.

    Returns:
        Current source content and evidence without invented timestamps.
    """
    text = read(title)
    return text, source_evidence(read, title)


def _observations(
    site: BaseSite,
    titles: Iterable[str],
) -> dict[str, _Observation]:
    """Batch current article and talk content with exact revision times.

    Returns:
        Available observations; absent pages are not empty observations.
    """
    result = {}
    for page in read_pages(site, titles):
        try:
            text = page.get()
        except NoPageError:
            continue
        revision = page.latest_revision
        stamp = revision.timestamp
        timestamp = (
            stamp.replace(tzinfo=dt.UTC)
            if stamp.utcoffset() is None
            else stamp.astimezone(dt.UTC)
        ).isoformat()
        result[page.title()] = _Observation(
            Evidence(
                page.title(),
                f"https://zh.wikipedia.org/w/index.php?oldid={revision.revid}",
                revision.revid,
                timestamp,
            ),
            text,
            page.pageid
            if type(page.pageid) is int and page.pageid > 0
            else None,
        )
    return result


def _members(membership: pl.DataFrame) -> pl.DataFrame:
    """Select complete article identities using column operations.

    Returns:
        Unique mainspace identities and original assessment strings.
    """
    return (
        membership
        .filter(pl.col("page_namespace") == 0)
        .with_columns(
            pl.col("full_title").alias("title"),
            pl.col("pa_class", "pa_importance").cast(pl.String),
        )
        .unique(subset="page_id", keep="none")
    )


def _nomination_expectations(
    read: WikiReader,
    members: pl.DataFrame,
    kind: str,
) -> list[_Expectation]:
    """Read authoritative current nominations independently of banners.

    Returns:
        Project nominations with the source revision that lists them.
    """
    text, source = _source(read, reviews.COMMUNITY_SOURCES[kind])
    rows = reviews.community_reviews(
        text,
        kind,
        url=partial(page_url, read.site),
    )
    read.resolve_many(row["title"] for row in rows)
    candidates = (
        pl
        .DataFrame(
            {"title": [read.resolve(row["title"]) for row in rows]},
            schema={"title": pl.String},
        )
        .join(members.select("title"), on="title", how="semi")
        .unique()
    )
    return [
        _Expectation(
            title,
            kind,
            "nomination_banner",
            _NOMINATIONS[kind],
            source,
        )
        for title in candidates["title"]
    ]


def good_report_appearances(text: str, through: dt.date) -> pl.DataFrame:
    """Read proven GA displays from the audited generated report table.

    Args:
        text: Successfully read GA homepage display report.
        through: Latest actual UTC display date permitted.

    Returns:
        Article titles and observed display dates. Never-displayed and
        future scheduled rows are excluded even when a counter is set.

    Raises:
        ValueError: The generated table format cannot be fully verified.
    """
    rows = []
    if not all(
        label in text for label in ("上次展示時間", "次數", "簡介頁面")
    ):
        message = "The GA display report header is unrecognized."
        raise ValueError(message)
    parsed = 0
    for line in text.splitlines():
        if not re.match(r"^\|\s*\d+\s*\|\|", line):
            continue
        cells = line.split("||")
        if (
            len(cells) != _GOOD_REPORT_COLUMNS
            or not cells[4].strip().isdecimal()
        ):
            message = "A GA display report row is incomplete."
            raise ValueError(message)
        links = mwparserfromhell.parse(cells[1]).filter_wikilinks()
        if len(links) != 1:
            message = "A GA display report subject is ambiguous."
            raise ValueError(message)
        parsed += 1
        if int(cells[4].strip()) == 0:
            continue
        dates = re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日", cells[3])
        if not dates or len(set(dates)) != 1:
            message = "A GA display report appearance has no exact date."
            raise ValueError(message)
        day = dt.date(*(int(value) for value in dates[0]))
        if day <= through:
            rows.append((str(links[0].title).strip(), day.isoformat()))
    if not parsed:
        message = "The GA display report contains no verified rows."
        raise ValueError(message)
    return pl.DataFrame(
        rows,
        schema={"title": pl.String, "date": pl.String},
        orient="row",
    ).unique()


def _good_expectations(
    read: WikiReader,
    members: pl.DataFrame,
    now: dt.datetime,
) -> list[_Expectation]:
    """Associate past GA displays with project identities.

    Returns:
        Missing-field candidates carrying the exact report revision.
    """
    text, source = _source(read, _GOOD_REPORT)
    appearances = good_report_appearances(text, now.date())
    read.resolve_many(appearances["title"])
    appearances = appearances.with_columns(
        pl.Series(
            "title",
            [read.resolve(title) for title in appearances["title"]],
            dtype=pl.String,
        ),
    ).join(members.select("title"), on="title", how="semi")
    return [
        _Expectation(
            title,
            "homepage_good",
            "homepage_history",
            "Article history",
            source,
            "maindateGA",
            day,
        )
        for title, day in appearances.iter_rows()
    ]


def _daily_expectations(
    read: WikiReader,
    members: pl.DataFrame,
    section: str,
    start: dt.date,
    end: dt.date,
) -> list[_Expectation]:
    """Retain daily display sources alongside existing subject parsing.

    Returns:
        Featured or anniversary facts with exact source revisions.
    """
    evidence = {}

    def at(title: str, before: dt.datetime) -> str:
        """Capture the same source revision used by a daily parser.

        Returns:
            Historical text read by the shared homepage collector.
        """
        text = read.at(title, before)
        evidence[(before.date() - dt.timedelta(days=1)).isoformat()] = (
            source_evidence(read, title)
        )
        return text

    rows = getattr(homepage, "collect_" + section)(
        start,
        end,
        set(members["title"]),
        read,
        resolve=read.resolve,
        resolve_many=read.resolve_many,
        read_at=at,
        url=partial(page_url, read.site),
    )
    return [
        _Expectation(
            row["title"],
            "homepage_" + section,
            "homepage_history",
            "Article history",
            evidence[row["date"]],
            _HISTORY_FIELDS[section],
            row["date"],
        )
        for row in rows
    ]


def _archive_expectations(
    archive: pl.DataFrame,
    members: pl.DataFrame,
    section: str,
) -> list[_Expectation]:
    """Match wiki-wide archived identities to current project members.

    Returns:
        Every proven archived display with its exact source revision.
    """
    selected = (
        archive
        .filter(pl.col("field") == _HISTORY_FIELDS[section])
        .drop("title")
        .join(members.select("page_id", "title"), on="page_id", how="inner")
    )
    return [
        _Expectation(
            row["title"],
            "homepage_" + section,
            "homepage_history",
            "Article history",
            Evidence(
                row["source_title"],
                "https://zh.wikipedia.org/w/index.php?oldid="
                + str(row["source_revision"]),
                row["source_revision"],
                row["source_timestamp"].isoformat(),
            ),
            row["field"],
            row["date"].isoformat(),
        )
        for row in selected.iter_rows(named=True)
    ]


def _timeline_expectations(
    read: WikiReader,
    members: pl.DataFrame,
    section: str,
    start: dt.date,
    end: dt.date,
) -> list[_Expectation]:
    """Use actual DYK or ITN revision timelines as appearance evidence.

    Returns:
        Proven appearance starts, excluding unknown carried-in starts.
    """
    ledger = []

    def history(title: str, begin: dt.date, stop: dt.date) -> list[dict]:
        """Retain the revisions used by the existing display parser.

        Returns:
            Original complete source history, including its baseline.
        """
        revisions = read.history(title, begin, stop)
        ledger.extend((title, revision) for revision in revisions)
        return revisions

    rows = getattr(homepage, "collect_" + section)(
        start,
        end,
        set(members["title"]),
        read,
        resolve=read.resolve,
        resolve_many=read.resolve_many,
        history=history,
        url=partial(page_url, read.site),
    )
    # Reuse the verified private source parsers called above.
    parsers = {
        "Template:Dyk": homepage._dyk_subjects,  # ruff: ignore[private-member-access]
        "Template:Itn": homepage._headline_subjects,  # ruff: ignore[private-member-access]
        "Template:Itn/Ongoing": homepage._separated_subjects,  # ruff: ignore[private-member-access]
        "Template:Itn/Recentdeaths": homepage._separated_subjects,  # ruff: ignore[private-member-access]
    }
    sources = {}
    for title, revision in ledger:
        stamp = revision["timestamp"]
        for linked in parsers[title](revision["text"]):
            subject = read.resolve(linked)
            sources.setdefault(
                (subject, stamp.date().isoformat()),
                Evidence(
                    title,
                    f"https://zh.wikipedia.org/w/index.php?oldid={revision['revision']}",
                    revision["revision"],
                    stamp.isoformat(),
                ),
            )
    return [
        _Expectation(
            row["title"],
            "homepage_" + section,
            "homepage_history",
            "Article history",
            sources[row["title"], row["date"]],
            _HISTORY_FIELDS[section],
            row["date"],
            entry=_dyk_entry(read, ledger, row) if section == "dyk" else None,
            link=(
                "Special:PermanentLink/"
                f"{sources[row['title'], row['date']].revision}"
            )
            if section in {"news", "history"}
            and sources[row["title"], row["date"]].revision
            else None,
        )
        for row in rows
        if row.get("date_kind") == "appearance"
        and (row["title"], row["date"]) in sources
    ]


def _dyk_entry(
    read: WikiReader,
    revisions: list[tuple[str, dict]],
    appearance: dict,
) -> str | None:
    """Read a hook only from its actual displayed DYK source revision.

    Returns:
        The verified hook, or none when its exact text is unavailable.
    """
    for title, revision in revisions:
        if (
            title == "Template:Dyk"
            and revision["timestamp"].date().isoformat() == appearance["date"]
        ):
            for original in homepage._dyk_subjects(revision["text"]):  # ruff: ignore[private-member-access]
                if read.resolve(original) == appearance["title"]:
                    entry = extract_dyk_entry(
                        revision["text"],
                        title=original,
                        site=read.site,
                    )
                    if entry:
                        return entry
    return None


def _appearance_rows(
    expectations: list[_Expectation],
    members: pl.DataFrame,
    now: dt.datetime,
) -> pl.DataFrame:
    """Attach stable project IDs to independently observed displays.

    Returns:
        Typed canonical appearance facts before any defect filtering.
    """
    rows = [
        {
            "wiki": "zhwiki",
            "title": item.title,
            "field": item.field,
            "source_kind": "homepage",
            "date": dt.date.fromisoformat(item.event_date),
            "source_title": item.source.title,
            "source_url": item.source.url,
            "source_revision": item.source.revision,
            "source_timestamp": dt.datetime.fromisoformat(
                item.source.timestamp,
            )
            if item.source.timestamp
            else None,
            "observed_at": now,
            "entry": item.entry,
            "article_revision": item.article_revision,
            "link": item.link,
        }
        for item in expectations
        if item.kind == "homepage_history" and item.event_date
    ]
    if not rows:
        return pl.DataFrame(schema=homepage_data.SCHEMA)
    return (
        pl
        .DataFrame(
            rows,
            schema={
                name: dtype
                for name, dtype in homepage_data.SCHEMA.items()
                if name != "page_id"
            },
        )
        .join(
            members.select("title", "page_id"),
            on="title",
            how="inner",
            validate="m:1",
        )
        .select(list(homepage_data.SCHEMA))
        .unique(subset=["wiki", "page_id", "field", "date", "source_url"])
    )


def _retained_expectations(
    appearances: pl.DataFrame,
    members: pl.DataFrame,
    check_id: str,
) -> list[_Expectation]:
    """Reuse all retained dates with current titles joined by page ID.

    Returns:
        One latest proof per date for the selected homepage check.
    """
    selected = (
        appearances
        .filter(
            pl.col("field") == _HOMEPAGE_CHECKS[check_id],
            pl.col("source_kind") == "homepage",
        )
        .drop("title")
        .join(
            members.select("page_id", "title"),
            on="page_id",
            how="inner",
            validate="m:1",
        )
        .sort("observed_at", "source_revision", nulls_last=False)
        .unique(subset=["page_id", "field", "date"], keep="last")
        .sort("date", "title")
    )
    return [
        _Expectation(
            row["title"],
            check_id,
            "homepage_history",
            "Article history",
            Evidence(
                row["source_title"],
                row["source_url"],
                row["source_revision"],
                row["source_timestamp"].isoformat()
                if row["source_timestamp"]
                else None,
            ),
            row["field"],
            row["date"].isoformat(),
            row["entry"],
            row["article_revision"],
            row["link"],
        )
        for row in selected.iter_rows(named=True)
    ]


def _check(
    context: _Checks,
    expectations: list[_Expectation],
    check_id: str,
) -> tuple[list[dict], dict]:
    """Check successfully read pages against source-backed expectations.

    Returns:
        Findings and an independent check availability outcome.
    """
    read, members, now = context.read, context.members, context.now
    observation_cache, alias_cache = context.observations, context.aliases
    findings = []
    titles = {
        item.title
        if item.kind == "award_template"
        else Page(read.site, item.title).toggleTalkPage().title()
        for item in expectations
    }
    observation_cache.update(
        _observations(
            read.site,
            titles - observation_cache.keys(),
        ),
    )
    for name in {item.template for item in expectations} - alias_cache.keys():
        alias_cache[name] = template_aliases(read.site, name)
    equivalents = ()
    if check_id in {"homepage_dyk", "homepage_news"} and expectations:
        equivalents = template_aliases(
            read.site,
            "DYKtalk" if check_id == "homepage_dyk" else "ITNtalk",
        )
    selected = pl.DataFrame(
        {
            "title": [item.title for item in expectations],
            "_index": list(range(len(expectations))),
        },
        schema={"title": pl.String, "_index": pl.Int64},
    ).join(
        members.select("title", "page_id", "pa_class", "pa_importance"),
        on="title",
        how="inner",
        validate="m:1",
    )
    checked = 0
    for member in selected.iter_rows(named=True):
        item = expectations[member["_index"]]
        title = (
            item.title
            if item.kind == "award_template"
            else Page(read.site, item.title).toggleTalkPage().title()
        )
        observation = observation_cache.get(title)
        if observation is None:
            continue
        checked += 1
        if not missing_metadata(
            observation.text,
            alias_cache[item.template],
            site=read.site,
            field=item.field,
            equivalents=equivalents,
            event_date=dt.date.fromisoformat(item.event_date)
            if item.event_date
            else None,
        ):
            continue
        findings.append(
            issue(
                site=read.site,
                kind=item.kind,
                check_id=item.check_id,
                title=item.title,
                page_id=member["page_id"],
                source=item.source,
                checked=observation.evidence,
                observed_at=now.isoformat(),
                expected_template=item.template,
                expected_field=item.field,
                event_date=item.event_date,
                assessment_class=member["pa_class"],
                assessment_importance=member["pa_importance"],
            ),
        )
    return findings, {
        "check_id": check_id,
        "status": "complete"
        if checked == len(expectations)
        else "unavailable",
        "checked_count": checked,
        "issue_count": len(findings),
    }


def _assessment_evidence(row: dict, site: BaseSite) -> Evidence:
    """Retain the known talk revision beside the assessed class.

    Returns:
        Original project observation reference without invented dates.
    """
    title = Page(site, row["title"]).toggleTalkPage().title()
    revision = row.get("talk_revision", row.get("talk_page_rev"))
    known = type(revision) is int and revision > 0
    return Evidence(
        title,
        f"https://zh.wikipedia.org/w/index.php?oldid={revision}"
        if known
        else page_url(site, title),
        revision if known else None,
        None,
    )


def _quality_history_grades(
    text: str,
    aliases: tuple[str, ...],
    site: BaseSite,
) -> set[str] | None:
    """Read successful grades from one unambiguous literal history.

    Returns:
        Recorded added grades, including undated actions. Missing
        history returns an empty set; malformed or ambiguous active
        markup returns none rather than establishing a missing action.
    """
    code = mwparserfromhell.parse(text)
    for tag in reversed(code.filter_tags()):
        if str(tag.tag).strip().casefold() in _QUOTED_TAGS:
            code.remove(tag)
    if any(
        isinstance(node, Text) and ("{{" in str(node) or "}}" in str(node))
        for node in code.nodes
    ):
        return None
    names = {_name(name, site) for name in aliases} - {None}
    histories = [
        template
        for template in code.filter_templates()
        if _name(str(template.name), site) in names
    ]
    if len(histories) > 1:
        return None
    if not histories:
        return set()
    events = article_history.extract_events(
        str(histories[0]),
        site,
        aliases,
        (),
    )
    return {
        event["grade"]
        for event in events
        if event["kind"] == "quality" and event["action"] == "added"
    }


def _quality_history_check(  # ruff: ignore[complex-structure]
    context: _Checks,
) -> tuple[list[dict], dict]:
    """Check current project grades against successful history actions.

    Returns:
        Findings and independent coverage. Only current nonredirect
        A, AL and Bplus articles participate; both A classes use WAR.
        Source failures and ambiguous markup remain unavailable.
    """
    profile = assessment_profile(context.read.site.dbName(), "电子游戏")
    candidates = context.members.with_columns(
        profile.expression("pa_class", "class", preserve_unknown=False).alias(
            "_grade",
        ),
    ).filter(pl.col("_grade").is_in(_PROJECT_ACTIONS))
    outcome = {
        "check_id": "quality_history",
        "status": "complete",
        "checked_count": 0,
        "issue_count": 0,
    }
    if candidates.is_empty():
        return [], outcome
    if "page_is_redirect" not in candidates.columns:
        return [], {**outcome, "status": "unavailable"}
    unknown_redirects = candidates["page_is_redirect"].null_count() > 0
    candidates = candidates.filter(pl.col("page_is_redirect") == 0)
    outcome["status"] = "unavailable" if unknown_redirects else "complete"
    if candidates.is_empty():
        return [], outcome
    talks = {
        Page(context.read.site, title).toggleTalkPage().title()
        for title in candidates["title"]
    }
    try:
        context.observations.update(
            _observations(
                context.read.site,
                talks - context.observations.keys(),
            ),
        )
        if "Article history" not in context.aliases:
            context.aliases["Article history"] = template_aliases(
                context.read.site,
                "Article history",
            )
    except Exception:
        _LOGGER.warning(
            "Project quality history source unavailable.",
            exc_info=True,
        )
        return [], {**outcome, "status": "unavailable"}
    aliases = context.aliases["Article history"]
    if not aliases:
        return [], {**outcome, "status": "unavailable"}
    findings = []
    for member in candidates.iter_rows(named=True):
        talk = (
            Page(context.read.site, member["title"]).toggleTalkPage().title()
        )
        observation = context.observations.get(talk)
        if observation is None:
            continue
        try:
            grades = _quality_history_grades(
                observation.text,
                aliases,
                context.read.site,
            )
        except Exception:
            _LOGGER.warning(
                "Project quality history parse unavailable: %s",
                talk,
                exc_info=True,
            )
            continue
        if grades is None:
            continue
        outcome["checked_count"] += 1
        grade, action = _PROJECT_ACTIONS[member["_grade"]]
        if grade not in grades:
            findings.append(
                issue(
                    site=context.read.site,
                    kind="quality_history",
                    check_id="quality_history",
                    title=member["title"],
                    page_id=member["page_id"],
                    source=_assessment_evidence(member, context.read.site),
                    checked=observation.evidence,
                    observed_at=context.now.isoformat(),
                    expected_template="Article history",
                    expected_field=action,
                    assessment_class=member["pa_class"],
                    assessment_importance=member["pa_importance"],
                ),
            )
    if outcome["checked_count"] != candidates.height:
        outcome["status"] = "unavailable"
    outcome["issue_count"] = len(findings)
    return findings, outcome


def _migration_candidates(context: _Checks, path: Path | None) -> pl.DataFrame:
    """Select retained DYK identities without scanning every talk page.

    Returns:
        Current members whose canonical talk history records DYK dates.
    """
    if path is None:
        return context.members.head(0)
    talks = history_store.load(path)
    identities = (
        talks
        .select("page_id", "events")
        .explode("events", empty_as_null=False)
        .unnest("events")
        .filter(pl.col("kind") == "dyk")
        .select("page_id")
        .unique()
    )
    return context.members.join(identities, on="page_id", how="semi")


def _appearance_record(row: dict) -> dict[str, object]:
    """Prepare JSON-safe provenance for one retained appearance proof.

    Returns:
        Date, field, exact source and independently known companions.
    """
    return {
        name: value.isoformat()
        if isinstance(value, (dt.date, dt.datetime))
        else value
        for name, value in row.items()
        if name not in {"wiki", "page_id", "title"}
    }


def _talk_snapshot(
    context: _Checks,
    member: dict,
    observation: _Observation,
) -> dict | None:
    """Prepare actual checked talk metadata without synthetic revisions.

    Returns:
        A complete parsed snapshot, or none when its page ID is unknown.
    """
    if not observation.page_id or not observation.evidence.revision:
        return None
    if "ITNtalk" not in context.aliases:
        context.aliases["ITNtalk"] = template_aliases(
            context.read.site,
            "ITNtalk",
        )
    contract = hashlib.sha256(
        json.dumps(
            {
                "parser": "consistency-homepage:dyk-questions",
                "aliases": {
                    name: list(context.aliases[name])
                    for name in ("Article history", "DYKtalk", "ITNtalk")
                },
            },
            sort_keys=True,
        ).encode(),
    ).hexdigest()
    return {
        "page_id": member["page_id"],
        "title": member["title"],
        "talk_page_id": observation.page_id,
        "revision_id": observation.evidence.revision,
        "observed_at": context.now,
        "contract": contract,
        "events": article_history.extract_events(
            observation.text,
            context.read.site,
            context.aliases["Article history"],
            context.aliases["DYKtalk"],
            context.aliases["ITNtalk"],
            article_title=member["title"],
            include_reviews=True,
        ),
    }


def _parameter_findings(
    context: _Checks,
    member: dict,
    observation: _Observation,
    suggestions: HomepageSuggestions,
    rows: list[dict],
) -> list[dict]:
    """Associate missing companions with their source proof.

    Returns:
        Hook/oldid or conversion findings; optional links alone are not
        classified as metadata defects.
    """
    required = [
        key
        for key in suggestions.parameters
        if not re.fullmatch(r"(?:itn|otd)(?:[1-9]\d*)?link", key)
        and not (
            (
                copied := re.fullmatch(
                    r"(dyk|itn|otd)[1-9]\d*(date|entry|oldid|link)",
                    key,
                )
            )
            and copied[1] + copied[2] in suggestions.obsolete_parameters
        )
    ]
    if not required and (
        suggestions.article_history_present
        or not suggestions.legacy_dyk_template_count
    ):
        return []
    findings = []
    for key in required or ["Article history"]:
        appearance = suggestions.parameter_appearances.get(key)
        proof = next(
            (
                row
                for row in rows
                if appearance is not None
                and row["field"] == appearance.field
                and row["date"] == appearance.date
                and row["source_kind"] == "homepage"
                and (
                    not key.endswith("entry")
                    or row["entry"] == appearance.entry
                )
                and (
                    not key.endswith("oldid")
                    or row["article_revision"] == appearance.article_revision
                )
            ),
            None,
        )
        source = (
            Evidence(
                proof["source_title"],
                proof["source_url"],
                proof["source_revision"],
                proof["source_timestamp"].isoformat()
                if proof["source_timestamp"]
                else None,
            )
            if proof
            else observation.evidence
        )
        findings.append(
            issue(
                site=context.read.site,
                kind="homepage_history",
                check_id="homepage_parameters",
                title=member["title"],
                page_id=member["page_id"],
                source=source,
                checked=observation.evidence,
                observed_at=context.now.isoformat(),
                expected_template="Article history",
                expected_field=key,
                event_date=appearance.date.isoformat() if appearance else None,
                assessment_class=member["pa_class"],
                assessment_importance=member["pa_importance"],
            ),
        )
    return findings


def _talk_appearance_rows(
    context: _Checks,
    member: dict,
    observation: _Observation,
    appearances: tuple[HomepageAppearance, ...],
) -> list[dict]:
    """Retain talk assertions separately from verified display sources.

    Returns:
        Actual checked-page provenance for every parsed date and hook.
    """
    rows = [
        {
            "wiki": "zhwiki",
            "page_id": member["page_id"],
            "title": member["title"],
            "field": appearance.field,
            "source_kind": "talk",
            "date": appearance.date,
            "source_title": observation.evidence.title,
            "source_url": observation.evidence.url,
            "source_revision": observation.evidence.revision,
            "source_timestamp": dt.datetime.fromisoformat(
                observation.evidence.timestamp,
            )
            if observation.evidence.timestamp
            else None,
            "observed_at": context.now,
            "entry": appearance.entry,
            "article_revision": appearance.article_revision,
            "link": appearance.link,
        }
        for appearance in appearances
    ]
    if not rows:
        return []
    companions = ("entry", "article_revision", "link")
    return (
        pl
        .DataFrame(rows, schema=homepage_data.SCHEMA)
        .group_by(
            [name for name in homepage_data.SCHEMA if name not in companions],
            maintain_order=True,
        )
        .agg(
            pl
            .when(pl.col(name).drop_nulls().n_unique() <= 1)
            .then(pl.col(name).drop_nulls().first())
            .otherwise(None)
            .alias(name)
            for name in companions
        )
        .select(list(homepage_data.SCHEMA))
        .to_dicts()
    )


def _withhold_ambiguous_suggestions(
    context: _Checks,
    observation: _Observation,
    conflict: AmbiguousArticleHistoryError,
    findings: list[dict],
    rows: list[dict],
) -> None:
    """Retain findings while explicitly withholding unsafe repairs.

    Related homepage checks remain unavailable until a unique template
    destination can be verified. Other article checks can still finish.
    """
    _LOGGER.warning(
        "Withheld homepage parameter suggestions for %s "
        "(talk_revision=%s): %s active Article history templates.",
        observation.evidence.title,
        observation.evidence.revision,
        conflict.template_count,
    )
    context.unavailable_checks.update(
        check_id
        for check_id, date_field in _HOMEPAGE_CHECKS.items()
        if any(row["field"] == date_field for row in rows)
    )
    context.unavailable_checks.update(
        finding["check_id"] for finding in findings
    )
    for finding in findings:
        finding.update(
            suggested_parameters={},
            suggested_parameters_available=False,
            article_history_present=True,
            removable_dyk_template_count=0,
            obsolete_parameters=[],
        )


def _retain_section0_repair(
    context: _Checks,
    member: dict,
    observation: _Observation,
    suggestions: HomepageSuggestions,
    findings: list[dict],
) -> None:
    """Retain one checked lead and share its finding reference."""
    if not findings or len(context.repair_inputs) >= MAX_REPAIR_INPUTS:
        return
    facts = prepare_section0_repair(
        observation.text,
        suggestions,
        aliases=context.aliases["Article history"],
        legacy_dyk_aliases=context.aliases["DYKtalk"],
        site=context.read.site,
    )
    if facts is None:
        return
    evidence = observation.evidence
    record = {
        "page_id": member["page_id"],
        "talk_page_id": observation.page_id,
        "title": evidence.title,
        "revision_id": evidence.revision,
        "timestamp": evidence.timestamp,
        **facts,
    }
    record["id"] = repair_identity(record)
    size = len(json.dumps({record["id"]: record}, ensure_ascii=False).encode())
    if (
        not valid_repair_input(record)
        or context.repair_input_bytes + size > MAX_REPAIR_INPUT_BYTES - 1024
    ):
        return
    linked = [
        finding
        for finding in findings
        if finding["checked_title"] == evidence.title
        and finding["checked_revision"] == evidence.revision
        and finding["checked_timestamp"] == evidence.timestamp
    ]
    if not linked:
        return
    context.repair_inputs[record["id"]] = record
    context.repair_input_bytes += size
    for finding in linked:
        finding["repair_id"] = record["id"]


# Keep checks, suggestions and retention in one article transaction.
def _attach_homepage_details(  # ruff: ignore[complex-structure, too-many-branches, too-many-locals]
    context: _Checks,
    items: list[dict],
    history_path: Path | None = None,
) -> dict:
    """Attach retained appearances and missing-slot suggestions.

    Args:
        context: Current source reads, members and appearances.
        items: Prepared findings updated with typed manual suggestions.
        history_path: Canonical history used for migration candidates.

    Returns:
        Independent availability and counts for parameter repairs.
    """
    read, members = context.read, context.members
    candidates = members.join(
        pl.concat([
            context.appearances.select("page_id"),
            _migration_candidates(context, history_path).select("page_id"),
        ]).unique(),
        on="page_id",
        how="semi",
    )
    if candidates.is_empty():
        return {
            "check_id": "homepage_parameters",
            "status": "complete",
            "checked_count": 0,
            "issue_count": 0,
        }
    titles = {
        Page(read.site, title).toggleTalkPage().title()
        for title in candidates["title"]
    }
    context.observations.update(
        _observations(read.site, titles - context.observations.keys()),
    )
    for name in ("Article history", "DYKtalk", "DYKEntry/archive"):
        if name not in context.aliases:
            context.aliases[name] = template_aliases(read.site, name)
    grouped = dict(
        context.appearances
        .sort("date", "observed_at", descending=[True, True])
        .group_by("page_id", maintain_order=True)
        .agg(pl.struct(pl.exclude("page_id")).alias("appearances"))
        .iter_rows(),
    )
    recorded = []
    talk_snapshots = []
    checked = 0
    original_count = len(items)
    for member in candidates.iter_rows(named=True):
        page_id, title = member["page_id"], member["title"]
        observation = context.observations.get(
            Page(read.site, title).toggleTalkPage().title(),
        )
        if observation is None:
            continue
        rows = grouped.get(page_id, [])
        appearances = [
            HomepageAppearance(
                row["field"],
                row["date"],
                row["entry"],
                row["article_revision"],
                row["link"],
            )
            for row in rows
            if row["source_kind"] == "homepage"
        ]
        snapshot = _talk_snapshot(context, member, observation)
        if snapshot is not None:
            talk_snapshots.append(snapshot)
        findings = [
            row
            for row in items
            if row["kind"] == "homepage_history" and row["page_id"] == page_id
        ]
        try:
            suggestions = prepare_history_suggestions(
                observation.text,
                appearances,
                aliases=context.aliases["Article history"],
                site=read.site,
                legacy_dyk_aliases=context.aliases["DYKtalk"],
                article_title=title,
                dyk_archive_aliases=context.aliases["DYKEntry/archive"],
            )
        except AmbiguousArticleHistoryError as error:
            _withhold_ambiguous_suggestions(
                context,
                observation,
                error,
                findings,
                rows,
            )
            talk_appearances = error.talk_appearances
        else:
            checked += 1
            if not findings:
                findings = _parameter_findings(
                    context,
                    member,
                    observation,
                    suggestions,
                    rows,
                )
                items.extend(findings)
            for finding in findings:
                finding.update(
                    suggested_parameters=suggestions.parameters,
                    suggested_parameters_available=True,
                    article_history_present=suggestions.article_history_present,
                    legacy_dyk_template_count=suggestions.legacy_dyk_template_count,
                    removable_dyk_template_count=suggestions.removable_dyk_template_count,
                    obsolete_parameters=list(suggestions.obsolete_parameters),
                )
            _retain_section0_repair(
                context,
                member,
                observation,
                suggestions,
                findings,
            )
            talk_appearances = suggestions.talk_appearances
        local_records = _talk_appearance_rows(
            context,
            member,
            observation,
            talk_appearances,
        )
        recorded.extend(local_records)
        known = {
            (row["field"], row["date"], row["source_url"]) for row in rows
        }
        for record in local_records:
            identity = record["field"], record["date"], record["source_url"]
            if identity not in known:
                rows.append(record)
                known.add(identity)
        if findings:
            findings[0]["appearance_history"] = [
                _appearance_record(row) for row in rows
            ]
    if recorded:
        observed = pl.DataFrame(recorded, schema=homepage_data.SCHEMA).unique()
        context.appearances = (
            history_store.merge_homepage(history_path, observed)
            if history_path is not None
            else homepage_data.merge(context.appearances, observed)
        )
    if talk_snapshots and history_path is not None:
        history_store.merge_save(
            history_path,
            pl.DataFrame(talk_snapshots, schema=history_store.SCHEMA),
        )
    return {
        "check_id": "homepage_parameters",
        "status": "complete"
        if checked == candidates.height
        else "unavailable",
        "checked_count": checked,
        "issue_count": len(items) - original_count,
    }


# One pass coordinates independent checks and their source coverage.
def collect_consistency(  # ruff: ignore[complex-structure, too-many-arguments, too-many-locals, too-many-statements, too-many-branches]
    read: WikiReader,
    membership: pl.DataFrame,
    *,
    now: dt.datetime,
    days: int = 100,
    project: str = "电子游戏",
    history_path: Path | None = None,
    archive_path: Path | None = None,
) -> dict[str, object]:
    """Collect independently evidenced website maintenance findings.

    Args:
        read: Existing read-only source reader for this cycle.
        membership: Current selected project identities and assessments.
        now: Actual aware UTC acquisition time.
        days: Recent homepage window; the GA report covers its history.
        project: Exact configured project identity.
        history_path: Shared canonical article-history Parquet file,
            or none for an in-memory offline pass.
        archive_path: Wiki-wide dated FA/GA inventory, initialized from
            all archives and extended daily when supplied.

    Returns:
        Findings with explicit availability outcomes. No source page is
        edited by this collector.

    Raises:
        ValueError: The source scope or acquisition bounds are invalid.
    """
    if (
        read.site.dbName() != "zhwiki"
        or project != "电子游戏"
        or now.utcoffset() is None
        or not 1 <= days <= _MAX_DAYS
    ):
        message = "Consistency checks require the verified wiki/project scope."
        raise ValueError(message)
    now = now.astimezone(dt.UTC)
    members = _members(membership)
    profile = assessment_profile(read.site.dbName(), project)
    items, checks = [], []
    context = _Checks(read, members, now)
    if history_path is not None:
        context.appearances = history_store.read_homepage(history_path)
    end = now.date() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=days)
    archive = pl.DataFrame(schema=homepage_archive.SCHEMA)
    archive_available = True
    if archive_path is not None:
        try:
            archive = homepage_archives.collect(read, archive_path, now=now)
        except Exception:
            _LOGGER.warning(
                "Homepage archive inventory unavailable",
                exc_info=True,
            )
            archive_available = False
    builders = {
        kind: lambda kind=kind: _nomination_expectations(read, members, kind)
        for kind in _NOMINATIONS
    }
    for kind, template in _AWARDS.items():
        rows = members.filter(
            profile.expression("pa_class", "class", preserve_unknown=False)
            == kind.upper(),
        )
        builders["award_" + kind] = (
            lambda rows=rows, kind=kind, template=template: [
                _Expectation(
                    row["title"],
                    "award_" + kind,
                    "award_template",
                    template,
                    _assessment_evidence(row, read.site),
                )
                for row in rows.iter_rows(named=True)
            ]
        )
    builders["homepage_good"] = lambda: [
        *_good_expectations(read, members, now),
        *_archive_expectations(archive, members, "good"),
    ]
    for section in ("featured", "history"):
        builders["homepage_" + section] = lambda section=section: ([
            *_daily_expectations(
                read,
                members,
                section,
                start,
                end,
            ),
            *(
                _archive_expectations(archive, members, section)
                if section == "featured"
                else []
            ),
        ])
    for section in ("dyk", "news"):
        builders["homepage_" + section] = lambda section=section: (
            _timeline_expectations(
                read,
                members,
                section,
                start,
                end,
            )
        )
    for check_id, build in builders.items():
        acquired = True
        try:
            expectations = build()
        except Exception:
            _LOGGER.warning(
                "Consistency source unavailable: %s",
                check_id,
                exc_info=True,
            )
            acquired = False
            expectations = []
        if check_id in _HOMEPAGE_CHECKS:
            observed = _appearance_rows(expectations, members, now)
            if observed.height:
                context.appearances = (
                    history_store.merge_homepage(history_path, observed)
                    if history_path is not None
                    else homepage_data.merge(context.appearances, observed)
                )
            expectations = _retained_expectations(
                context.appearances,
                members,
                check_id,
            )
        try:
            findings, outcome = _check(context, expectations, check_id)
        except Exception:
            _LOGGER.warning(
                "Consistency page check unavailable: %s",
                check_id,
                exc_info=True,
            )
            findings, outcome = (
                [],
                {
                    "check_id": check_id,
                    "status": "unavailable",
                    "checked_count": 0,
                    "issue_count": 0,
                },
            )
        if not acquired or (
            not archive_available
            and check_id in {"homepage_featured", "homepage_good"}
        ):
            outcome["status"] = "unavailable"
        items.extend(findings)
        checks.append(outcome)
    findings, outcome = _quality_history_check(context)
    items.extend(findings)
    checks.append(outcome)
    findings, outcomes = collect_list_checks(read, membership, now=now)
    items.extend(findings)
    checks.extend(outcomes)
    items = list({row["id"]: row for row in items}.values())
    checks.append(_attach_homepage_details(context, items, history_path))
    checks = [
        {**check, "status": "unavailable"}
        if check["check_id"] in context.unavailable_checks
        else check
        for check in checks
    ]
    return {
        "schema": 1,
        "wiki": "zhwiki",
        "project": project,
        "checked_at": now.isoformat(),
        "repair_inputs": context.repair_inputs,
        "items": items,
        "checks": checks,
        "sources": list(
            {
                row["source_url"]: {
                    "label": row["source_title"],
                    "url": row["source_url"],
                }
                for row in items
            }.values(),
        ),
    }
