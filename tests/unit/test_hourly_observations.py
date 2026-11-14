"""Verify centralized hourly membership and revisions."""

# Inspect source boundaries and exact fixture identities offline.
# ruff: file-ignore[magic-value-comparison]

import datetime as dt
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.articles import current_assessments
from data.caching import project_observations as cache
from runtime.project_observations import (
    collect_project_membership,
    project_cycle,
)
from shared.sources.cycle import read_membership, source_cycle
from shared.sources.quarry import projects
from shared.support.activity import activity_observer

_NOW = dt.datetime(2026, 11, 10, 15, 57, 54, tzinfo=dt.UTC)


def _members() -> pl.DataFrame:
    """Return complete source-native project and revision facts.

    Returns:
        One subject and its corresponding talk-page revision.
    """
    return pl.DataFrame(
        {
            "page_id": [1],
            "page_namespace": [0],
            "page_title": ["Game"],
            "page_revision": [101],
            "page_length": [100],
            "page_is_redirect": [0],
            "talk_page_id": [2],
            "talk_revision": [201],
            "pa_class": ["B"],
            "pa_importance": ["Low"],
        },
        schema=projects._PAGE_SCHEMA,  # ruff: ignore[private-member-access]
    )


def _categories() -> pl.DataFrame:
    """Return shared category links with a known uncategorized page.

    Returns:
        Both subject and talk identities with exact replica revisions.
    """
    return pl.DataFrame(
        [
            (1, 0, "Game", 101, None, "Games", True),
            (2, 1, "Game", 201, 1, "Grade_category", True),
            (3, 0, "Empty", 301, None, None, True),
        ],
        schema=cache.CATEGORY_SCHEMA,
        orient="row",
    )


class HourlyObservationTests(TestCase):
    """Reuse one completed hour without shifting the next boundary."""

    @staticmethod
    def test_midnight_scope_preserves_original_capture_time() -> None:
        """Do not relabel reused midnight rows with the restart time."""
        site = OfflineSite("zh", "wikipedia")
        captured = _NOW.replace(hour=0, minute=0, second=4)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "runtime.project_observations.query_pages_by_wikiproject",
                return_value=_members(),
            ),
            patch(
                "runtime.project_observations.project_data.read_snapshot",
                return_value=SimpleNamespace(
                    manifest={"calculated_at": captured.isoformat()},
                ),
            ),
        ):
            collect_project_membership(
                site,
                "电子游戏",
                now=_NOW.replace(hour=0),
            )
            assert (
                current_assessments.load(
                    current_assessments.DEFAULT_PATH,
                )["observed_at"].item()
                == captured
            )

    @staticmethod
    def test_current_quality_scope_rebuilds_from_fresh_membership() -> None:
        """Refresh missing durable eligibility despite a warm cache."""
        site = OfflineSite("zh", "wikipedia")
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "runtime.project_observations.query_pages_by_wikiproject",
                return_value=_members(),
            ) as fetch,
        ):
            saved, path = cache.cache("zhwiki", "电子游戏", _NOW)
            saved.write(
                path,
                cache.encode_members(
                    _members().with_columns(pl.lit("FA").alias("pa_class")),
                    "zhwiki",
                    "电子游戏",
                ),
            )
            collect_project_membership(site, "电子游戏", now=_NOW)
            fetch.assert_called_once()
            assert current_assessments.load(
                current_assessments.DEFAULT_PATH,
            )["pa_class"].to_list() == ["B"]
            collect_project_membership(site, "电子游戏", now=_NOW)
            fetch.assert_called_once()

    @staticmethod
    def test_current_quality_scope_updates_after_a_downgrade() -> None:
        """Retain a lower grade at the next successful source query."""
        site = OfflineSite("zh", "wikipedia")
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "runtime.project_observations.query_pages_by_wikiproject",
                side_effect=[
                    _members().with_columns(pl.lit("FA").alias("pa_class")),
                    _members(),
                ],
            ),
        ):
            collect_project_membership(site, "电子游戏", now=_NOW)
            assert (
                current_assessments.load(
                    current_assessments.DEFAULT_PATH,
                )["pa_class"].item()
                == "FA"
            )
            collect_project_membership(
                site,
                "电子游戏",
                now=_NOW + dt.timedelta(hours=1),
            )
            assert (
                current_assessments.load(
                    current_assessments.DEFAULT_PATH,
                )["pa_class"].item()
                == "B"
            )

    @staticmethod
    def test_preview_cannot_replace_current_quality_scope() -> None:
        """Keep preview membership out of current assessments."""
        site = OfflineSite("zh", "wikipedia")
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "runtime.project_observations.query_pages_by_wikiproject",
                return_value=_members(),
            ),
        ):
            collect_project_membership(site, "电子游戏", now=_NOW, dry=True)
            assert not current_assessments.DEFAULT_PATH.exists()

    @staticmethod
    def test_hour_paths_use_utc_seconds_and_preserve_retention_boundary() -> (
        None
    ):
        """Normalize offsets and preserve the retention boundary."""
        with TemporaryDirectory() as directory, chdir(directory):
            shifted = _NOW.astimezone(dt.timezone(dt.timedelta(hours=8)))
            store, path = cache.cache("zhwiki", "电子游戏", shifted)
            expected_store, expected = cache.cache("zhwiki", "电子游戏", _NOW)
            assert path == expected
            assert path.as_posix().isascii()
            assert store.contract == expected_store.contract
            assert path.name == "2026111014.parquet"
            _, preview = cache.cache("zhwiki", "电子游戏", _NOW, dry=True)
            _, other = cache.cache("zhwiki", "电子游戏/手机游戏", _NOW)
            assert preview.as_posix().isascii()
            assert preview.relative_to("dry-run") == path
            assert other != path
            for age in (0, 24, 25):
                old_store, old_path = cache.cache(
                    "zhwiki",
                    "电子游戏",
                    _NOW - dt.timedelta(hours=age),
                )
                old_store.write(
                    old_path,
                    cache.encode_members(_members(), "zhwiki", "电子游戏"),
                )
            cache.prune(_NOW)
            assert len(list(Path("data/hourly").rglob("*.parquet"))) == 2

    @staticmethod
    def test_independent_consumers_share_one_hourly_query() -> None:
        """Reuse saved Parquet until the next UTC hour."""
        site = OfflineSite("zh", "wikipedia")
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "runtime.project_observations.query_pages_by_wikiproject",
                return_value=_members(),
            ) as fetch,
        ):
            collect_project_membership(site, "Games", now=_NOW)
            for _ in range(2):
                with project_cycle(now=_NOW):
                    frame = read_membership(
                        "zhwiki",
                        "Games",
                        Mock(),
                        site=site,
                    )
                    assert frame.select(_members().columns).equals(_members())
                    with patch.object(projects, "_replica") as replica:
                        revisions = projects.latest_revisions(site, [1, 2])
                    replica.assert_not_called()
                    assert revisions.sort("page_id")[
                        "page_latest"
                    ].to_list() == [101, 201]
            assert fetch.call_count == 1
            collect_project_membership(
                site,
                "Games",
                now=_NOW.replace(hour=16, minute=0, second=0),
            )
            assert fetch.call_count == 2
            first, path = cache.cache("zhwiki", "Games", _NOW)
            assert first.read(path).equals(
                cache.encode_members(_members(), "zhwiki", "Games"),
            )

    @staticmethod
    def test_category_and_revision_consumers_need_no_more_database_reads() -> (
        None
    ):
        """Reuse categories, empty pages and replica revisions."""
        site = OfflineSite("zh", "wikipedia")
        with (
            source_cycle(
                category_reader=lambda _wiki: (
                    _categories(),
                    frozenset({"Grade_category", "Empty_category"}),
                ),
            ),
            patch.object(projects, "_replica") as replica,
        ):
            grade = projects.category_members(
                site,
                "Grade category",
                namespace=1,
            )
            assert grade["article_page_id"].to_list() == [1]
            assert projects.category_members(
                site,
                "Empty category",
                namespace=1,
            ).is_empty()
            assert projects.page_categories(site, [3]).is_empty()
            assert projects.latest_revisions(site, [2])[
                "page_latest"
            ].to_list() == [201]
        replica.assert_not_called()

    @staticmethod
    def test_all_category_observations_use_one_select_statement() -> None:
        """Read global lists and project tags in one SQL call."""
        site = OfflineSite("zh", "wikipedia")
        with patch.object(projects, "_replica") as replica:
            replica.return_value.query.return_value.collect.return_value = (
                _categories()
            )
            projects.category_observations(
                site,
                ["First", "Second"],
                [1, 2, 3],
            )
        query = replica.return_value.query
        query.assert_called_once()
        statement = query.call_args.args[0]
        assert "UNION ALL" in str(statement)
        assert statement.is_select
        assert query.call_args.kwargs["scope"]["categories"] == [
            "First",
            "Second",
        ]

    @staticmethod
    def test_local_parquet_reads_and_writes_are_observed_after_success() -> (
        None
    ):
        """Record successful local writes and reads."""
        observed = []
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            activity_observer(
                lambda kind, source: observed.append((kind, source)),
            ),
        ):
            store, path = cache.cache("zhwiki", "Games", _NOW)
            store.read(path)
            assert not observed
            store.write(
                path,
                cache.encode_members(_members(), "zhwiki", "Games"),
            )
            store.read(path)
        assert [kind for kind, _ in observed] == [
            "source_written",
            "source_loaded",
        ]
        assert all(
            source["metadata"]["file"].endswith("2026111014.parquet")
            for _, source in observed
        )
        assert all(
            not Path(source["metadata"]["file"]).is_absolute()
            for _, source in observed
        )
