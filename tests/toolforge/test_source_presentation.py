"""Verify linked sources and natural execution-stage prose."""

# User-facing Chinese sentences retain their authored punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

import copy
import datetime as dt
import re
from functools import partial
from html import unescape
from unittest import TestCase
from urllib.parse import parse_qs, urlsplit

from status_fixtures import status_snapshot

from webservice.job_history import (
    history_context as _history_context,
    source_description,
)
from webservice.presentation import Interface

history_context = partial(_history_context, recent_only=False)


class TestSourcePresentation(TestCase):
    """Keep source links safe and old records readable."""

    def setUp(self) -> None:
        """Prepare a complete source identity."""
        self.source = {
            "provider": "mediawiki",
            "dataset": "page_history",
            "wiki": "enwiki",
            "target": "Title, with comma",
            "date": "2026-11-02",
            "references": [{"kind": "page", "value": "Title, with comma"}],
            "reference_count": 1,
        }

    def test_titles_remain_single_linked_identities(self) -> None:
        """Link literal titles without interpreting their commas."""
        output = source_description(self.source, Interface("zh-hans"))
        assert output.count('href="https://en.wikipedia.org/wiki/Title') == 1
        assert "Title%2C_with_comma" in output
        assert ">en:Title, with comma</a>" in output
        assert " · " not in output
        assert "调用MediaWiki API" in output
        assert ">获取</a>「<a " in output
        assert "</a>」的修订记录" in output
        assert "Title%2C_with_comma?action=history" in output
        assert "oldid=" not in output
        assert "「<a " in output
        assert "</a>」" in output
        self.source["wiki"] = "zhwiki"
        assert ">Title, with comma</a>" in source_description(
            self.source,
            Interface("zh-hans"),
        )

    def test_text_sources_use_the_quoted_title_as_the_object(self) -> None:
        """Describe source code without a redundant noun."""
        self.source.update(
            dataset="page_text",
            wiki="zhwiki",
            target="Example",
            date="",
            references=[{"kind": "page", "value": "Example"}],
        )
        assert (
            source_description(
                self.source,
                Interface("zh-hans"),
                links=False,
            )
            == "调用MediaWiki API获取「Example」的源代码。"
        )
        assert source_description(
            self.source,
            Interface("zh-hant"),
            links=False,
        ) == ("呼叫MediaWiki API獲取「Example」的原始碼。")
        assert "page source code" in source_description(
            self.source,
            Interface("en"),
            links=False,
        )
        self.source.update(target="", references=[], reference_count=0)
        unscoped = source_description(
            self.source,
            Interface("zh-hans"),
            links=False,
        )
        assert "获取的源代码" not in unscoped
        assert "获取zhwiki上的源代码" in unscoped

    def test_batch_titles_each_keep_their_own_link_and_quotes(self) -> None:
        """Render each batch title with its own link and quotes."""
        titles = [
            "WikiProject:电子游戏/" + suffix
            for suffix in ("Bplus", "BPAN", "A", "AL", "ACC", "PPR")
        ]
        self.source.update(
            dataset="page_text",
            wiki="zhwiki",
            date="",
            target=", ".join(titles),
            references=[{"kind": "page", "value": title} for title in titles],
            reference_count=len(titles),
        )
        output = source_description(self.source, Interface("zh-hans"))
        actual_links = output.count(
            'href="https://zh.wikipedia.org/wiki/WikiProject',
        )
        assert actual_links == len(titles)
        for title in titles:
            assert f">{title}</a>」" in output
        plain = source_description(
            self.source,
            Interface("zh-hans"),
            links=False,
        )
        assert plain == (
            "调用MediaWiki API获取"
            + "、".join("「" + title + "」" for title in titles)
            + "的源代码。"
        )

    def test_revision_text_links_only_the_known_historical_revision(
        self,
    ) -> None:
        """Keep the historical-version label inside its permalink."""
        self.source.update(
            dataset="revision_text",
            wiki="zhwiki",
            date="",
            target="123",
            references=[{"kind": "revision", "value": "123"}],
        )
        output = source_description(self.source, Interface("zh-hans"))
        assert (
            '<a href="https://zh.wikipedia.org/w/index.php?oldid=123">'
            "历史版本 123</a>的正文。" in output
        )
        assert "action=history" not in output
        assert "en:" not in output

    def test_api_verbs_prefill_nonexecuting_sandbox_queries(self) -> None:
        """Use retained public selectors without running a request."""
        for dataset, kind, value, selector in (
            ("page_text", "page", "Title, with comma", "titles"),
            ("page_history", "page_id", "123", "pageids"),
            ("revision_text", "revision", "456", "revids"),
        ):
            with self.subTest(dataset=dataset):
                self.source.update(
                    dataset=dataset,
                    references=[{"kind": kind, "value": value}],
                )
                output = source_description(self.source, Interface("zh-hans"))
                href = re.search(r'<a href="([^"]+)">获取</a>', output)[1]
                parsed = urlsplit(unescape(href))
                assert parsed.netloc == "en.wikipedia.org"
                assert parsed.path == "/wiki/Special:ApiSandbox"
                assert not parsed.query
                parameters = parse_qs(parsed.fragment)
                assert parameters["action"] == ["query"]
                assert parameters[selector] == [value]
                assert parameters["prop"] == ["revisions"]
                assert "api.php" not in href
                assert (
                    not {"execute", "run", "rvstart", "rvend"}
                    & parameters.keys()
                )
                if dataset == "page_history":
                    assert "content" not in parameters["rvprop"][0]
                    assert "rvslots" not in parameters
                else:
                    assert parameters["rvslots"] == ["main"]
                    assert "content" in parameters["rvprop"][0]
                assert ">MediaWiki API</a>" not in output

    def test_pageviews_api_links_the_verb_to_primary_documentation(
        self,
    ) -> None:
        """Keep providers plain without a verified safe request form."""
        self.source.update(
            provider="pageviews",
            dataset="pageviews",
            references=[],
        )
        output = source_description(self.source, Interface("zh-hans"))
        assert 'page-views.html">获取</a>' in output
        assert ">维基媒体浏览量API</a>" not in output

    @staticmethod
    def _execution_labels(
        started: str,
        finished: str | None,
        *,
        outcome: str = "succeeded",
        locale: str = "zh-hans",
    ) -> list[str]:
        """Render actual boundaries independently of cached totals.

        Returns:
            Started and terminal labels for the supplied observation.
        """
        status = status_snapshot()
        run = next(iter(status["executions"].values()))
        run.update(
            started_at=started,
            finished_at=finished,
            duration_seconds=999,
            outcome=outcome,
            stages=[
                {"kind": "started", "time": started, "title": ""},
                {
                    "kind": "finished",
                    "time": finished or started,
                    "title": "",
                },
            ],
        )
        context = history_context(
            Interface(locale),
            status,
            dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
        )
        return [stage["label"] for stage in context["events"][0]["stages"]]

    def test_terminal_stages_describe_actual_elapsed_time(self) -> None:
        """Keep hundredths and omit unneeded zero hour/minute fields."""
        for finished, expected in (
            ("2026-11-03T12:00:02.500Z", "2.50秒"),
            ("2026-11-03T13:02:03.456Z", "1小时2分3.46秒"),
            ("2026-11-03T13:00:00Z", "1小时0.00秒"),
            ("2026-11-03T12:01:00Z", "1分0.00秒"),
            ("2026-11-03T12:00:59.999Z", "1分0.00秒"),
        ):
            with self.subTest(finished=finished):
                labels = self._execution_labels(
                    "2026-11-03T12:00:00Z",
                    finished,
                )
                assert labels == [
                    "开始准备浏览量排行作业。",
                    "作业完成，用时" + expected + "。",
                ]

    def test_failed_and_unknown_terminal_times_keep_truthful_labels(
        self,
    ) -> None:
        """Leave unavailable elapsed time unknown after failure."""
        for locale, expected in (
            ("zh-hans", "作业失败，用时2.50秒。"),
            ("zh-hant", "作業失敗，用時2.50秒。"),
            ("en", "Job failed after 2.50 sec."),
        ):
            labels = self._execution_labels(
                "2026-11-03T12:00:00Z",
                "2026-11-03T12:00:02.500Z",
                outcome="failed",
                locale=locale,
            )
            assert labels[-1] == expected
        assert (
            self._execution_labels(
                "2026-11-03T12:00:00Z",
                None,
                outcome="failed",
            )[-1]
            == "作业失败。"
        )

    def test_page_ids_have_direct_links_and_no_invented_titles(self) -> None:
        """Link numeric page identities to their exact source wiki."""
        self.source.update(
            references=[{"kind": "page_id", "value": "123"}],
            target="123",
        )
        output = source_description(self.source, Interface("en"))
        assert (
            'href="https://en.wikipedia.org/w/index.php?curid=123'
            '&amp;action=history"' in output
        )
        assert ">en:page ID 123</a>" in output

    def test_replica_tables_link_verified_primary_documentation(self) -> None:
        """Describe actual database and table identities."""
        self.source.update(
            provider="replica",
            dataset="database_query",
            wiki="zhwiki",
            references=[
                {"kind": "table", "value": name}
                for name in ("categorylinks", "linktarget", "page")
            ],
            reference_count=3,
        )
        output = source_description(self.source, Interface("zh-hans"))
        for title in ("Categorylinks", "Linktarget", "Page"):
            assert f"/Manual:{title}_table" in output
        assert "Wiki Replicas" in output
        assert "查询zhwiki." in output
        assert 'title="categorylinks">cl</a>|' in output
        assert 'title="linktarget">lt</a>|' in output
        assert " · " not in output
        for locale, prefix in (
            ("zh-hans", "从Wiki Replicas查询zhwiki."),
            ("zh-hant", "從Wiki Replicas查詢zhwiki."),
            ("en", "Querying database zhwiki."),
        ):
            with self.subTest(locale=locale):
                localized = source_description(self.source, Interface(locale))
                assert localized.startswith(prefix)
                assert localized.count("<a ") == self.source["reference_count"]
                assert "Help:Wiki_Replicas" not in localized
        self._check_explicit_query_purposes()

    def test_assessment_tables_use_compact_names_with_full_tooltips(
        self,
    ) -> None:
        """Keep compact database scope and exact table documentation."""
        self.source.update(
            provider="replica",
            dataset="database_query",
            wiki="zhwiki",
            date="",
            references=[
                {"kind": "table", "value": name}
                for name in (
                    "page",
                    "page_assessments",
                    "page_assessments_projects",
                )
            ],
            reference_count=3,
        )
        plain = source_description(
            self.source,
            Interface("zh-hans"),
            links=False,
        )
        assert plain == "从Wiki Replicas查询zhwiki.page|pa|pap。"
        assert "<a " not in plain
        output = source_description(self.source, Interface("zh-hans"))
        assert 'title="page_assessments">pa</a>' in output
        assert 'title="page_assessments_projects">pap</a>' in output
        assert "/page_assessments_table" in output
        assert "/page_assessments_projects_table" in output

    def _check_explicit_query_purposes(self) -> None:
        """Keep query intent separate from reused physical tables."""
        self.source.update(
            provider="replica",
            dataset="database_query",
            wiki="zhwiki",
            date="",
            references=[{"kind": "table", "value": "page"}],
            reference_count=1,
        )
        for purpose, scope, expected in (
            (
                "project_membership",
                {"project": "电子游戏"},
                "专题「电子游戏」的成员页面",
            ),
            (
                "category_members",
                {"categories": ["推薦的新條目"], "page_kind": "talk_pages"},
                "分类「推薦的新條目」中的讨论页",
            ),
            (
                "page_tags",
                {"categories": ["新條目推薦候選"], "page_kind": "talk_pages"},
                "分类「新條目推薦候選」中已选的讨论页",
            ),
            (
                "latest_revisions",
                {"page_kind": "talk_pages", "page_count": 1480},
                "已选1480个讨论页的版本号（page_latest）",
            ),
            (
                "template_aliases",
                {"template": "Template:Article history"},
                "「Template:Article history」的别名",
            ),
        ):
            with self.subTest(purpose=purpose):
                self.source.update(purpose=purpose, scope=scope)
                plain = source_description(
                    self.source,
                    Interface("zh-hans"),
                    links=False,
                )
                assert (
                    plain
                    == "从Wiki Replicas查询" + expected + "，使用zhwiki.page。"
                )
                assert "实时" not in plain
                output = source_description(self.source, Interface("zh-hans"))
                assert "Manual:Page_table" in output
                assert "Help:Wiki_Replicas" not in output
                assert "<a " not in plain
        self.source.update(
            purpose="category_members",
            scope={"categories": ["推薦的新條目"], "page_kind": "talk_pages"},
        )
        output = source_description(self.source, Interface("zh-hans"))
        assert 'href="https://zh.wikipedia.org/wiki/Category%3A' in output
        assert ">推薦的新條目</a>」中的讨论页" in output
        self.source.pop("purpose")
        self.source.pop("scope")
        assert (
            source_description(
                self.source,
                Interface("zh-hans"),
                links=False,
            )
            == "从Wiki Replicas查询zhwiki.page。"
        )

    def test_unfamiliar_tables_keep_their_full_names(self) -> None:
        """Keep unfamiliar table identities and documentation safe."""
        self.source.update(
            provider="replica",
            dataset="database_query",
            wiki="zhwiki",
            references=[{"kind": "table", "value": "new_source_table"}],
        )
        output = source_description(self.source, Interface("en"))
        assert 'title="new_source_table">new_source_table</a>' in output
        assert "/Manual:Database_layout" in output

    def test_legacy_targets_are_linked_only_when_unambiguous(self) -> None:
        """Preserve ambiguous legacy titles without splitting."""
        self.source.pop("references")
        self.source.pop("reference_count")
        output = source_description(self.source, Interface("en"))
        assert 'href="https://en.wikipedia.org/wiki/' not in output
        self.source["target"] = "Single title"
        assert (
            'href="https://en.wikipedia.org/wiki/Single_title?action=history"'
            in (source_description(self.source, Interface("en")))
        )
        self.source.update(
            provider="replica",
            dataset="database_query",
            target="page_assessments_projects, wb_items_per_site, page",
        )
        output = source_description(self.source, Interface("en"))
        assert "/page_assessments_projects_table" in output
        assert "/Wikibase/Schema/wb_items_per_site" in output

    def test_html_is_escaped_and_plain_cards_have_no_markup(self) -> None:
        """Keep hostile text inert in linked and plain views."""
        self.source["references"][0]["value"] = '<img src=x onerror="bad">'
        output = source_description(self.source, Interface("en"))
        assert "<img" not in output
        assert "&lt;img" in output
        plain = source_description(self.source, Interface("en"), links=False)
        assert type(plain) is str
        assert "<a " not in plain
        assert '<img src=x onerror="bad">' in plain
        assert '"en:<img src=x onerror="bad">"' in plain

    def test_saved_membership_links_the_file_without_metadata_prose(
        self,
    ) -> None:
        """Keep acquisition metadata in the linked data view."""
        self.source.update(
            provider="saved",
            dataset="project_membership",
            wiki="zhwiki",
            target="电子游戏/宝可梦工作组",
            metadata={
                "file": (
                    "reports/data/projects/zhwiki/Games/2026/20261102.parquet"
                ),
                "observed_at": "2026-11-03T00:00:04Z",
                "finished_at": "2026-11-03T00:00:19Z",
            },
        )
        output = source_description(
            self.source,
            Interface("en"),
            kind="source_loaded",
        )
        href = re.search(r'<a href="([^"]+)"', output)[1]
        parsed = urlsplit(unescape(href))
        assert parsed.path == "/data/"
        assert parse_qs(parsed.query) == {
            "file": [self.source["metadata"]["file"]],
            "lang": ["en"],
        }
        assert ">20261102.parquet</a>" in output
        assert "UTC" not in output
        assert "acquisition" not in output
        assert "built" not in output
        plain = source_description(
            self.source,
            Interface("en"),
            kind="source_loaded",
            links=False,
        )
        assert "<a " not in plain
        assert plain == (
            'Loaded member pages for "电子游戏/宝可梦工作组" '
            'from "20261102.parquet".'
        )
        self.source["metadata"]["written_at"] = "2026-11-03T00:00:20Z"
        with_written = source_description(
            self.source,
            Interface("en"),
            kind="source_loaded",
        )
        assert with_written == output
        traditional = source_description(self.source, Interface("zh-hant"))
        assert traditional.startswith("從「<a ")
        assert traditional.endswith(
            "</a>」調取專題「电子游戏/宝可梦工作组」的成員頁面。",
        )
        self.source["target"] = "<unsafe>"
        assert "&lt;unsafe&gt;" in source_description(
            self.source,
            Interface("en"),
        )
        self.source["target"] = ""
        assert (
            source_description(
                self.source,
                Interface("en"),
                links=False,
            )
            == 'Loaded project member pages from "20261102.parquet".'
        )
        self.source["metadata"]["file"] = "reports/data/legacy.parquet"
        assert "data-dataset-link" in source_description(
            self.source,
            Interface("zh-hant"),
        )

    def test_legacy_saved_membership_does_not_invent_file_metadata(
        self,
    ) -> None:
        """Explain source purpose when its filename is unknown."""
        self.source.update(
            provider="saved",
            dataset="project_membership",
            wiki="zhwiki",
            target="电子游戏",
        )
        plain = source_description(
            self.source,
            Interface("zh-hans"),
            links=False,
        )
        assert plain == ("从2026年11月2日的专题成员数据调取专题成员页面。")
        assert ".parquet" not in plain
        assert "UTC" not in plain
        self.source["date"] = ""
        plain = source_description(
            self.source,
            Interface("zh-hans"),
            links=False,
        )
        assert "已保存的专题成员数据" in plain
        assert "2026年" not in plain

    @staticmethod
    def test_stage_destinations_are_part_of_the_sentence() -> None:
        """Place linked destinations inside natural sentences."""
        status = status_snapshot()
        run = next(iter(status["executions"].values()))
        run["stages"] = [
            {
                "kind": "database_updated",
                "time": run["started_at"],
                "title": "WikiProject:电子游戏/新条目推荐",
            },
        ]
        context = history_context(
            Interface("zh-hans"),
            status,
            dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
        )
        label = context["events"][0]["stages"][0]["label"]
        assert label.startswith("已生成「<a ")
        assert label.endswith("</a>」的报告数据。")
        assert " · " not in label

    @staticmethod
    def test_all_destination_stages_quote_page_names() -> None:
        """Quote titles in both script variants and English actions."""
        status = status_snapshot()
        run = next(iter(status["executions"].values()))
        run["stages"] = [
            {
                "kind": kind,
                "time": run["started_at"],
                "title": "WikiProject:电子游戏/新条目推荐",
            }
            for kind in (
                "database_updated",
                "checking",
                "saving",
                "saved",
                "no_update",
                "save_failed",
                "unconfirmed",
            )
        ]
        for locale in ("zh-hans", "zh-hant", "en"):
            context = history_context(
                Interface(locale),
                status,
                dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
            )
            opening, closing = ('"', '"') if locale == "en" else ("「", "」")
            for stage in context["events"][0]["stages"]:
                label = unescape(str(stage["label"]))
                assert opening + "<a " in label
                assert "</a>" + closing in label
                assert "《" not in label
                assert "页面「" not in label
                assert "頁面「" not in label
            unchanged = unescape(
                str(context["events"][0]["stages"][4]["label"]),
            )
            expected = {
                "zh-hans": "无需编辑「<a ",
                "zh-hant": "無需編輯「<a ",
                "en": 'No edit needed for "<a ',
            }
            assert unchanged.startswith(expected[locale])
            saved_data = unescape(
                str(context["events"][0]["stages"][0]["label"]),
            )
            saved_page = unescape(
                str(context["events"][0]["stages"][3]["label"]),
            )
            expected_data, expected_page = {
                "zh-hans": ("报告数据", "已向维基保存"),
                "zh-hant": ("報告資料", "已向維基儲存"),
                "en": ("report data", "Saved wiki page"),
            }[locale]
            assert expected_data in saved_data
            assert expected_page in saved_page

    def test_source_links_survive_progress_counters(self) -> None:
        """Keep linked dialog progress safe with counters."""
        status = status_snapshot()
        run = copy.deepcopy(next(iter(status["executions"].values())))
        run.update(
            outcome="running",
            finished_at=None,
            progress={
                "phase": "source_read",
                "current": 1,
                "total": 2,
                "source": self.source,
            },
        )
        status["executions"] = {run["run_id"]: run}
        context = history_context(
            Interface("en"),
            status,
            dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
        )
        progress = context["events"][0]["progress"]
        assert "<a href=" in progress
        assert "&lt;a " not in progress
        assert "1/2" in progress


class TestPageviewsProgressPresentation(TestCase):
    """Describe retained bulk Pageviews milestones."""

    @staticmethod
    def test_deferred_finish_does_not_claim_completion() -> None:
        """Keep deferral explicit in the final history stage."""
        for locale in ("en", "zh-hans", "zh-hant"):
            status = status_snapshot()
            run = next(iter(status["executions"].values()))
            run.update(
                outcome="deferred",
                stages=[
                    {
                        "time": run["finished_at"],
                        "kind": "finished",
                        "title": "",
                    },
                ],
            )
            context = history_context(
                Interface(locale),
                status,
                dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
            )
            label = context["events"][0]["stages"][0]["label"]
            assert {"en": "deferred", "zh-hans": "延后", "zh-hant": "延後"}[
                locale
            ] in label
            assert (
                Interface(locale).text("reports.stage.finished") not in label
            )

    @staticmethod
    def test_pageviews_stages_show_date_and_completed_groups() -> None:
        """Render saved bulk milestones with dates and totals."""
        status = status_snapshot()
        run = next(iter(status["executions"].values()))
        run["stages"] = [
            {
                "time": run["started_at"],
                "kind": "pageviews_download",
                "title": "",
                "progress": {
                    "phase": "pageviews_download",
                    "report_date": "2026-11-02",
                    "current": current,
                    "total": 13062,
                },
            }
            for current in (0, 1000, 13062)
        ]
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            context = history_context(
                interface,
                status,
                dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
            )
            stages = context["events"][0]["stages"]
            for index, (stage, count) in enumerate(
                zip(
                    stages,
                    ("0/13,062", "1,000/13,062", "13,062/13,062"),
                    strict=True,
                ),
            ):
                assert count in stage["label"]
                assert " · " not in stage["label"]
                assert "（" not in stage["label"]
                assert stage["label"].endswith("." if locale == "en" else "。")
                assert (interface.date("2026-11-02") in stage["label"]) == (
                    index == 0
                )
                phase = interface.text("progress.pageviews_download")
                assert phase in stage["label"]
            run["stages"][-1]["progress"]["report_date"] = "2026-11-03"
            updated = history_context(
                interface,
                status,
                dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
            )
            assert (
                interface.date("2026-11-03")
                in updated["events"][0]["stages"][-1]["label"]
            )
            run["stages"][-1]["progress"]["report_date"] = "2026-11-02"


class TestNewPageSourcePresentation(TestCase):
    """Keep source batches concise and retain source identities."""

    def test_new_page_batches_describe_counts_instead_of_title_samples(
        self,
    ) -> None:
        """Summarize candidate counts while keeping the API link."""
        source = {
            "provider": "mediawiki",
            "dataset": "page_text",
            "wiki": "zhwiki",
            "date": "",
            "purpose": "new_page_candidates",
            "scope": {"new_page_count": 400, "redirect_count": 12},
            "target": "Neon, PKS",
            "references": [{"kind": "page", "value": "Neon"}],
            "reference_count": 412,
        }
        expected = {
            "zh-hans": (
                "调用MediaWiki API获取400个新页面"
                "和12个重定向改写页面的源代码。"
            ),
            "zh-hant": (
                "呼叫MediaWiki API獲取400個新頁面"
                "和12個重新導向改寫頁面的原始碼。"
            ),
            "en": (
                "Read source text for 400 new pages and 12 redirect "
                "conversions through the MediaWiki API."
            ),
        }
        for locale, text in expected.items():
            with self.subTest(locale=locale):
                interface = Interface(locale)
                assert (
                    source_description(
                        source,
                        interface,
                        links=False,
                    )
                    == text
                )
                rendered = source_description(source, interface)
                assert "/wiki/Special:ApiSandbox#" in rendered
                assert "412" not in rendered
                assert ">Neon</a>" not in rendered
                loaded = source_description(
                    source,
                    interface,
                    kind="source_loaded",
                    links=False,
                )
                assert "400" in loaded
                assert "12" in loaded
                assert "Neon" not in loaded


class TestGroupedSources(TestCase):
    """Keep grouped display stages separate from saved evidence."""

    @staticmethod
    def test_alias_stages_group_without_changing_saved_evidence() -> None:
        """Group template lookups around actual file operations."""
        status = status_snapshot()
        run = next(iter(status["executions"].values()))
        aliases = [
            {
                "kind": "source_loaded",
                "time": run["started_at"],
                "source": {
                    "provider": "saved",
                    "dataset": "template_aliases",
                    "wiki": "enwiki",
                    "date": "",
                    "target": name,
                    "purpose": "template_aliases",
                    "scope": {"template": name},
                },
            }
            for name in (
                "en:Template:Article history",
                "en:Template:GA",
                "en:Template:GA",
            )
        ]
        file_stage = {
            "kind": "source_written",
            "time": run["started_at"],
            "source": {
                "provider": "saved",
                "dataset": "parquet",
                "wiki": "",
                "date": "",
                "target": "",
                "metadata": {"file": "cache/history.parquet"},
            },
        }
        run["stages"] = [*aliases, file_stage, aliases[1]]
        original = copy.deepcopy(run["stages"])
        context = history_context(
            Interface("zh-hans"),
            status,
            dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
        )
        labels = [
            str(stage["label"]) for stage in context["events"][0]["stages"]
        ]
        assert labels[0] == "解析页面（{{Article history}}、{{GA}}）。"
        assert labels[2] == "解析页面（{{GA}}）。"
        assert "<code>cache/history.parquet</code></a>" in labels[1]
        assert 'data-dataset-link aria-haspopup="dialog"' in labels[1]
        assert "file=cache%2Fhistory.parquet" in labels[1]
        assert run["stages"] == original
