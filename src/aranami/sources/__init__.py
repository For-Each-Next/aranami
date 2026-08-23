"""Expose read-only Wikimedia data-source adapters.

Use :mod:`aranami.sources.quarry` for deferred Wiki Replica queries and
:mod:`aranami.sources.pageviews` for raw daily Pageviews data and eager
Polars frames.

Examples:
    >>> from aranami.sources import pageviews, quarry
    >>> quarry.Replica.__name__, pageviews.massive.__name__
    ('Replica', 'massive')

"""

__all__ = ("pageviews", "quarry")

from aranami.sources import pageviews, quarry
