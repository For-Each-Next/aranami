"""Provide ``gtshow`` for compact Polars frame previews."""

from __future__ import annotations

__all__ = ("gtshow",)

from html import escape
from typing import TYPE_CHECKING, Final

import polars as pl
from great_tables import GT, html as gt_html

if TYPE_CHECKING:
    from typing import Any

_DEFAULT_EDGE_ROWS: Final = 5
_OMISSION_TEXT: Final = "..."


def gtshow(
    frame: pl.DataFrame,
    *,
    first: int = _DEFAULT_EDGE_ROWS,
    last: int = _DEFAULT_EDGE_ROWS,
) -> None:
    """Display a compact Great Tables preview in JupyterLab.

    The preview retains native column types, shows the requested leading
    and trailing rows, and inserts an ellipsis row when records are
    omitted from the middle. Each column header shows its original
    Polars data type beneath its field name, while the footer reports
    the shape of the complete frame. A nonpositive edge count displays
    every row.

    Args:
        frame: Data frame to preview.
        first: Number of leading rows to show, or all rows when
            nonpositive.
        last: Number of trailing rows to show, or all rows when
            nonpositive.

    """
    omitted_row = None
    preview = frame
    if first > 0 and last > 0 and frame.height > first + last:
        omitted_row = first
        separator = frame.clear().select(
            pl.lit(None, dtype=dtype).alias(name)
            for name, dtype in frame.schema.items()
        )
        preview = pl.concat(
            (frame.head(first), separator, frame.tail(last)),
            how="vertical",
        )

    labels: dict[str, Any] = {
        name: gt_html(
            f"{escape(name)}<br><small>{escape(str(dtype))}</small>",
        )
        for name, dtype in frame.schema.items()
    }
    row_label = "row" if frame.height == 1 else "rows"
    column_label = "column" if frame.width == 1 else "columns"
    source_note = (
        f"{frame.height:,} {row_label}"
        f" \N{MULTIPLICATION SIGN}"
        f" {frame.width:,} {column_label}"
    )

    table = (
        GT(preview)
        .cols_label(cases=labels)
        .tab_source_note(source_note=source_note)
    )
    if omitted_row is not None:
        table = table.sub_missing(
            rows=omitted_row,
            missing_text=_OMISSION_TEXT,
        )
    table.show(target="notebook")
