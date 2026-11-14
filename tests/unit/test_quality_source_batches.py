"""Verify SQL-first quality acquisition and mixed revision batches."""

# Exact quantities and private batch boundaries are test contracts.
# ruff: file-ignore[magic-value-comparison,private-member-access,pytest-unittest-raises-assertion]

import datetime as dt
import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.articles import b_listings as listing_store
from shared.assessment_profiles import assessment_profile
from shared.sources import assessment_history, wikiwho
from shared.sources.assessment_history import HistoryBudget
from shared.sources.quarry import assessment as sql
from shared.sources.revisions import RevisionContent
from tasks.articles import b_listings, quality_analysis

_NOW = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)


class QualitySourceBatchTests(TestCase):
    """Batch source boundaries and reuse existing proof facts."""

    @staticmethod
    def test_current_sql_revision_skips_page_info_and_latest_queries() -> None:
        """Measure known revision contents without metadata rereads."""
        site = OfflineSite("zh", "wikipedia")
        content = RevisionContent(
            1,
            "Game",
            101,
            _NOW,
            "a" * 40,
            "PlayStation 5",
        )
        with (
            patch.object(
                quality_analysis,
                "read_revision_contents",
                return_value={101: content},
            ) as read,
            patch.object(
                quality_analysis,
                "extract_prose",
                return_value="PlayStation 5",
            ),
            patch(
                "shared.sources.wiki.pagegenerators.PagesFromPageidGenerator",
            ) as info,
        ):
            rows = quality_analysis._batch_measurements(
                site,
                ({"page_id": 1, "article_revision_id": 101, "grade": "FA"},),
            )
        read.assert_called_once_with(site, [101])
        info.assert_not_called()
        assert rows[0]["prose_characters"] == 4
        assert rows[0]["source_bytes"] == len("PlayStation 5")

    @staticmethod
    def test_importance_colors_keep_the_exact_pre_theme_parser_identity() -> (
        None
    ):
        """Preserve the exact hash of old cached scan states."""
        current = asdict(assessment_profile("zhwiki", "电子游戏"))
        old = deepcopy(current)
        for dimension in ("importance", "compatibility_importance"):
            for label in old[dimension]:
                label["color"] = None
                label["color_variants"] = ()
        contract = b_listings.CONTRACT
        legacy = hashlib.sha256(
            json.dumps([contract, ["Template:VG"], [], old]).encode(),
        ).hexdigest()
        assert (
            b_listings._parser_identity(
                contract,
                (["Template:VG"], [], current),
            )
            == legacy
        )
        assert (
            b_listings._parser_identity(contract, (["Template:VG"], [], old))
            == legacy
        )

    @staticmethod
    def test_fifty_talks_share_two_exact_content_requests() -> None:
        """Batch predecessors across talks in two API calls."""
        pending = []
        metadata = []
        contents = {}
        for index in range(50):
            selected = 1000 + index * 100
            pending.append({
                "page_id": index + 1,
                "talk_page_id": index + 101,
                "scanned_talk_rev_id": selected,
                "class": "乙",
                "parser_version": "fixture",
                "title": f"Game {index}",
                "talk_title": f"Talk:Game {index}",
                "observed_at": _NOW,
            })
            for offset in range(50):
                revision = selected - offset
                grade = "B" if offset == 0 else "C"
                stamp = _NOW - dt.timedelta(seconds=selected - revision)
                metadata.append({
                    "page_id": index + 101,
                    "revision_id": revision,
                    "parent_id": revision - 1,
                    "timestamp": stamp,
                    "text_visible": True,
                })
                contents[revision] = RevisionContent(
                    index + 101,
                    f"Talk:Game {index}",
                    revision,
                    stamp,
                    "a" * 40,
                    "{{VG|class=" + grade + "}}",
                    revision - 1,
                )
        frame = pl.DataFrame(pending, schema=listing_store.INFERENCE_SCHEMA)
        with (
            patch.object(
                assessment_history,
                "talk_revision_rows",
                return_value=pl.DataFrame(metadata),
            ) as lookup,
            patch.object(
                assessment_history,
                "read_revision_contents",
                side_effect=lambda _site, ids: {
                    key: contents[key] for key in ids
                },
            ) as fetch,
        ):
            rows = b_listings._attempt_batch(
                OfflineSite("zh", "wikipedia"),
                frame,
                ("Template:VG",),
                HistoryBudget(),
                (),
            )
        assert len(rows) == 50
        assert all(row["banner_transition_status"] == "proven" for row in rows)
        assert [len(call.args[1]) for call in fetch.call_args_list] == [50, 50]
        assert len(lookup.call_args.args[1]) == 50

    @staticmethod
    def test_content_budget_preserves_unread_talk_priority() -> None:
        """Keep the unread fifty-first talk first for the next pass."""
        members, metadata, contents = [], [], {}
        for page_id in range(1, 52):
            selected = 1000 + page_id * 100
            members.append({
                "page_id": page_id,
                "talk_page_id": page_id + 100,
                "scanned_talk_rev_id": selected,
                "class": "乙",
                "parser_version": "fixture",
                "title": f"Game {page_id}",
                "talk_title": f"Talk:Game {page_id}",
                "observed_at": _NOW,
            })
            for offset in range(2):
                revision = selected - offset
                stamp = _NOW - dt.timedelta(seconds=offset)
                metadata.append({
                    "page_id": page_id + 100,
                    "revision_id": revision,
                    "parent_id": revision - 1,
                    "timestamp": stamp,
                    "text_visible": True,
                })
                contents[revision] = RevisionContent(
                    page_id + 100,
                    f"Talk:Game {page_id}",
                    revision,
                    stamp,
                    "a" * 40,
                    "{{VG|class=B}}",
                    revision - 1,
                )
        frame = pl.DataFrame(members, schema=listing_store.INFERENCE_SCHEMA)
        budget = Mock(admit=Mock(side_effect=[True, True, False]))
        with (
            patch.object(
                assessment_history,
                "talk_revision_rows",
                return_value=pl.DataFrame(metadata),
            ),
            patch.object(
                assessment_history,
                "read_revision_contents",
                side_effect=lambda _site, ids: {
                    key: contents[key] for key in ids
                },
            ) as read,
        ):
            rows = b_listings._attempt_batch(
                OfflineSite("zh", "wikipedia"),
                frame,
                ("Template:VG",),
                budget,
                (),
            )
        assert len(rows) == 50
        assert 51 not in {row["page_id"] for row in rows}
        assert [len(call.args[1]) for call in read.call_args_list] == [50]
        rotated = pl.concat([frame.tail(1), frame.head(50)])
        with (
            patch.object(
                assessment_history,
                "talk_revision_rows",
                return_value=pl.DataFrame(metadata),
            ),
            patch.object(
                assessment_history,
                "read_revision_contents",
                side_effect=lambda _site, ids: {
                    key: contents[key] for key in ids
                },
            ) as read,
        ):
            rows = b_listings._attempt_batch(
                OfflineSite("zh", "wikipedia"),
                rotated,
                ("Template:VG",),
                Mock(admit=Mock(side_effect=[True, True, False])),
                (),
            )
        assert read.call_args.args[1][0] == 6100
        assert 51 in {row["page_id"] for row in rows}
        assert 50 not in {row["page_id"] for row in rows}
        with (
            patch.object(assessment_history, "talk_revision_rows") as lookup,
            patch.object(assessment_history, "read_revision_contents") as read,
        ):
            rows = b_listings._attempt_batch(
                OfflineSite("zh", "wikipedia"),
                frame,
                ("Template:VG",),
                Mock(admit=Mock(return_value=False)),
                (),
            )
        assert not rows
        lookup.assert_not_called()
        read.assert_not_called()

    @staticmethod
    def test_sql_listing_cutoffs_batch_fifty_and_keep_same_second_flag() -> (
        None
    ):
        """Read indexed visible predecessors for each exact cutoff."""
        populated = pl.DataFrame({
            "page_id": [1, 1],
            "revision_id": [11, 10],
            "timestamp": ["20261101000000", "20261101000000"],
        })
        empty = pl.DataFrame(
            schema={
                "page_id": pl.Int64,
                "revision_id": pl.Int64,
                "timestamp": pl.String,
            },
        )
        replica = Mock()
        replica.query.side_effect = [
            Mock(collect=Mock(return_value=populated)),
            Mock(collect=Mock(return_value=empty)),
        ]
        with patch.object(sql.Replica, "from_site", return_value=replica):
            selected = sql.listing_revisions(
                Mock(),
                dict.fromkeys(range(1, 52), _NOW),
            )
        assert replica.query.call_count == 2
        assert selected[1] == (11, _NOW, True)
        assert selected[51] is None
        query = str(replica.query.call_args_list[0].args[0])
        assert "rev_deleted" in query
        assert "LIMIT" in query
        assert "UNION ALL" in query

    @staticmethod
    def test_listing_scope_batches_sql_metadata_and_body() -> None:
        """Keep listing-day semantics without scalar metadata reads."""
        cutoff = _NOW + dt.timedelta(days=1)
        content = RevisionContent(1, "Game", 10, _NOW, "a" * 40, "body", 9)
        site = Mock()
        with (
            patch.object(
                wikiwho,
                "first_revisions",
                return_value={1: (1, _NOW - dt.timedelta(days=10))},
            ) as first,
            patch.object(
                wikiwho,
                "listing_revisions",
                return_value={1: (10, _NOW, False)},
            ) as last,
            patch.object(
                wikiwho,
                "read_revision_contents",
                return_value={10: content},
            ) as body,
            patch.object(wikiwho.pywikibot, "Page") as page,
            wikiwho.preloaded_listing_sources(site, [(1, cutoff)]),
        ):
            assert wikiwho.listing_metadata(site, "Game", 1, cutoff) == (
                10,
                1,
                _NOW - dt.timedelta(days=10),
            )
            assert wikiwho.revision_text(site, "Game", 1, 10) == "body"
            page.assert_not_called()
        first.assert_called_once_with(site, [1])
        last.assert_called_once_with(
            site,
            {1: cutoff - dt.timedelta(microseconds=1)},
        )
        body.assert_called_once_with(site, {10: 1})

    def test_preloaded_attribution_texts_never_repeat_scalar_page_reads(
        self,
    ) -> None:
        """Reuse visible and unavailable outcomes without fallback."""
        content = RevisionContent(1, "Game", 10, _NOW, "a" * 40, "body", 9)
        site = Mock()
        with (
            patch.object(
                wikiwho,
                "read_revision_contents",
                return_value={10: content, 20: None},
            ) as fetch,
            patch.object(wikiwho.pywikibot, "Page") as page,
            wikiwho.preloaded_revision_sources(site, {10: 1, 20: 2}),
        ):
            assert wikiwho.revision_text(site, "Game", 1, 10) == "body"
            assert (
                wikiwho.revision_source(site, "Game", 1, 10).parent_revision
                == 9
            )
            with self.assertRaisesRegex(ValueError, "unavailable"):
                wikiwho.revision_text(site, "Other", 2, 20)
            page.assert_not_called()
        fetch.assert_called_once_with(site, {10: 1, 20: 2})
