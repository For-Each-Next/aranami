"""Validate staged source bytes and setup receipts before promotion.

This bootstrap helper uses only Python 3.11+ standard-library APIs.
Application imports are validated separately by the Python 3.15 runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

_SCRIPTS = {
    "setup-env.sh",
    "deploy-source.sh",
    "run-hub.sh",
    "run-web.sh",
    "runtime-env.sh",
    "source-files.json",
    "validate-source.py",
}
_PACKAGES = {"backend", "updater", "webservice", "shared"}
_SUFFIXES = {".py", ".css", ".js", ".json", ".svg"}
_MAX_JSON = 4 * 1024 * 1024
_MAX_FILE = 32 * 1024 * 1024
_MAX_TOTAL = 128 * 1024 * 1024
_PRIVATE = {
    "user-config.py",
    "user-password.py",
    "user-password.cfg",
    "user-fixes.py",
    ".my.cnf",
    ".env",
}


def _canonical_below(home: Path, path: Path) -> bool:
    """Allow host mount aliases only above the trusted tool directory.

    Args:
        home: Tool home below the host's optional NFS mount alias.
        path: Descendant that must not traverse any runtime symlink.

    Returns:
        Whether the path stays in the physical tool home without
        traversing symlinks inside that home.
    """
    if (
        not home.is_absolute()
        or ".." in home.parts
        or home.is_symlink()
        or not home.is_dir()
        or ".." in path.parts
    ):
        return False
    try:
        relative = path.relative_to(home)
    except ValueError:
        return False
    current = home
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return False
    return path.resolve() == home.resolve() / relative


def _regular(root: Path, name: str, *, bound: int = _MAX_FILE) -> bytes:
    """Read a bounded regular payload file without following symlinks.

    Args:
        root: Canonical directory containing the artifact.
        name: Exact relative artifact name.
        bound: Maximum permitted artifact size in bytes.

    Returns:
        Exact source bytes from a canonical path below the source root.

    Raises:
        ValueError: A path escapes the payload or is not a regular file.
    """
    relative = PurePosixPath(name)
    canonical = (
        relative.parts
        and not relative.is_absolute()
        and relative.as_posix() == name
    )
    private = (
        ".." in relative.parts or "\\" in name or relative.name in _PRIVATE
    )
    if not canonical or private:
        msg = f"Unsafe source path: {name}"
        raise ValueError(msg)
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            msg = f"Source symlink is not permitted: {name}"
            raise ValueError(msg)
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > bound
    ):
        msg = f"Expected a bounded regular source file: {name}"
        raise ValueError(msg)
    return path.read_bytes()


def _json(root: Path, name: str) -> dict[str, object]:
    """Read a bounded source metadata object.

    Args:
        root: Canonical directory containing the artifact.
        name: Exact relative JSON filename.

    Returns:
        Decoded JSON object.

    Raises:
        TypeError: The metadata is not an object.
    """
    value = json.loads(_regular(root, name, bound=_MAX_JSON))
    if not isinstance(value, dict):
        msg = f"Expected a JSON object: {name}"
        raise TypeError(msg)
    return value


def _expected_sources(root: Path) -> set[str]:
    """Read the reviewed source names and required deployment helpers.

    Args:
        root: Source root containing the reviewed file contract.

    Returns:
        Exact filenames required in the source hash manifest.

    Raises:
        ValueError: Reviewed names have an invalid shape or scope.
    """
    contract = _json(root, "scripts/toolforge/source-files.json")
    if set(contract) != {"schema", "files"} or contract["schema"] != 1:
        msg = "Unsupported reviewed source manifest."
        raise ValueError(msg)
    names = contract["files"]
    if not isinstance(names, list) or not all(
        isinstance(name, str) for name in names
    ):
        msg = "Reviewed sources must be a list of filenames."
        raise ValueError(msg)
    if len(names) != len(set(names)):
        msg = "Reviewed sources contain duplicate filenames."
        raise ValueError(msg)
    for name in names:
        path = PurePosixPath(name)
        if (
            len(path.parts) < len(("src", "package", "file"))
            or path.parts[0] != "src"
            or path.parts[1] not in _PACKAGES
            or path.suffix not in _SUFFIXES
        ):
            msg = f"Unexpected reviewed source: {name}"
            raise ValueError(msg)
    return set(names) | {
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        *(f"scripts/toolforge/{name}" for name in _SCRIPTS),
    }


def _verify_hashes(root: Path, hashes: object, expected: set[str]) -> None:
    """Verify every reviewed file before any runtime mutation.

    Args:
        root: Canonical source directory.
        hashes: Untrusted deployment hash mapping.
        expected: Exact reviewed filenames.

    Raises:
        ValueError: A source is missing, corrupted, or oversized.
    """
    if not isinstance(hashes, dict) or set(hashes) != expected:
        msg = "Deployment files differ from the reviewed payload."
        raise ValueError(msg)
    total = 0
    for name, digest in hashes.items():
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            msg = f"Invalid source hash: {name}"
            raise ValueError(msg)
        data = _regular(root, name)
        total += len(data)
        if total > _MAX_TOTAL or hashlib.sha256(data).hexdigest() != digest:
            msg = f"Source hash or payload size is invalid: {name}"
            raise ValueError(msg)


def _actual_sources(root: Path) -> set[str]:
    """Require regular files and directories throughout source.

    Args:
        root: Canonical source directory.

    Returns:
        Actual relative regular filenames.

    Raises:
        ValueError: Any source entry is a symlink or special file.
    """
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            msg = f"Unexpected source symlink: {path.relative_to(root)}"
            raise ValueError(
                msg,
            )
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            msg = f"Unexpected source file type: {path.relative_to(root)}"
            raise ValueError(
                msg,
            )
    return actual


def verify(root: Path) -> dict[str, object]:
    """Verify reviewed source before stopping any application.

    Args:
        root: Staged or active source directory.

    Returns:
        Complete validated generation manifest.

    Raises:
        ValueError: Paths, hashes, contents, or metadata differ.
    """
    if not root.is_dir() or not _canonical_below(root.parent, root):
        msg = "Expected a canonical source directory."
        raise ValueError(msg)
    manifest = _json(root, "deployment.json")
    if (
        set(manifest) != {"schema", "version", "files"}
        or manifest["schema"] != 1
    ):
        msg = "Unsupported deployment manifest."
        raise ValueError(msg)
    expected = _expected_sources(root)
    _verify_hashes(root, manifest["files"], expected)
    if _actual_sources(root) != expected | {"deployment.json"}:
        msg = "Unexpected or missing files in the source directory."
        raise ValueError(
            msg,
        )
    metadata = tomllib.loads(_regular(root, "pyproject.toml").decode())
    if (
        metadata["project"]["version"] != manifest["version"]
        or _regular(root, ".python-version").decode().strip() != "3.15.0"
        or "build-system" in metadata
        or metadata["tool"]["uv"].get("package") is not False
    ):
        msg = "Expected consistent Python 3.15 source metadata."
        raise ValueError(msg)
    return manifest


def _receipt_path(root: Path, path: Path, *, write: bool) -> None:
    """Require setup evidence inside this tool's backup directory.

    Args:
        root: Validated source generation.
        path: Requested receipt artifact.
        write: Whether this request can create new setup evidence.

    Raises:
        ValueError: The receipt could overwrite unrelated runtime data.
    """
    backup = root.parent / "deployment-backups"
    if not write and path == root.parent / "active-deployment.json":
        if not _canonical_below(root.parent, path):
            msg = "Active setup evidence cannot be a symlink."
            raise ValueError(msg)
        return
    valid_directory = (
        path.parent.parent == backup
        and path.parent.is_dir()
        and _canonical_below(root.parent, path.parent)
    )
    if (
        path.name != "setup-success.json"
        or not valid_directory
        or path.is_symlink()
    ):
        msg = "Expected a transaction-specific setup receipt."
        raise ValueError(msg)


def receipt(
    root: Path,
    path: Path,
    *,
    write: bool = False,
    environment: Path | None = None,
) -> None:
    """Write or verify proof that this source passed runtime setup.

    Args:
        root: Validated source generation used by the setup job.
        path: Unique receipt selected before requesting setup.
        write: Record success after imports, or verify existing proof.
        environment: Fresh validated environment used for setup.

    Raises:
        ValueError: Evidence is missing, stale, or from another runtime.
    """
    manifest = verify(root)
    _receipt_path(root, path, write=write)
    evidence = {} if write else _json(path.parent, path.name)
    environment = environment or Path(str(evidence.get("environment", "")))
    if (
        environment.parent != root.parent / "deployment-envs"
        or not _canonical_below(root.parent, environment)
        or not environment.is_dir()
    ):
        msg = "Setup environment must be an isolated generation."
        raise ValueError(msg)
    source = str(root) if write else str(evidence.get("source", ""))
    source_path = Path(source)
    if (
        source_path.parent != root.parent
        or re.fullmatch(r"app-staging-[0-9a-f]{32}", source_path.name) is None
    ):
        msg = "Setup source must identify its staged generation."
        raise ValueError(msg)
    expected = {
        "schema": 1,
        "source": source,
        "version": manifest["version"],
        "manifest_sha256": hashlib.sha256(
            _regular(root, "deployment.json"),
        ).hexdigest(),
        "python": "3.15.0",
        "environment": str(environment),
    }
    if write:
        if (
            sys.version_info[:3] != (3, 15, 0)
            or sys.version_info.releaselevel != "final"
            or path.exists()
        ):
            msg = "Setup success requires fresh Python 3.15.0 evidence."
            raise ValueError(
                msg,
            )
        _atomic_write(path, json.dumps(expected) + "\n")
    elif evidence != expected:
        msg = "Setup did not confirm this exact source generation."
        raise ValueError(msg)
    if (
        not write
        and path == root.parent / "active-deployment.json"
        and (root.parent / "pyvenv-3.15").resolve() != environment.resolve()
    ):
        msg = "Active environment differs from its setup evidence."
        raise ValueError(msg)


def _atomic_write(path: Path, text: str) -> None:
    """Atomically write one setup receipt with private permissions.

    Args:
        path: Exact destination artifact.
        text: Complete replacement content.
    """
    descriptor, name = tempfile.mkstemp(prefix=".deployment-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o640)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    """Validate source or record a successful isolated setup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("verify", "record", "receipt"),
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--environment", type=Path)
    args = parser.parse_args()
    if args.action == "verify":
        verify(args.root)
    elif args.path is None:
        parser.error("This action requires a transaction-specific path.")
    else:
        receipt(
            args.root,
            args.path,
            write=args.action == "record",
            environment=args.environment,
        )


if __name__ == "__main__":
    main()
