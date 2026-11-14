"""Verify exact lead repairs, identities and read-only retention."""

# Source spans and malformed scalar facts are protocol fixtures.
# ruff: file-ignore[private-member-access,magic-value-comparison]

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import test_consistency_cache as cache_fixtures
from wiki_fixtures import OfflineSite

from data import consistency as records
from data.caching import consistency as cache
from tasks.zhwiki import consistency as collector
from tasks.zhwiki.homepage_parameters import (
    HomepageAppearance,
    HomepageSuggestions,
    prepare_history_suggestions,
    prepare_section0_repair,
)

_SITE = OfflineSite("zh", "wikipedia")
_STAMP = "2026-11-03T14:00:00+00:00"
_ALIASES = ("Article history", "Template:ArticleHistory")
_DYK = ("DYKtalk",)
_HISTORY = "{{Article history|action1=GAN|action1result=listed}}"
_LEGACY = "{{DYKtalk|date=2020-01-02|entry=Which [[Game]]?}}"


def _prepare(text: str) -> tuple[HomepageSuggestions, dict | None]:
    """Bind a proven appearance to the supplied complete talk source.

    Returns:
        Existing suggestions and optional exact lead targets.
    """
    suggestions = prepare_history_suggestions(
        text,
        [HomepageAppearance("maindateGA", dt.date(2026, 11, 2))],
        aliases=_ALIASES,
        legacy_dyk_aliases=_DYK,
        site=_SITE,
    )
    return suggestions, prepare_section0_repair(
        text,
        suggestions,
        aliases=_ALIASES,
        legacy_dyk_aliases=_DYK,
        site=_SITE,
    )


def _input() -> dict:
    """Prepare a bounded source record with actual destination guards.

    Returns:
        One complete raw repair input independent of its projection.
    """
    _suggestions, facts = _prepare("preserve\n" + _HISTORY + "\n")
    assert facts is not None
    value = {
        "page_id": 1,
        "talk_page_id": 2,
        "title": "Talk:Game",
        "revision_id": 3,
        "timestamp": _STAMP,
        **facts,
    }
    return {"id": records.repair_identity(value), **value}


def _report(value: dict) -> dict:
    """Link existing findings to the matching checked repair input.

    Returns:
        A valid full consistency report containing one ready repair.
    """
    result = cache_fixtures._envelope()["data"]
    result["items"][0].update(
        checked_timestamp=_STAMP,
        repair_id=value["id"],
    )
    result.update(
        repair_inputs={value["id"]: value},
        section0_repairs={
            value["id"]: {
                "text": value["original_text"] + "<!-- fixed -->",
                "edit_summary": "Update recorded homepage appearances.",
            },
        },
    )
    return result


class TestSection0Inputs(TestCase):
    """Retain full lead source while withholding unsafe destinations."""

    @staticmethod
    def test_real_heading_boundary_preserves_quoted_headings_and_targets() -> (
        None
    ):
        """Keep examples and identify the active top-level call."""
        quoted = (
            "<!--\n== Commented example ==\n-->\n"
            "<blockquote>\n== Quoted example ==\n</blockquote>\n"
            "<pre>\n== Example ==\n" + _HISTORY + "\n</pre>\n"
        )
        lead = "<!-- keep -->\n" + quoted + _HISTORY + "\n" + _LEGACY + "\n"
        suffix = "== Real discussion ==\nUnrelated content {{keep|x}}\n"
        _suggestions, facts = _prepare(lead + suffix)
        assert facts is not None
        assert facts["original_text"] == lead
        history = facts["history_template"]
        assert history["text"] == _HISTORY
        assert history["start"] == lead.rindex(_HISTORY)
        assert lead[history["start"] : history["end"]] == _HISTORY
        assert facts["legacy_dyk_templates"][0]["text"] == _LEGACY
        assert facts["parameters"]["dyk1entry"] == "Which [[Game]]?"
        assert suffix not in facts["original_text"]

    @staticmethod
    def test_targets_after_heading_or_inside_wrapper_are_withheld() -> None:
        """Avoid duplicating or removing metadata below section zero."""
        for text in (
            "preface\n== Discussion ==\n" + _HISTORY,
            _HISTORY + "\n== Discussion ==\n" + _LEGACY,
            "{{wrapper|body=" + _HISTORY + "}}",
        ):
            _suggestions, facts = _prepare(text)
            assert facts is None

    @staticmethod
    def test_unpreserved_legacy_and_multiple_histories_are_withheld() -> None:
        """Withhold repairs when original facts are ambiguous."""
        _suggestions, facts = _prepare(
            _HISTORY + "{{DYKtalk|date=2020-01-02|extra=preserve me}}",
        )
        assert facts is None
        assert (
            prepare_section0_repair(
                _HISTORY + _HISTORY,
                HomepageSuggestions(
                    {"maindateGA": "2026-11-02"},
                    article_history_present=True,
                ),
                aliases=_ALIASES,
                legacy_dyk_aliases=_DYK,
                site=_SITE,
            )
            is None
        )

    @staticmethod
    def test_empty_lead_allows_new_history_without_copying_discussion() -> (
        None
    ):
        """Retain an empty section zero as an insertion destination."""
        _suggestions, facts = _prepare("== Discussion ==\nKeep this text.")
        assert facts is not None
        assert not facts["original_text"]
        assert facts["history_template"] is None
        assert facts["parameters"] == {"maindateGA": "2026-11-02"}

    @staticmethod
    def test_conflicting_banner_order_retains_a_ranked_repair_plan() -> None:
        """Prepare known conflicting groups for guideline reordering."""
        original = "{{WikiProject Video games}}\n{{Talk header}}\n" + _HISTORY
        _suggestions, facts = _prepare(original)
        assert facts is not None
        assert facts["original_text"] == original
        assert [span["text"] for span in facts["banner_order"]] == [
            "{{Talk header}}",
            "{{WikiProject Video games}}",
        ]
        assert facts["history_order"] == 1

    @staticmethod
    def test_collector_shares_one_exact_head_input_between_findings() -> None:
        """Reuse checked source without new reads or duplicate leads."""
        original = _HISTORY + "\n== Discussion ==\nKeep."
        suggestions, _facts = _prepare(original)
        read = Mock(site=_SITE)
        context = collector._Checks(
            read,
            Mock(),
            dt.datetime.fromisoformat(_STAMP),
            aliases={"Article history": _ALIASES, "DYKtalk": _DYK},
        )
        observation = collector._Observation(
            records.Evidence(
                "Talk:Game",
                "https://zh.wikipedia.org/w/index.php?oldid=3",
                3,
                _STAMP,
            ),
            original,
            2,
        )
        first = cache_fixtures._envelope()["data"]["items"][0]
        first["checked_timestamp"] = _STAMP
        second = {
            **first,
            "id": "other-finding",
            "check_id": "homepage_parameters",
        }
        collector._retain_section0_repair(
            context,
            {"page_id": 1},
            observation,
            suggestions,
            [first, second],
        )
        assert len(context.repair_inputs) == 1
        assert first["repair_id"] == second["repair_id"]
        value = context.repair_inputs[first["repair_id"]]
        assert value["original_text"] == _HISTORY + "\n"
        assert records.valid_repair_input(value)
        read.assert_not_called()


class TestRepairRecords(TestCase):
    """Reject stale identities and retain bounded source/ready maps."""

    @staticmethod
    def test_lookup_validates_only_selected_records() -> None:
        """Resolve a fixed lead without revalidating the worklist."""
        value = _input()
        result = _report(value)
        assert records.valid_result(result)
        with patch.object(records, "valid_result", side_effect=AssertionError):
            ready = records.review_repair(result, value["id"])
        assert ready is not None
        assert ready["title"] == "Talk:Game"
        assert ready["original_text"] == value["original_text"]
        assert ready["text"].endswith("<!-- fixed -->")
        assert ready["revision_id"] == 3
        assert ready["id"] == "3"
        assert records.review_repair(result, "3") == ready
        assert ready["talk_page_id"] == 2
        assert ready["edit_summary"] == "Update recorded homepage appearances."
        assert records.review_repair(result, "missing") is None
        assert (
            records.review_repair(
                {"repair_inputs": result["repair_inputs"]},
                value["id"],
            )
            is None
        )
        original = result["repair_inputs"].pop(value["id"])
        result["repair_inputs"]["a" * 64] = original
        result["section0_repairs"]["a" * 64] = result["section0_repairs"].pop(
            value["id"],
        )
        assert records.review_repair(result, "a" * 64) is None

    @staticmethod
    def test_revision_lookup_rejects_ambiguous_or_changed_saved_plans() -> (
        None
    ):
        """Reject changed sources and ambiguous revision matches."""
        value = _input()
        result = _report(value)
        value["title"] = "Talk:Changed"
        assert records.review_repair(result, "3") is None
        value = _input()
        result = _report(value)
        duplicate = {**value, "title": "Talk:Other"}
        duplicate["id"] = records.repair_identity(duplicate)
        result["repair_inputs"][duplicate["id"]] = duplicate
        assert records.review_repair(result, "3") is None

    @staticmethod
    def test_source_identity_spans_and_proposal_fields_are_validated() -> None:
        """Reject stale heads, overlapping edits and unrelated keys."""
        for changes in (
            {"revision_id": 99},
            {"talk_page_id": None},
            {"parameters": {"action1result": "listed"}},
            {"parameters": None},
            {"history_insertion": -1},
            {"history_insertion": True},
            {"history_insertion": 1.5},
            {"history_insertion": None},
            {"original_text": "x" * (records.MAX_REPAIR_TEXT_BYTES + 1)},
        ):
            value = {**_input(), **changes}
            assert not records.valid_repair_input(value)
        value = _input()
        value["legacy_dyk_templates"] = [dict(value["history_template"])]
        value["id"] = records.repair_identity(value)
        assert not records.valid_repair_input(value)
        value = _input()
        value["history_template"]["end"] -= 1
        value["id"] = records.repair_identity(value)
        assert not records.valid_repair_input(value)

    @staticmethod
    def test_banner_layout_requires_exact_disjoint_paired_fields() -> None:
        """Reject incomplete, overlapping or unbounded banner plans."""
        _suggestions, facts = _prepare("{{Talk header}}\n" + _HISTORY + "\n")
        assert facts is not None
        base = {**_input(), **facts}
        base["id"] = records.repair_identity(base)
        assert records.valid_repair_input(base)
        invalid = [
            {"history_order": -1},
            {"history_order": True},
            {"history_order": len(base["banner_order"]) + 1},
            {"banner_order": [base["history_template"]]},
            {"banner_order": base["banner_order"] * 2},
            {"banner_order": base["banner_order"] * 257},
            {"banner_order": [{"start": 0, "end": 1, "text": "wrong"}]},
        ]
        for changes in invalid:
            record = {**base, **changes}
            record["id"] = records.repair_identity(record)
            assert not records.valid_repair_input(record)
        for omitted in ("banner_order", "history_order"):
            record = {
                key: value for key, value in base.items() if key != omitted
            }
            record["id"] = records.repair_identity(record)
            assert not records.valid_repair_input(record)

    @staticmethod
    def test_complete_repair_maps_can_retain_more_than_256_headers() -> None:
        """Retain larger worklists within the separate byte bound."""
        base = _input()
        inputs = {}
        for page_id in range(1, 258):
            record = {**base, "page_id": page_id}
            record["id"] = records.repair_identity(record)
            inputs[record["id"]] = record
        report = _report(base)
        report["repair_inputs"] = inputs
        assert records.valid_result(report)

    @staticmethod
    def test_finding_links_must_match_the_checked_source_head() -> None:
        """Bind a header proposal to its checked observation."""
        for field, value in (
            ("checked_revision", 99),
            ("checked_title", "Talk:Other"),
            ("checked_timestamp", None),
            ("page_id", 99),
        ):
            report = _report(_input())
            report["items"][0][field] = value
            assert not records.valid_result(report)

    @staticmethod
    def test_cache_round_trip_and_legacy_read_are_source_free() -> None:
        """Read source maps and old caches without file writes."""
        record = cache_fixtures._envelope()
        record["data"] = _report(_input())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "consistency.parquet"
            cache.save(path, record)
            before = path.read_bytes()
            with patch.object(
                type(cache._CACHE),
                "write",
                side_effect=AssertionError,
            ):
                assert cache.read(path) == record
            assert path.read_bytes() == before
            frame = cache._CACHE.read(path)
            cache._LEGACY_CACHE.write(path, frame)
            assert cache.read(path) == record
            older = cache_fixtures._envelope()
            cache.save(path, older)
            assert cache.read(path) == older

    @staticmethod
    def test_projection_maps_do_not_mutate_raw_source_record() -> None:
        """Retain original source and fixed lead separately."""
        value = _input()
        original = copy.deepcopy(value)
        report = _report(value)
        assert records.review_repair(report, value["id"]) is not None
        assert value == original
        report["section0_repairs"][value["id"]]["text"] = value[
            "original_text"
        ]
        assert not records.valid_result(report)
        assert records.review_repair(report, value["id"]) is None

    @staticmethod
    def test_ready_summary_is_bounded_and_cannot_replace_source_guards() -> (
        None
    ):
        """Require a bounded literal summary beside the fixed lead."""
        value = _input()
        for summary in ("", "unsafe\nsummary", "x" * 256, None):
            report = _report(value)
            report["section0_repairs"][value["id"]]["edit_summary"] = summary
            assert not records.valid_result(report)
            assert records.review_repair(report, value["id"]) is None
