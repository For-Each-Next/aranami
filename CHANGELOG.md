# Changelog

This file records completed repository changes, including work that has not
been committed to Git. Entries are grouped by package release line.

## Until 0.2

### 0.1.3.post6 (2026-08-22 10:55 UTC)

Overview: Added a PAWS-ready Wikimedia Pageviews source adapter with a primary
Polars interface and typed diagnostic observations.

- **Pageviews:** Added a Quarry-style
  `Pageviews.query(...).pipe(...).collect()` interface for cumulative Polars
  aggregation across multiple titles and half-open periods, including
  Pywikibot site configuration, typed unaggregated observations for debugging,
  and explicit null totals when Wikimedia returned no observations.
- **PAWS access:** Identified requests with a configurable Aranami User-Agent
  and limited sequential API calls to two per second.
- **Live verification:** Added a PAWS script that selects featured video-game
  pages through Quarry, derives both source identifiers from one Pywikibot
  site, collects two rolling years of daily views, and previews the first and
  last 20 records.
- **Source package:** Exposed `aranami.sources.pageviews` alongside the Quarry
  adapter.
- **Dependencies:** Replaced `polars-u64-idx` with standard `polars`, pinned
  Great Tables 0.23.0, and refreshed SQLAlchemy within its existing
  requirement.

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
