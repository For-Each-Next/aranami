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
from aranami.support.wikitext import managed_region

_DAY_HEADING_LEVEL = 3


class _TitleSite(BaseSite):
    """Provide Chinese title metadata without network access."""

    @staticmethod
    def _build_namespaces() -> dict[int, Namespace]:
        """Build localized Template metadata for title comparisons.

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
        context = JobContext(site, today, dry_run=True)
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
            assert "保留2天，补充1天记录" in edit.summary  # ruff: ignore[ambiguous-unicode-character-string]
            assert edit.tags == ("new-pages", "filled-1-dates")
            page.text = updated
            new_pages_job.run(context=context)
        assert context.edits[-1].original_text == updated
        assert context.edits[-1].text == updated
        assert "保留2天，补充0天记录" in context.edits[-1].summary  # ruff: ignore[ambiguous-unicode-character-string]
        assert context.edits[-1].tags == ("new-pages", "filled-0-dates")
        page.save.assert_not_called()

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
