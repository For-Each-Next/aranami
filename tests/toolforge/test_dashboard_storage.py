"""Store frontend JSON and read compatible legacy dashboard files."""

# The repository uses unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import copy
import datetime as dt
import json
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient

from data.prepared import write_values
from data.website import (
    APPEARANCE_SECTIONS,
    REPORT_KEYS,
    SNAPSHOT_SCHEMA,
    read_report_cache,
    read_snapshot,
    save_report_cache,
    save_snapshot,
    validate_dashboard_snapshot,
)
from webservice.server import create_app


def _document(stamp: str = "2026-11-14T12:00:00Z") -> dict:
    """Build a valid frontend document without acquired report data.

    Returns:
        A complete document with independent reports unavailable.
    """
    result = {
        "schema": SNAPSHOT_SCHEMA,
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
                "last_success_at": None,
                "last_attempt_at": None,
            }
            for name in REPORT_KEYS
        },
        "homepage": {
            key: None
            for values in APPEARANCE_SECTIONS.values()
            for key in values
        },
        **dict.fromkeys(REPORT_KEYS),
        "sources": [],
        "errors": [],
    }
    assert validate_dashboard_snapshot(result)
    return result


class DashboardDocumentTests(TestCase):
    """Separate frontend JSON from background Parquet observations."""

    def test_json_is_preferred_and_legacy_data_remains_readable(self) -> None:
        """Prefer current JSON and reject an invalid replacement."""
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            root = runtime / "data/parsed"
            legacy = _document()
            current = _document("2026-11-14T13:00:00Z")
            write_values(root / "dashboard.parquet", legacy)
            save_snapshot(root / "dashboard.json", current)
            before = sorted(runtime.rglob("*"))
            client = TestClient(create_app(runtime))
            self.addCleanup(client.close)
            with patch(
                "pywikibot.Site",
                side_effect=AssertionError("sources"),
            ):
                response = client.get("/api/dashboard")
                assert response.status_code == HTTPStatus.OK
                assert response.json() == current
                (root / "dashboard.json").write_text("invalid")
                assert client.get("/api/dashboard").status_code == (
                    HTTPStatus.SERVICE_UNAVAILABLE
                )
                (root / "dashboard.json").unlink()
                response = client.get("/api/dashboard")
                assert response.status_code == HTTPStatus.OK
                assert response.json() == legacy
                current_path = root / "dashboard.json"
                current_path.symlink_to(root / "missing-document.json")
                assert client.get("/api/dashboard").status_code == (
                    HTTPStatus.SERVICE_UNAVAILABLE
                )
                current_path.unlink()
            assert sorted(runtime.rglob("*")) == [
                path for path in before if path.name != "dashboard.json"
            ]

    @staticmethod
    def test_migration_removes_retired_payload_and_metadata() -> None:
        """Clean legacy data while retaining its original file."""
        value = _document()
        value.update(
            backlog={"retired": True},
            collection={
                "attempted": len(REPORT_KEYS) + 1,
                "failed": 1,
            },
        )
        value["reports"]["backlog"] = {"retired": True}
        value["errors"] = [
            {"section": "backlog", "message": "Retired failure"},
        ]
        original = copy.deepcopy(value)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "dashboard.parquet"
            current = root / "dashboard.json"
            write_values(legacy, value)
            before = legacy.read_bytes()
            migrated = read_snapshot(legacy)
            save_snapshot(current, migrated)
            assert json.loads(current.read_bytes()) == _document()
            assert validate_dashboard_snapshot(migrated)
            assert legacy.read_bytes() == before
            assert value == original
            save_snapshot(current, value)
            assert read_snapshot(current) == _document()

    @staticmethod
    def test_frontend_report_cache_reuses_legacy_data_without_collection() -> (
        None
    ):
        """Retain old source observations during the JSON transition."""
        stamp = "2026-11-14T12:00:00Z"
        record = {
            "schema": SNAPSHOT_SCHEMA,
            "config_id": "a" * 64,
            "attempt_config_id": "a" * 64,
            "observed_at": None,
            "attempted_at": stamp,
            "failed": True,
            "data": None,
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "recognized.parquet"
            current = root / "recognized.json"
            write_values(legacy, record)
            before = legacy.read_bytes()
            clock = dt.datetime.fromisoformat(stamp)
            assert read_report_cache(current, "recognized", 1, clock) == record
            assert not current.exists()
            save_report_cache(current, record)
            assert json.loads(current.read_bytes()) == record
            assert read_report_cache(current, "recognized", 1, clock) == record
            current.write_text("invalid")
            assert read_report_cache(current, "recognized", 1, clock) is None
            current.unlink()
            current.symlink_to(root / "missing-document.json")
            assert read_report_cache(current, "recognized", 1, clock) is None
            assert legacy.read_bytes() == before

    def test_failed_json_replacement_keeps_the_previous_document(self) -> None:
        """Retain the previous document after invalid replacements."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "dashboard.json"
            original = _document()
            save_snapshot(path, original)
            for replacement in (
                {"oversized": "x" * (8 * 1024 * 1024)},
                {"nonfinite": float("nan")},
            ):
                with self.assertRaises(ValueError):
                    save_snapshot(path, replacement)
                assert read_snapshot(path) == original
            linked = root / "linked.json"
            linked.symlink_to(path)
            with self.assertRaises(OSError):
                save_snapshot(linked, {"replaced": True})
            parent = root / "linked-directory"
            parent.symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                save_snapshot(parent / path.name, {"replaced": True})
            assert read_snapshot(path) == original
