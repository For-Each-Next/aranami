"""Verify evidence-backed maintenance checks without wiki edits."""

# Focused fixtures exercise source boundaries and validation failures.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.consistency import Evidence, valid_result
from data.website import valid_report_data
from tasks.consistency_evidence import issue
from tasks.website import collect
from tasks.zhwiki import consistency

_NOW = dt.datetime(2026, 11, 2, 13, tzinfo=dt.UTC)
_STAMP = _NOW.isoformat()
_TALK_REVISION = 101
_SITE = OfflineSite("zh", "wikipedia")
_EVIDENCE = Evidence(
    "Talk:Game",
    "https://zh.wikipedia.org/w/index.php?oldid=123",
    123,
    _STAMP,
)


def _members(grade: str = "FA") -> pl.DataFrame:
    """Provide one project article and its captured revisions.

    Returns:
        Complete scalar evidence used by these offline checks.
    """
    return pl.DataFrame({
        "page_id": [10],
        "page_namespace": [0],
        "full_title": ["Game"],
        "pa_class": [grade],
        "pa_importance": ["高"],
        "page_revision": [100],
        "talk_revision": [_TALK_REVISION],
    })


def _read() -> Mock:
    """Create a source client with no network-backed operations.

    Returns:
        Mock reader identifying the audited wiki and project namespace.
    """
    read = Mock()
    read.site = _SITE
    read.resolve.side_effect = lambda title: title
    read.sources = {}
    return read


def _missing(text: str, aliases: tuple | list, **kwargs: object) -> bool:
    """Check metadata using the offline wiki's actual title rules.

    Returns:
        Whether the documented metadata is absent.
    """
    return consistency.missing_metadata(text, aliases, site=_SITE, **kwargs)


class TestConsistencyMetadata(TestCase):
    """Separate missing metadata from unavailable source evidence."""

    @staticmethod
    def test_nested_literal_tags_never_detach_a_later_removal() -> None:
        """Exclude nested examples and retain active banners."""
        aliases = ("Template:GAN",)
        for text in (
            "<code><nowiki><s></s></nowiki>{{GAN}}</code>",
            "<pre><code>{{GAN}}</code></pre>",
            "<code><code>{{GAN}}</code>{{GAN}}</code>",
        ):
            assert _missing(text, aliases)
            assert not _missing(text + "{{GAN}}", aliases)

    @staticmethod
    def test_aliases_are_explicit_and_literal_examples_are_not_banners() -> (
        None
    ):
        """Accept aliases and ignore quoted template examples."""
        aliases = ("Template:GA nominee", "Template:GAN")
        assert not _missing("{{GAN}}", aliases)
        assert not _missing("{{GA_nominee}}", aliases)
        assert _missing("{{Other nomination}}", aliases)
        assert _missing(
            "<!-- {{GAN}} --> <nowiki>{{GAN}}</nowiki>",
            aliases,
        )
        assert not _missing("{{模版:GA_nominee}}", aliases)
        assert _missing("{{Talk:GA nominee}}", aliases)

    @staticmethod
    def test_numbered_history_fields_and_legacy_dates_satisfy_checks() -> None:
        """Recognize date records without requiring optional links."""
        for field, parameter in (
            ("maindateGA", "maindateGA"),
            ("maindateFA", "maindateFA"),
            ("dykdate", "dyk2date"),
            ("itndate", "itn3date"),
            ("otddate", "otd1date"),
        ):
            assert not _missing(
                "{{Article history|" + parameter + "=2026-11-01}}",
                ["Article history"],
                field=field,
            )
            assert _missing(
                "{{Article history|" + parameter + "= }}",
                ["Article history"],
                field=field,
            )
        assert not _missing(
            "{{DYKtalk|date=2026-11-01}}",
            ["Article history"],
            field="dykdate",
            equivalents=["DYKtalk"],
        )
        assert not _missing(
            "{{ITN talk|date2=2026-11-01}}",
            ["Article history"],
            field="itndate",
            equivalents=["ITN talk"],
        )

    @staticmethod
    def test_each_proven_appearance_requires_matching_recorded_dates() -> None:
        """Do not let old dates hide a new DYK or news appearance."""
        day = dt.date(2026, 11, 1)
        for field in ("itndate", "dykdate", "otddate"):
            assert _missing(
                "{{Article history|" + field + "=2020-01-01}}",
                ["Article history"],
                field=field,
                event_date=day,
            )
            assert not _missing(
                "{{Article history|" + field[:-4] + "2date=2026-11-01}}",
                ["Article history"],
                field=field,
                event_date=day,
            )
        assert _missing(
            "{{Article history|maindateGA=2026-11-02}}",
            ["Article history"],
            field="maindateGA",
            event_date=day,
        )
        assert not _missing(
            "{{Article history|maindateGA=2020-01-01, 2026-11-01}}",
            ["Article history"],
            field="maindateGA",
            event_date=day,
        )
        assert not _missing(
            "{{ITNtalk|1=2026年|2=11月1日}}",
            ["Article history"],
            equivalents=["ITNtalk"],
            field="itndate",
            event_date=day,
        )
        assert _missing(
            "{{ITNtalk|1=2026年}}",
            ["Article history"],
            equivalents=["ITNtalk"],
            field="itndate",
            event_date=day,
        )

    def test_ga_report_skips_never_displayed_and_future_slots(self) -> None:
        """Require positive counters and actual elapsed display days."""
        text = (
            "上次展示時間 次數 簡介頁面\n"
            "| 1 || [[Never]] || 電子遊戲 || 沒上過首頁 || 0 || [[Intro]]\n"
            "| 2 || [[Game]] || 電子遊戲 || "
            "[[Wikipedia:优良条目/2026年11月1日|2026年11月1日]] "
            "|| 1 || [[Intro]]\n"
            "| 3 || [[Future]] || 電子遊戲 || "
            "[[Wikipedia:优良条目/2026年11月3日|2026年11月3日]] "
            "|| 1 || [[Intro]]\n"
        )
        assert consistency.good_report_appearances(
            text,
            _NOW.date(),
        ).to_dicts() == [
            {"title": "Game", "date": "2026-11-01"},
        ]
        with self.assertRaises(ValueError):
            consistency.good_report_appearances("changed format", _NOW.date())
        with self.assertRaises(ValueError):
            consistency.good_report_appearances(
                text + "| 4 || [[Broken]] || Missing cells\n",
                _NOW.date(),
            )

    @staticmethod
    def test_exact_checked_revision_is_retained_for_a_missing_banner() -> None:
        """Retain source and checked-page revisions independently."""
        read = _read()
        expectation = consistency._Expectation(
            "Game",
            "gan",
            "nomination_banner",
            "GA nominee",
            _EVIDENCE,
        )
        observed = consistency._Observation(_EVIDENCE, "A talk page.")
        with (
            patch.object(
                consistency,
                "_observations",
                return_value={"Talk:Game": observed},
            ),
            patch.object(
                consistency,
                "template_aliases",
                return_value=("GA nominee",),
            ),
        ):
            findings, check = consistency._check(
                consistency._Checks(
                    read,
                    consistency._members(_members()),
                    _NOW,
                ),
                [expectation],
                "gan",
            )
        assert check == {
            "check_id": "gan",
            "status": "complete",
            "checked_count": 1,
            "issue_count": 1,
        }
        assert findings[0]["checked_revision"] == _EVIDENCE.revision
        assert findings[0]["source_revision"] == _EVIDENCE.revision
        assert findings[0]["checked_timestamp"] == _STAMP
        assert findings[0]["expected_template"] == "GA nominee"

    @staticmethod
    def test_missing_page_is_unavailable_not_a_missing_banner() -> None:
        """Do not infer an issue without successfully read page text."""
        expectation = consistency._Expectation(
            "Game",
            "gan",
            "nomination_banner",
            "GA nominee",
            _EVIDENCE,
        )
        with (
            patch.object(consistency, "_observations", return_value={}),
            patch.object(
                consistency,
                "template_aliases",
                return_value=("GA nominee",),
            ),
        ):
            findings, check = consistency._check(
                consistency._Checks(
                    _read(),
                    consistency._members(_members()),
                    _NOW,
                ),
                [expectation],
                "gan",
            )
        assert findings == []
        assert check["status"] == "unavailable"
        assert check["checked_count"] == 0

    @staticmethod
    def test_homepage_findings_keep_each_proven_date() -> None:
        """Retain repeated FA/GA displays and their exact evidence."""
        expectations = [
            consistency._Expectation(
                "Game",
                "homepage_good",
                "homepage_history",
                "Article history",
                _EVIDENCE,
                field,
                day,
            )
            for field, day in (
                ("maindateGA", "2019-02-04"),
                ("maindateGA", "2026-10-31"),
                ("maindateFA", "2026-11-01"),
            )
        ]
        with (
            patch.object(
                consistency,
                "_observations",
                return_value={
                    "Talk:Game": consistency._Observation(_EVIDENCE, ""),
                },
            ),
            patch.object(
                consistency,
                "template_aliases",
                return_value=("Article history",),
            ),
        ):
            findings, outcome = consistency._check(
                consistency._Checks(
                    _read(),
                    consistency._members(_members()),
                    _NOW,
                ),
                expectations,
                "homepage_good",
            )
        assert outcome["issue_count"] == len(expectations)
        assert [
            (finding["expected_field"], finding["event_date"])
            for finding in findings
        ] == [(item.field, item.event_date) for item in expectations]
        assert all(
            finding["source_revision"] == _EVIDENCE.revision
            and finding["checked_revision"] == _EVIDENCE.revision
            for finding in findings
        )

    @staticmethod
    def test_daily_display_retains_the_revision_used_for_its_date() -> None:
        """Link the exact daily source selected by the shared parser."""
        read = _read()

        def at(title: str, _before: dt.datetime) -> str:
            """Supply an audited daily display and its revision link.

            Returns:
                One direct featured-article introduction transclusion.
            """
            read.sources[title] = {"label": title, "url": _EVIDENCE.url}
            return "{{Wikipedia:典范条目/Game}}"

        read.at.side_effect = at
        [expectation] = consistency._daily_expectations(
            read,
            consistency._members(_members()),
            "featured",
            dt.date(2026, 11, 1),
            dt.date(2026, 11, 2),
        )
        assert expectation.event_date == "2026-11-01"
        assert expectation.source.revision == _EVIDENCE.revision
        assert expectation.source.title == "Wikipedia:典范条目/2026年11月1日"

    @staticmethod
    def test_dyk_appearance_uses_actual_revision_not_window_start() -> None:
        """Keep proven starts and exclude carried-in appearances."""
        read = _read()
        prior = {
            "text": "{{Dyk/auto|0='''[[Other]]'''}}",
            "timestamp": dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
            "revision": _TALK_REVISION,
        }
        appearance = {
            "text": "{{Dyk/auto|0='''[[Game]]'''}}",
            "timestamp": dt.datetime(2026, 11, 1, 12, tzinfo=dt.UTC),
            "revision": _EVIDENCE.revision,
        }
        read.history.return_value = [prior, appearance]
        [expectation] = consistency._timeline_expectations(
            read,
            consistency._members(_members()),
            "dyk",
            dt.date(2026, 11, 1),
            dt.date(2026, 11, 2),
        )
        assert expectation.event_date == "2026-11-01"
        assert expectation.source.revision == _EVIDENCE.revision
        read.history.return_value = [{**prior, "text": appearance["text"]}]
        assert (
            consistency._timeline_expectations(
                read,
                consistency._members(_members()),
                "dyk",
                dt.date(2026, 11, 1),
                dt.date(2026, 11, 2),
            )
            == []
        )

    def test_collector_keeps_independent_source_failures_explicit(
        self,
    ) -> None:
        """Keep valid award findings when nomination sources fail."""
        read = _read()
        observed = consistency._Observation(
            Evidence("Game", _EVIDENCE.url, 123, _STAMP),
            "No award template.",
        )
        with (
            patch.object(
                consistency,
                "_nomination_expectations",
                side_effect=OSError("source unavailable"),
            ),
            patch.object(consistency, "_good_expectations", return_value=[]),
            patch.object(consistency, "_daily_expectations", return_value=[]),
            patch.object(
                consistency,
                "_timeline_expectations",
                return_value=[],
            ),
            patch.object(
                consistency,
                "collect_list_checks",
                return_value=([], []),
            ),
            patch.object(
                consistency,
                "_observations",
                return_value={"Game": observed},
            ),
            patch.object(
                consistency,
                "template_aliases",
                side_effect=lambda _site, name: (name,),
            ),
            self.assertLogs(consistency._LOGGER, level="WARNING"),
            patch("pywikibot.Page.save") as save,
        ):
            result = consistency.collect_consistency(
                read,
                _members("典範級"),
                now=_NOW,
            )
        assert valid_result(result)
        assert len(result["items"]) == 1
        finding = result["items"][0]
        assert finding["kind"] == "award_template"
        assert finding["assessment_class"] == "典範級"
        assert finding["checked_title"] == "Game"
        assert finding["source_revision"] == _TALK_REVISION
        assert finding["source_timestamp"] is None
        unavailable = {
            check["check_id"]
            for check in result["checks"]
            if check["status"] == "unavailable"
        }
        assert unavailable == {"fac", "flc", "gan", "pr", "dykc"}
        save.assert_not_called()


class TestConsistencyStorage(TestCase):
    """Keep the saved website report bounded and read-only."""

    @staticmethod
    def test_findings_use_wiki_namespace_and_url_rules() -> None:
        """Normalize shortcuts and derive the project talk page."""
        finding = issue(
            site=_SITE,
            kind="quarterly_statistics_missing",
            check_id="quarterly-challenge",
            title="PJ:电子游戏/quarter_one",
            page_id=10,
            source=_EVIDENCE,
            checked=_EVIDENCE,
            observed_at=_STAMP,
        )
        assert finding["title"] == "WikiProject:电子游戏/quarter one"
        assert finding["url"].endswith(
            "/WikiProject%3A%E7%94%B5%E5%AD%90%E6%B8%B8%E6%88%8F%2Fquarter_one",
        )
        assert "/WikiProject_talk%3A" in finding["talk_url"]

    @staticmethod
    def test_validation_rejects_unsafe_links_and_unknown_numbers() -> None:
        """Validate new data without initializing any source client."""
        finding = issue(
            site=_SITE,
            kind="award_template",
            check_id="award_fa",
            title="Game",
            page_id=10,
            source=_EVIDENCE,
            checked=_EVIDENCE,
            observed_at=_STAMP,
            expected_template="Featured article",
        )
        result = {
            "schema": 1,
            "wiki": "zhwiki",
            "project": "电子游戏",
            "checked_at": _STAMP,
            "items": [finding],
            "checks": [
                {
                    "check_id": "award_fa",
                    "status": "complete",
                    "checked_count": 1,
                    "issue_count": 1,
                },
            ],
            "sources": [{"label": "Talk:Game", "url": _EVIDENCE.url}],
        }
        assert valid_result(result)
        assert valid_report_data("consistency", result, 100)
        assert collect._with_extracts("consistency", result, Mock()) is result
        for field, value in (
            ("checked_url", "javascript:alert(1)"),
            ("checked_timestamp", "2026-11-02T13:00:00"),
            ("checked_revision", 0),
            ("suggested_values", {"total": "unknown"}),
        ):
            invalid = deepcopy(result)
            invalid["items"][0][field] = value
            assert not valid_result(invalid)

    def test_unaudited_wiki_is_rejected_before_reading_sources(self) -> None:
        """Require exact source contracts before collection."""
        read = _read()
        read.site = OfflineSite("en", "wikipedia")
        with self.assertRaises(ValueError):
            consistency.collect_consistency(read, _members(), now=_NOW)
        read.assert_not_called()
