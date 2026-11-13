"""Verify batched Pywikibot reads use an isolated bounded transport."""

# SDK transport boundaries and precise request limits are the contract.
# ruff: file-ignore[private-member-access, magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import io
import json
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch
from urllib.parse import parse_qs, urlsplit

from pywikibot.data.api import ParamInfo, PropertyGenerator
from pywikibot.page import Revision
from pywikibot.site import Namespace, NamespacesDict

from shared.sources import redirect_scan as source


class _ClockEvent:
    """Advance request waits without real sleeps or network access."""

    def __init__(self) -> None:
        """Create a monotonic clock and unset cancellation signal."""
        self.now = 0.0
        self.stopped = False

    def wait(self, delay: float) -> bool:
        """Advance the fake clock and report cancellation.

        Returns:
            Whether the owner stopped this request.
        """
        self.now += delay
        return self.stopped

    def is_set(self) -> bool:
        """Read the test owner's cancellation flag.

        Returns:
            Whether the test owner requested cancellation.
        """
        return self.stopped


class RedirectPreloadingTests(TestCase):
    """Keep batch identity and historical evidence exact."""

    def setUp(self) -> None:
        """Prepare an inert client with a fake monotonic clock."""
        self.stop = _ClockEvent()
        self.admit = Mock()
        self.client = source.ScanClient(
            self.stop,
            clock=lambda: self.stop.now,
            before_request=self.admit,
        )
        self.client.opener = Mock()

    def test_sdk_site_is_inert_and_preserves_native_response_format(
        self,
    ) -> None:
        """Use a dedicated site and gate each SDK source attempt."""
        site = self.client.site
        assert isinstance(site, source._ScanSite)
        self.client.opener.open.assert_not_called()
        self.client.opener.open.return_value = io.BytesIO(
            b'{"query":{"pages":{"2":{"pageid":2}}}}',
        )
        request = site.simple_request(action="query", pageids=2, prop="info")
        result = request.submit()
        assert result["query"]["pages"]["2"]["pageid"] == 2
        self.admit.assert_called_once_with()
        url = self.client.opener.open.call_args.args[0].full_url
        parameters = parse_qs(urlsplit(url).query)
        assert "formatversion" not in parameters
        assert parameters["maxlag"] == ["5"]
        assert parameters["format"] == ["json"]

    def test_sdk_paraminfo_is_read_only_and_does_not_cache_source_response(
        self,
    ) -> None:
        """Permit SDK metadata reads without accepting write actions."""
        site = self.client.site
        self.client.opener.open.return_value = io.BytesIO(
            b'{"paraminfo":{"modules":[]}}',
        )
        request = site._request(
            parameters={"action": "paraminfo", "modules": "query+info"},
            expiry=1,
        )
        assert request.submit() == {"paraminfo": {"modules": []}}
        url = self.client.opener.open.call_args.args[0].full_url
        assert "expiry" not in parse_qs(urlsplit(url).query)
        with self.assertRaises(ValueError):
            site.simple_request(action="edit", title="Example", text="bad")
        assert self.client.opener.open.call_count == 1

    def test_sdk_requests_cannot_bypass_cancellation(self) -> None:
        """Cancel before the SDK can open a source socket."""
        request = self.client.site.simple_request(action="query", pageids=2)
        self.stop.stopped = True
        with self.assertRaises(source.ScanStoppedError):
            request.submit()
        self.client.opener.open.assert_not_called()

    def test_native_property_generator_selects_transport_before_action(
        self,
    ) -> None:
        """Support late SDK action selection."""
        site = self.client.site
        metadata = Mock()
        metadata.parameter.return_value = None
        self.client.opener.open.return_value = io.BytesIO(
            b'{"batchcomplete":"","query":{"pageids":["2"],'
            b'"pages":{"2":{"pageid":2,"ns":1,"title":"Talk:Moved"}}}}',
        )
        with (
            patch.object(site, "_paraminfo", metadata),
            patch.object(
                type(site),
                "mw_version",
                new_callable=PropertyMock,
                return_value="1.45",
            ),
        ):
            pages = list(
                PropertyGenerator(
                    "info",
                    site=site,
                    parameters={"pageids": [2]},
                ),
            )
        assert pages == [{"pageid": 2, "ns": 1, "title": "Talk:Moved"}]
        self.admit.assert_called_once_with()

    def test_preload_uses_stable_ids_and_fifty_page_groups(self) -> None:
        """Retain moved identities and allow omitted deleted IDs."""
        moved = Mock(pageid=2)
        moved.title.return_value = "Talk:Renamed"
        selected = [moved]
        with (
            patch.object(
                source.pagegenerators,
                "PagesFromPageidGenerator",
                return_value=selected,
            ) as identify,
            patch.object(
                source.pagegenerators,
                "PreloadingGenerator",
                return_value=[moved],
            ) as preload,
        ):
            loaded = self.client.preload([2, 3])
        assert loaded == {2: moved}
        identify.assert_called_once_with((2, 3), site=self.client.site)
        preload.assert_called_once_with(selected, groupsize=50, quiet=True)
        assert self.client._pages == loaded

    def test_preloading_rejects_unrequested_ids_and_oversized_batches(
        self,
    ) -> None:
        """Reject invalid scope without inventing candidate coverage."""
        with self.assertRaises(ValueError):
            self.client.preload(range(1, 52))
        with (
            patch.object(
                source.pagegenerators,
                "PagesFromPageidGenerator",
                return_value=[],
            ),
            patch.object(
                source.pagegenerators,
                "PreloadingGenerator",
                return_value=[Mock(pageid=4)],
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.preload([2])

    def test_preload_subjects_retains_requested_title_keys(self) -> None:
        """Preload redirect content without following its target."""
        page = Mock(pageid=1)
        page.exists.return_value = True
        with (
            patch.object(source.pywikibot, "Page", return_value=page),
            patch.object(
                source.pagegenerators,
                "PreloadingGenerator",
                return_value=[page],
            ) as preload,
        ):
            loaded = self.client.preload_titles(["Example", "Example"])
        assert loaded == {"Example": page}
        assert preload.call_args.kwargs == {"groupsize": 50, "quiet": True}
        page.getRedirectTarget.assert_not_called()

    def test_content_omission_does_not_turn_a_live_id_into_a_missing_page(
        self,
    ) -> None:
        """Reject omitted content for an observed identity."""
        with (
            patch.object(
                source.pagegenerators,
                "PagesFromPageidGenerator",
                return_value=[Mock(pageid=2)],
            ),
            patch.object(
                source.pagegenerators,
                "PreloadingGenerator",
                return_value=[],
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.preload([2])

    def test_history_uses_bounded_native_revisions_and_preserves_hidden_text(
        self,
    ) -> None:
        """Normalize history slots while preserving unknown text."""
        page = Mock(pageid=2, latest_revision_id=90)
        page.title.return_value = "Talk:Renamed"
        page.namespace.return_value = 1
        page.full_url.return_value = (
            "https://zh.wikipedia.org/wiki/Talk:Renamed"
        )
        page.exists.return_value = True
        page.isRedirectPage.return_value = False
        page.revisions.return_value = [
            Revision(
                revid=10,
                timestamp="2020-01-01T00:00:00Z",
                slots={"main": {"*": "{{VG}}"}},
            ),
            Revision(revid=11, timestamp="2020-01-02T00:00:00Z"),
        ]
        self.client._pages[2] = page
        observed = self.client.history(
            "Talk:Old name",
            5,
            oldest=True,
            page_id=2,
        )
        page.revisions.assert_called_once_with(
            total=5,
            reverse=True,
            content=True,
        )
        assert observed["title"] == "Talk:Renamed"
        assert observed["revisions"][0]["slots"]["main"]["content"] == "{{VG}}"
        assert observed["revisions"][1]["slots"]["main"]["content"] is None
        self.client.opener.open.assert_not_called()

    def test_anonymous_site_cannot_be_used_for_explicit_login(self) -> None:
        """Keep the isolated acquisition site read-only."""
        with self.assertRaises(ValueError):
            self.client.site.login()
        self.client.opener.open.assert_not_called()

    def test_real_sdk_batches_current_pages_and_bounds_history(self) -> None:
        """Exercise native batch and history reads without sockets."""
        site = self.client.site
        paraminfo = ParamInfo(site)
        paraminfo._modules = {"query": {"info", "revisions", "categoryinfo"}}
        paraminfo._paraminfo = {
            "main": {"parameters": []},
            "query": {"parameters": []},
            "query+info": {
                "prefix": "in",
                "parameters": [
                    {"name": "prop", "limit": 50, "highlimit": 500},
                ],
            },
            "query+revisions": {
                "prefix": "rv",
                "parameters": [
                    {"name": "limit", "max": 500, "highmax": 5000},
                ],
            },
            "query+categoryinfo": {"prefix": "ci", "parameters": []},
        }
        info = {
            "case": "first-letter",
            "articlepath": "/wiki/$1",
            "wikiid": "zhwiki",
            "lang": "zh",
        }
        self.enterContext(patch.object(site, "_siteinfo", info))
        self.enterContext(patch.object(site, "_paraminfo", paraminfo))
        self.enterContext(
            patch.object(
                site,
                "_namespaces",
                NamespacesDict(Namespace.builtin_namespaces()),
                create=True,
            ),
        )
        self.enterContext(
            patch.object(
                type(site),
                "mw_version",
                new_callable=PropertyMock,
                return_value="1.45",
            ),
        )
        opener = _SdkOpener()
        self.client.opener = opener
        pages = self.client.preload(range(1, 51))
        assert len(pages) == 50
        assert len(opener.queries) == 2
        assert pages[2].title() == "Talk:Moved"
        for page in pages.values():
            assert page.has_content()
            assert page.get(get_redirect=True) == "{{Old vfd multi}}"
            assert page.namespace() == 1
            assert page.latest_revision_id > 1000
            assert page.full_url().startswith("https://zh.wikipedia.org/wiki/")
        assert len(opener.queries) == 2
        subjects = self.client.preload_titles(
            page.toggleTalkPage().title() for page in pages.values()
        )
        assert len(subjects) == 50
        assert all(page.isRedirectPage() for page in subjects.values())
        assert all(
            page.get(get_redirect=True) == "#REDIRECT [[Target]]"
            for page in subjects.values()
        )
        assert len(opener.queries) == 3
        recent = self.client.history("Talk:Moved", 10, page_id=2)
        earliest = self.client.history("Talk:Moved", 5, oldest=True, page_id=2)
        assert len(recent["revisions"]) == 10
        assert len(earliest["revisions"]) == 5
        assert len(opener.queries) == 5
        assert opener.queries[-2]["rvlimit"] == ["10"]
        assert opener.queries[-2]["rvdir"] == ["older"]
        assert opener.queries[-1]["rvlimit"] == ["5"]
        assert opener.queries[-1]["rvdir"] == ["newer"]
        assert all(
            query["rvslots"] == ["*"]
            for query in opener.queries
            if "revisions" in query["prop"][0]
        )
        assert self.admit.call_count == 5


class _SdkOpener:
    """Return realistic query bodies to native SDK generators."""

    def __init__(self) -> None:
        """Start a request ledger without opening a socket."""
        self.queries: list[dict] = []

    def open(self, request: object, *, timeout: int) -> io.BytesIO:
        """Respond to one native metadata, content or history read.

        Args:
            request: Bounded transport request built by the SDK.
            timeout: Required source timeout.

        Returns:
            A formatversion-one response with complete page identities.
        """
        assert timeout == 20
        query = parse_qs(urlsplit(request.full_url).query)
        self.queries.append(query)
        content = "revisions" in query["prop"][0]
        if "pageids" in query:
            selected = [int(value) for value in query["pageids"][0].split("|")]
            pages = [
                self._page(identifier, content=content)
                for identifier in selected
            ]
        elif "rvlimit" in query:
            page = self._page(2)
            oldest = query["rvdir"] == ["newer"]
            page["revisions"] = [
                self._revision(index, old=oldest)
                for index in range(1, int(query["rvlimit"][0]) + 1)
            ]
            pages = [page]
        else:
            pages = [
                self._page(
                    2 if title == "Moved" else int(title.removeprefix("Game")),
                    content=True,
                    subject=True,
                )
                for title in query["titles"][0].split("|")
            ]
        result = {
            "batchcomplete": "",
            "query": {
                "pageids": [str(page["pageid"]) for page in pages],
                "pages": {str(page["pageid"]): page for page in pages},
            },
        }
        return io.BytesIO(json.dumps(result).encode())

    @staticmethod
    def _page(
        identifier: int,
        *,
        content: bool = False,
        subject: bool = False,
    ) -> dict:
        """Build page metadata with optional exact revision content.

        Returns:
            One native page object from a successful query.
        """
        title = "Moved" if identifier == 2 else f"Game{identifier}"
        text = "#REDIRECT [[Target]]" if subject else "{{Old vfd multi}}"
        page = {
            "pageid": identifier + 100 if subject else identifier,
            "ns": 0 if subject else 1,
            "title": title if subject else "Talk:" + title,
            "lastrevid": 1000 + identifier,
            "contentmodel": "wikitext",
        }
        if subject:
            page["redirect"] = ""
        if content:
            page["revisions"] = [
                {
                    "revid": 1000 + identifier,
                    "timestamp": "2026-11-04T00:00:00Z",
                    "slots": {"main": {"*": text}},
                },
            ]
        return page

    @staticmethod
    def _revision(index: int, *, old: bool) -> dict:
        """Build an ordered revision within the requested bounded slice.

        Returns:
            A complete public revision with exact text and timestamp.
        """
        timestamp = dt.datetime(
            2020 if old else 2026,
            1,
            1,
            tzinfo=dt.UTC,
        ) + dt.timedelta(seconds=index if old else -index)
        return {
            "revid": index if old else 1002 - index,
            "timestamp": timestamp.isoformat(),
            "slots": {"main": {"*": "{{VG}}"}},
        }
