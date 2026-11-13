"""Prove and project conservative reference-cleanup attribution repairs.

Build proofs from exact parent/origin source and stable WikiWho tokens.
Only unchanged, uniquely located non-reference characters can recover
their observed parent ownership. New text and unproved origins retain
their original WikiWho attribution. No names or article rules are used.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from data.articles import authorship_corrections as store
from shared.sources.wikiwho import TOKEN_ID_SCHEMA
from tasks.articles.authorship_text import _reference_mask, score_tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shared.sources.wikiwho import RevisionSource


@dataclass(frozen=True, slots=True)
class _Character:
    """Bind one source character to its exact raw token offset."""

    editor: str
    origin: int
    token_id: str
    offset: int
    token_text: str


@dataclass(frozen=True, slots=True)
class CleanupProof:
    """Carry proof facts and conservative character assignments.

    Attributes:
        record: Versioned source identities and reproducible hashes.
        assignments: Stable origin-token offsets mapped to raw and
            independently observed parent attribution.
    """

    record: dict
    assignments: Mapping[tuple[str, int], tuple[_Character, _Character]]


def _sha(value: str) -> str:
    """Fingerprint exact source without normalization.

    Returns:
        SHA-256 of original UTF-8 bytes.
    """
    return hashlib.sha256(value.encode()).hexdigest()


def _token_sha(tokens: pl.DataFrame) -> str:
    """Fingerprint ordered stable token and provenance observations.

    Returns:
        Digest independent of unrelated caller columns.
    """
    return _sha(tokens.select(list(TOKEN_ID_SCHEMA)).write_json())


def _characters(
    text: str,
    tokens: pl.DataFrame,
) -> tuple[str, list[_Character | None]]:
    """Project complete ordered tokens onto retained source positions.

    Returns:
        Exact reference-excluded text and per-character raw ownership.
        Whitespace remains unowned rather than being redistributed.

    Raises:
        ValueError: Token IDs or exact source alignment are incomplete.
    """
    if (
        any(
            tokens.schema.get(key) != value
            for key, value in TOKEN_ID_SCHEMA.items()
        )
        or tokens.select(pl.any_horizontal(pl.all().is_null()).any()).item()
        or tokens["token_id"].n_unique() != tokens.height
    ):
        message = "Provenance proofs require unique complete token identities."
        raise ValueError(message)
    # The shared scorer validates lowercase semantics and full coverage.
    score_tokens(tokens, text)
    mask = _reference_mask(text)
    positions = [
        index for index, value in enumerate(text) if not value.isspace()
    ]
    assigned: list[_Character | None] = [None] * len(text)
    position = 0
    for row in tokens.iter_rows(named=True):
        compact = "".join(row["text"].split())
        end = position + len(compact)
        for offset, source in enumerate(positions[position:end]):
            assigned[source] = _Character(
                row["editor"],
                row["origin"],
                row["token_id"],
                offset,
                row["text"],
            )
        position = end
    retained = [
        index
        for index, (original, kept) in enumerate(zip(text, mask, strict=True))
        if original == kept
    ]
    return (
        "".join(text[index] for index in retained),
        [assigned[index] for index in retained],
    )


def _unique(text: str, value: str) -> bool:
    """Require one occurrence, including overlapping matches.

    Returns:
        Whether an exact block has one unambiguous source location.
    """
    start = text.find(value)
    return bool(value) and start >= 0 and text.find(value, start + 1) < 0


def cleanup_alignment(
    before: str,
    after: str,
) -> tuple[str, str, list[tuple[int, int, int]], int] | None:
    """Recognize linear exact preservation with at most one insertion.

    Reference bytes must decrease. Every old retained character must
    survive in order, with each matched prefix/suffix occurring once in
    both sources. Reordered paragraphs, conversions, deletions and
    ambiguous repeated matches do not establish this proof contract.

    Args:
        before: Exact source immediately before the suspected cleanup.
        after: Exact source after that same cleanup.

    Returns:
        Retained texts, unique matching blocks and removed reference
        bytes, or none when the conservative contract is not proved.
    """
    before_mask, after_mask = _reference_mask(before), _reference_mask(after)
    old = "".join(
        a for a, b in zip(before, before_mask, strict=True) if a == b
    )
    new = "".join(a for a, b in zip(after, after_mask, strict=True) if a == b)
    removed = (len(before.encode()) - len(old.encode())) - (
        len(after.encode()) - len(new.encode())
    )
    if not old.strip() or removed <= 0 or len(new) < len(old):
        return None
    prefix = 0
    while prefix < len(old) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < len(old) - prefix and old[-suffix - 1] == new[-suffix - 1]:
        suffix += 1
    if prefix + suffix != len(old):
        return None
    blocks = ([(0, 0, prefix)] if prefix else []) + (
        [(prefix, len(new) - suffix, suffix)] if suffix else []
    )
    if any(
        not _unique(old, old[start : start + size])
        or not _unique(new, old[start : start + size])
        for start, _, size in blocks
    ):
        return None
    return old, new, blocks, removed


def build_cleanup_proof(
    before: RevisionSource,
    before_tokens: pl.DataFrame,
    after: RevisionSource,
    after_tokens: pl.DataFrame,
) -> CleanupProof | None:
    """Recover parent ownership only where exact source proves survival.

    Args:
        before: Exact source of the actual parent revision.
        before_tokens: Complete raw parent snapshot with stable IDs.
        after: Exact cleanup source with the verified parent identity.
        after_tokens: Complete raw cleanup snapshot with stable IDs.

    Returns:
        Proven source facts and character assignments, or none for an
        edit outside the conservative reference-cleanup contract.

    Raises:
        ValueError: Parent identity or token/source coverage is invalid.
    """
    valid_parent = (
        type(before.parent_revision) is int
        and 0 <= before.parent_revision < before.revision_id
    )
    valid_child = (
        type(after.parent_revision) is int
        and after.parent_revision == before.revision_id
    )
    if (
        any(
            type(value) is not int or value <= 0
            for value in (
                before.revision_id,
                after.revision_id,
            )
        )
        or not valid_parent
        or before.revision_id >= after.revision_id
        or not valid_child
    ):
        message = "A cleanup proof requires its exact actual parent."
        raise ValueError(message)
    for source, tokens in ((before, before_tokens), (after, after_tokens)):
        if (
            tokens.schema.get("origin") != pl.Int64
            or tokens.filter(
                pl.col("origin").is_null()
                | (pl.col("origin") <= 0)
                | (pl.col("origin") > source.revision_id),
            ).height
        ):
            message = "Nonmonotonic token origins withhold automatic repair."
            raise ValueError(message)
    alignment = cleanup_alignment(before.text, after.text)
    if alignment is None:
        return None
    old, new, blocks, removed = alignment
    _, old_owners = _characters(before.text, before_tokens)
    _, new_owners = _characters(after.text, after_tokens)
    assignments = {}
    for old_start, new_start, size in blocks:
        for offset in range(size):
            parent = old_owners[old_start + offset]
            origin = new_owners[new_start + offset]
            if parent and origin and origin.origin == after.revision_id:
                assignments[origin.token_id, origin.offset] = origin, parent
    record = {
        "proof_contract": store.PROOF_CONTRACT,
        "origin_revision": after.revision_id,
        "parent_revision": before.revision_id,
        "before_source_sha256": _sha(before.text),
        "after_source_sha256": _sha(after.text),
        "before_tokens_sha256": _token_sha(before_tokens),
        "after_tokens_sha256": _token_sha(after_tokens),
        "before_characters": len(old),
        "after_characters": len(new),
        "matched_characters": len(old),
        "added_characters": len(new) - len(old),
        "removed_reference_bytes": removed,
    }
    return CleanupProof(
        {"proof_id": store.proof_digest(record), **record},
        assignments,
    )


def project_cleanup(
    proof: CleanupProof,
    text: str,
    tokens: pl.DataFrame,
    *,
    names: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Project one verified origin into a later exact scored snapshot.

    Args:
        proof: Previously verified actual parent/origin observations.
        text: Original source of the revision being scored.
        tokens: Its ordered raw tokens with stable IDs.
        names: Independently resolved editor names, including IPs.

    Returns:
        Nonoverlapping character segments and actual current eligible
        byte weights. New and unproved characters remain raw WikiWho.

    Raises:
        ValueError: A stable token changed identity or source alignment.
    """
    retained, owners = _characters(text, tokens)
    rows = []
    for character, owner in zip(retained, owners, strict=True):
        if owner is None or owner.origin != proof.record["origin_revision"]:
            continue
        assignment = proof.assignments.get((owner.token_id, owner.offset))
        if assignment is None:
            continue
        raw, previous = assignment
        if owner.editor != raw.editor or owner.token_text != raw.token_text:
            message = "A proved stable token changed its raw identity."
            raise ValueError(message)
        if previous.editor == owner.editor:
            continue
        rows.append({
            "proof_id": proof.record["proof_id"],
            "token_id": owner.token_id,
            "token_start": owner.offset,
            "token_stop": owner.offset + 1,
            "raw_editor": owner.editor,
            "editor": previous.editor,
            "name": (names or {}).get(previous.editor),
            "origin": previous.origin,
            "bytes": len(character.encode()),
            "characters": 1,
        })
    if not rows:
        return pl.DataFrame(schema=store.TRANSFER_SCHEMA)
    keys = ["proof_id", "token_id", "raw_editor", "editor", "name", "origin"]
    return (
        pl
        .DataFrame(rows, schema=store.TRANSFER_SCHEMA)
        .sort("token_id", "token_start")
        .with_columns(
            (
                pl.col("token_start").cast(pl.Int64)
                - pl.col("token_start").cum_count().over(keys).cast(pl.Int64)
            ).alias("_run"),
        )
        .group_by(*keys, "_run")
        .agg(
            pl.col("token_start").min(),
            pl.col("token_stop").max(),
            pl.col("bytes").sum(),
            pl.col("characters").sum(),
        )
        .sort("token_id", "token_start")
        .select(list(store.TRANSFER_SCHEMA))
        .cast(store.TRANSFER_SCHEMA)
    )
