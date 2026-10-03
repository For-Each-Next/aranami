"""Verify bounded project queries and ordered page preloading."""

from datetime import UTC, date, datetime
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
import pywikibot
from sqlalchemy import (
    Column,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    create_engine,
)
from wiki_fixtures import OfflineSite

from aranami.sources import wiki
from aranami.sources.quarry import Replica, projects


class TestProjectTags(TestCase):
    """Verify page-key joins and safe empty membership queries."""

    @staticmethod
    def test_tags_join_main_and_talk_ids_preserving_input_order() -> None:
        """Map talk tags to articles and deduplicate labels."""
        site = OfflineSite("zh", "wikipedia")
        pages = pl.DataFrame(
            {"page_id": [2, 1, 3], "talk_page_id": [102, 101, None]},
        )
        specs = (
            ("ALERT", "main", "cat", "Category:Flagged pages"),
            ("REVIEW", "talk", "tl", "模版:Review"),
            ("ALERT", "talk", "cat", "Category:Talk flag"),
        )
        with patch(
            "aranami.sources.quarry.projects._tag_rows",
            side_effect=[
                pl.DataFrame({
                    "page_id": [1, 2],
                    "name": ["Flagged_pages"] * 2,
                }),
                pl.DataFrame({"page_id": [101, 101], "name": ["Review"] * 2}),
                pl.DataFrame({"page_id": [101], "name": ["Talk_flag"]}),
            ],
        ) as fetch:
            result = projects.query_tags(site, pages, specs)
        assert result["page_id"].to_list() == [2, 1, 3]
        assert result["tags"].to_list() == [["ALERT"], ["ALERT", "REVIEW"], []]
        assert dict(fetch.call_args_list[1].args[1]) == {"Review": "REVIEW"}
        assert fetch.call_args_list[1].args[2] == [102, 101]
        assert result.columns == ["page_id", "talk_page_id", "tags"]

    @staticmethod
    def test_empty_id_inputs_never_open_a_replica() -> None:
        """Return typed empty frames without an unrestricted query."""
        site = OfflineSite("zh", "wikipedia")
        pages = pl.DataFrame(
            schema={"page_id": pl.Int64, "talk_page_id": pl.Int64},
        )
        with patch("aranami.sources.quarry.projects._replica") as replica:
            tagged = projects.query_tags(
                site,
                pages,
                [("N", "main", "cat", "Needs attention")],
            )
            revisions = projects.latest_revisions(site, [])
            memberships = projects._tag_rows(  # ruff: ignore[private-member-access]
                site,
                ["Needs_attention"],
                [],
                category=True,
            )
        replica.assert_not_called()
        assert tagged.schema["tags"] == pl.List(pl.String)
        assert revisions.schema == {
            "page_id": pl.Int64,
            "page_latest": pl.Int64,
        }
        assert memberships.schema == {"page_id": pl.Int64, "name": pl.String}


class TestProjectStatements(TestCase):
    """Verify actual QueryFrame bindings and metadata restrictions."""

    @staticmethod
    def test_category_members_apply_explicit_main_namespace_filter() -> None:
        """Apply namespace zero as a membership restriction."""
        site = OfflineSite("zh", "wikipedia")
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(("page_id", "page_namespace", "page_title"), []),
        ) as fetch:
            result = projects.category_members(
                site,
                "Category:Video games",
                namespace=0,
            )
        compiled = fetch.call_args.args[1].compile()
        assert compiled.params["lt_namespace_1"] == 14  # ruff: ignore[magic-value-comparison]
        assert compiled.params["lt_title_1"] == "Video_games"
        assert compiled.params["page_namespace_1"] == 0
        assert result.schema == {
            "page_id": pl.Int64,
            "page_namespace": pl.Int64,
            "page_title": pl.String,
        }

    @staticmethod
    def test_talk_category_members_include_stable_article_ids() -> None:
        """Keep article and talk-page IDs distinct in category rows."""
        site = OfflineSite("zh", "wikipedia")
        columns = (
            "page_id",
            "page_namespace",
            "page_title",
            "article_page_id",
        )
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(
                columns,
                [(11, 1, "Game", 7), (12, 1, "Orphan", None)],
            ),
        ) as fetch:
            result = projects.category_members(
                site,
                "Category:Assessed games",
                namespace=1,
            )
        statement = fetch.call_args.args[1]
        compiled = statement.compile()
        assert statement.is_select
        assert "LEFT OUTER JOIN page AS article" in str(compiled)
        assert "article.page_title = page.page_title" in str(compiled)
        assert compiled.params["page_namespace_1"] == 0
        assert compiled.params["page_namespace_2"] == 1
        assert result["page_id"].to_list() == [11, 12]
        assert result["article_page_id"].to_list() == [7, None]
        assert result.schema["article_page_id"] == pl.Int64

    @staticmethod
    def test_project_name_is_bound_and_empty_metadata_remains_typed() -> None:
        """Bind project names and retain typed routine metadata."""
        site = OfflineSite("zh", "wikipedia")
        name = "Project with ' quotes; and spaces"
        columns = (
            "page_id",
            "page_namespace",
            "page_title",
            "talk_page_id",
            "pa_class",
            "pa_importance",
        )
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(columns, []),
        ) as fetch:
            result = projects.query_pages_by_wikiproject(site, name)
        replica, statement, _ = fetch.call_args.args
        compiled = statement.compile()
        assert replica.project == "zhwiki"
        assert statement.is_select
        assert name in compiled.params.values()
        assert name not in str(compiled)
        assert "talk.page_namespace" in str(compiled)
        assert "revision" not in str(compiled)
        assert "page_props" not in str(compiled)
        assert tuple(statement.selected_columns.keys()) == columns
        assert result.is_empty()
        assert result.schema == {
            "page_id": pl.Int64,
            "page_namespace": pl.Int64,
            "page_title": pl.String,
            "talk_page_id": pl.Int64,
            "pa_class": pl.String,
            "pa_importance": pl.String,
            "full_title": pl.String,
        }

    @staticmethod
    def test_membership_queries_bind_namespaces_names_and_page_ids() -> None:
        """Restrict link joins to their exact targets."""
        site = OfflineSite("zh", "wikipedia")
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(("page_id", "name"), []),
        ) as fetch:
            for category in (False, True):
                projects._tag_rows(  # ruff: ignore[private-member-access]
                    site,
                    ["Target_name"],
                    [1, 2, 1],
                    category=category,
                )
        for call, expected_namespace in zip(
            fetch.call_args_list,
            (10, 14),
            strict=True,
        ):
            statement = call.args[1]
            parameters = statement.compile().params
            assert parameters["lt_namespace_1"] == expected_namespace
            assert parameters["lt_title_1"] == ["Target_name"]
            ids = parameters.get("cl_from_1", parameters.get("tl_from_1"))
            assert ids == [1, 2]
            assert statement.is_select

    @staticmethod
    def test_latest_revisions_deduplicate_and_bound_each_batch() -> None:
        """Divide large inputs into constrained identifier batches."""
        site = OfflineSite("zh", "wikipedia")
        identifiers = [*range(1001), 0, 1]
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(("page_id", "page_latest"), []),
        ) as fetch:
            result = projects.latest_revisions(site, identifiers)
        batches = [
            call.args[1].compile().params["page_id_1"]
            for call in fetch.call_args_list
        ]
        assert [len(batch) for batch in batches] == [500, 500, 1]
        assert [value for batch in batches for value in batch] == list(
            range(1001),
        )
        assert result.is_empty()

    @staticmethod
    def test_new_page_query_uses_utc_bounds_and_content_filters() -> None:
        """Restrict creation searches to eligible content pages."""
        site = OfflineSite("zh", "wikipedia")
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(("page_id",), [(123,)]),
        ) as fetch:
            result = projects.new_page_ids(
                site,
                date(2026, 9, 1),
                date(2026, 9, 2),
            )
        statement = fetch.call_args.args[1]
        compiled = statement.compile()
        assert result == [123]
        assert compiled.params["rev_timestamp_1"] == "20260901000000"
        assert compiled.params["rev_timestamp_2"] == "20260902000000"
        assert compiled.params["rev_parent_id_1"] == 0
        assert compiled.params["page_is_redirect_1"] == 0
        assert "page.page_namespace %" in str(compiled)
        assert "page.page_namespace !=" in str(compiled)

    @staticmethod
    def test_redirect_query_binds_revision_dates_tag_and_content_filters() -> (
        None
    ):
        """Find tagged rewrites independently of creation revisions."""
        site = OfflineSite("zh", "wikipedia")
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(("page_id",), [(123,)]),
        ) as fetch:
            result = projects.redirect_converted_page_ids(
                site,
                date(2026, 9, 1),
                date(2026, 9, 2),
            )
        replica, statement, _ = fetch.call_args.args
        compiled = statement.compile()
        query = str(compiled)
        assert result == [123]
        assert replica.project == "zhwiki"
        assert statement.is_select
        assert tuple(statement.selected_columns.keys()) == ("page_id",)
        assert compiled.params["rev_timestamp_1"] == "20260901000000"
        assert compiled.params["rev_timestamp_2"] == "20260902000000"
        assert compiled.params["ctd_name_1"] == "mw-removed-redirect"
        assert "mw-removed-redirect" not in query
        assert "change_tag.ct_rev_id = revision.rev_id" in query
        assert "change_tag.ct_tag_id = change_tag_def.ctd_id" in query
        assert "revision.rev_page = page.page_id" in query
        assert "revision.rev_timestamp >=" in query
        assert "revision.rev_timestamp <" in query
        assert "rev_parent_id" not in query
        assert compiled.params["page_is_redirect_1"] == 0
        assert compiled.params["page_namespace_1"] == 2  # ruff: ignore[magic-value-comparison]
        assert compiled.params["param_1"] == 0
        assert compiled.params["page_namespace_2"] == 2  # ruff: ignore[magic-value-comparison]
        assert "page.page_namespace %" in query
        assert "page.page_namespace !=" in query
        assert "SELECT DISTINCT page.page_id" in query
        assert "ORDER BY page.page_id" in query

    @staticmethod
    def test_redirect_query_empty_result_remains_an_identifier_list() -> None:
        """Keep empty revision results typed."""
        site = OfflineSite("zh", "wikipedia")
        with (
            patch(
                "aranami.sources.quarry.query._fetch",
                return_value=(("page_id",), []),
            ),
            patch.object(
                Replica,
                "query",
                autospec=True,
                side_effect=Replica.query,
            ) as query,
        ):
            result = projects.redirect_converted_page_ids(
                site,
                date(2026, 9, 1),
                date(2026, 9, 2),
            )
        assert result == []
        assert query.call_args.kwargs["schema_overrides"] == {
            "page_id": pl.Int64,
        }

    @staticmethod
    def test_redirect_query_executes_bounds_filters_and_deduplication() -> (
        None
    ):
        """Filter daily rewrites and deduplicate eligible page IDs."""
        metadata = MetaData()
        page = Table(
            "page",
            metadata,
            Column("page_id", Integer),
            Column("page_namespace", Integer),
            Column("page_is_redirect", Integer),
        )
        revision = Table(
            "revision",
            metadata,
            Column("rev_id", Integer),
            Column("rev_page", Integer),
            Column("rev_timestamp", LargeBinary),
            Column("rev_parent_id", Integer),
        )
        tag = Table(
            "change_tag",
            metadata,
            Column("ct_rev_id", Integer),
            Column("ct_tag_id", Integer),
        )
        definition = Table(
            "change_tag_def",
            metadata,
            Column("ctd_id", Integer),
            Column("ctd_name", LargeBinary),
        )
        engine = create_engine("sqlite://")
        replica = Replica("zhwiki")
        replica._engine = engine  # ruff: ignore[private-member-access]
        try:
            with engine.begin() as connection:
                metadata.create_all(connection)
                connection.execute(
                    page.insert(),
                    [
                        {
                            "page_id": identifier,
                            "page_namespace": namespace,
                            "page_is_redirect": redirect,
                        }
                        for identifier, namespace, redirect in (
                            (8, 4, 0),
                            (1, 0, 0),
                            (2, 0, 1),
                            (3, 2, 0),
                            (4, 1, 0),
                            (5, 0, 0),
                            (6, 0, 0),
                            (7, 0, 0),
                            (9, 0, 0),
                        )
                    ],
                )
                connection.execute(
                    revision.insert(),
                    [
                        {
                            "rev_id": identifier,
                            "rev_page": page_id,
                            "rev_timestamp": timestamp,
                            "rev_parent_id": 999,
                        }
                        for identifier, page_id, timestamp in (
                            (80, 8, b"20260901235959"),
                            (10, 1, b"20260901000000"),
                            (11, 1, b"20260901120000"),
                            (20, 2, b"20260901120000"),
                            (30, 3, b"20260901120000"),
                            (40, 4, b"20260901120000"),
                            (50, 5, b"20260831235959"),
                            (60, 6, b"20260902000000"),
                            (70, 7, b"20260901120000"),
                            (90, 9, b"20260901120000"),
                            (100, 100, b"20260901120000"),
                        )
                    ],
                )
                connection.execute(
                    tag.insert(),
                    [
                        {"ct_rev_id": identifier, "ct_tag_id": tag_id}
                        for identifier, tag_id in (
                            (80, 1),
                            (10, 1),
                            (11, 1),
                            (20, 1),
                            (30, 1),
                            (40, 1),
                            (50, 1),
                            (60, 1),
                            (70, 2),
                            (100, 1),
                        )
                    ],
                )
                connection.execute(
                    definition.insert(),
                    [
                        {"ctd_id": 1, "ctd_name": b"mw-removed-redirect"},
                        {
                            "ctd_id": 2,
                            "ctd_name": b"mw-removed-redirect-extra",
                        },
                    ],
                )
            with patch(
                "aranami.sources.quarry.projects._replica",
                return_value=replica,
            ):
                result = projects.redirect_converted_page_ids(
                    OfflineSite("zh", "wikipedia"),
                    date(2026, 9, 1),
                    date(2026, 9, 2),
                )
            assert result == [1, 8]
        finally:
            engine.dispose()


class TestPageCreationMetadata(TestCase):
    """Verify bounded identity queries and original revision times."""

    @staticmethod
    def test_empty_titles_never_open_a_replica() -> None:
        """Retain the result schema without querying unrelated pages."""
        with patch("aranami.sources.quarry.projects._replica") as replica:
            result = projects.page_creation_metadata(
                OfflineSite("zh", "wikipedia"),
                [],
            )
        replica.assert_not_called()
        assert result.is_empty()
        assert result.schema == {
            "full_title": pl.String,
            "page_id": pl.Int64,
            "created_at": pl.Datetime(time_unit="us", time_zone="UTC"),
        }

    @staticmethod
    def test_titles_normalize_bind_and_deduplicate_before_batching() -> None:
        """Normalize namespace aliases and bound database-title keys."""
        site = OfflineSite("zh", "wikipedia")
        titles = [
            "PJ:Good_games",
            "WikiProject:Good games",
            "维基专题:Good games#Details",
            *[f"Game {number}" for number in range(1000)],
            "Game_0",
        ]
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(
                ("page_id", "page_namespace", "page_title", "created_at"),
                [],
            ),
        ) as fetch:
            result = projects.page_creation_metadata(site, titles)
        batches = []
        for call in fetch.call_args_list:
            replica, statement, _ = call.args
            compiled = statement.compile()
            assert replica.project == "zhwiki"
            assert statement.is_select
            assert tuple(statement.selected_columns.keys()) == (
                "page_id",
                "page_namespace",
                "page_title",
                "created_at",
            )
            query = str(compiled)
            assert "min(revision.rev_timestamp)" in query
            assert "revision.rev_page = page.page_id" in query
            assert "page_is_redirect" not in query
            assert "Good_games" not in query
            batches.append(compiled.params["param_1"])
        assert [len(batch) for batch in batches] == [500, 500, 1]
        assert [key for batch in batches for key in batch] == [
            (102, "Good_games"),
            *[(0, f"Game_{number}") for number in range(1000)],
        ]
        assert result.is_empty()
        assert result.schema["created_at"] == pl.Datetime(
            time_unit="us",
            time_zone="UTC",
        )

    @staticmethod
    def test_unavailable_revision_time_remains_null() -> None:
        """Keep page identities without inventing a creation date."""
        with patch(
            "aranami.sources.quarry.query._fetch",
            return_value=(
                ("page_id", "page_namespace", "page_title", "created_at"),
                [(123, 102, "Good_games", None)],
            ),
        ):
            result = projects.page_creation_metadata(
                OfflineSite("zh", "wikipedia"),
                ["PJ:Good games"],
            )
        assert result.to_dicts() == [
            {
                "full_title": "WikiProject:Good games",
                "page_id": 123,
                "created_at": None,
            },
        ]
        assert result.schema["created_at"] == pl.Datetime(
            time_unit="us",
            time_zone="UTC",
        )

    @staticmethod
    def test_query_reads_first_revision_for_each_current_identity() -> None:
        """Keep original times for rewrites and current redirects."""
        metadata = MetaData()
        page = Table(
            "page",
            metadata,
            Column("page_id", Integer),
            Column("page_namespace", Integer),
            Column("page_title", LargeBinary),
            Column("page_is_redirect", Integer),
        )
        revision = Table(
            "revision",
            metadata,
            Column("rev_id", Integer),
            Column("rev_page", Integer),
            Column("rev_timestamp", LargeBinary),
        )
        engine = create_engine("sqlite://")
        replica = Replica("zhwiki")
        replica._engine = engine  # ruff: ignore[private-member-access]
        try:
            with engine.begin() as connection:
                metadata.create_all(connection)
                connection.execute(
                    page.insert(),
                    [
                        {
                            "page_id": identifier,
                            "page_namespace": namespace,
                            "page_title": title,
                            "page_is_redirect": redirect,
                        }
                        for identifier, namespace, title, redirect in (
                            (7, 0, b"Game", 1),
                            (8, 4, b"Good_games", 0),
                            (9, 0, b"No_history", 0),
                            (10, 0, b"Good_games", 0),
                        )
                    ],
                )
                connection.execute(
                    revision.insert(),
                    [
                        {
                            "rev_id": identifier,
                            "rev_page": page_id,
                            "rev_timestamp": timestamp,
                        }
                        for identifier, page_id, timestamp in (
                            (1, 7, b"20260901120000"),
                            (2, 7, b"20260805112233"),
                            (3, 8, b"20260901000000"),
                            (4, 8, b"20240101000000"),
                            (5, 10, b"20000101000000"),
                        )
                    ],
                )
            with patch(
                "aranami.sources.quarry.projects._replica",
                return_value=replica,
            ):
                result = projects.page_creation_metadata(
                    OfflineSite("zh", "wikipedia"),
                    ["Game", "Project:Good games", "No history", "Missing"],
                )
            assert result.to_dicts() == [
                {
                    "full_title": "Game",
                    "page_id": 7,
                    "created_at": datetime(2026, 8, 5, 11, 22, 33, tzinfo=UTC),
                },
                {
                    "full_title": "Project:Good games",
                    "page_id": 8,
                    "created_at": datetime(2024, 1, 1, tzinfo=UTC),
                },
                {
                    "full_title": "No history",
                    "page_id": 9,
                    "created_at": None,
                },
            ]
        finally:
            engine.dispose()


class TestWikiPreloading(TestCase):
    """Verify caller order and explicit missing-page results."""

    @staticmethod
    def test_title_preloading_restores_requested_order() -> None:
        """Return loaded objects in their original request order."""
        site = OfflineSite("zh", "wikipedia")
        first = pywikibot.Page(site, "First")
        second = pywikibot.Page(site, "Missing")
        with patch.object(pywikibot.Page, "botMayEdit", return_value=True):
            first.text = "loaded text"
            second.text = ""
        with patch(
            "aranami.sources.wiki.pagegenerators.PreloadingGenerator",
            return_value=[second, first],
        ) as preload:
            pages = wiki.read_pages(site, ["First", "Missing", "First"])
        assert pages == [first, second]
        assert pages[0] is first
        assert pages[1] is second
        assert not pages[1].text
        assert len(preload.call_args.args[0]) == 2  # ruff: ignore[magic-value-comparison]

    def test_omitted_preload_response_is_reported(self) -> None:
        """Report incomplete preloading responses explicitly."""
        site = OfflineSite("zh", "wikipedia")
        with (
            patch(
                "aranami.sources.wiki.pagegenerators.PreloadingGenerator",
                return_value=[],
            ),
            self.assertRaises(RuntimeError),  # ruff: ignore[pytest-unittest-raises-assertion]
        ):
            wiki.read_pages(site, ["Missing response"])

    @staticmethod
    def test_identifier_preloading_keeps_order_and_omits_deleted_ids() -> None:
        """Preserve surviving IDs despite reordered API output."""
        site = OfflineSite("zh", "wikipedia")
        first = Mock(pageid=10)
        second = Mock(pageid=20)
        with (
            patch(
                "aranami.sources.wiki.pagegenerators.PagesFromPageidGenerator",
                return_value=iter([first, second]),
            ) as identify,
            patch(
                "aranami.sources.wiki.pagegenerators.PreloadingGenerator",
                return_value=[first, second],
            ),
        ):
            result = wiki.read_page_ids(site, [20, 10, 20, 99])
        assert result == [second, first]
        identify.assert_called_once_with((20, 10, 99), site=site)

    @staticmethod
    def test_empty_preload_inputs_do_not_create_generators() -> None:
        """Avoid generator work for empty title and ID requests."""
        site = OfflineSite("zh", "wikipedia")
        with patch(
            "aranami.sources.wiki.pagegenerators.PreloadingGenerator",
        ) as preload:
            assert wiki.read_pages(site, []) == []
            assert wiki.read_page_ids(site, []) == []
        preload.assert_not_called()
