"""Check bounded historical recognition searches with offline text."""

# Decorator arguments are unittest mocks, not pytest fixtures.
# ruff: file-ignore[pytest-fixture-param-without-value, ambiguous-unicode-character-string]

from datetime import date
from unittest import TestCase
from unittest.mock import Mock, patch

from test_dyks import _OfflineSite  # ruff: ignore[import-private-name]

from backend.services.zhwiki.article_milestones import extract_credits
from backend.website.article_milestone_sources import lookup_milestone
from shared.sources.article_milestones import SourceRevision

_ENTRY = (
    "{{DYKEntry/archive|article=Example|author=Writer"
    "|nominator=Voter|timestamp=1704067200|question=Which [[Example]]?}}"
)
_EVENT = {
    "title": "Example",
    "date": date(2024, 1, 5),
    "kind": "dyk",
    "grade": None,
    "action": "appeared",
    "revision_id": 10,
}
_SOURCE = SourceRevision("Talk:Example", 10, _ENTRY)


class RecognitionSourceTests(TestCase):
    """Retain proven credits and explicitly unknown source coverage."""

    def setUp(self) -> None:
        """Prepare local namespace metadata."""
        self.site = _OfflineSite("zh", "wikipedia")
        aliases = patch(
            "backend.website.article_milestone_sources.source.template_names",
            return_value={},
        )
        aliases.start()
        self.addCleanup(aliases.stop)

    def test_explicit_fields_are_separate(self) -> None:
        """The author never silently replaces a missing nominator."""
        result = extract_credits(_ENTRY, _EVENT, self.site)
        assert result[0]["contributors"] == ["Writer"]
        assert result[0]["nominator"] == "Voter"
        missing = extract_credits(
            _ENTRY.replace("nominator=Voter", "nominator="),
            _EVENT,
            self.site,
        )
        assert missing[0]["nominator"] is None

    def test_wrong_future_failed_and_quoted_entries_excluded(self) -> None:
        """Unrelated rounds and examples provide no proof."""
        for text in (
            _ENTRY.replace("Example", "Other"),
            _ENTRY.replace("1704067200", "1804067200"),
            _ENTRY.replace("}}", "|result=-}}"),
            "<nowiki>" + _ENTRY + "</nowiki>",
        ):
            assert extract_credits(text, _EVENT, self.site) == []

    def test_markup_and_anonymous_author_unknown(self) -> None:
        """Keep invalid contributor names unknown."""
        for name in ("", "127.0.0.1", "{{User|Writer}}"):
            result = extract_credits(
                _ENTRY.replace("author=Writer", "author=" + name),
                _EVENT,
                self.site,
            )
            assert result[0]["contributors"] is None

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
        return_value=([], None),
    )
    @patch(
        "backend.website.article_milestone_sources.source.read_revision",
        return_value=_SOURCE,
    )
    def test_unique_credit_retains_exact_provenance(
        self,
        _read: Mock,
        _archives: Mock,
    ) -> None:
        """Retain the exact revision used as proof."""
        result = lookup_milestone(self.site, _EVENT)
        assert result["status"] == "complete"
        assert result["sources"] == [
            {
                "title": "Talk:Example",
                "revision_id": 10,
                "url": "https://zh.wikipedia.org/w/index.php?oldid=10",
            },
        ]
        assert result["nomination_link"].endswith("oldid=10")

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
    )
    @patch(
        "backend.website.article_milestone_sources.source.read_revision",
        return_value=_SOURCE,
    )
    def test_archives_resume_before_claiming_unique(
        self,
        read: Mock,
        archives: Mock,
    ) -> None:
        """A later conflicting nomination prevents false completion."""
        archives.return_value = ([], "Talk:Example/Archive 8")
        first = lookup_milestone(self.site, _EVENT)
        assert first["status"] == "pending"
        assert "contributors" not in first
        read.reset_mock()
        archives.return_value = (
            [
                SourceRevision(
                    "Talk:Example/Archive 9",
                    11,
                    _ENTRY.replace("1704067200", "1704067201"),
                ),
            ],
            None,
        )
        final = lookup_milestone(
            self.site,
            {**_EVENT, "lookup_cursor": first["lookup_cursor"]},
        )
        assert final["reason"] == "ambiguous_nomination"
        read.assert_not_called()
        assert archives.call_args.kwargs["after"] == "Talk:Example/Archive 8"

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
        side_effect=OSError,
    )
    @patch(
        "backend.website.article_milestone_sources.source.read_revision",
        return_value=_SOURCE,
    )
    def test_failure_preserves_checkpoint(
        self,
        _read: Mock,
        _archives: Mock,
    ) -> None:
        """Unavailable reads cannot erase a saved continuation."""
        cursor = '{"after":"Talk:Example/Archive 8"}'
        result = lookup_milestone(
            self.site,
            {**_EVENT, "lookup_cursor": cursor},
        )
        assert result["status"] == "blocked"
        assert result["lookup_cursor"] == cursor

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
        return_value=([], None),
    )
    @patch(
        "backend.website.article_milestone_sources.source.read_revision",
        return_value=None,
    )
    def test_missing_sources_stay_unresolved(
        self,
        _read: Mock,
        _archives: Mock,
    ) -> None:
        """Exhausted samples remain unresolved."""
        result = lookup_milestone(self.site, _EVENT)
        assert result["status"] == "unresolved"
        assert "contributors" not in result

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
        return_value=([], None),
    )
    @patch("backend.website.article_milestone_sources.source.read_revision")
    def test_later_complete_revision_of_same_round_wins(
        self,
        read: Mock,
        _archives: Mock,
    ) -> None:
        """Natural archive corrections do not create false ambiguity."""
        old = SourceRevision(
            "Talk:Example",
            9,
            _ENTRY.replace("nominator=Voter", "nominator="),
        )
        read.side_effect = [old, _SOURCE, old, _SOURCE, _SOURCE]
        result = lookup_milestone(self.site, _EVENT)
        assert result["status"] == "complete"
        assert result["nominator"] == "Voter"
        assert result["sources"][0]["revision_id"] == _SOURCE.revision_id

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
        return_value=([], None),
    )
    @patch("backend.website.article_milestone_sources.source.read_revision")
    def test_previous_dyk_round_does_not_conflict(
        self,
        read: Mock,
        _archives: Mock,
    ) -> None:
        """Earlier appearances bound the matching nomination."""
        text = (
            "{{Article history|dykdate=2023-01-05|dyk2date=2024-01-05}}"
            + _ENTRY
            + _ENTRY.replace("1704067200", "1672531200")
        )
        read.return_value = SourceRevision("Talk:Example", 10, text)
        result = lookup_milestone(self.site, _EVENT)
        assert result["status"] == "complete"

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
        return_value=([], None),
    )
    @patch("backend.website.article_milestone_sources.source.read_revision")
    def test_explicit_external_archive_target(
        self,
        read: Mock,
        _archives: Mock,
    ) -> None:
        """Follow the recorded nomination title and exact anchor."""
        text = (
            "{{Article history|action1=GAN|action1result=listed"
            "|action1date=2024-01-05"
            "|action1link=Project:Review/Example#Round}}"
        )
        talk = SourceRevision("Talk:Example", 10, text)
        archive = SourceRevision(
            "Project:Review/Example",
            12,
            "==Round==\n提名者：[[User:Voter]] 2024年1月1日 (一) 00:00 (UTC)",
        )
        read.side_effect = [talk, talk, talk, talk, archive, archive]
        result = lookup_milestone(
            self.site,
            {**_EVENT, "kind": "quality", "grade": "GA", "action": "added"},
        )
        assert result["status"] == "complete"
        assert result["nomination_link"].endswith("oldid=12#Round")
        assert result["main_author"] is None

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
        return_value=([], None),
    )
    @patch("backend.website.article_milestone_sources.source.read_revision")
    def test_unique_missing_date_has_source_proof(
        self,
        read: Mock,
        _archives: Mock,
    ) -> None:
        """Recover one listing date with exact talk provenance."""
        read.return_value = SourceRevision(
            "Talk:Example",
            10,
            "{{Article history|action1=BPAR|action1result=approved"
            "|action1date=2024-01-05}}",
        )
        result = lookup_milestone(
            self.site,
            {
                **_EVENT,
                "kind": "quality",
                "grade": "Bplus",
                "action": "added",
                "date": None,
            },
        )
        assert result["date"] == _EVENT["date"]
        assert result["sources"][0]["revision_id"] == _SOURCE.revision_id

    def test_local_quality_round_retains_explicit_declaration(self) -> None:
        """Keep signed authorship declarations."""
        text = (
            "{{Article history|action1=GAN|action1result=listed"
            "|action1date=2024-01-05|action1link=#Round}}"
            "\n==Round==\n提名者：主編自薦 [[User:Voter]] "
            "2024年1月1日 (一) 00:00 (UTC)"
        )
        result = extract_credits(
            text,
            {**_EVENT, "kind": "quality", "grade": "GA", "action": "added"},
            self.site,
        )
        assert result[0]["nominator"] == "Voter"
        assert result[0]["main_author"] is True

    def test_verified_aliases_are_used(self) -> None:
        """Use resolved names without guessing unfamiliar templates."""
        event = {
            **_EVENT,
            "_aliases": {"DYKEntry/archive": ("Template:Entry alias",)},
        }
        text = _ENTRY.replace("DYKEntry/archive", "Entry alias")
        assert extract_credits(text, event, self.site)[0]["contributors"] == [
            "Writer",
        ]

    def test_question_keeps_rich_wikitext_and_rejects_controls(self) -> None:
        """Keep explicit question markup and unknown fields."""
        result = extract_credits(_ENTRY, _EVENT, self.site)
        assert result[0]["question"] == "Which [[Example]]?"
        for value in ("", "bad\x00text", "x" * 16385):
            invalid = _ENTRY.replace("Which [[Example]]?", value)
            observed = extract_credits(invalid, _EVENT, self.site)
            assert not observed or observed[0]["question"] is None

    @patch(
        "backend.website.article_milestone_sources.source.read_archives",
        return_value=([], "Talk:Example/Archive 8"),
    )
    @patch("backend.website.article_milestone_sources.source.read_revision")
    def test_question_text_does_not_overflow_continuation(
        self,
        read: Mock,
        _archives: Mock,
    ) -> None:
        """Keep long questions outside cursor payloads."""
        read.return_value = SourceRevision(
            "Talk:Example",
            10,
            _ENTRY.replace("Which [[Example]]?", "x" * 8000),
        )
        result = lookup_milestone(self.site, _EVENT)
        assert result["status"] == "pending"
        assert len(result["lookup_cursor"].encode()) < 2 * 1024
