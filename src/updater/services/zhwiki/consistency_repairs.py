"""Project prepared section-zero repairs for manual review.

Pass an exact checked section-zero input from the consistency collector.
The projection changes supplied template spans and parameters, ordering
literal banner slots by the prepared local guideline plan. Older saved
insertion boundaries remain supported. Runtime retains the complete
proposed header for manual preview or diff.
"""

from __future__ import annotations

from operator import itemgetter

import mwparserfromhell
from mwparserfromhell.nodes import Template

from data.consistency import MAX_REPAIR_TEXT_BYTES, valid_repair_input
from updater.edit_summary import EditSummary


def section0_edit_summary() -> str:
    """Describe the prepared header repair for a manually reviewed edit.

    Returns:
        A complete edit summary within the shared UTF-8 byte budget.
    """
    return EditSummary("Update talk-page article history.").render()


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
    history = record["history_template"]
    template = _history_template(history["text"] if history else None)
    for name in record["obsolete_parameters"]:
        if template.has(name):
            template.remove(name)
    for name, value in record["parameters"].items():
        template.add(name, value, preserve_spacing=True)
    replacement = _block_history(template)
    if "banner_order" in record:
        result = _ordered_header(record, replacement)
    else:
        result = _legacy_header(record, replacement)
    if len(result.encode()) > MAX_REPAIR_TEXT_BYTES:
        message = "The proposed section-zero repair exceeds its size bound."
        raise ValueError(message)
    return result


def _legacy_header(record: dict, replacement: str) -> str:
    """Project saved plans prepared before ranked banner slots existed.

    Returns:
        The corrected lead using its retained insertion boundary.
    """
    original = record["original_text"]
    history = record["history_template"]
    insertion = record.get(
        "history_insertion",
        history["start"] if history else 0,
    )
    result, insertion = _remove_templates(
        original,
        [
            *record["legacy_dyk_templates"],
            *([history] if history else []),
        ],
        insertion,
    )
    return _join_history(
        result[:insertion],
        replacement,
        result[insertion:],
        trailing_newline=original.endswith("\n"),
    )


def _ordered_header(record: dict, replacement: str) -> str:
    """Reorder only prepared slots and preserve all intervening source.

    Args:
        record: Validated literal banner spans in guideline order.
        replacement: Complete updated Article history template.

    Returns:
        The lead with sorted banners and unknown nodes intact.
    """
    original = record["original_text"]
    intervals = _template_intervals(original, record["legacy_dyk_templates"])
    result = original
    for start, end in reversed(intervals):
        result = result[:start] + result[end:]
    slots = [*record["banner_order"]]
    history = record["history_template"]
    if history is not None:
        slots.append(history)
    else:
        insertion = record.get("history_insertion", 0)
        slots.append({"start": insertion, "end": insertion})
    slots.sort(key=itemgetter("start", "end"))
    # Checked source forbids NUL, preventing a literal marker collision.
    marker = "\0article-history\0"
    targets = [span["text"] for span in record["banner_order"]]
    targets.insert(record["history_order"], marker)
    for slot, target in reversed(list(zip(slots, targets, strict=True))):
        start = _mapped_offset(slot["start"], intervals)
        end = _mapped_offset(slot["end"], intervals)
        inserted = target
        if start == end:
            before = "\n" if start and result[start - 1] != "\n" else ""
            after = "\n" if end < len(result) and result[end] != "\n" else ""
            inserted = before + target + after
        result = result[:start] + inserted + result[end:]
    before, after = result.split(marker)
    return _join_history(
        before,
        replacement,
        after,
        trailing_newline=original.endswith("\n"),
    )


def _join_history(
    before: str,
    replacement: str,
    after: str,
    *,
    trailing_newline: bool,
) -> str:
    """Remove boundary blank lines without trimming nonblank source.

    Returns:
        One newline around history while preserving significant prose
        indentation and trailing spaces on neighboring content lines.
    """
    before_end = len(before.rstrip(" \t\r\n"))
    gap = before[before_end:]
    breaks = [gap.find(character) for character in "\r\n" if character in gap]
    if breaks:
        before = before[: before_end + min(breaks)]
    gap = after[: len(after) - len(after.lstrip(" \t\r\n"))]
    after_start = max(gap.rfind("\r"), gap.rfind("\n")) + 1
    after = after[after_start:]
    if not before.strip(" \t\r\n"):
        before = ""
    if not after.strip(" \t\r\n"):
        after = ""
    return (
        (before + "\n" if before else "")
        + replacement
        + ("\n" + after if after else "\n" if trailing_newline else "")
    )


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
    intervals = _template_intervals(original, spans)
    remaining = original
    for start, end in reversed(intervals):
        remaining = remaining[:start] + remaining[end:]
    return remaining, _mapped_offset(insertion, intervals)


def _template_intervals(
    original: str,
    spans: list[dict],
) -> list[tuple[int, int]]:
    """Include otherwise empty target lines in merged removal spans.

    Returns:
        Sorted, nonoverlapping source intervals safe to remove.
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
    return merged


def _mapped_offset(offset: int, intervals: list[tuple[int, int]]) -> int:
    """Map a source boundary after removing the supplied intervals.

    Returns:
        The corresponding boundary in the remaining source.
    """
    return offset - sum(
        min(max(offset - start, 0), end - start) for start, end in intervals
    )


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
