# Changelog

This file records completed repository changes, including work that has not
been committed to Git. Entries are grouped by package release line.

## Until 0.3

### 0.2.0.post7 (2026-10-03 02:23 UTC)

Overview: Integrated Chinese Wikipedia's video-game report routines into the
PAWS wheel, with one-call execution, plain wikitext dry-run exports, shared
daily logging, disposable Polars caches, and separate, source-verified English
and Chinese template semantics.

- **Routine execution:** Connected DYK records and statistics, new-page
  discovery, project assessment lists, popularity rankings, English-article
  comparisons, and subscribed PexBot refreshes to `aranami.run()`. Reused PAWS
  authentication, continued independent jobs after failures, and kept
  scheduling outside the package.
- **Publication:** Added structured edit proposals, checks against concurrent
  changes, unchanged-page skips, and one compact Markdown task summary per
  dry run with UTC start/end times, aggregate and per-routine statuses, and
  links to exact UTF-8 `.wikitext` companions. Grouped PexBot refresh
  proposals as one task and removed embedded proposed text. Added a
  `scripts/run_aranami.py` launcher that saves results beside itself, retains
  earlier runs, and preserves completed proposals when another job fails.
  Suppressed wiki saves and delegated PexBot refreshes in dry runs.
- **PAWS scripts:** Added a standalone `scripts/install_aranami.py` installer
  using the active Python interpreter's pip, with an explicit wheel path or
  discovery of a single local wheel. Made pasted notebook cells ignore
  kernel arguments during installation and use the notebook's current
  directory for dry-run storage, while preserving script arguments and
  output placement. Documented installation and dry-run commands.
- **Hourly reports:** Retained the latest 100 completed new-page dates and
  filled gaps. Advanced Pageviews by at most one missing report date per call,
  with a default two-day lag; skipped publication and cache replacement when
  at least 95% of articles lacked observations for that date. Added bounded
  retries for transient 502/503/504 responses and deferred exhausted requests
  without treating them as missing observations or zeroes.
- **Storage and logs:** Appended all routine activity to one UTC-dated log
  file, retained 90 completed daily archives, and added visible disposable
  `cache/` storage with `run(clear_cache=True)`. Cached Pageviews in atomic
  Parquet snapshots, extended sufficient histories, and rebuilt short ones
  with an 800-day default buffer. Preserved completed Pageviews requests in
  separate pending snapshots so deferred runs could resume while retaining
  the healthy cache. Cached DYK dates against actual revisions.
- **Configuration and wikitext:** Added validated HTML-comment ranges and
  settings, preserved surrounding text and transclusion tags, and migrated
  legacy markers. Used parser-built wikitext and verified template contracts;
  moved PexBot's configurable prefix list into wheel configuration and
  restricted its service to Chinese Wikipedia. Moved destination pages,
  existing-page reads, publication proposals, and concrete report settings
  into jobs, including Pageviews periods and task-force selections. Kept
  services as content processors accepting supplied text and explicit
  configuration. Preserved target-day availability checks independently of
  displayed ranking periods and made report group headings configurable.
- **Site semantics:** Separated Chinese reports and English/Chinese extractors,
  used Pywikibot namespace normalization, and represented unknown dates as
  null. Replaced guessed Article history aliases with verified module rules,
  including English GAR listings and Chinese GAR's delisting-only semantics.
- **Manual analysis and sources:** Preserved character reports and English/
  Chinese quality analysis as manual Polars services. Added read-only replica
  queries, explicit-key tag joins, batched/preloaded wiki reads, and documented
  dependencies for date parsing, Chinese conversion, and segmentation.
- **Quality-analysis structure:** Moved reusable date parsing, template-value
  access, prose extraction, and language word counting into support modules.
  Kept quality workflows, promotion-history interpretation, and per-wiki
  profiles in services. Injected per-wiki projects, grade names, template
  contracts, and measurement rules into the shared analysis workflow and
  replica query. Replaced `services.quality_prose` with explicit
  support APIs while preserving `analyze_quality_contents()` and its outputs.
- **Engineering:** Removed the imported `package/` tree and its ignore rule,
  documented architecture and Polars conventions in `CONTRIBUTING.md` and
  scoped instructions, and added offline regressions for reports, storage,
  publication, logging, and cross-wiki extraction.

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
