"""Verify saved proofs reach author counts and activity readers."""

# Retain explicit contributor thresholds and unittest exception style.
# ruff: file-ignore[magic-value-comparison, private-member-access]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data.articles import (
    authorship_corrections as proofs,
    authorship_store,
    current_assessments,
    current_authorship,
    milestone_store,
    quality_authors,
)
from data.articles.storage import AUTHORSHIP_PATH, MILESTONES_PATH
from shared.sources.quarry import users
from tasks.articles import quality_authors as activity_collector

_NOW = dt.datetime(2026, 11, 10, 16, tzinfo=dt.UTC)
_DAY = dt.date(2026, 1, 1)


def _contributors() -> list[dict]:
    """Provide one inflated leader and an exact 33% contributor.

    Returns:
        Valid raw byte/token shares for both source contracts.
    """
    return [
        {
            "editor": "1",
            "name": "Cleanup",
            "tokens": 1,
            "bytes": 67,
            "share": 0.67,
        },
        {
            "editor": "2",
            "name": "Exact",
            "tokens": 1,
            "bytes": 33,
            "share": 0.33,
        },
    ]


def _proof_record(
    page_id: int,
    revision: int,
    contract: str,
    *,
    fetched_at: dt.datetime = _NOW,
) -> pl.DataFrame:
    """Prove forty unchanged bytes belonged to a registered predecessor.

    Returns:
        One complete proof bound to the supplied raw observation.
    """
    proof = {
        "proof_contract": proofs.PROOF_CONTRACT,
        "origin_revision": revision - 50,
        "parent_revision": revision - 51,
        "before_source_sha256": "a" * 64,
        "after_source_sha256": "b" * 64,
        "before_tokens_sha256": "c" * 64,
        "after_tokens_sha256": "d" * 64,
        "before_characters": 99,
        "after_characters": 100,
        "matched_characters": 99,
        "added_characters": 1,
        "removed_reference_bytes": 1000,
    }
    proof = {"proof_id": proofs.proof_digest(proof), **proof}
    return pl.DataFrame(
        [
            {
                "page_id": page_id,
                "revision_id": revision,
                "raw_contract": contract,
                "raw_fetched_at": fetched_at,
                "eligible_bytes": 100,
                "observed_at": _NOW + dt.timedelta(minutes=10),
                "status": "complete",
                "checked_origins": [proof["origin_revision"]],
                "proofs": [proof],
                "transfers": [
                    {
                        "proof_id": proof["proof_id"],
                        "token_id": f"{page_id}:1",
                        "token_start": 0,
                        "token_stop": 40,
                        "raw_editor": "1",
                        "editor": "3",
                        "name": "Original",
                        "origin": revision - 100,
                        "bytes": 40,
                        "characters": 40,
                    },
                ],
            },
        ],
        schema=proofs.SCHEMA,
    )


class SavedQualityAuthorCorrectionTests(TestCase):
    """Read corrected credits without rewriting raw facts."""

    @override
    def setUp(self) -> None:
        """Save source artifacts before applying proofs."""
        self.runtime = Path(self.enterContext(TemporaryDirectory()))
        raw = pl.DataFrame(
            [
                {
                    "page_id": 1,
                    "date": _DAY,
                    "grade": "GA",
                    "action": "added",
                    "contract": authorship_store.CONTRACT,
                    "eligible_bytes": 100,
                    "listing_revision": 200,
                    "creation_revision": 10,
                    "cutoff": dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                    "creation": dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
                    "fetched_at": _NOW,
                    "authors": ["Cleanup"],
                    "contributors": _contributors(),
                },
            ],
            schema=authorship_store.SCHEMA,
        )
        authorship_store.save(self.runtime / AUTHORSHIP_PATH, raw)
        milestone_store.sync_events(
            pl.DataFrame([
                {
                    "page_id": 1,
                    "title": "Quality game",
                    "talk_page_id": 101,
                    "revision_id": 901,
                    "date": _DAY,
                    "kind": "quality",
                    "grade": "GA",
                    "action": "added",
                    "parameter": "action1",
                },
            ]),
            self.runtime / MILESTONES_PATH,
        )
        membership = pl.DataFrame([
            {
                "page_id": 1,
                "page_title": "Quality game",
                "page_revision": 200,
                "page_namespace": 0,
                "page_is_redirect": 0,
                "pa_class": "优良",
            },
            {
                "page_id": 2,
                "page_title": "Current B list",
                "page_revision": 300,
                "page_namespace": 0,
                "page_is_redirect": 0,
                "pa_class": "乙级列表",
            },
        ])
        current_assessments.save(
            self.runtime / current_assessments.DEFAULT_PATH,
            membership,
            observed_at=_NOW,
        )
        current_authorship.checkpoint(
            self.runtime,
            members=current_authorship.prepare_membership(
                membership,
                _NOW,
            ).with_columns(
                pl.lit("fixture").alias("banner_contract"),
                pl.lit(300, dtype=pl.Int64).alias("revision_id"),
                pl.lit(102, dtype=pl.Int64).alias("talk_page_id"),
                pl.lit(400, dtype=pl.Int64).alias("talk_revision"),
                pl.lit(399, dtype=pl.Int64).alias("listing_talk_revision"),
                pl.lit(_NOW).alias("listing_timestamp"),
            ),
            evidence=pl.DataFrame(
                [
                    {
                        "page_id": 2,
                        "revision_id": 300,
                        "contract": current_authorship.CONTRACT,
                        "eligible_bytes": 100,
                        "revision_timestamp": _NOW - dt.timedelta(hours=1),
                        "fetched_at": _NOW,
                        "contributors": _contributors(),
                    },
                ],
                schema=current_authorship.EVIDENCE_SCHEMA,
            ),
        )
        self.raw_files = {
            path: path.read_bytes()
            for path in (self.runtime / "reports/data").rglob("*.parquet")
        }

    def _save_proofs(self, *, include_b: bool = True) -> None:
        """Retain synthetic exact-source corrections."""
        rows = [_proof_record(1, 200, authorship_store.CONTRACT)]
        if include_b:
            rows.append(_proof_record(2, 300, current_authorship.CONTRACT))
        proofs.merge_save(self.runtime / proofs.DEFAULT_PATH, pl.concat(rows))

    def _assert_raw_files_unchanged(self) -> None:
        """Keep every initial source file unchanged."""
        assert all(
            path.read_bytes() == original
            for path, original in self.raw_files.items()
        )

    def test_saved_proofs_change_hq_and_b_whole_credits_without_writes(
        self,
    ) -> None:
        """Restore predecessors and retain exact 33% coauthors."""
        before = quality_authors.read_quality_authors(self.runtime)
        assert {row["name"] for row in before["authors"]} == {
            "Cleanup",
            "Exact",
        }
        self._save_proofs()
        proof_bytes = (self.runtime / proofs.DEFAULT_PATH).read_bytes()
        real_load = proofs.load
        with (
            patch.object(proofs, "load", wraps=real_load) as load,
            patch.object(
                authorship_store,
                "save",
                side_effect=AssertionError("raw rewrite"),
            ),
            patch(
                "pywikibot.Site",
                side_effect=AssertionError("source client"),
            ),
        ):
            default = quality_authors.read_quality_authors(self.runtime)
            load.assert_called_once_with(self.runtime / proofs.DEFAULT_PATH)
        assert {row["name"] for row in default["authors"]} == {
            "Original",
            "Exact",
        }
        assert all(row["credit"] == 1 for row in default["authors"])
        included = quality_authors.read_quality_authors(
            self.runtime,
            include_b=True,
        )
        assert included["b_coverage"] == {"collected": 1, "total": 1}
        assert {row["name"] for row in included["authors"]} == {
            "Original",
            "Exact",
        }
        for row in included["authors"]:
            assert row["credit"] == 2
            assert row["high_quality_credit"] == 1
            assert row["grades"]["GA"] == row["grades"]["BL"] == 1
        self._assert_raw_files_unchanged()
        assert (self.runtime / proofs.DEFAULT_PATH).read_bytes() == proof_bytes

    def test_b_revision_and_fetched_time_mismatches_remain_pending(
        self,
    ) -> None:
        """Leave unmatched B evidence raw and correct HQ."""
        self._save_proofs(include_b=False)
        variants = (
            _proof_record(2, 301, current_authorship.CONTRACT),
            _proof_record(
                2,
                300,
                current_authorship.CONTRACT,
                fetched_at=_NOW - dt.timedelta(seconds=1),
            ),
        )
        for mismatched in variants:
            saved = proofs.merge_save(
                self.runtime / proofs.DEFAULT_PATH,
                mismatched,
            )
            raw_b = current_authorship.load(self.runtime)["evidence"]
            derived = proofs.apply(raw_b, saved, revision_column="revision_id")
            assert derived["correction_status"].to_list() == ["pending"]
            included = quality_authors.read_quality_authors(
                self.runtime,
                include_b=True,
            )
            totals = {
                row["name"]: row["credit"] for row in included["authors"]
            }
            assert totals == {"Original": 1, "Exact": 2, "Cleanup": 1}
        self._assert_raw_files_unchanged()

    def test_activity_uses_the_same_corrected_hq_and_b_names(self) -> None:
        """Refresh activity for corrected names once per pass."""
        self._save_proofs()
        observed = pl.DataFrame(
            [
                {"name": name, "last_active": _NOW - dt.timedelta(hours=1)}
                for name in ("Original", "Exact")
            ],
            schema=users.SCHEMA,
        )
        with (
            patch.object(
                activity_collector,
                "fetch_user_activity",
                return_value=observed,
            ) as fetch,
            patch.object(
                proofs,
                "load",
                wraps=proofs.load,
            ) as load,
        ):
            activity_collector.refresh_quality_author_activity(
                Mock(),
                runtime=self.runtime,
            )
        assert set(fetch.call_args.args[1]) == {"Original", "Exact"}
        load.assert_called_once_with(self.runtime / proofs.DEFAULT_PATH)
        saved = quality_authors.load_activity(
            self.runtime / quality_authors.ACTIVITY_PATH,
        )
        assert set(saved["name"]) == {"Original", "Exact"}
        self._assert_raw_files_unchanged()

    def test_optional_b_failure_keeps_corrected_hq_activity_refreshing(
        self,
    ) -> None:
        """Isolate B storage failures from HQ activity."""
        self._save_proofs()
        observed = pl.DataFrame(
            [
                {"name": name, "last_active": _NOW - dt.timedelta(hours=1)}
                for name in ("Original", "Exact")
            ],
            schema=users.SCHEMA,
        )
        with (
            patch.object(
                current_authorship,
                "load",
                side_effect=ValueError("broken B store"),
            ),
            patch.object(
                activity_collector,
                "fetch_user_activity",
                return_value=observed,
            ) as fetch,
            patch.object(activity_collector._LOGGER, "warning") as warning,
        ):
            activity_collector.refresh_quality_author_activity(
                Mock(),
                runtime=self.runtime,
            )
        assert set(fetch.call_args.args[1]) == {"Original", "Exact"}
        assert set(
            quality_authors.load_activity(
                self.runtime / quality_authors.ACTIVITY_PATH,
            )["name"],
        ) == {"Original", "Exact"}
        warning.assert_called_once()
        self._assert_raw_files_unchanged()
