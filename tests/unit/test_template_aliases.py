"""Verify local alias metadata reuse without template-body requests."""

# Private fixtures exercise invalid data and numeric source bounds.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import json
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
import pywikibot
from sqlalchemy import (
    Column,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    create_engine,
)
from wiki_fixtures import OfflineSite

from runtime.jobs import context as job_context
from shared.sources import template_aliases as cache_source, wiki
from shared.sources.quarry import Replica, templates as query_source
from shared.sources.quarry.templates import TemplateAliasMetadata
from shared.sources.template_aliases import template_alias_cache
from shared.support.activity import activity_observer
from shared.support.cache import latest_cache_path

_NOW = dt.datetime(2026, 11, 3, 12, tzinfo=dt.UTC)


class TestTemplateAliasCache(TestCase):
    """Keep independent source metadata fresh and isolated on disk."""

    def setUp(self) -> None:
        """Bind an isolated cache directory and an actual UTC clock."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.directory = self.root / "cache"
        self.enterContext(template_alias_cache(self.directory))
        self.enterContext(
            patch.object(cache_source, "_utc_now", return_value=_NOW),
        )
        self.site = OfflineSite("zh", "wikipedia")
        self.title = pywikibot.Page(self.site, "History", 10).title()
        self.metadata = TemplateAliasMetadata(
            self.title,
            123,
            (self.title, pywikibot.Page(self.site, "歷史", 10).title()),
        )

    @property
    def path(self) -> Path:
        """The normalized fixture's selected timestamped file."""
        path = latest_cache_path(
            self.directory / "zhwiki" / "History" / "aliases",
        )
        assert path is not None
        return path

    def populate(self) -> tuple[str, ...]:
        """Write one successful bounded source observation.

        Returns:
            Fresh aliases including local script variants.
        """
        with patch.object(
            cache_source,
            "template_alias_metadata",
            return_value=self.metadata,
        ):
            return wiki.template_aliases(self.site, "Template:History")

    def test_fresh_cache_avoids_every_source_and_body_request(self) -> None:
        """Reuse metadata for bare and explicitly prefixed names."""
        with (
            patch.object(
                cache_source,
                "template_alias_metadata",
                return_value=self.metadata,
            ) as source,
            patch.object(wiki, "read_pages") as bodies,
            patch.object(
                wiki.pagegenerators,
                "PreloadingGenerator",
            ) as preload,
            patch.object(pywikibot.Page, "get") as get,
        ):
            first = wiki.template_aliases(self.site, "History")
            second = wiki.template_aliases(self.site, "Template:History")
        assert first == second
        assert first == (self.title, "模板:歷史", "模板:历史")
        source.assert_called_once_with(self.site, self.title)
        bodies.assert_not_called()
        preload.assert_not_called()
        get.assert_not_called()
        metadata = pl.read_parquet_metadata(self.path)
        storage = json.loads(metadata["aranami.storage"])
        assert storage["contract"] == "template-alias-metadata"
        assert storage["written_at"]
        assert storage["columns"]["aliases"] == "List(String)"

    def test_expiry_refreshes_aliases_without_template_revision_change(
        self,
    ) -> None:
        """Find added redirects even when the shell is unchanged."""
        self.populate()
        revised = TemplateAliasMetadata(
            self.title,
            self.metadata.canonical_revision,
            (self.title, "模板:New alias"),
        )
        with (
            patch.object(
                cache_source,
                "_utc_now",
                return_value=_NOW + dt.timedelta(hours=24),
            ),
            patch.object(
                cache_source,
                "template_alias_metadata",
                return_value=revised,
            ) as source,
        ):
            aliases = wiki.template_aliases(self.site, "History")
        source.assert_called_once()
        assert aliases == (self.title, "模板:New alias")

    def test_source_failures_do_not_relabel_expired_data_as_fresh(
        self,
    ) -> None:
        """Propagate refresh failure and preserve prior cache bytes."""
        self.populate()
        previous = self.path.read_bytes()
        with (
            patch.object(
                cache_source,
                "_utc_now",
                return_value=_NOW + dt.timedelta(days=2),
            ),
            patch.object(
                cache_source,
                "template_alias_metadata",
                side_effect=OSError("replica unavailable"),
            ),
            self.assertRaises(OSError),
        ):
            wiki.template_aliases(self.site, "History")
        assert self.path.read_bytes() == previous

    def test_corrupt_and_future_observations_are_cache_misses(self) -> None:
        """Rebuild invalid storage without invented freshness."""
        self.populate()
        self.path.write_bytes(b"broken cache")
        self.populate()
        frame = pl.read_parquet(self.path).with_columns(
            pl.lit(_NOW + dt.timedelta(days=1)).alias("checked_at"),
        )
        cache_source._CACHE.write(self.path, frame)
        with patch.object(
            cache_source,
            "template_alias_metadata",
            return_value=self.metadata,
        ) as source:
            assert wiki.template_aliases(self.site, "History")
        source.assert_called_once()

    def test_cache_is_scoped_by_wiki_and_caller_directory(self) -> None:
        """Keep language sites and preview writes independent."""
        self.populate()
        english = OfflineSite("en", "wikipedia")
        with patch.object(
            cache_source,
            "template_alias_metadata",
            return_value=self.metadata,
        ) as source:
            assert wiki.template_aliases(english, "History")
            with template_alias_cache(self.root / "preview"):
                assert wiki.template_aliases(self.site, "History")
        assert source.call_count == 2
        assert (
            latest_cache_path(
                self.directory / "enwiki" / "History" / "aliases",
            )
            is not None
        )
        assert (
            latest_cache_path(self.root / "preview/zhwiki/History/aliases")
            is not None
        )
        assert self.path.exists()

    def test_disabled_cache_and_failed_write_keep_fresh_results_usable(
        self,
    ) -> None:
        """Keep fresh results when persistence fails or is disabled."""
        with (
            patch.object(
                cache_source,
                "template_alias_metadata",
                return_value=self.metadata,
            ) as source,
            patch.object(
                type(cache_source._CACHE),
                "write",
                side_effect=OSError("full"),
            ),
        ):
            assert wiki.template_aliases(self.site, "History")
            with template_alias_cache(None):
                assert wiki.template_aliases(self.site, "History")
        assert source.call_count == 2
        assert not self.directory.exists()

    def test_cache_context_entry_and_invalid_names_are_inert(self) -> None:
        """Reject foreign and non-template names before querying."""
        with patch.object(cache_source, "template_alias_metadata") as source:
            with template_alias_cache(self.directory):
                assert not self.directory.exists()
            for value in (
                "Category:History",
                "en:Template:History",
                ":History",
            ):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    wiki.template_aliases(self.site, value)
        source.assert_not_called()
        assert not self.directory.exists()

    def test_long_local_titles_have_bounded_stable_filenames(self) -> None:
        """Retain full Unicode identity in bounded path components."""
        title = "歷" * 70
        full = pywikibot.Page(self.site, title, 10).title()
        metadata = TemplateAliasMetadata(full, 123, (full,))
        with patch.object(
            cache_source,
            "template_alias_metadata",
            return_value=metadata,
        ) as source:
            expected = wiki.template_aliases(self.site, title)
            assert wiki.template_aliases(self.site, title) == expected
        source.assert_called_once()
        files = list((self.directory / "zhwiki").rglob("*.parquet"))
        assert len(files) == 1
        assert len(files[0].name.encode()) <= 255

    def test_saved_activity_exposes_real_file_and_observation_times(
        self,
    ) -> None:
        """Report local metadata reuse rather than a text request."""
        self.populate()
        observer = Mock()
        with activity_observer(observer):
            wiki.template_aliases(self.site, "History")
        kind, source = observer.call_args.args
        assert kind == "source_loaded"
        assert source["provider"] == "saved"
        assert source["dataset"] == "template_aliases"
        assert source["scope"] == {"template": self.title}
        assert source["metadata"]["file"] == self.path.name
        assert source["metadata"]["observed_at"] == _NOW.isoformat()
        assert source["metadata"]["finished_at"] == _NOW.isoformat()
        assert "written_at" not in source["metadata"]

    def test_template_named_like_parquet_retains_timestamp_files(self) -> None:
        """Keep template suffixes separate from storage filenames."""
        title = pywikibot.Page(self.site, "History.parquet", 10).title()
        metadata = TemplateAliasMetadata(title, 123, (title,))
        with patch.object(
            cache_source,
            "template_alias_metadata",
            return_value=metadata,
        ) as source:
            assert wiki.template_aliases(self.site, title) == (title,)
            assert wiki.template_aliases(self.site, title) == (title,)
        source.assert_called_once()
        assert (
            latest_cache_path(
                self.directory / "zhwiki" / "History.parquet" / "aliases",
            )
            is not None
        )

    def test_dry_job_binds_only_the_preview_cache_directory(self) -> None:
        """Keep ordinary preview jobs out of production alias caches."""
        active = job_context.JobContext(self.site, _NOW.date(), dry=True)
        with (
            chdir(self.root),
            patch.object(
                cache_source,
                "template_alias_metadata",
                return_value=self.metadata,
            ),
            job_context.job_run("dyks", context=active),
        ):
            assert wiki.template_aliases(self.site, "History")
        assert (
            latest_cache_path(
                self.root
                / "dry-run/cache/template-aliases/zhwiki/History/aliases",
            )
            is not None
        )
        assert not (self.root / "cache/template-aliases").exists()


class TestReplicaTemplateAliases(TestCase):
    """Exercise real SQLAlchemy queries over isolated metadata."""

    def setUp(self) -> None:
        """Build a network-free replica with redirect edge cases."""
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        metadata = MetaData()
        self.page = Table(
            "page",
            metadata,
            Column("page_id", Integer, primary_key=True),
            Column("page_namespace", Integer),
            Column("page_title", LargeBinary),
            Column("page_latest", Integer),
            Column("page_is_redirect", Integer),
        )
        self.redirect = Table(
            "redirect",
            metadata,
            Column("rd_from", Integer, primary_key=True),
            Column("rd_namespace", Integer),
            Column("rd_title", LargeBinary),
            Column("rd_interwiki", String(32)),
            Column("rd_fragment", LargeBinary),
        )
        metadata.create_all(self.engine)
        self.site = OfflineSite("zh", "wikipedia")
        self.replica = Replica("zhwiki")
        self.replica._engine = self.engine
        self.enterContext(
            patch.object(query_source, "_replica", return_value=self.replica),
        )
        self.add_page(1, "History")
        self.add_page(2, "AH", target="History")
        self.add_page(3, "Section", target="History", fragment="Notes")
        self.add_page(4, "Foreign", target="History", interwiki="en")
        self.add_page(5, "Non-template", target="History", namespace=0)
        self.add_page(6, "Chain", target="AH")

    def add_page(  # ruff: ignore[too-many-arguments]
        self,
        identifier: int,
        title: str,
        *,
        target: str | None = None,
        namespace: int = 10,
        target_namespace: int = 10,
        interwiki: str = "",
        fragment: str = "",
    ) -> None:
        """Prepare fixture metadata without touching Wikimedia.

        Args:
            identifier: Unique source page identifier.
            title: Database title without its namespace.
            target: Optional redirect target title.
            namespace: Source namespace.
            target_namespace: Redirect target namespace.
            interwiki: Optional nonlocal redirect destination.
            fragment: Optional redirect section target.
        """
        with self.engine.begin() as connection:
            connection.execute(
                self.page.insert(),
                {
                    "page_id": identifier,
                    "page_namespace": namespace,
                    "page_title": title.encode(),
                    "page_latest": 100 + identifier,
                    "page_is_redirect": int(target is not None),
                },
            )
            if target is not None:
                connection.execute(
                    self.redirect.insert(),
                    {
                        "rd_from": identifier,
                        "rd_namespace": target_namespace,
                        "rd_title": target.encode(),
                        "rd_interwiki": interwiki,
                        "rd_fragment": fragment.encode(),
                    },
                )

    def test_direct_aliases_and_chains_use_metadata_only(self) -> None:
        """Keep canonical-first order and local section redirects."""
        with patch.object(pywikibot.Page, "get") as bodies:
            observed = query_source.template_alias_metadata(
                self.site,
                "Template:Chain",
            )
        assert observed.canonical_title == "模板:History"
        assert observed.canonical_revision == 101
        assert observed.aliases == (
            "模板:History",
            "模板:AH",
            "模板:Section",
            "模板:Chain",
        )
        bodies.assert_not_called()

    def test_missing_external_and_non_template_targets_are_rejected(
        self,
    ) -> None:
        """Reject other wikis and non-template redirect targets."""
        self.add_page(7, "Article", target="Other", target_namespace=0)
        self.add_page(8, "Broken", target="Missing")
        for name in ("Missing", "Foreign", "Article", "Broken"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                query_source.template_alias_metadata(
                    self.site,
                    f"Template:{name}",
                )
        with self.assertRaises(ValueError):
            query_source.template_alias_metadata(
                self.site,
                "en:Template:History",
            )

    def test_cycles_depth_and_alias_bounds_never_return_partial_results(
        self,
    ) -> None:
        """Fail discovery when completeness cannot be established."""
        self.add_page(7, "CycleA", target="CycleB")
        self.add_page(8, "CycleB", target="CycleA")
        with self.assertRaises(ValueError):
            query_source.template_alias_metadata(self.site, "Template:CycleA")
        with (
            patch.object(query_source, "_MAX_REDIRECT_DEPTH", 1),
            self.assertRaises(ValueError),
        ):
            query_source.template_alias_metadata(self.site, "Template:Chain")
        with (
            patch.object(query_source, "MAX_ALIASES", 1),
            self.assertRaises(ValueError),
        ):
            query_source.template_alias_metadata(self.site, "Template:History")

    def test_batched_incoming_aliases_keep_exact_redirect_targets(
        self,
    ) -> None:
        """Discover chained names without resolving the frontier."""
        self.add_page(7, "Sibling", target="AH")
        self.add_page(8, "Leaf", target="Chain")
        self.add_page(9, "Wrong namespace", target="AH", target_namespace=0)
        with patch.object(pywikibot.Page, "get") as bodies:
            first = query_source.query_template_redirect_aliases(
                self.site,
                ["Template:History"],
            )
            second = query_source.query_template_redirect_aliases(
                self.site,
                ["模板:AH", "Template:Section", "模版:AH"],
            )
            third = query_source.query_template_redirect_aliases(
                self.site,
                ["Template:Chain", "Template:Sibling"],
            )
        assert first == ("模板:AH", "模板:Section")
        assert second == ("模板:Chain", "模板:Sibling")
        assert third == ("模板:Leaf",)
        bodies.assert_not_called()

    def test_batched_alias_query_is_bounded_read_only_metadata(self) -> None:
        """Use exact local targets and a bounded metadata select."""
        observed = []
        with (
            activity_observer(lambda _kind, source: observed.append(source)),
            patch(
                "shared.sources.quarry.query._fetch",
                return_value=(("page_id", "page_title"), []),
            ) as fetch,
        ):
            result = query_source.query_template_redirect_aliases(
                self.site,
                ["模板:AH", "Template:Section"],
            )
        replica, statement, _ = fetch.call_args.args
        compiled = statement.compile()
        query = str(compiled)
        assert replica.project == "zhwiki"
        assert statement.is_select
        assert tuple(statement.selected_columns.keys()) == (
            "page_id",
            "page_title",
        )
        assert "page.page_id = redirect.rd_from" in query
        assert compiled.params["rd_title_1"] == ["AH", "Section"]
        assert compiled.params["rd_namespace_1"] == 10
        assert compiled.params["page_namespace_1"] == 10
        assert statement._limit_clause.value == query_source.MAX_ALIASES + 1
        assert observed[0]["scope"] == {
            "templates": ["模板:AH", "模板:Section"],
            "page_kind": "pages",
        }
        assert result == ()

    def test_batched_alias_bounds_and_invalid_targets_fail_closed(
        self,
    ) -> None:
        """Reject partial alias coverage and invalid target batches."""
        with patch.object(query_source, "_replica") as replica:
            assert (
                query_source.query_template_redirect_aliases(self.site, [])
                == ()
            )
            for titles in (
                ["History"],
                ["Category:History"],
                ["en:Template:History"],
                [f"Template:Target {index}" for index in range(51)],
            ):
                with (
                    self.subTest(titles=titles),
                    self.assertRaises(ValueError),
                ):
                    query_source.query_template_redirect_aliases(
                        self.site,
                        titles,
                    )
        replica.assert_not_called()
        with (
            patch.object(query_source, "MAX_ALIASES", 1),
            self.assertRaises(ValueError),
        ):
            query_source.query_template_redirect_aliases(
                self.site,
                ["Template:History"],
            )
