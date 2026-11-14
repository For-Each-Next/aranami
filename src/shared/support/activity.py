"""Describe actual source reads and publication boundaries to observers.

Background owners bind an observer; source adapters stay independent of
runtime and storage. Facts contain public source identities, never SQL,
request parameters, credentials, or local filesystem paths.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

_OBSERVER: ContextVar[Callable[[str, dict[str, object]], None] | None] = (
    ContextVar("source_activity_observer", default=None)
)
_SOURCE_FIELDS = {"provider", "dataset", "wiki", "target", "date"}
_MAX_SOURCE_TEXT = 512
_FIRST_PRINTABLE = 32
_MAX_SOURCE_REFERENCES = 16
_MAX_REFERENCE_COUNT = 1_000_000_000
_MAX_TIMESTAMP_TEXT = 64
_MAX_SCOPE_ITEMS = 64

type SourcePurpose = Literal[
    "project_membership",
    "daily_replica_readiness",
    "category_members",
    "page_tags",
    "latest_revisions",
    "new_page_candidates",
    "template_aliases",
]


class SourceScope(TypedDict, total=False):
    """Describe the public selection supplied by a source caller.

    Fields identify intended pages rather than SQL expressions or bind
    parameters. Missing values remain unknown in older observations.
    """

    project: str
    projects: list[str]
    boundary: str
    template: str
    categories: list[str]
    templates: list[str]
    page_kind: Literal["pages", "talk_pages"]
    page_count: int
    new_page_count: int
    redirect_count: int
    batch_index: int
    batch_count: int


_PURPOSE_SCOPE_FIELDS = {
    "project_membership": {"project", "projects"},
    "daily_replica_readiness": {"boundary"},
    "category_members": {"categories", "page_kind"},
    "page_tags": {"categories", "templates", "page_kind"},
    "latest_revisions": {
        "page_kind",
        "page_count",
        "batch_index",
        "batch_count",
    },
    "new_page_candidates": {"new_page_count", "redirect_count"},
    "template_aliases": {"template"},
}


def _public_text(value: object, *, empty: bool = True) -> bool:
    """Check bounded printable text before constructing public facts.

    Returns:
        Whether the value can safely describe a source identity.
    """
    return (
        isinstance(value, str)
        and (empty or bool(value))
        and len(value) <= _MAX_SOURCE_TEXT
        and not any(ord(character) < _FIRST_PRINTABLE for character in value)
    )


def _valid_reference(value: object) -> bool:
    """Check one typed page, identifier, or database-table reference.

    Returns:
        Whether the reference has a complete bounded public identity.
    """
    if not isinstance(value, dict) or set(value) != {"kind", "value"}:
        return False
    kind, identity = value["kind"], value["value"]
    if not isinstance(kind, str) or kind not in {
        "page",
        "page_id",
        "revision",
        "table",
    }:
        return False
    if not _public_text(identity, empty=False):
        return False
    return kind not in {"page_id", "revision"} or (
        identity.isascii() and identity.isdigit() and int(identity) > 0
    )


def _references(values: object, count: object) -> list[dict[str, str]]:
    """Validate a sampled reference list and its complete known count.

    Returns:
        Independent reference records.

    Raises:
        ValueError: The list, identities, or count are malformed.
    """
    if (
        not isinstance(values, list)
        or len(values) > _MAX_SOURCE_REFERENCES
        or type(count) is not int
        or not len(values) <= count <= _MAX_REFERENCE_COUNT
        or not all(_valid_reference(value) for value in values)
    ):
        message = "Source references require bounded public identities."
        raise ValueError(message)
    return [dict(value) for value in values]


def _metadata(value: object) -> dict[str, str]:
    """Validate actual saved-file identity and capture timestamps.

    Returns:
        Independent public file facts; absent times remain empty.

    Raises:
        ValueError: Paths or recorded timestamps are malformed.
    """
    required = {"file", "observed_at", "finished_at"}
    if not isinstance(value, dict) or not required <= set(
        value,
    ) <= required | {"written_at"}:
        message = "Saved-source metadata requires public file facts."
        raise ValueError(message)
    filename = value["file"]
    if not _public_text(filename, empty=False):
        message = "Saved-source filenames must be bounded public text."
        raise ValueError(message)
    path = PurePosixPath(filename)
    if (
        "\\" in filename
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or str(path) != filename
    ):
        message = "Saved-source filenames must be safe relative paths."
        raise ValueError(message)
    for field in ("observed_at", "finished_at", "written_at"):
        instant = value.get(field, "")
        if not isinstance(instant, str) or len(instant) > _MAX_TIMESTAMP_TEXT:
            message = "Saved-source times must be bounded ISO timestamps."
            raise ValueError(message)
        if instant and dt.datetime.fromisoformat(instant).utcoffset() is None:
            message = "Saved-source times must be aware observations."
            raise ValueError(message)
    return dict(value)


def _scope(purpose: object, value: object) -> SourceScope:
    """Validate a caller's bounded public selection and purpose.

    Returns:
        Detached scope values suitable for saved execution records.

    Raises:
        ValueError: The purpose or its public selection is unsupported.
    """
    if (
        not isinstance(purpose, str)
        or purpose not in _PURPOSE_SCOPE_FIELDS
        or not isinstance(value, dict)
        or not set(value) <= _PURPOSE_SCOPE_FIELDS[purpose]
    ):
        message = "Source purposes require a known public scope."
        raise ValueError(message)
    if (
        purpose == "new_page_candidates"
        and set(value) != _PURPOSE_SCOPE_FIELDS[purpose]
    ):
        message = "New-page source scopes require both discovery counts."
        raise ValueError(message)
    result = {}
    for field, item in value.items():
        if field in {"project", "template"}:
            valid = _public_text(item, empty=False)
        elif field in {"categories", "templates", "projects"}:
            valid = (
                isinstance(item, list)
                and 0 < len(item) <= _MAX_SCOPE_ITEMS
                and all(_public_text(name, empty=False) for name in item)
            )
        elif field == "boundary":
            valid = (
                _public_text(item, empty=False)
                and dt.date.fromisoformat(item).isoformat() == item
            )
        elif field == "page_kind":
            valid = (
                item in {"pages", "talk_pages"}
                if isinstance(item, str)
                else False
            )
        else:
            valid = type(item) is int and 0 <= item <= _MAX_REFERENCE_COUNT
        if not valid:
            message = "Source selection values must be bounded public facts."
            raise ValueError(message)
        result[field] = list(item) if isinstance(item, list) else item
    return result


def validate_source(value: object) -> dict[str, object]:
    """Validate the bounded public description of a source operation.

    Args:
        value: Complete source identity supplied by an adapter.

    Returns:
        Detached facts, retaining compatibility with older source rows.

    Raises:
        ValueError: Source fields, bounds, or dates are malformed.
    """
    optional = {
        "references",
        "reference_count",
        "metadata",
        "purpose",
        "scope",
    }
    if (
        not isinstance(value, dict)
        or not _SOURCE_FIELDS <= set(value) <= _SOURCE_FIELDS | optional
        or (("references" in value) != ("reference_count" in value))
        or (("purpose" in value) != ("scope" in value))
    ):
        message = "Source activity requires complete bounded fields."
        raise ValueError(message)
    for key in _SOURCE_FIELDS:
        if not _public_text(
            value[key],
            empty=key not in {"provider", "dataset"},
        ):
            message = "Source activity values must be bounded public text."
            raise ValueError(message)
    if value["provider"] not in {"saved", "replica", "mediawiki", "pageviews"}:
        message = "Unsupported source activity provider."
        raise ValueError(message)
    if value["date"] and (
        dt.date.fromisoformat(value["date"]).isoformat() != value["date"]
    ):
        message = "Source activity dates must be ISO calendar dates."
        raise ValueError(message)
    result = dict(value)
    if "references" in value:
        result["references"] = _references(
            value["references"],
            value["reference_count"],
        )
    if "metadata" in value:
        result["metadata"] = _metadata(value["metadata"])
    if "purpose" in value:
        result["scope"] = _scope(value["purpose"], value["scope"])
    return result


@contextmanager
def activity_observer(
    callback: Callable[[str, dict[str, object]], None],
) -> Iterator[None]:
    """Observe public activity only while this execution scope is open.

    Args:
        callback: Owner receiving the operation kind and source facts.

    Yields:
        Control with an isolated observer, including in nested scopes.
    """
    active = True

    def forward(kind: str, source: dict[str, object]) -> None:
        """Ignore copied contexts after their original scope closes."""
        if active:
            callback(kind, source)

    token = _OBSERVER.set(forward)
    try:
        yield
    finally:
        active = False
        _OBSERVER.reset(token)


def report_activity(  # ruff: ignore[too-many-arguments]
    provider: str,
    dataset: str,
    *,
    wiki: str = "",
    target: str = "",
    date: dt.date | None = None,
    kind: str = "source_read",
    references: Sequence[tuple[str, str | int]] = (),
    metadata: dict[str, str] | None = None,
    purpose: SourcePurpose | None = None,
    scope: SourceScope | None = None,
) -> None:
    """Report an actual read, destination check, or imminent wiki save.

    Call immediately before the operation, after cache selection, so
    observations never claim a network request for a saved-data reuse.

    Args:
        provider: Saved data, Wiki Replicas, MediaWiki, or Pageviews.
        dataset: Stable public kind of data being read.
        wiki: Public wiki database name or API project hostname.
        target: Public page/project title or selected result columns.
        date: Actual saved/report date when known.
        kind: Source read, destination check, or actual save boundary.
        references: Exact public page, ID, revision or table identities.
        metadata: Saved-file identity and actual capture timestamps.
        purpose: Known reason for reading this source.
        scope: Public selection supplied by the source caller.
    """
    callback = _OBSERVER.get()
    if callback is not None:
        callback(
            kind,
            validate_source({
                "provider": provider,
                "dataset": dataset,
                "wiki": wiki,
                "target": " ".join(target.split())[:_MAX_SOURCE_TEXT],
                "date": date.isoformat() if date is not None else "",
                **(
                    {
                        "references": [
                            {"kind": kind, "value": str(value)}
                            for kind, value in references[
                                :_MAX_SOURCE_REFERENCES
                            ]
                        ],
                        "reference_count": len(references),
                    }
                    if references
                    else {}
                ),
                **({"metadata": metadata} if metadata is not None else {}),
                **(
                    {"purpose": purpose, "scope": scope or {}}
                    if purpose is not None
                    else {"scope": scope}
                    if scope is not None
                    else {}
                ),
            }),
        )
