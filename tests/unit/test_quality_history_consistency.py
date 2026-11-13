"""Verify Article history actions for current quality grades."""

# Check explicit source identities without wiki requests.
# ruff: file-ignore[private-member-access, magic-value-comparison]

import datetime as dt
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.consistency import Evidence
from tasks.zhwiki import consistency

_NOW = dt.datetime(2026, 11, 10, 15, tzinfo=dt.UTC)
_SITE = OfflineSite("zh", "wikipedia")
_BPLUS = "{{Article history|action1=BPAR|action1result=approved}}"


def _context(
    text: str | None,
    grade: str = "Bplus",
    *,
    title: str = "Game",
) -> consistency._Checks:
    """Prepare current assessments and a fresh exact talk observation.

    Returns:
        An isolated check context with verified template aliases.
    """
    read = Mock()
    read.site = _SITE
    read.sources = {}
    members = pl.DataFrame({
        "page_id": [10],
        "page_namespace": [0],
        "full_title": [title],
        "pa_class": [grade],
        "pa_importance": ["高"],
        "page_is_redirect": [0],
        "talk_page_id": [20],
        "talk_revision": [101],
    })
    talk = f"Talk:{title}"
    observations = (
        {
            talk: consistency._Observation(
                Evidence(
                    talk,
                    "https://zh.wikipedia.org/w/index.php?oldid=201",
                    201,
                    _NOW.isoformat(),
                ),
                text,
                page_id=20,
            ),
        }
        if text is not None
        else {}
    )
    return consistency._Checks(
        read,
        consistency._members(members),
        _NOW,
        observations=observations,
        aliases={
            "Article history": (
                "Template:Article history",
                "Template:ArticleHistory",
            ),
        },
    )


def _check(context: consistency._Checks) -> tuple[list, dict]:
    """Run only the quality-history check with network reads disabled.

    Returns:
        Findings and the check's independent coverage outcome.
    """
    with patch.object(consistency, "_observations", return_value={}):
        return consistency._quality_history_check(context)


class QualityHistoryConsistencyTests(TestCase):
    """Distinguish missing actions from unknown talk-page evidence."""

    def test_verified_successful_actions_accept_all_current_quality_aliases(
        self,
    ) -> None:
        """Accept matching WAR/BPAR actions with unknown dates."""
        for grade, action in (
            ("A", "WAR"),
            ("AL", "WAR"),
            ("Bplus", "BPAR"),
            ("甲", "WAR"),
            ("甲级列表", "WAR"),
            ("乙上", "BPAR"),
        ):
            with self.subTest(grade=grade):
                text = (
                    "{{ArticleHistory|action3="
                    + action
                    + "|action3result=approved}}"
                )
                findings, outcome = _check(_context(text, grade))
                assert findings == []
                assert outcome["status"] == "complete"
                assert outcome["checked_count"] == 1

    def test_missing_or_inapplicable_actions_are_confirmed_findings(
        self,
    ) -> None:
        """Reject other grades, failed actions and quoted examples."""
        for text in (
            "No history is recorded.",
            "{{Article history}}",
            "{{Article history|action1=GAN|action1result=listed}}",
            "{{Article history|action1=BPAR|action1result=failed}}",
            _BPLUS.replace("}}", "|action1ignore=yes}}"),
            "<!-- " + _BPLUS + " -->",
            "<nowiki>" + _BPLUS + "</nowiki>",
        ):
            with self.subTest(text=text):
                findings, outcome = _check(_context(text))
                assert outcome["status"] == "complete"
                assert len(findings) == 1
                assert findings[0]["check_id"] == "quality_history"
                assert findings[0]["kind"] == "quality_history"
                assert findings[0]["expected_template"] == "Article history"
                assert findings[0]["expected_field"] == "BPAR"

    @staticmethod
    def test_current_a_requires_war_instead_of_bpar() -> None:
        """Keep successful actions attached to the matching grade."""
        findings, outcome = _check(_context(_BPLUS, "A"))
        assert outcome["status"] == "complete"
        assert findings[0]["expected_field"] == "WAR"

    def test_malformed_or_duplicate_history_is_unknown(self) -> None:
        """Keep ambiguous source text out of missing findings."""
        for text in (
            "{{Article history|action1=BPAR|action1result=approved",
            _BPLUS + " }}",
            _BPLUS + "\n{{ArticleHistory}}",
        ):
            with self.subTest(text=text):
                findings, outcome = _check(_context(text))
                assert findings == []
                assert outcome["status"] == "unavailable"

    @staticmethod
    def test_unavailable_talk_does_not_establish_a_missing_action() -> None:
        """Keep absent talk sources unknown."""
        findings, outcome = _check(_context(None))
        assert findings == []
        assert outcome["status"] == "unavailable"

    @staticmethod
    def test_other_grades_redirects_and_nonarticles_are_out_of_scope() -> None:
        """Limit the check to current mainspace A/AL/Bplus articles."""
        context = _context("No history")
        context.members = consistency._members(
            pl.concat([
                context.members.with_columns(pl.lit("GA").alias("pa_class")),
                context.members.with_columns(
                    pl.lit(11, dtype=pl.Int64).alias("page_id"),
                    pl.lit(1, dtype=pl.Int64).alias("page_is_redirect"),
                ),
                context.members.with_columns(
                    pl.lit(12, dtype=pl.Int64).alias("page_id"),
                    pl.lit(1, dtype=pl.Int64).alias("page_namespace"),
                ),
            ]),
        )
        findings, outcome = _check(context)
        assert findings == []
        assert outcome["status"] == "complete"
        assert outcome["checked_count"] == 0

    @staticmethod
    def test_unknown_redirect_state_is_unavailable() -> None:
        """Require known redirect state for current scope."""
        context = _context("No history")
        context.members = context.members.drop("page_is_redirect")
        findings, outcome = _check(context)
        assert findings == []
        assert outcome["status"] == "unavailable"

    @staticmethod
    def test_renamed_article_retains_stable_page_and_source_identities() -> (
        None
    ):
        """Keep stable IDs and talk revisions after a title change."""
        context = _context("No history", title="Renamed game")
        findings, outcome = _check(context)
        assert outcome["status"] == "complete"
        assert findings[0]["page_id"] == 10
        assert findings[0]["title"] == "Renamed game"
        assert findings[0]["checked_revision"] == 201
        assert findings[0]["source_revision"] == 101
        context.read.save.assert_not_called()
