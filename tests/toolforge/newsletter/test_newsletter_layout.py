"""Verify newsletter layouts preserve their original wiki design.

The fixture reproduces Chinese Wikipedia revision 94691317 under
CC BY-SA 4.0. Source and contributor history are available at
https://zh.wikipedia.org/w/index.php?oldid=94691317.
"""

# Literal source counts and authored Chinese punctuation are fixtures.
# ruff: file-ignore[magic-value-comparison, ambiguous-unicode-character-string]

from __future__ import annotations

import copy
import datetime as dt
import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from pywikibot.exceptions import NoPageError

from data.newsletter.layout import valid_main_layout
from shared.newsletter import ROOT, Issue
from tasks.zhwiki.newsletter.collection import collect_source_facts
from tasks.zhwiki.newsletter.layout import prepare_main_layout

_FIXTURE = (
    Path(__file__).parents[2] / "fixtures/newsletter-main-2026-10.wikitext"
)
_SOURCE_ISSUE = Issue.parse("2026-10")


def _original() -> str:
    """Return the verified October 2026 main-page source."""
    return _FIXTURE.read_text(encoding="utf-8")


def _restore(layout: dict, issue: Issue = _SOURCE_ISSUE) -> str:
    """Fill source slots with known original values for comparison.

    Returns:
        The original source with its issue navigation intact.
    """
    values = {
        "issue": issue.value,
        "previous_issue": issue.previous,
        "next_issue": issue.next,
        "publication_date": f"{issue.year}年{issue.month}月",
        "quarter": issue.quarter_label,
        "number": "廿八" if issue == _SOURCE_ISSUE else "廿七",
        "overview_asof": f"截至{issue.quarter_label}底",
    }
    for key, count in layout["baseline_counts"].items():
        values[f"count:{key}"] = f"{count:,}"
        difference = {"total": 216, "ga": 1, "dyk": 21}.get(key)
        values[f"delta:{key}"] = (
            f"{layout['delta_open']}（+{difference}）{layout['delta_close']}"
            if difference is not None
            else ""
        )
    return re.sub(
        r"@@aranami:([a-z_:]+)@@",
        lambda match: values[match[1]],
        layout["text"],
    )


class SourceReader:
    """Supply actual layouts and synthetic numeric sources."""

    @staticmethod
    def resolve(title: str) -> str:
        """Return the unchanged canonical fixture title."""
        return title

    @staticmethod
    def resolve_many(_titles: list[str]) -> None:
        """Accept a batched fixture title-resolution request."""

    @staticmethod
    def resolve_page_id(_title: str) -> int | None:
        """Leave fixture page identities unknown.

        Returns:
            None because no page-ID fixture was supplied.
        """
        return None

    @staticmethod
    def history(title: str, _start: dt.date, _end: dt.date) -> list[dict]:
        """Report missing monthly publication evidence.

        Raises:
            NoPageError: No historical publication fixture was supplied.
        """
        page = Mock()
        page.title.return_value = title
        raise NoPageError(page)

    def __init__(self, originals: dict[str, str]) -> None:
        """Retain source pages and their revision provenance."""
        self.originals = {
            "Module:VGNL/data": ('return {{id="2026-10",date={2026,10,nil}}}'),
            **originals,
        }
        self.sources = {}
        self.reads = []

    def __call__(self, title: str) -> str:
        """Read a source with normal missing-page semantics.

        Returns:
            The retained source text.

        Raises:
            NoPageError: A source page is absent.
        """
        self.reads.append(title)
        if title not in self.originals:
            page = Mock()
            page.title.return_value = title
            raise NoPageError(page)
        self.sources[title] = {
            "label": title,
            "url": "https://zh.wikipedia.org/w/index.php?oldid=94691317",
        }
        return self.originals[title]

    @staticmethod
    def at(_title: str, _before: dt.datetime) -> str:
        """Return a literal count without daily totals."""
        return "<section begin=count/>7<section end=count/>"


class NewsletterSourceLayoutTests(unittest.TestCase):
    """Keep source markup and metadata boundaries intact."""

    @staticmethod
    def test_original_markup_round_trips() -> None:
        """Preserve the exact source outside explicitly managed data."""
        original = _original()
        layout = prepare_main_layout(original, _SOURCE_ISSUE)
        assert layout is not None
        assert _restore(layout) == original
        assert layout["number_style"] == "chinese"
        assert layout["baseline_counts"] == {
            "total": 13016,
            "fa": 25,
            "fl": 11,
            "ga": 216,
            "dyk": 1470,
            "bplus": 7,
            "topics": 3,
        }
        assert layout["delta_open"] == '<small style="color: grey;">'
        assert "skin-invert-image" in layout["text"]
        assert "|典範條目]]" in layout["text"]
        assert "#典范条目" in layout["text"]

    @staticmethod
    def test_missing_authored_links_are_restored_in_contents() -> None:
        """Restore both links while retaining the source design."""
        original = _original()
        reduced = re.sub(
            r"^\* \[\[[^\r\n]*/(?:專題|訪談)\|[^\r\n]*\]\]\n?",
            "",
            original,
            flags=re.MULTILINE,
        )
        layout = prepare_main_layout(reduced, _SOURCE_ISSUE)
        assert layout is not None
        assert _restore(layout) == original

    @staticmethod
    def test_existing_authored_labels_are_preserved() -> None:
        """Keep editor-provided section names exactly as written."""
        original = (
            _original()
            .replace(
                "專題報導：<待定>",
                "專題報導：電子遊戲保存",
            )
            .replace("訪談：<待定>", "訪談：專題成員")
        )
        layout = prepare_main_layout(original, _SOURCE_ISSUE)
        assert layout is not None
        assert _restore(layout) == original

    @staticmethod
    def test_unknown_or_incomplete_layout_is_not_redesigned() -> None:
        """Reject plain replacements and missing source metadata."""
        for text in (
            "= 電子遊戲專題簡訊 =\n* 1篇條目",
            _original().replace("第廿八號", ""),
            _original().replace("|典範條目", "|Unknown"),
            _original() + "@@aranami:unknown@@",
            _original().replace(">本期內容</div>", ">未知內容</div>"),
        ):
            assert prepare_main_layout(text, _SOURCE_ISSUE) is None

    @staticmethod
    def test_prepared_layout_validation_rejects_corruption() -> None:
        """Bound slots, source identities, count values, and markup."""
        original = prepare_main_layout(_original(), _SOURCE_ISSUE)
        assert original is not None
        for key, value in (
            ("source_issue", "2026-08"),
            ("number_style", {}),
            ("delta_open", "<script>"),
            ("delta_close", "</small><script>"),
            ("text", original["text"] + "@@aranami:unknown!@@"),
            ("baseline_counts", {"total": True}),
        ):
            malformed = copy.deepcopy(original)
            malformed[key] = value
            assert not valid_main_layout(malformed), key
        assert valid_main_layout(None)

    @staticmethod
    def test_saved_two_link_layout_remains_readable_until_refresh() -> None:
        """Keep older dashboard snapshots valid while refresh is due."""
        layout = prepare_main_layout(_original(), _SOURCE_ISSUE)
        assert layout is not None
        layout["text"] = re.sub(
            r"^\* \[\[[^\r\n]*/(?:專題|訪談)\|[^\r\n]*\]\]\n?",
            "",
            layout["text"],
            flags=re.MULTILINE,
        )
        assert "/專題|" not in layout["text"]
        assert "/訪談|" not in layout["text"]
        assert valid_main_layout(layout)

    @staticmethod
    def test_collector_never_uses_prior_text_for_baseline() -> None:
        """Keep missing metadata unknown despite prior text."""
        reader = SourceReader({f"{ROOT}/2026-10": _original()})
        facts = collect_source_facts(
            reader,
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
            current_counts={"total": 13062},
            overview_at=lambda boundary: (
                {"fa": 7},
                f"{boundary}T00:00:00+00:00",
            ),
        )["issues"]["2027-01"]
        assert facts["main_layout"]["source_issue"] == "2026-10"
        assert facts["previous_overview"]["total"] is None
        assert facts["previous_overview"]["fa"] == 7
        assert facts["previous_overview"]["dyk"] is None
        assert any(
            source["label"] == f"{ROOT}/2026-10" for source in facts["sources"]
        )
        assert "/專題|專題報導：<待定>]]" in facts["main_layout"]["text"]
        assert "/訪談|訪談：<待定>]]" in facts["main_layout"]["text"]
        assert not any(
            title.endswith(("/專題", "/訪談")) for title in reader.reads
        )

    @staticmethod
    def test_older_layout_never_supplies_an_immediate_prior_count() -> None:
        """Reuse a prior design without inventing missing baselines."""
        layout = prepare_main_layout(_original(), _SOURCE_ISSUE)
        assert layout is not None
        older = _restore(layout, Issue.parse("2026-07"))
        reader = SourceReader({f"{ROOT}/2026-07": older})
        facts = collect_source_facts(
            reader,
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
            current_counts={"total": 13062},
        )["issues"]["2027-01"]
        assert facts["main_layout"]["source_issue"] == "2026-07"
        assert facts["previous_overview"]["total"] is None


if __name__ == "__main__":
    unittest.main()
