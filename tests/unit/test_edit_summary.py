"""Verify shared edit-summary budgeting and complete link truncation."""

# Preserve wiki punctuation in quoted-title fixtures.
# ruff: file-ignore[ambiguous-unicode-character-string]
import datetime as dt
import pickle  # ruff: ignore[suspicious-pickle-import]
from copy import copy, deepcopy
from dataclasses import asdict
from unittest import TestCase
from unittest.mock import Mock

from updater.jobs import ProposedEdit
from updater.support.edit_summary import (
    MAX_EDIT_SUMMARY_BYTES,
    EditSummary,
    article_mention,
)

_CHINESE_CHARACTER_BYTES = 3
_EXAMPLE_SUFFIX = "([[toolforge:Aranami|Aranami]])"


class TestEditSummary(TestCase):
    """Check dates, membership punctuation, and UTF-8 byte limits."""

    @staticmethod
    def test_daily_date_uses_compact_english_months() -> None:
        """Keep report dates brief with unpadded days in every month."""
        for month, name in enumerate(
            (
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ),
            start=1,
        ):
            assert EditSummary.daily(dt.date(2026, month, 1)).render() == (
                f"Updated for 1 {name} 2026."
            )
        assert EditSummary.daily(dt.date(2026, 9, 15)).render() == (
            "Updated for 15 Sep 2026."
        )

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
            EditSummary
            .daily(dt.date(2026, 10, 2))
            .append(f"Daily leader «[[{'遊戲' * 100}]]».")
            .append("Weekly leader «[[Game]]».")
            .render()
        )
        assert summary == ("Updated for 2 Oct 2026. Weekly leader «[[Game]]».")

    @staticmethod
    def test_custom_small_budget_is_utf8_safe() -> None:
        """Cap small budgets without splitting a code point."""
        assert EditSummary("遊戲", max_bytes=4).render() == "遊"
        assert not EditSummary("Items.", max_bytes=0).render()

    @staticmethod
    def test_attribution_uses_exact_compact_link() -> None:
        """Reserve space for the exact configured attribution."""
        assert EditSummary.with_attribution("summary") == (
            f"summary {_EXAMPLE_SUFFIX}"
        )
        assert EditSummary.with_attribution("") == _EXAMPLE_SUFFIX
        assert "toolforge:Aranami" in _EXAMPLE_SUFFIX


class TestAttributedEditSummary(TestCase):
    """Preserve complete mentions when adding attribution."""

    @staticmethod
    def test_primary_and_companion_mentions_use_shared_punctuation() -> None:
        """Keep title parentheses inside primary and companion links."""
        assert article_mention("惡靈古堡：爆發夜") == "«[[惡靈古堡：爆發夜]]»"
        assert (
            article_mention(
                "Blade (Honkai)",
                interwiki="en",
                companion="刃 (崩壞：星穹鐵道)",
            )
            == "«[[:en:Blade (Honkai)]]» ([[刃 (崩壞：星穹鐵道)]])"
        )
        for companion in (None, ""):
            assert (
                article_mention(
                    "Blade (Honkai)",
                    interwiki="en",
                    companion=companion,
                )
                == "«[[:en:Blade (Honkai)]]»"
            )

    @staticmethod
    def test_bilingual_transition_uses_complete_byte_boundaries() -> None:
        """Rebudget a complete bilingual transition and attribution."""
        mention = (
            article_mention(
                "Blade (Honkai)",
                interwiki="en",
                companion="刃 (崩壞：星穹鐵道)",
            )
            + " (from GA)"
        )
        summary = EditSummary("1 item total.").add_group(
            "FA promoted ",
            [mention],
        )
        rendered = EditSummary.with_attribution(summary.render())
        assert (
            rendered
            == f"1 item total. FA promoted {mention}. {_EXAMPLE_SUFFIX}"
        )
        assert len(rendered.encode()) <= MAX_EDIT_SUMMARY_BYTES
        oversized = (
            EditSummary("1 item total.")
            .add_group(
                "FA promoted ",
                [
                    article_mention("遊" * 100, interwiki="en", companion="戲")
                    + " (from GA)",
                ],
            )
            .render()
        )
        assert EditSummary.with_attribution(oversized) == (
            f"1 item total. FA promoted 1 more. {_EXAMPLE_SUFFIX}"
        )

    @staticmethod
    def test_attribution_budget_preserves_total_and_change_groups() -> None:
        """Rebudget original mentions to keep the mandatory suffix."""
        original = EditSummary.membership(
            ["遊" * 30],
            ["舊" * 30],
            1234,
        ).render()
        assert "more" not in original
        summary = EditSummary.with_attribution(original)
        assert summary.startswith("1,234 items total. Added ")
        assert "; removed " in summary
        assert "more" in summary
        assert summary.endswith(_EXAMPLE_SUFFIX)
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert summary.count("[[") == summary.count("]]")
        assert summary.count("«") == summary.count("»")
        assert "more" not in original

    @staticmethod
    def test_short_attribution_retains_complete_article_details() -> None:
        """Retain an article link with the shorter attribution."""
        title = "遊" * 64
        original = EditSummary.membership([title], [], 1).render()
        summary = EditSummary.with_attribution(original)
        assert summary == (
            f"1 item total. Added «[[{title}]]». {_EXAMPLE_SUFFIX}"
        )
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_attribution_omits_whole_oversized_leader_clause() -> None:
        """Keep the date and attribution when a leader cannot fit."""
        original = (
            EditSummary
            .daily(dt.date(2025, 5, 5))
            .append(f"Daily leader «[[{'遊' * 61}]]».")
            .render()
        )
        assert "Daily leader" in original
        summary = EditSummary.with_attribution(original)
        assert summary == (f"Updated for 5 May 2025. {_EXAMPLE_SUFFIX}")

    @staticmethod
    def test_attribution_tries_ordered_complete_clause_alternatives() -> None:
        """Drop yearly then seasonal details to fit attribution."""
        daily = "Top: «[[Game]]» daily, weekly, and monthly"
        seasonal = f"«[[{'遊' * 48}]]» seasonal"
        yearly = "«[[Year]]» yearly"
        original = (
            EditSummary
            .daily(dt.date(2026, 10, 1))
            .append(
                f"{daily}; {seasonal}; {yearly}.",
                alternatives=(f"{daily}; {seasonal}.", f"{daily}."),
            )
            .render()
        )
        assert "yearly" in original
        summary = EditSummary.with_attribution(original)
        assert summary == (
            f"Updated for 1 Oct 2026. {daily}. {_EXAMPLE_SUFFIX}"
        )
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert "more" not in summary
        assert summary.count("[[") == summary.count("]]")
        assert summary.count("«") == summary.count("»")
        assert "yearly" in original

    @staticmethod
    def test_last_alternative_fits_before_clause_is_omitted() -> None:
        """Keep the shortest complete clause at its byte boundary."""
        retained = "Total. Short «[[Game]]»."
        size = len(retained.encode("utf-8"))
        for budget, expected in (
            (size, retained),
            (size - 1, "Total."),
        ):
            summary = (
                EditSummary("Total.", max_bytes=budget)
                .append(
                    f"Long «[[{'遊' * 100}]]».",
                    alternatives=("Short «[[Game]]».",),
                )
                .render()
            )
            assert summary == expected

    @staticmethod
    def test_plain_input_preserves_links_and_attribution_is_idempotent() -> (
        None
    ):
        """Keep complete links and one attribution after reuse."""
        original = f"2 items total. Added «[[Short]]», «[[{'遊戲' * 100}]]»."
        first = EditSummary.with_attribution(original)
        assert first == f"2 items total. Added «[[Short]]». {_EXAMPLE_SUFFIX}"
        for repeated in (first, f"  {first}  "):
            assert EditSummary.with_attribution(repeated) == first
        assert first.count("[[toolforge:Aranami|") == 1
        assert len(first.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_plain_input_can_fill_the_exact_final_byte_budget() -> None:
        """Include the complete suffix in the final size calculation."""
        summary = EditSummary.with_attribution("x" * MAX_EDIT_SUMMARY_BYTES)
        assert summary.endswith(_EXAMPLE_SUFFIX)
        assert len(summary.encode("utf-8")) == MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_plain_input_keeps_complete_bilingual_title_delimiters() -> None:
        """Keep linked titles balanced for plain summaries."""
        for opening, closing in (("«", "»"), ("“", "”")):
            retained = (
                f"2 items total. Added {opening}[[:en:Short]]{closing} "
                f"({opening}[[短]]{closing})"
            )
            original = (
                f"{retained}, {opening}[[:en:{'Long title' * 100}]]{closing} "
                f"({opening}[[長]]{closing})."
            )
            summary = EditSummary.with_attribution(original)
            assert summary == f"{retained}. {_EXAMPLE_SUFFIX}"
            assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_plain_input_keeps_complete_square_bracket_annotations() -> None:
        """Keep Chinese annotations whole when adding attribution."""
        retained = "2 items total. Added «[[:en:Short]]» &#91;[[短]]&#93;"
        for opening, closing in (("[", "]"), ("&#91;", "&#93;")):
            original = (
                f"{retained}, «[[:en:Long]]» "
                f"{opening}[[{'長' * 100}]]{closing}."
            )
            summary = EditSummary.with_attribution(original)
            assert summary == (
                f"{retained}, «[[:en:Long]]». {_EXAMPLE_SUFFIX}"
            )
            assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_plain_input_retains_annotations_at_the_exact_boundary() -> None:
        """Keep bracketed links only when closing delimiters fit."""
        for opening, closing in (("[", "]"), ("&#91;", "&#93;")):
            prefix = "Added «[[:en:Blade (Honkai)]]»"
            annotation = f"{opening}[[刃 (崩壞：星穹鐵道)]]{closing}"
            body_budget = (
                MAX_EDIT_SUMMARY_BYTES - len(_EXAMPLE_SUFFIX.encode()) - 1
            )
            padding = body_budget - len(
                f"{prefix} {annotation}.".encode(),
            )
            exact = f"{'x' * padding}{prefix} {annotation}."
            assert len(exact.encode()) == body_budget
            assert (
                EditSummary.with_attribution(exact)
                == f"{exact} {_EXAMPLE_SUFFIX}"
            )
            shortened = EditSummary.with_attribution("x" + exact)
            assert shortened == (
                f"x{'x' * padding}{prefix}. {_EXAMPLE_SUFFIX}"
            )

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
            assert EditSummary.with_attribution(
                preserved,
            ) == EditSummary.with_attribution(original)
        assert asdict(proposal)["summary"] == original
