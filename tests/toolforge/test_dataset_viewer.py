"""Verify safe project-file selection and readable previews."""

# Keep fixed protocol/preview assertions and repository exception style.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import io
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from starlette.testclient import TestClient

from data import parquet_preview, project_data
from data.paths import storage_key
from shared.support.cache import ParquetCache
from webservice.datasets import DatasetSelectionError, read_dataset
from webservice.server import create_app

_BOUNDARY = dt.date(2026, 11, 3)
_NOW = dt.datetime(2026, 11, 3, 0, 1, tzinfo=dt.UTC)
_PROJECT = "Example & Project"
_ROWS = 53


class _DescriptorReader(io.BufferedReader):
    """Reject Python payload copies from a usable descriptor."""

    def read(self, size: int = -1) -> bytes:
        """Fail if Polars tries to copy a Python file-like payload.

        Raises:
            AssertionError: A preview attempted a Python payload read.
        """
        message = f"Unexpected Python read({size}) on fd {self.fileno()}."
        raise AssertionError(message)


def _members() -> pl.DataFrame:
    """Provide valid public rows with zero and null observations.

    Returns:
        Canonical project members requiring no source client.
    """
    return pl.DataFrame(
        {
            "page_id": list(range(1, _ROWS + 1)),
            "page_namespace": [0] * _ROWS,
            "page_title": [f"Member {number:02}" for number in range(_ROWS)],
            "rev_id": list(range(100, 100 + _ROWS)),
            "talk_page_rev": [None] * _ROWS,
            "pa_class": ["C"] * _ROWS,
            "pa_importance": ["Low"] * _ROWS,
            "talk_page_id": [None] * _ROWS,
            "page_is_redirect": [0] * _ROWS,
            "page_length": [0] * _ROWS,
            "taskforces": [[]] * _ROWS,
        },
        schema=project_data.SCHEMA,
    )


def dataset_fixture() -> dict:
    """Describe a synthetic capture for responsive browser checks.

    Returns:
        Public metadata and three rows without reading runtime files.
    """
    frame = (
        _members()
        .head(3)
        .with_columns(
            pl
            .when(pl.col("page_id") == 1)
            .then(pl.lit("Long unbroken title " + "LongTitle" * 30))
            .otherwise(pl.col("page_title"))
            .alias("page_title"),
        )
    )
    schema = {name: str(dtype) for name, dtype in frame.schema.items()}
    return {
        "file": "reports/data/projects/enwiki/Example & Project/"
        "2026/20261102.parquet",
        "name": "20261102.parquet",
        "wiki": "enwiki",
        "project": _PROJECT,
        "manifest": {
            "metadata": {
                "snapshot_date": "2026-11-02",
                "boundary_date": "2026-11-03",
            },
            "started_at": _NOW.isoformat(),
            "finished_at": (_NOW + dt.timedelta(seconds=3)).isoformat(),
            "schema": 1,
            "generation": "20261103T000100000000Z",
            "application_version": "1.0.0a9",
            "sha256": "a" * 64,
        },
        "storage": {"written_at": "2026-11-03T00:01:04+00:00"},
        "columns": frame.columns,
        "schema": list(schema.items()),
        "rows": frame.to_dicts(),
        "row_count": _ROWS,
        "preview_limit": 50,
        "page": 1,
        "page_count": 2,
    }


class TestDatasetViewer(TestCase):
    """Expose only validated snapshots with bounded escaped previews."""

    def setUp(self) -> None:
        """Capture an isolated immutable file before HTTP reads."""
        self.runtime = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bundle = project_data.ensure_snapshot(
            "enwiki",
            _PROJECT,
            _BOUNDARY,
            load_members=lambda _: _members(),
            runtime=self.runtime,
            observed_at=_NOW,
        )
        self.filename = self.bundle.path.relative_to(self.runtime).as_posix()
        self.app = create_app(self.runtime)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_data_help_and_preview_preserve_locale_and_original_facts(
        self,
    ) -> None:
        """Show original metadata and fifty rows without acquisition."""
        original = self.bundle.path.read_bytes()
        for locale in ("en", "zh-hans", "zh-hant"):
            with (
                patch("pywikibot.Site", side_effect=AssertionError("source")),
                patch(
                    "data.project_data.ensure_snapshot",
                    side_effect=AssertionError("write"),
                ),
            ):
                response = self.client.get(
                    "/data/",
                    params={"file": self.filename, "lang": locale},
                )
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert "Example &amp; Project" in response.text
            assert "20261102.parquet" in response.text
            assert "2026-11-03 00:01:00" in response.text
            assert "Member 00" in response.text
            assert "Member 49" in response.text
            assert "Member 50" not in response.text
            note = response.text.split('id="dataset-preview"', 1)[1].split(
                "</p>",
                1,
            )[0]
            assert {"en": "records", "zh-hans": "记录", "zh-hant": "記錄"}[
                locale
            ] in note
            assert "page_id" in response.text
            assert "Enum(" in response.text
            assert (
                "file=reports%2Fdata%2Fprojects%2Fenwiki%2FExample"
                in response.text
            )
            assert 'href="/data/?' in response.text
        head = self.client.head("/data/", params={"file": self.filename})
        assert head.status_code == 200
        assert not head.content
        assert int(head.headers["Content-Length"]) > 0
        assert self.bundle.path.read_bytes() == original
        help_page = self.client.get("/data/?lang=en")
        assert help_page.status_code == 200
        assert "Follow a Parquet filename" in help_page.text

    def test_legacy_file_write_time_remains_unknown(self) -> None:
        """Keep absent file-write metadata explicitly unknown."""
        frame = pl.read_parquet(self.bundle.path)
        metadata = pl.read_parquet_metadata(self.bundle.path)
        frame.write_parquet(
            self.bundle.path,
            metadata={"aranami": metadata["aranami"]},
        )
        value = read_dataset(self.filename, self.runtime)
        assert value["storage"] is None
        response = self.client.get(
            "/data/",
            params={"file": self.filename, "lang": "en"},
        )
        assert response.status_code == 200
        assert "Not recorded" in response.text

    def test_invalid_selectors_do_not_open_other_runtime_files(self) -> None:
        """Reject traversal and noncanonical file identities."""
        for filename in (
            "../.my.cnf",
            "/reports/data/projects/enwiki/Example/2026/20261102.parquet",
            "reports/private/history.parquet",
            "reports/data/projects/enwiki/../2026/20261102.parquet",
            self.filename.replace("/2026/", "/2025/"),
            self.filename.replace("Example", "%45xample"),
            self.filename.replace(".parquet", ".json"),
            self.filename.replace("/projects/", "//projects/"),
        ):
            with self.subTest(filename=filename):
                with self.assertRaises(DatasetSelectionError):
                    read_dataset(filename, self.runtime)
                response = self.client.get("/data/", params={"file": filename})
                assert response.status_code == 400
        for query in (
            "file=",
            "file=a&file=b",
            "file=a&download=1",
            "file=%ff",
            "q=a",
        ):
            assert self.client.get("/data/?" + query).status_code == 400

    def test_missing_corrupt_and_symlink_files_remain_distinct(self) -> None:
        """Keep unavailable states distinct without path leaks."""
        missing = self.filename.replace("20261102", "20261103")
        assert (
            self.client.get("/data/", params={"file": missing}).status_code
            == 404
        )
        original = self.bundle.path.read_bytes()
        self.bundle.path.write_bytes(b"invalid parquet")
        broken = self.client.get("/data/", params={"file": self.filename})
        assert broken.status_code == 503
        assert str(self.runtime) not in broken.text
        target = self.runtime / "private.parquet"
        target.write_bytes(original)
        self.bundle.path.unlink()
        self.bundle.path.symlink_to(target)
        unsafe = self.client.get("/data/", params={"file": self.filename})
        assert unsafe.status_code == 503
        assert "private.parquet" not in unsafe.text

    def test_owner_metadata_matches_preview(self) -> None:
        """Retain exact storage schema, nulls and source generation."""
        value = read_dataset(self.filename, self.runtime)
        assert value["row_count"] == _ROWS
        assert len(value["rows"]) == 50
        assert [row["page_id"] for row in value["rows"]] == list(range(1, 51))
        assert value["rows"][0]["page_length"] == 0
        assert value["rows"][0]["talk_page_rev"] is None
        assert value["storage"]["rows"] == _ROWS
        assert value["storage"]["columns"] == dict(value["schema"])
        assert (
            value["manifest"]["generation"]
            == self.bundle.manifest["generation"]
        )
        assert value["storage"]["contract"] == "daily-project-data"
        assert (
            dt.datetime.fromisoformat(
                value["storage"]["written_at"],
            ).utcoffset()
            == dt.timedelta()
        )

    def test_ascii_project_files_keep_original_names_and_old_links(
        self,
    ) -> None:
        """Resolve both selectors to the same saved project facts."""
        project = "电子游戏 & Example"
        saved = project_data.ensure_snapshot(
            "enwiki",
            project,
            _BOUNDARY,
            load_members=lambda _: _members(),
            runtime=self.runtime,
            observed_at=_NOW,
        )
        current = saved.path.relative_to(self.runtime).as_posix()
        legacy = (
            "reports/data/projects/enwiki/电子游戏 & Example/"
            "2026/20261102.parquet"
        )
        assert current == storage_key(legacy)
        assert current.isascii()
        original = saved.path.read_bytes()
        for filename in (current, legacy):
            with self.subTest(filename=filename):
                value = read_dataset(filename, self.runtime, 2)
                assert value["wiki"] == "enwiki"
                assert value["project"] == project
                assert value["manifest"] == saved.manifest
                assert [row["page_id"] for row in value["rows"]] == [
                    51,
                    52,
                    53,
                ]
                response = self.client.get(
                    "/data/",
                    params={"file": filename, "lang": "en"},
                )
                assert response.status_code == 200
                assert "电子游戏 &amp; Example" in response.text
        assert saved.path.read_bytes() == original
        assert not (self.runtime / legacy).exists()

    def test_unmigrated_project_preview_retains_legacy_bytes(self) -> None:
        """Read old Unicode data without making its new path."""
        project = "电子游戏"
        saved = project_data.ensure_snapshot(
            "enwiki",
            project,
            _BOUNDARY,
            load_members=lambda _: _members(),
            runtime=self.runtime,
            observed_at=_NOW,
        )
        filename = (
            "reports/data/projects/enwiki/电子游戏/2026/20261102.parquet"
        )
        legacy = self.runtime / filename
        legacy.parent.mkdir(parents=True)
        saved.path.rename(legacy)
        original = legacy.read_bytes()
        value = read_dataset(filename, self.runtime)
        assert value["project"] == project
        assert value["manifest"] == saved.manifest
        assert legacy.read_bytes() == original
        assert not saved.path.exists()

    def test_generated_and_literal_target_names_keep_separate_previews(
        self,
    ) -> None:
        """Read two current paths with overlapping logical spellings."""
        unicode_project = "电子游戏"
        literal_project = storage_key(unicode_project)
        captures = [
            (
                project,
                project_data.ensure_snapshot(
                    "enwiki",
                    project,
                    _BOUNDARY,
                    load_members=lambda _: _members(),
                    runtime=self.runtime,
                    observed_at=_NOW,
                ),
            )
            for project in (unicode_project, literal_project)
        ]
        paths = {saved.path for _, saved in captures}
        assert len(paths) == 2
        for project, saved in captures:
            with self.subTest(project=project):
                filename = saved.path.relative_to(self.runtime).as_posix()
                assert filename.isascii()
                original = saved.path.read_bytes()
                value = read_dataset(filename, self.runtime)
                assert value["project"] == project
                assert value["manifest"] == saved.manifest
                response = self.client.get(
                    "/data/",
                    params={"file": filename, "lang": "en"},
                )
                assert response.status_code == 200
                assert project in response.text
                assert saved.path.read_bytes() == original

    def test_misplaced_ascii_capture_is_not_another_projects_evidence(
        self,
    ) -> None:
        """Reject a valid file moved under another project's digest."""
        project = "电子游戏"
        saved = project_data.ensure_snapshot(
            "enwiki",
            project,
            _BOUNDARY,
            load_members=lambda _: _members(),
            runtime=self.runtime,
            observed_at=_NOW,
        )
        other = project_data.snapshot_path(
            "enwiki",
            "電子遊戲",
            _BOUNDARY,
            runtime=self.runtime,
        )
        other.parent.mkdir(parents=True)
        other.write_bytes(saved.path.read_bytes())
        response = self.client.get(
            "/data/",
            params={"file": other.relative_to(self.runtime).as_posix()},
        )
        assert response.status_code == 503
        assert saved.path.exists()

    def test_pages_keep_saved_order(
        self,
    ) -> None:
        """Read fifty-row pages without using eager file readers."""
        filename = "cache/example/20261110T152000.parquet"
        path = self.runtime / filename
        frame = pl.DataFrame({
            "number": list(range(103)),
            "value": [None, "", "<script>alert(1)</script>"] + ["x"] * 100,
        })
        ParquetCache(
            schema=frame.schema,
            contract="preview-fixture",
            max_rows=200,
            max_bytes=1024 * 1024,
        ).write(path, frame)
        original = path.read_bytes()
        open_file = os.fdopen

        def descriptor_reader(descriptor: int, mode: str) -> _DescriptorReader:
            """Keep descriptor access while rejecting eager reads.

            Returns:
                Reader instrumenting the descriptor passed to Polars.
            """
            return _DescriptorReader(open_file(descriptor, mode, buffering=0))

        with (
            patch.object(
                pl,
                "read_parquet",
                side_effect=AssertionError("eager"),
            ),
            patch.object(
                parquet_preview.os,
                "fdopen",
                side_effect=descriptor_reader,
            ),
        ):
            first = read_dataset(filename, self.runtime)
            second = read_dataset(filename, self.runtime, 2)
            last = read_dataset(filename, self.runtime, 99)
            project = read_dataset(self.filename, self.runtime, 2)
        assert [row["number"] for row in first["rows"]] == list(range(50))
        assert [row["number"] for row in second["rows"]] == list(
            range(50, 100),
        )
        assert [row["number"] for row in last["rows"]] == [100, 101, 102]
        assert last["page"] == last["page_count"] == 3
        assert [row["page_id"] for row in project["rows"]] == [51, 52, 53]
        assert first["rows"][0]["value"] is None
        assert first["rows"][1]["value"] == ""  # ruff: ignore[compare-to-empty-string]
        response = self.client.get(
            "/data/",
            params={"file": filename, "lang": "en"},
        )
        assert response.status_code == 200
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
        assert "preview-fixture" in response.text
        assert "page=2" in response.text
        assert "Page 1 of 3" in response.text
        assert path.read_bytes() == original
        path.unlink()
        assert (
            self.client.get("/data/", params={"file": filename}).status_code
            == 404
        )

    def test_legacy_report_preview_keeps_write_metadata_unknown(self) -> None:
        """Inspect legacy rows without inventing write facts."""
        filename = "reports/data/legacy/example.parquet"
        path = self.runtime / filename
        path.parent.mkdir(parents=True)
        pl.DataFrame({"value": [0, None]}).write_parquet(path)
        value = read_dataset(filename, self.runtime)
        assert value["storage"] is None
        assert value["rows"] == [{"value": 0}, {"value": None}]
        response = self.client.get(
            "/data/",
            params={"file": filename, "lang": "en"},
        )
        assert response.status_code == 200
        assert "Not recorded" in response.text

    def test_retained_report_restores_saved_assessment_types(self) -> None:
        """Keep saved assessment types in generic report previews."""
        filename = "reports/records/example/generation/members.parquet"
        path = self.runtime / filename
        frame = pl.DataFrame({"pa_class": ["C", "FA"]}).cast({
            "pa_class": pl.Enum(["C", "FA"]),
        })
        ParquetCache(
            schema=frame.schema,
            contract="report-preview-fixture",
            max_rows=100,
            max_bytes=1024 * 1024,
        ).write(path, frame)
        value = read_dataset(filename, self.runtime)
        assert "Enum(" in dict(value["schema"])["pa_class"]
        assert value["rows"] == [{"pa_class": "C"}, {"pa_class": "FA"}]

    def test_cache_rejects_links_and_bad_metadata(
        self,
    ) -> None:
        """Reject linked ancestors and mismatched metadata."""
        filename = "cache/example/20261110T152000.parquet"
        path = self.runtime / filename
        path.parent.mkdir(parents=True)
        frame = pl.DataFrame({"value": [1, 2]})
        cache = ParquetCache(
            schema=frame.schema,
            contract="preview-fixture",
            max_rows=200,
            max_bytes=1024 * 1024,
        )
        cache.write(path, frame)
        private = self.runtime / "private"
        path.parent.rename(private)
        path.parent.symlink_to(private, target_is_directory=True)
        assert (
            self.client.get("/data/", params={"file": filename}).status_code
            == 503
        )
        path.parent.unlink()
        private.rename(path.parent)
        metadata = pl.read_parquet_metadata(path)
        frame.head(1).write_parquet(path, metadata=metadata)
        assert (
            self.client.get("/data/", params={"file": filename}).status_code
            == 503
        )
        for query in (
            "page=0",
            "page=-1",
            "page=1.5",
            "page=1&page=2",
            "page=%ff",
        ):
            response = self.client.get("/data/?file=" + filename + "&" + query)
            assert response.status_code == 400
        for unsafe in (
            "cache/../private.parquet",
            "cache/.hidden.parquet",
            "cache//example.parquet",
            "state/private.parquet",
            "dry-run/cache/example.parquet",
        ):
            with self.assertRaises(DatasetSelectionError):
                read_dataset(unsafe, self.runtime)
