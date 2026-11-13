"""Verify shared edit-summary budgeting and complete link truncation."""

# Preserve wiki punctuation in quoted-title fixtures.
# The offline runner uses unittest without a pytest dependency.
# ruff: file-ignore[ambiguous-unicode-character-string, pytest-unittest-raises-assertion]
import datetime as dt
import pickle  # ruff: ignore[suspicious-pickle-import]
from copy import copy, deepcopy
from dataclasses import asdict
from unittest import TestCase, addModuleCleanup
from unittest.mock import Mock, patch

from runtime.jobs import ProposedEdit
from updater.edit_summary import (
    MAX_EDIT_SUMMARY_BYTES,
    EditSummary,
    article_mention,
    format_summary_number,
    summary_change,
)

_CHINESE_CHARACTER_BYTES = 3
_EXAMPLE_SUFFIX = "([[toolforge:aranami|Aranami]])"


def setUpModule() -> None:
    """Keep summary year fixtures independent of the actual UTC year."""
    clock = patch("updater.edit_summary._current_year", return_value=2026)
    clock.start()
    addModuleCleanup(clock.stop)


class TestSummaryNumberFormatter(TestCase):
    """Group counts while preserving title and date digits."""

    @staticmethod
    def test_counts_use_narrow_no_break_spaces() -> None:
        """Keep signs and group only complete integer counts."""
        for count, expected in (
            (0, "0"),
            (999, "999"),
            (1000, "1\u202f000"),
            (3043, "3\u202f043"),
            (2641, "2\u202f641"),
            (1000000, "1\u202f000\u202f000"),
            (-12345, "-12\u202f345"),
        ):
            assert format_summary_number(count) == expected

    @staticmethod
    def test_dates_and_title_digits_stay_literal() -> None:
        """Leave other summary digits unchanged when grouping counts."""
        summary = (
            EditSummary
            .daily(
                dt.date(2025, 11, 2),
                action=f"Add {format_summary_number(13062)} articles",
            )
            .add_group("Add ", [article_mention("2001: A Space Odyssey")])
            .render()
        )
        assert summary == (
            "Add 13\u202f062 articles for Nov 2 2025. "
            "Add «[[2001: A Space Odyssey]]»."
        )

    def test_change_suffix_preserves_both_membership_directions(self) -> None:
        """Keep turnover visible even when the net change is zero."""
        for added, removed, expected in (
            (0, 0, ""),
            (1, 0, " (+1)"),
            (0, 2, " (-2)"),
            (1, 1, " (+1/-1)"),
            (1234, 2345, " (+1\u202f234/-2\u202f345)"),
        ):
            assert summary_change(added, removed) == expected
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            summary_change(-1, 0)
        with self.assertRaisesRegex(TypeError, "must be integers"):
            summary_change(added=True, removed=0)

    @staticmethod
    def test_group_omissions_include_separator_bytes_in_the_budget() -> None:
        """Keep grouped omissions whole at their UTF-8 boundary."""
        expected = "Update items. Add 1\u202f000 more."
        summary = (
            EditSummary("Update items.", max_bytes=len(expected.encode()))
            .add_group("Add ", [article_mention("Game")] * 1000)
            .render()
        )
        assert summary == expected
        assert len(summary.encode()) == len(expected.encode())


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
                f"Update report for {name} 1."
            )
        assert EditSummary.daily(dt.date(2026, 9, 15)).render() == (
            "Update report for Sep 15."
        )

    @staticmethod
    def test_daily_dates_keep_other_years_explicit() -> None:
        """Disambiguate historical reports after the year changes."""
        with patch("updater.edit_summary._current_year", return_value=2027):
            assert EditSummary.daily(dt.date(2026, 11, 1)).render() == (
                "Update report for Nov 1 2026."
            )
            assert EditSummary.daily(dt.date(2027, 11, 1)).render() == (
                "Update report for Nov 1."
            )

    @staticmethod
    def test_daily_action_and_count_precede_date() -> None:
        """Keep the action and count with one trailing full stop."""
        for action in ("Change 21 assessments", "Change 21 assessments."):
            assert (
                EditSummary.daily(
                    dt.date(2026, 11, 2),
                    action=action,
                ).render()
                == "Change 21 assessments for Nov 2."
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
            "1\u202f234 items (+2/-1). Add «[[New]]», «[[Another]]»; remove "
            "«[[Old]]»."
        )
        assert EditSummary.membership([], [], 1).render() == "1 item."
        assert EditSummary.membership([], ["Old"], 0).render() == (
            "0 items (-1). Remove «[[Old]]»."
        )

    @staticmethod
    def test_multibyte_links_are_shortened_as_whole_mentions() -> None:
        """Retain totals and both change counts for oversized titles."""
        summary = EditSummary.membership(
            ["遊戲" * 100],
            ["舊" * 100],
            1234,
        ).render()
        assert summary == (
            "1\u202f234 items (+1/-1). Add 1 more; remove 1 more."
        )
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
        assert summary == (
            "Update report for Oct 2. Weekly leader «[[Game]]»."
        )

    @staticmethod
    def test_custom_small_budget_is_utf8_safe() -> None:
        """Drop oversized complete clauses instead of splitting text."""
        assert not EditSummary("遊戲", max_bytes=4).render()
        assert EditSummary("Count. More.", max_bytes=8).render() == "Count."
        assert not EditSummary("Items.", max_bytes=0).render()

    @staticmethod
    def test_attribution_uses_exact_compact_link() -> None:
        """Reserve space for the exact configured attribution."""
        assert EditSummary.with_attribution("summary") == (
            f"summary {_EXAMPLE_SUFFIX}"
        )
        assert EditSummary.with_attribution("") == _EXAMPLE_SUFFIX
        assert "toolforge:aranami" in _EXAMPLE_SUFFIX


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
            == "«[[:en:Blade (Honkai)]]» &#91;[[刃 (崩壞：星穹鐵道)]]&#93;"
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
        summary = EditSummary("Update 1 item.").add_group(
            "Promote to FA ",
            [mention],
        )
        rendered = EditSummary.with_attribution(summary.render())
        assert (
            rendered
            == f"Update 1 item. Promote to FA {mention}. {_EXAMPLE_SUFFIX}"
        )
        assert len(rendered.encode()) <= MAX_EDIT_SUMMARY_BYTES
        oversized = (
            EditSummary("Update 1 item.")
            .add_group(
                "Promote to FA ",
                [
                    article_mention("遊" * 100, interwiki="en", companion="戲")
                    + " (from GA)",
                ],
            )
            .render()
        )
        assert EditSummary.with_attribution(oversized) == (
            f"Update 1 item. Promote to FA 1 more. {_EXAMPLE_SUFFIX}"
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
        assert summary.startswith(
            "1\u202f234 items (+1/-1). Add ",
        )
        assert "; remove " in summary
        assert "more" in summary
        assert summary.endswith(_EXAMPLE_SUFFIX)
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert summary.count("[[") == summary.count("]]")
        assert summary.count("«") == summary.count("»")
        assert "more" not in original

    @staticmethod
    def test_short_attribution_retains_complete_article_details() -> None:
        """Retain an article link with the shorter attribution."""
        title = "遊" * 61
        original = EditSummary.membership([title], [], 1).render()
        summary = EditSummary.with_attribution(original)
        assert summary == (
            f"1 item (+1). Add «[[{title}]]». {_EXAMPLE_SUFFIX}"
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
        assert summary == (f"Update report for May 5 2025. {_EXAMPLE_SUFFIX}")

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
            f"Update report for Oct 1. {daily}. {_EXAMPLE_SUFFIX}"
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
        original = f"Update 2 items. Add «[[Short]]», «[[{'遊戲' * 100}]]»."
        first = EditSummary.with_attribution(original)
        assert first == f"Update 2 items. Add «[[Short]]». {_EXAMPLE_SUFFIX}"
        for repeated in (first, f"  {first}  "):
            assert EditSummary.with_attribution(repeated) == first
        assert first.count("[[toolforge:aranami|") == 1
        assert len(first.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES

    @staticmethod
    def test_plain_input_can_fill_the_exact_final_byte_budget() -> None:
        """Include the complete suffix in the final size calculation."""
        budget = MAX_EDIT_SUMMARY_BYTES - len(_EXAMPLE_SUFFIX.encode()) - 1
        summary = EditSummary.with_attribution("x" * (budget - 1) + ".")
        assert summary.endswith(_EXAMPLE_SUFFIX)
        assert len(summary.encode("utf-8")) == MAX_EDIT_SUMMARY_BYTES
        assert (
            EditSummary.with_attribution("x" * MAX_EDIT_SUMMARY_BYTES)
            == _EXAMPLE_SUFFIX
        )

    @staticmethod
    def test_plain_input_keeps_complete_bilingual_title_delimiters() -> None:
        """Keep linked titles balanced for plain summaries."""
        for opening, closing in (("«", "»"), ("“", "”")):
            retained = (
                f"Update 2 items. Add {opening}[[:en:Short]]{closing} "
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
        retained = "Update 2 items. Add «[[:en:Short]]» &#91;[[短]]&#93;"
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
            prefix = "Add «[[:en:Blade (Honkai)]]»"
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
