"""Define publication schedules and dataset refresh policy."""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

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
    """

    pages: Mapping[str, str]
    hour: str = "*"
    minute: int = 0
    prefixes: tuple[str, ...] = ()
    misfire_grace_time: int | None = None
    cutoff: str = "Current source observations; publication is independent."


TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "dyks": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/新条目推荐"},
        minute=0,
    ),
    "new_pages": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/新进条目/关键词筛选"},
        hour="0",
        minute=7,
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
        minute=31,
    ),
    "assessment_log": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/评级/日志"},
        hour="0",
        minute=0,
        cutoff="First complete assessment capture for the completed UTC day.",
    ),
    "pageviews": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/热门条目"},
        minute=59,
        misfire_grace_time=3_500,
        cutoff="Completed UTC dates after 18:00 readiness; retry hourly :59.",
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
    ),
    "pexbot": TaskDefinition(
        pages={},
        hour="0",
        minute=0,
        prefixes=("WikiProject:电子游戏/数据库报告",),
        cutoff="Check actual wiki edit date before delegated publication.",
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
    """

    cutoff: str
    hour: str = "*"
    minute: int = 0
    interval: float | None = None


COLLECTION_DEFINITIONS: dict[str, CollectionDefinition] = {
    "newsletter-history": CollectionDefinition(
        cutoff="Current evidence; revisions retain historical cutoffs.",
    ),
    "newsletter-daily": CollectionDefinition(
        hour="0",
        cutoff="Completed UTC day; retain first complete daily observation.",
    ),
    "website": CollectionDefinition(
        interval=3600,
        cutoff="Refresh due datasets independently after their last success.",
    ),
}

WEBSITE_REFRESH_INTERVALS: dict[str, float] = {
    "reviews": 3600,
    "recognized": 86400,
    "dyk": 86400,
    "pageviews": 86400,
    "news_history": 86400,
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
    }


def configuration_id() -> str:
    """Identify configuration changes for completion and page caches.

    Returns:
        Stable hexadecimal digest of all routine definitions.
    """
    return hashlib.sha256(
        json.dumps(routine_configuration(), sort_keys=True).encode(),
    ).hexdigest()
