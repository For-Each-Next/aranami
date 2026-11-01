"""Verify bounded file readers shared by saved-data consumers."""

# The project uses unittest for exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from shared.support.files import (
    atomic_json,
    encode_json,
    open_regular,
    read_json_object,
    read_regular,
)


class TestSavedFileReaders(TestCase):
    """Reject malformed, oversized, and redirected files."""

    def setUp(self) -> None:
        """Create isolated runtime artifacts outside the source tree."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "saved.json"

    def test_exact_size_empty_and_missing_files(self) -> None:
        """Accept exact bounds and empty files, preserving absence."""
        self.path.write_bytes(b"{}")
        assert read_regular(self.path, 2) == b"{}"
        assert read_json_object(self.path, 2) == {}
        with self.assertRaises(ValueError):
            read_regular(self.path, 1)
        self.path.write_bytes(b"")
        assert read_regular(self.path, 0) == b""
        with self.assertRaises(ValueError):
            read_regular(self.path, -1)
        with self.assertRaises(FileNotFoundError):
            read_regular(self.directory / "missing", 1)

    def test_invalid_json_and_nonobject_roots(self) -> None:
        """Reject invalid documents and JSON values without keys."""
        for content in (b"{", b"[]", b"null", b"false", b"1", b'"text"'):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(ValueError):
                    read_json_object(self.path, 100)

    def test_file_and_parent_symlinks_are_rejected(self) -> None:
        """Refuse redirects through the file or its parent."""
        self.path.write_bytes(b"{}")
        linked_file = self.directory / "linked.json"
        linked_file.symlink_to(self.path)
        linked_parent = self.directory / "linked"
        linked_parent.symlink_to(self.directory, target_is_directory=True)
        for path in (linked_file, linked_parent / self.path.name):
            with self.subTest(path=path), self.assertRaises(OSError):
                read_regular(path, 100)

    def test_nonregular_files_do_not_block(self) -> None:
        """Reject directories and FIFOs without waiting on writers."""
        fifo = self.directory / "fifo"
        os.mkfifo(fifo)
        for path in (self.directory, fifo):
            with self.subTest(path=path), self.assertRaises(OSError):
                read_regular(path, 100)

    def test_stream_closes_after_reader_failure(self) -> None:
        """Close an opened descriptor when its consumer raises."""  # ruff: ignore[docstring-missing-exception]
        self.path.write_bytes(b"{}")
        with (
            self.assertRaises(ValueError),
            open_regular(self.path, 2) as stream,
        ):
            assert stream.read(1) == b"{"
            message = "consumer failed"
            raise ValueError(message)
        assert stream.closed

    def test_atomic_json_bounds_and_interruption_preserve_selected_bytes(
        self,
    ) -> None:
        """Preserve selected bytes when staging or replacement fails."""
        atomic_json(self.path, {"old": 1}, max_bytes=100)
        original = self.path.read_bytes()
        for point in ("os.fsync", "Path.replace"):
            target = "shared.support.files." + point
            with (
                self.subTest(point=point),
                patch(target, side_effect=OSError),
                self.assertRaises(OSError),
            ):
                atomic_json(self.path, {"new": 2}, max_bytes=100)
            assert self.path.read_bytes() == original
        assert not list((self.directory / "cache/report-staging").iterdir())
        with self.assertRaises(ValueError):
            atomic_json(self.path, {"new": 2}, max_bytes=1)
        assert self.path.read_bytes() == original
        assert encode_json({"z": 1, "a": "文"}) == '{"a":"文","z":1}'.encode()

    def test_directory_sync_failure_keeps_complete_new_json(self) -> None:
        """Keep complete new JSON after a directory sync failure."""
        with (
            patch(
                "shared.support.files.os.fsync",
                side_effect=[None, OSError],
            ),
            self.assertRaises(OSError),
        ):
            atomic_json(self.path, {"new": 2}, max_bytes=100)
        assert read_json_object(self.path, 100) == {"new": 2}
