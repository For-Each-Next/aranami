# History

This file records completed repository changes, including work that has not
been committed to Git. Entries are grouped by package release line.

## Until 0.2

### 0.1.2 (2026-07-28 18:11 UTC)

`feat(runtime): release version 0.1.2`

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
