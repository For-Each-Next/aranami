"""Verify date retention, gap recovery, and repeatable report output."""

import datetime as dt
from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import Mock, patch

import mwparserfromhell
import polars as pl
from pywikibot.site import BaseSite, Namespace

from aranami.jobs import JobContext, new_pages as new_pages_job
from aranami.services.zhwiki import new_pages
from aranami.support.edit_summary import MAX_EDIT_SUMMARY_BYTES
from aranami.support.wikitext import managed_region

_DAY_HEADING_LEVEL = 3


class _TitleSite(BaseSite):
    """Provide Chinese title metadata without network access."""

    @staticmethod
    def _build_namespaces() -> dict[int, Namespace]:
        """Build localized namespace metadata for title comparisons.

        Returns:
            Built-in metadata with Wikipedia and Chinese template names.
        """
        namespaces = Namespace.builtin_namespaces()
        namespaces[Namespace.PROJECT] = Namespace(
            Namespace.PROJECT,
            canonical_name="Project",
            custom_name="Wikipedia",
            aliases=["WP", "维基百科", "維基百科"],
            case="first-letter",
        )
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
        """Return the encoding used for local title parsing.

        Returns:
            UTF-8 as the only title encoding.
        """
        return ("utf-8",)

    def namespace(
        self,
        number: int,
        *,
        all_ns: bool = False,
    ) -> str | Namespace:
        """Return a namespace name or its complete metadata.

        Args:
            number: Namespace identifier to resolve.
            all_ns: Whether to return metadata instead of its name.

        Returns:
            Localized namespace name or metadata object.
        """
        namespace = self.namespaces[number]
        return namespace if all_ns else namespace[0]


def _section(day: dt.date) -> str:
    """Render a test record with the actual daily-heading format.

    Args:
        day: Date of the generated report heading.

    Returns:
        An empty report section for the supplied day.
    """
    return f"=== {day.year}年{day.month}月{day.day}日 ===\nNo matches.\n\n"


class TestNewPageRecords(TestCase):
    """Test persistent on-wiki records without network access."""

    @staticmethod
    def test_redirect_conversions_join_new_pages_once_and_keep_notes() -> None:
        """Keep redirect-conversion notes through repeated runs."""
        day = dt.date(2026, 10, 2)
        today = day + dt.timedelta(days=1)
        site = _TitleSite("zh", "wikipedia")
        original = managed_region("new-pages", "", {"days": "1"})
        titles = (
            "New game",
            "Created and converted game",
            "Converted game",
            "Wikipedia:优良条目/超执刀2",
            "Deleted game",
            "Redirect game",
            "Unrelated biography",
        )
        pages = [
            Mock(
                pageid=identifier,
                title=Mock(return_value=title),
                namespace=Mock(return_value=4 if identifier == 14 else 0),  # ruff: ignore[magic-value-comparison]
                exists=Mock(return_value=identifier != 15),  # ruff: ignore[magic-value-comparison]
                isRedirectPage=Mock(return_value=identifier == 16),  # ruff: ignore[magic-value-comparison]
                text="任天堂" if identifier != 17 else "Biography",  # ruff: ignore[magic-value-comparison]
            )
            for identifier, title in enumerate(titles, start=11)
        ]
        metadata = pl.DataFrame({
            "full_title": [titles[2], titles[3]],
            "pa_class": ["优良", None],
        })
        creation_metadata = pl.DataFrame(
            {
                "full_title": list(titles[:4]),
                "page_id": [11, 12, 13, 14],
                "created_at": [
                    dt.datetime(2026, 10, 2, tzinfo=dt.UTC),
                    dt.datetime(2026, 10, 2, 1, 2, 3, tzinfo=dt.UTC),
                    dt.datetime(2026, 8, 5, 11, 22, 33, tzinfo=dt.UTC),
                    dt.datetime(2026, 8, 6, 11, 22, 33, tzinfo=dt.UTC),
                ],
            },
            schema={
                "full_title": pl.String,
                "page_id": pl.Int64,
                "created_at": pl.Datetime("us", "UTC"),
            },
        )
        with (
            patch.object(
                new_pages,
                "new_page_ids",
                return_value=[11, 12],
            ) as creations,
            patch.object(
                new_pages,
                "redirect_converted_page_ids",
                return_value=[12, 13, 14, 15, 16, 17, 13, 18],
            ) as conversions,
            patch.object(
                new_pages,
                "read_page_ids",
                return_value=pages,
            ) as preload,
            patch.object(
                new_pages,
                "query_pages_by_wikiproject",
                return_value=metadata,
            ),
            patch.object(
                new_pages,
                "page_creation_metadata",
                return_value=creation_metadata,
            ) as creation_info,
        ):
            updated = new_pages.update_text(
                original,
                site,
                today,
                project="电子游戏",
            )
            repeated = new_pages.update_text(
                updated,
                site,
                today,
                project="电子游戏",
            )
        creations.assert_called_once_with(site, day, today)
        conversions.assert_called_once_with(site, day, today)
        preload.assert_called_once_with(site, list(range(11, 19)))
        creation_info.assert_called_once_with(site, list(titles[:4]))
        rows = [line for line in updated.splitlines() if line.startswith("*")]
        assert len(rows) == 4  # ruff: ignore[magic-value-comparison]
        assert rows[0].startswith("* [[:New game]]")
        assert "自重定向页改写" not in rows[0]
        assert rows[0].endswith(
            "<!-- 页面ID 11 · 创建时间 2026-10-02 00:00:00 -->",
        )
        assert rows[1].startswith("* [[:Created and converted game]]")
        assert rows[2].startswith("* {{class/icon|优良}} [[:Converted game]]")
        assert all(" — 自重定向页改写 <!-- 页面ID " in row for row in rows[1:])
        assert rows[2].endswith(
            "<!-- 页面ID 13 · 创建时间 2026-08-05 11:22:33 -->",
        )
        assert rows[3] == (
            "* [[:Wikipedia:优良条目/超执刀2]]"
            '<small style="margin-left: 0.33em;">'
            "〔{{Talk|1=Wikipedia:优良条目/超执刀2|2=討論}}〕</small>"  # ruff: ignore[ambiguous-unicode-character-string]
            " — 自重定向页改写"
            " <!-- 页面ID 14 · 创建时间 2026-08-06 11:22:33 -->"
        )
        assert "搜尋8個頁面，匹配到4個頁面" in updated  # ruff: ignore[ambiguous-unicode-character-string]
        assert new_pages.record_counts(updated, site, {day}) == (3, 1)
        assert new_pages_job._edit_summary(  # ruff: ignore[private-member-access]
            updated,
            site,
            day,
            {day},
        ) == (
            "Updated records for 2 October 2026. "
            "Found 3 article pages and 1 non-article page."
        )
        assert repeated == updated

    @staticmethod
    def test_retained_rows_receive_comments_without_rebuilding_dates() -> None:
        """Annotate retained records and preserve saved identities."""
        today = dt.date(2026, 10, 3)
        yesterday = today - dt.timedelta(days=1)
        previous = today - dt.timedelta(days=2)
        site = _TitleSite("zh", "wikipedia")
        saved_comment = "<!-- 页面ID 99 · 创建时间 2026-07-01 01:02:03 -->"
        outside = "* [[:Outside game]]\n"
        original = outside + managed_region(
            "new-pages",
            _section(yesterday)
            + f"* [[:Moved game]] {saved_comment}\n"
            + "* [[:Template:Game#Details|Display]] <!-- keep this -->\n"
            + _section(previous)
            + "* [[:模板:Game]] — 自重定向页改写\n",
            {"days": "2"},
        )
        creation_metadata = pl.DataFrame(
            {
                "full_title": ["模板:Game"],
                "page_id": [42],
                "created_at": [
                    dt.datetime(2026, 8, 5, 11, 22, 33, tzinfo=dt.UTC),
                ],
            },
            schema={
                "full_title": pl.String,
                "page_id": pl.Int64,
                "created_at": pl.Datetime("us", "UTC"),
            },
        )
        grades = pl.DataFrame({
            "full_title": ["Moved game", "模板:Game"],
            "pa_class": ["优良", "初"],
        })
        with (
            patch.object(new_pages, "_build_section") as build,
            patch.object(
                new_pages,
                "page_creation_metadata",
                return_value=creation_metadata,
            ) as fetch,
            patch.object(
                new_pages,
                "query_pages_by_wikiproject",
                return_value=grades,
            ),
        ):
            updated = new_pages.update_text(
                original,
                site,
                today,
                project="电子游戏",
            )
            repeated = new_pages.update_text(
                updated,
                site,
                today,
                project="电子游戏",
            )
        build.assert_not_called()
        fetch.assert_called_once_with(site, ["模板:Game"])
        assert updated.startswith(outside)
        assert (
            f"* {{{{class/icon|优良}}}} [[:Moved game]] {saved_comment}"
            in updated
        )
        annotation = "<!-- 页面ID 42 · 创建时间 2026-08-05 11:22:33 -->"
        assert updated.count(annotation) == 2  # ruff: ignore[magic-value-comparison]
        assert f"<!-- keep this --> {annotation}" in updated
        assert f"— 自重定向页改写 {annotation}" in updated
        days = {yesterday, previous}
        assert new_pages.record_counts(updated, site, days) == (1, 2)
        assert updated == repeated

    def test_unavailable_creation_metadata_never_invents_a_date(self) -> None:
        """Keep missing identities and unknown creation dates."""
        source = "* [[:Game]]\r\n* [[:Missing game]]\nUnrelated text.\n"
        metadata = pl.DataFrame(
            {"full_title": ["Game"], "page_id": [7], "created_at": [None]},
            schema={
                "full_title": pl.String,
                "page_id": pl.Int64,
                "created_at": pl.Datetime("us", "UTC"),
            },
        )
        with (
            patch.object(
                new_pages,
                "page_creation_metadata",
                return_value=metadata,
            ),
            self.assertLogs(new_pages.__name__, level="WARNING") as logs,
        ):
            updated = new_pages._annotate_items(  # ruff: ignore[private-member-access]
                source,
                _TitleSite("zh", "wikipedia"),
            )
        assert updated == (
            "* [[:Game]] <!-- 页面ID 7 · 创建时间 未知 -->\r\n"
            "* [[:Missing game]]\n"
            "Unrelated text.\n"
        )
        assert (
            "No creation metadata for listed page Missing game"
            in logs.output[0]
        )

    @staticmethod
    def test_empty_creation_and_conversion_day_renders_empty_record() -> None:
        """Keep empty dates reusable when neither query finds pages."""
        day = dt.date(2026, 10, 2)
        with (
            patch.object(new_pages, "new_page_ids", return_value=[]),
            patch.object(
                new_pages,
                "redirect_converted_page_ids",
                return_value=[],
            ),
            patch.object(
                new_pages,
                "read_page_ids",
                return_value=[],
            ) as preload,
        ):
            result = new_pages._build_section(Mock(), day)  # ruff: ignore[private-member-access]
        assert result.startswith("=== 2026年10月2日 ===\n")
        assert "搜尋0個頁面，匹配到0個頁面" in result  # ruff: ignore[ambiguous-unicode-character-string]
        assert "自重定向页改写" not in result
        assert new_pages.record_dates(result) == {day}
        assert preload.call_args.args[1] == []

    @staticmethod
    def test_gaps_old_dates_duplicates_and_repeated_hourly_run() -> None:
        """Fill gaps, prune old dates, and skip repeated requests."""
        today = dt.date(2026, 10, 3)
        days = [today - dt.timedelta(days=value) for value in range(1, 101)]
        missing = {days[2], days[76]}
        original = (
            "Manual introduction.\n"
            + "".join(_section(day) for day in days if day not in missing)
            + _section(days[0])
            + _section(today - dt.timedelta(days=150))
        )
        original += "== Manual notes ==\nKeep this note.\n"
        site = Mock()
        metadata = pl.DataFrame(
            schema={"full_title": pl.String, "pa_class": pl.String},
        )
        with (
            patch("aranami.sources.wiki.read_pages") as read_pages,
            patch.object(
                new_pages,
                "query_pages_by_wikiproject",
                return_value=metadata,
            ) as query_members,
            patch.object(
                new_pages,
                "_build_section",
                side_effect=lambda _site, day: _section(day),
            ) as build,
        ):
            updated = new_pages.update_text(
                original,
                site,
                today,
                project="Example project",
            )
            assert {call.args[1] for call in build.call_args_list} == missing
            assert "Manual introduction." in updated
            assert "Keep this note." in updated
            headings = mwparserfromhell.parse(updated).filter_headings()
            assert len([
                heading
                for heading in headings
                if heading.level == _DAY_HEADING_LEVEL
            ]) == len(days)
            assert 'aranami begin="new-pages" days="100"' in updated
            assert new_pages.record_dates(updated) == set(days)
            assert (
                new_pages.record_dates(updated)
                - new_pages.record_dates(original)
                == missing
            )
            build.reset_mock()
            repeated = new_pages.update_text(
                updated,
                site,
                today,
                project="Example project",
            )
            build.assert_not_called()
            assert repeated == updated
        query_members.assert_called_with(site, "Example project")
        read_pages.assert_not_called()

    @staticmethod
    def test_comment_configuration_and_unmanaged_dates() -> None:
        """Read retention comments and preserve outside text."""
        today = dt.date(2026, 10, 3)
        outside = _section(dt.date(2020, 1, 1))
        retained_days = 3
        original = outside + managed_region(
            "new-pages",
            "",
            {"days": str(retained_days)},
        )
        metadata = pl.DataFrame(
            schema={"full_title": pl.String, "pa_class": pl.String},
        )
        with (
            patch.object(
                new_pages,
                "query_pages_by_wikiproject",
                return_value=metadata,
            ),
            patch.object(
                new_pages,
                "_build_section",
                side_effect=lambda _site, day: _section(day),
            ) as build,
        ):
            updated = new_pages.update_text(
                original,
                Mock(),
                today,
                project="Example project",
            )
        assert build.call_count == retained_days
        assert updated.startswith(outside)
        assert 'days="3"' in updated
        assert new_pages.record_dates(original) == set()
        assert new_pages.record_dates(updated) == {
            today - dt.timedelta(days=offset)
            for offset in range(1, retained_days + 1)
        }

    @staticmethod
    @patch("aranami.jobs._execution.perf_counter", new=lambda: 0.0)
    def test_job_owns_target_and_records_filled_date_metadata() -> None:
        """Keep summaries and tags consistent with generated dates."""
        site = Mock()
        today = dt.date(2026, 10, 3)
        yesterday = today - dt.timedelta(days=1)
        previous = today - dt.timedelta(days=2)
        outside = _section(dt.date(2020, 1, 1))
        original = outside + managed_region(
            "new-pages",
            _section(previous),
            {"days": "2"},
        )
        updated = outside + managed_region(
            "new-pages",
            _section(yesterday) + _section(previous),
            {"days": "2"},
        )
        context = JobContext(site, today, dry=True)
        page = Mock(text=original)
        title = "WikiProject:电子游戏/新进条目/关键词筛选"
        with (
            patch.object(
                new_pages_job,
                "job_run",
                side_effect=lambda *_, **__: nullcontext(context),
            ),
            patch.object(
                new_pages_job,
                "read_pages",
                return_value=[page],
            ) as read_pages,
            patch.object(
                new_pages_job,
                "update_text",
                return_value=updated,
            ) as update_text,
        ):
            new_pages_job.run(context=context)
            read_pages.assert_called_once_with(site, [title])
            update_text.assert_called_once_with(
                original,
                site,
                today,
                project="电子游戏",
            )
            [edit] = context.edits
            assert edit.site is site
            assert edit.title == title
            assert edit.original_text == original
            assert edit.text == updated
            assert edit.summary == (
                "Updated records for 2 October 2026. "
                "Found 0 article pages and 0 non-article pages. "
                "Executed in 0.00\u2033."
            )
            assert edit.tags == ("new-pages", "filled-1-dates")
            page.text = updated
            new_pages_job.run(context=context)
        assert context.edits[-1].original_text == updated
        assert context.edits[-1].text == updated
        assert context.edits[-1].summary == edit.summary
        assert context.edits[-1].tags == ("new-pages", "filled-0-dates")
        page.save.assert_not_called()

    @staticmethod
    def test_namespace_counts_ignore_other_days_and_unmanaged_links() -> None:
        """Count primary list links using site namespace semantics."""
        day = dt.date(2025, 5, 5)
        earlier = dt.date(2025, 5, 4)
        text = "* [[Outside]]\n" + managed_region(
            "new-pages",
            _section(day)
            + "* [[:Game]] {{Talk|Game|討論}}\n"
            + "* [[:Category:Games]] [[Unrelated secondary link]]\n"
            + "* [[:Template:Game]]\n"
            + _section(earlier)
            + "* [[:Older game]]\n",
        )
        assert new_pages.record_counts(
            text,
            _TitleSite("zh", "wikipedia"),
            {day},
        ) == (1, 2)

    @staticmethod
    @patch("aranami.jobs._execution.perf_counter", new=lambda: 0.0)
    def test_summary_reports_latest_counts_and_older_backfill() -> None:
        """Separate the current day's matches from recovered records."""
        day = dt.date(2025, 5, 5)
        older = {day - dt.timedelta(days=offset) for offset in range(1, 12)}
        original = managed_region("new-pages", "", {"days": "12"})
        updated = managed_region(
            "new-pages",
            _section(day)
            + "* [[:Game]]\n* [[:Category:Games]]\n"
            + "".join(
                _section(value) for value in sorted(older, reverse=True)
            ),
            {"days": "12"},
        )
        context = JobContext(
            _TitleSite("zh", "wikipedia"),
            day + dt.timedelta(days=1),
            dry=True,
        )
        with (
            patch.object(
                new_pages_job,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                new_pages_job,
                "read_pages",
                return_value=[Mock(text=original)],
            ),
            patch.object(new_pages_job, "update_text", return_value=updated),
        ):
            new_pages_job.run(context=context)
        [edit] = context.edits
        assert edit.summary == (
            "Updated records for 5 May 2025. "
            "Found 1 article page and 1 non-article page. "
            "Backfilled 11 older daily records. "
            "Executed in 0.00\u2033."
        )
        assert len(edit.summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_icons_are_repeatable_and_preserve_links() -> None:
        """Replace aliased icons without changing links or spacing."""
        source = (
            "* {{模板:Class/icon|old}} [[:Game|Display]] tail\n"
            "* {{class/icon|GA}} [[Other]]\n"
        )
        site = _TitleSite("zh", "wikipedia")
        updated = new_pages.update_icons(source, {"Game": "优良"}, site)
        assert (
            updated
            == "* {{class/icon|优良}} [[:Game|Display]] tail\n* [[Other]]\n"
        )
        assert (
            new_pages.update_icons(updated, {"Game": "优良"}, site) == updated
        )

    @staticmethod
    def test_legacy_keyword_literal_errors_are_repaired() -> None:
        """Recognize console games separately from category syntax."""
        assert new_pages.matches_keywords("Console games", "")
        assert new_pages.matches_keywords("Example", "肉鸽 game")
        assert not new_pages.matches_keywords("Example", "Ordinary biography")
