"""Compare audited quality lists with observed project membership.

The current sections follow GA revision 94409217, FA 92059899, ACC
91872763 and BPCA 91951615. Nomination and former-content sections are
excluded. Unsupported markup never establishes emptiness.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from pywikibot import Page

from data.consistency import Evidence
from shared.assessment_profiles import assessment_profile
from shared.support.titles import page_url
from tasks.consistency_evidence import issue, source_evidence
from tasks.zhwiki.consistency_quarters import collect_quarter_check

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

    from shared.sources.wiki import WikiReader

_LOGGER = logging.getLogger(__name__)
_MAX_BYTES = 1024 * 1024
_MAX_ROWS = 3000
_LIST_SCHEMA = {"title": pl.String, "listed_class": pl.String}


@dataclass(frozen=True, slots=True)
class ListContract:
    """Identify an audited page and its current-grade sections.

    Attributes:
        check_id: Stable consistency check identity.
        title: Canonical source page, excluding shortcut redirects.
        sections: Exact level-two headings and their supported grade.
    """

    check_id: str
    title: str
    sections: tuple[tuple[str, str], ...]


LIST_CONTRACTS = (
    ListContract(
        "project_list_fa",
        "WikiProject:电子游戏/典范内容",
        (("当前典范条目", "FA"), ("当前特色列表", "FL")),
    ),
    ListContract(
        "project_list_acc",
        "WikiProject:电子游戏/甲级内容",
        (("当前甲级条目", "A"), ("当前甲级列表", "AL")),
    ),
    ListContract(
        "project_list_ga",
        "WikiProject:电子游戏/优良条目",
        (("当前优良条目", "GA"),),
    ),
    ListContract(
        "project_list_bpca",
        "WikiProject:电子游戏/乙上级条目",
        (("当前乙上级条目", "Bplus"),),
    ),
)


def _row_title(line: str, site: BaseSite, grade: str) -> str | None:
    """Validate one audited row and return its subject or empty marker.

    Returns:
        Canonical subject title, or null for the explicit empty marker.

    Raises:
        ValueError: The row differs from the audited row form.
    """
    match = re.fullmatch(r"\s*#\s+(.+)", line)
    if match is None:
        message = "Unsupported numbered project-list row."
        raise ValueError(message)
    row = mwparserfromhell.parse(match[1])
    templates = row.filter_templates(recursive=False)
    if (
        len(templates) != 1
        or str(templates[0].name).strip().casefold()
        not in {"icon", "class/icon"}
        or not templates[0].has(1)
        or str(templates[0].get(1).value).strip().casefold()
        != grade.casefold()
    ):
        message = "Unsupported project-list grade icon."
        raise ValueError(message)
    row.remove(templates[0])
    links = row.filter_wikilinks(recursive=False)
    if not links and str(row).strip() == "\uff08無\uff09":
        return None
    if len(links) != 1:
        message = "Project-list row lacks one subject link."
        raise ValueError(message)
    page = Page(site, str(links[0].title).lstrip(":"))
    if page.namespace() != 0 or page.section() is not None:
        message = "Project-list row does not identify a subject page."
        raise ValueError(message)
    row.remove(links[0])
    tags = row.filter_tags(recursive=False)
    if len(tags) != 1 or str(tags[0].tag).casefold() != "small":
        message = "Project-list row lacks its audited date wrapper."
        raise ValueError(message)
    date = re.fullmatch(
        r"\uff08(\d{4}-\d{2}-\d{2})\uff09",
        str(tags[0].contents).strip(),
    )
    if date is None:
        message = "Project-list date has unsupported markup."
        raise ValueError(message)
    dt.date.fromisoformat(date[1])
    row.remove(tags[0])
    if str(row).strip():
        message = "Unexpected project-list row content."
        raise ValueError(message)
    return page.title()


def _section_rows(text: str, site: BaseSite, grade: str) -> list[dict]:
    """Require explicit evidence for either populated or empty sections.

    Returns:
        Parsed current-content titles with their section's grade.

    Raises:
        ValueError: A section lacks its verified empty marker.
    """
    rows, empty = [], 0
    for line in text.splitlines():
        if not line.lstrip().startswith("#"):
            continue
        title = _row_title(line, site, grade)
        if title is None:
            empty += 1
        else:
            rows.append({"title": title, "listed_class": grade})
    if (not rows and empty != 1) or (rows and empty):
        message = "An empty project section requires its explicit marker."
        raise ValueError(message)
    return rows


def parse_current_list(
    text: str,
    site: BaseSite,
    contract: ListContract,
) -> pl.DataFrame:
    """Parse verified current-content rows and explicit empty markers.

    Args:
        text: Exact source revision text.
        site: Namespace and title configuration, without requests.
        contract: Audited current headings and grade icons.

    Returns:
        Distinct subject titles and their listed grade.

    Raises:
        ValueError: Source rows do not follow the audited contract.
    """
    if len(text.encode()) > _MAX_BYTES:
        message = "Project list exceeds its source bound."
        raise ValueError(message)
    code = mwparserfromhell.parse(text)
    sections = code.get_sections(levels=[2], include_lead=False, flat=False)
    rows = []
    for heading, grade in contract.sections:
        matches = [
            part
            for part in sections
            if str(part.filter_headings(recursive=False)[0].title).strip()
            == heading
        ]
        if len(matches) != 1:
            message = "Project list lacks one unambiguous current section."
            raise ValueError(message)
        rows.extend(_section_rows(str(matches[0]), site, grade))
    frame = pl.DataFrame(rows, schema=_LIST_SCHEMA)
    if frame.height > _MAX_ROWS or frame["title"].n_unique() != frame.height:
        message = "Project-list identities are duplicated or oversized."
        raise ValueError(message)
    return frame


def _compare(
    read: WikiReader,
    membership: pl.DataFrame,
    listed: pl.DataFrame,
    contract: ListContract,
    now: dt.datetime,
) -> list[dict]:
    """Join identities and retain both list and assessment evidence.

    Returns:
        Missing and extra current-list members, without automatic edits.

    Raises:
        ValueError: A listed subject has no stable page identity.
    """
    names = listed["title"].to_list()
    read.resolve_many(names)
    resolved = [
        {
            "page_id": read.resolve_page_id(name),
            "listed_title": read.resolve(name),
        }
        for name in names
    ]
    actual = pl.DataFrame(
        resolved,
        schema={"page_id": pl.Int64, "listed_title": pl.String},
    )
    if actual["page_id"].null_count():
        message = "A listed subject has no observed stable page identity."
        raise ValueError(message)
    actual = actual.unique("page_id")
    members = membership.filter(pl.col("page_namespace") == 0)
    expected = members.filter(
        assessment_profile("zhwiki", "电子游戏")
        .expression("pa_class", "class", preserve_unknown=False)
        .is_in([grade for _, grade in contract.sections]),
    )
    missing = expected.join(actual, on="page_id", how="anti").with_columns(
        pl.lit("project_list_missing").alias("kind"),
    )
    extra = (
        actual
        .join(expected.select("page_id"), on="page_id", how="anti")
        .join(members, on="page_id", how="left")
        .with_columns(
            pl.coalesce("full_title", "listed_title").alias("full_title"),
            pl.lit("project_list_extra").alias("kind"),
        )
    )
    checked = source_evidence(read, contract.title)
    result = []
    for row in pl.concat([missing, extra], how="diagonal_relaxed").iter_rows(
        named=True,
    ):
        title = row["full_title"]
        talk = Page(read.site, title).toggleTalkPage().title()
        revision = row.get("talk_revision", row.get("talk_page_rev"))
        source = Evidence(
            talk,
            f"https://zh.wikipedia.org/w/index.php?oldid={revision}"
            if revision
            else page_url(read.site, talk),
            revision,
            None,
        )
        result.append(
            issue(
                site=read.site,
                kind=row["kind"],
                check_id=contract.check_id,
                title=title,
                page_id=row["page_id"],
                source=source,
                checked=checked,
                observed_at=now.isoformat(),
                assessment_class=row.get("pa_class"),
                assessment_importance=row.get("pa_importance"),
            ),
        )
    return result


def collect_list_checks(
    read: WikiReader,
    membership: pl.DataFrame,
    *,
    now: dt.datetime,
) -> tuple[list[dict], list[dict]]:
    """Check each audited list independently against current membership.

    Args:
        read: Configured read-only wiki source with revision metadata.
        membership: Complete selected project rows with stable page IDs.
        now: Actual aware UTC collection time.

    Returns:
        Findings and independent source/contract statuses.
    """
    items, checks = [], []
    for contract in LIST_CONTRACTS:
        check = {
            "check_id": contract.check_id,
            "status": "unavailable",
            "checked_count": 0,
            "issue_count": 0,
        }
        try:
            text = read(contract.title)
        except Exception:
            _LOGGER.warning(
                "Project list unavailable: %s",
                contract.title,
                exc_info=True,
            )
        else:
            try:
                listed = parse_current_list(text, read.site, contract)
                found = _compare(read, membership, listed, contract, now)
            except ValueError:
                check["status"] = "unsupported"
                _LOGGER.warning(
                    "Project list contract unsupported: %s",
                    contract.title,
                    exc_info=True,
                )
            except Exception:
                _LOGGER.warning(
                    "Project list lookup unavailable: %s",
                    contract.title,
                    exc_info=True,
                )
            else:
                items.extend(found)
                check.update(
                    status="complete",
                    checked_count=listed.height,
                    issue_count=len(found),
                )
        checks.append(check)
    quarterly, quarterly_check = collect_quarter_check(read, now=now)
    items.extend(quarterly)
    checks.append(quarterly_check)
    return items, checks
