"""Project saved consistency findings into localized maintenance views.

These helpers consume validated observations. Consistency findings stay
separate from the maintenance catalogue and its article counts.
"""

from __future__ import annotations

import re
from hashlib import sha256
from typing import TYPE_CHECKING

import polars as pl

from data.consistency import review_repair
from webservice.consistency_review import repair_links
from webservice.presentation import display_title, title_url, wiki_link

if TYPE_CHECKING:
    from webservice.presentation import Interface, Record

_KINDS = {
    "nomination_banner": "nomination",
    "award_template": "award",
    "homepage_history": "history",
    "quality_history": "quality_history",
    "project_list_missing": "project_list_missing",
    "project_list_extra": "project_list_extra",
    "quarterly_statistics_missing": "quarterly",
}

_METRICS = (
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


def consistency_groups(
    snapshot: Record | None,
    interface: Interface,
) -> list[Record]:
    """Group consistency evidence for its own filtered views.

    Args:
        snapshot: Validated website observations, when available.
        interface: Selected interface language.

    Returns:
        Consistency groups only, preserving each observation time and
        excluding the independent maintenance catalogue and its counts.
    """
    consistency = (snapshot or {}).get("consistency")
    if consistency is None:
        return []
    repairs = {
        repair_id: review_repair(consistency, repair_id)
        for repair_id in consistency.get("section0_repairs", {})
    }
    groups = []
    for kind, task in _KINDS.items():
        articles = {}
        for finding in consistency["items"]:
            if finding["kind"] != kind:
                continue
            article = articles.setdefault(
                finding["page_id"],
                {
                    "page_id": finding["page_id"],
                    "title": finding["title"],
                    "url": finding["url"],
                    "assessment_class": finding.get("assessment_class"),
                    "assessment_importance": finding.get(
                        "assessment_importance",
                    ),
                    "page_length": None,
                    "target_type": finding.get("target_type", "article"),
                    "issues": [],
                    "oldest_issue_month": finding["observed_at"],
                    "latest_event_date": None,
                    "consistency": [],
                },
            )
            article["consistency"].append({
                **finding,
                "review_repair": repairs.get(finding.get("repair_id")),
            })
            article["oldest_issue_month"] = min(
                article["oldest_issue_month"],
                finding["observed_at"],
            )
            if finding.get("event_date"):
                article["latest_event_date"] = max(
                    article["latest_event_date"] or "",
                    finding["event_date"],
                )
        ordered = list(articles.values())
        if kind == "homepage_history":
            ordered = latest_history_first(ordered)
        groups.append({
            "key": f"consistency_{task}",
            "category": interface.text(f"consistency.task.{task}"),
            "label": interface.text(f"consistency.task.{task}"),
            "section_key": "consistency",
            "section_label": interface.text("consistency.heading"),
            "source_url": title_url("WikiProject:电子游戏"),
            "observed_at": consistency["checked_at"],
            "consistency": True,
            "target_type": "project" if task == "quarterly" else "article",
            "count": len(articles),
            "items": ordered,
            "ordered": kind == "homepage_history",
        })
    return groups


def latest_history_first(items: list[Record]) -> list[Record]:
    """Sort complete article groups by their latest affected display.

    Args:
        items: Article groups retaining every source-backed finding.

    Returns:
        Newest affected dates first, unknown dates last, with stable
        title and page-ID ties.
    """
    if not items:
        return []
    order = (
        pl
        .from_dicts(
            items,
            schema={
                "latest_event_date": pl.String,
                "title": pl.String,
                "page_id": pl.Int64,
            },
        )
        .with_row_index("position")
        .sort(
            "latest_event_date",
            "title",
            "page_id",
            descending=[True, False, False],
            nulls_last=True,
        )["position"]
    )
    return [items[position] for position in order]


def finding_details(item: Record, interface: Interface) -> list[Record]:
    """Localize evidence without changing expected template names.

    Args:
        item: Article row containing one or more saved findings.
        interface: Selected interface language.

    Returns:
        Escaped description inputs and safe evidence/revision links.
    """
    details = []
    history = []
    for finding in item.get("consistency", []):
        if finding["kind"] == "homepage_history":
            history.append(finding)
            continue
        detail = _finding_detail(finding, interface)
        detail["evidence"] = [detail.copy()]
        details.append(detail)
    if history:
        details.append(_history_detail(history, interface))
    return details


def _finding_detail(finding: Record, interface: Interface) -> Record:
    """Localize one unchanged source finding and its provenance.

    Returns:
        Escaped descriptions and exact checked/source revision links.
    """
    key = _KINDS[finding["kind"]]
    if key == "history" and not finding.get("expected_field"):
        key = "history_template"
    return {
        "article": wiki_link(
            finding["url"],
            display_title(finding["title"], interface.locale),
        ),
        "heading_id": "consistency-"
        + sha256(finding["id"].encode()).hexdigest()[:16],
        "missing_fields": [],
        "latest_date": finding.get("event_date"),
        "talk": wiki_link(
            finding["talk_url"],
            interface.text("consistency.talk"),
        ),
        "description": interface.text(
            f"consistency.finding.{key}",
            template=finding["expected_template"],
            field=finding.get("expected_field") or "",
        ),
        "source": wiki_link(
            finding["source_url"],
            finding["source_title"],
            finding["source_title"],
        ),
        "checked": wiki_link(
            finding["checked_url"],
            interface.text(
                "consistency.checked_revision"
                if finding.get("checked_revision") is not None
                else "consistency.checked_page",
            ),
            finding["checked_title"],
        ),
        "event_date": finding.get("event_date"),
        "expected_field": finding.get("expected_field"),
        "event_label": interface.text(
            "consistency.quarter_end"
            if key == "quarterly"
            else "consistency.event_date",
        ),
        "suggestions": [
            {
                "label": interface.text(f"consistency.metrics.{metric}"),
                "value": interface.text("consistency.unknown")
                if finding["suggested_values"].get(metric) is None
                else f"{finding['suggested_values'][metric]:,}"
                + ("%" if metric == "start_percentage" else ""),
                "unknown": finding["suggested_values"].get(metric) is None,
            }
            for metric in _METRICS
        ]
        if "suggested_values" in finding
        else [],
        "snapshot_date": (finding.get("suggested_snapshot") or {}).get(
            "snapshot_date",
        ),
        "observed_at": finding["observed_at"],
        "repair_actions": repair_links(
            finding.get("review_repair"),
            interface,
        ),
    }


def _parameter_order(name: str) -> tuple[int, int, int, str]:
    """Keep supported date slots beside their related source fields.

    Returns:
        Scalar FA/GA fields first, then numbered appearance groups.
    """
    if name in {"maindateFA", "maindateGA"}:
        return (0, 0, 0 if name == "maindateFA" else 1, name)
    match = re.fullmatch(r"(itn|dyk|otd)(\d*)(date|entry|oldid|link)", name)
    if match:
        group, slot, field = match.groups()
        return (
            {"itn": 2, "dyk": 3, "otd": 4}[group],
            int(slot or "1"),
            {"date": 0, "entry": 1, "oldid": 1, "link": 2}[field],
            name,
        )
    return 5, 0, 0, name


def _history_detail(findings: list[Record], interface: Interface) -> Record:
    """Consolidate one article's dates without discarding evidence.

    Args:
        findings: Homepage observations belonging to one article.
        interface: Selected language for surrounding labels.

    Returns:
        One article finding, all evidence, and literal parameter code
        using source-verified parameter updates when available. Older
        saved rows can still supply the scalar FA/GA date updates.
    """
    ordered = sorted(
        findings,
        key=lambda finding: finding.get("event_date") or "",
        reverse=True,
    )
    detail = _finding_detail(ordered[0], interface)
    fields = sorted({
        finding["expected_field"]
        for finding in findings
        if finding.get("expected_field")
    })
    if fields:
        detail["description"] = interface.text(
            "consistency.finding.history",
            template="Article history",
            field=(", " if interface.locale == "en" else "、").join(fields),
        )
    detail["missing_fields"] = fields
    evidence = {}
    for finding in ordered:
        base = _finding_detail(finding, interface)
        identity = (
            finding.get("expected_field"),
            finding.get("event_date"),
            finding["source_url"],
        )
        evidence.setdefault(identity, base)
        for appearance in finding.get("appearance_history", []):
            identity = (
                appearance["field"],
                appearance["date"],
                appearance["source_url"],
            )
            evidence.setdefault(
                identity,
                {
                    **base,
                    "expected_field": appearance["field"],
                    "event_date": appearance["date"],
                    "source": wiki_link(
                        appearance["source_url"],
                        appearance["source_title"],
                        appearance["source_title"],
                    ),
                },
            )
    detail["evidence"] = sorted(
        evidence.values(),
        key=lambda item: item.get("event_date") or "",
        reverse=True,
    )
    detail["latest_date"] = ordered[0].get("event_date")
    detail.update(_history_suggestion(findings, interface))
    return detail


def _history_suggestion(
    findings: list[Record],
    interface: Interface,
) -> Record:
    """Prepare code when checked templates permit a safe suggestion.

    Returns:
        Literal code and conversion counts, or a manual-review note.
        Older observations retain their scalar-date fallback.
    """
    if any(
        finding.get("suggested_parameters_available") is False
        for finding in findings
    ):
        return {
            "code": "",
            "legacy_dyk_count": 0,
            "suggestion_note": interface.text("consistency.ambiguous_history"),
        }
    detail = {}
    detail["obsolete_parameters"] = sorted({
        name
        for finding in findings
        for name in finding.get("obsolete_parameters", [])
    })
    parameters: dict[str, str] = {}
    for finding in findings:
        parameters.update(finding.get("suggested_parameters", {}))
    if not parameters:
        # Older rows only prove safe scalar FA/GA field updates.
        for finding in findings:
            field = finding.get("expected_field")
            day = finding.get("event_date")
            if field in {"maindateFA", "maindateGA"} and day:
                parameters[field] = max(parameters.get(field, ""), day)
    parameter_names = sorted(parameters, key=_parameter_order)
    if parameters:
        detail["missing_fields"] = parameter_names
    for name in parameter_names:
        if re.fullmatch(r"dyk(?:[1-9]\d*)?date", name):
            parameters.setdefault(name[:-4] + "entry", "")
    parameter_names = sorted(parameters, key=_parameter_order)
    detail["code"] = _parameter_code(parameters, parameter_names)
    present = {
        finding["article_history_present"]
        for finding in findings
        if type(finding.get("article_history_present")) is bool
    }
    if detail["code"] and present == {False}:
        detail["code"] = "{{Article history\n" + detail["code"] + "\n}}"
    detail["legacy_dyk_count"] = max(
        (
            finding.get("removable_dyk_template_count", 0)
            for finding in findings
        ),
        default=0,
    )
    detail["code_id"] = f"history-code-{findings[0]['page_id']}"
    return detail


def _parameter_code(parameters: dict[str, str], names: list[str]) -> str:
    """Align literal values and separate independent appearance slots.

    Returns:
        One-space-indented parameters with globally aligned equals
        signs, adjacent FA/GA fields and blank lines between notice
        slots.
        Multiline values retain their original content and whitespace.
    """
    width = max(map(len, names), default=0)
    groups: dict[tuple[int, int], list[str]] = {}
    for name in names:
        group = _parameter_order(name)[:2]
        groups.setdefault(group, []).append(
            f" | {name:<{width}} = {parameters[name]}",
        )
    return "\n\n".join("\n".join(lines) for lines in groups.values())


def coverage_status(snapshot: Record | None, interface: Interface) -> Record:
    """Describe checked and unavailable coverage separately.

    Args:
        snapshot: Validated website observations, when available.
        interface: Selected interface language.

    Returns:
        Localized collection state without treating unknown as empty.
    """
    data = (snapshot or {}).get("consistency")
    if data is None:
        return {"message": interface.text("consistency.unavailable")}
    checks = data["checks"]
    return {
        "message": interface.text(
            "consistency.coverage",
            complete=sum(check["status"] == "complete" for check in checks),
            unavailable=sum(
                check["status"] == "unavailable" for check in checks
            ),
            unsupported=sum(
                check["status"] == "unsupported" for check in checks
            ),
        ),
        "empty": not data["items"],
        "incomplete": any(check["status"] != "complete" for check in checks),
        "observed_at": data["checked_at"],
    }
