"""Verify source promotion and recovery with fake job commands."""

# Exercise shell commands offline with isolated filesystem targets.

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, override
from unittest import TestCase

if TYPE_CHECKING:
    from types import ModuleType

_ROOT = Path(__file__).resolve().parents[2]
_OBSOLETE_CACHE = "cache/toolforge/quality-video-games-all.parquet"
_FAKE_COMMAND = r'''
"""Emulate only the deployment's offline Toolforge and Kubernetes calls."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["ARANAMI_TOOL_DIR"])
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "commands.jsonl").open("a") as stream:
    stream.write(json.dumps([name, *args]) + "\n")
if name == "python3":
    if (len(args) > 1 and args[1] == "configure-web"
            and os.environ.get("FAKE_CONFIG_FAILURE")):
        raise SystemExit(17)
    os.execv(os.environ["TEST_PYTHON"], [os.environ["TEST_PYTHON"], *args])
if name == "kubectl":
    if os.environ.get("FAKE_PODS_STUCK"):
        if args[0] == "get":
            print("pod/still-running")
        else:
            raise SystemExit(9)
    raise SystemExit(0)
if name == "uv":
    if args == ["--version"]:
        print("uv 0.13.3")
    elif args[:2] == ["python", "install"]:
        path = root / "python/cpython-3.15.0-test/bin/python3.15"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("managed interpreter fixture")
    elif args[:2] == ["python", "find"]:
        path = root / "python/cpython-3.15.0-test/bin/python3.15"
        if os.environ.get("FAKE_UV_PHYSICAL_PATH"):
            path = path.resolve()
        print(path)
    elif args[0] == "sync":
        path = Path(os.environ["UV_PROJECT_ENVIRONMENT"]) / "bin/python"
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\nexec "
                        + shlex.quote(os.environ["TEST_PYTHON"]) + ' "$@"\n')
        path.chmod(0o750)
    else:
        raise AssertionError(args)
    raise SystemExit(0)
assert name == "toolforge", name
if args[:2] == ["jobs", "dump"]:
    Path(args[args.index("-f") + 1]).write_text("jobs-before-promotion\n")
elif args[:2] == ["jobs", "show"]:
    assert args[2] in {"aranami", "aranami-dashboard", "aranami-logs"}
elif args[:2] == ["jobs", "delete"]:
    pass
elif args[:2] == ["jobs", "run"]:
    mode = os.environ.get("FAKE_SETUP_MODE", "success")
    if mode == "silent-failure":
        raise SystemExit(0)
    if mode == "failure":
        raise SystemExit(12)
    command = args[args.index("--command") + 1]
    result = subprocess.run(["sh", "-c", command], check=False)
    raise SystemExit(result.returncode)
else:
    raise AssertionError(args)
'''


def _uploader() -> ModuleType:
    """Load exact reviewed payload generation without invoking SSH.

    Returns:
        Maintained source uploader module.
    """
    spec = importlib.util.spec_from_file_location(
        "deployment_shell_uploader",
        _ROOT / "scripts/upload_toolforge.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDeploymentShell(TestCase):
    """Run actual deployment scripts without SSH or production paths."""

    @override
    def setUp(self) -> None:
        """Create old source, runtime data, and fake commands."""
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory).resolve()
        self.payload = _uploader().source_payload()
        commands = self.root / "fake-bin"
        commands.mkdir()
        for name in ("toolforge", "kubectl", "python3"):
            path = commands / name
            path.write_text(f"#!{sys.executable}\n" + _FAKE_COMMAND)
            path.chmod(0o750)
        uv = self.root / "uv-bootstrap/bin/uv"
        uv.parent.mkdir(parents=True)
        uv.write_text(f"#!{sys.executable}\n" + _FAKE_COMMAND)
        uv.chmod(0o750)
        self.env = {
            **os.environ,
            "ARANAMI_TOOL_DIR": str(self.root),
            "TEST_PYTHON": sys.executable,
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
        }
        for name, text in {
            "app/old-source.txt": "current source",
            "app.previous/older-source.txt": "older source",
            "pyvenv-3.15/old-environment.txt": "prepared old environment",
            "public_html/old-index.txt": "previous website",
            "logs/keep.log": "retained log",
            "reports/keep.parquet": "retained report",
            "cache/keep.parquet": "retained cache",
            _OBSOLETE_CACHE: "obsolete badges",
            "user-config.py": "# private account configuration\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.routing = (
            "# unrelated local configuration\nserver.max-connections = 25\n"
            f'include "{self.root}/log-viewer.lighttpd.conf"\n'
            f'  include "{self.root}/app/src/webservice/'
            'log-viewer.lighttpd.conf"\n'
        )
        (self.root / ".lighttpd.conf").write_text(self.routing)

    def _stage(self, token: str = "a" * 32) -> Path:
        """Materialize one exact staged source payload.

        Returns:
            Staging root accepted by the real deployment script.
        """
        stage = self.root / f"app-staging-{token}"
        for name, content in self.payload.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return stage

    def _deploy(
        self,
        stage: Path,
        **environment: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run the promotion script against the isolated fake CLI.

        Returns:
            Captured shell result with no external requests.
        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [
                "/bin/sh",
                str(stage / "scripts/toolforge/deploy-source.sh"),
                str(stage),
            ],
            env={**self.env, **environment},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def _commands(self) -> list[list[str]]:
        """Read actual offline commands issued by the deployment.

        Returns:
            Ordered command arguments observed by the fake executables.
        """
        path = self.root / "commands.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_hash_failure_precedes_every_job_and_source_mutation(self) -> None:
        """Reject corrupt staged bytes before stopping applications."""
        stage = self._stage()
        (stage / "src/shared/config.py").write_text("corrupt staged file")
        result = self._deploy(stage)
        assert result.returncode != 0
        assert "hash" in result.stderr
        assert all(command[0] == "python3" for command in self._commands())
        assert (
            self.root / "app/old-source.txt"
        ).read_text() == "current source"
        assert not (self.root / "deployment.lock").exists()

    def test_wait_exit_without_success_receipt_keeps_running_source(
        self,
    ) -> None:
        """Require setup proof even when the job CLI reports success."""
        result = self._deploy(self._stage(), FAKE_SETUP_MODE="silent-failure")
        assert result.returncode != 0
        assert (self.root / "app/old-source.txt").exists()
        assert (self.root / "app.previous/older-source.txt").exists()
        assert (self.root / ".lighttpd.conf").read_text() == self.routing
        deleted = [
            command[-1]
            for command in self._commands()
            if command[:3] == ["toolforge", "jobs", "delete"]
        ]
        assert not {
            "aranami",
            "aranami-dashboard",
            "aranami-logs",
        }.intersection(deleted)

    def test_setup_failure_leaves_old_source_and_runtime_data_untouched(
        self,
    ) -> None:
        """Keep old applications available when staged setup fails."""
        result = self._deploy(self._stage(), FAKE_SETUP_MODE="failure")
        assert result.returncode != 0
        assert (self.root / "app/old-source.txt").exists()
        assert (self.root / "pyvenv-3.15/old-environment.txt").exists()
        assert (
            self.root / "reports/keep.parquet"
        ).read_text() == "retained report"

    def test_success_validates_before_stop_and_keeps_publication_stopped(
        self,
    ) -> None:
        """Promote a complete generation and preserve private data."""
        result = self._deploy(self._stage())
        assert result.returncode == 0, result.stderr
        assert (self.root / "app/deployment.json").is_file()
        assert (self.root / "app.previous/old-source.txt").exists()
        assert (self.root / "public_html.previous/old-index.txt").exists()
        assert (self.root / "public_html").readlink() == Path(
            "app/src/webservice/public_html",
        )
        assert (self.root / "pyvenv-3.15").is_symlink()
        assert (self.root / "logs/keep.log").read_text() == "retained log"
        assert (
            self.root / "cache/keep.parquet"
        ).read_text() == "retained cache"
        assert (
            self.root / "user-config.py"
        ).read_text() == "# private account configuration\n"
        routing = (self.root / ".lighttpd.conf").read_text()
        assert routing.count("include ") == 1
        assert "server.max-connections = 25" in routing
        assert not (
            self.root / "cache/toolforge/quality-video-games-all.parquet"
        ).exists()
        assert list(
            (self.root / "deployment-backups").glob(
                "*/retired-cache/quality-video-games-all.parquet",
            ),
        )
        commands = self._commands()
        run = next(
            index
            for index, command in enumerate(commands)
            if command[:3] == ["toolforge", "jobs", "run"]
        )
        stopped = next(
            index
            for index, command in enumerate(commands)
            if command == ["toolforge", "jobs", "delete", "aranami"]
        )
        assert run < stopped
        started = [
            command
            for command in commands
            if command[:3] == ["toolforge", "jobs", "run"]
        ]
        assert len(started) == 1
        assert started[0][3].startswith("aranami-setup-")
        assert "Applications remain stopped" in result.stdout

    def test_post_promotion_failure_restores_source_environment_and_routing(
        self,
    ) -> None:
        """Restore predecessors without starting applications."""
        result = self._deploy(self._stage(), FAKE_CONFIG_FAILURE="1")
        assert result.returncode != 0
        assert (self.root / "app/old-source.txt").exists()
        assert (self.root / "app.previous/older-source.txt").exists()
        assert (self.root / "pyvenv-3.15/old-environment.txt").exists()
        assert (self.root / "public_html/old-index.txt").exists()
        assert (self.root / ".lighttpd.conf").read_text() == self.routing
        assert not (self.root / "active-deployment.json").exists()
        assert list(
            (self.root / "deployment-backups").glob(
                "*/rejected-app/deployment.json",
            ),
        )

    def test_repeated_promotions_keep_current_and_older_predecessors(
        self,
    ) -> None:
        """Preserve predecessors across later source uploads."""
        first = self._deploy(self._stage())
        assert first.returncode == 0, first.stderr
        first_environment = (self.root / "pyvenv-3.15").resolve()
        second = self._deploy(self._stage("b" * 32))
        assert second.returncode == 0, second.stderr
        assert (self.root / "app.previous/deployment.json").exists()
        assert first_environment.exists()
        assert (self.root / "pyvenv-3.15").resolve() != first_environment
        assert list(
            (self.root / "deployment-backups").glob(
                "*/older-app/old-source.txt",
            ),
        )
        assert (self.root / ".lighttpd.conf").read_text().count(
            "include ",
        ) == 1

    def test_stuck_application_pods_prevent_source_promotion(self) -> None:
        """Require applications to stop before promotion."""
        result = self._deploy(self._stage(), FAKE_PODS_STUCK="1")
        assert result.returncode != 0
        assert (self.root / "app/old-source.txt").exists()
        assert (self.root / "app.previous/older-source.txt").exists()

    def test_toolforge_mount_alias_keeps_portable_receipt_paths(self) -> None:
        """Accept mount aliases without saving host-only paths."""
        stage = self._stage()
        alias_directory = Path(
            self.enterContext(TemporaryDirectory()),
        ).resolve()
        mount = alias_directory / "project"
        mount.symlink_to(self.root.parent, target_is_directory=True)
        home = mount / self.root.name
        result = self._deploy(
            home / stage.name,
            ARANAMI_TOOL_DIR=str(home),
            FAKE_UV_PHYSICAL_PATH="1",
        )
        assert result.returncode == 0, result.stderr
        evidence = json.loads(
            (self.root / "active-deployment.json").read_text(),
        )
        assert Path(evidence["source"]).parent == home
        assert Path(evidence["environment"]).parent == home / "deployment-envs"
        assert (self.root / ".lighttpd.conf").read_text().count(
            "include ",
        ) == 1

    def test_source_symlink_inside_tool_home_is_rejected(self) -> None:
        """Reject staged source symlinks inside the tool home."""
        stage = self._stage()
        retained = self.root / "retained-stage"
        stage.rename(retained)
        stage.symlink_to(retained, target_is_directory=True)
        result = self._deploy(stage)
        assert result.returncode != 0
        assert "canonical source directory" in result.stderr
        assert (self.root / "app/old-source.txt").exists()
        assert not (self.root / "deployment.lock").exists()

    def test_runtime_directory_symlink_is_rejected_before_setup(self) -> None:
        """Reject runtime directory symlinks inside the tool home."""
        stage = self._stage()
        for name in ("deployment-backups", "deployment-envs"):
            with self.subTest(name=name), TemporaryDirectory() as directory:
                linked = self.root / name
                linked.symlink_to(directory, target_is_directory=True)
                try:
                    result = self._deploy(stage)
                    assert result.returncode != 0
                    assert (self.root / "app/old-source.txt").exists()
                    assert not (self.root / "deployment.lock").exists()
                    assert not list(Path(directory).iterdir())
                finally:
                    linked.unlink()

    @staticmethod
    def test_bootstrap_validator_parses_bastion_grammar() -> None:
        """Parse the bootstrap helper with the older bastion grammar."""
        path = _ROOT / "scripts/toolforge/validate-source.py"
        ast.parse(
            path.read_text(),
            filename=str(path),
            feature_version=(3, 11),
        )
