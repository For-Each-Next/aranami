"""Verify assessment links retain captured revision evidence."""

# Exercise internal fact preparation with the existing offline fixtures.
# ruff: file-ignore[private-member-access]

from unittest.mock import patch

import polars as pl
import test_assessment_log as fixtures

from tasks.zhwiki import assessment_log as source


class TestAssessmentSnapshotReferences(fixtures._AssessmentLogFixture):
    """Keep later moves and edits out of a frozen daily comparison."""

    def test_captured_titles_and_revisions_need_no_current_metadata(
        self,
    ) -> None:
        """Link reassessments and departures to their captured facts."""
        old_members = fixtures._members(
            (1, "Former title", "C", "Low"),
            (2, "Departed", "B", "High"),
        )
        new_members = fixtures._members((1, "Captured title", "B", "High"))
        prior = fixtures._replica_rows(old_members).with_columns(
            (pl.col("page_id") * 100).alias("rev_id"),
            (pl.col("page_id") * 100 + 1).alias("talk_page_rev"),
        )
        current = fixtures._replica_rows(new_members).with_columns(
            pl.lit(300, dtype=pl.Int64).alias("rev_id"),
            pl.lit(301, dtype=pl.Int64).alias("talk_page_rev"),
        )
        changes = source.compare_assessments(
            source._assessment_members(prior),
            source._assessment_members(current),
        )
        with patch.object(
            source,
            "page_metadata",
            side_effect=AssertionError("Live read"),
        ) as lookup:
            resolved, metadata = source._resolve_changes(
                self.site,
                changes,
                current,
                prior,
                old_members,
            )
        lookup.assert_not_called()
        reassessed = resolved.filter(pl.col("kind") == "reassessed")
        removed = resolved.filter(pl.col("kind") == "removed")
        assert reassessed["title"].to_list() == ["Captured title"]
        assert removed["old_title"].to_list() == ["Departed"]
        references = source._references(metadata, metadata.clear(), resolved)
        assert references[1][1:] == (300, 301)
        assert references[2][1:] == (200, 201)
