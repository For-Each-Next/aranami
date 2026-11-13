"""Verify embedded identities and unknown historical write times."""

# Stored schemas and timestamps are explicit contract assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
import io
import json
from unittest import TestCase

import polars as pl

from shared.support.parquet import parquet_metadata, validate_parquet_metadata


class TestParquetMetadata(TestCase):
    """Keep file-write metadata separate from source observations."""

    def setUp(self) -> None:
        """Prepare a typed table with nulls and a measured zero."""
        self.frame = pl.DataFrame({"value": [None, 0, 1]})

    def test_round_trip_preserves_owner_metadata_and_actual_file_facts(
        self,
    ) -> None:
        """Validate embedded facts against the actual file rows."""
        started = dt.datetime.now(dt.UTC)
        metadata = parquet_metadata(
            self.frame,
            contract="fixture:3",
            metadata={"owner": "retained"},
        )
        stream = io.BytesIO()
        self.frame.write_parquet(stream, metadata=metadata)
        stream.seek(0)
        embedded = pl.read_parquet_metadata(stream)
        stream.seek(0)
        frame = pl.read_parquet(stream)
        facts = validate_parquet_metadata(
            embedded,
            frame,
            contract="fixture:3",
        )
        assert embedded["owner"] == "retained"
        assert frame.equals(self.frame)
        assert facts["schema"] == 1
        assert facts["rows"] == self.frame.height
        assert facts["columns"] == {"value": "Int64"}
        assert started <= dt.datetime.fromisoformat(facts["written_at"])
        assert dt.datetime.fromisoformat(
            facts["written_at"],
        ) <= dt.datetime.now(dt.UTC)
        assert (
            facts["generation"]
            != json.loads(
                parquet_metadata(self.frame, contract="fixture:3")[
                    "aranami.storage"
                ],
            )["generation"]
        )

    def test_old_files_keep_unknown_write_times(self) -> None:
        """Preserve existing rows without inventing write metadata."""
        assert validate_parquet_metadata({}, self.frame) is None
        with self.assertRaises(ValueError):
            validate_parquet_metadata({}, self.frame, allow_legacy=False)

    def test_inconsistent_embedded_metadata_is_rejected(self) -> None:
        """Reject malformed schema, identity and timestamp facts."""
        metadata = parquet_metadata(self.frame, contract="fixture:3")
        original = json.loads(metadata["aranami.storage"])
        for change in (
            {"schema": 2},
            {"rows": 99},
            {"rows": True},
            {"columns": {"value": "String"}},
            {"generation": "not-a-generation"},
            {"written_at": "2026-11-03T00:00:00"},
            {"contract": "other"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_parquet_metadata(
                    {"aranami.storage": json.dumps({**original, **change})},
                    self.frame,
                    contract="fixture:3",
                )

    def test_invalid_contracts_do_not_produce_metadata(self) -> None:
        """Reject unbounded or control-bearing storage labels."""
        for contract in ("", " ", "x" * 201, "line\nbreak"):
            with (
                self.subTest(contract=contract),
                self.assertRaises(ValueError),
            ):
                parquet_metadata(self.frame, contract=contract)
