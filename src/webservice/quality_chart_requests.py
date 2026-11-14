"""Render filtered Matplotlib charts from a saved catalog."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import TYPE_CHECKING

from data.articles.current_quality import (
    GRADES,
    current_quality_identity,
    read_current_quality,
)
from shared.assessment_profiles import assessment_profile
from shared.support.files import encode_json
from webservice.presentation import Interface
from webservice.quality_analysis import prose_length_maximum
from webservice.quality_charts import render_charts

if TYPE_CHECKING:
    from pathlib import Path

_FIRST_YEAR = 2002
_QUERY_FIELDS = {
    "generation",
    "basis",
    "cells",
    "from",
    "to",
    "unknown",
    "lang",
}


class StaleQualityChartError(ValueError):
    """Reject charts requested for a replaced saved catalog."""


def chart_generation(identity: tuple) -> str:
    """Identify the exact saved source used by a chart preset.

    Returns:
        A stable opaque token for the observed file generation.
    """
    return sha256(encode_json({"identity": list(identity)})).hexdigest()


def filtered_charts(runtime: Path, parameters: dict[str, list[str]]) -> dict:
    """Read one saved catalog and render a bounded matrix selection.

    Args:
        runtime: Existing Toolforge data home.
        parameters: Single-valued native matrix controls.

    Returns:
        The source token and SVG documents for this selection.

    Raises:
        ValueError: Controls are invalid or outside the saved matrix.
        LookupError: The prepared catalog is unavailable.
        StaleQualityChartError: The source changed during rendering.
    """
    if set(parameters) != _QUERY_FIELDS or any(
        len(values) != 1 for values in parameters.values()
    ):
        message = "Choose one bounded chart selection."
        raise ValueError(message)
    controls = {name: values[0] for name, values in parameters.items()}
    if (
        not re.fullmatch(r"[0-9a-f]{64}", controls["generation"])
        or controls["basis"] not in {"current", "listed"}
        or controls["unknown"] not in {"0", "1"}
        or controls["lang"] not in {"en", "zh-hans", "zh-hant"}
        or any(
            re.fullmatch(r"[0-9]{4}", controls[name]) is None
            for name in ("from", "to")
        )
    ):
        message = "Invalid chart generation or controls."
        raise ValueError(message)
    identity = current_quality_identity(runtime)
    if identity == (None,):
        message = "The prepared chart catalog is unavailable."
        raise LookupError(message)
    if chart_generation(identity) != controls["generation"]:
        message = "The prepared chart catalog has changed."
        raise StaleQualityChartError(message)
    try:
        saved = read_current_quality(
            runtime,
            authorship_basis=controls["basis"],
            all_grades=True,
        )["analysis"]
    except ValueError as error:
        message = "The saved chart catalog is invalid."
        raise LookupError(message) from error
    selection = saved["selection"]
    first, last = int(controls["from"]), int(controls["to"])
    if not _FIRST_YEAR <= first <= last <= selection["last_year"]:
        message = "Chart years must remain within the saved catalog."
        raise ValueError(message)
    importance = {
        label.code
        for label in assessment_profile("zhwiki", "电子游戏").importance
        if label.code not in {"NA", "Bottom"}
    }
    allowed = {f"{grade}:{level}" for grade in GRADES for level in importance}
    chosen = controls["cells"].split(",") if controls["cells"] else []
    if len(chosen) != len(set(chosen)) or not set(chosen) <= allowed:
        message = "Chart cells must identify the saved matrix."
        raise ValueError(message)
    chosen = set(chosen)
    rows = [
        row
        for row in selection["articles"]
        if f"{row['grade']}:{row['importance'] or 'Unknown'}" in chosen
        and (
            first <= int(row["listed_date"][:4]) <= last
            if row.get("listed_date")
            else controls["unknown"] == "1"
        )
    ]
    result = render_charts(
        {
            "articles": rows,
            "length_scale_maximum": prose_length_maximum(saved),
            "year_from": first,
            "year_to": last,
            "grade_codes": [
                grade
                for grade in GRADES
                if any(cell.startswith(grade + ":") for cell in chosen)
            ],
        },
        Interface(controls["lang"]),
    )
    if current_quality_identity(runtime) != identity:
        message = "The prepared chart catalog changed during rendering."
        raise StaleQualityChartError(message)
    return {"generation": controls["generation"], **result}
