"""Verify stable ASCII storage paths and lossless legacy reads."""

# Follow existing filesystem boundary assertion style.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from data.paths import read_storage_path, storage_key, write_storage_path


class TestStoragePaths(TestCase):
    """Separate logical identities from physical components."""

    @staticmethod
    def test_ascii_keys_and_date_partitions_keep_their_names() -> None:
        """Preserve already portable storage and UTC date partitions."""
        key = "projects/enwiki/Video games/2026/20261102.parquet"
        assert storage_key(key) == key

    @staticmethod
    def test_unicode_and_reserved_names_have_distinct_digests() -> None:
        """Hash exact UTF-8 identities without normalizing them."""
        title = "电子游戏"
        digest = hashlib.sha256(title.encode()).hexdigest()
        physical = f"target-{digest}"
        assert storage_key(f"projects/zhwiki/{title}/2026") == (
            f"projects/zhwiki/{physical}/2026"
        )
        assert storage_key(physical) != physical
        identities = (title, "電子遊戲", title + " ", "é", "e\u0301")
        mapped = {storage_key(identity) for identity in identities}
        assert len(mapped) == len(identities)
        assert all(value.isascii() for value in mapped)

    @staticmethod
    def test_long_components_are_bounded_without_truncating_identity() -> None:
        """Accept long titles and bound physical components."""
        for title in ("x" * 256, "电" * 1365, "x" * 4096):
            mapped = storage_key(f"projects/zhwiki/{title}/2026")
            assert mapped.isascii()
            assert max(len(part.encode()) for part in mapped.split("/")) <= 255
            assert mapped != storage_key(f"projects/zhwiki/{title[:-1]}/2026")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key = "projects/zhwiki/" + "电" * 1365 + "/20261102.parquet"
            assert read_storage_path(root, key) == write_storage_path(
                root,
                key,
            )

    def test_invalid_relative_keys_fail_before_filesystem_access(self) -> None:
        """Reject unsafe and oversized logical components."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for key in (
                "",
                "/absolute",
                "../outside",
                "safe/../outside",
                "safe/./file",
                "safe//file",
                "safe/file/",
                "safe\\file",
                "safe/line\nfeed",
                "safe/null\x00byte",
                "safe/delete\x7fbyte",
                "x" * 4097,
                "电" * 1366,
            ):
                with self.subTest(key=repr(key)):
                    for operation in (
                        storage_key,
                        lambda value: read_storage_path(root, value),
                        lambda value: write_storage_path(root, value),
                    ):
                        with self.assertRaises(ValueError):
                            operation(key)
            assert not list(root.iterdir())

    @staticmethod
    def test_new_writes_use_ascii_without_creating_directories() -> None:
        """Return the portable destination without materializing it."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key = "projects/zhwiki/电子游戏/2026/20261102.parquet"
            path = write_storage_path(root, key)
            assert path == root / storage_key(key)
            assert path.relative_to(root).as_posix().isascii()
            assert read_storage_path(root, key) == path
            assert not list(root.iterdir())

    @staticmethod
    def test_ascii_files_are_preferred_without_rewriting_evidence() -> None:
        """Read the mapped file while preserving its original bytes."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key = "projects/zhwiki/电子游戏/2026/20261102.parquet"
            mapped = root / storage_key(key)
            mapped.parent.mkdir(parents=True)
            mapped.write_bytes(b"retained ASCII evidence")
            assert read_storage_path(root, key) == mapped
            assert mapped.read_bytes() == b"retained ASCII evidence"
            assert not (root / key).exists()

    def test_legacy_reads_are_lossless_and_block_shadow_writes(self) -> None:
        """Reuse retained evidence until an explicit migration."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key = "projects/zhwiki/电子游戏/2026/20261102.parquet"
            legacy = root / key
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"immutable legacy evidence")
            assert read_storage_path(root, key) == legacy
            with self.assertRaises(ValueError):
                write_storage_path(root, key)
            assert legacy.read_bytes() == b"immutable legacy evidence"
            assert not (root / storage_key(key)).exists()

    def test_duplicate_legacy_and_mapped_locations_require_resolution(
        self,
    ) -> None:
        """Reject duplicate identities even when their bytes match."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key = "projects/zhwiki/电子游戏/2026/20261102.parquet"
            paths = (root / key, root / storage_key(key))
            for path in paths:
                path.parent.mkdir(parents=True)
                path.write_bytes(b"same retained facts")
            for operation in (read_storage_path, write_storage_path):
                with self.assertRaises(ValueError):
                    operation(root, key)
            assert all(
                path.read_bytes() == b"same retained facts" for path in paths
            )

    def test_symlinked_or_nondirectory_parents_are_rejected(self) -> None:
        """Refuse linked roots, linked ancestors and blocking files."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            linked_root = root / "linked"
            linked_root.symlink_to(outside, target_is_directory=True)
            for operation in (read_storage_path, write_storage_path):
                with self.assertRaises(OSError):
                    operation(linked_root, "projects/example.parquet")
            for name, symlink in (
                ("linked-parent", True),
                ("file-parent", False),
            ):
                parent = root / name
                if symlink:
                    parent.symlink_to(outside, target_is_directory=True)
                else:
                    parent.write_bytes(b"not a directory")
                for operation in (read_storage_path, write_storage_path):
                    with self.assertRaises(OSError):
                        operation(root, f"{name}/example.parquet")
            assert not list(outside.iterdir())

    def test_legacy_symlink_cannot_be_bypassed_by_ascii_destination(
        self,
    ) -> None:
        """Reject unsafe ancestors in the old storage path."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (root / "电子游戏").symlink_to(outside, target_is_directory=True)
            for operation in (read_storage_path, write_storage_path):
                with self.assertRaises(OSError):
                    operation(root, "电子游戏/20261102.parquet")
            assert not list(outside.iterdir())

    def test_leaf_symlinks_never_expose_other_retained_files(self) -> None:
        """Reject current and legacy links without reading targets."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "private.parquet"
            outside.write_bytes(b"private evidence")
            key = "电子游戏/20261102.parquet"
            for linked in (root / storage_key(key), root / key):
                linked.parent.mkdir(parents=True)
                linked.symlink_to(outside)
                for operation in (read_storage_path, write_storage_path):
                    with self.assertRaises(OSError):
                        operation(root, key)
                linked.unlink()
            assert outside.read_bytes() == b"private evidence"
