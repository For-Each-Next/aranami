"""Read bounded exact talk history for requested assessment inference.

High-level Pywikibot owns API parameters. Each call follows a bounded
chain of exact parent IDs and caps individual source bodies. Same-second
edits remain visible without timestamp cursor gaps.
"""

# Source, SDK and storage contract errors propagate through helpers.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pywikibot

from shared.sources.quarry.assessment import listing_revision
from shared.support.activity import report_activity

if TYPE_CHECKING:
    from collections.abc import Callable

    from pywikibot.site import BaseSite


MAX_REVISIONS = 20
_REQUEST_INTERVAL = 0.2
_MAX_TEXT_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class HistoricalRevision:
    """Carry exact revision identity and nullable content."""

    revision_id: int
    parent_id: int
    timestamp: dt.datetime
    text: str | None


@dataclass(slots=True)
class HistoryBudget:
    """Admit and pace all inference reads in one bounded source pass.

    Attributes:
        deadline: Monotonic admission deadline, or none for a direct
            read.
        should_yield: Optional foreground priority or cancellation
            callback.
        previous_attempt: Monotonic start of the last admitted source
            read.
    """

    deadline: float | None = None
    should_yield: Callable[[], bool] | None = None
    previous_attempt: float = 0

    def admit(self) -> bool:
        """Pace and admit a read while this pass stays active.

        Returns:
            Whether a new high-level source read may begin.
        """
        if self._stopped():
            return False
        time.sleep(
            max(
                0,
                self.previous_attempt + _REQUEST_INTERVAL - time.monotonic(),
            ),
        )
        if self._stopped():
            return False
        self.previous_attempt = time.monotonic()
        return True

    def _stopped(self) -> bool:
        """Check deadline and source-owner cancellation without waiting.

        Returns:
            Whether the next request must defer to another pass.
        """
        return (
            self.deadline is not None and time.monotonic() >= self.deadline
        ) or (self.should_yield is not None and self.should_yield())


class AssessmentCohortChangedError(ValueError):
    """Signal a source talk newer than the requested cohort."""


def _restriction_flags(revision: object) -> tuple[str, ...]:
    """Find explicit restriction flags without guessing from absence.

    Returns:
        Exact restriction flag names positively supplied by the API.
    """
    slots = revision.get("slots") or {}
    main = slots.get("main", {}) if isinstance(slots, dict) else {}
    return tuple(
        f"{layer}.{flag}"
        for layer, record in (("revision", revision), ("main", main))
        for flag in ("texthidden", "sha1hidden")
        if flag in record
        and (
            record[flag] is True
            or (isinstance(record[flag], str) and not record[flag])
        )
    )


def _revision_text(revision: object) -> str | None:
    """Read visible exact wikitext without template expansion.

    Returns:
        Source text, or none for inaccessible or unsupported content.
    """
    if (
        _restriction_flags(revision)
        or revision.get("contentmodel") != "wikitext"
    ):
        return None
    return revision.get("text")


def _selected_page(
    site: BaseSite,
    page_id: int,
    title: str,
    selected_revision: int,
) -> pywikibot.Page:
    """Check the selected talk identity without fetching its body.

    Returns:
        High-level page matching the exact requested cohort.

    Raises:
        ValueError: The title belongs to another talk page.
        AssessmentCohortChangedError: The current revision has changed.
    """
    report_activity(
        "mediawiki",
        "assessment_history",
        wiki=site.dbName(),
        target=title,
        references=[("page_id", page_id)],
    )
    page = pywikibot.Page(site, title)
    latest = next(iter(page.revisions(content=False, total=1)), None)
    if page.pageid != page_id:
        message = "Assessment history belongs to another talk-page ID."
        raise ValueError(message)
    if latest is None or latest.revid != selected_revision:
        message = "Latest talk revision differs from the selected cohort."
        raise AssessmentCohortChangedError(message)
    return page


def read_talk_revisions(  # ruff: ignore[too-many-arguments,complex-structure] - Validate exact identities and one bounded SDK batch together.
    site: BaseSite,
    *,
    page_id: int,
    title: str,
    selected_revision: int,
    start_revision: int | None = None,
    limit: int = 5,
    budget: HistoryBudget | None = None,
    stop_revision: int | None = None,
) -> tuple[HistoricalRevision, ...]:
    """Read a bounded exact-parent chain for an unchanged selected talk.

    Args:
        site: Existing read-only wiki client.
        page_id: Exact talk-page identity supplying evidence.
        title: Observed canonical talk title, checked against its ID.
        selected_revision: Latest talk revision required by the cohort.
        start_revision: Exact next parent to resume, or none for latest.
        limit: Maximum exact revision content reads in this batch.
        budget: Shared admission deadline, pacing and yield policy.
        stop_revision: Cached boundary excluded from the content batch.

    Returns:
        Exact newest-first revisions with inaccessible text left
        unknown.

    Raises:
        ValueError: Bounds, ownership, or returned metadata is invalid.
        AssessmentCohortChangedError: Current talk has a newer revision.
    """
    identifiers = (
        page_id,
        selected_revision,
        start_revision or selected_revision,
    )
    if stop_revision is not None:
        identifiers = (*identifiers, stop_revision)
    if (
        any(type(value) is not int or value <= 0 for value in identifiers)
        or type(limit) is not int
        or not 1 <= limit <= MAX_REVISIONS
    ):
        message = "Assessment history requires exact IDs and bounded reads."
        raise ValueError(message)
    admission = budget or HistoryBudget()
    if not admission.admit():
        return ()
    page = _selected_page(site, page_id, title, selected_revision)
    identifier = start_revision or selected_revision
    if stop_revision is not None and identifier <= stop_revision:
        return ()
    if not admission.admit():
        return ()
    options = {"startid": identifier, "total": limit}
    if stop_revision is not None:
        if identifier == stop_revision + 1:
            options = {"revids": [identifier]}
        else:
            options["endid"] = stop_revision + 1
    site.loadrevisions(page, content=True, **options)
    # The supported ID-bounded loader has no public batch iterator.
    # Read its SDK result cache without another API call.
    revisions = sorted(
        (
            revision
            for revision in page._revisions.values()  # ruff: ignore[private-member-access] - SDK result cache after its supported batch loader.
            if revision.revid <= identifier
            and (stop_revision is None or revision.revid > stop_revision)
        ),
        key=lambda revision: revision.revid,
        reverse=True,
    )[:limit]
    result = []
    for revision in revisions:
        if (
            revision.revid != identifier
            or type(revision.parentid) is not int
            or revision.parentid < 0
        ):
            message = "Assessment history returned inconsistent parent IDs."
            raise ValueError(message)
        text = _revision_text(revision)
        if text is not None and (
            not isinstance(text, str) or len(text.encode()) > _MAX_TEXT_BYTES
        ):
            text = None
        result.append(
            HistoricalRevision(
                revision.revid,
                revision.parentid,
                revision.timestamp.replace(tzinfo=dt.UTC),
                text,
            ),
        )
        if text is None:
            break
        identifier = revision.parentid
    return tuple(result)


def article_revision_at(
    site: BaseSite,
    *,
    page_id: int,
    title: str,
    instant: dt.datetime,
    budget: HistoryBudget | None = None,
) -> tuple[int, dt.datetime, bool] | None:
    """Infer the visible subject revision at a proven entry instant.

    Args:
        site: Existing read-only wiki client.
        page_id: Exact subject identity; redirects are not followed.
        title: Selected subject title used to read its history.
        instant: Proven talk transition instant, with UTC timezone.
        budget: Shared pacing and admission limits for this source pass.

    Returns:
        Inferred subject ID/time and a same-second ambiguity flag, or
        none
        when no visible revision precedes the transition. This never
        claims
        to identify the article version actually read by an assessor.

    Raises:
        ValueError: Identity, cutoff, or returned ownership is
            inconsistent.
    """
    if (
        type(page_id) is not int
        or page_id <= 0
        or instant.utcoffset() != dt.timedelta(0)
    ):
        message = (
            "Assessment article selection requires an ID and UTC instant."
        )
        raise ValueError(message)
    if budget is not None and not budget.admit():
        return None
    report_activity(
        "mediawiki",
        "assessment_revision",
        wiki=site.dbName(),
        target=title,
        references=[("page_id", page_id)],
    )
    return listing_revision(site, page_id, instant)
