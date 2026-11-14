"""Validate and read saved newsletter observations.

Use read_newsletter_data for the latest complete prepared generation.
Missing evidence stays unknown; this module never collects sources.
"""

from __future__ import annotations

import datetime as dt
import re
from itertools import starmap
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from data.newsletter.grades import QUALITY_GRADES, QUALITY_HISTORY_GRADES
from data.newsletter.layout import valid_main_layout
from data.newsletter.observations import OBSERVATIONS_PATH, load_observation
from data.newsletter.publication import MAX_ISSUES, valid_lifecycle
from data.newsletter.subpages import valid_subpage_layouts
from data.newsletter.validation import valid_new_pages, year_quarters
from shared.newsletter import DYK_PROJECTS, SUFFIXES, Issue

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

STAT_KEYS = ("total", "fa", "fl", "ga", "dyk", "bplus", "topics")
_HOMEPAGE_KEYS = {"featured", "good", "news", "history"}
_FORECAST_KINDS = {
    "featured": {"fa", "fl"},
    "good": {"ga"},
    "dyk": {"dyk"},
    "news": {"itn"},
    "history": {"otd"},
}
_MAX_TEXT = 250_000
_MAX_ROWS = 5000
_MAX_SOURCES = 1600
_MAX_COUNT = 10_000_000
_EARLIEST_GAME_YEAR = 1900
_LATEST_GAME_YEAR = 2099
_MAX_IDENTITIES = 100
_MAX_STATISTIC_YEARS = 100
_EARLIEST_STATISTIC_YEAR = 2000
_MAX_REVISION_ROWS = 100_000
_MONTHS = 12
_MIN_MONTH_DAYS = 28
_MAX_MONTH_DAYS = 31
# Retain read access to immutable observations written before 0.7.11.
_DYK_PROJECTS = frozenset((*DYK_PROJECTS, "音樂"))


def _text(value: object, maximum: int = _MAX_TEXT) -> bool:
    """Check text bounds and supported control characters.

    Returns:
        Whether the text is safe to retain.
    """
    if not isinstance(value, str):
        return False
    try:
        size = len(value.encode())
    except UnicodeEncodeError:
        return False
    return (
        size <= maximum
        and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value) is None
    )


def _counts(value: object) -> bool:
    """Check nullable integer observations.

    Returns:
        Whether every observation is a bounded integer or null.
    """
    return (
        isinstance(value, dict)
        and set(value) == set(STAT_KEYS)
        and all(
            item is None or (type(item) is int and 0 <= item <= _MAX_COUNT)
            for item in value.values()
        )
    )


def _rows(value: object, predicate: Callable[[object], bool]) -> bool:
    """Validate optional bounded observation lists.

    Returns:
        Whether every present row satisfies its validator.
    """
    return value is None or (
        isinstance(value, list)
        and len(value) <= _MAX_ROWS
        and all(predicate(row) for row in value)
    )


def _title(value: object) -> bool:
    """Require a wiki title safe for link interpolation.

    Returns:
        Whether the value is a bounded plain title.
    """
    return (
        _text(value, 1000)
        and bool(value.strip())
        and re.search(r"[\[\]{}|<>\r\n\t]", value) is None
    )


def _source(value: object) -> bool:
    """Require bounded HTTPS Wikipedia provenance links.

    Returns:
        Whether the source label and URL are valid.
    """
    if not isinstance(value, dict) or not _text(value.get("label"), 1000):
        return False
    url = value.get("url")
    if not _text(url, 4096) or re.search(r"[\x00-\x20\\]", url):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "zh.wikipedia.org"
        and (parsed.path == "/w/index.php" or parsed.path.startswith("/wiki/"))
    )


def _identities(value: object) -> bool:
    """Validate optional explicit account lists without inventing names.

    Returns:
        Whether every supplied account is safe and unique.
    """
    return value is None or (
        isinstance(value, list)
        and len(value) <= _MAX_IDENTITIES
        and all(_title(name) for name in value)
        and len(set(value)) == len(value)
    )


def _quality_row(
    value: object,
    issue: Issue,
    through: dt.date,
    *,
    start: dt.date | None = None,
) -> bool:
    """Validate a list change inside the reporting quarter.

    Returns:
        Whether the event has valid date, grade, and identity.
    """
    if not isinstance(value, dict) or not _title(value.get("title")):
        return False
    try:
        day = dt.date.fromisoformat(value["date"])
    except TypeError, ValueError, KeyError:
        return False
    return (
        (start or issue.start) <= day < through
        and isinstance(value.get("grade"), str)
        and value["grade"] in {"FA", "FL", "GA", "A", "AL", "Bplus"}
        and isinstance(value.get("action"), str)
        and value["action"] in {"added", "removed"}
        and _identities(value.get("authors"))
    )


def _homepage_row(
    row: object,
    section: str,
    issue: Issue,
    through: dt.date,
) -> bool:
    """Validate a primary subject and its observed display date.

    Returns:
        Whether the record belongs to this section and elapsed quarter.
    """
    if not isinstance(row, dict) or not _title(row.get("title")):
        return False
    try:
        day = dt.date.fromisoformat(row["date"])
    except KeyError, TypeError, ValueError:
        return False
    return (
        issue.start <= day < through
        and row["date"] == day.isoformat()
        and _source({"label": row["title"], "url": row.get("url")})
        and (
            isinstance(row.get("kind"), str) and row["kind"] in {"fa", "fl"}
            if section == "featured"
            else row.get("kind") == "ga"
            if section == "good"
            else True
        )
        and (
            row.get("date_kind") == "appearance"
            or (row.get("date_kind") == "window_start" and day == issue.start)
            if section == "news"
            else row.get("date_kind") is None
        )
    )


def _homepage_facts(  # ruff: ignore[too-many-return-statements]
    facts: dict,
    issue: Issue,
    through: dt.date,
) -> bool:
    """Validate optional quarter-specific homepage data and evidence.

    Returns:
        Whether source coverage agrees, or legacy fields are absent.
    """
    if "homepage" not in facts:
        return "homepage_sources" not in facts
    sections = facts["homepage"]
    sources = facts.get("homepage_sources")
    if (
        not isinstance(sections, dict)
        or set(sections) != _HOMEPAGE_KEYS
        or not isinstance(sources, dict)
    ):
        return False
    if through == issue.start:
        return all(rows is None for rows in sections.values()) and not sources
    if set(sources) != _HOMEPAGE_KEYS:
        return False
    for section, rows in sections.items():
        evidence = sources[section]
        if not _rows(
            rows,
            lambda row, section=section: _homepage_row(
                row,
                section,
                issue,
                through,
            ),
        ) or not isinstance(evidence, dict):
            return False
        if (
            evidence.get("period")
            != {"start": str(issue.start), "end": str(through)}
            or evidence.get("updated_at") != facts["updated_at"]
            or not isinstance(evidence.get("sources"), list)
            or len(evidence["sources"]) > _MAX_SOURCES
            or not all(_source(source) for source in evidence["sources"])
        ):
            return False
        if rows is None and evidence["sources"]:
            return False
        if "complete" in evidence and type(evidence["complete"]) is not bool:
            return False
    return True


def _forecast_row(row: object, issue: Issue, as_of: dt.date) -> bool:
    """Keep scheduled records separate from observed quarter history.

    Returns:
        Whether a bounded forecast belongs to the next two UTC dates.
    """
    if not isinstance(row, dict) or set(row) != {
        "section",
        "title",
        "url",
        "date",
        "kind",
        "status",
    }:
        return False
    try:
        day = dt.date.fromisoformat(row["date"])
        limit = as_of + dt.timedelta(days=2)
    except KeyError, TypeError, ValueError, OverflowError:
        return False
    section = row.get("section")
    return (
        isinstance(section, str)
        and section in _FORECAST_KINDS
        and _title(row.get("title"))
        and _source({"label": row["title"], "url": row.get("url")})
        and issue.start <= day < issue.end
        and as_of < day <= limit
        and row["date"] == day.isoformat()
        and isinstance(row.get("kind"), str)
        and row["kind"] in _FORECAST_KINDS[section]
        and isinstance(row.get("status"), str)
        and row["status"] in {"scheduled", "estimated"}
    )


def _homepage_forecasts(facts: dict, issue: Issue) -> bool:
    """Validate optional preview forecasts without changing old facts.

    Returns:
        Whether both forecast fields are absent or agree completely.
    """
    fields = {"homepage_forecasts", "homepage_forecast_as_of"}
    if not fields.intersection(facts):
        return True
    if not fields.issubset(facts):
        return False
    try:
        stamp = dt.datetime.fromisoformat(facts["homepage_forecast_as_of"])
    except TypeError, ValueError:
        return False
    rows = facts["homepage_forecasts"]
    return (
        stamp.utcoffset() == dt.timedelta()
        and facts["homepage_forecast_as_of"]
        == stamp.isoformat(timespec="seconds")
        and isinstance(rows, list)
        and _rows(rows, lambda row: _forecast_row(row, issue, stamp.date()))
    )


def with_homepage_forecasts(
    facts: dict,
    homepage: dict,
    *,
    now: dt.datetime,
) -> dict:
    """Add next-two-day forecasts from an already saved dashboard.

    Args:
        facts: Selected issue fields with actual observations.
        homepage: Saved dashboard snapshot with flattened homepage rows.
        now: Aware preview clock selecting the next two UTC dates.

    Returns:
        Detached issue fields with optional scheduled or estimated rows.
        Actual homepage history, DYK records and statistics stay intact.
        Missing sections remove stale forecasts without source reads.

    Raises:
        ValueError: The issue or preview clock is invalid.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        message = "Newsletter forecasts require an aware preview clock."
        raise ValueError(message)
    issue = Issue.parse(facts["issue"])
    stamp = now.astimezone(dt.UTC).replace(microsecond=0)
    result = {
        key: value
        for key, value in facts.items()
        if key not in {"homepage_forecasts", "homepage_forecast_as_of"}
    }
    saved = homepage.get("homepage")
    if not isinstance(saved, dict) or not any(
        isinstance(saved.get(section), list)
        for section in ("upcoming", "dyk", "news", "history")
    ):
        return result
    forecasts = {}
    for source in ("upcoming", "dyk", "news", "history"):
        rows = saved.get(source)
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            section = (
                (
                    "featured"
                    if isinstance(kind, str) and kind in {"fa", "fl"}
                    else "good"
                )
                if source == "upcoming"
                else source
            )
            record = {
                "section": section,
                "title": row.get("title"),
                "url": row.get("url"),
                "date": row.get("date"),
                "kind": {"dyk": "dyk", "news": "itn", "history": "otd"}.get(
                    source,
                    kind,
                ),
                "status": row.get("date_kind"),
            }
            if _forecast_row(record, issue, stamp.date()):
                key = (
                    section,
                    record["date"],
                    record["title"],
                    record["kind"],
                )
                forecasts.setdefault(key, record)
    result.update(
        homepage_forecasts=[forecasts[key] for key in sorted(forecasts)],
        homepage_forecast_as_of=stamp.isoformat(timespec="seconds"),
    )
    return result


def _valid_observation_stamp(
    observed: object,
    stamp: dt.datetime,
    observation_end: dt.date | None,
) -> bool:
    """Validate observation time against capture and an optional day.

    Returns:
        Whether no timestamp was retained or the aware observation is
        within a specified boundary's day and no later than collection.
    """
    if observed is None:
        return True
    try:
        observation = dt.datetime.fromisoformat(observed)
    except ValueError, TypeError:
        return False
    return (
        observation.tzinfo is not None
        and observation.utcoffset() is not None
        and stamp.tzinfo is not None
        and stamp.utcoffset() is not None
        and (
            observation_end is None
            or observation.astimezone(dt.UTC).date() == observation_end
        )
        and observation <= stamp
    )


def _award_row(row: object) -> bool:
    """Check one observed award recipient and bounded score counts.

    Returns:
        Whether the row has a valid user, gain, and cumulative total.
    """
    return (
        isinstance(row, dict)
        and _title(row.get("user"))
        and all(
            type(row.get(field)) is int and 0 <= row[field] <= _MAX_COUNT
            for field in ("gained", "total")
        )
    )


def _year_attribution(row: dict) -> bool:
    """Keep nominators distinct from unverified principal authors.

    Returns:
        Whether optional attribution fields contain safe observations.
    """
    return (
        (row.get("nominator") is None or _title(row["nominator"]))
        and _identities(row.get("authors"))
        and (
            row.get("main_author") is None
            or (
                type(row["main_author"]) is bool
                and row.get("nominator") is not None
            )
        )
        and (
            row.get("nomination_link") is None
            or _title(row["nomination_link"])
        )
    )


def _valid_dyk_quarters(
    value: object,
    year: int,
    through: dt.date,
) -> bool:
    """Validate completed quarterly DYK dates and cumulative counts.

    Returns:
        Whether observed quarters are complete, ordered, and monotonic.
    """
    if value is None:
        return True
    expected_dates = [
        end.isoformat()
        for quarter in year_quarters(Issue(year + 1, 1))
        if (end := quarter.end - dt.timedelta(days=1)) < through
    ]
    return (
        isinstance(value, list)
        and all(
            isinstance(row, dict)
            and set(row) == {"date", "count"}
            and type(row["count"]) is int
            and 0 <= row["count"] <= _MAX_COUNT
            for row in value
        )
        and [row["date"] for row in value] == expected_dates
        and [row["count"] for row in value]
        == sorted(row["count"] for row in value)
    )


def _valid_quality_years(value: object, year: int) -> bool:
    """Validate annual grade counts, including legacy additions.

    Returns:
        Whether observed years are ordered, unique, and fully shaped.
    """
    if value is None:
        return True
    return (
        isinstance(value, list)
        and len(value) <= _MAX_STATISTIC_YEARS
        and all(
            isinstance(row, dict)
            and set(row) == {"year", "grades"}
            and type(row["year"]) is int
            and _EARLIEST_STATISTIC_YEAR <= row["year"] <= year
            and isinstance(row["grades"], dict)
            and set(row["grades"]) == set(QUALITY_HISTORY_GRADES)
            and all(
                type(count) is int and 0 <= count <= _MAX_COUNT
                for count in row["grades"].values()
            )
            for row in value
        )
        and [row["year"] for row in value]
        == sorted({row["year"] for row in value})
    )


def _valid_dyk_series(value: object, through: dt.date) -> bool:
    """Validate prepared aggregate points without consulting providers.

    Returns:
        Whether optional saved dates and counts are bounded and unique.
    """
    if value is None:
        return True
    if not isinstance(value, list) or len(value) > _MAX_ROWS:
        return False
    try:
        dates = [dt.date.fromisoformat(row["date"]) for row in value]
        return (
            dates == sorted(set(dates))
            and all(day <= through for day in dates)
            and all(
                type(row["count"]) is int and 0 <= row["count"] <= 2**63 - 1
                for row in value
            )
        )
    except ValueError, TypeError, KeyError:
        return False


def _valid_annual_statistics(
    value: dict,
    year: int,
    through: dt.date,
) -> bool:
    """Validate optional annual chart and ranking observations.

    Returns:
        Whether present statistics have bounded counts and safe titles.
    """

    def count(item: object) -> bool:
        """Accept a nonnegative measured count within signed 64 bits.

        Returns:
            Whether the value is a bounded integer.
        """
        return type(item) is int and 0 <= item <= 2**63 - 1

    def item(row: object, metric: str) -> bool:
        """Validate one title and its measured count.

        Returns:
            Whether the row contains a safe title and count.
        """
        return (
            isinstance(row, dict)
            and _title(row.get("title"))
            and count(row.get(metric))
        )

    charts = value.get("quality_statistics")
    if charts is not None and not (
        isinstance(charts, list)
        and len(charts) <= _MAX_STATISTIC_YEARS
        and all(
            isinstance(row, dict)
            and type(row.get("year")) is int
            and _EARLIEST_STATISTIC_YEAR <= row["year"] <= year
            and isinstance(row.get("counts"), dict)
            and set(row["counts"]) == set(QUALITY_GRADES)
            and all(count(number) for number in row["counts"].values())
            and type(row.get("provisional")) is bool
            and (row.get("source") is None or _title(row["source"]))
            for row in charts
        )
    ):
        return False
    revisions = value.get("revision_ranking")
    if revisions is not None and not (
        isinstance(revisions, list)
        and len(revisions) <= _MAX_REVISION_ROWS
        and all(item(row, "revisions") for row in revisions)
    ):
        return False
    months = value.get("monthly_popularity")
    if months is not None and not (
        isinstance(months, list)
        and len(months) <= _MONTHS
        and all(
            item(row, "views")
            and type(row.get("month")) is int
            and 1 <= row["month"] <= _MONTHS
            and type(row.get("days")) is int
            and _MIN_MONTH_DAYS <= row["days"] <= _MAX_MONTH_DAYS
            and (
                "revision_id" not in row
                or (
                    type(row["revision_id"]) is int
                    and 0 < row["revision_id"] <= 2**63 - 1
                )
            )
            for row in months
        )
    ):
        return False
    annual = value.get("annual_popularity")
    if annual is not None:
        try:
            start, end = (
                dt.date.fromisoformat(annual[key]) for key in ("start", "end")
            )
            valid = (
                start <= end
                and end.year == year
                and count(annual.get("article_count"))
                and (
                    annual.get("total_views") is None
                    or count(annual["total_views"])
                )
                and type(annual.get("provisional")) is bool
                and _rows(annual.get("items"), lambda row: item(row, "views"))
            )
        except KeyError, TypeError, ValueError:
            return False
        if not valid:
            return False
    return (
        _valid_dyk_series(value.get("dyk_series"), through)
        and _valid_quality_years(value.get("quality_years"), year)
        and _valid_quality_years(value.get("quality_year_ends"), year)
        and _valid_dyk_quarters(
            value.get("dyk_new_quarters"),
            year,
            through,
        )
        and all(
            value.get(key) is None or count(value[key])
            for key in ("dyk_new_count", "previous_dyk_count")
        )
    )


def _valid_dyk_current(
    value: object,
    year: int,
    stamp: dt.datetime,
    quarters: list[dict] | None,
) -> bool:
    """Validate the latest observed DYK point against quarter totals.

    Returns:
        Whether the optional point has the capped UTC observation date
        and a bounded count consistent with any completed quarters.
    """
    if value is None:
        return True
    day = min(stamp.astimezone(dt.UTC).date(), dt.date(year, 12, 31))
    return (
        isinstance(value, dict)
        and set(value) == {"date", "count"}
        and value["date"] == day.isoformat()
        and day.year == year
        and type(value["count"]) is int
        and 0 <= value["count"] <= _MAX_COUNT
        and (
            not quarters
            or (
                value["date"] >= quarters[-1]["date"]
                and value["count"] >= quarters[-1]["count"]
                and (
                    value["date"] != quarters[-1]["date"]
                    or value["count"] == quarters[-1]["count"]
                )
            )
        )
    )


def _valid_year_review(
    value: object,
    issue: Issue,
    through: dt.date,
    stamp: dt.datetime,
) -> bool:
    """Validate optional annual facts while accepting older snapshots.

    Returns:
        Whether the January feature has bounded calendar-year evidence.
    """
    if value is None:
        return True
    if not isinstance(value, dict) or issue.month != 1:
        return False
    start = dt.date(issue.year - 1, 1, 1)
    quarters = value.get("new_pages_quarters")
    try:
        dyk_valid = _rows(
            value.get("dyk"),
            lambda row: (
                isinstance(row, dict)
                and _title(row.get("title"))
                and _identities(row.get("contributors"))
                and _identities(row.get("projects"))
                and (
                    row.get("projects") is None
                    or (
                        set(row["projects"]) <= _DYK_PROJECTS
                        and value.get("dyk_projects_observed_at") is not None
                    )
                )
                and start <= dt.date.fromisoformat(row["date"]) < through
                and (
                    row.get("is_game") is None or type(row["is_game"]) is bool
                )
                and (
                    row.get("release_year") is None
                    or (
                        row.get("is_game") is True
                        and type(row["release_year"]) is int
                        and _EARLIEST_GAME_YEAR
                        <= row["release_year"]
                        <= _LATEST_GAME_YEAR
                    )
                )
                and row.get("grade")
                in {None, "FA", "FL", "GA", "A", "AL", "Bplus"}
            ),
        )
    except KeyError, TypeError, ValueError:
        return False
    return (
        type(value.get("year")) is int
        and value["year"] == start.year
        and value.get("period") == f"{start}/{issue.end}"
        and value.get("observation_end") == through.isoformat()
        and type(value.get("complete")) is bool
        and value["complete"] == (through == issue.end)
        and _valid_annual_statistics(value, start.year, through)
        and _valid_dyk_current(
            value.get("dyk_current"),
            start.year,
            stamp,
            value.get("dyk_new_quarters"),
        )
        and _counts(value.get("overview"))
        and _counts(value.get("previous_overview"))
        and _valid_observation_stamp(
            value.get("overview_observed_at"),
            stamp,
            through,
        )
        and _valid_observation_stamp(
            value.get("previous_overview_observed_at"),
            stamp,
            start,
        )
        and dyk_valid
        and _valid_observation_stamp(
            value.get("dyk_assessments_observed_at"),
            stamp,
            through,
        )
        and _valid_observation_stamp(
            value.get("dyk_projects_observed_at"),
            stamp,
            None,
        )
        and _rows(
            value.get("quality_changes"),
            lambda row: (
                _quality_row(row, issue, through, start=start)
                and _year_attribution(row)
            ),
        )
        and _rows(value.get("awards"), _award_row)
        and _rows(value.get("newcomers"), _title)
        and (
            quarters is None
            or (
                isinstance(quarters, list)
                and len(quarters) == len(year_quarters(issue))
                and all(
                    isinstance(data, dict)
                    and valid_new_pages(
                        data,
                        quarter,
                        through=max(quarter.start, min(quarter.end, through)),
                    )
                    for data, quarter in zip(
                        quarters,
                        year_quarters(issue),
                        strict=True,
                    )
                )
            )
        )
    )


def _valid_issue(key: str, facts: object) -> bool:
    """Validate one saved issue and its presentation fields.

    Returns:
        Whether the entire issue satisfies the saved contract.
    """
    if not isinstance(facts, dict):
        return False
    try:
        issue = Issue.parse(key)
        stamp = dt.datetime.fromisoformat(facts["updated_at"])
        observation_end = dt.date.fromisoformat(facts["observation_end"])
        dated = facts.get("dyk") or []
        dates_valid = all(
            isinstance(row, dict)
            and _title(row.get("title"))
            and issue.start
            <= dt.date.fromisoformat(row["date"])
            < observation_end
            for row in dated
        )
    except ValueError, TypeError, KeyError, AttributeError:
        return False
    existing = facts.get("existing")
    return (
        facts.get("issue") == key
        and facts.get("period") == f"{issue.start}/{issue.end}"
        and observation_end == max(issue.start, min(issue.end, stamp.date()))
        and type(facts.get("complete")) is bool
        and facts["complete"] == (observation_end == issue.end)
        and type(facts.get("available")) is bool
        and (
            not facts["available"]
            or observation_end > issue.start
            or facts.get("overview_observed_at") is not None
        )
        and stamp.utcoffset() == dt.timedelta()
        and _counts(facts.get("overview"))
        and _valid_observation_stamp(
            facts.get("overview_observed_at"),
            stamp,
            observation_end,
        )
        and _counts(facts.get("previous_overview"))
        and (
            facts.get("quality_source") is None
            or facts["quality_source"] == "article_history"
        )
        and (
            "history_undated" not in facts
            or (
                isinstance(facts["history_undated"], dict)
                and set(facts["history_undated"])
                == {"quality", "dyk", "news", "history"}
                and all(
                    type(count) is int and 0 <= count <= _MAX_COUNT
                    for count in facts["history_undated"].values()
                )
            )
        )
        and valid_main_layout(facts.get("main_layout"))
        and valid_subpage_layouts(facts.get("subpage_layouts"))
        and valid_new_pages(
            facts.get("new_pages_data"),
            issue,
            through=observation_end,
        )
        and _valid_observation_stamp(
            (facts.get("new_pages_data") or {}).get("grades_observed_at"),
            stamp,
            None
            if "source_observed_at" in (facts.get("new_pages_data") or {})
            else observation_end,
        )
        and _valid_observation_stamp(
            (facts.get("new_pages_data") or {}).get("source_observed_at"),
            stamp,
            None,
        )
        and _homepage_facts(facts, issue, observation_end)
        and _homepage_forecasts(facts, issue)
        and _valid_year_review(
            facts.get("year_review"),
            issue,
            max(dt.date(issue.year - 1, 1, 1), min(issue.end, stamp.date())),
            stamp,
        )
        and all(
            facts.get(name) is None or _text(facts[name])
            for name in ("quality", "new_pages", "announcements")
        )
        and isinstance(existing, dict)
        and set(existing) == set(SUFFIXES)
        and all(value is None or _text(value) for value in existing.values())
        and dates_valid
        and _rows(
            facts.get("dyk"),
            lambda row: (
                isinstance(row, dict) and _identities(row.get("contributors"))
            ),
        )
        and _rows(
            facts.get("quality_changes"),
            lambda row: _quality_row(row, issue, observation_end),
        )
        and _rows(facts.get("newcomers"), _title)
        and _rows(facts.get("awards"), _award_row)
        and isinstance(facts.get("sources"), list)
        and len(facts["sources"]) <= _MAX_SOURCES
        and all(_source(source) for source in facts["sources"])
    )


def valid_newsletter(value: object) -> bool:
    """Validate saved newsletter facts without external reads.

    Args:
        value: Untrusted decoded snapshot value.

    Returns:
        Whether all issues satisfy the bounded presentation contract.
    """
    return (
        isinstance(value, dict)
        and isinstance(value.get("issues"), dict)
        and len(value["issues"]) <= MAX_ISSUES
        and valid_lifecycle(value.get("lifecycle"))
        and (
            value.get("lifecycle") is None
            or set(value["lifecycle"]["active"]) <= value["issues"].keys()
        )
        and all(starmap(_valid_issue, value["issues"].items()))
    )


def read_newsletter_data(path: Path = OBSERVATIONS_PATH) -> dict | None:
    """Read the latest complete saved facts without collecting sources.

    Args:
        path: Durable prepared-observation directory.

    Returns:
        Validated saved facts, or none before the first acquisition.
    """
    return load_observation(path, valid_newsletter)
