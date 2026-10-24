"""Build complete website pages in Python from saved backend data.

The public server selects and validates observations, then calls these
renderers. All output uses shared escaped HTML builders, authored locale
messages, responsive CSS, and native controls. Optional browser updates
refresh status and timers without requiring a page reload.
"""

from __future__ import annotations

import datetime as dt
import json
import operator
import secrets
from typing import TYPE_CHECKING

from webservice.presentation import (
    Interface,
    Markup,
    article_extract,
    assessment_code,
    element as tag,
    event_duration,
    failure_description,
    fragment,
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
    uptime,
    utc_time,
    wiki_link,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from webservice.presentation import Record

from webservice.presentation import title_url

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


def _time(value: object, *, seconds: bool = False) -> Markup:
    """Render an actual UTC observation or an unknown marker.

    Returns:
        A time element or the explicit unknown marker.
    """
    label = utc_time(value, seconds=seconds)
    return (
        fragment(label) if label == "—" else tag("time", label, datetime=value)
    )


def _clock_attributes(
    interface: Interface,
    schedule: Record,
    now: dt.datetime,
) -> Record:
    """Share server clock anchors between status views.

    Returns:
        Escaped-element attributes for local clocks and state expiry.
    """
    return {
        "data_server_time": now.isoformat(),
        "data_schedule_expires": schedule.get("expires_at"),
        "data_schedule_stale": interface.text("ops.schedule_stale"),
        "data_clock_messages": json.dumps({
            key: interface.text(
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
                "time.minutes_ahead",
                "time.hours_ahead",
                "time.soon",
                "time.just_now",
            )
        }),
    }


class _Page:
    """Compose localized fragments with one escaping boundary."""

    def __init__(self, locale: str) -> None:
        """Use one negotiated locale throughout a complete response."""
        self.interface = Interface(locale)
        self.text = self.interface.text

    def relative_note(self, value: object, now: dt.datetime) -> Markup:
        """Render a small relative annotation for a known timestamp.

        Returns:
            A localized small element, or nothing for unknown times.
        """
        label = relative_time(value, self.interface, now)
        return (
            tag(
                "small",
                label,
                class_="relative-time",
                data_clock="relative",
                data_observed_at=value,
            )
            if label
            else fragment()
        )

    @staticmethod
    def _duration_attributes(event: Record | None) -> Record:
        """Expose running starts for optional browser clocks.

        Returns:
            Timer attributes for active observations with a valid start.
        """
        if (
            not event
            or event.get("status") != "running"
            or timestamp(event.get("started_at")) is None
        ):
            return {}
        return {
            "data_clock": "elapsed",
            "data_started_at": event["started_at"],
            "data_duration_template": "{duration}",
        }

    @staticmethod
    def _active_progress(
        event: Record | None,
        schedule: Record,
    ) -> Record | None:
        """Select progress from the current active monitor session.

        Args:
            event: Saved routine lifecycle and optional progress.
            schedule: Validated monitor state and session start.

        Returns:
            Saved progress or a preparing phase while fresh and running.
            None for inactive, stale, or superseded observations.
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

    def _progress_note(self, progress: Record | None) -> Markup:
        """Describe the phase separately from the update subject.

        Returns:
            A phase and optional counters, or nothing when inactive.
        """
        if progress is None:
            return fragment()
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
        if current is not None and total is not None:
            label = self.text(
                "progress.count",
                phase=label,
                current=f"{current:,}",
                total=f"{total:,}",
            )
        return tag(
            "li",
            label,
            class_="routine-progress context",
            data_progress_active=True,
        )

    def _routine_state(
        self,
        routine: str,
        event: Record | None,
        progress: Record | None,
        popup_id: str,
    ) -> Markup:
        """Render a result button with fresh update details.

        Args:
            routine: Configured task whose update subject is shown.
            event: Saved lifecycle outcome, if available.
            progress: Current-session progress selected by the caller.
            popup_id: Existing destination-details dialog identifier.

        Returns:
            A result button retaining a generic fallback after expiry.
        """
        fallback = self._outcome_label(event)
        label = fallback
        if _completed_daily_report(routine, event):
            label = self.text(
                "state.UpdatedReportDate",
                date=self.interface.date(event["report_date"]),
            )
        if progress is not None:
            label = (
                self.text(
                    "progress.pageviews_date"
                    if routine == "pageviews"
                    else "progress.report_date",
                    date=self.interface.date(progress["report_date"]),
                )
                if progress.get("report_date")
                else self.text(f"progress.routine.{routine}")
            )
        return tag(
            "button",
            label,
            type="button",
            class_="status-link",
            popovertarget=popup_id,
            aria_haspopup="dialog",
            data_progress_label=label if progress is not None else None,
            data_progress_fallback=fallback if progress is not None else None,
        )

    def _outcome_label(self, event: Record | None) -> str:
        """Describe an outcome using its saved failure observations.

        Args:
            event: Routine or destination outcome with failure facts.

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

    def document(
        self,
        heading: str,
        body: Markup,
        footer: str | Markup = "",
        *,
        live: bool = False,
        navigation: Markup | None = None,
    ) -> str:
        """Wrap content with shared metadata, navigation, and styles.

        Returns:
            A complete escaped HTML document.
        """
        nav = tag(
            "nav",
            *(
                tag("a", self.text(key), href=path)
                for path, key in (
                    ("/about/", "nav.about"),
                    ("/logs/", "nav.logs"),
                )
            ),
            wiki_link(
                "https://zh.wikipedia.org/wiki/WikiProject:电子游戏",
                self.text("nav.project"),
            ),
            aria_label="Aranami",
        )
        head = tag(
            "head",
            tag("meta", charset="utf-8"),
            tag(
                "meta",
                name="viewport",
                content="width=device-width, initial-scale=1",
            ),
            tag("meta", name="theme-color", content="#edf9f4"),
            tag(
                "meta",
                name="description",
                content=self.text("page.description"),
            ),
            tag("link", rel="icon", href="/aranami.svg", type="image/svg+xml"),
            tag("link", rel="stylesheet", href="/styles.css"),
            tag("script", src="/live.js", defer=True) if live else fragment(),
            tag("title", f"{heading} · Aranami"),
        )
        header = tag(
            "header",
            tag(
                "div",
                tag(
                    "a",
                    tag(
                        "img",
                        src="/aranami.svg",
                        width=36,
                        height=36,
                        alt="",
                    ),
                    tag("span", "Aranami"),
                    href="/",
                    class_="brand",
                ),
                tag("h1", heading),
                navigation
                if navigation is not None
                else tag(
                    "nav",
                    tag("a", self.text("nav.backlog"), href="/#backlog"),
                    class_="page-toc",
                    aria_label=self.text("nav.contents"),
                ),
                class_="shell",
            ),
            class_="masthead",
        )
        contents = tag(
            "body",
            tag("a", self.text("nav.skip"), href="#main", class_="skip"),
            header,
            tag("main", body, class_="shell", id="main"),
            tag(
                "footer",
                tag(
                    "div",
                    tag("p", footer, class_="footer-uptime"),
                    nav,
                    class_="shell footer-inner",
                ),
            ),
        )
        return "<!doctype html>\n" + str(
            tag("html", head, contents, lang=self.interface.language_tag),
        )

    def assessments(self, item: Record) -> Markup:
        """Show recorded grades as independently wrapping chips.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        chips = []
        for dimension in ("class", "importance"):
            value = item.get(f"assessment_{dimension}")
            if not isinstance(value, str) or not value.strip():
                continue
            code = assessment_code(value, dimension)
            label = (
                self.text(f"assessment.{dimension}.{code}") if code else value
            )
            chips.append(
                tag(
                    "span",
                    label,
                    class_="assessment-chip",
                    data_assessment=dimension,
                    title=label,
                ),
            )
        return (
            tag("span", *chips, class_="assessment-chips")
            if chips
            else fragment()
        )

    def article_summary(self, item: Record) -> Markup:
        """Share title links and assessment chips across cards.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        return tag(
            "span",
            tag(
                "bdi",
                wiki_link(item["url"], item["title"], item["title"]),
                lang="zh",
            ),
            " ",
            self.assessments(item),
            class_="article-summary",
        )

    def entry_meta(self, item: Record) -> Markup:
        """Retain scheduled and carry-in date semantics.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        parts = []
        if item.get("date") is not None:
            if item.get("date_kind") == "scheduled":
                label = self.text("date.scheduled")
                parts.append(
                    f"({label}) "
                    if self.interface.locale == "en"
                    else f"\uff08{label}\uff09",
                )
            parts.append(
                tag(
                    "time",
                    self.interface.date(item["date"]),
                    datetime=item["date"],
                ),
            )
        kind = {
            "fa": "appearance.featured",
            "fl": "appearance.featured",
            "featured": "appearance.featured",
            "ga": "appearance.good",
            "news": "homepage.news",
            "history": "homepage.history",
        }.get(item.get("kind"))
        if kind is not None:
            if item.get("date") is not None and (
                self.interface.locale == "en"
                or item.get("kind") in {"news", "history"}
            ):
                parts.append(" · ")
            parts.append(self.text(kind))
        note = {
            "window_start": "date.window_start",
            "observed": "date.observed",
        }.get(item.get("date_kind"))
        if note:
            parts.append(
                tag(
                    "abbr",
                    "†",
                    class_="date-note-marker",
                    title=self.text(note),
                    aria_label=self.text(note),
                ),
            )
        return tag("p", *parts, class_="card-meta")

    def pageviews_meta(self, item: Record, day: str) -> Markup:
        """Link daily counts to trends and keep site ranks separate.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        count = tag(
            "a",
            self.text("pageviews.views", count=f"{item['views']:,}"),
            href=pageviews_url(item, day),
            class_="pageviews-number",
        )
        rank = fragment()
        if item.get("site_rank") is not None:
            rank = fragment(
                " · ",
                tag(
                    "span",
                    self.text(
                        "pageviews.site_rank",
                        rank=f"{item['site_rank']:,}",
                    ),
                    class_="site-rank",
                ),
            )
        return tag("p", count, rank, class_="card-meta")

    def article_cards(
        self,
        items: list[Record] | None,
        pageviews: Record | None = None,
    ) -> Markup:
        """Render supplied cards with saved introductions and metadata.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        if items is None:
            return tag("p", self.text("empty.section"), class_="empty")
        if not items:
            return tag("p", self.text("empty.period"), class_="empty")
        cards = []
        for item in items:
            language, extract = article_extract(item, self.interface.locale)
            intro = (
                tag(
                    "p",
                    tag("bdi", extract, lang=Interface(language).language_tag),
                    class_="article-extract",
                )
                if extract is not None
                else fragment()
            )
            meta = (
                self.pageviews_meta(item, pageviews["date"])
                if pageviews
                else self.entry_meta(item)
            )
            body = tag(
                "div",
                self.article_summary(item),
                intro,
                meta,
                class_="article-card-body",
            )
            cards.append(
                tag(
                    "li",
                    body,
                    class_="article-card",
                    value=item["rank"] if pageviews else None,
                ),
            )
        return tag("ol", *cards, class_="card-grid article-list highlights")

    def source_updated(
        self,
        value: object,
        *,
        attribution: bool = False,
    ) -> Markup:
        """Attach attribution to the section update time.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        if value is None:
            return fragment()
        credit = fragment()
        if attribution:
            before, after = self.text("extracts.attribution").split(
                "{license}",
            )
            credit = tag(
                "span",
                " · ",
                before,
                tag(
                    "a",
                    "CC BY-SA 4.0",
                    href="https://creativecommons.org/licenses/by-sa/4.0/",
                ),
                after,
                class_="extract-attribution",
            )
        return tag(
            "p",
            self.text("snapshot.updated"),
            " ",
            _time(value),
            credit,
            class_="source-updated",
        )

    def pageviews_description(self, observation: Record | None) -> Markup:
        """Link the measured day to the full-site popularity ranking.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        day = fragment()
        top = self.text("pageviews.top")
        if observation is not None:
            day = fragment(
                tag(
                    "time",
                    self.interface.date(observation["date"]),
                    datetime=observation["date"],
                ),
                " · " if self.interface.locale == "en" else "",
            )
            top = tag(
                "a",
                top,
                href=local_url(
                    "https://pageviews.wmcloud.org/topviews",
                    project="zh.wikipedia.org",
                    date=observation["date"],
                ),
            )
        before, remainder = self.text("pageviews.description").split("{date}")
        middle, after = remainder.split("{top}")  # ruff: ignore[missing-f-string-syntax]
        return fragment(before, day, middle, top, after)

    def highlights(self, snapshot: Record) -> Markup:
        """Render article sections while omitting confirmed empties.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        sections = []
        for section in homepage_sections(snapshot):
            key = section["key"]
            views = snapshot.get("pageviews") if key == "pageviews" else None
            title = self.text(
                "pageviews.heading"
                if key == "pageviews"
                else f"homepage.{key}",
            )
            description = (
                self.pageviews_description(views)
                if key == "pageviews"
                else self.text(f"homepage.{key}_description")
            )
            cards = (
                tag("p", self.text("pageviews.unavailable"), class_="empty")
                if key == "pageviews" and views is None
                else self.article_cards(section["items"], views)
            )
            updated = (
                snapshot.get("reports", {}).get(key, {}).get("last_success_at")
            )
            sections.append(
                tag(
                    "section",
                    tag("h3", title, id=f"homepage-{key}"),
                    tag("p", description, class_="report-description"),
                    cards,
                    self.source_updated(updated, attribution=True),
                    class_="report-section",
                    aria_labelledby=f"homepage-{key}",
                ),
            )
        if not sections:
            return fragment()
        return tag(
            "section",
            tag("h2", self.text("homepage.heading"), id="homepage-heading"),
            *sections,
            class_="section-flat",
            id="homepage",
            aria_labelledby="homepage-heading",
        )

    def backlog(self, backlog: Record | None) -> Markup:
        """Link nonempty tasks in their configured section order.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        if backlog is not None and backlog["total"] == 0:
            return fragment()
        content = [
            tag("h2", self.text("backlog.heading"), id="backlog-heading"),
        ]
        if backlog is None:
            content.append(
                tag("p", self.text("backlog.unavailable"), class_="empty"),
            )
        else:
            groups = [
                group for group in backlog["groups"] if group["count"] > 0
            ]
            for key, tasks in maintenance_sections(groups).items():
                cards = [
                    tag(
                        "a",
                        tag(
                            "span",
                            self.interface.label(
                                group.get("label"),
                                group["category"],
                            ),
                            class_="backlog-label",
                        ),
                        tag(
                            "span",
                            self.text(
                                "backlog.count",
                                count=f"{group['count']:,}",
                            ),
                            class_="backlog-count",
                        ),
                        href=local_url(
                            "/maintenance/",
                            task=group["key"],
                            page=1,
                        ),
                        class_="backlog-task",
                    )
                    for group in tasks
                ]
                label = self.interface.label(
                    tasks[0].get("section_label"),
                    self.text("backlog.heading"),
                )
                content.append(
                    tag(
                        "section",
                        tag("h3", label, id=f"backlog-section-{key}"),
                        tag("div", *cards, class_="card-grid"),
                        class_="backlog-section",
                        aria_labelledby=f"backlog-section-{key}",
                    ),
                )
            content.extend((
                tag("p", self.text("backlog.note"), class_="homepage-note"),
                self.source_updated(backlog["observed_at"]),
            ))
        return tag(
            "section",
            *content,
            class_="section-flat",
            id="backlog",
            aria_labelledby="backlog-heading",
        )

    def routine_links(self, name: str, definition: Record) -> Markup:
        """Render configured targets with accessible link names.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        targets = list(definition.get("pages", {}).values()) or definition.get(
            "prefixes",
            [],
        )
        if not targets:
            return fragment(name)
        multiple = len(targets) > 1
        links = []
        for index, title in enumerate(targets, 1):
            description = (
                f"{index} · {name} · {title}"
                if multiple
                else f"{name} · {title}"
            )
            links.extend((
                " " if index > 1 else "",
                wiki_link(
                    title_url(title),
                    str(index) if multiple else name,
                    description,
                ),
            ))
        if not multiple:
            return fragment(*links)
        opening, closing = (
            (" (", ")")
            if self.interface.locale == "en"
            else ("\uff08", "\uff09")
        )
        return fragment(name, opening, *links, closing)

    def report_details(
        self,
        routine: str,
        popup_id: str,
        records: list[Record],
        now: dt.datetime,
    ) -> Markup:
        """Build a keyboard-accessible native popup without JavaScript.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        keys = (
            "column.article",
            "ops.column_status",
            "ops.started",
            "ops.finished",
            "ops.column_duration",
        )
        rows = [
            tag(
                "tr",
                tag(
                    "th",
                    wiki_link(
                        title_url(record["title"], record["site"]),
                        record["title"],
                    ),
                    scope="row",
                ),
                tag("td", self._outcome_label(record)),
                tag(
                    "td",
                    _time(
                        None
                        if record["status"] == "queued"
                        else record.get("started_at"),
                        seconds=True,
                    ),
                ),
                tag("td", _time(record.get("finished_at"), seconds=True)),
                tag(
                    "td",
                    event_duration(record, self.interface, now),
                    **self._duration_attributes(record),
                ),
                data_key=f"{record['site']}:{record['title']}",
            )
            for record in records
        ]
        table = tag(
            "table",
            tag(
                "thead",
                tag(
                    "tr",
                    *(tag("th", self.text(key), scope="col") for key in keys),
                ),
            ),
            tag("tbody", *rows),
        )
        heading = self.text("ops.details", routine=routine)
        return tag(
            "div",
            tag(
                "div",
                tag("h3", heading, id=f"{popup_id}-heading"),
                tag(
                    "button",
                    self.text("ops.close"),
                    type="button",
                    popovertarget=popup_id,
                    popovertargetaction="hide",
                    autofocus=True,
                ),
                class_="popup-heading",
            ),
            tag("p", self.text("ops.timing_note"), class_="context"),
            tag(
                "div",
                table,
                class_="table-scroll",
                tabindex=0,
                role="region",
                aria_label=heading,
            )
            if records
            else tag("p", self.text("ops.no_record"), class_="empty"),
            id=popup_id,
            popover="auto",
            role="dialog",
            aria_labelledby=f"{popup_id}-heading",
            class_="page-updates",
        )

    def routine_cards(
        self,
        status: Record,
        definitions: Record,
        schedule: Record,
        now: dt.datetime,
    ) -> Markup:
        """Show actual outcomes and schedule states independently.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        events = (status.get("routines") or {}).get("routines", {})
        records = (status.get("reports") or {}).get("reports", {}).values()
        cards = []
        for identifier, definition in definitions.items():
            name = self.text(f"routine.{identifier}")
            event = events.get(identifier)
            running = (event or {}).get("status") == "running"
            progress = self._active_progress(event, schedule)
            pages = sorted(
                (
                    record
                    for record in records
                    if record["routine"] == identifier
                    and record.get("dry") is False
                ),
                key=operator.itemgetter("title"),
            )
            popup_id = f"report-details-{identifier}"
            state = self._routine_state(identifier, event, progress, popup_id)
            observed = (
                event["started_at" if running else "finished_at"]
                if event
                else None
            )
            scheduled = schedule["routines"].get(identifier, {})
            upcoming = scheduled.get("next_run_at")
            metadata = tag(
                "ul",
                tag(
                    "li",
                    tag(
                        "span",
                        "✅"
                        if _completed_daily_report(identifier, event)
                        else _STATE_ICONS.get(
                            (event or {}).get("status"),
                            "❔",
                        ),
                        aria_hidden="true",
                    ),
                    " ",
                    state,
                    class_="routine-status",
                ),
                self._progress_note(progress),
                tag(
                    "li",
                    self.text(
                        "ops.execution_started"
                        if running
                        else "ops.execution_finished",
                    ),
                    _time(observed),
                    self.relative_note(observed, now),
                ),
                tag(
                    "li",
                    self.text("ops.next_run"),
                    tag(
                        "span",
                        next_run(schedule, identifier, self.interface),
                        self.relative_note(upcoming, now),
                        data_schedule_time=schedule.get("expires_at")
                        is not None,
                    ),
                )
                if not running
                else fragment(),
                class_="routine-meta",
            )
            cards.append(
                tag(
                    "article",
                    tag("h3", self.routine_links(name, definition)),
                    metadata,
                    self.report_details(name, popup_id, pages, now),
                    class_="routine-card",
                    id=f"routine-{identifier}",
                ),
            )
        missing = (
            tag("p", self.text("ops.schedule_missing"), class_="empty")
            if not definitions
            else fragment()
        )
        return tag(
            "section",
            tag("h2", self.text("ops.heading"), id="tool-status-heading"),
            tag(
                "p",
                self.text("ops.reconnecting"),
                id="live-notice",
                class_="context",
                role="status",
                hidden=True,
            ),
            missing,
            tag("div", *cards, class_="card-grid"),
            class_="section-flat",
            id="tool-status",
            aria_labelledby="tool-status-heading",
            **_clock_attributes(self.interface, schedule, now),
        )

    def worklist_card(self, item: Record, groups: Record) -> Markup:
        """Render exact issue sources and the earliest month category.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        issues = [
            groups[key] for key in item.get("issues", []) if key in groups
        ]
        issue_links = [
            tag(
                "li",
                wiki_link(
                    group["source_url"],
                    self.interface.label(
                        group.get("label"),
                        group["category"],
                    ),
                ),
            )
            for group in issues
        ]
        if not issue_links:
            issue_links = [tag("li", "—", class_="unknown")]
        month = item.get("oldest_issue_month")
        month_label = (
            tag(
                "time",
                self.interface.date(month, month_only=True),
                datetime=month,
            )
            if month
            else fragment("—")
        )
        if month and item.get("oldest_issue_category"):
            month_label = tag(
                "a",
                month_label,
                href=title_url("Category:" + item["oldest_issue_category"]),
            )
        return tag(
            "li",
            self.article_summary(item),
            tag(
                "ul",
                *issue_links,
                class_="card-meta issues",
                aria_label=self.text("backlog.column.issues"),
            ),
            tag(
                "p",
                month_label,
                class_="card-meta",
                aria_label=self.text("backlog.column.oldest"),
            ),
            class_="article-card",
        )

    def sort_controls(self, selection: dict[str, str]) -> Markup:
        """Preserve random seeds and reverse controls across pages.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        choices = {
            "title": "backlog.sort.title",
            "class": "backlog.sort.class",
            "importance": "backlog.column.importance",
            "oldest": "backlog.sort.time",
            "random": "backlog.sort.random",
        }
        links = [
            tag(
                "a",
                self.text(key),
                href=local_url(
                    "/maintenance/",
                    task=selection["task"],
                    sort=sort,
                ),
                aria_current="true" if sort == selection["sort"] else None,
            )
            for sort, key in choices.items()
        ]
        reverse = {
            **selection,
            "page": "1",
            "order": "desc" if selection["order"] == "asc" else "asc",
        }
        return tag(
            "nav",
            tag("span", self.text("backlog.sort"), ":"),
            *links,
            tag(
                "a",
                "[",
                self.text("backlog.sort.reverse"),
                "]",
                href=local_url("/maintenance/", **reverse),
            ),
            class_="sort-controls",
            aria_label=self.text("backlog.sort"),
        )

    def pagination(self, selection: dict[str, str], page: Record) -> Markup:
        """Retain sort controls in previous and next page links.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        previous = (
            tag(
                "a",
                self.text("backlog.previous"),
                href=local_url(
                    "/maintenance/",
                    **{**selection, "page": page["page"] - 1},
                ),
                rel="prev",
            )
            if page["page"] > 1
            else fragment()
        )
        following = (
            tag(
                "a",
                self.text("backlog.next"),
                href=local_url(
                    "/maintenance/",
                    **{**selection, "page": page["page"] + 1},
                ),
                rel="next",
            )
            if page["page"] < page["pages"]
            else fragment()
        )
        return tag(
            "nav",
            previous,
            tag(
                "p",
                self.text(
                    "backlog.page",
                    page=page["page"],
                    pages=page["pages"],
                ),
            ),
            following,
            class_="pagination",
            aria_label=self.text("nav.backlog"),
        )

    def log_controls(self, data: Record) -> Markup:
        """Show date and literal-text controls in a GET form.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        options = [
            tag(
                "option",
                row["date"],
                value=row["date"],
                selected=row["date"] == data["date"],
            )
            for row in data["files"]
        ]
        return tag(
            "form",
            tag(
                "label",
                self.text("logs.column_date"),
                tag("select", *options, name="date"),
            ),
            tag(
                "label",
                self.text("logs.query"),
                tag(
                    "input",
                    type="search",
                    name="q",
                    maxlength=256,
                    value=data["query"],
                ),
                class_="search",
            ),
            tag("button", self.text("logs.apply"), type="submit"),
            action="/logs/",
            method="get",
            class_="log-query",
        )

    def log_files(self, files: list[Record]) -> Markup:
        """List exact daily files with download links and sizes.

        Returns:
            Escaped HTML for the selected presentation content.
        """
        headings = tag(
            "tr",
            *(
                tag("th", self.text(key), scope="col")
                for key in (
                    "logs.column_date",
                    "logs.column_file",
                    "logs.column_size",
                )
            ),
        )
        rows = [
            tag(
                "tr",
                tag(
                    "td",
                    tag(
                        "a",
                        row["date"],
                        href=local_url("/logs/", date=row["date"]),
                    ),
                ),
                tag(
                    "td",
                    tag(
                        "a",
                        tag("code", row["name"]),
                        href=local_url("/logs/", date=row["date"], download=1),
                        download=True,
                    ),
                    class_="filename",
                ),
                tag("td", readable_size(row["size"])),
            )
            for row in files
        ]
        return tag(
            "div",
            tag(
                "table",
                tag("thead", headings),
                tag("tbody", *rows),
                class_="logs-table",
            ),
            class_="table-scroll",
        )


def _live_status(
    page: _Page,
    status: Record,
    config: Record,
    now: dt.datetime,
) -> tuple[Markup, Markup, Markup]:
    """Share status cards and uptime between initial and live views.

    Returns:
        Routine cards, uptime, and compact server-state fragments.
    """
    definitions = (config.get("routines") or {}).get("routines", {})
    schedule = schedule_state(status.get("schedule"), definitions, now)
    version = config.get("version") or page.text("ops.unavailable")
    started = schedule.get("started_at")
    footer = tag(
        "span",
        uptime(schedule, version, page.interface, now),
        id="live-uptime",
        data_clock="uptime" if started else None,
        data_started_at=started,
        data_uptime_template=page.text(
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
        data_uptime_unknown=page.text(
            "footer.uptime_unknown",
            version=version,
        ),
    )
    server = tag(
        "section",
        tag("h2", page.text("server.heading"), id="server-status-heading"),
        tag(
            "p",
            page.text(f"server.{schedule['state']}"),
            id="server-state",
            data_schedule_time=schedule.get("expires_at") is not None,
            data_stale_text=page.text("server.stale"),
        ),
        tag(
            "p",
            page.text("server.observed"),
            _time(schedule.get("updated_at"), seconds=True),
            page.relative_note(schedule.get("updated_at"), now),
            class_="source-updated",
        ),
        tag(
            "p",
            page.text("ops.reconnecting"),
            id="live-server-notice",
            class_="context",
            role="status",
            hidden=True,
        ),
        id="live-server-status",
        aria_labelledby="server-status-heading",
        **_clock_attributes(page.interface, schedule, now),
    )
    return (
        page.routine_cards(status, definitions, schedule, now),
        footer,
        server,
    )


def render_dashboard(
    snapshot: Record | None,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
) -> str:
    """Build the dashboard from independent saved observations.

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
    now = now or dt.datetime.now(dt.UTC)
    status, config = status or {}, config or {}
    status_cards, footer, _ = _live_status(page, status, config, now)
    notice = fragment()
    if snapshot is None:
        notice = tag("p", page.text("notice.preparing"), class_="notice")
    elif snapshot.get("errors"):
        notice = tag("p", page.text("notice.partial"), class_="notice")
    snapshot = snapshot or {}
    sections = homepage_sections(snapshot)
    navigation = tag(
        "nav",
        *(
            tag(
                "a",
                page.text(
                    "pageviews.heading"
                    if section["key"] == "pageviews"
                    else f"homepage.{section['key']}",
                ),
                href=f"#homepage-{section['key']}",
            )
            for section in sections
        ),
        tag("a", page.text("nav.backlog"), href="#backlog")
        if (snapshot.get("backlog") or {}).get("total") != 0
        else fragment(),
        tag("a", page.text("ops.heading"), href="#tool-status"),
        class_="page-toc",
        aria_label=page.text("nav.contents"),
    )
    body = fragment(
        notice,
        page.highlights(snapshot),
        page.backlog(snapshot.get("backlog")),
        status_cards,
    )
    return page.document(
        page.text("page.heading"),
        body,
        footer,
        live=True,
        navigation=navigation,
    )


def render_live_status(
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
) -> str:
    """Render fresh status and uptime without loading website datasets.

    Args:
        status: Validated routine, schedule, and page observations.
        config: Current source version and routine definitions.
        locale: The language of the existing page.
        now: Optional aware server clock for deterministic elapsed time.

    Returns:
        Escaped fragments with the same identities as the full page.
    """
    return str(
        fragment(
            *_live_status(
                _Page(locale),
                status or {},
                config or {},
                now or dt.datetime.now(dt.UTC),
            ),
            _runtime_status(
                _Page(locale),
                status or {},
                config or {},
                now or dt.datetime.now(dt.UTC),
            ),
        ),
    )


def render_maintenance(
    snapshot: Record | None,
    params: Mapping[str, list[str]],
    locale: str,
) -> str:
    """Build a maintenance list page after globally sorting it.

    Args:
        snapshot: Validated website snapshot containing the catalogue.
        params: Parsed controls with repeated values retained.
        locale: Negotiated interface language.

    Returns:
        Complete escaped HTML document with stable pagination links.

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
    cards = (
        tag(
            "ol",
            *(page.worklist_card(item, groups) for item in portion["items"]),
            class_="card-grid article-list",
            start=portion["offset"] + 1,
            aria_label=heading,
        )
        if portion["items"]
        else tag("p", page.text("backlog.empty"), class_="empty")
    )
    body = fragment(
        tag("a", page.text("backlog.back"), href="/#backlog", class_="back"),
        tag("p", page.text("backlog.heading"), class_="context"),
        tag(
            "div",
            tag(
                "p",
                page.text("backlog.count", count=f"{selected['count']:,}"),
            ),
            page.source_updated(backlog["observed_at"]),
            class_="metadata",
        ),
        page.sort_controls(selection),
        cards,
        page.pagination(selection, portion),
        tag(
            "p",
            page.text("backlog.source"),
            ": ",
            wiki_link(selected["source_url"], selected["category"]),
        ),
    )
    return page.document(heading, body)


def _runtime_status(
    page: _Page,
    status: Record,
    config: Record,
    now: dt.datetime,
) -> Markup:
    """Render saved hub environment and fresh resource observations.

    Returns:
        A live region with explicit unknown values for stale usage.
    """
    snapshot = status.get("schedule") or {}
    definitions = (config.get("routines") or {}).get("routines", {})
    schedule = schedule_state(snapshot, definitions, now)
    runtime = snapshot.get("runtime") or {}
    unknown = page.text("ops.unavailable")
    fresh = schedule["state"] in {"running", "paused"}
    memory = runtime.get("memory_bytes") if fresh else None
    limit = runtime.get("memory_limit_bytes") if fresh else None
    cpu = runtime.get("cpu_percent") if fresh else None
    cpu_limit = runtime.get("cpu_limit") if fresh else None
    rows = (
        ("platform", "Wikimedia Toolforge", False),
        ("python", runtime.get("python") or unknown, False),
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
        (
            "memory",
            readable_size(memory) if memory is not None else unknown,
            True,
        ),
        (
            "memory_limit",
            readable_size(limit) if limit is not None else unknown,
            True,
        ),
        ("cpu", f"{cpu:.1f}%" if cpu is not None else unknown, True),
        (
            "cpu_limit",
            f"{cpu_limit:g}" if cpu_limit is not None else unknown,
            True,
        ),
    )
    return tag(
        "div",
        tag("h3", page.text("server.environment_heading")),
        tag("p", page.text("server.usage_note"), class_="context"),
        tag(
            "table",
            tag(
                "tbody",
                *(
                    tag(
                        "tr",
                        tag("th", page.text(f"server.{key}"), scope="row"),
                        tag(
                            "td",
                            value,
                            data_schedule_time=True
                            if usage and fresh
                            else None,
                            data_stale_text=unknown if usage else None,
                        ),
                    )
                    for key, value, usage in rows
                ),
            ),
            class_="server-metrics",
        ),
        id="server-runtime",
    )


def render_about(
    config: Record | None,
    locale: str,
    *,
    status: Record | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Explain the tool and show its observed server environment.

    Args:
        config: Current application version and routine configuration.
        locale: Negotiated interface language.
        status: Validated saved monitor and resource observations.
        now: Optional clock for deterministic freshness checks.

    Returns:
        An informative About page with a read-only live server monitor.
    """
    page = _Page(locale)
    config, status = config or {}, status or {}
    now = now or dt.datetime.now(dt.UTC)
    _, footer, server = _live_status(page, status, config, now)
    sections = [
        tag(
            "section",
            tag("h2", page.text(f"about.{key}_heading"), id=f"{key}-heading"),
            tag("p", page.text(f"about.{key}")),
            aria_labelledby=f"{key}-heading",
        )
        for key in ("discover", "improve", "reports")
    ]
    body = tag(
        "div",
        tag("p", page.text("about.intro")),
        *sections,
        tag("p", page.text("about.freshness")),
        server,
        _runtime_status(page, status, config, now),
        class_="prose",
    )
    return page.document(page.text("nav.about"), body, footer, live=True)


def render_logs(
    data: Record,
    locale: str,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Display bounded log excerpts and exact daily-file downloads.

    Args:
        data: Validated log listing, selection, and bounded excerpt.
        locale: Negotiated interface language.
        status: Validated routine and scheduler observations.
        config: Current source version and routine definitions.
        now: Optional aware clock for deterministic status freshness.

    Returns:
        Escaped HTML diagnostics without interpreting them as status.
    """
    page = _Page(locale)
    _, footer, server = _live_status(
        page,
        status or {},
        config or {},
        now or dt.datetime.now(dt.UTC),
    )
    if not data["files"]:
        return page.document(
            page.text("logs.heading"),
            fragment(
                server,
                tag("p", page.text("logs.empty"), class_="empty"),
            ),
            footer,
            live=True,
        )
    notice = (
        tag("p", page.text("logs.truncated"), class_="source-updated")
        if data["truncated"]
        else fragment()
    )
    excerpt = (
        tag("pre", "\n".join(data["lines"]), class_="log-lines")
        if data["lines"]
        else tag("p", page.text("logs.no_matches"), class_="empty")
    )
    body = fragment(
        server,
        page.log_controls(data),
        tag("h2", page.text("logs.recent")),
        tag("p", page.text("logs.reader_note"), class_="context"),
        notice,
        excerpt,
        page.log_files(data["files"]),
    )
    return page.document(page.text("logs.heading"), body, footer, live=True)


def render_error(message_key: str, locale: str) -> str:
    """Show a localized failure with working site navigation.

    Returns:
        Escaped HTML for the selected presentation content.
    """
    page = _Page(locale)
    return page.document(
        page.text("page.heading"),
        tag("p", page.text(message_key), class_="notice", role="alert"),
    )
