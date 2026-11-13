"""Prepare retained task executions and their saved stage details.

Call ``history_context`` with a validated observability envelope. Stages
remain observations in chronological order, without reading log files.
"""

from __future__ import annotations

import datetime as dt
import operator
import re
from html import unescape
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from markupsafe import Markup, escape

from data.parquet_preview import preview_path
from webservice.presentation import (
    catalog,
    duration,
    failure_description,
    local_url,
    timestamp,
    title_url,
    utc_time,
    wiki_link,
)

if TYPE_CHECKING:
    from webservice.presentation import Interface, Record

_HISTORY_HOURS = 168


_PROVIDER_DOCUMENTATION = {
    "mediawiki": "https://www.mediawiki.org/wiki/API:Main_page",
    "pageviews": (
        "https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/"
        "reference/page-views.html"
    ),
}
_TABLE_DOCUMENTATION = {
    "page": "https://www.mediawiki.org/wiki/Manual:Page_table",
    "categorylinks": "https://www.mediawiki.org/wiki/Manual:Categorylinks_table",
    "linktarget": "https://www.mediawiki.org/wiki/Manual:Linktarget_table",
    "page_assessments": (
        "https://www.mediawiki.org/wiki/Extension:PageAssessments/"
        "page_assessments_table"
    ),
    "page_assessments_projects": (
        "https://www.mediawiki.org/wiki/Extension:PageAssessments/"
        "page_assessments_projects_table"
    ),
    "wb_items_per_site": (
        "https://www.mediawiki.org/wiki/Wikibase/Schema/wb_items_per_site"
    ),
}
_TABLE_ALIASES = {
    "page_assessments": "pa",
    "page_assessments_projects": "pap",
    "categorylinks": "cl",
    "linktarget": "lt",
}
_DATABASE_DOCUMENTATION = (
    "https://www.mediawiki.org/wiki/Manual:Database_layout"
)


def _message(interface: Interface, key: str, **values: object) -> Markup:
    """Format prose with escaped values and explicitly safe links.

    Returns:
        Localized markup containing only explicitly prepared links.
    """
    return escape(interface.text(key)).format(**values)


def _documentation(
    url: str,
    label: str,
    *,
    title: str | None = None,
) -> Markup:
    """Link a fixed primary documentation URL with an escaped label.

    Returns:
        A link using an authored primary documentation destination.
    """
    if title is not None:
        return Markup('<a href="{}" title="{}">{}</a>').format(
            url,
            title,
            label,
        )
    return Markup('<a href="{}">{}</a>').format(url, label)


def _wiki_host(wiki: str) -> str | None:
    """Resolve supported public wiki identities without a source client.

    Returns:
        An allowlisted Wikipedia or Wikidata hostname, or none.
    """
    hosts = {
        "zhwiki": "zh.wikipedia.org",
        "enwiki": "en.wikipedia.org",
        "wikidatawiki": "www.wikidata.org",
    }
    return hosts.get(wiki) or (
        wiki
        if re.fullmatch(r"[a-z0-9-]+\.wikipedia\.org", wiki)
        or wiki == "www.wikidata.org"
        else None
    )


def _source_references(source: Record) -> list[Record]:
    """Read typed references with unambiguous legacy fallbacks.

    Returns:
        Retained references without splitting ambiguous page titles.
    """
    references = source.get("references", [])
    target = source.get("target", "")
    if not references and target:
        if (
            source.get("provider") == "replica"
            and source.get("dataset") == "database_query"
        ):
            names = [value.strip() for value in target.split(",")]
            if all(re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in names):
                references = [
                    {"kind": "table", "value": value} for value in names
                ]
        elif (
            source.get("provider") == "mediawiki"
            and "," not in target
            and not target.isdecimal()
        ):
            references = [{"kind": "page", "value": target}]
    return references


def _source_targets(
    source: Record,
    interface: Interface,
    *,
    links: bool,
) -> Markup:
    """Render exact typed references without splitting ambiguous titles.

    Returns:
        Escaped public identities, linked when their types are known.
    """
    pieces = []
    host = _wiki_host(str(source.get("wiki", "")))
    references = _source_references(source)
    history = (
        source.get("provider") == "mediawiki"
        and source.get("dataset") == "page_history"
    )
    for reference in references:
        value, kind = reference["value"], reference["kind"]
        if kind == "table":
            piece = (
                _documentation(
                    _TABLE_DOCUMENTATION.get(value, _DATABASE_DOCUMENTATION),
                    _TABLE_ALIASES.get(value, value),
                    title=value,
                )
                if links
                else escape(_TABLE_ALIASES.get(value, value))
            )
        elif kind == "revision":
            label = interface.text("reports.source.revision", value=value)
            piece = (
                wiki_link(
                    f"https://{host}/w/index.php?oldid={value}"
                    if host
                    else None,
                    label,
                )
                if links
                else escape(label)
            )
        elif kind == "page_id":
            prefix = (
                "en:"
                if host == "en.wikipedia.org"
                else "d:"
                if host == "www.wikidata.org"
                else ""
            )
            label = prefix + interface.text(
                "reports.source.page_id",
                value=value,
            )
            piece = (
                wiki_link(
                    f"https://{host}/w/index.php?curid={value}"
                    + ("&action=history" if history else "")
                    if host
                    else None,
                    label,
                )
                if links
                else escape(label)
            )
        else:
            path = quote(value.replace(" ", "_"), safe="")
            url = (
                f"https://{host}/wiki/{path}"
                + ("?action=history" if history else "")
                if host
                else None
            )
            prefix = (
                "en:"
                if host == "en.wikipedia.org"
                else "d:"
                if host == "www.wikidata.org"
                else ""
            )
            label = prefix + value
            piece = _message(
                interface,
                "reports.source.quoted_page",
                page=wiki_link(url, label) if links else label,
            )
        pieces.append(piece)
    if not references and source.get("target"):
        # Old targets may contain one title or an ambiguous batch.
        # Preserve commas because they are valid title characters.
        pieces.append(
            _message(
                interface,
                "reports.source.quoted_page",
                page=source["target"],
            )
            if source.get("provider") == "mediawiki"
            else escape(source["target"]),
        )
    count = source.get("reference_count", len(references))
    if count > len(references):
        pieces.append(
            escape(
                interface.text(
                    "reports.source.more",
                    count=count - len(references),
                ),
            ),
        )
    separator = (
        "|"
        if references and all(item["kind"] == "table" for item in references)
        else ", "
        if interface.locale == "en"
        else "、"
    )
    return escape(separator).join(pieces)


def _api_request_url(source: Record) -> str | None:
    """Prefill a nonexecuting request form using retained identities.

    Returns:
        A safe API Sandbox fragment or primary API documentation.
    """
    provider = source.get("provider")
    if provider != "mediawiki":
        return (
            _PROVIDER_DOCUMENTATION.get(provider)
            if provider == "pageviews"
            else None
        )
    host = _wiki_host(str(source.get("wiki", "")))
    references = _source_references(source)
    selectors = {"page": "titles", "page_id": "pageids", "revision": "revids"}
    kinds = {reference["kind"] for reference in references}
    dataset = source.get("dataset")
    if (
        not host
        or len(kinds) != 1
        or not kinds <= selectors.keys()
        or dataset
        not in {"page_text", "revision_text", "page_history", "page_metadata"}
    ):
        return _PROVIDER_DOCUMENTATION["mediawiki"]
    selector = selectors[next(iter(kinds))]
    parameters = {
        "action": "query",
        "prop": "info" if dataset == "page_metadata" else "revisions",
        selector: "|".join(reference["value"] for reference in references),
    }
    if dataset != "page_metadata":
        parameters["rvprop"] = (
            "ids|timestamp|user"
            if dataset == "page_history"
            else "ids|timestamp|content"
        )
        if dataset != "page_history":
            parameters["rvslots"] = "main"
    return f"https://{host}/wiki/Special:ApiSandbox#" + urlencode(parameters)


def _source_verb(
    source: Record,
    interface: Interface,
    *,
    links: bool,
) -> Markup | str:
    """Link the read verb to a safe request form when available.

    Returns:
        Escaped linked text or the plain localized verb for a card.
    """
    verb = interface.text("reports.source.verb")
    url = _api_request_url(source) if links else None
    return _documentation(url, verb) if url else verb


def _saved_membership_description(
    source: Record,
    interface: Interface,
    *,
    links: bool,
) -> Markup | str:
    """Name the saved membership file and its project when known.

    Returns:
        Linked file identity, or a truthful legacy source description.
    """
    metadata = source.get("metadata")
    day = source.get("date")
    if metadata:
        filename = metadata["file"]
        label = filename.rsplit("/", 1)[-1]
        file = (
            _parquet_file(filename, interface, label=label) if links else label
        )
        result = _message(
            interface,
            "reports.source.membership.file_project"
            if source.get("target")
            else "reports.source.membership.file",
            file=file,
            project=source.get("target", ""),
        )
    else:
        identity = _message(
            interface,
            "reports.source.membership.data_dated"
            if day
            else "reports.source.membership.data_undated",
            date=interface.date(day) if day else "",
        )
        result = _message(
            interface,
            "reports.source.membership.legacy",
            source=identity,
        )
    return result if links else unescape(str(result))


def _parquet_file(
    filename: str,
    interface: Interface,
    *,
    label: str | None = None,
) -> Markup:
    """Link a public retained Parquet file to its lazy viewer.

    Returns:
        Escaped file markup, with an ordinary GET fallback for dialogs.
    """
    content = (
        escape(label) if label else Markup("<code>{}</code>").format(filename)
    )
    try:
        preview_path(filename)
    except ValueError:
        return content
    return Markup(
        '<a href="{}" title="{}" data-dataset-link '
        'aria-haspopup="dialog">{}</a>',
    ).format(
        local_url("/data/", file=filename, lang=interface.locale),
        filename,
        content,
    )


def _named_scope(
    names: list[str],
    namespace: str,
    source: Record,
    interface: Interface,
    *,
    links: bool,
) -> Markup:
    """Link explicitly supplied category or template selections.

    Returns:
        Quoted public names without interpreting SQL table identities.
    """
    host = _wiki_host(str(source.get("wiki", "")))
    prefix = (
        "en:"
        if host == "en.wikipedia.org"
        else "d:"
        if host == "www.wikidata.org"
        else ""
    )
    values = []
    for name in names:
        title = (
            name
            if namespace == "Template" and ":" in name
            else f"{namespace}:{name}"
        )
        url = (
            f"https://{host}/wiki/" + quote(title.replace(" ", "_"), safe="")
            if host
            else None
        )
        values.append(
            _message(
                interface,
                "reports.source.quoted_page",
                page=wiki_link(url, prefix + name) if links else prefix + name,
            ),
        )
    return escape(", " if interface.locale == "en" else "、").join(values)


def _purpose_description(
    source: Record,
    interface: Interface,
    *,
    links: bool,
) -> Markup | None:
    """Describe the caller's intent without guessing from table names.

    Returns:
        Natural scoped prose, or none for an older unclassified query.
    """
    purpose = source.get("purpose")
    scope = source.get("scope") or {}
    page_kind = interface.text(
        "reports.source.selection.talk_pages"
        if scope.get("page_kind") == "talk_pages"
        else "reports.source.selection.pages",
    )
    if purpose == "project_membership" and scope.get("project"):
        description = _message(
            interface,
            "reports.source.purpose.project_membership",
            project=scope["project"],
        )
    elif purpose == "category_members" and scope.get("categories"):
        description = _message(
            interface,
            "reports.source.purpose.category_members",
            categories=_named_scope(
                scope["categories"],
                "Category",
                source,
                interface,
                links=links,
            ),
            page_kind=page_kind,
        )
    elif purpose == "page_tags" and (
        scope.get("categories") or scope.get("templates")
    ):
        selections = []
        for key, namespace in (
            ("categories", "Category"),
            ("templates", "Template"),
        ):
            if scope.get(key):
                selections.append(
                    _message(
                        interface,
                        f"reports.source.selection.{key}",
                        names=_named_scope(
                            scope[key],
                            namespace,
                            source,
                            interface,
                            links=links,
                        ),
                    ),
                )
        description = _message(
            interface,
            "reports.source.purpose.page_tags",
            selections=escape(
                " and " if interface.locale == "en" else "及",
            ).join(selections),
            page_kind=page_kind,
        )
    elif purpose == "latest_revisions":
        description = _message(
            interface,
            "reports.source.purpose.latest_revisions_batch"
            if scope.get("batch_count")
            else "reports.source.purpose.latest_revisions_count"
            if "page_count" in scope
            else "reports.source.purpose.latest_revisions",
            page_kind=page_kind,
            count=scope.get("page_count"),
            batch=scope.get("batch_index"),
            batches=scope.get("batch_count"),
        )
    elif purpose == "template_aliases" and scope.get("template"):
        description = _message(
            interface,
            "reports.source.purpose.template_aliases",
            template=_named_scope(
                [scope["template"]],
                "Template",
                source,
                interface,
                links=links,
            ),
        )
    else:
        return None
    if source.get("provider") == "saved":
        return _message(
            interface,
            "reports.source.purpose.saved",
            purpose=description,
        )
    tables = _source_targets(source, interface, links=links)
    details = (
        _message(
            interface,
            "reports.source.purpose.tables",
            wiki=source.get("wiki", ""),
            tables=tables,
        )
        if tables
        else ""
    )
    return _message(
        interface,
        "reports.source.purpose.query",
        purpose=description,
        details=details,
    )


def _traffic_description(
    source: Record,
    interface: Interface,
    *,
    links: bool,
) -> Markup | str | None:
    """Describe dated traffic with an optional tool link.

    Returns:
        Localized traffic prose, or none for other source operations.
    """
    if source.get("provider") == "pageviews" and source.get("dataset") in {
        "site_traffic",
        "top_pages",
    }:
        day = source.get("date")
        if not day and re.fullmatch(
            r"\d{4}/\d{2}/\d{2}",
            source.get("target", ""),
        ):
            day = source["target"].replace("/", "-")
        if day:
            label = interface.text(
                "reports.source.traffic."
                + ("top" if source["dataset"] == "top_pages" else "site"),
                date=interface.date(day),
            )
            host = _wiki_host(source.get("wiki", ""))
            url = (
                "https://pageviews.wmcloud.org/topviews/?"
                + urlencode({"project": host, "date": day, "excludes": ""})
                if host and source["dataset"] == "top_pages"
                else None
            )
            result = _message(
                interface,
                "reports.source.traffic.action",
                wiki=source.get("wiki", ""),
                traffic=_documentation(url, label) if links and url else label,
            )
            return result if links else unescape(str(result))
    return None


def _local_source_description(
    source: Record,
    interface: Interface,
    *,
    kind: str,
    links: bool,
) -> Markup | str | None:
    """Describe local file operations and grouped template parsing.

    Returns:
        Escaped local operation prose, or none for another source.
    """
    if source.get("dataset") == "template_parsing":
        result = _message(
            interface,
            "reports.source.parse_templates",
            templates=(", " if interface.locale == "en" else "、").join(
                "{{" + item["value"] + "}}"
                for item in source.get("references", [])
            ),
        )
        return result
    if (
        source.get("provider") == "saved"
        and source.get("dataset") == "parquet"
    ):
        operation = (
            "written"
            if kind == "source_written"
            else "unchanged"
            if kind == "source_unchanged"
            else "read"
        )
        result = _message(
            interface,
            f"reports.source.parquet.{operation}",
            file=_parquet_file(source["metadata"]["file"], interface)
            if links
            else source["metadata"]["file"],
        )
        return result
    if (
        source.get("provider") == "saved"
        and source.get("dataset") == "project_membership"
    ):
        return _saved_membership_description(source, interface, links=links)
    return _traffic_description(source, interface, links=links)


def source_description(
    source: Record,
    interface: Interface,
    *,
    kind: str = "source_read",
    links: bool = True,
) -> Markup | str:
    """Describe a source operation with public links and file facts.

    Args:
        source: Validated source observation retained by the execution.
        interface: Selected language for the activity description.
        kind: Recorded read, completed load, destination check or save.
        links: Include anchors for dialogs, but not linked card text.

    Returns:
        Natural localized prose with known identities and timestamps.
        A plain string is returned when links are disabled.
    """
    if (
        result := _local_source_description(
            source,
            interface,
            kind=kind,
            links=links,
        )
    ) is not None:
        return result if links else unescape(str(result))
    if (
        source.get("provider") == "mediawiki"
        and source.get("dataset") == "page_text"
        and source.get("purpose") == "new_page_candidates"
        and kind in {"source_read", "source_loaded"}
    ):
        scope = source["scope"]
        result = _message(
            interface,
            "reports.source.new_pages.loaded"
            if kind == "source_loaded"
            else "reports.source.new_pages.read",
            verb=_source_verb(source, interface, links=links),
            new_pages=scope["new_page_count"],
            redirects=scope["redirect_count"],
        )
        return result if links else unescape(str(result))
    if source.get("provider") in {"replica", "saved"} and source.get(
        "purpose",
    ):
        purpose = _purpose_description(source, interface, links=links)
        if purpose is not None:
            return purpose if links else unescape(str(purpose))
    labels = {}
    for field in ("provider", "dataset"):
        value = source.get(field, "")
        key = f"reports.source.{field}.{value}"
        labels[field] = interface.text(key) if key in catalog() else value
    provider = source.get("provider")
    labels["verb"] = _source_verb(source, interface, links=links)
    wiki = source.get("wiki", "")
    scope = (
        _message(interface, "reports.source.wiki", wiki=wiki) if wiki else ""
    )
    targets = _source_targets(source, interface, links=links)
    query = kind == "source_read" and provider == "replica"
    references = _source_references(source)
    table_query = (
        query
        and references
        and all(reference["kind"] == "table" for reference in references)
    )
    if targets:
        targets = _message(
            interface,
            "reports.source.tables"
            if table_query
            else "reports.source.pages"
            if provider == "mediawiki"
            else "reports.source.targets",
            targets=targets,
        )
    day = source.get("date")
    date = (
        _message(interface, "reports.source.date", date=interface.date(day))
        if day
        else ""
    )
    key = (
        "query"
        if query
        else "loaded"
        if kind == "source_loaded"
        else "mediawiki"
        if kind == "source_read" and provider == "mediawiki" and targets
        else "mediawiki_unscoped"
        if kind == "source_read" and provider == "mediawiki"
        else kind
    )
    result = _message(
        interface,
        f"reports.source.action.{key}",
        **labels,
        wiki=wiki if query else scope,
        targets=targets,
        date=date,
    )
    metadata = source.get("metadata")
    if metadata:
        result += " " + _message(
            interface,
            "reports.source.metadata",
            file=_parquet_file(metadata["file"], interface)
            if links
            else metadata["file"],
            observed=utc_time(metadata["observed_at"], seconds=True)
            if metadata["observed_at"]
            else interface.text("ops.unavailable"),
            finished=utc_time(metadata["finished_at"], seconds=True)
            if metadata["finished_at"]
            else interface.text("ops.unavailable"),
        )
        if metadata.get("written_at"):
            result += " " + _message(
                interface,
                "reports.source.written",
                written=utc_time(metadata["written_at"], seconds=True),
            )
    return result if links else unescape(str(result))


def _elapsed_duration(seconds: float, interface: Interface) -> str:
    """Format known elapsed time with optional hours and minutes.

    Returns:
        Hundredths of a second, with zero hours and minutes omitted.
    """
    hours, remaining = divmod(round(seconds * 100), 360_000)
    minutes, hundredths = divmod(remaining, 6000)
    pieces = [
        interface.text("reports.elapsed.hours", count=f"{hours:,}")
        if hours
        else "",
        interface.text("reports.elapsed.minutes", count=minutes)
        if minutes
        else "",
        interface.text(
            "reports.elapsed.seconds",
            count=f"{hundredths / 100:.2f}",
        ),
    ]
    return (" " if interface.locale == "en" else "").join(
        piece for piece in pieces if piece
    )


def _run_elapsed(run: Record) -> float | None:
    """Read elapsed time only from observed execution boundaries.

    Returns:
        Nonnegative actual elapsed seconds, or none without both times.
    """
    started = timestamp(run.get("started_at"))
    finished = timestamp(run.get("finished_at"))
    return (
        (finished - started).total_seconds()
        if started is not None and finished is not None and finished >= started
        else None
    )


def _stage_body(  # ruff: ignore[too-many-return-statements]
    stage: Record,
    interface: Interface,
    *,
    elapsed_seconds: float | None = None,
    outcome: str = "succeeded",
    include_date: bool = True,
) -> Markup | str:
    """Integrate destination and source links into a complete sentence.

    Returns:
        Safe stage prose with no detached destination separator.
    """
    if progress := stage.get("progress"):
        label = _progress(progress, interface)
        if label and not label.endswith((
            ".",
            "。",
            "!",
            "\uff01",
            "?",
            "\uff1f",
        )):
            label = Markup("{}{}").format(
                label,
                "." if interface.locale == "en" else "。",
            )
        if include_date and (day := progress.get("report_date")):
            return Markup("{} {}").format(
                interface.text(
                    "progress.pageviews_date_sentence",
                    date=interface.date(day),
                ),
                label,
            )
        return label
    if stage["kind"] == "finished":
        key = (
            "reports.stage.failed"
            if outcome in {"failed", "interrupted"}
            else "reports.stage.deferred"
            if outcome == "deferred"
            else "reports.stage.finished"
        )
        return (
            interface.text(
                key + "_duration",
                elapsed=_elapsed_duration(elapsed_seconds, interface),
            )
            if elapsed_seconds is not None
            else interface.text(key)
        )
    if stage.get("source") and stage["kind"] in {
        "source_read",
        "source_loaded",
        "source_written",
        "source_unchanged",
        "checking",
        "saving",
    }:
        return source_description(
            stage["source"],
            interface,
            kind=stage["kind"],
        )
    target = stage.get("title")
    if target and stage["kind"] == "started":
        return interface.text("reports.started_task", task=target)
    key = f"reports.target.{stage['kind']}"
    if target and key in catalog():
        return _message(
            interface,
            key,
            target=wiki_link(title_url(target), target),
        )
    key = f"reports.stage.{stage['kind']}"
    return interface.text(key) if key in catalog() else stage["kind"]


def _stage_description(
    stage: Record,
    interface: Interface,
    *,
    elapsed_seconds: float | None = None,
    outcome: str = "succeeded",
    include_date: bool = True,
) -> Markup | str:
    """Render a complete, punctuated sentence for a history stage.

    Returns:
        Safe localized prose with the recorded stage details.
    """
    result = _stage_body(
        stage,
        interface,
        elapsed_seconds=elapsed_seconds,
        outcome=outcome,
        include_date=include_date,
    )
    if result.endswith((".", "。", "!", "\uff01", "?", "\uff1f")):
        return result
    return Markup("{}{}").format(
        result,
        "." if interface.locale == "en" else "。",
    )


def _group_template_stages(stages: list[Record]) -> list[Record]:
    """Condense consecutive alias lookups into one parsing description.

    Returns:
        Display-only stage copies, preserving original stored evidence.
    """
    result = []
    names: list[str] = []
    first = None

    def flush() -> None:
        """Emit one compact template group before the next operation."""
        if first is None:
            return
        result.append({
            **first,
            "kind": "source_loaded",
            "source": {
                "provider": "saved",
                "dataset": "template_parsing",
                "wiki": "",
                "date": "",
                "target": "",
                "references": [
                    {"kind": "page", "value": name} for name in names
                ],
            },
        })

    for stage in stages:
        source = stage.get("source") or {}
        if source.get("purpose") == "template_aliases":
            if first is None:
                first = stage
            name = (source.get("scope") or {}).get("template") or source.get(
                "target",
                "",
            )
            name = re.sub(
                r"^(?:[a-z-]+:)?(?:Template|模板):",
                "",
                name,
                flags=re.IGNORECASE,
            )
            if name not in names:
                names.append(name)
        else:
            flush()
            first = None
            names = []
            result.append(stage)
    flush()
    return result


def _history_stages(
    run: Record,
    interface: Interface,
    elapsed: float | None,
) -> list[Record]:
    """Show each changed report date once in an execution's history.

    Returns:
        Localized stage rows in their original order.
    """
    rows = []
    previous_date = None
    stages = _group_template_stages(run.get("stages", []))
    resolved = {
        (source["wiki"], source["dataset"], source["target"]): source
        for stage in stages
        if stage["kind"] == "source_loaded"
        and (source := stage.get("source"))
        and source.get("references")
        and all(ref["kind"] == "page" for ref in source["references"])
    }
    for original_stage in stages:
        stage = original_stage
        if (
            stage["kind"] == "source_read"
            and (source := stage.get("source"))
            and source.get("provider") == "mediawiki"
            and all(
                ref["kind"] == "page_id"
                for ref in source.get("references", [])
            )
            and (
                titles := resolved.get(
                    (source["wiki"], source["dataset"], source["target"]),
                )
            )
        ):
            stage = {**stage, "source": titles}
        if stage["kind"] == "started":
            stage = {
                **stage,
                "title": interface.text(
                    f"routine.{run['task_id']}",
                )
                if f"routine.{run['task_id']}" in catalog()
                else run["task_id"],
            }
        day = (stage.get("progress") or {}).get("report_date")
        include_date = bool(day and day != previous_date)
        if day:
            previous_date = day
        rows.append({
            "time": {
                "value": stage["time"],
                "label": utc_time(stage["time"], milliseconds=True),
                "known": True,
            },
            "label": _stage_description(
                stage,
                interface,
                elapsed_seconds=elapsed,
                outcome=run["outcome"],
                include_date=include_date,
            ),
            "link": None,
        })
    return rows


def _progress(progress: Record | None, interface: Interface) -> str | None:
    """Describe an actual recorded phase and only its known counters.

    Returns:
        Localized progress, or none when no phase was observed.
    """
    if not progress:
        return None
    key = f"progress.{progress['phase']}"
    phase = (
        source_description(
            progress["source"],
            interface,
            kind=progress["phase"],
        )
        if progress.get("source")
        and progress["phase"]
        in {
            "source_read",
            "source_loaded",
            "source_written",
            "source_unchanged",
            "checking",
            "saving",
        }
        else interface.text(key)
        if key in catalog()
        else interface.text(f"reports.running.{progress['phase']}", scope="")
        if f"reports.running.{progress['phase']}" in catalog()
        else progress["phase"]
    )
    current, total = progress.get("current"), progress.get("total")
    return (
        _message(
            interface,
            "progress.count",
            phase=phase.rstrip(".。…"),
            current=f"{current:,}",
            total=f"{total:,}",
        )
        if current is not None and total is not None
        else phase
    )


def history_context(  # ruff: ignore[too-many-arguments]
    interface: Interface,
    data: Record,
    now: dt.datetime,
    *,
    recent_only: bool = True,
    offset: int = 0,
    limit: int | None = None,
) -> Record:
    """Localize executions observed within the last 168 hours.

    Args:
        interface: Negotiated interface language and authored messages.
        data: Validated status containing independent execution records.
        now: UTC rendering clock for a currently observed running task.
        recent_only: Restrict the table to the seven-day window. Report
            cards may request an older run's details independently.
        offset: First requested row, clamped to an existing page.
        limit: Maximum rows to localize, or none for all eligible rows.
            Zero counts eligible rows without preparing stage details.

    Returns:
        Execution rows, matching dialogs and complete window counts.
    """
    rows = []
    owners = {
        row["boot_id"]: row
        for row in data.get("services", {}).values()
        if row["freshness"] == "current"
        and row["lifecycle"] in {"starting", "running", "paused", "draining"}
    }
    cutoff = now - dt.timedelta(hours=_HISTORY_HOURS)
    runs = sorted(
        (
            run
            for run in data.get("executions", {}).values()
            if not recent_only
            or run["outcome"] == "running"
            or timestamp(
                run.get("finished_at")
                or run.get("detected_at")
                or run.get("updated_at")
                or run["started_at"],
            )
            >= cutoff
        ),
        key=operator.itemgetter("started_at"),
        reverse=True,
    )
    total = len(runs)
    start = max(0, offset)
    if limit:
        start = min(start, max(0, ((total - 1) // limit) * limit))
    selected = runs[start:] if limit is None else runs[start : start + limit]
    for run in selected:
        task_key = f"routine.{run['task_id']}"
        task = (
            interface.text(task_key)
            if task_key in catalog()
            else run["task_id"]
        )
        result = interface.text(f"observability.execution.{run['outcome']}")
        started = run["started_at"]
        elapsed = _run_elapsed(run)
        owner = (
            owners.get(run["boot_id"]) if run["outcome"] == "running" else None
        )
        running = owner is not None
        seconds = (
            max(0, (now - timestamp(started)).total_seconds())
            if running
            else run.get("duration_seconds")
        )
        rows.append({
            "task": task,
            "result": result,
            "date": interface.date(started[:10]),
            "time": {
                "value": started,
                "label": utc_time(started, seconds=True),
                "known": True,
            },
            "duration": duration(seconds, interface, running=running),
            "timer_start": started if running else None,
            "timer_expires": (
                timestamp(owner["heartbeat_at"]) + dt.timedelta(seconds=90)
            ).isoformat()
            if running
            else None,
            "popup_id": f"job-history-{run['run_id']}",
            "truncated": run.get("stages_truncated", False),
            "run_id": run["run_id"],
            "failure": failure_description(
                run.get("failure") or {},
                interface,
            ),
            "attempted_date": interface.date(
                run["failure"]["failure_date"],
            )
            if run["outcome"] == "deferred"
            and (run.get("failure") or {}).get("failure_date")
            else None,
            "report_date": interface.date(run["report_date"])
            if run.get("report_date")
            else None,
            "progress": _progress(run.get("progress"), interface)
            if run["outcome"] == "running"
            else None,
            "stages": _history_stages(run, interface, elapsed),
        })
    return {
        "events": rows,
        "history_dialogs": rows,
        "history_total": total,
        "history_offset": start,
    }
