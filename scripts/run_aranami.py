"""Save routine proposals beside this script without wiki writes.

Run ``python scripts/run_aranami.py`` after installing the wheel,
or copy this script beside the PAWS notebook after installing the wheel.
Existing Pywikibot configuration and Wiki Replica access are needed.
Results are saved in ``dry-run/`` beside this script, with one Markdown
summary and one plain UTF-8 ``.wikitext`` file per proposed edit.
When pasted into a notebook cell, use its current directory for storage.
"""

from contextlib import chdir
from pathlib import Path

import aranami


def main() -> None:
    """Run once with storage beside the script or in the notebook.

    Completed proposals are saved even when a later routine fails. Any
    failure propagates after restoring the caller's working directory.
    """
    script_file = globals().get("__file__")
    root = Path(script_file).resolve().parent if script_file else Path.cwd()
    print(f"Saving dry-run results to {root / 'dry-run'}")  # ruff: ignore[print]
    with chdir(root):
        aranami.run(dry_run=True)


if __name__ == "__main__":
    main()
