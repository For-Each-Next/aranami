"""Project prepared section-zero repairs for manual review.

Pass an exact checked section-zero input from the consistency collector.
The projection changes its supplied template spans and parameters, using
the prepared insertion boundary to preserve the local banner order;
runtime retains the complete proposed header for manual preview or diff.
"""

from __future__ import annotations

import mwparserfromhell
from mwparserfromhell.nodes import Template

from data.consistency import MAX_REPAIR_TEXT_BYTES, valid_repair_input
from updater.edit_summary import EditSummary


def section0_edit_summary() -> str:
    """Describe the prepared manual header repair with tool attribution.

    Returns:
        A complete edit summary within the shared UTF-8 byte budget.
    """
    summary = EditSummary("Update talk-page homepage history.").render()
    return EditSummary.with_attribution(summary)


def render_section0_repair(record: dict) -> str:
    """Apply a prepared parameter plan to the exact checked header.

    Args:
        record: Validated source header, literal template spans and
            parameter updates prepared by the acquisition owner.

    Returns:
        Complete replacement section zero, preserving unrelated source.

    Raises:
        ValueError: Input proof is invalid or the result is oversized.
    """
    if not valid_repair_input(record):
        message = "Section-zero repair requires valid checked source facts."
        raise ValueError(message)
    original = record["original_text"]
    history = record["history_template"]
    template = _history_template(history["text"] if history else None)
    for name in record["obsolete_parameters"]:
        if template.has(name):
            template.remove(name)
    for name, value in record["parameters"].items():
        template.add(name, value, preserve_spacing=True)
    replacement = _block_history(template)
    insertion = record.get(
        "history_insertion",
        history["start"] if history else 0,
    )
    result, insertion = _remove_templates(
        original,
        [*record["legacy_dyk_templates"], *([history] if history else [])],
        insertion,
    )
    before = result[:insertion].rstrip(" \t\r\n")
    after = result[insertion:].lstrip(" \t\r\n")
    result = (
        (before + "\n" if before else "")
        + replacement
        + ("\n" + after if after else "\n" if original.endswith("\n") else "")
    )
    if len(result.encode()) > MAX_REPAIR_TEXT_BYTES:
        message = "The proposed section-zero repair exceeds its size bound."
        raise ValueError(message)
    return result


def _block_history(template: Template) -> str:
    """Render aligned explicit parameters and preserve value content.

    Named values discard only surrounding formatting whitespace, which
    MediaWiki already trims. Nested markup, comments and internal line
    breaks retain their source. Positional whitespace cannot be changed
    safely when making its key explicit.

    Args:
        template: Detached literal call with prepared parameter changes.

    Returns:
        A block template with one parameter per line and no added gaps.

    Raises:
        ValueError: An implicit parameter has significant whitespace.
    """
    parameters = []
    for parameter in template.params:
        value = str(parameter.value)
        if not parameter.showkey and value != value.strip():
            message = "Cannot reformat significant positional whitespace."
            raise ValueError(message)
        parameters.append((str(parameter.name).strip(), value.strip()))
    width = max((len(name) for name, _value in parameters), default=0)
    return "\n".join([
        "{{" + str(template.name).strip(),
        *(f"| {name:<{width}} = {value}" for name, value in parameters),
        "}}",
    ])


def _remove_templates(
    original: str,
    spans: list[dict],
    insertion: int,
) -> tuple[str, int]:
    """Remove exact targets and their otherwise empty source lines.

    Args:
        original: Complete checked leading section.
        spans: Nonoverlapping literal targets from the prepared plan.
        insertion: Original boundary selected for Article history.

    Returns:
        Remaining source and the corresponding insertion boundary,
        including boundaries within a removed line's indentation.
    """
    intervals = []
    for span in spans:
        start, end = span["start"], span["end"]
        line_start = original.rfind("\n", 0, start) + 1
        line_end = original.find("\n", end)
        line_end = len(original) if line_end < 0 else line_end
        if not original[line_start:start].strip(" \t\r") and not original[
            end:line_end
        ].strip(" \t\r"):
            start, end = line_start, min(line_end + 1, len(original))
        intervals.append((start, end))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    remaining = original
    mapped = insertion
    for start, end in reversed(merged):
        remaining = remaining[:start] + remaining[end:]
        mapped -= min(max(insertion - start, 0), end - start)
    return remaining, mapped


def _history_template(text: str | None) -> Template:
    """Build the supplied literal call or a canonical new destination.

    Returns:
        A detached template ready for prepared parameter changes.

    Raises:
        ValueError: The supplied span is not exactly one template call.
    """
    if text is None:
        return Template("Article history\n")
    code = mwparserfromhell.parse(text)
    if len(code.nodes) != 1 or not isinstance(code.nodes[0], Template):
        message = "The checked history span is not a literal template call."
        raise ValueError(message)
    return code.nodes[0]
