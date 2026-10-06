"""Verify zhwiki PexBot configuration and delegated refreshes."""

# Retain the standard unittest runner and inspect the transport seam.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
import json
from contextlib import nullcontext
from io import BytesIO
from typing import override
from unittest import TestCase
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs, urlparse

import polars as pl
from pywikibot.site import BaseSite, Namespace

from aranami.jobs import JobContext, pexbot as job
from aranami.services.zhwiki import pexbot as service


class _OfflineSite(BaseSite):
    """Supply deterministic title normalization without API calls."""

    @override
    def dbName(self) -> str:
        """Return the Chinese Wikipedia database identifier.

        Returns:
            The database name supplied by a real Chinese Wikipedia site.
        """
        return "zhwiki"

    @staticmethod
    def _build_namespaces() -> dict[int, Namespace]:
        """Build localized project namespace metadata.

        Returns:
            Built-in namespaces with a Chinese Wikipedia alias.
        """
        namespaces = Namespace.builtin_namespaces()
        namespaces[Namespace.PROJECT] = Namespace(
            Namespace.PROJECT,
            canonical_name="Wikipedia",
            custom_name="维基百科",
            aliases=["WP"],
            case="first-letter",
        )
        return namespaces

    @staticmethod
    def encodings() -> tuple[str, ...]:
        """Return the local title encoding.

        Returns:
            UTF-8 as the only encoding to attempt.
        """
        return ("utf-8",)

    def namespace(
        self,
        number: int,
        *,
        all_ns: bool = False,
    ) -> str | Namespace:
        """Return the localized namespace name or metadata.

        Args:
            number: Namespace identifier to resolve.
            all_ns: Whether to return metadata instead of its name.

        Returns:
            Preferred namespace name or its complete metadata.
        """
        namespace = self.namespaces[number]
        return namespace if all_ns else namespace[0]


def _site(database: str = "zhwiki") -> Mock:
    """Build a minimal site identity for job transport tests.

    Args:
        database: Wiki database identifier accepted by the site guard.

    Returns:
        Mock site whose database name is explicitly configured.
    """
    site = Mock()
    site.dbName.return_value = database
    return site


class TestSubscriptionScopes(TestCase):
    """Verify configurable roots and site-aware namespace boundaries."""

    @staticmethod
    def test_multiple_prefixes_normalize_aliases_and_match_boundaries() -> (
        None
    ):
        """Select exact roots and subpages while excluding siblings."""
        site = _OfflineSite("zh", "wikipedia")
        members = pl.DataFrame({
            "page_namespace": [4, 4, 4, 4, 2, 2],
            "page_title": [
                "Report_root",
                "Report_root/One",
                "Report_rootTwo",
                "Unselected",
                "Other/Child",
                "Other",
            ],
        })
        with patch.object(service, "category_members", return_value=members):
            titles = service.subscribed_titles(
                site,
                ["WP:Report root", "User:Other/"],
            )
        assert titles == [
            "User:Other/Child",
            "维基百科:Report root",
            "维基百科:Report root/One",
        ]

    @staticmethod
    def test_empty_prefixes_disable_selection_without_querying() -> None:
        """Treat an empty allowed list as disabled PexBot work."""
        with (
            patch.object(service, "category_members") as query,
            patch.object(service.pywikibot, "Page") as page,
        ):
            assert service.subscribed_titles(_site(), []) == []
        query.assert_not_called()
        page.assert_not_called()

    def test_other_wikis_fail_before_category_query(self) -> None:
        """Reject English Wikipedia and Chinese sister projects."""
        for database in ("enwiki", "zhwikisource"):
            with (
                self.subTest(database=database),
                patch.object(service, "category_members") as query,
                self.assertRaises(ValueError),
            ):
                service.subscribed_titles(_site(database), ["Wikipedia:X"])
            query.assert_not_called()

    def test_empty_scope_and_bare_string_are_rejected(self) -> None:
        """Reject malformed scopes before querying members."""
        with patch.object(service, "category_members") as query:
            with self.assertRaises(ValueError):
                service.subscribed_titles(_site(), [" "])
            with self.assertRaises(TypeError):
                service.subscribed_titles(_site(), "Wikipedia:Report")
        query.assert_not_called()


class TestPexbotEvents(TestCase):
    """Verify event decoding and explicit completion requirements."""

    @staticmethod
    def test_nondata_lines_and_progress_arguments() -> None:
        """Ignore SSE framing and preserve valid progress data."""
        assert service.parse_event(": heartbeat") is None
        assert service.parse_event("event: message") is None
        assert service.parse_event("data:") is None
        assert service.parse_event(
            'data: {"code":"step","args":["中文",2]}',
        ) == (service.StreamEvent("step", ("中文", 2)))
        assert service.parse_event('data: {"code":"step","args":"one"}') == (
            service.StreamEvent("step", ("one",))
        )

    def test_invalid_event_payloads_are_rejected(self) -> None:
        """Do not interpret invalid server data as completed work."""
        for line in ("data: []", 'data: {"code":1}'):
            with self.subTest(line=line), self.assertRaises(TypeError):
                service.parse_event(line)
        with self.assertRaises(json.JSONDecodeError):
            service.parse_event("data: not json")

    @staticmethod
    def test_refresh_encodes_title_and_requires_successful_end() -> None:
        """Send one scoped refresh and close its completed response."""
        response = BytesIO(b'data: {"code":"step"}\n\ndata: {"code":"end"}\n')
        with patch.object(
            job.urllib.request,
            "urlopen",
            return_value=response,
        ) as request:
            job._refresh(_site(), "Wikipedia:Report name/C++")
        prepared = request.call_args.args[0]
        parsed = urlparse(prepared.full_url)
        assert parsed.netloc == "pexbot.toolforge.org"
        assert parsed.path == "/database-report/stream"
        assert parse_qs(parsed.query) == {
            "page": ["Wikipedia:Report_name/C++"],
        }
        assert prepared.get_header("Accept") == "text/event-stream"
        assert response.closed

    def test_errors_and_incomplete_streams_fail(self) -> None:
        """Reject errors and streams missing the completion event."""
        for body in (
            b'data: {"code":"error-query"}\ndata: {"code":"end"}\n',
            b'data: {"code":"step"}\n',
        ):
            response = BytesIO(body)
            with (
                self.subTest(body=body),
                patch.object(
                    job.urllib.request,
                    "urlopen",
                    return_value=response,
                ),
                self.assertRaises(RuntimeError),
            ):
                job._refresh(_site(), "Wikipedia:Report")
            assert response.closed

    def test_refresh_logs_only_present_event_arguments(self) -> None:
        """Omit empty brackets and preserve progress details."""
        response = BytesIO(
            b'data: {"code":"shutoff-checked"}\n'
            b'data: {"code":"started","args":[]}\n'
            b'data: {"code":"step","args":["queries",2]}\n'
            b'data: {"code":"end"}\n',
        )
        with (
            patch.object(job.urllib.request, "urlopen", return_value=response),
            self.assertLogs(job._LOGGER, level="INFO") as logs,
        ):
            job._refresh(_site(), "Wikipedia:Report")
        assert [record.getMessage() for record in logs.records] == [
            "PexBot Wikipedia:Report: shutoff-checked.",
            "PexBot Wikipedia:Report: started.",
            "PexBot Wikipedia:Report: step ('queries', 2).",
            "PexBot Wikipedia:Report: end.",
        ]

    def test_refresh_rejects_other_wiki_without_request(self) -> None:
        """Validate the site at the publication transport boundary."""
        with (
            patch.object(job.urllib.request, "urlopen") as request,
            self.assertRaises(ValueError),
        ):
            job._refresh(_site("enwiki"), "Wikipedia:Report")
        request.assert_not_called()


class TestPexbotJob(TestCase):
    """Verify configuration, overrides, dry runs, and failures."""

    @staticmethod
    def test_dry_run_reads_wheel_defaults_without_refreshing() -> None:
        """Use configured defaults and record proposed actions."""
        context = JobContext(_site(), dt.date(2026, 10, 3), dry=True)
        configured = ("Wikipedia:Custom", "User:Another/")
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(job, "PEXBOT_PREFIXES", configured),
            patch.object(
                job,
                "subscribed_titles",
                return_value=["Target", "Another target"],
            ) as select,
            patch.object(job, "_refresh") as refresh,
            patch.object(job.time, "sleep") as pause,
        ):
            job.run(context=context)
        select.assert_called_once_with(context.site, configured)
        refresh.assert_not_called()
        pause.assert_not_called()
        assert "Target" in context.notes[0]
        assert "Another target" in context.notes[1]

    @staticmethod
    def test_explicit_prefix_override_including_disable() -> None:
        """Honor an explicit empty list instead of wheel defaults."""
        context = JobContext(_site(), dt.date(2026, 10, 3), dry=False)
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(job, "_refresh") as refresh,
            patch.object(job.time, "sleep") as pause,
            patch.object(service, "category_members") as query,
        ):
            job.run(context=context, prefixes=[])
        refresh.assert_not_called()
        pause.assert_not_called()
        query.assert_not_called()

    @staticmethod
    def test_live_refreshes_pause_between_completed_requests() -> None:
        """Pause after completion before each next request only."""
        context = JobContext(_site(), dt.date(2026, 10, 3), dry=False)
        sequence = Mock()
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "subscribed_titles",
                return_value=["A", "B", "C"],
            ),
            patch.object(job, "_refresh") as refresh,
            patch.object(job.time, "sleep") as pause,
        ):
            sequence.attach_mock(refresh, "refresh")
            sequence.attach_mock(pause, "pause")
            job.run(context=context, prefixes=["Wikipedia:Custom"])
        assert sequence.mock_calls == [
            call.refresh(context.site, "A"),
            call.pause(15),
            call.refresh(context.site, "B"),
            call.pause(15),
            call.refresh(context.site, "C"),
        ]

    def test_empty_and_single_report_batches_never_pause(self) -> None:
        """Avoid a cooldown when no subsequent refresh is selected."""
        context = JobContext(_site(), dt.date(2026, 10, 3), dry=False)
        for titles in ([], ["A"]):
            with (
                self.subTest(titles=titles),
                patch.object(
                    job,
                    "job_run",
                    return_value=nullcontext(context),
                ),
                patch.object(job, "subscribed_titles", return_value=titles),
                patch.object(job, "_refresh") as refresh,
                patch.object(job.time, "sleep") as pause,
            ):
                job.run(context=context, prefixes=["Wikipedia:Custom"])
            pause.assert_not_called()
            assert refresh.call_args_list == [
                call(context.site, title) for title in titles
            ]

    def test_failed_report_does_not_skip_remaining_reports(self) -> None:
        """Continue other refreshes and report aggregate failure."""
        context = JobContext(_site(), dt.date(2026, 10, 3), dry=False)
        sequence = Mock()
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(job, "subscribed_titles", return_value=["A", "B"]),
            patch.object(
                job,
                "_refresh",
                side_effect=[RuntimeError("failed"), None],
            ) as refresh,
            patch.object(job.time, "sleep") as pause,
            self.assertLogs(job._LOGGER, level="ERROR"),
            self.assertRaises(ExceptionGroup),
        ):
            sequence.attach_mock(refresh, "refresh")
            sequence.attach_mock(pause, "pause")
            job.run(context=context, prefixes=["Wikipedia:Custom"])
        assert sequence.mock_calls == [
            call.refresh(context.site, "A"),
            call.pause(15),
            call.refresh(context.site, "B"),
        ]
