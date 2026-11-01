"""Encode assessment strings for Parquet and restore String schemas."""

import polars as pl

CLASS_CATEGORIES = (
    "FA",
    "A",
    "GA",
    "Bplus",
    "B",
    "C",
    "D",
    "Start",
    "Stub",
    "Substub",
    "FL",
    "AL",
    "BL",
    "CL",
    "List",
    "SL",
    "Unassessed",
    "Disambig",
    "Setindex",
    "Draft",
    "FP",
    "File",
    "Category",
    "Template",
    "Module",
    "Portal",
    "Project",
    "User",
    "Help",
    "MediaWiki",
    "Sandbox",
    "Redirect",
    "NA",
)
IMPORTANCE_CATEGORIES = (
    "极高",
    "高",
    "中",
    "低",
    "极低",
    "不适用",
    "无",
    "Top",
    "High",
    "Mid",
    "Low",
    "Bottom",
    "NA",
)


def _categories(name: str) -> tuple[str, ...] | None:
    """Select the vocabulary for an assessment column.

    Args:
        name: Column name to classify.

    Returns:
        Standard labels, or None for unrelated columns.
    """
    if name == "class" or name.endswith("_class"):
        return CLASS_CATEGORIES
    if name == "importance" or name.endswith("_importance"):
        return IMPORTANCE_CATEGORIES
    return None


def encode_assessments(frame: pl.DataFrame) -> pl.DataFrame:
    """Encode assessment strings without changing source values.

    Args:
        frame: Eager table with optional assessment columns.

    Returns:
        A table whose assessment strings use Enum declarations with
        standard labels followed by sorted observed extra labels.
        Nulls remain null and other columns are unchanged.
    """
    expressions = []
    for name, dtype in frame.schema.items():
        categories = _categories(name)
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
