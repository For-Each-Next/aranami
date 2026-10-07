"""Read configuration and replace named regions in HTML comments.

Canonical boundaries are ``<!-- aranami begin="name" -->`` and
``<!-- aranami end="name" -->``. Extra begin attributes configure the
workflow.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

import mwparserfromhell
from mwparserfromhell.nodes import Comment

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from mwparserfromhell.wikicode import Wikicode


@dataclass(frozen=True, slots=True)
class _Region:
    """Locate a validated pair of boundaries within a parser tree."""

    code: Wikicode
    begin: int
    end: int
    options: dict[str, str]


@dataclass(frozen=True, slots=True)
class _Boundary:
    """Describe one matching boundary comment and its configuration."""

    position: int
    kind: str
    options: dict[str, str]


_PAIR_SIZE = 2
_ATTRIBUTE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*")


def _trees(code: Wikicode) -> Iterator[Wikicode]:
    """Yield parser subtrees containing directly replaceable nodes.

    Args:
        code: Root or nested wikitext tree.

    Yields:
        Every subtree, including the supplied root.
    """
    yield code
    for node in code.nodes:
        for child in node.__children__():
            yield from _trees(child)


def _attributes(comment: Comment) -> dict[str, str] | None:
    """Read canonical marker attributes without evaluating their values.

    Args:
        comment: HTML comment whose fields may configure a job.

    Returns:
        Attribute values, or ``None`` for an unrelated HTML comment.

    Raises:
        ValueError: An Aranami marker has malformed or duplicate fields.
    """
    source = str(comment.contents).strip()
    if not source or source.split(maxsplit=1)[0] != "aranami":
        return None
    if "--" in source:
        msg = "Configuration cannot contain HTML comment delimiters."
        raise ValueError(msg)
    attributes = shlex.split(source)
    fields: dict[str, str] = {}
    for attribute in attributes[1:]:
        if attribute == "/":
            continue
        key, separator, value = attribute.partition("=")
        if (
            not separator
            or not value
            or key in fields
            or _ATTRIBUTE_NAME.fullmatch(key) is None
        ):
            msg = "Malformed or duplicate Aranami marker attribute."
            raise ValueError(msg)
        fields[key] = value
    if ("begin" in fields) == ("end" in fields):
        msg = "An Aranami marker needs exactly one begin or end name."
        raise ValueError(msg)
    return fields


def _boundary(
    comment: Comment,
    position: int,
    name: str,
) -> _Boundary | None:
    """Interpret a canonical boundary for one region.

    Args:
        comment: Candidate HTML comment.
        position: Comment's index within its containing parser subtree.
        name: Requested managed-region name.

    Returns:
        Matching boundary details, or none for an unrelated comment.
    """
    attributes = _attributes(comment)
    if attributes is not None:
        kind = "begin" if "begin" in attributes else "end"
        if attributes[kind] != name:
            return None
        options = {
            key: value
            for key, value in attributes.items()
            if key not in {"begin", "end"}
        }
        return _Boundary(position, kind, options)
    return None


def _paired_region(
    code: Wikicode,
    name: str,
    boundaries: list[_Boundary],
) -> _Region:
    """Validate one ordered pair of canonical boundaries.

    Args:
        code: Parser subtree containing the candidate boundaries.
        name: Region name included in validation errors.
        boundaries: Matching comments in source order.

    Returns:
        Validated region ready for replacement.

    Raises:
        ValueError: Boundaries are incomplete, duplicated, or reversed.
    """
    message = f"Expected one ordered marker pair for {name!r}."
    if len(boundaries) != _PAIR_SIZE:
        raise ValueError(message)
    first, last = boundaries
    if first.kind != "begin" or last.kind != "end":
        raise ValueError(message)
    return _Region(
        code,
        first.position,
        last.position,
        first.options,
    )


def _locate(
    code: Wikicode,
    name: str,
) -> _Region | None:
    """Validate one uniquely paired region across the parser tree.

    Args:
        code: Complete parsed page source.
        name: Requested managed-region name.

    Returns:
        The region location, or ``None`` when no markers name it.

    Raises:
        ValueError: Its markers are duplicated, mismatched, or reversed.
    """
    found: list[_Region] = []
    for tree in _trees(code):
        markers = [
            boundary
            for index, node in enumerate(tree.nodes)
            if isinstance(node, Comment)
            and (boundary := _boundary(node, index, name)) is not None
        ]
        if not markers:
            continue
        found.append(_paired_region(tree, name, markers))
    if len(found) > 1:
        msg = f"Duplicate managed regions for {name!r}."
        raise ValueError(msg)
    return found[0] if found else None


def region_options(text: str, name: str) -> dict[str, str]:
    """Read validated begin-comment configuration for a named range.

    Args:
        text: Existing page wikitext.
        name: Name assigned to the managed range.

    Returns:
        String attributes, or an empty mapping for absent markers.
        Malformed markers raise instead of defaulting.
    """
    region = _locate(mwparserfromhell.parse(text), name)
    return {} if region is None else dict(region.options)


def region_content(
    text: str,
    name: str,
) -> str | None:
    """Read the contents of one validated managed region.

    Args:
        text: Existing page source.
        name: Stable region name.

    Returns:
        Enclosed wikitext, or ``None`` when the region is absent.
    """
    region = _locate(mwparserfromhell.parse(text), name)
    if region is None:
        return None
    return "".join(
        str(node) for node in region.code.nodes[region.begin + 1 : region.end]
    )


def integer_option(
    options: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    """Read a bounded integer setting from managed-comment attributes.

    Args:
        options: Attributes from the region's begin marker.
        key: Setting name.
        default: Value when the setting is absent.
        minimum: Inclusive smallest accepted value.
        maximum: Optional inclusive largest accepted value.

    Returns:
        The validated setting or its default.

    Raises:
        ValueError: The supplied value is not an integer in range.
    """
    value = int(options.get(key, str(default)))
    if value < minimum or (maximum is not None and value > maximum):
        msg = f"Invalid {key!r}: {value}; expected {minimum}..{maximum}."
        raise ValueError(msg)
    return value


def managed_region(
    name: str,
    content: str,
    options: Mapping[str, str] | None = None,
) -> str:
    """Wrap generated text in a fresh named HTML-comment range.

    Args:
        name: Stable range name without quotes or comment delimiters.
        content: Generated wikitext to wrap.
        options: Optional initial configuration on the begin comment.

    Returns:
        Wikitext containing a canonical pair of boundary comments.

    Raises:
        ValueError: The name or configuration has unsafe attributes.
    """
    if not name or any(
        value in name for value in ('"', "--", "\n", "\r", "\\")
    ):
        msg = "Invalid managed-region name."
        raise ValueError(msg)
    if any(
        key in {"begin", "end"} or _ATTRIBUTE_NAME.fullmatch(key) is None
        for key in options or {}
    ):
        msg = "Invalid managed-region configuration key."
        raise ValueError(msg)
    attributes = "".join(
        f" {key}={json.dumps(value, ensure_ascii=False)}"
        for key, value in (options or {}).items()
    )
    if "--" in attributes:
        msg = "Configuration cannot contain HTML comment delimiters."
        raise ValueError(msg)
    code = mwparserfromhell.parse("")
    code.append(Comment(f' aranami begin="{name}"{attributes} '))
    code.append(content)
    code.append(Comment(f' aranami end="{name}" '))
    return str(code)


def replace_by_tag(
    tag: str,
    content: str,
    text: str,
) -> str:
    """Replace the content enclosed by one canonical managed region.

    Args:
        tag: Stable managed-region name.
        content: Replacement wikitext.
        text: Complete existing page source.

    Returns:
        Updated text preserving canonical begin options and other text.

    Raises:
        ValueError: Boundaries are absent, ambiguous, or malformed.
    """
    code = mwparserfromhell.parse(text)
    region = _locate(code, tag)
    if region is None:
        msg = f"Missing managed-region markers for {tag!r}."
        raise ValueError(msg)
    region.code.nodes[region.begin + 1 : region.end] = mwparserfromhell.parse(
        content,
    ).nodes
    return str(code)
