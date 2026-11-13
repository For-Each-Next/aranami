"""Define publication schedules and dataset refresh policy."""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from shared.assessment_profiles import assessment_profile_configuration

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    """Configure default pages and recurring UTC execution times.

    Attributes:
        pages: Destination titles keyed by report names.
        hour: Selected UTC cron hours, or every hour.
        minute: Minute within each selected hour.
        prefixes: Allowed slash-delimited PexBot subscription roots.
        misfire_grace_time: Maximum accepted scheduler delay in seconds.
        cutoff: Dataset boundary or current-source freshness contract.
        idle: Wait for ordinary scheduled work before starting.
        retry_hourly: Daily work retries at hourly checks while behind.
    """

    pages: Mapping[str, str]
    hour: str = "*"
    minute: int = 0
    prefixes: tuple[str, ...] = ()
    misfire_grace_time: int | None = None
    cutoff: str = "Current source observations; publication is independent."
    idle: bool = False
    retry_hourly: bool = False


TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "dyks": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/新条目推荐"},
        minute=0,
        idle=True,
    ),
    "new_pages": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/新进条目/关键词筛选"},
        hour="*",
        minute=0,
        idle=True,
        retry_hourly=True,
        cutoff="Complete UTC dates through yesterday; resume missing dates.",
    ),
    "assessment_lists": TaskDefinition(
        pages={
            "Bplus": "WikiProject:电子游戏/认证条目/Bplus",
            "BPAN": "WikiProject:电子游戏/认证条目/BPAN",
            "A": "WikiProject:电子游戏/认证条目/A",
            "AL": "WikiProject:电子游戏/认证条目/AL",
            "ACC": "WikiProject:电子游戏/认证条目/ACC",
            "PPR": "WikiProject:电子游戏/认证条目/PPR",
        },
        hour="0",
        minute=0,
        misfire_grace_time=None,
        idle=True,
    ),
    "assessment_log": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/评级/日志"},
        hour="0",
        minute=0,
        idle=True,
        cutoff="First complete assessment capture for the completed UTC day.",
    ),
    "pageviews": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/热门条目"},
        minute=0,
        retry_hourly=True,
        idle=True,
        misfire_grace_time=3_500,
        cutoff="Completed UTC dates after 19:00 readiness; retry hourly :00.",
    ),
    "enwp_key_articles": TaskDefinition(
        pages={
            "important": (
                "WikiProject:电子游戏/数据库报告/英文维基百科重要条目"
            ),
            "quality": "WikiProject:电子游戏/数据库报告/英文维基百科优质条目",
        },
        hour="0",
        minute=0,
        cutoff="Reuse the completed-day assessment capture, including delays.",
        idle=True,
    ),
    "pexbot": TaskDefinition(
        pages={},
        hour="0",
        minute=0,
        prefixes=("WikiProject:电子游戏/数据库报告",),
        cutoff="Skip PexBot edits within 24 hours before delegation.",
        idle=True,
    ),
}


@dataclass(frozen=True, slots=True)
class CollectionDefinition:
    """Describe a dataset owner's trigger and source-time semantics.

    Attributes:
        hour: Selected UTC cron hours, or every hour.
        minute: UTC minute within each selected hour.
        interval: Seconds between due checks for an interval job.
        cutoff: Source boundary or freshness rule, separate from the
            actual time collection finishes.
        idle: Wait outside workers until ordinary work finishes.
    """

    cutoff: str
    hour: str = "*"
    minute: int = 0
    interval: float | None = None
    idle: bool = False


REDIRECT_SCAN_CHECK_SECONDS = 86400
ASSESSMENT_CATEGORIES = (
    "乙上级电子游戏条目",
    "请求乙上级评审的电子游戏条目",
    "甲级电子游戏条目",
    "甲级列表级电子游戏条目",
    "请求甲级评审的电子游戏条目",
    "请求专题评审的电子游戏条目",
)

COLLECTION_DEFINITIONS: dict[str, CollectionDefinition] = {
    "project-data": CollectionDefinition(
        cutoff=(
            "Page lists and replica revision IDs first each UTC hour; "
            "first complete daily snapshot at midnight."
        ),
    ),
    "newsletter-history": CollectionDefinition(
        cutoff="Current evidence; revisions retain historical cutoffs.",
    ),
    "newsletter-daily": CollectionDefinition(
        hour="0",
        idle=True,
        cutoff="Completed UTC day; retain first complete daily observation.",
    ),
    "website": CollectionDefinition(
        cutoff="Refresh due datasets independently after their last success.",
    ),
    "quality-analysis": CollectionDefinition(
        minute=2,
        idle=True,
        cutoff=(
            "Current quality membership and exact changed article revisions; "
            "retain precomputed Parquet summaries for saved-data web reads."
        ),
    ),
    "quality-authorship": CollectionDefinition(
        minute=2,
        idle=True,
        cutoff=(
            "Latest continuous B/BL talk ranges and inferred article cutoffs; "
            "resume bounded cached history and listing-time attribution."
        ),
    ),
}

PROJECT_DATA_SCOPES: tuple[tuple[str, str], ...] = (
    ("zh", "电子游戏"),
    ("zh", "电子游戏/宝可梦工作组"),
    ("zh", "电子游戏/我的世界"),
    ("zh", "电子游戏/史克威尔艾尼克斯工作组"),
    ("zh", "电子游戏/世嘉工作组"),
    ("zh", "电子游戏/任天堂工作组"),
    ("zh", "电子游戏/米哈游工作组"),
)

PROJECT_DATA_TASKFORCES: dict[tuple[str, str], tuple[str, ...]] = {
    ("zhwiki", "电子游戏"): tuple(
        project for _, project in PROJECT_DATA_SCOPES[1:]
    ),
}

WEBSITE_REFRESH_INTERVALS: dict[str, float] = {
    "reviews": 3600,
    "consistency": 3600,
    "recognized": 3600,
    "dyk": 86400,
    "pageviews": 86400,
    "news_history": 3600,
    "backlog": 86400,
    "newsletter": 86400,
}


def routine_configuration() -> dict[str, object]:
    """Return the serializable destination and UTC schedule contract.

    Returns:
        Versioned metadata shared with the read-only website.
    """
    return {
        "schema": 1,
        "routines": {
            name: asdict(definition)
            for name, definition in TASK_DEFINITIONS.items()
        },
        "collections": {
            name: asdict(definition)
            for name, definition in COLLECTION_DEFINITIONS.items()
        },
        "website_refresh_intervals": dict(WEBSITE_REFRESH_INTERVALS),
        "assessment_profiles": assessment_profile_configuration(),
        "project_data_scopes": [
            {"language": language, "project": project}
            for language, project in PROJECT_DATA_SCOPES
        ],
        "project_data_taskforces": [
            {"wiki": wiki, "project": project, "taskforces": list(children)}
            for (wiki, project), children in PROJECT_DATA_TASKFORCES.items()
        ],
    }


def configuration_id() -> str:
    """Identify configuration changes for completion and page caches.

    Returns:
        Stable hexadecimal digest of all routine definitions.
    """
    return hashlib.sha256(
        json.dumps(routine_configuration(), sort_keys=True).encode(),
    ).hexdigest()
