"""Verify bounded project queries and ordered page preloading."""

from datetime import date
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
import pywikibot
from wiki_fixtures import OfflineSite

from aranami.sources import wiki
from aranami.sources.quarry import projects


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
    def test_project_name_is_bound_and_empty_metadata_remains_typed() -> None:
        """Pass exact project names as bound values."""
        site = OfflineSite("zh", "wikipedia")
        name = "Project with ' quotes; and spaces"
        columns = (
            "page_id",
            "page_namespace",
            "page_title",
            "page_len",
            "page_is_redirect",
            "page_latest",
            "latest_timestamp",
            "defaultsort",
            "talk_page_id",
            "talk_page_latest",
            "pa_class",
            "pa_importance",
            "wikibase_item",
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
        assert result.is_empty()
        assert result.schema["full_title"] == pl.String
        assert result.schema["wikibase_qid"] == pl.Int64

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
