"""Keep repeated project reviews out of general discussion pages."""

from __future__ import annotations

import datetime as dt
from unittest import TestCase
from urllib.parse import unquote

from wiki_fixtures import article_url

from tasks.zhwiki.discussions import talk_topics
from tasks.zhwiki.reviews import talk_review
from webservice.pages import render_dashboard, render_discussions

_NOW = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)


def _observations(kind: str, heading: str, *, lead: str = "") -> dict:
    """Parse an old topic and a current review with the same heading.

    Returns:
        Saved observations with no response or verified comment URL.
    """
    text = (
        lead + f"== {heading} ==\n"
        "Old opening [[User:Old]] 2026年11月8日 (日) 12:00 (UTC)\n"
        f"== {heading} ==\n"
        "Current review [[User:New]] 2026年11月9日 (一) 12:00 (UTC)"
    )
    review = talk_review(
        text,
        kind,
        title="Game",
        talk="Talk:Game",
        url=article_url,
    )[0]
    topics = talk_topics(text, talk="Talk:Game", url=article_url)
    for row in [review, *topics]:
        row.update(
            url=article_url("Game"),
            source_title="Talk:Game",
            subject_title="Game",
            subject_url=article_url("Game"),
            assessment_class="Bplus",
        )
    return {"reviews": {"items": [review], "discussions": topics}}


class ReviewSectionIdentityTests(TestCase):
    """Use the selected heading node instead of its repeated text."""

    def test_project_reviews_link_the_selected_duplicate_heading(self) -> None:
        """Give each recognized last review the same exact topic URL."""
        for kind, heading in (
            ("acc", "甲級內容評審"),
            ("bpan", "乙上級評審"),
            ("ppr", "專題同儕評審"),
            ("reassess", "重新評級"),
        ):
            with self.subTest(kind=kind):
                data = _observations(kind, heading)["reviews"]
                review = data["items"][0]
                older, current = data["discussions"]
                assert review["review_url"] == current["review_url"]
                assert review["review_url"] != older["review_url"]
                assert unquote(review["review_url"]).endswith(
                    "#" + heading + "_2",
                )
                assert review["nominator"] == "New"
                assert review["replied_at"] is None
                assert review["replied_url"] is None
                assert review["latest_comment_url"] is None

    @staticmethod
    def test_review_anchor_reserves_headings_at_every_level() -> None:
        """Respect occupied suffixes before the selected review."""
        heading = "乙上級評審"
        data = _observations(
            "bpan",
            heading,
            lead=f"= {heading} =\n=== {heading}_2 ===\n",
        )["reviews"]
        review = data["items"][0]
        current = data["discussions"][-1]
        assert review["review_url"] == current["review_url"]
        assert unquote(review["review_url"]).endswith("#" + heading + "_4")

    @staticmethod
    def test_both_general_pages_keep_only_the_independent_old_topic() -> None:
        """Exclude the current review without verified reply IDs."""
        snapshot = _observations("bpan", "乙上級評審")
        older, current = snapshot["reviews"]["discussions"]
        dashboard = render_dashboard(snapshot, {}, {}, "en", _NOW)
        general = dashboard.split('id="discussions"', 1)[1].split(
            "</section>",
            1,
        )[0]
        monthly = render_discussions(snapshot, {}, {}, "en", _NOW)
        for output in (general, monthly):
            assert older["review_url"] in output
            assert current["review_url"] not in output
