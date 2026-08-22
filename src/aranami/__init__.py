"""Expose Aranami's PAWS run bootstrap and Wikimedia data sources.

Call :func:`run` to initialize per-run logging; no report jobs are wired
yet. Use :mod:`aranami.sources` for read-only Wiki Replica and Pageviews
access while developing reports.
"""

__all__ = ("run", "sources")

from aranami import sources
from aranami.runner import run
