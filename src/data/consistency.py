"""Describe and validate evidence for website consistency findings.

Findings retain observed disagreements and bounded manual suggestions.
Unavailable source revisions and timestamps remain explicitly unknown.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass
from itertools import pairwise
from operator import itemgetter
from urllib.parse import urlsplit

from data.articles.homepage_data import FIELDS as HOMEPAGE_FIELDS

_MAX_ITEMS = 10_000
_MAX_CHECKS = 64
_MAX_TEXT = 1000
_MAX_TIMESTAMP = 64
_MAX_URL = 4096
_MAX_SUGGESTED_PARAMETERS = 3000
_MAX_LEGACY_DYK_TEMPLATES = 1000
_MAX_APPEARANCES = 1000
MAX_REPAIR_INPUTS = 256
MAX_REPAIR_INPUT_BYTES = 2 * 1024 * 1024
MAX_REPAIR_TEXT_BYTES = 128 * 1024
_MAX_EDIT_SUMMARY_BYTES = 255
_REPAIR_FIELDS = {
    "id",
    "page_id",
    "talk_page_id",
    "title",
    "revision_id",
    "timestamp",
    "original_text",
    "history_template",
    "legacy_dyk_templates",
    "parameters",
    "obsolete_parameters",
}
_FIRST_PRINTABLE = 32
_UNNUMBERED_NOTICE_PARAMETERS = frozenset({
    "dykdate",
    "dykentry",
    "itndate",
    "itnlink",
    "otddate",
    "otdoldid",
    "otdlink",
})
_SUGGESTED_FIELDS = frozenset({
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
})
KINDS = frozenset({
    "nomination_banner",
    "award_template",
    "homepage_history",
    "quality_history",
    "project_list_missing",
    "project_list_extra",
    "quarterly_statistics_missing",
})


@dataclass(frozen=True, slots=True)
class Evidence:
    """Identify a source page and the revision actually observed.

    Attributes:
        title: Full source page title.
        url: Exact revision link when known, otherwise the source page.
        revision: Actual revision identifier, or none if not retained.
        timestamp: Actual revision timestamp, or none if unavailable.
    """

    title: str
    url: str
    revision: int | None
    timestamp: str | None


def _timestamp(value: object) -> bool:
    """Require an explicit aware timestamp without inventing a timezone.

    Returns:
        Whether the supplied value is a bounded aware ISO timestamp.
    """
    if not isinstance(value, str) or len(value) > _MAX_TIMESTAMP:
        return False
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _text(value: object, *, empty: bool = False) -> bool:
    """Require bounded text safe for escaped presentation.

    Returns:
        Whether text has no controls and meets the length bound.
    """
    return (
        isinstance(value, str)
        and len(value) <= _MAX_TEXT
        and (empty or bool(value.strip()))
        and not any(ord(character) < _FIRST_PRINTABLE for character in value)
    )


def _url(value: object) -> bool:
    """Require an ordinary supported-wiki source or revision link.

    Returns:
        Whether a bounded URL targets Chinese Wikipedia over HTTPS.
    """
    if not isinstance(value, str) or len(value) > _MAX_URL:
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "zh.wikipedia.org"
        and parsed.path.startswith(("/wiki/", "/w/index.php"))
    )


def _valid_issue(row: object) -> bool:
    """Validate one complete finding without consulting source clients.

    Returns:
        Whether retained identities, links, and observations are safe.
    """
    if not isinstance(row, dict):
        return False
    return (
        row.get("kind") in KINDS
        and _text(row.get("id"))
        and _text(row.get("check_id"))
        and type(row.get("page_id")) is int
        and row["page_id"] > 0
        and all(
            _text(row.get(key))
            for key in (
                "title",
                "source_title",
                "checked_title",
            )
        )
        and all(
            _url(row.get(key))
            for key in (
                "url",
                "talk_url",
                "source_url",
                "checked_url",
            )
        )
        and _timestamp(row.get("observed_at"))
        and _valid_suggestions(row)
        and _valid_homepage_details(row)
        and _text(row.get("expected_template"), empty=True)
        and all(
            row.get(key) is None or _text(row[key])
            for key in (
                "expected_field",
                "assessment_class",
                "assessment_importance",
            )
        )
        and all(
            row.get(key) is None or (type(row[key]) is int and row[key] > 0)
            for key in ("source_revision", "checked_revision")
        )
        and all(
            row.get(key) is None or _timestamp(row[key])
            for key in (
                "source_timestamp",
                "checked_timestamp",
            )
        )
        and (
            row.get("event_date") is None
            or dt.date.fromisoformat(row["event_date"]).isoformat()
            == row["event_date"]
        )
    )


def _valid_suggestions(row: dict) -> bool:
    """Keep quarterly suggestions numeric or explicitly unknown.

    Returns:
        Whether every suggested metric uses a known bounded field.
    """
    values = row.get("suggested_values")
    return values is None or (
        isinstance(values, dict)
        and set(values) <= _SUGGESTED_FIELDS
        and all(
            value is None
            or (
                type(value) in {int, float}
                and math.isfinite(value)
                and value >= 0
            )
            for value in values.values()
        )
    )


def _parameter_value(key: str, value: object) -> bool:  # ruff: ignore[too-many-return-statements]
    """Validate bounded literal suggestions for documented date slots.

    Returns:
        Whether a known parameter has an appropriate complete value.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    if key.endswith("entry"):
        return len(value.encode()) <= 16 * 1024 and not any(
            ord(character) < _FIRST_PRINTABLE and character not in "\n\t"
            for character in value
        )
    if key in {"maindateFA", "maindateGA"}:
        commented = re.fullmatch(
            r"<!-- (\d{4}-\d{2}-\d{2}(?:, \d{4}-\d{2}-\d{2})*) --> "
            r"(\d{4}-\d{2}-\d{2})",
            value,
        )
        dates = (
            [*commented[1].split(", "), commented[2]]
            if commented
            else value.split(", ")
        )
        return (
            len(value) <= 16 * 1024
            and len(dates) <= _MAX_APPEARANCES
            and dates == sorted(set(dates))
            and all(
                dt.date.fromisoformat(day).isoformat() == day for day in dates
            )
        )
    if not _text(value):
        return False
    if key.endswith("oldid"):
        return value.isascii() and value.isdecimal() and 0 < int(value) < 2**63
    if key.endswith("date"):
        return dt.date.fromisoformat(value).isoformat() == value
    return True


def _valid_appearance(row: object) -> bool:
    """Require complete public provenance for one retained appearance.

    Returns:
        Whether a dated source or talk assertion is safe to present.
    """
    return (
        isinstance(row, dict)
        and row.get("field") in HOMEPAGE_FIELDS
        and isinstance(row.get("date"), str)
        and dt.date.fromisoformat(row["date"]).isoformat() == row["date"]
        and row.get("source_kind", "homepage") in {"homepage", "talk"}
        and _text(row.get("source_title"))
        and _url(row.get("source_url"))
        and _timestamp(row.get("observed_at"))
        and (
            row.get("source_timestamp") is None
            or _timestamp(row["source_timestamp"])
        )
        and all(
            row.get(key) is None
            or (type(row[key]) is int and 0 < row[key] < 2**63)
            for key in ("source_revision", "article_revision")
        )
        and (
            row.get("entry") is None
            or _parameter_value("dykentry", row["entry"])
        )
        and (row.get("link") is None or _text(row["link"]))
    )


def _valid_obsolete_parameters(row: dict) -> bool:
    """Require a dated replacement before suggesting old-key removal.

    Returns:
        Whether retained migration names are bounded and accompanied by
        a dated replacement, without accepting unrelated parameters.
    """
    names = row.get("obsolete_parameters")
    if names is None:
        return True
    if (
        row.get("kind") != "homepage_history"
        or not isinstance(names, list)
        or len(names) > len(_UNNUMBERED_NOTICE_PARAMETERS)
        or not all(
            isinstance(name, str) and name in _UNNUMBERED_NOTICE_PARAMETERS
            for name in names
        )
        or len(names) != len(set(names))
    ):
        return False
    parameters = row.get("suggested_parameters") or {}
    return not names or (
        row.get("article_history_present") is True
        and row.get("suggested_parameters_available", True) is True
        and isinstance(parameters, dict)
        and all(
            any(
                re.fullmatch(rf"{name[:3]}[1-9]\d*date", key)
                for key in parameters
            )
            for name in names
        )
    )


def _valid_homepage_details(row: dict) -> bool:
    """Validate optional manual repairs and retained appearance history.

    Returns:
        Whether new fields are bounded while older findings stay valid.
    """
    parameters = row.get("suggested_parameters")
    available = row.get("suggested_parameters_available", True)
    if (
        not _valid_obsolete_parameters(row)
        or type(available) is not bool
        or (
            not available
            and (row.get("kind") != "homepage_history" or parameters)
        )
    ):
        return False
    if parameters is not None and (
        row.get("kind") != "homepage_history"
        or not isinstance(parameters, dict)
        or len(parameters) > _MAX_SUGGESTED_PARAMETERS
        or not all(
            isinstance(key, str)
            and re.fullmatch(
                r"maindate(?:FA|GA)|(?:dyk(?:[1-9]\d*)?(?:date|entry)|"
                r"itn(?:[1-9]\d*)?(?:date|link)|"
                r"otd(?:[1-9]\d*)?(?:date|oldid|link))",
                key,
            )
            and _parameter_value(key, value)
            for key, value in parameters.items()
        )
    ):
        return False
    if (
        "article_history_present" in row
        and type(row["article_history_present"]) is not bool
    ):
        return False
    total = row.get("legacy_dyk_template_count", 0)
    removed = row.get("removable_dyk_template_count", 0)
    if (
        type(total) is not int
        or type(removed) is not int
        or not 0 <= removed <= total <= _MAX_LEGACY_DYK_TEMPLATES
        or (not available and removed != 0)
    ):
        return False
    history = row.get("appearance_history")
    return history is None or (
        row.get("kind") == "homepage_history"
        and isinstance(history, list)
        and len(history) <= _MAX_APPEARANCES
        and all(_valid_appearance(appearance) for appearance in history)
    )


def repair_identity(record: dict) -> str:
    """Identify an exact checked lead and its prepared literal proposal.

    Args:
        record: Repair input, with its optional existing ID ignored.

    Returns:
        Stable SHA-256 identity covering source and proposal facts.
    """
    source = {key: value for key, value in record.items() if key != "id"}
    encoded = json.dumps(
        source,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _repair_text(value: object) -> bool:
    """Bound literal lead source while allowing its actual whitespace.

    Returns:
        Whether the source is bounded text without unsafe controls.
    """
    return (
        isinstance(value, str)
        and len(value.encode()) <= MAX_REPAIR_TEXT_BYTES
        and not any(
            ord(character) < _FIRST_PRINTABLE and character not in "\n\r\t"
            for character in value
        )
    )


def _repair_span(value: object, original: str) -> bool:
    """Require an exact nonempty character span in the observed lead.

    Returns:
        Whether offsets select the complete retained literal target.
    """
    return (
        isinstance(value, dict)
        and set(value) == {"start", "end", "text"}
        and type(value["start"]) is int
        and type(value["end"]) is int
        and 0 <= value["start"] < value["end"] <= len(original)
        and isinstance(value["text"], str)
        and original[value["start"] : value["end"]] == value["text"]
    )


def valid_repair_input(value: object) -> bool:
    """Validate one prepared lead repair without reading source formats.

    Args:
        value: Untrusted exact-source record prepared by the collector.

    Returns:
        Whether source guards, spans and bounded literal facts agree.
    """
    if not isinstance(value, dict) or set(value) != _REPAIR_FIELDS:
        return False
    try:
        original = value["original_text"]
        history, legacy = (
            value["history_template"],
            value["legacy_dyk_templates"],
        )
        if not (
            _repair_text(original)
            and _text(value["title"])
            and _timestamp(value["timestamp"])
            and all(
                type(value[key]) is int and 0 < value[key] < 2**63
                for key in ("page_id", "talk_page_id", "revision_id")
            )
            and (history is None or _repair_span(history, original))
            and isinstance(legacy, list)
            and len(legacy) <= _MAX_LEGACY_DYK_TEMPLATES
            and all(_repair_span(span, original) for span in legacy)
            and isinstance(value["parameters"], dict)
            and isinstance(value["obsolete_parameters"], list)
            and _valid_homepage_details({
                "kind": "homepage_history",
                "suggested_parameters": value["parameters"],
                "obsolete_parameters": value["obsolete_parameters"],
                "article_history_present": history is not None,
            })
        ):
            return False
        spans = sorted(
            [*legacy, *([history] if history is not None else [])],
            key=itemgetter("start"),
        )
        return (
            all(
                left["end"] <= right["start"]
                for left, right in pairwise(spans)
            )
            and isinstance(value["id"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["id"]) is not None
            and value["id"] == repair_identity(value)
            and bool(
                value["parameters"] or value["obsolete_parameters"] or legacy,
            )
        )
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return False


def _valid_repair_output(value: object, original: str) -> bool:
    """Validate one complete projected lead without redoing projection.

    Returns:
        Whether bounded finished source differs from its base.
    """
    return (
        isinstance(value, dict)
        and set(value) == {"text", "edit_summary"}
        and _repair_text(value["text"])
        and bool(value["text"].strip())
        and value["text"] != original
        and _text(value["edit_summary"])
        and len(value["edit_summary"].encode()) <= _MAX_EDIT_SUMMARY_BYTES
    )


def _valid_repair_maps(result: dict) -> bool:
    """Bind optional repair maps and finding links to one checked head.

    Returns:
        Whether bounded source and output maps preserve identity.
    """
    inputs = result.get("repair_inputs", {})
    outputs = result.get("section0_repairs", {})
    if not (
        isinstance(inputs, dict)
        and isinstance(outputs, dict)
        and len(inputs) <= MAX_REPAIR_INPUTS
        and set(outputs) <= set(inputs)
        and all(
            isinstance(row, dict)
            and key == row.get("id")
            and valid_repair_input(row)
            for key, row in inputs.items()
        )
        and len(json.dumps(inputs, ensure_ascii=False).encode())
        <= MAX_REPAIR_INPUT_BYTES
        and all(
            _valid_repair_output(row, inputs[key]["original_text"])
            for key, row in outputs.items()
        )
        and len(json.dumps([inputs, outputs], ensure_ascii=False).encode())
        <= 2 * MAX_REPAIR_INPUT_BYTES
    ):
        return False
    for item in result.get("items", []):
        key = item.get("repair_id")
        if key is None:
            continue
        record = inputs.get(key)
        if record is None:
            return False
        if (
            item["kind"] != "homepage_history"
            or item["page_id"] != record["page_id"]
            or item["checked_title"] != record["title"]
            or item["checked_revision"] != record["revision_id"]
            or item["checked_timestamp"] != record["timestamp"]
        ):
            return False
    return True


def review_repair(result: dict, repair_id: str) -> dict | None:
    """Resolve one ready repair without source reads or projection.

    Args:
        result: Consistency observations already validated as a whole.
        repair_id: User-selected exact source-and-proposal identity.

    Returns:
        Complete projected lead and original source guards, or none.
        This validates the selected records, not the whole worklist.
    """
    if (
        not isinstance(repair_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", repair_id) is None
    ):
        return None
    inputs, outputs = (
        result.get("repair_inputs", {}),
        result.get("section0_repairs", {}),
    )
    if not isinstance(inputs, dict) or not isinstance(outputs, dict):
        return None
    record, projected = inputs.get(repair_id), outputs.get(repair_id)
    if (
        not valid_repair_input(record)
        or record["id"] != repair_id
        or not _valid_repair_output(projected, record["original_text"])
    ):
        return None
    return {
        "id": repair_id,
        "title": record["title"],
        "revision_id": record["revision_id"],
        "timestamp": record["timestamp"],
        "talk_page_id": record["talk_page_id"],
        "original_text": record["original_text"],
        "text": projected["text"],
        "edit_summary": projected["edit_summary"],
    }


def valid_result(value: object) -> bool:
    """Validate saved consistency results without source reads.

    Args:
        value: Untrusted decoded report payload.

    Returns:
        Whether findings and independent check outcomes are bounded.
    """
    if not isinstance(value, dict):
        return False
    try:
        items, checks, sources = (
            value.get(key) for key in ("items", "checks", "sources")
        )
        return (
            type(value.get("schema")) is int
            and value["schema"] == 1
            and value.get("wiki") == "zhwiki"
            and value.get("project") == "电子游戏"
            and _timestamp(value.get("checked_at"))
            and isinstance(items, list)
            and len(items) <= _MAX_ITEMS
            and all(_valid_issue(row) for row in items)
            and _valid_repair_maps(value)
            and len({row["id"] for row in items}) == len(items)
            and isinstance(checks, list)
            and len(checks) <= _MAX_CHECKS
            and all(
                isinstance(check, dict)
                and _text(check.get("check_id"))
                and check.get("status")
                in {
                    "complete",
                    "unavailable",
                    "unsupported",
                }
                and all(
                    type(check.get(key)) is int and check[key] >= 0
                    for key in ("checked_count", "issue_count")
                )
                for check in checks
            )
            and isinstance(sources, list)
            and len(sources) <= _MAX_ITEMS
            and all(
                isinstance(source, dict)
                and _text(source.get("label"))
                and _url(source.get("url"))
                for source in sources
            )
        )
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return False
