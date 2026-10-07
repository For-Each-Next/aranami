"""Verify DYK parsing, counts, cache reuse, and proposed edits."""

# Keep calls to internal routines available to focused offline tests.
# ruff: file-ignore[private-member-access]
# Verify the exact mathematical monospace version glyphs.
# ruff: file-ignore[ambiguous-unicode-character-string]

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
from aranami.support import dyk_cache, report_membership
from aranami.support.dates import parse_date
from aranami.support.edit_summary import MAX_EDIT_SUMMARY_BYTES, EditSummary
from aranami.support.regions import region_content
from aranami.support.report_membership import MembershipReport


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
                "page_id": [1, 2, 3],
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
        assert "aranami-member" not in completed_text
        assert "aranami-member" not in nomination_text
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
            first = dyks._dated_pages(site, members)
            read.reset_mock()
            read.return_value = []
            second = dyks._dated_pages(site, members)
            assert first.equals(second)
            assert read.call_args.args[1] == []
            latest.return_value = pl.DataFrame(
                {"page_id": [11], "page_latest": [101]},
            )
            read.return_value = [
                TalkPage(11, 102, "{{DYKtalk|date=2025-01-01}}"),
            ]
            changed = dyks._dated_pages(site, members)
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
            report = dyks.prepare_report(
                original_text,
                site,
                date(2024, 5, 1),
                project="Example project",
                report_title="Project:Example DYK report",
                statistics_title="c:Data:Example statistics.tab",
            )
            repeated = dyks.update_text(
                report.text,
                site,
                date(2024, 5, 1),
                project="Example project",
                report_title="Project:Example DYK report",
                statistics_title="c:Data:Example statistics.tab",
            )
        read_pages.assert_not_called()
        query_members.assert_called_with(site, "Example project")
        updated = report.text
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
        assert report.members == {"游戏甲": 1, "游戏乙": 2}
        assert dyks.article_members(updated) == {
            "游戏甲": None,
            "游戏乙": None,
        }
        assert "aranami-member" not in updated

    @staticmethod
    @patch("aranami.jobs._execution.perf_counter", new=lambda: 0.0)
    def test_job_reads_target_and_owns_proposal_metadata() -> None:
        """Pass target text to the service and retain the source."""
        site = Mock()
        context = JobContext(site, date(2024, 5, 1), dry=True)
        original = (
            '<!-- aranami begin="dyk" -->\n'
            '# {{PJ:VG/DYK/item|Old|初级}}<!-- aranami-member id="1" -->\n'
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n<!-- aranami end="dykn" -->'
        )
        updated = (
            original
            .replace("Old", "Renamed")
            .replace("初级", "优良")
            .replace('<!-- aranami-member id="1" -->', "")
        )
        page = Mock(text=original)
        title = "WikiProject:电子游戏/新条目推荐"
        with (
            patch(
                "aranami.support.edit_summary.version",
                return_value="0.3.5",
            ),
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
                "prepare_report",
                return_value=MembershipReport(updated, {"Renamed": 1}),
            ) as prepare_report,
            patch.object(dyk_job, "load_membership", return_value={}),
            patch.object(dyk_job, "save_membership") as save,
        ):
            dyk_job.run(context=context)
        read_pages.assert_called_once_with(site, [title])
        prepare_report.assert_called_once_with(
            original,
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
        assert edit.original_text == original
        assert edit.text == updated
        assert (
            edit.summary == "1 article, 0 nominees. "
            "Executed by 𝙰𝚛𝚊𝚗𝚊𝚖𝚒 𝟶.𝟹.𝟻 in 0.00\u2033."
        )
        assert "aranami-member" not in edit.text
        save.assert_called_once_with(site, title, original, {"Old": 1})
        page.save.assert_not_called()

    @staticmethod
    def test_summary_counts_articles_and_nominees_separately() -> None:
        """Count each managed list, including active repeat nominees."""
        ranges = (
            '<!-- aranami begin="dyk" -->{completed}'
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->{nominated}'
            '<!-- aranami end="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Unmanaged|初级}}"
        )
        cases = (
            ((), (), "0 articles, 0 nominees."),
            ((), ("Candidate",), "0 articles, 1 nominee."),
            (("Completed",), (), "1 article, 0 nominees."),
            (
                ("Repeat", "Other"),
                ("Repeat", "Other"),
                "2 articles, 2 nominees.",
            ),
        )
        for completed, nominated, expected in cases:
            text = ranges.format(
                completed="\n".join(
                    f"# {{{{PJ:VG/DYK/item|{title}|初级"
                    "|date=2023-01-01、2024-05-01}}"
                    for title in completed
                ),
                nominated="\n".join(
                    f"# {{{{PJ:VG/DYK/item|{title}|初级}}}}"
                    for title in nominated
                )
                or "# {{icon|DYKC}}\uff08無\uff09",
            )
            assert dyk_job._content_summary(text, text) == expected

    @staticmethod
    def test_membership_changes_cover_completed_and_nominated_items() -> None:
        """Describe additions and removals while recognizing renames."""
        original = (
            '<!-- update start="dyk" -->\n'
            '# {{PJ:VG/DYK/item|Old|初级}}<!-- aranami-member id="1" -->\n'
            '# {{PJ:VG/DYK/item|Removed|初级}}<!-- aranami-member id="2" -->\n'
            '<!-- update end="dyk" -->\n'
            '<!-- update start="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Candidate|初级}}"
            '<!-- aranami-member id="3" -->\n'
            '<!-- update end="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Unmanaged|初级}}"
        )
        updated = original.replace("Old", "Renamed").replace("初级", "优良")
        updated = updated.replace(
            '# {{PJ:VG/DYK/item|Removed|优良}}<!-- aranami-member id="2" -->',
            '# {{PJ:VG/DYK/item|Added|优良}}<!-- aranami-member id="4" -->',
        )
        assert dyk_job._content_summary(original, updated) == (
            "2 articles, 1 nominee. Passed nominee «[[Added]]»; "
            "removed «[[Removed]]»."
        )
        assert "Unmanaged" not in dyks.article_members(updated)
        assert dyks.article_members(updated, range_name="dykn") == {
            "Candidate": 3,
        }
        assert dyks.article_members(updated, range_name="dyk") == {
            "Renamed": 1,
            "Added": 4,
        }

    @staticmethod
    def test_candidate_promotion_reports_passed_nominee() -> None:
        """Count each passed nominee once."""
        ranges = (
            '<!-- aranami begin="dyk" -->{completed}'
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->{nominated}'
            '<!-- aranami end="dykn" -->'
        )
        item = (
            '\n# {{PJ:VG/DYK/item|Game|初级}}<!-- aranami-member id="1" -->\n'
        )
        original = ranges.format(completed="\n", nominated=item)
        updated = ranges.format(completed=item, nominated="\n")
        assert dyk_job._content_summary(original, updated) == (
            "1 article, 0 nominees. Passed nominee «[[Game]]»."
        )

    @staticmethod
    def test_summary_distinguishes_new_passed_and_failed_nominees() -> None:
        """Label simultaneous new, passed, and failed nominations."""
        ranges = (
            '<!-- aranami begin="dyk" -->{completed}'
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->{nominated}'
            '<!-- aranami end="dykn" -->'
        )
        historical = "\n# {{PJ:VG/DYK/item|Historical|初级|date=2023-01-01}}\n"
        original = ranges.format(
            completed=historical,
            nominated=(
                "\n# {{PJ:VG/DYK/item|Passed|初级}}\n"
                "# {{PJ:VG/DYK/item|花园多惠|初级}}\n"
            ),
        )
        updated = ranges.format(
            completed=(
                historical
                + "# {{PJ:VG/DYK/item|Passed|优良|date=2024-05-01}}\n"
            ),
            nominated="\n# {{PJ:VG/DYK/item|New|初级}}\n",
        )
        assert dyk_job._content_summary(original, updated) == (
            "2 articles, 1 nominee. New nominee «[[New]]»; "
            "passed nominee «[[Passed]]»; failed nominee «[[花园多惠]]»."
        )

    @staticmethod
    def test_candidate_promotion_matches_normalized_title_without_ids() -> (
        None
    ):
        """Recognize promotions without a membership snapshot."""
        original = (
            '<!-- update start="dyk" -->\n<!-- update end="dyk" -->\n'
            '<!-- update start="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Video_Game|初级}}\n"
            '<!-- update end="dykn" -->'
        )
        updated = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Video Game|优良|date=2024-05-01}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            '<!-- aranami end="dykn" -->'
        )
        assert dyk_job._content_summary(original, updated) == (
            "1 article, 0 nominees. Passed nominee «[[Video Game]]»."
        )

    @staticmethod
    def test_cached_identity_recognizes_rename_during_promotion() -> None:
        """Recognize success when the candidate is also renamed."""
        original = (
            '<!-- aranami begin="dyk" -->\n<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Old name|初级}}\n"
            '<!-- aranami end="dykn" -->'
        )
        updated = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|New name|优良|date=2024-05-01}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            '<!-- aranami end="dykn" -->'
        )
        assert (
            dyk_job._content_summary(
                original,
                updated,
                previous_members={"Old name": 1},
                current_members={"New name": 1},
            )
            == "1 article, 0 nominees. Passed nominee «[[New name]]»."
        )

    @staticmethod
    def test_repeat_nomination_passes_when_completed_date_is_added() -> None:
        """Recognize repeat success from its new date."""
        original = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级|date=2023-01-01}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级}}\n"
            '<!-- aranami end="dykn" -->'
        )
        updated = original.replace(
            "date=2023-01-01",
            "date=2023-01-01、2024-05-01",
        ).replace("# {{PJ:VG/DYK/item|Game|初级}}\n", "")
        assert dyk_job._content_summary(original, updated) == (
            "1 article, 0 nominees. Passed nominee «[[Game]]»."
        )

    @staticmethod
    def test_unchanged_historical_completion_leaves_outcome_unknown() -> None:
        """Keep ambiguous historical outcomes unlabeled."""
        original = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级|date=2023-01-01、2023-04-01}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级}}\n"
            '<!-- aranami end="dykn" -->'
        )
        updated = (
            original
            .replace("# {{PJ:VG/DYK/item|Game|初级}}\n", "")
            .replace("2023-01-01、2023-04-01", "2023-04-01、2023-01-01")
            .replace("初级", "优良")
        )
        assert (
            dyk_job._content_summary(original, updated)
            == "1 article, 0 nominees."
        )

    @staticmethod
    def test_delayed_candidate_cleanup_does_not_reverse_a_pass() -> None:
        """Preserve success after delayed candidate cleanup."""
        ranges = (
            '<!-- aranami begin="dyk" -->{completed}'
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->{nominated}'
            '<!-- aranami end="dykn" -->'
        )
        candidate = "\n# {{PJ:VG/DYK/item|Game|初级}}\n"
        completed = "\n# {{PJ:VG/DYK/item|Game|初级|date=2024-05-01}}\n"
        original = ranges.format(completed="\n", nominated=candidate)
        passed = ranges.format(completed=completed, nominated=candidate)
        cleaned = ranges.format(completed=completed, nominated="\n")
        assert dyk_job._content_summary(original, passed) == (
            "1 article, 1 nominee. Passed nominee «[[Game]]»."
        )
        assert (
            dyk_job._content_summary(passed, cleaned)
            == "1 article, 0 nominees."
        )

    @staticmethod
    def test_repeat_pass_survives_delayed_candidate_cleanup() -> None:
        """Report repeat success before candidate cleanup."""
        original = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级|date=2023-01-01}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级}}\n"
            '<!-- aranami end="dykn" -->'
        )
        passed = original.replace(
            "date=2023-01-01",
            "date=2023-01-01、2024-05-01",
        )
        cleaned = passed.replace("# {{PJ:VG/DYK/item|Game|初级}}\n", "")
        assert dyk_job._content_summary(original, passed) == (
            "1 article, 1 nominee. Passed nominee «[[Game]]»."
        )
        assert (
            dyk_job._content_summary(passed, cleaned)
            == "1 article, 0 nominees."
        )

    @staticmethod
    def test_unknown_completion_date_does_not_imply_repeat_success() -> None:
        """Require a valid new date before reporting repeat success."""
        original = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级|date=2023-01-01}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级}}\n"
            '<!-- aranami end="dykn" -->'
        )
        updated = original.replace(
            "date=2023-01-01",
            "date=2023-01-01、日期不詳",
        )
        assert (
            dyk_job._content_summary(original, updated)
            == "1 article, 1 nominee."
        )

    @staticmethod
    def test_same_title_with_another_id_does_not_pass_previous_candidate() -> (
        None
    ):
        """Distinguish a replacement article from the prior nominee."""
        original = (
            '<!-- aranami begin="dyk" -->\n<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级}}\n"
            '<!-- aranami end="dykn" -->'
        )
        updated = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Game|优良|date=2024-05-01}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            '<!-- aranami end="dykn" -->'
        )
        assert dyk_job._content_summary(
            original,
            updated,
            previous_members={"Game": 1},
            current_members={"Game": 2},
        ) == (
            "1 article, 0 nominees. Passed nominee «[[Game]]»; "
            "failed nominee «[[Game]]»."
        )

    @staticmethod
    def test_removed_completed_article_is_not_a_failed_nominee() -> None:
        """Keep historical removals separate from failed nominations."""
        original = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Historical|初级|date=2023-01-01}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            '<!-- aranami end="dykn" -->'
        )
        updated = original.replace(
            "# {{PJ:VG/DYK/item|Historical|初级|date=2023-01-01}}\n",
            "",
        )
        assert dyk_job._content_summary(original, updated) == (
            "0 articles, 0 nominees. Removed «[[Historical]]»."
        )

    @staticmethod
    def test_long_nominee_summary_keeps_complete_links_with_timing() -> None:
        """Fit multibyte outcomes and timing within the byte limit."""
        ranges = (
            '<!-- aranami begin="dyk" -->{completed}'
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->{nominated}'
            '<!-- aranami end="dykn" -->'
        )
        short_title = "新遊戲" * 6
        new_title = "花园多惠" * 30
        passed_title = "成功" * 60
        failed_title = "失敗" * 60
        original = ranges.format(
            completed="\n",
            nominated=f"\n# {{{{PJ:VG/DYK/item|{failed_title}|初级}}}}\n",
        )
        updated = ranges.format(
            completed=(
                f"\n# {{{{PJ:VG/DYK/item|{passed_title}|初级"
                "|date=2024-05-01}}\n"
            ),
            nominated=(
                f"\n# {{{{PJ:VG/DYK/item|{short_title}|初级}}}}\n"
                f"# {{{{PJ:VG/DYK/item|{new_title}|初级}}}}\n"
            ),
        )
        with patch(
            "aranami.support.edit_summary.version",
            return_value="0.3.5",
        ):
            summary = EditSummary.with_execution_time(
                dyk_job._content_summary(original, updated),
                7.12,
            )
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert summary.startswith("1 article, 2 nominees.")
        assert summary.endswith("Executed by 𝙰𝚛𝚊𝚗𝚊𝚖𝚒 𝟶.𝟹.𝟻 in 7.12\u2033.")
        assert f"«[[{short_title}]]»" in summary
        assert summary.count("«") == summary.count("»")
        assert summary.count("[[") == summary.count("]]")
        assert {
            str(link.title)
            for link in mwparserfromhell.parse(summary).filter_wikilinks()
        } <= {short_title, new_title, passed_title, failed_title}

    @staticmethod
    def test_legacy_title_membership_and_grade_only_changes() -> None:
        """Ignore class changes in unmarked template items."""
        original = (
            '<!-- update start="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Video_Game|初级}}\n"
            '<!-- update end="dyk" -->'
        )
        updated = original.replace("Video_Game", "Video Game").replace(
            "初级",
            "优良",
        )
        assert (
            dyk_job._content_summary(original, updated)
            == "1 article, 0 nominees."
        )

    @staticmethod
    def test_clean_candidate_promotion_and_rename_use_cached_ids() -> None:
        """Recognize promotions and renames from cached identities."""
        original = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Removed|初级}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            "# {{PJ:VG/DYK/item|Candidate|初级}}\n"
            '<!-- aranami end="dykn" -->'
        )
        updated = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Renamed|优良|date=2024-01-01}}\n"
            "# {{PJ:VG/DYK/item|Added|初级|date=2024-01-02}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            '<!-- aranami end="dykn" -->'
        )
        site = _OfflineSite("zh", "wikipedia")
        context = Mock(site=site, today=date(2024, 5, 1), dry=False)
        with (
            patch.object(
                dyk_job,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                dyk_job,
                "read_pages",
                return_value=[Mock(text=original)],
            ),
            patch.object(
                dyk_job,
                "prepare_report",
                return_value=MembershipReport(
                    updated,
                    {"Renamed": 1, "Added": 3},
                ),
            ) as prepare,
            patch.object(
                dyk_job,
                "load_membership",
                return_value={"Candidate": 1, "Removed": 2},
            ),
            patch.object(dyk_job, "save_membership") as save,
        ):
            dyk_job.run(title="Report", context=context)
        prepare.assert_called_once()
        [call] = context.publish.call_args_list
        edit = call.args[0]
        assert edit.summary == (
            "2 articles, 0 nominees. Passed nominee «[[Added]]», "
            "«[[Renamed]]»; "
            "removed «[[Removed]]»."
        )
        assert "aranami-member" not in edit.text
        save.assert_called_once_with(
            site,
            "Report",
            updated,
            {"Renamed": 1, "Added": 3},
        )

    @staticmethod
    def test_repeated_dry_runs_save_only_original_membership() -> None:
        """Keep original identities through repeated previews."""
        site = _OfflineSite("zh", "wikipedia")
        context = Mock(site=site, today=date(2024, 5, 1), dry=True)
        original = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            '<!-- aranami end="dykn" -->'
        )
        updated = original.replace(
            '<!-- aranami end="dyk" -->',
            '# {{PJ:VG/DYK/item|Added|初级}}\n<!-- aranami end="dyk" -->',
        )
        with (
            TemporaryDirectory() as directory,
            patch.object(
                report_membership.Path,
                "cwd",
                return_value=Path(directory),
            ),
            patch.object(
                dyk_job,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                dyk_job,
                "read_pages",
                return_value=[Mock(text=original)],
            ),
            patch.object(
                dyk_job,
                "prepare_report",
                return_value=MembershipReport(
                    updated,
                    {"Game": 1, "Added": 2},
                ),
            ) as prepare,
        ):
            dyk_job.run(title="Report", context=context)
            dyk_job.run(title="Report", context=context)
            assert dyk_job.load_membership(site, "Report", original) == {
                "Game": 1,
            }
            assert dyk_job.load_membership(site, "Report", updated) == {}
        assert prepare.call_count == 2  # ruff: ignore[magic-value-comparison]
        summaries = [
            call.args[0].summary for call in context.publish.call_args_list
        ]
        assert (
            summaries
            == [
                "2 articles, 0 nominees. Passed nominee «[[Added]]».",
            ]
            * 2
        )

    def test_failed_publication_does_not_save_membership(self) -> None:
        """Preserve cached identities when publication fails."""
        site = _OfflineSite("zh", "wikipedia")
        context = Mock(site=site, today=date(2024, 5, 1), dry=False)
        context.publish.side_effect = RuntimeError("publication failed")
        original = (
            '<!-- aranami begin="dyk" -->\n'
            "# {{PJ:VG/DYK/item|Game|初级}}\n"
            '<!-- aranami end="dyk" -->\n'
            '<!-- aranami begin="dykn" -->\n'
            '<!-- aranami end="dykn" -->'
        )
        with (
            patch.object(
                dyk_job,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                dyk_job,
                "read_pages",
                return_value=[Mock(text=original)],
            ),
            patch.object(
                dyk_job,
                "prepare_report",
                return_value=MembershipReport(original, {"Game": 1}),
            ),
            patch.object(dyk_job, "load_membership", return_value={"Game": 1}),
            patch.object(dyk_job, "save_membership") as save,
            self.assertRaises(RuntimeError),  # ruff: ignore[pytest-unittest-raises-assertion]
        ):
            dyk_job.run(title="Report", context=context)
        save.assert_not_called()
