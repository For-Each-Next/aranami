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
    replacement = str(template)
    edits = [
        (span["start"], span["end"], "")
        for span in record["legacy_dyk_templates"]
    ]
    insertion = record.get(
        "history_insertion",
        history["start"] if history else 0,
    )
    if history and insertion == history["start"]:
        edits.append((history["start"], history["end"], replacement))
    else:
        if history:
            edits.append((history["start"], history["end"], ""))
        prefix = "\n" if insertion and original[insertion - 1] != "\n" else ""
        edits.append((insertion, insertion, prefix + replacement + "\n"))
    result = original
    for start, end, text in sorted(edits, reverse=True):
        result = result[:start] + text + result[end:]
    if len(result.encode()) > MAX_REPAIR_TEXT_BYTES:
        message = "The proposed section-zero repair exceeds its size bound."
        raise ValueError(message)
    return result


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
