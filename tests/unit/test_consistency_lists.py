"""Verify audited lists, stable identities, and independent checks."""

# Counts and source revisions are assertions about audited fixtures.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from tasks.zhwiki import consistency_lists as lists

_FIXTURES = Path(__file__).parents[1] / "fixtures/consistency"
_NOW = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
_FILES = (
    "fa-92059899",
    "acc-91872763",
    "ga-94409217",
    "bpca-91951615",
)


def _members() -> pl.DataFrame:
    """Provide observed article grades and actual revision identities.

    Returns:
        One renamed, one missing, and one downgraded current article.
    """
    return pl.DataFrame({
        "page_id": [1, 2, 3],
        "page_namespace": [0, 0, 0],
        "full_title": ["New name", "Missing", "Former"],
        "pa_class": ["GA", "GA", "C"],
        "pa_importance": ["高", "中", "低"],
        "talk_revision": [101, 102, 103],
    })


class TestProjectListContracts(TestCase):
    """Use exact audited pages as executable source contracts."""

    @staticmethod
    def test_audited_fixtures_keep_only_current_sections() -> None:
        """Exclude former content and nomination transclusions."""
        site = OfflineSite("zh", "wikipedia")
        for contract, name, count in zip(
            lists.LIST_CONTRACTS,
            _FILES,
            (36, 0, 216, 7),
            strict=True,
        ):
            text = (_FIXTURES / f"{name}.wikitext").read_text()
            rows = lists.parse_current_list(text, site, contract)
            assert rows.height == count
            assert rows["title"].n_unique() == count

    def test_absent_or_malformed_source_never_establishes_empty(self) -> None:
        """Require headings, grade icons, and explicit empty markers."""
        contract = lists.LIST_CONTRACTS[1]
        text = (_FIXTURES / "acc-91872763.wikitext").read_text()
        site = OfflineSite("zh", "wikipedia")
        for invalid in (
            "",
            text.replace("\uff08無\uff09", ""),
            text.replace("当前甲级条目", "未知条目"),
            text.replace("{{class/icon|a}}", "{{class/icon|ga}}"),
        ):
            with (
                self.subTest(invalid=invalid[:70]),
                self.assertRaises(ValueError),
            ):
                lists.parse_current_list(invalid, site, contract)

    @staticmethod
    def test_current_ids_detect_missing_and_extra_without_rename_noise() -> (
        None
    ):
        """Compare stable identities and retain observed revisions."""
        read = Mock()
        read.site = OfflineSite("zh", "wikipedia")
        read.sources = {
            lists.LIST_CONTRACTS[2].title: {
                "url": "https://zh.wikipedia.org/w/index.php?oldid=94409217",
            },
        }
        read.side_effect = lambda _title: (
            "== 当前优良条目 ==\n"
            "# {{icon|GA}} [[:Old name]]"
            "<small>\uff082020-01-01\uff09</small>\n"
            "# {{icon|GA}} [[:Former]]<small>\uff082020-01-01\uff09</small>\n"
        )
        read.resolve_page_id.side_effect = {"Old name": 1, "Former": 3}.get
        read.resolve.side_effect = {
            "Old name": "New name",
            "Former": "Former",
        }.get
        with (
            patch.object(lists, "LIST_CONTRACTS", lists.LIST_CONTRACTS[2:3]),
            patch.object(
                lists,
                "collect_quarter_check",
                return_value=([], {}),
            ),
        ):
            items, checks = lists.collect_list_checks(
                read,
                _members(),
                now=_NOW,
            )
        assert checks[0]["status"] == "complete"
        assert {(row["title"], row["kind"]) for row in items} == {
            ("Missing", "project_list_missing"),
            ("Former", "project_list_extra"),
        }
        assert {row["checked_revision"] for row in items} == {94409217}
        assert {row["source_revision"] for row in items} == {102, 103}
        assert all(row["source_timestamp"] is None for row in items)
        assert not read.save.called

    @staticmethod
    def test_sources_fail_independently_and_distinguish_bad_markup() -> None:
        """A failed list does not skip later contract validation."""
        read = Mock()
        read.site = OfflineSite("zh", "wikipedia")
        read.side_effect = [OSError("offline"), "unsupported"]
        with (
            patch.object(lists, "LIST_CONTRACTS", lists.LIST_CONTRACTS[:2]),
            patch.object(
                lists,
                "collect_quarter_check",
                return_value=([], {}),
            ),
        ):
            items, checks = lists.collect_list_checks(
                read,
                _members(),
                now=_NOW,
            )
        assert items == []
        assert [row.get("status") for row in checks[:2]] == [
            "unavailable",
            "unsupported",
        ]
