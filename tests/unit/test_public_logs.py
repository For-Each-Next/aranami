"""Verify bounded event reads without parsing diagnostic logs."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import patch

from backend.support import public_data

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import BinaryIO

_FILENAME = "aranami.2026-10-24.log"
_EVENT_FILENAME = "events.2026-10-24.jsonl"
_STAMP = "2026-10-24T03:14:15+00:00"


def _event(
    kind: str = "service_started",
    **fields: object,
) -> dict[str, object]:
    """Build one event from explicit lifecycle facts.

    Args:
        kind: Structured lifecycle event identifier.
        **fields: Observations or intentionally invalid fixture fields.

    Returns:
        A schema-one event dictionary.
    """
    return {
        "schema": 1,
        "time": _STAMP,
        "kind": kind,
        "dry": False,
        **({"service": "hub"} if kind.startswith("service_") else {}),
        **fields,
    }


def _line(event: object) -> str:
    """Encode one UTF-8 JSONL fixture with a terminating newline.

    Returns:
        One JSON record, including intentionally invalid values.
    """
    return json.dumps(event, ensure_ascii=False) + "\n"


class TestPublicLogEvents(TestCase):
    """Keep public events separate from detailed runtime diagnostics."""

    @staticmethod
    def test_valid_lifecycle_events_preserve_observed_fields() -> None:
        """Retain outcomes, unknown edit counts, and preview facts."""
        events = [
            _event(),
            _event("service_stopping", service="scheduler"),
            _event("service_stopped", service="website"),
            _event("routine_started", routine="pageviews"),
            _event(
                "routine_finished",
                routine="pexbot",
                status="unconfirmed",
                updated=None,
                duration_seconds=12.34,
            ),
            _event(
                "routine_finished",
                routine="pageviews",
                status="no_update",
                updated=0,
                duration_seconds=0,
                dry=True,
            ),
            _event("website_started"),
            _event(
                "website_finished",
                status="partial",
                failures=1,
                duration_seconds=2.5,
            ),
            _event("run_started", tasks=6),
            _event(
                "run_finished",
                tasks=6,
                failures=1,
                deferred=2,
                duration_seconds=12.5,
            ),
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / _FILENAME
            raw.write_text(
                "private source request and traceback /credentials\n",
            )
            path = root / _EVENT_FILENAME
            content = "".join(_line(event) for event in events)
            path.write_text(content)
            with patch.object(
                public_data,
                "open_regular",
                wraps=public_data.open_regular,
            ) as opened:
                result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {"events": events, "truncated": False}
            opened.assert_called_once_with(
                path,
                public_data.MAX_EVENT_FILE_BYTES,
            )
            assert path.read_text() == content
            assert "/credentials" in raw.read_text()

    @staticmethod
    def test_diagnostics_are_never_parsed_or_used_as_a_fallback() -> None:
        """Keep formerly recognized English messages out of the page."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _FILENAME).write_text(
                "2026-10-24 03:14:15Z INFO [backend.hub.run]: "
                "Aranami 0.6.2 backend hub started.\n"
                "RuntimeError: private-token /secret/path\n",
            )
            assert public_data.log_excerpt(root, _FILENAME, "") == {
                "events": [],
                "truncated": False,
            }
            assert not (root / _EVENT_FILENAME).exists()
            (root / _EVENT_FILENAME).write_text("not JSON\n")
            assert public_data.log_excerpt(
                root,
                _FILENAME,
                "private-token",
            ) == {
                "events": [],
                "truncated": False,
            }

    @staticmethod
    def test_unknown_malformed_and_free_text_events_are_omitted() -> None:
        """Require the known UTC schema and exclude arbitrary text."""
        invalid = [
            None,
            [],
            _event(schema=2),
            _event(time="2026-10-24T03:14:15"),
            _event(time="2026-10-24T03:14:15+08:00"),
            _event("unknown_event"),
            _event(dry=0),
            _event(service="private-source"),
            _event(message="private-token /secret/path"),
            _event(title="Wikipedia:电子游戏"),
            _event("routine_started", routine="unknown"),
            _event(
                "routine_finished",
                routine="pexbot",
                status="success",
                updated=0,
                duration_seconds=1,
            ),
            _event(
                "routine_finished",
                routine="pexbot",
                status="failed",
                updated=-1,
                duration_seconds=1,
            ),
            _event(
                "routine_finished",
                routine="pexbot",
                status="failed",
                updated=None,
                duration_seconds=float("nan"),
            ),
            _event("run_started", tasks=7),
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            valid = _event("service_stopped")
            (root / _EVENT_FILENAME).write_text(
                "{malformed JSON\n"
                + "".join(_line(event) for event in invalid)
                + _line(valid),
            )
            assert public_data.log_excerpt(root, _FILENAME, "") == {
                "events": [valid],
                "truncated": False,
            }

    @staticmethod
    def test_event_utc_day_must_match_the_selected_daily_file() -> None:
        """Do not attribute another day's valid record to this date."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _EVENT_FILENAME).write_text(
                _line(_event(time="2026-10-23T23:59:59+00:00"))
                + _line(_event(time="2026-10-25T00:00:00Z"))
                + _line(_event(time="2026-10-24T00:00:00Z")),
            )
            assert public_data.log_excerpt(root, _FILENAME, "")["events"] == [
                _event(time="2026-10-24T00:00:00Z"),
            ]

    @staticmethod
    def test_recent_hundred_events_are_returned_for_localized_filtering() -> (
        None
    ):
        """Bound the recent search window before localized filtering."""
        events = [
            _event(
                time=f"2026-10-24T03:{index // 60:02}:{index % 60:02}+00:00",
            )
            for index in range(150)
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _EVENT_FILENAME).write_text("".join(map(_line, events)))
            for query in ("", "service_started", "已启动", "not present"):
                assert public_data.log_excerpt(root, _FILENAME, query) == {
                    "events": events[50:],
                    "truncated": True,
                }

    @staticmethod
    def test_small_blocks_skip_long_and_invalid_utf8_lines() -> None:
        """Preserve split records and omit unsafe encoded content."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            valid = _event()
            (root / _EVENT_FILENAME).write_bytes(
                b"x" * (public_data.MAX_EVENT_LINE_BYTES + 1)
                + b"\n"
                + b"\xff\xfe\n"
                + _line(_event(message="非结构化文本")).encode()
                + _line(valid).replace("\n", "\r\n").encode(),
            )
            with patch.object(public_data, "_READ_BYTES", 7):
                result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {"events": [valid], "truncated": True}

    @staticmethod
    def test_scan_boundary_and_encoded_output_remain_bounded() -> None:
        """Drop partial boundary records and enforce the byte budget."""
        first = _line(_event())
        second_event = _event("service_stopped")
        second = _line(second_event)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _EVENT_FILENAME).write_text(first + second)
            with (
                patch.object(public_data, "_SCAN_BYTES", len(second) + 7),
                patch.object(public_data, "_READ_BYTES", 13),
            ):
                result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {"events": [second_event], "truncated": True}
            with patch.object(public_data, "_DISPLAY_BYTES", len(second) - 2):
                result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {"events": [], "truncated": True}

    @staticmethod
    def test_bytes_appended_after_opening_do_not_extend_the_scan() -> None:
        """Keep one read bounded to its observed opening size."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / _EVENT_FILENAME
            event = _event()
            path.write_text(_line(event))
            reader = public_data._recent_event_lines  # ruff: ignore[private-member-access]

            def append_before_scan(
                stream: BinaryIO,
                size: int,
            ) -> Iterator[bytes]:
                """Append a record after the reader observed file size.

                Yields:
                    Complete lines within the observed file boundary.
                """
                with path.open("a") as appended:
                    appended.write(_line(_event("service_stopped")))
                yield from reader(stream, size)

            with patch.object(
                public_data,
                "_recent_event_lines",
                side_effect=append_before_scan,
            ):
                result = public_data.log_excerpt(root, _FILENAME, "")
            assert result == {"events": [event], "truncated": False}

    def test_invalid_selection_and_unsafe_event_files_are_rejected(
        self,
    ) -> None:
        """Keep strict dates, query bounds, and safe file opens."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, query in (
                ("../other.log", ""),
                ("aranami.2026-02-30.log", ""),
                (_FILENAME, "x" * 1025),
                (_FILENAME, "中" * 342),
            ):
                with (
                    self.subTest(filename=filename),
                    self.assertRaises(ValueError),  # ruff: ignore[pytest-unittest-raises-assertion]
                ):
                    public_data.log_excerpt(root, filename, query)
            target = root / "private.jsonl"
            target.write_text(_line(_event()))
            path = root / _EVENT_FILENAME
            path.symlink_to(target)
            assert public_data.log_excerpt(root, _FILENAME, "")["events"] == []
            path.unlink()
            path.mkdir()
            assert public_data.log_excerpt(root, _FILENAME, "")["events"] == []
            path.rmdir()
            with path.open("wb") as oversized:
                oversized.truncate(public_data.MAX_EVENT_FILE_BYTES + 1)
            assert public_data.log_excerpt(root, _FILENAME, "")["events"] == []
            linked = root / "linked"
            linked.symlink_to(root, target_is_directory=True)
            assert (
                public_data.log_excerpt(linked, _FILENAME, "")["events"] == []
            )

    @staticmethod
    def test_raw_listing_and_download_selection_keep_existing_contracts() -> (
        None
    ):
        """List bounded diagnostic files independently from events."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / _FILENAME).write_text("raw diagnostics\n")
            (root / _EVENT_FILENAME).write_text(_line(_event()))
            (root / "aranami.2026-10-23.log").symlink_to(root / _FILENAME)
            with (root / "aranami.2026-10-22.log").open("wb") as oversized:
                oversized.truncate(public_data.MAX_LOG_BYTES + 1)
            files = public_data.available_logs(root)
            assert files == [
                {
                    "date": "2026-10-24",
                    "name": _FILENAME,
                    "size": len("raw diagnostics\n"),
                },
            ]
            assert public_data.log_selection({}, files) == (
                _FILENAME,
                "",
                False,
            )
            assert public_data.log_selection(
                {"date": ["2026-10-24"], "q": [" 网站 "], "download": ["1"]},
                files,
            ) == (_FILENAME, "网站", True)
