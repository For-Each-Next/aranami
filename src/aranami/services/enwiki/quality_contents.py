"""Configure manual quality-content analysis for English Wikipedia.

The shared workflow consumes ``PROFILE`` to select video-game articles
and interpret English assessment names, templates, and prose.
"""

from aranami.services.enwiki import quality_dates
from aranami.services.enwiki.prose import PROFILE as PROSE_PROFILE
from aranami.services.quality_profile import QualityAnalysisProfile

PROFILE = QualityAnalysisProfile(
    project_title="Video games",
    classes=("FA", "FL", "GA"),
    quality_names={"FA": "FA", "FL": "FL", "GA": "GA"},
    importance_names={
        "Top": "Top",
        "High": "High",
        "Mid": "Mid",
        "Low": "Low",
        "Unknown": "Unknown",
    },
    templates={"history": "Template:Article history", "ga": "Template:GA"},
    extract_milestone=quality_dates.extract_milestone,
    prose=PROSE_PROFILE,
    template_value_classes=frozenset({"FL"}),
    language="en",
)
