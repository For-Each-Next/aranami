"""Render saved article statistics as interactive Matplotlib SVG charts.

The renderer consumes bounded saved catalog rows, never source clients.
Each Figure lives only for its render. Links and statistic titles keep
browser navigation and the article-list dialogs accessible.
"""

# Only this process's Matplotlib-generated XML is parsed.
# ruff: file-ignore[suspicious-xml-etree-import]

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from functools import lru_cache
from io import StringIO
from threading import RLock
from typing import TYPE_CHECKING
from xml.etree import (
    ElementTree as ET,
)

import numpy as np
from matplotlib import rc_context
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure

from data.articles.quality_analysis import GRADES
from shared.assessment_profiles import assessment_profile
from webservice.quality_authorship import grade_label

if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from webservice.presentation import Interface

_LOCK = RLock()
_PROFILE = assessment_profile("zhwiki", "电子游戏")
_IMPORTANCE = ("Top", "High", "Mid", "Low", "Unknown")
_MAX_ARTICLES = 30_000
_MAX_YEARS = 200
_LINEAR_LIMIT = 25_000
_LINEAR_SHARE = 0.9
_SVG = "http://www.w3.org/2000/svg"
_XLINK = "http://www.w3.org/1999/xlink"
_STATISTICS = ("minimum", "p25", "p50", "p75", "maximum", "mean")
ET.register_namespace("", _SVG)
ET.register_namespace("xlink", _XLINK)


def _color(code: str, dimension: str) -> str:
    """Use the retained assessment colors and shared unknown gray.

    Returns:
        A Matplotlib-compatible color for one catalog dimension.
    """
    if (dimension, code) in {
        ("class", "Unassessed"),
        ("importance", "Unknown"),
    }:
        return "#72989c"
    return _PROFILE.color(code, dimension)


def _number(value: float) -> str:
    """Format character measurements with two significant digits.

    Returns:
        A grouped integer retaining measured zero.
    """
    if value == 0:
        return "0"
    step = 10 ** max(0, math.floor(math.log10(value)) - 1)
    return f"{math.floor(value / step + 0.5) * step:,}"


def _scale(value: np.ndarray, maximum: float) -> np.ndarray:
    """Map a shared linear body and logarithmic tail to one unit axis.

    Returns:
        Vectorized positions without taking logarithms of zero.
    """
    values = np.asarray(value)
    tail = math.log(max(maximum / _LINEAR_LIMIT, 1.000001))
    return np.where(
        values <= _LINEAR_LIMIT,
        values / _LINEAR_LIMIT * _LINEAR_SHARE,
        _LINEAR_SHARE
        + (1 - _LINEAR_SHARE)
        * np.log(np.maximum(values, _LINEAR_LIMIT) / _LINEAR_LIMIT)
        / tail,
    )


def _inverse_scale(value: np.ndarray, maximum: float) -> np.ndarray:
    """Restore character counts for Matplotlib's function scale.

    Returns:
        Original sample coordinates for the shared axis transform.
    """
    values = np.asarray(value)
    tail = math.log(max(maximum / _LINEAR_LIMIT, 1.000001))
    return np.where(
        values <= _LINEAR_SHARE,
        values / _LINEAR_SHARE * _LINEAR_LIMIT,
        _LINEAR_LIMIT
        * np.exp((values - _LINEAR_SHARE) / (1 - _LINEAR_SHARE) * tail),
    )


def _link(group: str, value: str, label: str, year: int | None = None) -> dict:
    """Describe one native SVG link to the selected article list.

    Returns:
        Controlled browser data attributes for chart dialog activation.
    """
    result = {
        "href": "#analysis-articles",
        "data-chart-group": group,
        "data-chart-value": value,
        "data-chart-label": label,
        "tabindex": "0",
    }
    if year is not None:
        result["data-chart-year"] = str(year)
    return result


def _presentation_attributes(root: ET.Element) -> None:
    """Replace generated CSS with CSP-compatible SVG attributes."""
    parents = {child: parent for parent in root.iter() for child in parent}
    for element in list(root.iter()):
        if element.tag == f"{{{_SVG}}}style":
            parents[element].remove(element)
            continue
        for declaration in element.attrib.pop("style", "").split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                element.set(name.strip(), value.strip())
    root.set("stroke-linejoin", "round")
    root.set("stroke-linecap", "butt")


def _annotate(root: ET.Element, annotations: dict[str, dict]) -> None:
    """Attach controlled link attributes and titles to local artists."""
    parents = {child: parent for parent in root.iter() for child in parent}
    for element in list(root.iter()):
        annotation = annotations.get(element.get("id", ""))
        if annotation is None:
            continue
        element.attrib.update(annotation.get("attributes", {}))
        if title := annotation.get("title"):
            ET.SubElement(element, f"{{{_SVG}}}title").text = title
        if link := annotation.get("link"):
            parent = parents[element]
            position = list(parent).index(element)
            parent.remove(element)
            anchor = ET.Element(f"{{{_SVG}}}a", link)
            anchor.append(element)
            parent.insert(position, anchor)


def _prefix_identifiers(root: ET.Element, prefix: str) -> None:
    """Keep inline chart definitions and fragment links independent."""
    identifiers = {
        element.attrib["id"]: f"{prefix}-{element.attrib['id']}"
        for element in root.iter()
        if "id" in element.attrib
    }
    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if name == "id":
                element.set(name, identifiers[value])
            elif value.startswith("#") and value[1:] in identifiers:
                element.set(name, "#" + identifiers[value[1:]])
            elif "url(#" in value:
                updated = value
                for old, new in identifiers.items():
                    updated = updated.replace(f"url(#{old})", f"url(#{new})")
                element.set(name, updated)


def _svg(
    figure: Figure,
    prefix: str,
    attributes: dict,
    annotations: dict[str, dict],
) -> str:
    """Serialize a Figure and decorate its genuine SVG artists.

    Returns:
        Standalone safe SVG with unique IDs, links and escaped titles.
    """
    output = StringIO()
    FigureCanvasSVG(figure).print_svg(output, metadata={"Date": None})
    root = ET.fromstring(output.getvalue())  # ruff: ignore[suspicious-xml-element-tree-usage] - Trusted local Figure output.
    _presentation_attributes(root)
    _annotate(root, annotations)
    _prefix_identifiers(root, prefix)
    root.attrib.update(attributes)
    root.set("data-chart-renderer", "matplotlib")
    root.set("data-renderer", "matplotlib")
    root.set("role", "img")
    figure.clear()
    return ET.tostring(root, encoding="unicode")


@lru_cache(maxsize=128)
def _pie(rows: tuple, dimension: str, caption: str) -> str:
    """Render assessment counts with Matplotlib's pie artists.

    Returns:
        A clickable pie, including a neutral empty-cohort circle.
    """
    figure = Figure(figsize=(2.6, 2.6))
    axes = figure.add_axes((0.02, 0.02, 0.96, 0.96))
    axes.set_axis_off()
    annotations = {}
    if rows:
        container = axes.pie(
            [row[3] for row in rows],
            colors=[row[2] for row in rows],
            startangle=90,
            counterclock=False,
            wedgeprops={"linewidth": 1, "edgecolor": "white"},
        )
        total = sum(row[3] for row in rows)
        for index, (wedge, row) in enumerate(
            zip(container.wedges, rows, strict=True),
        ):
            code, label, _color_value, count = row
            identifier = f"slice-{index}"
            wedge.set_gid(identifier)
            annotations[identifier] = {
                "attributes": {"class": "analysis-pie-slice"},
                "title": f"{label}: {count:,} ({count / total:.1%})",
                "link": _link(dimension, code, label),
            }
    else:
        axes.pie([1], colors=["#f3f7f7"])
    return _svg(
        figure,
        f"quality-pie-{dimension}",
        {
            "class": "analysis-pie",
            "data-analysis-pie": "class"
            if dimension == "grade"
            else dimension,
            "aria-label": caption,
        },
        annotations,
    )


@lru_cache(maxsize=64)
def _violin(  # ruff: ignore[too-many-locals]
    values: tuple,
    maximum: float,
    option: tuple,
    labels: tuple,
    pages: int,
) -> str:
    """Render exact saved samples through Axes.violinplot.

    Returns:
        A horizontal violin with individually titled native markers.
    """
    dimension, code, label, color = option
    titles, summary_template = labels[:-1], labels[-1]
    figure = Figure(figsize=(5, 0.55))
    axes = figure.add_axes((0.015, 0.05, 0.97, 0.9))
    axes.set_axis_off()
    axes.set_xscale(
        "function",
        functions=(
            lambda x: _scale(x, maximum),
            lambda x: _inverse_scale(x, maximum),
        ),
    )
    axes.set_xlim(0, max(maximum, _LINEAR_LIMIT))
    axes.set_ylim(-0.65, 0.65)
    axes.axhline(0, color="#dce6e7", linewidth=0.7)
    annotations = {
        "figure_1": {
            "link": {
                **_link(dimension, code, label),
                "data-chart-measured": "1",
            },
        },
    }
    summary_values = dict.fromkeys(_STATISTICS, "—")
    if values:
        artists = axes.violinplot(
            values,
            positions=[0],
            orientation="horizontal",
            widths=1.0,
            showextrema=False,
            points=129,
            bw_method="scott",
        )
        for index, body in enumerate(artists["bodies"]):
            body.set_facecolor(color)
            body.set_edgecolor("#52797e")
            body.set_alpha(0.75)
            body.set_linewidth(0.7)
            body.set_gid(f"density-{index}")
            annotations[f"density-{index}"] = {
                "attributes": {"class": "analysis-violin-density"},
            }
        sample = np.asarray(values)
        statistics = (
            *np.quantile(sample, [0, 0.25, 0.5, 0.75, 1]),
            float(sample.mean()),
        )
        summary_values = {
            kind: _number(float(value))
            for kind, value in zip(_STATISTICS, statistics, strict=True)
        }
        for kind, value, template in zip(
            _STATISTICS,
            statistics,
            titles,
            strict=True,
        ):
            identifier = f"statistic-{kind}"
            if kind == "mean":
                artist = axes.plot(
                    [value],
                    [0],
                    marker="x",
                    color="#9c3e32",
                    markersize=7,
                    markeredgewidth=1.4,
                    linestyle="none",
                )[0]
                attributes = {
                    "class": "analysis-violin-mean",
                    "data-mean-x": str(
                        float(_scale(np.asarray(value), maximum)) * 1000,
                    ),
                }
            elif kind in {"p25", "p75"}:
                artist = axes.plot(
                    [value],
                    [0],
                    marker="o",
                    color="#163c41",
                    markersize=3.5,
                    linestyle="none",
                )[0]
                attributes = {
                    "class": "analysis-violin-quartile",
                    "data-percentile": kind[1:],
                }
            else:
                artist = axes.vlines(
                    value,
                    -0.3,
                    0.3,
                    colors="#163c41",
                    linewidth=1.2,
                )
                attributes = {
                    "class": "analysis-violin-median"
                    if kind == "p50"
                    else "analysis-violin-extreme",
                }
                if kind != "p50":
                    attributes["data-extreme"] = kind
            artist.set_gid(identifier)
            attributes["data-statistic"] = kind
            attributes["data-value"] = str(float(value))
            annotations[identifier] = {
                "attributes": attributes,
                "title": template.format(value=_number(float(value))),
            }
    result = _svg(
        figure,
        f"quality-violin-{dimension}-{code}",
        {
            "class": "analysis-mini-violin",
            "data-analysis-violin": code,
            "aria-label": label
            + ": "
            + summary_template.format(
                pages=f"{pages:,}",
                measured=f"{len(values):,}",
                **summary_values,
            ),
            "preserveAspectRatio": "none",
        },
        annotations,
    )
    return result


@lru_cache(maxsize=64)
def _years(rows: tuple, grades: tuple, caption: str) -> str:
    """Render continuous annual cohorts as stacked Matplotlib bars.

    Returns:
        Year/grade bars linked to their matching article subsets.
    """
    figure = Figure(figsize=(max(6, len(rows) * 0.46 + 0.72), 3))
    axes: Axes = figure.add_axes((0.06, 0.16, 0.92, 0.78))
    axes.set_axisbelow(True)
    axes.grid(axis="y", color="#dce6e7", linewidth=0.7)
    axes.spines[["top", "right"]].set_visible(False)
    axes.tick_params(axis="both", labelsize=8, length=0)
    years = [row[0] for row in rows]
    bottom = np.zeros(len(rows))
    annotations = {}
    for index, (code, label, color) in enumerate(grades):
        values = [row[1][index] for row in rows]
        bars = axes.bar(
            years,
            values,
            bottom=bottom,
            color=color,
            width=0.72,
            edgecolor="white",
            linewidth=0.5,
        )
        for bar, year, count in zip(bars, years, values, strict=True):
            if not count:
                continue
            identifier = f"bar-{year}-{code}"
            bar.set_gid(identifier)
            annotations[identifier] = {
                "title": f"{year} · {label}: {count:,}",
                "attributes": {
                    "data-analysis-year": str(year),
                    "data-year-count": str(count),
                },
                "link": _link("grade", code, f"{year} · {label}", year),
            }
        bottom += values
    axes.set_xticks(years, [str(year) for year in years], rotation=45)
    axes.set_ylim(0, max(1, float(bottom.max(initial=0))) * 1.18)
    for year, count in zip(years, bottom, strict=True):
        axes.annotate(
            f"{count:,.0f}",
            (year, count),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            fontsize=8,
        )
    return _svg(
        figure,
        "quality-years",
        {"class": "analysis-year-columns", "aria-label": caption},
        annotations,
    )


def _year_bounds(articles: list[dict], data: dict) -> tuple[int, int]:
    """Bound a continuous saved year selection before drawing.

    Returns:
        The inclusive first and last displayed years.

    Raises:
        ValueError: The chosen range exceeds the plot bound.
    """
    dates = [
        int(str(row["listed_date"])[:4])
        for row in articles
        if row.get("listed_date")
    ]
    first = int(data.get("year_from", min([2009, *dates])))
    last = int(
        data.get("year_to", max([dt.datetime.now(dt.UTC).year, *dates])),
    )
    if not 0 < last - first + 1 <= _MAX_YEARS:
        message = "Chart year range exceeds its bound."
        raise ValueError(message)
    return first, last


def render_charts(data: dict, interface: Interface) -> dict:
    """Render all quality charts from one selected saved catalog.

    Args:
        data: Selected article rows, the full length-scale maximum, and
            optional year_from/year_to and selected grade_codes.
        interface: Existing localized interface without source access.

    Returns:
        Grade/importance/year SVG strings and dimension-keyed violins.

    Raises:
        ValueError: The catalog or selected year range exceeds bounds.
    """
    articles = data.get("articles", [])
    if len(articles) > _MAX_ARTICLES:
        message = "Chart catalog exceeds its article bound."
        raise ValueError(message)
    lengths = [
        row["prose_characters"]
        for row in articles
        if row.get("prose_characters") is not None
    ]
    if any(not math.isfinite(value) or value < 0 for value in lengths):
        message = "Chart lengths must be finite nonnegative measurements."
        raise ValueError(message)
    maximum = max(
        _LINEAR_LIMIT,
        float(data.get("length_scale_maximum") or max(lengths, default=0)),
    )
    first, last = _year_bounds(articles, data)
    selected_grades = tuple(
        code for code in GRADES if code in data.get("grade_codes", GRADES)
    )
    options = {
        "grade": tuple(
            (code, grade_label(code, interface), _color(code, "class"))
            for code in GRADES
        ),
        "importance": tuple(
            (
                code,
                interface.text(f"assessment.importance.{code}"),
                _color(code, "importance"),
            )
            for code in _IMPORTANCE
        ),
    }
    captions = {
        "grade": interface.text("quality_analysis.grade_pie"),
        "importance": interface.text("quality_analysis.importance_pie"),
    }
    titles = tuple(
        interface.text(f"quality_analysis.violin_{kind}")
        for kind in _STATISTICS
    )
    result = {"lengths": {}}
    with (
        _LOCK,
        rc_context({
            "svg.fonttype": "none",
            "svg.hashsalt": "aranami-quality",
        }),
    ):
        for dimension, choices in options.items():
            counts = Counter(row[dimension] or "Unknown" for row in articles)
            rows = tuple(
                (*option, counts[option[0]])
                for option in choices
                if counts[option[0]]
            )
            result[
                "grades_svg" if dimension == "grade" else "importance_svg"
            ] = _pie(rows, dimension, captions[dimension])
            for code, label, color in choices:
                if not counts[code]:
                    continue
                values = tuple(
                    sorted(
                        float(row["prose_characters"])
                        for row in articles
                        if (row[dimension] or "Unknown") == code
                        and row.get("prose_characters") is not None
                    ),
                )
                result["lengths"][f"{dimension}:{code}"] = _violin(
                    values,
                    maximum,
                    (dimension, code, label, color),
                    (
                        *titles,
                        interface.text("quality_analysis.violin_summary"),
                    ),
                    counts[code],
                )
        counts = Counter(
            (int(str(row["listed_date"])[:4]), row["grade"])
            for row in articles
            if row.get("listed_date")
        )
        rows = tuple(
            (year, tuple(counts[year, code] for code in selected_grades))
            for year in range(first, last + 1)
        )
        grades = tuple(
            option
            for option in options["grade"]
            if option[0] in selected_grades
        )
        result["years_svg"] = _years(
            rows,
            grades,
            interface.text("quality_analysis.years"),
        )
    return result
