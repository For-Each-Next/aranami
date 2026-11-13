"""Verify independent history sources for retired redirect articles."""

# Fixtures exercise source internals and exact observed identities.
# ruff: file-ignore[private-member-access, magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from wiki_fixtures import OfflineSite

from data.articles import scope as article_scope
from runtime import hub
from shared.sources.quarry import Replica, projects
from shared.sources.revisions import RevisionContent
from tasks.articles.history import collect_article_history, history_membership


def _members() -> pl.DataFrame:
    """Build one current member without discarding its assessment.

    Returns:
        Stable article and talk identities plus the current grade.
    """
    return pl.DataFrame({
        "page_id": [1],
        "page_namespace": [0],
        "full_title": ["Current"],
        "talk_page_id": [11],
        "page_is_redirect": [0],
        "pa_class": ["B"],
    })


def _redirects() -> pl.DataFrame:
    """Build a retired article with its own independent talk.

    Returns:
        A redirect source without a current project assessment.
    """
    return pl.DataFrame({
        "page_id": [2],
        "page_namespace": [0],
        "full_title": ["Old article"],
        "talk_page_id": [12],
        "page_is_redirect": [1],
        "pa_class": pl.Series([None], dtype=pl.String),
    })


class RedirectSourceTests(TestCase):
    """Discover local article redirects and their independent talks."""

    def test_local_section_redirects_and_chains_keep_source_ids(
        self,
    ) -> None:
        """Keep historical subjects separate from the target."""
        metadata = MetaData()
        page = Table(
            "page",
            metadata,
            Column("page_id", Integer, primary_key=True),
            Column("page_namespace", Integer),
            Column("page_title", String),
            Column("page_latest", Integer),
            Column("page_len", Integer),
            Column("page_is_redirect", Integer),
        )
        redirect = Table(
            "redirect",
            metadata,
            Column("rd_from", Integer, primary_key=True),
            Column("rd_namespace", Integer),
            Column("rd_title", String),
            Column("rd_interwiki", String),
            Column("rd_fragment", String),
        )
        engine = create_engine("sqlite://")
        replica = Replica("zhwiki")
        replica._engine = engine
        subjects = (
            (1, 0, "Current", 0),
            (2, 0, "Old_article", 1),
            (3, 0, "Older_article", 1),
            (4, 0, "Foreign", 1),
            (5, 2, "User_redirect", 1),
            (6, 0, "No_talk", 1),
            (7, 0, "Talk_target", 1),
            (8, 0, "Before_no_talk", 1),
            (11, 1, "Current", 0),
            (12, 1, "Old_article", 0),
            (13, 1, "Older_article", 0),
            (14, 1, "Foreign", 0),
            (15, 1, "User_redirect", 0),
            (17, 1, "Talk_target", 0),
            (18, 1, "Before_no_talk", 0),
        )
        redirects = (
            (2, 0, "Current", "", "Controversy"),
            (3, 0, "Old_article", None, ""),
            (4, 0, "Current", "en", ""),
            (5, 0, "Current", "", ""),
            (6, 0, "Current", "", ""),
            (7, 1, "Current", "", ""),
            (8, 0, "No_talk", "", ""),
        )
        try:
            with engine.begin() as connection:
                metadata.create_all(connection)
                connection.execute(
                    page.insert(),
                    [
                        {
                            "page_id": ident,
                            "page_namespace": ns,
                            "page_title": title,
                            "page_latest": ident * 10,
                            "page_len": 20,
                            "page_is_redirect": flag,
                        }
                        for ident, ns, title, flag in subjects
                    ],
                )
                connection.execute(
                    redirect.insert(),
                    [
                        {
                            "rd_from": ident,
                            "rd_namespace": ns,
                            "rd_title": title,
                            "rd_interwiki": wiki,
                            "rd_fragment": section,
                        }
                        for ident, ns, title, wiki, section in redirects
                    ],
                )
            with (
                patch.object(
                    projects.tables,
                    "Page",
                    SimpleNamespace(__table__=page),
                ),
                patch.object(
                    projects.tables,
                    "Redirect",
                    SimpleNamespace(__table__=redirect),
                ),
                patch.object(projects, "_replica", return_value=replica),
            ):
                observed = projects.query_history_redirects(
                    OfflineSite("zh", "wikipedia"),
                    _members(),
                ).sort("page_id")
                with (
                    patch.object(projects, "_MAX_REDIRECT_DEPTH", 1),
                    self.assertRaisesRegex(ValueError, "depth bound"),
                ):
                    projects.query_history_redirects(
                        OfflineSite("zh", "wikipedia"),
                        _members(),
                    )
                with (
                    patch.object(projects, "_MAX_HISTORY_REDIRECTS", 1),
                    self.assertRaisesRegex(ValueError, "row bound"),
                ):
                    projects.query_history_redirects(
                        OfflineSite("zh", "wikipedia"),
                        _members(),
                    )
                with (
                    patch.object(projects, "_MAX_HISTORY_REDIRECTS", 3),
                    self.assertRaisesRegex(ValueError, "row bound"),
                ):
                    projects.query_history_redirects(
                        OfflineSite("zh", "wikipedia"),
                        _members(),
                    )
            assert observed["page_id"].to_list() == [2, 3, 8]
            assert observed["full_title"].to_list() == [
                "Old article",
                "Older article",
                "Before no talk",
            ]
            assert observed["talk_page_id"].to_list() == [12, 13, 18]
            assert observed["pa_class"].to_list() == [None, None, None]
        finally:
            engine.dispose()

    @staticmethod
    def test_empty_scope_never_queries_replica() -> None:
        """Return an explicitly typed empty observation."""
        with patch.object(projects, "_replica") as replica:
            observed = projects.query_history_redirects(
                OfflineSite("zh", "wikipedia"),
                _members().clear(),
            )
        replica.assert_not_called()
        assert observed.is_empty()
        assert observed.schema["talk_page_id"] == pl.Int64


class RedirectHistoryTests(TestCase):
    """Retain recognition through verified redirect associations."""

    @staticmethod
    def test_hourly_scope_includes_redirects_without_badge_updates() -> None:
        """Observe old sources without inventing current assessments."""
        current = _members()
        expanded = pl.concat([current, _redirects()])
        empty = pl.DataFrame(schema={"kind": pl.String})
        with (
            patch.object(hub.pywikibot, "Site", return_value=Mock()),
            patch.object(
                hub,
                "query_pages_by_wikiproject",
                return_value=current,
            ),
            patch.object(hub, "history_membership", return_value=expanded),
            patch.object(hub, "reconcile_scope") as scope,
            patch.object(
                hub,
                "collect_article_history",
                return_value=empty,
            ) as history,
            patch.object(
                hub,
                "enrich_authorship",
                side_effect=lambda events, _site, **_options: events,
            ),
            patch.object(
                hub,
                "enrich_milestones",
                side_effect=lambda events, _site, **_options: events,
            ),
            patch.object(hub, "sync_current_assessments") as badges,
        ):
            hub.refresh_article_history()
        assert scope.call_args.args[0].equals(expanded)
        assert history.call_args.args[1].equals(expanded)
        assert badges.call_args.args[0].equals(current)

    @staticmethod
    def test_redirect_talk_is_read_without_current_history_tags() -> None:
        """Keep recognition under the original article identity."""
        site = OfflineSite("zh", "wikipedia")
        text = (
            "{{Article history|action1=GAN|"
            "action1date=2017-04-01|action1result=listed|"
            "action2=GAR|action2date=2020-05-01|"
            "action2result=delisted}}"
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.parquet"
            with (
                patch(
                    "tasks.articles.history.query_history_redirects",
                    return_value=_redirects(),
                ),
                patch(
                    "tasks.articles.history.template_aliases",
                    side_effect=lambda _site, title: (title,),
                ),
                patch(
                    "tasks.articles.history.query_tags",
                    side_effect=lambda _site, members, _tags: (
                        members.with_columns(
                            pl.lit([], dtype=pl.List(pl.String)).alias("tags"),
                        )
                    ),
                ),
                patch(
                    "tasks.articles.history.latest_revisions",
                    return_value=pl.DataFrame({
                        "page_id": [12],
                        "page_latest": [120],
                    }),
                ),
                patch(
                    "tasks.articles.history.read_revision_contents",
                    return_value={
                        120: RevisionContent(
                            12,
                            "Talk:Old article",
                            120,
                            dt.datetime(2026, 10, 30, tzinfo=dt.UTC),
                            hashlib.sha1(
                                text.encode(),
                                usedforsecurity=False,
                            ).hexdigest(),
                            text,
                        ),
                    },
                ) as preload,
            ):
                members = history_membership(site, _members())
                events = collect_article_history(
                    site,
                    members,
                    now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
                    path=path,
                )
            identities = events.select("page_id").unique()
            assert identities.to_series().to_list() == [2]
            quality = events.filter(pl.col("kind") == "quality")
            assert quality["action"].to_list() == ["added", "removed"]
            assert quality["title"].to_list() == ["Old article"] * 2
            assert quality["talk_page_id"].to_list() == [12] * 2
            assert quality["revision_id"].to_list() == [120] * 2
            preload.assert_called_once_with(site, [120])

    @staticmethod
    def test_verified_redirect_association_resets_scope_absence() -> None:
        """Do not expire history while its redirect stays associated."""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            observed = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
            with patch(
                "tasks.articles.history.query_history_redirects",
                return_value=_redirects(),
            ):
                expanded = history_membership(
                    OfflineSite("zh", "wikipedia"),
                    _members(),
                )
            article_scope.reconcile_scope(expanded, root, observed_at=observed)
            article_scope.reconcile_scope(
                _members(),
                root,
                observed_at=observed + dt.timedelta(days=1),
            )
            article_scope.reconcile_scope(
                expanded,
                root,
                observed_at=observed + dt.timedelta(days=367),
            )
            retired = article_scope.load(root).filter(pl.col("page_id") == 2)
            assert retired["purged_at"].item() is None
            assert retired["first_out_of_scope"].item() is None
            assert (
                expanded.filter(pl.col("page_id") == 1)["pa_class"].item()
                == "B"
            )
