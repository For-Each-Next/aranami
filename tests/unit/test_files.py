"""Verify bounded file readers shared by saved-data consumers."""

# The project uses unittest for exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import os
import tempfile
from pathlib import Path
from unittest import TestCase

from shared.support.files import open_regular, read_json_object, read_regular


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
