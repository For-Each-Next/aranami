"""Validate and stage one source payload without starting publication.

Use ``--dry-run`` to inspect the payload without SSH, and optionally
``--archive`` to retain it. Promote an upload explicitly with the staged
``scripts/toolforge/deploy-source.sh`` script.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from shared import config  # ruff: ignore[module-import-not-at-top-of-file]

_PACKAGES = ("backend", "updater", "webservice", "shared")
_SUFFIXES = {".py", ".php", ".json", ".svg", ".conf"}
_SCRIPTS = (
    "setup-env.sh",
    "deploy-source.sh",
    "run-hub.sh",
    "source-files.json",
    "validate-source.py",
)
_PRIVATE_NAMES = {
    "user-config.py",
    "user-password.py",
    "user-password.cfg",
    "user-fixes.py",
    ".my.cnf",
    ".env",
}
_RUNTIME_PARTS = {"cache", "dry-run", "reports", "state", ".git", ".venv"}


def _relative_path(name: str) -> PurePosixPath:
    """Validate an exact relative archive path.

    Args:
        name: Untrusted path from the reviewed source contract.

    Returns:
        Safe normalized POSIX path with no traversal or runtime data.

    Raises:
        ValueError: The path is unsafe, private, or not canonical.
    """
    relative = PurePosixPath(name)
    canonical = (
        relative.parts
        and not relative.is_absolute()
        and relative.as_posix() == name
        and "\\" not in name
    )
    private = (
        relative.name in _PRIVATE_NAMES
        or ".." in relative.parts
        or _RUNTIME_PARTS.intersection(relative.parts)
        or (relative.parts and relative.parts[0] == "logs")
    )
    if not canonical or private:
        msg = f"Unsafe deployment path: {name}"
        raise ValueError(msg)
    return relative


def _read_regular(root: Path, name: str) -> bytes:
    """Read a regular deployment file without traversing symlinks.

    Args:
        root: Repository or isolated fixture root.
        name: Exact reviewed relative deployment path.

    Returns:
        Complete source bytes.

    Raises:
        ValueError: A path component is a symlink or the file is absent.
    """
    relative = _relative_path(name)
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            msg = f"Deployment symlinks are not allowed: {name}"
            raise ValueError(msg)
    if not path.is_file():
        msg = f"Missing regular deployment file: {name}"
        raise ValueError(msg)
    return path.read_bytes()


def _actual_sources(root: Path) -> set[str]:
    """Collect runtime sources and reject source symlinks.

    Args:
        root: Repository or isolated fixture root.

    Returns:
        Complete source names within the runtime packages.

    Raises:
        ValueError: A package is missing or contains a symlink.
    """
    actual = set()
    for package in _PACKAGES:
        _read_regular(root, f"src/{package}/__init__.py")
        for path in sorted((root / "src" / package).rglob("*")):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or path.name == "AGENTS.md":
                continue
            if path.is_symlink():
                msg = f"Source symlinks are not allowed: {relative}"
                raise ValueError(msg)
            if path.is_file() and path.suffix in _SUFFIXES:
                actual.add(relative.as_posix())
    return actual


def _source_names(root: Path, data: bytes) -> list[str]:
    """Check all application sources against the reviewed file contract.

    Args:
        root: Repository or isolated fixture root.
        data: Reviewed versioned source-files manifest bytes.

    Returns:
        Sorted complete source filenames.

    Raises:
        ValueError: The contract or source tree differs from its review.
    """
    contract = json.loads(data)
    if (
        not isinstance(contract, dict)
        or set(contract) != {"schema", "files"}
        or contract.get("schema") != 1
        or not isinstance(contract.get("files"), list)
        or not all(isinstance(name, str) for name in contract["files"])
    ):
        msg = "Invalid reviewed source-files manifest."
        raise ValueError(msg)
    expected = set(contract["files"])
    if len(expected) != len(contract["files"]):
        msg = "Source-files manifest contains duplicate paths."
        raise ValueError(msg)
    for name in expected:
        relative = _relative_path(name)
        if (
            relative.parts[:2]
            not in {("src", package) for package in _PACKAGES}
            or relative.suffix not in _SUFFIXES
        ):
            msg = f"Unexpected reviewed source path: {name}"
            raise ValueError(msg)
    actual = _actual_sources(root)
    if actual != expected:
        msg = (
            "Source files differ from the reviewed payload: "
            f"{sorted(actual ^ expected)}"
        )
        raise ValueError(msg)
    return sorted(expected)


def source_payload(root: Path = ROOT) -> dict[str, bytes]:
    """Return the exact validated upload, excluding all runtime data.

    Args:
        root: Repository root containing both applications.

    Returns:
        Relative archive filenames and their validated bytes.

    Raises:
        ValueError: A required file is unsafe, missing, or inconsistent.
    """
    required = ["pyproject.toml", "uv.lock", ".python-version"]
    required += [f"scripts/toolforge/{name}" for name in _SCRIPTS]
    payload = {name: _read_regular(root, name) for name in required}
    metadata = tomllib.loads(payload["pyproject.toml"].decode())
    lock = tomllib.loads(payload["uv.lock"].decode())
    version = metadata["project"]["version"]
    packaging_invalid = (
        "build-system" in metadata
        or metadata["tool"]["uv"].get("package") is not False
        or "pywikibot[mwoauth]" not in metadata["project"]["dependencies"]
    )
    runtime_invalid = (
        metadata["project"]["requires-python"] != ">=3.15"
        or lock.get("requires-python") not in {">=3.15", "==3.15.*"}
        or payload[".python-version"].decode().strip() != "3.15.0"
    )
    if (
        packaging_invalid
        or runtime_invalid
        or not re.fullmatch(
            r"\d+\.\d+\.\d+(?:\.(?:dev|post)\d+)?",
            version,
        )
    ):
        msg = "Expected locked Python 3.15 source deployment metadata."
        raise ValueError(msg)
    for name in _source_names(
        root,
        payload["scripts/toolforge/source-files.json"],
    ):
        payload[name] = _read_regular(root, name)
    for name in (
        "src/webservice/public_html/index.php",
        "src/webservice/status.php",
        "src/backend/website/maintenance.json",
        "src/shared/config.py",
    ):
        if name not in payload:
            msg = f"Incomplete deployment: {name}"
            raise ValueError(msg)
    payload["src/shared/version.json"] = json.dumps({
        "schema": 1,
        "version": version,
    }).encode()
    payload["src/shared/routines.json"] = json.dumps(
        config.routine_configuration(),
        ensure_ascii=False,
    ).encode()
    payload["deployment.json"] = json.dumps(
        {
            "schema": 1,
            "version": version,
            "files": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in sorted(payload.items())
            },
        },
        sort_keys=True,
    ).encode()
    return dict(sorted(payload.items()))


def write_archive(stream: BinaryIO, payload: Mapping[str, bytes]) -> None:
    """Write regular-file source archives with fixed modes.

    Args:
        stream: Writable binary archive stream.
        payload: Validated relative names and complete contents.

    Raises:
        ValueError: An archive name is unsafe or points to private data.
    """  # ruff: ignore[docstring-extraneous-exception]
    with tarfile.open(
        fileobj=stream,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as archive:
        for name, data in sorted(payload.items()):
            _relative_path(name)
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o750 if name.endswith(".sh") else 0o640
            archive.addfile(info, io.BytesIO(data))


def main() -> None:
    """Stage sources without starting either application."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--archive", type=Path)
    arguments = parser.parse_args()
    payload = source_payload()
    target = os.environ.get(
        "TOOLFORGE_SSH_TARGET",
        "for-each-next@login.toolforge.org",
    )
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", target):
        parser.error(
            "TOOLFORGE_SSH_TARGET must name an SSH host or user@host.",
        )
    print("\n".join(payload))  # ruff: ignore[print]
    manifest = payload["deployment.json"]
    version = json.loads(manifest)["version"]
    digest = hashlib.sha256(manifest).hexdigest()
    print(  # ruff: ignore[print]
        f"Aranami {version}; source manifest SHA-256: {digest}",
    )
    if arguments.archive is not None:
        with arguments.archive.open("xb") as stream:
            write_archive(stream, payload)
    if arguments.dry_run:
        return
    ssh = shutil.which("ssh")
    if ssh is None:
        parser.error("OpenSSH is required for upload.")
    options = [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    hosts = os.environ.get("TOOLFORGE_KNOWN_HOSTS")
    if hosts:
        if not Path(hosts).is_file():
            parser.error("TOOLFORGE_KNOWN_HOSTS must be a readable file.")
        options += [
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={hosts}",
        ]
    staging = f"/data/project/aranami/app-staging-{uuid4().hex}"
    command = (
        f"become aranami sh -c 'umask 027; mkdir {staging} "
        f"&& tar --no-same-owner -xf - -C {staging}'"
    )
    with tempfile.TemporaryFile() as archive:
        write_archive(archive, payload)
        archive.seek(0)
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [*options, target, command],
            stdin=archive,
            check=True,
        )
    print(f"Staged: {staging}")  # ruff: ignore[print]
    print(  # ruff: ignore[print]
        f"Promote: become aranami sh {staging}/scripts/toolforge/"
        f"deploy-source.sh {staging}",
    )


if __name__ == "__main__":
    main()
