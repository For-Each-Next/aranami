"""Keep all prepared corrections beyond the compact dashboard."""

# Protocol bounds and private HTTP helpers are regression assertions.
# ruff: file-ignore[magic-value-comparison,private-member-access,pytest-unittest-raises-assertion]

from __future__ import annotations

import copy
import json
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from starlette.testclient import TestClient
from test_consistency_presentation import consistency_snapshot

from data.caching import consistency as consistency_cache
from data.consistency import repair_identity, review_repair, valid_result
from data.website import (
    APPEARANCE_SECTIONS,
    MAX_SNAPSHOT_BYTES,
    REPORT_KEYS,
    save_snapshot,
    validate_dashboard_snapshot,
)
from runtime import consistency_repairs as coordinator
from runtime.dashboard import DashboardUpdater
from webservice import server


def _snapshot(count: int = 1) -> dict:
    """Build valid checked heads and one finding for each correction.

    Returns:
        Acquired facts that need section-zero projection before serving.
    """
    result = consistency_snapshot()["consistency"]
    source = next(iter(result["repair_inputs"].values()))
    finding = next(row for row in result["items"] if row.get("repair_id"))
    inputs = {}
    items = []
    for index in range(1, count + 1):
        record = {
            **copy.deepcopy(source),
            "page_id": index,
            "talk_page_id": index + 1000,
            "revision_id": index + 200,
            "title": f"Talk:Game {index:03}",
            "original_text": source["original_text"]
            + "<!-- preserved source "
            + "x" * 3000
            + " -->\n",
        }
        record["id"] = repair_identity(record)
        inputs[record["id"]] = record
        items.append({
            **finding,
            "id": f"homepage_history:{index}",
            "page_id": index,
            "title": f"Game {index:03}",
            "checked_title": record["title"],
            "checked_revision": record["revision_id"],
            "repair_id": record["id"],
        })
    result.update(
        items=items,
        repair_inputs=inputs,
        section0_repairs={},
        checks=[
            {
                "check_id": "gan",
                "status": "complete",
                "checked_count": count,
                "issue_count": count,
            },
        ],
    )
    assert valid_result(result)
    return {"consistency": result}


def _dashboard(data: dict) -> dict:
    """Place checked consistency data in a validated public envelope.

    Returns:
        Complete dashboard metadata with other reports unavailable.
    """
    stamp = data["checked_at"]
    snapshot = {
        "schema": 2,
        "period": {
            "label": "fixture",
            "start": "2026-11-02",
            "end": "2026-11-03",
        },
        "updated_at": stamp,
        "reports": {
            name: {
                "config_id": "a" * 64,
                "refresh_interval_seconds": 3600,
                "failed": False,
                "last_success_at": stamp,
                "last_attempt_at": stamp,
            }
            for name in REPORT_KEYS
        },
        "homepage": {
            key: None
            for values in APPEARANCE_SECTIONS.values()
            for key in values
        },
        **dict.fromkeys(REPORT_KEYS),
        "consistency": data,
        "sources": [],
        "errors": [],
    }
    assert validate_dashboard_snapshot(snapshot)
    return snapshot


class TestConsistencyRepairSidecar(TestCase):
    """Serve complete prepared repairs with safe compact fallbacks."""

    @staticmethod
    def test_full_result_keeps_all_actions_outside_dashboard_capacity() -> (
        None
    ):
        """Retain 768 proposals beyond both former capacity limits."""
        snapshot = _snapshot(768)
        empty = copy.deepcopy(snapshot)
        empty["consistency"].update(repair_inputs={}, section0_repairs={})
        for item in empty["consistency"]["items"]:
            item.pop("repair_id")
        baseline = len(
            json.dumps(
                empty,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode(),
        )
        snapshot["payload"] = "x" * (MAX_SNAPSHOT_BYTES - baseline - 300_000)
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            path = runtime / "cache/toolforge/dashboard.json"
            coordinator.save_dashboard_snapshot(path, snapshot)
            compact = json.loads(path.read_text())
            prepared = consistency_cache.read(
                path.with_name("consistency-repairs.parquet"),
            )["data"]
            assert len(compact["consistency"]["section0_repairs"]) < 768
            assert len(prepared["section0_repairs"]) == 768
            assert len(prepared["repair_inputs"]) == 768
            assert (
                len(json.dumps(prepared["repair_inputs"]).encode())
                > 2 * 1024 * 1024
            )
            assert path.stat().st_size <= MAX_SNAPSHOT_BYTES
            identity = next(reversed(prepared["section0_repairs"]))
            assert identity not in compact["consistency"]["section0_repairs"]
            before = sorted(runtime.rglob("*"))
            with patch.object(
                server._PublicViews,
                "_dashboard",
                return_value=compact,
            ):
                client = TestClient(server.create_app(runtime))
                response = client.get(
                    "/api/consistency-repair",
                    params={
                        "repair": review_repair(
                            prepared,
                            identity,
                        )["id"],
                    },
                )
                assert response.status_code == HTTPStatus.OK
                assert response.json()["repair"] == review_repair(
                    prepared,
                    identity,
                )
                page = client.get("/checks/?page=77&lang=en")
                assert page.status_code == HTTPStatus.OK
                assert (
                    "aranami-check-job="
                    + str(prepared["repair_inputs"][identity]["revision_id"])
                    in page.text
                )
            assert sorted(runtime.rglob("*")) == before

    def test_startup_reprojection_keeps_full_maps_and_source_guards(
        self,
    ) -> None:
        """Reformat every saved plan under the existing output lease."""
        snapshot = _dashboard(_snapshot(768)["consistency"])
        empty = copy.deepcopy(snapshot)
        empty["consistency"].update(repair_inputs={}, section0_repairs={})
        for item in empty["consistency"]["items"]:
            item.pop("repair_id")
        baseline = len(json.dumps(empty, separators=(",", ":")).encode())
        snapshot["payload"] = "x" * (MAX_SNAPSHOT_BYTES - baseline - 300_000)
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            path = runtime / "cache/toolforge/dashboard.json"
            coordinator.save_dashboard_snapshot(path, snapshot)
            sidecar = path.with_name("consistency-repairs.parquet")
            full = consistency_cache.read(sidecar)["data"]
            compact = json.loads(path.read_text())
            expected = copy.deepcopy(full["section0_repairs"])
            for data in (full, compact["consistency"]):
                for proposal in data["section0_repairs"].values():
                    proposal["text"] = (
                        proposal["text"]
                        .replace("\n| ", "|")
                        .replace("\n}}", "}}")
                    )
            assert full["section0_repairs"] != expected
            consistency_cache.save(sidecar, {"data": full})
            save_snapshot(path, compact)
            collect = Mock()
            updater = DashboardUpdater(
                path,
                collect,
                coordinator.save_dashboard_snapshot,
                log_dir=runtime / "logs",
                runtime=runtime,
            )
            updater.start()
            try:
                other = DashboardUpdater(
                    path,
                    collect,
                    coordinator.save_dashboard_snapshot,
                    log_dir=runtime / "logs",
                    runtime=runtime,
                )
                with self.assertRaises(RuntimeError):
                    other.start()
                assert coordinator.refresh_saved_repairs(path)
            finally:
                updater.stop()
            retained = consistency_cache.read(sidecar)["data"]
            assert retained["section0_repairs"] == expected
            assert retained["repair_inputs"] == full["repair_inputs"]
            assert len(retained["section0_repairs"]) == 768
            updated = json.loads(path.read_text())
            assert len(updated["consistency"]["section0_repairs"]) < 768
            assert updated["updated_at"] == compact["updated_at"]
            assert updated["reports"] == compact["reports"]
            assert validate_dashboard_snapshot(updated)
            collect.assert_not_called()

    @staticmethod
    def test_startup_keeps_unavailable_cache_without_writes() -> None:
        """Keep unavailable saved generations unchanged."""
        for state in ("missing", "invalid", "stale", "invalid-dashboard"):
            with TemporaryDirectory() as directory:
                path = Path(directory) / "dashboard.json"
                coordinator.save_dashboard_snapshot(
                    path,
                    _dashboard(_snapshot()["consistency"]),
                )
                sidecar = path.with_name("consistency-repairs.parquet")
                if state == "missing":
                    sidecar.unlink()
                elif state == "invalid":
                    sidecar.write_bytes(b"broken parquet")
                elif state == "stale":
                    data = consistency_cache.read(sidecar)["data"]
                    data["checked_at"] = "2026-11-04T00:05:00Z"
                    consistency_cache.save(sidecar, {"data": data})
                else:
                    path.write_text('{"schema": 2}')
                before = {
                    file.name: file.read_bytes()
                    for file in Path(directory).iterdir()
                    if file.is_file()
                }
                with patch.object(
                    coordinator,
                    "save_dashboard_snapshot",
                ) as save:
                    assert not coordinator.refresh_saved_repairs(path)
                    save.assert_not_called()
                assert {
                    file.name: file.read_bytes()
                    for file in Path(directory).iterdir()
                    if file.is_file()
                } == before

    @staticmethod
    def test_source_and_prepared_cache_have_independent_limits() -> None:
        """Retain source envelopes above the dashboard size limit."""
        snapshot = _snapshot(768)
        snapshot["consistency"]["sources"] = [
            {
                "label": "source evidence " + "x" * 980,
                "url": f"https://zh.wikipedia.org/wiki/Source_{index}",
            }
            for index in range(5000)
        ]
        source = {"data": snapshot["consistency"]}
        assert valid_result(source["data"])
        assert len(json.dumps(source).encode()) > MAX_SNAPSHOT_BYTES
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.json"
            acquired = path.with_name("consistency.parquet")
            consistency_cache.save(acquired, source)
            retained = consistency_cache.read(acquired)
            assert retained == source
            coordinator.save_dashboard_snapshot(path, snapshot)
            complete = consistency_cache.read(
                path.with_name("consistency-repairs.parquet"),
            )["data"]
            assert len(complete["section0_repairs"]) == 768
            assert set(complete["section0_repairs"]) == set(
                source["data"]["repair_inputs"],
            )
            assert len(json.dumps(complete).encode()) > MAX_SNAPSHOT_BYTES
            assert len(json.dumps(complete).encode()) < (
                consistency_cache.MAX_CACHE_BYTES
            )
            compact = json.loads(path.read_text())
            assert len(compact["consistency"]["section0_repairs"]) < 768
            assert path.stat().st_size <= MAX_SNAPSHOT_BYTES

    @staticmethod
    def test_oversized_cache_cannot_replace_the_previous_complete_result() -> (
        None
    ):
        """Keep the prior cache when an update exceeds sixteen MiB."""
        source = {"data": _snapshot()["consistency"]}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "consistency.parquet"
            consistency_cache.save(path, source)
            previous = path.read_bytes()
            rejected = False
            try:
                consistency_cache.save(
                    path,
                    {
                        **source,
                        "padding": "x" * consistency_cache.MAX_CACHE_BYTES,
                    },
                )
            except ValueError:
                rejected = True
            assert rejected
            assert path.read_bytes() == previous
            assert consistency_cache.read(path) == source

    @staticmethod
    def test_missing_invalid_and_stale_sidecars_use_compact_results() -> None:
        """Never mix an unavailable or different checked generation."""
        snapshot = _snapshot()
        compact = coordinator.prepare_snapshot_repairs(snapshot)
        identity = next(iter(compact["consistency"]["section0_repairs"]))
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            sidecar = runtime / "cache/toolforge/consistency-repairs.parquet"
            sidecar.parent.mkdir(parents=True)
            with patch.object(
                server._PublicViews,
                "_dashboard",
                return_value=compact,
            ):
                client = TestClient(server.create_app(runtime))
                for mode in ("missing", "invalid", "stale"):
                    if mode == "invalid":
                        sidecar.write_bytes(b"broken parquet")
                    elif mode == "stale":
                        stale = copy.deepcopy(compact["consistency"])
                        stale["checked_at"] = "2026-11-04T00:05:00Z"
                        stale["section0_repairs"] = {}
                        consistency_cache.save(sidecar, {"data": stale})
                    response = client.get(
                        "/api/consistency-repair",
                        params={
                            "repair": review_repair(
                                compact["consistency"],
                                identity,
                            )["id"],
                        },
                    )
                    assert response.status_code == HTTPStatus.OK
                    assert response.json()["repair"] == review_repair(
                        compact["consistency"],
                        identity,
                    )

    @staticmethod
    def test_failed_sidecar_write_preserves_selected_dashboard() -> None:
        """Preserve the dashboard when Parquet retention fails."""
        snapshot = _snapshot()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.json"
            coordinator.save_dashboard_snapshot(path, snapshot)
            original = path.read_bytes()
            previous = path.with_name(
                "consistency-repairs.parquet",
            ).read_bytes()
            with (
                patch.object(
                    coordinator.consistency_cache,
                    "save",
                    side_effect=OSError("retention failed"),
                ),
                patch.object(coordinator, "save_snapshot") as save,
            ):
                rejected = False
                try:
                    coordinator.save_dashboard_snapshot(path, snapshot)
                except OSError:
                    rejected = True
                assert rejected
                save.assert_not_called()
            assert path.read_bytes() == original
            assert (
                path.with_name("consistency-repairs.parquet").read_bytes()
                == previous
            )

    @staticmethod
    def test_sidecar_replacement_invalidates_page_cache_identity() -> None:
        """Observe prepared results before a dashboard rewrite."""
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            views = server._PublicViews(runtime, server._ASSETS)
            original = views._page_identity()
            sidecar = runtime / "cache/toolforge/consistency-repairs.parquet"
            prepared = coordinator.prepare_snapshot_repairs(_snapshot())
            consistency_cache.save(sidecar, {"data": prepared["consistency"]})
            assert views._page_identity() != original

    @staticmethod
    def test_checks_pagination_queries_remain_validated() -> None:
        """Reject malformed checks overview page queries."""
        snapshot = coordinator.prepare_snapshot_repairs(_snapshot())
        with (
            TemporaryDirectory() as directory,
            patch.object(
                server._PublicViews,
                "_dashboard",
                return_value=snapshot,
            ),
        ):
            client = TestClient(server.create_app(Path(directory)))
            for query in (
                "page=0",
                "page=-1",
                "page=bad",
                "page=1&page=2",
                "other=1",
            ):
                response = client.get("/checks/?" + query)
                assert response.status_code == HTTPStatus.BAD_REQUEST

    @staticmethod
    def test_failed_dashboard_update_cannot_mix_prepared_generations() -> None:
        """Use compact fallback after a partial generation update."""
        snapshot = _snapshot()
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            path = runtime / "cache/toolforge/dashboard.json"
            coordinator.save_dashboard_snapshot(path, snapshot)
            original = path.read_bytes()
            previous = json.loads(original)
            snapshot["consistency"]["checked_at"] = "2026-11-04T00:05:00Z"
            with patch.object(
                coordinator,
                "save_snapshot",
                side_effect=OSError("dashboard retention failed"),
            ):
                rejected = False
                try:
                    coordinator.save_dashboard_snapshot(path, snapshot)
                except OSError:
                    rejected = True
                assert rejected
            assert path.read_bytes() == original
            views = server._PublicViews(runtime, server._ASSETS)
            assert views._with_consistency_repairs(previous) is previous
