"""Serve saved website data and live status on Toolforge with Django.

Run ``python -m webservice.server`` as the published web job. Requests
read fixed assets and validated runtime snapshots; they never
start the hub, initialize source clients, or publish wiki changes.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import re
from contextlib import ExitStack, asynccontextmanager, suppress
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlsplit

from django.core.exceptions import TooManyFieldsSent
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBase,
    StreamingHttpResponse,
)
from django.urls import path as url_path, re_path
from polars.exceptions import PolarsError
lazy import uvicorn

from data.articles import quality_pages
from data.caching import consistency as consistency_cache
from data.caching.game_titles import (
    DEFAULT_PATH as GAME_TITLES_PATH,
    read_game_title_labels,
)
from data.consistency import review_repair
from data.newsletter.observations import OBSERVATIONS_PATH
from data.paths import artifact_path
from data.redirects import (
    DEFAULT_PATH as REDIRECTS_PATH,
    read as read_redirects,
)
from data.runtime.events import event_extra
from data.runtime.log_store import (
    available_logs,
    log_selection,
    open_log,
    read_activity,
)
from data.runtime.logs import open_run_log
from data.runtime.server_samples import read_live_status as read_status
from data.runtime.status import get_boot_id, save_status
from data.website import read_snapshot, validate_dashboard_snapshot
from shared.config import routine_configuration
from shared.deployment import project_version, resource_root
from shared.newsletter import Issue
from webservice.asgi import DjangoWebsite, LiveSocket, SocketDisconnectError
from webservice.asset_cache import AssetCache
from webservice.datasets import DatasetSelectionError, read_dataset
from webservice.django_setup import setup_django
from webservice.http import accepts_gzip, encoded_response, read_only_headers
from webservice.live import LiveFeed
from webservice.page_cache import PageCache
lazy from data.newsletter.archive import (
    DEFAULT_PATH as DRAFT_ARCHIVE_PATH,
    NewsletterDraftArchive,
)
lazy from data.newsletter.historical import (
    FrameFigureSource,
    prepare_dyk_series,
    read_historical_data,
)
lazy from data.newsletter.schema import (
    read_newsletter_data,
    with_homepage_forecasts,
)
lazy from updater.services.newsletter import render_issue
lazy from updater.services.newsletter_editorial import render_annual_feature
lazy from webservice import pages
lazy from webservice.presentation import (
    Interface,
    local_url,
    select_locale,
    title_display_scope,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
    from typing import BinaryIO

    from django.core.handlers.asgi import ASGIRequest


_ASSETS = resource_root("webservice") / "public_html"
_MAX_BYTES = 8 * 1024 * 1024
_MAX_QUERY_BYTES = 4096
_MAX_QUERY_FIELDS = 12
_MAX_HEADER_BYTES = 64 * 1024
_MAX_PORT = 65535
_CHUNK_BYTES = 64 * 1024
_SOCKET_CLIENTS = 16
_SOCKET_MESSAGE_BYTES = 512
_SOCKET_HEARTBEAT_SECONDS = 15
_SOCKET_SEND_SECONDS = 5
_WEB_HEARTBEAT_SECONDS = 30
_LOGGER = logging.getLogger(__name__)
_LANGUAGES = {"en": "English", "zh-hans": "简体中文", "zh-hant": "繁体中文"}
_LANGUAGE_COOKIE = "aranami-language"


def _legacy_checks_url(
    path: str,
    parameters: dict[str, list[str]],
    query: str,
) -> str | None:
    """Preserve old checks links after moving them to their own route.

    Returns:
        The replacement URL, or None for unrelated requests.
    """
    task = parameters.get("task", [])
    if path == "/maintenance/checks/" or (
        path == "/maintenance/"
        and len(task) == 1
        and task[0].startswith("consistency_")
    ):
        return "/checks/" + (f"?{query}" if query else "")
    return None


_ATTRIBUTION_PATHS = (
    "/assets/icons/credits.html",
    "/assets/icons/maintenance/credits.html",
    "/codex-license.html",
    "/assets/icons/gpl-2.0.html",
    "/assets/icons/lgpl-2.1.html",
)
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
    """Offer unpublished drafts and an early annual feature.

    Returns:
        Calendar, retained, and captured choices not known to be closed.
    """
    choices = list(
        dict.fromkeys([automatic.previous, automatic.value, selected.value]),
    )
    annual = Issue.parse(f"{automatic.year + int(automatic.month != 1)}-01")
    if annual.value not in choices and archive.feature_dates(annual):
        choices.append(annual.value)
    lifecycle = (retained or {}).get("lifecycle") or {}
    states = lifecycle.get("states", {})
    candidates = (
        set(choices)
        | set((retained or {}).get("issues", {}))
        | set(lifecycle.get("active", []))
    )
    return sorted(
        choice
        for choice in candidates
        if states.get(choice) not in {"published", "cancelled"}
    )


def _newsletter_feature(  # ruff: ignore[too-many-arguments]
    archive: NewsletterDraftArchive,
    issue: Issue,
    requested: str | None,
    value: dict,
    current_facts: dict | None = None,
    *,
    runtime: Path,
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
            "text": render_annual_feature(
                issue,
                _newsletter_figures(issue, entry["year_review"], runtime),
            ),
            "data_through": selected,
        })
        value["available"] = True
    return {
        "selected_date": selected,
        "generated_at": entry["generated_at"] if entry else None,
        "missing": selected is not None and entry is None,
    }


def _newsletter_figures(
    issue: Issue,
    review: dict | None,
    runtime: Path,
) -> dict | None:
    """Enrich an older annual review from validated canonical figures.

    Args:
        issue: Selected issue identifying the annual reporting year.
        review: Saved annual facts, including any prepared DYK series.
        runtime: Explicit runtime whose retained figures may be read.

    Returns:
        Original facts when already prepared or unavailable; otherwise
        a copied review with covered historical points. No saved facts
        or filesystem paths are changed.

    Raises:
        OSError: Existing canonical data cannot be safely read.
        ValueError: Existing canonical data fails validation.
    """  # ruff: ignore[docstring-extraneous-exception]
    if (
        issue.month != 1
        or review is None
        or review.get("dyk_series") is not None
    ):
        return review
    bundle = read_historical_data(runtime)
    if bundle is None:
        return review
    series = prepare_dyk_series(
        review,
        issue.year - 1,
        source=FrameFigureSource(
            "canonical-historical",
            bundle.tables["figures"],
        ),
    )
    return {**review, "dyk_series": series} if series else review


def _newsletter_facts(issue: Issue, facts: dict, runtime: Path) -> dict:
    """Supply canonical figure enrichment to an older full issue.

    Returns:
        A facts copy only when its annual review needs enrichment.
    """
    original = facts.get("year_review")
    review = _newsletter_figures(issue, original, runtime)
    return (
        {**facts, "year_review": review} if review is not original else facts
    )


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


def _json(
    value: object,
    status: HTTPStatus = HTTPStatus.OK,
) -> HttpResponseBase:
    """Return validated metadata encoded as standard JSON."""
    return HttpResponse(
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def _error(status: HTTPStatus) -> HttpResponseBase:
    """Return a JSON error without framework HTML templates."""
    return _json({"error": status.phrase}, status)


def _validate_request(request: ASGIRequest) -> HttpResponseBase | None:
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
        or query.count(b"&") >= _MAX_QUERY_FIELDS
    ):
        return _error(HTTPStatus.BAD_REQUEST)
    if any(token in target.path for token in ("\\", "%", "//")) or any(
        part in {".", ".."} for part in target.path.split("/")
    ):
        return _error(HTTPStatus.NOT_FOUND)
    return None


def _request_size_error(request: ASGIRequest) -> HttpResponseBase | None:
    """Bound headers and declared bodies without consuming input.

    Returns:
        A size or syntax rejection, or None for bounded input.
    """
    try:
        lengths = [
            value.decode("ascii")
            for name, value in request.scope["headers"]
            if name.lower() == b"content-length"
        ]
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


class _AssetHttpResponse(HttpResponse):
    """Mark encoded assets with conditional browser caching."""


def _response_policy(
    request: ASGIRequest,
    response: HttpResponseBase,
) -> HttpResponseBase:
    """Return shared headers and gzip without buffering log streams."""
    for name, value in read_only_headers(
        response.status_code,
        websocket_origin="wss://aranami.toolforge.org",
        wikipedia_refresh=request.path == "/redirects/",
    ).items():
        if name == "Cache-Control" and isinstance(
            response,
            _AssetHttpResponse,
        ):
            continue
        response.headers[name] = value
    language = _explicit_language(request)
    if language is not None and not isinstance(response, _AssetHttpResponse):
        response.set_cookie(
            _LANGUAGE_COOKIE,
            language,
            max_age=365 * 24 * 60 * 60,
            httponly=True,
            secure=request.headers.get("Host", "").split(":", 1)[0]
            == "aranami.toolforge.org"
            or request.is_secure(),
            samesite="lax",
        )
    if (
        not response.streaming
        and "Content-Disposition" not in response.headers
    ):
        if isinstance(response, _AssetHttpResponse):
            content, headers = response.content, {}
        else:
            content, headers = encoded_response(
                response.content,
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
        response.content = b"" if request.method == "HEAD" else content

    return response


def _html(
    content: str,
    locale: str,
    status: HTTPStatus = HTTPStatus.OK,
) -> HttpResponseBase:
    """Return a complete localized server-rendered document."""
    return HttpResponse(
        content.encode(),
        status=status,
        content_type="text/html; charset=utf-8",
        headers={
            "Content-Language": Interface(locale).language_tag,
            "Vary": "Accept-Language, Cookie",
        },
    )


def _explicit_language(request: ASGIRequest) -> str | None:
    """Read one supported explicit language choice.

    Returns:
        The selected locale, or none for absent or invalid choices.
    """
    try:
        values = request.GET.getlist("lang")
    except TooManyFieldsSent:
        return None
    return (
        values[0].lower()
        if len(values) == 1 and values[0].lower() in _LANGUAGES
        else None
    )


def _request_locale(request: ASGIRequest) -> str:
    """Prefer an explicit choice, then cookie, then browser language.

    Returns:
        A supported interface locale without changing source content.
    """
    chosen = _explicit_language(request)
    saved = request.COOKIES.get(_LANGUAGE_COOKIE)
    browser = select_locale(request.headers.get("Accept-Language", ""))
    selected = chosen or (saved if saved in _LANGUAGES else browser)
    if selected == "zh-hant" and browser in {"zh-tw", "zh-hk"}:
        return browser
    return selected


def _navigation(request: ASGIRequest, locale: str) -> dict[str, object]:
    """Build language links retaining the current route and query state.

    Returns:
        Safe local URLs and the active page for shared navigation.
    """
    parameters = {
        key: value
        for key, value in request.GET.items()
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
            "run_id",
            "stream",
            "issue",
            "file",
            "count_b",
            "listing_authors",
            "all_grades",
        }
    }
    return {
        "path": request.path,
        "languages": [
            {
                "label": label,
                "tag": {"zh-hans": "zh-Hans", "zh-hant": "zh-Hant"}.get(
                    language,
                    language,
                ),
                "url": local_url(
                    request.path,
                    **parameters,
                    lang=language,
                ),
                "current": language == Interface(locale).message_locale
                or (language == "zh-hans" and locale == "zh"),
            }
            for language, label in _LANGUAGES.items()
        ],
    }


def _page_error(key: str, locale: str, status: HTTPStatus) -> HttpResponseBase:
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


class _DownloadHttpResponse(StreamingHttpResponse):
    """Close bounded downloads on completion or disconnect."""

    def __init__(
        self,
        *args: object,
        resources: ExitStack,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.resources = resources

    def close(self) -> None:
        """Release the log on completion or client disconnection."""
        try:
            super().close()
        finally:
            self.resources.close()


def _download(
    request: ASGIRequest,
    directory: Path,
    stream_name: str,
    filename: str,
    *,
    attachment: bool,
) -> StreamingHttpResponse:
    """Stream a log inline or as an attachment, closing on abort.

    Args:
        request: Selection of GET content or HEAD metadata.
        directory: Existing runtime log directory.
        stream_name: Validated activity or diagnostics stream.
        filename: Exact validated segment filename.
        attachment: Force a download only for an explicit request.

    Returns:
        A bounded plain-text response retaining the safe reader.
    """
    with ExitStack() as resources:
        stream = resources.enter_context(
            open_log(directory, stream_name, filename),
        )
        size = os.fstat(stream.fileno()).st_size
        response = _DownloadHttpResponse(
            _download_chunks(stream, 0)
            if request.method == "HEAD"
            else _download_chunks(stream, size),
            content_type="text/plain; charset=utf-8",
            headers={
                "Content-Length": str(size),
                "Content-Disposition": (
                    f"{'attachment' if attachment else 'inline'}; "
                    f'filename="{filename}"'
                ),
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
        self.live_feed = LiveFeed(
            lambda: read_status(self.runtime),
            lambda status, locale: pages.render_live_status(
                status,
                _configuration(),
                locale,
            ),
            self._live_identity,
        )

    def page(self, request: ASGIRequest) -> HttpResponseBase:
        """Render using saved conversion labels without external reads.

        Returns:
            The response localized within an isolated title-label scope.
        """
        labels = {}
        title_identity = None
        if (
            _request_locale(request) == "zh-tw"
            and not (self.runtime / "cache").is_symlink()
        ):
            title_identity, labels = read_game_title_labels(
                self.runtime / GAME_TITLES_PATH,
            )
        with title_display_scope(labels):
            return self._page(request, title_identity=title_identity)

    def _page(  # ruff: ignore[complex-structure,too-many-locals]
        self,
        request: ASGIRequest,
        *,
        title_identity: tuple | None,
    ) -> HttpResponseBase:
        """Return a page cached by locale and saved-input identity."""
        path = request.path
        locale = _request_locale(request)
        direct = {
            "/reports/": self.reports,
            "/logs/": self.logs,
            "/data/": self.dataset,
        }.get(path)
        if direct is not None:
            return direct(request, locale=locale)
        try:
            query = request.scope.get("query_string", b"").decode("ascii")
            parameters = parse_qs(
                query,
                keep_blank_values=True,
                errors="strict",
                max_num_fields=12
                if path in {"/maintenance/", "/checks/"}
                else 5,
            )
        except ValueError, UnicodeError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        legacy_url = _legacy_checks_url(path, parameters, query)
        if legacy_url:
            return HttpResponse(
                status=HTTPStatus.TEMPORARY_REDIRECT,
                headers={"Location": legacy_url},
            )
        parameters.pop("lang", None)
        navigation = _navigation(request, locale)
        saved_tool = {
            "/redirects/": self._redirects_page,
            "/quality-authorship/": self._quality_authorship_page,
            "/quality-analysis/": self._quality_analysis_page,
        }.get(path)
        if saved_tool is not None:
            return saved_tool(parameters, locale, navigation)
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
        identity = (*self._page_identity(), title_identity)
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
        elif path in {"/about/", *_ATTRIBUTION_PATHS}:
            response = self._information_page(path, locale, navigation)
        else:
            snapshot = self._dashboard()
            renderer = (
                pages.render_discussions
                if path == "/discussions/"
                else pages.render_dashboard
            )
            response = (
                _html(
                    renderer(
                        snapshot,
                        read_status(self.runtime),
                        _configuration(),
                        locale,
                        navigation=navigation,
                    ),
                    locale,
                )
                if path in {"/", "/discussions/"}
                else self._maintenance_page(
                    snapshot,
                    parameters,
                    locale,
                    navigation,
                )
            )
        if not randomized and response.status_code == HTTPStatus.OK:
            self.pages.put(key, identity, response.content.decode())
        return response

    def _quality_analysis_page(
        self,
        parameters: dict[str, list[str]],
        locale: str,
        navigation: dict[str, object],
    ) -> HttpResponseBase:
        """Read precomputed metrics and reuse bounded rendered HTML.

        Returns:
            A complete analysis page without source acquisition or
            request-time statistical calculations.
        """
        if set(parameters) - {
            "count_b",
            "listing_authors",
            "all_grades",
        } or parameters.get(
            "count_b",
            ["0"],
        ) not in (["0"], ["1"]):
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        if any(
            parameters.get(name, ["0"]) not in (["0"], ["1"])
            for name in ("listing_authors", "all_grades")
        ):
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        basis = (
            "listed"
            if parameters.get("listing_authors") == ["1"]
            else "current"
        )
        include_b = parameters.get("count_b") == ["1"]
        all_grades = parameters.get("all_grades") == ["1"]
        key = ("/quality-analysis/", locale, include_b, basis, all_grades)
        try:  # ruff: ignore[too-many-statements-in-try-clause] - One optional prepared-page read boundary.
            preset = quality_pages.path_for(
                self.runtime,
                Interface(locale).message_locale,
                include_b=include_b,
                basis=basis,
                all_grades=all_grades,
            )
            identity = quality_pages.identity(preset)
            content = self.pages.get(key, identity)
            if content is None:
                content = quality_pages.read(preset)
                if content is not None:
                    self.pages.put(key, identity, content)
            if content is not None:
                return _html(content, locale)
        except OSError, ValueError, PolarsError:
            pass
        # Missing preparation remains explicitly unavailable.
        # Requests never render a cohort from underlying facts.
        return _html(
            pages.render_quality_analysis(
                {
                    "analysis": {"available": False, "include_b": include_b},
                    "authorship": {"available": False, "authors": []},
                },
                read_status(self.runtime),
                _configuration(),
                locale,
                navigation=navigation,
            ),
            locale,
        )

    @staticmethod
    def _quality_authorship_page(
        parameters: dict[str, list[str]],
        locale: str,
        _navigation: dict[str, object],
    ) -> HttpResponseBase:
        """Redirect the former author directory to the merged page.

        Returns:
            A canonical saved-data URL preserving the selected cohort.
        """
        if set(parameters) - {"count_b"} or parameters.get(
            "count_b",
            ["0"],
        ) not in (["0"], ["1"]):
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        query = {"lang": locale}
        if parameters.get("count_b") == ["1"]:
            query["count_b"] = "1"
        return HttpResponse(
            status=HTTPStatus.MOVED_PERMANENTLY,
            headers={
                "Location": "/quality-analysis/?" + urlencode(query),
            },
        )

    def _redirects_page(
        self,
        parameters: dict[str, list[str]],
        locale: str,
        navigation: dict[str, object],
    ) -> HttpResponseBase:
        """Read incremental saved scan data without starting collection.

        Returns:
            A bounded worklist or a localized invalid-selection error.
        """
        unavailable = False
        try:
            snapshot = read_redirects(self.runtime / REDIRECTS_PATH)
        except FileNotFoundError:
            snapshot = None
        except OSError, ValueError:
            snapshot = None
            unavailable = True
        try:
            content = pages.render_redirects(
                snapshot,
                parameters,
                locale,
                navigation=navigation,
                status=read_status(self.runtime),
                config=_configuration(),
                unavailable=unavailable,
            )
        except ValueError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        return _html(content, locale)

    def _information_page(
        self,
        path: str,
        locale: str,
        navigation: dict[str, object],
    ) -> HttpResponseBase:
        """Render information and credits in the shared page shell.

        Returns:
            Localized HTML with the same saved status and navigation.
        """
        status = read_status(self.runtime)
        configuration = _configuration()
        content = (
            pages.render_about(
                configuration,
                locale,
                status=status,
                navigation=navigation,
            )
            if path == "/about/"
            else pages.render_attribution(
                path,
                locale,
                navigation=navigation,
                status=status,
                config=configuration,
            )
        )
        return _html(content, locale)

    def _newsletter_context(
        self,
        parameters: dict[str, list[str]],
    ) -> tuple[Issue, dict | None, bool]:
        """Select the next open issue from saved publication states.

        Returns:
            The default issue, current facts, and a read-error flag.
            Saved dates survive unavailable current metadata.
        """
        automatic = Issue.upcoming(dt.datetime.now(dt.UTC).date())
        try:
            retained = read_newsletter_data(self.runtime / OBSERVATIONS_PATH)
        except OSError, ValueError:
            dated = bool({"date", "feature_date"}.intersection(parameters))
            return automatic, None, not dated
        states = ((retained or {}).get("lifecycle") or {}).get("states", {})
        while states.get(automatic.value) in {"published", "cancelled"}:
            automatic = Issue.parse(automatic.next)
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
            issue, _ = _newsletter_selection(parameters, automatic_issue)
            store = NewsletterDraftArchive(
                self.runtime / DRAFT_ARCHIVE_PATH,
            )
            archive = store.identity(issue)
            feature_dates = (
                tuple(store.feature_dates(issue)) if issue.month == 1 else ()
            )
            figures = (
                read_historical_data(self.runtime)
                if issue.month == 1
                else None
            )
        except OSError, ValueError:
            return None
        figure_identity = (
            (
                figures.manifest["generation"],
                figures.manifest["tables"]["figures"]["sha256"],
            )
            if figures is not None
            else None
        )
        pointer = artifact_path(
            self.runtime / OBSERVATIONS_PATH,
            "current.parquet",
        )
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
        return archive, observation, feature_dates, figure_identity

    def _newsletter_saved(
        self,
        archive: NewsletterDraftArchive,
        issue: Issue,
        day: dt.date | None,
        retained: dict | None,
    ) -> tuple[dt.date | None, list[str], dict | None, dict | None]:
        """Read selected saved facts with optional canonical figures.

        Returns:
            Selected date, available dates, current facts, and the dated
            archive entry. Canonical enrichment changes only copies.
        """
        durable_facts = (
            (retained or {}).get("issues", {}).get(issue.value)
            if day is None
            else None
        )
        dates = _newsletter_dates(archive, issue)
        if day is None and dates and durable_facts is None:
            day = dt.date.fromisoformat(dates[-1])
        entry = (
            archive.read_entry(issue, day)
            if day is not None and issue.start <= day < issue.end
            else None
        )
        if durable_facts is not None:
            durable_facts = _newsletter_facts(
                issue,
                durable_facts,
                self.runtime,
            )
        if entry is not None:
            entry = {
                **entry,
                "facts": _newsletter_facts(
                    issue,
                    entry["facts"],
                    self.runtime,
                ),
            }
        return day, dates, durable_facts, entry

    def _newsletter_page(
        self,
        parameters: dict[str, list[str]],
        locale: str,
        navigation: dict[str, object],
        context: tuple[Issue, dict | None, bool],
    ) -> HttpResponseBase:
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
            day, dates, durable_facts, entry = self._newsletter_saved(
                archive,
                issue,
                day,
                retained,
            )
        except OSError, ValueError:
            return _page_error(
                "newsletter.history.read_error",
                locale,
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if durable_facts is not None:
            state = (
                ((retained or {}).get("lifecycle") or {})
                .get("states", {})
                .get(issue.value)
            )
            if (
                day is None
                and not durable_facts.get("complete")
                and state not in {"published", "cancelled"}
            ):
                durable_facts = with_homepage_forecasts(
                    durable_facts,
                    self._dashboard() or {},
                    now=dt.datetime.now(dt.UTC),
                )
            value = render_issue(issue, durable_facts)
            generated_at = durable_facts["updated_at"]
            day = _newsletter_current_day(issue, durable_facts, dates)
        elif entry is not None:
            value = render_issue(issue, entry["facts"])
            generated_at = entry["generated_at"]
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
                runtime=self.runtime,
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
                status=read_status(self.runtime),
                config=_configuration(),
                navigation=navigation,
            ),
            locale,
        )

    def _maintenance_page(  # ruff: ignore[too-many-return-statements]
        self,
        snapshot: dict[str, object] | None,
        parameters: dict[str, list[str]],
        locale: str,
        navigation: dict[str, object],
    ) -> HttpResponseBase:
        """Return saved worklists or the separate checks page."""
        snapshot = self._with_consistency_repairs(snapshot)
        if navigation["path"] == "/checks/" and set(parameters) <= {"page"}:
            try:
                content = pages.render_consistency_checks(
                    snapshot,
                    read_status(self.runtime),
                    _configuration(),
                    locale,
                    navigation=navigation,
                    parameters=parameters,
                )
            except ValueError:
                return _page_error(
                    "backlog.not_found",
                    locale,
                    HTTPStatus.BAD_REQUEST,
                )
            return _html(content, locale)
        if navigation["path"] == "/checks/" and not (
            len(parameters.get("task", [])) == 1
            and parameters["task"][0].startswith("consistency_")
        ):
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        if not parameters:
            return _html(
                pages.render_maintenance_overview(
                    snapshot,
                    read_status(self.runtime),
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
                status=read_status(self.runtime),
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
                "data/parsed",
                "data/parsed/dashboard.parquet",
                "data/parsed/dashboard.json",
                "data/parsed/consistency-repairs.parquet",
                str(GAME_TITLES_PATH),
                "state",
                "state/observability",
                "state/observability/status.json",
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
        root = self.runtime / "data/parsed"
        if any(
            path.is_symlink()
            for path in (root, root.parent, root.parent.parent)
        ):
            return None
        path = root / "dashboard.parquet"
        if not path.exists():
            path = root / "dashboard.json"
        try:
            value = read_snapshot(path)
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

    def _with_consistency_repairs(
        self,
        snapshot: dict[str, object] | None,
    ) -> dict[str, object] | None:
        """Read complete corrections for the selected check generation.

        Returns:
            A detached snapshot with the matching prepared consistency
            result, or the compact snapshot when that result is missing,
            malformed or belongs to another check timestamp.
        """
        consistency = (snapshot or {}).get("consistency")
        if (
            not isinstance(consistency, dict)
            or (self.runtime / "cache").is_symlink()
        ):
            return snapshot
        try:
            record = consistency_cache.read(
                self.runtime / "data/parsed/consistency-repairs.parquet",
            )
        except OSError, ValueError, TypeError, RecursionError, OverflowError:
            return snapshot
        prepared = record.get("data")
        if isinstance(prepared, dict) and prepared[
            "checked_at"
        ] == consistency.get("checked_at"):
            return {**snapshot, "consistency": prepared}
        return snapshot

    def api(self, request: ASGIRequest) -> HttpResponseBase:
        """Return data through its runtime validation contract."""
        kind = request.path.rsplit("/", 1)[-1]
        if kind == "config":
            return _json(_configuration())
        if kind == "status":
            return _json(read_status(self.runtime))
        if kind == "consistency-repair":
            return self._consistency_repair_api(request)
        value = self._dashboard()
        return (
            _json(value)
            if value is not None
            else _error(HTTPStatus.SERVICE_UNAVAILABLE)
        )

    def _consistency_repair_api(
        self,
        request: ASGIRequest,
    ) -> HttpResponseBase:
        """Return one saved correction without source acquisition.

        Returns:
            A bounded JSON correction selected by its talk revision ID,
            or a missing/invalid query response.
        """
        try:
            parameters = parse_qs(
                request.scope.get("query_string", b"").decode("ascii"),
                keep_blank_values=True,
                errors="strict",
                max_num_fields=1,
            )
        except ValueError, UnicodeError:
            return _error(HTTPStatus.BAD_REQUEST)
        selected = parameters.get("repair", [])
        if (
            set(parameters) != {"repair"}
            or len(selected) != 1
            or re.fullmatch(r"[1-9][0-9]{0,18}", selected[0]) is None
            or int(selected[0]) >= 2**63
        ):
            return _error(HTTPStatus.BAD_REQUEST)
        snapshot = self._with_consistency_repairs(self._dashboard())
        repair = review_repair(
            (snapshot or {}).get("consistency") or {},
            selected[0],
        )
        return (
            _json({"schema": 1, "repair": repair})
            if repair is not None
            else _error(HTTPStatus.NOT_FOUND)
        )

    def asset(self, request: ASGIRequest) -> HttpResponseBase:
        """Revalidate cached source assets without directory listings.

        Returns:
            Negotiated bytes, an unchanged response, or a missing error.
        """
        path = request.path.lstrip("/")
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
            if request.GET.getlist("v") == [asset.digest]
            else "public, max-age=0, must-revalidate",
            "ETag": asset.etag,
            "Vary": "Accept-Encoding",
        }
        validators = {
            value.strip().removeprefix("W/")
            for value in request.headers.get("If-None-Match", "").split(",")
        }
        if "*" in validators or asset.etag.removeprefix("W/") in validators:
            return _AssetHttpResponse(
                status=HTTPStatus.NOT_MODIFIED,
                headers=headers,
            )
        compressed = asset.gzip and accepts_gzip(
            request.headers.get("Accept-Encoding", ""),
        )
        if compressed:
            headers["Content-Encoding"] = "gzip"
        return _AssetHttpResponse(
            asset.compressed if compressed else asset.content,
            content_type=_CONTENT_TYPES[relative.suffix],
            headers=headers,
        )

    def dataset(
        self,
        request: ASGIRequest,
        *,
        locale: str,
    ) -> HttpResponseBase:
        """Show one saved project capture without live source requests.

        Returns:
            A localized preview, selection help or a bounded error page.
        """
        try:
            parameters = parse_qs(
                request.scope.get("query_string", b"").decode("ascii"),
                keep_blank_values=True,
                errors="strict",
                max_num_fields=4,
            )
        except ValueError, UnicodeError:
            return _page_error(
                "data.invalid",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        page_value = parameters.get("page", ["1"])[0]
        invalid_page = (
            not page_value.isascii()
            or not page_value.isdigit()
            or len(page_value) > len(str(20_000_000))
            or int(page_value) < 1
        )
        if (
            set(parameters) - {"file", "lang", "page"}
            or any(len(values) != 1 for values in parameters.values())
            or ("file" in parameters and not parameters["file"][0])
            or invalid_page
        ):
            return _page_error(
                "data.invalid",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        value = None
        if "file" in parameters:
            try:
                value = read_dataset(
                    parameters["file"][0],
                    self.runtime,
                    int(parameters.get("page", ["1"])[0]),
                )
            except DatasetSelectionError:
                return _page_error(
                    "data.invalid",
                    locale,
                    HTTPStatus.BAD_REQUEST,
                )
            except LookupError:
                return _page_error(
                    "data.missing",
                    locale,
                    HTTPStatus.NOT_FOUND,
                )
            except OSError, ValueError, PolarsError:
                return _page_error(
                    "data.unavailable",
                    locale,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
        return _html(
            pages.render_dataset(
                value,
                locale,
                navigation=_navigation(request, locale),
                status=read_status(self.runtime),
                config=_configuration(),
            ),
            locale,
        )

    def reports(
        self,
        request: ASGIRequest,
        *,
        locale: str,
    ) -> HttpResponseBase:
        """Read per-job history independently of diagnostic logs.

        Returns:
            A Reports page with retained invocations across all dates.
        """
        status = read_status(self.runtime)
        history_page = request.GET.get("page", "1")[:5]
        return _html(
            pages.render_reports(
                status,
                locale,
                status=status,
                config=_configuration(),
                navigation=_navigation(request, locale),
                history_page=int(history_page)
                if history_page.isascii() and history_page.isdecimal()
                else 1,
            ),
            locale,
        )

    def logs(
        self,
        request: ASGIRequest,
        *,
        locale: str | None = None,
    ) -> HttpResponseBase:
        """Read bounded activity or view a saved log segment.

        Returns:
            Structured JSON, a localized page, or bounded plain text.
            Explicit download requests retain attachment behavior.
        """
        directory = self.runtime / "logs"
        files = available_logs(directory)
        try:
            parameters = parse_qs(
                request.scope.get("query_string", b"").decode("ascii"),
                keep_blank_values=True,
                errors="strict",
                max_num_fields=7,
            )
            if locale is not None:
                parameters.pop("lang", None)
            selection = log_selection(parameters, files)
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
        if selection["filename"] is not None or selection["download"]:
            filename = selection["filename"]
            if filename is None or not any(
                row["name"] == filename
                and row["stream"] == selection["stream"]
                for row in files
            ):
                return _error(HTTPStatus.NOT_FOUND)
            return _download(
                request,
                directory,
                selection["stream"],
                filename,
                attachment=selection["download"],
            )
        activity = (
            read_activity(
                directory,
                selection["date"],
                run_id=selection["run_id"],
                query=selection["q"],
            )
            if selection["stream"] == "activity"
            else {
                "events": [],
                "truncated": False,
                "availability": "available"
                if any(
                    row["stream"] == "diagnostics"
                    and row["date"] == selection["date"]
                    for row in files
                )
                else "missing",
            }
        )
        value = {
            "schema": 1,
            "observed_at": dt.datetime.now(dt.UTC).isoformat(),
            "files": files,
            "date": selection["date"],
            "stream": selection["stream"],
            "query": selection["q"],
            "run_id": selection["run_id"],
            **activity,
        }
        return (
            _json(value)
            if locale is None
            else _html(
                pages.render_logs(
                    value,
                    locale,
                    status=read_status(self.runtime),
                    config=_configuration(),
                    navigation=_navigation(request, locale),
                ),
                locale,
            )
        )

    async def live_api(self, request: ASGIRequest) -> HttpResponseBase:
        """Return the ordered snapshot shared with WebSocket clients."""
        locale = _request_locale(request)
        frame = await self.live_feed.snapshot(locale)
        etag = f'"{frame["instance"]}:{frame["sequence"]}:{locale}"'
        if request.headers.get("If-None-Match") == etag:
            return HttpResponse(
                status=HTTPStatus.NOT_MODIFIED,
                headers={
                    "ETag": etag,
                    "Content-Language": Interface(locale).language_tag,
                    "Vary": "Accept-Language, Cookie",
                },
            )
        response = _json(frame)
        response.headers["ETag"] = etag
        response.headers["Content-Language"] = Interface(locale).language_tag
        response.headers["Vary"] = "Accept-Language, Cookie"
        return response

    def api_logs(self, request: ASGIRequest) -> HttpResponseBase:
        """Return the log API with its fixed presentation contract."""
        return self.logs(request)

    def _live_identity(self) -> tuple:
        """Return saved-status metadata without reading datasets."""
        identities = []
        for name in (
            "state/observability",
            "state/observability/status.json",
            "state/observability/server-sample.json",
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

    async def live_socket(self, websocket: LiveSocket) -> None:
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
        except SocketDisconnectError, TimeoutError, OSError:
            pass
        finally:
            self.socket_clients -= 1
            if receiver is not None:
                receiver.cancel()
                with suppress(asyncio.CancelledError, SocketDisconnectError):
                    await receiver
            if websocket.connected:
                with suppress(
                    SocketDisconnectError,
                    RuntimeError,
                    TimeoutError,
                ):
                    await asyncio.wait_for(
                        websocket.close(code=close_code),
                        timeout=1,
                    )

    async def _watch_live(
        self,
        websocket: LiveSocket,
        locale: str,
        receiver: asyncio.Task[int],
    ) -> int:
        """Watch saved metadata while one task owns all outgoing frames.

        Returns:
            The close code observed by the read-only message receiver.
        """
        async with self.live_feed.subscribe(locale) as subscription:
            while not receiver.done():
                pending = asyncio.create_task(subscription.receive())
                try:
                    done, _ = await asyncio.wait(
                        (pending, receiver),
                        timeout=_SOCKET_HEARTBEAT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if receiver in done:
                        return receiver.result()
                    frame = (
                        pending.result()
                        if pending in done
                        else {
                            "schema": 1,
                            "type": "heartbeat",
                            "instance": self.live_feed.instance,
                            "sequence": self.live_feed.sequence,
                        }
                    )
                    await asyncio.wait_for(
                        websocket.send_json(frame),
                        timeout=_SOCKET_SEND_SECONDS,
                    )
                finally:
                    if not pending.done():
                        pending.cancel()
                        with suppress(asyncio.CancelledError):
                            await pending
            return receiver.result()


def _socket_locale(websocket: LiveSocket) -> str | None:
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


def _socket_origin(websocket: LiveSocket) -> bool:
    """Check the actual host origin and Toolforge TLS termination.

    Returns:
        Whether the caller is non-browser or has the matching origin.
    """
    origins = websocket.headers("origin")
    if not origins:
        return True
    hosts = websocket.headers("host")
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


async def _socket_receive(websocket: LiveSocket) -> int:
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


def _routing_error(
    request: ASGIRequest,
    exception: Exception,
) -> HttpResponseBase:
    """Return sanitized errors with the shared response policy."""
    status = (
        HTTPStatus.NOT_FOUND
        if isinstance(exception, Http404)
        else HTTPStatus.SERVICE_UNAVAILABLE
        if isinstance(
            exception,
            (OSError, ValueError, TypeError, RecursionError, OverflowError),
        )
        else HTTPStatus.INTERNAL_SERVER_ERROR
    )
    return _response_policy(request, _error(status))


def _route(handler: Callable) -> Callable:
    """Apply response policy and sanitize errors around a Django view.

    Returns:
        A synchronous or asynchronous view matching the supplied reader.
    """
    if asyncio.iscoroutinefunction(handler):

        async def async_view(request: ASGIRequest) -> HttpResponseBase:
            """Render an asynchronous saved-status response.

            Returns:
                A bounded response with public error and header policy.
            """
            try:
                return _response_policy(request, await handler(request))
            except Exception as error:  # ruff: ignore[blind-except] - Sanitize the public boundary.
                return _routing_error(request, error)

        return async_view

    def view(request: ASGIRequest) -> HttpResponseBase:
        """Render a saved page in Django's worker thread.

        Returns:
            A bounded response with public error and header policy.
        """
        try:
            return _response_policy(request, handler(request))
        except Exception as error:  # ruff: ignore[blind-except] - Sanitize the public boundary.
            return _routing_error(request, error)

    return view


@asynccontextmanager
async def _website_lifecycle(app: DjangoWebsite) -> AsyncIterator[None]:
    """Record web lifecycle and heartbeats outside request handling.

    Yields:
        The active application lifetime with one bounded heartbeat task.
    """
    started = dt.datetime.now(dt.UTC).isoformat()
    sequence = 0

    async def observe(lifecycle: str) -> None:
        """Persist one actual service observation."""
        nonlocal sequence
        sequence += 1
        now = dt.datetime.now(dt.UTC).isoformat()
        await asyncio.to_thread(
            save_status,
            "services",
            "web",
            {
                "service_id": "web",
                "boot_id": get_boot_id(),
                "lifecycle": lifecycle,
                "started_at": started,
                "heartbeat_at": now,
                "updated_at": now,
                "sequence": sequence,
                "config_id": None,
                "schedules": {"routines": {}, "collections": {}},
                "resources": None,
            },
            runtime=app.state.runtime,
        )

    async def heartbeat() -> None:
        """Refresh service freshness until lifespan shutdown."""
        while True:
            await asyncio.sleep(_WEB_HEARTBEAT_SECONDS)
            app.state.page_cache.expire()
            await observe("running")

    await observe("starting")
    _LOGGER.info(
        "Aranami web application started.",
        extra=event_extra("service_started", service="web"),
    )
    await observe("running")
    task = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await app.state.live_feed.close()
        await observe("stopped")
        _LOGGER.info(
            "Aranami web application stopped.",
            extra=event_extra("service_stopped", service="web"),
        )


def create_app(runtime: Path, assets: Path = _ASSETS) -> DjangoWebsite:
    """Create an isolated Django website without starting any jobs.

    Args:
        runtime: Tool home containing saved data, state, and logs.
        assets: Source directory containing public browser assets.

    Returns:
        Read-only Django application ready for ASGI serving or tests.
    """
    setup_django()
    views = _PublicViews(runtime, assets)
    patterns = [
        url_path(route.lstrip("/"), _route(views.page))
        for route in (
            "/",
            "/discussions/",
            "/quality-authorship/",
            "/quality-analysis/",
            "/about/",
            "/maintenance/",
            "/maintenance/checks/",
            "/checks/",
            "/redirects/",
            "/reports/",
            "/data/",
            "/logs/",
            "/newsletter/",
            *_ATTRIBUTION_PATHS,
        )
    ]
    patterns.extend(
        url_path(f"api/{kind}", _route(views.api))
        for kind in ("config", "status", "dashboard", "consistency-repair")
    )
    patterns.extend([
        url_path("api/live", _route(views.live_api)),
        url_path("api/logs", _route(views.api_logs)),
        re_path(r"^.*$", _route(views.asset)),
    ])
    return DjangoWebsite(
        patterns,
        state=SimpleNamespace(
            runtime=runtime,
            live_feed=views.live_feed,
            page_cache=views.pages,
        ),
        validate=_validate_request,
        policy=_response_policy,
        socket=views.live_socket,
        lifespan=_website_lifecycle,
    )


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
