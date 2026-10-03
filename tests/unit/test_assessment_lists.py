"""Verify assessment content, anchors, and job publication offline."""

# Preserve intentional Chinese report punctuation in expected values.
# ruff: file-ignore[ambiguous-unicode-character-string, private-member-access]
# ruff: file-ignore[pytest-unittest-raises-assertion]

from contextlib import nullcontext
from dataclasses import fields
from unittest import TestCase
from unittest.mock import MagicMock, patch

import polars as pl

from aranami.jobs import assessment_lists as job
from aranami.services.zhwiki import assessment_lists as service

_TEXT = (
    'Intro<section begin="count" />1<section end="count" />'
    "\n<section begin=list />\n# {{icon|A}} [[Old]]\n<section end=list />"
    "\nFooter [[Unmanaged]]"
)
_A_CONFIG = service.AssessmentList(
    "Category:甲级电子游戏条目",
    icon_template="A",
)
_BPLUS_CONFIG = service.AssessmentList(
    "Category:乙上级电子游戏条目",
    icon_template="Bplus",
)
_ACC_CONFIG = service.AssessmentList(
    "Category:请求甲级评审的电子游戏条目",
    review_grade="acc",
)


class TestAssessmentContent(TestCase):
    """Verify managed sections without any publication destination."""

    @staticmethod
    def test_update_preserves_unmanaged_text() -> None:
        """Return text while retaining the surrounding page content."""
        text = service.update_text(
            _A_CONFIG,
            _TEXT,
            ["New", "Another", "New"],
            {},
        )
        assert isinstance(text, str)
        assert text.startswith('Intro<section begin="count" />')
        assert '<!-- aranami begin="assessment-count" -->2<!--' in text
        assert text.endswith("Footer [[Unmanaged]]")
        assert text.index("[[Another]]") < text.index("[[New]]")
        assert service.article_titles(text) == {"Another", "New"}
        assert service.article_titles(_TEXT) == {"Old"}

    @staticmethod
    def test_content_configuration_has_no_destination() -> None:
        """Keep content configuration independent of output pages."""
        assert {field.name for field in fields(service.AssessmentList)} == {
            "category_title",
            "icon_template",
            "review_grade",
        }

    @staticmethod
    def test_empty_category_clears_stale_rows() -> None:
        """Render an explicit empty list and reset the article count."""
        text = service.update_text(_BPLUS_CONFIG, _TEXT, [], {})
        assert "# {{icon|Bplus}} （無）" in text
        assert '<!-- aranami begin="assessment-count" -->0<!--' in text
        assert "[[Old]]" not in text
        assert service.article_titles(text) == set()

    @staticmethod
    def test_candidate_link_preserves_latest_review_anchor() -> None:
        """Render candidate icons and review-section links."""
        text = service.update_text(
            _ACC_CONFIG,
            _TEXT,
            ["Game"],
            {"Game": "Talk:Game#甲级评审 2"},
        )
        assert "{{icon3|A candidate.svg}} [[Game]]" in text
        assert "<small>〔[[Talk:Game#甲级评审 2|评审]]〕</small>" in text
        assert service.article_titles(text) == {"Game"}

    def test_missing_or_duplicate_section_boundaries_reject_updates(
        self,
    ) -> None:
        """Reject missing or ambiguous managed sections."""
        for text in ("No sections", _TEXT + "<section begin=list />"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                service.update_text(_BPLUS_CONFIG, text, [], {})


class TestReviewSources(TestCase):
    """Verify review matching and replica-based member selection."""

    @staticmethod
    def test_latest_matching_heading_and_grade_alias() -> None:
        """Match Chinese and English review headings."""
        text = (
            "== A-Class review ==\n== 甲級評審 2 ==\n"
            "=== 甲级评审 wrong level ===\n== Unrelated ==\n"
        )
        assert service.review_heading(text, "acc") == "甲級評審 2"
        assert service.review_heading("== B-Class assessment ==", "bpcn") == (
            "B-Class assessment"
        )
        assert service.review_heading("No review", "ppr") is None

    @staticmethod
    def test_category_uses_replica_and_preloaded_talk_pages() -> None:
        """Normalize titles and resolve review links in batches."""
        site = MagicMock()
        members = pl.DataFrame({"page_title": ["Video_Game", "Other"]})
        other = MagicMock(text="No review")
        other.title.return_value = "Talk:Other"
        game = MagicMock(text="== 甲级评审 ==")
        game.title.return_value = "Talk:Video Game"
        subjects = [MagicMock(), MagicMock()]
        subjects[
            0
        ].toggleTalkPage.return_value.title.return_value = "Talk:Other"
        subjects[
            1
        ].toggleTalkPage.return_value.title.return_value = "Talk:Video Game"
        with (
            patch.object(
                service,
                "category_members",
                return_value=members,
            ) as query,
            patch.object(
                service,
                "read_pages",
                return_value=[other, game],
            ) as read,
            patch.object(service, "Page", side_effect=subjects),
        ):
            text = service.prepare_text(site, _ACC_CONFIG, _TEXT)
        query.assert_called_once_with(
            site,
            _ACC_CONFIG.category_title,
            namespace=1,
        )
        read.assert_called_once_with(site, ["Talk:Other", "Talk:Video Game"])
        assert isinstance(text, str)
        assert "[[Talk:Other|评审]]" in text
        assert "[[Talk:Video Game#甲级评审|评审]]" in text

    @staticmethod
    def test_marker_category_override_survives_repeated_updates() -> None:
        """Retain category settings inside labeled sections."""
        text = _TEXT.replace(
            "<section begin=list />",
            '<section begin=list /><!-- aranami begin="assessment-list" '
            'category="分类:自定义电子游戏条目" -->',
        ).replace(
            "<section end=list />",
            '<!-- aranami end="assessment-list" --><section end=list />',
        )
        site = MagicMock()
        empty = pl.DataFrame(schema={"page_title": pl.String})
        with patch.object(
            service,
            "category_members",
            return_value=empty,
        ) as query:
            first = service.prepare_text(site, _BPLUS_CONFIG, text)
            second = service.prepare_text(site, _BPLUS_CONFIG, first)
        query.assert_called_with(site, "分类:自定义电子游戏条目", namespace=1)
        assert 'category="分类:自定义电子游戏条目"' in first
        assert first == second


class TestAssessmentJob(TestCase):
    """Verify job targets, metadata, and independent failures."""

    @staticmethod
    def test_custom_destination_and_content_are_passed_to_job() -> None:
        """Publish content to the destination selected by the job."""
        context = MagicMock()
        text = service.update_text(
            _A_CONFIG,
            _TEXT,
            ["New", "Another"],
            {},
        )
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=_TEXT)],
            ) as read,
            patch.object(job, "prepare_text", return_value=text) as prepare,
        ):
            job.run(
                lists=(("User:Example/Custom report", _A_CONFIG),),
                context=context,
            )
        read.assert_called_once_with(
            context.site,
            ["User:Example/Custom report"],
        )
        prepare.assert_called_once_with(context.site, _A_CONFIG, _TEXT)
        edit = context.publish.call_args.args[0]
        assert edit.site is context.site
        assert edit.title == "User:Example/Custom report"
        assert edit.text == text
        assert edit.original_text == _TEXT
        assert edit.tags == ("assessment-lists",)
        assert edit.summary == (
            "added [[Another]], [[New]]; removed [[Old]]; current 2 articles"
        )

    @staticmethod
    def test_empty_category_summary_describes_removed_articles() -> None:
        """Derive removals from original and returned content."""
        text = service.update_text(_BPLUS_CONFIG, _TEXT, [], {})
        assert job._content_summary(_TEXT, text) == (
            "removed [[Old]]; current 0 articles"
        )

    @staticmethod
    def test_long_multibyte_titles_fit_summary_limit() -> None:
        """Fall back to change counts if even one title is too long."""
        summary = job._edit_summary(["遊戲" * 150], ["旧" * 200], 1)
        assert len(summary.encode("utf-8")) <= job._MAX_SUMMARY_BYTES
        assert "current 1 article" in summary
        assert "added +1 more" in summary

    def test_failure_continues_other_targets(self) -> None:
        """Attempt all targets before reporting failures."""
        context = MagicMock()
        pages = [MagicMock(text=_TEXT) for _ in job.ASSESSMENT_LISTS]
        outputs = [
            RuntimeError("one list failed"),
            *[_TEXT for _ in pages[1:]],
        ]
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(job, "read_pages", return_value=pages),
            patch.object(job, "prepare_text", side_effect=outputs) as prepare,
            self.assertLogs(job.logger, level="INFO"),
            self.assertRaises(ExceptionGroup),
        ):
            job.run(context=context)
        assert prepare.call_count == len(job.ASSESSMENT_LISTS)
        assert context.publish.call_count == len(job.ASSESSMENT_LISTS) - 1
