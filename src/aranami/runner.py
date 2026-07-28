"""Coordinate one complete Aranami run.

The bootstrap runner currently records that execution began and then
returns. Report jobs will be added separately.

"""

__all__ = ("run",)

from aranami.support import open_run_log


def run(*, dry_run: bool = False) -> None:
    """Record an ``INFO`` message at the beginning of one Aranami run.

    Args:
        dry_run: Whether later report work should avoid on-wiki writes.

    """
    with open_run_log() as logger:
        logger.info("Aranami run started (dry_run=%s).", dry_run)
