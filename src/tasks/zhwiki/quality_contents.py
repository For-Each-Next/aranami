"""Configure manual quality-content analysis for Chinese Wikipedia.

The shared workflow consumes ``PROFILE`` to select video-game articles
and normalize Chinese assessments before applying local template rules.
"""

from shared.assessment_profiles import assessment_profile
from tasks.prose import PROFILE as PROSE_PROFILE
from tasks.quality_profile import QualityAnalysisProfile
from tasks.zhwiki import quality_dates

_ASSESSMENTS = assessment_profile("zhwiki", "电子游戏")

PROFILE = QualityAnalysisProfile(
    project_title="电子游戏",
    classes=tuple(
        label.raw_value
        for label in _ASSESSMENTS.classes
        if label.code in {"FA", "FL", "GA"}
    ),
    assessments=_ASSESSMENTS,
    templates={"history": "Template:Article history"},
    extract_milestone=quality_dates.extract_milestone,
    prose=PROSE_PROFILE,
    template_value_classes=frozenset({"FL"}),
    language="zh",
)
