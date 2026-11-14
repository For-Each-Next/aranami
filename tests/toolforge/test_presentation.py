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
import re
import sys
import tempfile
import unittest
from hashlib import sha256
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import polars as pl
from analysis_fixtures import saved_analysis_fixture
from status_fixtures import (
    active_collection_record,
    executions,
    report_card_snapshot,
    status_snapshot,
)
from test_consistency_presentation import consistency_snapshot
from test_dataset_viewer import dataset_fixture
from test_maintenance_controls import (
    _snapshot as maintenance_snapshot,  # ruff: ignore[import-private-name]
)

from shared.config import routine_configuration
from shared.deployment import resource_root
from webservice.pages import (
    render_about,
    render_attribution,
    render_consistency_checks,
    render_dashboard,
    render_dataset,
    render_discussions,
    render_error,
    render_logs,
    render_maintenance,
    render_maintenance_overview,
    render_newsletter,
    render_quality_analysis,
    render_quality_authorship,
    render_reports,
)
from webservice.presentation import (
    Interface,
    article_extract,
    assessment_code,
    assessment_rank,
    catalog,
    duration,
    homepage_sections,
    local_url,
    maintenance_items,
    maintenance_page,
    maintenance_sections,
    maintenance_selection,
    merged_items,
    pageviews_url,
    readable_size,
    relative_time,
    select_locale,
    timestamp,
    title_url,
    utc_time,
    wiki_link,
    wiki_url,
)


def _fixtures() -> dict[str, object]:
    """Build saved observations with hostile text and all sections.

    Returns:
        Independent website, routine, scheduler, and source fixtures.
    """
    config = {
        "version": "0.6.0.dev1",
        "routines": routine_configuration(),
    }
    status = status_snapshot()
    stamp = "2026-10-18T12:00:00Z"
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


def _quality_authorship_fixture() -> dict:
    """Build whole-article author statistics for browser checks.

    Returns:
        Three authors with article pagination, missing activity and long
        canonical names and titles. Qualified coauthors each receive one
        article count, independently of other authors.
    """
    grades = ("FA", "FL", "GA", "Bplus")
    articles = [
        {
            "page_id": index,
            "title": "LongUnbrokenQualityArticleIdentifier" * 6
            if index == 1
            else f"Quality article {index}",
            "grade": grades[(index - 1) % len(grades)],
            "date": f"2026-10-{index:02d}",
            "nomination_link": "#典范条目评选",
            "coauthor_count": 1,
            "credit": 1,
        }
        for index in range(1, 13)
    ]
    shared = {
        "page_id": 201,
        "title": "Hypothetical jointly attributed article",
        "grade": "GA",
        "date": "2022-06-01",
        "coauthor_count": 2,
        "coauthors": ["Author A", "Author B"],
        "credit": 1,
    }
    authors = [
        {
            "name": "LongUnbrokenQualityAuthorIdentifier" * 5,
            "last_active": "2026-10-17T12:00:00Z",
            "credit": 12,
            "grades": dict.fromkeys(grades, 3),
            "articles": list(reversed(articles)),
        },
        *[
            {
                "name": name,
                "last_active": "2024-09-01T12:00:00Z" if index == 0 else None,
                "credit": 1,
                "grades": {"GA": 1},
                "articles": [shared],
            }
            for index, name in enumerate(("Author A", "Author B"))
        ],
    ]
    return {
        "available": True,
        "observed_at": "2026-10-18T12:00:00Z",
        "authors": authors,
    }


def _b_quality_authorship_fixture() -> dict:
    """Build the saved B-Class response for asynchronous switch checks.

    Returns:
        A directory with an additional B article and quality subset.
    """
    b_directory = _quality_authorship_fixture()
    b_directory["include_b"] = True
    b_directory["b_coverage"] = {"collected": 1, "total": 1}
    author = b_directory["authors"][0]
    author["high_quality_credit"] = author["credit"]
    author["credit"] += 1
    author["grades"]["B"] = 1
    author["articles"].append({
        "page_id": 500,
        "title": "B listing fixture",
        "grade": "B",
        "date": "2026-10-01",
        "nomination_link": "#B_listing",
        "revision_id": 100,
        "coauthor_count": 1,
        "credit": 1,
    })
    return b_directory


def _quality_documents(
    destination: Path,
    fixture: dict,
    analysis: dict,
    locale: str,
    now: dt.datetime,
) -> dict[str, str]:
    """Write deferred resources beside the localized quality fixtures.

    Returns:
        Default and B-inclusive pages with production resource links.
    """
    documents = {}
    for include_b in (False, True):
        resources = {}
        prefix = (
            f"/quality-analysis/resources/{locale}/{int(include_b)}/current/"
        )
        surface = "quality-analysis-b" if include_b else "quality-analysis"
        documents[surface] = render_quality_analysis(
            saved_analysis_fixture(now, include_b=True)
            if include_b
            else analysis,
            fixture["status"],
            fixture["config"],
            locale,
            now,
            resources=resources,
            resource_prefix=prefix,
        )
        resource_directory = destination / prefix.lstrip("/")
        resource_directory.mkdir(parents=True, exist_ok=True)
        for name, content in resources.items():
            (resource_directory / name).write_text(content)
    return documents


def write_interface_fixtures(destination: Path) -> None:
    """Write localized synthetic pages for offline browser verification.

    The disposable output includes every review type and hostile or long
    text. It contains no runtime data and performs no external requests.

    Args:
        destination: Directory receiving localized browser-check pages.
    """
    fixture = _fixtures()
    fixture["status"] = report_card_snapshot()
    for index, run in enumerate(executions(25).values(), start=1000):
        run["run_id"] = f"{index:032x}"
        fixture["status"]["executions"][run["run_id"]] = run
    for index, task_id in enumerate(("website", "project-data"), start=2000):
        run = active_collection_record(task_id, index)
        run["progress"]["source"]["target"] = (
            "CurrentWebsiteSourceWithLongUnbrokenIdentifier" * 4
        )
        run["progress"]["source"]["references"][0]["value"] = run["progress"][
            "source"
        ]["target"]
        fixture["status"]["executions"][run["run_id"]] = run
    fixture["config"]["routines"] = routine_configuration()
    fixture["status"]["services"]["scheduler"]["resources"] = {
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
    snapshot["consistency"] = consistency_snapshot()["consistency"]
    snapshot["consistency"]["items"] = snapshot["consistency"]["items"][:2]
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
                "started_at": {
                    "fac": "2026-06-08T12:00:00Z",
                    "flc": "2024-08-18T12:00:00Z",
                    "gan": "2026-10-14T12:00:00Z",
                    "bpan": "2026-10-18T09:00:00Z",
                    "pr": "2026-10-18T11:30:00Z",
                }.get(kind, "2026-10-07T03:04:00Z"),
                "replied_at": None
                if kind == "fac"
                else "2026-10-18T10:00:00Z",
                "participant_count": 1 if kind == "fac" else 3,
                "comment_count": 1 if kind == "fac" else 5,
                "section_bytes": 35_000 if kind == "fac" else 1000,
                **(
                    {
                        "latest_comment_at": "2026-06-08T12:00:00Z",
                        "latest_comment_by": "OpeningEditor",
                        "latest_comment_url": "https://zh.wikipedia.org/wiki/Talk:Game#c-Solo",
                        "nominator": "OpeningEditor",
                    }
                    if kind == "fac"
                    else {}
                ),
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
    snapshot["reviews"]["discussions"] = [
        {
            "title": "LongUnbrokenDiscussionTopicIdentifier" * 5
            + "討論專題來源列表",
            "url": "https://zh.wikipedia.org/wiki/WikiProject_talk:电子游戏#Sources",
            "review_url": "https://zh.wikipedia.org/wiki/WikiProject_talk:电子游戏#Sources",
            "review_type": "discussion",
            "subject_title": "WikiProject:电子游戏",
            "subject_url": "https://zh.wikipedia.org/wiki/WikiProject:电子游戏",
            "assessment_class": "Project",
            "assessment_importance": "NA",
            "section_bytes": 35_000,
            "source_title": "WikiProject talk:电子游戏/"
            + "LongSourcePageIdentifier" * 5,
            "source_revision": 95405188,
            "started_at": "2026-10-07T03:04:00Z",
            "replied_at": "2026-10-18T10:00:00Z",
            "nominator": "LongOpeningEditorIdentifier" * 5,
            "replied_by": "LongLatestCommentAuthorIdentifier" * 5,
            "replied_url": "https://zh.wikipedia.org/wiki/WikiProject_talk:电子游戏#c-Latest-20261018100000",
            "participant_count": 12,
            "comment_count": 31,
            "extract": (
                "An opening discussion about improving the project's "
                "reliable-source list. This saved excerpt ends before "
                "later replies…"
            ),
            "extract_zh": (
                "我们需要完善专题的可靠来源列表\uff0c"
                "先整理当前仍然适用的出版物和编辑标准。"
                "讨论也包括新来源的审核方法\uff0c"
                "以及如何记录适用范围和限制…"
            ),
            "extract_hant": (
                "我們需要完善專題的可靠來源列表\uff0c"
                "先整理目前仍然適用的出版物和編輯標準。"
                "討論也包括新來源的審核方法\uff0c"
                "以及如何記錄適用範圍和限制…"
            ),
        },
    ]
    for title, replied_at, participants, comments in (
        ("Recent reply", "2026-10-18T11:00:00Z", 2, 2),
        ("Hot topic", "2026-10-16T12:00:00Z", 3, 10),
        ("Ordinary topic", "2026-10-12T12:00:00Z", 2, 9),
    ):
        snapshot["reviews"]["discussions"].append({
            **snapshot["reviews"]["discussions"][0],
            "title": title,
            "replied_at": replied_at,
            "participant_count": participants,
            "comment_count": comments,
            "extract": "An observed opening comment.",
            "extract_zh": "已觀察到的開場留言。",
            "extract_hant": "已觀察到的開場留言。",
        })
    maintenance_rows = maintenance["backlog"]["groups"][0]["items"]
    maintenance_rows[0]["title"] = "LongUnbrokenArticleIdentifier" * 7
    maintenance_rows[1]["title"] = "含有較長中文名稱的遊戲條目" * 5
    maintenance_rows[1]["oldest_issue_month"] = "2019-01"
    fixture["status"]["publications"]["target"]["title"] = (
        "WikiProject:电子游戏/" + "LongReportIdentifier" * 9
    )
    now = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)
    discussion_snapshot = copy.deepcopy(snapshot)
    discussion_rows = discussion_snapshot["reviews"]["discussions"]
    for subject, grade, days in (
        ("Older featured article", "FA", 6),
        ("Newer stub article", "Stub", 1),
        ("Second-week article", "GA", 10),
        ("Third-week article", "B", 17),
        ("Fourth-week article", "C", 28),
        ("Draft:Proposed game article", "Draft", 15),
        ("Category:Video game topics", "Category", 4),
        ("Template:Video game navigation", "Template", 3),
        ("Game title disambiguation", "Disambig", 5),
        ("Game title redirect", "Redirect", 8),
    ):
        discussion_rows.append({
            **discussion_rows[0],
            "title": "Discuss " + subject,
            "subject_title": subject,
            "subject_url": title_url(subject),
            "source_title": "Talk:" + subject,
            "review_url": title_url("Talk:" + subject) + "#Topic",
            "assessment_class": grade,
            "replied_at": (now - dt.timedelta(days=days)).isoformat(),
            "started_at": (now - dt.timedelta(days=40)).isoformat(),
            "extract": "A saved opening comment about this subject.",
            "extract_zh": "关于这一页面的已保存开场留言。",
            "extract_hant": "關於這一頁面的已儲存開場留言。",
        })
    events = [
        {
            "schema": 1,
            "kind": "routine_finished",
            "time": "2026-10-18T12:00:02Z",
            "routine": "pageviews",
            "dry": False,
            "outcome": "succeeded",
            "updated": 1,
            "duration_seconds": 73.7,
        },
        {
            "schema": 1,
            "kind": "routine_finished",
            "time": "2026-10-18T11:00:02Z",
            "routine": "pageviews",
            "dry": False,
            "outcome": "failed",
            "updated": None,
            "duration_seconds": 6.83,
        },
    ]
    logs = {
        "files": [
            {
                "date": "2026-10-18",
                "name": "20261018.000.log",
                "stream": "diagnostics",
                "size": 1024,
            },
        ],
        "date": "2026-10-18",
        "events": events,
        "dates": ["2026-10-18"],
        "query": "",
    }
    for index, event in enumerate(events):
        event.update(
            event_id=f"{index:032x}",
            boot_id="1" * 32,
            run_id=f"{index:032x}",
            parent_run_id=None,
            attempt_id=None,
            level="INFO",
        )
    logs["availability"] = "available"
    logs["files"][0]["stream"] = "diagnostics"
    logs["files"][0]["name"] = "20261018.000.log"
    stored = destination / "data/parsed/assessments/zhwiki/Video games"
    stored.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "page_id": [1],
        "pa_class": ["GA"],
        "pa_importance": ["Mid"],
    }).write_parquet(stored / "assessments-20260808.parquet")
    analysis = saved_analysis_fixture(now)
    for locale in ("en", "zh-hans", "zh-hant"):
        directory = destination / locale
        directory.mkdir(parents=True, exist_ok=True)
        documents = {
            **_quality_documents(destination, fixture, analysis, locale, now),
            "home": render_dashboard(
                snapshot,
                fixture["status"],
                fixture["config"],
                locale,
                now,
            ),
            "discussions": render_discussions(
                discussion_snapshot,
                fixture["status"],
                fixture["config"],
                locale,
                now,
            ),
            "quality-authorship-b": render_quality_authorship(
                _b_quality_authorship_fixture(),
                fixture["status"],
                fixture["config"],
                locale,
                now,
            ),
            "quality-authorship": render_quality_authorship(
                _quality_authorship_fixture(),
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
            "checks": render_consistency_checks(
                snapshot,
                fixture["status"],
                fixture["config"],
                locale,
            ),
            "maintenance": render_maintenance(
                maintenance,
                {"task": ["references"]},
                locale,
            ),
            "consistency": render_maintenance(
                consistency_snapshot(),
                {"task": ["consistency_history"]},
                locale,
            ),
            "quarterly": render_maintenance(
                consistency_snapshot(),
                {"task": ["consistency_quarterly"]},
                locale,
            ),
            "reports": render_reports(
                logs,
                locale,
                status=fixture["status"],
                config=fixture["config"],
                now=now,
            ),
            "reports2": render_reports(
                logs,
                locale,
                status=fixture["status"],
                config=fixture["config"],
                now=now,
                history_page=2,
            ),
            "logs": render_logs(
                logs,
                locale,
                status=fixture["status"],
                config=fixture["config"],
                now=now,
            ),
            "dataset": render_dataset(
                dataset_fixture(),
                locale,
                status=fixture["status"],
                config=fixture["config"],
            ),
            "dataset2": render_dataset(
                {**dataset_fixture(), "page": 2},
                locale,
                status=fixture["status"],
                config=fixture["config"],
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
        for surface, path in {
            "contentcredits": "/assets/icons/credits.html",
            "maintenancecredits": "/assets/icons/maintenance/credits.html",
            "codex": "/codex-license.html",
            "gpl": "/assets/icons/gpl-2.0.html",
            "lgpl": "/assets/icons/lgpl-2.1.html",
        }.items():
            documents[surface] = render_attribution(
                path,
                locale,
                status=fixture["status"],
                config=fixture["config"],
                now=now,
            )
        for surface, document in documents.items():
            (directory / f"{surface}.html").write_text(document)


class ReviewPresentationTests(unittest.TestCase):
    """Render review cards from saved observations in every language."""

    def setUp(self) -> None:
        """Create saved observations and a deterministic clock."""
        self.data = _fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.en = Interface("en")

    def test_voting_status_uses_deadline_and_observed_result(self) -> None:
        """Separate elapsed voting time from observed outcomes."""
        snapshot = self.data["snapshot"]
        future = (self.now + dt.timedelta(seconds=1)).isoformat()
        for kind in ("dykc", "gan", "fac", "flc"):
            for result, ends, expected in (
                (None, future, "ongoing"),
                (None, self.now.isoformat(), "ended"),
                ("passed", future, "passed"),
                ("failed", future, "failed"),
                ("ended", future, "ended"),
                (None, None, "ongoing"),
            ):
                snapshot["reviews"] = {
                    "items": [
                        {
                            **self.data["article"],
                            "review_type": kind,
                            "review_url": "https://zh.wikipedia.org/wiki/Review#Game",
                            "started_at": (
                                self.now - dt.timedelta(days=1)
                            ).isoformat(),
                            "ends_at": ends,
                            "result": result,
                        },
                    ],
                }
                for locale in ("en", "zh-hans", "zh-hant"):
                    with self.subTest(
                        kind=kind,
                        result=result,
                        ends=ends,
                        locale=locale,
                    ):
                        rendered = render_dashboard(
                            snapshot,
                            {},
                            {},
                            locale,
                            self.now,
                        )
                        label = Interface(locale).text(f"reviews.{expected}")
                        assert (
                            f'href="https://zh.wikipedia.org/wiki/Review#Game">{label}</a>'
                            in rendered
                        )
                        assert (
                            Interface(locale).text("reviews.link") + "</a>"
                            not in rendered
                        )

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
                assert re.search(
                    rf'<time datetime="{re.escape(source)}" '
                    r'title="[^"]*2026-10-18 08:00[^:"]*" '
                    r'aria-label="[^"]+">',
                    dashboard,
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
                    assert (
                        "UTC" not in visible
                        or 'id="recent-events"' in document
                    )
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
                    section.count('class="review-icon-link ui-card__icon"')
                    == 9
                )
                assert section.count('class="review-icon"') == 9
                for index in range(9):
                    link_label = interface.text(
                        "reviews.ongoing"
                        if index in {0, 1, 3, 7}
                        else "reviews.link",
                    )
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
                    link_label = interface.text(
                        "reviews.ongoing"
                        if index in {0, 1, 3, 7}
                        else "reviews.link",
                    )
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
                    r'<time datetime="([^"]+)" title="([^"]+)" '
                    r'aria-label="([^"]+)">([^<]+)</time>',
                    section,
                )
                assert [
                    (value, description, elapsed)
                    for value, _, description, elapsed in review_times
                ] == [
                    (
                        "2026-10-07T03:04:00Z",
                        interface.text("reviews.started_weeks", count=1),
                        interface.text("reviews.age_weeks", count=1),
                    ),
                    (
                        "2026-10-16T12:00:30Z",
                        interface.text("reviews.replied_days", count=2),
                        interface.text("reviews.age_days", count=2),
                    ),
                    (
                        "2026-10-18T10:00:00Z",
                        interface.text("reviews.started_hours", count=2),
                        interface.text("reviews.age_hours", count=2),
                    ),
                    (
                        "2026-10-18T10:00:00Z",
                        interface.text("reviews.replied_hours", count=2),
                        interface.text("reviews.age_hours", count=2),
                    ),
                ]
                for value, tooltip, description, _ in review_times:
                    assert description in tooltip
                    assert utc_time(value) in tooltip
                    assert utc_time(value, seconds=True) not in tooltip
                assert "Review 0 &lt;&amp;&gt;" in section
                if locale != "zh":
                    assert (
                        "&lt;script&gt;never execute&lt;/script&gt;" in section
                    )
                assert "#Review_0" in section
                assert 'data-assessment="class"' not in rendered
                assert 'data-assessment="importance"' not in rendered
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
            for hours in (-1, 0, 23, 24, 48, None):
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
                    if hours is None or hours < 0:
                        assert section.count("<time") == 1
                        continue
                    assert section.count("<time") == 2
                    label = interface.text(
                        "reviews.replied_days"
                        if hours >= 24
                        else "reviews.replied_hours"
                        if hours
                        else "reviews.replied_just_now",
                        count=hours // 24 if hours >= 24 else hours,
                    )
                    compact = interface.text(
                        "reviews.age_days"
                        if hours >= 24
                        else "reviews.age_hours"
                        if hours
                        else "reviews.age_just_now",
                        count=hours // 24 if hours >= 24 else hours,
                    )
                    tooltip = interface.text(
                        "reviews.time_details",
                        event=label,
                        time=utc_time(row["replied_at"]),
                    )
                    assert (
                        f'<time datetime="{row["replied_at"]}" '
                        f'title="{tooltip}" aria-label="{label}">'
                        f"{compact}</time>" in section
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
                assert (
                    '<span class="review-mark" aria-hidden="true">+</span>'
                    in items[0]
                )
                assert (
                    '<span class="review-mark" aria-hidden="true">'
                    "\u203a</span>" in items[1]
                )
                assert '<span aria-hidden="true">☺\ufe0e 3</span>' in items[2]
                assert "\ufe0f" not in items[2]

    def test_review_ages_keep_fixed_units_and_exact_timestamps(
        self,
    ) -> None:
        """Share compact age units while retaining exact hover times."""
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
                (dt.timedelta(seconds=0), "just_now", 0, "just now"),
                (dt.timedelta(seconds=59), "just_now", 0, "just now"),
                (dt.timedelta(minutes=1), "minutes", 1, "1m"),
                (dt.timedelta(minutes=59), "minutes", 59, "59m"),
                (dt.timedelta(hours=1), "hours", 1, "1h"),
                (dt.timedelta(hours=23), "hours", 23, "23h"),
                (dt.timedelta(days=1), "days", 1, "1d"),
                (dt.timedelta(days=7, seconds=-1), "days", 6, "6d"),
                (dt.timedelta(days=7), "weeks", 1, "1w"),
                (dt.timedelta(days=14), "weeks", 2, "2w"),
                (month - dt.timedelta(seconds=1), "weeks", 4, "4w"),
                (month, "months", 1, "1mo"),
                (month + dt.timedelta(seconds=1), "months", 1, "1mo"),
                (2 * month, "months", 2, "2mo"),
                (dt.timedelta(days=132), "months", 4, "4mo"),
                (8 * month, "months", 8, "8mo"),
                (12 * month - dt.timedelta(seconds=1), "months", 11, "11mo"),
                (12 * month, "years", 1, "1yr"),
                (24 * month, "years", 2, "2yr"),
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
                        r'title="([^"]+)" aria-label="([^"]+)">'
                        r"([^<]+)</time>",
                        output,
                    )
                    assert len(review_times) == 2
                    for event, (tooltip, description, age) in zip(
                        ("started", "replied"),
                        review_times,
                        strict=True,
                    ):
                        assert description == interface.text(
                            f"reviews.{event}_{unit}",
                            count=count,
                        )
                        assert description in tooltip
                        assert utc_time(instant) in tooltip
                        assert utc_time(instant, seconds=True) not in tooltip
                        assert age == (
                            "just now"
                            if locale == "en" and unit == "just_now"
                            else f"{compact} ago"
                            if locale == "en"
                            else interface.text(
                                f"reviews.age_{unit}",
                                count=count,
                            )
                        )

    def test_known_participant_counts_survive_missing_reply_times(
        self,
    ) -> None:
        """Keep participant totals independently of reply dates."""
        row = {
            **self.data["article"],
            "review_type": "fac",
            "review_url": "https://zh.wikipedia.org/wiki/Talk:Game#Review",
            "started_at": "2026-10-07T03:04:00Z",
        }
        snapshot = self.data["snapshot"]
        snapshot["reviews"] = {"items": [row]}
        for locale in ("en", "zh-hans", "zh-hant", "zh"):
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
                    if count is None:
                        assert 'class="review-participants"' not in output
                    else:
                        label = Interface(locale).text(
                            "reviews.participants",
                            count=str(count),
                        )
                        assert re.search(
                            r'<span class="review-participants" '
                            rf'title="{label}">'
                            f'<span aria-hidden="true">☺\ufe0e {count}</span>'
                            rf'<span class="visually-hidden">{label}</span>',
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
                    if fixture.stem == "home":
                        assert "discussions" in targets
                        assert 'id="article-discussions-1"' in output
                        assert "LongOpeningEditorIdentifier" in output
                        assert "LongLatestCommentAuthorIdentifier" in output


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
                re.findall(r"\{([a-z_][a-z0-9_]*)\}", translations["en"]),
            )
            for message in translations.values():
                assert message
                assert (
                    sorted(re.findall(r"\{([a-z_][a-z0-9_]*)\}", message))
                    == parameters
                )

    def test_percentile_placeholders_accept_digits_and_preserve_values(
        self,
    ) -> None:
        """Replace percentile names while preserving supplied values."""
        values = {
            "pages": 4,
            "measured": 3,
            "minimum": "0",
            "p25": "0",
            "p50": "1,234.5",
            "p75": "—",
            "maximum": "9,876",
        }
        key = "quality_analysis.violin_summary"
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                interface = Interface(locale)
                assert interface.text(key, **values) == catalog()[key][
                    locale
                ].format(**values)
                assert interface.text(key) == catalog()[key][locale]
                partial = interface.text(key, p50="<span>{p75}</span>")
                assert "<span>{p75}</span>" in partial
                assert "{p25}" in partial

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
        assert readable_size(1024) == "1.0 KiB"
        assert readable_size(10) == "10 B"

    def test_relative_times_use_whole_units_from_minutes_to_years(
        self,
    ) -> None:
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
            (-604799, "(6d ago)"),
            (604799, "(6d from now)"),
            (-604800, "(1w ago)"),
            (604800, "(1w from now)"),
            (-1209600, "(2w ago)"),
            (1209600, "(2w from now)"),
            (-2629799, "(4w ago)"),
            (2629799, "(4w from now)"),
            (-2629800, "(1mo ago)"),
            (2629800, "(1mo from now)"),
            (-5259600, "(2mo ago)"),
            (5259600, "(2mo from now)"),
            (-31557599, "(11mo ago)"),
            (31557599, "(11mo from now)"),
            (-31557600, "(1yr ago)"),
            (31557600, "(1yr from now)"),
            (-63115200, "(2yr ago)"),
            (63115200, "(2yr from now)"),
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

    def test_relative_age_units_follow_interface_language(self) -> None:
        """Use English abbreviations and Chinese time unit labels."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            for seconds, unit, english, hans, hant in (
                (60, "minutes", "m", "分钟", "分鐘"),
                (3600, "hours", "h", "小时", "小時"),
                (86400, "days", "d", "日", "日"),
                (604800, "weeks", "w", "周", "週"),
                (2629800, "months", "mo", "月", "月"),
                (31557600, "years", "yr", "年", "年"),
            ):
                suffix = (
                    english
                    if locale == "en"
                    else hans
                    if locale == "zh-hans"
                    else hant
                )
                for count in (1, 2):
                    age = (
                        f"{count}{suffix} ago"
                        if locale == "en"
                        else f"{count}{suffix}前"
                    )
                    assert (
                        interface.text(f"reviews.age_{unit}", count=count)
                        == age
                    )
                    for event, english_verb, hans_verb, hant_verb in (
                        ("started", "Started", "发起", "發起"),
                        ("replied", "Last comment", "最后留言", "最後留言"),
                    ):
                        expected_event = (
                            f"{english_verb} {age}"
                            if locale == "en"
                            else age
                            + (hans_verb if locale == "zh-hans" else hant_verb)
                        )
                        assert (
                            interface.text(
                                f"reviews.{event}_{unit}",
                                count=count,
                            )
                            == expected_event
                        )
                for direction in (-1, 1):
                    with self.subTest(
                        locale=locale,
                        suffix=suffix,
                        direction=direction,
                    ):
                        instant = (
                            self.now
                            + dt.timedelta(seconds=seconds * direction)
                        ).isoformat()
                        if locale == "en":
                            word = "ago" if direction < 0 else "from now"
                            expected = f"(1{suffix} {word})"
                        else:
                            word = (
                                "前"
                                if direction < 0
                                else "后"
                                if locale == "zh-hans"
                                else "後"
                            )
                            expected = f"\uff081{suffix}{word}\uff09"
                        assert (
                            relative_time(instant, interface, self.now)
                            == expected
                        )
            assert (
                relative_time(
                    (self.now + dt.timedelta(seconds=30)).isoformat(),
                    interface,
                    self.now,
                )
                == {
                    "en": "(within 1m)",
                    "zh-hans": "\uff081分钟内\uff09",
                    "zh-hant": "\uff081分鐘內\uff09",
                }[locale]
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
        assert {item["title"] for item in sections["recognized"][:2]} == {
            self.data["article"]["title"],
            "Scheduled game",
        }
        assert len(sections["pageviews"]) == 6
        assert len(sections["recognized"]) == 6
        assert sections["pageviews"][-1]["title"] == "Popular game 6"
        assert sections["recognized"][-1]["title"] == "DYK game 3"
        assert len(self.data["snapshot"]["pageviews"]["items"]) == 13
        assert set(sections) == {"pageviews", "recognized"}
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
        """Keep appearance years and compact popularity date labels."""
        snapshot = copy.deepcopy(self.data["snapshot"])
        snapshot["pageviews"]["date"] = "2026-10-31"
        snapshot["pageviews"]["items"][0]["title"] = "Date popularity"
        examples = {
            "featured": ("Date showcase", "2026-12-31", "fa", "observed"),
            "upcoming": ("Date upcoming", "2027-01-01", "ga", "scheduled"),
            "dyk": ("Date DYK", "2026-10-31", "dyk", "observed"),
            "news": ("Date news", "2026-12-31", "news", "observed"),
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
            "Date popularity": ("2026-10-31", "Oct 31", "Oct 31"),
            "Date showcase": ("2026-12-31", "Dec 31, 2026", "Dec 31, 2026"),
            "Date DYK": ("2026-10-31", "Oct 31, 2026", "Oct 31, 2026"),
            "Date news": ("2026-12-31", "Dec 31, 2026", "Dec 31, 2026"),
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
                    assert ">Date upcoming</a>" in output
                    for title, dates in expected.items():
                        if title == "Date news" and reference_index == 2:
                            assert ">Date news</a>" not in output
                            continue
                        card = next(
                            body for body in cards if f">{title}</a>" in body
                        )
                        date = dates[0]
                        label = (
                            dates[reference_index]
                            if locale == "en"
                            else "10月31日"
                            if title == "Date popularity"
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
        for section in ("good", "dyk", "news", "history"):
            snapshot["homepage"][section] = []
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
                assert '<span class="homepage-type">' in items[0]
                assert ">TFA</abbr>" not in items[0]
                key = (
                    "homepage.scheduled_type.tfa"
                    if date_kind == "scheduled"
                    else "homepage.type.tfa"
                )
                assert interface.text(key) in unescape(
                    items[0],
                )
                label = (
                    "Oct 13, 2026"
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
        homepage["upcoming"] = []
        expectations = {
            "featured": (
                ("Featured once", "fa", "B", "featured"),
                ("Featured list once", "fl", "GA", "featured"),
                ("Unspecified featured once", "ga", "Stub", "featured"),
            ),
            "good": (("Good once", "fa", "FA", "good"),),
            "dyk": (
                ("DYK now featured", "fa", "FA", "dyk"),
                ("DYK now good", "ga", "GA", "dyk"),
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
                for title, _, _assessment, icon in items:
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
                    assert 'data-assessment="' not in card
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
                assert 'class="article-medal ui-card__icon"' in card[1]
                assert f"/assets/icons/medal-{rank}.svg?v=" in card[1]
                assert 'alt="" aria-hidden="true"' in card[1]
            assert (
                "article-medal"
                not in output.split(
                    'id="homepage-recognized"',
                    1,
                )[1].split("</section>", 1)[0]
            )
        assert snapshot == original

    def _assert_popularity_date_prefix(self) -> None:
        """Show month and day with full historical date provenance."""
        popularity = self.data["snapshot"]["pageviews"]
        popularity["date"] = "2025-10-31"
        popularity["items"][0]["views"] = 10_110
        for locale, date_label, views_label, rank_label in (
            (
                "en",
                "Oct 31",
                "10,110 views",
                "14th site-wide",
            ),
            (
                "zh-hans",
                "10月31日",
                "10,110次浏览",
                "全站第14名",
            ),
            (
                "zh-hant",
                "10月31日",
                "10,110次瀏覽",
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
                        '<time datetime="2025-10-31" title="2025-10-31">'
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
                    assert unescape(re.sub(r"<[^>]+>", "", lead)) == (
                        date_label
                    )
                    assert unescape(re.sub(r"<[^>]+>", "", items[0])) == (
                        views_label
                    )
                    assert len(items) == (2 if rank is not None else 1)
                    if rank is not None:
                        assert rank_label in items[1]
                    assert (
                        '<ul class="metadata-items" role="list">' in metadata
                    )
                    assert " · " not in metadata

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
            assert "DYK game 3" in output
            assert "DYK game 4" not in output
            assert "News game 7" not in output
            assert output.count('class="article-extract"') == 7
            assert 'class="extract-attribution"' not in output
            for key in ("pageviews", "recognized"):
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
        assert 'id="homepage-recognized"' in render_dashboard(
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
                            "name": "20261018.000.log",
                            "stream": "diagnostics",
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
            assert "&lt;script&gt;bad&lt;/script&gt;" in output
            assert 'id="recent-events"' not in output
            assert 'class="log-query"' not in output
            assert "<pre" not in output
            assert "1.0 KiB" in output
            assert 'file=20261018.000.log"' in output
            assert "download=1" not in output
            assert " download>" not in output
            assert (
                '<caption class="visually-hidden">'
                + Interface(locale).text("logs.archive")
                + "</caption>"
            ) in output
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
            assert (
                "/components.js" in {urlsplit(asset).path for asset in assets}
            ) == (
                'id="log-activity"' in output or 'id="recent-events"' in output
            )
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
            assert (
                output.count('<script type="module" src="/components.js?v=')
                == 1
            )
        assert not list(root.rglob("*.php"))
        assert set(root.rglob("*.js")) == {
            root / "public_html" / "consistency-repair.user.js",
            root / "public_html" / "components.js",
            root / "public_html" / "live.js",
            root / "public_html" / "newsletter.js",
            root / "public_html" / "navigation.js",
            *(
                root / "public_html" / f"{name}.js"
                for name in (
                    "copy",
                    "datasets",
                    "heading_links",
                    "history",
                    "maintenance",
                    "quality_authorship",
                    "quality_analysis",
                    "quality_matrix",
                )
            ),
        }
        assert not list(root.rglob("*.mjs"))


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
