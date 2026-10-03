"""Expose recurring monitoring, immediate reports, and data sources.

Call :func:`run` on PAWS to start its scheduled WikiProject Video games
monitor. Call :func:`run_once` to execute selected reports immediately,
with an optional date anchor. Both default to live publication; pass
``dry=True`` to write previews instead of wiki edits.
"""

__all__ = ("run", "run_once", "sources")

from aranami import sources
from aranami.runner import run, run_once
