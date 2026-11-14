"""Verify latest B ranges, durable scan progress and Replica cutoffs."""

# Explicit source IDs describe the source evidence boundary.
# ruff: file-ignore[magic-value-comparison,private-member-access]

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from pywikibot.page import Revision
from pywikibot.site import Namespace, NamespacesDict
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

    def setUp(self) -> None:
        """Exercise SDK fallback with exact-parent fixtures."""
        self.enterContext(
            patch.object(
                collector,
                "read_talk_revision_batches",
                side_effect=OSError("Replica unavailable"),
            ),
        )

    @staticmethod
    def test_colors_preserve_completed_and_partial_proofs() -> None:
        """Keep historical evidence across importance color changes."""
        profile = assessment_profile("zhwiki", "电子游戏")
        old = replace(
            profile,
            importance=tuple(
                replace(label, color=None, color_variants=())
                for label in profile.importance
            ),
            compatibility_importance=tuple(
                replace(label, color=None, color_variants=())
                for label in profile.compatibility_importance
            ),
        )
        for revisions in (
            (_revision(5, "B"), _revision(4, "C")),
            (_revision(5, "B"), HistoricalRevision(4, 3, _NOW, None)),
        ):
            with (
                TemporaryDirectory() as directory,
                patch.object(
                    collector,
                    "template_aliases",
                    return_value=_ALIASES,
                ),
                patch.object(
                    collector,
                    "read_talk_revisions",
                    return_value=revisions,
                ) as sdk,
            ):
                runtime = Path(directory)
                site = OfflineSite("zh", "wikipedia")
                with patch.object(
                    collector,
                    "assessment_profile",
                    return_value=old,
                ):
                    collector.refresh_b_listings(
                        site,
                        _membership(),
                        runtime=runtime,
                    )
                before = store.load(runtime)["inference"].row(0, named=True)
                sdk.reset_mock()
                with patch.object(
                    collector,
                    "read_talk_revision_batches",
                ) as batch:
                    collector.refresh_b_listings(
                        site,
                        _membership(),
                        runtime=runtime,
                    )
                after = store.load(runtime)["inference"].row(0, named=True)
                assert after["parser_version"] == before["parser_version"]
                assert after["reason"] == before["reason"]
                assert (
                    after["banner_entry_talk_rev_id"]
                    == before["banner_entry_talk_rev_id"]
                )
                sdk.assert_not_called()
                batch.assert_not_called()

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
        site._namespaces = NamespacesDict({
            **dict(site.namespaces.items()),
            10: Namespace(
                10,
                canonical_name="Template",
                custom_name="模板",
                aliases=["模版"],
                case="first-letter",
            ),
        })
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
    def test_completed_old_parser_proofs_reuse_identical_metadata() -> None:
        """Keep completed dates across the parser upgrade."""
        for revisions, reason in (
            (
                (_revision(5, "乙"), _revision(4, "丙")),
                "contiguous_transition",
            ),
            (
                (HistoricalRevision(5, 0, _NOW, "{{VG|class=B}}"),),
                "earliest_visible",
            ),
        ):
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
                    return_value=revisions,
                ) as read,
            ):
                runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
                with patch.object(
                    collector,
                    "CONTRACT",
                    collector._COMPLETED_PROOF_PREVIOUS_CONTRACT,
                ):
                    collector.refresh_b_listings(
                        site,
                        _membership(),
                        runtime=runtime,
                    )
                before = store.load(runtime)["inference"].row(0, named=True)
                collector.refresh_b_listings(
                    site,
                    _membership(),
                    runtime=runtime,
                )
                after = store.load(runtime)["inference"].row(0, named=True)
                read.assert_called_once()
                assert before["parser_version"] != after["parser_version"]
                assert after["reason"] == before["reason"] == reason
                for key in (
                    "banner_entry_talk_rev_id",
                    "banner_entry_timestamp",
                    "banner_prior_talk_rev_id",
                ):
                    assert after[key] == before[key]

    @staticmethod
    def test_old_unknown_parser_proof_reopens_at_unchanged_head() -> None:
        """Reopen the stopped scan when parser semantics change."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(collector, "template_aliases", return_value=_ALIASES),
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
            with patch.object(
                collector,
                "CONTRACT",
                collector._COMPLETED_PROOF_PREVIOUS_CONTRACT,
            ):
                collector.refresh_b_listings(
                    site,
                    _membership(),
                    runtime=runtime,
                )
            assert (
                store.load(runtime)["inference"]["reason"].item()
                == "unknown_interval"
            )
            read.return_value = (
                _revision(5, "乙"),
                HistoricalRevision(4, 3, _NOW, "{{VG}}"),
            )
            collector.refresh_b_listings(site, _membership(), runtime=runtime)
            assert read.call_count == 2
            assert read.call_args.kwargs["start_revision"] == 5
            after = store.load(runtime)["inference"].row(0, named=True)
            assert after["banner_transition_status"] == "proven"
            assert after["banner_prior_talk_rev_id"] == 4

    @staticmethod
    def test_parser_migration_rejects_changed_alias_or_profile_metadata() -> (
        None
    ):
        """Do not transfer proof across unrelated metadata changes."""
        base_profile = assessment_profile("zhwiki", "电子游戏")
        for change in ("aliases", "shells", "profile"):
            aliases = list(_ALIASES)
            shells = list(_SHELL_ALIASES)
            with (
                TemporaryDirectory() as temporary,
                patch.object(
                    collector,
                    "template_aliases",
                    side_effect=lambda _site, title, vg=aliases, hdr=shells: (
                        tuple(
                            hdr if title.endswith("banner shell") else vg,
                        )
                    ),
                ),
                patch.object(
                    collector,
                    "read_talk_revisions",
                    return_value=(_revision(5, "乙"), _revision(4, "丙")),
                ) as read,
            ):
                runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
                with patch.object(
                    collector,
                    "CONTRACT",
                    collector._COMPLETED_PROOF_PREVIOUS_CONTRACT,
                ):
                    collector.refresh_b_listings(
                        site,
                        _membership(),
                        runtime=runtime,
                    )
                if change == "aliases":
                    aliases.append("Template:电子游戏专题")
                elif change == "shells":
                    shells.append("Template:多个专题")
                revised_profile = (
                    replace(base_profile, identifier="different-profile")
                    if change == "profile"
                    else base_profile
                )
                read.return_value = ()
                with patch.object(
                    collector,
                    "assessment_profile",
                    return_value=revised_profile,
                ):
                    collector.refresh_b_listings(
                        site,
                        _membership(),
                        runtime=runtime,
                    )
                assert read.call_count == 2
                assert read.call_args.kwargs["stop_revision"] is None
                assert (
                    store.load(runtime)["inference"][
                        "banner_entry_talk_rev_id"
                    ].item()
                    is None
                )

    @staticmethod
    def test_migrated_completed_proof_keeps_new_head_anchor() -> None:
        """Scan only new revisions above a compatible completed head."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(collector, "template_aliases", return_value=_ALIASES),
            patch.object(
                collector,
                "read_talk_revisions",
                return_value=(_revision(5, "乙"), _revision(4, "丙")),
            ) as read,
        ):
            runtime, site = Path(temporary), OfflineSite("zh", "wikipedia")
            with patch.object(
                collector,
                "CONTRACT",
                collector._COMPLETED_PROOF_PREVIOUS_CONTRACT,
            ):
                collector.refresh_b_listings(
                    site,
                    _membership(),
                    runtime=runtime,
                )
            read.return_value = (_revision(6, "乙"),)
            collector.refresh_b_listings(
                site,
                _membership().with_columns(pl.lit(6).alias("talk_revision")),
                runtime=runtime,
            )
            assert read.call_count == 2
            assert read.call_args.kwargs["stop_revision"] == 5
            after = store.load(runtime)["inference"].row(0, named=True)
            assert after["scanned_talk_rev_id"] == 6
            assert after["banner_entry_talk_rev_id"] == 5
            assert after["banner_prior_talk_rev_id"] == 4
            assert after["banner_transition_status"] == "proven"

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
            ("{{VG}}\n== Later ==\n{{WPBS|class=B}}", "未评"),
            ("{{VG}}{{DYKEntry/archive}}{{WPBS|class=B}}", "未评"),
            ("<!-- {{WPBS|class=B}} -->{{VG}}", "未评"),
            ("<nowiki>{{WPBS|class=B}}</nowiki>{{VG}}", "未评"),
            ("{{WPBS|class=B}}{{WPBS|class=B}}{{VG}}", None),
            ("{{WPBS|class=B|class=C}}{{VG}}", None),
            ("{{WPBS|class={{x}}}}{{VG}}", None),
            ("{{WPBS|class=B}}{{VG|class={{x}}}}", None),
            ("{{WPBS|class=B}}{{VG|class=B|class=C}}", None),
            ("{{WPBS|class=B}}{{Other project}}", "not-listed"),
            ("{{WPBS|class=B}}{{VG {{subst:Bckeck|y|y}}}}", "乙"),
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
    def test_explicit_classes_precede_the_complete_criteria_fallback() -> None:
        """Keep explicit classes ahead of the criteria fallback."""
        site = OfflineSite("zh", "wikipedia")
        criteria = "|b1=yes|b2=no|b3=y|b4=n|b5=|b6={{unknown}}"
        for text, expected in (
            ("{{VG}}", "未评"),
            ("{{VG|class=}}", "未评"),
            ("{{WPBS}}{{VG}}", "未评"),
            ("{{WPBS|class=}}{{VG|class=}}", "未评"),
            ("{{VG" + criteria + "}}", "未评"),
            ("{{VG|class=B" + criteria + "}}", "乙"),
            ("{{WPBS|class=B}}{{VG" + criteria + "}}", "乙"),
            ("{{WPBS|class=B}}{{VG|class=C" + criteria + "}}", "丙"),
            ("{{WPBS|class=C}}{{VG|class=B" + criteria + "}}", "乙"),
        ):
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
    def test_complete_criteria_accept_yes_y_and_mixed_case() -> None:
        """Require six literal positives when both grades are absent."""
        site = OfflineSite("zh", "wikipedia")
        positive = "|b1=yes|b2=y|b3=YES|b4=Y|b5= yes |b6= y "
        for flags in (
            "|b1=yes|b2=yes|b3=yes|b4=yes|b5=yes|b6=yes",
            "|b1=y|b2=y|b3=y|b4=y|b5=y|b6=y",
            positive,
        ):
            assert banner_class("{{VG" + flags + "}}", site, _ALIASES) == "乙"
            assert (
                banner_class(
                    "{{WPBS|class=}}{{VG|class=" + flags + "}}",
                    site,
                    _ALIASES,
                    shell_aliases=_SHELL_ALIASES,
                )
                == "乙"
            )
            assert (
                banner_class(
                    "{{WPBS|class=C}}{{VG" + flags + "}}",
                    site,
                    _ALIASES,
                    shell_aliases=_SHELL_ALIASES,
                )
                == "丙"
            )
            assert (
                banner_class(
                    "{{WPBS|class=B}}{{VG|class=C" + flags + "}}",
                    site,
                    _ALIASES,
                    shell_aliases=_SHELL_ALIASES,
                )
                == "丙"
            )
        for flags, expected in (
            ("", "未评"),
            ("|b1=y|b2=y|b3=y|b4=y|b5=y", "未评"),
            (positive.replace("b3=YES", "b3=no"), "未评"),
            (positive.replace("b3=YES", "b3={{unknown}}"), None),
            (positive + "|b3=yes", None),
        ):
            assert (
                banner_class(
                    "{{VG" + flags + "}}",
                    site,
                    _ALIASES,
                )
                == expected
            )

    @staticmethod
    def test_only_known_legacy_criteria_names_can_inherit_shell_class() -> (
        None
    ):
        """Accept Bckeck without accepting arbitrary dynamic names."""
        site = OfflineSite("zh", "wikipedia")
        cases = (
            ("{{WPBS|class=B}}{{VG {{subst:Bckeck|y|y|y|y|y|y}}}}", "乙"),
            ("{{WPBS|class=C}}{{VG {{subst:Bckeck|y|n}}|class=B}}", "乙"),
            ("{{WPBS|class=B}}{{VG {{subst:Bckeck|y|n}}|class={{x}}}}", None),
            ("{{WPBS|class=B}}{{VG {{unknown}}}}", None),
            ("{{WPBS|class=B}}{{VG {{subst:Bckeck|{{unknown}}}}}}", None),
            ("{{WPBS|class=B}}{{VG {{subst:Bckeck|y}} extra}}", None),
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
