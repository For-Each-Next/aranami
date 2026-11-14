"""Verify actual source reads and truthful publication stages."""

# Keep the repository's unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from sqlalchemy import select

from data.runtime.status import read_status
from runtime.jobs import JobContext, ProposedEdit, job_run
from shared.sources.quarry import Replica, tables
from shared.sources.wiki import read_page_ids
from shared.support.activity import (
    activity_observer,
    report_activity,
    validate_source,
)


class TestSourceActivity(TestCase):
    """Keep source adapters inert without a live execution observer."""

    @staticmethod
    def test_new_page_preloading_retains_counts_and_identities() -> None:
        """Keep batch counts alongside exact public lookup samples."""
        observer = Mock()
        site = Mock()
        site.dbName.return_value = "zhwiki"
        page = Mock(pageid=123)
        page.title.return_value = "Example"
        scope = {"new_page_count": 1, "redirect_count": 0}
        with (
            activity_observer(observer),
            patch("shared.sources.wiki.pywikibot.Page", return_value=page),
            patch(
                "shared.sources.wiki.page_metadata",
                return_value=pl.DataFrame({"full_title": ["Example"]}),
            ),
            patch(
                "shared.sources.wiki.pagegenerators.PreloadingGenerator",
                return_value=[page],
            ),
        ):
            assert read_page_ids(
                site,
                [123],
                purpose="new_page_candidates",
                scope=scope,
            ) == [page]
        reads = [call.args for call in observer.call_args_list]
        assert [kind for kind, _ in reads] == ["source_read", "source_loaded"]
        assert all(source["scope"] == scope for _, source in reads)
        assert reads[0][1]["references"] == [
            {"kind": "page_id", "value": "123"},
        ]
        assert reads[1][1]["references"] == [
            {"kind": "page", "value": "Example"},
        ]

    @staticmethod
    def test_query_observations_exclude_sql_and_bound_values() -> None:
        """Observe actual collection with public table identities."""
        observer = Mock()
        statement = select(tables.Page.page_id).where(
            tables.Page.page_title == "PRIVATE BIND VALUE",
        )
        with (
            activity_observer(observer),
            patch(
                "shared.sources.quarry.query._fetch",
                return_value=(("page_id",), [(1,)]),
            ),
        ):
            query = Replica("zhwiki").query(statement)
            observer.assert_not_called()
            assert query.collect()["page_id"].to_list() == [1]
        observer.assert_called_once_with(
            "source_read",
            {
                "provider": "replica",
                "dataset": "database_query",
                "wiki": "zhwiki",
                "target": "page",
                "date": "",
                "references": [{"kind": "table", "value": "page"}],
                "reference_count": 1,
            },
        )

    @staticmethod
    def test_copied_context_stops_reporting_after_scope_closes() -> None:
        """Expire inherited observers when their owner finishes."""
        observer = Mock()
        with activity_observer(observer):
            copied = copy_context()
        copied.run(report_activity, "mediawiki", "page_text", target="Page")
        observer.assert_not_called()

    @staticmethod
    def test_query_intent_is_explicit_and_deferred_until_collection() -> None:
        """Retain public intent without exposing private SQL values."""
        observer = Mock()
        with (
            activity_observer(observer),
            patch(
                "shared.sources.quarry.query._fetch",
                return_value=(("page_latest",), [(123,)]),
            ),
        ):
            query = Replica("zhwiki").query(
                select(tables.Page.page_latest),
                purpose="latest_revisions",
                scope={"page_kind": "talk_pages", "page_count": 2},
            )
            observer.assert_not_called()
            query.collect()
        facts = observer.call_args.args[1]
        assert facts["purpose"] == "latest_revisions"
        assert facts["scope"] == {"page_kind": "talk_pages", "page_count": 2}
        assert facts["references"] == [{"kind": "table", "value": "page"}]

    def test_purpose_scope_is_bounded_typed_and_independent(self) -> None:
        """Keep old sources valid and reject invented query meanings."""
        source = {
            "provider": "replica",
            "dataset": "database_query",
            "wiki": "zhwiki",
            "target": "categorylinks, linktarget",
            "date": "",
            "purpose": "page_tags",
            "scope": {
                "categories": ["推薦的新條目"],
                "page_kind": "talk_pages",
            },
        }
        valid = validate_source(source)
        source["scope"]["categories"].append("changed")
        assert valid["scope"]["categories"] == ["推薦的新條目"]
        for invalid in (
            {"purpose": "dyk_because_categorylinks"},
            {"scope": {"query": "SELECT private"}},
            {"scope": {"categories": "推薦的新條目"}},
            {"scope": {"categories": ["bad\nname"]}},
            {"scope": {"page_kind": []}},
            {"purpose": "latest_revisions", "scope": {"page_count": True}},
            {"purpose": "latest_revisions", "scope": {"page_count": -1}},
            {
                "purpose": "new_page_candidates",
                "scope": {"new_page_count": 1},
            },
            {
                "purpose": "new_page_candidates",
                "scope": {"new_page_count": True, "redirect_count": 0},
            },
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_source({**source, **invalid})
        without_scope = {
            key: value for key, value in source.items() if key != "scope"
        }
        with self.assertRaises(ValueError):
            validate_source(without_scope)
        with (
            activity_observer(Mock()),
            self.assertRaises(ValueError),
        ):
            report_activity(
                "replica",
                "database_query",
                scope={"project": "Games"},
            )
        legacy = {
            key: value
            for key, value in source.items()
            if key not in {"purpose", "scope"}
        }
        assert validate_source(legacy) == legacy

    def test_malformed_public_source_facts_are_rejected(self) -> None:
        """Reject unbounded fields and malformed provider values."""
        source = {
            "provider": "saved",
            "dataset": "project_membership",
            "wiki": "zhwiki",
            "target": "Video games",
            "date": "2026-11-02",
        }
        for change in (
            {"provider": []},
            {"provider": "private"},
            {"target": "x" * 513},
            {"target": "line\nbreak"},
            {"date": "2026-99-99"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_source({**source, **change})

    def test_reference_fields_are_bounded_and_independent(self) -> None:
        """Reject malformed identities while preserving exact titles."""
        source = {
            "provider": "mediawiki",
            "dataset": "page_text",
            "wiki": "enwiki",
            "target": "A, B",
            "date": "",
            "references": [{"kind": "page", "value": "A, B"}],
            "reference_count": 1,
        }
        validated = validate_source(source)
        source["references"][0]["value"] = "changed"
        assert validated["references"][0]["value"] == "A, B"
        for reference in (
            {"kind": [], "value": "123"},
            {"kind": "page_id", "value": "0"},
            {"kind": "page_id", "value": "1, 2"},
            {"kind": "revision", "value": "0"},
            {"kind": "revision", "value": "\uff11\uff12"},
            {"kind": "revision", "value": "1, 2"},
            {"kind": "page", "value": "line\nbreak"},
        ):
            with (
                self.subTest(reference=reference),
                self.assertRaises(ValueError),
            ):
                validate_source({**source, "references": [reference]})
        with self.assertRaises(ValueError):
            validate_source({**source, "reference_count": 0})
        assert validate_source({
            **source,
            "references": [{"kind": "revision", "value": "123"}],
        })["references"] == [{"kind": "revision", "value": "123"}]

    def test_saved_metadata_rejects_private_paths_and_naive_times(
        self,
    ) -> None:
        """Keep unknown times while rejecting private paths."""
        source = {
            "provider": "saved",
            "dataset": "project_membership",
            "wiki": "zhwiki",
            "target": "Games",
            "date": "2026-11-02",
            "metadata": {
                "file": "data/daily/Games.parquet",
                "observed_at": "",
                "finished_at": "",
            },
        }
        assert not validate_source(source)["metadata"]["observed_at"]
        for change in (
            {"file": "/private/Games.parquet"},
            {"file": "reports/../secret"},
            {"observed_at": "2026-11-02T00:00:00"},
            {"written_at": "2026-11-02T00:00:00"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_source({
                    **source,
                    "metadata": {**source["metadata"], **change},
                })


class TestPublicationStages(TestCase):
    """Distinguish destination checks from actual wiki submission."""

    def setUp(self) -> None:
        """Keep status records and publication attempts disposable."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"

    def test_unchanged_destination_omits_save_stage(self) -> None:
        """Confirm unchanged from a fresh read after a prior save."""
        page = Mock(text="stale cache")
        page.get.return_value = "new"
        with (
            patch("updater.publisher.pywikibot.Page", return_value=page),
            job_run(
                "dyks",
                context=JobContext(self.site, dt.date(2026, 11, 3), dry=False),
            ) as active,
        ):
            active.publish(
                ProposedEdit(
                    self.site,
                    "Target",
                    "new",
                    "Update report.",
                    original_text="old",
                ),
            )
        page.get.assert_called_once_with(force=True)
        page.save.assert_not_called()
        run = next(iter(read_status(self.root)["executions"].values()))
        assert [stage["kind"] for stage in run["stages"]] == [
            "started",
            "checking",
            "no_update",
            "finished",
        ]
        assert run["updated_count"] == 0

    def test_distinct_category_purposes_survive_source_sampling(self) -> None:
        """Keep completed and candidate selections as separate facts."""
        with (
            patch("runtime.execution.perf_counter", return_value=100),
            job_run(
                "dyks",
                context=JobContext(self.site, dt.date(2026, 11, 3), dry=False),
            ),
        ):
            for category in ("推薦的新條目", "新條目推薦候選"):
                report_activity(
                    "replica",
                    "database_query",
                    wiki="zhwiki",
                    target="categorylinks, linktarget",
                    purpose="page_tags",
                    scope={
                        "categories": [category],
                        "page_kind": "talk_pages",
                    },
                )
        run = next(iter(read_status(self.root)["executions"].values()))
        sources = [
            stage["source"]
            for stage in run["stages"]
            if stage["kind"] == "source_read"
        ]
        assert [source["scope"]["categories"] for source in sources] == [
            ["推薦的新條目"],
            ["新條目推薦候選"],
        ]

    def test_coalesced_reads_preserve_actual_save_stages(self) -> None:
        """Bound repeated reads while preserving save boundaries."""
        page = Mock(text="old")
        page.get.return_value = "old"
        with (
            patch("updater.publisher.pywikibot.Page", return_value=page),
            patch("runtime.execution.perf_counter", return_value=100),
            job_run(
                "dyks",
                context=JobContext(self.site, dt.date(2026, 11, 3), dry=False),
            ) as active,
        ):
            for index in range(100):
                report_activity(
                    "saved",
                    "project_membership",
                    wiki="zhwiki",
                    target=f"Project {index}",
                    date=dt.date(2026, 11, 2),
                )
            active.publish(
                ProposedEdit(
                    self.site,
                    "Target",
                    "new",
                    "Update report.",
                    original_text="old",
                ),
            )
        page.save.assert_called_once()
        run = next(iter(read_status(self.root)["executions"].values()))
        assert [stage["kind"] for stage in run["stages"]] == [
            "started",
            "source_read",
            "checking",
            "saving",
            "saved",
            "finished",
        ]
        source = run["stages"][1]["source"]
        assert source["provider"] == "saved"
        assert source["target"] == "Project 0"
        assert source["date"] == "2026-11-02"
