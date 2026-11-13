"""Verify saved evidence in localized maintenance worklists."""

# Fixture rows deliberately exercise every source kind together.
# ruff: file-ignore[manual-list-comprehension]

from __future__ import annotations

from copy import deepcopy
from html import unescape
from unittest import TestCase

from data.consistency import repair_identity
from webservice.consistency import consistency_groups, finding_details
from webservice.pages import (
    render_consistency_checks,
    render_maintenance,
    render_maintenance_overview,
)
from webservice.presentation import Interface


def consistency_snapshot() -> dict:
    """Build independent evidence with every supported finding kind.

    Returns:
        Saved observations suitable for offline rendering and browsing.
    """
    stamp = "2026-11-03T00:05:00Z"
    kinds = (
        "nomination_banner",
        "award_template",
        "homepage_history",
        "project_list_missing",
        "project_list_extra",
    )
    items = []
    for number in range(1, 14):
        for kind in kinds:
            items.append({
                "id": f"{kind}:{number}",
                "page_id": number,
                "kind": kind,
                "check_id": "gan",
                "title": f"Game {number:02}",
                "url": f"https://zh.wikipedia.org/wiki/Game_{number}",
                "talk_url": f"https://zh.wikipedia.org/wiki/Talk:Game_{number}",
                "source_title": "Wikipedia:優良條目候選",
                "source_url": "https://zh.wikipedia.org/w/index.php?oldid=101",
                "source_revision": 101,
                "source_timestamp": "2026-11-02T12:00:00Z",
                "checked_title": f"Talk:Game {number:02}",
                "checked_url": (
                    "https://zh.wikipedia.org/w/index.php?"
                    f"oldid={200 + number}"
                ),
                "checked_revision": 200 + number,
                "checked_timestamp": "2026-11-02T14:00:00Z",
                "observed_at": stamp,
                "expected_template": "ArticleHistory"
                if kind == "homepage_history"
                else "GAN",
                "expected_field": "maindateGA"
                if kind == "homepage_history"
                else None,
                "event_date": "2026-10-30"
                if kind == "homepage_history"
                else None,
                "assessment_class": "优良",
                "assessment_importance": "高",
            })
    milestone = deepcopy(items[0])
    milestone.update(
        id="quarterly:2026-09-30",
        page_id=99,
        kind="quarterly_statistics_missing",
        check_id="quarterly_challenge",
        target_type="project",
        title="WikiProject:电子游戏/里程碑/基础条目扩充挑战",
        url="https://zh.wikipedia.org/wiki/WikiProject:电子游戏/里程碑/基础条目扩充挑战",
        talk_url="https://zh.wikipedia.org/wiki/WikiProject_talk:电子游戏/里程碑/基础条目扩充挑战",
        expected_template="",
        expected_field=None,
        event_date="2026-09-30",
        assessment_class=None,
        assessment_importance=None,
        suggested_values={
            "fa": 10,
            "ga": 20,
            "b": 30,
            "c": 40,
            "start": 50,
            "stub": 0,
            "top": 5,
            "high": 15,
            "total": 150,
            "start_percentage": 100.0,
            "quality_index": 5.73,
            "en_total": None,
        },
        suggested_snapshot={"snapshot_date": "2026-09-30"},
    )
    items.append(milestone)
    original = "<!-- keep -->\n{{Article history|currentstatus=GA}}\n"
    history = "{{Article history|currentstatus=GA}}"
    start = original.index(history)
    repair = {
        "page_id": 1,
        "talk_page_id": 101,
        "title": "Talk:Game 01",
        "revision_id": 201,
        "timestamp": "2026-11-02T14:00:00Z",
        "original_text": original,
        "history_template": {
            "start": start,
            "end": start + len(history),
            "text": history,
        },
        "legacy_dyk_templates": [],
        "parameters": {"maindateGA": "2026-10-30"},
        "obsolete_parameters": [],
    }
    repair["id"] = repair_identity(repair)
    for item in items:
        if item["kind"] == "homepage_history" and item["page_id"] == 1:
            item["repair_id"] = repair["id"]
    return {
        "backlog": None,
        "consistency": {
            "schema": 1,
            "wiki": "zhwiki",
            "project": "电子游戏",
            "checked_at": stamp,
            "items": items,
            "checks": [
                {"check_id": "gan", "status": "complete"},
                {"check_id": "fac", "status": "unavailable"},
                {"check_id": "pr", "status": "unsupported"},
            ],
            "sources": [],
            "repair_inputs": {repair["id"]: repair},
            "section0_repairs": {
                repair["id"]: {
                    "text": original.replace(
                        "|currentstatus=GA",
                        "|currentstatus=GA|maindateGA=2026-10-30",
                    ),
                    "edit_summary": "Update recorded homepage appearances.",
                },
            },
        },
    }


class TestConsistencyPresentation(TestCase):
    """Keep findings localized and separate from unknown data."""

    @staticmethod
    def test_project_quality_history_has_a_localized_checks_group() -> None:
        """Link missing review actions to their checked revisions."""
        snapshot = consistency_snapshot()
        finding = dict(snapshot["consistency"]["items"][0])
        finding.update(
            kind="quality_history",
            check_id="quality_history",
            expected_template="Article history",
            expected_field="BPAR",
            assessment_class="乙上",
            event_date=None,
        )
        snapshot["consistency"]["items"] = [finding]
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = render_consistency_checks(snapshot, None, None, locale)
            assert interface.text("consistency.task.quality_history") in output
            assert interface.text(
                "consistency.finding.quality_history",
                template="Article history",
                field="BPAR",
            ) in unescape(output)
            assert finding["talk_url"] in output
            assert finding["checked_url"].replace("&", "&amp;") in output

    @staticmethod
    def test_every_kind_has_native_localized_worklist_and_evidence() -> None:
        """Expose exact checked/source links with observed times."""
        snapshot = consistency_snapshot()
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            for kind in (
                "nomination",
                "award",
                "history",
                "project_list_missing",
                "project_list_extra",
            ):
                output = render_maintenance(
                    snapshot,
                    {"task": [f"consistency_{kind}"]},
                    locale,
                )
                assert interface.text(f"consistency.task.{kind}") in output
                assert (
                    '<div class="consistency-findings consistency-cards '
                    'consistency-history-cards">'
                    if kind == "history"
                    else '<ul class="consistency-findings consistency-cards">'
                ) in output
                assert '<table class="maintenance-table' not in output
                assert (
                    'href="https://zh.wikipedia.org/wiki/Talk:Game_1"'
                    in output
                )
                assert (
                    'href="https://zh.wikipedia.org/w/index.php?oldid=101"'
                    in output
                )
                assert (
                    'href="https://zh.wikipedia.org/w/index.php?oldid=201"'
                    in output
                )
                assert 'datetime="2026-11-03T00:05:00' in output
                assert 'method="post"' not in output
                assert 'id="maintenance-consistency"' in output

    @staticmethod
    def test_article_grouping_keeps_all_findings_before_pagination() -> None:
        """Keep repeated findings together and filter all rows."""
        snapshot = consistency_snapshot()
        duplicate = deepcopy(snapshot["consistency"]["items"][0])
        duplicate.update(id="other", expected_template="FAC")
        snapshot["consistency"]["items"].append(duplicate)
        groups = consistency_groups(snapshot, Interface("en"))
        nomination = groups[0]
        assert nomination["count"] == 13  # ruff: ignore[magic-value-comparison]
        assert len(nomination["items"][0]["consistency"]) == 2  # ruff: ignore[magic-value-comparison]
        output = render_maintenance(
            snapshot,
            {"task": ["consistency_nomination"], "q": ["Game 13"]},
            "en",
        )
        assert "Game 13" in output
        assert ">Game 01<" not in output
        assert 'name="q"' in output

    @staticmethod
    def test_overview_findings_keep_maintenance_counts_separate() -> None:
        """List issues without inserting consistency category cards."""
        snapshot = consistency_snapshot()
        snapshot["backlog"] = {
            "observed_at": "2026-11-02T17:33:00Z",
            "total": 0,
            "groups": [],
        }
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = render_maintenance_overview(snapshot, None, None, locale)
            assert interface.text("backlog.overview_empty") in output
            assert 'class="consistency-findings' not in output
            assert 'id="backlog-section-consistency"' not in output
            assert "task=consistency_" not in output
            assert 'class="backlog-card"' not in output
            assert interface.text("consistency.heading") not in output
            assert 'datetime="2026-11-02T17:33:00Z"' in output
            output = render_consistency_checks(snapshot, None, None, locale)
            assert output.count('class="consistency-group"') == 6  # ruff: ignore[magic-value-comparison]
            assert 'class="ui-card consistency-card' in output
            assert 'class="ui-card consistency-group"' not in output
            assert output.count('<ul class="consistency-findings ') == 5  # ruff: ignore[magic-value-comparison]
            assert output.count(' consistency-history-cards"') == 1
            assert '<ol class="consistency-findings ' not in output
            assert '<table class="maintenance-table' not in output
            assert interface.text("consistency.heading") in output
            article = 'href="https://zh.wikipedia.org/wiki/Game_1"'
            assert output.count(article) == 5  # ruff: ignore[magic-value-comparison]
            assert 'datetime="2026-11-03T00:05:00Z"' in output
            assert 'datetime="2026-11-02T17:33:00Z"' not in output

    @staticmethod
    def test_quarterly_hints_keep_project_pages_and_unknown_metrics() -> None:
        """Keep project-page hints separate from article assessments."""
        snapshot = consistency_snapshot()
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = render_maintenance(
                snapshot,
                {"task": ["consistency_quarterly"]},
                locale,
            )
            assert interface.text("consistency.quarter_end") in output
            assert interface.text("consistency.snapshot_note") in output
            assert 'class="consistency-unknown"' in output
            assert "100.0%" in output
            assert ">5.73</dd>" in output
            assert ">0</dd>" in output
            assert interface.text("consistency.unknown") in output
            assert (
                f">{interface.text('assessment.class.Unassessed')}</td>"
                not in output
            )
            assert 'datetime="2026-09-30"' in output

    @staticmethod
    def test_filtered_empty_does_not_claim_no_global_findings() -> None:
        """Keep empty filters distinct from empty evidence."""
        output = render_maintenance(
            consistency_snapshot(),
            {"task": ["consistency_nomination"], "q": ["no matching title"]},
            "en",
        )
        interface = Interface("en")
        assert interface.text("backlog.empty") in output
        assert interface.text("consistency.empty") not in output

    @staticmethod
    def test_unknown_coverage_is_distinct_from_completed_empty_checks() -> (
        None
    ):
        """Keep unavailable checks explicit without saved findings."""
        snapshot = consistency_snapshot()
        snapshot["consistency"]["items"] = []
        output = render_consistency_checks(snapshot, None, None, "en")
        assert "1 complete · 1 unavailable · 1 unsupported" in output
        assert "No discrepancies were found in completed checks." in output
        missing = render_consistency_checks(None, None, None, "en")
        assert "Consistency observations are not available yet." in missing
        assert "No discrepancies were found" not in missing

    @staticmethod
    def test_unknown_revisions_and_hostile_template_text_remain_honest() -> (
        None
    ):
        """Escape text without inventing revision provenance."""
        snapshot = consistency_snapshot()
        item = snapshot["consistency"]["items"][0]
        item.update(
            expected_template="GAN<script>alert(1)</script>",
            checked_revision=None,
            checked_timestamp=None,
        )
        snapshot["consistency"]["items"] = [item]
        output = render_maintenance(
            snapshot,
            {"task": ["consistency_nomination"]},
            "en",
        )
        assert "GAN&lt;script&gt;alert(1)&lt;/script&gt;" in output
        assert ">Checked page</a>" in output
        assert ">Checked revision</a>" not in output

    @staticmethod
    def test_homepage_dates_are_consolidated_sorted_and_copyable() -> None:
        """Keep one numbered article with every source and date."""
        snapshot = consistency_snapshot()
        original = next(
            row
            for row in snapshot["consistency"]["items"]
            if row["kind"] == "homepage_history"
        )
        rows = []
        for number, field, day in (
            (1, "maindateGA", "2019-02-04"),
            (1, "maindateGA", "2026-10-31"),
            (1, "maindateFA", "2026-11-01"),
            (2, "maindateGA", "2026-11-02"),
        ):
            finding = deepcopy(original)
            finding.update(
                id=f"{number}:{field}:{day}",
                page_id=number,
                title=f"Game {number:02}",
                url=f"https://zh.wikipedia.org/wiki/Game_{number}",
                expected_field=field,
                event_date=day,
                source_url=f"https://zh.wikipedia.org/wiki/Source/{day}",
            )
            rows.append(finding)
        snapshot["consistency"]["items"] = rows
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            output = render_consistency_checks(snapshot, None, None, locale)
            assert output.index(">Game 02<") < output.index(">Game 01<")
            assert output.count(">Game 01<") == 1
            assert (
                '<div class="consistency-findings consistency-cards '
                'consistency-history-cards">'
            ) in output
            assert '<span class="consistency-ordinal">1.</span>' in output
            assert '<span class="consistency-ordinal">2.</span>' in output
            assert '<ol class="consistency-findings ' not in output
            assert '<ul class="consistency-findings ' not in output
            assert (
                " | maindateFA = 2026-11-01\n | maindateGA = 2026-10-31"
            ) in output
            assert " | maindateGA = 2019-02-04" not in output
            assert " | maindateGA2" not in output
            assert 'datetime="2019-02-04"' in output
            assert "<code>maindateFA</code>" in output
            assert "<code>maindateGA</code>" in output
            assert (
                'href="https://zh.wikipedia.org/wiki/Source/2019-02-04"'
                in output
            )
            assert interface.text("consistency.copy_code") in output
            assert 'data-copy-target="history-code-1"' in output
            assert "readonly" in output
            assert 'method="post"' not in output
            filtered = render_maintenance(
                snapshot,
                {"task": ["consistency_history"]},
                locale,
            )
            assert filtered.index(">Game 02<") < filtered.index(">Game 01<")
            assert filtered.count(">Game 01<") == 1
            assert (
                '<div class="consistency-findings consistency-cards '
                'consistency-history-cards">' in filtered
            )
            assert '<span class="consistency-ordinal">1.</span>' in filtered
            assert '<span class="consistency-ordinal">2.</span>' in filtered
            assert '<ol class="consistency-findings ' not in filtered
            assert '<ul class="consistency-findings ' not in filtered

    @staticmethod
    def test_homepage_numbering_continues_on_later_pages() -> None:
        """Keep direct-card ordinals after sorting and pagination."""
        snapshot = consistency_snapshot()
        snapshot["consistency"]["items"] = [
            row
            for row in snapshot["consistency"]["items"]
            if row["kind"] == "homepage_history"
        ]
        for index, finding in enumerate(snapshot["consistency"]["items"]):
            finding["event_date"] = f"2026-10-{index + 1:02}"
            finding["expected_field"] = "maindateGA"
        output = render_maintenance(
            snapshot,
            {"task": ["consistency_history"], "page": ["2"]},
            "en",
        )
        assert (
            '<div class="consistency-findings consistency-cards '
            'consistency-history-cards">' in output
        )
        assert '<span class="consistency-ordinal">11.</span>' in output
        assert '<span class="consistency-ordinal">13.</span>' in output
        assert '<span class="consistency-ordinal">1.</span>' not in output
        assert '<ol class="consistency-findings ' not in output
        assert '<ul class="consistency-findings ' not in output
        assert output.index(">Game 03<") < output.index(">Game 02<")
        assert ">Game 13<" not in output

    @staticmethod
    def test_checks_preview_keeps_the_total_and_links_to_every_article() -> (
        None
    ):
        """Bound the preview and retain the complete worklist."""
        snapshot = consistency_snapshot()
        snapshot["consistency"]["items"] = [
            row
            for row in snapshot["consistency"]["items"]
            if row["kind"] == "homepage_history"
        ]
        for index, finding in enumerate(snapshot["consistency"]["items"]):
            finding["event_date"] = f"2026-10-{index + 1:02}"
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_consistency_checks(snapshot, None, None, locale)
            assert output.count('class="ui-card consistency-card') == 10  # ruff: ignore[magic-value-comparison]
            assert '<span class="consistency-count">13</span>' in output
            assert '<span class="consistency-ordinal">10.</span>' in output
            assert '<span class="consistency-ordinal">11.</span>' not in output
            assert output.index(">Game 13<") < output.index(">Game 04<")
            assert ">Game 03<" not in output
            assert (
                'href="/checks/?task=consistency_history&amp;lang='
                + locale
                + '"'
            ) in output
            assert (
                Interface(locale).text("consistency.view_all", count=13)
                in output
            )
            filtered = render_maintenance(
                snapshot,
                {"task": ["consistency_history"], "q": ["Game 03"]},
                locale,
            )
            assert ">Game 03<" in filtered
            assert 'action="/checks/"' in filtered
            assert 'href="/checks/" class="back"' in filtered

    @staticmethod
    def test_compact_cards_keep_history_separate_from_needed_updates() -> None:
        """Show every date while suggesting only the missing field."""
        snapshot = consistency_snapshot()
        finding = deepcopy(
            next(
                row
                for row in snapshot["consistency"]["items"]
                if row["kind"] == "homepage_history"
            ),
        )
        finding.update(
            expected_field="maindateGA",
            event_date="2026-10-30",
            suggested_parameters={"maindateGA": "2026-10-30"},
            article_history_present=True,
            appearance_history=[
                {
                    "field": field,
                    "date": day,
                    "source_title": "Homepage history",
                    "source_url": f"https://zh.wikipedia.org/wiki/History/{day}",
                    "source_revision": 101,
                    "source_timestamp": "2026-11-02T12:00:00Z",
                    "observed_at": "2026-11-03T00:05:00Z",
                }
                for field, day in (
                    ("maindateFA", "2020-02-03"),
                    ("maindateGA", "2019-02-04"),
                    ("maindateGA", "2026-10-30"),
                )
            ],
        )
        snapshot["consistency"]["items"] = [finding]
        output = render_consistency_checks(snapshot, None, None, "zh-hans")
        assert output.count('class="ui-card consistency-card') == 1
        assert (
            'class="consistency-missing">缺失<code>maindateGA</code>' in output
        )
        assert " | maindateGA = 2026-10-30" in output
        assert " | maindateFA" not in output
        assert "{{Article history" not in output
        assert '<details class="ui-accordion ' in output
        assert "2019-02-04" in output
        assert "2020-02-03" in output
        assert "/History/2019-02-04" in output
        assert "将这些参数加入现有的" not in output

    @staticmethod
    def test_ambiguous_history_keeps_evidence_without_fallback_code() -> None:
        """Withhold fallback for ambiguous current templates."""
        snapshot = consistency_snapshot()
        finding = deepcopy(
            next(
                row
                for row in snapshot["consistency"]["items"]
                if row["kind"] == "homepage_history"
            ),
        )
        finding.update(
            suggested_parameters={},
            suggested_parameters_available=False,
            removable_dyk_template_count=2,
        )
        snapshot["consistency"]["items"] = [finding]
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_consistency_checks(snapshot, None, None, locale)
            assert (
                Interface(locale).text("consistency.ambiguous_history")
                in output
            )
            assert 'class="ui-card consistency-card' in output
            assert (
                'href="https://zh.wikipedia.org/w/index.php?oldid=101"'
                in output
            )
            assert 'class="consistency-code"' not in output
            assert " | maindateGA" not in output
            assert 'data-copy-target="history-code-' not in output

    @staticmethod
    def test_numbered_parameters_and_safe_dyk_conversion_are_literal() -> None:
        """Offer verified slots and lossless legacy conversion."""
        snapshot = consistency_snapshot()
        finding = deepcopy(
            next(
                row
                for row in snapshot["consistency"]["items"]
                if row["kind"] == "homepage_history"
            ),
        )
        finding.update(
            suggested_parameters={
                "dyk2entry": "A verified hook about [[Game]]?",
                "itn2date": "2026-10-29",
                "otd2oldid": "12345",
                "otd2date": "2026-10-31",
                "dyk1date": "2019-02-04",
                "dyk2date": "2026-10-30",
            },
            article_history_present=False,
            legacy_dyk_template_count=2,
            removable_dyk_template_count=2,
        )
        snapshot["consistency"]["items"] = [finding]
        output = render_consistency_checks(snapshot, None, None, "zh-hans")
        assert "{{Article history\n | itn2date  = 2026-10-29" in output
        assert (
            " | dyk1date  = 2019-02-04\n | dyk1entry = \n\n"
            " | dyk2date  = 2026-10-30" in output
        )
        assert " | dyk2entry = A verified hook about [[Game]]?" in output
        assert " | otd2date  = 2026-10-31\n | otd2oldid = 12345\n}}" in output
        assert "移除2个DYK模板" in output
        assert 'method="post"' not in output
        finding["removable_dyk_template_count"] = 0
        output = render_consistency_checks(snapshot, None, None, "zh-hans")
        assert "移除2个DYK模板" not in output

    @staticmethod
    def test_history_code_aligns_keys_and_separates_each_notice_slot() -> None:
        """Keep FA/GA adjacent, multiline hooks and separate notices."""
        finding = deepcopy(
            next(
                row
                for row in consistency_snapshot()["consistency"]["items"]
                if row["kind"] == "homepage_history"
            ),
        )
        hook = "Which [[Game]] used {{lang|en|A & B}}?\nSecond source line."
        finding.update(
            article_history_present=False,
            suggested_parameters={
                "maindateFA": ("<!-- 2018-10-30, 2022-07-23 --> 2026-10-13"),
                "maindateGA": "2018-09-16",
                "itn1date": "2020-01-01",
                "itn1link": "Wikipedia:First news",
                "itn2date": "2021-01-01",
                "dyk1date": "2020-02-01",
                "dyk1entry": hook,
                "dyk2date": "2021-02-01",
                "dyk2entry": "Second hook",
                "otd1date": "2020-03-01",
                "otd1oldid": "123",
                "otd2date": "2021-03-01",
                "otd2oldid": "456",
            },
        )
        code = finding_details({"consistency": [finding]}, Interface("en"))[0][
            "code"
        ]
        assert code == (
            "{{Article history\n"
            " | maindateFA = "
            "<!-- 2018-10-30, 2022-07-23 --> 2026-10-13\n"
            " | maindateGA = 2018-09-16\n\n"
            " | itn1date   = 2020-01-01\n"
            " | itn1link   = Wikipedia:First news\n\n"
            " | itn2date   = 2021-01-01\n\n"
            " | dyk1date   = 2020-02-01\n"
            f" | dyk1entry  = {hook}\n\n"
            " | dyk2date   = 2021-02-01\n"
            " | dyk2entry  = Second hook\n\n"
            " | otd1date   = 2020-03-01\n"
            " | otd1oldid  = 123\n\n"
            " | otd2date   = 2021-03-01\n"
            " | otd2oldid  = 456\n"
            "}}"
        )
        snapshot = consistency_snapshot()
        snapshot["consistency"]["items"] = [finding]
        output = render_consistency_checks(snapshot, None, None, "en")
        assert code in unescape(output)
        assert "&lt;!-- 2018-10-30, 2022-07-23 --&gt;" in output
        assert "maindateFA2" not in output
        finding["article_history_present"] = True
        partial = finding_details(
            {"consistency": [finding]},
            Interface("en"),
        )[0]["code"]
        assert partial == code.removeprefix(
            "{{Article history\n",
        ).removesuffix(
            "\n}}",
        )

    @staticmethod
    def test_numbered_migration_names_the_old_parameters_to_remove() -> None:
        """Keep replacement code beside its precise cleanup hint."""
        snapshot = consistency_snapshot()
        finding = deepcopy(
            next(
                row
                for row in snapshot["consistency"]["items"]
                if row["kind"] == "homepage_history"
            ),
        )
        finding.update(
            expected_field="dyk1entry",
            article_history_present=True,
            suggested_parameters={
                "dyk1date": "2026-10-30",
                "dyk1entry": "Which [[Game]]?",
            },
            obsolete_parameters=["dykdate", "dykentry"],
        )
        snapshot["consistency"]["items"] = [finding]
        output = render_consistency_checks(snapshot, None, None, "zh-hans")
        label = Interface("zh-hans").text("consistency.remove_parameters")
        assert label + "<code>dykdate</code>、<code>dykentry</code>" in output
        assert " | dyk1date  = 2026-10-30" in output
        assert " | dyk1entry = Which [[Game]]?" in output

    @staticmethod
    def test_unknown_dyk_hook_is_a_blank_code_placeholder() -> None:
        """Keep unknown hooks distinct from proven missing metadata."""
        finding = deepcopy(
            next(
                row
                for row in consistency_snapshot()["consistency"]["items"]
                if row["kind"] == "homepage_history"
            ),
        )
        finding.update(
            expected_field="dyk1date",
            article_history_present=True,
            suggested_parameters={"dyk1date": "2026-10-30"},
        )
        detail = finding_details({"consistency": [finding]}, Interface("en"))[
            0
        ]
        assert detail["code"] == (" | dyk1date  = 2026-10-30\n | dyk1entry = ")
        assert detail["missing_fields"] == ["dyk1date"]
        assert finding["suggested_parameters"] == {
            "dyk1date": "2026-10-30",
        }
