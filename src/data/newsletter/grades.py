"""Describe newsletter grade series without source collection.

Use shared assessment metadata for labels and normalization. These
inventories select grades and colors for persisted newsletter charts.
"""

from shared.assessment_profiles import assessment_profile

_ASSESSMENTS = assessment_profile("zhwiki", "电子游戏")
QUALITY_HISTORY_GRADES = ("FA", "FL", "A", "AL", "GA", "Bplus")
NEW_PAGE_GRADES = frozenset(
    label.code
    for label in _ASSESSMENTS.labels("class")
    if label.order is not None or label.code == "Unknown"
)
# Select persisted series; grade labels and colors come from metadata.
_QUALITY_SERIES = (
    "FA",
    "FL",
    "A",
    "AL",
    "GA",
    "Bplus",
    "B",
    "BL",
    "C",
    "CL",
    "Start",
    "List",
    "Stub",
    "SL",
    "Unassessed",
)
QUALITY_GRADES = {
    code.lower(): (
        _ASSESSMENTS.resolve(code, "class").long_name,
        _ASSESSMENTS.color(code, "class", variant="newsletter_annual"),
    )
    for code in _QUALITY_SERIES
}
