"""Supply audited aggregate facts through the normal figure interface.

This temporary adapter owns the packaged historical evidence. The data
layer receives explicit metadata and never searches an acquisition tree.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from data.newsletter import historical
from shared.support.files import read_json_object

if TYPE_CHECKING:
    import datetime as dt

    import polars as pl

    from data.reports import ReportBundle

_MAX_BYTES = 256 * 1024


def audited_metadata() -> dict:
    """Load the bounded audited source without creating runtime files.

    Returns:
        Original source metadata, validated by the canonical contract.
    """
    value = read_json_object(Path(__file__).with_suffix(".json"), _MAX_BYTES)
    historical.decode_import(value)
    return value


class AuditedFigureSource:
    """Read audit metadata only when its policy is selected."""

    identity = "temporary-audited-metadata"

    def read(self, metric: str, through: dt.date) -> pl.DataFrame:
        """Read covered audit rows through an inclusive date.

        Args:
            metric: Metric identity shared with formal figure sources.
            through: Latest permitted observation date.

        Returns:
            Typed rows with their exact historical provenance.
        """
        _, frame = historical.decode_import(audited_metadata())
        return historical.FrameFigureSource(self.identity, frame).read(
            metric,
            through,
        )


def audited_source() -> AuditedFigureSource:
    """Expose temporary metadata through the normal source interface.

    Returns:
        A read-only provider retaining exact audited aggregate coverage.
    """
    return AuditedFigureSource()


def import_historical_data(
    runtime: Path | None = None,
    *,
    dry: bool = False,
) -> ReportBundle:
    """Supply audited evidence to the canonical historical importer.

    Args:
        runtime: Runtime home, or the current working directory.
        dry: Keep the complete import inside preview storage.

    Returns:
        Accepted canonical records with an import receipt.
    """
    if historical.read_historical_data(runtime, dry=dry) is not None:
        return historical.import_historical_data(runtime, dry=dry)
    return historical.import_historical_data(
        runtime,
        dry=dry,
        seed=audited_metadata(),
    )


def source_for_issue(issue: str) -> historical.NewsletterFigureSource:
    """Select the issue's source with an explicit historical provider.

    Args:
        issue: Exact issue identity selecting normal provider policy.

    Returns:
        Canonical or explicitly supplied temporary aggregate records.
    """
    return historical.source_for_issue(issue, fallback=audited_source())


def prepare_dyk_series(review: dict, year: int) -> list[dict]:
    """Prepare frozen series using the issue's explicit figure provider.

    Args:
        review: Already prepared annual observations.
        year: Reviewed calendar year, preceding its January issue.

    Returns:
        Dated cumulative observations without invented missing history.
    """
    with historical.using_source(source_for_issue(f"{year + 1}-01")):
        return historical.prepare_dyk_series(review, year)
