"""Upload the built Aranami wheel and its Toolforge setup script.

Run ``uv run python scripts/upload_toolforge.py`` from the repository.
Use ``--dry-run`` to check the filename and version without uploading.
The upload reuses your SSH configuration and never starts the bot.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tarfile
import tempfile
from email.parser import BytesParser
from pathlib import Path
from zipfile import BadZipFile, ZipFile

SSH_TARGET = "for-each-next@login.toolforge.org"
TOOL_DIR = "/data/project/aranami"


def _find_wheel(dist_dir: Path) -> tuple[Path, str]:
    """Find one wheel and verify its name against its package metadata.

    Args:
        dist_dir: Directory containing the locally built wheel.

    Returns:
        The wheel path and its declared package version.

    Raises:
        ValueError: A unique, valid Aranami wheel cannot be selected.
    """
    wheels = sorted(dist_dir.glob("aranami-*.whl"))
    if len(wheels) != 1:
        message = (
            f"Expected exactly one regular Aranami wheel in {dist_dir}; "
            f"found {len(wheels)} candidates. Keep only the intended wheel."
        )
        raise ValueError(message)
    wheel = wheels[0]
    if wheel.is_symlink() or not wheel.is_file():
        message = f"Not a regular wheel file: {wheel}"
        raise ValueError(message)
    with ZipFile(wheel) as archive:
        metadata_files = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            message = "The wheel must contain exactly one package METADATA."
            raise ValueError(message)
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
    version = metadata.get("Version", "")
    if (
        metadata.get("Name", "").casefold() != "aranami"
        or not version
        or not wheel.name.startswith(f"aranami-{version}-")
        or any(
            not (
                character.isascii()
                and (character.isalnum() or character in "_.+-")
            )
            for character in wheel.name
        )
    ):
        message = (
            "The wheel filename and Aranami package metadata do not match."
        )
        raise ValueError(message)
    return wheel, version


def _upload(wheel: Path, setup_script: Path) -> None:
    """Upload code and the wheel filename as the tool account.

    Args:
        wheel: Validated wheel to upload.
        setup_script: Server setup script bundled beside this uploader.

    Raises:
        FileNotFoundError: No SSH client is available.
    """
    ssh = shutil.which("ssh")
    if ssh is None:
        message = "OpenSSH is required; ssh was not found."
        raise FileNotFoundError(message)
    with tempfile.TemporaryFile() as upload:
        with tarfile.open(
            fileobj=upload,
            mode="w",
            format=tarfile.GNU_FORMAT,
        ) as archive:
            for path, name, mode in (
                (setup_script, "setup-env.sh", 0o750),
                (wheel, wheel.name, 0o640),
            ):
                info = archive.gettarinfo(str(path), arcname=name)
                info.mode = mode
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                content = path.read_bytes()
                if path == setup_script:
                    content = content.replace(b"\r\n", b"\n")
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            manifest = (wheel.name + "\n").encode("utf-8")
            info = tarfile.TarInfo("aranami-wheel.txt")
            info.mode = 0o600
            info.size = len(manifest)
            archive.addfile(info, io.BytesIO(manifest))
        upload.seek(0)
        ssh_environment = os.environ.copy()
        ssh_environment.pop("LC_CTYPE", None)
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [
                ssh,
                SSH_TARGET,
                f"become aranami tar --no-same-owner -xf - -C {TOOL_DIR}",
            ],
            stdin=upload,
            env=ssh_environment,
            check=True,
        )


def main() -> None:
    """Check the wheel and upload it unless dry-run was requested."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parent.parent
    setup_script = repository / "scripts" / "toolforge" / "setup-env.sh"
    if setup_script.is_symlink() or not setup_script.is_file():
        parser.exit(1, f"Missing regular setup script: {setup_script}\n")
    try:
        wheel, version = _find_wheel(repository / "dist")
    except (OSError, ValueError, BadZipFile) as error:
        parser.exit(1, f"Invalid wheel: {error}\n")
    print(f"Wheel: {wheel.name}\nVersion: {version}\nServer: {SSH_TARGET}")  # ruff: ignore[print]
    if args.dry_run:
        return
    try:
        _upload(wheel, setup_script)
    except (
        OSError,
        ValueError,
        BadZipFile,
        subprocess.CalledProcessError,
    ) as error:
        parser.exit(1, f"Upload failed: {error}\n")
    print("Upload complete. Continue with the installation job on Toolforge.")  # ruff: ignore[print]


if __name__ == "__main__":
    main()
