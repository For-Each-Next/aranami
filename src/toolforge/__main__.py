"""Run the dashboard updater or collect one snapshot."""

from __future__ import annotations

import sys


def main() -> None:
    """Serve by default, or collect once when explicitly requested."""
    if sys.argv[1:2] in (["collect"], ["--once"]):
        del sys.argv[1]
        from toolforge.collect import (  # ruff: ignore[import-outside-top-level]
            main as run,
        )
    else:
        if sys.argv[1:2] == ["serve"]:
            del sys.argv[1]
        from toolforge.server import (  # ruff: ignore[import-outside-top-level]
            main as run,
        )
    run()


if __name__ == "__main__":
    main()
