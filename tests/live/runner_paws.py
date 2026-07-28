"""Exercise the public runner safely from Wikimedia PAWS."""

from pathlib import Path

import aranami


def main() -> None:
    """Run the PAWS-safe bootstrap and print the active log."""
    aranami.run(dry_run=True)
    log_path = Path.cwd() / "logs" / "aranami.log"
    print(  # ruff: ignore[print]
        log_path.read_text(encoding="utf-8"),
        end="",
    )


if __name__ == "__main__":
    main()
