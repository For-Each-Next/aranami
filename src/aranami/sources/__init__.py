"""Expose read-only Wikimedia data-source adapters.

Use :mod:`aranami.sources.quarry` for deferred Wiki Replica queries and
:mod:`aranami.sources.pageviews` for cumulative Pageviews totals.

Examples:
    >>> from aranami.sources import pageviews, quarry
    >>> quarry.Replica.__name__, pageviews.Pageviews.__name__
    ('Replica', 'Pageviews')

"""

__all__ = ("pageviews", "quarry")

from aranami.sources import pageviews, quarry
