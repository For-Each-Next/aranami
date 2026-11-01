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
from hashlib import sha256
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from shared.deployment import project_version, resource_root
from shared.newsletter import Issue
from webservice.job_history import history_context
from webservice.presentation import (
    Interface,
    article_extract,
    assessment_code,
    catalog,
    event_duration,
    failure_description,
    homepage_sections,
    local_url,
    maintenance_filter,
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
_REPORT_ICONS = {
    "dyks": "lightbulb",
    "new_pages": "article-add",
    "assessment_lists": "article-check",
    "assessment_log": "history",
    "pageviews": "chart-bar",
    "enwp_key_articles": "language",
    "pexbot": "robot",
}
# Module:PJ:VG/RVW, revision 92100885, with credited vector equivalents.
# Reassessment uses Codex history.
_REVIEW_ICONS = {
    "fac": ("featured-candidate.svg", "Cscr-candidate.svg"),
    "flc": ("featured-candidate.svg", "Cscr-candidate.svg"),
    "acc": ("a-candidate.svg", "A_candidate.svg"),
    "gan": ("ga-candidate.svg", "GA_candidate.svg"),
    "bpan": ("bplus-candidate.svg", "Bplus_candidate.svg"),
    "pr": ("peer-review.svg", "Nuvola_apps_kedit.svg"),
    "ppr": ("project-peer-review.svg", "Cvg_peerreview_icon.png"),
    "dykc": ("dyk-candidate.svg", "DYK_candidate.svg"),
}
_HIGHLIGHT_ICONS = {
    "featured": ("featured.svg", "Cscr-featured.svg"),
    "good": ("good.svg", "Symbol_support_vote.svg"),
    "dyk": ("dyk.svg", "Symbol_question.svg"),
}
_HOUR_SECONDS = 3600
_DAY_SECONDS = 24 * _HOUR_SECONDS
_MONTH_SECONDS = 365.25 / 12 * _DAY_SECONDS


@lru_cache(maxsize=64)
def _asset_url(name: str) -> str:
    """Identify an authored source asset by its immutable content.

    Args:
        name: Relative asset path supplied by a template or renderer.

    Returns:
        A local URL whose digest permits safe browser caching.
    """
    asset = resource_root("webservice") / "public_html" / name
    return f"/{name}?v={sha256(asset.read_bytes()).hexdigest()}"


@lru_cache(maxsize=1)
def _templates() -> Environment:
    """Load cached presentation templates with HTML autoescaping.

    Returns:
        A Jinja environment rooted in the source template directory.
    """
    environment = Environment(
        loader=FileSystemLoader(resource_root("webservice") / "templates"),
        autoescape=select_autoescape(["html"]),
        auto_reload=False,
    )
    environment.globals["asset_url"] = _asset_url
    return environment


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
        routine in {"new_pages", "pageviews", "assessment_log"}
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


def _english_ordinal(value: int) -> str:
    """Format an English rank with grouped digits and an ordinal suffix.

    Args:
        value: Observed positive rank.

    Returns:
        A rank such as ``1st``, ``12th``, or ``1,000th``.
    """
    suffix = (
        "th"
        if value % 100 in {11, 12, 13}
        else {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    )
    return f"{value:,}{suffix}"


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
        before, after = self.text("extracts.attribution").split("{license}")
        return {
            "heading": heading,
            "language_tag": self.interface.language_tag,
            "t": _messages(self.interface.locale),
            "live": live,
            "footer": {
                "label": self.text(
                    "footer.uptime_unknown",
                    version=project_version(),
                ),
                "unknown": self.text(
                    "footer.uptime_unknown",
                    version=project_version(),
                ),
            },
            "clock": self.clock({}, dt.datetime.now(dt.UTC)),
            "extract_attribution": {"before": before, "after": after},
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
            A localized relative label, without presentation wrappers.
        """
        label = relative_time(value, self.interface, now)
        return {
            "label": label[1:-1] if inline and label else label,
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
                    hours="{hours}",
                    days="{days}",
                )
                for key in (
                    "duration.seconds",
                    "duration.minutes",
                    "duration.hours",
                    "duration.days",
                    "time.months_ago",
                    "time.months_ahead",
                    "time.minutes_ago",
                    "time.hours_ago",
                    "time.days_ago",
                    "time.minutes_ahead",
                    "time.hours_ahead",
                    "time.days_ahead",
                    "time.soon",
                    "time.just_now",
                    "time.minutes_ago_one",
                    "time.minutes_ahead_one",
                    "time.hours_ago_one",
                    "time.hours_ahead_one",
                    "time.days_ago_one",
                    "time.days_ahead_one",
                    "time.months_ago_one",
                    "time.months_ahead_one",
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
            code = assessment_code(value, dimension) or (
                "Unassessed"
                if dimension == "class"
                and (not isinstance(value, str) or not value.strip())
                else "Unknown"
            )
            label = self.text(f"assessment.{dimension}.{code}")
            dimension_label = self.text(f"assessment.dimension.{dimension}")
            assessments.append({
                "dimension": dimension,
                "label": label,
                "dimension_label": dimension_label,
                "description": self.text(
                    f"assessment.description.{dimension}.{code}",
                ),
                "tooltip": self.text(
                    f"assessment.tooltip.{dimension}",
                    grade=self.text(
                        f"assessment.description.{dimension}.{code}",
                    ),
                ),
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
        section: str,
    ) -> Record:
        """Prepare date, appearance, and popularity observations.

        Args:
            item: Saved observations, including the appearance kind.
            pageviews: Observed metadata for the popularity section.
            section: Source section for DYK or showcase appearances.

        Returns:
            An article card preserving each observation's meaning.
        """
        card = self.article(item)
        appearance = (
            "dyk"
            if section == "dyk"
            else {
                "fa": "featured",
                "fl": "featured",
                "featured": "featured",
                "ga": "good",
            }.get(item.get("kind"))
            if section == "recognized"
            else None
        )
        if appearance:
            icon, source = _HIGHLIGHT_ICONS[appearance]
            card["highlight_icon"] = {
                "url": _asset_url(f"assets/icons/{icon}"),
                "source": "https://commons.wikimedia.org/wiki/File:" + source,
                "label": self.text(
                    "homepage.dyk"
                    if appearance == "dyk"
                    else f"appearance.{appearance}",
                ),
            }
        if pageviews:
            card.update(
                rank=item["rank"],
                date={
                    "value": pageviews["date"],
                    "label": self.interface.date(pageviews["date"]),
                },
                date_separator=": "
                if self.interface.locale == "en"
                else "\uff1a",
                pageviews_url=pageviews_url(item, pageviews["date"]),
                views=self.text(
                    "pageviews.views_one"
                    if self.interface.locale == "en" and item["views"] == 1
                    else "pageviews.views",
                    count=f"{item['views']:,}",
                ),
                site_rank=self.text(
                    "pageviews.site_rank",
                    rank=_english_ordinal(item["site_rank"])
                    if self.interface.locale == "en"
                    else f"{item['site_rank']:,}",
                )
                if item.get("site_rank") is not None
                else None,
                site_rank_url=local_url(
                    "https://pageviews.wmcloud.org/topviews/",
                    project="zh.wikipedia.org",
                    date=pageviews["date"],
                    search=item["title"],
                ).replace("+", "%20"),
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
            date_note=self.text(note) if note else None,
        )
        return card

    @staticmethod
    def source_updated(value: object) -> Record | None:
        """Prepare the section's observed source time.

        Args:
            value: Saved source observation timestamp, when available.

        Returns:
            Plain source metadata, or none without a saved observation.
        """
        return {"time": _time(value)} if value is not None else None

    def _review_elapsed(
        self,
        instant: dt.datetime,
        now: dt.datetime,
        event: str,
    ) -> str:
        """Describe elapsed opening and latest-reply ages.

        Returns:
            A localized complete month, day, hour, or minute count.
            Months use 365.25 / 12 days for both starts and replies.
        """
        seconds = (now - instant).total_seconds()
        if seconds >= _MONTH_SECONDS:
            unit, divisor = "months", _MONTH_SECONDS
        elif seconds >= _DAY_SECONDS:
            unit, divisor = "days", _DAY_SECONDS
        else:
            unit, divisor = "hours", _HOUR_SECONDS
        if seconds < _HOUR_SECONDS:
            unit, divisor = "minutes", 60
        count = int(seconds // divisor)
        key = f"reviews.{event}_{unit}"
        if count == 1:
            key += "_one"
        return self.text(key, count=count)

    def reviews(self, snapshot: Record, now: dt.datetime) -> Record | None:
        """Prepare complete review cards with observed elapsed time.

        Args:
            snapshot: Saved dashboard facts, possibly without reviews.
            now: Aware server time for duration calculations.

        Returns:
            The review section, or none for a confirmed empty list.
        """
        data = snapshot.get("reviews")
        if data is not None and not data["items"]:
            return None
        cards = []
        for item in (data or {}).get("items", []):
            card = self.article(item)
            review_type = item["review_type"]
            review_label = self.text(f"reviews.{review_type}")
            icon = _REVIEW_ICONS.get(review_type)
            icon_source = icon[1] if icon else "Codex_icon_history_(v2.6).svg"
            dates = []
            for field, event in (
                ("started_at", "started"),
                ("replied_at", "replied"),
            ):
                instant = timestamp(item.get(field))
                if instant is None or instant > now:
                    continue
                dates.append({
                    "value": item[field],
                    "label": utc_time(item[field], seconds=True),
                    "elapsed": self._review_elapsed(instant, now, event),
                    "participants": self.text(
                        "reviews.participants",
                        count=f"{item['participant_count']:,}",
                    )
                    if event == "replied"
                    and item.get("participant_count") is not None
                    else None,
                })
            card.update(
                review_icon=_asset_url(f"assets/icons/{icon[0]}")
                if icon
                else None,
                review_icon_symbol=None if icon else "history",
                review_icon_label=review_label,
                review_icon_source=(
                    "https://commons.wikimedia.org/wiki/File:" + icon_source
                ),
                review_link=wiki_link(
                    item["review_url"],
                    self.text("reviews.link"),
                ),
                review_dates=dates,
            )
            cards.append(card)
        return {
            "cards": cards,
            "is_review": True,
            "empty": self.text("empty.section") if data is None else None,
            "updated": self.source_updated(
                snapshot
                .get("reports", {})
                .get("reviews", {})
                .get("last_success_at"),
            ),
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
                if key not in {"pageviews", "recognized", "dyk"}
                else None,
                "empty": self.text("pageviews.unavailable")
                if key == "pageviews" and views is None
                else self.text("empty.section")
                if section["items"] is None
                else self.text("empty.period")
                if not section["items"]
                else None,
                "cards": [
                    self.article_card(item, views, key)
                    for item in section["items"] or []
                ],
                "updated": self.source_updated(updated),
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
                                "backlog.count_one"
                                if group["count"] == 1
                                else "backlog.count",
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
        """Prepare a compact destination count and detailed page links.

        Returns:
            Target links, count, and localized separators.
        """
        targets = list(definition.get("pages", {}).values()) or definition.get(
            "prefixes",
            [],
        )
        multiple = len(targets) > 1
        return {
            "name": name,
            "multiple": multiple,
            "count": self.text("ops.page_count", count=len(targets)),
            "opening": " (" if self.interface.locale == "en" else "\uff08",
            "closing": ")" if self.interface.locale == "en" else "\uff09",
            "targets": [
                wiki_link(
                    title_url(title),
                    (
                        self.text(f"report.{title.rsplit('/', 1)[-1]}")
                        if f"report.{title.rsplit('/', 1)[-1]}" in catalog()
                        else title.rsplit("/", 1)[-1]
                    )
                    if multiple
                    else name,
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
            "outcome": wiki_link(
                "https://"
                + ("zh" if record["site"] == "zhwiki" else "en")
                + ".wikipedia.org/w/index.php?oldid="
                + str(record["revision_id"]),
                self._outcome_label(record),
            )
            if record.get("revision_id") and record["status"] == "updated"
            else self._outcome_label(record),
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

    def routine_rows(
        self,
        status: Record,
        definitions: Record,
        schedule: Record,
        now: dt.datetime,
    ) -> list[Record]:
        """Prepare actual outcomes and independent configured schedules.

        Returns:
            Routine rows with progress and destination details.
        """
        events = (status.get("routines") or {}).get("routines", {})
        records = (status.get("reports") or {}).get("reports", {}).values()
        rows = []
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
            routine_schedule = schedule["routines"].get(identifier, {})
            upcoming = (
                routine_schedule.get("next_run_at")
                if not routine_schedule.get("queued_since")
                else None
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
            rows.append({
                "identifier": identifier,
                "icon": _REPORT_ICONS.get(identifier, "article"),
                "links": self._routine_links(name, definition),
                "running": running,
                "status": "updated"
                if completed_daily
                else (event or {}).get("status", "unknown"),
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
        return rows

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
            "routines": self.routine_rows(status, definitions, schedule, now),
            "footer": {
                "label": uptime(schedule, version, self.interface, now),
                "started": started,
                "template": self.text(
                    "footer.uptime",
                    version=version,
                    duration=self.text(
                        "duration.days",
                        days="{days}",
                        hours="{hours}",
                        minutes="{minutes}",
                        seconds="{seconds}",
                    ),
                    since=utc_time(started),
                )
                if started
                else None,
                "compact_template": self.text(
                    "footer.uptime",
                    version=version,
                    duration="{duration}",
                    since=utc_time(started),
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
            "runtime": self.runtime(status, schedule, version),
        }

    def runtime(
        self,
        status: Record,
        schedule: Record,
        version: str,
    ) -> list[Record]:
        """Prepare version details and fresh resource observations.

        Returns:
            Card values with unknowns for missing resource facts.
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
        groups = (
            (
                "application",
                (
                    ("web_version", version, False),
                    (
                        "hub_version",
                        runtime.get("aranami_version") or unknown,
                        False,
                    ),
                ),
            ),
            (
                "memory",
                (("memory", f"{used_memory} / {allocated_memory}", True),),
            ),
            (
                "cpu",
                (("cpu", f"{cpu_use}{separator}{cpu_cores}", True),),
            ),
        )
        return [
            {
                "key": group,
                "label": self.text(
                    "server.group.application"
                    if group == "application"
                    else f"server.{group}",
                ),
                "rows": [
                    {
                        "label": self.text(f"server.{key}"),
                        "value": value,
                        "fresh": usage and fresh,
                        "stale": unknown if usage else None,
                    }
                    for key, value, usage in rows
                ],
            }
            for group, rows in groups
        ]

    def worklist_card(
        self,
        item: Record,
        groups: Record,
        task: str = "",
    ) -> Record:
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
                if key in groups and key != task
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


def render_dashboard(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
    *,
    navigation: Record | None = None,
) -> str:
    """Render the dashboard from independent saved observations.

    Args:
        snapshot: Validated website snapshot, or unavailable data.
        status: Validated routine, schedule, and page observations.
        config: Current source version and routine definitions.
        locale: Negotiated interface language.
        now: Optional aware clock for deterministic elapsed time.
        navigation: Request route and language choices, when available.

    Returns:
        Complete escaped HTML document, including unavailable states.
    """
    page = _Page(locale)
    context = page.context(page.text("page.heading"), live=True)
    context["navigation"] = navigation
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
        reviews=page.reviews(snapshot or {}, now or dt.datetime.now(dt.UTC)),
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


def render_maintenance_overview(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    status: Record | None,
    config: Record | None,
    locale: str,
    now: dt.datetime | None = None,
    *,
    navigation: Record | None = None,
) -> str:
    """Render saved maintenance categories on their own overview page.

    Args:
        snapshot: Validated website snapshot containing the catalogue.
        status: Validated routine, schedule, and page observations.
        config: Current source version and routine definitions.
        locale: Negotiated interface language.
        now: Optional aware clock for deterministic elapsed time.
        navigation: Request route and language choices, when available.

    Returns:
        Complete escaped HTML with category links and source timestamps,
        preserving unavailable and confirmed-empty observations.
    """
    page = _Page(locale)
    context = page.context(page.text("backlog.heading"), live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    backlog = (snapshot or {}).get("backlog")
    context.update(
        navigation=navigation,
        backlog=page.backlog(backlog),
        updated=page.source_updated(backlog["observed_at"])
        if backlog is not None
        else None,
    )
    return _render("backlog.html", context)


def render_maintenance(  # ruff: ignore[too-many-arguments]
    snapshot: Record | None,
    params: Mapping[str, list[str]],
    locale: str,
    *,
    navigation: Record | None = None,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Render a maintenance list after sorting all saved observations.

    Args:
        snapshot: Validated website snapshot containing the catalogue.
        params: Parsed controls with repeated values retained.
        locale: Negotiated interface language.
        navigation: Request-specific language links and active location.
        status: Saved service state for the shared uptime footer.
        config: Source version and routine definitions.
        now: Optional clock for deterministic uptime.

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
    if selection["sort"] == "random" and "seed" not in selection:
        selection["seed"] = secrets.token_hex(8)
    selected = groups[selection["task"]]
    items = maintenance_items(
        maintenance_filter(selected["items"], selection),
        selection["sort"],
        selection["order"],
        selection.get("seed", ""),
    )
    portion = maintenance_page(
        items,
        selection["page"],
        int(selection.get("limit", "10")),
    )
    if navigation:
        navigation = {
            **navigation,
            "languages": [
                {
                    **language,
                    "url": local_url(
                        "/maintenance/",
                        **selection,
                        lang=language["tag"].lower(),
                    ),
                }
                for language in navigation["languages"]
            ],
        }
    page = _Page(locale)
    heading = page.interface.label(selected.get("label"), selected["category"])
    context = page.context(heading, live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    context.update(
        navigation=navigation,
        count=page.text("backlog.count", count=f"{selected['count']:,}"),
        selection=selection,
        task_choices=[
            {
                "value": key,
                "label": page.interface.label(
                    group.get("label"),
                    group["category"],
                ),
                "current": key == selection["task"],
            }
            for key, group in groups.items()
        ],
        assessment_choices={
            control: [
                {
                    "value": code,
                    "label": page.text(f"backlog.grade.{dimension}.{code}"),
                    "current": code == selection.get(control),
                }
                for code in codes
            ]
            for control, dimension, codes in (
                (
                    "quality",
                    "class",
                    (
                        "FA",
                        "A",
                        "GA",
                        "Bplus",
                        "B",
                        "C",
                        "Start",
                        "Stub",
                        "Unassessed",
                    ),
                ),
                (
                    "importance",
                    "importance",
                    ("Top", "High", "Mid", "Low", "Unknown"),
                ),
            )
        },
        clear_url=local_url(
            "/maintenance/",
            task=selection["task"],
        ),
        range_label=page.text(
            "backlog.range",
            start=portion["offset"] + 1 if items else 0,
            end=portion["offset"] + len(portion["items"]),
            count=f"{len(items):,}",
        ),
        updated=page.source_updated(backlog["observed_at"]),
        sort_choices=[
            {
                "label": page.text(key),
                "value": sort,
                "current": sort == selection["sort"],
            }
            for sort, key in (
                ("title", "backlog.sort.title"),
                ("class", "backlog.sort.class"),
                ("importance", "backlog.column.importance"),
                ("oldest", "backlog.sort.time"),
                ("random", "backlog.sort.random"),
                ("size", "backlog.sort.size"),
            )
        ],
        direction_choices=[
            {
                "value": order,
                "label": page.text(
                    f"backlog.direction.{order}",
                ),
                "current": order == selection["order"],
            }
            for order in ("asc", "desc")
        ],
        cards=[
            page.worklist_card(item, groups, selection["task"])
            for item in portion["items"]
        ],
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
        source_link=wiki_link(selected["source_url"], selected["category"]),
    )
    return _render("maintenance.html", context)


def _newsletter_edit_url(title: str, data_through: str | None) -> str:
    """Link to the wiki editor with dated Chinese source attribution.

    Args:
        title: Newsletter page receiving the reviewed draft.
        data_through: Inclusive date of the draft's saved observations.

    Returns:
        Edit-form URL with an attributed summary for manual review.
    """
    day = dt.date.fromisoformat(data_through) if data_through else None
    label = f"{day.year}年{day.month}月{day.day}日资料" if day else "简讯资料"
    query = urlencode({
        "action": "edit",
        "summary": f"[[toolforge:aranami/newsletter/|{label}]]",
    })
    return f"{title_url(title)}?{query}"


def render_newsletter(  # ruff: ignore[too-many-arguments]
    value: Record,
    locale: str,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
    navigation: Record | None = None,
) -> str:
    """Present saved newsletter drafts by issue and data date.

    Args:
        value: Issue, reporting period, drafts, source observations,
            and optional daily draft history.
        locale: Negotiated interface language; wikitext stays Chinese.
        status: Saved monitor observations for the shared footer.
        config: Current application version and routine definitions.
        now: Optional server clock for observed uptime.
        navigation: Request route and language choices, when available.

    Returns:
        Escaped HTML with native issue/date selection and copy controls.
    """
    page = _Page(locale)
    context = page.context(page.text("newsletter.heading"), live=True)
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    labels = {
        "主頁": "main",
        "特優內容": "quality",
        "宣告": "announcements",
        "專題": "feature",
        "訪談": "interview",
    }
    automatic = value.get("automatic_issue", value["issue"])
    previous = Issue.parse(str(automatic)).previous
    choices = value.get("issue_choices", [previous, automatic])
    history = value.get("draft_history") or {}
    issue = Issue.parse(str(value["issue"]))
    dates = sorted(history.get("dates") or [])
    data_through = history.get("selected_date") or value.get("data_through")
    context.update(
        navigation=navigation,
        available=value["available"],
        locale=page.interface.locale,
        selected_issue=value["issue"],
        archived_issue=value["issue"] not in choices,
        draft_history={
            "dates": dates,
            "selected": history.get("selected_date"),
            "form_date": history.get("selected_date")
            or (dates[-1] if dates else ""),
            "minimum": f"{issue.year - 1}-01-01"
            if issue.month == 1
            else issue.start.isoformat(),
            "maximum": (issue.end - dt.timedelta(days=1)).isoformat(),
            "generated": _time(history.get("generated_at"), seconds=True),
            "missing": history.get("missing", False),
        },
        issue_choices=[
            {
                "value": choice,
                "checked": choice == value["issue"],
                "label": page.text(
                    "newsletter.choice.current"
                    if choice == automatic
                    else "newsletter.choice.previous"
                    if choice == previous
                    else "newsletter.choice.annual"
                    if str(choice).endswith("-01") and choice != value["issue"]
                    else "newsletter.choice.selected",
                ),
            }
            for choice in choices
        ],
        drafts=[
            {
                **draft,
                "heading": page.text(
                    "newsletter.page."
                    + labels[draft["label"].lstrip("/") or "主頁"],
                ),
                "edit_url": _newsletter_edit_url(
                    draft["title"],
                    draft.get("data_through") or data_through,
                ),
            }
            for draft in value["pages"]
            if value["available"]
        ],
    )
    return _render("newsletter.html", context)


def render_about(
    config: Record | None,
    locale: str,
    *,
    status: Record | None = None,
    now: dt.datetime | None = None,
    navigation: Record | None = None,
) -> str:
    """Explain the tool and credit its sources and artwork.

    Args:
        config: Current application version and routine configuration.
        locale: Negotiated interface language.
        status: Validated saved monitor and resource observations.
        now: Optional clock for deterministic freshness checks.
        navigation: Request route and language choices, when available.

    Returns:
        An informative About page with a live footer uptime.
    """
    page = _Page(locale)
    context = page.context(page.text("nav.about"), live=True)
    context["navigation"] = navigation
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    context.update(
        introduction=escape(page.text("about.intro")).format(
            name="荒波",
            wikipedia=wiki_link(
                title_url("中文维基百科"),
                page.text("about.wikipedia"),
            ),
            project=wiki_link(
                title_url("WikiProject:电子游戏"),
                page.text("about.project"),
            ),
        ),
        contact=escape(page.text("about.contact")).format(
            source=Markup(
                '<a href="https://github.com/For-Each-Next/aranami">GitHub</a>',
            ),
            user=wiki_link(
                "https://zh.wikipedia.org/wiki/User_talk:For_Each_..._Next",
                "For Each ... Next",
            ),
        ),
    )
    return _render("about.html", context)


def render_logs(  # ruff: ignore[too-many-arguments]
    data: Record,
    locale: str,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
    navigation: Record | None = None,
) -> str:
    """Render diagnostic file downloads with shared footer uptime.

    Args:
        data: Validated daily-file listing and selection.
        locale: Negotiated interface language.
        status: Saved monitor observations for the shared footer.
        config: Current source version and routine definitions.
        now: Optional clock for deterministic uptime.
        navigation: Request route and language choices.

    Returns:
        A file archive without interpreting diagnostic text.
    """
    page = _Page(locale)
    context = page.context(page.text("logs.heading"), live=True)
    context.update(navigation=navigation)
    context.update(_log_archive(data))
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    return _render("logs.html", context)


def render_reports(  # ruff: ignore[too-many-arguments]
    data: Record,
    locale: str,
    *,
    status: Record | None = None,
    config: Record | None = None,
    now: dt.datetime | None = None,
    navigation: Record | None = None,
) -> str:
    """Render saved update status and per-invocation report history.

    Args:
        data: Validated independent invocations.
        locale: Negotiated interface language.
        status: Validated routine and scheduler observations.
        config: Current source version and routine definitions.
        now: Optional clock for deterministic freshness checks.
        navigation: Request route and language choices.

    Returns:
        A status panel and recent invocation history with stage dialogs.
    """
    page = _Page(locale)
    context = page.context(page.text("reports.heading"), live=True)
    context.update(navigation=navigation)
    context.update(history_context(page.interface, data))
    context.update(
        page.live_status(
            status or {},
            config or {},
            now or dt.datetime.now(dt.UTC),
        ),
    )
    return _render("reports.html", context)


def _log_archive(data: Record) -> Record:
    """Prepare diagnostic file links without interpreting log content.

    Returns:
        File metadata, selected date, and separate download links.
    """
    files = data.get("files", [])
    selected_date = data.get("date") or (files[0]["date"] if files else None)
    return {
        "files": [
            {
                **row,
                "size_label": readable_size(row["size"]),
                "url": local_url("/logs/", date=row["date"]),
                "download_url": local_url(
                    "/logs/",
                    date=row["date"],
                    download=1,
                ),
            }
            for row in files
        ],
        "selected_date": selected_date,
        "selected_download": local_url(
            "/logs/",
            date=selected_date,
            download=1,
        )
        if selected_date
        else None,
    }


def render_error(message_key: str, locale: str) -> str:
    """Show a localized failure with working site navigation.

    Returns:
        An escaped HTML document with a localized alert.
    """
    page = _Page(locale)
    context = page.context(page.text("page.heading"))
    context["message"] = page.text(message_key)
    return _render("error.html", context)
