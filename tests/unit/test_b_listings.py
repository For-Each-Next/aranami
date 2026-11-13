"""Verify latest B ranges, durable scan progress and Replica cutoffs."""

# Explicit source IDs describe the source evidence boundary.
# ruff: file-ignore[magic-value-comparison,private-member-access]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from pywikibot.page import Revision
from wiki_fixtures import OfflineSite

from data.articles import b_listings as store
from shared.assessment_profiles import assessment_profile
from shared.config import WEBSITE_REFRESH_INTERVALS
from shared.sources import assessment_history as history_source
from shared.sources.assessment_history import HistoricalRevision
from shared.sources.quarry import assessment as replica_source
from tasks.articles import b_listings as collector
from tasks.zhwiki.assessment_banner import banner_class

_NOW = dt.datetime(2026, 11, 12, tzinfo=dt.UTC)
_ALIASES = ("Template:WikiProject Video games", "Template:VG")
_SHELL_ALIASES = ("Template:WikiProject banner shell", "Template:WPBS")
_PROJECT_REDIRECTS = (
    "Template:电子游戏专题",
    "Template:電子遊戲專題",
    "Template:WPVG",
    "Template:WikiProject Video Games",
)


def _membership() -> pl.DataFrame:
    """Build current B membership with separate subject and talk IDs.

    Returns:
        One replica-shaped current project row.
    """
    return pl.DataFrame([
        {
            "page_id": 1,
            "page_title": "Game",
            "full_title": "Game",
            "page_revision": 100,
            "page_namespace": 0,
            "page_is_redirect": 0,
            "pa_class": "乙",
            "talk_page_id": 2,
            "talk_revision": 5,
            "talk_title": "Talk:Game",
        },
    ])


def _revision(identifier: int, grade: str) -> HistoricalRevision:
    """Build one visible exact-parent talk revision.

    Returns:
        Metadata and literal banner content for the selected instant.
    """
    return HistoricalRevision(
        identifier,
        identifier - 1,
        _NOW + dt.timedelta(seconds=identifier),
        "{{VG|class=" + grade + "}}",
    )


class BListingTests(TestCase):
    """Reuse proved talk ranges independently of subject edits."""

    @staticmethod
    def test_latest_range_and_subject_edit_reuse() -> None:
        """Select the latest B run and avoid unchanged history reads."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                collector,
                "template_aliases",
                return_value=_ALIASES,
            ),
            patch.object(
                collector,
                "read_talk_revisions",
                return_value=(
                    _revision(5, "乙"),
                    _revision(4, "乙"),
                    _revision(3, "丙"),
                    _revision(2, "乙"),
                ),
            ) as read,
        ):
            runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            revised = _membership().with_columns(
                pl.lit(101).alias("page_revision"),
            )
            collector.refresh_b_listings(site, revised, runtime=runtime)
            read.assert_called_once()
            row = store.load(runtime)["inference"].row(0, named=True)
            assert row["banner_entry_talk_rev_id"] == 4
            assert row["banner_entry_timestamp"] == _NOW + dt.timedelta(
                seconds=4,
            )
            assert row["banner_prior_talk_rev_id"] == 3
            assert row["banner_transition_status"] == "proven"
            assert list(runtime.rglob("*.parquet"))
            read.return_value = (_revision(6, "乙"),)
            changed = revised.with_columns(pl.lit(6).alias("talk_revision"))
            collector.refresh_b_listings(site, changed, runtime=runtime)
            assert read.call_count == 2
            assert read.call_args.kwargs["stop_revision"] == 5
            assert (
                store.load(runtime)["inference"][
                    "banner_entry_talk_rev_id"
                ].item()
                == 4
            )
            read.return_value = (_revision(8, "乙"), _revision(7, "丙"))
            changed = changed.with_columns(pl.lit(8).alias("talk_revision"))
            collector.refresh_b_listings(site, changed, runtime=runtime)
            assert read.call_args.kwargs["stop_revision"] == 6
            assert (
                store.load(runtime)["inference"][
                    "banner_entry_talk_rev_id"
                ].item()
                == 8
            )

    @staticmethod
    def test_incremental_prefix_survives_restart_and_new_head() -> None:
        """Resume new revisions against the saved complete head."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(collector, "template_aliases", return_value=_ALIASES),
            patch.object(
                collector,
                "read_talk_revisions",
                side_effect=[
                    (_revision(5, "乙"), _revision(4, "丙")),
                    (_revision(9, "乙"), _revision(8, "乙")),
                    (_revision(10, "乙"), _revision(9, "乙")),
                    (
                        _revision(8, "乙"),
                        _revision(7, "乙"),
                        _revision(6, "乙"),
                    ),
                    (_revision(11, "乙级列表"),),
                ],
            ) as read,
        ):
            runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            for head in (9, 10, 10):
                changed = _membership().with_columns(
                    pl.lit(head).alias("talk_revision"),
                )
                collector.refresh_b_listings(site, changed, runtime=runtime)
            assert read.call_args.kwargs["start_revision"] == 8
            assert read.call_args.kwargs["stop_revision"] == 5
            row = store.load(runtime)["inference"].row(0, named=True)
            assert row["banner_entry_talk_rev_id"] == 5
            assert row["scanned_talk_rev_id"] == 10
            changed = changed.with_columns(
                pl.lit(11).alias("talk_revision"),
                pl.lit("乙级列表").alias("pa_class"),
            )
            collector.refresh_b_listings(site, changed, runtime=runtime)
            row = store.load(runtime)["inference"].row(0, named=True)
            assert row["banner_entry_talk_rev_id"] == 11
            assert row["banner_prior_talk_rev_id"] == 10

    @staticmethod
    def test_bounded_prefix_resumes_at_next_parent() -> None:
        """Keep parsed revisions through bounded retries."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                collector,
                "template_aliases",
                return_value=_ALIASES,
            ),
            patch.object(
                collector,
                "read_talk_revisions",
                side_effect=[
                    (_revision(5, "乙"), _revision(4, "乙")),
                    (_revision(3, "丙"),),
                ],
            ) as read,
        ):
            runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            assert read.call_args.kwargs["start_revision"] == 3
            assert (
                store.load(runtime)["inference"][
                    "banner_entry_talk_rev_id"
                ].item()
                == 4
            )

    @staticmethod
    def test_hidden_history_and_visible_creation() -> None:
        """Distinguish an unknown interval from a visible creation."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                collector,
                "template_aliases",
                return_value=_ALIASES,
            ),
            patch.object(
                collector,
                "read_talk_revisions",
                return_value=(
                    _revision(5, "乙"),
                    HistoricalRevision(4, 3, _NOW, None),
                ),
            ) as read,
        ):
            runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            assert (
                store.load(runtime)["inference"]["reason"].item()
                == "unknown_interval"
            )
            read.return_value = (_revision(1, "乙"),)
            creation = _membership().with_columns(
                pl.lit(1).alias("talk_revision"),
            )
            collector.refresh_b_listings(site, creation, runtime=runtime)
            collector.refresh_b_listings(site, creation, runtime=runtime)
            assert (
                store.load(runtime)["inference"]["reason"].item()
                == "earliest_visible"
            )
            assert read.call_count == 2

    @staticmethod
    def test_literal_parser_keeps_ambiguous_content_unknown() -> None:
        """Parse only one active and unambiguous project banner."""
        site = OfflineSite("zh", "wikipedia")
        for text, expected in (
            ("{{VG|class=B}}", "乙"),
            ("{{VG|class=乙}}", "乙"),
            ("{{VG|class=BL}}", "乙级列表"),
            ("{{VG|class=B|class=C}}", None),
            ("{{VG|class={{x}}}}", None),
            ("<!-- {{VG|class=B}} -->", "not-listed"),
            ("<nowiki>{{VG|class=B}}</nowiki>", "not-listed"),
            (None, None),
        ):
            assert banner_class(text, site, _ALIASES) == expected

    @staticmethod
    def test_chinese_classes_and_exact_project_redirects() -> None:
        """Resolve Chinese classes through exact verified redirects."""
        site = OfflineSite("zh", "wikipedia")
        profile = assessment_profile("zhwiki", "电子游戏")
        assert profile.resolve("乙", "class").code == "B"
        assert profile.resolve("乙級列表", "class").code == "BL"
        aliases = (*_ALIASES, *_PROJECT_REDIRECTS)
        shells = (
            *_SHELL_ALIASES,
            "Template:多个专题",
            "Template:多個專題",
        )
        for text, expected in (
            ("{{电子游戏专题|class=乙}}", "乙"),
            ("{{模板:電子遊戲專題|class=乙}}", "乙"),
            ("{{WikiProject Video Games|class=乙}}", "乙"),
            ("{{WPBS|class=丙|1={{WPVG|class=乙}}}}", "乙"),
            ("{{多个专题|class=乙|1={{电子游戏专题}}}}", "乙"),
            ("{{WPBS|CLASS=乙}}{{WPVG|class=}}", "乙"),
            ("{{電子遊戲專題}}{{多個專題|class=乙}}", "乙"),
            ("{{WPBS|class=乙級列表|1={{WPVG}}}}", "乙级列表"),
            ("{{WPBS|class=乙}}{{WPVG|class=丙}}", "丙"),
            ("{{wIkIpRoJeCt ViDeO gAmEs|class=乙}}", "not-listed"),
        ):
            assert (
                banner_class(
                    text,
                    site,
                    aliases,
                    shell_aliases=shells,
                )
                == expected
            ), text

    @staticmethod
    def test_new_verified_alias_reopens_an_unchanged_cached_head() -> None:
        """Recheck unsupported evidence after alias metadata changes."""
        aliases = list(_ALIASES)
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                collector,
                "template_aliases",
                side_effect=lambda _site, title: (
                    _SHELL_ALIASES
                    if title.endswith("banner shell")
                    else tuple(aliases)
                ),
            ) as metadata,
            patch.object(
                collector,
                "read_talk_revisions",
                return_value=(
                    HistoricalRevision(
                        5,
                        4,
                        _NOW + dt.timedelta(seconds=5),
                        "{{WPBS|class=乙|1={{电子游戏专题}}}}",
                    ),
                    _revision(4, "丙"),
                ),
            ) as read,
        ):
            runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            before = store.load(runtime)["inference"].row(0, named=True)
            assert before["reason"] == "cohort_mismatch"
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            read.assert_called_once()
            aliases.append("Template:电子游戏专题")
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            after = store.load(runtime)["inference"].row(0, named=True)
            assert read.call_count == 2
            assert after["parser_version"] != before["parser_version"]
            assert after["scanned_talk_rev_id"] == 5
            assert after["banner_entry_talk_rev_id"] == 5
            assert after["banner_prior_talk_rev_id"] == 4
            assert after["banner_transition_status"] == "proven"
            assert {call.args[1] for call in metadata.call_args_list} == {
                "Template:WikiProject Video games",
                "Template:WikiProject banner shell",
            }

    @staticmethod
    def test_header_shell_inheritance_obeys_verified_child_precedence() -> (
        None
    ):
        """Read verified header inheritance only."""
        site = OfflineSite("zh", "wikipedia")
        cases = (
            ("{{WPBS|class=B|1={{VG|importance=Low}}}}", "乙"),
            ("{{WPBS|class=BL|1={{VG|class=}}}}", "乙级列表"),
            ("{{WPBS|CLASS=B}}{{VG}}", "乙"),
            ("{{WPBS|class=B}}{{VG|class=C}}", "丙"),
            ("{{WPBS|class=C}}{{VG|class=B}}", "乙"),
            ("{{WPBS|class=B}}{{VG|class=¬}}", "乙"),
            ("{{WPBS|class=B}}{{VG|class=no_input}}", "乙"),
            ("{{:Template:VG|class=B}}", "乙"),
            ("{{:Template:WPBS|class=BL}}{{:Template:VG}}", "乙级列表"),
            ("{{VG}}\n== Later ==\n{{WPBS|class=B}}", None),
            ("{{VG}}{{DYKEntry/archive}}{{WPBS|class=B}}", None),
            ("<!-- {{WPBS|class=B}} -->{{VG}}", None),
            ("<nowiki>{{WPBS|class=B}}</nowiki>{{VG}}", None),
            ("{{WPBS|class=B}}{{WPBS|class=B}}{{VG}}", None),
            ("{{WPBS|class=B|class=C}}{{VG}}", None),
            ("{{WPBS|class={{x}}}}{{VG}}", None),
            ("{{WPBS|class=B}}{{VG|class={{x}}}}", None),
            ("{{WPBS|class=B}}{{VG|class=B|class=C}}", None),
            ("{{WPBS|class=B}}{{Other project}}", "not-listed"),
            ("{{WPBS|class=B}}{{VG {{subst:Bckeck|y|y}}}}", None),
        )
        for text, expected in cases:
            assert (
                banner_class(
                    text,
                    site,
                    _ALIASES,
                    shell_aliases=_SHELL_ALIASES,
                )
                == expected
            ), text

    @staticmethod
    def test_shell_source_proves_a_transition_from_the_prior_class() -> None:
        """Prove a shell-style head in one batched history read."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                collector,
                "template_aliases",
                side_effect=lambda _site, title: (
                    _SHELL_ALIASES
                    if title.endswith("banner shell")
                    else _ALIASES
                ),
            ),
            patch.object(
                collector,
                "read_talk_revisions",
                return_value=(
                    HistoricalRevision(
                        5,
                        4,
                        _NOW + dt.timedelta(seconds=5),
                        "{{WPBS|class=B|1={{VG|importance=Low}}}}",
                    ),
                    _revision(4, "丙"),
                ),
            ) as read,
        ):
            runtime = Path(temporary)
            site = OfflineSite("zh", "wikipedia")
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            row = store.load(runtime)["inference"].row(0, named=True)
            assert row["banner_transition_status"] == "proven"
            assert row["banner_entry_talk_rev_id"] == 5
            assert row["banner_prior_talk_rev_id"] == 4
            read.assert_called_once()

    @staticmethod
    def test_stable_unknown_heads_only_retry_when_their_identity_changes() -> (
        None
    ):
        """Cache unsupported evidence until head or parser changes."""
        for text in ("{{VG|class={{x}}}}", "{{VG|class=C}}"):
            with (
                TemporaryDirectory() as temporary,
                patch.object(
                    collector,
                    "template_aliases",
                    return_value=_ALIASES,
                ),
                patch.object(
                    collector,
                    "read_talk_revisions",
                    return_value=(HistoricalRevision(5, 4, _NOW, text),),
                ) as read,
            ):
                runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
                collector.refresh_b_listings(
                    site,
                    _membership(),
                    runtime=runtime,
                )
                collector.refresh_b_listings(
                    site,
                    _membership(),
                    runtime=runtime,
                )
                read.assert_called_once()
                read.return_value = (HistoricalRevision(6, 5, _NOW, text),)
                changed = _membership().with_columns(
                    pl.lit(6).alias("talk_revision"),
                )
                collector.refresh_b_listings(site, changed, runtime=runtime)
                assert read.call_count == 2
                with patch.object(collector, "CONTRACT", "changed-parser"):
                    collector.refresh_b_listings(
                        site,
                        changed,
                        runtime=runtime,
                    )
                assert read.call_count == 3

    @staticmethod
    def test_transient_failures_and_budget_deferrals_remain_retryable() -> (
        None
    ):
        """Retry transient failures and bounded deferrals."""
        for first in (TimeoutError, ()):
            with (
                TemporaryDirectory() as temporary,
                patch.object(
                    collector,
                    "template_aliases",
                    return_value=_ALIASES,
                ),
                patch.object(
                    collector,
                    "read_talk_revisions",
                    side_effect=[
                        first,
                        (_revision(5, "乙"), _revision(4, "丙")),
                    ],
                ) as read,
            ):
                runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
                collector.refresh_b_listings(
                    site,
                    _membership(),
                    runtime=runtime,
                )
                collector.refresh_b_listings(
                    site,
                    _membership(),
                    runtime=runtime,
                )
                assert read.call_count == 2
                assert (
                    store.load(runtime)["inference"][
                        "banner_transition_status"
                    ].item()
                    == "proven"
                )


class HistoryBatchTests(TestCase):
    """Fetch multiple exact talk bodies in one supported SDK batch."""

    @staticmethod
    def test_batch_uses_only_revisions_newer_than_cached_head() -> None:
        """Bound the source by IDs even for same-second revisions."""
        page = Mock(pageid=2)
        page.revisions.return_value = [Mock(revid=10)]
        page._revisions = {}
        site = Mock()

        def load(_page: object, **_options: object) -> None:
            """Populate the SDK result cache with one loaded batch."""
            for identifier, parent in ((10, 8), (8, 5)):
                revision = Revision(
                    revid=identifier,
                    parentid=parent,
                    timestamp=_NOW.isoformat(),
                    contentmodel="wikitext",
                    **{"*": "{{VG|class=B}}"},
                )
                page._revisions[identifier] = revision

        site.loadrevisions.side_effect = load
        with patch.object(history_source.pywikibot, "Page", return_value=page):
            revisions = history_source.read_talk_revisions(
                site,
                page_id=2,
                title="Talk:Game",
                selected_revision=10,
                stop_revision=5,
                budget=Mock(),
                limit=20,
            )
        assert [revision.revision_id for revision in revisions] == [10, 8]
        site.loadrevisions.assert_called_once_with(
            page,
            content=True,
            startid=10,
            endid=6,
            total=20,
        )
        page.get_revision.assert_not_called()


class ListingReplicaTests(TestCase):
    """Use bounded read-only page/time queries for article cutoffs."""

    @staticmethod
    def test_cutoff_order_visibility_and_same_second_edits() -> None:
        """Retain the latest visible timestamp and ambiguity."""
        replica = Mock()
        replica.query.return_value = pl.DataFrame({
            "revision_id": [20, 19],
            "timestamp": ["20261112000000", "20261112000000"],
        }).lazy()
        with patch.object(
            replica_source.Replica,
            "from_site",
            return_value=replica,
        ):
            assert replica_source.listing_revision(Mock(), 1, _NOW) == (
                20,
                _NOW,
                True,
            )
        statement = str(
            replica.query.call_args.args[0].compile(
                compile_kwargs={"literal_binds": True},
            ),
        )
        assert "revision.rev_page = 1" in statement
        assert "revision.rev_timestamp <= '20261112000000'" in statement
        assert "revision.rev_deleted & 1" in statement
        assert (
            "ORDER BY revision.rev_timestamp DESC, revision.rev_id DESC"
            in statement
        )
        assert "LIMIT 2" in statement

    @staticmethod
    def test_missing_visible_revision_stays_unknown() -> None:
        """Leave absent cutoff evidence pending."""
        replica = Mock()
        replica.query.return_value = pl.DataFrame(
            schema={"revision_id": pl.Int64, "timestamp": pl.String},
        ).lazy()
        with patch.object(
            replica_source.Replica,
            "from_site",
            return_value=replica,
        ):
            assert replica_source.listing_revision(Mock(), 1, _NOW) is None

    @staticmethod
    def test_highlights_are_hourly_and_newsletter_remains_daily() -> None:
        """Keep independently timed website products separate."""
        assert WEBSITE_REFRESH_INTERVALS["recognized"] == 3600
        assert WEBSITE_REFRESH_INTERVALS["newsletter"] == 86400
