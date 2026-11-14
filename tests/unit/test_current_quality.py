"""Verify current quality cohorts and saved attribution ranges."""

# Protocol identities and counts are deliberate fixture literals.
# ruff: file-ignore[magic-value-comparison,private-member-access]

from __future__ import annotations

import datetime as dt
import json
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
from webservice.pages import render_quality_analysis
from webservice.quality_analysis import main_author_directory

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


def _selected_tables(
    payload: dict,
    *,
    all_grades: bool = False,
) -> dict[str, pl.DataFrame]:
    """Decode one prepared default for focused projection assertions.

    Returns:
        Tables selected from the shared catalog payload.
    """
    return {
        name: pl.DataFrame(rows)
        for name, rows in payload["defaults"][int(all_grades)].items()
    }


class CurrentQualityTests(TestCase):
    """Keep every section bound to the same selected current pages."""

    @staticmethod
    def test_ten_percent_evidence_survives_saved_projection_and_html() -> None:
        """Retain the inclusive dialog threshold through storage."""
        evidence = _evidence().with_columns(
            pl
            .when((pl.col("page_id") == 1) & (pl.col("grade") == "GA"))
            .then(
                pl.lit(
                    _contributors((
                        ("3", "Boundary", 10),
                        ("1", "Alice", 50),
                        ("4", "Minor", 5),
                        ("2", "Bob", 35),
                    )),
                    dtype=_evidence().schema["contributors"],
                ),
            )
            .otherwise(pl.col("contributors"))
            .alias("contributors"),
        )
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            quality_analysis.checkpoint(
                runtime,
                members=_articles(),
                observed_at=_NOW,
            )
            authorship_store.save(runtime / AUTHORSHIP_PATH, evidence)
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
            saved = store.read_current_quality(runtime)
            selection = saved["analysis"]["selection"]
            article = next(
                row for row in selection["articles"] if row["page_id"] == 1
            )
            assert [
                editor["name"] for editor in article["authorship_editors"]
            ] == ["Alice", "Bob", "Boundary"]
            assert [
                editor["bytes"] for editor in article["authorship_editors"]
            ] == [50, 35, 10]
            assert set(article) == set(store.ARTICLE_SCHEMA)
            authors = main_author_directory(
                selection["articles"],
                selection["authors"],
            )
            bob = next(author for author in authors if author["name"] == "Bob")
            assert [item["page_id"] for item in bob["articles"]] == [1]
            assert bob["credit"] == 1
            output = render_quality_analysis(
                saved,
                None,
                None,
                "en",
                _NOW,
                resources={},
            )
            popup = output.split('id="quality-article-1-authorship"', 1)[1]
            popup = popup.split("</dialog>", 1)[0]
            assert ">Boundary</a>" in popup
            assert ">10.0%</td>" in popup
            assert ">Minor</a>" not in popup

    @staticmethod
    def test_default_and_all_grade_presets_are_precomputed() -> None:
        """Prepare C and unknown dates in the complete-grade preset."""
        extra = (
            _articles()
            .head(1)
            .with_columns(
                pl.lit(5, dtype=pl.Int64).alias("page_id"),
                pl.lit("C").alias("grade"),
                pl.lit(None, dtype=pl.Date).alias("listed_date"),
            )
        )
        tables = store.prepare_current_quality(
            pl.concat([_articles(), extra]),
            _evidence(),
            _b_tables(),
            _activity(),
            observed_at=_NOW,
        )
        defaults = tables["defaults"]
        assert [row["coverage"][0]["total"] for row in defaults] == [3, 5]
        assert {row["grade"] for row in defaults[1]["articles"]} == {
            "FA",
            "FL",
            "GA",
            "B",
            "C",
        }
        assert defaults[1]["coverage"][0]["dated"] == 4
        store._validate(tables)
        assert all(
            set(article) == set(store.ARTICLE_SCHEMA)
            for article in tables["articles"]
        )

    @staticmethod
    def test_density_preserves_zero_unknown_and_constant_samples() -> None:
        """Preserve real distributions and unknown lengths."""
        frame = pl.DataFrame({
            "grade": ["FA", "FA", "FA", "GA", "AL"],
            "prose_characters": [0, 100, None, 10, None],
        })
        rows = store._length_density(frame, "grade")
        assert rows["grade"].to_list() == ["FA", "GA"]
        varied, constant = rows["density"].to_list()
        assert varied[0]["position"] == 0
        assert varied[-1]["position"] == 100
        assert all(point["position"] == 10 for point in constant)
        for points in (varied, constant):
            assert max(point["density"] for point in points) == 1
            assert all(0 <= point["density"] <= 1 for point in points)
        assert store._length_density(
            frame.filter(pl.col("grade") == "AL"),
            "grade",
        ).is_empty()

    @staticmethod
    def test_presets_share_catalog_and_use_current_grades() -> None:
        """Count selected pages and reject stronger former badges."""
        tables = store.prepare_current_quality(
            _articles(),
            _evidence(),
            _b_tables(),
            _activity(),
            observed_at=_NOW,
        )
        store._validate(tables)
        for all_grades, expected_total, alice_credit in (
            (False, 3, 2),
            (True, 4, 3),
        ):
            selected = _selected_tables(tables, all_grades=all_grades)
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
                .filter(
                    pl.col("name") == "Bob",
                )
                .is_empty()
            )
            game = (
                selected["articles"]
                .filter(pl.col("page_id") == 1)
                .row(0, named=True)
            )
            assert game["grade"] == "GA"
            assert game["authorship_editors"][0]["name"] == "Alice"
            assert [
                editor["name"] for editor in game["authorship_editors"]
            ] == ["Alice", "Bob", None]
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
        selected = _selected_tables(tables)
        years = selected["years"]
        assert years["year"].to_list() == list(range(2009, 2027))
        assert years.filter(pl.col("year").is_in([2010, 2011]))[
            "pages"
        ].to_list() == [0, 0]
        coverage = selected["coverage"].row(0, named=True)
        assert coverage["measured"] == 2
        assert coverage["median_characters"] == 50
        lengths = selected["lengths"].filter(pl.col("group") == "grade")
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
        rows = _selected_tables(tables, all_grades=True)["articles"].filter(
            pl.col("page_id").is_in([1, 2]),
        )
        assert rows["authorship_available"].to_list() == [False, False]
        assert rows["authorship_editors"].to_list() == [[], []]

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
        for all_grades in (False, True):
            coverage = _selected_tables(tables, all_grades=all_grades)[
                "coverage"
            ]
            assert coverage["measured"].item() == 0
            assert coverage["median_characters"].null_count() == 1

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
        selected = _selected_tables(tables)
        current = selected["articles"].row(0, named=True)
        assert current["grade"] == "AL"
        assert current["authorship_editors"][0]["name"] == "Alice"
        authors = selected["authors"].row(0, named=True)
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
            ReportStore(runtime / "data/parsed").write(
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
            ReportStore(runtime / "data/parsed").write(
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
        """Read both presets from one immutable universal catalog."""
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
                expanded = store.read_current_quality(runtime, all_grades=True)
            assert default["analysis"]["coverage"]["total"] == 3
            assert expanded["analysis"]["coverage"]["total"] == 4
            assert set(expanded["authorship"]) == {
                "available",
                "observed_at",
                "prepared_at",
                "authors",
                "all_grades",
            }
            assert (
                default["observed_at"]
                == expanded["observed_at"]
                == _NOW.isoformat()
            )
            saved = json.loads((runtime / store._PATH).read_text())
            assert saved["schema"] == 1
            assert set(saved["selection"]) == {
                "articles",
                "current_articles",
                "authors",
                "current_authors",
                "cells",
                "defaults",
                "last_year",
            }
            assert not list(
                (runtime / "data/parsed").glob("current-quality-*parquet"),
            )
            prepared = saved["prepared_at"]
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
