# History

This file records completed repository changes, including work that has not
been committed to Git. Entries are grouped by package release line.

## Until 0.2

### 0.1.1.post1 (2026-07-28 16:55 UTC)

`refactor(logging): rotate run logs at UTC midnight`

Overview: Simplified daily run logging while preserving UTC separation and
retention.

- **Logging:** Appended runs to `logs/aranami.log`, rolled the active file at
  UTC midnight, and retained the latest 90 daily archives.

### 0.1.1 (2026-07-28 16:19 UTC)

`feat(runtime): add typed replica access and run logging`

Overview: Added typed Wiki Replica access and a public PAWS runner with durable
logging, plus safer wheel management and focused operational documentation.

- **Quarry queries:** Supported typed, read-only Wiki Replica queries with
  Polars results and reusable postprocessing.
- **Replica coverage:** Supported wiki projects, Pywikibot sites, Wikidata term
  data, and MediaWiki, PageAssessments, and Wikibase table metadata.
- **Runner:** Provided the public `aranami.run(dry_run=...)` entry point for one
  complete invocation.
- **Logging:** Appended each invocation to a UTC daily log under the caller's
  `logs/` directory and retained the latest 90 dates.
- **PAWS and development:** Installed the newest valid local wheel, removed
  older wheels only after successful installation, cleared stale build
  artifacts, and documented PAWS operation and local development.

### 0.1.0 (2026-07-27 21:38 UTC)

`chore: initialize aranami 0.1.0`

- **Package:** Initialized the pure-Python package and its source, service,
  support, job, and runner layers.
- **PAWS:** Documented installation, runtime storage, and dry-run workflows.
- **Development:** Added locked build tooling and established Semantic
  Versioning and PEP 440 release rules.
