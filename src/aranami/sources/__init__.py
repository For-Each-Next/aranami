"""Expose read-only external data sources.

Examples:
    >>> from aranami.sources.quarry import QueryFrame
    >>> QueryFrame.__name__
    'QueryFrame'

"""

__all__ = ("quarry",)

from aranami.sources import quarry
