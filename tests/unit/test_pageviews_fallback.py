"""Verify fresh endpoint fallback before chronological daily reports."""

# Exercise backend seams and ordinary unittest exception assertions.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
import json
from contextlib import chdir
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError

import mwparserfromhell
import polars as pl

from backend.services.zhwiki import pageviews
from updater.jobs import pageviews as job
from updater.services.zhwiki import pageviews as rendering

_FIRST = dt.date(2026, 10, 16)
_TARGET = dt.date(2026, 10, 18)
_START = _FIRST + dt.timedelta(days=1) - dt.timedelta(days=800)
_SCHEMA = {"date": pl.Date, "pageview": pl.Int64}
_SETTINGS = replace(
    job.REPORT_SETTINGS.source_settings(),
    task_forces=(),
)


def _section(report: rendering.PageviewReport, index: int) -> dict:
    """Read one JSON list produced for a fallback report.

    Args:
        report: Generated wikitext and its prepared ranking metadata.
        index: Position of the selected project ranking interval.

    Returns:
        Inclusive metadata and ordered displayed observations.
    """
    templates = mwparserfromhell.parse(report.text).filter_templates()
    invocation = templates[index]
    assert str(invocation.name) == "#invoke:PJ:VG/HOT/v2"
    assert str(invocation.get("1").value) == "main"
    return json.loads(str(invocation.get("data").value))


def _site() -> MagicMock:
    """Return a stable offline wiki identity."""
    site = MagicMock()
    site.hostname.return_value = "zh.wikipedia.org"
    site.dbName.return_value = "zhwiki"
    return site


def _members(count: int = 2) -> pl.DataFrame:
    """Return normalized distinct project members."""
    return pl.DataFrame({
        "title": [f"A{index}" for index in range(count)],
        "class": ["B"] * count,
        "importance": ["低"] * count,
    })


def _observations(*days: dt.date) -> pl.DataFrame:
    """Return explicit zero observations for the requested dates."""
    return pl.DataFrame(
        [(day, 0) for day in days],
        schema=_SCHEMA,
        orient="row",
    )


class TestDownloadFallback(TestCase):
    """Validate candidates independently of publication and caches."""

    def setUp(self) -> None:
        """Isolate history tests from readiness probes."""
        readiness = patch.object(pageviews, "_require_day_ready")
        readiness.start()
        self.addCleanup(readiness.stop)

    def test_shorter_endpoints_are_fresh_and_feed_every_daily_report(
        self,
    ) -> None:
        """Try earlier ends and reuse only the healthy batch."""
        for healthy_day in (18, 17, 16):
            candidate = dt.date(2026, 10, healthy_day)
            days = tuple(
                dt.date(2026, 10, day) for day in range(16, healthy_day + 1)
            )
            site = _site()
            with (
                self.subTest(healthy_day=healthy_day),
                TemporaryDirectory() as directory,
                chdir(directory),
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_members(),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=[_observations(_FIRST)] * (18 - healthy_day)
                    + [_observations(*days)] * 2,
                ) as fetch,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    return_value=pl.DataFrame(
                        schema={"title": pl.String, "top": pl.Int64},
                    ),
                ),
                pageviews.download_batch(),
            ):
                end = pageviews.prepare_downloads(
                    site,
                    _FIRST,
                    _TARGET,
                    settings=_SETTINGS,
                )
                assert end == candidate
                expected = [
                    call(site, "A0", _START, dt.date(2026, 10, day + 1))
                    for day in range(18, healthy_day, -1)
                ]
                expected.extend(
                    call(site, title, _START, end + dt.timedelta(days=1))
                    for title in ("A0", "A1")
                )
                assert fetch.call_args_list == expected
                for day in days:
                    prepared = pageviews.prepare_report(
                        site,
                        day + dt.timedelta(days=1),
                        settings=_SETTINGS,
                        prefetch_stop=end + dt.timedelta(days=1),
                    )
                    assert prepared.data_date == day
                assert fetch.call_args_list == expected

    def test_every_endpoint_fails_without_snapshot_writes(self) -> None:
        """Keep snapshots unchanged when every endpoint fails."""
        site = _site()
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(),
            ) as fetch,
            patch.object(pageviews.PageviewsCache, "load") as load,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            pageviews.prepare_downloads(
                site,
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
        assert fetch.call_args_list == [
            call(site, "A0", _START, dt.date(2026, 10, day))
            for day in (19, 18, 17)
        ]
        load.assert_not_called()

    @staticmethod
    def test_exactly_one_percent_missing_accepts_measured_zeroes() -> None:
        """Count distinct membership, retaining the strict threshold."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=pl.concat([_members(100), _members(1)]),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=lambda _site, title, _start, _stop: (
                    _observations()
                    if title == "A0"
                    else _observations(_TARGET)
                ),
            ) as fetch,
        ):
            assert (
                pageviews.prepare_downloads(
                    _site(),
                    _FIRST,
                    _TARGET,
                    settings=_SETTINGS,
                )
                == _TARGET
            )
        expected_count = 100
        assert fetch.call_count == expected_count

    def test_quick_stop_uses_full_population_not_processed_prefix(
        self,
    ) -> None:
        """Stop on the second missing article out of one hundred."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(100),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(),
            ) as fetch,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            pageviews.prepare_downloads(
                _site(),
                _TARGET,
                _TARGET,
                settings=_SETTINGS,
            )
        assert [request.args[1] for request in fetch.call_args_list] == [
            "A0",
            "A1",
        ]

    def test_source_outage_defers_without_endpoint_fallback(self) -> None:
        """Distinguish exhausted HTTP retries from missing days."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=HTTPError(
                    "https://example.test",
                    503,
                    "",
                    {},
                    None,
                ),
            ) as fetch,
            self.assertRaises(pageviews.PageviewsDeferredError),
        ):
            pageviews.prepare_downloads(
                _site(),
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
        attempts = 3
        assert fetch.call_count == attempts
        assert all(
            request.args[-1] == _TARGET + dt.timedelta(days=1)
            for request in fetch.call_args_list
        )

    @staticmethod
    def test_healthy_endpoint_zeroes_historical_gaps() -> None:
        """Preserve raw gaps while reporting historical zeroes."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(_TARGET),
            ) as fetch,
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(
                    schema={"title": pl.String, "top": pl.Int64},
                ),
            ),
        ):
            site = _site()
            assert (
                pageviews.prepare_downloads(
                    site,
                    _FIRST,
                    _TARGET,
                    settings=_SETTINGS,
                )
                == _TARGET
            )
            prepared = pageviews.prepare_report(
                site,
                _FIRST + dt.timedelta(days=1),
                settings=_SETTINGS,
                prefetch_stop=_TARGET + dt.timedelta(days=1),
            )
            daily = prepared.rankings[0]
            assert daily.period == "daily"
            assert daily.rows.get_column("views").to_list() == [0, 0]
            assert daily.rows.get_column("old_views").to_list() == [0, 0]
            assert daily.rows.get_column("rank").to_list() == [1, 1]
            assert daily.rows.get_column("old_rank").to_list() == [1, 1]
            assert daily.rows.get_column("observed").to_list() == [
                False,
                False,
            ]
            assert daily.rows.get_column("old_observed").to_list() == [
                False,
                False,
            ]
            assert daily.total_views == 0
            totals = prepared.result.tables["totals"].filter(
                pl.col("period") == "daily",
            )
            assert totals.item(0, "observed_articles") == 0
            assert totals.item(0, "previous_observed_articles") == 0
            report = rendering.build_report(
                prepared,
                settings=replace(job.REPORT_SETTINGS, task_forces=()),
            )
            section = _section(report, 0)
            assert section["metadata"]["total_views"] == 0
            assert [
                (item["rank"], item["previous_rank"])
                for item in section["items"]
            ] == [(1, 1), (1, 1)]
            assert [item["views"] for item in section["items"]] == [None, None]
            cache = pageviews.PageviewsCache.load(site.hostname())
            history = cache.get("A0")
            assert history is not None
            assert history.filter(pl.col("date") == _FIRST).is_empty()
            assert (
                history.filter(pl.col("date") == _TARGET).item(
                    0,
                    "pageview",
                )
                == 0
            )
            measured = pageviews.prepare_report(
                site,
                _TARGET + dt.timedelta(days=1),
                settings=_SETTINGS,
            )
            assert measured.rankings[0].rows.get_column(
                "observed",
            ).to_list() == [True, True]
            totals = measured.result.tables["totals"].filter(
                pl.col("period") == "daily",
            )
            assert totals.item(0, "observed_articles") == _members().height
            assert fetch.call_count == _members().height

    @staticmethod
    def test_later_run_refills_gaps_from_fresh_full_histories() -> None:
        """Recover historical gaps from a later full download."""
        missing = _FIRST + dt.timedelta(days=1)
        later = _TARGET + dt.timedelta(days=1)
        initial = pl.DataFrame(
            [(_FIRST, 5), (_TARGET, 7)],
            schema=_SCHEMA,
            orient="row",
        )
        refreshed = pl.DataFrame(
            [(_FIRST, 5), (missing, 3), (_TARGET, 7), (later, 9)],
            schema=_SCHEMA,
            orient="row",
        )
        site = _site()
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(1),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=[initial, refreshed],
            ) as fetch,
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(
                    schema={"title": pl.String, "top": pl.Int64},
                ),
            ),
        ):
            with pageviews.download_batch():
                pageviews.prepare_downloads(
                    site,
                    _FIRST,
                    _TARGET,
                    settings=_SETTINGS,
                )
                first = pageviews.prepare_report(
                    site,
                    later,
                    settings=_SETTINGS,
                )
            assert first.rankings[0].rows.item(0, "old_views") == 0
            assert first.rankings[0].rows.item(0, "old_observed") is False
            weekly = first.rankings[1].rows
            expected_first_total = 12
            assert weekly.item(0, "views") == expected_first_total
            history = pageviews.PageviewsCache.load(site.hostname()).get(
                "A0",
            )
            assert history is not None
            assert history.filter(pl.col("date") == missing).is_empty()

            with pageviews.download_batch():
                pageviews.prepare_downloads(
                    site,
                    later,
                    later,
                    settings=_SETTINGS,
                )
                second = pageviews.prepare_report(
                    site,
                    later + dt.timedelta(days=1),
                    settings=_SETTINGS,
                )
            expected_downloads = 2
            assert fetch.call_count == expected_downloads
            assert fetch.call_args_list[-1] == call(
                site,
                "A0",
                later + dt.timedelta(days=1) - dt.timedelta(days=800),
                later + dt.timedelta(days=1),
            )
            weekly = second.rankings[1].rows
            expected_total = 24
            assert weekly.item(0, "views") == expected_total
            assert weekly.item(0, "observed") is True
            assert second.rankings[0].rows.item(0, "observed") is True
            assert second.rankings[0].rows.item(0, "old_observed") is True
            assert weekly.item(0, "start") == dt.date(2026, 10, 13)
            assert weekly.item(0, "stop") == dt.date(2026, 10, 20)
            report = rendering.build_report(
                second,
                settings=replace(job.REPORT_SETTINGS, task_forces=()),
            )
            rendered = _section(report, 1)
            assert rendered["metadata"] == {
                "project": job.PROJECT,
                "duration": "weekly",
                "start": [2026, 10, 13],
                "end": [2026, 10, 19],
                "total_pages": 1,
                "total_views": expected_total,
            }
            assert rendered["items"][0]["title"] == "A0"
            assert rendered["items"][0]["views"] == expected_total
            history = pageviews.PageviewsCache.load(site.hostname()).get(
                "A0",
            )
            assert history is not None
            expected_recovered = 3
            assert (
                history.filter(pl.col("date") == missing).item(
                    0,
                    "pageview",
                )
                == expected_recovered
            )

    def test_accepted_batch_does_not_relax_other_reports(self) -> None:
        """Limit zero fill to accepted identities and coverage."""
        for changed in ("site", "project", "members", "history", "stop"):
            site = _site()
            report_site = _site()
            settings = _SETTINGS
            history_days = 800
            stop = _FIRST + dt.timedelta(days=1)
            if changed == "site":
                report_site.hostname.return_value = "en.wikipedia.org"
            elif changed == "project":
                settings = replace(settings, project="Another project")
            elif changed == "history":
                history_days += 1
            elif changed == "stop":
                stop = _TARGET + dt.timedelta(days=2)
            with (
                self.subTest(changed=changed),
                TemporaryDirectory() as directory,
                chdir(directory),
                pageviews.download_batch(),
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_members(),
                ) as members,
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    return_value=_observations(_TARGET),
                ),
                patch.object(pageviews.PageviewsCache, "save") as save,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                ) as top,
            ):
                pageviews.prepare_downloads(
                    site,
                    _FIRST,
                    _TARGET,
                    settings=_SETTINGS,
                )
                if changed == "members":
                    members.return_value = _members(3)
                with self.assertRaises(pageviews.PageviewsUnavailableError):
                    pageviews.prepare_report(
                        report_site,
                        stop,
                        settings=settings,
                        history_days=history_days,
                        prefetch_stop=_TARGET + dt.timedelta(days=1),
                    )
                save.assert_not_called()
                top.assert_not_called()

    @staticmethod
    def test_accepted_batch_still_omits_gateway_timeout_articles() -> None:
        """Do not invent report zeroes for a failed article request."""
        responses = [
            HTTPError("https://example.test", 504, "", {}, None),
            *[_observations(_TARGET)] * 99,
        ]
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(100),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=responses,
            ) as fetch,
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(
                    schema={"title": pl.String, "top": pl.Int64},
                ),
            ),
        ):
            site = _site()
            pageviews.prepare_downloads(
                site,
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
            report = pageviews.prepare_report(
                site,
                _FIRST + dt.timedelta(days=1),
                settings=_SETTINGS,
                prefetch_stop=_TARGET + dt.timedelta(days=1),
            )
            daily = report.rankings[0].rows
            expected_articles = 99
            assert daily.height == expected_articles
            assert "A0" not in daily.get_column("title").to_list()
            assert daily.get_column("views").to_list() == [0] * daily.height
            expected_downloads = 100
            assert fetch.call_count == expected_downloads

    def test_empty_population_fails_without_downloads(self) -> None:
        """Never classify an empty project as a healthy endpoint."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(0),
            ),
            patch.object(pageviews.pageviews, "fetch_data_dataframe") as fetch,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            pageviews.prepare_downloads(
                _site(),
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
        fetch.assert_not_called()

    def test_preflight_requires_a_batch_and_an_unpublished_range(self) -> None:
        """Reject invalid coordination before collecting data."""
        with self.assertRaises(RuntimeError):
            pageviews.prepare_downloads(
                _site(),
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
        with pageviews.download_batch(), self.assertRaises(ValueError):
            pageviews.prepare_downloads(
                _site(),
                _TARGET,
                _FIRST,
                settings=_SETTINGS,
            )

    @staticmethod
    def test_membership_is_stable_only_within_one_batch() -> None:
        """Freeze the denominator only for the current invocation."""
        source = pl.DataFrame({
            "page_namespace": [0],
            "full_title": ["A"],
            "pa_class": ["B"],
            "pa_importance": ["低"],
        })
        site = _site()
        with patch.object(
            pageviews,
            "query_pages_by_wikiproject",
            return_value=source,
        ) as query:
            with pageviews.download_batch():
                first = pageviews._project_articles(site, "电子游戏")
                assert pageviews._project_articles(site, "电子游戏") is first
                query.assert_called_once()
            with pageviews.download_batch():
                assert (
                    pageviews._project_articles(site, "电子游戏") is not first
                )
        expected_count = 2
        assert query.call_count == expected_count
