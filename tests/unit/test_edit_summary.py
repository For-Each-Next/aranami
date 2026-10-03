"""Verify shared edit-summary budgeting and complete link truncation."""

# Preserve the user's minute and second prime symbols.
# ruff: file-ignore[ambiguous-unicode-character-string]
import pickle  # ruff: ignore[suspicious-pickle-import]
from copy import copy, deepcopy
from dataclasses import asdict
from unittest import TestCase
from unittest.mock import Mock

from aranami.jobs import ProposedEdit
from aranami.support.edit_summary import MAX_EDIT_SUMMARY_BYTES, EditSummary

_CHINESE_CHARACTER_BYTES = 3
_EXAMPLE_DURATION = 1313.95
_EXAMPLE_SUFFIX = "Executed in 21′53.95″."


class TestEditSummary(TestCase):
    """Check Unicode byte limits and useful truncated descriptions."""

    @staticmethod
    def test_membership_counts_and_both_change_groups() -> None:
        """Put the current total before additions and removals."""
        summary = EditSummary.membership(
            ["New", "Another"],
            ["Old"],
            1234,
        ).render()
        assert summary == (
            "1,234 items total. Added «[[New]]», «[[Another]]»; "
            "removed «[[Old]]»."
        )
        assert EditSummary.membership([], [], 1).render() == "1 item total."
        assert EditSummary.membership([], ["Old"], 0).render() == (
            "0 items total. Removed «[[Old]]»."
        )

    @staticmethod
    def test_multibyte_links_are_shortened_as_whole_mentions() -> None:
        """Retain totals and both change counts for oversized titles."""
        summary = EditSummary.membership(
            ["遊戲" * 100],
            ["舊" * 100],
            1234,
        ).render()
        assert summary == "1,234 items total. Added 1 more; removed 1 more."
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert "[[" not in summary

    @staticmethod
    def test_exact_budget_and_hard_cap() -> None:
        """Count encoded bytes, including Chinese text."""
        assert len("遊".encode()) == _CHINESE_CHARACTER_BYTES
        exact = "Date. " + "遊" * 82 + "ab."
        assert len(exact.encode("utf-8")) == MAX_EDIT_SUMMARY_BYTES
        assert EditSummary("Date.").append("遊" * 82 + "ab.").render() == exact
        oversized = (
            EditSummary("Date.", max_bytes=500).append("遊" * 83).render()
        )
        assert oversized == "Date."

    @staticmethod
    def test_later_short_clause_survives_oversized_earlier_clause() -> None:
        """Drop a long leader link and preserve a shorter leader."""
        summary = (
            EditSummary("Updated records for 2026-10-02.")
            .append(f"Daily leader «[[{'遊戲' * 100}]]».")
            .append("Weekly leader «[[Game]]».")
            .render()
        )
        assert summary == (
            "Updated records for 2026-10-02. Weekly leader «[[Game]]»."
        )

    @staticmethod
    def test_custom_small_budget_is_utf8_safe() -> None:
        """Cap small budgets without splitting a code point."""
        assert EditSummary("遊戲", max_bytes=4).render() == "遊"
        assert not EditSummary("Items.", max_bytes=0).render()

    @staticmethod
    def test_execution_time_uses_measured_minutes_and_hundredths() -> None:
        """Omit zero minutes and carry rounded seconds into minutes."""
        assert (
            EditSummary.with_execution_time(
                EditSummary("1 item total.").render(),
                _EXAMPLE_DURATION,
            )
            == f"1 item total. {_EXAMPLE_SUFFIX}"
        )
        assert EditSummary.with_execution_time("summary", 0) == (
            "summary Executed in 0.00″."
        )
        assert EditSummary.with_execution_time("summary", 5.2) == (
            "summary Executed in 5.20″."
        )
        assert EditSummary.with_execution_time("summary", 7.12) == (
            "summary Executed in 7.12″."
        )
        assert EditSummary.with_execution_time("summary", 59.999) == (
            "summary Executed in 1′00.00″."
        )
        assert EditSummary.with_execution_time("summary", 3600) == (
            "summary Executed in 60′00.00″."
        )

    @staticmethod
    def test_timing_budget_preserves_total_and_change_groups() -> None:
        """Rebudget original mentions to keep the mandatory suffix."""
        original = EditSummary.membership(
            ["遊" * 30],
            ["舊" * 30],
            1234,
        ).render()
        assert "more" not in original
        summary = EditSummary.with_execution_time(
            original,
            _EXAMPLE_DURATION,
        )
        assert summary.startswith("1,234 items total. Added ")
        assert "; removed " in summary
        assert "more" in summary
        assert summary.endswith(_EXAMPLE_SUFFIX)
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert summary.count("[[") == summary.count("]]")
        assert summary.count("«") == summary.count("»")
        assert "more" not in original

    @staticmethod
    def test_timing_omits_whole_oversized_leader_clause() -> None:
        """Keep the date and duration when a leader no longer fits."""
        original = (
            EditSummary("Updated records for 5 May 2025.")
            .append(f"Daily leader «[[{'遊' * 61}]]».")
            .render()
        )
        assert "Daily leader" in original
        summary = EditSummary.with_execution_time(
            original,
            _EXAMPLE_DURATION,
        )
        assert summary == (
            f"Updated records for 5 May 2025. {_EXAMPLE_SUFFIX}"
        )

    @staticmethod
    def test_plain_input_preserves_links_and_replaces_old_suffix() -> None:
        """Handle caller strings and keep only the latest duration."""
        original = f"2 items total. Added «[[Short]]», «[[{'遊戲' * 100}]]»."
        first = EditSummary.with_execution_time(original, _EXAMPLE_DURATION)
        assert first == (
            f"2 items total. Added «[[Short]]». {_EXAMPLE_SUFFIX}"
        )
        for previous in (
            first,
            first.replace("Executed in", "executed in"),
            first.replace(_EXAMPLE_SUFFIX, "Executed in 0′05.20″."),
            first.replace(_EXAMPLE_SUFFIX, "Executed in 5.20″."),
        ):
            second = EditSummary.with_execution_time(previous, 5.2)
            assert second == (
                "2 items total. Added «[[Short]]». Executed in 5.20″."
            )
            assert second.count("Executed in") == 1
            assert len(second.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_plain_input_can_fill_the_exact_final_byte_budget() -> None:
        """Include the complete suffix in the final size calculation."""
        summary = EditSummary.with_execution_time(
            "x" * MAX_EDIT_SUMMARY_BYTES,
            _EXAMPLE_DURATION,
        )
        assert summary.endswith(_EXAMPLE_SUFFIX)
        assert len(summary.encode("utf-8")) == MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_plain_input_keeps_complete_bilingual_title_delimiters() -> None:
        """Keep linked titles balanced for new and legacy summaries."""
        for opening, closing in (("«", "»"), ("“", "”")):
            retained = (
                f"2 items total. Added {opening}[[:en:Short]]{closing} "
                f"({opening}[[短]]{closing})"
            )
            original = (
                f"{retained}, {opening}[[:en:{'Long title' * 100}]]{closing} "
                f"({opening}[[長]]{closing})."
            )
            summary = EditSummary.with_execution_time(
                original,
                _EXAMPLE_DURATION,
            )
            assert summary == f"{retained}. {_EXAMPLE_SUFFIX}"
            assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_rendered_summaries_preserve_normal_string_copy_behavior() -> None:
        """Keep complete clauses through copy and serialization."""
        original = EditSummary.membership(
            ["遊" * 30],
            ["舊" * 30],
            1234,
        ).render()
        proposal = ProposedEdit(Mock(), "Target", "text", original)
        # The pickle contains only the summary created above.
        restored = pickle.loads(pickle.dumps(original))  # ruff: ignore[suspicious-pickle-usage]
        for preserved in (copy(original), deepcopy(original), restored):
            assert preserved == original
            assert EditSummary.with_execution_time(
                preserved,
                _EXAMPLE_DURATION,
            ) == EditSummary.with_execution_time(original, _EXAMPLE_DURATION)
        assert asdict(proposal)["summary"] == original
