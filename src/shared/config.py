"""Define routine destinations and schedules for both applications."""

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
    """

    pages: Mapping[str, str]
    hour: str = "*"
    minute: int = 0
    prefixes: tuple[str, ...] = ()
    misfire_grace_time: int | None = None


TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "dyks": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/新条目推荐"},
        minute=0,
    ),
    "new_pages": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/新进条目/关键词筛选"},
        hour="0",
        minute=7,
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
    "pageviews": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/热门条目"},
        minute=59,
        misfire_grace_time=3_500,
    ),
    "enwp_key_articles": TaskDefinition(
        pages={
            "important": (
                "WikiProject:电子游戏/数据库报告/英文维基百科重要条目"
            ),
            "quality": "WikiProject:电子游戏/数据库报告/英文维基百科优质条目",
        },
        hour="23",
        minute=24,
    ),
    "pexbot": TaskDefinition(
        pages={},
        hour="0",
        minute=0,
        prefixes=("WikiProject:电子游戏/数据库报告",),
    ),
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
    }


def configuration_id() -> str:
    """Identify configuration changes for completion and page caches.

    Returns:
        Stable hexadecimal digest of all routine definitions.
    """
    return hashlib.sha256(
        json.dumps(routine_configuration(), sort_keys=True).encode(),
    ).hexdigest()
