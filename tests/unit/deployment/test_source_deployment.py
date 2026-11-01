"""Verify reviewed source uploads and isolated application imports."""

# Exercise private transport seams with temporary local fixtures.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

from __future__ import annotations

import ast
import gzip
import hashlib
import importlib.util
import io
import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tarfile
import tomllib
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import patch

from shared import deployment

if TYPE_CHECKING:
    from types import ModuleType
    from typing import BinaryIO

_ROOT = Path(__file__).resolve().parents[3]
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


def _validator() -> ModuleType:
    """Load the current bootstrap validator for older source fixtures.

    Returns:
        Current validator without running its command-line entry point.
    """
    spec = importlib.util.spec_from_file_location(
        "source_deployment_validator",
        _ROOT / "scripts/toolforge/validate-source.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generation_without_drain(
    root: Path,
    payload: dict[str, bytes],
    version: str,
) -> dict[str, object]:
    """Write a complete hashed generation using the older helper layout.

    Args:
        root: Canonical isolated source directory.
        payload: Current reviewed sources used to populate the fixture.
        version: Version declared consistently by the fixture metadata.

    Returns:
        Deployment manifest with every retained file's actual digest.
    """
    files = {
        name: content
        for name, content in payload.items()
        if name not in {"deployment.json", "scripts/toolforge/drain-hub.py"}
    }
    previous = tomllib.loads(files["pyproject.toml"].decode())["project"][
        "version"
    ]
    for name in ("pyproject.toml", "uv.lock"):
        files[name] = files[name].replace(previous.encode(), version.encode())
    manifest = {
        "schema": 1,
        "version": version,
        "files": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in files.items()
        },
    }
    _fixture(root, files)
    (root / "deployment.json").write_text(json.dumps(manifest))
    return manifest


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


def _development_fixture(root: Path, payload: dict[str, bytes]) -> None:
    """Copy authored build inputs and a verified disposable web tree."""
    receipt = json.loads((_ROOT / "dist/webservice/build.json").read_bytes())
    files = {
        name: data
        for name, data in payload.items()
        if not name.startswith((
            "src/webservice/public_html/",
            "src/webservice/templates/",
        ))
    }
    files.update({
        name: (_ROOT / name).read_bytes() for name in receipt["inputs"]
    })
    files.update({
        f"dist/webservice/{name}": (
            _ROOT / "dist/webservice" / name
        ).read_bytes()
        for name in receipt["outputs"]
    })
    files["dist/webservice/build.json"] = json.dumps(receipt).encode()
    _fixture(root, files)


def _archive_contents(content: bytes) -> dict[str, bytes]:
    """Read exact bytes from regular-file archive members.

    Args:
        content: In-memory tar bytes passed to the fake SSH process.

    Returns:
        Exact member filenames and bytes.
    """
    with tarfile.open(
        fileobj=io.BytesIO(gzip.decompress(content)),
        mode="r:",
    ) as archive:
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

    def test_validator_rejects_unsupported_bootstrap_layout(
        self,
    ) -> None:
        """Reject generations lacking the supported drain protocol."""
        validator = _validator()
        for version in ("0.7.10", "0.7.10.post1", "0.7.10.post12"):
            with (
                self.subTest(version=version),
                TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                _generation_without_drain(
                    root,
                    self.payload,
                    version,
                )
                assert not (root / "scripts/toolforge/drain-hub.py").exists()
                with self.assertRaisesRegex(ValueError, "reviewed payload"):
                    validator.verify(root)

    def test_validator_requires_drain_in_current_source_payload(self) -> None:
        """Reject omitted helpers despite a consistent hash manifest."""
        current = json.loads(self.payload["deployment.json"])["version"]
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _generation_without_drain(root, self.payload, current)
            with self.assertRaisesRegex(ValueError, "reviewed payload"):
                _validator().verify(root)

    def test_both_boundaries_reject_inconsistent_source_metadata(self) -> None:
        """Reject invalid metadata even when file hashes match."""
        manifest = json.loads(self.payload["deployment.json"])
        version = manifest["version"]
        lock_identity = f'name = "aranami"\nversion = "{version}"'.encode()
        cases = (
            (
                "uv.lock",
                lock_identity,
                b'name = "aranami"\nversion = "99.99.99"',
            ),
            ("uv.lock", b'name = "aranami"', b'name = "missing"'),
            ("uv.lock", b'virtual = "."', b'path = "."'),
            (
                "uv.lock",
                b'requires-python = ">=3.15"',
                b'requires-python = ">=3.14"',
            ),
            (
                "pyproject.toml",
                f'version = "{version}"'.encode(),
                b'version = "garbage"',
            ),
            ("pyproject.toml", b'"pywikibot[mwoauth]"', b'"pywikibot"'),
        )
        for name, before, after in cases:
            with (
                self.subTest(name=name, changed=after),
                TemporaryDirectory() as directory,
            ):
                payload = dict(self.payload)
                assert before in payload[name]
                payload[name] = payload[name].replace(before, after, 1)
                root = Path(directory).resolve()
                _fixture(root, payload)
                changed = {
                    **manifest,
                    "version": "garbage"
                    if after == b'version = "garbage"'
                    else version,
                    "files": {
                        path: hashlib.sha256(data).hexdigest()
                        for path, data in payload.items()
                        if path != "deployment.json"
                    },
                }
                (root / "deployment.json").write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, "locked Python 3.15"):
                    self.uploader.source_payload(root)
                with self.assertRaisesRegex(ValueError, "locked Python 3.15"):
                    _validator().verify(root)

    def test_metadata_declares_locked_production_web_dependencies(
        self,
    ) -> None:
        """Declare locked web dependencies and project metadata."""
        metadata = tomllib.loads(self.payload["pyproject.toml"].decode())
        project = metadata["project"]
        assert {"fastapi", "jinja2", "uvicorn", "websockets"} <= set(
            project["dependencies"],
        )
        assert {
            "Development Status :: 4 - Beta",
            "Environment :: Web Environment",
            "Framework :: FastAPI",
        } <= set(project["classifiers"])
        assert (
            "Development Status :: 1 - Planning"
            not in (project["classifiers"])
        )
        assert metadata["tool"]["uv"]["package"] is False
        lock = tomllib.loads(self.payload["uv.lock"].decode())
        packages = {package["name"] for package in lock["package"]}
        assert {"fastapi", "jinja2", "uvicorn", "websockets"} <= packages
        assert not {"django", "flask", "werkzeug", "waitress"} & packages

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
        assert "src/webservice/pages.py" in self.payload
        assert "src/webservice/templates/base.html" in self.payload
        png_files = {
            name for name in self.payload if Path(name).suffix == ".png"
        }
        assert png_files == {
            "src/webservice/public_html/assets/images/aranami-icon.png",
            "src/webservice/public_html/assets/images/aranami-wordmark.png",
            "src/webservice/public_html/assets/images/favicon.png",
        }
        assert all(
            self.payload[name].startswith(b"\x89PNG\r\n\x1a\n")
            for name in png_files
        )
        assert {
            Path(name).parts[1]
            for name in self.payload
            if name.startswith("src/")
        } == _PACKAGES
        assert (
            'exec "$runtime_python" -m backend "$@"'
            in self.payload["scripts/toolforge/run-hub.sh"].decode()
        )
        assert (
            "umask 077\n"
            in self.payload["scripts/toolforge/run-hub.sh"].decode()
        )
        assert (
            "--mem 4Gi"
            in self.payload["scripts/toolforge/deploy-source.sh"].decode()
        )
        assert not any(
            Path(name).suffix in {".php", ".mjs"} for name in self.payload
        )
        assert {
            name for name in self.payload if Path(name).suffix == ".js"
        } == {
            "src/webservice/public_html/components.js",
            "src/webservice/public_html/live.js",
            "src/webservice/public_html/navigation.js",
            "src/webservice/public_html/newsletter.js",
        }
        assert (
            "-m backend.public --host 0.0.0.0 --port 8000 "
            '--runtime "$tool_dir"'
            in self.payload["scripts/toolforge/run-web.sh"].decode()
        )
        for name in ("run-hub.sh", "run-web.sh"):
            assert (
                '. "$tool_dir/app/scripts/toolforge/runtime-env.sh"'
                in self.payload[f"scripts/toolforge/{name}"].decode()
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
            archive = Path(directory) / "source.tar.gz"
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

    def test_compressed_archive_is_reproducible_and_extractable(self) -> None:
        """Keep archive bytes stable and preserve extracted payloads."""
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.tar.gz"
            second = Path(directory) / "second.tar.gz"
            for path in (first, second):
                with path.open("wb") as stream:
                    self.uploader.write_archive(stream, self.payload)
            compressed = first.read_bytes()
            assert compressed == second.read_bytes()
            assert compressed[:8] == b"\x1f\x8b\x08\x00\x00\x00\x00\x00"
            assert len(compressed) < len(gzip.decompress(compressed))
            extracted = Path(directory) / "source"
            with tarfile.open(first, mode="r:gz") as archive:
                archive.extractall(extracted, filter="data")
            assert self.uploader.source_payload(extracted) == self.payload
            _validator().verify(extracted)

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
                assert "tar --no-same-owner -xzf - -C" in arguments[-1]
                assert check is True
                assert _archive_contents(stdin.read()) == self.payload

            remote.side_effect = capture
            self.uploader.main()
        remote.assert_called_once()


class TestWebBuildPayload(TestCase):
    """Require current builds and preserve the deployed source tree."""

    def setUp(self) -> None:
        """Prepare one reviewed payload and an isolated checkout."""
        self.uploader = _uploader()
        self.payload = self.uploader.source_payload()
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        _development_fixture(self.root, self.payload)

    def test_build_maps_to_runtime_paths_without_build_dependencies(
        self,
    ) -> None:
        """Upload checked outputs while keeping source paths stable."""
        assert self.uploader.source_payload(self.root) == self.payload
        assert not any(name.startswith("dist/") for name in self.payload)
        assert "package.json" not in self.payload
        assert not any(name.startswith("frontend/") for name in self.payload)
        assert not (self.root / "src/webservice/public_html/live.js").exists()

    def test_missing_receipt_and_stale_inputs_are_rejected(self) -> None:
        """Require new build evidence after authored sources change."""
        receipt = self.root / "dist/webservice/build.json"
        original = receipt.read_bytes()
        receipt.unlink()
        with self.assertRaisesRegex(ValueError, "Missing regular"):
            self.uploader.source_payload(self.root)
        receipt.write_bytes(original)
        source = self.root / "frontend/live.ts"
        source.write_text(source.read_text() + "\n// Changed input.\n")
        with self.assertRaisesRegex(ValueError, "Stale web build"):
            self.uploader.source_payload(self.root)

    def test_changed_missing_and_unreviewed_outputs_are_rejected(self) -> None:
        """Check content, completeness, and review independently."""
        script = self.root / "dist/webservice/public_html/live.js"
        original = script.read_bytes()
        script.write_bytes(original + b"\n// Changed output.\n")
        with self.assertRaisesRegex(ValueError, "Stale web build"):
            self.uploader.source_payload(self.root)
        script.unlink()
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            self.uploader.source_payload(self.root)
        script.write_bytes(original)
        extra = self.root / "dist/webservice/public_html/unreviewed.js"
        extra.write_bytes(b"'unreviewed';")
        receipt_path = self.root / "dist/webservice/build.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["outputs"]["public_html/unreviewed.js"] = hashlib.sha256(
            extra.read_bytes(),
        ).hexdigest()
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "reviewed payload"):
            self.uploader.source_payload(self.root)

    def test_omitted_inputs_and_linked_build_ancestors_are_rejected(
        self,
    ) -> None:
        """Reject incomplete receipts and linked output directories."""
        receipt_path = self.root / "dist/webservice/build.json"
        original = receipt_path.read_bytes()
        receipt = json.loads(original)
        del receipt["inputs"]["frontend/live.ts"]
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            self.uploader.source_payload(self.root)
        receipt_path.write_bytes(original)
        build = self.root / "dist"
        build.rename(self.root / "linked-build")
        build.symlink_to(self.root / "linked-build", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.uploader.source_payload(self.root)

    def test_resource_resolution_never_uses_partial_authored_tree(
        self,
    ) -> None:
        """Select dist locally and package resources in deployments."""
        with patch.object(
            deployment,
            "__file__",
            str(self.root / "src/shared/deployment.py"),
        ):
            assert deployment.resource_root("webservice") == (
                self.root / "dist/webservice"
            )
            (self.root / "dist").rename(self.root / "missing-dist")
            assert not deployment.resource_root("webservice").exists()
            (self.root / "package.json").unlink()
            assert deployment.resource_root("webservice") == (
                self.root / "src/webservice"
            )

    def test_deployed_pages_render_without_node_or_dist(self) -> None:
        """Load the archive using only its pinned Python runtime."""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _fixture(root, self.payload)
            code = (
                "from shared.deployment import resource_root; "
                "from webservice.pages import render_about; "
                "assert resource_root('webservice').name == 'webservice'; "
                "assert '/styles.css?v=' in render_about(None, 'en')"
            )
            result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [
                    sys.executable,
                    "-c",
                    code,
                ],
                cwd=root,
                env={
                    **os.environ,
                    "PYTHONPATH": str(root / "src"),
                    "PYWIKIBOT_NO_USER_CONFIG": "2",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert not (root / "dist").exists()
            assert not (root / "node_modules").exists()


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
                path_forbidden = (
                    forbidden - {"webservice"}
                    if path == _ROOT / "src/backend/public.py"
                    else forbidden
                )
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
                        not {name.split(".")[0] for name in names}
                        & path_forbidden
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
from updater.edit_summary import EditSummary
assert issubclass(JobContext, RuntimeContext)
assert key_articles.__name__ == "backend.services.enwiki.key_articles"
assert report_membership.__name__ == "backend.support.report_membership"
assert regions.__name__ == "shared.support.regions"
for name in TASK_DEFINITIONS:
    importlib.import_module(f"updater.jobs.{name}")
import backend.__main__
import backend.monitor as monitor
import backend.server
import backend.public
import backend.hub
import webservice.pages
for name in ("logs", "routine_status", "schedule_status"):
    importlib.import_module(f"backend.support.{name}")
    assert importlib.util.find_spec(f"shared.support.{name}") is None
assert importlib.util.find_spec("wiki_report_updater") is None
assert importlib.util.find_spec("backend.jobs") is None
assert importlib.util.find_spec("updater.services.enwiki") is None
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
