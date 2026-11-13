"""Score surviving WikiWho tokens by reference-excluded source bytes.

Call with exact listing-revision wikitext and ordered surviving tokens.
Reference markup is masked by parser-node identity before alignment;
repeated source strings retain their individual positions.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Heading, Tag, Template, Text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mwparserfromhell.nodes import Node
    from mwparserfromhell.wikicode import Wikicode

_REFERENCE_TAGS = frozenset({"ref", "references"})
_LITERAL_TAGS = frozenset({"nowiki", "source", "syntaxhighlight"})
_REFERENCE_TEMPLATES = frozenset({
    "reflist",
    "references",
    "reference list",
    "reference-list",
    "参考文献",
    "參考文獻",
    "参考资料",
    "參考資料",
})
_REFERENCE_HEADINGS = frozenset({
    "references",
    "bibliography",
    "works cited",
    "notes",
    "footnotes",
    "sources",
    "参考文献",
    "參考文獻",
    "参考资料",
    "參考資料",
    "参考来源",
    "參考來源",
    "注释",
    "註釋",
    "脚注",
    "腳註",
})
_UNPARSED_REFERENCE = re.compile(
    r"<\s*/?\s*(?:ref|references)\b",
    flags=re.IGNORECASE,
)


def _active_nodes(code: Wikicode) -> Iterator[Node]:
    """Walk parser nodes without interpreting literal tag contents.

    Yields:
        Source nodes in depth-first order, retaining node identity.
    """
    for node in code.nodes:
        yield node
        if isinstance(node, Tag) and str(node.tag).strip().casefold() in (
            _LITERAL_TAGS
        ):
            continue
        for child in node.__children__():
            yield from _active_nodes(child)


def _reference_template(node: Template) -> bool:
    """Recognize citation and reference-list template calls.

    Returns:
        Whether the complete template invocation belongs to references.
    """
    name = " ".join(str(node.name).replace("_", " ").split()).casefold()
    for prefix in ("template:", "模板:", "subst:", "safesubst:"):
        name = name.removeprefix(prefix).strip()
    return (
        name in _REFERENCE_TEMPLATES
        or name == "citation"
        or name.startswith(("cite", "引用"))
    )


def _mask_reference_sections(code: Wikicode) -> None:
    """Mask audited reference sections through the next peer heading.

    Nested subsections remain part of their enclosing reference section.
    Only exact normalized headings match; a later peer or higher heading
    returns to ordinary scoring.

    Args:
        code: Source parser tree whose node positions must be preserved.
    """
    reference_level = None
    excluded = []
    for node in code.nodes:
        if isinstance(node, Heading):
            if reference_level is not None and node.level <= reference_level:
                reference_level = None
            heading = " ".join(str(node.title).split()).casefold()
            if reference_level is None and heading in _REFERENCE_HEADINGS:
                reference_level = node.level
        if reference_level is not None:
            excluded.append(node)
    for node in reversed(excluded):
        code.replace(node, Text("\0" * len(str(node))), recursive=False)


def _reference_mask(text: str) -> str:
    """Replace reference nodes with nulls at their original positions.

    Returns:
        Same-length wikitext with references masked. Comments remain.

    Raises:
        ValueError: Malformed reference markup cannot be classified.
    """
    code = mwparserfromhell.parse(text)
    _mask_reference_sections(code)
    for node in reversed(list(_active_nodes(code))):
        excluded = (
            isinstance(node, Tag)
            and str(node.tag).strip().casefold() in _REFERENCE_TAGS
        ) or (isinstance(node, Template) and _reference_template(node))
        if excluded:
            # Preserve identical strings at other source positions.
            code.replace(node, Text("\0" * len(str(node))), recursive=True)
    if any(
        isinstance(node, Text) and _UNPARSED_REFERENCE.search(str(node))
        for node in _active_nodes(code)
    ):
        message = "Unparsed reference markup prevents safe byte scoring."
        raise ValueError(message)
    masked = str(code)
    if len(masked) != len(text):
        message = "Reference masking changed source positions."
        raise ValueError(message)
    return masked


def eligible_bytes(wikitext: str) -> int:
    """Measure reference-excluded source including comments and spaces.

    Args:
        wikitext: Exact listing-revision source for token alignment.

    Returns:
        Full eligible UTF-8 byte count. Whitespace contributes to this
        denominator even when WikiWho omits its ownership provenance.
        The difference from summed token scores stays unattributed.

    Raises:
        ValueError: Source has nulls or references are unclassifiable.
    """
    if not isinstance(wikitext, str) or "\0" in wikitext:
        message = "Eligible byte counts require null-free source text."
        raise ValueError(message)
    return sum(
        len(original.encode())
        for original, retained in zip(
            wikitext,
            _reference_mask(wikitext),
            strict=True,
        )
        if original == retained
    )


def _source_scores(text: str, masked: str) -> tuple[str, list[int]]:
    """Map normalized positions to cumulative original byte weights.

    WikiWho's upstream ``wikiwho.py`` lowercases complete revisions;
    ``utils.py`` splits spaces/newlines and strips paragraph/sentence
    boundaries. Compact whitespace permits those splits without changing
    non-whitespace order. Case folding and Unicode normalization are not
    applied. Ambiguous length-changing or contextual lowercase mappings
    fail rather than assigning an original character to the wrong token.

    Returns:
        Compact lowercase source and cumulative retained UTF-8 bytes.

    Raises:
        ValueError: Lowercasing cannot preserve character positions.
    """
    lowered = [character.lower() for character in text]
    if any(len(value) != 1 for value in lowered) or "".join(lowered) != (
        text.lower()
    ):
        message = "Contextual Unicode casing prevents exact token alignment."
        raise ValueError(message)
    compact = []
    prefix = [0]
    characters = zip(text, lowered, masked, strict=True)
    for original, normalized, retained in characters:
        if original.isspace():
            continue
        compact.append(normalized)
        prefix.append(
            prefix[-1]
            + (len(original.encode()) if retained == original else 0),
        )
    return "".join(compact), prefix


def score_tokens(tokens: pl.DataFrame, wikitext: str) -> pl.DataFrame:
    """Count each surviving token's non-reference UTF-8 source bytes.

    Args:
        tokens: Ordered final-revision WikiWho tokens with text strings.
            Editor, origin and other supplied columns remain unchanged.
        wikitext: Exact original source of that same final revision.

    Returns:
        Original token rows with a UInt32 bytes column. References,
        reference-list/citation templates and audited reference sections
        score zero. Comments and other markup remain attributable.
        Whitespace lacks token ownership and scores zero here while
        remaining in the separate eligible-byte denominator. This
        measures source bytes, not rendered prose or revision sizes.

    Raises:
        ValueError: Coverage, markup or casing prevents exact alignment.
    """
    if (
        not isinstance(wikitext, str)
        or tokens.schema.get("text") != pl.String
        or tokens["text"].null_count()
    ):
        message = "Byte scoring requires original source and token strings."
        raise ValueError(message)
    source, prefix = _source_scores(wikitext, _reference_mask(wikitext))
    scores, position = [], 0
    for text in tokens["text"].to_list():
        compact = "".join(
            character for character in text if not character.isspace()
        )
        end = position + len(compact)
        if not text or source[position:end] != compact:
            message = "WikiWho tokens do not align with the exact source."
            raise ValueError(message)
        scores.append(prefix[end] - prefix[position])
        position = end
    if position != len(source):
        message = "WikiWho tokens leave unmatched non-whitespace source."
        raise ValueError(message)
    return tokens.with_columns(pl.Series("bytes", scores, dtype=pl.UInt32))
