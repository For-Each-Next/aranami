"""Verify manual header projection and runtime snapshot integration."""

# The fixtures use small protocol scalar values.

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import mwparserfromhell
from wiki_fixtures import OfflineSite

from data.consistency import repair_identity, valid_repair_input
from runtime import consistency_repairs as coordinator
from tasks.zhwiki.homepage_parameters import (
    HomepageAppearance,
    prepare_history_suggestions,
    prepare_section0_repair,
)
from updater.edit_summary import MAX_EDIT_SUMMARY_BYTES
from updater.services.zhwiki.consistency_repairs import (
    render_section0_repair,
    section0_edit_summary,
)

_SITE = OfflineSite("zh", "wikipedia")
_ALIASES = ("Template:Article history", "Template:ArticleHistory")
_DYK = ("Template:DYKtalk",)


def _record(
    text: str,
    appearances: tuple[HomepageAppearance, ...] = (),
) -> dict:
    """Prepare source facts from a real leading-section fixture.

    Returns:
        One exact checked header and its supported literal repair plan.
    """
    suggestions = prepare_history_suggestions(
        text,
        [HomepageAppearance("maindateGA", dt.date(2020, 8, 7)), *appearances],
        aliases=_ALIASES,
        legacy_dyk_aliases=_DYK,
        site=_SITE,
    )
    facts = prepare_section0_repair(
        text,
        suggestions,
        aliases=_ALIASES,
        legacy_dyk_aliases=_DYK,
        site=_SITE,
    )
    assert facts is not None
    value = {
        "page_id": 1,
        "talk_page_id": 2,
        "title": "Talk:Game",
        "revision_id": 42,
        "timestamp": "2026-11-13T12:00:00+00:00",
        **facts,
    }
    return {"id": repair_identity(value), **value}


class TestSection0Projection(TestCase):
    """Apply prepared changes to complete checked leads."""

    @staticmethod
    def test_exact_spans_preserve_quotes_unrelated_fields_and_tail() -> None:
        """Update the active target and preserve quoted examples."""
        history = "{{Article history|action1=GAN|action1result=listed}}"
        before = "<!-- " + history + " -->\n{{Maintained|Editor}}\n"
        after = "\n{{WikiProject Video games|class=GA}}\n<!-- keep -->\n"
        tail = "== Discussion ==\nThe rest of this page stays here.\n"
        record = _record(before + history + after + tail)
        original = copy.deepcopy(record)
        result = render_section0_repair(record)
        assert record == original
        assert result.startswith(before)
        assert result.endswith(after)
        assert tail not in result
        assert (
            record["original_text"] + tail == before + history + after + tail
        )
        active = mwparserfromhell.parse(result).filter_templates()[1]
        assert str(active.name).strip() == "Article history"
        assert str(active.get("action1").value) == "GAN"
        assert str(active.get("action1result").value) == "listed"
        assert str(active.get("maindateGA").value).strip() == "2020-08-07"

    @staticmethod
    def test_new_history_preserves_header_and_nested_hook_markup() -> None:
        """Convert a proven notice into a complete new history."""
        banner = (
            "{{WikiProject Video games|class=GA}}\n{{Translated|en|Game}}\n"
        )
        hook = "Which '''[[Game|title]]''' uses {{nowrap|A|B}}?"
        legacy = "{{DYKtalk|date=2020-08-13|entry=" + hook + "}}"
        record = _record(
            legacy + "\n" + banner + "== Talk ==\nKeep discussion",
        )
        result = render_section0_repair(record)
        assert legacy not in result
        assert result.endswith("\n" + banner)
        template = mwparserfromhell.parse(result).filter_templates()[0]
        assert str(template.name).strip() == "Article history"
        assert str(template.get("dyk1entry").value).strip() == hook
        assert str(template.get("dyk1date").value).strip() == "2020-08-13"
        assert str(template.get("maindateGA").value).strip() == "2020-08-07"

    @staticmethod
    def test_obsolete_fields_are_removed_only_after_numbered_migration() -> (
        None
    ):
        """Migrate complete notice fields before removing old keys."""
        record = _record(
            "{{Article history|dykdate=2020-08-13"
            "|action1=GAN|action1result=listed}}\n",
            (
                HomepageAppearance(
                    "dykdate",
                    dt.date(2020, 8, 13),
                    entry="Which [[Game]]?",
                ),
            ),
        )
        result = render_section0_repair(record)
        template = mwparserfromhell.parse(result).filter_templates()[0]
        assert not template.has("dykdate")
        assert not template.has("dykentry")
        assert (
            str(template.get("dyk1entry").value).strip() == "Which [[Game]]?"
        )
        assert str(template.get("action1").value) == "GAN"

    @staticmethod
    def test_invalid_source_identity_is_not_projected() -> None:
        """Reject source changed after its proof was recorded."""
        record = _record("{{Article history}}\n")
        record["original_text"] += "different source"
        assert not valid_repair_input(record)
        rejected = False
        try:
            render_section0_repair(record)
        except ValueError:
            rejected = True
        assert rejected

    @staticmethod
    def test_summary_is_complete_and_bounded() -> None:
        """Retain the purpose and bounded tool attribution."""
        summary = section0_edit_summary()
        assert "homepage history" in summary
        assert summary.endswith("([[toolforge:aranami|Aranami]])")
        assert len(summary.encode()) <= MAX_EDIT_SUMMARY_BYTES


class TestRepairSnapshotCoordination(TestCase):
    """Prepare manual output only in the runtime write path."""

    @staticmethod
    def test_finished_snapshot_is_detached_from_raw_cached_inputs() -> None:
        """Retain a complete proposal without changing source facts."""
        record = _record("{{Article history}}\n{{WikiProject Video games}}\n")
        snapshot = {"consistency": {"repair_inputs": {record["id"]: record}}}
        untouched = copy.deepcopy(snapshot)
        result = coordinator.prepare_snapshot_repairs(snapshot)
        assert snapshot == untouched
        prepared = result["consistency"]["section0_repairs"][record["id"]]
        assert "2020-08-07" in prepared["text"]
        assert prepared["edit_summary"] == section0_edit_summary()
        assert (
            result["consistency"]["repair_inputs"]
            == snapshot["consistency"]["repair_inputs"]
        )

    @staticmethod
    def test_atomic_writer_receives_projected_snapshot() -> None:
        """Pass complete manual code to the atomic data writer."""
        record = _record("{{Article history}}\n")
        snapshot = {"consistency": {"repair_inputs": {record["id"]: record}}}
        with patch.object(coordinator, "save_snapshot") as save:
            coordinator.save_dashboard_snapshot(
                Path("snapshot.parquet"),
                snapshot,
            )
        path, result = save.call_args.args
        assert path == Path("snapshot.parquet")
        assert record["id"] in result["consistency"]["section0_repairs"]

    @staticmethod
    def test_legacy_snapshot_needs_no_repair_projection() -> None:
        """Preserve saved observations with no repair inputs."""
        snapshot = {"consistency": {"items": []}}
        assert coordinator.prepare_snapshot_repairs(snapshot) is snapshot

    @staticmethod
    def test_combined_output_budget_withholds_oversized_proposal() -> None:
        """Withhold finished code when its byte budget is full."""
        record = _record("{{Article history}}\n")
        snapshot = {"consistency": {"repair_inputs": {record["id"]: record}}}
        with patch.object(coordinator, "MAX_REPAIR_INPUT_BYTES", 1):
            result = coordinator.prepare_snapshot_repairs(snapshot)
        assert result["consistency"]["section0_repairs"] == {}

    @staticmethod
    def test_full_snapshot_budget_preserves_an_existing_readable_result() -> (
        None
    ):
        """Withhold optional output when the snapshot is full."""
        record = _record("{{Article history}}\n")
        snapshot = {"consistency": {"repair_inputs": {record["id"]: record}}}
        size = len(json.dumps(snapshot, ensure_ascii=False).encode())
        with patch.object(coordinator, "MAX_SNAPSHOT_BYTES", size + 1):
            result = coordinator.prepare_snapshot_repairs(snapshot)
        assert result["consistency"]["repair_inputs"] == {}
        assert result["consistency"]["section0_repairs"] == {}
        assert "section0_repairs" not in snapshot["consistency"]

    @staticmethod
    def test_full_snapshot_headroom_can_withhold_only_finished_headers() -> (
        None
    ):
        """Omit optional source pairs when no complete proposal fits."""
        record = _record("{{Article history}}\n")
        snapshot = {"consistency": {"repair_inputs": {record["id"]: record}}}
        blank = copy.deepcopy(snapshot)
        blank["consistency"]["section0_repairs"] = {}
        size = len(json.dumps(blank, ensure_ascii=False).encode())
        with patch.object(coordinator, "MAX_SNAPSHOT_BYTES", size):
            result = coordinator.prepare_snapshot_repairs(snapshot)
        assert result["consistency"]["section0_repairs"] == {}
        assert result["consistency"]["repair_inputs"] == {}
        assert record["id"] in snapshot["consistency"]["repair_inputs"]

    @staticmethod
    def test_optional_pairs_cannot_block_a_large_dashboard_refresh() -> None:
        """Fit complete pairs and omit unavailable finding links."""
        first = _record("{{Article history}}\n")
        second = {**first, "page_id": 3}
        second["id"] = repair_identity(second)
        snapshot = {
            "payload": "中" * 1000,
            "consistency": {
                "repair_inputs": {row["id"]: row for row in (first, second)},
                "items": [
                    {"page_id": row["page_id"], "repair_id": row["id"]}
                    for row in (first, second)
                ],
            },
        }
        original = copy.deepcopy(snapshot)
        baseline = {
            **snapshot,
            "consistency": {
                "repair_inputs": {},
                "section0_repairs": {},
                "items": [{"page_id": 1}, {"page_id": 3}],
            },
        }
        size = len(
            json.dumps(
                baseline,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode(),
        )
        with patch.object(coordinator, "MAX_SNAPSHOT_BYTES", size + 1000):
            result = coordinator.prepare_snapshot_repairs(snapshot)
        assert snapshot == original
        inputs = result["consistency"]["repair_inputs"]
        assert list(inputs) == [first["id"]]
        assert set(inputs) == set(result["consistency"]["section0_repairs"])
        assert (
            len(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode(),
            )
            <= size + 1000
        )
        for finding in result["consistency"]["items"]:
            if "repair_id" in finding:
                assert finding["repair_id"] in inputs
            else:
                assert finding["page_id"] == second["page_id"]
