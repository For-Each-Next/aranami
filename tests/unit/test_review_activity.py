"""Verify review lengths and independent latest-comment facts."""

# Source fixtures retain Chinese signature syntax and observed counts.
# ruff: file-ignore[private-member-access, magic-value-comparison]
# ruff: file-ignore[ambiguous-unicode-character-string]

from functools import partial
from unittest import TestCase
from unittest.mock import Mock

import polars as pl
from wiki_fixtures import OfflineSite

from data.website import valid_report_data
from shared.support.titles import page_url
from tasks.zhwiki import review_cache, reviews

_OPENED = "2026-10-07T03:04:00+00:00"
_REPLIED = "2026-10-08T04:00:00+00:00"
_FOLLOWED = "2026-10-09T05:00:00+00:00"
_OPENING = "[[User:Opening]] 2026年10月7日 (三) 03:04 (UTC)\n"
_RESPONSE = "[[User:Reviewer]] 2026年10月8日 (四) 04:00 (UTC)\n"
_FOLLOWUP = "[[User:Opening]] 2026年10月9日 (五) 05:00 (UTC)\n"


class ReviewActivityTests(TestCase):
    """Preserve source facts even without another participant."""

    def setUp(self) -> None:
        """Bind real title and URL rules to an offline wiki."""
        self.site = OfflineSite("zh", "wikipedia")
        self.url = partial(page_url, self.site)

    def _row(self, source: str) -> dict:
        """Read one complete ordinary review without source requests.

        Returns:
            The actual parsed nomination row.
        """
        return reviews.community_reviews(source, "fac", url=self.url)[0]

    def test_one_comment_is_latest_without_becoming_a_reply(self) -> None:
        """Keep a solo opening, count and complete byte length."""
        source = "== [[Game]] ==\n漢字 [[File:Image.jpg|thumb]]\n" + _OPENING
        row = self._row(source)
        assert row["started_at"] == _OPENED
        assert row["latest_comment_at"] == _OPENED
        assert row["latest_comment_by"] == "Opening"
        assert row["replied_at"] is None
        assert row["replied_by"] is None
        assert row["participant_count"] == 1
        assert row["comment_count"] == 1
        assert row["section_bytes"] == len(source.encode("utf-8"))

    def test_nominator_followup_does_not_replace_last_other_reply(
        self,
    ) -> None:
        """Keep reply and latest-comment identities independent."""
        section = (
            "== Game ==\n"
            + _OPENING
            + _RESPONSE
            + "=== Followup ===\n[[File:Media.jpg]]\n"
            + _FOLLOWUP
        )
        row = self._row(section + "== Other ==\n" + _FOLLOWUP)
        assert row["replied_at"] == _REPLIED
        assert row["replied_by"] == "Reviewer"
        assert row["latest_comment_at"] == _FOLLOWED
        assert row["latest_comment_by"] == "Opening"
        assert row["participant_count"] == 2
        assert row["comment_count"] == 3
        assert row["section_bytes"] == len(section.encode("utf-8"))
        solo = self._row("== Game ==\n" + _OPENING + _FOLLOWUP)
        assert solo["latest_comment_at"] == _FOLLOWED
        assert solo["participant_count"] == 1
        assert solo["replied_at"] is None

    def test_nomination_metadata_does_not_invent_a_comment(self) -> None:
        """Keep unsigned latest-comment times and authors unknown."""
        row = self._row(
            "== Game ==\n投票期：2026年10月7日 (三) 03:04 (UTC)\n",
        )
        assert row["started_at"] == _OPENED
        assert row["latest_comment_at"] is None
        assert row["latest_comment_by"] is None
        assert row["participant_count"] is None
        assert row["section_bytes"] > 0
        missing = reviews.talk_review(
            "== Other topic ==\n" + _OPENING,
            "ppr",
            title="Game",
            talk="Talk:Game",
            url=self.url,
        )[0]
        assert missing["section_bytes"] is None
        assert missing["latest_comment_at"] is None

    def test_dyk_bytes_include_own_entry_and_bounded_comments(self) -> None:
        """Include candidate metadata and its own subsections."""
        first = (
            "{{DYKEntry|article=Game|nominator=Opening|timestamp=1791342240}}\n"
            + _OPENING
            + "==== Comment ====\n[[File:Image.jpg]]\n"
            + _FOLLOWUP
        )
        rows = reviews.community_reviews(
            "=== 10月7日 ===\n"
            + first
            + "{{DYKEntry|article=Other|nominator=Someone}}\n"
            + _RESPONSE,
            "dykc",
            url=self.url,
        )
        assert rows[0]["section_bytes"] == len(first.encode("utf-8"))
        assert rows[0]["latest_comment_at"] == _FOLLOWED
        assert rows[0]["replied_at"] is None
        assert rows[0]["participant_count"] == 1

    def test_optional_public_fields_validate_without_breaking_old_rows(
        self,
    ) -> None:
        """Accept legacy facts and reject malformed new fields."""
        row = self._row("== Game ==\n" + _OPENING + _RESPONSE)
        row["url"] = self.url("Game")
        assert valid_report_data("reviews", {"items": [row]}, 1)
        legacy = {
            key: value
            for key, value in row.items()
            if not key.startswith("latest_comment_") and key != "section_bytes"
        }
        assert valid_report_data("reviews", {"items": [legacy]}, 1)
        for field, value in (
            ("latest_comment_at", "2026-10-07T03:00:00+00:00"),
            ("latest_comment_at", "invalid"),
            ("latest_comment_by", "Bad\x00name"),
            ("latest_comment_url", "https://example.org/comment"),
            ("section_bytes", 0),
        ):
            with self.subTest(field=field, value=value):
                assert not valid_report_data(
                    "reviews",
                    {"items": [{**row, field: value}]},
                    1,
                )

    def test_links_deduplicate_identical_ids_and_keep_real_ambiguity(
        self,
    ) -> None:
        """Reuse one lookup for distinct response/comment links."""
        row = self._row("== Game ==\n" + _OPENING + _RESPONSE + _FOLLOWUP)
        public = pl.DataFrame([row], schema=review_cache.ITEM_SCHEMA)
        sources = public.with_columns(
            pl.lit(reviews.COMMUNITY_SOURCES["fac"]).alias("source_title"),
            pl.lit(123).alias("source_revision"),
        )
        reply = {
            "id": "c-response",
            "author": "Reviewer",
            "timestamp": _REPLIED,
        }
        latest = {
            "id": "c-followup",
            "author": "Opening",
            "timestamp": _FOLLOWED,
        }
        threads = [reply, {**latest, "replies": [latest]}, latest]
        self.site.simple_request = Mock(
            return_value=Mock(
                submit=Mock(
                    return_value={
                        "discussiontoolspageinfo": {
                            "threaditemshtml": threads,
                        },
                    },
                ),
            ),
        )
        linked = reviews._reply_links(self.site, public, sources).row(
            0,
            named=True,
        )
        assert linked["replied_url"].endswith("#c-response")
        assert linked["latest_comment_url"].endswith("#c-followup")
        threads.append({**latest, "id": "c-distinct"})
        ambiguous = reviews._reply_links(self.site, public, sources).row(
            0,
            named=True,
        )
        assert ambiguous["latest_comment_url"] is None
        assert ambiguous["replied_url"].endswith("#c-response")
