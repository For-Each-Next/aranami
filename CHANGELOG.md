# Changelog

This file records completed repository changes, including work that has not
been committed to Git. Entries are grouped by package release line.

## Until 0.4

### 0.3.3 (2026-10-06 06:59 UTC)

Overview: Clarified DYK report totals and paced consecutive PexBot refreshes.

- Separated completed article and active nominee counts in DYK edit
  summaries while retaining nomination outcomes and execution timing.
- Added a 15-second cooldown after each live PexBot request finishes before
  starting the next report, including after failed attempts.

### 0.3.2 (2026-10-04 03:22 UTC)

Overview: Clarified DYK edit summaries with explicit nomination outcomes.

- Labeled new, passed, and failed DYK nominees in edit summaries while
  distinguishing removals of historical completed entries.
- Recognized renamed nominees and repeat completions without mislabeling
  delayed candidate cleanup as a failed nomination.

### 0.3.1 (2026-10-03 07:25 UTC)

Overview: Corrected Nintendo task-force membership in Pageviews rankings.

- Corrected Nintendo Pageviews selection to use the Nintendo task-force
  membership instead of the broader Nintendo project.

### 0.3.0 (2026-10-03 06:54 UTC)

Overview: Added scheduled PAWS reports, resilient Pageviews updates, improved
new-page discovery, and automated wheel releases.

- Added six independently scheduled reports, immediate startup checks, and
  PAWS authentication reuse; isolated failures between routines.
- Changed `run(dry=False)` to recurring live publication and added `run_once()`
  for immediate selected runs; replaced `dry_run` with `dry`.
- Returned reusable schedulers and required shutdown before changing output
  mode or clearing caches.
- Added publication proposals, concurrent-change checks, and unchanged-page
  skips; dry runs saved Markdown summaries and wikitext previews.
- Kept English edit summaries within 255 UTF-8 bytes, including execution
  timing, linked Pageviews leaders, and membership changes.
- Tracked stable page IDs across renames and promotions; advanced validated
  membership snapshots only after successful publication.
- Consolidated wheel installation and fresh-process monitoring in
  `scripts/run_aranami.py`, with interrupt forwarding and safe wheel cleanup.
- Retained 100 complete UTC dates of new pages, filled gaps, and included
  redirect conversions without duplicate page IDs.
- Preserved new-page ID and creation-time comments across icon refreshes and
  enriched saved rows without repeating discovery.
- Used `Updated class icons.` when yesterday's new-page section existed;
  reserved dated discovery counts for newly generated latest sections.
- Processed one missing Pageviews date per call; required canonical report
  date comments and invocation time at or after 18:00 UTC for yesterday.
- Removed Pageviews `lag-days`; deferred dates with at least 95% missing
  observations while preserving measured zeroes and healthy caches.
- Skipped HTTP 504 articles, retried HTTP 502/503 responses, and resumed
  completed Pageviews requests from pending Parquet checkpoints.
- Added atomic disposable caches, 800-day Pageviews histories, and shared
  UTC daily logs with 90 completed dates of retention.
- Added configurable HTML-comment ranges and migrated legacy markers while
  preserving surrounding text; restricted PexBot to Chinese Wikipedia.
- Preserved manual English and Chinese quality analysis with verified wiki
  profiles, batched reads, and read-only Polars replica queries.
- Replaced `services.quality_prose` with shared support APIs for date,
  template, prose, and word-counting mechanics.
- Added APScheduler 3.x and ipykernel 7.4 requirements, multilingual usage
  guides, engineering conventions, and focused offline regressions.
- Added reusable GitHub CI checks and automatic tagged releases with verified
  wheels and version-specific changelog notes.
- Removed character-report APIs, tests, and unused character-only queries.

## Until 0.3

### 0.2.0 (2026-08-23 08:49 UTC)

Overview: Replaced deferred Pageviews totals with direct daily-data functions,
aligned Quarry pipelines with eager Polars frames, and added template lookup.

- Replaced deferred Pageviews classes and totals with `DailyPageviews`,
  `fetch_data()`, `fetch_data_dataframe()`, and `massive()`.
- Changed cumulative `title/start/stop/views` rows to raw `page/date/pageview`
  data, preserving duplicates, API order, zeroes, and typed empty frames.
- Identified Pageviews requests with an operator contact; made one attempt,
  treated HTTP 404 as missing data, and removed fixed pacing and timeouts.
- Changed Quarry `pipe()` callbacks and query results to eager DataFrames.
- Removed `ratelimit` and its stubs; added `mwparserfromhell` at runtime.
- Added recursive `get_templates()` lookup with normalized titles, source
  order, duplicates, and editable parser objects.

## Until 0.2

### 0.1.4 (2026-08-22 11:22 UTC)

Overview: Added a PAWS-ready Pageviews adapter with Polars totals, typed
observations, rate-limited API access, and practical usage documentation.

- Added `Pageviews.query(...).pipe(...).collect()` for cumulative Polars
  totals, Pywikibot sites, typed observations, and null missing totals.
- Added identifiable requests limited to two per second and a workflow
  combining Quarry selections with two years of daily views.
- Replaced `polars-u64-idx` with `polars`, pinned Great Tables 0.23.0, added
  `ratelimit` and stubs, and adopted Ruff-only verification.
- Documented source adapters, frame previews, and the bootstrap runner.

### 0.1.3 (2026-07-29 11:11 UTC)

Overview: Improved frame previews and aligned logging, licensing, and wheel
contents with Python project conventions.

- Rendered empty frames safely and displayed all rows for nonpositive limits.
- Adopted the stable `aranami` logging hierarchy and documented UTC rotation.
- Added the complete CC0 1.0 license and included it in wheels; excluded
  development instructions and bounded the build backend's minor release.

### 0.1.2 (2026-07-28 18:11 UTC)

Overview: Added compact Polars previews, streamlined PAWS wheel updates, and
simplified UTC daily log rotation.

- Added `aranami.support.gtshow` for configurable JupyterLab Polars previews
  with omission markers, field types, and complete frame dimensions.
- Added UTC midnight rotation for `logs/aranami.log` with 90 archives.
- Allowed installation of the newest local wheel when Aranami was loaded.

### 0.1.1 (2026-07-28 16:19 UTC)

Overview: Added typed Wiki Replica queries and a public PAWS runner with
persistent logging and safer wheel management.

- Added typed, read-only Wiki Replica queries with Polars postprocessing,
  project/site support, Wikidata terms, and Wikimedia table metadata.
- Added `aranami.run(dry_run=...)` for one complete invocation.
- Added UTC daily logs under the caller's `logs/` directory with 90 dates.
- Installed the newest valid local wheel and removed older wheels after
  success; documented PAWS operation and local development.

### 0.1.0 (2026-07-27 21:38 UTC)

Overview: Initialized the Aranami package, documented PAWS workflows, and
established locked development tooling.

- Initialized the pure-Python package and its source, service, support,
  job, and runner layers.
- Documented PAWS installation, runtime storage, and dry-run workflows.
- Added locked tooling and Semantic Versioning with PEP 440 release rules.
