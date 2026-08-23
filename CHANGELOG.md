# Changelog

This file records completed repository changes, including work that has not
been committed to Git. Entries are grouped by package release line.

## Until 0.3

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
