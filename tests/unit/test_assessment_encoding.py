"""Verify lossless assessment enums across Parquet and String APIs."""

# unittest discovers instance methods.
# ruff: file-ignore[no-self-use, pytest-unittest-raises-assertion]

from io import BytesIO
from unittest import TestCase

import polars as pl
from polars.testing import assert_frame_equal

from shared.assessment_profiles import (
    OBSERVED_PROFILE,
    ZHWIKI_VIDEO_GAMES,
    assessment_profile,
)
from shared.support.assessment_encoding import (
    decode_assessments,
    encode_assessments,
    restore_assessment_schema,
)


class TestAssessmentEncoding(TestCase):
    """Preserve observations and query semantics through storage."""

    def test_chinese_project_profile_preserves_raw_simplified_labels(
        self,
    ) -> None:
        """Keep audited raw labels and unknown additions losslessly."""
        source = pl.DataFrame({
            "pa_class": ["典范", "特色列表", "甲", "未来评级", "", None],
            "pa_importance": ["极高", "高", "未知", "未来重要度", "", None],
        })
        profile = assessment_profile("zhwiki", "电子游戏")
        assert profile is ZHWIKI_VIDEO_GAMES
        encoded = encode_assessments(source, profile=profile)
        assert encoded.schema["pa_class"].categories.to_list() == [
            *profile.class_values,
            "",
            "未来评级",
        ]
        assert "FA" not in encoded.schema["pa_class"].categories.to_list()
        assert "典範" not in encoded.schema["pa_class"].categories.to_list()
        assert (
            "Top" not in encoded.schema["pa_importance"].categories.to_list()
        )
        payload = BytesIO()
        encoded.write_parquet(payload)
        payload.seek(0)
        assert_frame_equal(
            decode_assessments(pl.read_parquet(payload)),
            source,
        )

    def test_other_wikis_and_taskforces_do_not_inherit_parent_profile(
        self,
    ) -> None:
        """Use site standards without inheriting a parent project."""
        for wiki, project, expected in (
            ("enwiki", "电子游戏", "enwiki-general:1"),
            ("enwiki", "Video games", "enwiki-general:1"),
            ("zhwiki", "电子游戏/任天堂工作组", "zhwiki-general:1"),
            ("unconfiguredwiki", "电子游戏", "observed-raw-1"),
        ):
            with self.subTest(wiki=wiki, project=project):
                profile = assessment_profile(wiki, project)
                assert profile.identifier == expected
                assert profile is not ZHWIKI_VIDEO_GAMES
                encoded = encode_assessments(
                    pl.DataFrame({"pa_class": ["FA", None]}),
                    profile=profile,
                )
                assert encoded.schema["pa_class"].categories.to_list() == (
                    list(dict.fromkeys((*profile.class_values, "FA")))
                )
                assert decode_assessments(encoded)["pa_class"].to_list() == [
                    "FA",
                    None,
                ]

    def test_observed_profile_keeps_all_unknown_observations_null(
        self,
    ) -> None:
        """Preserve an unassessed source without inventing labels."""
        source = pl.DataFrame({
            "pa_class": pl.Series([None], dtype=pl.String),
            "pa_importance": pl.Series([None], dtype=pl.String),
        })
        encoded = encode_assessments(source, profile=OBSERVED_PROFILE)
        assert encoded.schema["pa_class"].categories.to_list() == []
        assert_frame_equal(decode_assessments(encoded), source)

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
            "",
            "B+",
            "FA",
            "Future",
            "Unknown",
            "典範",
        ]
        assert encoded.schema["importance"].categories.to_list() == [
            "",
            "Legacy",
            "NA",
            "Top",
            "Unknown",
            "无",
        ]
        assert encoded.schema["en_class"] == pl.Enum(["Stub"])
        assert encoded.schema["zh_importance"] == pl.Enum(["High"])
        assert encoded.schema["title"] == pl.String
        payload = BytesIO()
        encoded.write_parquet(payload)
        payload.seek(0)
        restored = restore_assessment_schema(
            pl.read_parquet(payload),
            {name: str(dtype) for name, dtype in encoded.schema.items()},
        )
        assert_frame_equal(restored, encoded)
        assert_frame_equal(decode_assessments(restored), source)

    def test_saved_schema_rejects_corrupt_declarations_and_values(
        self,
    ) -> None:
        """Reject lost categories without translating observations."""
        frame = pl.DataFrame({"pa_class": ["未来评级", None]})
        for declaration in (
            "Enum(categories=['FA'])",
            "Enum(categories=['未来评级', '未来评级'])",
            "Enum(categories=[1, '未来评级'])",
            "Enum(categories=invalid)",
        ):
            with (
                self.subTest(declaration=declaration),
                self.assertRaises(ValueError),
            ):
                restore_assessment_schema(frame, {"pa_class": declaration})

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
            "class": pl.Enum([]),
            "importance": pl.Enum([]),
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
