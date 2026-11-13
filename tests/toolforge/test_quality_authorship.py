"""Verify localized quality-author summaries and article tables."""

# Keep fixture counts and authored Chinese punctuation explicit.
# ruff: file-ignore[ambiguous-unicode-character-string, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import re
import unittest
from html import unescape

from webservice.pages import render_quality_authorship
from webservice.presentation import Interface, title_url
from webservice.quality_authorship import quality_authorship_context

_NOW = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)


def _directory() -> dict:
    """Prepare shared-article evidence with hostile source text.

    Returns:
        Saved input with whole article counts and unknown activity.
    """
    return {
        "available": True,
        "observed_at": "2026-11-10T09:00:00+00:00",
        "authors": [
            {
                "name": 'Editor <&> "name"',
                "last_active": "2026-10-01T15:00:00+00:00",
                "credit": 2,
                "grades": {"FA": 1, "FL": 0, "GA": 1, "Bplus": 0},
                "articles": [
                    {
                        "page_id": 1,
                        "title": "Game <script> & title",
                        "grade": "FA",
                        "date": "2024-01-01",
                        "nomination_link": "#典范条目评选",
                        "coauthor_count": 1,
                        "credit": 1,
                    },
                    {
                        "page_id": 2,
                        "title": "Shared 遊戲",
                        "grade": "GA",
                        "date": "2026-11-09",
                        "nomination_link": "https://zh.wikipedia.org/w/index.php?oldid=123#GA_review",
                        "coauthor_count": 2,
                        "coauthors": ['Editor <&> "name"', "Another <editor>"],
                        "credit": 1,
                    },
                ],
            },
            {
                "name": "Unknown activity",
                "last_active": None,
                "credit": 1,
                "grades": {"FA": 0, "FL": 0, "GA": 1, "Bplus": 0},
                "articles": [],
            },
        ],
    }


class BClassAuthorshipPresentationTests(unittest.TestCase):
    """Separate current B revisions from dated high-quality credits."""

    @staticmethod
    def test_nonzero_subsets_share_parentheses_and_omit_zero_counts() -> None:
        """Render the requested compact totals without empty subsets."""
        for quality, featured, expected in (
            (45, 7, "47条目（45高品质 | 7典范）"),
            (45, 0, "47条目（45高品质）"),
            (0, 0, "47条目"),
        ):
            source = _directory()
            source["include_b"] = True
            author = source["authors"][0]
            author.update(
                credit=47,
                high_quality_credit=quality,
                grades={"FA": featured},
            )
            output = render_quality_authorship(
                source,
                None,
                None,
                "zh-hans",
                _NOW,
            )
            text = unescape(re.sub(r"<[^>]*>", "", output))
            assert expected in text
            assert "0高品质" not in text
            assert "0典范" not in text

    @staticmethod
    def test_b_toggle_has_developed_and_high_quality_subsets() -> None:
        """Keep all three dialogs and current revision provenance."""
        source = _directory()
        source.update(
            include_b=True,
            b_coverage={"collected": 1, "total": 420},
        )
        author = source["authors"][0]
        author.update(credit=3, high_quality_credit=2)
        author["grades"]["BL"] = 1
        author["articles"].append({
            "page_id": 3,
            "title": "Current list",
            "grade": "BL",
            "date": None,
            "revision_id": 95500001,
            "source_url": "https://untrusted.invalid/",
            "nomination_link": None,
            "coauthors": [author["name"]],
            "coauthor_count": 1,
            "credit": 1,
        })
        for locale, developed, high_quality in (
            ("en", "Developed articles by", "2 high-quality"),
            ("zh-hans", "的成型条目", "2高品质"),
            ("zh-hant", "的成型條目", "2高品質"),
        ):
            context = quality_authorship_context(
                source,
                Interface(locale),
                _NOW,
            )
            data = context["quality_authorship"]
            row = data["authors"][0]
            assert developed in row["dialog_title"]
            assert row["quality_summary"] == high_quality
            assert [
                article["grade"] for article in row["quality_articles"]
            ] == ["FA", "GA"]
            assert [
                article["grade"] for article in row["featured_articles"]
            ] == ["FA"]
            current = row["articles"][-1]
            assert current["date_label"] is None
            assert current["age"] is None
            assert (
                current["source_url"]
                == "https://zh.wikipedia.org/w/index.php?oldid=95500001"
            )
            output = render_quality_authorship(
                source,
                None,
                None,
                locale,
                _NOW,
            )
            assert 'name="count_b" value="1" checked' in output
            assert "1/420" in output
            assert high_quality in output
            assert 'href="#quality-author-1-quality"' in output
            assert 'href="#quality-author-1-featured"' in output
            assert 'datetime="None"' not in output
            assert "untrusted.invalid" not in output

    @staticmethod
    def test_b_is_off_by_default_and_keeps_the_original_summary() -> None:
        """Require an explicit GET choice before including B credits."""
        context = quality_authorship_context(
            _directory(),
            Interface("en"),
            _NOW,
        )
        data = context["quality_authorship"]
        assert not data["include_b"]
        assert data["authors"][0]["summary"] == "2 articles"
        assert data["authors"][0]["quality_summary"] is None
        output = render_quality_authorship(
            _directory(),
            None,
            None,
            "en",
            _NOW,
        )
        assert 'name="count_b" value="1" checked' not in output
        assert 'method="get" data-quality-authorship-filter' in output
        assert 'type="submit"' not in output
        assert "<noscript><p><a href=" in output


class QualityAuthorshipPresentationTests(unittest.TestCase):
    """Keep credits, source dates and dialog links truthful."""

    @staticmethod
    def test_a_and_al_articles_render_without_featured_credit() -> None:
        """Render quality grades outside the featured subset."""
        for grade in ("A", "AL"):
            source = _directory()
            author = source["authors"][0]
            author["articles"][0]["grade"] = grade
            author["grades"]["FA"] = 0
            author["grades"][grade] = 1
            context = quality_authorship_context(source, Interface("en"), _NOW)
            row = context["quality_authorship"]["authors"][0]
            assert row["articles"][0]["grade"] == grade
            assert row["featured_articles"] == []
            assert not row["featured_summary"]

    @staticmethod
    def test_whole_article_summaries_and_compact_listing_ages() -> None:
        """Keep whole counts and verified listing links."""
        data = quality_authorship_context(_directory(), Interface("en"), _NOW)
        authors = data["quality_authorship"]["authors"]
        assert authors[0]["summary"] == "2 articles"
        assert authors[0]["featured_summary"] == "1 featured"
        assert [a["grade"] for a in authors[0]["featured_articles"]] == ["FA"]
        assert authors[0]["last_active"]["age"] == "1 month ago"
        assert authors[0]["articles"][0]["age"] == "2 years ago"
        assert authors[0]["articles"][1]["age"] == "1 day ago"
        assert authors[0]["articles"][0]["listing_url"] == (
            title_url("Talk:Game <script> & title")
            + "#%E5%85%B8%E8%8C%83%E6%9D%A1%E7%9B%AE%E8%AF%84%E9%80%89"
        )
        assert authors[0]["articles"][1]["listing_url"] == (
            "https://zh.wikipedia.org/w/index.php?oldid=123#GA_review"
        )
        assert "Special%3AContributions%2F" in authors[0]["contributions_url"]
        assert authors[1]["last_active"] is None

    @staticmethod
    def test_relative_age_units_and_utc_day_boundaries() -> None:
        """Use shared whole-unit thresholds for both date columns."""
        for days, english, simplified, traditional in (
            (0, "today", "今日", "今日"),
            (1, "1 day ago", "1日前", "1日前"),
            (6, "6 days ago", "6日前", "6日前"),
            (7, "1 week ago", "1周前", "1週前"),
            (13, "1 week ago", "1周前", "1週前"),
            (14, "2 weeks ago", "2周前", "2週前"),
            (30, "4 weeks ago", "4周前", "4週前"),
            (31, "1 month ago", "1个月前", "1個月前"),
            (365, "11 months ago", "11个月前", "11個月前"),
            (366, "1 year ago", "1年前", "1年前"),
            (731, "2 years ago", "2年前", "2年前"),
            (4582, "12 years ago", "12年前", "12年前"),
            (-1, "in 1 day", "1日后", "1日後"),
            (-7, "in 1 week", "1周后", "1週後"),
            (-14, "in 2 weeks", "2周后", "2週後"),
            (-31, "in 1 month", "1个月后", "1個月後"),
            (-366, "in 1 year", "1年后", "1年後"),
        ):
            source = _directory()
            author = source["authors"][0]
            instant = _NOW - dt.timedelta(days=days)
            author["last_active"] = instant.isoformat()
            author["articles"][0]["date"] = instant.date().isoformat()
            for locale, expected in (
                ("en", english),
                ("zh-hans", simplified),
                ("zh-hant", traditional),
            ):
                context = quality_authorship_context(
                    source,
                    Interface(locale),
                    _NOW,
                )
                row = context["quality_authorship"]["authors"][0]
                assert row["last_active"]["age"] == expected
                assert row["articles"][0]["age"] == expected

    @staticmethod
    def test_activity_age_keeps_exact_utc_timestamp_tooltip() -> None:
        """Use UTC dates and preserve exact seconds in hover details."""
        source = _directory()
        source["authors"][0]["last_active"] = "2026-11-10T00:30:42+08:00"
        context = quality_authorship_context(
            source,
            Interface("zh-hans"),
            _NOW,
        )
        active = context["quality_authorship"]["authors"][0]["last_active"]
        assert active["value"] == "2026-11-09T16:30:42+00:00"
        assert active["title"] == "2026-11-09 16:30:42 UTC"
        assert active["age"] == "1日前"

    @staticmethod
    def test_parentheses_touch_names_and_dates_in_chinese() -> None:
        """Join Chinese punctuation directly to names and dates."""
        source = _directory()
        source["authors"][0]["name"] = "Basedora"
        source["authors"][0]["last_active"] = (
            _NOW - dt.timedelta(days=4582)
        ).isoformat()
        for locale, title, opening, closing, talk, age in (
            (
                "en",
                "High-quality articles by Basedora",
                " (",
                ")",
                "talk",
                "12 years ago",
            ),
            (
                "zh-hans",
                "Basedora的高品质条目",
                "\uff08",
                "\uff09",
                "讨论",
                "12年前",
            ),
            (
                "zh-hant",
                "Basedora的高品質條目",
                "\uff08",
                "\uff09",
                "討論",
                "12年前",
            ),
        ):
            output = render_quality_authorship(
                source,
                None,
                None,
                locale,
                _NOW,
            )
            assert f">Basedora</a>{opening}<a href=" in output
            assert f">{talk}</a>{closing}</td>" in output
            assert (
                f'</a><span class="context">{opening}{age}{closing}</span>'
                in output
            )
            assert f'class="ui-dialog__header__title">{title}</h3>' in output
            assert "4582" not in output

    @staticmethod
    def test_all_locales_preserve_native_tables_and_escape_sources() -> None:
        """Escape source names in linked authors and dialogs."""
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_quality_authorship(
                _directory(),
                None,
                None,
                locale,
                _NOW,
            )
            assert output.count("data-quality-authorship-table ") == 4
            assert 'aria-haspopup="dialog"' in output
            assert 'aria-controls="quality-author-1"' in output
            assert '<dialog id="quality-author-1"' in output
            assert "Game &lt;script&gt; &amp; title" in output
            assert "Editor &lt;&amp;&gt; &#34;name&#34;" in output
            assert 'href="https://zh.wikipedia.org/wiki/User%20talk' not in (
                output
            )
            assert 'href="https://zh.wikipedia.org/wiki/User_talk%3A' in output
            assert "½" not in output
            assert 'aria-controls="quality-author-1-featured"' in output
            assert '<dialog id="quality-author-1-featured"' in output
            assert 'data-default-sort="column3"' in output
            assert 'id="authorship-method"' not in output
            assert "quality-authorship-credit" not in output
            assert "Another &lt;editor&gt;" in output
            assert "quality-authorship-coauthors" in output
            assert (
                'href="https://zh.wikipedia.org/w/index.php?oldid=123#GA_review"'
                in output
            )

            assert (
                'href="https://zh.wikipedia.org/wiki/Special%3AContributions%2F'
                in output
            )
            assert "20%" not in output
            for title in (
                "WikiProject:電子遊戲/簡訊/年终回顾导航模板",
                "WikiProject:電子遊戲/簡訊/2021-01/專題",
            ):
                assert title_url(title) in output
            interface = Interface(locale)
            assert f"<p>{interface.text('quality_authorship.intro')}</p>" in (
                output
            )
            assert (
                "<p>"
                + interface.text("quality_authorship.accuracy_prefix")
                + '<a href="https://www.mediawiki.org/wiki/WikiWho">'
                + "WikiWho</a>"
            ) in output
            assert (
                "</a>"
                + interface.text(
                    "quality_authorship.accuracy_sources_separator",
                )
                + '<a href="'
                + title_url("WikiProject:電子遊戲/簡訊/2021-01/專題")
            ) in output
            assert (
                interface.text("quality_authorship.earlier_intro")
                + "</a>"
                + interface.text("quality_authorship.accuracy_end")
                + "</p>"
            ) in output

    @staticmethod
    def test_guidance_copy_uses_body_paragraphs_and_inline_sources() -> None:
        """Present ordinary body prose with inline source links."""
        output = render_quality_authorship(
            _directory(),
            None,
            None,
            "zh-hans",
            _NOW,
        )
        assert (
            "<p>本页列出电子游戏专题高品质条目的重大贡献者。"
            "有志打磨条目的编者，可向仍然活跃的用户征求编辑建议。</p>"
        ) in output
        assert "本表并非荣誉榜，使用" in output
        assert (
            "自动推定重大贡献者，判定未必准确。人工核查的作者名单，可参考"
            in (unescape(output))
        )
        assert "名单，可参考：" not in output
        assert "</a> · <a" not in output

    @staticmethod
    def test_title_conversion_keeps_canonical_article_and_review_urls() -> (
        None
    ):
        """Convert visible titles and preserve canonical source URLs."""
        for locale, title in (
            ("zh-hans", "Shared 游戏"),
            ("zh-hant", "Shared 遊戲"),
        ):
            context = quality_authorship_context(
                _directory(),
                Interface(locale),
                _NOW,
            )
            article = context["quality_authorship"]["authors"][0]["articles"][
                1
            ]
            assert article["title_label"] == title
            assert article["url"] == title_url("Shared 遊戲")
            assert article["listing_url"] == (
                "https://zh.wikipedia.org/w/index.php?oldid=123#GA_review"
            )

    @staticmethod
    def test_featured_summary_counts_and_filters_both_fa_and_fl() -> None:
        """Link the total and featured subset separately."""
        source = _directory()
        author = source["authors"][0]
        author["credit"] = 44
        author["grades"] = {"FA": 5, "FL": 2, "GA": 37, "Bplus": 0}
        author["articles"].append({
            **author["articles"][0],
            "page_id": 3,
            "title": "Featured list",
            "grade": "FL",
        })
        for locale, total, featured in (
            ("en", "44 articles", "7 featured"),
            ("zh-hans", "44条目", "7典范"),
            ("zh-hant", "44條目", "7典範"),
        ):
            context = quality_authorship_context(
                source,
                Interface(locale),
                _NOW,
            )
            row = context["quality_authorship"]["authors"][0]
            assert row["summary"] == total
            assert row["featured_summary"] == featured
            assert [
                article["grade"] for article in row["featured_articles"]
            ] == [
                "FA",
                "FL",
            ]

    @staticmethod
    def test_unverified_or_external_review_links_remain_unlinked() -> None:
        """Do not invent a discussion link when evidence is absent."""
        for link in (
            None,
            "",
            "https://other.example/#review",
            "#",
            "javascript:alert(1)",
        ):
            source = _directory()
            source["authors"][0]["articles"][0]["nomination_link"] = link
            context = quality_authorship_context(source, Interface("en"), _NOW)
            assert (
                context["quality_authorship"]["authors"][0]["articles"][0][
                    "listing_url"
                ]
                is None
            )

    @staticmethod
    def test_missing_evidence_is_distinct_from_an_empty_directory() -> None:
        """Preserve unavailable and empty as distinct states."""
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            missing = render_quality_authorship(
                {"available": False, "authors": []},
                None,
                None,
                locale,
                _NOW,
            )
            empty = render_quality_authorship(
                {"available": True, "authors": []},
                None,
                None,
                locale,
                _NOW,
            )
            assert interface.text("quality_authorship.unavailable") in missing
            assert interface.text("quality_authorship.empty") in empty
            assert interface.text("quality_authorship.empty") not in missing


if __name__ == "__main__":
    unittest.main()
