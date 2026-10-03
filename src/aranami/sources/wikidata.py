"""Read Wikidata claims unavailable from the Wiki Replica term tables.

Use targeted, preloaded ItemPage reads for P8345 relationships.
Sitelinks and labels remain replica-backed; this never writes entities.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import polars as pl
import pywikibot
from pywikibot import pagegenerators

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pywikibot.site import BaseSite

_FRANCHISE_SCHEMA = {"wikibase_qid": pl.Int64, "franchise_qid": pl.Int64}
_LOGGER = logging.getLogger(__name__)


def _franchise_targets(item: pywikibot.ItemPage) -> list[int]:
    """Extract concrete best-rank P8345 item targets.

    Args:
        item: Preloaded Wikidata item with claim data.

    Returns:
        Unique numeric targets in deterministic numeric order.
    """
    claims = item.claims.get("P8345", [])
    best_rank = (
        "preferred"
        if any(claim.rank == "preferred" for claim in claims)
        else "normal"
    )
    targets = {
        int(target.id[1:])
        for claim in claims
        if claim.rank == best_rank
        and claim.snaktype == "value"
        and isinstance(target := claim.getTarget(), pywikibot.ItemPage)
    }
    return sorted(targets)


def fetch_franchises(site: BaseSite, item_ids: Sequence[int]) -> pl.DataFrame:
    """Read one best-rank media franchise for each requested character.

    P8345 statements have no equivalent typed Wiki Replica table. Entity
    preloading batches the content reads; ties use the lowest numeric
    target instead of depending on endpoint result order.

    Args:
        site: Existing wiki whose data repository supplies the claims.
        item_ids: Numeric Wikidata item IDs from project page metadata.

    Returns:
        Character IDs and their selected franchise IDs.
    """
    identifiers = sorted(set(item_ids))
    if not identifiers:
        return pl.DataFrame(schema=_FRANCHISE_SCHEMA)
    repository = site.data_repository()
    items = (
        pywikibot.ItemPage(repository, f"Q{identifier}")
        for identifier in identifiers
    )
    rows = [
        (int(item.id[1:]), targets[0])
        for item in pagegenerators.PreloadingEntityGenerator(items)
        if not item.isRedirectPage()
        if (targets := _franchise_targets(item))
    ]
    _LOGGER.info(
        "Read media franchises for %d of %d character items.",
        len(rows),
        len(identifiers),
    )
    return pl.DataFrame(rows, schema=_FRANCHISE_SCHEMA, orient="row")
