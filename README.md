# Aranami

Aranami (荒波, "rough waves") maintains on-wiki reports, primarily for
WikiProject Video games on the Chinese Wikipedia. It is developed and built
locally, then installed as a wheel in [Wikimedia PAWS][1].

## Routine reports

`aranami.run()` performs one complete routine for Chinese Wikipedia, using
the existing PAWS authentication and Wiki Replica credentials:

- DYK records and statistics, with explicit unknown dates.
- New-page keyword discovery, retaining the latest 100 complete UTC dates
  through yesterday and filling missing dates.
- Assessment lists for A, AL, and Bplus classes and active project reviews.
- Popularity rankings, processing at most one missing report date per run.
- English Wikipedia key-article comparisons for the Chinese project.
- Subscribed PexBot database reports selected by configured page prefixes.

The caller schedules hourly invocations. Up-to-date reports are skipped;
independent jobs continue if another fails, and failures are reported together
at the end. Character reports and cross-wiki quality analysis are manual
services, not scheduled routine jobs.

## Data sources

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

Upload the wheel and the two Python files from `scripts/` to PAWS.
From a directory containing one Aranami wheel, install it with:

```shell
python scripts/install_aranami.py
```

The [installer](scripts/install_aranami.py) searches the current directory
and `dist/`. If several wheels are available, pass the desired path explicitly:

```shell
python scripts/install_aranami.py dist/aranami-0.2.0.post6-py3-none-any.whl
```

It uses the same Python interpreter's pip to install the wheel and its
metadata-declared dependencies. Then run all routines in dry-run mode:

```shell
python scripts/run_aranami.py
```

If the scripts are uploaded beside the notebook rather than in a `scripts/`
folder, omit `scripts/` from these commands. Restart an already-running kernel
after replacing an imported wheel so Python loads the newly installed code.

From a notebook cell, run the files with IPython:

```python
%run scripts/install_aranami.py
%run scripts/run_aranami.py
```

You can also paste each complete script into its own cell. The installer
then ignores Jupyter's kernel arguments, and the launcher saves results
under the notebook's current directory. When calling the installer as an
imported function, supply its arguments explicitly:

```python
from scripts.install_aranami import main

main([])  # Discover one uploaded wheel
```

Use `main(["path/to/aranami-<version>.whl"])` to select a wheel directly.

PAWS environments are temporary, so reinstall the wheel after a server
restart. Runtime files are written beneath the notebook's current directory.
See [Python with pip on PAWS][3] for platform guidance.

Once installed, the routine entry point is simply:

```python
import aranami

aranami.run()
```

Use `aranami.run(dry_run=True)` to write one compact Markdown report and
a plain UTF-8 `.wikitext` file for each proposed edit under `dry-run/`,
without wiki saves or PexBot refresh requests. The report begins with its
UTC date, start/end times, and overall status: `success`, `partly success`,
or `failed`. It lists one task per routine, with individual statuses and
links to proposed wikitext files instead of embedding their contents.
Each wikitext file contains the exact proposed page text. PexBot's
refresh proposals are grouped under one task; external generation cannot
return proposed wikitext without triggering an edit.

The [dry-run script](scripts/run_aranami.py) always enables dry-run mode:

```shell
python scripts/run_aranami.py
```

File execution saves results in `dry-run/` beside the script even from
another working directory. Files share a unique run identifier, such as
`aranami-<timestamp>-<id>.md` and `aranami-<timestamp>-<id>-001.wikitext`,
so repeated runs keep previous results. Successful proposals are retained
if another job fails; a run with no proposals writes only its summary.
The script needs existing Pywikibot configuration and Wiki Replica access,
just like the routine runner. On PAWS, copy it beside the notebook after
installing the wheel and run `python run_aranami.py`.

## Configuration and runtime storage

Generated ranges use HTML comments, with optional settings on the opening
marker:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...generated content...
<!-- aranami end="new-pages" -->
```

Current settings cover new-page retention, Pageviews cache history,
publication lag and missing-data threshold, and assessment source categories.
See [CONTRIBUTING.md][9] for the marker contracts and defaults. Existing
legacy markers migrate when a report is rewritten, preserving surrounding
content and labeled-section transclusion tags.

PexBot works only on Chinese Wikipedia. Its allowed report subtrees are a
tuple in `aranami.config.PEXBOT_PREFIXES`, shipped with the wheel. Add several
prefixes there before building a custom wheel; an empty tuple disables
refreshes. A standalone invocation can override them with
`aranami.jobs.pexbot.run(prefixes=[...])`.

`cache/` contains disposable Parquet data processed with Polars. Delete it
manually or call `aranami.run(clear_cache=True)` to force a rebuild. Pageviews
keeps 800 days by default and extends sufficient histories from their cached
end date; histories too short for the report are fetched again. Rankings have
a two-day publication lag by default. When at least 95% of articles lack an
observation for the target day, the report and cache remain unchanged for an
hourly retry. An observed zero counts as valid data. Daily observations stay
in the internal cache, rather than becoming separate routine data artifacts.

An HTTP 504 is a failed request, not a no-data observation. The routine makes
up to three attempts for transient 502/503/504 responses. If those attempts
fail, it defers the report, keeps the published date unchanged, and retains
completed article requests in a separate pending Parquet snapshot. The next
run resumes that date. The healthy cache is replaced only after the target-day
availability check passes; a failed check discards pending data for a fresh
retry.

`aranami.run()` appends package log records at `INFO` or above to
`logs/aranami.YYYY-MM-DD.log`, one shared file per UTC date for all routines.
Each job logs its start, finish, elapsed time, skipped work, and failures.
The latest 90 completed daily archives are retained alongside today's log.
Importing Aranami does not configure the root logger or create runtime files.

Report jobs select destinations, read current target text, and construct
publication proposals. Content services accept that text and return updated
wikitext. Concrete ranking periods, task-force selections, and report
definitions belong in `aranami.jobs`; services receive them explicitly.

## Manual analysis

The quality-analysis workflows are ordinary Python services using Polars:

```python
import pywikibot

from aranami.services.quality_contents import analyze_quality_contents

english = analyze_quality_contents(pywikibot.Site("en", "wikipedia"))
chinese = analyze_quality_contents(pywikibot.Site("zh", "wikipedia"))
```

Article history extractors use separate, source-verified English and Chinese
rules. In particular, English GAR can establish a GA listing; Chinese GAR
does not. Unknown dates remain null, and no manual-analysis files are written
unless the caller explicitly saves the returned frames.

The shared `services/quality_contents.py` coordinates the analysis. Its
English and Chinese service profiles supply project and grade names,
importance mappings, template contracts, milestone extraction, and prose
rules. Reusable date parsing, prose extraction, and word counting live in
the corresponding support modules.

## Development

Aranami requires Python 3.12 or newer and uses [`uv`][2]:

```shell
uv sync
uv run python -m unittest discover -s tests/unit -v
uv run python -m unittest discover -s tests/integration -v
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv build --wheel --clear
```

Repository rules and verification requirements are maintained in
[AGENTS.md][4], [CONTRIBUTING.md][9], and the scoped package instructions.
Advance the wheel's test-build suffix and regenerate the lockfile before each
explicit build. Completed changes are recorded in [CHANGELOG.md][5].

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
[9]: CONTRIBUTING.md
