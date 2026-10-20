"""Verify reviewed source uploads and isolated application imports."""

# Exercise private transport seams with temporary local fixtures.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tarfile
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import patch

if TYPE_CHECKING:
    from types import ModuleType
    from typing import BinaryIO

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGES = {"backend", "updater", "webservice", "shared"}


def _uploader() -> ModuleType:
    """Load the maintained uploader without executing its CLI.

    Returns:
        Source uploader module with its exact production validation.
    """
    spec = importlib.util.spec_from_file_location(
        "source_deployment_uploader",
        _ROOT / "scripts/upload_toolforge.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(root: Path, payload: dict[str, bytes]) -> None:
    """Materialize reviewed source inputs in an isolated directory.

    Args:
        root: Temporary repository root.
        payload: Validated source upload used to seed the fixture.
    """
    for name, content in payload.items():
        if name == "deployment.json":
            continue
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _archive_contents(content: bytes) -> dict[str, bytes]:
    """Read exact bytes from regular-file archive members.

    Args:
        content: In-memory tar bytes passed to the fake SSH process.

    Returns:
        Exact member filenames and bytes.
    """
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        result = {}
        for member in archive.getmembers():
            assert member.isfile()
            assert not member.issym()
            assert not member.islnk()
            assert member.mode == (
                0o750 if member.name.endswith(".sh") else 0o640
            )
            stream = archive.extractfile(member)
            assert stream is not None
            result[member.name] = stream.read()
        return result


def _import_targets(node: ast.AST) -> tuple[str, ...]:
    """Resolve imported names for static responsibility checks.

    Args:
        node: An import or another node from an application module.

    Returns:
        Qualified imported targets, including from-import members.
    """
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return tuple(f"{module}.{alias.name}" for alias in node.names)
    return ()


class TestSourcePayload(TestCase):
    """Check reviewed sources, exact archives, and upload isolation."""

    def setUp(self) -> None:
        """Read the actual payload without touching runtime data."""
        self.uploader = _uploader()
        self.payload = self.uploader.source_payload()

    def test_payload_matches_reviewed_manifest_and_recorded_hashes(
        self,
    ) -> None:
        """Match reviewed files and recorded artifact hashes."""
        contract = json.loads(
            self.payload["scripts/toolforge/source-files.json"],
        )
        expected = set(contract["files"]) | {
            ".python-version",
            "pyproject.toml",
            "uv.lock",
            "deployment.json",
            *(f"scripts/toolforge/{name}" for name in self.uploader._SCRIPTS),
        }
        assert set(self.payload) == expected
        assert {
            Path(name).parts[1]
            for name in self.payload
            if name.startswith("src/")
        } == _PACKAGES
        assert (
            'exec "$tool_dir/pyvenv-3.15/bin/python" -m backend "$@"'
            in self.payload["scripts/toolforge/run-hub.sh"].decode()
        )
        manifest = json.loads(self.payload["deployment.json"])
        assert manifest["files"] == {
            name: hashlib.sha256(content).hexdigest()
            for name, content in self.payload.items()
            if name != "deployment.json"
        }
        stream = io.BytesIO()
        self.uploader.write_archive(stream, self.payload)
        assert _archive_contents(stream.getvalue()) == self.payload

    def test_runtime_credentials_and_retired_packages_stay_outside_upload(
        self,
    ) -> None:
        """Exclude runtime data and retired source trees."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root, self.payload)
            for name in (
                "user-config.py",
                "user-password.py",
                ".my.cnf",
                ".env",
                "logs/current.log",
                "cache/private.parquet",
                "reports/latest.json",
                "dry-run/proposal.wikitext",
                "src/aranami/__init__.py",
                "src/toolforge/__init__.py",
                "src/wiki_report_updater/__init__.py",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private runtime fixture", encoding="utf-8")
            assert self.uploader.source_payload(root) == self.payload

    def test_unreviewed_sources_and_symlink_ancestors_are_rejected(
        self,
    ) -> None:
        """Reject unreviewed sources and symlink ancestors."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root, self.payload)
            forged = root / "src/shared/unreviewed.py"
            forged.write_text('"""Unreviewed fixture."""\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reviewed payload"):
                self.uploader.source_payload(root)
            forged.unlink()
            (root / "src").rename(root / "real-source")
            (root / "src").symlink_to(
                root / "real-source",
                target_is_directory=True,
            )
            with self.assertRaisesRegex(ValueError, "symlinks"):
                self.uploader.source_payload(root)

    def test_unsafe_manifest_and_archive_paths_are_rejected(self) -> None:
        """Reject unsafe paths and duplicate contract entries."""
        for name in (
            "../secret",
            "/absolute",
            "user-config.py",
            "cache/data.json",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.uploader.write_archive(io.BytesIO(), {name: b"private"})
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root, self.payload)
            path = root / "scripts/toolforge/source-files.json"
            contract = json.loads(path.read_text())
            contract["files"].append(contract["files"][0])
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                self.uploader.source_payload(root)

    def test_dry_run_never_resolves_or_starts_ssh(self) -> None:
        """Inspect an archive without remote access."""
        with TemporaryDirectory() as directory:
            archive = Path(directory) / "source.tar"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "upload_toolforge.py",
                        "--dry-run",
                        "--archive",
                        str(archive),
                    ],
                ),
                patch.object(
                    self.uploader,
                    "source_payload",
                    return_value=self.payload,
                ),
                patch.object(self.uploader.shutil, "which") as which,
                patch.object(self.uploader.subprocess, "run") as remote,
                redirect_stdout(io.StringIO()),
            ):
                self.uploader.main()
            which.assert_not_called()
            remote.assert_not_called()
            assert _archive_contents(archive.read_bytes()) == self.payload

    def test_fake_ssh_receives_only_the_exact_staged_archive(self) -> None:
        """Stage exact source bytes through a fake SSH process."""
        with (
            patch.object(sys, "argv", ["upload_toolforge.py"]),
            patch.object(
                self.uploader,
                "source_payload",
                return_value=self.payload,
            ),
            patch.object(
                self.uploader.shutil,
                "which",
                return_value="/fake/ssh",
            ),
            patch.dict(
                os.environ,
                {
                    "TOOLFORGE_SSH_TARGET": "test@toolforge.invalid",
                    "TOOLFORGE_KNOWN_HOSTS": "",
                },
            ),
            patch.object(self.uploader.subprocess, "run") as remote,
            redirect_stdout(io.StringIO()),
        ):

            def capture(
                arguments: list[str],
                *,
                stdin: BinaryIO,
                check: bool,
            ) -> None:
                """Read the tar before its temporary stream closes.

                Args:
                    arguments: Complete fake SSH command arguments.
                    stdin: Rewound temporary source archive.
                    check: Require a successful subprocess exit.
                """
                assert arguments[0] == "/fake/ssh"
                assert arguments[-2] == "test@toolforge.invalid"
                assert (
                    "mkdir /data/project/aranami/app-staging-" in arguments[-1]
                )
                assert "deploy-source.sh" not in arguments[-1]
                assert check is True
                assert _archive_contents(stdin.read()) == self.payload

            remote.side_effect = capture
            self.uploader.main()
        remote.assert_called_once()


class TestSourceImports(TestCase):
    """Check application boundaries and source imports."""

    @staticmethod
    def test_backend_data_layers_do_not_depend_on_updater() -> None:
        """Keep data and runtime storage independent of publication."""
        for layer in ("services", "support"):
            for path in (_ROOT / "src/backend" / layer).rglob("*.py"):
                tree = ast.parse(path.read_text(), filename=str(path))
                for node in ast.walk(tree):
                    assert not any(
                        name == "updater" or name.startswith("updater.")
                        for name in _import_targets(node)
                    ), path

    @staticmethod
    def test_updater_services_only_render_prepared_data() -> None:
        """Prevent source clients and runtime writes in renderers."""
        forbidden_roots = {
            "os",
            "pathlib",
            "shutil",
            "tempfile",
            "requests",
            "urllib",
            "http",
            "socket",
        }
        write_methods = {
            "write_text",
            "write_bytes",
            "write_parquet",
            "write_csv",
            "write_json",
            "mkdir",
            "unlink",
            "rmdir",
            "rename",
            "rmtree",
            "open",
        }
        for path in (_ROOT / "src/updater/services").rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                for target in _import_targets(node):
                    assert not target.startswith("shared.sources"), path
                    assert target.split(".")[0] not in forbidden_roots, path
                    assert (
                        not target.startswith("pywikibot")
                        or target == "pywikibot.site.BaseSite"
                    ), path
                if isinstance(node, ast.Call):
                    assert not (
                        isinstance(node.func, ast.Name)
                        and node.func.id == "open"
                    ), path
                    assert not (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr in write_methods
                    ), path

    @staticmethod
    def test_jobs_only_read_destinations_and_refresh_pexbot() -> None:
        """Allow destination reads and intentional PexBot refreshes."""
        for path in (_ROOT / "src/updater/jobs").rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                for target in _import_targets(node):
                    if target.startswith("shared.sources"):
                        assert isinstance(node, ast.ImportFrom), path
                        assert node.module == "shared.sources.wiki", path
                        assert target == "shared.sources.wiki.read_pages", path
                    if target.startswith("pywikibot"):
                        assert (
                            path.name == "_execution.py"
                            or target == "pywikibot.site.BaseSite"
                        ), path
                    if target.split(".")[0] in {
                        "requests",
                        "urllib",
                        "http",
                        "socket",
                    }:
                        assert path.name == "pexbot.py", path

    @staticmethod
    def test_python315_ast_enforces_source_application_boundaries() -> None:
        """Inspect lazy imports using Python 3.15's AST."""
        assert sys.version_info[:2] == (3, 15)
        for package in _PACKAGES:
            allowed = (
                {"backend", "updater", "shared"}
                if package in {"backend", "updater"}
                else {package, "shared"}
            )
            forbidden = _PACKAGES - allowed
            forbidden |= {"aranami", "toolforge", "wiki_report_updater"}
            for path in (_ROOT / "src" / package).rglob("*.py"):
                tree = ast.parse(path.read_text(), filename=str(path))
                for node in ast.walk(tree):
                    names = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                        if isinstance(node, ast.ImportFrom)
                        else []
                    )
                    assert (
                        not {name.split(".")[0] for name in names} & forbidden
                    ), path
        public = ast.parse(
            (_ROOT / "src/backend/__init__.py").read_text(),
        )
        lazy = [
            node
            for node in ast.walk(public)
            if isinstance(node, ast.ImportFrom) and node.is_lazy
        ]
        assert any(node.module == "backend.runner" for node in lazy)

    @staticmethod
    def test_imports_and_attribution_need_no_distribution_or_wiki_client() -> (
        None
    ):
        """Block wheel lookup and wiki clients during imports."""
        code = """
import importlib
import importlib.metadata
import importlib.util
import pywikibot
original_version = importlib.metadata.version
def forbidden(*args, **kwargs):
    raise AssertionError("Import attempted external initialization")
def source_version(name):
    return forbidden() if name == "aranami" else original_version(name)
importlib.metadata.version = source_version
pywikibot.Site = forbidden
pywikibot.Page.__init__ = forbidden
from shared.config import TASK_DEFINITIONS
from shared.deployment import project_version
from backend.support.execution import RuntimeContext
from backend.services.enwiki import key_articles
from backend.support import report_membership
from shared.support import regions
from updater.jobs import JobContext
from updater.support.edit_summary import EditSummary
assert issubclass(JobContext, RuntimeContext)
assert key_articles.__name__ == "backend.services.enwiki.key_articles"
assert report_membership.__name__ == "backend.support.report_membership"
assert regions.__name__ == "shared.support.regions"
for name in TASK_DEFINITIONS:
    importlib.import_module(f"updater.jobs.{name}")
import backend.__main__
import backend.monitor as monitor
import backend.server
import backend.hub
for name in ("logs", "routine_status", "schedule_status"):
    importlib.import_module(f"backend.support.{name}")
    assert importlib.util.find_spec(f"shared.support.{name}") is None
assert importlib.util.find_spec("wiki_report_updater") is None
assert importlib.util.find_spec("backend.jobs") is None
assert importlib.util.find_spec("updater.services.enwiki") is None
assert importlib.util.find_spec("updater.support.regions") is None
assert importlib.util.find_spec("updater.support.report_membership") is None
assert importlib.util.find_spec("webservice.collect") is None
assert importlib.util.find_spec("webservice.server") is None
assert importlib.util.find_spec("webservice.__main__") is None
assert monitor._scheduler is None
assert project_version()
assert EditSummary.with_attribution("Checked.") == (
    "Checked. ([[toolforge:Aranami|Aranami]])"
)
"""
        with TemporaryDirectory() as directory:
            completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [sys.executable, "-c", code],
                cwd=directory,
                env={
                    **os.environ,
                    "PYTHONPATH": str(_ROOT / "src"),
                    "PYWIKIBOT_NO_USER_CONFIG": "2",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            assert completed.returncode == 0, completed.stderr
            assert not list(Path(directory).iterdir())
