"""Exercise the public runner safely from Wikimedia PAWS."""

from datetime import UTC, datetime
from pathlib import Path

import aranami


def main() -> None:
    """Run the PAWS-safe bootstrap and print today's UTC log."""
    aranami.run(dry_run=True)
    today = datetime.now(UTC).date().isoformat()
    log_path = Path.cwd() / "logs" / f"aranami-{today}.log"
    print(  # ruff: ignore[print]
        log_path.read_text(encoding="utf-8"),
        end="",
    )


if __name__ == "__main__":
    main()
