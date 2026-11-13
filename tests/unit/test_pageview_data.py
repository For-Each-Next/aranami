"""Verify reusable daily traffic survives failed publication."""

# Tests exercise offline boundaries and deliberate failed writes.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data import pageviews as pageview_data
from data.reports import ReportStore
from runtime.jobs import JobContext, pageviews as job
from tasks.zhwiki import pageviews

_START = dt.date(2026, 1, 1)
_STOP = dt.date(2026, 1, 3)
_NOW = dt.datetime(2026, 1, 3, 19, tzinfo=dt.UTC)
_PROJECT = "电子游戏"


def _rows() -> pl.DataFrame:
    """Return real zero and positive daily measurements."""
    return pl.DataFrame({
        "date": [_START, _START + dt.timedelta(days=1)],
        "pageview": [0, 7],
    })


class PageviewDataTests(TestCase):
    """Read detailed traffic independently of rendered rankings."""

    def setUp(self) -> None:
        """Isolate runtime files and provide full member metadata."""
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(chdir(self.root))
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"
        self.site.hostname.return_value = "zh.wikipedia.org"
        self.members = pl.DataFrame({
            "page_id": [1],
            "title": ["Game"],
            "class": ["GA"],
            "importance": ["High"],
            "taskforces": [["电子游戏/任天堂"]],
        })

    def _save(self, *, dry: bool = False, offset: int = 0) -> object:
        """Retain a measured fixture under the production contract.

        Returns:
            Complete selected bundle.
        """
        return pageview_data.save_snapshot(
            "zhwiki",
            _PROJECT,
            self.members,
            {"Game": (_START, _STOP, _rows())},
            started_at=_NOW + dt.timedelta(seconds=offset),
            observed_at=_NOW + dt.timedelta(seconds=offset),
            hostname=self.site.hostname(),
            dry=dry,
        )

    def test_daily_reader_retains_identity_counts_and_coverage(self) -> None:
        """Keep measured zero separate from missing observations."""
        self._save()
        bundle = pageview_data.read_snapshot("zhwiki", _PROJECT)
        assert bundle.tables["daily"]["pageview"].to_list() == [0, 7]
        assert bundle.tables["daily"]["page_id"].to_list() == [1, 1]
        assert bundle.tables["coverage"]["complete"].item()
        assert bundle.tables["members"]["taskforces"].to_list() == [
            ["电子游戏/任天堂"],
        ]
        frame = pageview_data.as_cache_frame(bundle)
        assert frame["date"].null_count() == 1
        assert frame["pageview"].to_list() == [None, 0, 7]

    def test_reader_never_creates_runtime_paths(self) -> None:
        """Open existing source locks read-only for website readers."""
        self._save()
        with patch.object(
            Path,
            "mkdir",
            side_effect=AssertionError("Read-only reader"),
        ):
            assert pageview_data.read_snapshot("zhwiki", _PROJECT)
            assert pageview_data.read_snapshot("zhwiki", "Absent") is None

    def test_daily_summary_validation_preserves_row_guards(self) -> None:
        """Reject duplicate dates, outside dates and negative counts."""
        first = self._save()
        for raw in (
            pl.concat([_rows(), _rows().head(1)]),
            _rows().with_columns(pl.col("date") + pl.duration(days=1)),
            _rows().with_columns(pl.lit(-1).alias("pageview")),
        ):
            with self.subTest(rows=raw), self.assertRaises(ValueError):
                pageview_data.save_snapshot(
                    "zhwiki",
                    _PROJECT,
                    self.members,
                    {"Game": (_START, _STOP, raw)},
                    started_at=_NOW + dt.timedelta(seconds=1),
                    observed_at=_NOW + dt.timedelta(seconds=1),
                    hostname=self.site.hostname(),
                )
            assert (
                pageview_data.read_snapshot("zhwiki", _PROJECT).path
                == first.path
            )

    def test_missing_dates_are_not_stored_as_zero(self) -> None:
        """Retain explicit incomplete coverage for sparse responses."""
        pageview_data.save_snapshot(
            "zhwiki",
            _PROJECT,
            self.members,
            {"Game": (_START, _STOP, _rows().tail(1))},
            started_at=_NOW,
            observed_at=_NOW,
            hostname=self.site.hostname(),
        )
        bundle = pageview_data.read_snapshot("zhwiki", _PROJECT)
        assert bundle.tables["daily"]["pageview"].to_list() == [7]
        assert not bundle.tables["coverage"]["complete"].item()

    def test_preview_never_selects_production_data(self) -> None:
        """Keep preview traffic under the preview runtime subtree."""
        self._save(dry=True)
        assert pageview_data.read_snapshot("zhwiki", _PROJECT) is None
        assert pageview_data.read_snapshot("zhwiki", _PROJECT, dry=True)

    def test_failed_request_preserves_selected_generation(self) -> None:
        """Do not replace detailed data with a later failed response."""
        first = self._save()
        selected = pageview_data.save_snapshot(
            "zhwiki",
            _PROJECT,
            self.members,
            {"Game": (_START, _STOP, None)},
            started_at=_NOW + dt.timedelta(hours=1),
            observed_at=_NOW + dt.timedelta(hours=1),
            hostname=self.site.hostname(),
        )
        assert selected.path == first.path
        assert (
            pageview_data.read_snapshot("zhwiki", _PROJECT).path == first.path
        )

    def test_failed_storage_retains_previous_selection(self) -> None:
        """A failed generation write cannot advance the selector."""
        first = self._save()
        with (
            patch.object(ReportStore, "_persist", side_effect=OSError("full")),
            self.assertRaises(OSError),
        ):
            self._save(offset=1)
        assert (
            pageview_data.read_snapshot("zhwiki", _PROJECT).path == first.path
        )

    def test_newsletter_reads_durable_traffic_without_cache(self) -> None:
        """Use detailed saved data without disposable files."""
        self._save()
        with patch.object(
            pageviews,
            "read_pageviews_snapshot",
            side_effect=AssertionError("Legacy cache fallback"),
        ):
            annual = pageviews.read_annual_popularity(
                self.site,
                2026,
                _STOP,
                title="Report",
                titles=["Game"],
            )
        assert annual["total_views"] == 7
        assert annual["items"] == [{"title": "Game", "views": 7}]

    def test_damaged_durable_data_does_not_fall_back_to_cache(self) -> None:
        """Surface corruption without substituting disposable data."""
        first = self._save()
        (first.path / "daily.parquet").write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            pageviews.read_annual_popularity(
                self.site,
                2026,
                _STOP,
                title="Report",
                titles=["Game"],
            )

    def _job(self, *, storage_failure: bool) -> None:
        """Run an offline publisher with deliberate failures."""
        text = (
            '<!-- aranami begin="page_views" -->'
            "<!-- report date 2026-01-01 -->"
            '<!-- aranami end="page_views" -->'
        )
        page = Mock(text=text)
        page.get.side_effect = lambda _page=page, **_kwargs: _page.text
        page.save.side_effect = OSError("Wiki save failed")
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(job.PROJECT_PERIODS[0],),
            task_forces=(),
        )
        top = pl.DataFrame(
            schema={
                "title": pl.String,
                "start": pl.Date,
                "stop": pl.Date,
                "top": pl.Int64,
            },
        )
        context = JobContext(
            self.site,
            _STOP,
            dry=False,
            started_at=_NOW,
        )
        with (
            patch.object(job, "read_pages", return_value=[page]),
            patch("updater.publisher.pywikibot.Page", return_value=page),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=self.members,
            ),
            patch.object(pageviews, "_require_day_ready"),
            patch.object(pageviews, "_site_top_ranks", return_value=top),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_rows(),
            ),
            patch.object(
                pageview_data,
                "save_snapshot",
                side_effect=OSError("Durable write failed")
                if storage_failure
                else None,
                wraps=None if storage_failure else pageview_data.save_snapshot,
            ),
            self.assertRaises(OSError),
        ):
            job.run(context=context, settings=settings)
        if storage_failure:
            page.save.assert_not_called()
            assert pageview_data.read_snapshot("zhwiki", _PROJECT) is None
        else:
            page.save.assert_called_once()
            assert pageview_data.read_snapshot("zhwiki", _PROJECT) is not None

    def test_wiki_failure_leaves_reusable_daily_data(self) -> None:
        """Commit source facts before a wiki save can fail."""
        self._job(storage_failure=False)

    def test_durable_failure_prevents_wiki_publication(self) -> None:
        """Stop publication when reusable facts cannot be persisted."""
        self._job(storage_failure=True)
