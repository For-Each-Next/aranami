# Changelog

This file records completed repository changes, including work that has not
been committed to Git. Entries are grouped by package release line.

## Until 0.3

### 0.2.1a1 (2026-10-03 05:15 UTC)

Overview: Added scheduled PAWS monitoring and immediate selected passes for
Chinese Wikipedia's video-game reports, with safe publication, dry-run
previews, shared logs, and disposable caches. Improved new-page discovery,
Pageviews readiness and recovery, and summaries of dated reports and
membership changes. Preserved manual English and Chinese quality analysis,
consolidated installation, documented usage in three languages, and retired
the character-report workflow.

- **Routine execution:** Connected DYK records and statistics, new-page
  discovery, assessment lists, popularity rankings, English-article
  comparisons, and subscribed PexBot refreshes. Reused PAWS authentication
  and continued independent jobs after failures. Defined six UTC cron
  schedules and default targets in `monitor.py`, using APScheduler 3.x
  with one active instance per task and coalesced missed executions.
- **Public API migration:** Changed `run(dry=False)` to start recurring
  publication and `run(dry=True)` to generate recurring previews. Returned
  the scheduler, reused active monitors, and required shutdown before mode
  changes or cache clearing. Added `run_once(date=..., dry=..., tasks=...)`
  for immediate all-task or selected-task passes with an optional UTC anchor.
  Kept both defaults live. Callers of the former one-shot `run()` must use
  `run_once()`; callers of public, job, and context interfaces must replace
  `dry_run` with `dry`.
- **Publication and previews:** Added structured proposals, concurrent-change
  checks, unchanged-page skips, and one compact Markdown summary per dry run
  with UTC times, run and task statuses, and links to exact UTF-8 `.wikitext`
  companions. Retained completed proposals when another job failed, grouped
  PexBot actions under one task, and suppressed saves and refresh requests
  during previews.
- **Edit summaries:** Centralized factual English summaries under a 255-byte
  UTF-8 limit, preserving complete wiki links and `«...»` article-title
  delimiters. Included report dates, Pageviews leaders and rank gains,
  new-page article/non-article counts and backfilled dates, and current
  totals with grouped membership additions and removals. Ended live and
  preview summaries with measured routine time, such as
  `Executed in 21′53.95″.` or `Executed in 7.12″.`, reserving space for the
  complete suffix.
- **Membership tracking:** Cached stable article IDs in Parquet snapshots to
  recognize renames, grade changes, and DYK promotions. Validated snapshots
  against destination text and advanced them only after successful live
  publication; previews and failed saves preserved prior membership. Kept
  title/Wikidata fallback matching for missing or stale snapshots and removed
  legacy ID comments from real-time report rows. Both English key-article
  reports used English page IDs across renames and English interwiki links
  for additions and removals.
- **PAWS launcher:** Consolidated installation and previews in
  `scripts/run_aranami.py`, replacing the separate installer. Used the active
  interpreter to install an explicit wheel or the newest `aranami-*.whl`
  by modification time, then started a fresh routine process. Stopped on
  installation failure and removed older wheels only after success,
  preserving newer uploads for explicit selections. Saved script output
  beside the launcher and pasted-cell output in the notebook's current
  directory. Replaced `find_wheel()` and `main(argv=None)` with
  `install_wheel(argv=None)` and `run()`.
- **New-page discovery:** Retained 100 completed UTC dates, filled gaps,
  and reused daily records. Combined creations with replica revisions tagged
  `mw-removed-redirect`, deduplicated page IDs, retained keyword and namespace
  filters and assessment icons, and labeled conversions
  ` — 自重定向页改写`. Appended resolved rows with page-ID and earliest-revision
  UTC timestamp comments, enriched retained rows without repeating discovery,
  and preserved metadata across updates. Kept unknown creation dates explicit
  and unresolved identities unchanged.
- **Pageviews readiness and recovery:** Processed at most one missing report
  date per call. Made yesterday eligible from actual invocation time at
  18:00 UTC, allowed older catch-up beforehand, and prevented manual anchors
  from bypassing readiness. Removed the `lag-days` setting. Skipped edits
  and healthy-cache replacement when at least 95% of queried articles lacked
  observations, preserving explicit zeroes. Retried 502/503/504 responses up
  to three times and deferred exhausted requests. Kept completed requests in
  separate pending Parquet checkpoints for resumption, committed them after
  the availability guard passed, and discarded them when it rejected a day.
- **Storage and logs:** Added visible disposable `cache/` storage and
  `run(clear_cache=True)`, atomic Parquet snapshots, and one shared UTC-dated
  routine log with 90 completed daily archives. Cached Pageviews with an
  800-day default history, reused sufficient coverage, fetched missing tails
  and new-article histories, and rebuilt insufficient intervals. Distinguished
  queried empty intervals from unqueried data. Cached DYK dates against actual
  revisions.
- **Configuration and architecture:** Added validated HTML-comment ranges
  and settings, preserved surrounding text and transclusion tags, and
  migrated legacy markers. Used parser-built wikitext and verified template
  contracts. Centralized targets, schedules, and PexBot roots in `monitor.py`
  while keeping Pageviews readiness in its job and manual overrides available.
  Restricted PexBot to Chinese Wikipedia. Kept page reads, report settings,
  and proposals in jobs, with services processing supplied text and explicit
  configuration; separated target-day availability from displayed periods
  and made report group headings configurable.
- **Quality analysis and site semantics:** Preserved manual Polars analysis
  and `analyze_quality_contents()` outputs. Added read-only replica queries,
  explicit-key joins, and batched/preloaded wiki reads. Moved reusable date,
  template, prose, and word-counting mechanics into support, replacing
  `services.quality_prose` with explicit support APIs. Kept workflows and
  verified per-wiki profiles in services and injected projects, grade names,
  contracts, and measurement rules into shared analysis and queries. Used
  Pywikibot namespace normalization, null unknown dates, and verified Article
  history semantics, including English GAR listings and Chinese delistings.
- **Documentation and engineering:** Added English, Traditional Chinese,
  and Simplified Chinese READMEs covering public APIs, manual analysis,
  destinations, UTC schedules, catch-up behavior, icon updates, PexBot
  subscriptions, and output locations. Documented dependencies for date
  parsing, Chinese conversion, and segmentation. Added runtime requirements
  for `APScheduler>=3.11,<4` and `ipykernel>=7.4.0`, established purpose-first
  documentation guidance, and recorded architecture and Polars conventions
  in `CONTRIBUTING.md` and scoped instructions. Removed the imported
  `package/` tree and added offline report, storage, publication, logging,
  and cross-wiki regressions.
- **Retired character tool:** Removed character-report APIs and tests, the
  unused Wikidata franchise reader, character-only project-query fields and
  joins, and multilingual selectors in shared sitelink and label lookups.

### 0.2.0 (2026-08-23 08:49 UTC)

Overview: Replaced deferred Pageviews totals with identified direct daily-data
functions, aligned Quarry pipelines with eager Polars DataFrames, and added
site-aware template lookup and clearer frame previews.

- **Pageviews:** Sent a versioned Aranami User-Agent with the operator's
  Meta-Wiki user page as its contact, made one attempt per sequential call,
  treated HTTP 404 as no observations, propagated other transport failures,
  stopped configuring request timeouts, and removed fixed pacing.
- **Compatibility:** Replaced `DailyView`, `DatePeriod`, `Pageviews`,
  `PageviewFrame`, and deferred `query().pipe().collect()` totals with
  `DailyPageviews`, `fetch_data()`, `fetch_data_dataframe()`, and `massive()`,
  changing cumulative `title`/`start`/`stop`/`views` rows to raw
  `page`/`date`/`pageview` rows.
- **Daily data:** Returned typed eager frames, concatenated iterable page
  occurrences sequentially with a 0.24-second progress refresh interval,
  preserved duplicates, API row order, and explicit zeroes, omitted missing
  observations, and returned typed empty frames without requests.
- **Frame pipelines:** Changed Quarry `pipe()` postprocessors from LazyFrame
  callbacks to DataFrame callbacks and built eager results directly from
  database rows.
- **Dependencies:** Removed the runtime `ratelimit` package and its development
  stubs, and added `mwparserfromhell` as a direct runtime dependency.
- **Template lookup:** Added recursive `get_templates(...)` matching for source
  strings and `Wikicode`, ignored leading spaces and colons through forced
  main-namespace title comparison, treated remaining namespace-like prefixes
  literally, skipped invalid candidate page titles, and preserved source order,
  duplicates, and editable parser objects.
- **Result previews:** Formatted frame-shape source notes with thousands
  separators, the multiplication sign, and singular labels for dimensions of
  one.

## Until 0.2

### 0.1.4 (2026-08-22 11:22 UTC)

Overview: Added a PAWS-ready Wikimedia Pageviews source adapter with a primary
Polars interface, typed diagnostic observations, rate-limited API access, and
documentation aligned with the package's current capabilities.

- **Pageviews:** Added a Quarry-style
  `Pageviews.query(...).pipe(...).collect()` interface for cumulative Polars
  aggregation across multiple titles and half-open periods, including
  Pywikibot site configuration, typed unaggregated observations for debugging,
  explicit null totals when Wikimedia returned no observations, and exposure
  through `aranami.sources` alongside Quarry.
- **PAWS access:** Identified requests with a configurable Aranami User-Agent,
  limited sequential API calls to two per second, and added a live workflow
  that combines Quarry selection with two rolling years of daily views.
- **Dependencies and tooling:** Replaced `polars-u64-idx` with standard
  `polars`, pinned Great Tables 0.23.0, refreshed SQLAlchemy within its existing
  requirement, adopted PyPI `ratelimit` with maintained development stubs, and
  removed stale ty and Pyrefly configuration in favor of Ruff-only project
  verification.
- **Documentation:** Documented the available Quarry, Pageviews, and frame
  preview capabilities, clarified the current bootstrap-only runner, and
  aligned package documentation with the exported source adapters.

### 0.1.3 (2026-07-29 11:11 UTC)

Overview: Improved frame preview edge cases and aligned logging, documentation,
licensing, and wheel contents with Python project conventions.

- **Result previews:** Displayed every row when either edge limit was
  nonpositive and rendered empty frames without error, preserving any available
  column structure.
- **Logging:** Replaced timestamp-named per-run loggers with the stable
  `aranami` hierarchy and documented the rotating UTC file-log lifecycle.
- **Project documentation:** Renamed the release record to `CHANGELOG.md` and
  added the complete CC0 1.0 Universal legal text.
- **Packaging:** Included the license in wheels, omitted development-only
  `AGENTS.md` files, and bounded the build backend to its compatible minor
  release line.

### 0.1.2 (2026-07-28 18:11 UTC)

Overview: Added compact Polars previews, streamlined PAWS wheel updates, and
simplified UTC daily log rotation.

- **Result previews:** Added `aranami.support.gtshow` to display configurable
  leading and trailing Polars rows in JupyterLab, mark omitted records, label
  field types, and report the complete frame shape.
- **Logging:** Replaced UTC date-named active logs with a stable
  `logs/aranami.log` that rotates at UTC midnight and retains 90 archives.
- **PAWS workflow:** Allowed the newest local wheel to install when Aranami was
  already loaded.

### 0.1.1 (2026-07-28 16:19 UTC)

Overview: Added typed Wiki Replica access and a public PAWS runner with durable
logging, plus safer wheel management and focused operational documentation.

- **Quarry queries:** Supported typed, read-only Wiki Replica queries with
  Polars results and reusable postprocessing.
- **Replica coverage:** Supported wiki projects, Pywikibot sites, Wikidata term
  data, and MediaWiki, PageAssessments, and Wikibase table metadata.
- **Runner:** Provided the public `aranami.run(dry_run=...)` entry point for
  one complete invocation.
- **Logging:** Appended each invocation to a UTC daily log under the caller's
  `logs/` directory and retained the latest 90 dates.
- **PAWS and development:** Installed the newest valid local wheel, removed
  older wheels only after successful installation, cleared stale build
  artifacts, and documented PAWS operation and local development.

### 0.1.0 (2026-07-27 21:38 UTC)

Overview: Initialized the Aranami package, documented PAWS workflows, and
established locked development tooling.

- **Package:** Initialized the pure-Python package and its source, service,
  support, job, and runner layers.
- **PAWS:** Documented installation, runtime storage, and dry-run workflows.
- **Development:** Added locked build tooling and established Semantic
  Versioning and PEP 440 release rules.
