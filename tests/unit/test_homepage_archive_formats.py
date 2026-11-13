"""Verify audited historical homepage subjects and source provenance."""

# Fixtures preserve exact wiki title punctuation and explicit oldids.
# ruff: file-ignore[ambiguous-unicode-character-string, private-member-access]
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import Mock, patch

import mwparserfromhell
import polars as pl
from wiki_fixtures import OfflineSite

from data.articles import homepage_archive
from tasks.zhwiki import homepage_archives
from tasks.zhwiki.legacy.homepage_displays import introduction_subject

if TYPE_CHECKING:
    from pywikibot.site import Namespace

_NOW = dt.datetime(2026, 11, 9, 12, tzinfo=dt.UTC)
_FEATURED = homepage_archives._ROOTS["Wikipedia:典范条目/"][1]
_GOOD = homepage_archives._ROOTS["Wikipedia:优良条目/"][1]


class _ArchiveSite(OfflineSite):
    """Use the verified Chinese namespace and redirect aliases."""

    @staticmethod
    def _build_namespaces() -> dict[int, Namespace]:
        """Return aliases observed by the source audit."""
        namespaces = OfflineSite._build_namespaces()
        namespaces[4].aliases.extend(["維基百科", "专案", "專案"])
        namespaces[6].aliases.extend(["文件", "檔案", "图像", "圖像"])
        return namespaces

    @property
    def redirect_regex(self) -> re.Pattern[str]:
        """Recognize the redirect spellings used by dated sources."""
        return re.compile(
            r"\s*#(?:REDIRECT|重定向)\s*:?\s*\[\[(.+?)(?:\|.*?)?\]\]",
            re.IGNORECASE | re.DOTALL,
        )

    @staticmethod
    def interwiki(prefix: str) -> OfflineSite:
        """Keep title parsing local without network lookups.

        Raises:
            KeyError: The fixture has no interwiki destinations.
        """
        raise KeyError(prefix)


class HistoricalHomepageFormatTests(TestCase):
    """Identify a lead subject without interpreting incidental links."""

    def setUp(self) -> None:
        """Use real title normalization without source requests."""
        self.site = _ArchiveSite("zh", "wikipedia")

    def test_verified_legacy_featured_transclusion(self) -> None:
        """Read the former featured root used at oldid 52642498."""
        subject = homepage_archives._subject(
            mwparserfromhell.parse("{{Wikipedia:特色条目/我们能做到！}}"),
            _FEATURED,
            self.site,
        )
        assert subject == "我们能做到！"

    def test_verified_traditional_roots_and_namespace_alias(self) -> None:
        """Normalize spellings found in old daily archives."""
        for text, roots, expected in (
            ("{{Wikipedia:典範條目/清东陵}}", _FEATURED, "清东陵"),
            ("{{Wikipedia:優良條目/玉環市圖書館}}", _GOOD, "玉環市圖書館"),
            ("{{维基百科:优良条目/秀丽隐杆线虫}}", _GOOD, "秀丽隐杆线虫"),
            ("{{維基百科:特色條目/賈達德}}", _FEATURED, "賈達德"),
            ("{{Wikipedia:優良条目/晉國}}", _GOOD, "晉國"),
        ):
            with self.subTest(text=text):
                assert (
                    homepage_archives._subject(
                        mwparserfromhell.parse(text),
                        roots,
                        self.site,
                    )
                    == expected
                )

    def test_verified_summary_parameters_and_redirects(self) -> None:
        """Read the article parameter instead of s or 摘要."""
        for text in (
            "{{Wikipedia:特色条目/s|北海道}}",
            "{{維基百科:特色條目/s|北海道}}",
            "{{Wikipedia:優良條目/s|北海道}}",
            "{{Wikipedia:优良条目/摘要|北海道}}",
            "{{Wikipedia:特色条目/摘要|北海道}}",
            "#REDIRECT [[Wikipedia:优良条目/北海道]]",
            "#重定向 [[Wikipedia:优良条目/北海道]]",
        ):
            with self.subTest(text=text):
                assert (
                    homepage_archives._subject(
                        mwparserfromhell.parse(text),
                        _GOOD,
                        self.site,
                    )
                    == "北海道"
                )
        for text in (
            "{{Wikipedia:优良条目/s}}",
            "{{Wikipedia:优良条目/s|1=One|1=Two}}",
            "{{Wikipedia:特色列表/s|Unverified}}",
            "#REDIRECT [[Unverified]]",
            "{{Wikipedia:优良条目/One}}{{Wikipedia:优良条目/Two}}",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                homepage_archives._subject(
                    mwparserfromhell.parse(text),
                    _GOOD,
                    self.site,
                )

    def test_dated_source_reuse_rejects_cycles_and_unavailable_text(
        self,
    ) -> None:
        """Follow daily references without inventing article titles."""
        code = mwparserfromhell.parse("{{Wikipedia:优良条目/2010年2月1日}}")
        read = Mock(return_value="'''[[矮行星]]''' is a subject.")
        assert (
            homepage_archives._subject(
                code,
                _GOOD,
                self.site,
                read_daily=read,
            )
            == "矮行星"
        )
        read.assert_called_once_with("Wikipedia:优良条目/2010年2月1日")
        with self.assertRaisesRegex(ValueError, "unavailable or cyclic"):
            homepage_archives._subject(code, _GOOD, self.site)
        with self.assertRaisesRegex(ValueError, "unavailable or cyclic"):
            homepage_archives._subject(
                code,
                _GOOD,
                self.site,
                read_daily=Mock(return_value=str(code)),
            )

    def test_verified_lead_wrappers_and_line_boundary_bold(self) -> None:
        """Ignore conversion/media metadata and preserve bold leads."""
        for text, expected in (
            (
                "{{NoteTA|G1=Example}}'''[[Primary]]''' is a subject.",
                "Primary",
            ),
            ("{{Image|zh-hans=X.svg|zh-hant=Y.svg}}'''[[天下]]'''", "天下"),
            (
                (
                    "<div style='float:left'>[[文件:X.jpg|90px]]</div>"
                    "'''[[Primary]]'''"
                ),
                "Primary",
            ),
            (
                "[[File:X.jpg]]-{H|zh-hans:'''Hidden''';}-'''[[Primary]]'''",
                "Primary",
            ),
            ("[[File:X.jpg]]'''\n'''[[福州历史]]'''可以追溯。", "福州历史"),
            (
                "{{單雙書名號轉換|'''[[Jihad (歌曲)|Jihad]]'''}}是一首歌曲。",
                "Jihad (歌曲)",
            ),
            (
                "斯里兰卡的职位更名為'''[[斯里兰卡总理列表|斯里兰卡总理]]'''。",
                "斯里兰卡总理列表",
            ),
            (
                "'''克麗奧佩脫拉七世'''<!-- delinked bot test -->，",
                "克麗奧佩脫拉七世",
            ),
        ):
            with self.subTest(text=text):
                assert (
                    introduction_subject(
                        mwparserfromhell.parse(text),
                        self.site,
                    )
                    == expected
                )

    def test_initial_bold_subject_excludes_later_formula_links(self) -> None:
        """Preserve the water lead subject at oldid 19446787."""
        code = mwparserfromhell.parse(
            "[[File:Drinking water.jpg|90px|right]]\n"
            "'''[[水]]'''（'''[[氢|H]]<sub>2</sub>[[氧|O]]'''）"
            "是由[[氢]]、[[氧]]两种[[元素]]组成的。",
        )
        assert introduction_subject(code, self.site) == "水"

    def test_book_bracket_and_converted_label_keep_link_target(self) -> None:
        """Read oldid 8968653's bracketed subject and variant labels."""
        for text, expected in (
            ("《'''[[西遊記]]'''》是一部[[中国]]小说。", "西遊記"),
            (
                (
                    "'''[[科倫拜校園事件|-{zh-hans:哥伦拜恩;zh-hant:科倫拜}-"
                    "校園事件]]'''是在[[1999年]]发生的。"
                ),
                "科倫拜校園事件",
            ),
            ("[[Game_name#Lead|'''Game name''']] is an article.", "Game name"),
        ):
            with self.subTest(text=text):
                assert (
                    introduction_subject(
                        mwparserfromhell.parse(text),
                        self.site,
                    )
                    == expected
                )

    def test_image_captions_comments_and_noinclude_are_not_subjects(
        self,
    ) -> None:
        """Ignore non-lead bold links before the introduction."""
        code = mwparserfromhell.parse(
            "<!-- '''[[Comment]]''' -->"
            "<noinclude>'''[[Documentation]]'''</noinclude>"
            "[[Image:Picture.jpg|right|'''[[Caption]]''']]\n"
            "'''[[Primary]]''' has [[Incidental]] and '''[[Later]]''' links.",
        )
        assert introduction_subject(code, self.site) == "Primary"

    def test_arbitrary_links_and_ambiguous_leads_are_rejected(self) -> None:
        """Require one explicit local article in the bold lead."""
        for text in (
            "[[Incidental]] and '''[[Later]]''' are described here.",
            "Introductory prose mentions [[Unbolded]].",
            "'''[[One]] and [[Two]]''' are coequal subjects.",
            "'''[[Category:Example]]''' has a page.",
            "'''[[:File:Picture.jpg]]''' has a description.",
            "{{Unknown|'''[[Hidden]]'''}} '''[[Later]]'''",
            "[https://example.org/ '''Primary'''] [[Other]]",
            "[[File:Picture.jpg|'''[[Caption]]''']] [[Unbolded]]",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                introduction_subject(mwparserfromhell.parse(text), self.site)


class HistoricalHomepageAcquisitionTests(TestCase):
    """Keep source dates and oldids when historical formats are read."""

    def setUp(self) -> None:
        """Use a source cycle with resolved article identities."""
        self.read = Mock()
        self.read.site = _ArchiveSite("zh", "wikipedia")
        self.read.resolve.side_effect = lambda value: {"水": "Water"}.get(
            value,
            value,
        )
        self.read.resolve_page_id.side_effect = lambda value: {
            "我们能做到！": 10,
            "水": 20,
        }[value]

    @staticmethod
    def _page(title: str, text: str, revision: int) -> Mock:
        """Build a current source snapshot with explicit provenance.

        Returns:
            A preloaded page without any external requests.
        """
        page = Mock()
        page.title.return_value = title
        page.exists.return_value = True
        page.get.return_value = text
        page.latest_revision.revid = revision
        page.latest_revision.timestamp = _NOW - dt.timedelta(days=3)
        return page

    def test_initial_inventory_preserves_daily_source_identity(self) -> None:
        """Retain the date, exact oldid and canonical article."""
        featured = self._page(
            "Wikipedia:典范条目/2013年8月20日",
            "{{Wikipedia:特色条目/我们能做到！}}",
            52642498,
        )
        good = self._page(
            "Wikipedia:优良条目/2006年9月19日",
            "[[File:Drinking water.jpg|90px|right]]\n"
            "'''[[水]]'''（'''[[氢|H]]<sub>2</sub>[[氧|O]]'''）是水。",
            19446787,
        )
        with (
            patch.object(
                homepage_archives,
                "pages_with_prefix",
                side_effect=[
                    pl.DataFrame({"full_title": [featured.title()]}),
                    pl.DataFrame({"full_title": [good.title()]}),
                ],
            ),
            patch.object(
                homepage_archives,
                "read_pages",
                side_effect=[[featured], [good]],
            ),
        ):
            result = homepage_archives._acquire(
                self.read,
                _NOW.date() - dt.timedelta(days=1),
                None,
                _NOW,
            )
        assert result.select(
            "title",
            "page_id",
            "field",
            "date",
            "source_revision",
        ).to_dicts() == [
            {
                "title": "我们能做到！",
                "page_id": 10,
                "field": "maindateFA",
                "date": dt.date(2013, 8, 20),
                "source_revision": 52642498,
            },
            {
                "title": "Water",
                "page_id": 20,
                "field": "maindateGA",
                "date": dt.date(2006, 9, 19),
                "source_revision": 19446787,
            },
        ]
        assert result["source_title"].to_list() == [
            featured.title(),
            good.title(),
        ]
        assert result["observed_at"].to_list() == [_NOW, _NOW]

    def test_reused_day_keeps_its_date_revision_and_display_slot(self) -> None:
        """Keep the outer display evidence when reusing source text."""
        featured = self._page(
            "Wikipedia:典范条目/2013年11月29日",
            "{{Wikipedia:优良条目/水}}",
            52642405,
        )
        original = self._page(
            "Wikipedia:优良条目/2010年2月1日",
            "'''[[水]]''' is the primary subject.",
            86043386,
        )
        reused = self._page(
            "Wikipedia:优良条目/2012年7月29日",
            "{{Wikipedia:优良条目/2010年2月1日}}",
            22181271,
        )
        with (
            patch.object(
                homepage_archives,
                "pages_with_prefix",
                side_effect=[
                    pl.DataFrame({"full_title": [featured.title()]}),
                    pl.DataFrame({
                        "full_title": [original.title(), reused.title()],
                    }),
                ],
            ),
            patch.object(
                homepage_archives,
                "read_pages",
                side_effect=[[featured], [original, reused]],
            ) as preload,
        ):
            result = homepage_archives._acquire(
                self.read,
                _NOW.date(),
                None,
                _NOW,
            )
        assert result["title"].to_list() == ["Water", "Water", "Water"]
        assert result["field"].to_list() == [
            "maindateFA",
            "maindateGA",
            "maindateGA",
        ]
        assert result["date"].to_list() == [
            dt.date(2013, 11, 29),
            dt.date(2010, 2, 1),
            dt.date(2012, 7, 29),
        ]
        assert result["source_revision"].to_list() == [
            52642405,
            86043386,
            22181271,
        ]
        assert result["source_title"].to_list() == [
            featured.title(),
            original.title(),
            reused.title(),
        ]
        assert preload.call_args_list == [
            ((self.read.site, (featured.title(),)),),
            ((self.read.site, (original.title(), reused.title())),),
        ]

    def test_unknown_format_keeps_previous_complete_generation(self) -> None:
        """Withhold coverage and identify the exact failing source."""
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "homepage-archives.parquet"
            homepage_archive.refresh(
                path,
                _NOW.date() - dt.timedelta(days=2),
                lambda _covered: pl.DataFrame(schema=homepage_archive.SCHEMA),
            )
            before = path.read_bytes()
            page = self._page(
                "Wikipedia:典范条目/2026年11月8日",
                "[[Incidental]] with no assigned bold subject.",
                12345678,
            )
            with (
                patch.object(
                    homepage_archives,
                    "read_pages",
                    return_value=[page],
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "Wikipedia:典范条目/2026年11月8日 at revision 12345678",
                ),
            ):
                homepage_archives.collect(self.read, path, now=_NOW)
            assert path.read_bytes() == before
            assert homepage_archive.read(path)[1] == (
                _NOW.date() - dt.timedelta(days=2)
            )
