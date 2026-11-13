"""Verify publication-driven daily newsletter acquisition."""

# Fixture records use literal issue dates and unittest parameterization.
# ruff: file-ignore[no-self-use, pytest-unittest-raises-assertion]

from __future__ import annotations

import copy
import datetime as dt
import unittest

from data.newsletter.publication import PUBLICATION_PAGE, valid_lifecycle
from data.newsletter.schema import valid_newsletter
from newsletter.test_newsletter_data import Reader
from tasks.zhwiki.newsletter.collection import collect_source_facts
from tasks.zhwiki.newsletter.publication import read_publication_plan


def _reader(records: str) -> Reader:
    """Supply a publication registry alongside synthetic sources.

    Returns:
        A reader retaining the module's exact revision identity.
    """
    reader = Reader()
    reader.existing[PUBLICATION_PAGE] = "return {" + records + "}"
    return reader


class NewsletterPublicationTests(unittest.TestCase):
    """Separate reporting boundaries from declared publication state."""

    def test_registry_states_do_not_guess_from_release_dates(self) -> None:
        """Interpret nil and negative years without comparing clocks."""
        reader = _reader(
            '{id="2026-10", date={2026,10,nil}},'
            '{id="2026-07", date={2028,7,2}},'
            '{id="2026-04", date={-2026,4,nil}},',
        )
        plan = read_publication_plan(reader, dt.date(2026, 11, 1))
        assert plan["states"]["2026-10"] == "draft"
        assert plan["states"]["2026-07"] == "published"
        assert plan["states"]["2026-04"] == "cancelled"
        assert plan["active"] == ["2026-10", "2027-01"]
        assert plan["default_issue"] == "2026-10"
        assert plan["source"] == reader.sources[PUBLICATION_PAGE]
        assert valid_lifecycle(plan)

    def test_reporting_period_gates_quarter_and_annual_acquisition(
        self,
    ) -> None:
        """Start January's feature before its final quarterly window."""
        reader = _reader('{id="2026-10", date={2026,10,1}},')
        plan = read_publication_plan(reader, dt.date(2026, 1, 2))
        assert plan["active"] == ["2027-01"]
        future = _reader('{id="2027-04", date={2027,4,nil}},')
        result = collect_source_facts(
            future,
            now=dt.datetime(2026, 12, 31, tzinfo=dt.UTC),
        )
        assert result["issues"] == {}
        assert result["lifecycle"]["active"] == []
        assert future.calls == []
        assert valid_newsletter(result)

    def test_overdue_issue_keeps_refreshing_after_april(self) -> None:
        """Keep an unpublished issue beyond calendar rollover."""
        reader = _reader('{id="2027-01", date={2027,1,nil}},')
        result = collect_source_facts(
            reader,
            now=dt.datetime(2027, 4, 2, tzinfo=dt.UTC),
        )
        assert result["issues"]["2027-01"]["updated_at"] == (
            "2027-04-02T00:00:00+00:00"
        )
        assert result["issues"]["2027-01"]["observation_end"] == "2027-01-01"
        assert result["lifecycle"]["default_issue"] == "2027-01"
        assert "2027-07" in result["lifecycle"]["active"]
        assert "2027-10" not in result["issues"]
        assert valid_newsletter(result)

    def test_publication_freezes_existing_facts_without_source_collection(
        self,
    ) -> None:
        """Stop source reads while retaining the latest draft."""
        reader = _reader(
            '{id="2026-10", date={2026,10,nil}},'
            '{id="2027-01", date={2027,1,nil}},',
        )
        prior = collect_source_facts(
            reader,
            now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        )
        latest = collect_source_facts(
            reader,
            now=dt.datetime(2026, 11, 2, tzinfo=dt.UTC),
            prior_facts=prior,
        )
        assert (
            latest["issues"]["2026-10"]["updated_at"]
            != (prior["issues"]["2026-10"]["updated_at"])
        )
        reader.existing[PUBLICATION_PAGE] = (
            'return {{id="2026-10", date={2026,11,2}},'
            '{id="2027-01", date={2027,1,8}}}'
        )
        reader.calls.clear()
        frozen = collect_source_facts(
            reader,
            now=dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
            prior_facts=latest,
        )
        assert frozen["lifecycle"]["active"] == []
        assert frozen["issues"] == latest["issues"]
        assert reader.calls == []
        assert valid_newsletter(frozen)

    def test_published_issue_without_prior_facts_is_not_bootstrapped(
        self,
    ) -> None:
        """Leave absent published drafts to their retained archives."""
        reader = _reader('{id="2027-01", date={2027,1,8}},')
        result = collect_source_facts(
            reader,
            now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        )
        assert result["issues"] == {}
        assert reader.calls == []

    def test_literal_registry_rejects_code_duplicates_and_invalid_dates(
        self,
    ) -> None:
        """Fail acquisition instead of guessing publication state."""
        for records in (
            (
                '{id="2026-10", date={2026,10,nil}},'
                '{id="2026-10", date={2026,10,1}},'
            ),
            '{id="2026-10", date={2026,10}},',
            '{id="2026-10", date={2026,2,30}},',
            '{id="2026-11", date={2026,11,nil}},',
            '{id="2026-10", date={2026,10,nil}, feature=os.execute("x")},',
            '{id="2026-10", date={2026,10,nil}, unknown="field"},',
        ):
            with self.subTest(records=records), self.assertRaises(ValueError):
                collect_source_facts(
                    _reader(records),
                    now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
                )

    def test_comments_and_quoted_editorial_fields_remain_literal(self) -> None:
        """Ignore declarations inside comments and ordinary prose."""
        reader = _reader(
            '-- {id="2026-07", date={2026,7,1}},\n'
            '{id="2026-10", date={2026,10,nil}, '
            r'feature="A \"quoted\" feature", interview="feature"},',
        )
        plan = read_publication_plan(reader, dt.date(2026, 11, 1))
        assert "2026-07" not in plan["states"]
        assert plan["states"]["2026-10"] == "draft"

    def test_saved_lifecycle_validation_rejects_inconsistent_selections(
        self,
    ) -> None:
        """Validate optional metadata while retaining legacy facts."""
        plan = read_publication_plan(
            _reader('{id="2026-10", date={2026,10,nil}},'),
            dt.date(2026, 11, 1),
        )
        for key, value in (
            ("active", ["2027-01", "2026-10"]),
            ("default_issue", "2027-01"),
            ("states", {"2026-10": "published"}),
            ("source", {"label": PUBLICATION_PAGE, "url": "bad"}),
        ):
            with self.subTest(key=key):
                invalid = copy.deepcopy(plan)
                invalid[key] = value
                assert not valid_lifecycle(invalid)
        assert valid_newsletter({"issues": {}})


if __name__ == "__main__":
    unittest.main()
