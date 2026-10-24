"""Verify concise public lifecycle summaries over bounded saved logs."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend.support import public_data

_FILENAME = "aranami.2026-10-24.log"
_STAMP = "2026-10-24 03:14:15Z"


def _record(source: str, message: str, level: str = "INFO") -> str:
    """Build the daily handler's natural-language format.

    Returns:
        One complete UTC diagnostic line.
    """
    return f"{_STAMP} {level} [{source}]: {message}\n"


class TestPublicLogSummaries(TestCase):
    """Keep detailed diagnostics out of the public lifecycle view."""

    @staticmethod
    def test_real_lifecycle_messages_have_concise_timestamped_outcomes() -> (
        None
    ):
        """Preserve server, routine, website, and page lifecycle."""
        samples = (
            (
                "backend.hub.run",
                "Aranami 0.6.2 backend hub started.",
                "Server started.",
            ),
            (
                "backend.support.execution.routine_run",
                "Started pageviews.",
                "Started Pageviews reports.",
            ),
            (
                "updater.jobs._execution._save_edit",
                "Updating Wikipedia:电子游戏/热门 on zhwiki: Update counts.",
                "Started updating Wikipedia:电子游戏/热门 on zhwiki.",
            ),
            (
                "updater.jobs._execution._save_edit",
                "Saved Wikipedia:电子游戏/热门 on zhwiki.",
                "Updated Wikipedia:电子游戏/热门 on zhwiki.",
            ),
            (
                "updater.jobs._execution._save_edit",
                "No changes needed for Wikipedia:电子游戏/热门 on zhwiki.",
                "No changes needed for Wikipedia:电子游戏/热门 on zhwiki.",
            ),
            (
                "backend.support.execution.routine_run",
                (
                    "Finished pageviews: updated. Confirmed edits: 1. "
                    "Duration: 56.090 seconds."
                ),
                "Updated Pageviews reports.",
            ),
            (
                "backend.website.collect.collect_dashboard",
                "Refreshing website popular articles data.",
                "Started updating website popular articles.",
            ),
            (
                "backend.website.collect.collect_dashboard",
                "Updated website popular articles: 12 records.",
                "Updated website popular articles.",
            ),
            (
                "backend.website.collect.collect_dashboard",
                (
                    "Could not refresh website news and history (OSError). "
                    "Previous results were retained; retrying at the next "
                    "hourly check."
                ),
                (
                    "Failed to update website news and history; "
                    "previous data retained."
                ),
            ),
            (
                "updater.jobs.assessment_lists.run",
                "Assessment list failed: Wikipedia:电子游戏/列表",
                "Failed to update Wikipedia:电子游戏/列表.",
            ),
            (
                "updater.jobs.pexbot.run",
                "Delegated refresh failed: Wikipedia:电子游戏/清理.",
                "Failed to refresh Wikipedia:电子游戏/清理 with PexBot.",
            ),
            (
                "updater.jobs.pexbot._refresh",
                (
                    "PexBot Wikipedia:电子游戏/清理: refresh completed; "
                    "edit count is unconfirmed."
                ),
                (
                    "PexBot refresh completed for Wikipedia:电子游戏/清理; "
                    "edits are unconfirmed."
                ),
            ),
            (
                "updater.jobs.pexbot.run",
                (
                    "Skipped PexBot refresh: Wikipedia:电子游戏/清理. "
                    "PexBot already edited this page on 2026-10-24 UTC."
                ),
                (
                    "No changes needed for Wikipedia:电子游戏/清理; "
                    "PexBot already updated it today."
                ),
            ),
            (
                "backend.hub.run",
                (
                    "Aranami backend hub stopping (SIGTERM); "
                    "waiting for active work."
                ),
                "Server shutting down; waiting for active work.",
            ),
            (
                "backend.hub.__exit__",
                "Aranami 0.6.2 backend hub stopped.",
                "Server stopped.",
            ),
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = "".join(
                _record(source, text) for source, text, _ in samples
            )
            path = root / _FILENAME
            path.write_text(content)
            result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {
                "lines": [f"{_STAMP} {summary}" for _, _, summary in samples],
                "truncated": False,
            }
            assert path.read_text() == content

    def test_routine_outcomes_never_invent_success_or_edit_counts(
        self,
    ) -> None:
        """Distinguish unchanged, failed, deferred, and unknown work."""
        outcomes = {
            "no changes needed": "No changes needed for PexBot reports.",
            "failed": "Failed to update PexBot reports.",
            "waiting to retry": "Waiting to retry PexBot reports.",
            "outcome unconfirmed": (
                "Update outcome unconfirmed for PexBot reports."
            ),
        }
        for outcome, expected in outcomes.items():
            with (
                self.subTest(outcome=outcome),
                TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / _FILENAME).write_text(
                    _record(
                        "backend.support.execution.routine_run",
                        f"Finished pexbot: {outcome}. "
                        "Confirmed edits: unknown. "
                        "Duration: 56.090 seconds.",
                        "ERROR" if outcome == "failed" else "WARNING",
                    ),
                )
                assert public_data.log_excerpt(root, _FILENAME, "")[
                    "lines"
                ] == [
                    f"{_STAMP} {expected}",
                ]

    @staticmethod
    def test_preview_completion_does_not_claim_live_updates() -> None:
        """Keep preview results visibly distinct from live outcomes."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _FILENAME).write_text(
                _record(
                    "backend.support.execution.routine_run",
                    "Finished pageviews preview: no changes needed. "
                    "Confirmed edits: 0. Duration: 0.123 seconds.",
                ),
            )
            assert public_data.log_excerpt(root, _FILENAME, "")["lines"] == [
                f"{_STAMP} Finished Pageviews reports preview.",
            ]

    @staticmethod
    def test_tracebacks_progress_and_unrecognized_sources_are_hidden() -> None:
        """Retain failing tasks and omit diagnostic continuations."""
        messages = (
            _record(
                "backend.services.zhwiki.pageviews.fetch_period_totals",
                "Aggregated 12000/13048 articles",
            ),
            _record("shared.sources.wiki.read", "Started pageviews."),
            _record(
                "backend.website.extracts.save",
                "Could not save the article-introduction cache.",
                "WARNING",
            ),
            _record(
                "updater.jobs.pexbot._refresh",
                "PexBot Wikipedia:Report: running a database query.",
            ),
            _record(
                "backend.support.execution.routine_run",
                "Finished pageviews: failed. Confirmed edits: 0. "
                "Duration: 123.456 seconds.",
                "ERROR",
            ),
            "Traceback (most recent call last):\n",
            '  File "/private/source.py", line 2, in fetch\n',
            "    raise RuntimeError('details')\n",
            "RuntimeError: details\n",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _FILENAME).write_text("".join(messages))
            assert public_data.log_excerpt(root, _FILENAME, "") == {
                "lines": [f"{_STAMP} Failed to update Pageviews reports."],
                "truncated": False,
            }
            assert (
                public_data.log_excerpt(root, _FILENAME, "RuntimeError")[
                    "lines"
                ]
                == []
            )

    @staticmethod
    def test_filter_matches_visible_text_and_keeps_newest_hundred() -> None:
        """Filter visible text before limiting; retain literal HTML."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _FILENAME).write_text(
                "".join(
                    _record(
                        "updater.jobs._execution._save_edit",
                        f"Saved Report {index} <script> [x] on zhwiki.",
                    )
                    for index in range(150)
                ),
            )
            result = public_data.log_excerpt(root, _FILENAME, "[X]")
            expected_limit = 100
            assert len(result["lines"]) == expected_limit
            assert result["lines"][0] == (
                f"{_STAMP} Updated Report 50 <script> [x] on zhwiki."
            )
            assert result["truncated"]
            assert public_data.log_excerpt(root, _FILENAME, "REPORT 149")[
                "lines"
            ] == [f"{_STAMP} Updated Report 149 <script> [x] on zhwiki."]
            assert (
                public_data.log_excerpt(root, _FILENAME, "_save_edit")["lines"]
                == []
            )

    @staticmethod
    def test_noisy_megabyte_does_not_hide_latest_lifecycle_event() -> None:
        """Look beyond the former one-MiB tail with bounded reads."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _FILENAME).write_text(
                _record(
                    "backend.hub.run",
                    "Aranami 0.6.2 backend hub started.",
                )
                + _record(
                    "backend.services.zhwiki.pageviews.fetch_period_totals",
                    "Aggregated 12000/13048 articles",
                )
                * 12000,
            )
            assert public_data.log_excerpt(root, _FILENAME, "") == {
                "lines": [f"{_STAMP} Server started."],
                "truncated": False,
            }

    @staticmethod
    def test_chunk_boundaries_preserve_utf8_and_skip_long_lines() -> None:
        """Reassemble split codepoints and omit oversized lines."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _FILENAME).write_text(
                "x" * 20000
                + "\n"
                + _record(
                    "updater.jobs._execution._save_edit",
                    "Saved Wikipedia:电子游戏 on zhwiki.",
                ).replace("\n", "\r\n"),
            )
            with patch.object(public_data, "_READ_BYTES", 7):
                result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {
                "lines": [f"{_STAMP} Updated Wikipedia:电子游戏 on zhwiki."],
                "truncated": True,
            }

    @staticmethod
    def test_scan_boundary_and_display_bytes_remain_bounded() -> None:
        """Drop incomplete boundary records and cap rendered bytes."""
        first = _record(
            "backend.hub.run",
            "Aranami 0.6.2 backend hub started.",
        )
        second = _record(
            "backend.hub.run",
            "Aranami 0.6.2 backend hub stopped.",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _FILENAME).write_text(first + second)
            with (
                patch.object(public_data, "_SCAN_BYTES", len(second) + 7),
                patch.object(public_data, "_READ_BYTES", 13),
            ):
                result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {
                "lines": [f"{_STAMP} Server stopped."],
                "truncated": True,
            }
            with patch.object(public_data, "_DISPLAY_BYTES", 40):
                result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {
                "lines": [f"{_STAMP} Server stopped."],
                "truncated": True,
            }

    def test_unsafe_selection_is_rejected(self) -> None:
        """Retain valid dates, query bounds, and safe file checks."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "other.log"
            target.write_text("private diagnostics")
            (root / _FILENAME).symlink_to(target)
            for filename, query in (
                ("../other.log", ""),
                ("aranami.2026-02-30.log", ""),
                (_FILENAME, "x" * 1025),
                (_FILENAME, ""),
            ):
                with (
                    self.subTest(filename=filename, query=query),
                    self.assertRaises((OSError, ValueError)),  # ruff: ignore[pytest-unittest-raises-assertion]
                ):
                    public_data.log_excerpt(root, filename, query)
