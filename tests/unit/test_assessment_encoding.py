"""Verify lossless assessment enums across Parquet and String APIs."""

# unittest discovers instance methods.
# ruff: file-ignore[no-self-use]

from io import BytesIO
from unittest import TestCase

import polars as pl
from polars.testing import assert_frame_equal

from shared.support.assessment_encoding import (
    CLASS_CATEGORIES,
    IMPORTANCE_CATEGORIES,
    decode_assessments,
    encode_assessments,
)


class TestAssessmentEncoding(TestCase):
    """Preserve observations and query semantics through storage."""

    def test_parquet_round_trip_preserves_every_source_label(self) -> None:
        """Retain all source labels and null observations."""
        source = pl.DataFrame({
            "class": ["FA", None, "", "Future", "B+", "典範", "Unknown"],
            "importance": ["Top", None, "", "无", "NA", "Legacy", "Unknown"],
            "en_class": ["Stub"] * 7,
            "zh_importance": ["High"] * 7,
            "title": ["a", "b", "c", "d", "e", "f", "g"],
        })
        encoded = encode_assessments(source)
        assert encoded.schema["class"].categories.to_list() == [
            *CLASS_CATEGORIES,
            "",
            "B+",
            "Future",
            "Unknown",
            "典範",
        ]
        assert encoded.schema["importance"].categories.to_list() == [
            *IMPORTANCE_CATEGORIES,
            "",
            "Legacy",
            "Unknown",
        ]
        assert encoded.schema["en_class"] == pl.Enum(CLASS_CATEGORIES)
        assert encoded.schema["zh_importance"] == pl.Enum(
            IMPORTANCE_CATEGORIES,
        )
        assert encoded.schema["title"] == pl.String
        payload = BytesIO()
        encoded.write_parquet(payload)
        payload.seek(0)
        restored = pl.read_parquet(payload)
        assert_frame_equal(restored, encoded)
        assert_frame_equal(decode_assessments(restored), source)

    def test_decoded_join_and_filter_match_string_input(self) -> None:
        """Join separate category declarations as source strings."""
        left = pl.DataFrame({"class": ["FA", "legacy", None, ""]})
        right = pl.DataFrame({
            "class": ["legacy", "", "Stub"],
            "value": [1, 2, 3],
        })
        restored_left = decode_assessments(encode_assessments(left))
        restored_right = decode_assessments(encode_assessments(right))
        assert_frame_equal(
            restored_left.join(restored_right, on="class", how="left"),
            left.join(right, on="class", how="left"),
        )
        assert_frame_equal(
            restored_left.filter(pl.col("class").is_in(["legacy", ""])),
            left.filter(pl.col("class").is_in(["legacy", ""])),
        )

    def test_empty_null_and_unrelated_columns_keep_their_contracts(
        self,
    ) -> None:
        """Handle empty frames and preserve unrelated enums."""
        empty = pl.DataFrame(
            schema={"class": pl.String, "importance": pl.String},
        )
        assert encode_assessments(empty).schema == {
            "class": pl.Enum(CLASS_CATEGORIES),
            "importance": pl.Enum(IMPORTANCE_CATEGORIES),
        }
        assert_frame_equal(
            decode_assessments(encode_assessments(empty)),
            empty,
        )
        source = pl.DataFrame({
            "class": pl.Series([None], dtype=pl.String),
            "importance": pl.Series([None], dtype=pl.Null),
            "other": pl.Series(["x"], dtype=pl.Enum(["x"])),
            "numeric_class": [1],
        })
        encoded = encode_assessments(source)
        assert_frame_equal(encode_assessments(encoded), encoded)
        assert_frame_equal(decode_assessments(encoded), source)
