"""Preload redirect candidates and read bounded Pywikibot histories.

A dedicated site uses the same bounded, interruptible transport for
every SDK request. Pywikibot builds batch and revision queries while
this adapter controls individual attempts without global HTTP changes,
automatic retries, redirects or concurrent requests.
High-level generators do not expose per-attempt idle admission or
interruptible retry waits, so only this site's request transport is
replaced; page loading and revision interpretation remain native.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, override
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pywikibot
from pywikibot import pagegenerators
from pywikibot.data.api import Request as WikiRequest
from pywikibot.exceptions import NoPageError
from pywikibot.site import APISite

from shared.identity import USER_AGENT
from shared.support.activity import report_activity

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from threading import Event
    from typing import BinaryIO

MIN_REQUEST_INTERVAL = 0.2
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_REQUEST_TIMEOUT = 20
_MAX_RETRY_AFTER = 86400
_CHUNK_BYTES = 64 * 1024
_PRELOAD_SIZE = 50
_MAX_HISTORY = 20


class _ScanRequest(WikiRequest):
    """Submit SDK reads through the scanner's bounded transport."""

    def __init__(self, **kwargs: object) -> None:
        """Reject write actions before the SDK checks authentication.

        Args:
            **kwargs: Native SDK request construction arguments.

        Raises:
            ValueError: The completed request specifies a write action.
        """
        parameters = kwargs.get("parameters", kwargs)
        if parameters.get("action") not in {"query", "paraminfo"}:
            message = "Redirect acquisition only permits read-only queries."
            raise ValueError(message)
        super().__init__(**kwargs)

    @override
    def submit(self) -> dict:
        """Submit one read-only attempt without hidden SDK retries.

        Returns:
            API response in the SDK's requested response format.
        """
        return self.site.scan_client._fetch(  # ruff: ignore[private-member-access]
            self._encoded_items(),
        )


class _ScanSite(APISite):
    """Isolate scanner request handling from ordinary report sites."""

    scan_client: ScanClient

    @staticmethod
    @override
    def _request_class(kwargs: dict) -> type[_ScanRequest]:
        """Select the isolated transport and reject write operations.

        Returns:
            Request implementation for public query and parameter reads.

        Raises:
            ValueError: A caller attempted a different API action.
        """
        parameters = kwargs.get("parameters", kwargs)
        if parameters.get("action") not in {None, "query", "paraminfo"}:
            message = "Redirect acquisition only permits read-only queries."
            raise ValueError(message)
        # The SDK site owns metadata caching; source reads stay fresh.
        kwargs.pop("expiry", None)
        return _ScanRequest

    @override
    def login(
        self,
        autocreate: bool = False,
        user: str | None = None,
        *,
        cookie_only: bool = False,
    ) -> None:
        """Keep construction inert and public acquisition anonymous.

        Raises:
            ValueError: Explicit authentication was requested.
        """
        if not cookie_only:
            message = "The redirect source site cannot authenticate or write."
            raise ValueError(message)


class ScanStoppedError(Exception):
    """Signal cooperative cancellation before the next HTTP attempt."""


class _NoRedirect(HTTPRedirectHandler):
    """Prevent unpaced follow-up HTTP requests through redirects."""

    @override
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        """Reject automatic, unpaced follow-up requests."""
        return


class ScanClient:
    """Read serial API attempts at least 0.2 seconds apart.

    Args:
        stop: Cancellation signal shared with the runtime owner.
        clock: Monotonic clock used only for pacing tests.
        retry_after_at: Saved UTC deadline from a previous server hint.
        on_backoff: Optional owner callback retaining changed deadlines.
        before_request: Optional admission before each attempt.
    """

    def __init__(
        self,
        stop: Event,
        *,
        clock: Callable[[], float] = time.monotonic,
        retry_after_at: str | None = None,
        on_backoff: Callable[[str | None], None] | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        """Initialize an inert client and delay the first request."""
        self.stop = stop
        self.clock = clock
        self.next_attempt = clock() + MIN_REQUEST_INTERVAL
        if retry_after_at is not None:
            delay = (
                dt.datetime.fromisoformat(retry_after_at)
                - dt.datetime.now(dt.UTC)
            ).total_seconds()
            self.next_attempt = clock() + max(MIN_REQUEST_INTERVAL, delay)
        self.on_backoff = on_backoff
        self.before_request = before_request
        self.opener = build_opener(_NoRedirect())
        self._site: _ScanSite | None = None
        self._pages: dict[int, pywikibot.Page] = {}

    @property
    def site(self) -> _ScanSite:
        """The dedicated SDK site, created without reading a wiki.

        The factory cache distinguishes this interface from normal
        report sites. The scanner has only one active locked owner.
        """
        if self._site is None:
            self._site = pywikibot.Site(
                "zh",
                "wikipedia",
                interface=_ScanSite,
            )
            self._site.scan_client = self
        return self._site

    def preload(self, page_ids: Iterable[int]) -> dict[int, pywikibot.Page]:
        """Preload at most fifty current pages by their stable IDs.

        Args:
            page_ids: Candidate identities, including pages renamed
                since the replica observation.

        Returns:
            Observed identities with current content and status. The
            SDK omits IDs that no longer exist.

        Raises:
            ValueError: The requested batch is invalid or too large.
            RuntimeError: The SDK returned an unrequested identity or
                omitted content for an observed page.
        """
        identifiers = tuple(dict.fromkeys(page_ids))
        if len(identifiers) > _PRELOAD_SIZE or any(
            type(identifier) is not int or identifier <= 0
            for identifier in identifiers
        ):
            message = "Redirect preloading requires at most fifty page IDs."
            raise ValueError(message)
        self._pages.clear()
        if not identifiers:
            return {}
        pages = list(
            pagegenerators.PagesFromPageidGenerator(
                identifiers,
                site=self.site,
            ),
        )
        observed_ids = {page.pageid for page in pages}
        loaded = {
            page.pageid: page
            for page in pagegenerators.PreloadingGenerator(
                pages,
                groupsize=_PRELOAD_SIZE,
                quiet=True,
            )
        }
        if set(loaded) - set(identifiers):
            message = "Redirect preloading returned an unrequested page ID."
            raise RuntimeError(message)
        if set(loaded) != observed_ids:
            message = "Redirect content omitted an observed page ID."
            raise RuntimeError(message)
        self._pages.update(loaded)
        return loaded

    def preload_titles(
        self,
        titles: Iterable[str],
    ) -> dict[str, pywikibot.Page]:
        """Preload fifty paired subjects without following redirects.

        Args:
            titles: Current canonical titles derived from talk pages.

        Returns:
            Requested titles mapped to preloaded current page objects.

        Raises:
            ValueError: More than fifty unique titles were requested.
            RuntimeError: Preloading omitted a requested title.
        """
        selected = tuple(dict.fromkeys(titles))
        if len(selected) > _PRELOAD_SIZE:
            message = "Redirect preloading permits at most fifty subjects."
            raise ValueError(message)
        pages = {title: pywikibot.Page(self.site, title) for title in selected}
        loaded = {
            page: page
            for page in pagegenerators.PreloadingGenerator(
                pages.values(),
                groupsize=_PRELOAD_SIZE,
                quiet=True,
            )
        }
        if any(page not in loaded for page in pages.values()):
            message = "Redirect preloading omitted a requested subject."
            raise RuntimeError(message)
        result = {title: loaded[page] for title, page in pages.items()}
        self._pages.update(
            (page.pageid, page) for page in result.values() if page.exists()
        )
        return result

    def history(
        self,
        title: str,
        count: int,
        *,
        oldest: bool = False,
        page_id: int | None = None,
    ) -> dict:
        """Read a bounded native revision sample for one exact identity.

        Args:
            title: Current canonical page title.
            count: Maximum number of sampled revisions.
            oldest: Start at the first rather than latest revision.
            page_id: Stable identity obtained during preloading.

        Returns:
            Current page metadata and normalized sampled revision slots.

        Raises:
            ValueError: The requested history bound is invalid.
        """
        if type(count) is not int or not 1 <= count <= _MAX_HISTORY:
            message = "Redirect history samples require 1-20 revisions."
            raise ValueError(message)
        page = self._pages.get(page_id)
        if page is None:
            if page_id is not None:
                page = self.preload([page_id]).get(page_id)
                if page is None:
                    return {"pageid": page_id, "title": title, "missing": True}
            else:
                page = self.preload_titles([title])[title]
        result = {
            "pageid": page.pageid,
            "title": page.title(),
            "ns": page.namespace(),
            "fullurl": page.full_url(),
        }
        if not page.exists():
            return {**result, "missing": True}
        result["lastrevid"] = page.latest_revision_id
        if page.isRedirectPage():
            result["redirect"] = True
        try:
            revisions = list(
                page.revisions(total=count, reverse=oldest, content=True),
            )
        except NoPageError:
            return {**result, "missing": True}
        result["revisions"] = [
            {
                "revid": revision.revid,
                "timestamp": revision.timestamp.isoformat(),
                "slots": {"main": {"content": revision.get("text")}},
            }
            for revision in revisions
        ]
        return result

    def _fetch(self, parameters: dict) -> dict:
        """Submit one bounded read built by the SDK.

        Returns:
            Successful query or parameter metadata in its native format.

        Raises:
            RuntimeError: The source response or action is invalid.
            ScanStoppedError: Shutdown interrupted an attempt or wait.
            HTTPError: The server rejected the request.
        """
        action = parameters.get("action")
        if action not in {"query", "paraminfo"}:
            message = "Redirect acquisition only permits read-only queries."
            raise RuntimeError(message)
        self._wait_for_attempt()
        report_activity(
            "mediawiki",
            "redirect_recovery",
            wiki="zhwiki",
            target=str(
                parameters.get(
                    "titles",
                    parameters.get("eititle", "template aliases"),
                ),
            ),
        )
        request = Request(
            "https://zh.wikipedia.org/w/api.php?"
            + urlencode({**parameters, "format": "json", "maxlag": 5}),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with self.opener.open(
                request,
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                content = self._read(response)
        except HTTPError as error:
            # Consume backoff before a retry creates a fresh client.
            # Release the HTTP response before an interruptible wait.
            retry = error.headers.get("Retry-After", "")
            error.close()
            delay = self._retry_after(retry)
            if delay:
                if self.on_backoff is not None:
                    self.on_backoff(
                        (
                            dt.datetime.now(dt.UTC)
                            + dt.timedelta(seconds=delay)
                        ).isoformat(),
                    )
                if self.stop.wait(delay):
                    raise ScanStoppedError from error
                if self.on_backoff is not None:
                    self.on_backoff(None)
            raise
        if len(content) > MAX_RESPONSE_BYTES:
            message = "Redirect scan response exceeded its bound."
            raise RuntimeError(message)
        result = json.loads(content)
        if (
            not isinstance(result, dict)
            or "error" in result
            or action not in result
        ):
            message = "Redirect scan source returned an API error."
            raise RuntimeError(message)
        return result

    def _wait_for_attempt(self) -> None:
        """Wait for request pacing and the owner's source admission.

        Raises:
            ScanStoppedError: Shutdown interrupted either wait.
        """
        delay = max(0.0, self.next_attempt - self.clock())
        if self.stop.wait(delay) or self.stop.is_set():
            raise ScanStoppedError
        if self.before_request is not None:
            self.before_request()
        if self.stop.is_set():
            raise ScanStoppedError
        self.next_attempt = self.clock() + MIN_REQUEST_INTERVAL

    def _read(self, response: BinaryIO) -> bytes:
        """Read bounded chunks with cancellation and a deadline.

        The socket timeout bounds a stalled read. Checking the deadline
        between single-buffer reads also bounds a slowly trickling body.

        Returns:
            Complete bounded response bytes.

        Raises:
            ScanStoppedError: Runtime shutdown interrupted the response.
            TimeoutError: Response processing exceeded its deadline.
            RuntimeError: The body exceeded its byte limit.
        """
        deadline = self.clock() + _REQUEST_TIMEOUT
        chunks = []
        length = 0
        while True:
            if self.stop.is_set():
                raise ScanStoppedError
            if self.clock() >= deadline:
                message = "Redirect scan response exceeded its deadline."
                raise TimeoutError(message)
            chunk = response.read1(_CHUNK_BYTES)
            if not chunk:
                return b"".join(chunks)
            length += len(chunk)
            if length > MAX_RESPONSE_BYTES:
                message = "Redirect scan response exceeded its bound."
                raise RuntimeError(message)
            chunks.append(chunk)

    @staticmethod
    def _retry_after(value: str) -> float:
        """Interpret bounded Retry-After seconds and HTTP dates.

        Returns:
            Nonnegative delay, or zero for an invalid server hint.
        """
        if value.isdecimal():
            return min(int(value), _MAX_RETRY_AFTER)
        try:
            instant = parsedate_to_datetime(value)
        except TypeError, ValueError, OverflowError:
            return 0
        if instant.utcoffset() is None:
            return 0
        return min(
            _MAX_RETRY_AFTER,
            max(0, (instant - dt.datetime.now(dt.UTC)).total_seconds()),
        )
