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
    *,
    banner_aliases: dict[str, tuple[str, ...]] | None = None,
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
        banner_aliases=banner_aliases,
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
    def test_requested_hook_uses_aligned_block_style() -> None:
        """Keep the complete hook in the requested canonical block."""
        hook = (
            "'''[[仓田真白|哪個《BanG Dream!》角色]]'''為作中"
            "-{zh-cn:乐队;zh-hk:樂隊;zh-tw:樂團;}-Morfonica的主唱\uff1f"
        )
        source = (
            "{{Article history\n|dyk1date=2026-10-05|dyk1entry=" + hook + "}}"
        )
        record = _record(source)
        record["parameters"] = {
            "dyk1date": "2026-10-05",
            "dyk1entry": hook,
        }
        record["id"] = repair_identity(record)
        assert render_section0_repair(record) == (
            "{{Article history\n"
            "| dyk1date  = 2026-10-05\n"
            "| dyk1entry = " + hook + "\n}}"
        )

    @staticmethod
    def test_formatting_preserves_complex_parameter_values() -> None:
        """Reformat boundaries while retaining all parameter content."""
        value = (
            "first <!-- preserve this comment -->\n"
            "* {{nowrap|[[Game|name]]}}\n\nlast"
        )
        source = "{{Article history|currentstatus=GA|notes=" + value + "}}\n"
        result = render_section0_repair(_record(source))
        template = mwparserfromhell.parse(result).filter_templates()[0]
        assert str(template.get("notes").value).strip() == value
        assert str(template.get("currentstatus").value).strip() == "GA"
        assert "\n| currentstatus = GA\n" in result
        assert "\n| maindateGA    = 2020-08-07\n}}\n" in result

    @staticmethod
    def test_legacy_conversion_removes_only_adjacent_blank_lines() -> None:
        """Keep one newline around history and preserve other gaps."""
        before = "\n\n{{Talk header}}"
        after = (
            "{{WikiProject Video games|class=GA}}\n\n"
            "<!-- unrelated spacing -->\n\n{{Archives}}\n"
        )
        source = (
            before
            + "\n\n\n  {{DYKtalk|date=2020-08-13|entry=Which [[Game]]?}}\n"
            + "\n\n"
            + after
        )
        result = render_section0_repair(_record(source))
        assert result.startswith(before + "\n{{Article history\n")
        assert "\n}}\n" + after in result
        assert result.endswith(after)
        assert "DYKtalk" not in result

    @staticmethod
    def test_indented_removed_line_does_not_corrupt_insertion_boundary() -> (
        None
    ):
        """Map an insertion within a removed line's indentation."""
        source = (
            " \n  {{DYKtalk|date=2020-08-13|entry=Which [[Game]]?}}\n\n"
            "{{WikiProject Video games|class=GA}}\n"
        )
        result = render_section0_repair(_record(source))
        assert result.startswith("{{Article history\n")
        assert result.endswith("\n}}\n{{WikiProject Video games|class=GA}}\n")
        assert "\n\n" not in result

    @staticmethod
    def test_significant_positional_whitespace_is_not_silently_changed() -> (
        None
    ):
        """Reject implicit values with significant whitespace."""
        record = _record("{{Article history| keep spaces |currentstatus=GA}}")
        rejected = False
        try:
            render_section0_repair(record)
        except ValueError:
            rejected = True
        assert rejected

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
        assert result.startswith("<!-- " + history + " -->\n")
        assert "{{Maintained|Editor}}\n" in result
        assert result.endswith(after)
        assert tail not in result
        assert (
            record["original_text"] + tail == before + history + after + tail
        )
        active = mwparserfromhell.parse(result).filter_templates()[0]
        assert str(active.name).strip() == "Article history"
        assert str(active.get("action1").value).strip() == "GAN"
        assert str(active.get("action1result").value).strip() == "listed"
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
    def test_new_history_follows_redirected_mixed_script_notices() -> None:
        """Put history after notices and ahead of the project shell."""
        before = (
            "<!-- {{WikiProject banner shell}} is only an example -->\n"
            "{{GAN}}\n{{跳至讨论}}\n{{討論顶板}}\n{{Warning}}\n"
        )
        after = "{{多个專題|1={{WPVG|class=GA}}}}\n{{译自|en|Game}}\n"
        aliases = {
            "GA nominee": ("Template:GA nominee", "Template:GAN"),
            "跳至討論": ("Template:跳至讨论",),
            "Talk header": ("Template:討論頂板",),
            "WikiProject banner shell": ("Template:多個專題",),
            "Translated page": ("Template:译自",),
        }
        record = _record(before + after, banner_aliases=aliases)
        result = render_section0_repair(record)
        assert result.index("{{Warning}}") < result.index("{{Article history")
        assert result.index("{{Article history") < result.index("{{多个專題")
        assert result.endswith(after)
        assert before.rstrip() in result

    @staticmethod
    def test_existing_history_moves_after_header_before_project_banners() -> (
        None
    ):
        """Relocate history and preserve unrelated source spans."""
        header = "{{Talk header}}\n"
        projects = "{{WikiProject Video games|class=GA}}\n{{Archives}}\n"
        history = "{{Article history|action1=GAN|action1result=listed}}"
        for source in (
            history + "\n" + header + projects,
            header + projects + history + "\n",
        ):
            result = render_section0_repair(_record(source))
            assert result.index("{{Talk header}}") < result.index(
                "{{Article history",
            )
            assert result.index("{{Article history") < result.index(
                "{{WikiProject Video games",
            )
            assert header.rstrip() in result
            assert projects in result
            assert result.count("{{Article history") == 1
            template = mwparserfromhell.parse(result).filter_templates()[1]
            assert str(template.get("action1result").value).strip() == "listed"

    @staticmethod
    def test_history_precedes_unfamiliar_project_banners() -> None:
        """Put history before known and unfamiliar project banners."""
        source = (
            "{{Talk header}}\n{{WikiProject Taiwan|class=GA}}\n"
            "{{Article history}}\n{{WikiProject Video games|class=GA}}\n"
        )
        result = render_section0_repair(_record(source))
        assert result.index("{{Talk header}}") < result.index(
            "{{Article history",
        )
        assert result.index("{{Article history") < result.index(
            "{{WikiProject Taiwan",
        )
        assert "{{WikiProject Taiwan|class=GA}}\n" in result
        assert "{{WikiProject Video games|class=GA}}\n" in result

    @staticmethod
    def test_quoted_and_nested_notices_cannot_move_history() -> None:
        """Keep quoted rules inert and preserve unknown wrappers."""
        source = (
            "{{Article history}}\n{{WikiProject Video games}}\n"
            "<nowiki>{{Talk header}}</nowiki>\n"
            "{{wrapper|example={{Warning}}}}\n"
        )
        result = render_section0_repair(_record(source))
        assert result.startswith("{{Article history")
        assert result.endswith(source[source.index("\n") :])

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
        assert str(template.get("action1").value).strip() == "GAN"

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
