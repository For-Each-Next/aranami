"""Order wiki titles consistently across reports and summaries."""


def title_sort_key(title: str) -> tuple[str, str]:
    """Return a case-insensitive ordering with deterministic exact ties.

    Args:
        title: Already normalized title whose spelling is preserved.

    Returns:
        Case-folded title followed by its original spelling.
    """
    return title.casefold(), title
