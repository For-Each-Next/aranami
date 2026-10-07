"""Run Aranami's schedule monitor until interrupted.

Invoke ``python -m aranami`` after installing the wheel. Reports follow
the public API's schedules and use the caller's working directory for
runtime files. Interrupt the process to stop after active jobs finish.
Pass ``--dry`` for previews and ``--no-run-immediately`` to wait for
scheduled times before the first reports.
"""

from __future__ import annotations

import argparse
from threading import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    """Start configured schedules and wait for graceful shutdown.

    By default, live monitoring checks every routine immediately, then
    follows its UTC schedule. An interrupt stops the scheduler after
    active jobs finish. Other waiting errors shut down the scheduler
    before propagating.

    Args:
        argv: Explicit command-line options, or ``None`` to use the
            process arguments. ``--dry`` selects previews and
            ``--no-run-immediately`` waits for the next cron times.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Write local previews instead of publishing reports.",
    )
    parser.add_argument(
        "--run-immediately",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Check reports immediately before following UTC cron times.",
    )
    arguments = parser.parse_args(argv)
    from aranami import run  # ruff: ignore[import-outside-top-level]

    scheduler = run(
        dry=arguments.dry,
        run_immediately=arguments.run_immediately,
    )
    try:
        Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()
