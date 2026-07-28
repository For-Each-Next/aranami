# Aranami package instructions

These instructions apply to `src/aranami/` in addition to the
repository-wide instructions.

## Runtime boundary

- Target Wikimedia PAWS with Python 3.12.
- The PAWS notebook is an external consumer. It uploads and installs the
  wheel, imports `aranami`, and calls the public package API.
- Runtime package code must behave as ordinary Python and must not detect
  or depend on a notebook environment.
- Keep Aranami buildable as a pure-Python wheel with
  `uv build --wheel --clear`.
- The `aranami.run(dry_run=False)` call performs one complete run and
  returns. Do not implement a scheduler, daemon, sleep loop, or notebook
  callback.
- `aranami.run(dry_run=True)` performs the same report construction but
  writes proposed edits locally and must not modify Wikipedia.
- On PAWS, runtime data belongs under the caller's current directory:
  `logs/` for UTC daily logs, `cache/` for disposable cached data, and
  `dry-run/` for proposed-edit artifacts.
- Append all runs on one UTC date to one file under `logs/` and retain
  the latest 90 completed UTC-dated archives.
- Keep `logs/`, `cache/`, and `dry-run/` visible; do not rename them to
  hidden paths.
- Treat `cache/` as an optimization only. Deleting it must never lose
  authoritative data or change the final report.
- Never write runtime data into the installed package or `site-packages`.
- Preserve PAWS's Wikimedia OAuth integration throughout runtime code. Do
  not add custom credential handling when the authenticated PAWS `Site` is
  sufficient.

## Package architecture

Keep dependencies flowing down through these layers:

- `aranami.__init__` is the small public API and exports `run`.
- `runner.py` orchestrates one run by invoking jobs. It contains no report
  implementation or scheduling loop.
- `jobs/` contains one independently runnable report task per module.
  Jobs coordinate services and select intentional on-wiki publication or
  local dry-run output.
- `services/` contains reusable, higher-level report workflows shared by
  jobs.
- `sources/` contains low-level, read-only external data access. Use a
  Quarry source adapter for Wiki Replica queries and a Pageviews source
  adapter for the Wikimedia Pageviews API. Either adapter may be a module
  or a domain-named subpackage as its implementation grows.
- `support/` contains narrowly named cross-cutting helpers such as paths,
  caching, logging, dates, and rate limiting.
- Do not create generic dumping grounds named `utils`, `common`, or
  `helpers`. Add a domain-named module to the appropriate layer.
- Lower layers must not import `jobs` or `runner`, and jobs must not import
  one another.

## Layer instructions

The package instructions remain in force throughout `src/aranami/`. These
directories add rules for their own layer:

- [Source adapter instructions][1] cover Wiki Replica queries, read-only
  Pywikibot fallbacks, and page preloading.
- [Service instructions][2] cover Polars data processing, wikitext
  construction, and proposed-edit values.
- [Job instructions][3] cover report coordination, intentional
  publication, and dry-run artifacts.

[1]: sources/AGENTS.md
[2]: services/AGENTS.md
[3]: jobs/AGENTS.md
