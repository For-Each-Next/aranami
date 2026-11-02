"""Verify presentation contracts and render synthetic browser fixtures.

Run without options for the offline tests. Use ``--render-fixtures``
with a destination directory to write localized browser-check pages.
"""

# Keep unittest exceptions and expected fixture counts.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import sys
import tempfile
import unittest
from hashlib import sha256
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import polars as pl
from test_maintenance_controls import (
    _snapshot as maintenance_snapshot,  # ruff: ignore[import-private-name]
)

from shared.config import routine_configuration
from shared.deployment import resource_root
from webservice.pages import (
    render_about,
    render_dashboard,
    render_error,
    render_live_status,
    render_logs,
    render_maintenance,
    render_maintenance_overview,
    render_newsletter,
    render_reports,
)
from webservice.presentation import (
    Interface,
    article_extract,
    assessment_code,
    assessment_rank,
    catalog,
    duration,
    event_duration,
    homepage_sections,
    local_url,
    maintenance_items,
    maintenance_page,
    maintenance_sections,
    maintenance_selection,
    merged_items,
    next_run,
    pageviews_url,
    readable_size,
    relative_time,
    schedule_state,
    select_locale,
    timestamp,
    title_url,
    uptime,
    utc_time,
    wiki_link,
    wiki_url,
)


def _fixtures() -> dict[str, object]:
    """Build saved observations with hostile text and all sections.

    Returns:
        Independent website, routine, scheduler, and source fixtures.
    """
    stamp = "2026-10-18T12:00:00Z"
    definition = {
        "pages": {"report": "WikiProject:电子游戏/热门条目"},
        "prefixes": [],
        "hour": "*",
        "minute": 59,
    }
    config = {
        "version": "0.6.0.dev1",
        "routines": {"schema": 1, "routines": {"pageviews": definition}},
    }
    routine = {
        "routine": "pageviews",
        "status": "deferred",
        "dry": False,
        "updated": 0,
        "duration_seconds": 12.5,
        "started_at": "2026-10-18T11:59:47Z",
        "finished_at": stamp,
        "run_id": "a" * 32,
    }
    schedule = {
        "schema": 1,
        "updated_at": stamp,
        "state": "running",
        "config_id": "b" * 64,
        "started_at": "2026-10-16T08:56:00Z",
        "routines": {
            "pageviews": {
                **definition,
                "state": "scheduled",
                "next_run_at": "2026-10-20T18:59:00Z",
            },
        },
    }
    report = {
        "update_id": "c" * 32,
        "run_id": "a" * 32,
        "routine": "pageviews",
        "site": "zhwiki",
        "title": "WikiProject:电子游戏/热门条目 <Latest & safe>",
        "status": "updated",
        "dry": False,
        "started_at": "2026-10-18T11:59:55Z",
        "finished_at": stamp,
        "duration_seconds": 5,
        "confirmed_edits": 1,
    }
    status = {
        "routines": {
            "schema": 1,
            "updated_at": stamp,
            "routines": {"pageviews": routine},
        },
        "schedule": schedule,
        "reports": {
            "schema": 1,
            "updated_at": stamp,
            "reports": {"first": report},
        },
    }
    article = {
        "title": "Game & Characters / 中文",
        "url": "https://zh.wikipedia.org/wiki/Game",
        "extract": "简介 <script>never execute</script> & 中文。第二句。",
        "extract_hant": "簡介 <script>never execute</script> & 中文。第二句。",
        "extract_zh": "原文遊戲与音乐\uff0c保留原始简介。",
        "views": 723,
        "rank": 1,
        "site_rank": 681,
        "assessment_class": "GA",
        "assessment_importance": "High",
        "date": "2026-10-17",
        "kind": "fa",
    }
    maintenance = {
        "page_id": 17,
        "title": "Maintenance <&> article",
        "url": "https://zh.wikipedia.org/wiki/Maintenance",
        "assessment_class": "B",
        "assessment_importance": "High",
        "issues": ["references"],
        "oldest_issue_month": "2020-03",
        "oldest_issue_category": "自2020年03月需补充来源的条目",
    }
    group = {
        "key": "references",
        "category": "缺少来源的条目",
        "source_url": "https://zh.wikipedia.org/wiki/Category:References",
        "section_key": "sources",
        "section_label": {
            "en": "References",
            "zh-hans": "参考来源",
            "zh-hant": "參考來源",
        },
        "label": {
            "en": "Add citations",
            "zh-hans": "补充来源",
            "zh-hant": "補充來源",
        },
        "count": 1,
        "items": [maintenance],
    }
    snapshot = {
        "schema": 2,
        "updated_at": stamp,
        "reports": {
            key: {"last_success_at": stamp}
            for key in (
                "recognized",
                "dyk",
                "pageviews",
                "news_history",
                "backlog",
            )
        },
        "homepage": {
            "featured": [article],
            "good": [{**article, "title": "Good game", "kind": "ga"}],
            "upcoming": [
                {
                    **article,
                    "title": "Scheduled game",
                    "date": "2026-10-20",
                    "date_kind": "scheduled",
                },
            ],
            "dyk": [
                {**article, "title": f"DYK game {index}", "kind": "dyk"}
                for index in range(1, 14)
            ],
            "news": [
                {**article, "title": f"News game {index}", "kind": "news"}
                for index in range(1, 8)
            ],
            "history": [
                {**article, "title": "Historic game", "kind": "history"},
            ],
        },
        "pageviews": {
            "date": "2026-10-17",
            "limit": 10,
            "scope": "site_top_1000",
            "items": [article],
        },
        "backlog": {"observed_at": stamp, "total": 1, "groups": [group]},
        "errors": [],
    }
    return {
        "config": config,
        "status": status,
        "snapshot": snapshot,
        "article": article,
    }


def write_interface_fixtures(destination: Path) -> None:
    """Write localized synthetic pages for offline browser verification.

    The disposable output includes every review type and hostile or long
    text. It contains no runtime data and performs no external requests.

    Args:
        destination: Directory receiving localized browser-check pages.
    """
    fixture = _fixtures()
    fixture["config"]["routines"] = routine_configuration()
    fixture["status"]["schedule"]["runtime"] = {
        "python": "3.15.0",
        "system": "Linux",
        "machine": "x86_64",
        "os_release": "Debian GNU/Linux 13 (trixie)",
        "kernel": "6.12.0-example-amd64",
        "python_implementation": "CPython",
        "python_compiler": "CompilerWithLongUnbrokenBuildIdentifier" * 2,
        "aranami_version": "0.8.5",
        "memory_bytes": 104_857_600,
        "memory_limit_bytes": 4_294_967_296,
        "cpu_percent": 12.5,
        "cpu_limit": 2,
    }
    snapshot = fixture["snapshot"]
    maintenance = maintenance_snapshot()
    article = snapshot["pageviews"]["items"][0]
    article["title"] = "LongUnbrokenArticleIdentifier" * 7
    article["assessment_class"] = None
    article["assessment_importance"] = "unrecognized value"
    article["extract"] = "A longer source introduction with context. " * 20
    article["extract_zh"] = "保留中文源文与标点的简介。" * 30
    article["extract_hant"] = "保留中文來源與標點的簡介。" * 30
    snapshot["reports"]["reviews"] = {
        "last_success_at": "2026-10-18T12:00:00Z",
    }
    snapshot["reviews"] = {
        "items": [
            {
                **article,
                "title": f"Review {kind.upper()} game",
                "review_type": kind,
                "review_url": (
                    f"https://zh.wikipedia.org/wiki/Talk:Game#Review_{kind}"
                ),
                "started_at": "2026-06-08T12:00:00Z"
                if kind == "fac"
                else "2026-10-07T03:04:00Z",
                "replied_at": "2026-06-08T12:00:00Z"
                if kind == "fac"
                else "2026-10-18T10:00:00Z",
                "participant_count": 9 if kind == "fac" else 3,
                "extract": "An article introduction for this review.",
                "extract_zh": "用于评审卡片的条目简介。",
                "extract_hant": "用於評審卡片的條目簡介。",
            }
            for kind in (
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
        ],
    }
    maintenance_rows = maintenance["backlog"]["groups"][0]["items"]
    maintenance_rows[0]["title"] = "LongUnbrokenArticleIdentifier" * 7
    maintenance_rows[1]["title"] = "含有較長中文名稱的遊戲條目" * 5
    maintenance_rows[1]["oldest_issue_month"] = "2019-01"
    fixture["status"]["reports"]["reports"]["first"]["title"] = (
        "WikiProject:电子游戏/" + "LongReportIdentifier" * 9
    )
    now = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)
    events = [
        {
            "schema": 1,
            "kind": "routine_finished",
            "time": "2026-10-18T12:00:02Z",
            "routine": "pageviews",
            "dry": False,
            "status": "updated",
            "updated": 1,
            "duration_seconds": 73.7,
        },
        {
            "schema": 1,
            "kind": "routine_finished",
            "time": "2026-10-18T11:00:02Z",
            "routine": "pageviews",
            "dry": False,
            "status": "unknown",
            "updated": None,
            "duration_seconds": 6.83,
        },
    ]
    logs = {
        "files": [
            {
                "date": "2026-10-18",
                "name": "aranami.2026-10-18.log",
                "size": 1024,
            },
        ],
        "date": "2026-10-18",
        "events": events,
        "dates": ["2026-10-18"],
        "runs": [
            {
                "run_id": f"{index:032x}",
                "routine": event["routine"],
                "started_at": event["time"],
                "finished_at": event["time"],
                "status": event["status"],
                "duration_seconds": event["duration_seconds"],
                "truncated": False,
                "stages": [
                    {"kind": "started", "time": event["time"], "title": ""},
                    {
                        "kind": "database_updated",
                        "time": event["time"],
                        "title": "WikiProject:电子游戏/典范条目",
                    },
                    {"kind": "finished", "time": event["time"], "title": ""},
                ],
            }
            for index, event in enumerate(events)
        ],
        "query": "",
    }
    latest_run = logs["runs"][0]
    fixture["status"]["routines"]["routines"]["pageviews"].update(
        run_id=latest_run["run_id"],
        finished_at=latest_run["finished_at"],
    )
    fixture["status"]["reports"]["reports"]["first"]["run_id"] = latest_run[
        "run_id"
    ]
    fixture["status"]["history"] = logs
    stored = destination / "reports/data/assessments/zhwiki/Video games"
    stored.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "page_id": [1],
        "pa_class": ["GA"],
        "pa_importance": ["Mid"],
    }).write_parquet(stored / "assessments-20260808.parquet")
    for locale in ("en", "zh-hans", "zh-hant"):
        directory = destination / locale
        directory.mkdir(parents=True, exist_ok=True)
        documents = {
            "home": render_dashboard(
                snapshot,
                fixture["status"],
                fixture["config"],
                locale,
                now,
            ),
            "backlog": render_maintenance_overview(
                snapshot,
                fixture["status"],
                fixture["config"],
                locale,
                now,
            ),
            "maintenance": render_maintenance(
                maintenance,
                {"task": ["references"]},
                locale,
            ),
            "reports": render_reports(
                logs,
                locale,
                status=fixture["status"],
                config=fixture["config"],
                now=now,
            ),
            "logs": render_logs(
                logs,
                locale,
                status=fixture["status"],
                config=fixture["config"],
                now=now,
            ),
            "about": render_about(
                fixture["config"],
                locale,
                status=fixture["status"],
                now=now,
            ),
            "error": render_error("backlog.not_found", locale),
            "newsletter": render_newsletter(
                {
                    "issue": "2027-01",
                    "available": True,
                    "draft_history": {
                        "dates": [
                            "2026-01-01",
                            "2026-02-28",
                            "2026-10-29",
                            "2026-10-30",
                        ],
                        "selected_date": "2026-10-30",
                        "generated_at": "2026-10-31T00:04:05+00:00",
                        "missing": False,
                    },
                    "pages": [
                        {
                            "title": "WikiProject:電子遊戲/簡訊/2027-01"
                            + suffix,
                            "url": "https://zh.wikipedia.org/wiki/Fixture",
                            "label": suffix.lstrip("/") or "主頁",
                            "text": "\n{{模板|標題}}\r\n"
                            + "長條目名稱" * 100
                            + "\n</textarea><script>literal</script>\n",
                        }
                        for suffix in ("", "/特優內容", "/宣告", "/專題")
                    ],
                },
                locale,
            ),
        }
        for surface, document in documents.items():
            (directory / f"{surface}.html").write_text(document)


class ReviewPresentationTests(unittest.TestCase):
    """Render review cards from saved observations in every language."""

    def setUp(self) -> None:
        """Create saved observations and a deterministic clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.en = Interface("en")

    def test_times_omit_zone_labels_and_keep_source_instants(self) -> None:
        """Convert offset observations and preserve machine instants."""
        source = "2026-10-18T16:00:07+08:00"
        snapshot = self.data["snapshot"]
        snapshot["reviews"] = {
            "items": [
                {
                    **self.data["article"],
                    "review_type": "acc",
                    "review_url": "https://zh.wikipedia.org/wiki/Talk:Game",
                    "started_at": source,
                },
            ],
        }
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                dashboard = render_dashboard(
                    snapshot,
                    self.data["status"],
                    self.data["config"],
                    locale,
                    self.now,
                )
                assert (
                    f'<time datetime="{source}" '
                    'title="2026-10-18 08:00:07">' in dashboard
                )
                documents = (
                    dashboard,
                    render_about(
                        self.data["config"],
                        locale,
                        status=self.data["status"],
                        now=self.now,
                    ),
                    render_reports(
                        {"runs": []},
                        locale,
                        status=self.data["status"],
                        config=self.data["config"],
                        now=self.now,
                    ),
                )
                for document in documents:
                    visible = unescape(re.sub(r"<[^>]+>", "", document))
                    assert "UTC" not in visible
                    assert all(
                        "UTC" not in title
                        for title in re.findall(r'title="([^"]+)"', document)
                    )

    def test_review_cards_precede_highlights_in_every_language(self) -> None:
        """Show localized review cards with observed durations."""
        snapshot = self.data["snapshot"]
        snapshot["reports"]["reviews"] = {
            "last_success_at": "2026-10-18T12:00:00Z",
        }
        snapshot["reviews"] = {
            "items": [
                {
                    **self.data["article"],
                    "title": f"Review {index} <&>",
                    "review_type": kind,
                    "review_url": f"https://zh.wikipedia.org/wiki/Talk:Game#Review_{index}",
                    "started_at": "2026-10-07T03:04:00Z"
                    if index == 0
                    else "2026-10-18T10:00:00Z"
                    if index == 1
                    else None,
                    "replied_at": "2026-10-16T12:00:30Z"
                    if index == 0
                    else "2026-10-18T10:00:00Z"
                    if index == 1
                    else None,
                }
                for index, kind in enumerate((
                    "fac",
                    "flc",
                    "acc",
                    "gan",
                    "bpan",
                    "pr",
                    "ppr",
                    "dykc",
                    "reassess",
                ))
            ],
        }
        for locale in ("en", "zh-hans", "zh-hant", "zh"):
            with self.subTest(locale=locale):
                interface = Interface(locale)
                rendered = render_dashboard(snapshot, {}, {}, locale, self.now)
                assert rendered.index('id="reviews-heading"') < rendered.index(
                    'id="homepage-pageviews"',
                )
                section = rendered.split('id="reviews"', 1)[1].split(
                    "</section>",
                    1,
                )[0]
                assert (
                    '<h2 id="reviews-heading">'
                    f"{interface.text('reviews.heading')}</h2>" in section
                )
                assert section.count('class="article-card"') == 9
                assert 'class="card-grid article-list review-cards"' in section
                assert (
                    section.count('class="review-icon-link cdx-card__icon"')
                    == 9
                )
                assert section.count('class="review-icon"') == 9
                link_label = {
                    "en": "Review",
                    "zh-hant": "評審區",
                }.get(locale, "评审区")
                for index in range(9):
                    assert (
                        f'href="https://zh.wikipedia.org/wiki/Talk:Game#Review_{index}">'
                        f"{link_label}</a>" in section
                    )
                metadata = re.findall(
                    r'<div class="card-meta review-meta">(.*?)</div>',
                    section,
                    re.DOTALL,
                )
                assert len(metadata) == 9
                for index, details in enumerate(metadata):
                    assert ('<ul class="metadata-items"' in details) == (
                        index < 2
                    )
                    if index >= 2:
                        assert f"{link_label}</a></span>" in details
                assert "https://commons.wikimedia.org/wiki/File:" in section
                assert "/assets/icons/featured-candidate.svg" in section
                assert "/assets/icons/peer-review.svg" in section
                assert "/assets/icons/project-peer-review.svg" in section
                assert re.search(
                    r"/assets/icons/report-icons\.svg\?v=[a-f0-9]{64}#history",
                    section,
                )
                assert "highlights" not in section
                review_times = re.findall(
                    r'<time datetime="([^"]+)" title="([^"]+)">([^<]+)</time>',
                    section,
                )
                assert review_times == [
                    (
                        "2026-10-07T03:04:00Z",
                        "2026-10-07 03:04:00",
                        interface.text("reviews.started_days", count=11),
                    ),
                    (
                        "2026-10-16T12:00:30Z",
                        "2026-10-16 12:00:30",
                        interface.text("reviews.replied_days", count=2),
                    ),
                    (
                        "2026-10-18T10:00:00Z",
                        "2026-10-18 10:00:00",
                        interface.text("reviews.started_hours", count=2),
                    ),
                    (
                        "2026-10-18T10:00:00Z",
                        "2026-10-18 10:00:00",
                        interface.text("reviews.replied_hours", count=2),
                    ),
                ]
                assert "Review 0 &lt;&amp;&gt;" in section
                if locale != "zh":
                    assert (
                        "&lt;script&gt;never execute&lt;/script&gt;" in section
                    )
                assert "#Review_0" in section
                assert 'data-assessment="class"' in section
                assert 'data-assessment="importance"' in section
                assert 'href="#reviews"' in rendered

    def test_review_reply_boundaries_and_missing_observations(self) -> None:
        """Keep reply age units and tooltips tied to saved replies."""
        row = {
            **self.data["article"],
            "review_type": "fac",
            "review_url": "https://zh.wikipedia.org/wiki/Talk:Game#Review",
            "started_at": "2026-10-07T03:04:00Z",
        }
        snapshot = self.data["snapshot"]
        snapshot["reviews"] = {"items": [row]}
        for locale in ("en", "zh-hans", "zh-hant", "zh"):
            interface = Interface(locale)
            for hours in (0, 23, 24, 48, None):
                with self.subTest(locale=locale, hours=hours):
                    if hours is None:
                        row.pop("replied_at", None)
                    else:
                        row["replied_at"] = (
                            self.now - dt.timedelta(hours=hours)
                        ).isoformat()
                    rendered = render_dashboard(
                        snapshot,
                        {},
                        {},
                        locale,
                        self.now,
                    )
                    section = rendered.split('id="reviews"', 1)[1].split(
                        "</section>",
                        1,
                    )[0]
                    if hours is None:
                        assert section.count("<time") == 1
                        continue
                    assert section.count("<time") == 2
                    label = interface.text(
                        "reviews.replied_days"
                        if hours >= 24
                        else "reviews.replied_hours"
                        if hours
                        else "reviews.replied_minutes",
                        count=hours // 24 if hours >= 24 else hours,
                    )
                    assert (
                        f'<time datetime="{row["replied_at"]}" '
                        f'title="{utc_time(row["replied_at"], seconds=True)}">'
                        f"{label}</time>" in section
                    )

    def test_review_months_icons_and_footer_separators(self) -> None:
        """Expose accessible icons and localized review metadata."""
        snapshot = self.data["snapshot"]
        snapshot["reviews"] = {
            "items": [
                {
                    **self.data["article"],
                    "review_type": "acc",
                    "review_url": "https://zh.wikipedia.org/wiki/Talk:Game#Review",
                    "started_at": "2026-02-05T14:15:00Z",
                    "replied_at": "2026-10-17T11:30:00Z",
                    "participant_count": 3,
                },
            ],
        }
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                interface = Interface(locale)
                output = render_dashboard(snapshot, {}, {}, locale, self.now)
                label = interface.text("reviews.acc")
                assert f'title="{label}"' in output
                assert f'alt="{label}"' in output
                assert (
                    interface.text("reviews.started_months", count=8) in output
                )
                assert (
                    interface.text("reviews.replied_days", count=1) in output
                )
                metadata = re.search(
                    r'<div class="card-meta review-meta">(.*?)</div>',
                    output,
                    re.DOTALL,
                )
                assert metadata is not None
                items = re.findall(
                    r"<li\b[^>]*>(.*?)</li>",
                    metadata[1],
                    re.DOTALL,
                )
                assert len(items) == 3
                link_label = {
                    "en": "Review",
                    "zh-hans": "评审区",
                    "zh-hant": "評審區",
                }[locale]
                lead = re.search(
                    r'<span class="card-meta-lead">(.*?)</span>',
                    metadata[1],
                )[1]
                punctuation = ": " if locale == "en" else "\uff1a"
                assert lead.endswith(f"{link_label}</a>{punctuation}")
                assert '<ul class="metadata-items" role="list">' in metadata[1]
                assert (
                    interface.text("reviews.started_months", count=8)
                    in items[0]
                )
                assert (
                    interface.text("reviews.replied_days", count=1) in items[1]
                )
                assert (
                    interface.text("reviews.participants", count=3) in items[2]
                )
                assert " · " not in metadata[1]
                assert " | " not in metadata[1]

    def test_review_ages_keep_fixed_units_and_exact_timestamps(
        self,
    ) -> None:
        """Abbreviate English ages while retaining exact hover times."""
        row = {
            **self.data["article"],
            "review_type": "fac",
            "review_url": "https://zh.wikipedia.org/wiki/Talk:Game#Review",
        }
        snapshot = self.data["snapshot"]
        snapshot["reviews"] = {"items": [row]}
        month = dt.timedelta(days=365.25 / 12)
        for locale in ("en", "zh-hans", "zh-hant", "zh"):
            interface = Interface(locale)
            for elapsed, unit, count, compact in (
                (dt.timedelta(seconds=0), "minutes", 0, "0m"),
                (dt.timedelta(seconds=59), "minutes", 0, "0m"),
                (dt.timedelta(minutes=1), "minutes", 1, "1m"),
                (dt.timedelta(minutes=59), "minutes", 59, "59m"),
                (dt.timedelta(hours=1), "hours", 1, "1h"),
                (dt.timedelta(hours=23), "hours", 23, "23h"),
                (dt.timedelta(days=1), "days", 1, "1d"),
                (month - dt.timedelta(seconds=1), "days", 30, "30d"),
                (month, "months", 1, "1mo"),
                (month + dt.timedelta(seconds=1), "months", 1, "1mo"),
                (2 * month, "months", 2, "2mo"),
                (dt.timedelta(days=132), "months", 4, "4mo"),
                (8 * month, "months", 8, "8mo"),
                (12 * month, "months", 12, "12mo"),
            ):
                with self.subTest(locale=locale, elapsed=elapsed):
                    instant = (self.now - elapsed).isoformat()
                    row.update(started_at=instant, replied_at=instant)
                    output = render_dashboard(
                        snapshot,
                        {},
                        {},
                        locale,
                        self.now,
                    )
                    review_times = re.findall(
                        rf'<time datetime="{re.escape(instant)}" '
                        r'title="([^"]+)">([^<]+)</time>',
                        output,
                    )
                    tooltip = utc_time(instant, seconds=True)
                    assert review_times == [
                        (
                            tooltip,
                            f"Started {compact} ago"
                            if locale == "en"
                            else interface.text(
                                f"reviews.started_{unit}",
                                count=count,
                            ),
                        ),
                        (
                            tooltip,
                            f"Last comment {compact} ago"
                            if locale == "en"
                            else interface.text(
                                f"reviews.replied_{unit}",
                                count=count,
                            ),
                        ),
                    ]

    def test_participant_counts_follow_observed_replies(self) -> None:
        """Count signed participants and hide solo annotations."""
        row = {
            **self.data["article"],
            "review_type": "fac",
            "review_url": "https://zh.wikipedia.org/wiki/Talk:Game#Review",
            "started_at": "2026-10-07T03:04:00Z",
        }
        snapshot = self.data["snapshot"]
        snapshot["reviews"] = {"items": [row]}
        for locale, label in (
            ("en", "3 participants"),
            ("zh-hans", "3讨论者"),
            ("zh-hant", "3討論者"),
            ("zh", "3讨论者"),
        ):
            for replied, count in (
                ("2026-10-18T10:00:00Z", 3),
                ("2026-10-18T10:00:00Z", None),
                (None, 1),
            ):
                with self.subTest(locale=locale, replied=replied, count=count):
                    row.update(replied_at=replied, participant_count=count)
                    output = render_dashboard(
                        snapshot,
                        {},
                        {},
                        locale,
                        self.now,
                    )
                    if replied is None or count is None:
                        assert 'class="review-participants"' not in output
                    else:
                        assert re.search(
                            r"</time></li>\s*<li[^>]*>\s*"
                            r'<span class="review-participants">'
                            rf"{label}</span>",
                            output,
                        )

    def test_empty_reviews_hide_section_and_missing_reviews_stay_unknown(
        self,
    ) -> None:
        """Distinguish empty reviews from unavailable data."""
        snapshot = self.data["snapshot"]
        snapshot["reviews"] = {"items": []}
        rendered = render_dashboard(snapshot, {}, {}, "en", self.now)
        assert 'id="reviews-heading"' not in rendered
        assert 'href="#reviews"' not in rendered
        snapshot["reviews"] = None
        rendered = render_dashboard(snapshot, {}, {}, "en", self.now)
        assert 'id="reviews-heading"' in rendered
        section = rendered.split('id="reviews"', 1)[1].split("</section>", 1)[
            0
        ]
        assert self.en.text("empty.section") in section


class MaintenanceOverviewTests(unittest.TestCase):
    """Keep saved maintenance tasks on a dedicated localized page."""

    @staticmethod
    def test_overview_uses_top_level_category_headings() -> None:
        """Link grouped tasks and retain the actual observation time."""
        data = _fixtures()
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = render_maintenance_overview(
                data["snapshot"],
                data["status"],
                data["config"],
                locale,
                dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC),
            )
            assert f"<h1>{interface.text('backlog.heading')}</h1>" in output
            assert '<h2 id="backlog-section-sources">' in output
            assert '<h3 id="backlog-section-sources">' not in output
            assert 'href="#backlog-section-sources"' in output
            assert 'href="/maintenance/?task=references&amp;page=1"' in output
            assert "2026-10-18 12:00" in output
            assert 'id="tool-status"' not in output
            assert 'id="homepage-pageviews"' not in output
            assert 'id="live-uptime"' in output

    @staticmethod
    def test_empty_and_unavailable_catalogues_remain_distinct() -> None:
        """Distinguish empty observations from unavailable data."""
        data = _fixtures()
        backlog = data["snapshot"]["backlog"]
        backlog.update(total=0, groups=[])
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = render_maintenance_overview(
                data["snapshot"],
                None,
                None,
                locale,
            )
            assert interface.text("backlog.overview_empty") in output
            assert interface.text("backlog.unavailable") not in output
            assert "2026-10-18 12:00" in output
            missing = render_maintenance_overview(None, None, None, locale)
            assert interface.text("backlog.unavailable") in missing
            assert interface.text("backlog.overview_empty") not in missing


class ContentsNavigationTests(unittest.TestCase):
    """Keep every full page on the shared contents layout."""

    def test_all_pages_share_accessible_contents_navigation(self) -> None:
        """Keep desktop and mobile contents linked to their sections."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_interface_fixtures(directory)
            for fixture in directory.glob("*/*.html"):
                with self.subTest(fixture=fixture.relative_to(directory)):
                    output = fixture.read_text()
                    assert output.count('class="contents-panel"') == 1
                    assert (
                        output.count(
                            'class="contents-mobile navigation-tools"',
                        )
                        == 1
                    )
                    contents = re.search(
                        r'<aside class="contents-panel">(.*?)</aside>',
                        output,
                        re.DOTALL,
                    )
                    assert contents is not None
                    targets = re.findall(r'href="#([^"]+)"', contents[1])
                    assert targets
                    assert len(targets) % 2 == 0
                    midpoint = len(targets) // 2
                    assert targets[:midpoint] == targets[midpoint:]
                    identifiers = set(re.findall(r'\bid="([^"]+)"', output))
                    assert set(targets) <= identifiers


class PresentationTests(unittest.TestCase):
    """Exercise routes without network or runtime writes."""

    def setUp(self) -> None:
        """Create saved observations and a deterministic clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.en = Interface("en")

    def test_locales_and_catalog(self) -> None:
        """Preserve language weights, scripts, and placeholders."""
        cases = {
            "": "en",
            "fr,de;q=0.9": "en",
            "*": "en",
            "en-US": "en",
            "zh": "zh",
            "zh-CN": "zh-hans",
            "zh-SG": "zh-hans",
            "zh-MY": "zh-hans",
            "zh-TW": "zh-tw",
            "zh-HK": "zh-hk",
            "zh-MO": "zh-hk",
            "zh-Hans-TW": "zh-hans",
            "zh-Hant-CN": "zh-hant",
            "en;q=0.3,zh-TW;q=0.9": "zh-tw",
            "zh-TW;q=0.8,en;q=0.8": "zh-tw",
            "en;q=invalid,zh-TW;q=0.5": "zh-tw",
            "en;q=0.7;q=1,zh-TW;q=0.2": "zh-tw",
            "zh-HK;q=0,en;q=0.001": "en",
            "../../en,zh-TW;q=0.2": "zh-tw",
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                assert select_locale(header) == expected
        assert Interface("zh-hant").language_tag == "zh-Hant"
        assert Interface("zh").text("column.article") == "条目"
        assert self.en.text("backlog.count", count=12) == "12 articles"
        for translations in catalog().values():
            assert list(translations) == ["en", "zh-hans", "zh-hant"]
            parameters = sorted(
                re.findall(r"\{([a-z_]+)\}", translations["en"]),
            )
            for message in translations.values():
                assert message
                assert (
                    sorted(re.findall(r"\{([a-z_]+)\}", message)) == parameters
                )

    def test_safe_html_and_links(self) -> None:
        """Escape data once and reject executable source links."""
        rendered = str(
            wiki_link(
                "https://zh.wikipedia.org/wiki/Test",
                '<script>"&',
                title='"data',
            ),
        )
        assert rendered == (
            '<a href="https://zh.wikipedia.org/wiki/Test" '
            'title="&#34;data" aria-label="&#34;data">'
            "&lt;script&gt;&#34;&amp;</a>"
        )
        for value in (
            "javascript:alert(1)",
            "https://evil.example/wiki/a",
            "//zh.wikipedia.org/wiki/a",
            "https://name:secret@zh.wikipedia.org/wiki/a",
            "https://zh.wikipedia.org:443/wiki/a",
            "https://zh.wikipedia.org\\@evil.example/wiki/a",
            "https://zh.wikipedia.org/other",
            None,
        ):
            assert wiki_url(value) is None
            assert str(wiki_link(value, "<unsafe>")) == "&lt;unsafe&gt;"
        assert (
            wiki_url(self.data["article"]["url"])
            == self.data["article"]["url"]
        )
        assert title_url("A & B") == "https://zh.wikipedia.org/wiki/A_%26_B"
        assert title_url("A", "enwiki") == "https://en.wikipedia.org/wiki/A"
        assert title_url("A", "unknown") is None
        assert local_url("/#backlog", q="a&b") == "/?q=a%26b#backlog"

    def test_dates_and_durations(self) -> None:
        """Retain UTC instants, precision, and unknown observations."""
        assert utc_time("2026-10-18T12:00:00Z") == "2026-10-18 12:00"
        assert utc_time("2026-10-18T20:00:07+08:00", seconds=True) == (
            "2026-10-18 12:00:07"
        )
        for seconds in (False, True):
            assert (
                utc_time(
                    "2026-10-18T20:00:07.123456+08:00",
                    seconds=seconds,
                    milliseconds=True,
                )
                == "2026-10-18 12:00:07.123"
            )
        assert utc_time("2026-10-18T12:00:07Z", milliseconds=True) == (
            "2026-10-18 12:00:07.000"
        )
        assert utc_time(None, milliseconds=True) == "—"
        assert utc_time(None) == "—"
        assert timestamp("2026-02-30T12:00:00Z") is None
        assert timestamp("2026-10-18T12:00:00") is None
        assert self.en.date("2020-03", month_only=True) == "March 2020"
        assert self.en.date("2026-10-31") == "October 31, 2026"
        assert self.en.date("2026-10-31", compact=True) == "Oct 31, 2026"
        assert self.en.date("2028-02-09", compact=True) == "Feb 9, 2028"
        assert (
            self.en.date(
                "2026-10-31",
                compact=True,
                reference_year=2026,
            )
            == "Oct 31"
        )
        assert (
            self.en.date(
                "2025-10-31",
                compact=True,
                reference_year=2026,
            )
            == "Oct 31, 2025"
        )
        assert Interface("zh-hant").date("2026-10-18") == "2026年10月18日"
        assert Interface("zh-hant").date("2026-10-18", compact=True) == (
            "2026年10月18日"
        )
        assert duration(0.25, self.en) == "0.25 sec"
        assert duration(59.999, self.en) == "1 min 0.00 sec"
        assert duration(65.1, self.en) == "1 min 5.10 sec"
        for seconds, expected in (
            (0, "0.00 sec"),
            (0.004, "0.00 sec"),
            (0.44, "0.44 sec"),
            (1, "1.00 sec"),
            (5.3, "5.30 sec"),
            (32.89, "32.89 sec"),
            (32.49, "32.49 sec"),
            (59.994, "59.99 sec"),
            (60, "1 min 0.00 sec"),
            (725.3, "12 min 5.30 sec"),
            (3600, "1 hr 0 min 0.00 sec"),
            (90061.25, "1 d 1 hr 1 min 1.25 sec"),
        ):
            for locale in ("en", "zh-hans", "zh-hant"):
                localized = (
                    expected
                    if locale == "en"
                    else expected
                    .replace(
                        " min ",
                        "分",
                    )
                    .replace(" hr ", "小时" if locale == "zh-hans" else "小時")
                    .replace(" d ", "日")
                    .replace(" sec", "秒")
                )
                assert duration(seconds, Interface(locale)) == localized
        for seconds, expected in (
            (0.44, "0 sec…"),
            (32.89, "32 sec…"),
            (59.999, "59 sec…"),
            (60, "1 min 0 sec…"),
            (725.3, "12 min 5 sec…"),
        ):
            assert duration(seconds, self.en, running=True) == expected
        assert duration(None, self.en) == "—"
        assert (
            event_duration(
                {"status": "running", "started_at": "2026-10-18T11:54:15Z"},
                self.en,
                self.now,
            )
            == "6 min 15 sec…"
        )
        assert (
            event_duration(
                {"status": "running", "started_at": "2026-10-18T13:00:00Z"},
                self.en,
                self.now,
            )
            == "0 sec…"
        )
        assert readable_size(1024) == "1.0 KiB"
        assert readable_size(10) == "10 B"

    def test_relative_times_use_whole_minutes_hours_or_days(self) -> None:
        """Keep directions, unit boundaries, and timezones exact."""
        for seconds, expected in (
            (0, "(just now)"),
            (-59, "(just now)"),
            (59, "(within 1m)"),
            (-60, "(1m ago)"),
            (60, "(1m from now)"),
            (-3599, "(59m ago)"),
            (3599, "(59m from now)"),
            (-3600, "(1h ago)"),
            (3600, "(1h from now)"),
            (-7199, "(1h ago)"),
            (7199, "(1h from now)"),
            (-86399, "(23h ago)"),
            (86399, "(23h from now)"),
            (-86400, "(1d ago)"),
            (86400, "(1d from now)"),
            (-172799, "(1d ago)"),
            (172800, "(2d from now)"),
            (-2629800, "(1mo ago)"),
            (2629800, "(1mo from now)"),
            (-5259600, "(2mo ago)"),
            (5259600, "(2mo from now)"),
        ):
            with self.subTest(seconds=seconds):
                value = (self.now + dt.timedelta(seconds=seconds)).isoformat()
                assert relative_time(value, self.en, self.now) == expected
        assert (
            relative_time(
                "2026-10-18T20:01:30+08:00",
                self.en,
                self.now,
            )
            == "(1m from now)"
        )
        for invalid in (None, "", "invalid", "2026-10-18T12:00:00"):
            assert not relative_time(invalid, self.en, self.now)
        for locale, past, future, future_days in (
            ("zh", "13分钟前", "3小时后", "2日后"),
            ("zh-hans", "13分钟前", "3小时后", "2日后"),
            ("zh-hant", "13分鐘前", "3小時後", "2日後"),
        ):
            interface = Interface(locale)
            assert (
                relative_time(
                    (self.now - dt.timedelta(minutes=13)).isoformat(),
                    interface,
                    self.now,
                )
                == f"\uff08{past}\uff09"
            )
            assert (
                relative_time(
                    (self.now + dt.timedelta(hours=3)).isoformat(),
                    interface,
                    self.now,
                )
                == f"\uff08{future}\uff09"
            )
            assert (
                relative_time(
                    (self.now + dt.timedelta(days=2)).isoformat(),
                    interface,
                    self.now,
                )
                == f"\uff08{future_days}\uff09"
            )

    def test_assessment_aliases(self) -> None:
        """Rank equivalent grades together and leave unknowns last."""
        levels = (
            ("FA", "FL", "典范", "典範", "特色列表"),
            ("A", "AL", "甲", "甲级列表", "甲級列表"),
            ("GA", "优良", "優良"),
            ("Bplus", "乙上"),
            ("B", "BL", "乙", "乙级列表"),
            ("C", "CL", "丙", "丙級列表"),
            ("D", "丁"),
            ("Start", "List", "初", "列表"),
            ("Stub", "SL", "StubList", "小作品", "小列表"),
            ("Substub", "小小作品"),
        )
        for index, values in enumerate(levels):
            for value in values:
                assert assessment_rank(value, "class") == 10 - index
        for value in (
            None,
            "",
            "Unassessed",
            "未评级",
            "Disambig",
            "NA",
            "不适用",
            "custom",
        ):
            assert assessment_rank(value, "class") is None
            assert assessment_rank(value, "importance") is None
        assert assessment_code(" ga ", "class") == "GA"
        assert self.en.label({"zh-hant": "來源"}, "") == "來源"
        assert self.en.label(None, "Fallback") == "Fallback"

    @staticmethod
    def test_maintenance_sorting() -> None:
        """Keep nulls last, stable ties, and seeded reversal."""
        items = [
            {"page_id": 4, "title": "D", "oldest_issue_month": None},
            {"page_id": 1, "title": "B", "oldest_issue_month": "2020-01"},
            {"page_id": 3, "title": "A", "oldest_issue_month": "2018-05"},
            {"page_id": 2, "title": "A", "oldest_issue_month": "2018-05"},
            {"page_id": 5, "title": "AA"},
        ]
        for sort, order, expected in (
            ("oldest", "asc", [2, 3, 1, 5, 4]),
            ("oldest", "desc", [1, 2, 3, 5, 4]),
            ("title", "asc", [2, 3, 5, 1, 4]),
            ("title", "desc", [4, 1, 5, 2, 3]),
        ):
            assert [
                row["page_id"] for row in maintenance_items(items, sort, order)
            ] == expected
        random = maintenance_items(items, "random", "asc", "0123456789abcdef")
        assert random == maintenance_items(
            list(reversed(items)),
            "random",
            "asc",
            "0123456789abcdef",
        )
        assert list(reversed(random)) == maintenance_items(
            items,
            "random",
            "desc",
            "0123456789abcdef",
        )
        assert random != maintenance_items(
            items,
            "random",
            "asc",
            "fedcba9876543210",
        )
        assert [item["page_id"] for item in items] == [4, 1, 3, 2, 5]
        last_page_id = 101
        many = [
            {
                "page_id": index,
                "title": f"{index:03}",
                "assessment_class": "FA" if index == last_page_id else "Stub",
            }
            for index in range(1, 102)
        ]
        ordered = maintenance_items(many, "class", "desc")
        assert maintenance_page(ordered)["items"][0]["page_id"] == 101
        assert maintenance_page(ordered, "2")["items"][0]["page_id"] == 10
        assert maintenance_page(ordered, "9" * 1000)["page"] == 11
        assert maintenance_page([])["pages"] == 1
        assert list(
            maintenance_sections([
                {"section_key": "a"},
                {"section_key": "b"},
                {"section_key": "a"},
            ]),
        ) == ["a", "b"]

    def test_maintenance_queries_and_rendering(self) -> None:
        """Reject bad controls and retain categories and seeds."""
        for query in (
            "",
            "task=../escape",
            "task=references&page=0",
            "task=references&sort=invalid",
            "task=references&order=invalid",
            "task=references&seed=abc",
            "task=references&task=other",
            "task=references&extra=1",
        ):
            with self.subTest(query=query), self.assertRaises(ValueError):
                maintenance_selection(parse_qs(query))
        selection = parse_qs(
            "task=references&sort=random&seed=0123456789abcdef",
        )
        output = render_maintenance(self.data["snapshot"], selection, "en")
        assert "March 2020" in output
        assert "Category%3A%E8%87%AA2020" in output
        assert "Maintenance &lt;&amp;&gt; article" in output
        assert 'type="hidden" name="seed" value="0123456789abcdef"' in output
        assert 'name="order" value="desc"' in output
        with self.assertRaises(LookupError):
            render_maintenance(
                self.data["snapshot"],
                {"task": ["missing"]},
                "en",
            )
        with self.assertRaises(RuntimeError):
            render_maintenance(None, {"task": ["references"]}, "en")

    def test_highlight_semantics(self) -> None:
        """Keep dates, scripts, and unavailable sections distinct."""
        assert merged_items({}, ("featured", "good"))["items"] is None
        self.data["snapshot"]["pageviews"]["items"] = [
            {
                **self.data["article"],
                "title": f"Popular game {index}",
                "rank": index,
            }
            for index in range(1, 14)
        ]
        sections = {
            row["key"]: row["items"]
            for row in homepage_sections(self.data["snapshot"])
        }
        assert sections["recognized"][0]["title"] == "Scheduled game"
        assert len(sections["pageviews"]) == 6
        assert len(sections["dyk"]) == 6
        assert sections["pageviews"][-1]["title"] == "Popular game 6"
        assert sections["dyk"][-1]["title"] == "DYK game 6"
        assert len(self.data["snapshot"]["pageviews"]["items"]) == 13
        assert len(sections["news_history"]) == 8
        article = self.data["article"]
        assert article_extract(article, "zh")[1] == article["extract_zh"]
        assert article_extract({"extract": "simplified"}, "zh")[1] is None
        assert (
            article_extract(article, "zh-hant")[1] == article["extract_hant"]
        )
        assert article_extract(article, "en")[1] == article["extract"]
        query = parse_qs(urlsplit(pageviews_url(article, "2026-10-17")).query)
        assert query["start"] == ["2026-09-18"]
        assert query["end"] == ["2026-10-17"]
        assert query["pages"] == [article["title"]]
        self._assert_card_dates_follow_dashboard_year()
        self._assert_popularity_date_prefix()
        self._assert_popularity_ordinals()
        self._assert_showcase_metadata_order()
        self._assert_highlight_icons()

    def _assert_card_dates_follow_dashboard_year(self) -> None:
        """Omit only the current English year and retain exact dates."""
        snapshot = copy.deepcopy(self.data["snapshot"])
        snapshot["pageviews"]["date"] = "2026-10-31"
        snapshot["pageviews"]["items"][0]["title"] = "Date popularity"
        examples = {
            "featured": ("Date showcase", "2025-10-31", "fa", "observed"),
            "upcoming": ("Date upcoming", "2027-01-01", "ga", "scheduled"),
            "dyk": ("Date DYK", "2026-10-31", "dyk", "observed"),
            "news": ("Date news", "2026-10-31", "news", "observed"),
        }
        for section, (title, date, kind, date_kind) in examples.items():
            snapshot["homepage"][section] = [
                {
                    **self.data["article"],
                    "title": title,
                    "date": date,
                    "kind": kind,
                    "date_kind": date_kind,
                },
            ]
        expected = {
            "Date popularity": ("2026-10-31", "Oct 31", "Oct 31, 2026"),
            "Date showcase": ("2025-10-31", "Oct 31, 2025", "Oct 31, 2025"),
            "Date upcoming": ("2027-01-01", "Jan 1, 2027", "Jan 1"),
            "Date DYK": ("2026-10-31", "Oct 31", "Oct 31, 2026"),
            "Date news": ("2026-10-31", "Oct 31", "Oct 31, 2026"),
        }
        original = copy.deepcopy(snapshot)
        for reference_index, clock in enumerate(
            (
                dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=dt.UTC),
                dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
            ),
            start=1,
        ):
            for locale in ("en", "zh-hans", "zh-hant"):
                with self.subTest(clock=clock, locale=locale):
                    output = render_dashboard(snapshot, {}, {}, locale, clock)
                    cards = re.findall(
                        r"<article\b[^>]*>(.*?)</article>",
                        output,
                        re.DOTALL,
                    )
                    for title, dates in expected.items():
                        card = next(
                            body for body in cards if f">{title}</a>" in body
                        )
                        date = dates[0]
                        label = (
                            dates[reference_index]
                            if locale == "en"
                            else Interface(locale).date(date)
                        )
                        assert (
                            f'<time datetime="{date}" title="{date}">'
                            f"{label}</time>" in card
                        )
        assert snapshot == original

    def _assert_showcase_metadata_order(self) -> None:
        """Lead showcase metadata with kind and retain date evidence."""
        snapshot = copy.deepcopy(self.data["snapshot"])
        snapshot["homepage"]["upcoming"] = []
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            for date_kind in ("observed", "scheduled"):
                snapshot["homepage"]["featured"] = [
                    {
                        **self.data["article"],
                        "title": "Metadata game",
                        "date": "2026-10-13",
                        "date_kind": date_kind,
                    },
                ]
                output = render_dashboard(snapshot, {}, {}, locale, self.now)
                card = next(
                    body
                    for body in re.findall(
                        r"<article\b[^>]*>(.*?)</article>",
                        output,
                        re.DOTALL,
                    )
                    if ">Metadata game</a>" in body
                )
                metadata = re.search(
                    r'<ul class="card-meta metadata-items" role="list">'
                    r"(.*?)</ul>",
                    card,
                    re.DOTALL,
                )[1]
                items = re.findall(r"<li>(.*?)</li>", metadata, re.DOTALL)
                assert len(items) == 2
                assert items[0] == interface.text("appearance.featured")
                label = (
                    "Oct 13"
                    if locale == "en"
                    else interface.date("2026-10-13")
                )
                assert (
                    '<time datetime="2026-10-13" title="2026-10-13">'
                    f"{label}</time>" in items[1]
                )
                if date_kind == "scheduled":
                    assert interface.text("date.scheduled") in items[1]
                else:
                    assert interface.text("date.observed") in items[1]
                    assert 'class="date-note-marker"' in items[1]
                assert " · " not in metadata

    def _assert_popularity_ordinals(self) -> None:
        """Show English metric phrases with correct ordinal endings."""
        snapshot = copy.deepcopy(self.data["snapshot"])
        item = snapshot["pageviews"]["items"][0]
        for rank, ordinal in (
            (1, "1st"),
            (2, "2nd"),
            (3, "3rd"),
            (11, "11th"),
            (12, "12th"),
            (13, "13th"),
            (14, "14th"),
            (21, "21st"),
            (111, "111th"),
            (1000, "1,000th"),
        ):
            item["site_rank"] = rank
            item["views"] = 1 if rank == 1 else 10_110
            output = render_dashboard(snapshot, {}, {}, "en", self.now)
            site_rank = re.search(
                r'<a\b[^>]*class="site-rank"[^>]*>(.*?)</a>',
                output,
            )
            assert site_rank is not None
            assert site_rank[1] == f"{ordinal} site-wide"
            views = re.search(
                r'<a\b[^>]*class="pageviews-number"[^>]*>(.*?)</a>',
                output,
            )
            assert views is not None
            assert views[1] == ("1 view" if rank == 1 else "10,110 views")

    def _assert_highlight_icons(self) -> None:
        """Keep appearance icons independent of current assessment."""
        snapshot = copy.deepcopy(self.data["snapshot"])
        homepage = snapshot["homepage"]
        expectations = {
            "featured": (
                ("Featured once", "fa", "B", "featured"),
                ("Featured list once", "fl", "GA", "featured"),
                ("Unspecified featured once", "ga", "Stub", "featured"),
            ),
            "good": (("Good once", "fa", "FA", "good"),),
            "upcoming": (
                ("Scheduled featured", "fa", "GA", "featured"),
                ("Scheduled good", "ga", "FA", "good"),
            ),
            "dyk": (
                ("DYK now featured", "fa", "FA", "dyk"),
                ("DYK now good", "ga", "GA", "dyk"),
                ("DYK now B", "dyk", "B", "dyk"),
            ),
        }
        for section, items in expectations.items():
            homepage[section] = [
                {
                    **self.data["article"],
                    "title": title,
                    "kind": kind,
                    "assessment_class": assessment,
                    "date_kind": "scheduled"
                    if section == "upcoming"
                    else "observed",
                }
                for title, kind, assessment, _ in items
            ]
        original = copy.deepcopy(snapshot)
        for locale in ("en", "zh-hans", "zh-hant", "zh"):
            interface = Interface(locale)
            output = render_dashboard(snapshot, {}, {}, locale, self.now)
            cards = re.findall(
                r"<article\b[^>]*>(.*?)</article>",
                output,
                re.DOTALL,
            )
            for section, items in expectations.items():
                for title, _, assessment, icon in items:
                    card = next(
                        body for body in cards if f">{title}</a>" in body
                    )
                    assert f"/assets/icons/{icon}.svg?v=" in card
                    assert "-candidate.svg" not in card
                    label = interface.text(
                        "homepage.dyk"
                        if section == "dyk"
                        else f"appearance.{icon}",
                    )
                    assert f'alt="{label}"' in card
                    assert (
                        '<span class="cdx-info-chip__text">'
                        f"{interface.text(f'assessment.class.{assessment}')}"
                        "</span>" in card
                    )
                    assert "article-rank" not in card
            popular = output.split('id="homepage-pageviews"', 1)[1].split(
                "</section>",
                1,
            )[0]
            for rank in range(1, 7):
                card = re.search(
                    rf'<li class="article-card" value="{rank}">(.*?)</li>',
                    popular,
                    re.DOTALL,
                )
                assert card is not None
                assert 'class="article-medal cdx-card__icon"' in card[1]
                assert f"/assets/icons/medal-{rank}.svg?v=" in card[1]
                assert 'alt="" aria-hidden="true"' in card[1]
            assert (
                "article-medal"
                not in output.split(
                    'id="homepage-news_history"',
                    1,
                )[1].split("</section>", 1)[0]
            )
        assert snapshot == original

    def _assert_popularity_date_prefix(self) -> None:
        """Keep the observed date visible independently of site rank."""
        popularity = self.data["snapshot"]["pageviews"]
        popularity["date"] = "2026-10-31"
        popularity["items"][0]["views"] = 10_110
        for locale, prefix, rank_label in (
            (
                "en",
                "Oct 31: 10,110 views",
                "14th site-wide",
            ),
            (
                "zh-hans",
                "2026年10月31日\uff1a10,110次浏览",
                "全站第14名",
            ),
            (
                "zh-hant",
                "2026年10月31日\uff1a10,110次瀏覽",
                "全站第14名",
            ),
        ):
            for rank in (14, None):
                with self.subTest(locale=locale, rank=rank):
                    popularity["items"][0]["site_rank"] = rank
                    output = render_dashboard(
                        self.data["snapshot"],
                        self.data["status"],
                        self.data["config"],
                        locale,
                        self.now,
                    )
                    section = output.split('id="homepage-pageviews"', 1)[
                        1
                    ].split("</section>", 1)[0]
                    metadata = re.search(
                        r'<div class="card-meta popularity-meta">(.*?)</div>',
                        section,
                        re.DOTALL,
                    )[1]
                    assert (
                        '<time datetime="2026-10-31" title="2026-10-31">'
                        in metadata
                    )
                    assert 'class="pageviews-number"' in metadata
                    assert ('class="site-rank"' in metadata) == (
                        rank is not None
                    )
                    lead = re.search(
                        r'<span class="card-meta-lead">(.*?)</span>',
                        metadata,
                    )[1]
                    items = re.findall(r"<li>(.*?)</li>", metadata, re.DOTALL)
                    text = unescape(re.sub(r"<[^>]+>", "", lead + items[0]))
                    assert text == prefix
                    assert len(items) == (2 if rank is not None else 1)
                    if rank is not None:
                        assert rank_label in items[1]
                    assert (
                        '<ul class="metadata-items" role="list">' in metadata
                    )
                    assert " · " not in metadata

    def test_actual_scheduling_and_uptime(self) -> None:
        """Suppress stale data and retain actual execution times."""
        schedule = self.data["status"]["schedule"]
        definitions = self.data["config"]["routines"]["routines"]
        selected = schedule_state(schedule, definitions, self.now)
        assert next_run(selected, "pageviews", self.en) == "2026-10-20 18:59"
        assert "running for 2 d 3 hr 4 min" in uptime(
            selected,
            "0.6.0",
            self.en,
            self.now,
        )
        for change in (
            {"updated_at": "2026-10-18T11:58:29Z"},
            {"updated_at": "2026-10-18T12:01:00Z"},
            {"state": "stopped"},
            {"state": "missing"},
            {"started_at": None},
        ):
            altered = schedule_state(
                {**schedule, **change},
                definitions,
                self.now,
            )
            assert "unavailable" in uptime(altered, "0.6.0", self.en, self.now)
        paused = schedule_state(
            {**schedule, "state": "paused"},
            definitions,
            self.now,
        )
        assert next_run(paused, "pageviews", self.en) == "Scheduler paused"
        assert "2 d 3 hr 4 min" in uptime(paused, "0.6.0", self.en, self.now)
        changed = copy.deepcopy(definitions)
        changed["pageviews"]["minute"] = 58
        assert (
            next_run(
                schedule_state(schedule, changed, self.now),
                "pageviews",
                self.en,
            )
            == "Schedule unavailable"
        )

    def test_python_configuration_matches_json_schedule(self) -> None:
        """Keep times when source tuples become JSON prefix arrays."""
        definitions = routine_configuration()["routines"]
        rows = json.loads(json.dumps(definitions))
        for row in rows.values():
            row.update(state="scheduled", next_run_at="2026-10-20T18:59:00Z")
        schedule = {**self.data["status"]["schedule"], "routines": rows}
        selected = schedule_state(schedule, definitions, self.now)
        assert set(selected["routines"]) == set(definitions)
        for key in definitions:
            assert next_run(selected, key, self.en) == "2026-10-20 18:59"

    def test_all_localized_dashboards(self) -> None:
        """Build escaped article pages with direct section headings."""
        for locale, extract in (
            ("en", "简介"),
            ("zh-hans", "简介"),
            ("zh-hant", "簡介"),
        ):
            output = render_dashboard(
                self.data["snapshot"],
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            )
            assert (
                f"{extract} &lt;script&gt;never execute&lt;/script&gt;"
                " &amp; 中文" in output
            )
            assert output.count('<script src="/live.js?v=') == 1
            assert "DYK game 6" in output
            assert "DYK game 7" not in output
            assert "News game 7" in output
            assert output.count('class="article-extract"') == 18
            assert 'class="extract-attribution"' not in output
            for key in ("pageviews", "recognized", "dyk", "news_history"):
                assert f'<h2 id="homepage-{key}">' in output
            for removed in (
                'id="homepage-heading"',
                'id="tool-status"',
                'id="backlog"',
                'class="backlog-task"',
                'commandfor="report-details-pageviews"',
            ):
                assert removed not in output
            assert 'href="/maintenance/"' in output
            assert 'href="/reports/"' in output
            assert 'href="/about/"' in output
        generic = render_dashboard(
            self.data["snapshot"],
            self.data["status"],
            self.data["config"],
            "zh",
            self.now,
        )
        assert 'lang="zh">原文遊戲与音乐' in generic
        missing = render_dashboard(None, None, None, "en", self.now)
        assert "Project data is being prepared" in missing
        assert "temporarily unavailable" in missing

    def test_status_line_labels_and_independent_timing(self) -> None:
        """Link status text and show independent check times."""
        for locale, finished, next_label, report_duration in (
            ("en", "Checked at ", "Next check ", "5.00 sec"),
            (
                "zh-hans",
                "检查时间\uff1a",
                "下次检查\uff1a",
                "5.00秒",
            ),
            (
                "zh-hant",
                "檢查時間\uff1a",
                "下次檢查\uff1a",
                "5.00秒",
            ),
        ):
            output = render_reports(
                {"files": []},
                locale,
                status=self.data["status"],
                config=self.data["config"],
                now=self.now,
            )
            line = re.search(
                r'<p class="routine-status"[^>]*>(.*?)</p>',
                output,
            )
            assert line is not None
            interface = Interface(locale)
            assert line[1].startswith('<a class="status-label status-link"')
            assert "<svg" not in line[1]
            assert "<img" not in line[1]
            assert line[1].endswith(
                f">{interface.text('state.Deferred')}</a>",
            )
            assert interface.text("ops.details_link") not in line[1]
            assert "data-clock" not in line[1]
            assert 'href="#report-details-' in line[1]
            assert "<div" not in line[1]
            assert "<br" not in line[1]
            assert finished in output
            assert next_label in output
            assert f">{report_duration}</td>" in output
            assert "5.000" not in output
        self.data["status"]["reports"] = None
        output = render_reports(
            {"files": []},
            "en",
            status=self.data["status"],
            config=self.data["config"],
            now=self.now,
        )
        assert '<dialog id="report-details-pageviews"' in output
        assert '<div class="cdx-message__content">No record</div>' in output

    def test_queued_reports_wait_until_individual_execution_starts(
        self,
    ) -> None:
        """Hide registration clocks until each report actually runs."""
        status = self.data["status"]
        report = status["reports"]["reports"]["first"]
        routine = status["routines"]["routines"]["pageviews"]
        routine.update(
            status="running",
            started_at=self.now.isoformat(),
            finished_at=None,
            duration_seconds=None,
        )
        for locale, waiting, running, completed in (
            ("en", "Waiting to run…", "2 sec…", "5.00 sec"),
            (
                "zh-hans",
                "等待执行…",
                "2秒…",
                "5.00秒",
            ),
            (
                "zh-hant",
                "等待執行…",
                "2秒…",
                "5.00秒",
            ),
        ):
            report.update(
                status="queued",
                started_at=self.now.isoformat(),
                finished_at=None,
                duration_seconds=None,
                confirmed_edits=None,
            )
            later = self.now + dt.timedelta(seconds=30)
            for now in (self.now, later):
                output = render_live_status(
                    status,
                    self.data["config"],
                    locale,
                    now,
                )
                row = re.search(
                    r'<tr data-key="[^"]*"[^>]*>(.*?)</tr>',
                    output,
                )
                assert row is not None
                assert row[1].count(waiting) == 2
                assert "<time" not in row[1]
            assert 'data-clock="elapsed"' not in output
            page = render_reports(
                {"files": []},
                locale,
                status=status,
                config=self.data["config"],
                now=later,
            )
            assert row[0] in page
            report.update(status="running", started_at=later.isoformat())
            output = render_live_status(
                status,
                self.data["config"],
                locale,
                later + dt.timedelta(seconds=2),
            )
            assert waiting not in output
            assert f">{running}</td>" in output
            assert 'data-clock="elapsed"' in output
            assert "2026-10-18 12:01:00" in output
            report.update(
                status="updated",
                finished_at=(later + dt.timedelta(seconds=5)).isoformat(),
                duration_seconds=5,
                confirmed_edits=1,
            )
            output = render_live_status(
                status,
                self.data["config"],
                locale,
                later + dt.timedelta(seconds=30),
            )
            assert f">{completed}</td>" in output

    def test_live_fragments_advance_timers_and_expire_schedules(self) -> None:
        """Refresh clocks and observed states with the server's time."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event.update(
            status="running",
            started_at=self.now.isoformat(),
            finished_at=None,
            duration_seconds=None,
        )
        status["reports"]["reports"]["first"].update(
            status="running",
            started_at=self.now.isoformat(),
            finished_at=None,
            duration_seconds=None,
        )
        for locale, elapsed in (
            ("en", "1 sec…"),
            ("zh-hans", "1秒…"),
            ("zh-hant", "1秒…"),
        ):
            now = self.now + dt.timedelta(seconds=1)
            live = render_live_status(status, self.data["config"], locale, now)
            assert f">{elapsed}</td>" in live
            assert "&lt;Latest &amp; safe&gt;" in live
            assert 'id="tool-status"' in live
            assert 'id="live-uptime"' in live
            assert f'data-server-time="{now.isoformat()}"' in live
            assert 'data-schedule-expires="2026-10-18T12:02:00+00:00"' in live
            assert 'data-clock="uptime"' in live
            assert "<script" not in live
            assert "<html" not in live
        fresh = render_live_status(
            status,
            self.data["config"],
            "en",
            self.now + dt.timedelta(seconds=1),
        )
        assert "2 d 3 hr 4 min 31 sec" in fresh
        stale = render_live_status(
            status,
            self.data["config"],
            "en",
            self.now + dt.timedelta(seconds=121),
        )
        assert "uptime: unavailable" in stale
        assert "2026-10-20 18:59" not in stale
        assert "data-schedule-expires" not in stale
        assert 'data-clock="uptime"' not in stale

    def test_only_active_observations_receive_timer_anchors(self) -> None:
        """Keep completed and queued times fixed between syncs."""
        status = self.data["status"]
        report = status["reports"]["reports"]["first"]
        for state in ("updated", "queued", "running"):
            report["status"] = state
            output = render_live_status(
                status,
                self.data["config"],
                "en",
                self.now,
            )
            assert ('data-clock="elapsed"' in output) == (state == "running")
            assert "Status syncs automatically" not in output
        report["started_at"] = None
        output = render_live_status(
            status,
            self.data["config"],
            "en",
            self.now,
        )
        assert 'data-clock="elapsed"' not in output

    def test_routine_time_annotations_preserve_absolute_observations(
        self,
    ) -> None:
        """Append plain annotations only to observed timestamps."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["finished_at"] = (
            self.now - dt.timedelta(hours=3, minutes=35)
        ).isoformat()
        schedule = status["schedule"]
        schedule["routines"]["pageviews"]["next_run_at"] = (
            self.now + dt.timedelta(minutes=23, seconds=40)
        ).isoformat()
        output = render_reports(
            {"files": []},
            "zh-hans",
            status=status,
            config=self.data["config"],
            now=self.now,
        )
        assert "2026-10-18 08:25</time> \uff083小时前\uff09" in output
        assert "2026-10-18 12:24 \uff0823分钟后\uff09" in output
        assert 'class="relative-time"' not in output
        assert 'data-clock="relative"' not in output
        assert "<small" not in output
        status["routines"] = None
        for change in (
            {"state": "paused"},
            {"state": "stopped"},
            {"updated_at": "2026-10-18T11:00:00Z"},
        ):
            status["schedule"] = {**schedule, **change}
            output = render_reports(
                {"files": []},
                "zh-hans",
                status=status,
                config=self.data["config"],
                now=self.now,
            )
            assert 'class="relative-time"' not in output

    def test_confirmed_empty_sections_are_omitted(self) -> None:
        """Omit empty headings and retain unavailable observations."""
        empty = self.data["snapshot"]
        empty["homepage"] = {key: [] for key in empty["homepage"]}
        empty["pageviews"]["items"] = []
        empty["backlog"]["total"] = 0
        output = render_dashboard(empty, None, None, "en", self.now)
        assert 'id="homepage"' not in output
        assert 'id="backlog"' not in output
        empty["homepage"]["news"] = None
        assert 'id="homepage-news_history"' in render_dashboard(
            empty,
            None,
            None,
            "en",
            self.now,
        )

    def test_about_logs_and_errors(self) -> None:
        """Share navigation and escape log controls in every locale."""
        for locale in ("en", "zh-hans", "zh-hant"):
            assert "0.6.0.dev1" in render_about(self.data["config"], locale)
            assert 'role="alert"' in render_error("backlog.not_found", locale)
            output = render_logs(
                {
                    "files": [
                        {
                            "date": "2026-10-18",
                            "name": "aranami.2026-10-18.log",
                            "size": 1024,
                        },
                    ],
                    "date": "2026-10-18",
                    "query": '"><script>bad</script>',
                    "events": [],
                    "truncated": True,
                },
                locale,
            )
            assert output.count('<script src="/live.js?v=') == 1
            assert "<script>bad</script>" not in output
            assert "&lt;script&gt;bad&lt;/script&gt;" not in output
            assert 'id="recent-events"' not in output
            assert 'class="log-query"' not in output
            assert "<pre" not in output
            assert "1.0 KiB" in output
            assert "/logs/?date=2026-10-18&amp;download=1" in output
            assert Interface(locale).text("logs.empty") in render_logs(
                {"files": []},
                locale,
            )

    @staticmethod
    def test_interface_assets_are_self_contained() -> None:
        """Ship local assets without a remote browser dependency."""
        root = resource_root("webservice")
        for output in (
            render_dashboard(None, None, None, "en"),
            render_reports({"files": []}, "en"),
            render_maintenance_overview(None, None, None, "en"),
            render_logs({"files": []}, "en"),
            render_about(None, "en"),
        ):
            assets = re.findall(
                r'<(?:link|script)\b[^>]*(?:href|src)="([^"]+)"',
                output,
            )
            assert "/styles.css" in {urlsplit(asset).path for asset in assets}
            assert "/components.js" not in {
                urlsplit(asset).path for asset in assets
            }
            assert all(asset.startswith("/") for asset in assets)
            for asset in assets:
                parsed = urlsplit(asset)
                source = root / "public_html" / parsed.path.lstrip("/")
                assert source.is_file()
                assert parse_qs(parsed.query) == {
                    "v": [sha256(source.read_bytes()).hexdigest()],
                }
        for output in (
            render_maintenance(
                maintenance_snapshot(),
                {"task": ["references"]},
                "en",
            ),
            render_newsletter(
                {"issue": "2027-01", "available": False, "pages": []},
                "en",
            ),
        ):
            assert output.count('<script src="/components.js?v=') == 1
        assert not list(root.rglob("*.php"))
        assert set(root.rglob("*.js")) == {
            root / "public_html" / "components.js",
            root / "public_html" / "live.js",
            root / "public_html" / "newsletter.js",
            root / "public_html" / "navigation.js",
        }
        assert not list(root.rglob("*.mjs"))


class LiveVisibilityTests(unittest.TestCase):
    """Keep routine schedules and connection hints relevant to state."""

    def setUp(self) -> None:
        """Create saved observations and a deterministic clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)

    def test_updated_report_status_links_the_observed_revision(self) -> None:
        """Link saved revisions in full and live status dialogs."""
        report = self.data["status"]["reports"]["reports"]["first"]
        report.update(status="updated", revision_id=12345)
        for output in self._outputs("zh-hans"):
            assert (
                'href="https://zh.wikipedia.org/w/index.php?oldid=12345"'
                in output
            )
            assert ">已更新</a>" in output

    def _outputs(self, locale: str) -> tuple[str, str]:
        """Render full and live pages from the same saved observations.

        Args:
            locale: Interface language for both renderers.

        Returns:
            Complete reports page followed by its live status fragments.
        """
        return (
            render_reports(
                {"files": []},
                locale,
                status=self.data["status"],
                config=self.data["config"],
                now=self.now,
            ),
            render_live_status(
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            ),
        )

    @staticmethod
    def _card(output: str, routine: str = "pageviews") -> str:
        """Select one routine row without matching report-popup states.

        Args:
            output: Rendered dashboard or live status fragments.
            routine: Stable routine identifier of the expected row.

        Returns:
            Contents of the selected routine row.
        """
        card = re.search(
            rf'<article[^>]*id="routine-{re.escape(routine)}"[^>]*>'
            r"(.*?)</article>",
            output,
            re.DOTALL,
        )
        assert card is not None
        return card[1]

    def test_queued_idle_reports_show_waiting(self) -> None:
        """Show waiting idle reports in the page and live fragments."""
        definition = self.data["config"]["routines"]["routines"]["pageviews"]
        definition["idle"] = True
        schedule = self.data["status"]["schedule"]
        schedule["routines"]["pageviews"]["queued_since"] = (
            "2026-10-18T11:00:00Z"
        )
        for locale in ("en", "zh-hans", "zh-hant"):
            for output in self._outputs(locale):
                card = self._card(output)
                assert Interface(locale).text("ops.schedule_idle") in card
                assert "2026-10-20 18:59" not in card
                future = Interface(locale).text("time.days_ahead", count=2)
                assert future not in card
        schedule["state"] = "paused"
        assert Interface("en").text("ops.schedule_paused") in self._card(
            self._outputs("en")[0],
        )

    def test_completed_daily_reports_show_their_confirmed_date(self) -> None:
        """Use dated success labels after verified daily work."""
        status = self.data["status"]
        config = self.data["config"]["routines"]["routines"]
        original = status["routines"]["routines"]["pageviews"]
        for routine in ("pageviews", "new_pages"):
            config[routine] = config["pageviews"]
            status["routines"]["routines"][routine] = original
            for outcome in ("updated", "no_update"):
                original.update(status=outcome, report_date="2026-10-17")
                for locale, label in (
                    ("en", "Updated report for October 17, 2026"),
                    ("zh-hans", "已更新2026年10月17日报告"),
                    ("zh-hant", "已更新2026年10月17日報告"),
                ):
                    with self.subTest(
                        routine=routine,
                        outcome=outcome,
                        locale=locale,
                    ):
                        for output in self._outputs(locale):
                            card = self._card(output, routine)
                            assert f">{label}</a>" in card
                            assert 'data-status="updated"' in card

    def test_contents_links_match_each_page(self) -> None:
        """Link dashboard and report contents to visible sections."""
        for locale in ("en", "zh-hans", "zh-hant"):
            dashboard = render_dashboard(
                self.data["snapshot"],
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            )
            reports = self._outputs(locale)[0]
            for output, anchors in (
                (
                    dashboard,
                    (
                        "homepage-pageviews",
                        "homepage-recognized",
                        "homepage-dyk",
                        "homepage-news_history",
                    ),
                ),
                (reports, ("tool-status", "recent-events")),
            ):
                contents = re.search(
                    r'<nav class="page-toc"[^>]*>(.*?)</nav>',
                    output,
                    re.DOTALL,
                )
                assert contents is not None
                assert 'href="/"' not in contents[1]
                assert 'class="brand"' in output.split("</header>")[0]
                for anchor in anchors:
                    assert f'href="#{anchor}"' in contents[1]
                    assert f'id="{anchor}"' in output
            assert 'href="#tool-status"' not in dashboard
            assert 'href="#homepage-pageviews"' not in reports
        snapshot = self.data["snapshot"]
        snapshot["pageviews"]["items"] = []
        output = render_dashboard(snapshot, None, None, "en", self.now)
        assert 'href="#homepage-pageviews"' not in output
        assert 'href="#backlog"' not in output

    def test_about_monitor_keeps_one_flat_localized_resource_table(
        self,
    ) -> None:
        """Keep versions and fresh metrics in one accessible table."""
        status = self.data["status"]
        status["schedule"]["runtime"] = {
            "python": "3.15.0",
            "system": "Linux",
            "machine": "x86_64",
            "os_release": "Debian GNU/Linux 13 (trixie)",
            "kernel": "6.12.0-example",
            "python_implementation": "CPython",
            "python_compiler": "Clang 20.1.4",
            "aranami_version": "0.8.5",
            "memory_bytes": 104_857_600,
            "memory_limit_bytes": 4_294_967_296,
            "cpu_percent": 12.5,
            "cpu_limit": 2,
            "storage_used_bytes": 8_686_141_859_840,
            "storage_total_bytes": 10_885_165_114_982,
        }
        for locale, memory, cpu, started in (
            ("en", "100.0 MiB / 4.0 GiB", "12.5% (2 cores)", "started at "),
            (
                "zh-hans",
                "100.0 MiB / 4.0 GiB",
                "12.5%\uff082核心\uff09",
                "启动时间",
            ),
            (
                "zh-hant",
                "100.0 MiB / 4.0 GiB",
                "12.5%\uff082核心\uff09",
                "啟動時間",
            ),
        ):
            initial = render_about(
                self.data["config"],
                locale,
                status=status,
                now=self.now,
            )
            live = self._outputs(locale)[1]
            stale_now = self.now + dt.timedelta(minutes=3)
            stale = render_about(
                self.data["config"],
                locale,
                status=status,
                now=stale_now,
            )
            stale_live = render_live_status(
                status,
                self.data["config"],
                locale,
                stale_now,
            )
            for output, fresh in (
                (initial, True),
                (live, True),
                (stale, False),
                (stale_live, False),
            ):
                assert 'id="server-runtime"' in output
                for value in (
                    "3.15.0",
                    "Linux",
                    "x86_64",
                    "Debian GNU/Linux 13 (trixie)",
                    "6.12.0-example",
                    "CPython",
                    "Clang 20.1.4",
                ):
                    assert value not in output
                assert "0.8.5" in output
                assert self.data["config"]["version"] in output
                assert "server-card" not in output
                assert "server-group-" not in output
                assert started in output
                assert "2026-10-16 08:56:00" in output
                age = Interface(locale).text("time.days_ago", count=2)[1:-1]
                assert f" {age}" in output
                assert "data-relative-inline" not in output
                assert output.count('id="server-runtime"') == 1
                tables = re.findall(
                    r'<table class="[^"]*\bserver-metrics\b[^"]*">'
                    r"(.*?)</table>",
                    output,
                    re.DOTALL,
                )
                assert len(tables) == 1
                metrics = "".join(tables)
                assert metrics.count("<tr>") == 5
                assert metrics.count('<th scope="row">') == 5
                assert '<caption class="visually-hidden">' in metrics
                assert Interface(locale).text("server.heading") in metrics
                assert "<table" not in metrics
                assert "cdx-card" not in metrics
                assert 'id="server-state"' in metrics
                assert "0.8.5" in metrics
                assert self.data["config"]["version"] in metrics
                for key, value in (("memory", memory), ("cpu", cpu)):
                    label = re.escape(Interface(locale).text(f"server.{key}"))
                    row = re.search(
                        rf'<tr><th scope="row">{label}</th>'
                        r"<td([^>]*)>(.*?)</td></tr>",
                        metrics,
                        re.DOTALL,
                    )
                    assert row is not None
                    assert ("data-schedule-time" in row[1]) == fresh
                    assert (value in row[2]) == fresh
                    if not fresh:
                        assert (
                            Interface(locale).text("ops.unavailable") in row[2]
                        )
                assert "TiB" not in metrics
                for label in (
                    "Storage used / total",
                    "存储已用\uff0f总量",
                    "儲存已用\uff0f總量",
                ):
                    assert label not in metrics
                if not fresh:
                    assert "12.5%" not in output
                    assert "100.0 MiB" not in output
                    assert "data-schedule-time" not in output
                    assert "2026-10-18 12:00:00" in output
            assert re.search(r'src="/live\.js\?v=[a-f0-9]{64}"', initial)
            assert 'id="live-server-status"' in initial

    def test_unconfirmed_daily_dates_keep_the_actual_failure_label(
        self,
    ) -> None:
        """Never show a success date for failed or deferred work."""
        event = self.data["status"]["routines"]["routines"]["pageviews"]
        for outcome, label in (
            ("failed", "Failed"),
            ("deferred", "Waiting to retry"),
            ("unknown", "Result unavailable"),
        ):
            event.update(status=outcome, report_date="2026-10-17")
            for output in self._outputs("en"):
                card = self._card(output)
                assert label in card
                assert "Updated report for" not in card
        event.update(status="updated", report_date=None)
        for output in self._outputs("en"):
            card = self._card(output)
            assert "Updated report for" not in card
            assert ">Updated</a>" in card

    def test_unknown_failure_identifiers_never_expose_diagnostic_text(
        self,
    ) -> None:
        """Use localized fallbacks for unknown private details."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        report = status["reports"]["reports"]["first"]
        for record in (event, report):
            record.update(
                status="failed",
                failure_date="2026-10-19",
                failure_reason="TimeoutError <script>unsafe</script> & retry",
            )
        event["report_date"] = "2026-10-17"
        for locale, label in (
            (
                "en",
                (
                    "Record update for October 19, 2026 failed: "
                    "The update could not be completed; "
                    "see run logs for details"
                ),
            ),
            (
                "zh-hans",
                "2026年10月19日记录更新失败\uff1a无法完成更新\uff0c具体原因请查看运行日志",
            ),
            (
                "zh-hant",
                "2026年10月19日紀錄更新失敗\uff1a無法完成更新\uff0c詳情請查看執行日誌",
            ),
        ):
            with self.subTest(locale=locale):
                for output in self._outputs(locale):
                    card = self._card(output)
                    assert card.count(label) == 2
                    assert 'data-status="failed"' in card
                    assert "<script>unsafe</script>" not in card
                    assert "TimeoutError" not in card
                    assert "Updated report for" not in card
                    assert "2026年10月17日" not in card

    def test_failed_report_details_preserve_unknown_dates_and_reasons(
        self,
    ) -> None:
        """Keep unknown failure dates and reasons unknown."""
        event = self.data["status"]["routines"]["routines"]["pageviews"]
        event.update(status="failed", report_date="2026-10-17")
        for fields, label in (
            (
                {"failure_reason": "TimeoutError"},
                "Failed: The request timed out",
            ),
            (
                {"failure_date": "2026-10-19"},
                "Record update for October 19, 2026 failed",
            ),
            ({}, "Failed"),
        ):
            with self.subTest(fields=fields):
                event.pop("failure_date", None)
                event.pop("failure_reason", None)
                event.update(fields)
                for output in self._outputs("en"):
                    card = self._card(output)
                    assert f">{label}</a>" in card
                    assert "October 17, 2026" not in card
                    assert "October 18, 2026" not in card
        event.update(
            status="updated",
            report_date=None,
            failure_date="2026-10-19",
            failure_reason="TimeoutError",
        )
        for output in self._outputs("en"):
            card = self._card(output)
            assert ">Updated</a>" in card
            assert "TimeoutError" not in card

    def test_http_causes_are_human_readable_in_every_status_view(self) -> None:
        """Localize service outages in initial and live responses."""
        status = self.data["status"]
        for record in (
            status["routines"]["routines"]["pageviews"],
            status["reports"]["reports"]["first"],
        ):
            record.update(status="failed", failure_reason="HTTP503")
        for locale, reason in (
            (
                "en",
                "The external service is temporarily unavailable (HTTP 503)",
            ),
            ("zh-hans", "外部服务暂时不可用\uff08HTTP 503\uff09"),
            ("zh-hant", "外部服務暫時無法使用\uff08HTTP 503\uff09"),
        ):
            for output in self._outputs(locale):
                card = self._card(output)
                assert card.count(reason) == 2
                assert "HTTP503" not in card

    def test_legacy_exception_groups_have_a_readable_fallback(self) -> None:
        """Never present a Python exception-group name to readers."""
        event = self.data["status"]["routines"]["routines"]["pageviews"]
        event.update(status="failed", failure_reason="ExceptionGroup")
        for locale in ("en", "zh-hans", "zh-hant"):
            for output in self._outputs(locale):
                card = self._card(output)
                assert "ExceptionGroup" not in card
                assert 'data-status="failed"' in card

    def test_running_progress_uses_saved_dates_and_counts(self) -> None:
        """Separate dated status labels from phase and counters."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event.update(
            status="running",
            progress={
                "phase": "pageviews_download",
                "report_date": "2026-10-20",
                "current": 2000,
                "total": 13500,
            },
        )
        for locale, label, note, fallback in (
            (
                "en",
                "Updating pageviews for October 20, 2026",
                "Fetching pageviews (2,000/13,500)",
                "Running",
            ),
            (
                "zh-hans",
                "更新2026年10月20日记录",
                "抓取浏览量\uff082,000/13,500\uff09",
                "更新中",
            ),
            (
                "zh-hant",
                "更新2026年10月20日紀錄",
                "擷取瀏覽量\uff082,000/13,500\uff09",
                "更新中",
            ),
        ):
            for output in self._outputs(locale):
                card = self._card(output)
                status_label = re.search(
                    r'<a[^>]*class="status-label status-link"[^>]*>(.*?)</a>',
                    card,
                )
                line = re.search(
                    r'<p class="routine-progress"[^>]*>(.*?)</p>',
                    card,
                )
                assert status_label is not None
                assert status_label[1] == label
                assert f'data-progress-label="{label}"' in status_label[0]
                assert (
                    f'data-progress-fallback="{fallback}"' in status_label[0]
                )
                assert line is not None
                assert line[1] == note
                assert "data-progress-active" in line[0]
                assert "2026" not in line[1]
                assert "2,000/13,500" not in status_label[1]

    def test_progress_phases_and_unknown_counters_remain_explicit(
        self,
    ) -> None:
        """Preserve fallback phases and distinguish zero from null."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["status"] = "running"
        for phase, expected in (
            ("preparing", "Preparing"),
            ("publishing", "Publishing"),
            ("pageviews_readiness", "Checking daily availability"),
            ("pageviews_aggregate", "Calculating rankings"),
            ("unknown", "Preparing"),
        ):
            event["progress"] = {"phase": phase}
            output = render_live_status(
                status,
                self.data["config"],
                "en",
                self.now,
            )
            assert f"data-progress-active>{expected}</p>" in output
            assert ">Updating pageview records</a>" in output
        for current, total, expected in (
            (None, None, "Fetching pageviews"),
            (None, 13500, "Fetching pageviews"),
            (0, None, "Fetching pageviews"),
            (0, 13500, "Fetching pageviews (0/13,500)"),
            (0, 0, "Fetching pageviews (0/0)"),
        ):
            event["progress"] = {
                "phase": "pageviews_download",
                "current": current,
                "total": total,
            }
            for output in self._outputs("en"):
                assert f"data-progress-active>{expected}</p>" in output
        event.pop("progress")
        assert "data-progress-active>Preparing</p>" in render_live_status(
            status,
            self.data["config"],
            "en",
            self.now,
        )

    def test_undated_running_labels_name_each_task(self) -> None:
        """Localize active task labels independently of phase notes."""
        status = self.data["status"]
        config = self.data["config"]["routines"]
        definition = config["routines"]["pageviews"]
        schedule = status["schedule"]["routines"]["pageviews"]
        event = status["routines"]["routines"]["pageviews"]
        for routine, labels in (
            (
                "dyks",
                (
                    "Updating DYK recommendations",
                    "更新新条目推荐",
                    "更新新條目推薦",
                ),
            ),
            (
                "new_pages",
                (
                    "Updating new-page records",
                    "更新新进条目记录",
                    "更新新進條目紀錄",
                ),
            ),
            (
                "assessment_lists",
                (
                    "Updating recognized content lists",
                    "更新认证条目列表",
                    "更新認證條目列表",
                ),
            ),
            (
                "assessment_log",
                (
                    "Updating assessment change log",
                    "更新评级变更日志",
                    "更新評級變更日誌",
                ),
            ),
            (
                "pageviews",
                (
                    "Updating pageview records",
                    "更新浏览量记录",
                    "更新瀏覽量紀錄",
                ),
            ),
            (
                "enwp_key_articles",
                (
                    "Updating English Wikipedia comparisons",
                    "更新英文维基百科条目比较报告",
                    "更新英文維基百科條目比較報告",
                ),
            ),
            (
                "pexbot",
                (
                    "Updating database reports",
                    "更新数据库报告",
                    "更新資料庫報告",
                ),
            ),
        ):
            config["routines"] = {routine: definition}
            status["schedule"]["routines"] = {routine: schedule}
            status["routines"]["routines"] = {
                routine: {**event, "routine": routine, "status": "running"},
            }
            for locale, label in zip(
                ("en", "zh-hans", "zh-hant"),
                labels,
                strict=True,
            ):
                for output in self._outputs(locale):
                    card = self._card(output, routine)
                    assert f">{label}</a>" in card
                    assert f'data-progress-label="{label}"' in card
                    assert "data-progress-active" in card

    def test_dated_other_routines_keep_generic_report_labels(self) -> None:
        """Keep other report dates in status labels, not phase notes."""
        status = self.data["status"]
        config = self.data["config"]["routines"]
        config["routines"] = {"new_pages": config["routines"]["pageviews"]}
        status["schedule"]["routines"] = {
            "new_pages": status["schedule"]["routines"]["pageviews"],
        }
        event = status["routines"]["routines"]["pageviews"]
        status["routines"]["routines"] = {"new_pages": event}
        event.update(
            routine="new_pages",
            status="running",
            progress={"phase": "publishing", "report_date": "2026-10-20"},
        )
        for locale, label, note in (
            ("en", "Updating report for October 20, 2026", "Publishing"),
            ("zh-hans", "更新2026年10月20日报告", "发布中"),
            ("zh-hant", "更新2026年10月20日報告", "發布中"),
        ):
            for output in self._outputs(locale):
                card = self._card(output, "new_pages")
                assert f">{label}</a>" in card
                assert f"data-progress-active>{note}</p>" in card

    def test_progress_requires_fresh_active_observations(self) -> None:
        """Hide progress for completed tasks and stale server state."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["progress"] = {
            "phase": "pageviews_download",
            "report_date": "2026-10-20",
        }
        for state in ("updated", "no_update", "failed", "deferred"):
            event["status"] = state
            for output in self._outputs("en"):
                card = self._card(output)
                assert "data-progress-active" not in card
                assert "data-progress-label" not in card
                assert "Updating pageviews for" not in card
        event["status"] = "running"
        schedule = copy.deepcopy(status["schedule"])
        for change in (
            {"state": "paused"},
            {"state": "stopped"},
            {"updated_at": "2026-10-18T11:00:00Z"},
            None,
        ):
            status["schedule"] = (
                {**schedule, **change} if change is not None else None
            )
            for locale, fallback in (
                ("en", "Running"),
                ("zh-hans", "更新中"),
                ("zh-hant", "更新中"),
            ):
                for output in self._outputs(locale):
                    card = self._card(output)
                    assert "data-progress-active" not in card
                    assert "data-progress-label" not in card
                    assert "data-progress-fallback" not in card
                    assert f">{fallback}</a>" in card

    def test_progress_requires_the_current_monitor_session(self) -> None:
        """Hide old running records after a monitor restart."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["status"] = "running"
        event["progress"] = {
            "phase": "pageviews_download",
            "report_date": "2026-10-20",
        }
        status["schedule"]["started_at"] = "2026-10-18T11:59:48Z"
        for started_at, visible in (
            ("2026-10-18T11:59:47Z", False),
            ("2026-10-18T11:59:48Z", True),
            ("2026-10-18T11:59:49Z", True),
        ):
            event["started_at"] = started_at
            for output in self._outputs("en"):
                card = self._card(output)
                label = (
                    "Updating pageviews for October 20, 2026"
                    if visible
                    else "Running"
                )
                assert f">{label}</a>" in card
                assert ("data-progress-active" in card) is visible
                assert ("data-progress-label" in card) is visible

    def test_logs_keep_footer_without_server_status(self) -> None:
        """Keep footer uptime without the removed server panel."""
        for locale in ("en", "zh-hans", "zh-hant"):
            for state in ("running", "paused", "stopped", "stale", "missing"):
                status = copy.deepcopy(self.data["status"])
                if state == "missing":
                    status["schedule"] = None
                elif state == "stale":
                    status["schedule"]["updated_at"] = (
                        self.now - dt.timedelta(seconds=121)
                    ).isoformat()
                else:
                    status["schedule"]["state"] = state
                output = render_logs(
                    {"files": []},
                    locale,
                    status=status,
                    config=self.data["config"],
                    now=self.now,
                )
                assert 'id="live-server-status"' not in output
                assert 'id="server-runtime"' not in output
                assert 'href="#live-server-status"' not in output
                assert 'id="live-uptime"' in output
                assert 'id="tool-status"' not in output
                assert 'id="routine-' not in output
                assert re.search(r'src="/live\.js\?v=[a-f0-9]{64}"', output)
                assert "0.6.0.dev1" in output
                assert ('data-clock="uptime"' in output) == (
                    state in {"running", "paused"}
                )

    def test_running_routines_hide_next_run_until_finished(self) -> None:
        """Show actual timestamps and hide active schedules."""
        status = self.data["status"]
        event = status["routines"]["routines"]["pageviews"]
        event["progress"] = {
            "phase": "pageviews_download",
            "report_date": "2026-10-20",
        }
        event["finished_at"] = (self.now - dt.timedelta(minutes=2)).isoformat()
        event["started_at"] = (self.now - dt.timedelta(minutes=10)).isoformat()
        status["schedule"]["routines"]["pageviews"]["next_run_at"] = (
            self.now + dt.timedelta(minutes=35)
        ).isoformat()
        scheduled = copy.deepcopy(status["schedule"])
        for locale, started, finished, label, ago, ahead in (
            (
                "en",
                "Started at ",
                "Checked at ",
                "Next check ",
                "(2m ago)",
                "(35m from now)",
            ),
            (
                "zh-hans",
                "开始时间\uff1a",
                "检查时间\uff1a",
                "下次检查\uff1a",
                "\uff082分钟前\uff09",
                "\uff0835分钟后\uff09",
            ),
            (
                "zh-hant",
                "開始時間\uff1a",
                "檢查時間\uff1a",
                "下次檢查\uff1a",
                "\uff082分鐘前\uff09",
                "\uff0835分鐘後\uff09",
            ),
        ):
            for state in (
                "running",
                "updated",
                "no_update",
                "failed",
                "deferred",
                "unconfirmed",
                "queued",
                "unknown",
            ):
                event["status"] = state
                live = render_live_status(
                    status,
                    self.data["config"],
                    locale,
                    self.now,
                )
                card = re.search(
                    r'<article[^>]*id="routine-pageviews"[^>]*>(.*?)</article>',
                    live,
                    re.DOTALL,
                )
                assert card is not None
                status_line = re.search(
                    r'<p class="routine-status"[^>]*>(.*?)</p>',
                    card[1],
                    re.DOTALL,
                )
                assert status_line is not None
                assert f'data-status="{state}"' in status_line[0]
                assert status_line[1].startswith(
                    '<a class="status-label status-link"',
                )
                assert "<svg" not in status_line[1]
                assert "<img" not in status_line[1]
                assert 'class="report-icon cdx-card__icon"' in card[1]
                metadata = re.search(
                    r'<ul class="routine-meta">(.*?)</ul>',
                    card[1],
                    re.DOTALL,
                )
                assert metadata is not None
                assert (label in card[1]) == (state != "running")
                if state == "running":
                    assert started in metadata[1]
                    assert finished not in metadata[1]
                    assert event["started_at"] in metadata[1]
                    assert event["finished_at"] not in metadata[1]
                else:
                    assert finished in metadata[1]
                    assert started not in metadata[1]
                    assert event["finished_at"] in metadata[1]
                    assert event["started_at"] not in metadata[1]
                    assert ago in metadata[1]
                    assert ahead in metadata[1]
                assert status["schedule"] == scheduled

    def test_connection_notice_only_describes_failures(self) -> None:
        """Hide the failure notice until synchronization fails."""
        for locale, removed in (
            ("en", "Timers advance every second."),
            ("zh-hans", "计时每秒更新"),
            ("zh-hant", "計時每秒更新"),
        ):
            live = render_live_status(
                self.data["status"],
                self.data["config"],
                locale,
                self.now,
            )
            notice = re.search(
                r'<p id="live-notice"([^>]*)>([^<]+)</p>',
                live,
            )
            assert notice is not None
            assert "hidden" in notice[1]
            assert 'role="status"' in notice[1]
            assert notice[2] == Interface(locale).text("ops.reconnecting")
            assert removed not in live


class ServerPresentationTests(unittest.TestCase):
    """Verify compact saved-server facts and the authored About page."""

    def setUp(self) -> None:
        """Create saved observations and a fixed UTC clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)

    def _live(self, locale: str) -> str:
        """Render saved status without reading runtime data.

        Returns:
            Localized live fragments at the fixed UTC clock.
        """
        return render_live_status(
            self.data["status"],
            self.data["config"],
            locale,
            self.now,
        )

    def test_uptime_sentence_omits_leading_zero_units(self) -> None:
        """Keep observed starts and natural elapsed-time sentences."""
        for locale, prefix, suffix, elapsed in (
            ("en", "started ", ", running for ", "1 hr 7 min 16 sec."),
            ("zh-hans", "自", "启动\uff0c已执行", "1小时7分16秒。"),
            ("zh-hant", "自", "啟動\uff0c已執行", "1小時7分16秒。"),
        ):
            with self.subTest(locale=locale):
                started = self.now - dt.timedelta(
                    hours=1,
                    minutes=7,
                    seconds=16,
                )
                schedule = {
                    "state": "running",
                    "started_at": started.isoformat(),
                    "updated_at": self.now.isoformat(),
                }
                label = uptime(schedule, "0.7.9", Interface(locale), self.now)
                assert prefix + utc_time(started.isoformat()) + suffix in label
                assert label.endswith(elapsed)
                assert "0日" not in label
                assert "0 d" not in label
        for seconds, expected in (
            (0, "0 sec."),
            (59, "59 sec."),
            (60, "1 min 0 sec."),
            (3600, "1 hr 0 min 0 sec."),
            (86400, "1 d 0 hr 0 min 0 sec."),
        ):
            started = self.now - dt.timedelta(seconds=seconds)
            label = uptime(
                {
                    "state": "running",
                    "started_at": started.isoformat(),
                    "updated_at": self.now.isoformat(),
                },
                "0.7.9",
                Interface("en"),
                self.now,
            )
            assert label.endswith(expected)

    def test_about_explains_name_purpose_and_contact(self) -> None:
        """Localize the linked name, explanation, and contact."""
        for locale, purpose, contact in (
            (
                "en",
                "It updates on-wiki reports",
                "If you have questions, contact ",
            ),
            (
                "zh-hans",
                "自动更新站内报告",
                "如有疑问\uff0c请联系",
            ),
            (
                "zh-hant",
                "自動更新站內報告",
                "如有疑問\uff0c請聯絡",
            ),
        ):
            output = render_about(self.data["config"], locale)
            assert "荒波" in output
            assert purpose in output
            assert 'id="server-runtime"' in output
            assert 'id="live-server-status"' in output
            assert 'href="/logs/"' not in output
            assert 'id="credits"' in output
            assert "CC0-1.0" in output
            assert "CC BY-SA 4.0" in output
            assert (
                '<a href="/assets/icons/maintenance/credits.html">'
                + Interface(locale).text("about.maintenance_icons")
                + "</a>"
            ) in output
            assert "wiki/%E4%B8%AD%E6%96%87" in output
            assert "WikiProject%3A" in output
            assert 'id="tool-status"' not in output
            assert 'id="live-uptime"' in output
            assert "data-clock-messages" in output
            assert (
                'href="https://github.com/For-Each-Next/aranami">GitHub</a>'
                in output
            )
            assert (
                contact + '<a href="https://zh.wikipedia.org/wiki/'
                'User_talk:For_Each_..._Next">For Each ... Next</a>' in output
            )
            for removed in (
                "discover-heading",
                "improve-heading",
                "reports-heading",
                "Lists refresh automatically",
                "列表会自动刷新",
                "列表會自動重新整理",
            ):
                assert removed not in output

    def test_server_details_preserve_unknowns_and_escape_saved_text(
        self,
    ) -> None:
        """Keep older snapshots and saved text safe in every locale."""
        status = self.data["status"]
        status["schedule"]["runtime"] = {
            "python": "3.15.0",
            "system": "Linux",
            "machine": "x86_64",
            "memory_bytes": None,
            "memory_limit_bytes": None,
            "cpu_percent": None,
            "cpu_limit": None,
            "python_compiler": "<compiler & build>",
        }
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_about(
                self.data["config"],
                locale,
                status=status,
                now=self.now,
            )
            interface = Interface(locale)
            assert (
                f'<th scope="row">{interface.text("server.hub_version")}'
                f"</th><td>{interface.text('ops.unavailable')}</td>" in output
            )
            for removed in (
                "3.15.0",
                "Linux",
                "x86_64",
                "&lt;compiler &amp; build&gt;",
            ):
                assert removed not in output
            assert "<compiler & build>" not in output
            status["schedule"]["runtime"]["aranami_version"] = (
                "<release & build>"
            )
            escaped = render_about(
                self.data["config"],
                locale,
                status=status,
                now=self.now,
            )
            assert "&lt;release &amp; build&gt;" in escaped
            assert "<release & build>" not in escaped
            del status["schedule"]["runtime"]["aranami_version"]

    def test_server_resources_keep_zero_and_unknown_allocation(self) -> None:
        """Combine resources without inventing missing measurements."""
        runtime = {
            "python": "3.15.0",
            "system": "Linux",
            "machine": "x86_64",
            "memory_bytes": 0,
            "memory_limit_bytes": None,
            "cpu_percent": 0,
            "cpu_limit": 1,
        }
        self.data["status"]["schedule"]["runtime"] = runtime
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = self._live(locale)
            assert f"0 B / {interface.text('ops.unavailable')}" in output
            cores = interface.text("server.cpu_core", count=1)
            assert f"0.0%{' ' if locale == 'en' else ''}{cores}" in output
        runtime.update(memory_bytes=None, memory_limit_bytes=1_073_741_824)
        runtime.update(cpu_percent=None, cpu_limit=0.5)
        output = self._live("en")
        unknown = Interface("en").text("ops.unavailable")
        assert f"{unknown} / 1.0 GiB" in output
        assert f"{unknown} (0.5 cores)" in output
        assert "0.0%" not in output
        runtime.update(cpu_percent=0, cpu_limit=None)
        output = self._live("en")
        assert "0.0% (core allocation unavailable)" in output

    def test_server_sample_and_start_times_keep_actual_seconds(self) -> None:
        """Retain actual seconds and ordinary relative annotations."""
        status = self.data["status"]
        status["schedule"]["started_at"] = "2026-10-18T11:45:07Z"
        status["schedule"]["updated_at"] = "2026-10-18T12:00:02Z"
        for locale, prefix, age, recent in (
            ("en", "Last updated: ", "15m ago", "(just now)"),
            ("zh-hans", "最近更新\uff1a", "15分钟前", "\uff08刚刚\uff09"),
            ("zh-hant", "最近更新\uff1a", "15分鐘前", "\uff08剛剛\uff09"),
        ):
            output = self._live(locale)
            assert "2026-10-18 11:45:07" in output
            assert f" {age}" in output
            assert (
                prefix + '<time datetime="2026-10-18T12:00:02Z">'
                "2026-10-18 12:00:02</time>" in output
            )
            assert f"</time> {recent}" in output
            assert 'data-clock="relative"' not in output
            assert "data-relative-inline" not in output


class EventPresentationTests(unittest.TestCase):
    """Show independent invocations with explicit stage dialogs."""

    def setUp(self) -> None:
        """Create saved observations and a fixed UTC clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)

    @staticmethod
    def _run(index: int = 0, **facts: object) -> dict[str, object]:
        """Return a completed job and its observed execution stages.

        Returns:
            A complete saved history invocation.
        """
        return {
            "run_id": f"{index:032x}",
            "routine": "pageviews",
            "started_at": "2026-10-18T12:00:02Z",
            "finished_at": "2026-10-18T12:00:08Z",
            "status": "updated",
            "duration_seconds": 5.3,
            "truncated": False,
            "stages": [
                {
                    "kind": "started",
                    "time": "2026-10-18T12:00:02Z",
                    "title": "",
                },
                {
                    "kind": "database_updated",
                    "time": "2026-10-18T20:00:04.123456+08:00",
                    "title": "<Long & safe destination>",
                },
                {"kind": "saved", "time": "2026-10-18T12:00:07Z", "title": ""},
                {
                    "kind": "finished",
                    "time": "2026-10-18T12:00:08Z",
                    "title": "",
                },
            ],
            **facts,
        }

    def _render(
        self,
        runs: list[dict[str, object]],
        locale: str = "en",
    ) -> str:
        """Render Reports from validated invocation facts.

        Returns:
            A status panel and independent history table.
        """
        return render_reports(
            {
                "runs": runs,
            },
            locale,
            status=self.data["status"],
            config=self.data["config"],
            now=self.now,
        )

    @staticmethod
    def _rows(output: str) -> list[list[str]]:
        """Return visible cell text from the invocation table."""
        table = re.search(
            r'<table class="[^"]*\blogs-table\b[^"]*">.*?<tbody>(.*?)</tbody>',
            output,
            re.DOTALL,
        )
        if table is None:
            return []
        return [
            [
                unescape(re.sub(r"<[^>]+>", "", cell))
                for cell in re.findall(
                    r"<t[dh]\b[^>]*>(.*?)</t[dh]>",
                    row,
                    re.DOTALL,
                )
            ]
            for row in re.findall(
                r"<tr\b[^>]*>(.*?)</tr>",
                table[1],
                re.DOTALL,
            )
        ]

    def test_reports_keep_status_panel_and_four_column_log(self) -> None:
        """Localize four columns and accessible stage dialogs."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = self._render([self._run()], locale)
            for identifier, key in (
                ("tool-status-heading", "reports.panel"),
                ("recent-events", "reports.log"),
            ):
                assert (
                    f'<h2 id="{identifier}">{interface.text(key)}</h2>'
                    in output
                )
            assert '<dialog id="report-details-pageviews"' in output
            assert 'commandfor="job-history-' in output
            assert 'class="job-stages"' in output
            for stage in ("started", "database_updated", "saved", "finished"):
                assert interface.text(f"reports.stage.{stage}") in output
            stages = re.search(
                r'<ol class="job-stages">(.*?)</ol>',
                output,
                re.DOTALL,
            )
            assert stages is not None
            assert "<br>" not in stages[1]
            assert (
                '<time datetime="2026-10-18T20:00:04.123456+08:00">'
                "2026-10-18 12:00:04.123</time>" in stages[1]
            )
            assert (
                interface.text("reports.stage.database_updated")
                + ' · <span lang="zh"><a href="https://zh.wikipedia.org/wiki/'
                '%3CLong_%26_safe_destination%3E">'
                "&lt;Long &amp; safe destination&gt;</a></span>" in stages[1]
            )
            assert "<Long & safe destination>" not in stages[1]
            assert interface.text("reports.intro") in output
            assert output.count('<script src="/live.js?v=') == 1
            assert 'id="live-server-status"' not in output
            assert "download=1" not in output
            assert self._rows(output) == [
                [
                    "2026-10-18 12:00:02",
                    interface.text("routine.pageviews"),
                    interface.text("logs.result.updated"),
                    interface.text("duration.seconds", seconds="5.30"),
                ],
            ]

    def test_unknown_zero_and_running_outcomes_remain_explicit(self) -> None:
        """Show zero durations and retain unknown active duration."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            for outcome in (
                "updated",
                "no_update",
                "failed",
                "deferred",
                "unknown",
            ):
                rows = self._rows(
                    self._render(
                        [
                            self._run(status=outcome, duration_seconds=0),
                        ],
                        locale,
                    ),
                )
                assert rows[0][2] == interface.text(f"logs.result.{outcome}")
                assert rows[0][3] == interface.text(
                    "duration.seconds",
                    seconds="0.00",
                )
            active = self._run(
                status="running",
                finished_at=None,
                duration_seconds=None,
            )
            rows = self._rows(self._render([active], locale))
            assert rows[0][2] == interface.text("state.Running")
            assert rows[0][3] == "—"
        output = self._render([])
        assert not self._rows(output)
        assert "No saved job invocations" in output

    def test_history_spans_dates_without_query_controls(self) -> None:
        """Show all retained dates without query controls."""
        runs = [
            self._run(
                1,
                started_at="2026-10-17T11:00:00Z",
                status="running",
                finished_at=None,
                duration_seconds=None,
            ),
            self._run(2, status="failed"),
        ]
        for locale in ("en", "zh-hans", "zh-hant"):
            output = self._render(runs, locale)
            assert [row[0] for row in self._rows(output)] == [
                "2026-10-18 12:00:02",
                "2026-10-17 11:00:00",
            ]
            assert 'class="log-query"' not in output
            assert 'class="log-match-count"' not in output
            assert 'class="pagination"' not in output

    def test_history_is_bounded_and_complete(self) -> None:
        """Show twenty recent invocations and their stage dialogs."""
        runs = [
            self._run(
                index,
                started_at=(
                    self.now + dt.timedelta(minutes=index)
                ).isoformat(),
            )
            for index in range(105)
        ]
        for locale in ("en", "zh-hans", "zh-hant"):
            output = self._render(runs, locale)
            rows = self._rows(output)
            assert len(rows) == 20
            assert rows[0][0] == "2026-10-18 13:44:30"
            assert rows[-1][0] == "2026-10-18 13:25:30"
            assert output.count('<dialog id="job-history-') == 20
            assert Interface(locale).text("reports.history_note") in output
        output = self._render([
            self._run(truncated=True),
        ])
        assert Interface("en").text("reports.stage_truncated") in output


if __name__ == "__main__":
    if "--render-fixtures" in sys.argv:
        fixture_parser = argparse.ArgumentParser(
            description="Write synthetic localized browser-check pages.",
        )
        fixture_parser.add_argument(
            "--render-fixtures",
            type=Path,
            required=True,
        )
        write_interface_fixtures(fixture_parser.parse_args().render_fixtures)
    else:
        unittest.main()
