"""Expose typed, read-only Wikimedia Wiki Replica queries.

Use :mod:`aranami.sources.quarry.tables` to build SQLAlchemy
``select()`` expressions and :class:`Replica` to collect them into
Polars frames.

Examples:
    >>> from aranami.sources.quarry import Replica, tables
    >>> from sqlalchemy import select
    >>> frame = Replica("zhwiki").query(
    ...     select(tables.Page.page_id, tables.Page.page_title),
    ... )
    >>> frame.replica.database
    'zhwiki_p'

"""

from __future__ import annotations

__all__ = ("QueryFrame", "Replica", "tables")

from aranami.sources.quarry import tables
from aranami.sources.quarry.query import QueryFrame, Replica
