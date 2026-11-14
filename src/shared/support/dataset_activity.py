"""Forward Parquet update boundaries to an optional runtime owner.

The observer is inert outside collection scopes and never controls data
writes. Call begin before acquisition or serialization, and completed
after the target has been atomically accepted.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_CALLBACK: ContextVar[Callable[[str, Path, Path | None], None] | None] = (
    ContextVar("dataset_activity_callback", default=None)
)
_LOGGER = logging.getLogger(__name__)


@contextmanager
def dataset_observer(
    callback: Callable[[str, Path, Path | None], None],
) -> Iterator[None]:
    """Observe updates while their owning collection remains active.

    Yields:
        The scoped collection observer.
    """
    active = True

    def forward(kind: str, path: Path, baseline: Path | None) -> None:
        """Ignore copied contexts after the collection ends."""
        if active:
            callback(kind, path, baseline)

    token = _CALLBACK.set(forward)
    try:
        yield
    finally:
        active = False
        _CALLBACK.reset(token)


def dataset_activity(
    kind: str,
    path: Path,
    *,
    baseline: Path | None = None,
) -> None:
    """Report a real boundary without letting diagnostics fail a write.

    Args:
        kind: Begin, completed, or unchanged accepted local operation.
        path: Actual final Parquet path, never a temporary staging file.
        baseline: Explicit previous immutable generation, when known.
    """
    callback = _CALLBACK.get()
    if callback is not None:
        try:
            callback(kind, path, baseline)
        except Exception:
            _LOGGER.warning(
                "Could not retain dataset update evidence.",
                exc_info=True,
            )
