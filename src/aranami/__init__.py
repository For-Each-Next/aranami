"""Expose Aranami's routine entry point and Wikimedia data sources.

Call :func:`run` after installing the wheel on PAWS to perform one full
WikiProject Video games routine. Pass ``dry_run=True`` to write proposed
edits locally. Authentication and scheduling remain with the caller.
"""

__all__ = ("run", "sources")

from aranami import sources
from aranami.runner import run
