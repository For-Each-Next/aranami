"""Verify quality-author evidence and offline activity boundaries."""

# Keep explicit source identities and expected scalar observations.
# ruff: file-ignore[pytest-unittest-raises-assertion, private-member-access]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from sqlalchemy import Table
from sqlalchemy.dialects import mysql
from sqlalchemy.sql.visitors import iterate

from data.articles import (
    authorship_store,
    current_assessments,
    quality_authors as store,
)
from shared.sources.quarry import users
from tasks.articles import quality_authors as collector

_NOW = dt.datetime(2026, 11, 10, 8, tzinfo=dt.UTC)
_EDIT = dt.datetime(2026, 11, 9, 18, tzinfo=dt.UTC)


def _assessments(grade: str | None = "典范") -> pl.DataFrame:
    """Prepare current project state for the listing fixture.

    Returns:
        Complete typed membership with its source-observation time.
    """
    return pl.DataFrame(
        [
            {
                "page_id": 1,
                "pa_class": grade,
                "page_namespace": 0,
                "page_is_redirect": 0,
                "observed_at": _NOW,
            },
        ],
        schema=current_assessments.SCHEMA,
    )


def _activity(
    *,
    name: str = "Yumeto",
    last_active: dt.datetime | None = _EDIT,
    observed_at: dt.datetime = _NOW,
) -> pl.DataFrame:
    """Prepare one explicit activity observation.

    Returns:
        Typed rows with independent edit and capture times.
    """
    return pl.DataFrame(
        [
            {
                "name": name,
                "last_active": last_active,
                "observed_at": observed_at,
            },
        ],
        schema=store.ACTIVITY_SCHEMA,
    )


def _quality_frames(
    shares: list[tuple[str, str, int]],
    *,
    grade: str = "FA",
    day: dt.date = dt.date(2022, 12, 1),
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Prepare one listing with explicit contributor token observations.

    Returns:
        Inventory and token-evidence frames for pure view preparation.
    """
    key = {
        "page_id": 1,
        "date": day,
        "grade": grade,
        "action": "added",
    }
    event = pl.DataFrame([
        {
            **key,
            "title": "亞絲娜",
            "kind": "quality",
            "updated_at": _NOW,
        },
    ])
    total = sum(tokens for _, _, tokens in shares)
    contributors = [
        {
            "editor": editor,
            "name": name,
            "tokens": tokens,
            "bytes": tokens,
            "share": tokens / total,
        }
        for editor, name, tokens in shares
    ]
    evidence = pl.DataFrame(
        [
            {
                **key,
                "contract": authorship_store.CONTRACT,
                "contributors": contributors,
                "eligible_bytes": total,
                "fetched_at": _NOW,
            },
        ],
        schema={
            "page_id": pl.Int64,
            "date": pl.Date,
            "grade": pl.String,
            "action": pl.String,
            "contract": pl.String,
            "contributors": pl.List(
                pl.Struct(authorship_store.CONTRIBUTOR_SCHEMA),
            ),
            "eligible_bytes": pl.UInt32,
            "fetched_at": pl.Datetime("us", "UTC"),
        },
    )
    return event, evidence


class QualityAuthorPreparationTests(TestCase):
    """Count each qualified editor once per retained article."""

    @staticmethod
    def test_largest_contributor_alone_receives_credit() -> None:
        """Keep unequal shares from implying coauthorship."""
        events, evidence = _quality_frames([
            ("1", "Yumeto", 72),
            ("2", "Newbamboo", 28),
        ])
        articles = store.prepare_quality_articles(events, evidence)
        assert articles["name"].to_list() == ["Yumeto"]
        assert articles["credit"].to_list() == [1.0]
        assert articles["coauthor_count"].to_list() == [1]

    @staticmethod
    def test_byte_scores_choose_author_independently_of_token_counts() -> None:
        """Use UTF-8 byte contribution instead of raw token totals."""
        events, evidence = _quality_frames([
            ("1", "Yumeto", 72),
            ("2", "Newbamboo", 28),
        ])
        evidence = evidence.with_columns(
            pl.col("contributors").list.eval(
                pl.element().struct.with_fields(
                    (100 - pl.element().struct.field("bytes")).alias("bytes"),
                ),
            ),
        )
        articles = store.prepare_quality_articles(events, evidence)
        assert articles["name"].to_list() == ["Newbamboo"]
        assert articles["credit"].to_list() == [1.0]

    @staticmethod
    def test_exact_largest_ties_each_receive_one_article() -> None:
        """Count the same article once for each qualified editor."""
        events, evidence = _quality_frames([
            ("1", "Yumeto", 50),
            ("2", "Newbamboo", 50),
        ])
        articles = store.prepare_quality_articles(events, evidence)
        assert set(articles["name"]) == {"Yumeto", "Newbamboo"}
        assert articles["credit"].to_list() == [1, 1]
        assert articles["coauthor_count"].to_list() == [2, 2]

    @staticmethod
    def test_unequal_qualified_shares_both_receive_one_article() -> None:
        """Include a qualifying runner-up without an exact tie."""
        events, evidence = _quality_frames([
            ("1", "Yumeto", 60),
            ("2", "Newbamboo", 40),
        ])
        articles = store.prepare_quality_articles(events, evidence)
        assert set(articles["name"]) == {"Yumeto", "Newbamboo"}
        assert articles["credit"].to_list() == [1, 1]

    @staticmethod
    def test_exact_thirds_and_subthreshold_primary_authors_qualify() -> None:
        """Count primary authors and every qualifying runner-up."""
        events, evidence = _quality_frames([
            ("1", "First", 1),
            ("2", "Second", 1),
            ("3", "Third", 1),
        ])
        articles = store.prepare_quality_articles(events, evidence)
        assert set(articles["name"]) == {"First", "Second", "Third"}
        assert articles["credit"].to_list() == [1, 1, 1]
        events, evidence = _quality_frames([
            ("1", "First", 30),
            ("2", "Second", 25),
            ("3", "Third", 25),
            ("4", "Fourth", 20),
        ])
        assert store.prepare_quality_articles(events, evidence)[
            "name"
        ].to_list() == ["First"]

    @staticmethod
    def test_exact_33_percent_runner_up_qualifies() -> None:
        """Include exactly 33% and exclude smaller nonleading shares."""
        events, evidence = _quality_frames([
            ("1", "First", 34),
            ("2", "Second", 33),
            ("3", "Third", 32),
            ("4", "Fourth", 1),
        ])
        articles = store.prepare_quality_articles(events, evidence)
        assert set(articles["name"]) == {"First", "Second"}
        assert articles["coauthor_count"].to_list() == [2, 2]

    @staticmethod
    def test_large_byte_totals_preserve_33_percent_boundary() -> None:
        """Compare large eligible totals without integer overflow."""
        events, evidence = _quality_frames([
            ("1", "First", 2_640_000_000),
            ("2", "Second", 1_320_000_000),
            ("3", "Third", 40_000_000),
        ])
        articles = store.prepare_quality_articles(events, evidence)
        assert set(articles["name"]) == {"First", "Second"}

    @staticmethod
    def test_unowned_bytes_stay_in_the_eligibility_denominator() -> None:
        """Include unowned whitespace in the denominator."""
        events, evidence = _quality_frames([
            ("1", "First", 50),
            ("2", "Second", 50),
        ])
        evidence = evidence.with_columns(
            pl.lit(150, dtype=pl.UInt32).alias("eligible_bytes"),
        )
        articles = store.prepare_quality_articles(events, evidence)
        assert set(articles["name"]) == {"First", "Second"}
        assert articles["coauthors"].to_list() == [
            ["First", "Second"],
            ["First", "Second"],
        ]
        evidence = evidence.with_columns(
            pl.lit(151, dtype=pl.UInt32).alias("eligible_bytes"),
        )
        assert set(
            store.prepare_quality_articles(events, evidence)["name"],
        ) == {"First", "Second"}

    @staticmethod
    def test_anonymous_bytes_do_not_inflate_registered_authors() -> None:
        """Omit anonymous authors without reassigning their tokens."""
        events, evidence = _quality_frames([
            ("0|192.0.2.1", "192.0.2.1", 72),
            ("2", "Newbamboo", 28),
        ])
        assert store.prepare_quality_articles(events, evidence).is_empty()
        events, evidence = _quality_frames([
            ("0|192.0.2.1", "192.0.2.1", 50),
            ("2", "Newbamboo", 50),
        ])
        articles = store.prepare_quality_articles(events, evidence)
        assert articles["name"].to_list() == ["Newbamboo"]
        assert articles["credit"].to_list() == [1]
        events, evidence = _quality_frames([
            ("0|192.0.2.1", "192.0.2.1", 60),
            ("2", "Newbamboo", 40),
        ])
        articles = store.prepare_quality_articles(events, evidence)
        assert articles["name"].to_list() == ["Newbamboo"]
        assert articles["coauthors"].to_list() == [["Newbamboo"]]

    @staticmethod
    def test_strongest_grade_and_latest_same_grade_do_not_double_count() -> (
        None
    ):
        """Deduplicate promotions and repeated listings."""
        frames = [
            _quality_frames(
                [("1", "Yumeto", 1)],
                grade=grade,
                day=day,
            )
            for grade, day in (
                ("GA", dt.date(2023, 1, 1)),
                ("FA", dt.date(2024, 1, 1)),
                ("FA", dt.date(2025, 1, 1)),
            )
        ]
        events = pl.concat([frame[0] for frame in frames])
        evidence = pl.concat([frame[1] for frame in frames])
        articles = store.prepare_quality_articles(events, evidence)
        assert articles.height == 1
        assert articles["grade"].item() == "FA"
        assert articles["date"].item() == dt.date(2025, 1, 1)

    @staticmethod
    def test_article_order_uses_listing_date_before_grade() -> None:
        """Order by listing dates and retain recorded review links."""
        older, older_evidence = _quality_frames([("1", "Yumeto", 1)])
        newer, newer_evidence = _quality_frames(
            [("1", "Yumeto", 1)],
            grade="GA",
            day=dt.date(2026, 1, 1),
        )
        newer = newer.with_columns(
            pl.lit(2, dtype=pl.Int64).alias("page_id"),
            pl.lit("Newer game").alias("title"),
        )
        newer_evidence = newer_evidence.with_columns(
            pl.lit(2, dtype=pl.Int64).alias("page_id"),
        )
        events = pl.concat([older, newer]).with_columns(
            pl.lit("#優良條目評選").alias("nomination_link"),
        )
        articles = store.prepare_quality_articles(
            events,
            pl.concat([older_evidence, newer_evidence]),
        )
        assert articles["grade"].to_list() == ["GA", "FA"]
        assert articles["nomination_link"].to_list() == [
            "#優良條目評選",
            "#優良條目評選",
        ]

    @staticmethod
    def test_only_matching_added_quality_listings_establish_experience() -> (
        None
    ):
        """Exclude unrelated events, removals and unsupported grades."""
        events, evidence = _quality_frames([("1", "Yumeto", 1)])
        variants = (
            events.with_columns(pl.lit("removed").alias("action")),
            events.with_columns(pl.lit("dyk").alias("kind")),
            events.with_columns(pl.lit("B").alias("grade")),
            events.with_columns(pl.lit(2).cast(pl.Int64).alias("page_id")),
        )
        for selected in variants:
            assert store.prepare_quality_articles(
                selected,
                evidence,
            ).is_empty()


class CurrentQualityAuthorPreparationTests(TestCase):
    """Apply current eligibility without rewriting historical credit."""

    @staticmethod
    def test_delisted_zywoo_is_removed_despite_retained_ga_authorship() -> (
        None
    ):
        """Exclude historical credit after a project downgrade."""
        events, evidence = _quality_frames([("1", "Writer", 1)], grade="GA")
        events = events.with_columns(pl.lit("ZywOo").alias("title"))
        assert store.prepare_quality_articles(events, evidence).height == 1
        assert store.prepare_current_quality_articles(
            events,
            evidence,
            _assessments("乙"),
        ).is_empty()

    @staticmethod
    def test_all_six_current_quality_grades_and_aliases_are_included() -> None:
        """Accept all six quality classes and their audited aliases."""
        for grade, source in (
            ("FA", "典范"),
            ("FL", "特色列表"),
            ("A", "甲"),
            ("AL", "甲级列表"),
            ("GA", "优良"),
            ("Bplus", "乙上"),
            ("FA", "FA"),
            ("AL", "甲級列表"),
            ("GA", "優良級"),
        ):
            events, evidence = _quality_frames(
                [("1", "Writer", 1)],
                grade=grade,
            )
            articles = store.prepare_current_quality_articles(
                events,
                evidence,
                _assessments(source),
            )
            assert articles["grade"].to_list() == [grade]
            assert articles["credit"].to_list() == [1]

    @staticmethod
    def test_invalid_current_grades_and_nonarticles_are_excluded() -> None:
        """Require current quality and current article identity."""
        events, evidence = _quality_frames([("1", "Writer", 1)])
        invalid = [
            *(
                _assessments(grade)
                for grade in ("乙", "丙", "Unassessed", None)
            ),
            _assessments().head(0),
            _assessments().with_columns(
                pl.lit(2).cast(pl.Int64).alias("page_id"),
            ),
            _assessments().with_columns(
                pl.lit(1).cast(pl.Int64).alias("page_is_redirect"),
            ),
            _assessments().with_columns(
                pl.lit(2).cast(pl.Int64).alias("page_namespace"),
            ),
        ]
        for current in invalid:
            assert store.prepare_current_quality_articles(
                events,
                evidence,
                current,
            ).is_empty()

    @staticmethod
    def test_current_grade_only_gates_historical_listing_attribution() -> None:
        """Preserve listing grades, dates and byte credits."""
        events, evidence = _quality_frames([
            ("1", "Primary", 34),
            ("2", "Coauthor", 33),
            ("3", "Other", 33),
        ])
        historical = store.prepare_quality_articles(events, evidence)
        current = store.prepare_current_quality_articles(
            events,
            evidence,
            _assessments("乙上"),
        )
        assert current.equals(historical)
        assert current["grade"].to_list() == ["FA", "FA", "FA"]


class QualityActivityStorageTests(TestCase):
    """Distinguish known, unknown and failed activity observations."""

    def setUp(self) -> None:
        """Allocate isolated runtime storage."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "activity.parquet"

    def test_first_use_and_unknown(self) -> None:
        """Keep missing storage empty and unknown times nullable."""
        assert store.load_activity(self.path).schema == pl.Schema(
            store.ACTIVITY_SCHEMA,
        )
        assert store.load_activity(self.path).is_empty()
        unknown = _activity(last_active=None)
        store.save_activity(self.path, unknown)
        assert store.load_activity(self.path).equals(unknown)

    def test_stale_observation_cannot_replace_newer(self) -> None:
        """Ignore a delayed collector's older observation."""
        current = _activity()
        store.save_activity(self.path, current)
        store.save_activity(
            self.path,
            _activity(
                last_active=None,
                observed_at=_NOW - dt.timedelta(hours=1),
            ),
        )
        assert store.load_activity(self.path).equals(current)

    def test_bad_observation_preserves_existing_file(self) -> None:
        """Reject duplicate identities and impossible edit times."""
        store.save_activity(self.path, _activity())
        previous = self.path.read_bytes()
        for invalid in (
            pl.concat([_activity(), _activity()]),
            _activity(last_active=_NOW + dt.timedelta(seconds=1)),
            _activity(name="<unsafe>"),
        ):
            with self.assertRaises(ValueError):
                store.save_activity(self.path, invalid)
            assert self.path.read_bytes() == previous

    def test_failed_write_preserves_previous(self) -> None:
        """Leave the selected file intact when serialization fails."""
        store.save_activity(self.path, _activity())
        previous = self.path.read_bytes()
        with (
            patch.object(pl.DataFrame, "write_parquet", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            store.save_activity(self.path, _activity(name="Newbamboo"))
        assert self.path.read_bytes() == previous
        assert not list(self.path.parent.glob("tmp*"))

    def test_reject_unsafe_and_corrupt_storage(self) -> None:
        """Reject symlinks and malformed saved evidence."""
        actual = self.path.with_name("actual.parquet")
        store.save_activity(actual, _activity())
        self.path.symlink_to(actual)
        with self.assertRaises(OSError):
            store.load_activity(self.path)
        self.path.unlink()
        self.path.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            store.load_activity(self.path)


class QualityActivitySourceTests(TestCase):
    """Query indexed public metadata without touching live sources."""

    @staticmethod
    def test_source_query_uses_registered_actors_and_latest_revision() -> None:
        """Read edit times while excluding concealed identities."""
        source = pl.DataFrame({
            "name": ["Yumeto", "Newbamboo"],
            "last_active": ["20261109180000", None],
        })
        replica = Mock()
        replica.query.return_value.collect.return_value = source
        site = Mock()
        with patch.object(users.Replica, "from_site", return_value=replica):
            observed = users.fetch_user_activity(site, ["Yumeto", "Newbamboo"])
        assert observed.schema == pl.Schema(users.SCHEMA)
        assert observed["last_active"].to_list() == [_EDIT, None]
        statement = replica.query.call_args.args[0]
        sql = str(
            statement.compile(
                dialect=mysql.dialect(),
                compile_kwargs={"literal_binds": True},
            ),
        )
        assert "actor.actor_name IN" in sql
        assert "actor.actor_user IS NOT NULL" in sql
        assert "FROM revision_userindex" in sql
        assert "FROM revision \n" not in sql
        assert "revision_userindex.rev_actor = actor.actor_id" in sql
        assert "revision_userindex.rev_deleted & 4 = 0" in sql
        assert (
            "ORDER BY revision_userindex.rev_timestamp DESC, "
            "revision_userindex.rev_id DESC" in sql
        )
        assert "LIMIT 1" in sql
        assert {
            node.name for node in iterate(statement) if isinstance(node, Table)
        } == {"actor", "revision_userindex"}

    @staticmethod
    def test_empty_names_do_not_connect() -> None:
        """Keep an empty inference set inert."""
        with patch.object(users.Replica, "from_site") as connect:
            assert users.fetch_user_activity(Mock(), []).is_empty()
        connect.assert_not_called()

    @staticmethod
    def test_collection_retains_unknown_activity_for_missing_accounts() -> (
        None
    ):
        """Record activity independently from listing dates."""
        names = pl.DataFrame({"name": ["Yumeto", "Newbamboo"]})
        observed = pl.DataFrame(
            [{"name": "Yumeto", "last_active": _EDIT}],
            schema=users.SCHEMA,
        )
        with (
            patch.object(collector.authorship_store, "load"),
            patch.object(collector.milestone_store, "load"),
            patch.object(
                current_assessments,
                "load",
                return_value=_assessments(),
            ),
            patch.object(
                store,
                "prepare_current_quality_articles",
                return_value=names,
            ),
            patch.object(
                collector,
                "fetch_user_activity",
                return_value=observed,
            ),
            patch.object(store, "save_activity") as save,
        ):
            collector.refresh_quality_author_activity(Mock())
        result = save.call_args.args[1].sort("name")
        assert result["last_active"].to_list() == [None, _EDIT]
        assert result["observed_at"].null_count() == 0

    @staticmethod
    def test_source_failure_preserves_previous_saved_activity() -> None:
        """Preserve saved activity after a replica failure."""
        with (
            patch.object(collector.authorship_store, "load"),
            patch.object(collector.milestone_store, "load"),
            patch.object(
                current_assessments,
                "load",
                return_value=_assessments(),
            ),
            patch.object(
                store,
                "prepare_current_quality_articles",
                return_value=pl.DataFrame({"name": ["Yumeto"]}),
            ),
            patch.object(
                collector,
                "fetch_user_activity",
                side_effect=OSError,
            ),
            patch.object(store, "save_activity") as save,
            patch.object(collector._LOGGER, "warning") as warning,
        ):
            collector.refresh_quality_author_activity(Mock())
        save.assert_not_called()
        warning.assert_called_once()
