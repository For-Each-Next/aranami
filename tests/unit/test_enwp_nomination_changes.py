"""Test nomination actions without duplicate quality promotions."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.article_changes import (
    NOMINATION_GRADES,
    ArticleChanges,
    NominationChanges,
    OldArticle,
    compare_nominations,
)
from data.reports import ReportResult, ReportStore, report_key
from shared.support.regions import managed_region
from tasks.enwiki import key_articles as service
from updater.services.zhwiki import enwp_key_articles as reports


def _rows(*articles: tuple[str, int, int, str, str | None]) -> pl.DataFrame:
    """Build typed report rows with identities and review flags.

    Returns:
        Typed observations, including an explicit empty schema.
    """
    return pl.DataFrame(
        articles,
        schema={
            "en_title": pl.String,
            "item_id": pl.Int64,
            "page_id": pl.Int64,
            "en_class": pl.String,
            "en_reviews": pl.String,
        },
        orient="row",
    )


def _prepare(
    rows: pl.DataFrame,
    body: str,
    cached: dict[str, int] | None = None,
) -> service.PreparedPublication:
    """Prepare one managed quality report against parsed live content.

    Returns:
        Quality changes with nomination-specific deltas and identities.
    """
    site = OfflineSite("zh", "wikipedia")
    parsed = service.parse_old_articles(body, site)
    candidates = rows.filter(~pl.col("en_class").is_in(("FA", "FL", "GA")))
    data = service.ReportData(
        rows,
        {
            "quality": {
                title: article
                for title, article in parsed.items()
                if article.en_class in {"FA", "FL", "GA", None}
            },
        },
        {},
        reviews=candidates,
    )
    original = (
        "{{PJ:VG/DBR/EN/item|en=Outside|wd=99|en_cls=B/GAN}}"
        f'<!-- aranami begin="body" -->{body}'
        '<!-- aranami end="body" -->'
    )
    with (
        patch.object(service, "load_membership", return_value=cached or {}),
        patch.object(service, "_stored_articles", return_value=None),
    ):
        return service.prepare_publication(
            data,
            service.ReportDefinition(
                "quality",
                "en_class",
                ("FA", "FL", "GA"),
                membership_region="body",
                requires_listing_dates=True,
            ),
            site,
            "Wikipedia:Quality",
            original,
        )


class TestNominationChanges(TestCase):
    """Compare nomination types using established article identities."""

    def test_each_new_kind_names_its_candidate(self) -> None:
        """Append typed candidate groups while retaining totals."""
        for kind, _grade in NOMINATION_GRADES:
            with self.subTest(kind=kind):
                candidate = OldArticle("New candidate", 2, 20, "B")
                changes = ArticleChanges(
                    current_total=3046,
                    added=(),
                    removed=(),
                    quality_changes=(),
                    quality_counts=(("FA", 301), ("FL", 101), ("GA", 2644)),
                    nominations=(NominationChanges(kind, (candidate,), ()),),
                )
                assert reports.build_edit_summary(
                    changes,
                    {},
                    quality=True,
                    new_nominees=35,
                ) == (
                    "3\u202f046 articles: FA 301, FL 101, GA 2\u202f644; "
                    f"35 new nominees. {kind} «[[:en:New candidate]]»."
                )

    def test_retained_renamed_candidate_is_quiet(self) -> None:
        """Match page IDs even when both title and Wikidata change."""
        for kind, _grade in NOMINATION_GRADES:
            with self.subTest(kind=kind):
                previous = {kind: {"Before": OldArticle("Before", 1, 10)}}
                assert (
                    compare_nominations(
                        previous,
                        _rows(("After", 2, 10, "B", kind)),
                        _rows(),
                    )
                    == ()
                )

    def test_successful_promotion_suppresses_departure(self) -> None:
        """Name each corresponding quality addition once."""
        for kind, grade in NOMINATION_GRADES:
            with self.subTest(kind=kind):
                prepared = _prepare(
                    _rows(("After", 2, 10, grade, None)),
                    "{{PJ:VG/DBR/EN/item|en=Before|wd=1|en_cls=B/"
                    f"{kind}}}}}",
                    {"Before": 10},
                )
                assert prepared.changes.current_total == 1
                assert len(prepared.changes.added) == 1
                assert not prepared.changes.removed
                assert prepared.changes.nominations == ()
                summary = reports.build_edit_summary(
                    prepared.changes,
                    {},
                    quality=True,
                    new_nominees=prepared.new_nominees,
                )
                assert f"{grade} «[[:en:After]]»" in summary
                assert kind not in summary
                assert "nominee" not in summary
                assert prepared.preview_members == {"Before": 10}

    @staticmethod
    def test_standalone_departure_names_previous_kind() -> None:
        """Keep withdrawals useful without changing quality counts."""
        prepared = _prepare(
            _rows(),
            "{{PJ:VG/DBR/EN/item|en=Withdrawn|wd=2|en_cls=C/GAN}}",
        )
        assert not prepared.changes.added
        assert not prepared.changes.removed
        assert reports.build_edit_summary(
            prepared.changes,
            {2: "Chinese companion"},
            quality=True,
        ) == (
            "0 articles: FA 0, FL 0, GA 0. Remove GAN "
            "«[[:en:Withdrawn]]» &#91;[[Chinese companion]]&#93;."
        )

    @staticmethod
    def test_known_page_conflict_is_not_a_promotion() -> None:
        """Keep a withdrawal after a same-title page recreation."""
        previous = {"GAN": {"Same": OldArticle("Same", 1, 10, "B")}}
        changes = compare_nominations(
            previous,
            _rows(),
            _rows(("Same", 1, 20, "GA", None)),
        )
        assert changes == (
            NominationChanges("GAN", (), (previous["GAN"]["Same"],)),
        )

    def test_missing_old_page_id_uses_item_then_title(self) -> None:
        """Use identity fallbacks when no known IDs conflict."""
        for item_id in (1, None):
            with self.subTest(item_id=item_id):
                previous = {
                    "GAN": {"Same": OldArticle("Same", item_id, None, "B")},
                }
                assert (
                    compare_nominations(
                        previous,
                        _rows(),
                        _rows(("Same", 1, 20, "GA", None)),
                    )
                    == ()
                )

    @staticmethod
    def test_different_quality_grade_does_not_hide_departure() -> None:
        """Suppress only a matching kind-to-grade promotion."""
        previous = {"FAC": {"Candidate": OldArticle("Candidate", 1, 10)}}
        changes = compare_nominations(
            previous,
            _rows(),
            _rows(("Candidate", 1, 10, "GA", None)),
        )
        assert changes[0].removed == (previous["FAC"]["Candidate"],)

    @staticmethod
    def test_kind_change_reports_addition_and_departure() -> None:
        """Separate review-process changes from retained flags."""
        article = OldArticle("Candidate", 1, 10, "B")
        changes = compare_nominations(
            {"GAN": {"Candidate": article}},
            _rows(("Candidate", 1, 10, "B", "FAC")),
            _rows(),
        )
        assert changes == (
            NominationChanges("FAC", (article,), ()),
            NominationChanges("GAN", (), (article,)),
        )

    @staticmethod
    def test_candidate_identity_stays_outside_quality_membership() -> None:
        """Save candidate IDs separately from quality counts."""
        prepared = _prepare(
            _rows(
                ("Quality", 1, 10, "GA", "FAC"),
                ("Retained", 2, 20, "B", "GAN"),
                ("New candidate", 3, 30, "C", "FLC"),
            ),
            "{{PJ:VG/DBR/EN/item|en=Quality|wd=1|en_cls=GA/FAC}}"
            "{{PJ:VG/DBR/EN/item|en=Retained|wd=2|en_cls=B/GAN}}",
            {"Quality": 10, "Retained": 20},
        )
        assert prepared.changes.current_total == 1
        assert not prepared.changes.added
        assert not prepared.changes.removed
        assert prepared.new_nominees == len(("Retained", "New candidate"))
        assert prepared.members == {
            "Quality": 10,
            "Retained": 20,
            "New candidate": 30,
        }
        assert prepared.preview_members == {"Quality": 10, "Retained": 20}
        assert prepared.changes.nominations == (
            NominationChanges(
                "FLC",
                (OldArticle("New candidate", 3, 30, "C"),),
                (),
            ),
        )

    @staticmethod
    def test_parse_only_supported_non_quality_candidates() -> None:
        """Ignore quality review flags and unsupported reviews."""
        parsed = service.parse_old_nominations(
            "{{PJ:VG/DBR/EN/item|en=Encoded &amp; title|wd=Q2"
            "|en_cls= c / gan / FLC }}"
            "{{PJ:VG/DBR/EN/item|en=Quality|wd=1|en_cls=GA/FAC}}"
            "{{PJ:VG/DBR/EN/item|en=Other|wd=3|en_cls=B/GAR/FAR/FFLC}}"
            "<!--{{PJ:VG/DBR/EN/item|en=Hidden|wd=4|en_cls=B/GAN}}-->"
            "<nowiki>{{PJ:VG/DBR/EN/item|en=Inert|wd=5|en_cls=B/GAN}}"
            "</nowiki>",
            OfflineSite("zh", "wikipedia"),
        )
        expected = OldArticle("Encoded & title", 2, en_class="C")
        assert parsed == {
            "FAC": {},
            "FLC": {"Encoded & title": expected},
            "GAN": {"Encoded & title": expected},
        }

    @staticmethod
    def test_long_nominee_groups_preserve_links_within_byte_budget() -> None:
        """Shorten entire mentions while preserving encoded titles."""
        prepared = _prepare(
            _rows(
                *(
                    (
                        f"Candidate {number} " + "漢" * 40,
                        number,
                        number,
                        "B",
                        "GAN",
                    )
                    for number in range(1, 5)
                ),
            ),
            "",
        )
        summary = reports.build_edit_summary(
            prepared.changes,
            {},
            quality=True,
            new_nominees=prepared.new_nominees,
        )
        assert len(summary.encode()) <= reports.MAX_EDIT_SUMMARY_BYTES
        assert summary.count("[[") == summary.count("]]")
        assert "GAN " in summary
        assert "more" in summary

    @staticmethod
    def test_matching_snapshot_supplies_candidate_page_ids() -> None:
        """Recover candidate identities from stored reviews."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            site = OfflineSite("zh", "wikipedia")
            title = "Wikipedia:Quality"
            body = "{{PJ:VG/DBR/EN/item|en=Before|wd=1|en_cls=B/GAN}}"
            with patch("pathlib.Path.cwd", return_value=runtime):
                key = report_key(
                    "english-comparisons",
                    "zhwiki",
                    title,
                    variant="quality",
                )
                ReportStore().write(
                    key,
                    ReportResult(
                        "enwp_key_articles",
                        {
                            "members": _rows(),
                            "reviews": _rows(("Before", 1, 10, "B", "GAN")),
                        },
                    ),
                    body,
                    run_id="1" * 32,
                    started_at=dt.datetime(2026, 11, 13, tzinfo=dt.UTC),
                    target={"site": "zhwiki", "title": title},
                )
                current = _rows(("After", 2, 10, "GA", None))
                prepared = service.prepare_publication(
                    service.ReportData(
                        current,
                        {"quality": {}},
                        {},
                        reviews=current.clear(),
                    ),
                    service.ReportDefinition(
                        "quality",
                        "en_class",
                        ("FA", "FL", "GA"),
                    ),
                    site,
                    title,
                    body,
                )
        assert len(prepared.changes.added) == 1
        assert not prepared.changes.removed
        assert prepared.changes.nominations == ()
        assert prepared.preview_members == {"Before": 10}

    @staticmethod
    def test_configured_candidate_range_overrides_other_report_body() -> None:
        """Use the configured range for retained candidate proof."""
        current = _rows(("Retained", 1, 10, "B", "GAN"))
        data = service.ReportData(
            current,
            {"quality": {}},
            {},
            reviews=current,
        )
        original = managed_region(
            "body",
            "{{PJ:VG/DBR/EN/item|en=Outside|wd=2|en_cls=B/FAC}}",
        ) + managed_region(
            "custom-members",
            "{{PJ:VG/DBR/EN/item|en=Retained|wd=1|en_cls=B/GAN}}",
        )
        with (
            patch.object(service, "load_membership", return_value={}),
            patch.object(service, "_stored_articles", return_value=None),
        ):
            prepared = service.prepare_publication(
                data,
                service.ReportDefinition(
                    "quality",
                    "en_class",
                    ("FA", "FL", "GA"),
                    membership_region="custom-members",
                    requires_listing_dates=True,
                ),
                OfflineSite("zh", "wikipedia"),
                "User:Example/Custom quality",
                original,
            )
        assert prepared.changes.nominations == ()
        assert prepared.changes.current_total == 0
        assert prepared.new_nominees == 1
        assert prepared.preview_members == {"Retained": 10}

    def test_unavailable_range_cannot_invent_candidate_changes(self) -> None:
        """Reject absent or ambiguous candidate proof."""
        candidate = "{{PJ:VG/DBR/EN/item|en=Previous|wd=1|en_cls=B/GAN}}"
        region = managed_region("custom-members", candidate)
        current = _rows(("Current", 2, 20, "B", "FAC"))
        data = service.ReportData(
            current,
            {"quality": {}},
            {},
            reviews=current,
        )
        for original in (candidate, region + region):
            with (
                self.subTest(original=original),
                patch.object(service, "load_membership", return_value={}),
                patch.object(service, "_stored_articles", return_value=None),
                patch.object(service, "compare_nominations") as compare,
            ):
                failure = None
                try:
                    service.prepare_publication(
                        data,
                        service.ReportDefinition(
                            "quality",
                            "en_class",
                            ("FA", "FL", "GA"),
                            membership_region="custom-members",
                            requires_listing_dates=True,
                        ),
                        OfflineSite("zh", "wikipedia"),
                        "User:Example/Custom quality",
                        original,
                    )
                except ValueError as error:
                    failure = error
                assert failure is not None
                compare.assert_not_called()
