"""Encode assessment strings for Parquet and restore String schemas."""

from __future__ import annotations

from ast import literal_eval
from collections.abc import Mapping
from typing import TYPE_CHECKING

import polars as pl

from shared.assessment_profiles import OBSERVED_PROFILE

if TYPE_CHECKING:
    from shared.assessment_profiles import AssessmentProfile


def _categories(
    name: str,
    profile: AssessmentProfile = OBSERVED_PROFILE,
) -> tuple[str, ...] | None:
    """Select the vocabulary for an assessment column.

    Args:
        name: Column name to classify.
        profile: Raw source vocabulary for one wiki/project scope.

    Returns:
        Standard labels, or None for unrelated columns.
    """
    if name == "class" or name.endswith("_class"):
        return profile.class_values
    if name == "importance" or name.endswith("_importance"):
        return profile.importance_values
    return None


def encode_assessments(
    frame: pl.DataFrame,
    *,
    profile: AssessmentProfile = OBSERVED_PROFILE,
) -> pl.DataFrame:
    """Encode assessment strings without changing source values.

    Args:
        frame: Eager table with optional assessment columns.
        profile: Explicit wiki/project raw vocabulary. Derived or
            unscoped tables retain only their observed labels; they
            do not inherit a combined multilingual declaration.

    Returns:
        A table whose assessment strings use Enum declarations with
        declared labels followed by sorted observed extra labels.
        Nulls remain null and other columns are unchanged.
    """
    expressions = []
    for name, dtype in frame.schema.items():
        categories = _categories(name, profile)
        if dtype != pl.String or categories is None:
            continue
        declared = frame.select(
            pl
            .lit(pl.Series(categories, dtype=pl.String))
            .append(pl.col(name).drop_nulls().unique().sort())
            .unique(maintain_order=True),
        ).to_series()
        expressions.append(pl.col(name).cast(pl.Enum(declared)))
    return frame.with_columns(expressions)


def decode_assessments(frame: pl.DataFrame) -> pl.DataFrame:
    """Restore assessment enums to the String API contract.

    Args:
        frame: Eager table loaded from storage or supplied by a caller.

    Returns:
        A table with assessment enums cast to String. Other columns and
        types, including unrelated enums and existing strings, survive.
    """
    return frame.with_columns(
        pl.col(name).cast(pl.String)
        for name, dtype in frame.schema.items()
        if isinstance(dtype, pl.Enum) and _categories(name) is not None
    )


def restore_assessment_schema(
    frame: pl.DataFrame,
    declared_columns: Mapping[str, str],
) -> pl.DataFrame:
    """Restore saved assessment Enums after a Parquet String read.

    Polars 2 reads Parquet Enum columns as String. Retained canonical
    column declarations restore their exact original vocabularies,
    independently of current profile configuration. Callers must bound
    manifest input and compare the complete schema after restoration.

    Args:
        frame: Loaded table with original raw observations.
        declared_columns: Saved column-to-type declarations.

    Returns:
        Table with declared assessment Enums restored. Other columns,
        raw unknown values, and null observations are unchanged.

    Raises:
        ValueError: An Enum declaration is malformed or excludes an
            observed value.
    """
    if not isinstance(declared_columns, Mapping):
        message = "Stored assessment columns require a schema mapping."
        raise ValueError(message)  # ruff: ignore[type-check-without-type-error]
    expressions = []
    for name, dtype in frame.schema.items():
        declared = declared_columns.get(name)
        if (
            name == "pa_taskforces"
            and isinstance(declared, str)
            and declared.startswith("List(Enum(categories=")
        ):
            expressions.append(
                pl.col(name).cast(pl.List(_saved_enum(declared[5:-1]))),
            )
            continue
        if (
            dtype != pl.String
            or _categories(name) is None
            or not isinstance(declared, str)
            or not declared.startswith("Enum(categories=")
        ):
            continue
        expressions.append(pl.col(name).cast(_saved_enum(declared)))
    try:
        return frame.with_columns(expressions)
    except pl.exceptions.PolarsError:
        message = "Stored assessments disagree with their Enum declaration."
        raise ValueError(message) from None


def _saved_enum(declared: str) -> pl.Enum:
    """Validate a canonical saved Enum type without evaluating code.

    Returns:
        Exact original assessment vocabulary, including unknown extras.

    Raises:
        ValueError: The declaration is malformed or noncanonical.
    """
    message = "Stored assessments disagree with their Enum declaration."
    try:
        categories = literal_eval(declared[len("Enum(categories=") : -1])
    except ValueError, TypeError, SyntaxError, RecursionError:
        raise ValueError(message) from None
    if not isinstance(categories, list) or not all(
        isinstance(value, str) for value in categories
    ):
        raise ValueError(message)
    try:
        restored = pl.Enum(categories)
    except pl.exceptions.PolarsError:
        raise ValueError(message) from None
    if str(restored) != declared:
        raise ValueError(message)
    return restored
