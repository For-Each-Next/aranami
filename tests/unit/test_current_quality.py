"""Verify current quality cohorts and saved attribution ranges."""

# Protocol identities and counts are deliberate fixture literals.
# ruff: file-ignore[magic-value-comparison,private-member-access]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from data.articles import (
    authorship_store,
    current_authorship,
    current_quality as store,
    quality_analysis,
    quality_authors,
)
from data.articles.storage import AUTHORSHIP_PATH
from data.reports import ReportResult, ReportStore

_NOW = dt.datetime(2026, 11, 14, 12, tzinfo=dt.UTC)
_DAY = dt.date(2020, 1, 2)
_CUTOFF = dt.datetime(2020, 1, 3, tzinfo=dt.UTC)
_CREATION = dt.datetime(2019, 1, 1, tzinfo=dt.UTC)


def _contributors(
    values: tuple[tuple[str, str | None, int], ...],
) -> list[dict]:
    """Prepare exact byte shares with registered and anonymous editors.

    Returns:
        Typed contributor records for a one-hundred-byte source.
    """
    return [
        {
            "editor": editor,
            "name": name,
            "tokens": size,
            "bytes": size,
            "share": size / 100,
        }
        for editor, name, size in values
    ]


def _articles() -> pl.DataFrame:
    """Prepare three current quality pages and one optional B article.

    Returns:
        Exact current-body metrics with independently retained listings.
    """
    return pl.DataFrame(
        [
            {
                "page_id": page_id,
                "title": f"Game {page_id}",
                "grade": grade,
                "importance": importance,
                "article_revision_id": page_id + 100,
                "talk_page_id": page_id + 200,
                "talk_revision_id": page_id + 300,
                "source_bytes": 1000,
                "listed_date": _DAY,
                "listed_oldid": page_id + 50,
                "listed_review_link": "#Review",
                "observed_at": _NOW,
                "measurement_contract": "weighted-character-fixture",
                "prose_words": None,
                "prose_characters": characters,
                "prose_bytes": 300 if characters is not None else None,
                "measured_at": _NOW if characters is not None else None,
                "attempted_at": _NOW,
            }
            for page_id, grade, importance, characters in (
                (1, "GA", "Top", 100),
                (2, "B", "Low", 200),
                (3, "FA", "High", None),
                (4, "FL", "Unknown", 0),
            )
        ],
        schema=quality_analysis.ARTICLE_SCHEMA,
    )


def _evidence() -> pl.DataFrame:
    """Retain current-grade evidence and a stronger former-grade record.

    Returns:
        Exact saved sources without requiring source collection.
    """
    return pl.DataFrame(
        [
            {
                "page_id": page_id,
                "date": _DAY,
                "grade": grade,
                "action": "added",
                "contract": authorship_store.CONTRACT,
                "eligible_bytes": 100,
                "listing_revision": page_id + 50,
                "creation_revision": page_id + 10,
                "cutoff": _CUTOFF,
                "creation": _CREATION,
                "fetched_at": _NOW,
                "authors": [
                    name
                    for _editor, name, size in contributors
                    if size == max(value[2] for value in contributors)
                ],
                "contributors": _contributors(contributors),
            }
            for page_id, grade, contributors in (
                (
                    1,
                    "GA",
                    (("1", "Alice", 60), ("2", "Bob", 30), ("0|IP", None, 10)),
                ),
                (1, "FA", (("2", "Bob", 100),)),
                (
                    4,
                    "FL",
                    (("1", "Alice", 34), ("2", "Bob", 33), ("0|IP", None, 33)),
                ),
            )
        ],
        schema=authorship_store.SCHEMA,
    )


def _b_tables() -> dict[str, pl.DataFrame]:
    """Prepare one exact B cutoff with a saved attribution proof.

    Returns:
        Complete owner tables whose article and talk identities agree.
    """
    members = pl.DataFrame(
        [
            {
                "page_id": 2,
                "revision_id": 52,
                "title": "Game 2",
                "grade": "B",
                "observed_at": _NOW,
                "article_timestamp": _CUTOFF - dt.timedelta(seconds=1),
                "banner_contract": "fixture",
                "talk_page_id": 202,
                "talk_revision": 302,
                "listing_talk_revision": 250,
                "listing_timestamp": _CUTOFF,
            },
        ],
        schema=current_authorship.MEMBER_SCHEMA,
    )
    evidence = pl.DataFrame(
        [
            {
                "page_id": 2,
                "revision_id": 52,
                "contract": current_authorship.CONTRACT,
                "eligible_bytes": 100,
                "revision_timestamp": _CUTOFF - dt.timedelta(seconds=1),
                "creation_revision": 12,
                "creation_timestamp": _CREATION,
                "fetched_at": _NOW,
                "contributors": _contributors((
                    ("1", "Alice", 70),
                    ("2", "Bob", 20),
                    ("0|IP", None, 10),
                )),
            },
        ],
        schema=current_authorship.EVIDENCE_SCHEMA,
    )
    return {
        "members": members,
        "evidence": evidence,
        "attempts": pl.DataFrame(schema=current_authorship.ATTEMPT_SCHEMA),
    }


def _activity() -> pl.DataFrame:
    """Prepare saved public-edit times independently of listing dates.

    Returns:
        Two ordinary activity observations.
    """
    return pl.DataFrame(
        [
            {
                "name": name,
                "last_active": _NOW - dt.timedelta(days=1),
                "observed_at": _NOW,
            }
            for name in ("Alice", "Bob")
        ],
        schema=quality_authors.ACTIVITY_SCHEMA,
    )


class CurrentQualityTests(TestCase):
    """Keep every section bound to the same selected current pages."""

    @staticmethod
    def test_toggle_changes_every_section_and_uses_current_grades() -> None:
        """Count optional B pages and reject stronger former badges."""
        tables = store.prepare_current_quality(
            _articles(),
            _evidence(),
            _b_tables(),
            _activity(),
            observed_at=_NOW,
        )
        store._validate(tables)
        for include_b, expected_total, alice_credit in (
            (False, 3, 2),
            (True, 4, 3),
        ):
            selected = {
                name: frame.filter(pl.col("include_b") == include_b)
                for name, frame in tables.items()
            }
            assert selected["coverage"]["total"].item() == expected_total
            assert selected["grade_totals"]["pages"].sum() == expected_total
            assert (
                selected["importance_totals"]["pages"].sum() == expected_total
            )
            assert selected["years"]["pages"].sum() == expected_total
            assert (
                selected["authors"]
                .filter(pl.col("name") == "Alice")["credit"]
                .item()
                == alice_credit
            )
            assert (
                selected["authors"]
                .filter(pl.col("name") == "Bob")["credit"]
                .item()
                == 1
            )
            game = (
                selected["articles"]
                .filter(pl.col("page_id") == 1)
                .row(0, named=True)
            )
            assert game["grade"] == "GA"
            assert game["authors"] == ["Alice"]
            assert [
                editor["name"] for editor in game["authorship_editors"]
            ] == ["Alice", "Bob"]
            assert game["authorship_start_revision"] == 11
            assert game["authorship_end_revision"] == 51

    @staticmethod
    def test_continuous_years_zero_measurements_and_cohort_length_groups() -> (
        None
    ):
        """Distinguish empty years, zero prose and missing prose."""
        tables = store.prepare_current_quality(
            _articles(),
            _evidence(),
            _b_tables(),
            _activity(),
            observed_at=_NOW,
        )
        years = tables["years"].filter(~pl.col("include_b"))
        assert years["year"].to_list() == list(range(2010, 2027))
        assert years.filter(pl.col("year").is_in([2010, 2011]))[
            "pages"
        ].to_list() == [0, 0]
        coverage = (
            tables["coverage"].filter(~pl.col("include_b")).row(0, named=True)
        )
        assert coverage["measured"] == 2
        assert coverage["median_characters"] == 50
        lengths = tables["lengths"].filter(
            ~pl.col("include_b") & (pl.col("group") == "grade"),
        )
        assert lengths["pages"].sum() == 3
        assert (
            lengths.filter(pl.col("grade") == "FL")["median_characters"].item()
            == 0
        )
        assert (
            lengths.filter(pl.col("grade") == "FA")["median_characters"].item()
            is None
        )

    @staticmethod
    def test_mismatched_talk_heads_and_listing_dates_do_not_gain_credit() -> (
        None
    ):
        """Keep stale successful caches outside the current view."""
        articles = _articles().with_columns(
            pl
            .when(pl.col("page_id") == 1)
            .then(pl.lit(_DAY + dt.timedelta(days=1)))
            .otherwise(pl.col("listed_date"))
            .alias("listed_date"),
            (pl.col("talk_revision_id") + 1).alias("talk_revision_id"),
        )
        tables = store.prepare_current_quality(
            articles,
            _evidence(),
            _b_tables(),
            _activity(),
            observed_at=_NOW,
        )
        rows = tables["articles"].filter(
            pl.col("include_b") & pl.col("page_id").is_in([1, 2]),
        )
        assert rows["authorship_available"].to_list() == [False, False]
        assert rows["authors"].to_list() == [[], []]

    @staticmethod
    def test_successful_cutoff_metadata_survives_unchanged_membership() -> (
        None
    ):
        """Reuse dated oldids without repeating fallback lookups."""
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            original = _articles()
            quality_analysis.checkpoint(
                runtime,
                members=original,
                observed_at=_NOW,
            )
            fresh = original.with_columns(
                pl.lit(None, dtype=pl.Int64).alias("listed_oldid"),
                pl.lit(None, dtype=pl.String).alias("listed_review_link"),
                pl.lit(_NOW + dt.timedelta(hours=1)).alias("observed_at"),
            )
            saved = quality_analysis.checkpoint(
                runtime,
                members=fresh,
                observed_at=_NOW + dt.timedelta(hours=1),
            )
            assert (
                saved.sort("page_id")["listed_oldid"].to_list()
                == original["listed_oldid"].to_list()
            )
            assert (
                saved.sort("page_id")["listed_review_link"].to_list()
                == original["listed_review_link"].to_list()
            )
            revised = fresh.with_columns(
                (pl.col("listed_date") + pl.duration(days=1)).alias(
                    "listed_date",
                ),
            )
            changed = quality_analysis.checkpoint(
                runtime,
                members=revised,
                observed_at=_NOW + dt.timedelta(hours=1),
            )
            assert changed["listed_oldid"].null_count() == changed.height

    @staticmethod
    def test_new_character_evidence_never_relabels_saved_word_counts() -> None:
        """Keep old token counts as unknown weighted characters."""
        articles = _articles().with_columns(
            pl.lit(None, dtype=pl.Int64).alias("prose_characters"),
            pl.lit(80, dtype=pl.Int64).alias("prose_words"),
            pl.lit(300, dtype=pl.Int64).alias("prose_bytes"),
            pl.lit(_NOW).alias("measured_at"),
        )
        tables = store.prepare_current_quality(
            articles,
            _evidence(),
            _b_tables(),
            _activity(),
            observed_at=_NOW,
        )
        assert tables["coverage"]["measured"].to_list() == [0, 0]
        assert tables["coverage"]["median_characters"].null_count() == 2

    @staticmethod
    def test_a_list_reuses_source_a_and_keeps_current_al_credit() -> None:
        """Reuse the WAR source grade and retain list status."""
        articles = (
            _articles()
            .filter(pl.col("page_id") == 1)
            .with_columns(pl.lit("AL").alias("grade"))
        )
        evidence = (
            _evidence()
            .filter((pl.col("page_id") == 1) & (pl.col("grade") == "GA"))
            .with_columns(pl.lit("A").alias("grade"))
        )
        tables = store.prepare_current_quality(
            articles,
            evidence,
            _b_tables(),
            _activity(),
            observed_at=_NOW,
        )
        current = (
            tables["articles"].filter(~pl.col("include_b")).row(0, named=True)
        )
        assert current["grade"] == "AL"
        assert current["authors"] == ["Alice"]
        authors = (
            tables["authors"].filter(~pl.col("include_b")).row(0, named=True)
        )
        assert authors["grades"]["AL"] == 1
        assert authors["grades"]["A"] == 0

    @staticmethod
    def test_legacy_successful_evidence_survives_schema_migration() -> None:
        """Keep successful evidence while adding unknown fields."""
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            articles = (
                _articles()
                .filter(pl.col("grade") != "B")
                .with_columns(
                    pl.lit(80, dtype=pl.Int64).alias("prose_words"),
                    pl.lit(300, dtype=pl.Int64).alias("prose_bytes"),
                    pl.lit(_NOW).alias("measured_at"),
                )
            )
            tables = quality_analysis._summaries(articles)
            tables["articles"] = tables["articles"].drop(
                "prose_characters",
                "listed_review_link",
            )
            tables["years"] = tables["years"].drop("B", "BL")
            ReportStore(runtime / "data/hourly").write(
                quality_analysis._KEY,
                ReportResult(
                    family="quality-analysis",
                    tables=tables,
                    metadata={"observed_at": _NOW.isoformat()},
                ),
                "",
                run_id="a" * 32,
                started_at=_NOW,
                target={"site": "zhwiki", "title": "legacy analysis"},
            )
            migrated = quality_analysis.load_articles(runtime)
            assert migrated["prose_characters"].null_count() == migrated.height
            assert migrated["prose_words"].to_list() == [80] * migrated.height
            b = _b_tables()
            old_evidence = b["evidence"].drop(
                "creation_revision",
                "creation_timestamp",
            )
            b["evidence"] = old_evidence
            ReportStore(runtime / "data/hourly").write(
                current_authorship._KEY,
                ReportResult(family="current-authorship", tables=b),
                "",
                run_id="b" * 32,
                started_at=_NOW,
                target={"site": "zhwiki", "title": "legacy attribution"},
            )
            retained = current_authorship.load(runtime)
            assert (
                retained["evidence"]
                .drop("creation_revision", "creation_timestamp")
                .equals(old_evidence)
            )
            assert retained["evidence"]["creation_revision"].null_count() == 1
            assert current_authorship.pending(retained).is_empty()

    @staticmethod
    def test_saved_reader_never_joins_groups_or_writes() -> None:
        """Read both complete variants from one immutable generation."""
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            assert not store.read_current_quality(runtime)["available"]
            assert list(runtime.iterdir()) == []
            quality_analysis.checkpoint(
                runtime,
                members=_articles(),
                observed_at=_NOW,
            )
            authorship_store.save(runtime / AUTHORSHIP_PATH, _evidence())
            b = _b_tables()
            current_authorship.checkpoint(
                runtime,
                members=b["members"],
                evidence=b["evidence"],
            )
            quality_authors.save_activity(
                runtime / quality_authors.ACTIVITY_PATH,
                _activity(),
            )
            assert store.refresh_saved_quality(runtime)
            before = {
                str(path): path.stat().st_mtime_ns
                for path in runtime.rglob("*")
                if path.is_file()
            }
            with (
                patch.object(pl.DataFrame, "join", side_effect=AssertionError),
                patch.object(
                    pl.DataFrame,
                    "group_by",
                    side_effect=AssertionError,
                ),
            ):
                default = store.read_current_quality(runtime)
                expanded = store.read_current_quality(runtime, include_b=True)
            assert default["analysis"]["coverage"]["total"] == 3
            assert expanded["analysis"]["coverage"]["total"] == 4
            assert expanded["authorship"]["b_coverage"] == {
                "total": 1,
                "collected": 1,
            }
            assert (
                default["observed_at"]
                == expanded["observed_at"]
                == _NOW.isoformat()
            )
            saved = ReportStore(runtime / "data/hourly").read(
                store._KEY,
                locked=True,
            )
            assert saved is not None
            prepared = saved.manifest["generated_at"]
            assert (
                default["prepared_at"] == expanded["prepared_at"] == prepared
            )
            assert default["analysis"]["prepared_at"] == prepared
            assert expanded["authorship"]["prepared_at"] == prepared
            assert {
                str(path): path.stat().st_mtime_ns
                for path in runtime.rglob("*")
                if path.is_file()
            } == before
