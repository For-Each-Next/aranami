"""Verify saved discussion windows, exclusive groups and order."""

# Exact time boundaries and section IDs define public page contracts.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt
import re
import tempfile
from pathlib import Path
from unittest import TestCase

from starlette.testclient import TestClient

from webservice.pages import render_dashboard, render_discussions
from webservice.presentation import Interface, title_url
from webservice.server import create_app

_NOW = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)
_GROUPS = (
    "discussion-week-1",
    "discussion-week-2",
    "discussion-week-3",
    "discussion-week-4",
    "discussion-drafts",
    "discussion-projects",
    "discussion-categories-templates",
    "discussion-other",
)


def _topic(
    subject: str,
    *,
    days: int = 0,
    seconds: int = 0,
    grade: str | None = "C",
) -> dict:
    """Create detached signed activity with an older opening comment.

    Returns:
        Saved discussion facts requiring no source requests.
    """
    source = f"Talk:{subject}"
    url = title_url(source) + "#Topic"
    return {
        "title": "Topic",
        "url": url,
        "review_type": "discussion",
        "review_url": url,
        "source_title": source,
        "subject_title": subject,
        "subject_url": title_url(subject),
        "assessment_class": grade,
        "assessment_importance": "Low",
        "started_at": (_NOW - dt.timedelta(days=100)).isoformat(),
        "replied_at": (
            _NOW - dt.timedelta(days=days, seconds=seconds)
        ).isoformat(),
        "participant_count": 2,
        "comment_count": 2,
        "extract": "A complete opening comment.",
    }


def _snapshot(rows: list[dict]) -> dict:
    """Wrap saved topics in the existing dashboard envelope.

    Returns:
        Known complete discussion observations.
    """
    return {"reviews": {"items": [], "discussions": rows}}


def _sections(output: str) -> dict[str, str]:
    """Read each dedicated discussion section in document order.

    Returns:
        Section markup keyed by its public fragment identifier.
    """
    return dict(
        re.findall(
            r'<section\b[^>]*\bid="(discussion-[^"]+)"[^>]*>'
            r"(.*?)</section>",
            output,
            re.DOTALL,
        ),
    )


class DiscussionGroupTests(TestCase):
    """Keep recent comments visible once in their intended sections."""

    @staticmethod
    def test_dashboard_requires_comment_within_seven_days() -> None:
        """Include the seven-day boundary and exclude stale activity."""
        rows = [
            _topic("Current comment"),
            _topic("Seven day boundary", days=7),
            _topic("Too old", days=7, seconds=1),
            _topic("Future comment", seconds=-1),
        ]
        for value in (None, "invalid", "2026-11-10T12:00:00"):
            row = _topic(f"Unknown {value}")
            row.update(replied_at=value, started_at=_NOW.isoformat())
            rows.append(row)
        output = render_dashboard(_snapshot(rows), {}, {}, "en", _NOW)
        section = output.split('id="discussions"', 1)[1].split(
            "</section>",
            1,
        )[0]
        assert ">Current comment</a>" in section
        assert ">Seven day boundary</a>" in section
        assert ">Too old</a>" not in section
        assert ">Future comment</a>" not in section
        assert "Unknown " not in section
        assert 'href="/discussions/' in output

    @staticmethod
    def test_dashboard_orders_page_types_by_time_before_grade() -> None:
        """Order each page type by time with stable subject ties."""
        rows = [
            _topic("File:Newest cover.png", grade="File"),
            _topic("Featured older", days=5, grade="FA"),
            _topic("WikiProject:New project topic", grade="Project"),
            _topic("Same time B", days=2, grade="GA"),
            _topic("Draft:New draft topic", grade="Draft"),
            _topic("Stub newer", days=1, grade="Stub"),
            _topic("Template:New navigation", grade="Template"),
            _topic("Same time A", days=2, grade="Start"),
            _topic("Newest", grade=None),
        ]
        output = render_dashboard(_snapshot(rows), {}, {}, "en", _NOW)
        section = output.split('id="discussions"', 1)[1].split(
            "</section>",
            1,
        )[0]
        expected = [
            "Newest",
            "Stub newer",
            "Same time A",
            "Same time B",
            "Featured older",
            "Draft:New draft topic",
            "WikiProject:New project topic",
            "Template:New navigation",
            "File:Newest cover.png",
        ]
        positions = [section.index(f">{subject}</a>") for subject in expected]
        assert positions == sorted(positions)
        assert "assessment-chips" not in section
        assert 'data-assessment="' not in section

    def test_article_weeks_cover_four_weeks_without_overlap(self) -> None:
        """Retain comments through exactly four seven-day weeks."""
        cases = (
            ("Newest", 0, 0, _GROUPS[0]),
            ("Seven", 7, 0, _GROUPS[0]),
            ("After seven", 7, 1, _GROUPS[1]),
            ("Fourteen", 14, 0, _GROUPS[1]),
            ("After fourteen", 14, 1, _GROUPS[2]),
            ("Twenty one", 21, 0, _GROUPS[2]),
            ("After twenty one", 21, 1, _GROUPS[3]),
            ("Twenty eight", 28, 0, _GROUPS[3]),
        )
        rows = [
            _topic(subject, days=days, seconds=seconds)
            for subject, days, seconds, _ in reversed(cases)
        ]
        rows.extend((
            _topic("Expired", days=28, seconds=1),
            _topic("Twenty nine", days=29),
            _topic("Thirty", days=30),
            _topic("Future", seconds=-1),
        ))
        for value in (None, "invalid", "2026-11-10T12:00:00"):
            row = _topic(f"Unknown {value}")
            row.update(replied_at=value, started_at=_NOW.isoformat())
            rows.append(row)
        output = render_discussions(_snapshot(rows), {}, {}, "en", _NOW)
        sections = _sections(output)
        assert tuple(sections) == _GROUPS
        for subject, _, _, expected in cases:
            with self.subTest(subject=subject):
                marker = f">{subject}</a>"
                assert marker in sections[expected]
                assert (
                    sum(marker in section for section in sections.values())
                    == 1
                )
        assert ">Expired</a>" not in output
        assert ">Twenty nine</a>" not in output
        assert ">Thirty</a>" not in output
        assert ">Future</a>" not in output
        assert ">Unknown " not in output
        assert "assessment-chips" not in output
        assert 'data-assessment="' not in output

    def test_nonarticle_groups_share_the_four_week_cutoff(self) -> None:
        """Keep the same display cutoff without pruning saved facts."""
        subjects = (
            ("Draft:Boundary", "Draft", _GROUPS[4]),
            ("WikiProject:Boundary", "Project", _GROUPS[5]),
            ("Category:Boundary", "Category", _GROUPS[6]),
            ("File:Boundary", "File", _GROUPS[7]),
        )
        rows = []
        for subject, grade, _ in subjects:
            rows.extend((
                _topic(subject, days=28, grade=grade),
                _topic(subject + " expired", days=28, seconds=1, grade=grade),
            ))
        sections = _sections(
            render_discussions(_snapshot(rows), {}, {}, "en", _NOW),
        )
        for subject, _, group in subjects:
            with self.subTest(subject=subject):
                assert f">{subject}</a>" in sections[group]
                assert f">{subject} expired</a>" not in sections[group]
        assert len(rows) == 8

    def test_profile_classes_select_nonarticle_groups(self) -> None:
        """Normalize local classes before considering namespaces."""
        cases = (
            ("Article shaped draft", "草稿級", _GROUPS[4]),
            ("Article shaped project", "專題級", _GROUPS[5]),
            ("Article shaped portal", "主題級", _GROUPS[5]),
            ("Article shaped category", "分類級", _GROUPS[6]),
            ("Article shaped template", "模板級", _GROUPS[6]),
            ("Ambiguous subject", "消歧義級", _GROUPS[7]),
            ("Redirected subject", "重定向級", _GROUPS[7]),
            ("Index subject", "同類索引", _GROUPS[7]),
            ("File subject", "File", _GROUPS[7]),
            ("Unassessed article", None, _GROUPS[0]),
        )
        rows = [_topic(subject, grade=grade) for subject, grade, _ in cases]
        sections = _sections(
            render_discussions(_snapshot(rows), {}, {}, "en", _NOW),
        )
        for subject, _, expected in cases:
            with self.subTest(subject=subject):
                assert f">{subject}</a>" in sections[expected]

    def test_legacy_topics_use_their_subject_namespace(self) -> None:
        """Classify old saved rows without starting wiki clients."""
        cases = (
            ("Talk:Unclassified game", "Unclassified game", _GROUPS[0]),
            ("草稿讨论:Draft game", "Draft:Draft game", _GROUPS[4]),
            (
                "WikiProject talk:电子游戏",
                "WikiProject:电子游戏",
                _GROUPS[5],
            ),
            ("主題討論:Games", "Portal:Games", _GROUPS[5]),
            ("分類討論:Games", "Category:Games", _GROUPS[6]),
            ("模板讨论:Games", "Template:Games", _GROUPS[6]),
            ("User talk:Editor", "User:Editor", _GROUPS[7]),
        )
        rows = []
        for source, subject, _ in cases:
            row = _topic(subject, grade=None)
            row.pop("subject_title")
            row.pop("subject_url")
            row["source_title"] = source
            rows.append(row)
        sections = _sections(
            render_discussions(_snapshot(rows), {}, {}, "en", _NOW),
        )
        for _, subject, expected in cases:
            with self.subTest(subject=subject):
                assert f">{subject}</a>" in sections[expected]

    def test_each_group_orders_by_latest_comment(self) -> None:
        """Order recent replies before older replies of any grade."""
        cases = (
            ("Article", "FA", "Stub", _GROUPS[0]),
            ("Draft", "Draft", "Draft", _GROUPS[4]),
            ("Project", "Project", "Portal", _GROUPS[5]),
            ("Categorized", "Category", "Template", _GROUPS[6]),
            ("Other", "Disambig", "Redirect", _GROUPS[7]),
        )
        rows = []
        for prefix, older, newer, _ in cases:
            rows.extend((
                _topic(prefix + " older", days=5, grade=older),
                _topic(prefix + " newest", days=1, grade=newer),
            ))
        sections = _sections(
            render_discussions(_snapshot(rows), {}, {}, "en", _NOW),
        )
        for prefix, _, _, expected in cases:
            with self.subTest(group=expected):
                section = sections[expected]
                assert section.index(f">{prefix} newest</a>") < section.index(
                    f">{prefix} older</a>",
                )

    def test_empty_and_missing_observations_remain_distinct(self) -> None:
        """Retain all headings for confirmed empties in every locale."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            with self.subTest(locale=locale):
                output = render_discussions(
                    _snapshot([]),
                    {},
                    {},
                    locale,
                    _NOW,
                )
                sections = _sections(output)
                assert tuple(sections) == _GROUPS
                for section in sections.values():
                    assert interface.text("empty.period") in section
                for snapshot in ({}, {"reviews": {"items": []}}):
                    missing = render_discussions(
                        snapshot,
                        {},
                        {},
                        locale,
                        _NOW,
                    )
                    assert interface.text("empty.section") in missing
                    assert interface.text("empty.period") not in missing

    def test_review_sections_are_excluded_from_both_general_views(
        self,
    ) -> None:
        """Keep reviews once and preserve independent topics."""
        kinds = (
            "fac",
            "flc",
            "acc",
            "gan",
            "bpan",
            "pr",
            "ppr",
            "dykc",
            "reassess",
        )
        for kind in kinds:
            with self.subTest(review_type=kind):
                topic = _topic("Shared game")
                topic["title"] = "Quality review"
                topic["review_url"] = topic["review_url"].replace(
                    "#Topic",
                    "#Quality_review",
                )
                other = {
                    **topic,
                    "title": "Independent topic",
                    "review_url": topic["review_url"].replace(
                        "#Quality_review",
                        "#Independent_topic",
                    ),
                }
                review = {**topic, "title": "Shared game", "review_type": kind}
                snapshot = _snapshot([topic, other])
                snapshot["reviews"]["items"] = [review]
                for general in self.general_views(snapshot):
                    assert ">Quality review</a>" not in general
                    assert ">Independent topic</a>" in general

    def test_review_url_identity_normalizes_encoding_and_spaces(self) -> None:
        """Recognize the same section under equivalent encoded URLs."""
        topic = _topic("Shared game")
        topic.update(
            title="Quality review",
            review_url="https://zh.wikipedia.org/wiki/Talk%3AShared_game#Quality_review",
        )
        review = {
            **topic,
            "title": "Shared game",
            "review_type": "bpan",
            "review_url": "https://zh.wikipedia.org/wiki/Talk%3AShared%20game#Quality%20review",
        }
        snapshot = _snapshot([topic])
        snapshot["reviews"]["items"] = [review]
        for general in self.general_views(snapshot):
            assert ">Quality review</a>" not in general

    def test_exact_comment_identity_matches_different_section_links(
        self,
    ) -> None:
        """Match review comments against general reply anchors."""
        topic = _topic("Shared game")
        topic["replied_url"] = (
            "https://zh.wikipedia.org/wiki/Talk%3AShared_game#c-Reviewer-20261110000000"
        )
        review = {
            **topic,
            "review_type": "bpan",
            "review_url": topic["review_url"].replace(
                "#Topic",
                "#Another_heading",
            ),
            "replied_url": None,
            "latest_comment_url": topic["replied_url"].replace(
                "Shared_game",
                "Shared%20game",
            ),
        }
        snapshot = _snapshot([topic])
        snapshot["reviews"]["items"] = [review]
        for general in self.general_views(snapshot):
            assert ">Topic</a>" not in general

    def test_review_page_links_do_not_hide_independent_sections(self) -> None:
        """Require a section or comment before deduplicating."""
        topic = _topic("Shared game")
        review = {
            **topic,
            "review_type": "bpan",
            "review_url": topic["review_url"].split("#", 1)[0],
        }
        snapshot = _snapshot([topic])
        snapshot["reviews"]["items"] = [review]
        for general in self.general_views(snapshot):
            assert ">Topic</a>" in general

    @staticmethod
    def general_views(snapshot: dict) -> tuple[str, str]:
        """Read only the general discussion parts of both saved views.

        Returns:
            The dashboard section and the full four-week section list.
        """
        dashboard = render_dashboard(snapshot, {}, {}, "en", _NOW)
        section = (
            dashboard.split('id="discussions"', 1)[1].split("</section>", 1)[0]
            if 'id="discussions"' in dashboard
            else ""
        )
        monthly = render_discussions(snapshot, {}, {}, "en", _NOW)
        return section, "".join(_sections(monthly).values())

    @staticmethod
    def test_discussions_route_reads_saved_data() -> None:
        """Serve the linked page and HEAD without saved observations."""
        with (
            tempfile.TemporaryDirectory() as directory,
            TestClient(create_app(Path(directory))) as client,
        ):
            response = client.get("/discussions/?lang=en")
            assert response.status_code == 200
            assert Interface("en").text("empty.section") in response.text
            assert 'href="/discussions/"' in response.text
            head = client.head("/discussions/?lang=en")
            assert head.status_code == 200
            assert not head.content
