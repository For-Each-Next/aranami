"""Locate the shared durable article evidence stores.

Article collectors and newsletter preparation use these paths under the
Toolforge runtime directory for history, authorship, and milestones.
"""

from pathlib import Path

DEFAULT_ROOT = Path("reports/data/articles")
HISTORY_PATH = DEFAULT_ROOT / "history.parquet"
AUTHORSHIP_PATH = DEFAULT_ROOT / "authorship.parquet"
MILESTONES_PATH = DEFAULT_ROOT / "milestones.parquet"
