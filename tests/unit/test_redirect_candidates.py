"""Verify bounded replica discovery for historical redirect recovery."""

# Isolate query internals and SQLite fixtures from real Wiki Replicas.
# ruff: file-ignore[private-member-access, magic-value-comparison, pytest-unittest-raises-assertion]

from types import SimpleNamespace
from typing import override
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

from shared.sources.quarry import Replica, projects
from shared.support.activity import activity_observer


class RedirectCandidateQueryTests(TestCase):
    """Pair current talk identities with bounded redirect subjects."""

    @override
    def setUp(self) -> None:
        """Create offline page and modern template-link tables."""
        self.site = OfflineSite("zh", "wikipedia")
        self.metadata = MetaData()
        self.page = Table(
            "page",
            self.metadata,
            Column("page_id", Integer, primary_key=True),
            Column("page_namespace", Integer),
            Column("page_title", String),
            Column("page_latest", Integer),
            Column("page_is_redirect", Integer),
        )
        self.target = Table(
            "linktarget",
            self.metadata,
            Column("lt_id", Integer, primary_key=True),
            Column("lt_namespace", Integer),
            Column("lt_title", String),
        )
        self.transclusion = Table(
            "templatelinks",
            self.metadata,
            Column("tl_from", Integer),
            Column("tl_from_namespace", Integer),
            Column("tl_target_id", Integer),
        )
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        replica = Replica("zhwiki")
        replica._engine = self.engine
        for name, table in (
            ("Page", self.page),
            ("Linktarget", self.target),
            ("Templatelinks", self.transclusion),
        ):
            self.enterContext(
                patch.object(
                    projects.tables,
                    name,
                    SimpleNamespace(__table__=table),
                ),
            )
        self.enterContext(
            patch.object(projects, "_replica", return_value=replica),
        )
        with self.engine.begin() as connection:
            self.metadata.create_all(connection)
            connection.execute(
                self.target.insert(),
                [
                    {
                        "lt_id": identifier,
                        "lt_namespace": namespace,
                        "lt_title": title,
                    }
                    for identifier, namespace, title in (
                        (1, 10, "Old_vfd_multi"),
                        (2, 10, "Multidel"),
                        (3, 10, "Unselected"),
                        (4, 0, "Old_vfd_multi"),
                    )
                ],
            )

    def _load(
        self,
        pages: list[tuple[int, int, str, int]],
        links: list[tuple[int, int, int]],
    ) -> None:
        """Seed page identities and explicit template transclusions."""
        with self.engine.begin() as connection:
            connection.execute(
                self.page.insert(),
                [
                    {
                        "page_id": identifier,
                        "page_namespace": namespace,
                        "page_title": title,
                        "page_latest": identifier * 10,
                        "page_is_redirect": redirect,
                    }
                    for identifier, namespace, title, redirect in pages
                ],
            )
            connection.execute(
                self.transclusion.insert(),
                [
                    {
                        "tl_from": identifier,
                        "tl_from_namespace": namespace,
                        "tl_target_id": target,
                    }
                    for identifier, namespace, target in links
                ],
            )

    def test_selection_normalizes_aliases_and_pairs_current_redirects(
        self,
    ) -> None:
        """Exclude missing subjects and unrelated page scopes."""
        self._load(
            [
                (1, 0, "First", 1),
                (2, 0, "Renamed_page", 1),
                (3, 0, "Current_article", 0),
                (4, 0, "Redirect_talk", 1),
                (5, 0, "Other_template", 1),
                (6, 2, "User_only", 1),
                (7, 0, "Wrong_target_namespace", 1),
                (8, 0, "Stale_link_namespace", 1),
                (101, 1, "First", 0),
                (102, 1, "Renamed_page", 0),
                (103, 1, "Current_article", 0),
                (104, 1, "Redirect_talk", 1),
                (105, 1, "Other_template", 0),
                (106, 1, "No_subject", 0),
                (107, 3, "First", 0),
                (108, 1, "User_only", 0),
                (109, 1, "Wrong_target_namespace", 0),
                (110, 1, "Stale_link_namespace", 0),
            ],
            [
                (101, 1, 1),
                (102, 1, 1),
                (102, 1, 2),
                (103, 1, 1),
                (104, 1, 1),
                (105, 1, 3),
                (106, 1, 1),
                (107, 3, 1),
                (108, 1, 1),
                (109, 1, 4),
                (110, 0, 1),
            ],
        )
        result = projects.query_redirect_recovery_candidates(
            self.site,
            ["Template:old vfd multi", "模板:Multidel", "模版:Old_vfd_multi"],
        )
        assert result["talk_page_id"].to_list() == [101, 102]
        assert result["page_id"].to_list() == [1, 2]
        assert result["page_revision"].to_list() == [10, 20]
        assert result["talk_revision"].to_list() == [1010, 1020]
        assert result["page_is_redirect"].to_list() == [1, 1]
        assert result["page_title"].to_list() == ["First", "Renamed_page"]
        assert result["full_title"].to_list() == ["First", "Renamed page"]
        assert result["talk_title"].to_list() == [
            "Talk:First",
            "Talk:Renamed page",
        ]

    def test_keyset_returns_fifty_unique_talks_then_remaining_rows(
        self,
    ) -> None:
        """Limit unique talks and advance by their stable IDs."""
        self._load(
            [
                row
                for identifier in range(1, 61)
                for row in (
                    (identifier, 0, f"Article_{identifier}", 1),
                    (identifier + 100, 1, f"Article_{identifier}", 0),
                )
            ],
            [
                (identifier + 100, 1, target)
                for identifier in range(1, 61)
                for target in (1, 2)
            ],
        )
        aliases = ["Template:Old vfd multi", "Template:Multidel"]
        first = projects.query_redirect_recovery_candidates(self.site, aliases)
        second = projects.query_redirect_recovery_candidates(
            self.site,
            aliases,
            after_page_id=first["talk_page_id"][-1],
        )
        final = projects.query_redirect_recovery_candidates(
            self.site,
            aliases,
            after_page_id=second["talk_page_id"][-1],
        )
        assert first["talk_page_id"].to_list() == list(range(101, 151))
        assert second["talk_page_id"].to_list() == list(range(151, 161))
        assert final.is_empty()
        assert first.schema == second.schema == final.schema
        assert first.schema["page_id"] == pl.Int64
        assert first.schema["full_title"] == pl.String

    def test_empty_selection_never_opens_a_replica(self) -> None:
        """Return typed empty rows without an unrestricted query."""
        with patch.object(projects, "_replica") as replica:
            result = projects.query_redirect_recovery_candidates(self.site, [])
        replica.assert_not_called()
        assert result.is_empty()
        assert result.schema["talk_page_id"] == pl.Int64
        assert result.schema["talk_title"] == pl.String

    def test_query_uses_indexed_template_targets_and_bounded_keyset(
        self,
    ) -> None:
        """Select metadata with exact namespace joins and no offset."""
        observed = []
        with (
            activity_observer(lambda _kind, source: observed.append(source)),
            patch(
                "shared.sources.quarry.query._fetch",
                return_value=(tuple(projects._REDIRECT_RECOVERY_SCHEMA), []),
            ) as fetch,
        ):
            result = projects.query_redirect_recovery_candidates(
                self.site,
                ["Template:Old vfd multi", "模板:Multidel"],
                after_page_id=345,
            )
        replica, statement, _ = fetch.call_args.args
        compiled = statement.compile()
        query = str(compiled)
        assert replica.project == "zhwiki"
        assert statement.is_select
        assert query.startswith("SELECT DISTINCT ")
        assert "templatelinks.tl_target_id = linktarget.lt_id" in query
        assert "recovery_talk.page_id = templatelinks.tl_from" in query
        assert (
            "recovery_subject.page_title = recovery_talk.page_title" in query
        )
        assert "templatelinks.tl_from_namespace =" in query
        assert "recovery_talk.page_namespace =" in query
        assert "recovery_subject.page_namespace =" in query
        assert "recovery_talk.page_id >" in query
        assert "ORDER BY recovery_talk.page_id" in query
        assert statement._offset_clause is None
        assert statement._limit_clause.value == 50
        assert compiled.params["page_id_1"] == 345
        assert compiled.params["lt_namespace_1"] == 10
        assert compiled.params["lt_title_1"] == ["Old_vfd_multi", "Multidel"]
        assert observed[0]["purpose"] == "page_tags"
        assert observed[0]["scope"] == {
            "page_kind": "talk_pages",
            "templates": ["模板:Old vfd multi", "模板:Multidel"],
        }
        assert result.is_empty()

    def test_large_alias_selection_keeps_source_observations_valid(
        self,
    ) -> None:
        """Keep source facts valid for a large alias selection."""
        observed = []
        with (
            activity_observer(lambda _kind, source: observed.append(source)),
            patch(
                "shared.sources.quarry.query._fetch",
                return_value=(tuple(projects._REDIRECT_RECOVERY_SCHEMA), []),
            ) as fetch,
        ):
            projects.query_redirect_recovery_candidates(
                self.site,
                [f"Template:Alias {index}" for index in range(65)],
            )
        statement = fetch.call_args.args[1]
        assert len(statement.compile().params["lt_title_1"]) == 65
        assert observed[0]["scope"] == {"page_kind": "talk_pages"}

    def test_invalid_selectors_never_open_a_replica(self) -> None:
        """Reject invalid cursors, large batches and non-templates."""
        for selectors in (
            {"after_page_id": -1},
            {"after_page_id": True},
            {"limit": 0},
            {"limit": 51},
            {"limit": True},
        ):
            with (
                self.subTest(selectors=selectors),
                patch.object(projects, "_replica") as replica,
                self.assertRaises(ValueError),
            ):
                projects.query_redirect_recovery_candidates(
                    self.site,
                    ["Template:Old vfd multi"],
                    **selectors,
                )
            replica.assert_not_called()
        for titles in (
            ["Category:Old vfd multi"],
            ["Old vfd multi"],
            [f"Template:Alias {index}" for index in range(257)],
        ):
            with (
                self.subTest(titles=titles),
                patch.object(projects, "_replica") as replica,
                self.assertRaises(ValueError),
            ):
                projects.query_redirect_recovery_candidates(self.site, titles)
            replica.assert_not_called()
