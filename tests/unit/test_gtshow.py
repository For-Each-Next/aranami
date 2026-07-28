"""Test ``gtshow`` previews of Polars frames."""

import re
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from great_tables import GT

from aranami.support import gtshow


def _shown_table(
    frame: pl.DataFrame,
    *,
    first: int = 5,
    last: int = 5,
) -> GT:
    """Capture the Great Tables object displayed for a frame.

    Returns:
        The displayed table.

    """
    with patch.object(GT, "show", autospec=True) as show:
        result = gtshow(frame, first=first, last=last)

    assert result is None
    table = show.call_args.args[0]
    assert isinstance(table, GT)
    show.assert_called_once_with(table, target="notebook")
    return table


def _body_cells(table: GT) -> list[str]:
    """Extract rendered body-cell values from a table.

    Returns:
        The body cells in display order.

    """
    rendered = table.as_raw_html()
    body = re.search(r"<tbody\b.*?</tbody>", rendered, flags=re.DOTALL)

    assert body is not None
    return re.findall(
        r"<td\b[^>]*>(.*?)</td>",
        body.group(),
        flags=re.DOTALL,
    )


class TestGtShow(TestCase):
    """Test preview rows and typed column headings."""

    @staticmethod
    def test_default_preview_shows_edges_types_shape_and_omission() -> None:
        """Show five rows per edge, field types, shape, and omission."""
        frame = pl.DataFrame(
            {
                "number": range(12),
                "label": [f"row-{index}" for index in range(12)],
            },
        )

        table = _shown_table(frame)
        rendered = table.as_raw_html()
        expected_cells = [
            value
            for index in range(5)
            for value in (str(index), f"row-{index}")
        ]
        expected_cells.extend(("...", "..."))
        expected_cells.extend(
            value
            for index in range(7, 12)
            for value in (str(index), f"row-{index}")
        )

        assert "number<br><small>Int64</small>" in rendered
        assert "label<br><small>String</small>" in rendered
        assert "12 rows x 2 columns" in rendered
        assert _body_cells(table) == expected_cells

    @staticmethod
    def test_custom_edges_and_short_frames_avoid_duplicate_rows() -> None:
        """Honor custom edge sizes and show short frames in full."""
        frame = pl.DataFrame({"value": range(6)})

        abbreviated = _shown_table(frame, first=1, last=2)
        complete = _shown_table(frame, first=3, last=3)
        all_from_first = _shown_table(frame, first=0, last=2)
        all_from_last = _shown_table(frame, first=2, last=-1)

        assert "6 rows x 1 columns" in abbreviated.as_raw_html()
        assert _body_cells(abbreviated) == ["0", "...", "4", "5"]
        assert _body_cells(complete) == [str(value) for value in range(6)]
        assert _body_cells(all_from_first) == [
            str(value) for value in range(6)
        ]
        assert _body_cells(all_from_last) == [str(value) for value in range(6)]

    @staticmethod
    def test_preview_displays_empty_frames_and_escapes_labels() -> None:
        """Display empty frames and escape typed column headings."""
        frame = pl.DataFrame({"<value>": [1]})

        rendered = _shown_table(frame).as_raw_html()
        empty = _shown_table(pl.DataFrame())
        structured_empty = _shown_table(
            pl.DataFrame(schema={"value": pl.Int64}),
        )

        assert "&lt;value&gt;<br><small>Int64</small>" in rendered
        assert "0 rows x 0 columns" in empty.as_raw_html()
        assert _body_cells(empty) == []
        structured_rendered = structured_empty.as_raw_html()

        assert "value<br><small>Int64</small>" in structured_rendered
        assert "0 rows x 1 columns" in structured_empty.as_raw_html()
        assert _body_cells(structured_empty) == []
