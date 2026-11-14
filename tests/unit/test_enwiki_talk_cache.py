"""Verify exact English talk reuse across parsing and report updates."""

# Revision and page IDs are exact synthetic fixture identities.
# ruff: file-ignore[magic-value-comparison]

import datetime as dt
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from wiki_fixtures import OfflineSite

from data import enwiki
from shared.sources.revisions import RevisionContent
from tasks.enwiki import quality_listing_dates, reviews, talks


class EnglishTalkCacheTests(TestCase):
    """Use captured revision keys without repeating source requests."""

    @staticmethod
    def test_unchanged_keys_and_parser_changes_reuse_raw_text() -> None:
        """Fetch changed revisions without another SQL query."""
        site = OfflineSite("en", "wikipedia")
        pages = pl.DataFrame({
            "page_id": [1, 2],
            "en_class": ["GA", "B"],
            "en_reviews": [None, "GAN"],
            "talk_page_id": [101, 102],
            "talk_page_latest": [201, 202],
        })
        texts = {
            201: (101, "{{GA|5 September 2026}}"),
            202: (102, "{{GA nominee|6 September 2026}}"),
            203: (102, "{{GA nominee|7 September 2026}}"),
        }

        def fetch(_site: object, identifiers: list[int]) -> dict:
            """Return immutable fixture revisions for requested IDs."""
            return {
                oldid: RevisionContent(
                    texts[oldid][0],
                    "Talk:Fixture",
                    oldid,
                    dt.datetime(2026, 9, 8, tzinfo=dt.UTC),
                    "0" * 40,
                    texts[oldid][1],
                    None,
                )
                for oldid in identifiers
            }

        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                talks,
                "read_revision_contents",
                side_effect=fetch,
            ) as api,
            patch.object(
                talks,
                "fetch_en_talk_revisions",
                side_effect=AssertionError("Extra SQL"),
            ),
            patch.object(
                quality_listing_dates,
                "fetch_en_talk_revisions",
                side_effect=AssertionError("Extra SQL"),
            ),
            patch.object(
                quality_listing_dates,
                "template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch.object(
                reviews,
                "template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch.object(reviews.pywikibot, "Site", return_value=site),
        ):
            first = quality_listing_dates.listing_dates(
                pages,
                site=site,
                talk_observations=pages,
            )
            candidates = pages.filter(pl.col("en_reviews").is_not_null())
            start = reviews.review_dates(candidates, talk_observations=pages)
            assert first["quality_date"].to_list() == [dt.date(2026, 9, 5)]
            assert start["review_date"].to_list() == [dt.date(2026, 9, 6)]
            quality_listing_dates.listing_dates(
                pages,
                site=site,
                talk_observations=pages,
            )
            reviews.review_dates(candidates, talk_observations=pages)
            # A changed parser reuses the same raw revisions.
            with patch.object(
                reviews,
                "template_aliases",
                side_effect=lambda _site, title: (title, "Template:Alias"),
            ):
                reviews.review_dates(candidates, talk_observations=pages)
            changed = pages.with_columns(
                pl
                .when(pl.col("page_id") == 2)
                .then(203)
                .otherwise(pl.col("talk_page_latest"))
                .alias("talk_page_latest"),
            )
            updated = reviews.review_dates(
                candidates,
                talk_observations=changed,
            )
            assert updated["review_date"].to_list() == [dt.date(2026, 9, 7)]
            assert [call.args[1] for call in api.call_args_list] == [
                [201],
                [202],
                [203],
            ]
            assert enwiki.REVIEWS.read(enwiki.review_path())[
                "oldid"
            ].to_list() == [203]
            assert all(path.is_file() for path in Path("data/raw").iterdir())
