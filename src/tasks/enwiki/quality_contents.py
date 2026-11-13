"""Configure manual quality-content analysis for English Wikipedia.

The shared workflow consumes ``PROFILE`` to select video-game articles
and interpret English assessment names, templates, and prose.
"""

from shared.assessment_profiles import assessment_profile
from tasks.enwiki import quality_dates
from tasks.prose import PROFILE as PROSE_PROFILE
from tasks.quality_profile import QualityAnalysisProfile

_ASSESSMENTS = assessment_profile("enwiki", "Video games")

PROFILE = QualityAnalysisProfile(
    project_title="Video games",
    classes=tuple(
        label.raw_value
        for label in _ASSESSMENTS.classes
        if label.code in {"FA", "FL", "GA"}
    ),
    assessments=_ASSESSMENTS,
    templates={"history": "Template:Article history", "ga": "Template:GA"},
    extract_milestone=quality_dates.extract_milestone,
    prose=PROSE_PROFILE,
    template_value_classes=frozenset({"FL"}),
    language="en",
)
