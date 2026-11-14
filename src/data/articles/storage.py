"""Locate the shared durable article evidence stores.

Article collectors and newsletter preparation use these paths under the
Toolforge runtime directory for history, authorship, and milestones.
"""

from pathlib import Path

DEFAULT_ROOT = Path("data/hourly")
HISTORY_PATH = DEFAULT_ROOT / "article-history.parquet"
AUTHORSHIP_PATH = DEFAULT_ROOT / "article-authorship.parquet"
MILESTONES_PATH = DEFAULT_ROOT / "article-milestones.parquet"
