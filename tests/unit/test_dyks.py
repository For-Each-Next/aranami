"""Verify DYK parsing, counts, cache reuse, and proposed edits."""

from contextlib import nullcontext
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import mwparserfromhell
import polars as pl
from pywikibot.site import BaseSite, Namespace

from aranami.jobs import JobContext, dyks as dyk_job
from aranami.services.zhwiki import dyks
from aranami.services.zhwiki.dyk_dates import extract_dates
from aranami.sources.dyk import TalkPage
from aranami.support import dyk_cache
from aranami.support.dates import parse_date
from aranami.support.regions import region_content


class _OfflineSite(BaseSite):
    """Provide Chinese namespace identities without network requests."""

    @staticmethod
    def _build_namespaces() -> dict[int, Namespace]:
        """Build the localized template namespace for title parsing.

        Returns:
            Built-in namespace metadata with Chinese template aliases.
        """
        namespaces = Namespace.builtin_namespaces()
        namespaces[Namespace.TEMPLATE] = Namespace(
            Namespace.TEMPLATE,
            canonical_name="Template",
            custom_name="模板",
            aliases=["模版"],
            case="first-letter",
        )
        return namespaces

    @staticmethod
    def encodings() -> tuple[str, ...]:
        """Return the supported encoding for local title parsing."""
        return ("utf-8",)

    def dbName(self) -> str:  # ruff: ignore[invalid-function-name]
        """Return the expected wiki identity without siteinfo access."""
        return f"{self.code}wiki"

    def namespace(
        self,
        number: int,
        *,
        all_ns: bool = False,
    ) -> str | Namespace:
        """Return localized namespace metadata or its canonical title.

        Args:
            number: Numeric namespace identifier.
            all_ns: Whether to return complete metadata.

        Returns:
            Namespace metadata or its localized name.
        """
        namespace = self.namespaces[number]
        return namespace if all_ns else namespace[0]


class TestDykDates(TestCase):
    """Verify multilingual dates and template extraction."""

    @staticmethod
    def test_parses_historical_date_formats_and_rejects_missing_year() -> None:
        """Parse complete and partial dates deterministically in UTC."""
        cases = {
            "20240115123456": date(2024, 1, 15),
            "202402": date(2024, 2, 1),
            "2024": date(2024, 1, 1),
            "2024年1月1日 (一) 12:34:56": date(2024, 1, 1),
            "January 15, 2024": date(2024, 1, 15),
            "2024-01": date(2024, 1, 1),
            "@0": date(1970, 1, 1),
            "": None,
            "February 30, 2024": None,
            "January 1": None,
            "20240230": None,
        }
        assert {text: parse_date(text) for text in cases} == cases

    @staticmethod
    def test_extracts_aliases_all_history_fields_and_unknown_dates() -> None:
        """Keep repeated DYK events and dates beyond the old limit."""
        text = (
            "{{条目历史|dykdate=2020-01-01|dyk30date=2021-03-02}}"
            "{{Template:DYKtalk|date=2022年2月3日}}"
            "{{DYK_talk|2023年|4月5日}}{{DYKtalk|date=unknown}}"
            "{{Article history|dykdate=1999-01-01}}"
        )
        actual = extract_dates(
            text,
            _OfflineSite("zh", "wikipedia"),
            ("Template:Article history", "模板:条目历史"),
            ("Template:DYKtalk", "Template:DYK talk"),
        )
        assert actual == [
            date(2020, 1, 1),
            date(2021, 3, 2),
            date(2022, 2, 3),
            date(2023, 4, 5),
            None,
        ]

    @staticmethod
    def test_invalid_named_date_falls_back_to_positional_components() -> None:
        """Match the verified DYKtalk named-date error fallback."""
        assert extract_dates(
            "{{DYKtalk|date=invalid|2024年|5月6日}}",
            _OfflineSite("zh", "wikipedia"),
            ("Article history",),
            ("DYKtalk",),
        ) == [date(2024, 5, 6)]

    def test_chinese_extractor_rejects_the_english_wiki(self) -> None:
        """Keep English and Chinese template semantics separate."""
        with self.assertRaises(ValueError):  # ruff: ignore[pytest-unittest-raises-assertion]
            extract_dates(
                "{{Article history|dykdate=2024-01-01}}",
                _OfflineSite("en", "wikipedia"),
                ("Article history",),
                ("DYKtalk",),
            )


class TestDykReports(TestCase):
    """Verify report grouping and first-appearance counts."""

    @staticmethod
    def test_statistics_count_first_valid_event_once_per_article() -> None:
        """Ignore unknown dates and count articles at their debut."""
        frame = pl.DataFrame(
            {
                "dyk_dates": [
                    [date(2024, 4, 2), date(2024, 1, 3)],
                    [date(2024, 3, 31), None],
                    [None],
                ],
            },
        )
        assert dyks.build_statistics(frame, date(2024, 5, 20)) == [
            ["2023-12-31", 0],
            ["2024-03-31", 2],
            ["2024-05-20", 2],
        ]
        assert dyks.build_statistics(frame, date(2024, 3, 31)) == [
            ["2023-12-31", 0],
            ["2024-03-31", 2],
        ]

    @staticmethod
    def test_visible_report_uses_latest_date_and_includes_comment() -> None:
        """List repeat DYKs by latest year and count their debut."""
        completed = pl.DataFrame(
            {
                "title": ["游戏甲", "游戏乙", "游戏丙"],
                "class": ["优良", "初级", "初级"],
                "importance": ["高", "低", "低"],
                "dyk_dates": [
                    [date(2022, 1, 2), date(2024, 4, 3)],
                    [None, date(2023, 1, 1)],
                    [None],
                ],
            },
        )
        completed_text, nomination_text = dyks.render_reports(
            completed,
            completed.select("title", "class", "importance").head(0),
            date(2024, 5, 1),
            report_title="Project:Example DYK report",
            statistics_title="c:Data:Example statistics.tab",
        )
        parsed = mwparserfromhell.parse(completed_text)
        assert "共計3篇條目" in completed_text
        assert completed_text.index("=== 2024年 ===") < completed_text.index(
            "=== 2023年 ===",
        )
        assert "date=2022-01-02、2024-04-03" in completed_text
        assert "date=2023-01-01、日期不詳" in completed_text
        assert completed_text.index("=== 2023年 ===") < completed_text.index(
            "=== 日期不詳 ===",
        )
        assert "8888" not in completed_text
        assert len(parsed.filter_comments()) == 1
        assert "for [[:c:Data:Example statistics.tab]]" in completed_text
        assert (
            '"sources": "See [[:w:zh:Project:Example DYK report]]"'
            in completed_text
        )
        assert '"data": [["2021-12-31", 0]' in completed_text
        assert nomination_text == "# {{icon|DYKC}} \uff08無\uff09"


class TestDykCache(TestCase):
    """Verify disposable caches keyed by the actual page revision."""

    @staticmethod
    def test_unchanged_revisions_skip_download_and_changes_refresh() -> None:
        """Reuse cached text and preserve API revision identity."""
        site = _OfflineSite("zh", "wikipedia")
        members = pl.DataFrame({"talk_page_id": [11]})
        revisions = pl.DataFrame({"page_id": [11], "page_latest": [100]})
        with (
            TemporaryDirectory() as directory,
            patch(
                "aranami.support.dyk_cache.Path.cwd",
                return_value=Path(directory),
            ),
            patch(
                "aranami.services.zhwiki.dyks.template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch(
                "aranami.services.zhwiki.dyks.latest_revisions",
                return_value=revisions,
            ) as latest,
            patch(
                "aranami.services.zhwiki.dyks.read_talk_pages",
                return_value=[
                    TalkPage(11, 100, "{{DYKtalk|date=2024-01-01}}"),
                ],
            ) as read,
        ):
            first = dyks._dated_pages(site, members)  # ruff: ignore[private-member-access]
            read.reset_mock()
            read.return_value = []
            second = dyks._dated_pages(site, members)  # ruff: ignore[private-member-access]
            assert first.equals(second)
            assert read.call_args.args[1] == []
            latest.return_value = pl.DataFrame(
                {"page_id": [11], "page_latest": [101]},
            )
            read.return_value = [
                TalkPage(11, 102, "{{DYKtalk|date=2025-01-01}}"),
            ]
            changed = dyks._dated_pages(site, members)  # ruff: ignore[private-member-access]
            assert read.call_args.args[1] == [11]
            assert changed["dyk_dates"].to_list() == [[date(2025, 1, 1)]]
            assert dyk_cache.load()["oldid"].to_list() == [102]

    @staticmethod
    def test_corrupt_cache_is_ignored() -> None:
        """Treat corrupt local bytes as a normal cache miss."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "cache" / "vg_dyks_talk_pages-v2.parquet"
            path.parent.mkdir()
            path.write_bytes(b"not parquet")
            with patch(
                "aranami.support.dyk_cache.Path.cwd",
                return_value=root,
            ):
                assert dyk_cache.load().is_empty()


class TestDykEdit(TestCase):
    """Verify content processing and job-owned publication metadata."""

    @staticmethod
    def test_update_text_preserves_unmanaged_text_and_repeats() -> None:
        """Replace both lists using caller-supplied text and project."""
        site = Mock()
        original_text = (
            'Introduction\n<!-- update start="dyk" -->old'
            '<!-- update end="dyk" -->\nMiddle\n'
            '<!-- update start="dykn" -->old candidates'
            '<!-- update end="dykn" -->\nFooter'
        )
        members = pl.DataFrame(
            {
                "full_title": ["游戏甲", "游戏乙"],
                "pa_class": ["优良", "初级"],
                "pa_importance": ["高", "低"],
                "page_id": [1, 2],
                "talk_page_id": [11, 12],
            },
        )
        dated = pl.DataFrame(
            {"talk_page_id": [11], "dyk_dates": [[date(2024, 1, 1)]]},
        )
        with (
            patch(
                "aranami.services.zhwiki.dyks.query_pages_by_wikiproject",
                return_value=members,
            ) as query_members,
            patch(
                "aranami.services.zhwiki.dyks.query_tags",
                side_effect=[
                    pl.DataFrame(
                        {"talk_page_id": [11, 12], "tags": [["DYK"], []]},
                    ),
                    pl.DataFrame(
                        {"talk_page_id": [11, 12], "tags": [[], ["DYKN"]]},
                    ),
                ]
                * 2,
            ),
            patch(
                "aranami.services.zhwiki.dyks._dated_pages",
                return_value=dated,
            ),
            patch("aranami.sources.wiki.read_pages") as read_pages,
        ):
            updated = dyks.update_text(
                original_text,
                site,
                date(2024, 5, 1),
                project="Example project",
                report_title="Project:Example DYK report",
                statistics_title="c:Data:Example statistics.tab",
            )
            repeated = dyks.update_text(
                updated,
                site,
                date(2024, 5, 1),
                project="Example project",
                report_title="Project:Example DYK report",
                statistics_title="c:Data:Example statistics.tab",
            )
        read_pages.assert_not_called()
        query_members.assert_called_with(site, "Example project")
        assert isinstance(updated, str)
        assert repeated == updated
        assert "游戏甲" in updated
        assert "游戏乙" in updated
        nomination = region_content(updated, "dykn")
        assert nomination is not None
        assert "游戏甲" not in nomination
        assert updated.startswith("Introduction\n")
        assert "\nMiddle\n" in updated
        assert updated.endswith("\nFooter")

    @staticmethod
    def test_job_reads_target_and_owns_proposal_metadata() -> None:
        """Pass target text to the service and retain the source."""
        site = Mock()
        context = JobContext(site, date(2024, 5, 1), dry_run=True)
        page = Mock(text="original page text")
        title = "WikiProject:电子游戏/新条目推荐"
        with (
            patch.object(
                dyk_job,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                dyk_job,
                "read_pages",
                return_value=[page],
            ) as read_pages,
            patch.object(
                dyk_job,
                "update_text",
                return_value="updated page text",
            ) as update_text,
        ):
            dyk_job.run(context=context)
        read_pages.assert_called_once_with(site, [title])
        update_text.assert_called_once_with(
            "original page text",
            site,
            context.today,
            project="电子游戏",
            report_title=title,
            statistics_title=(
                "c:Data:Zhwiki_WikiProject_Video_Games_DYK_Annual_Statistics.tab"
            ),
        )
        [edit] = context.edits
        assert edit.site is site
        assert edit.title == title
        assert edit.original_text == "original page text"
        assert edit.text == "updated page text"
        assert edit.summary == "更新电子游戏专题新条目推荐及候选列表"
        page.save.assert_not_called()
