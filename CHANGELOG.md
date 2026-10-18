# Changelog

This file records major completed changes, grouped by package release line.

## Until 0.6

### 0.5.1 (2026-10-18 05:21 UTC)

Overview: Shortened edit-summary attribution to leave more room for report
changes while linking to Aranami's Toolforge homepage.

- Added a concise parenthetical Toolforge link with the installed version
  and measured duration to live and dry-run edit summaries.
- Kept two decimal places below one minute and rounded longer durations
  to whole seconds in edit summaries.
- Replaced prior attribution when reusing summaries, preserving complete
  links within the 255-byte limit.

### 0.5.0 (2026-10-18 04:51 UTC)

Overview: Established Toolforge as the primary runtime, with repeatable wheel
deployment, public status and daily logs, and a consolidated maintenance
workflow.

- Added validated wheel uploads and Python 3.13 environment setup using
  existing wiki authentication and Wiki Replica configuration.
- Added a public daily-log viewer at `/logs/`, served by the separate
  `aranami-logs` webservice with its own uploader.
- Added a homepage with the version, action names, results, and durations,
  linked to WikiProject Video games on Chinese Wikipedia.
- Abbreviated PexBot query payloads in application logs while retaining
  other progress and failure details.
- Skipped reports already edited by PexBot on the current UTC date, including
  when a later edit was made by another author.
- Consolidated Toolforge operation, upgrades, previews, and recovery across
  the documentation, retaining PAWS as an optional notebook environment.

## Until 0.5

### 0.4.1 (2026-10-12 11:51 UTC)

Overview: Refreshed English quality report introduction counters alongside
the dated article list.

- Updated total, featured article, featured list, and good article counts
  from the current English quality report membership.
- Required paired counter ranges and preserved surrounding descriptions
  and section-transclusion tags.

### 0.4.0 (2026-10-07 08:29 UTC)

Overview: Simplified report maintenance around current marker and identity
contracts, removing obsolete compatibility code and redundant documentation.

- Required paired `aranami` comments for managed ranges; removed migration
  from `update` comments, section-only ranges, and unmarked new-page reports.
- Removed old row-ID comment readers while retaining cached identities and
  title/Wikidata matching when caches are absent.
- Removed unused summary helpers and managed-range reexports; range helpers
  now live only in `aranami.support.regions`.
- Removed the `bpcn` review alias, ignored assessment `member_ids` argument,
  and `PEXBOT_PREFIXES` alias in favor of central task definitions.
- Removed the notebook kernel dependency from the runtime package.
- Consolidated current usage guidance and removed redundant interface catalogs.
- Preserved daily gap filling, Pageviews catch-up, and pending-request recovery.

## Until 0.4

### 0.3.5 (2026-10-07 08:04 UTC)

Overview: Ordered English quality articles by their listing dates and
described English quality-class changes in edit summaries.

- Grouped FA, FL, and GA together by year and descending English listing
  date, with dated comments and unresolved dates last.
- Cached resolved and unresolved dates by discussion revision and class,
  reducing repeated English Wikipedia content requests.
- Added English listing, promotion, removal, and class-transition summaries.
- Preserved manually maintained quality-page surroundings and the existing
  importance report's ordering and counters.

### 0.3.4 (2026-10-07 04:44 UTC)

Overview: Made scheduled starts configurable, streamlined wheel launching,
and clarified report updates, edit summaries, and documentation.

- Added `run_immediately` to control the first scheduled report pass.
- Skipped new-page updates when all retained dates were complete.
- Added versioned edit summaries with concise ranking and bilingual links.
- Simplified wheel installation and scheduling with ordinary Python entry points.
- Aligned the three READMEs around scheduled tasks and their results.
- Moved usage and development guides into English-only `docs/`.

### 0.3.3 (2026-10-06 06:59 UTC)

Overview: Clarified DYK report totals and paced consecutive PexBot refreshes.

- Separated completed DYK article and active nominee counts in edit summaries.
- Added a 15-second cooldown between live PexBot refresh requests.

### 0.3.2 (2026-10-04 03:22 UTC)

Overview: Clarified DYK nomination outcomes in edit summaries.

- Distinguished new, passed, and failed nominees from historical removals.
- Recognized renamed and repeat nominees without mislabeling delayed cleanup.

### 0.3.1 (2026-10-03 07:25 UTC)

Overview: Corrected Nintendo task-force membership in Pageviews rankings.

- Used Nintendo task-force membership instead of the broader Nintendo project.

### 0.3.0 (2026-10-03 06:54 UTC)

Overview: Added scheduled PAWS reports, resilient daily updates, and
automated wheel releases.

- Added six independent UTC schedules with immediate startup checks and
  reused PAWS authentication.
- Made `run(dry=False)` start monitoring and added `run_once()` for immediate
  reports; replaced `dry_run` with `dry`.
- Added reusable schedulers, graceful shutdown, fresh-process wheel launching,
  and safe cleanup of older wheels.
- Added publication checks, unchanged-page skips, complete previews, and
  concise edit summaries that track article identity across renames.
- Added new-page retention and gap filling, including redirect conversions,
  with metadata and assessment updates.
- Added daily Pageviews catch-up, availability checks, recoverable checkpoints,
  bounded retries, and handling of missing and zero observations.
- Added disposable caches, UTC daily logs, configurable report ranges,
  and scoped PexBot refreshes.
- Preserved English and Chinese quality analysis with wiki-specific extraction,
  read-only replica queries, and shared text processing.
- Added locked scheduling dependencies, offline checks, and automated tagged
  wheel releases.
- Removed character-report APIs and moved `services.quality_prose` helpers
  into shared support modules.

## Until 0.3

### 0.2.0 (2026-08-23 08:49 UTC)

Overview: Replaced deferred Pageviews totals with direct daily-data APIs
and aligned Quarry processing with Polars.

- Replaced deferred Pageviews classes with direct data functions returning
  raw `page/date/pageview` rows.
- Changed Quarry pipelines and query results to eager Polars DataFrames.
- Simplified Pageviews request handling and replaced `ratelimit` with
  runtime support for recursive template extraction.

## Until 0.2

### 0.1.4 (2026-08-22 11:22 UTC)

Overview: Added a PAWS-ready Pageviews adapter and Polars-based analysis.

- Added the Pageviews query pipeline with cumulative totals and explicit
  handling of missing observations.
- Added identifiable, rate-limited requests and multi-year analysis examples.
- Standardized Polars dependencies and Ruff verification; documented
  source adapters, previews, and wheel launching.

### 0.1.3 (2026-07-29 11:11 UTC)

Overview: Improved previews and standardized logging, licensing, and packaging.

- Made empty and unlimited table previews safe.
- Standardized the `aranami` logger and UTC rotation.
- Included CC0 licensing in wheels and excluded development instructions.

### 0.1.2 (2026-07-28 18:11 UTC)

Overview: Added compact table previews and streamlined PAWS wheel updates.

- Added configurable Polars previews through `aranami.support.gtshow`.
- Added UTC daily log rotation with 90 retained archives.
- Allowed wheel updates while an older Aranami package was already imported.

### 0.1.1 (2026-07-28 16:19 UTC)

Overview: Added read-only replica queries and a public PAWS runner.

- Added typed Wiki Replica queries with Polars results and Wikimedia metadata.
- Added `aranami.run(dry_run=...)` for immediate report execution.
- Added UTC daily logs and safe installation of the newest local wheel.

### 0.1.0 (2026-07-27 21:38 UTC)

Overview: Established the Aranami package and PAWS development workflow.

- Established the package layers and PAWS installation and runtime guidance.
- Added locked tooling and versioning conventions.
