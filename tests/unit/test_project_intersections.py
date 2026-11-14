"""Verify project intersections and annual revision queries."""

import datetime as dt
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
)
from wiki_fixtures import OfflineSite

from shared.newsletter import DYK_PROJECT_DATABASE_TITLES
from shared.sources.quarry import Replica, projects


class TestProjectIntersections(TestCase):
    """Keep scope filtering and overlapping memberships."""

    @staticmethod
    def test_empty_projects_skip_replica() -> None:
        """Keep empty input typed without a connection."""
        with patch("shared.sources.quarry.projects._replica") as replica:
            result = projects.query_project_intersections(
                OfflineSite("zh", "wikipedia"),
                "电子游戏",
                (),
            )
        replica.assert_not_called()
        assert result.is_empty()
        assert result.schema == {"page_id": pl.Int64, "project": pl.String}

    @staticmethod
    def test_one_bound_query_deduplicates_names_and_keeps_empty_schema() -> (
        None
    ):
        """Bind names once and omit article metadata."""
        names = ("虚构角色", "传记", "公司", "音乐")
        with patch(
            "shared.sources.quarry.query._fetch",
            return_value=(("page_id", "project"), []),
        ) as fetch:
            result = projects.query_project_intersections(
                OfflineSite("zh", "wikipedia"),
                "电子游戏",
                (*names, "公司"),
            )
        fetch.assert_called_once()
        replica, statement, _ = fetch.call_args.args
        assert replica.project == "zhwiki"
        assert tuple(statement.selected_columns.keys()) == (
            "page_id",
            "project",
        )
        compiled = statement.compile()
        assert list(compiled.params.values()) == ["电子游戏", list(names)]
        assert all(name not in str(compiled) for name in (*names, "电子游戏"))
        assert "page_title" not in str(compiled)
        assert "talk" not in str(compiled)
        assert result.is_empty()
        assert result.schema == {"page_id": pl.Int64, "project": pl.String}

    @staticmethod
    def test_sql_filters_scope_and_retains_overlapping_memberships() -> None:
        """Retain overlaps and exclude unrelated pages."""
        metadata = MetaData()
        project = Table(
            "page_assessments_projects",
            metadata,
            Column("pap_project_id", Integer, primary_key=True),
            Column("pap_project_title", String),
        )
        assessment = Table(
            "page_assessments",
            metadata,
            Column("pa_page_id", Integer, primary_key=True),
            Column("pa_project_id", Integer, primary_key=True),
        )
        engine = create_engine("sqlite://")
        replica = Replica("zhwiki")
        replica._engine = engine  # ruff: ignore[private-member-access]
        try:
            with engine.begin() as connection:
                metadata.create_all(connection)
                connection.execute(
                    project.insert(),
                    [
                        {
                            "pap_project_id": number,
                            "pap_project_title": title,
                        }
                        for number, title in enumerate(
                            (
                                "电子游戏",
                                "虚构角色",
                                "传记",
                                "公司",
                                "音樂",
                                "其他",
                                "音乐",
                                "音樂/流行音樂",
                            ),
                        )
                    ],
                )
                connection.execute(
                    assessment.insert(),
                    [
                        {"pa_page_id": page_id, "pa_project_id": project_id}
                        for page_id, project_id in (
                            (1, 0),
                            (1, 1),
                            (1, 2),
                            (1, 3),
                            (1, 4),
                            (2, 0),
                            (2, 3),
                            (2, 5),
                            (3, 1),
                            (3, 4),
                            (4, 0),
                            (4, 5),
                            (1, 6),
                            (2, 6),
                            (3, 6),
                            (4, 7),
                        )
                    ],
                )
            with patch(
                "shared.sources.quarry.projects._replica",
                return_value=replica,
            ):
                result = projects.query_project_intersections(
                    OfflineSite("zh", "wikipedia"),
                    "电子游戏",
                    ("虚构角色", "传记", "公司", "音乐", "公司"),
                )
            assert result.rows() == [
                (1, "传记"),
                (1, "公司"),
                (1, "虚构角色"),
                (1, "音乐"),
                (2, "公司"),
                (2, "音乐"),
            ]
            assert result.schema == {"page_id": pl.Int64, "project": pl.String}
            with patch(
                "shared.sources.quarry.projects._replica",
                return_value=replica,
            ):
                exact = projects.query_project_intersections(
                    OfflineSite("zh", "wikipedia"),
                    "电子游戏",
                    DYK_PROJECT_DATABASE_TITLES,
                )
            # Explicit aliases include traditional rows only.
            assert exact.filter(pl.col("project") == "音樂").rows() == [
                (1, "音樂"),
            ]
            assert not exact.filter(
                pl.col("project") == "音樂/流行音樂",
            ).height
        finally:
            engine.dispose()


class TestProjectRevisionCounts(TestCase):
    """Verify annual revision queries stay bounded and read-only."""

    @staticmethod
    def test_exact_utc_boundaries_and_main_namespace() -> None:
        """Bind UTC year limits and project article scope."""
        with patch(
            "shared.sources.quarry.query._fetch",
            return_value=(("page_id", "title", "revisions"), []),
        ) as fetch:
            result = projects.project_revision_counts(
                OfflineSite("zh", "wikipedia"),
                "电子游戏",
                dt.date(2026, 1, 1),
                dt.date(2027, 1, 1),
            )
        fetch.assert_called_once()
        _, statement, _ = fetch.call_args.args
        compiled = statement.compile()
        assert set(compiled.params.values()) == {
            "电子游戏",
            0,
            "20260101000000",
            "20270101000000",
        }
        sql = str(compiled)
        assert "page_namespace =" in sql
        assert "rev_timestamp >=" in sql
        assert "rev_timestamp <" in sql
        assert "count(revision.rev_id) DESC" in sql
        assert result.schema == {
            "page_id": pl.Int64,
            "title": pl.String,
            "revisions": pl.Int64,
        }
