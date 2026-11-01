"""Locate prepared newsletter observations consumed by rendering."""

from pathlib import Path

DATA_VERSION = 2
DEFAULT_ROOT = Path("reports/data/newsletter")
OBSERVATIONS_PATH = DEFAULT_ROOT / "observations"
