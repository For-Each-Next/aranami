"""Verify English listing dates and disposable cache recovery."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from sqlalchemy.dialects import mysql
from wiki_fixtures import OfflineSite

from aranami.services.enwiki import quality_listing_dates as dates
from aranami.sources.dyk import TalkPage
from aranami.sources.quarry import enwp
from aranami.support import quality_dates_cache as cache


def _pages(*classes: str) -> pl.DataFrame:
    """Build typed article selections for date-service tests.

    Args:
        classes: Assessment classes ordered by article identifier.

    Returns:
        Rows with stable article IDs and English classes.
    """
    return pl.DataFrame(
        {"page_id": list(range(1, len(classes) + 1)), "en_class": classes},
        schema={"page_id": pl.Int64, "en_class": pl.String},
    )


def _revisions(*oldids: int | None) -> pl.DataFrame:
    """Build corresponding replica talk-page identities.

    Args:
        oldids: Latest revision IDs, or missing talk pages.

    Returns:
        Article and nullable talk-page metadata.
    """
    return pl.DataFrame(
        {
            "page_id": list(range(1, len(oldids) + 1)),
            "talk_page_id": [
                identifier + 100 if oldid is not None else None
                for identifier, oldid in enumerate(oldids, start=1)
            ],
            "oldid": oldids,
        },
        schema={
            "page_id": pl.Int64,
            "talk_page_id": pl.Int64,
            "oldid": pl.Int64,
        },
    )


class TestListingDates(TestCase):
    """Check source selection, revision reuse, and unknown dates."""

    @staticmethod
    def test_unknown_dates_reuse_cache_and_changed_subset_refreshes() -> None:
        """Reuse unknown dates and fetch only changed talks."""
        site = OfflineSite("en", "wikipedia")
        talks = [
            TalkPage(101, 201, "{{GA|5 September 2026}}"),
            TalkPage(
                102,
                202,
                "{{Article history|action1=FAC"
                "|action1result=promoted|action1date=September 2026}}",
            ),
        ]
        with (
            TemporaryDirectory() as directory,
            patch.object(cache.Path, "cwd", return_value=Path(directory)),
            patch.object(
                dates,
                "template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch.object(
                dates,
                "fetch_en_talk_revisions",
                side_effect=[
                    _revisions(201, 202),
                    _revisions(201, 202),
                    _revisions(201, 203),
                ],
            ),
            patch.object(
                dates,
                "read_talk_pages",
                side_effect=[talks, [TalkPage(102, 203, "No milestone.")]],
            ) as reads,
        ):
            first = dates.listing_dates(_pages("GA", "FA"), site=site)
            second = dates.listing_dates(_pages("GA", "FA"), site=site)
            third = dates.listing_dates(_pages("GA", "FA"), site=site)
            saved = cache.load().sort("page_id")
        expected = [date(2026, 9, 5), None]
        assert first.sort("page_id")["quality_date"].to_list() == expected
        assert second.equals(first)
        assert third.equals(first)
        assert [call.args[1] for call in reads.call_args_list] == [
            [101, 102],
            [102],
        ]
        assert saved["oldid"].to_list() == [201, 203]
        assert saved["quality_date"].to_list() == expected

    @staticmethod
    def test_class_and_alias_changes_invalidate_cached_dates() -> None:
        """Reparse text after grade or template-identity changes."""
        site = OfflineSite("en", "wikipedia")
        text = (
            "{{Article history|action1=GAN|action1result=listed"
            "|action1date=1 January 2025|action2=FAC|action2result=promoted"
            "|action2date=2 February 2026}}"
        )
        aliases = Mock(side_effect=lambda _site, title: (title,))
        with (
            TemporaryDirectory() as directory,
            patch.object(cache.Path, "cwd", return_value=Path(directory)),
            patch.object(dates, "template_aliases", aliases),
            patch.object(
                dates,
                "fetch_en_talk_revisions",
                return_value=_revisions(201),
            ),
            patch.object(
                dates,
                "read_talk_pages",
                return_value=[TalkPage(101, 201, text)],
            ) as reads,
        ):
            ga = dates.listing_dates(_pages("GA"), site=site)
            fa = dates.listing_dates(_pages("FA"), site=site)
            aliases.side_effect = lambda _site, title: (title, "Template:AH")
            aliased = dates.listing_dates(_pages("FA"), site=site)
        assert ga.item(0, "quality_date") == date(2025, 1, 1)
        assert fa.item(0, "quality_date") == date(2026, 2, 2)
        assert aliased.equals(fa)
        expected_reads = 3
        assert reads.call_count == expected_reads

    @staticmethod
    def test_missing_talk_pages_and_pruning_do_not_read_text() -> None:
        """Keep unknown dates for absent talks and trim old members."""
        site = OfflineSite("en", "wikipedia")
        with (
            TemporaryDirectory() as directory,
            patch.object(cache.Path, "cwd", return_value=Path(directory)),
            patch.object(
                dates,
                "template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch.object(
                dates,
                "fetch_en_talk_revisions",
                side_effect=[_revisions(None, None), _revisions(None)],
            ),
            patch.object(dates, "read_talk_pages") as reads,
        ):
            dates.listing_dates(_pages("GA", "FA"), site=site)
            result = dates.listing_dates(_pages("GA"), site=site)
            saved = cache.load()
        assert result.to_dicts() == [{"page_id": 1, "quality_date": None}]
        assert saved["page_id"].to_list() == [1]
        assert saved["oldid"].to_list() == [None]
        reads.assert_not_called()

    @staticmethod
    def test_cache_records_actual_text_revision_during_replica_lag() -> None:
        """Keep fresh text associated with its preloaded revision ID."""
        site = OfflineSite("en", "wikipedia")
        with (
            TemporaryDirectory() as directory,
            patch.object(cache.Path, "cwd", return_value=Path(directory)),
            patch.object(
                dates,
                "template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch.object(
                dates,
                "fetch_en_talk_revisions",
                side_effect=[_revisions(200), _revisions(201)],
            ),
            patch.object(
                dates,
                "read_talk_pages",
                return_value=[TalkPage(101, 201, "{{GA|5 September 2026}}")],
            ) as reads,
        ):
            first = dates.listing_dates(_pages("GA"), site=site)
            saved = cache.load()
            second = dates.listing_dates(_pages("GA"), site=site)
        assert saved["oldid"].to_list() == [201]
        assert second.equals(first)
        reads.assert_called_once_with(site, [101])

    @staticmethod
    def test_omitted_api_talk_page_remains_eligible_for_retry() -> None:
        """Retry API omissions without caching false parsing results."""
        site = OfflineSite("en", "wikipedia")
        with (
            TemporaryDirectory() as directory,
            patch.object(cache.Path, "cwd", return_value=Path(directory)),
            patch.object(
                dates,
                "template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch.object(
                dates,
                "fetch_en_talk_revisions",
                return_value=_revisions(201),
            ),
            patch.object(
                dates,
                "read_talk_pages",
                side_effect=[
                    [],
                    [TalkPage(101, 201, "{{GA|5 September 2026}}")],
                ],
            ) as reads,
        ):
            first = dates.listing_dates(_pages("GA"), site=site)
            saved = cache.load()
            second = dates.listing_dates(_pages("GA"), site=site)
        assert first["quality_date"].to_list() == [None]
        assert saved.is_empty()
        assert second["quality_date"].to_list() == [date(2026, 9, 5)]
        expected_reads = 2
        assert reads.call_count == expected_reads

    @staticmethod
    def test_empty_quality_selection_skips_external_reads() -> None:
        """Skip external reads for importance-only articles."""
        with (
            patch.object(dates, "template_aliases") as aliases,
            patch.object(dates, "fetch_en_talk_revisions") as revisions,
            patch.object(dates, "read_talk_pages") as reads,
            patch.object(dates.pywikibot, "Site") as site,
        ):
            frame = dates.listing_dates(_pages("B", "Start"))
        assert frame.schema == {"page_id": pl.Int64, "quality_date": pl.Date}
        assert frame.is_empty()
        aliases.assert_not_called()
        revisions.assert_not_called()
        reads.assert_not_called()
        site.assert_not_called()


class TestQualityDatesCache(TestCase):
    """Check null-date persistence and atomic disposable snapshots."""

    @staticmethod
    def test_malformed_snapshot_rebuilds_with_diagnostic() -> None:
        """Ignore unreadable files and rebuild authoritative dates."""
        with (
            TemporaryDirectory() as directory,
            patch.object(cache.Path, "cwd", return_value=Path(directory)),
        ):
            path = Path(directory) / "cache" / cache._FILENAME  # ruff: ignore[private-member-access]
            path.parent.mkdir()
            path.write_text("invalid parquet", encoding="utf-8")
            with patch.object(cache._LOGGER, "warning") as warning:  # ruff: ignore[private-member-access]
                result = cache.load()
        assert result.is_empty()
        assert result.schema == cache.SCHEMA
        warning.assert_called_once()

    @staticmethod
    def test_failed_atomic_replacement_preserves_healthy_snapshot() -> None:
        """Keep the healthy cache and clean up a failed replacement."""
        frame = pl.DataFrame(
            [
                {
                    "page_id": 1,
                    "en_class": "GA",
                    "talk_page_id": 101,
                    "oldid": 201,
                    "aliases": "verified aliases",
                    "quality_date": None,
                },
            ],
            schema=cache.SCHEMA,
        )
        with (
            TemporaryDirectory() as directory,
            patch.object(cache.Path, "cwd", return_value=Path(directory)),
        ):
            cache.save(frame)
            with patch.object(cache.Path, "replace", side_effect=OSError):
                cache.save(frame.with_columns(pl.lit(202).alias("oldid")))
            actual = cache.load()
            files = list((Path(directory) / "cache").iterdir())
        assert actual.equals(frame)
        expected_files = 1
        assert len(files) == expected_files


class TestTalkRevisionQuery(TestCase):
    """Check bounded read-only metadata joins for English talk pages."""

    @staticmethod
    def test_talk_revisions_use_batched_article_ids_and_nullable_talks() -> (
        None
    ):
        """Read nullable talk identities by namespace and title."""
        replica = Mock()
        replica.query.return_value.collect.return_value = _revisions(None)
        identifiers = list(range(1, 502))
        with patch.object(enwp, "Replica", return_value=replica):
            result = enwp.fetch_en_talk_revisions([*identifiers, 1])
        expected_queries = 2
        assert replica.query.call_count == expected_queries
        statement = replica.query.call_args_list[0].args[0]
        compiled = statement.compile(dialect=mysql.dialect())
        assert "LEFT OUTER JOIN" in str(compiled)
        assert "talk.page_title = page.page_title" in str(compiled)
        assert "talk.page_latest AS oldid" in str(compiled)
        assert identifiers[:500] in compiled.params.values()
        assert result.schema == {
            "page_id": pl.Int64,
            "talk_page_id": pl.Int64,
            "oldid": pl.Int64,
        }
