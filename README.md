# Aranami

Aranami (荒波, "rough waves") is a Python package for maintaining on-wiki
reports. It primarily serves the WikiProject Video games (`WPVG`) on the
Chinese Wikipedia (`zhwiki`). Development, testing, and wheel building happen
on a development computer. A separate Jupyter notebook on [Wikimedia PAWS][1]
installs the uploaded wheel and calls Aranami's public API.

> **Status:** Typed Wiki Replica query frames and the logging runner
> bootstrap are implemented. Report jobs and Pageviews access are not.

The development repository never contains or depends on a notebook. There are
no `.ipynb` files, Jupyter imports, or notebook-specific branches in the
package.

## Package architecture

The tree below shows the initial layout. A source adapter may become a
subpackage as its implementation grows.

```text
src/aranami/
├── __init__.py
├── runner.py
├── sources/
│   ├── __init__.py
│   ├── quarry.py
│   └── pageviews.py
├── services/
│   └── __init__.py
├── support/
│   ├── __init__.py
│   └── logs.py
└── jobs/
    └── __init__.py
```

The intended dependency flow is:

```text
aranami.run()
  └── runner
      └── jobs
          └── services
              ├── sources
              └── support
```

- `__init__.py` exposes the small public API, including `run()`.
- `runner.py` currently logs the beginning of one run and returns. It will
  later call selected jobs without adding a scheduler or background loop.
- `jobs/` will contain one task per module, such as building and publishing a
  particular report.
- `services/` will contain reusable higher-level report operations shared by
  multiple jobs.
- The Quarry source adapter in `sources/quarry.py` collects read-only
  SQLAlchemy queries from Wikimedia [Wiki Replicas][2] into Polars. The replica
  tables follow the [MediaWiki database layout][3].
- The Pageviews source adapter, initially `sources/pageviews.py`, will fetch
  data from the [Wikimedia Pageviews API][4].
- `support/` will hold narrowly focused logging, cache, path, date, and
  rate-limit support.

The names `sources`, `services`, and `jobs` describe their responsibilities
more clearly than `utils`, `share`, and `jobber`. New code should use a
specific domain module rather than growing a generic utilities module.

## Data and publication rules

Database access is read-only:

- Query only Wikimedia Wiki Replicas.
- Build queries with SQLAlchemy `select()` and expression APIs.
- Do not insert, update, delete, alter schemas, commit mutations, use raw SQL,
  or use `text()`.

For read-only wiki metadata, prefer the Wiki Replica-backed Quarry source
adapter over Pywikibot API access whenever the replicated database contains the
required data. Use high-level Pywikibot methods when the replicas cannot
provide it, such as current page text. When content or page status is needed
for multiple pages, use
[`PreloadingGenerator`][5] to batch the Pywikibot fallback instead of fetching
pages one at a time.

### Wiki Replica query frames

Create a `Replica` directly from a project database name or from a Pywikibot
site, then bind a SQLAlchemy select:

```python
import polars as pl
import pywikibot
from sqlalchemy import column, select, table

from aranami.sources.quarry import Replica

page = table(
    "page",
    column("page_id"),
    column("page_namespace"),
    column("page_title"),
)
site = pywikibot.Site("zh", "wikipedia")
replica = Replica.from_site(site)
frame = replica.query(
    select(page.c.page_id, page.c.page_title).where(
        page.c.page_namespace == 0,
    ),
).pipe(
    lambda lazy_frame: lazy_frame.with_columns(
        pl.col("page_title").cast(pl.String),
    ),
)
pages = frame.collect()
```

`Replica.wikidata_terms()` selects Wikidata's `termstore` hostname while
retaining the real `wikidatawiki_p` database name. On PAWS, connections use the
existing `.my.cnf` credentials file. Each `Replica` reuses one pooled
connection and recycles it after ten minutes.

Reading data and publishing a report are deliberately separate. On-wiki changes
are made with high-level Pywikibot methods, such as saving a
`Page`, and not through direct `pywikibot.api` requests when a supported method
exists.

The current `aranami.run(dry_run=True)` bootstrap records that execution began
and returns without performing report work. Future report jobs will build
proposed edits without saving them to Wikipedia and write one UTF-8 Markdown
report under `dry-run/`.

This single report is intended for human review. A JSON manifest and nested
per-edit file structure are not required.

## Build on the development computer

Install the locked environment and run the checks:

```shell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pyrefly check
```

Build the wheel:

```shell
uv build --wheel --clear
```

The `--clear` option removes stale artifacts from `dist/` before writing the
current wheel:

```text
dist/aranami-<version>-py3-none-any.whl
```

Before every build, advance the `.devN` or `.postN` number. This gives each
uploaded test wheel a distinct version that `pip --upgrade` can select, even
when rebuilding identical contents.

## External PAWS layout

The PAWS home directory is outside this repository and is expected to look like
this:

```text
PAWS home/
├── run_aranami.ipynb
├── aranami-<version>-py3-none-any.whl
├── aranami-YYYY-MM-DD.log
├── cache/
└── dry-run/
```

- Upload each new wheel beside the notebook. The installation cell selects
  the newest valid Aranami version and removes the other Aranami wheels only
  after installation succeeds.
- `aranami-YYYY-MM-DD.log` appends every run on that UTC date. Aranami retains
  the latest 90 UTC dates.
- `cache/` contains disposable acceleration data. It is intentionally visible
  and safe to delete.
- `dry-run/` contains local proposed-edit artifacts and is created only when
  dry-run output is requested.
- Aranami must write runtime data under the caller's current directory, never
  inside the installed wheel.

PAWS package installations are temporary, so the wheel must be installed again
after the PAWS server restarts. See [Python with pip on PAWS][6].

## Python installation and run cell for PAWS

Copy the following ordinary Python into the notebook's first code cell. Run it
after starting a fresh kernel. It uses PAWS's installed `packaging` library to
select and install the newest Aranami wheel in the current directory. After
installation succeeds, it deletes the other valid Aranami wheels and completes
one Aranami run.

Keep `DRY_RUN = True` to produce a local review report without editing
Wikipedia. Set it to `False` only when publication is intentional.

```python
import subprocess
import sys
from pathlib import Path

from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import Version

DRY_RUN = True


def install() -> None:
    """Ask pip to install the newest local Aranami wheel."""
    if "aranami" in sys.modules:
        raise RuntimeError(
            "Restart the kernel before running this cell again.",
        )

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
    """Run Aranami once with the selected publication mode."""
    import aranami

    aranami.run(dry_run=dry_run)
    print(f"Aranami completed successfully with DRY_RUN={dry_run}.")


if __name__ == "__main__":
    install()
    run(DRY_RUN)
```

## Development conventions

- Keep all reusable behavior in `src/aranami/`.
- Keep the external PAWS notebook limited to installation and
  `aranami.run()`.
- Use Conventional Commits 1.0.0 for commit messages.
- Follow the global repository instructions in [AGENTS.md][7] and the scoped
  `AGENTS.md` nearest to the package code being changed.

### Docstrings

Start every Python file with a module-level docstring. Document public classes
and callables, plus private code whose behavior is nontrivial or not obvious.
Follow the [Google Python docstring guidance][8].

Put the one-sentence summary on the opening line and end it with punctuation.
If more explanation is useful, add one blank line before the longer
description. Google style uses `Args:`, not `Params:`; use
`Returns:`, `Yields:`, and `Raises:` when applicable:

```python
"""Summarize the object in one sentence.

Add a longer description here when the summary is not enough. Keep each
line to 72 characters or fewer, including leading indentation.

Args:
    first_parameter: Describe the first parameter. For descriptions
        that span multiple lines, indent continuation lines by four
        additional spaces instead of aligning them with the first line.
    second_parameter: Describe the second parameter.

Returns:
    Describe the returned value.
"""
```

## License

Aranami is released under CC0-1.0.

[1]: https://wikitech.wikimedia.org/wiki/PAWS
[2]: https://wikitech.wikimedia.org/wiki/Help:Wiki_Replicas
[3]: https://www.mediawiki.org/wiki/Manual:Database_layout
[4]: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html
[5]: https://doc.wikimedia.org/pywikibot/stable/faq.html
[6]: https://wikitech.wikimedia.org/wiki/PAWS/Python_with_Pip
[7]: AGENTS.md
[8]: https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings
