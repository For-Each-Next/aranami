"""Render Jinja pages from validated, saved backend observations.

The public application validates facts before calling these renderers.
Templates own HTML structure and escaping; context preparation localizes
observations without reading runtime files or starting source clients.
"""

from __future__ import annotations

import datetime as dt
import json
import operator
import secrets
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from webservice.presentation import (
    Interface,
    article_extract,
    assessment_code,
    catalog,
    duration,
    event_duration,
    failure_description,
    homepage_sections,
    local_url,
    maintenance_items,
    maintenance_page,
    maintenance_sections,
    maintenance_selection,
    next_run,
    pageviews_url,
    readable_size,
    relative_time,
    schedule_state,
    timestamp,
    title_url,
    uptime,
    utc_time,
    wiki_link,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from webservice.presentation import Record

_STATES = {
    "queued": "Queued",
    "running": "Running",
    "updated": "Updated",
    "no_update": "NoUpdate",
    "failed": "Failed",
    "deferred": "Deferred",
    "unknown": "Result unavailable",
    "unconfirmed": "Result unavailable",
}
_STATE_ICONS = {
    "running": "⏳",
    "updated": "✅",
    "no_update": "🆗",
    "failed": "❌",
    "deferred": "⏸️",
    "unknown": "❔",
    "unconfirmed": "❔",
}
_LOG_EVENT_LIMIT = 100
_LOG_EVENT_KINDS = frozenset({
    "service_started",
    "service_stopping",
    "service_stopped",
    "routine_started",
    "routine_finished",
    "website_started",
    "website_finished",
    "run_started",
    "run_finished",
})


@lru_cache(maxsize=1)
def _templates() -> Environment:
    """Load cached presentation templates with HTML autoescaping.

    Returns:
        A Jinja environment rooted in the source template directory.
    """
    return Environment(
        loader=FileSystemLoader(Path(__file__).with_name("templates")),
        autoescape=select_autoescape(["html"]),
        auto_reload=False,
    )


def _render(name: str, context: Record) -> str:
    """Render plain context values through an autoescaping template.

    Returns:
        A complete document or live fragments selected by the caller.
    """
    return _templates().get_template(name).render(context)


@lru_cache(maxsize=4)
def _messages(locale: str) -> Record:
    """Expose authored message namespaces to Jinja templates.

    Returns:
        Nested message keys containing localized plain text.
    """
    interface = Interface(locale)
    result = {}
    for key in catalog():
        namespace = result
        *parents, leaf = key.split(".")
        for parent in parents:
            if isinstance(namespace.get(parent), str):
                namespace[parent] = {"heading": namespace[parent]}
            namespace = namespace.setdefault(parent, {})
        if isinstance(namespace.get(leaf), dict):
            namespace[leaf]["heading"] = interface.text(key)
        else:
            namespace[leaf] = interface.text(key)
    return result


def _completed_daily_report(routine: str, event: Record | None) -> bool:
    """Identify successful daily work with a confirmed report date.

    Returns:
        Whether the saved outcome supports a dated completion label.
    """
    return (
        routine in {"new_pages", "pageviews"}
        and event is not None
        and event.get("status") in {"updated", "no_update"}
        and bool(event.get("report_date"))
    )


def _time(value: object, *, seconds: bool = False) -> Record:
    """Prepare an actual UTC observation or its unknown marker.

    Returns:
        The original timestamp, its label, and whether it is known.
    """
    label = utc_time(value, seconds=seconds)
    return {"value": value, "label": label, "known": label != "—"}


class _Page:
    """Prepare localized template values from unchanged source facts."""

    def __init__(self, locale: str) -> None:
        """Use one negotiated locale throughout a complete response."""
        self.interface = Interface(locale)
        self.text = self.interface.text

    def context(self, heading: str, *, live: bool = False) -> Record:
        """Prepare metadata and shared navigation for a document.

        Returns:
            Plain context values and an explicitly validated wiki link.
        """
        return {
            "heading": heading,
            "language_tag": self.interface.language_tag,
            "t": _messages(self.interface.locale),
            "live": live,
            "project_link": wiki_link(
                "https://zh.wikipedia.org/wiki/WikiProject:电子游戏",
                self.text("nav.project"),
            ),
        }

    def relative_note(
        self,
        value: object,
        now: dt.datetime,
        *,
        inline: bool = False,
    ) -> Record:
        """Prepare a relative annotation only for a known timestamp.

        Returns:
            A localized label and the original observed timestamp.
        """
        label = relative_time(value, self.interface, now)
        return {
            "label": label[1:-1] if inline and label else label,
            "value": value,
            "inline": inline,
        }

    def clock(self, schedule: Record, now: dt.datetime) -> Record:
        """Share server clock anchors between status views.

        Returns:
            Plain values for browser clocks and observed state expiry.
        """
        return {
            "server_time": now.isoformat(),
            "expires": schedule.get("expires_at"),
            "stale": self.text("ops.schedule_stale"),
            "messages": json.dumps({
                key: self.text(
                    key,
                    count="{count}",
                    seconds="{seconds}",
                    minutes="{minutes}",
                )
                for key in (
                    "duration.seconds",
                    "duration.minutes",
                    "time.minutes_ago",
                    "time.hours_ago",
                    "time.days_ago",
                    "time.minutes_ahead",
                    "time.hours_ahead",
                    "time.days_ahead",
                    "time.soon",
                    "time.just_now",
                )
            }),
        }

    @staticmethod
    def _active_progress(
        event: Record | None,
        schedule: Record,
    ) -> Record | None:
        """Select progress from the current active monitor session.

        Returns:
            Saved progress or a preparing phase while fresh and running;
            none for inactive, stale, or superseded observations.
        """
        if (
            not event
            or event.get("status") != "running"
            or schedule["state"] != "running"
        ):
            return None
        started = timestamp(event.get("started_at"))
        session = timestamp(schedule.get("started_at"))
        if started is None or session is None or started < session:
            return None
        return event.get("progress") or {"phase": "preparing"}

    def _progress_note(self, progress: Record | None) -> str | None:
        """Describe the saved phase separately from the update subject.

        Returns:
            A phase and optional counters, or none when inactive.
        """
        if progress is None:
            return None
        phase = progress.get("phase", "preparing")
        if phase not in {
            "preparing",
            "publishing",
            "pageviews_readiness",
            "pageviews_download",
            "pageviews_aggregate",
        }:
            phase = "preparing"
        label = self.text(f"progress.{phase}")
        current, total = progress.get("current"), progress.get("total")
        return (
            self.text(
                "progress.count",
                phase=label,
                current=f"{current:,}",
                total=f"{total:,}",
            )
            if current is not None and total is not None
            else label
        )

    def _outcome_label(self, event: Record | None) -> str:
        """Describe an outcome from its saved failure observations.

        Returns:
            Localized text, with a generic label for older records.
        """
        if event is None:
            return self.text("ops.no_record")
        if event["status"] != "failed":
            return self.text(f"state.{_STATES[event['status']]}")
        failure_date = event.get("failure_date")
        reason = failure_description(event, self.interface)
        label = (
            self.text(
                "state.FailedRecordDate",
                date=self.interface.date(failure_date),
            )
            if failure_date
            else self.text("state.Failed")
        )
        return (
            self.text("state.FailedReason", failure=label, reason=reason)
            if reason
            else label
        )

    def article(self, item: Record) -> Record:
        """Prepare a title, assessment chips, and saved introduction.

        Returns:
            Localized plain observations and a safe upstream title link.
        """
        assessments = []
        for dimension in ("class", "importance"):
            value = item.get(f"assessment_{dimension}")
            if not isinstance(value, str) or not value.strip():
                continue
            code = assessment_code(value, dimension)
            assessments.append({
                "dimension": dimension,
                "label": self.text(f"assessment.{dimension}.{code}")
                if code
                else value,
            })
        language, extract = article_extract(item, self.interface.locale)
        return {
            "link": wiki_link(item["url"], item["title"], item["title"]),
            "assessments": assessments,
            "extract": extract,
            "extract_language": Interface(language).language_tag,
        }

    def article_card(
        self,
        item: Record,
        pageviews: Record | None,
    ) -> Record:
        """Prepare date, appearance, and popularity observations.

        Returns:
            An article card preserving each observation's meaning.
        """
        card = self.article(item)
        if pageviews:
            card.update(
                rank=item["rank"],
                pageviews_url=pageviews_url(item, pageviews["date"]),
                views=self.text("pageviews.views", count=f"{item['views']:,}"),
                site_rank=self.text(
                    "pageviews.site_rank",
                    rank=f"{item['site_rank']:,}",
                )
                if item.get("site_rank") is not None
                else None,
            )
            return card
        date = item.get("date")
        kind = {
            "fa": "appearance.featured",
            "fl": "appearance.featured",
            "featured": "appearance.featured",
            "ga": "appearance.good",
            "news": "homepage.news",
            "history": "homepage.history",
        }.get(item.get("kind"))
        note = {
            "window_start": "date.window_start",
            "observed": "date.observed",
        }.get(item.get("date_kind"))
        card.update(
            date={"value": date, "label": self.interface.date(date)}
            if date is not None
            else None,
            scheduled=(
                f"({self.text('date.scheduled')}) "
                if self.interface.locale == "en"
                else f"\uff08{self.text('date.scheduled')}\uff09"
            )
            if date is not None and item.get("date_kind") == "scheduled"
            else "",
            kind=self.text(kind) if kind else None,
            separator=date is not None
            and kind is not None
            and (
                self.interface.locale == "en"
                or item.get("kind") in {"news", "history"}
            ),
            date_note=self.text(note) if note else None,
        )
        return card

    def source_updated(
        self,
        value: object,
        *,
        attribution: bool = False,
    ) -> Record | None:
        """Prepare attribution alongside the section's observed time.

        Returns:
            Plain source metadata, or none without a saved observation.
        """
        if value is None:
            return None
        before, after = (
            self.text("extracts.attribution").split("{license}")
            if attribution
            else ("", "")
        )
        return {
            "time": _time(value),
            "attribution": attribution,
            "before": before,
            "after": after,
        }

    def highlights(self, snapshot: Record) -> list[Record]:
        """Prepare visible article sections, omitting confirmed empties.

        Returns:
            Ordered sections retaining unavailable and empty states.
        """
        sections = []
        for section in homepage_sections(snapshot):
            key = section["key"]
            views = snapshot.get("pageviews") if key == "pageviews" else None
            description = None
            if key == "pageviews":
                before, remainder = self.text("pageviews.description").split(
                    "{date}",
                )
                middle, after = remainder.split("{top}")
                description = {
                    "before": before,
                    "middle": middle,
                    "after": after,
                    "top": self.text("pageviews.top"),
                    "date": self.interface.date(views["date"])
                    if views
                    else None,
                    "day": views["date"] if views else None,
                    "separator": " · "
                    if self.interface.locale == "en"
                    else "",
                    "url": local_url(
                        "https://pageviews.wmcloud.org/topviews",
                        project="zh.wikipedia.org",
                        date=views["date"],
                    )
                    if views
                    else None,
                }
            updated = (
                snapshot.get("reports", {}).get(key, {}).get("last_success_at")
            )
            sections.append({
                "key": key,
                "title": self.text(
                    "pageviews.heading"
                    if key == "pageviews"
                    else f"homepage.{key}",
                ),
                "description": self.text(f"homepage.{key}_description")
                if key != "pageviews"
                else None,
                "pageviews_description": description,
                "empty": self.text("pageviews.unavailable")
                if key == "pageviews" and views is None
                else self.text("empty.section")
                if section["items"] is None
                else self.text("empty.period")
                if not section["items"]
                else None,
                "cards": [
                    self.article_card(item, views)
                    for item in section["items"] or []
                ],
                "updated": self.source_updated(updated, attribution=True),
            })
        return sections

    def backlog(self, backlog: Record | None) -> Record | None:
        """Prepare nonempty maintenance tasks in their configured order.

        Returns:
            Ordered sections, unavailable state, or none when empty.
        """
        if backlog is not None and backlog["total"] == 0:
            return None
        if backlog is None:
            return {"unavailable": True}
        groups = [group for group in backlog["groups"] if group["count"] > 0]
        return {
            "sections": [
                {
                    "key": key,
                    "label": self.interface.label(
                        tasks[0].get("section_label"),
                        self.text("backlog.heading"),
                    ),
                    "tasks": [
                        {
                            "label": self.interface.label(
                                group.get("label"),
                                group["category"],
                            ),
                            "count": self.text(
                                "backlog.count",
                                count=f"{group['count']:,}",
                            ),
                            "url": local_url(
                                "/maintenance/",
                                task=group["key"],
                                page=1,
                            ),
                        }
                        for group in tasks
                    ],
                }
                for key, tasks in maintenance_sections(groups).items()
            ],
            "updated": self.source_updated(backlog["observed_at"]),
        }

    def _routine_links(self, name: str, definition: Record) -> Record:
        """Prepare configured report links with accessible names.

        Returns:
            Validated target links and locale-specific separators.
        """
        targets = list(definition.get("pages", {}).values()) or definition.get(
            "prefixes",
            [],
        )
        multiple = len(targets) > 1
        return {
            "name": name,
            "multiple": multiple,
            "opening": " (" if self.interface.locale == "en" else "\uff08",
            "closing": ")" if self.interface.locale == "en" else "\uff09",
            "targets": [
                wiki_link(
                    title_url(title),
                    str(index) if multiple else name,
                    f"{index} · {name} · {title}"
                    if multiple
                    else f"{name} · {title}",
                )
                for index, title in enumerate(targets, 1)
            ],
        }

    def _report(self, record: Record, now: dt.datetime) -> Record:
        """Prepare destination outcomes and independent execution times.

        Returns:
            Localized values and an active timer anchor when observed.
        """
        return {
            "key": f"{record['site']}:{record['title']}",
            "link": wiki_link(
                title_url(record["title"], record["site"]),
                record["title"],
            ),
            "outcome": self._outcome_label(record),
            "started": _time(
                None
                if record["status"] == "queued"
                else record.get("started_at"),
                seconds=True,
            ),
            "finished": _time(record.get("finished_at"), seconds=True),
            "duration": event_duration(record, self.interface, now),
            "timer_start": record.get("started_at")
            if record["status"] == "running"
            and timestamp(record.get("started_at")) is not None
            else None,
        }

    def routine_cards(
        self,
        status: Record,
        definitions: Record,
        schedule: Record,
        now: dt.datetime,
    ) -> list[Record]:
        """Prepare actual outcomes and independent configured schedules.

        Returns:
            Routine cards with current-session progress and report rows.
        """
        events = (status.get("routines") or {}).get("routines", {})
        records = (status.get("reports") or {}).get("reports", {}).values()
        cards = []
        for identifier, definition in definitions.items():
            name = self.text(f"routine.{identifier}")
            event = events.get(identifier)
            running = (event or {}).get("status") == "running"
            progress = self._active_progress(event, schedule)
            fallback = self._outcome_label(event)
            label = fallback
            completed_daily = _completed_daily_report(identifier, event)
            if completed_daily:
                label = self.text(
                    "state.UpdatedReportDate",
                    date=self.interface.date(event["report_date"]),
                )
            if progress is not None:
                label = (
                    self.text(
                        "progress.pageviews_date"
                        if identifier == "pageviews"
                        else "progress.report_date",
                        date=self.interface.date(progress["report_date"]),
                    )
                    if progress.get("report_date")
                    else self.text(f"progress.routine.{identifier}")
                )
            observed = (
                event["started_at" if running else "finished_at"]
                if event
                else None
            )
            upcoming = (
                schedule["routines"].get(identifier, {}).get("next_run_at")
            )
            pages = sorted(
                (
                    record
                    for record in records
                    if record["routine"] == identifier
                    and record.get("dry") is False
                ),
                key=operator.itemgetter("title"),
            )
            cards.append({
                "identifier": identifier,
                "links": self._routine_links(name, definition),
                "running": running,
                "icon": "✅"
                if completed_daily
                else _STATE_ICONS.get((event or {}).get("status"), "❔"),
                "state": label,
                "progress": self._progress_note(progress),
                "fallback": fallback,
                "observed": _time(observed),
                "relative": self.relative_note(observed, now),
                "next_run": next_run(schedule, identifier, self.interface),
                "upcoming": self.relative_note(upcoming, now),
                "schedule_expires": schedule.get("expires_at") is not None,
                "popup_id": f"report-details-{identifier}",
                "popup_heading": self.text("ops.details", routine=name),
                "reports": [self._report(record, now) for record in pages],
            })
        return cards

    def live_status(
        self,
        status: Record,
        config: Record,
        now: dt.datetime,
    ) -> Record:
        """Prepare shared status, footer, server, and resource data.

        Returns:
            Context shared by complete pages and the live fragment view.
        """
        definitions = (config.get("routines") or {}).get("routines", {})
        schedule = schedule_state(status.get("schedule"), definitions, now)
        version = config.get("version") or self.text("ops.unavailable")
        started = schedule.get("started_at")
        snapshot = status.get("schedule") or {}
        observed_start = snapshot.get("started_at")
        server_started = (
            _time(observed_start, seconds=True)
            if timestamp(observed_start) is not None
            else None
        )
        return {
            "clock": self.clock(schedule, now),
            "routines": self.routine_cards(status, definitions, schedule, now),
            "footer": {
                "label": uptime(schedule, version, self.interface, now),
                "started": started,
                "template": self.text(
                    "footer.uptime",
                    version=version,
                    days="{days}",
                    hours="{hours}",
                    minutes="{minutes}",
                    seconds="{seconds}",
                    since=utc_time(started).removesuffix(" UTC"),
                )
                if started
                else None,
                "unknown": self.text("footer.uptime_unknown", version=version),
            },
            "server": {
                "state": self.text(f"server.{schedule['state']}"),
                "observed": _time(schedule.get("updated_at"), seconds=True),
                "relative": self.relative_note(
                    schedule.get("updated_at"),
                    now,
                ),
                "expires": schedule.get("expires_at") is not None,
                "started": server_started,
                "started_relative": self.relative_note(
                    observed_start,
                    now,
                    inline=True,
                ),
                "opening": " (" if self.interface.locale == "en" else "\uff08",
                "separator": ", "
                if self.interface.locale == "en"
                else "\uff0c",
                "closing": ")" if self.interface.locale == "en" else "\uff09",
            },
            "runtime": self.runtime(status, schedule),
        }

    def runtime(self, status: Record, schedule: Record) -> list[Record]:
        """Prepare saved environment and fresh resource observations.

        Returns:
            Metric rows with explicit unknowns for missing observations.
        """
        runtime = (status.get("schedule") or {}).get("runtime") or {}
        unknown = self.text("ops.unavailable")
        fresh = schedule["state"] in {"running", "paused"}
        memory = runtime.get("memory_bytes") if fresh else None
        limit = runtime.get("memory_limit_bytes") if fresh else None
        cpu = runtime.get("cpu_percent") if fresh else None
        cpu_limit = runtime.get("cpu_limit") if fresh else None
        used_memory = readable_size(memory) if memory is not None else unknown
        allocated_memory = (
            readable_size(limit) if limit is not None else unknown
        )
        cpu_use = f"{cpu:.1f}%" if cpu is not None else unknown
        cpu_cores = (
            self.text(
                "server.cpu_core" if cpu_limit == 1 else "server.cpu_cores",
                count=f"{cpu_limit:g}",
            )
            if cpu_limit is not None
            else self.text("server.cpu_cores_unknown")
        )
        separator = " " if self.interface.locale == "en" else ""
        rows = (
            (
                "environment",
                " · ".join(
                    value
                    for key in ("system", "machine")
                    if (value := runtime.get(key))
                )
                or unknown,
                False,
            ),
            ("python", runtime.get("python") or unknown, False),
            ("memory", f"{used_memory} / {allocated_memory}", True),
            ("cpu", f"{cpu_use}{separator}{cpu_cores}", True),
        )
        return [
            {
                "label": self.text(f"server.{key}"),
                "value": value,
                "fresh": usage and fresh,
                "stale": unknown if usage else None,
            }
            for key, value, usage in rows
        ]

    def worklist_card(self, item: Record, groups: Record) -> Record:
        """Prepare exact issue sources and the earliest observed month.

        Returns:
            An article summary with localized issue links and dates.
        """
        card = self.article(item)
        month = item.get("oldest_issue_month")
        card.update(
            issues=[
                wiki_link(
                    groups[key]["source_url"],
                    self.interface.label(
                        groups[key].get("label"),
                        groups[key]["category"],
                    ),
                )
                for key in item.get("issues", [])
                if key in groups
            ],
            month={
                "value": month,
                "label": self.interface.date(month, month_only=True),
                "url": title_url("Category:" + item["oldest_issue_category"])
                if item.get("oldest_issue_category")
                else None,
            }
            if month
            else None,
        )
        return card

    def log_events(self, data: Record, config: Record) -> list[Record]:
        """Localize saved events and filter their visible content.

        Args:
            data: Validated events and the literal-text search query.
            config: Serialized routine definitions for report links.

        Returns:
            Localized summaries, UTC timestamps, and safe report links.
            Unknown event kinds are omitted instead of exposing codes.
        """
        definitions = (config.get("routines") or {}).get("routines", {})
        query = data.get("query", "").casefold()
        events = []
        for event in data.get("events", [])[:_LOG_EVENT_LIMIT]:
            content = self._log_event(event, definitions)
            if content is None:
                continue
            if query and query not in content["searchable"].casefold():
                continue
            events.append(content)
        return events

    def _log_event(self, event: Record, definitions: Record) -> Record | None:
        """Prepare one authored event summary without diagnostic fields.

        Returns:
            Localized event data, or none for an unsupported event kind.
        """
        kind = event.get("kind")
        if kind not in _LOG_EVENT_KINDS:
            return None
        preview = "preview_" if event.get("dry") is True else ""
        routine_key = f"routine.{event.get('routine')}"
        service_key = f"logs.service.{event.get('service')}"
        summary = self.text(
            f"logs.event.{preview}{kind}",
            routine=self.text(routine_key)
            if routine_key in catalog()
            else self.text("logs.routine_unknown"),
            service=self.text(service_key)
            if service_key in catalog()
            else self.text("logs.service.unknown"),
            result=self._log_result(
                event.get("status"),
                preview=event.get("dry") is True
                and kind == "routine_finished",
            ),
            tasks=self._log_task_count(event.get("tasks")),
            failed_tasks=self._log_task_count(event.get("failures")),
            deferred_tasks=self._log_task_count(event.get("deferred")),
        )
        definition = definitions.get(event.get("routine"), {})
        titles = list(definition.get("pages", {}).values()) or list(
            definition.get("prefixes", []),
        )
        separator = " " if self.interface.locale == "en" else ""
        summary = separator.join((summary, *self._log_details(event)))
        reports = self._log_reports(titles)
        time = _time(event.get("time"), seconds=True)
        return {
            "time": time,
            "time_intro": self.text("logs.time_intro"),
            "time_separator": self.text("logs.time_separator"),
            "summary": summary,
            "sentence_separator": separator,
            "reports": reports,
            "searchable": (
                self.text("logs.time_intro")
                + time["label"]
                + self.text("logs.time_separator")
                + summary
                + (separator + reports["text"] if reports else "")
            ),
        }

    def _log_task_count(self, count: object) -> str:
        """Use the correct noun form for an observed run count.

        Returns:
            A localized count phrase, or empty text for absent counts.
        """
        return (
            self.text("logs.task" if count == 1 else "logs.tasks", count=count)
            if count is not None
            else ""
        )

    def _log_reports(self, titles: list[str]) -> Record | None:
        """Introduce related reports without claiming they were edited.

        Returns:
            Safe links and prose separators, or none without targets.
        """
        if not titles:
            return None
        english = self.interface.locale == "en"
        separator = ", " if english else "、"
        last_separator = " and " if english else "、"
        joined = (
            separator.join(titles[:-1]) + last_separator + titles[-1]
            if len(titles) > 1
            else titles[0]
        )
        before, after = self.text("logs.related_reports").split("{reports}")
        return {
            "before": before,
            "after": after,
            "separator": separator,
            "last_separator": last_separator,
            "links": [wiki_link(title_url(title), title) for title in titles],
            "text": self.text("logs.related_reports", reports=joined),
        }

    def _log_details(self, event: Record) -> list[str]:
        """Keep measured durations and confirmed counts independent.

        Returns:
            Localized details without inventing edits for previews.
        """
        details = []
        if (
            event["kind"] == "routine_finished"
            and not event["dry"]
            and event.get("updated") is not None
        ):
            details.append(
                self.text(
                    "logs.edit" if event["updated"] == 1 else "logs.edits",
                    count=f"{event['updated']:,}",
                ),
            )
        if (
            event["kind"] == "website_finished"
            and event.get("failures") is not None
        ):
            details.append(
                self.text(
                    "logs.failure"
                    if event["failures"] == 1
                    else "logs.failures",
                    count=event["failures"],
                ),
            )
        if event.get("duration_seconds") is not None:
            details.append(
                self.text(
                    "logs.duration",
                    duration=duration(
                        event["duration_seconds"],
                        self.interface,
                    ),
                ),
            )
        return details

    def _log_result(self, status: object, *, preview: bool = False) -> str:
        """Localize outcomes without exposing unexpected identifiers.

        Args:
            status: Validated saved task or website outcome.
            preview: Whether a routine only prepared proposed changes.

        Returns:
            Localized results. Successful previews report completion
            without claiming that no changes were needed.
        """
        if preview and status in {"updated", "no_update"}:
            return self.text("logs.result.updated")
        key = (
            status
            if status
            in {
                "updated",
                "no_update",
                "failed",
                "deferred",
                "unconfirmed",
                "partial",
            }
            else "unknown"
        )
        return self.text(f"logs.result.{key}")


def render_dashboard(
    snapshot: Record | None,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
) -> str:
    """Render the dashboard from independent saved observations.

    Args:
        snapshot: Validated website snapshot, or unavailable data.
        status: Validated routine, schedule, and page observations.
        config: Current source version and routine definitions.
        locale: Negotiated interface language.
        now: Optional aware clock for deterministic elapsed time.

    Returns:
        Complete escaped HTML document, including unavailable states.
    """
    page = _Page(locale)
    context = page.context(page.text("page.heading"), live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    context.update(
        notice=page.text("notice.preparing")
        if snapshot is None
        else page.text("notice.partial")
        if snapshot.get("errors")
        else None,
        sections=page.highlights(snapshot or {}),
        backlog=page.backlog((snapshot or {}).get("backlog")),
    )
    return _render("dashboard.html", context)


def render_live_status(
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
) -> str:
    """Render status fragments without loading website datasets.

    Args:
        status: Validated routine, schedule, and page observations.
        config: Current source version and routine definitions.
        locale: The language of the existing page.
        now: Optional aware server clock for deterministic elapsed time.

    Returns:
        Escaped fragments with the same identities as the full page.
    """
    page = _Page(locale)
    context = {"t": _messages(locale)}
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    return _render("live.html", context)


def render_maintenance(
    snapshot: Record | None,
    params: Mapping[str, list[str]],
    locale: str,
) -> str:
    """Render a maintenance list after sorting all saved observations.

    Args:
        snapshot: Validated website snapshot containing the catalogue.
        params: Parsed controls with repeated values retained.
        locale: Negotiated interface language.

    Returns:
        Complete escaped HTML with stable sorting and pagination links.

    Raises:
        LookupError: The selected maintenance task does not exist.
        RuntimeError: Maintenance observations are unavailable.

    Invalid controls propagate ValueError from maintenance_selection.
    """
    selection = maintenance_selection(params)
    backlog = (snapshot or {}).get("backlog")
    if backlog is None:
        message = "Maintenance data is unavailable."
        raise RuntimeError(message)
    groups = {group["key"]: group for group in backlog["groups"]}
    if selection["task"] not in groups:
        message = "Maintenance task not found."
        raise LookupError(message)
    if selection["sort"] == "random":
        selection.setdefault("seed", secrets.token_hex(8))
    selected = groups[selection["task"]]
    items = maintenance_items(
        selected["items"],
        selection["sort"],
        selection["order"],
        selection.get("seed", ""),
    )
    portion = maintenance_page(items, selection["page"])
    page = _Page(locale)
    heading = page.interface.label(selected.get("label"), selected["category"])
    context = page.context(heading)
    reverse = {
        **selection,
        "page": "1",
        "order": "desc" if selection["order"] == "asc" else "asc",
    }
    context.update(
        count=page.text("backlog.count", count=f"{selected['count']:,}"),
        updated=page.source_updated(backlog["observed_at"]),
        sort_choices=[
            {
                "label": page.text(key),
                "url": local_url(
                    "/maintenance/",
                    task=selection["task"],
                    sort=sort,
                ),
                "current": sort == selection["sort"],
            }
            for sort, key in (
                ("title", "backlog.sort.title"),
                ("class", "backlog.sort.class"),
                ("importance", "backlog.column.importance"),
                ("oldest", "backlog.sort.time"),
                ("random", "backlog.sort.random"),
            )
        ],
        reverse_url=local_url("/maintenance/", **reverse),
        cards=[page.worklist_card(item, groups) for item in portion["items"]],
        start=portion["offset"] + 1,
        previous=local_url(
            "/maintenance/",
            **{**selection, "page": portion["page"] - 1},
        )
        if portion["page"] > 1
        else None,
        following=local_url(
            "/maintenance/",
            **{**selection, "page": portion["page"] + 1},
        )
        if portion["page"] < portion["pages"]
        else None,
        pagination=page.text(
            "backlog.page",
            page=portion["page"],
            pages=portion["pages"],
        ),
        source_link=wiki_link(selected["source_url"], selected["category"]),
    )
    return _render("maintenance.html", context)


def render_about(
    config: Record | None,
    locale: str,
    *,
    status: Record | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Explain the tool and show its saved server environment.

    Args:
        config: Current application version and routine configuration.
        locale: Negotiated interface language.
        status: Validated saved monitor and resource observations.
        now: Optional clock for deterministic freshness checks.

    Returns:
        An informative About page with a read-only live server monitor.
    """
    page = _Page(locale)
    context = page.context(page.text("nav.about"), live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    before_name, after_name = page.text("about.intro").split("{name}")
    before_user, after_user = page.text("about.contact").split("{user}")
    context.update(
        before_name=before_name,
        after_name=after_name,
        before_user=before_user,
        after_user=after_user,
    )
    return _render("about.html", context)


def render_logs(
    data: Record,
    locale: str,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Render localized major events and daily-file downloads.

    Args:
        data: Validated daily-file listing, selection, and major events.
        locale: Negotiated interface language.
        status: Validated routine and scheduler observations.
        config: Current source version and routine definitions.
        now: Optional aware clock for deterministic status freshness.

    Returns:
        Escaped event summaries without exposing diagnostic text.
    """
    page = _Page(locale)
    context = page.context(page.text("logs.heading"), live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    context.update(
        files=[
            {
                **row,
                "size_label": readable_size(row["size"]),
                "url": local_url("/logs/", date=row["date"]),
                "download_url": local_url(
                    "/logs/",
                    date=row["date"],
                    download=1,
                ),
                "selected": row["date"] == data.get("date"),
            }
            for row in data["files"]
        ],
        query=data.get("query", ""),
        truncated=data.get("truncated", False),
        events=page.log_events(data, config or {}),
    )
    return _render("logs.html", context)


def render_error(message_key: str, locale: str) -> str:
    """Show a localized failure with working site navigation.

    Returns:
        An escaped HTML document with a localized alert.
    """
    page = _Page(locale)
    context = page.context(page.text("page.heading"))
    context["message"] = page.text(message_key)
    return _render("error.html", context)
