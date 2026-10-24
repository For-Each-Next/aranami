"""Verify assessment content, anchors, and job publication offline."""

# Preserve intentional Chinese report punctuation in expected values.
# ruff: file-ignore[ambiguous-unicode-character-string, private-member-access]
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import nullcontext
from dataclasses import fields
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

import polars as pl

from backend.services.zhwiki import assessment_lists as source
from backend.support.report_membership import MembershipReport
from backend.support.report_status import read_report_status
from updater.jobs import JobContext, assessment_lists as job
from updater.services.zhwiki import assessment_lists as service
from updater.support.edit_summary import MAX_EDIT_SUMMARY_BYTES

_TEXT = (
    'Intro<section begin="count" /><!-- aranami begin="assessment-count" -->'
    '1<!-- aranami end="assessment-count" --><section end="count" />'
    '\n<section begin=list /><!-- aranami begin="assessment-list" -->'
    '\n# {{icon|A}} [[Old]]\n<!-- aranami end="assessment-list" -->'
    "<section end=list />"
    "\nFooter [[Unmanaged]]"
)
_A_CONFIG = source.AssessmentList(
    "Category:甲级电子游戏条目",
    icon_template="A",
)
_BPLUS_CONFIG = source.AssessmentList(
    "Category:乙上级电子游戏条目",
    icon_template="Bplus",
)
_ACC_CONFIG = source.AssessmentList(
    "Category:请求甲级评审的电子游戏条目",
    review_grade="acc",
)


def _render_source(
    site: object,
    config: source.AssessmentList,
    text: str,
) -> MembershipReport:
    """Render real backend data under the test's source mocks.

    Args:
        site: Offline wiki identity and source adapter input.
        config: Category and review configuration under test.
        text: Original destination text with managed ranges.

    Returns:
        Complete rendered report from real backend observations.
    """
    return service.prepare_report(
        source.prepare_data(site, config, text),
        text,
    )


def _source_data(  # ruff: ignore[too-many-arguments]
    members: dict[str, int | None],
    site: object,
    config: source.AssessmentList,
    text: str,
    *,
    assessments: pl.DataFrame | None = None,
    previous_members: dict[str, int | None] | None = None,
) -> source.AssessmentData:
    """Supply replica facts for real backend interpretation.

    Args:
        members: Current category members and their article IDs.
        site: Offline wiki identity passed through the publication job.
        config: Category and review configuration selected by the job.
        text: Current destination text interpreted by backend.
        assessments: Shared project assessment observations.
        previous_members: Cached IDs validated against current text.

    Returns:
        Real backend data prepared from deterministic source fixtures.
    """
    rows = pl.DataFrame(
        {
            "page_title": list(members),
            "article_page_id": list(members.values()),
        },
        schema={"page_title": pl.String, "article_page_id": pl.Int64},
    )

    def page(_site: object, title: str) -> MagicMock:
        """Resolve a deterministic talk title without external reads.

        Args:
            _site: Unused offline wiki identity.
            title: Article whose talk page is resolved.

        Returns:
            Offline article object exposing its talk-page title.
        """
        result = MagicMock()
        result.toggleTalkPage.return_value.title.return_value = f"Talk:{title}"
        return result

    def read(_site: object, titles: list[str]) -> list[MagicMock]:
        """Return current talk texts without review headings.

        Args:
            _site: Unused offline wiki identity.
            titles: Talk-page titles requested by backend.

        Returns:
            Preloaded empty talk texts with their exact source titles.
        """
        result = []
        for title in titles:
            item = MagicMock(text="")
            item.title.return_value = title
            result.append(item)
        return result

    with (
        patch.object(source, "category_members", return_value=rows),
        patch.object(source, "Page", side_effect=page),
        patch.object(source, "read_pages", side_effect=read),
    ):
        return source.prepare_data(
            site,
            config,
            text,
            assessments=assessments,
            previous_members=previous_members,
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
        assert source.article_titles(text) == {"Another", "New"}
        assert source.article_titles(_TEXT) == {"Old"}

    @staticmethod
    def test_member_ids_are_not_rendered_in_page_content() -> None:
        """Keep source IDs outside generated list wikitext."""
        text = service.update_text(
            _A_CONFIG,
            _TEXT,
            ["New"],
            {},
        )
        assert "aranami-member" not in text
        assert source.article_members(text) == {"New": None}
        annotated = text.replace(
            "[[New]]",
            '[[New]]<!-- aranami-member id="7458204" -->',
        )
        assert source.article_members(annotated) == {"New": None}

    @staticmethod
    def test_content_configuration_has_no_destination() -> None:
        """Keep content configuration independent of output pages."""
        assert {field.name for field in fields(source.AssessmentList)} == {
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
        assert source.article_titles(text) == set()

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
        assert source.article_titles(text) == {"Game"}

    def test_missing_or_duplicate_marker_boundaries_reject_updates(
        self,
    ) -> None:
        """Reject missing or ambiguous managed sections."""
        for text in (
            "No markers",
            (
                '<section begin="count" />1<section end="count" />'
                '<section begin="list" />old<section end="list" />'
            ),
            _TEXT + '<!-- aranami begin="assessment-list" -->',
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                service.update_text(_BPLUS_CONFIG, text, [], {})

    @staticmethod
    def test_section_wrappers_and_unmanaged_members_are_preserved() -> None:
        """Preserve transclusion tags and unmanaged list entries."""
        source_text = _TEXT.replace(
            '<!-- aranami begin="assessment-list" -->',
            '\n# [[Manual]]\n<!-- aranami begin="assessment-list" -->',
        )
        updated = service.update_text(_A_CONFIG, source_text, ["New"], {})
        assert "\n# [[Manual]]\n" in updated
        assert "<section begin=list />" in updated
        assert "<section end=list />" in updated
        assert source.article_titles(updated) == {"New"}


class TestReviewSources(TestCase):
    """Verify review matching and replica-based member selection."""

    @staticmethod
    def test_prepared_changes_and_rows_render_without_source_reads() -> None:
        """Keep source identities and assessments behind backend."""
        site = MagicMock()
        site.dbName.return_value = "zhwiki"
        assessments = pl.DataFrame({
            "page_id": [11, 12],
            "pa_class": ["A", "B"],
            "pa_importance": ["High", None],
        })
        data = _source_data(
            {"Renamed": 11, "Added": 12},
            site,
            _A_CONFIG,
            _TEXT,
            assessments=assessments,
            previous_members={"Old": 11, "Unmanaged": 99},
        )
        assert data.previous_members == {"Old": 11}
        assert data.preview_members == {"Old": 11}
        assert data.changes == source.AssessmentChanges(("Added",), (), 2)
        assert data.rows.sort("page_id").select(
            "page_id",
            "class",
            "importance",
        ).to_dicts() == [
            {"page_id": 11, "class": "A", "importance": "High"},
            {"page_id": 12, "class": "B", "importance": None},
        ]
        with (
            patch.object(source, "category_members") as members,
            patch.object(source, "read_pages") as read,
            patch.object(source, "Page") as page,
        ):
            report = service.prepare_report(data, _TEXT)
        members.assert_not_called()
        read.assert_not_called()
        page.assert_not_called()
        assert "[[Renamed]]" in report.text
        assert report.members == data.members
        assert report.result.tables["members"].equals(data.rows)

    @staticmethod
    def test_latest_matching_heading() -> None:
        """Match Chinese and English review headings."""
        text = (
            "== A-Class review ==\n== 甲級評審 2 ==\n"
            "=== 甲级评审 wrong level ===\n== Unrelated ==\n"
        )
        assert source.review_heading(text, "acc") == "甲級評審 2"
        assert source.review_heading("== B-Class assessment ==", "bpan") == (
            "B-Class assessment"
        )
        assert source.review_heading("No review", "ppr") is None

    @staticmethod
    def test_category_uses_replica_and_preloaded_talk_pages() -> None:
        """Normalize titles and resolve review links in batches."""
        site = MagicMock()
        members = pl.DataFrame(
            {
                "page_title": ["Video_Game", "Other"],
                "page_id": [11, 12],
                "article_page_id": [1, 2],
            },
        )
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
                source,
                "category_members",
                return_value=members,
            ) as query,
            patch.object(
                source,
                "read_pages",
                return_value=[other, game],
            ) as read,
            patch.object(source, "Page", side_effect=subjects),
        ):
            report = _render_source(site, _ACC_CONFIG, _TEXT)
        text = report.text
        query.assert_called_once_with(
            site,
            _ACC_CONFIG.category_title,
            namespace=1,
        )
        read.assert_called_once_with(site, ["Talk:Other", "Talk:Video Game"])
        assert isinstance(text, str)
        assert "[[Talk:Other|评审]]" in text
        assert "[[Talk:Video Game#甲级评审|评审]]" in text
        assert report.members == {"Other": 2, "Video Game": 1}
        assert source.article_members(text) == {
            "Other": None,
            "Video Game": None,
        }
        assert "aranami-member" not in text

    @staticmethod
    def test_marker_category_override_survives_repeated_updates() -> None:
        """Retain category settings inside labeled sections."""
        text = _TEXT.replace(
            '<!-- aranami begin="assessment-list" -->',
            '<!-- aranami begin="assessment-list" '
            'category="分类:自定义电子游戏条目" -->',
        )
        site = MagicMock()
        empty = pl.DataFrame(
            schema={
                "page_title": pl.String,
                "page_id": pl.Int64,
                "article_page_id": pl.Int64,
            },
        )
        with patch.object(
            source,
            "category_members",
            return_value=empty,
        ) as query:
            first = _render_source(site, _BPLUS_CONFIG, text).text
            second = _render_source(site, _BPLUS_CONFIG, first).text
        query.assert_called_with(site, "分类:自定义电子游戏条目", namespace=1)
        assert 'category="分类:自定义电子游戏条目"' in first
        assert first == second

    @staticmethod
    def test_talk_page_replacement_keeps_article_membership() -> None:
        """Retain article identity when its talk page changes."""
        site = MagicMock()
        original = pl.DataFrame(
            {
                "page_title": ["Old"],
                "page_id": [11],
                "article_page_id": [1],
            },
        )
        replacement = original.with_columns(pl.lit(12).alias("page_id"))
        with patch.object(
            source,
            "category_members",
            side_effect=[original, replacement],
        ):
            first = _render_source(site, _A_CONFIG, _TEXT)
            second = _render_source(site, _A_CONFIG, first.text)
        assert second.members == {"Old": 1}
        assert first == second
        assert (
            job._content_summary(
                first.text,
                second.text,
                previous_members=first.members,
                current_members=second.members,
            )
            == "1 item total."
        )


class TestAssessmentJob(TestCase):
    """Verify job targets, metadata, and independent failures."""

    def setUp(self) -> None:
        """Keep assessments and destination identities offline."""
        self.assessments = pl.DataFrame(
            schema={
                "page_id": pl.Int64,
                "pa_class": pl.String,
                "pa_importance": pl.String,
            },
        )
        self.enterContext(
            patch.object(
                job,
                "read_assessments",
                return_value=self.assessments,
            ),
        )
        self.enterContext(
            patch.object(job, "report_key", return_value="assessment-test"),
        )

    @staticmethod
    def test_custom_destination_and_content_are_passed_to_job() -> None:
        """Publish content to the destination selected by the job."""
        context = MagicMock()
        context.dry = False
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
            patch.object(
                job,
                "prepare_data",
                side_effect=partial(_source_data, {"New": 1, "Another": 2}),
            ) as collect,
            patch.object(
                job,
                "prepare_report",
                return_value=MembershipReport(text, {"New": 1, "Another": 2}),
            ) as prepare,
            patch.object(job, "load_membership", return_value={}),
            patch.object(job, "save_membership") as save,
        ):
            job.run(
                lists=(("User:Example/Custom report", _A_CONFIG),),
                context=context,
            )
        read.assert_called_once_with(
            context.site,
            ["User:Example/Custom report"],
        )
        assert prepare.call_count == 1
        assert collect.call_args.args == (context.site, _A_CONFIG, _TEXT)
        assert collect.call_args.kwargs["assessments"].columns == [
            "page_id",
            "pa_class",
            "pa_importance",
        ]
        edit = context.publish.call_args.args[0]
        assert edit.site is context.site
        assert edit.title == "User:Example/Custom report"
        assert edit.text == text
        assert edit.original_text == _TEXT
        assert edit.tags == ("assessment-lists",)
        assert edit.summary == (
            "2 items total. Added «[[Another]]», «[[New]]»; removed «[[Old]]»."
        )
        save.assert_called_once_with(
            context.site,
            "User:Example/Custom report",
            text,
            {"New": 1, "Another": 2},
        )

    @staticmethod
    def test_empty_category_summary_describes_removed_articles() -> None:
        """Derive removals from original and returned content."""
        text = service.update_text(_BPLUS_CONFIG, _TEXT, [], {})
        assert job._content_summary(_TEXT, text) == (
            "0 items total. Removed «[[Old]]»."
        )

    @staticmethod
    def test_long_multibyte_titles_fit_summary_limit() -> None:
        """Fall back to change counts if even one title is too long."""
        summary = job._edit_summary(["遊戲" * 150], ["旧" * 200], 1)
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert summary == "1 item total. Added 1 more; removed 1 more."

    @staticmethod
    def test_rename_and_review_anchor_changes_only_report_total() -> None:
        """Recognize the stable article page ID across a move."""
        original = service.update_text(
            _ACC_CONFIG,
            _TEXT,
            ["Old"],
            {"Old": "Talk:Old#甲级评审"},
        )
        updated = service.update_text(
            _ACC_CONFIG,
            original,
            ["Renamed"],
            {"Renamed": "Talk:Renamed#甲级评审 2"},
        )
        assert (
            job._content_summary(
                original,
                updated,
                previous_members={"Old": 11},
                current_members={"Renamed": 11},
            )
            == "1 item total."
        )
        assert source.article_members(updated) == {"Renamed": None}
        assert "aranami-member" not in updated

    @staticmethod
    def test_uncached_unchanged_titles_only_report_total() -> None:
        """Compare unchanged titles when cached IDs are unavailable."""
        updated = service.update_text(
            _A_CONFIG,
            _TEXT,
            ["Old"],
            {},
        )
        assert (
            job._content_summary(
                _TEXT,
                updated,
                current_members={"Old": 11},
            )
            == "1 item total."
        )

    @staticmethod
    def test_recreated_title_reports_actual_replacement() -> None:
        """Distinguish a replacement using its changed source ID."""
        original = service.update_text(
            _A_CONFIG,
            _TEXT,
            ["Old"],
            {},
        )
        updated = service.update_text(
            _A_CONFIG,
            original,
            ["Old"],
            {},
        )
        assert job._content_summary(
            original,
            updated,
            previous_members={"Old": 11},
            current_members={"Old": 12},
        ) == ("1 item total. Added «[[Old]]»; removed «[[Old]]».")

    @staticmethod
    def test_unchanged_list_uses_comma_separated_total() -> None:
        """Format large totals without repeating the page's purpose."""
        assert job._edit_summary([], [], 1234) == "1,234 items total."

    def test_failure_continues_other_targets(self) -> None:
        """Attempt all targets before reporting failures."""
        context = MagicMock()
        context.dry = False
        pages = [MagicMock(text=_TEXT) for _ in job.ASSESSMENT_LISTS]
        outputs = [
            RuntimeError("one list failed"),
            *[MembershipReport(_TEXT, {"Old": 1}) for _ in pages[1:]],
        ]
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(job, "read_pages", return_value=pages),
            patch.object(
                job,
                "prepare_data",
                side_effect=partial(_source_data, {"Old": 1}),
            ),
            patch.object(
                job,
                "prepare_report",
                side_effect=outputs,
            ) as prepare,
            patch.object(job, "load_membership", return_value={}),
            patch.object(job, "save_membership") as save,
            self.assertLogs(job.logger, level="INFO"),
            self.assertRaises(ExceptionGroup),
        ):
            job.run(context=context)
        assert prepare.call_count == len(job.ASSESSMENT_LISTS)
        assert context.publish.call_count == len(job.ASSESSMENT_LISTS) - 1
        assert save.call_count == len(job.ASSESSMENT_LISTS) - 1

    def test_each_list_measures_its_own_preparation_and_failure(self) -> None:
        """Measure each list independently of shared reads."""
        elapsed = [0.0]
        started = dt.datetime(2026, 10, 24, tzinfo=dt.UTC)
        site = MagicMock()
        site.dbName.return_value = "zhwiki"
        context = JobContext(site, started.date(), dry=False)

        def preload(*_args: object) -> list[MagicMock]:
            """Spend ten seconds on the shared destination preload.

            Returns:
                The two destinations' existing contents.
            """
            elapsed[0] += 10
            return [MagicMock(text=_TEXT), MagicMock(text=_TEXT)]

        def prepare(
            site: object,
            config: source.AssessmentList,
            text: str,
            **kwargs: object,
        ) -> source.AssessmentData:
            """Spend a distinct duration on each list's source read.

            Returns:
                Prepared members for the first destination.

            Raises:
                RuntimeError: The second destination's source is down.
            """
            if config == _A_CONFIG:
                elapsed[0] += 2
                return _source_data({"Old": 1}, site, config, text, **kwargs)
            elapsed[0] += 3
            message = "The second list source failed."
            raise RuntimeError(message)

        with (
            TemporaryDirectory() as temporary,
            patch("pathlib.Path.cwd", return_value=Path(temporary)),
            patch(
                "backend.support.execution.perf_counter",
                side_effect=lambda: elapsed[0],
            ),
            patch(
                "backend.support.report_status.perf_counter",
                side_effect=lambda: elapsed[0],
            ),
            patch(
                "backend.support.report_status._utc_now",
                side_effect=lambda: started + dt.timedelta(seconds=elapsed[0]),
            ),
            patch.object(job, "read_pages", side_effect=preload),
            patch.object(job, "prepare_data", side_effect=prepare),
            patch.object(
                job,
                "prepare_report",
                return_value=MembershipReport(_TEXT, {"Old": 1}),
            ),
            patch.object(job, "load_membership", return_value={}),
            patch.object(job, "save_membership"),
            self.assertLogs(job.logger, level="ERROR"),
            self.assertLogs("backend.support.execution", level="INFO"),
        ):
            with self.assertRaises(ExceptionGroup):
                job.run(
                    lists=(("First", _A_CONFIG), ("Second", _ACC_CONFIG)),
                    context=context,
                )
            records = {
                record["title"]: record
                for record in read_report_status(
                    Path(temporary) / "state" / "report-updates.json",
                )["reports"].values()
            }
        assert records["First"]["status"] == "no_update"
        assert [
            records[title]["duration_seconds"] for title in ("First", "Second")
        ] == [2, 3]
        assert records["First"]["started_at"] == "2026-10-24T00:00:10.000000Z"
        assert records["Second"]["status"] == "failed"
        assert records["Second"]["started_at"] == "2026-10-24T00:00:12.000000Z"
        assert (
            records["Second"]["finished_at"] == "2026-10-24T00:00:15.000000Z"
        )
        expected_total = 15
        assert context.tasks[0].elapsed_seconds == expected_total

    def test_cached_ids_suppress_rename_and_save_only_after_publication(
        self,
    ) -> None:
        """Save matching identities after successful publication."""
        original = _TEXT.replace(
            "# {{icon|A}} [[Old]]",
            "# {{icon|A}} [[Old]]\n# {{icon|A}} [[Removed]]",
        )
        text = service.update_text(
            _A_CONFIG,
            original,
            ["Renamed", "Added"],
            {},
        )
        report = MembershipReport(text, {"Renamed": 11, "Added": 13})
        for dry, failed in (
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ):
            with self.subTest(dry=dry, failed=failed):
                context = MagicMock(dry=dry)
                if failed:
                    context.publish.side_effect = RuntimeError(
                        "publication failed",
                    )
                calls = MagicMock()
                calls.attach_mock(context.publish, "publish")
                with (
                    patch.object(
                        job,
                        "job_run",
                        return_value=nullcontext(context),
                    ),
                    patch.object(
                        job,
                        "read_pages",
                        return_value=[MagicMock(text=original)],
                    ),
                    patch.object(
                        job,
                        "prepare_data",
                        side_effect=partial(
                            _source_data,
                            dict(report.members),
                        ),
                    ),
                    patch.object(job, "prepare_report", return_value=report),
                    patch.object(
                        job,
                        "load_membership",
                        return_value={"Old": 11, "Removed": 12},
                    ),
                    patch.object(job, "save_membership") as save,
                    self.assertLogs(job.logger, level="INFO"),
                ):
                    calls.attach_mock(save, "save")
                    expectation = (
                        self.assertRaises(ExceptionGroup)
                        if failed
                        else nullcontext()
                    )
                    with expectation:
                        job.run(
                            lists=(("Report", _A_CONFIG),),
                            context=context,
                        )
                assert context.publish.call_args.args[0].summary == (
                    "2 items total. Added «[[Added]]»; removed «[[Removed]]»."
                )
                if failed:
                    save.assert_not_called()
                    assert [call[0] for call in calls.mock_calls] == [
                        "publish",
                    ]
                else:
                    save.assert_called_once_with(
                        context.site,
                        "Report",
                        original if dry else text,
                        {"Old": 11, "Removed": 12} if dry else report.members,
                    )
                    assert [call[0] for call in calls.mock_calls] == [
                        "publish",
                        "save",
                    ]

    @staticmethod
    def test_repeated_dry_runs_retain_the_original_membership() -> None:
        """Keep additions visible while hydrating original titles."""
        context = MagicMock(dry=True)
        context.site.dbName.return_value = "zhwiki"
        text = service.update_text(_A_CONFIG, _TEXT, ["Old", "New"], {})
        report = MembershipReport(text, {"Old": 11, "New": 12})
        previews = 2
        with (
            TemporaryDirectory() as directory,
            patch(
                "backend.support.report_membership.Path.cwd",
                return_value=Path(directory),
            ),
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=_TEXT)],
            ),
            patch.object(
                job,
                "prepare_data",
                side_effect=partial(_source_data, dict(report.members)),
            ),
            patch.object(
                job,
                "prepare_report",
                return_value=report,
            ) as prepare,
        ):
            for _ in range(previews):
                job.run(lists=(("Report", _A_CONFIG),), context=context)
            assert job.load_membership(context.site, "Report", _TEXT) == {
                "Old": 11,
            }
            assert job.load_membership(context.site, "Report", text) == {}
        assert prepare.call_count == previews
        summaries = [
            call.args[0].summary for call in context.publish.call_args_list
        ]
        assert summaries == ["2 items total. Added «[[New]]»."] * previews
