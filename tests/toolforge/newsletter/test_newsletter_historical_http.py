"""Verify saved historical enrichment for older newsletters."""

# Exact chart fixtures make preservation and cache invalidation visible.

from __future__ import annotations

import datetime as dt
from contextlib import chdir
from html import unescape
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient

from data.newsletter.archive import DEFAULT_PATH, NewsletterDraftArchive
from data.newsletter.historical import import_historical_data
from data.newsletter.observations import OBSERVATIONS_PATH, save_observation
from data.newsletter.schema import valid_newsletter
from data.paths import artifact_path
from shared.newsletter import SUFFIXES, Issue
from webservice.server import create_app

_DAY = dt.date(2026, 10, 31)
_CLOCK = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
_ISSUE = Issue.parse("2027-01")
_COUNTS = dict.fromkeys(("total", "fa", "fl", "ga", "dyk", "bplus", "topics"))
_URL = "/newsletter/?issue=2027-01&date=2026-10-31&lang=en"
_UNKNOWN = "尚未取得累計新條目推薦數量的完整歷史資料。"


def _review() -> dict:
    """Return a valid pre-series annual observation.

    Returns:
        Saved current-year counts with unknown historical totals.
    """
    return {
        "year": 2026,
        "period": "2026-01-01/2027-01-01",
        "observation_end": "2026-11-01",
        "complete": False,
        "overview": _COUNTS,
        "previous_overview": _COUNTS,
        "quality_changes": [],
        "dyk": [],
        "awards": [],
        "newcomers": [],
        "dyk_current": {"date": "2026-11-01", "count": 2},
    }


def _seed(count: int = 41) -> dict:
    """Build an explicit synthetic historical import fixture.

    Returns:
        One covered baseline with no invented page or revision evidence.
    """
    return {
        "schema": 1,
        "dataset": "newsletter-audited-historical-aggregates",
        "rows": [
            {
                "kind": "dyk_cumulative",
                "date": "2025-12-31",
                "grade": None,
                "count": count,
                "source_title": None,
                "source_revision": None,
                "source_url": None,
                "license": None,
                "coverage": "cumulative-through-date",
                "audited_on": "2026-11-02",
                "source_note": "Offline fixture.",
            },
        ],
    }


def _files(runtime: Path) -> dict[Path, bytes]:
    """Read exact saved contents for write detection.

    Returns:
        Every retained file and its original bytes.
    """
    return {
        path: path.read_bytes()
        for path in runtime.rglob("*")
        if path.is_file()
    }


class HistoricalNewsletterRequests(TestCase):
    """Use only validated retained figures during HTTP rendering."""

    def setUp(self) -> None:
        """Create an isolated saved archive and real HTTP client."""
        self.runtime = Path(self.enterContext(TemporaryDirectory()))
        self.archive = NewsletterDraftArchive(self.runtime / DEFAULT_PATH)
        self.client = self.enterContext(TestClient(create_app(self.runtime)))

    def _save_feature(self, review: dict | None = None) -> None:
        """Retain an independent annual archive before the request."""
        self.archive.save_feature(
            _ISSUE,
            _review() if review is None else review,
            generated_at=_CLOCK,
        )

    def _get(self, url: str = _URL) -> tuple[int, str]:
        """Exercise HTTP while forbidding source clients and writes.

        Returns:
            HTTP status and decoded source wikitext within the HTML.
        """
        before = _files(self.runtime)
        with (
            patch("pywikibot.Site", side_effect=AssertionError("source")),
            patch(
                "pywikibot.Page.__init__",
                side_effect=AssertionError("page"),
            ),
            patch.object(Path, "mkdir", side_effect=AssertionError("write")),
        ):
            response = self.client.get(url)
        assert _files(self.runtime) == before
        return response.status_code, unescape(response.text)

    def test_missing_provider_keeps_unknown_and_creates_no_data(self) -> None:
        """Render a saved annual page without reconstructing history."""
        self._save_feature()
        status, text = self._get()
        assert status == HTTPStatus.OK
        assert _UNKNOWN in text
        assert "| y1 = 2 " not in text
        assert not (
            self.runtime / "data/parsed/newsletter/historical"
        ).exists()

    def test_import_invalidates_warm_page_and_uses_explicit_runtime(
        self,
    ) -> None:
        """Replace unknown output when canonical data appears."""
        self._save_feature()
        assert _UNKNOWN in self._get()[1]
        import_historical_data(self.runtime, seed=_seed())
        other = Path(self.enterContext(TemporaryDirectory()))
        import_historical_data(other, seed=_seed(900))
        with chdir(other):
            status, text = self._get()
        assert status == HTTPStatus.OK
        assert "| y1 = 41, 43 " in text
        assert "| y1 = 900, 902 " not in text
        assert _UNKNOWN not in text
        saved = self.archive.read_feature(_ISSUE, _DAY)
        assert "dyk_series" not in saved["year_review"]

    def test_corrupt_provider_invalidates_warm_page_with_read_error(
        self,
    ) -> None:
        """Expose corrupt saved history instead of cached good HTML."""
        self._save_feature()
        bundle = import_historical_data(self.runtime, seed=_seed())
        assert self._get()[0] == HTTPStatus.OK
        (artifact_path(bundle.path, "figures.parquet")).write_bytes(b"damaged")
        status, text = self._get()
        assert status == HTTPStatus.SERVICE_UNAVAILABLE
        assert "data-newsletter-wikitext" not in text

    def test_prepared_series_survives_unrelated_corrupt_provider(self) -> None:
        """Preserve the observation already retained in newer facts."""
        review = _review()
        review["dyk_series"] = [{"date": "2025-12-31", "count": 777}]
        self._save_feature(review)
        bundle = import_historical_data(self.runtime, seed=_seed())
        (artifact_path(bundle.path, "figures.parquet")).write_bytes(b"damaged")
        status, text = self._get()
        assert status == HTTPStatus.OK
        assert "| y1 = 777 " in text
        assert "| y1 = 41, 43 " not in text

    def test_current_and_dated_full_issues_receive_same_saved_baseline(
        self,
    ) -> None:
        """Enrich both issue paths without changing saved files."""
        facts = {
            "issue": _ISSUE.value,
            "period": "2026-10-01/2027-01-01",
            "updated_at": _CLOCK.isoformat(),
            "available": True,
            "observation_end": "2026-11-01",
            "complete": False,
            "overview": _COUNTS,
            "previous_overview": _COUNTS,
            "existing": dict.fromkeys(SUFFIXES),
            "sources": [],
            "year_review": _review(),
        }
        data = {"issues": {_ISSUE.value: facts}}
        assert valid_newsletter(data)
        save_observation(
            self.runtime / OBSERVATIONS_PATH,
            data,
            valid_newsletter,
        )
        self.archive.save(data, generated_at=_CLOCK)
        import_historical_data(self.runtime, seed=_seed())
        for url in ("/newsletter/?issue=2027-01&lang=en", _URL):
            with self.subTest(url=url):
                status, text = self._get(url)
                assert status == HTTPStatus.OK
                assert "| y1 = 41, 43 " in text
                assert _UNKNOWN not in text
