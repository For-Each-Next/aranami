"""Configure manual quality-content analysis for Chinese Wikipedia.

The shared workflow consumes ``PROFILE`` to select video-game articles
and normalize Chinese assessments before applying local template rules.
"""

from aranami.services.quality_profile import QualityAnalysisProfile
from aranami.services.zhwiki import quality_dates
from aranami.services.zhwiki.prose import PROFILE as PROSE_PROFILE

PROFILE = QualityAnalysisProfile(
    project_title="电子游戏",
    classes=("典范", "特色列表", "优良"),
    quality_names={"典范": "FA", "特色列表": "FL", "优良": "GA"},
    importance_names={
        "极高": "Top",
        "高": "High",
        "中": "Mid",
        "低": "Low",
        "未知": "Unknown",
    },
    templates={"history": "Template:Article history"},
    extract_milestone=quality_dates.extract_milestone,
    prose=PROSE_PROFILE,
    template_value_classes=frozenset({"FL"}),
    language="zh",
)
