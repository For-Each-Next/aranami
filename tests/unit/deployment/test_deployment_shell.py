"""Verify source promotion and recovery with fake job commands."""

# Exercise shell commands offline with isolated filesystem targets.

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, override
from unittest import TestCase

if TYPE_CHECKING:
    from types import ModuleType

_ROOT = Path(__file__).resolve().parents[3]
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
    if (len(args) > 1 and args[1] == "select"
            and os.environ.get("FAKE_SELECTION_FAILURE")):
        raise SystemExit(17)
    os.execv(os.environ["TEST_PYTHON"], [os.environ["TEST_PYTHON"], *args])
if name == "kubectl":
    stuck_service = os.environ.get("FAKE_STUCK_SERVICE")
    stuck = os.environ.get("FAKE_PODS_STUCK") or (
        stuck_service and stuck_service in " ".join(args)
    )
    state = root / "fake-replicas.json"
    replicas = json.loads(state.read_text()) if state.exists() else {
        "aranami-hub": os.environ.get("FAKE_HUB_REPLICAS", "1"),
        "aranami-web": os.environ.get("FAKE_WEB_REPLICAS", "1"),
    }
    if args[:2] == ["get", "deployment"]:
        print(replicas[args[2]], end="")
    elif args[:2] == ["get", "pods"]:
        selector = args[args.index("-l") + 1]
        if stuck:
            print("pod/still-running")
        else:
            for service, count in replicas.items():
                if service in selector and count not in {"", "0"}:
                    print(f"pod/{service}-old")
    elif args[0] == "scale":
        service = args[2]
        expected = next(arg.split("=", 1)[1] for arg in args
                        if arg.startswith("--current-replicas="))
        assert replicas[service] == expected, (args, replicas)
        replicas[service] = next(arg.split("=", 1)[1] for arg in args
                                 if arg.startswith("--replicas="))
        state.write_text(json.dumps(replicas))
    elif args[0] == "wait":
        if stuck:
            raise SystemExit(9)
    elif args[:2] == ["rollout", "status"]:
        assert (root / "app").is_symlink()
        if os.environ.get("FAKE_ROLLOUT_FAILURE"):
            raise SystemExit(10)
    elif args[0] == "exec":
        assert "drain-hub.py" in " ".join(args), args
        if os.environ.get("FAKE_DRAIN_FAILURE"):
            raise SystemExit(19)
        if os.environ.get("FAKE_DRAIN_UNSUPPORTED"):
            raise SystemExit(19)
    else:
        raise AssertionError(args)
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
    assert args[2] in {"aranami-hub", "aranami-web"}
elif args[:2] == ["jobs", "delete"]:
    assert args[2].startswith("aranami-setup-"), args
elif args[:2] == ["jobs", "restart"]:
    assert args[2] == "aranami-web", args
    assert (root / "app").is_symlink()
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


class TestDeploymentShell(TestCase):  # ruff: ignore[too-many-public-methods]
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
            "logs/keep.log": "retained log",
            "reports/keep.parquet": "retained report",
            "cache/keep.parquet": "retained cache",
            "user-config.py": "# private account configuration\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

        self.initial = self._stage("0" * 32)
        self.initial_config = (
            self.initial / "src/shared/config.py"
        ).read_text()
        environment = self.root / f"deployment-envs/source-{'0' * 32}"
        (environment / "bin").mkdir(parents=True)
        (environment / "bin/python").symlink_to(sys.executable)
        receipt = (
            self.root
            / f"deployment-backups/source-{'0' * 32}/setup-success.json"
        )
        receipt.parent.mkdir(parents=True)
        manifest = json.loads(self.payload["deployment.json"])
        receipt.write_text(
            json.dumps({
                "schema": 1,
                "source": str(self.initial),
                "version": manifest["version"],
                "manifest_sha256": hashlib.sha256(
                    self.payload["deployment.json"],
                ).hexdigest(),
                "python": "3.15.0",
                "environment": str(environment),
            }),
        )
        (self.root / "app").symlink_to(
            self.initial.name,
            target_is_directory=True,
        )

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
            self.root / "app/src/shared/config.py"
        ).read_text() == self.initial_config
        assert not (self.root / "deployment.lock").exists()

    def test_wait_exit_without_success_receipt_keeps_running_source(
        self,
    ) -> None:
        """Require setup proof even when the job CLI reports success."""
        result = self._deploy(self._stage(), FAKE_SETUP_MODE="silent-failure")
        assert result.returncode != 0
        assert (self.root / "app/src/shared/config.py").exists()
        deleted = [
            command[-1]
            for command in self._commands()
            if command[:3] == ["toolforge", "jobs", "delete"]
        ]
        assert not {
            "aranami-hub",
            "aranami-web",
        }.intersection(deleted)

    def test_setup_failure_leaves_old_source_and_runtime_data_untouched(
        self,
    ) -> None:
        """Keep old applications available when staged setup fails."""
        result = self._deploy(self._stage(), FAKE_SETUP_MODE="failure")
        assert result.returncode != 0
        assert (self.root / "app/src/shared/config.py").exists()
        assert (
            self.root / "reports/keep.parquet"
        ).read_text() == "retained report"

    def test_unconfirmed_setup_exit_keeps_the_deployment_guard(self) -> None:
        """Keep the guard when setup termination is unconfirmed."""
        result = self._deploy(
            self._stage(),
            FAKE_SETUP_MODE="failure",
            FAKE_PODS_STUCK="1",
        )
        assert result.returncode != 0
        assert (self.root / "deployment.lock").is_dir()
        assert (self.root / "app/src/shared/config.py").exists()
        assert "Setup shutdown is unconfirmed" in result.stderr

    def test_generation_upgrade_preserves_jobs_and_runtime_data(
        self,
    ) -> None:
        """Select immutable code and replace only existing services."""
        stage = self._stage()
        result = self._deploy(stage)
        assert result.returncode == 0, result.stderr
        assert (self.root / "app").is_symlink()
        assert (self.root / "app").resolve() == stage
        assert (self.root / "app/deployment.json").is_file()
        backup = self.root / f"deployment-backups/source-{'a' * 32}"
        assert (backup / "app.before").is_symlink()
        assert (backup / "app.before").readlink().name == self.initial.name
        assert (self.root / "app/src/webservice/pages.py").is_file()
        assert (self.root / "logs/keep.log").read_text() == "retained log"
        assert (
            self.root / "cache/keep.parquet"
        ).read_text() == "retained cache"
        assert (
            self.root / "user-config.py"
        ).read_text() == "# private account configuration\n"
        commands = self._commands()
        run = next(
            index
            for index, command in enumerate(commands)
            if command[:3] == ["toolforge", "jobs", "run"]
        )
        stopped = next(
            index
            for index, command in enumerate(commands)
            if command[:2] == ["kubectl", "scale"]
        )
        assert run < stopped
        assert {"aranami-hub"} == {
            command[3]
            for command in commands
            if command[:2] == ["kubectl", "scale"]
        }
        assert not any(
            command[:3] == ["toolforge", "jobs", "delete"]
            for command in commands
        )
        started = [
            command
            for command in commands
            if command[:3] == ["toolforge", "jobs", "run"]
        ]
        assert len(started) == len(("setup", "migration"))
        assert started[0][3].startswith("aranami-setup-")
        assert started[1][3].startswith("aranami-data-")
        assert "existing services replaced" in result.stdout
        assert "--port 8000 --publish" in result.stdout
        assert (
            f"--command 'exec sh {self.root}/app/scripts/toolforge/run-web.sh'"
            in result.stdout
        )
        assert (
            f"--command 'exec sh {self.root}/app/scripts/toolforge/run-hub.sh'"
            in result.stdout
        )

    def _legacy_data(self) -> Path:
        """Retain a source artifact and completed copy receipt.

        Returns:
            The legacy artifact that remains readable until rollout.
        """
        source = self.root / "data/hourly/legacy.parquet"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"original data")
        receipt = self.root / "state/data-layout-v3.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(
            json.dumps({"schema": 3, "complete": True, "files": []}),
        )
        return source

    def test_successful_rollout_retires_legacy_data(self) -> None:
        """Retire old folders only after services become available."""
        source = self._legacy_data()
        result = self._deploy(self._stage())
        assert result.returncode == 0, result.stderr
        assert not source.exists()
        assert (
            self.root
            / "deployment-backups/data-layout-v3/hourly"
            / "legacy.parquet"
        ).read_bytes() == b"original data"
        commands = self._commands()
        retired = next(
            i for i, command in enumerate(commands) if "retire-data" in command
        )
        rollouts = [
            i
            for i, command in enumerate(commands)
            if command[:3] == ["kubectl", "rollout", "status"]
        ]
        assert rollouts
        assert max(rollouts) < retired

    def test_failed_rollout_keeps_legacy_data(self) -> None:
        """Leave rollback originals in place when replacement fails."""
        source = self._legacy_data()
        result = self._deploy(self._stage(), FAKE_ROLLOUT_FAILURE="1")
        assert result.returncode != 0
        assert (
            "Code activated, but service replacement failed" in result.stderr
        )
        assert source.read_bytes() == b"original data"
        assert not (self.root / "deployment-backups/data-layout-v3").exists()

    def test_selection_failure_keeps_source_and_resumes_drained_hub(
        self,
    ) -> None:
        """Restore predecessors when activation has not occurred."""
        source = self._legacy_data()
        result = self._deploy(self._stage(), FAKE_SELECTION_FAILURE="1")
        assert result.returncode != 0
        assert source.read_bytes() == b"original data"
        assert (self.root / "app/src/shared/config.py").exists()
        assert not (self.root / "active-deployment.json").exists()
        assert json.loads((self.root / "fake-replicas.json").read_text()) == {
            "aranami-hub": "1",
            "aranami-web": "1",
        }

    def test_both_launchers_reject_changed_source_before_application_start(
        self,
    ) -> None:
        """Guard both runtime processes with the source receipt."""
        result = self._deploy(self._stage())
        assert result.returncode == 0, result.stderr
        (self.root / "app/src/shared/config.py").write_text("changed source")
        for name in ("run-hub.sh", "run-web.sh"):
            with self.subTest(launcher=name):
                result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                    [
                        "/bin/sh",
                        str(self.root / "app/scripts/toolforge" / name),
                        "--help",
                    ],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                assert result.returncode != 0
                assert "hash" in result.stderr
                assert "usage:" not in result.stdout

    def test_later_promotions_keep_generations_and_restart_sequentially(
        self,
    ) -> None:
        """Keep the website available while replacing its publisher."""
        first_stage = self._stage()
        first = self._deploy(first_stage)
        assert first.returncode == 0, first.stderr
        first_environment = self.root / f"deployment-envs/source-{'a' * 32}"
        count = len(self._commands())
        second_stage = self._stage("b" * 32)
        second = self._deploy(second_stage)
        assert second.returncode == 0, second.stderr
        assert (self.root / "app").resolve() == second_stage
        assert (first_stage / "deployment.json").exists()
        assert first_environment.exists()
        commands = self._commands()[count:]
        restart = commands.index([
            "toolforge",
            "jobs",
            "restart",
            "aranami-web",
        ])
        paused = next(
            index
            for index, command in enumerate(commands)
            if command[:4] == ["kubectl", "scale", "deployment", "aranami-hub"]
        )
        assert restart < paused
        assert not any(
            command[:4] == ["kubectl", "scale", "deployment", "aranami-web"]
            for command in commands
        )
        backup = self.root / f"deployment-backups/source-{'b' * 32}"
        assert (backup / "app.before").readlink() == Path(first_stage.name)

    def test_hub_drain_precedes_selection_and_publisher_shutdown(self) -> None:
        """Confirm the existing publisher is idle before replacement."""
        first_stage = self._stage()
        assert self._deploy(first_stage).returncode == 0
        count = len(self._commands())
        second_stage = self._stage("b" * 32)
        result = self._deploy(second_stage)
        assert result.returncode == 0, result.stderr
        commands = self._commands()[count:]
        drain = next(
            index
            for index, command in enumerate(commands)
            if command[:2] == ["kubectl", "exec"]
        )
        selection = next(
            index
            for index, command in enumerate(commands)
            if command[0] == "python3" and "select" in command
        )
        shutdown = next(
            index
            for index, command in enumerate(commands)
            if command[:4] == ["kubectl", "scale", "deployment", "aranami-hub"]
            and "--replicas=0" in command
        )
        assert drain < selection < shutdown
        assert (
            str(second_stage / "scripts/toolforge/drain-hub.py")
            in commands[drain]
        )

    def test_failed_hub_drain_preserves_source_and_running_publisher(
        self,
    ) -> None:
        """Preserve live services when publisher idle is unconfirmed."""
        first_stage = self._stage()
        assert self._deploy(first_stage).returncode == 0
        count = len(self._commands())
        second_stage = self._stage("b" * 32)
        result = self._deploy(second_stage, FAKE_DRAIN_FAILURE="1")
        assert result.returncode != 0
        assert (self.root / "app").resolve() == first_stage
        assert json.loads((self.root / "fake-replicas.json").read_text()) == {
            "aranami-hub": "1",
            "aranami-web": "1",
        }
        commands = self._commands()[count:]
        assert any(command[:2] == ["kubectl", "exec"] for command in commands)
        assert not any(
            command[:2] == ["kubectl", "scale"] for command in commands
        )
        assert not any(
            command[:3] == ["toolforge", "jobs", "restart"]
            for command in commands
        )

    def test_modern_selection_failure_restores_drained_old_hub(self) -> None:
        """Restore old scheduling after drain if activation fails."""
        first_stage = self._stage()
        assert self._deploy(first_stage).returncode == 0
        count = len(self._commands())
        second_stage = self._stage("b" * 32)
        result = self._deploy(second_stage, FAKE_SELECTION_FAILURE="1")
        assert result.returncode != 0
        assert (self.root / "app").resolve() == first_stage
        commands = self._commands()[count:]
        hub_scales = [
            command
            for command in commands
            if command[:4] == ["kubectl", "scale", "deployment", "aranami-hub"]
        ]
        assert [
            next(value for value in command if value.startswith("--replicas="))
            for command in hub_scales
        ] == ["--replicas=0", "--replicas=1"]
        assert not any(
            command[:3] == ["toolforge", "jobs", "restart"]
            for command in commands
        )
        assert json.loads((self.root / "fake-replicas.json").read_text()) == {
            "aranami-hub": "1",
            "aranami-web": "1",
        }

    def test_missing_and_paused_services_are_not_started(self) -> None:
        """Preserve absent jobs and deliberately paused publication."""
        result = self._deploy(
            self._stage(),
            FAKE_HUB_REPLICAS="",
            FAKE_WEB_REPLICAS="0",
        )
        assert result.returncode == 0, result.stderr
        assert not any(
            command[:2] == ["kubectl", "scale"]
            or command[:3] == ["toolforge", "jobs", "restart"]
            for command in self._commands()
        )

    def test_failed_web_restart_keeps_selected_code_and_hub_running(
        self,
    ) -> None:
        """Retain both immutable generations for explicit recovery."""
        first_stage = self._stage()
        assert self._deploy(first_stage).returncode == 0
        count = len(self._commands())
        second_stage = self._stage("b" * 32)
        result = self._deploy(second_stage, FAKE_ROLLOUT_FAILURE="1")
        assert result.returncode != 0
        assert (self.root / "app").resolve() == second_stage
        assert first_stage.exists()
        assert "Code activated" in result.stderr
        assert not any(
            command[:4] == ["kubectl", "scale", "deployment", "aranami-hub"]
            for command in self._commands()[count:]
        )

    def test_hub_shutdown_timeout_never_starts_another_publisher(
        self,
    ) -> None:
        """Keep a stuck publisher paused and the website running."""
        assert self._deploy(self._stage()).returncode == 0
        count = len(self._commands())
        result = self._deploy(
            self._stage("b" * 32),
            FAKE_STUCK_SERVICE="aranami-hub",
        )
        assert result.returncode != 0
        assert json.loads((self.root / "fake-replicas.json").read_text()) == {
            "aranami-hub": "0",
            "aranami-web": "1",
        }
        assert not any(
            command[:4] == ["kubectl", "scale", "deployment", "aranami-hub"]
            and "--replicas=1" in command
            for command in self._commands()[count:]
        )

    def test_runtime_pins_late_imports_and_python_across_selection(
        self,
    ) -> None:
        """Pin running code while another generation is selected."""
        first = self._stage()
        assert self._deploy(first).returncode == 0
        second = self._stage("b" * 32)
        assert self._deploy(second).returncode == 0
        (self.root / "deployment.lock").mkdir()
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [
                "/bin/sh",
                "-c",
                (
                    "set -eu; tool_dir=$1; "
                    "validator=scripts/toolforge/validate-source.py; "
                    'python3 "$2/$validator" select "$2"; '
                    '. "$tool_dir/app/scripts/toolforge/runtime-env.sh"; '
                    'python3 "$3/$validator" select "$3"; '
                    'printf "%s\\n" "$PYTHONPATH" "$runtime_python"; '
                    'exec "$runtime_python" -c '
                    "'import os, shared; print(shared.__file__); "
                    'print(os.environ["POLARS_MAX_THREADS"])\''
                ),
                "pin-test",
                str(self.root),
                str(first),
                str(second),
            ],
            env={
                key: value
                for key, value in self.env.items()
                if key != "POLARS_MAX_THREADS"
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            str(first / "src"),
            str(self.root / f"deployment-envs/source-{'a' * 32}/bin/python"),
            str(first / "src/shared/__init__.py"),
            "2",
        ]
        assert (self.root / "app").resolve() == second

    def test_runtime_rejects_receipt_from_identical_other_generation(
        self,
    ) -> None:
        """Require source identity even for matching source bytes."""
        first = self._stage()
        assert self._deploy(first).returncode == 0
        second = self._stage("b" * 32)
        assert self._deploy(second).returncode == 0
        receipt = (
            self.root
            / f"deployment-backups/source-{'b' * 32}"
            / "setup-success.json"
        )
        evidence = json.loads(receipt.read_text())
        evidence["source"] = str(first)
        receipt.write_text(json.dumps(evidence))
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [
                "/bin/sh",
                str(second / "scripts/toolforge/run-web.sh"),
                "--help",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode != 0
        assert "different source directory" in result.stderr

    def test_reused_upload_never_resumes_an_old_transaction(self) -> None:
        """Reject reused tokens without resuming old transactions."""
        first = self._stage()
        assert self._deploy(first).returncode == 0
        assert self._deploy(self._stage("b" * 32)).returncode == 0
        backup = self.root / f"deployment-backups/source-{'a' * 32}"
        (backup / "aranami-hub.stopped").touch()
        count = len(self._commands())
        result = self._deploy(first)
        assert result.returncode != 0
        assert (backup / "aranami-hub.stopped").exists()
        assert all(
            command[0] == "python3" for command in self._commands()[count:]
        )

    def test_stuck_application_pods_prevent_source_promotion(self) -> None:
        """Require applications to stop before promotion."""
        result = self._deploy(self._stage(), FAKE_PODS_STUCK="1")
        assert result.returncode != 0
        assert (self.root / "app/src/shared/config.py").exists()

    def test_toolforge_mount_alias_keeps_portable_receipt_paths(self) -> None:
        """Accept mount aliases without saving host-only paths."""
        stage = self._stage()
        alias_directory = Path(
            self.enterContext(TemporaryDirectory()),
        ).resolve()
        mount = alias_directory / "project"
        mount.symlink_to(self.root.parent, target_is_directory=True)
        home = mount / self.root.name
        receipt = (
            self.root
            / f"deployment-backups/source-{'0' * 32}/setup-success.json"
        )
        prior = json.loads(receipt.read_text())
        prior["source"] = str(home / self.initial.name)
        prior["environment"] = str(home / f"deployment-envs/source-{'0' * 32}")
        receipt.write_text(json.dumps(prior))
        result = self._deploy(
            home / stage.name,
            ARANAMI_TOOL_DIR=str(home),
            FAKE_UV_PHYSICAL_PATH="1",
        )
        assert result.returncode == 0, result.stderr
        evidence = json.loads(
            (
                self.root
                / f"deployment-backups/source-{'a' * 32}"
                / "setup-success.json"
            ).read_text(),
        )
        assert Path(evidence["source"]).parent == home
        assert Path(evidence["environment"]).parent == home / "deployment-envs"

    def test_source_symlink_inside_tool_home_is_rejected(self) -> None:
        """Reject staged source symlinks inside the tool home."""
        stage = self._stage()
        retained = self.root / "retained-stage"
        stage.rename(retained)
        stage.symlink_to(retained, target_is_directory=True)
        result = self._deploy(stage)
        assert result.returncode != 0
        assert "canonical source directory" in result.stderr
        assert (self.root / "app/src/shared/config.py").exists()
        assert not (self.root / "deployment.lock").exists()

    def test_runtime_directory_symlink_is_rejected_before_setup(self) -> None:
        """Reject runtime directory symlinks inside the tool home."""
        stage = self._stage()
        for name in ("deployment-backups", "deployment-envs"):
            with self.subTest(name=name), TemporaryDirectory() as directory:
                linked = self.root / name
                saved = Path(directory) / "original"
                shutil.move(linked, saved)
                linked.symlink_to(directory, target_is_directory=True)
                try:
                    result = self._deploy(stage)
                    assert result.returncode != 0
                    assert (self.root / "app/src/shared/config.py").exists()
                    assert not (self.root / "deployment.lock").exists()
                    assert list(Path(directory).iterdir()) == [saved]
                finally:
                    linked.unlink()
                    shutil.move(saved, linked)

    @staticmethod
    def test_bootstrap_validator_parses_bastion_grammar() -> None:
        """Parse the bootstrap helper with the older bastion grammar."""
        path = _ROOT / "scripts/toolforge/validate-source.py"
        ast.parse(
            path.read_text(),
            filename=str(path),
            feature_version=(3, 11),
        )
