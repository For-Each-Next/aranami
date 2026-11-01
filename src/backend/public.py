"""Serve saved website data and live status on Toolforge with FastAPI.

Run ``python -m backend.public`` as the published web job. Requests only
read fixed source assets and validated runtime snapshots; they never
start the hub, initialize source clients, or publish wiki changes.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
from contextlib import ExitStack, asynccontextmanager, suppress
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import Response, StreamingResponse
from starlette.websockets import WebSocketState
lazy import uvicorn

from backend.newsletter.observations import OBSERVATIONS_PATH
from backend.support.asset_cache import AssetCache
from backend.support.events import event_extra
from backend.support.http import (
    accepts_gzip,
    encoded_response,
    read_only_headers,
)
from backend.support.job_history import read_job_history
from backend.support.logs import open_run_log
from backend.support.page_cache import PageCache
from backend.support.public_data import (
    MAX_LOG_BYTES,
    available_logs,
    log_date,
    log_excerpt,
    log_selection,
    saved_status,
)
from shared.config import routine_configuration
from shared.deployment import project_version, resource_root
from shared.newsletter import Issue
from shared.support.files import open_regular, read_json_object
lazy from backend.newsletter.archive import (
    DEFAULT_PATH as DRAFT_ARCHIVE_PATH,
    NewsletterDraftArchive,
)
lazy from backend.newsletter.schema import read_newsletter_data
lazy from backend.website.collect import validate_dashboard_snapshot
lazy from updater.services.newsletter import render_issue
lazy from updater.services.newsletter_editorial import render_annual_feature
lazy from webservice import pages
lazy from webservice.presentation import Interface, local_url, select_locale

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
    from typing import BinaryIO

    from starlette.types import ASGIApp, Receive, Scope, Send

_ASSETS = resource_root("webservice") / "public_html"
_MAX_BYTES = 8 * 1024 * 1024
_MAX_QUERY_BYTES = 4096
_MAX_HEADER_BYTES = 64 * 1024
_MAX_PORT = 65535
_CHUNK_BYTES = 64 * 1024
_SOCKET_CLIENTS = 16
_SOCKET_MESSAGE_BYTES = 512
_SOCKET_HEARTBEAT_SECONDS = 15
_SOCKET_REFRESH_SECONDS = 60
_SOCKET_CHECK_SECONDS = 1
_SOCKET_SEND_SECONDS = 5
_LOGGER = logging.getLogger(__name__)
_LANGUAGES = {"en": "English", "zh-hans": "简体中文", "zh-hant": "繁体中文"}
_LANGUAGE_COOKIE = "aranami-language"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def _newsletter_selection(
    parameters: dict[str, list[str]],
    automatic_issue: Issue,
) -> tuple[Issue, dt.date | None]:
    """Validate a newsletter issue and optional saved data date.

    Returns:
        The selected issue and completed data date, if supplied.

    Raises:
        ValueError: Controls repeat or fall outside the reporting dates.
    """
    if set(parameters) - {"issue", "date", "feature_date"} or any(
        len(values) != 1 for values in parameters.values()
    ):
        message = "Choose one newsletter issue and data date."
        raise ValueError(message)
    day = None
    if "date" in parameters and "feature_date" in parameters:
        message = "Choose only one newsletter data date."
        raise ValueError(message)
    if "date" in parameters or "feature_date" in parameters:
        value = parameters.get("date", parameters.get("feature_date"))[0]
        day = dt.date.fromisoformat(value)
        if day.isoformat() != value:
            message = "Newsletter dates require YYYY-MM-DD."
            raise ValueError(message)
    issue = (
        Issue.parse(parameters["issue"][0])
        if "issue" in parameters
        else Issue.parse(f"{day.year + 1}-01")
        if day is not None and "feature_date" in parameters
        else Issue.upcoming(day)
        if day is not None
        else automatic_issue
    )
    start = dt.date(issue.year - 1, 1, 1) if issue.month == 1 else issue.start
    if day is not None and not start <= day < issue.end:
        message = (
            "The saved date must belong to the selected reporting period."
        )
        raise ValueError(message)
    if "feature_date" in parameters and issue.month != 1:
        message = "Annual feature dates require a January issue."
        raise ValueError(message)
    return issue, day


def _newsletter_choices(
    archive: NewsletterDraftArchive,
    automatic: Issue,
    selected: Issue,
    retained: dict | None = None,
) -> list[str]:
    """Expose the year-long feature before its reporting quarter.

    Returns:
        Current, previous, selected, and captured annual issue choices.
    """
    choices = list(
        dict.fromkeys([automatic.previous, automatic.value, selected.value]),
    )
    annual = Issue.parse(f"{automatic.year + int(automatic.month != 1)}-01")
    if annual.value not in choices and archive.feature_dates(annual):
        choices.append(annual.value)
    return sorted(set(choices) | set((retained or {}).get("issues", {})))


def _newsletter_feature(
    archive: NewsletterDraftArchive,
    issue: Issue,
    requested: str | None,
    value: dict,
    current_facts: dict | None = None,
) -> dict | None:
    """Select annual evidence for the shared newsletter data date.

    Returns:
        Annual selection metadata, or none for non-January issues.
    """
    if issue.month != 1:
        return None
    selected = requested
    review = (current_facts or {}).get("year_review")
    if (
        selected
        and review
        and review["observation_end"]
        == (dt.date.fromisoformat(selected) + dt.timedelta(days=1)).isoformat()
    ):
        entry = {
            "year_review": review,
            "generated_at": current_facts["updated_at"],
        }
    else:
        entry = (
            archive.read_feature(issue, dt.date.fromisoformat(selected))
            if selected
            else None
        )
    if entry is not None:
        value["pages"] = [
            page for page in value["pages"] if page["label"] != "專題"
        ]
        value["pages"].append({
            "label": "專題",
            "title": f"WikiProject:電子遊戲/簡訊/{issue.value}/專題",
            "url": issue.page_url("專題"),
            "text": render_annual_feature(issue, entry["year_review"]),
            "data_through": selected,
        })
        value["available"] = True
    return {
        "selected_date": selected,
        "generated_at": entry["generated_at"] if entry else None,
        "missing": selected is not None and entry is None,
    }


def _newsletter_dates(
    archive: NewsletterDraftArchive,
    issue: Issue,
) -> list[str]:
    """List dates with saved quarterly or annual newsletter evidence.

    Returns:
        Sorted calendar choices within the issue's reporting period.
    """
    dates = archive.available_dates(issue)
    if issue.month == 1:
        dates = sorted(set(dates) | set(archive.feature_dates(issue)))
    return dates


def _newsletter_current_day(
    issue: Issue,
    facts: dict,
    dates: list[str],
) -> dt.date | None:
    """Match current facts to a saved quarterly or annual date.

    Returns:
        The captured day, or an early annual archive fallback.
    """
    boundary = dt.date.fromisoformat(facts["observation_end"])
    early_annual = issue.month == 1 and boundary <= issue.start
    if early_annual:
        boundary = dt.date.fromisoformat(
            (facts.get("year_review") or {}).get(
                "observation_end",
                facts["observation_end"],
            ),
        )
    day = boundary - dt.timedelta(days=1)
    if day.isoformat() in dates:
        return day
    return dt.date.fromisoformat(dates[-1]) if early_annual and dates else None


def _json(value: object, status: HTTPStatus = HTTPStatus.OK) -> Response:
    """Return validated metadata encoded as standard JSON."""
    return Response(
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
        status_code=status,
        media_type="application/json; charset=utf-8",
    )


def _error(status: HTTPStatus) -> Response:
    """Return a JSON error without framework HTML templates."""
    return _json({"error": status.phrase}, status)


def _validate_request(request: Request) -> Response | None:
    """Return a rejection for unsafe requests, otherwise None."""
    if request.method not in {"GET", "HEAD"}:
        return _error(HTTPStatus.METHOD_NOT_ALLOWED)
    rejection = _request_size_error(request)
    if rejection is not None:
        return rejection
    try:
        query = request.scope.get("query_string", b"")
        target = urlsplit(request.scope["raw_path"].decode("ascii"))
    except ValueError, UnicodeError:
        return _error(HTTPStatus.BAD_REQUEST)
    if (
        target.scheme
        or target.netloc
        or target.fragment
        or len(query) > _MAX_QUERY_BYTES
    ):
        return _error(HTTPStatus.BAD_REQUEST)
    if any(token in target.path for token in ("\\", "%", "//")) or any(
        part in {".", ".."} for part in target.path.split("/")
    ):
        return _error(HTTPStatus.NOT_FOUND)
    return None


def _request_size_error(request: Request) -> Response | None:
    """Bound headers and declared bodies without consuming input.

    Returns:
        A size or syntax rejection, or None for bounded input.
    """
    try:
        lengths = request.headers.getlist("content-length")
        length = int(lengths[0]) if lengths else 0
    except ValueError:
        return _error(HTTPStatus.BAD_REQUEST)
    if len(lengths) > 1 or length < 0:
        return _error(HTTPStatus.BAD_REQUEST)
    if length > _MAX_QUERY_BYTES:
        return _error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    if (
        sum(len(key) + len(value) for key, value in request.scope["headers"])
        > _MAX_HEADER_BYTES
    ):
        return _error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
    return None


async def _content_chunks(content: bytes) -> AsyncIterator[bytes]:
    """Yield already prepared responses in bounded ASGI chunks."""
    for offset in range(0, len(content), _CHUNK_BYTES):
        await asyncio.sleep(0)
        yield content[offset : offset + _CHUNK_BYTES]


class _AssetResponse(Response):
    """Mark encoded assets with conditional browser caching."""


def _response_policy(
    request: Request,
    response: Response,
) -> Response:
    """Return shared headers and gzip without buffering log streams."""
    for name, value in read_only_headers(
        response.status_code,
        websocket_origin="wss://aranami.toolforge.org",
    ).items():
        if name == "Cache-Control" and isinstance(response, _AssetResponse):
            continue
        response.headers[name] = value
    language = _explicit_language(request)
    if language is not None and not isinstance(response, _AssetResponse):
        response.set_cookie(
            _LANGUAGE_COOKIE,
            language,
            max_age=365 * 24 * 60 * 60,
            httponly=True,
            secure=request.url.hostname == "aranami.toolforge.org"
            or request.url.scheme == "https",
            samesite="lax",
        )
    if (
        not isinstance(response, StreamingResponse)
        and "Content-Disposition" not in response.headers
    ):
        if isinstance(response, _AssetResponse):
            content, headers = response.body, {}
        else:
            content, headers = encoded_response(
                response.body,
                request.headers.get("Accept-Encoding", ""),
            )
            vary = response.headers.get("Vary", "")
            response.headers["Vary"] = ", ".join(
                value for value in (vary, "Accept-Encoding") if value
            )
        if response.status_code != HTTPStatus.NOT_MODIFIED:
            response.headers["Content-Length"] = str(len(content))
        if "Content-Encoding" in headers:
            response.headers["Content-Encoding"] = headers["Content-Encoding"]
        response = StreamingResponse(
            _content_chunks(b"" if request.method == "HEAD" else content),
            status_code=response.status_code,
            headers=dict(response.headers),
            background=response.background,
        )
    return response


def _html(
    content: str,
    locale: str,
    status: HTTPStatus = HTTPStatus.OK,
) -> Response:
    """Return a complete localized server-rendered document."""
    return Response(
        content.encode(),
        status_code=status,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Language": Interface(locale).language_tag,
            "Vary": "Accept-Language, Cookie",
        },
    )


def _explicit_language(request: Request) -> str | None:
    """Read one supported explicit language choice.

    Returns:
        The selected locale, or none for absent or invalid choices.
    """
    values = request.query_params.getlist("lang")
    return (
        values[0].lower()
        if len(values) == 1 and values[0].lower() in _LANGUAGES
        else None
    )


def _request_locale(request: Request) -> str:
    """Prefer an explicit choice, then cookie, then browser language.

    Returns:
        A supported interface locale without changing source content.
    """
    chosen = _explicit_language(request)
    saved = request.cookies.get(_LANGUAGE_COOKIE)
    browser = select_locale(request.headers.get("Accept-Language", ""))
    selected = chosen or (saved if saved in _LANGUAGES else browser)
    if selected == "zh-hant" and browser in {"zh-tw", "zh-hk"}:
        return browser
    return selected


def _navigation(request: Request, locale: str) -> dict[str, object]:
    """Build language links retaining the current route and query state.

    Returns:
        Safe local URLs and the active page for shared navigation.
    """
    parameters = {
        key: value
        for key, value in request.query_params.items()
        if key
        in {
            "task",
            "sort",
            "order",
            "page",
            "seed",
            "q",
            "quality",
            "importance",
            "date",
            "status",
            "issue",
        }
    }
    return {
        "path": request.url.path,
        "languages": [
            {
                "label": label,
                "tag": {"zh-hans": "zh-Hans", "zh-hant": "zh-Hant"}.get(
                    language,
                    language,
                ),
                "url": local_url(
                    request.url.path,
                    **parameters,
                    lang=language,
                ),
                "current": language == Interface(locale).message_locale
                or (language == "zh-hans" and locale == "zh"),
            }
            for language, label in _LANGUAGES.items()
        ],
    }


def _page_error(key: str, locale: str, status: HTTPStatus) -> Response:
    """Return the common localized document for request failures."""
    return _html(pages.render_error(key, locale), locale, status)


def _configuration() -> dict[str, object]:
    """Return the source version and shared routine definitions."""
    return {
        "version": project_version(),
        "routines": routine_configuration(),
    }


async def _download_chunks(
    stream: BinaryIO,
    remaining: int,
) -> AsyncIterator[bytes]:
    """Yield at most the validated size even if a log keeps growing."""
    while remaining:
        chunk = await asyncio.to_thread(
            stream.read,
            min(remaining, _CHUNK_BYTES),
        )
        if not chunk:
            break
        yield chunk
        remaining -= len(chunk)


class _DownloadResponse(StreamingResponse):
    """Close bounded downloads on completion or disconnect."""

    def __init__(
        self,
        *args: object,
        resources: ExitStack,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.resources = resources

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Stream through Starlette and release the opened log."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.resources.close()


def _download(request: Request, path: Path) -> StreamingResponse:
    """Return a bounded log stream closed on completion or abort."""
    with ExitStack() as resources:
        stream = resources.enter_context(open_regular(path, MAX_LOG_BYTES))
        size = os.fstat(stream.fileno()).st_size
        response = _DownloadResponse(
            _content_chunks(b"")
            if request.method == "HEAD"
            else _download_chunks(stream, size),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Length": str(size),
                "Content-Disposition": f'attachment; filename="{path.name}"',
            },
            resources=resources.pop_all(),
        )
        return response


class _PublicViews:
    """Read validated saved inputs for one public application."""

    def __init__(self, runtime: Path, assets: Path) -> None:
        self.runtime = runtime
        self.assets = assets
        self.asset_cache = AssetCache()
        self.pages = PageCache()
        self.socket_clients = 0

    def page(self, request: Request) -> Response:
        """Return a page cached by locale and saved-input identity."""
        path = request.url.path
        locale = _request_locale(request)
        if path == "/reports/":
            return self.reports(request, locale=locale)
        if path == "/logs/":
            return self.logs(request, locale=locale)
        try:
            query = request.scope.get("query_string", b"").decode("ascii")
            parameters = parse_qs(
                query,
                keep_blank_values=True,
                errors="strict",
                max_num_fields=12 if path == "/maintenance/" else 5,
            )
        except ValueError, UnicodeError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        parameters.pop("lang", None)
        navigation = _navigation(request, locale)
        randomized = parameters.get("sort") == [
            "random",
        ] and not parameters.get(
            "seed",
        )
        automatic_issue, retained_newsletter, newsletter_error = (
            self._newsletter_context(parameters)
            if path == "/newsletter/"
            else (None, None, False)
        )
        key = (
            path,
            query,
            locale,
            automatic_issue.value if automatic_issue is not None else "",
            newsletter_error,
        )
        identity = self._page_identity()
        if automatic_issue is not None:
            identity += (
                self._newsletter_identity(parameters, automatic_issue),
            )
        if not randomized:
            content = self.pages.get(key, identity)
            if content is not None:
                return _html(content, locale)
        if automatic_issue is not None:
            response = self._newsletter_page(
                parameters,
                locale,
                navigation,
                (automatic_issue, retained_newsletter, newsletter_error),
            )
        elif path == "/about/":
            response = _html(
                pages.render_about(
                    _configuration(),
                    locale,
                    status=saved_status(self.runtime),
                    navigation=navigation,
                ),
                locale,
            )
        else:
            snapshot = self._dashboard()
            response = (
                _html(
                    pages.render_dashboard(
                        snapshot,
                        saved_status(self.runtime),
                        _configuration(),
                        locale,
                        navigation=navigation,
                    ),
                    locale,
                )
                if path == "/"
                else self._maintenance_page(
                    snapshot,
                    parameters,
                    locale,
                    navigation,
                )
            )
        if not randomized and response.status_code == HTTPStatus.OK:
            self.pages.put(key, identity, response.body.decode())
        return response

    def _newsletter_context(
        self,
        parameters: dict[str, list[str]],
    ) -> tuple[Issue, dict | None, bool]:
        """Select the default issue from saved publication metadata.

        Returns:
            The default issue, current facts, and a read-error flag.
            Explicit reporting dates do not read current observations.
        """
        automatic = Issue.upcoming(dt.datetime.now(dt.UTC).date())
        if {"date", "feature_date"}.intersection(parameters):
            return automatic, None, False
        try:
            retained = read_newsletter_data(self.runtime / OBSERVATIONS_PATH)
        except OSError, ValueError:
            return automatic, None, True
        lifecycle = (retained or {}).get("lifecycle")
        if lifecycle is not None:
            automatic = Issue.parse(lifecycle["default_issue"])
        return automatic, retained, False

    def _newsletter_identity(
        self,
        parameters: dict[str, list[str]],
        automatic_issue: Issue,
    ) -> tuple | None:
        """Read saved observation and calendar identities for caching.

        Returns:
            Catalog metadata, or none for absent or invalid selections.
            The page handler reports validation and read errors.
        """
        try:
            issue, day = _newsletter_selection(parameters, automatic_issue)
            store = NewsletterDraftArchive(
                self.runtime / DRAFT_ARCHIVE_PATH,
            )
            archive = store.identity(issue)
            feature_dates = (
                tuple(store.feature_dates(issue)) if issue.month == 1 else ()
            )
        except OSError, ValueError:
            return None
        if day is not None:
            return archive, feature_dates
        pointer = self.runtime / OBSERVATIONS_PATH / "current.json"
        try:
            info = pointer.lstat()
        except OSError:
            observation = None
        else:
            observation = (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
            )
        return archive, observation, feature_dates

    def _newsletter_page(
        self,
        parameters: dict[str, list[str]],
        locale: str,
        navigation: dict[str, object],
        context: tuple[Issue, dict | None, bool],
    ) -> Response:
        """Render an issue from validated saved facts.

        Returns:
            A draft page or a localized invalid-selection response.
        """
        automatic_issue, retained, read_error = context
        try:
            issue, day = _newsletter_selection(parameters, automatic_issue)
        except ValueError:
            return _page_error(
                "newsletter.history.invalid_date"
                if "date" in parameters or "feature_date" in parameters
                else "newsletter.invalid_issue",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        if read_error:
            return _page_error(
                "newsletter.history.read_error",
                locale,
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        archive = NewsletterDraftArchive(self.runtime / DRAFT_ARCHIVE_PATH)
        try:
            durable_facts = (retained or {}).get("issues", {}).get(issue.value)
            dates = _newsletter_dates(archive, issue)
            if day is None and dates and durable_facts is None:
                day = dt.date.fromisoformat(dates[-1])
            entry = (
                archive.read_entry(issue, day)
                if day is not None and issue.start <= day < issue.end
                else None
            )
        except OSError, ValueError:
            return _page_error(
                "newsletter.history.read_error",
                locale,
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if durable_facts is not None:
            value = render_issue(issue, durable_facts)
            generated_at = durable_facts["updated_at"]
            day = _newsletter_current_day(issue, durable_facts, dates)
        elif entry is not None:
            value = render_issue(issue, entry["facts"])
            generated_at = entry["generated_at"]
        elif day is not None:
            value = render_issue(issue)
            generated_at = None
        else:
            value = render_issue(issue)
            generated_at = None
        try:
            feature_history = _newsletter_feature(
                archive,
                issue,
                day.isoformat() if day is not None else None,
                value,
                durable_facts,
            )
            generated_at = (feature_history or {}).get(
                "generated_at",
            ) or generated_at
            choices = _newsletter_choices(
                archive,
                automatic_issue,
                issue,
                retained,
            )
        except OSError, ValueError:
            return _page_error(
                "newsletter.history.read_error",
                locale,
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        value.update(
            automatic_issue=automatic_issue.value,
            issue_choices=choices,
            draft_history={
                "dates": dates,
                "selected_date": day.isoformat() if day is not None else None,
                "generated_at": generated_at,
                "missing": (day is not None and not value["available"]),
            },
        )
        return _html(
            pages.render_newsletter(
                value,
                locale,
                status=saved_status(self.runtime),
                config=_configuration(),
                navigation=navigation,
            ),
            locale,
        )

    def _maintenance_page(
        self,
        snapshot: dict[str, object] | None,
        parameters: dict[str, list[str]],
        locale: str,
        navigation: dict[str, object],
    ) -> Response:
        """Return the saved catalogue or a selected task worklist."""
        if not parameters:
            return _html(
                pages.render_maintenance_overview(
                    snapshot,
                    saved_status(self.runtime),
                    _configuration(),
                    locale,
                    navigation=navigation,
                ),
                locale,
            )
        try:
            content = pages.render_maintenance(
                snapshot,
                parameters,
                locale,
                navigation=navigation,
                status=saved_status(self.runtime),
                config=_configuration(),
            )
        except ValueError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        except LookupError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.NOT_FOUND,
            )
        except RuntimeError:
            return _page_error(
                "backlog.unavailable",
                locale,
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        return _html(content, locale)

    def _page_identity(self) -> tuple:
        """Return metadata identities for all saved page inputs."""
        paths = [
            self.runtime / relative
            for relative in (
                "cache",
                "cache/toolforge",
                "cache/toolforge/dashboard.json",
                "state",
                "state/routine-status.json",
                "state/schedule-status.json",
                "state/report-updates.json",
            )
        ]
        paths.append(Path(__file__).resolve().parents[2] / "pyproject.toml")
        identities = []
        for path in paths:
            try:
                info = path.lstat()
            except OSError:
                identities.append(None)
            else:
                identities.append((
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ))
        return tuple(identities)

    def _dashboard(self) -> dict[str, object] | None:
        """Return saved dashboard data or None when unavailable."""
        cache = self.runtime / "cache"
        if cache.is_symlink():
            return None
        try:
            value = read_json_object(
                cache / "toolforge" / "dashboard.json",
                _MAX_BYTES,
            )
        except OSError, ValueError, TypeError, RecursionError, OverflowError:
            return None
        if validate_dashboard_snapshot(value):
            return value
        # Older newsletter data must not hide other valid saved reports.
        reports = value.get("reports")
        if isinstance(reports, dict) and "newsletter" in reports:
            value = {
                **value,
                "newsletter": None,
                "reports": {
                    name: report
                    for name, report in reports.items()
                    if name != "newsletter"
                },
            }
            if validate_dashboard_snapshot(value):
                return value
        return None

    def api(self, request: Request) -> Response:
        """Return data through its backend validation contract."""
        kind = request.url.path.rsplit("/", 1)[-1]
        if kind == "config":
            return _json(_configuration())
        if kind == "status":
            return _json(saved_status(self.runtime))
        if kind == "live":
            locale = _request_locale(request)
            return _html(
                pages.render_live_status(
                    saved_status(self.runtime),
                    _configuration(),
                    locale,
                ),
                locale,
            )
        value = self._dashboard()
        return (
            _json(value)
            if value is not None
            else _error(HTTPStatus.SERVICE_UNAVAILABLE)
        )

    def asset(self, request: Request, path: str) -> Response:
        """Revalidate cached source assets without directory listings.

        Returns:
            Negotiated bytes, an unchanged response, or a missing error.
        """
        relative = Path(path)
        if (
            path.startswith("api/")
            or relative.suffix not in _CONTENT_TYPES
            or relative.is_absolute()
        ):
            return _error(HTTPStatus.NOT_FOUND)
        location = self.assets / relative
        if any(parent.is_symlink() for parent in location.parents):
            return _error(HTTPStatus.NOT_FOUND)
        try:
            asset = self.asset_cache.read(location)
        except OSError, ValueError:
            return _error(HTTPStatus.NOT_FOUND)
        headers = {
            "Cache-Control": "public, max-age=31536000, immutable"
            if request.query_params.getlist("v") == [asset.digest]
            else "public, max-age=0, must-revalidate",
            "ETag": asset.etag,
            "Vary": "Accept-Encoding",
        }
        validators = {
            value.strip().removeprefix("W/")
            for value in request.headers.get("If-None-Match", "").split(",")
        }
        if "*" in validators or asset.etag.removeprefix("W/") in validators:
            return _AssetResponse(
                status_code=HTTPStatus.NOT_MODIFIED,
                headers=headers,
            )
        compressed = asset.gzip and accepts_gzip(
            request.headers.get("Accept-Encoding", ""),
        )
        if compressed:
            headers["Content-Encoding"] = "gzip"
        return _AssetResponse(
            asset.compressed if compressed else asset.content,
            media_type=_CONTENT_TYPES[relative.suffix],
            headers=headers,
        )

    def reports(self, request: Request, *, locale: str) -> Response:
        """Read per-job history independently of diagnostic logs.

        Returns:
            A Reports page with retained invocations across all dates.
        """
        try:
            runs = read_job_history(
                self.runtime / "state" / "job-history.json",
            )["runs"]
        except OSError, ValueError, TypeError, OverflowError, RecursionError:
            runs = []
        return _html(
            pages.render_reports(
                {"runs": runs},
                locale,
                status=saved_status(self.runtime),
                config=_configuration(),
                navigation=_navigation(request, locale),
            ),
            locale,
        )

    def logs(
        self,
        request: Request,
        *,
        locale: str | None = None,
    ) -> Response:
        """Return saved events, a file archive, or a daily download."""
        directory = self.runtime / "logs"
        files = available_logs(directory)
        try:
            selection = parse_qs(
                request.scope.get("query_string", b"").decode("ascii"),
                keep_blank_values=True,
                errors="strict",
                max_num_fields=4 if locale is not None else 3,
            )
            if locale is not None:
                selection.pop("lang", None)
            filename, text, download = log_selection(selection, files)
        except ValueError, UnicodeError:
            return (
                _error(HTTPStatus.BAD_REQUEST)
                if locale is None
                else _page_error(
                    "logs.invalid_query",
                    locale,
                    HTTPStatus.BAD_REQUEST,
                )
            )
        if filename is not None and not any(
            row["name"] == filename for row in files
        ):
            return (
                _error(HTTPStatus.NOT_FOUND)
                if locale is None
                else _page_error(
                    "logs.not_found",
                    locale,
                    HTTPStatus.NOT_FOUND,
                )
            )
        if download:
            return (
                _error(HTTPStatus.NOT_FOUND)
                if filename is None
                else _download(request, directory / filename)
            )
        excerpt = (
            {"events": [], "truncated": False}
            if filename is None or locale is not None
            else log_excerpt(directory, filename, text)
        )
        value = {
            "files": files,
            "date": log_date(filename) if filename is not None else None,
            "query": text,
            **excerpt,
        }
        return (
            _json(value)
            if locale is None
            else _html(
                pages.render_logs(
                    value,
                    locale,
                    status=saved_status(self.runtime),
                    config=_configuration(),
                    navigation=_navigation(request, locale),
                ),
                locale,
            )
        )

    def api_logs(self, request: Request) -> Response:
        """Return the log API with its fixed presentation contract."""
        return self.logs(request)

    def _live_identity(self) -> tuple:
        """Return saved-status metadata without reading datasets."""
        identities = []
        for name in (
            "state",
            "state/routine-status.json",
            "state/schedule-status.json",
            "state/report-updates.json",
        ):
            try:
                info = (self.runtime / name).lstat()
            except OSError:
                identities.append(None)
            else:
                identities.append((
                    info.st_ino,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ))
        return tuple(identities)

    def _live_html(self, locale: str) -> str:
        """Render saved live observations.

        Returns:
            Complete localized live fragments without dataset reads.
        """
        return pages.render_live_status(
            saved_status(self.runtime),
            _configuration(),
            locale,
        )

    async def live_socket(self, websocket: WebSocket) -> None:
        """Push saved-status changes until the client disconnects.

        Args:
            websocket: Connection requesting a supported locale.
        """
        locale = _socket_locale(websocket)
        if locale is None or not _socket_origin(websocket):
            await websocket.close(code=1008)
            return
        if self.socket_clients >= _SOCKET_CLIENTS:
            await websocket.close(code=1013)
            return
        self.socket_clients += 1
        receiver = None
        close_code = 1000
        try:
            await websocket.accept()
            receiver = asyncio.create_task(_socket_receive(websocket))
            close_code = await self._watch_live(websocket, locale, receiver)
        except WebSocketDisconnect, TimeoutError, OSError:
            pass
        finally:
            self.socket_clients -= 1
            if receiver is not None:
                receiver.cancel()
                with suppress(asyncio.CancelledError, WebSocketDisconnect):
                    await receiver
            if websocket.application_state == WebSocketState.CONNECTED:
                with suppress(WebSocketDisconnect, RuntimeError, TimeoutError):
                    await asyncio.wait_for(
                        websocket.close(code=close_code),
                        timeout=1,
                    )

    async def _watch_live(
        self,
        websocket: WebSocket,
        locale: str,
        receiver: asyncio.Task[int],
    ) -> int:
        """Watch saved metadata while one task owns all outgoing frames.

        Returns:
            The close code observed by the read-only message receiver.
        """
        identity = await asyncio.to_thread(self._live_identity)
        await self._send_live(websocket, locale)
        loop = asyncio.get_running_loop()
        refreshed = heartbeat = loop.time()
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(receiver),
                    timeout=_SOCKET_CHECK_SECONDS,
                )
            except TimeoutError:
                pass
            current = await asyncio.to_thread(self._live_identity)
            now = loop.time()
            if (
                current != identity
                or now - refreshed >= _SOCKET_REFRESH_SECONDS
            ):
                await self._send_live(websocket, locale)
                identity = current
                refreshed = heartbeat = now
            elif now - heartbeat >= _SOCKET_HEARTBEAT_SECONDS:
                await asyncio.wait_for(
                    websocket.send_json({"type": "heartbeat"}),
                    timeout=_SOCKET_SEND_SECONDS,
                )
                heartbeat = now

    async def _send_live(self, websocket: WebSocket, locale: str) -> None:
        """Send current saved-status HTML with bounded backpressure."""
        html = await asyncio.to_thread(self._live_html, locale)
        await asyncio.wait_for(
            websocket.send_json({"type": "status", "html": html}),
            timeout=_SOCKET_SEND_SECONDS,
        )


def _socket_locale(websocket: WebSocket) -> str | None:
    """Validate the exact socket path and locale query.

    Returns:
        A supported locale, or None for an invalid request.
    """
    try:
        raw_path = websocket.scope["raw_path"].decode("ascii")
        query = websocket.scope.get("query_string", b"")
        if raw_path != "/api/live/ws" or len(query) > _SOCKET_MESSAGE_BYTES:
            return None
        parameters = parse_qs(
            query.decode("ascii"),
            keep_blank_values=True,
            errors="strict",
            max_num_fields=1,
        )
    except ValueError, UnicodeError:
        return None
    locale = parameters.get("lang", ["en"])
    return (
        {"zh-hant-tw": "zh-tw", "zh-hant-hk": "zh-hk"}.get(
            locale[0],
            locale[0],
        )
        if set(parameters) <= {"lang"}
        and len(locale) == 1
        and locale[0]
        in {
            "en",
            "zh",
            "zh-hans",
            "zh-hant",
            "zh-tw",
            "zh-hk",
            "zh-hant-tw",
            "zh-hant-hk",
        }
        else None
    )


def _socket_origin(websocket: WebSocket) -> bool:
    """Check the actual host origin and Toolforge TLS termination.

    Returns:
        Whether the caller is non-browser or has the matching origin.
    """
    origins = websocket.headers.getlist("origin")
    if not origins:
        return True
    hosts = websocket.headers.getlist("host")
    if (
        len(origins) != 1
        or len(hosts) != 1
        or len(origins[0]) > _SOCKET_MESSAGE_BYTES
    ):
        return False
    try:
        origin = urlsplit(origins[0])
    except ValueError:
        return False
    host = hosts[0].lower()
    scheme = (
        "https"
        if websocket.scope["scheme"] == "wss"
        or host == "aranami.toolforge.org"
        else "http"
    )
    return (
        origin.scheme == scheme
        and origin.netloc.lower() == host
        and not any((
            origin.path,
            origin.query,
            origin.fragment,
            origin.username,
        ))
    )


async def _socket_receive(websocket: WebSocket) -> int:
    """Observe disconnects and validate bounded ping frames.

    Returns:
        A normal close code or a protocol-policy rejection.
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return 1000
        text = message.get("text")
        if (
            not isinstance(text, str)
            or len(text.encode()) > _SOCKET_MESSAGE_BYTES
        ):
            return 1008
        try:
            value = json.loads(text)
        except ValueError, RecursionError:
            value = None
        if value != {"type": "ping"}:
            return 1008


class _PublicRoute(APIRoute):
    """Apply shared response encoding to FastAPI route handling."""

    def get_route_handler(self) -> Callable:
        """Return the route handler with the public response policy."""
        handler = super().get_route_handler()

        async def respond(request: Request) -> Response:
            """Return a validated route response with shared headers."""
            return _response_policy(request, await handler(request))

        return respond


class _RequestBounds:
    """Reject unsafe HTTP targets before FastAPI resolves routes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Validate HTTP requests and pass other ASGI events through."""
        if scope["type"] == "http":
            request = Request(scope)
            response = _validate_request(request)
            if response is not None:
                await _response_policy(request, response)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _routing_error(request: Request, exception: Exception) -> Response:
    """Return sanitized API errors with the shared response policy."""
    status = (
        HTTPStatus(exception.status_code)
        if isinstance(exception, HTTPException)
        else HTTPStatus.SERVICE_UNAVAILABLE
        if isinstance(
            exception,
            (OSError, ValueError, TypeError, RecursionError, OverflowError),
        )
        else HTTPStatus.INTERNAL_SERVER_ERROR
    )
    return _response_policy(request, _error(status))


@asynccontextmanager
async def _website_lifecycle(_app: FastAPI) -> AsyncIterator[None]:
    """Record web application startup and orderly shutdown.

    Yields:
        The active ASGI application lifetime.
    """
    _LOGGER.info(
        "Aranami web application started.",
        extra=event_extra("service_started", service="web"),
    )
    try:
        yield
    finally:
        _LOGGER.info(
            "Aranami web application stopped.",
            extra=event_extra("service_stopped", service="web"),
        )


def create_app(runtime: Path, assets: Path = _ASSETS) -> FastAPI:
    """Create an isolated read-only website without starting any jobs.

    Args:
        runtime: Tool home containing saved data, state, and logs.
        assets: Source directory containing public browser assets.

    Returns:
        Isolated FastAPI application ready for ASGI serving or tests.
    """
    app = FastAPI(
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=_website_lifecycle,
    )
    app.router.route_class = _PublicRoute
    app.add_middleware(_RequestBounds)
    for exception in (
        HTTPException,
        OSError,
        ValueError,
        TypeError,
        RecursionError,
        OverflowError,
        Exception,
    ):
        app.add_exception_handler(exception, _routing_error)
    views = _PublicViews(runtime, assets)
    for path in (
        "/",
        "/about/",
        "/maintenance/",
        "/reports/",
        "/logs/",
        "/newsletter/",
    ):
        app.add_api_route(path, views.page, methods=["GET", "HEAD"])
    for kind in ("config", "status", "live", "dashboard"):
        app.add_api_route(f"/api/{kind}", views.api, methods=["GET", "HEAD"])
    app.add_api_route("/api/logs", views.api_logs, methods=["GET", "HEAD"])
    app.add_api_websocket_route("/api/live/ws", views.live_socket)
    app.add_api_route("/{path:path}", views.asset, methods=["GET", "HEAD"])
    return app


def main(argv: Sequence[str] | None = None) -> None:
    """Run Uvicorn with explicit runtime paths and bounded resources.

    Args:
        argv: Optional command-line arguments for offline verification.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path.cwd())
    parser.add_argument("--host", default="0.0.0.0")  # ruff: ignore[hardcoded-bind-all-interfaces]
    parser.add_argument("--port", type=int, default=8000)
    options = parser.parse_args(argv)
    if not 0 <= options.port <= _MAX_PORT:
        parser.error("--port must be between 0 and 65535")
    with open_run_log(options.runtime / "logs"), suppress(KeyboardInterrupt):
        uvicorn.run(
            create_app(options.runtime),
            host=options.host,
            port=options.port,
            limit_concurrency=64,
            ws="websockets-sansio",
            ws_max_size=_SOCKET_MESSAGE_BYTES,
            ws_max_queue=16,
            ws_ping_interval=_SOCKET_HEARTBEAT_SECONDS,
            ws_ping_timeout=_SOCKET_HEARTBEAT_SECONDS,
            timeout_keep_alive=15,
            h11_max_incomplete_event_size=64 * 1024,
            proxy_headers=False,
            access_log=False,
            server_header=False,
            log_level="warning",
        )


if __name__ == "__main__":
    main()
