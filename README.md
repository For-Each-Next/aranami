# Aranami

Aranami (荒波, "rough waves") maintains on-wiki reports, primarily for
WikiProject Video games on the Chinese Wikipedia. It is developed and built
locally, then installed as a wheel in [Wikimedia PAWS][1].

> **Status:** Aranami is in early development. Its data-source adapters are
> usable, but no report jobs are wired into `aranami.run()` yet.

## Current capabilities

- **Wiki Replicas:** `aranami.sources.quarry.Replica` binds read-only SQLAlchemy
  selections to Wikimedia Wiki Replicas and collects their results through
  composable eager Polars DataFrame pipelines.
- **Pageviews:** `aranami.sources.pageviews.fetch_data` reads one page's raw
  daily observations, `fetch_data_dataframe` converts one page to an eager
  `page`/`date`/`pageview` Polars frame, and `massive` sequentially concatenates
  the same half-open date range for an iterable of pages.
- **Frame previews:** `aranami.support.gtshow` displays compact Polars previews
  with field types, frame dimensions, and configurable leading and trailing
  rows in JupyterLab.
- **Wikitext templates:** `aranami.support.get_templates` recursively finds
  editable `mwparserfromhell` template nodes using site-aware Pywikibot page
  titles, ignores leading spaces and colons, compares remaining
  namespace-like prefixes as literal main-page text, and skips candidates that
  are not valid pages.

The [live Quarry example][7] exercises replica access from PAWS, while the
[combined Pageviews example][8] uses Quarry-selected pages to collect and
preview two rolling years of daily traffic.

## Run on PAWS

Upload one or more Aranami wheels beside the PAWS notebook, then copy the
following Python into its first code cell. The current `aranami.run()` bootstrap
initializes per-run logging and returns without running a report job or editing
a wiki. Keep `DRY_RUN = True` while developing so future report jobs default to
local proposed output; set it to `False` only for an intentional on-wiki run.

```python
import subprocess
import sys
from pathlib import Path

from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import Version

DRY_RUN = True


def install() -> None:
    """Install or update from the newest local Aranami wheel."""
    versioned_wheels: list[tuple[Version, Path]] = []
    for wheel_file in Path.cwd().glob("aranami-*.whl"):
        try:
            distribution, version, _, _ = parse_wheel_filename(
                wheel_file.name,
            )
        except InvalidWheelFilename:
            continue
        if distribution == "aranami":
            versioned_wheels.append((version, wheel_file))

    if not versioned_wheels:
        raise RuntimeError(
            "Expected at least one valid Aranami wheel in the "
            "current directory.",
        )

    _, newest_wheel = max(
        versioned_wheels,
        key=lambda candidate: (candidate[0], candidate[1].name),
    )
    other_wheels = [
        wheel_file
        for _, wheel_file in versioned_wheels
        if wheel_file != newest_wheel
    ]
    args = (
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--quiet",
        str(newest_wheel),
    )
    subprocess.run(args, check=True)

    for wheel_file in other_wheels:
        wheel_file.unlink(missing_ok=True)

    print(
        f"Installed {newest_wheel.name}; "
        f"removed {len(other_wheels)} other wheel(s).",
    )


def run(dry_run: bool) -> None:
    """Run Aranami once."""
    import aranami

    aranami.run(dry_run=dry_run)
    print(f"Aranami completed successfully with DRY_RUN={dry_run}.")


if __name__ == "__main__":
    install()
    run(DRY_RUN)
```

PAWS environments are temporary, so reinstall the wheel after a server
restart. Runtime files are written beneath the notebook's current directory.
See [Python with pip on PAWS][3] for platform guidance.

## Logging

`aranami.run()` appends package log records at `INFO` or above to
`logs/aranami.log`. The active file rotates at UTC midnight and retains the
latest 90 dated archives. Its handler exists only for the duration of the run;
importing Aranami does not configure the root logger or create runtime files.

## Development

Aranami requires Python 3.12 or newer and uses [`uv`][2]:

```shell
uv sync
uv run python -m unittest discover -s tests/unit -v
uv run python -m unittest discover -s tests/integration -v
uv build --wheel --clear
```

Repository rules and verification requirements are maintained in
[AGENTS.md][4] and its scoped package instructions. Completed changes are
recorded in [CHANGELOG.md][5].

## License

Aranami is released under [CC0 1.0 Universal][6].

[1]: https://wikitech.wikimedia.org/wiki/PAWS
[2]: https://docs.astral.sh/uv/
[3]: https://wikitech.wikimedia.org/wiki/PAWS/Python_with_Pip
[4]: AGENTS.md
[5]: CHANGELOG.md
[6]: LICENSE
[7]: tests/live/quarry_paws.py
[8]: tests/live/pageviews_paws.py
