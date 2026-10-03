"""Exercise the public runner safely from Wikimedia PAWS."""

import datetime as dt
from pathlib import Path

import aranami


def main() -> None:
    """Construct routine proposals and print today's shared UTC log."""
    aranami.run(dry_run=True)
    today = dt.datetime.now(dt.UTC).date()
    log_path = Path.cwd() / "logs" / f"aranami.{today.isoformat()}.log"
    print(  # ruff: ignore[print]
        log_path.read_text(encoding="utf-8"),
        end="",
    )


if __name__ == "__main__":
    main()
