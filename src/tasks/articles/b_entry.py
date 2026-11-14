"""Prove the latest grade entry from contiguous talk revisions.

Feed revisions in reverse chronological order through ``advance``. Stop
at
unknown content, a broken parent link, or the first known non-target
predecessor. The state is small and serializable; older grades after an
unknown interval cannot prove the transition. No source reads occur
here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

from shared.assessment_profiles import assessment_profile

TARGET_CLASSES = tuple(
    label.raw_value
    for label in assessment_profile("zhwiki", "电子游戏").classes
    if label.is_article
    and label.code not in {"FA", "FL", "A", "AL", "GA", "Bplus"}
)


@dataclass(frozen=True, slots=True)
class AssessmentRevision:
    """Describe one exact talk revision and its supported assessment.

    Attributes:
        revision_id: Positive exact talk revision ID.
        parent_id: Visible preceding revision ID, or zero at page
            creation.
        timestamp: Actual aware UTC instant of this revision.
        grade: Parsed raw class, or none for unknown or hidden content.
    """

    revision_id: int
    parent_id: int
    timestamp: dt.datetime
    grade: str | None


@dataclass(frozen=True, slots=True)
class EntryProgress:
    """Carry one requested cohort's contiguous backward proof state.

    Attributes:
        target: Requested raw class from the selected cohort.
        selected_revision: Exact latest talk revision required by that
            cohort.
        entry_revision: Oldest known target-class revision in the latest
            run.
        entry_timestamp: Exact time of that candidate entry revision.
        prior_revision: Known non-target predecessor after a complete
            scan.
        cursor_revision: Last completely inspected target revision.
        cursor_timestamp: Time selecting an inclusive bounded resume
            request.
        expected_revision: Exact parent required next for contiguous
            coverage.
        status: proven, partial or unavailable, independently of article
            lookup.
        reason: Explicit coverage or completion diagnostic.
        stopped: Whether this scan must stop before any older source
            read.
    """

    target: str
    selected_revision: int
    entry_revision: int | None = None
    entry_timestamp: dt.datetime | None = None
    prior_revision: int | None = None
    cursor_revision: int | None = None
    cursor_timestamp: dt.datetime | None = None
    expected_revision: int | None = None
    status: str = "partial"
    reason: str = "pending"
    stopped: bool = False


def begin(target: str, selected_revision: int) -> EntryProgress:
    """Start proof for an eligible requested talk-page grade.

    Args:
        target: Raw class in the consumer's selected cohort.
        selected_revision: Positive exact selected talk revision ID.

    Returns:
        Empty proof state requiring the selected talk revision first.

    Raises:
        ValueError: The requested grade or revision ID is invalid.
    """
    if (
        target not in TARGET_CLASSES
        or type(selected_revision) is not int
        or selected_revision <= 0
    ):
        message = "Assessment inference requires a grade and exact talk ID."
        raise ValueError(message)
    return EntryProgress(
        target,
        selected_revision,
        expected_revision=selected_revision,
    )


def advance(
    state: EntryProgress,
    revision: AssessmentRevision,
) -> EntryProgress:
    """Consume one exact contiguous predecessor without skipping gaps.

    Args:
        state: Current successful prefix and candidate boundary.
        revision: Next descending revision, including explicit unknown
            grades.

    Returns:
        Updated bounded state. Hidden content and missing parent links
        stop
        the attempt as partial; a known predecessor proves the latest
        entry.

    Raises:
        ValueError: Source revision identity or timestamp is malformed.
    """
    if state.stopped:
        return state
    if (
        type(revision.revision_id) is not int
        or revision.revision_id <= 0
        or type(revision.parent_id) is not int
        or revision.parent_id < 0
        or revision.parent_id >= revision.revision_id
    ):
        message = "B-entry history requires exact ordered revision metadata."
        raise ValueError(message)
    if revision.timestamp.utcoffset() != dt.timedelta(0):
        message = "B-entry history requires aware UTC instants."
        raise ValueError(message)
    if revision.revision_id != state.expected_revision or (
        state.cursor_timestamp is not None
        and revision.timestamp > state.cursor_timestamp
    ):
        return replace(
            state,
            status="partial",
            reason="history_gap",
            stopped=True,
        )
    if revision.grade is None:
        return replace(
            state,
            status="partial",
            reason="unknown_interval",
            stopped=True,
        )
    if state.cursor_revision is None and revision.grade != state.target:
        return replace(
            state,
            status="unavailable",
            reason="cohort_mismatch",
            stopped=True,
        )
    if revision.grade != state.target:
        return replace(
            state,
            prior_revision=revision.revision_id,
            status="proven",
            reason="contiguous_transition",
            stopped=True,
        )
    return replace(
        state,
        entry_revision=revision.revision_id,
        entry_timestamp=revision.timestamp,
        cursor_revision=revision.revision_id,
        cursor_timestamp=revision.timestamp,
        expected_revision=revision.parent_id,
        status="partial",
        reason="earliest_visible"
        if revision.parent_id == 0
        else "bounded_prefix",
        stopped=revision.parent_id == 0,
    )
