"""Prepare prior newsletter subpages for offline rendering tests."""

from pathlib import Path

from backend.newsletter.subpages import prepare_subpage_layout
from shared.newsletter import Issue


def subpage_layouts() -> dict:
    """Return prepared quality and announcement source layouts.

    Returns:
        Both original October 2026 subpages with generated data slots.
    """
    root = Path(__file__).resolve().parents[2] / "fixtures"
    layouts = {}
    for suffix, kind in (
        ("特優內容", "quality"),
        ("宣告", "announcements"),
    ):
        source = (root / f"newsletter-{kind}-2026-10.wikitext").read_text(
            encoding="utf-8",
        )
        layout = prepare_subpage_layout(source, Issue.parse("2026-10"), kind)
        assert layout is not None
        layouts[suffix] = layout
    return layouts
