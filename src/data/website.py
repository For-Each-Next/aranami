"""Validate and retain saved dashboard observations.

Collectors use the same bounded contracts as read-only HTTP consumers.
Each successful report keeps its observed dates across failed refreshes.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from data import consistency as consistency_records
from data.caching import consistency as consistency_cache
from data.caching.extracts import MAX_EXTRACT_BYTES
from data.newsletter import schema as newsletter
from data.prepared import read_values, write_values
from shared.support.files import read_json_object

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


def read_snapshot(path: Path) -> dict[str, object]:
    """Read a precomputed Parquet view without source requests.

    Legacy JSON is accepted only for existing migrated observations.

    Returns:
        Complete precomputed presentation values and source identities.

    """
    if path.suffix != ".parquet":
        return read_json_object(path, MAX_SNAPSHOT_BYTES)
    return read_values(path, MAX_SNAPSHOT_BYTES)


SNAPSHOT_SCHEMA = 2
REPORT_KEYS = (
    "reviews",
    "consistency",
    "recognized",
    "dyk",
    "pageviews",
    "news_history",
    "backlog",
    "newsletter",
)
APPEARANCE_SECTIONS = {
    "recognized": ("featured", "good", "upcoming"),
    "dyk": ("dyk",),
    "news_history": ("news", "history"),
}
_SECTION_RECORD_LIMIT = 10_000
_SECTION_GROUP_LIMIT = 64
_SECTION_HEADING_LIMIT = 12
_SECTION_LABEL_MAX_BYTES = 800
_CATEGORY_MAX_BYTES = 800
_ASSESSMENT_MAX_BYTES = 192
_ACCOUNT_MAX_BYTES = 1000
_SUBJECT_TITLE_MAX_BYTES = 1000
_PAGEVIEWS_LIMIT = 12
_PAGEVIEWS_SITE_LIMIT = 1000
_URL_MAX_BYTES = 4096
MAX_DAYS = 365
REVIEW_TYPES = (
    "fac",
    "flc",
    "acc",
    "gan",
    "bpan",
    "pr",
    "ppr",
    "dykc",
    "reassess",
)


def read_timestamp(value: object) -> dt.datetime | None:
    """Read an exact UTC seconds timestamp from a section cache.

    Args:
        value: Untrusted JSON field.

    Returns:
        UTC timestamp, or none for invalid values.
    """
    if not isinstance(value, str):
        return None
    try:
        stamp = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return (
        stamp
        if stamp.utcoffset() == dt.timedelta()
        and (
            stamp.isoformat(timespec="seconds") == value.replace("Z", "+00:00")
        )
        else None
    )


def _cache_wiki_url(value: object) -> bool:
    """Validate a bounded public Chinese Wikipedia URL.

    Args:
        value: Untrusted cached link.

    Returns:
        Whether the URL satisfies the presentation contract.
    """
    if (
        not isinstance(value, str)
        or len(value.encode()) > _URL_MAX_BYTES
        or re.search(
            r"[\x00-\x20\\]",
            value,
        )
    ):
        return False
    url = urlsplit(value)
    return (
        url.scheme == "https"
        and url.netloc == "zh.wikipedia.org"
        and (url.path.startswith("/wiki/") or url.path == "/w/index.php")
    )


def _cache_article(value: object) -> bool:
    """Validate a cached article identity and optional assessments.

    Args:
        value: Untrusted JSON record.

    Returns:
        Whether the article can safely be reused.
    """
    return (
        isinstance(value, dict)
        and isinstance(value.get("title"), str)
        and (
            bool(value["title"].strip())
            and _cache_wiki_url(value.get("url"))
            and (
                all(
                    value.get(field) is None
                    or (
                        isinstance(value[field], str)
                        and bool(value[field].strip())
                        and len(value[field].encode()) <= MAX_EXTRACT_BYTES
                        and re.search(r"[\x00-\x1f\x7f]", value[field]) is None
                    )
                    for field in (
                        "extract",
                        "extract_hant",
                        "extract_zh",
                        "extract_tw",
                        "extract_hk",
                    )
                )
            )
            and all(
                value.get(field) is None
                or (
                    isinstance(value[field], str)
                    and bool(value[field].strip())
                    and len(value[field].encode()) <= _ASSESSMENT_MAX_BYTES
                    and re.search(r"[\x00-\x1f\x7f]", value[field]) is None
                )
                for field in ("assessment_class", "assessment_importance")
            )
        )
    )


def _valid_backlog_cache(value: object) -> bool:  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
    """Validate complete maintenance groups and distinct article counts.

    Args:
        value: Untrusted cached backlog observation.

    Returns:
        Whether every group and referenced issue is consistent.
    """
    if (
        not isinstance(value, dict)
        or read_timestamp(value.get("observed_at")) is None
        or type(value.get("total")) is not int
        or value["total"] < 0
    ):
        return False
    groups = value.get("groups")
    if not isinstance(groups, list) or len(groups) > _SECTION_GROUP_LIMIT:
        return False
    keys, articles, references = set(), set(), set()
    sections = {}
    issues = 0
    try:  # ruff: ignore[too-many-statements-in-try-clause] - Validate the complete bounded nested contract together.
        for group in groups:
            key = group["key"]
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None
                or key in keys
            ):
                return False
            if (
                not isinstance(group["category"], str)
                or not group["category"].strip()
                or not _cache_wiki_url(group["source_url"])
                or re.fullmatch(
                    r"/wiki/(?:Category|Template):.+",
                    unquote(urlsplit(group["source_url"]).path),
                )
                is None
            ):
                return False
            rows = group["items"]
            if (
                not isinstance(rows, list)
                or len(rows) > _SECTION_RECORD_LIMIT
                or type(group["count"]) is not int
                or group["count"] != len(rows)
            ):
                return False
            for field in ("label", "section_label"):
                label = group.get(field)
                labels = (
                    label.values()
                    if isinstance(label, dict)
                    and label
                    and set(label) <= {"en", "zh-hans", "zh-hant"}
                    else [label]
                )
                if not all(
                    isinstance(text, str)
                    and text.strip()
                    and len(text.encode()) <= _SECTION_LABEL_MAX_BYTES
                    for text in labels
                ):
                    return False
            if (
                not isinstance(group.get("section_key"), str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", group["section_key"])
                is None
            ):
                return False
            section = group["section_key"]
            if (
                section in sections
                and sections[section] != group["section_label"]
            ):
                return False
            sections[section] = group["section_label"]
            if len(sections) > _SECTION_HEADING_LIMIT:
                return False
            seen = set()
            for row in rows:
                if (
                    not _cache_article(row)
                    or type(row.get("page_id")) is not int
                    or row["page_id"] <= 0
                    or row["page_id"] in seen
                ):
                    return False
                month = row.get("oldest_issue_month")
                if row.get("page_length") is not None and (
                    type(row["page_length"]) is not int
                    or row["page_length"] < 0
                ):
                    return False
                if month is not None and (
                    dt.date.fromisoformat(month + "-01").isoformat()[:-3]
                    != month
                ):
                    return False
                category = row.get("oldest_issue_category")
                if category is not None:
                    if month is None or not isinstance(category, str):
                        return False
                    if (
                        not category.strip()
                        or len(category.encode()) > _CATEGORY_MAX_BYTES
                        or re.search(r"[\x00-\x1f\x7f]", category) is not None
                    ):
                        return False
                tags = row["issues"]
                if (
                    not isinstance(tags, list)
                    or len(tags) > _SECTION_GROUP_LIMIT
                    or key not in tags
                    or any(not isinstance(tag, str) for tag in tags)
                    or len(set(tags)) != len(tags)
                ):
                    return False
                seen.add(row["page_id"])
                references.update(tags)
            keys.add(key)
            articles.update(seen)
            issues += len(rows)
    except KeyError, TypeError, ValueError, AttributeError:
        return False
    return (
        value["total"] == len(articles)
        and references <= keys
        and (
            "issue_total" not in value
            or (
                type(value["issue_total"]) is int
                and value["issue_total"] == issues
            )
        )
    )


def _valid_popularity_cache(
    value: dict[str, object],
) -> bool:
    """Validate a complete measured daily ranking.

    Returns:
        Whether ranks, source identity, and measured counts are valid.
    """
    day = dt.date.fromisoformat(value["date"])
    if (
        day.isoformat() != value["date"]  # ruff: ignore[too-many-boolean-expressions]
        or type(value["limit"]) is not int
        or value["limit"] not in {10, _PAGEVIEWS_LIMIT}
        or value["scope"] != "site_top_1000"
        or value["source_url"]
        != (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
            f"zh.wikipedia.org/all-access/{day:%Y/%m/%d}"
        )
        or not isinstance(value["items"], list)
        or len(value["items"]) > value["limit"]
    ):
        return False
    titles = set()
    previous = 2**64 - 1
    for index, row in enumerate(value["items"], start=1):
        if (
            not _cache_article(row)  # ruff: ignore[too-many-boolean-expressions]
            or row["title"] in titles
            or type(row.get("views")) is not int
            or not 0 <= row["views"] <= previous
            or type(row.get("rank")) is not int
            or row["rank"] != index
            or (
                row.get("site_rank") is not None
                and (
                    type(row["site_rank"]) is not int
                    or not 1 <= row["site_rank"] <= _PAGEVIEWS_SITE_LIMIT
                )
            )
        ):
            return False
        titles.add(row["title"])
        previous = row["views"]
    return True


def _valid_estimated_appearance(
    row: dict[str, object],
    section: str,
    day: dt.date | None,
    end: dt.date,
) -> bool:
    """Require bounded DYK queue projections with explicit provenance.

    Returns:
        Whether the estimate has a matching UTC slot and date.
    """
    if (
        section != "dyk"
        or day is None
        or not end - dt.timedelta(days=1) <= day < end + dt.timedelta(days=2)
    ):
        return False
    instant = read_timestamp(row.get("scheduled_at"))
    return (
        instant is not None
        and instant.date() == day
        and row.get("schedule_basis") == "source-order-cadence-estimate"
    )


def _valid_appearance_cache(  # ruff: ignore[too-many-return-statements]
    name: str,
    value: dict[str, object],
    days: int,
) -> bool:
    """Validate one complete group of appearance records.

    Returns:
        Whether source periods and every article record are valid.
    """
    period = value["period"]
    start, end = (
        dt.date.fromisoformat(period[key]) for key in ("start", "end")
    )
    if (
        end - start != dt.timedelta(days=days)
        or start.isoformat() != period["start"]
        or end.isoformat() != period["end"]
        or not isinstance(period["label"], str)
    ):
        return False
    for section in APPEARANCE_SECTIONS[name]:
        records = value["homepage"][section]
        if (
            not isinstance(records, list)
            or len(records) > _SECTION_RECORD_LIMIT
        ):
            return False
        for row in records:
            if not _cache_article(row) or "date" not in row:
                return False
            day = (
                dt.date.fromisoformat(row["date"])
                if row["date"] is not None
                else None
            )
            if day is not None and day.isoformat() != row["date"]:
                return False
            if row.get("date_kind") not in {
                None,
                "appearance",
                "window_start",
                "observed",
                "scheduled",
                "estimated",
            }:
                return False
            if row.get("date_kind") == "estimated" and not (
                _valid_estimated_appearance(row, section, day, end)
            ):
                return False
            if section == "upcoming" and (
                day is None
                or not end <= day < end + dt.timedelta(days=30)
                or row.get("date_kind") != "scheduled"
                or row.get("kind") not in {"fa", "fl", "ga"}
            ):
                return False
    return (
        isinstance(value["sources"], list)
        and len(value["sources"]) <= _SECTION_RECORD_LIMIT
        and all(
            isinstance(row, dict)
            and isinstance(row.get("label"), str)
            and _cache_wiki_url(row.get("url"))
            for row in value["sources"]
        )
    )


def _valid_review_times(row: dict[str, object]) -> bool:
    """Validate observed review times while accepting older saved rows.

    Returns:
        Whether observed times are UTC timestamps or null, a known reply
        does not precede its start, and the latest comment is not older
        than a known reply. Missing additive fields remain valid.
    """
    started = read_timestamp(row.get("started_at"))
    replied = read_timestamp(row.get("replied_at"))
    latest = read_timestamp(row.get("latest_comment_at"))
    ends = read_timestamp(row.get("ends_at"))
    return (
        "started_at" in row
        and (row["started_at"] is None or started is not None)
        and (
            row.get("replied_at") is None
            or (
                replied is not None and (started is None or replied >= started)
            )
        )
        and (row.get("latest_comment_at") is None or latest is not None)
        and (latest is None or replied is None or latest >= replied)
        and (
            row.get("ends_at") is None
            or (ends is not None and (started is None or ends > started))
        )
        and row.get("result") in {None, "passed", "failed", "ended"}
    )


def _valid_review_participants(row: dict[str, object]) -> bool:
    """Validate optional observed counts without changing older records.

    Returns:
        Whether a known count is positive and bounded, with at least two
        participants when another user's reply was observed.
    """
    count = row.get("participant_count")
    return count is None or (
        type(count) is int
        and 1 <= count <= _SECTION_RECORD_LIMIT
        and (row.get("replied_at") is None or count > 1)
    )


def _valid_discussion_metadata(row: dict[str, object]) -> bool:
    """Validate optional authors, comment counts and exact reply links.

    Returns:
        Whether bounded metadata can safely enrich older saved rows.
    """
    return (
        all(
            row.get(field) is None
            or (
                isinstance(row[field], str)
                and 0 < len(row[field].encode()) <= _ACCOUNT_MAX_BYTES
                and re.search(r"[\x00-\x1f\x7f]", row[field]) is None
            )
            for field in ("nominator", "replied_by", "latest_comment_by")
        )
        and (
            row.get("comment_count") is None
            or (
                type(row["comment_count"]) is int
                and 0 <= row["comment_count"] <= _SECTION_RECORD_LIMIT
            )
        )
        and (
            row.get("replied_url") is None
            or _cache_wiki_url(row["replied_url"])
        )
        and (
            row.get("latest_comment_url") is None
            or _cache_wiki_url(row["latest_comment_url"])
        )
        and (
            row.get("section_bytes") is None
            or (
                type(row["section_bytes"]) is int
                and 0 < row["section_bytes"] <= 64 * 1024 * 1024
            )
        )
    )


def _valid_discussions(items: object) -> bool:
    """Validate saved topics without requiring another participant.

    Returns:
        Whether every bounded topic has valid activity and source links.
    """
    return (
        isinstance(items, list)
        and len(items) <= _SECTION_RECORD_LIMIT
        and all(
            _cache_article(row)
            and row.get("review_type") == "discussion"
            and _cache_wiki_url(row.get("review_url"))
            and _valid_review_times(row)
            and type(row.get("participant_count")) is int
            and 1 <= row["participant_count"] <= _SECTION_RECORD_LIMIT
            and _valid_discussion_metadata(row)
            and (
                (
                    row.get("subject_title") is None
                    and row.get("subject_url") is None
                )
                or (
                    isinstance(row["subject_title"], str)
                    and 0
                    < len(row["subject_title"].encode())
                    <= _SUBJECT_TITLE_MAX_BYTES
                    and re.search(r"[\x00-\x1f\x7f]", row["subject_title"])
                    is None
                    and _cache_wiki_url(row.get("subject_url"))
                )
            )
            for row in items
        )
    )


def _valid_reviews_cache(value: dict[str, object]) -> bool:
    """Validate bounded review records and observed discussion dates.

    Returns:
        Whether every article and discussion link is safe to display.
    """
    items = value.get("items")
    return (
        isinstance(items, list)
        and len(items) <= _SECTION_RECORD_LIMIT
        and all(
            _cache_article(row)
            and row.get("review_type") in REVIEW_TYPES
            and _cache_wiki_url(row.get("review_url"))
            and _valid_review_times(row)
            and _valid_review_participants(row)
            and _valid_discussion_metadata(row)
            for row in items
        )
        and _valid_discussions(value.get("discussions", []))
    )


def valid_report_data(  # ruff: ignore[too-many-return-statements]
    name: str,
    value: object,
    days: int,
) -> bool:
    """Validate one independent result before persisting or reusing it.

    Returns:
        Whether all required source data and original dates are valid.
    """
    if not isinstance(value, dict):
        return False
    try:
        if name == "newsletter":
            return newsletter.valid_newsletter(value)
        if name == "reviews":
            return _valid_reviews_cache(value)
        if name == "consistency":
            return consistency_records.valid_result(value)
        if name == "backlog":
            return _valid_backlog_cache(value)
        if name == "pageviews":
            return _valid_popularity_cache(value)
        return _valid_appearance_cache(name, value, days)
    except KeyError, TypeError, ValueError:
        return False


def _valid_report_metadata(value: object) -> bool:
    """Validate refresh observations independently of report contents.

    Returns:
        Whether configuration, intervals, and UTC attempts are valid.
    """
    if not isinstance(value, dict):
        return False
    for key in ("config_id", "source_config_id"):
        identity = value.get(key)
        if key == "source_config_id" and identity is None:
            continue
        if (
            not isinstance(identity, str)
            or re.fullmatch(
                r"[0-9a-f]{64}",
                identity,
            )
            is None
        ):
            return False
    interval = value.get("refresh_interval_seconds")
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int | float)
        or not math.isfinite(interval)
        or interval <= 0
        or type(value.get("failed")) is not bool
    ):
        return False
    for key in ("last_success_at", "last_attempt_at"):
        if key not in value or (
            value[key] is not None and read_timestamp(value[key]) is None
        ):
            return False
    success = read_timestamp(value["last_success_at"])
    attempt = read_timestamp(value["last_attempt_at"])
    return success is None or (attempt is not None and success <= attempt)


def _period_days(period: object) -> int:
    """Read a canonical bounded half-open display window.

    Returns:
        Window length, or zero when the period is invalid.
    """
    if not isinstance(period, dict) or not isinstance(
        period.get("label"),
        str,
    ):
        return 0
    try:
        start, end = (
            dt.date.fromisoformat(period[key]) for key in ("start", "end")
        )
    except KeyError, TypeError, ValueError:
        return 0
    days = (end - start).days
    return (
        days
        if start.isoformat() == period["start"]
        and end.isoformat() == period["end"]
        and 1 <= days <= MAX_DAYS
        else 0
    )


def _valid_dashboard_report(  # ruff: ignore[too-many-return-statements]
    name: str,
    snapshot: dict[str, object],
) -> bool:
    """Reuse private report validators for their public observations.

    Returns:
        Whether an independent report is absent or completely valid.
    """
    if (
        name in {"reviews", "newsletter", "consistency"}
        and name not in snapshot["reports"]
    ):
        return snapshot.get(name) is None
    metadata = snapshot["reports"][name]
    if not _valid_report_metadata(metadata):
        return False
    if name in APPEARANCE_SECTIONS:
        sections = {
            key: snapshot["homepage"][key] for key in APPEARANCE_SECTIONS[name]
        }
        if all(value is None for value in sections.values()):
            return True
        period = metadata.get("period")
        days = _period_days(period)
        return bool(days) and valid_report_data(
            name,
            {"homepage": sections, "period": period, "sources": []},
            days,
        )
    data = snapshot[name]
    if data is None:
        return True
    if name == "pageviews":
        success = read_timestamp(metadata["last_success_at"])
        day = dt.date.fromisoformat(data["date"])
        return (
            valid_report_data(name, data, 1)
            and success is not None
            and (success.date() - dt.timedelta(days=7) <= day < success.date())
        )
    return valid_report_data(name, data, 1)


def validate_dashboard_snapshot(value: object) -> bool:
    """Validate the public saved dashboard without creating clients.

    Public readers and private cache readers share the article, ranking,
    maintenance, and date contracts. Missing observations remain null;
    successful empty collections remain valid and distinguishable.

    Args:
        value: Decoded schema-two dashboard envelope.

    Returns:
        Whether the complete snapshot can be displayed safely.
    """
    if (
        not isinstance(value, dict)  # ruff: ignore[too-many-boolean-expressions]
        or type(value.get("schema")) is not int
        or value["schema"] != SNAPSHOT_SCHEMA
        or not _period_days(value.get("period"))
        or read_timestamp(value.get("updated_at")) is None
        or not isinstance(value.get("reports"), dict)
        or not isinstance(value.get("homepage"), dict)
    ):
        return False
    collection = value.get("collection")
    if "collection" in value and (
        not isinstance(collection, dict)
        or set(collection) != {"attempted", "failed"}
        or any(
            type(collection[key]) is not int for key in ("attempted", "failed")
        )
        or not 0
        <= collection["failed"]
        <= collection["attempted"]
        <= len(REPORT_KEYS)
    ):
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        reports_valid = all(
            _valid_dashboard_report(name, value) for name in REPORT_KEYS
        )
        sources, errors = value["sources"], value["errors"]
        popular = value.get("pageviews")
        popularity_url = popular["source_url"] if popular else None
        sources_valid = (
            isinstance(sources, list)
            and len(sources) <= _SECTION_RECORD_LIMIT
            and all(
                isinstance(source, dict)
                and isinstance(source.get("label"), str)
                and (
                    _cache_wiki_url(source.get("url"))
                    or (
                        popularity_url is not None
                        and source.get("url") == popularity_url
                    )
                )
                for source in sources
            )
        )
        errors_valid = (
            isinstance(errors, list)
            and len(errors) <= _SECTION_RECORD_LIMIT
            and all(
                isinstance(error, dict)
                and isinstance(error.get("section"), str)
                and isinstance(error.get("message"), str)
                for error in errors
            )
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,
        RecursionError,
    ):
        return False
    return reports_valid and sources_valid and errors_valid


def read_report_cache(
    path: Path | None,
    name: str,
    days: int,
    now: dt.datetime,
) -> dict[str, object] | None:
    """Read a bounded validated report and its persisted attempt state.

    Args:
        path: Private report cache, or none to disable persistence.
        name: Stable independent report key.
        days: Configured historical window length.
        now: UTC clock for rejecting future metadata.

    Returns:
        Validated envelope with original successful source data.
    """
    if path is None:
        return None
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        record = (
            consistency_cache.read(path)
            if name == "consistency"
            else read_snapshot(path)
        )
        required = {
            "schema",
            "config_id",
            "attempt_config_id",
            "observed_at",
            "attempted_at",
            "failed",
            "data",
        }
        if not isinstance(record, dict) or not required.issubset(record):
            return None
        attempted = read_timestamp(record.get("attempted_at"))
        observed = read_timestamp(record.get("observed_at"))
        if (
            record.get("schema") != SNAPSHOT_SCHEMA  # ruff: ignore[too-many-boolean-expressions]
            or any(
                not isinstance(record[field], str)
                or re.fullmatch(r"[0-9a-f]{64}", record[field]) is None
                for field in ("config_id", "attempt_config_id")
            )
            or attempted is None
            or attempted > now
            or type(record.get("failed")) is not bool
            or (
                record.get("data") is None
                and (
                    record.get("observed_at") is not None
                    or not record["failed"]
                )
            )
            or (
                record.get("data") is not None
                and (
                    observed is None
                    or observed > attempted
                    or not valid_report_data(name, record["data"], days)
                )
            )
        ):
            return None
    except OSError, ValueError, TypeError, AttributeError, RecursionError:
        return None
    return record


def save_report_cache(path: Path | None, record: dict[str, object]) -> None:
    """Persist one complete result without altering other report caches.

    Args:
        path: Private destination, or none to disable persistence.
        record: Validated successful result or failed-attempt fallback.

    Raises:
        OSError: The destination is unsafe or cannot be replaced.
    """
    if path is None:
        return
    if (
        path.parent.is_symlink()
        or path.is_symlink()
        or (
            path.exists()
            and not (
                path.is_dir() if path.name == "consistency" else path.is_file()
            )
        )
    ):
        message = "Dashboard report cache must not follow symbolic links."
        raise OSError(message)
    if path.name in {"consistency", "consistency.parquet"}:
        consistency_cache.save(path, record)
    else:
        save_snapshot(path, record)


def save_snapshot(path: Path, snapshot: dict[str, object]) -> None:
    """Atomically replace a bounded public presentation snapshot.

    Args:
        path: Destination outside the webservice's public directory.
        snapshot: Collected JSON-compatible presentation values.

    Raises:
        ValueError: The snapshot exceeds the reader's size limit.
    """
    content = json.dumps(
        snapshot,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    if len(content) > MAX_SNAPSHOT_BYTES:
        message = "The dashboard snapshot exceeds its eight MiB limit."
        raise ValueError(message)
    if path.suffix == ".parquet":
        write_values(path, snapshot, max_bytes=MAX_SNAPSHOT_BYTES)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            temporary.chmod(0o640)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
