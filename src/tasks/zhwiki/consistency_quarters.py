"""Find missing quarterly challenge rows and explain saved counts.

The challenge table follows revision 94947356. Families and weights
follow Module:PJ:VG/COUNT revision 92241022. Live category counts are
not historical project snapshots: suggestions retain that distinction.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl

from data import project_assessments, project_data
from shared.assessment_profiles import assessment_profile
from tasks.consistency_evidence import issue, source_evidence

if TYPE_CHECKING:
    from data.reports import ReportBundle
    from shared.sources.wiki import WikiReader

TITLE = "WikiProject:电子游戏/里程碑/基础条目扩充挑战"
CHECK_ID = "quarterly_challenge"
FIELDS = (
    "fa",
    "ga",
    "b",
    "c",
    "start",
    "stub",
    "top",
    "high",
    "total",
    "start_percentage",
    "quality_index",
    "en_total",
)
_FAMILIES = {
    "fa": ("FA", "FL", "A", "AL"),
    "ga": ("GA", "Bplus"),
    "b": ("B", "BL"),
    "c": ("C", "CL"),
    "start": ("Start", "List"),
    "stub": ("Stub", "SL", "Unassessed"),
}
_WEIGHTS = {"fa": 10, "ga": 8, "b": 7, "c": 5, "start": 3, "stub": 1}
_MAX_SOURCE_BYTES = 1024 * 1024
_MAX_QUARTERS = 400
_LOGGER = logging.getLogger(__name__)


def latest_quarter_end(day: dt.date) -> dt.date:
    """Return the last fully completed UTC calendar quarter.

    Args:
        day: Actual acquisition date, independent of report anchors.

    Returns:
        The day before the current quarter began.
    """
    return dt.date(day.year, ((day.month - 1) // 3) * 3 + 1, 1) - dt.timedelta(
        days=1,
    )


def _row_date(row: str) -> dt.date | None:
    """Validate a static table row, skipping headers and the today row.

    Returns:
        Exact quarter end, or null for the audited nondated rows.

    Raises:
        ValueError: A static row has an unsupported date or cell.
    """
    heading = re.search(r'(?m)^!\s+(?:scope="row"\s*\|\s*)?(.+)$', row)
    if heading is None or heading[1].startswith(("scope=", "colspan=")):
        return None
    label = heading[1].strip()
    if label == "{{#time:Y年Mj日}}":
        return None
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", label)
    if match is None:
        message = "Challenge row has an unsupported static date."
        raise ValueError(message)
    day = dt.date(*map(int, match.groups()))
    if latest_quarter_end(day + dt.timedelta(days=1)) != day:
        message = "Challenge static row is not a quarter endpoint."
        raise ValueError(message)
    cells = [
        line.rsplit("|", 1)[-1].strip()
        for line in row.splitlines()
        if line.startswith("|") and not line.startswith(("|-", "|}"))
    ]
    if len(cells) != len(FIELDS) or any(
        re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%?", value) is None
        for value in cells
    ):
        message = "Challenge static row lacks complete numeric cells."
        raise ValueError(message)
    return day


def _quarter_ends(first: dt.date, latest: dt.date) -> list[dt.date]:
    """Enumerate the bounded observed challenge period's quarter ends.

    Returns:
        All completed quarter ends starting with the first audited row.

    Raises:
        ValueError: The period exceeds the source's supported bound.
    """
    ends = []
    day = first
    while day <= latest:
        ends.append(day)
        if len(ends) > _MAX_QUARTERS:
            message = "Challenge period exceeds its supported bound."
            raise ValueError(message)
        day = latest_quarter_end(day + dt.timedelta(days=100))
    return ends


def parse_quarter_dates(text: str) -> list[dt.date]:
    """Read static quarter rows while excluding the live today row.

    Args:
        text: Exact audited challenge-page revision.

    Returns:
        Unique quarter endpoints with twelve explicit numeric cells.

    Raises:
        ValueError: The source table or dated rows are unsupported.
    """
    if len(text.encode()) > _MAX_SOURCE_BYTES:
        message = "Challenge source exceeds its bound."
        raise ValueError(message)
    code = mwparserfromhell.parse(text)
    for comment in code.filter_comments():
        code.remove(comment)
    _, separator, after = str(code).partition("== 统计 ==")
    if not separator or "== 统计 ==" in after:
        message = "Challenge source lacks its audited statistics section."
        raise ValueError(message)
    section = re.split(r"(?m)^==[^=].*?==\s*$", after, maxsplit=1)[0]
    if (
        section.count('{| class="wikitable plainlinks"') != 1
        or "电子游戏专题极高·高重要度条目统计" not in section
        or "{{#invoke:PJ:VG/COUNT|main|FA-A, ≥High}}" not in section
    ):
        message = "Challenge statistics table contract is unsupported."
        raise ValueError(message)
    dates = [
        day
        for row in re.split(r"(?m)^\|-\s*$", section)
        if (day := _row_date(row)) is not None
    ]
    if (
        not dates
        or len(dates) > _MAX_QUARTERS
        or len(set(dates)) != len(dates)
    ):
        message = "Challenge dates are missing, repeated or oversized."
        raise ValueError(message)
    return sorted(dates)


def snapshot_values(
    bundle: ReportBundle | None,
) -> dict[str, int | float | None]:
    """Calculate suggestions from exact-boundary subject observations.

    Args:
        bundle: Exact quarter's saved project snapshot, or unavailable.

    Returns:
        Class-family counts and snapshot-derived weighted statistics.
        Unknown populations, classes or importance leave values unknown.
    """
    values = dict.fromkeys(FIELDS)
    if bundle is None:
        return values
    frame = bundle.tables["members"]
    required = {
        "page_namespace",
        "page_is_redirect",
        "pa_class",
        "pa_importance",
    }
    if (
        not required.issubset(frame.columns)
        or frame.select(
            pl.any_horizontal(
                pl.col(
                    "page_namespace",
                    "page_is_redirect",
                    "pa_importance",
                ).is_null(),
            ).any(),
        ).item()
    ):
        return values
    profile = assessment_profile("zhwiki", "电子游戏")
    subjects = frame.filter(
        (pl.col("page_namespace") == 0) & (pl.col("page_is_redirect") == 0),
    ).with_columns(
        profile.expression("pa_class", "class", preserve_unknown=False),
        profile.expression(
            "pa_importance",
            "importance",
            preserve_unknown=False,
        ),
    )
    if subjects["pa_importance"].null_count():
        return values
    high = subjects.filter(pl.col("pa_importance").is_in(["Top", "High"]))
    grade = pl.col("pa_class")
    allowed = [label for labels in _FAMILIES.values() for label in labels]
    if high.filter(grade.is_null() | ~grade.is_in(allowed)).height:
        return values
    counts = high.select(
        *(
            grade.is_in(labels).sum().alias(key)
            for key, labels in _FAMILIES.items()
        ),
    ).row(0, named=True)
    values.update(counts)
    values.update(
        top=high.filter(pl.col("pa_importance") == "Top").height,
        high=high.filter(pl.col("pa_importance") == "High").height,
        total=high.height,
    )
    if high.height:
        values["start_percentage"] = round(
            (high.height - counts["stub"]) / high.height * 100,
            1,
        )
        values["quality_index"] = round(
            sum(counts[key] * weight for key, weight in _WEIGHTS.items())
            / high.height,
            2,
        )
    return values


def _snapshot_reference(bundle: ReportBundle, day: dt.date) -> dict:
    """Require and describe an exact completed-day snapshot.

    Returns:
        The persisted data identity and actual observation timestamp.

    Raises:
        ValueError: A saved snapshot has the wrong completed date.
    """
    if bundle.manifest["metadata"].get("snapshot_date") != day.isoformat():
        message = "Quarterly suggestions require an exact data date."
        raise ValueError(message)
    return {
        "snapshot_date": day.isoformat(),
        "boundary_date": (day + dt.timedelta(days=1)).isoformat(),
        "observed_at": bundle.manifest.get("calculated_at"),
        "sha256": bundle.manifest.get("sha256"),
    }


def _english_total(bundle: ReportBundle) -> int | None:
    """Count saved English subjects without filling observation gaps.

    Returns:
        The exact saved count, or null for missing observations.
    """
    frame = bundle.tables["members"]
    if frame.select(
        pl.any_horizontal(
            pl.col(
                "page_namespace",
                "page_is_redirect",
                "pa_importance",
            ).is_null(),
        ).any(),
    ).item():
        return None
    return frame.filter(
        (pl.col("page_namespace") == 0)
        & (pl.col("page_is_redirect") == 0)
        & assessment_profile("enwiki", "Video games")
        .expression("pa_importance", "importance", preserve_unknown=False)
        .is_in(["Top", "High"]),
    ).height


def _suggestions(read: WikiReader, day: dt.date) -> tuple[dict, dict]:
    """Read each wiki's quarter independently without live substitution.

    Returns:
        Suggested cells and independently retained snapshot provenance.
    """
    values, references = dict.fromkeys(FIELDS), {}
    try:
        bundle = project_assessments.read_snapshot(
            read.site.dbName(),
            "电子游戏",
            day,
        )
        if bundle is not None:
            references["zhwiki"] = _snapshot_reference(bundle, day)
            values.update(snapshot_values(bundle))
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOGGER.warning(
            "Chinese quarterly snapshot unavailable: %s",
            day,
            exc_info=True,
        )
    try:
        english = project_data.read_snapshot(
            "enwiki",
            "Video games",
            day + dt.timedelta(days=1),
        )
        if english is not None:
            references["enwiki"] = _snapshot_reference(english, day)
            values["en_total"] = _english_total(english)
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOGGER.warning(
            "English quarterly snapshot unavailable: %s",
            day,
            exc_info=True,
        )
    return values, references


def collect_quarter_check(
    read: WikiReader,
    *,
    now: dt.datetime,
) -> tuple[list[dict], dict]:
    """Report missing completed-quarter rows with only evidenced hints.

    Args:
        read: Read-only challenge source and exact revision metadata.
        now: Actual aware UTC acquisition time.

    Returns:
        Findings and independent source/contract status. Current values
        never fill a missing historical quarter.
    """
    check = {
        "check_id": CHECK_ID,
        "status": "unavailable",
        "checked_count": 0,
        "issue_count": 0,
    }
    try:
        text = read(TITLE)
    except Exception:
        _LOGGER.warning(
            "Quarterly challenge source unavailable",
            exc_info=True,
        )
        return [], check
    try:
        dates = parse_quarter_dates(text)
    except ValueError:
        check["status"] = "unsupported"
        return [], check
    latest = latest_quarter_end(now.astimezone(dt.UTC).date())
    present = set(dates)
    try:
        expected = _quarter_ends(dates[0], latest)
    except ValueError:
        check["status"] = "unsupported"
        return [], check
    missing = [day for day in expected if day not in present]
    page_id = read.source_page_ids.get(TITLE)
    if missing and (type(page_id) is not int or page_id <= 0):
        return [], check
    evidence = source_evidence(read, TITLE)
    items = []
    for day in missing:
        values, references = _suggestions(read, day)
        found = issue(
            site=read.site,
            kind="quarterly_statistics_missing",
            check_id=CHECK_ID,
            title=TITLE,
            page_id=page_id,
            source=evidence,
            checked=evidence,
            observed_at=now.isoformat(),
            event_date=day.isoformat(),
        )
        found.update(
            target_type="project",
            suggested_values=values,
            suggested_snapshot=references.get("zhwiki"),
            suggested_english_snapshot=references.get("enwiki"),
        )
        items.append(found)
    check.update(
        status="complete",
        checked_count=len(expected),
        issue_count=len(items),
    )
    return items, check
