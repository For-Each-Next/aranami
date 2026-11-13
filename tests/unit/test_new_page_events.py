"""Verify monthly discovery and responsible event ordering."""

from __future__ import annotations

import datetime as dt
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.new_pages import DailyRecord
from shared.sources.quarry import projects
from shared.support.regions import managed_region
from tasks.zhwiki import new_pages

_SITE = OfflineSite("zh", "wikipedia")
_EVENT_SCHEMA = {
    "page_id": pl.Int64,
    "user_id": pl.Int64,
    "occurred_at": pl.Datetime("us", "UTC"),
}


class TestNewPageEvents(TestCase):
    """Retain required dates and order by responsible revisions."""

    def test_calendar_month_backfills_oldest_first(self) -> None:
        """Cover October 8 to November 7 and clamp short months."""
        for stop, start in (
            (dt.date(2026, 11, 8), dt.date(2026, 10, 8)),
            (dt.date(2026, 3, 31), dt.date(2026, 2, 28)),
            (dt.date(2028, 3, 31), dt.date(2028, 2, 29)),
            (dt.date(2026, 1, 8), dt.date(2025, 12, 8)),
        ):
            with (
                self.subTest(stop=stop),
                patch.object(
                    new_pages,
                    "collect_day",
                    side_effect=lambda _, day, **_kwargs: DailyRecord(
                        day,
                        discovery_count=0,
                    ),
                ) as collect,
                patch.object(
                    new_pages,
                    "query_pages_by_wikiproject",
                    return_value=pl.DataFrame(
                        schema={
                            "page_id": pl.Int64,
                            "full_title": pl.String,
                            "pa_class": pl.String,
                        },
                    ),
                ),
            ):
                prepared = new_pages.prepare_report(
                    managed_region("new-pages", ""),
                    _SITE,
                    stop,
                    project="电子游戏",
                )
            expected = tuple(
                start + dt.timedelta(days=offset)
                for offset in range((stop - start).days)
            )
            assert (
                tuple(call.args[1] for call in collect.call_args_list)
                == expected
            )
            assert prepared.dates == frozenset(expected)

    @staticmethod
    def test_namespace_groups_then_responsible_user_and_event_time() -> None:
        """Place drafts after articles and exclude discussion pages."""
        namespaces = [10, 118, 0, 0, 0, 0, 1]
        pages = [
            Mock(
                pageid=identifier,
                title=Mock(return_value=f"Game {identifier}"),
                namespace=Mock(return_value=namespace),
                exists=Mock(return_value=True),
                isRedirectPage=Mock(return_value=False),
                text="电子游戏",
            )
            for identifier, namespace in enumerate(namespaces, 1)
        ]
        events = pl.DataFrame(
            {
                "page_id": list(range(1, 8)),
                "user_id": [1, 1, 20, 10, 10, None, 2],
                "occurred_at": [
                    dt.datetime(2026, 11, 1, hour, tzinfo=dt.UTC)
                    for hour in [0, 0, 0, 2, 1, 0, 0]
                ],
            },
            schema=_EVENT_SCHEMA,
        )
        with (
            patch.object(
                new_pages,
                "new_page_ids",
                return_value=list(range(1, 8)),
            ),
            patch.object(
                new_pages,
                "redirect_converted_page_ids",
                return_value=[4],
            ),
            patch.object(
                new_pages,
                "page_discovery_metadata",
                return_value=events,
            ),
            patch.object(new_pages, "read_page_ids", return_value=pages),
        ):
            record = new_pages.collect_day(_SITE, dt.date(2026, 11, 1))
        assert [page.page_id for page in record.pages] == [5, 4, 3, 6, 2, 1]
        assert record.pages[1].converted

    @staticmethod
    def test_discovery_metadata_selects_first_event_and_nullable_actor() -> (
        None
    ):
        """Distinguish tagged conversions from initial creations."""
        with patch(
            "shared.sources.quarry.query._fetch",
            return_value=(
                ("page_id", "user_id", "occurred_at"),
                [
                    (1, 20, "20261101120000"),
                    (1, 10, "20261101010000"),
                    (2, None, "20261101020000"),
                ],
            ),
        ) as fetch:
            result = projects.page_discovery_metadata(
                _SITE,
                [1, 2],
                [2],
                dt.date(2026, 11, 1),
                dt.date(2026, 11, 2),
            )
        statement = fetch.call_args.args[1]
        sql = str(statement)
        assert "actor.actor_user" in sql
        assert "revision.rev_actor = actor.actor_id" in sql
        assert "mw-removed-redirect" in statement.compile().params.values()
        assert result["user_id"].to_list() == [10, None]
        assert result["occurred_at"].to_list() == [
            dt.datetime(2026, 11, 1, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 11, 1, 2, tzinfo=dt.UTC),
        ]
