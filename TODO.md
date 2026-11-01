# Aranami v0.8.0 refactor and release TODO

This is an implementation plan based on a read-only source audit of `031bc72`
(`0.7.13`) on 2026-11-01 UTC. It identifies where to change the project, how to
simplify it, and what must demonstrate completion. Creating this plan does not
implement the refactor, bump the version, or authorize a deployment by itself.

The audit covered source ownership, collection and persistence paths,
newsletter organization, scheduling, deployment scripts, tests, configuration,
and documentation. Tests and live jobs were not run. Toolforge files, running
services, retained data formats, and production request counts were not
inspected. Findings below distinguish observed code from work needing those
measurements. An unchecked item is future work, not a confirmed defect.

## Execution evidence (2026-11-01 UTC)

Revalidation started from `abc5bf8` (`0.7.14`), not the older audited
`031bc72`. The new view/edit controls were preserved. Production initially
selected `app-staging-e70f17472d2848cd94cbc6fde5c863dc` (`0.7.13`), with
manifest digest `f42dfe19ef3d0d767b341e66284cfbb5dc6f8677125fa91d1fbb0b8fea8bab48`.
Its own validator confirmed its matching Python 3.15 environment and receipt;
health advertised drain protocol 1. Exactly one hub and one web pod ran,
with no manual/setup jobs. This generation is the retained recovery baseline.

Implementation results T01–T23 refer to refactor commit `ca419d7`. Final cleanup removed unused observation enrichment/seed options and their two
obsolete tests; locks and timestamp ordering remain. Dashboard newsletter facts
were removed (18,614 → 3,357 fixture bytes); private refresh/retry checkpoints
remain disposable and never serve public requests.

| Tasks | Completed outcome and evidence |
|---|---|
| T01 | Baseline: 901 unit tests / 90.824 s; 14 integration / 0.238 s; 508 website / 12.744 s with two local-socket sandbox failures. Post-refactor website suite passed with loopback permission. Measurements below use fixed inputs, not production throughput estimates. |
| T02 | Configuration/architecture separate triggers, cutoffs, actual captures and report dates. Snapshot tests cover concurrent first capture, delayed 02:13 capture after midnight, completed-day/year labels, restarts and preview isolation; log publication remains independent. |
| T03 | Inspected canonical report roots, all newsletter envelope kinds, article schemas, source/environment generations, jobs and process paths. Current observations: 36 inline envelopes; quarterly archives: 3 schema-2 dates; annual archives: 2 schema-2 dates; 3 catalogs. History: 11,328 canonical rows / 222,004 bytes; milestones: 2,427 / 242,668. Legacy originals exist only in preserved migration backups. |
| T04 | Newsletter preparation moved into `backend.newsletter`: schema, collection, observations, archive, homepage, newpages, layout, subpages, statistics, annual and refresh. Tiny constants merged into observations. Direct callers, tests, docs and reviewed payload changed together; no aliases. |
| T05 | Article stores/acquisition moved to `backend.articles`; updater summary flattened to `updater.edit_summary`; wiki reader/revision cache moved to `shared.sources`. Newsletter acquisition moved out of the general website collector. Existing runtime helpers, HTTP orchestration and four top-level ownership packages retained: further topology changes offered no measured benefit. |
| T06 | `shared.config` owns report/collection schedules, cutoffs and website freshness. Hub binds callbacks; `/api/config` and schedule state expose independent collections and actual interval overrides. |
| T07 | Existing source-cycle membership and top-list ownership extended without global cross-job caching. Shared daily assessment lock gives two consumers one capture. Distinct independently timed current-state jobs retain separate observations. Shared membership failure logs once and retains seven independent retry checkpoints. |
| T08 | One typed complete top-list decoder supplies eager title/source-rank/views frames. Tests in both consumer orders preserve report readiness, dashboard lookback, verified exclusions, measured empty lists and null ranks. |
| T09 | Four custom HTTP clients share `shared.identity`; Pywikibot uses its documented description setting and retains framework identity. Mock requests and actual framework header construction verified; imports stay inert. |
| T10 | Native database replacement rejected with evidence: locked Polars 1.44.2 / SQLAlchemy 2.1.2 imports asyncio support requiring absent `greenlet` before execution. Adding an unrelated dependency to replace the small working adapter is not simpler. Bound/empty/bytes/null/query-count/closure tests cover retained eager adapter behavior. No database writes introduced. |
| T11 | Authoritative milestone merge reads once under lock and skips identical writes; new observation/progress facts still persist per lookup. Preserved aliases, source ordering, tombstones, concurrency and unsafe-path checks before lock creation. |
| T12 | Public newsletter routes select canonical current facts or requested immutable archives. Removed dashboard fallback, request enrichment and implicit dashboard-to-archive seeds. Missing/corrupt/date/annual results and zero-source/read-only HTTP paths covered. |
| T13 | Shared deterministic JSON encoding and atomic writer flush file and parent directory. Tests cover interruption, replacement/fsync failures, concurrency, immutable conflicts and corruption. Last complete bytes remain readable. |
| T14 | Active data already canonical; no invented conversion needed. Normal newsletter/article/report reads no longer consult legacy formats or implicitly migrate. Explicit backup import/conversion commands retained because originals actually exist and remain recovery evidence. Optional historical facts remain unknown when evidence was never captured. |
| T15 | Removed real-directory promotion, idle override and exact historical-version exceptions. Each immutable predecessor validates its own contract/receipt; shell tests cover current upgrade/recovery mechanics. Actual 0.7.13 compatibility requires observed production drain, recorded separately below. |
| T16 | Concrete `WikiReader` contract moved to source boundary; removed capability probes, SimpleNamespace adapters and fake revision interfaces. Retained one documented batch API operation for conversion/redirect/page-ID evidence; high-level revisions/getOldVersion remain supported by pinned Pywikibot 11.8.0. |
| T17 | Removed repeated archive validation, reader probes, Pageviews cache eviction/reparse catch, new-page revision getattr chains and partial shared-membership dispatch. Retained traced output isolation, external failures/rate limits, corruption, publication conflicts, cancellation and best-effort diagnostic persistence. Missing evidence stays distinct from zero. |
| T18 | Removed obsolete imports, wrappers, enrichment helper, compatibility options and normal readers with callers/fixtures. Retained historical HOT parsing and explicit backup converters because retained evidence needs them. Source import/boundary and exact manifest checks cover executable paths. |
| T19 | Required TOML is version authority; removed tracked/generated version JSON fallback. Shared bootstrap JSON policy owns packages, suffixes, scripts and metadata requirements. Both trust boundaries reject inconsistent locks, missing OAuth, unsafe/private/extra/corrupt payloads. |
| T20 | Schema owns statistic keys and literal escaping has one owner. Distinct link wrappers retained deliberately for colon/label/apostrophe behavior; localized/source-specific grade order/colors retain their meanings. |
| T21 | Shared scoped/all milestone inventory uses one read with native page-ID joins. Typed Pageviews filtering/ranking stays native. Existing annual/statistics grouping already uses Polars; parser/request loops and small semantic mappings retained instead of cosmetic rewrites. |
| T22 | Every locked dependency has a runtime/test consumer, including dynamic `mysql+pymysql`, OAuth and `httpx2`; none removed speculatively. Python 3.15 and source-only deployment preserved. |
| T23 | Moved 21 newsletter suites plus fixture helper into discoverable `tests/toolforge/newsletter`; two deployment suites into `tests/unit/deployment`. Removed seven obsolete enrichment tests and two obsolete deployment compatibility tests; added behavioral regressions. CI runs Node and Chromium gates with artifacts. |

Representative fixture counts (baseline source `abc5bf8`; rerun the source-cycle,
milestone, archive, section-cache, snapshot and public suites to reproduce):

| Equivalent work | Before | After |
|---|---|---|
| Shared top-pageviews report + dashboard | 1 fetch, 2 parses | 1 fetch, 1 parse |
| Unchanged/empty-overlap milestone merge (4 retained rows) | 2 reads, 1 write | 1 read, 0 writes |
| Scoped newsletter + full annual milestone inventory | 2 reads | 1 read |
| Two-issue archive save | 3 full schema validations | 1 validation |
| Unchanged website pass / cached restart | 0 upstream work | 0 upstream work |
| Read-only public requests | 0 upstream calls/writes | 0 upstream calls/writes |
| Concurrent completed-day assessment consumers | 1 shared capture | 1 shared capture |

Measurements are operation counts, not general latency claims. The native-query
experiment measured the existing 100,000-row path at 0.200 s / 29,948,533 bytes
Python peak allocation; native execution failed before querying, so no speed or
memory comparison is claimed. No permanent benchmark pipeline was introduced.

Browser verification: Chromium 155.0.8059.84 passed 336 page cases and 33 cases
without JavaScript across 14 widths, 3 locales and 8 surfaces; six desktop/narrow
screenshots were visually inspected. All 43 Node behavior tests passed. These
are synthetic offline results, not production scheduling observations.

Local inventory preserved `.venv`, ignored runtime facts/previews, IDE files and
all three unrelated worktrees. Server cleanup must preserve durable facts,
migration originals, credentials, active generations/processes and recovery.
An attempted report export was rejected by automatic approval review; all
subsequent production data inspection was performed in place.

### Validated release candidate

- Final offline suites: **910 unit** (82.829 s), **16 integration** (0.219 s),
  **500 website** (6.429 s), all passing. Baseline discovery was 901/14/508;
  two deployment compatibility tests and nine obsolete newsletter enrichment/
  seed tests were removed with their implementations. Added boundary, source,
  identity, atomic-storage, unsafe-path, scheduling and canonical-data cases.
- Lockfile, Ruff no-fix, formatting (288 Python files), all shell syntax,
  Markdown file targets, source-upload and extracted-payload verification passed.
  The initial local-socket errors disappeared with loopback permission.
- Formal source manifest SHA-256:
  `ff0c05f9e3623aa3995ab9ed46b47c474093d2fa645b9c902ab57c395ce885d7`.
- Dev1 selected `app-staging-3d4b4b859f9f4d858649c2c92847d58f`, digest
  `12a57ea0b86a3c1d3a2105a6de4c87f0e9efd66cfca7f3782e18e03690143f4e`.
  Setup receipt, real predecessor drain acknowledgement, web availability,
  old-hub termination and single replacement publisher were observed.
- In-place dev1 reads validated current facts, all 35 historical captures,
  all three quarterly dates and both annual dates. Seven startup routines
  completed `no_update`; the 08:31 assessment-list trigger also completed.
  Next observed dates included newsletter/assessment/comparisons at November 2
  00:00, New pages 00:07, and Pageviews November 1 18:59 (all UTC).
- A schema diagnostic generated eight bytecode files in the recovery source.
  Promotion correctly rejected unexpected files before setup or service changes.
  Only the eight inventoried bytecode files were removed; original source hashes
  and its matching receipt were revalidated before a successful retry. Later
  diagnostics disabled bytecode writes.
- Remote ancestry was refreshed and reviewed: nineteen prior Aranami release/
  refinement commits were already local ancestors, with no unrelated remote
  changes. Publishing the new tag necessarily retains those ancestors.

Formal tag publication, formal promotion, exact cleanup results and the next
production boundary are recorded separately after observation; dev1 success
alone does not complete those gates.

### Formal deployment and selective cleanup

- Release commit `ea67331` was merged into `trunk`; immutable `v0.8.0` was
  pushed. Every tagged runtime file was compared with its payload digest.
- Formal generation: `app-staging-77d387c62c814ce58d8ef630ab347cb7`, manifest
  `ff0c05f9e3623aa3995ab9ed46b47c474093d2fa645b9c902ab57c395ce885d7`.
  Setup passed, drain was acknowledged, the web became available, and the old
  hub terminated before one replacement started. Both process PYTHONPATH values,
  version and receipt matched. Health had no error; seven public page/locale
  requests returned HTTP 200. Current facts, 35 historical captures, three
  quarterly dates and two annual dates validated in place after promotion.
- Removed **108 exact inventoried paths**: 52 obsolete source generations,
  53 unused environments, `app.previous`, `active-deployment.json` and
  `pyvenv-3.15`. Inventory SHA-256:
  `1242a53cf00499d74f386b87543820af995bf8737916edf86fcbf403c89b91ae`.
  The durable receipt is `deployment-backups/cleanup-v0.8.0.json`.
  Preserved formal, dev1 and 0.7.13 source/environment/receipt sets; all
  transaction receipts, legacy migration source originals, runtime data and
  credentials. No obsolete running service or manual job needed removal.
  Locally removed only the inventoried obsolete updater/support bytecode folder;
  unrelated worktrees and ignored runtime artifacts were preserved.
- Tag-triggered GitHub validation passed Python/Node checks but failed six
  hover assertions because headless Linux Chrome exposed no hover device.
  Reproducing that input state locally produced the same six failures. The
  checker now explicitly selects/asserts desktop mouse input (the official
  Playwright Chromium launcher uses the same settings), without weakening
  checks or changing runtime files. All 336 page and 33 no-JavaScript cases
  then passed in the reproduced environment. The published tag is unchanged;
  corrected trunk CI and GitHub source-artifact publication are verified below.
- **Pending production observation:** November 2 00:00 UTC assessment/newsletter
  boundary and subsequent 00:07 New pages; Pageviews November 1 18:59 UTC.
  Controlled tests and saved next-run times are not those future executions.

### Published release receipt

- [GitHub release v0.8.0](https://github.com/For-Each-Next/aranami/releases/tag/v0.8.0)
  was published after [corrected CI](https://github.com/For-Each-Next/aranami/actions/runs/41290234667)
  passed at `2f9d804`. The immutable tag remains `ea67331`; the CI portability
  fix changes only the checker, not any runtime payload file.
- The CI source archive matched the original formal archive byte for byte.
  Published `aranami-source.tar` (2,631,680 bytes) was downloaded and verified:
  SHA-256 `9c58ba3d1be32e7e5c372badfaf1e9a4632fc8502f9b251eea7f4a056b12624f`.
  Its manifest digest remains `ff0c05f9e3623aa3995ab9ed46b47c474093d2fa645b9c902ab57c395ce885d7`.
  Release notes came from the tagged changelog. The initial tag workflow's
  headless-input failure remains historical; publication used the verified
  successful CI artifact without moving the tag or changing deployed source.
- Post-cleanup validation again confirmed all three retained generations and
  environments, exactly one healthy hub plus one web pod, unchanged routine
  outcomes, and public version `0.8.0`. Sixty setup receipts remain preserved.
- Commit evidence: implementation `ca419d7`, final cleanup/release `ea67331`,
  browser portability and deployment/cleanup record `2f9d804`; this receipt
  is a documentation-only follow-up. T27 and T28 are complete. T29's cleanup,
  restart, archive and current-schedule checks are complete; its future daily
  production observations remain explicitly pending above.
- Concurrent comma-formatting work began after the release and was preserved
  outside its tag/payload. This receipt was prepared in an isolated worktree
  and published without that unrelated commit.

## Direction and existing strengths

Keep the useful ownership model: backend prepares and persists facts; updater
projects them into wikitext and publishes intentional edits; webservice renders
saved facts; shared supplies read-only adapters and small infrastructure.
Restructure within and across these boundaries when it removes real coupling.

The project already has source-cycle reuse, shared assessment snapshots,
revision caches, durable report bundles, immutable newsletter observations,
independent output status, UTC schedules, and staged source deployments. Extend
or simplify these implementations instead of adding parallel infrastructure.

Wiki Replicas are **read-only**. “Database consolidation” here means fewer
replica queries and fewer duplicate local Parquet/JSON reads and writes. A new
writable application database is not an established requirement.

Use UTC internally. The web UI already omits repeated UTC labels through
`webservice.presentation.utc_time`; preserve this behavior. Distinguish an
exact midnight **trigger/boundary** from the time a source query completes.
An ordinary delayed replica query does not reconstruct exact historical state.

## Execution rules and order

- Work from a new task branch based on `trunk`; preserve unrelated changes and
  existing ahead-of-remote commits. Follow root and scoped `AGENTS.md` files.
- Resolve P0 contracts first. Make structural moves separately from behavior
  changes where practical, then complete P1 consolidation/removal, P2 cleanup,
  and the release gates. Independent items may proceed in parallel.
- Each completed item needs a short result, relevant verification, and commit
  reference here. Mark a rejected candidate as “not needed” with evidence;
  mark an external dependency as blocked with its concrete prerequisite.
- Keep aggressive removal of redundant checks and obsolete paths a primary
  objective. Remove branches together with their callers, configuration,
  fixtures, tests, and documentation; leave no forwarding compatibility layer.
- Retain handling of genuine network failures, rate limits, corrupt storage,
  missing source evidence, publication conflicts, and process interruption.
  Validate at external/persistence boundaries, then trust the accepted contract.
- Measure reduced queries, parses, writes, and complexity. Fewer lines or files
  alone is not proof of improvement. Do not create a new framework to remove a
  few direct calls, and do not delete a test solely to make a refactor pass.
- Source moves do not require runtime-data moves. Change stored layouts only
  for a demonstrated benefit, with preservation and verification of history.
- During future implementation, use one `0.8.0.devN` sequence for test
  deployments, then formal `0.8.0`. This planning change leaves `0.7.13` intact.

## P0 — Contracts and baseline

### T01 — Establish a behavioral and efficiency baseline

- [x] **Where:** `src/backend/hub.py`, `monitor.py`, `runner.py`,
  `website/collect.py`, `shared/sources/cycle.py`, and the existing test suites.
  **What/how:** trace each producer, consumer, source identity, persisted path,
  freshness rule, and writer. Run the current offline suites before moving
  code. Count source calls, parses, replica queries, file reads/writes, test
  discovery, and representative durations with fixture-driven instrumentation.
  Cover simultaneous midnight jobs, an hourly pass, an unchanged pass, a
  restart, and a public request; record existing failures separately.
  **Done when:** there is a reproducible before/after comparison and an explicit
  owner for each shared dataset, without introducing permanent telemetry solely
  for this refactor.

### T02 — Define exact boundary versus flexible collection semantics

- [x] **Where:** `shared/config.py`, `backend/monitor.py`, `backend/hub.py`,
  `backend/services/project_assessments.py::get_snapshot`, and
  `docs/architecture.md` scheduling/report contracts.
  **Evidence:** report cron definitions and hub-only newsletter/history jobs
  live separately; assessment files retain actual collection times separately
  from `scheduled_for`. Startup and delayed execution can occur after midnight.
  **What/how:** record the intended schedule and data cutoff for every job using
  the table below. Preserve the first complete daily assessment observation and
  share its capture lock with English comparisons. Define late/missed-run
  behavior explicitly; never label a late query as exact midnight evidence.
  If exact as-of state is required beyond the existing observation contract,
  establish a supported historical source before promising that behavior.
  **Done when:** clock-controlled tests cover midnight, delay, restart, concurrent
  capture, report-date labels, publication independence, and month/year changes.

| Work | Current schedule / freshness | Refactor requirement |
|---|---|---|
| Assessment log + English comparisons | `00:00:00` UTC | One retained completed-day assessment capture; comparisons depend on it, not on log publication. |
| Newsletter daily capture | `00:00` UTC | Preserve completed-day cutoff, first complete capture, quarterly rollover, and independent annual stream. |
| Article history/milestones | Hourly `:00` | Reusable enrichment; may share compatible reads without delaying boundary captures. |
| DYK | Hourly `:00` | Fresh report facts with independent publication status. |
| Assessment lists | Hourly `:31` | Preserve report semantics; only reschedule after demonstrating compatible freshness. |
| New pages | Daily `00:07` | Completed dates through yesterday and durable completion checks. |
| Pageviews | `:59`, normally `18:59` after readiness | Preserve the 18:00 eligibility boundary, readiness evidence, catch-up, and hourly retry. |
| PexBot | Daily `00:00` | Delegated publication; preserve already-edited-today checks and unknown edit counts. |
| Website reports | Reviews hourly; others normally 24 hours, with newsletter daily and issue/configuration invalidation | Select due work before creating clients; failures keep good data and retry independently. |

### T03 — Inventory retained data and deployment dependencies

- [x] **Where:** runtime `reports/`, `state/`, `cache/`, `dry-run/`, migration
  receipts/backups, source generations, environments, and Toolforge jobs.
  **What/how:** during authorized execution, inspect the server read-only and
  record exact paths, schemas, active/manual readers, retention purpose, and
  migration requirements. Separately inventory ignored local artifacts and
  active worktrees. Identify one verified recovery generation with its matching
  environment and receipt. Server paths mentioned in docs are candidates, not
  proof that obsolete files currently exist.
  **Done when:** every proposed deletion or legacy-reader removal has evidence
  that its inputs are unused or preserved through a verified conversion.
  **Dependency:** required before T14, T15, T24, and production cleanup.

## P1 — Coherent directories and ownership

### T04 — Group the substantial newsletter backend domain

- [x] **Where:** ten `backend/website/newsletter*.py` modules and
  `backend/support/newsletter_{data,observations}.py`.
  **Evidence:** collection, archives, validation, annual analysis, and source
  layouts are spread across website/support; the constants module is seven
  lines. Newsletter functionality is much larger than a small website helper.
  **What/how:** create `src/backend/newsletter/` using the mapping below. Merge
  the tiny constants module into the owning observation/schema module. Expose
  direct APIs, update every import, scoped instruction, test, fixture reference,
  source manifest, and documentation link; remove the previous modules.
  **Done when:** no old imports or forwarding aliases remain, all tests are
  discovered, and the source archive contains the complete new package.

| Existing path under `src/backend/` | Proposed path |
|---|---|
| `website/newsletter.py` | `newsletter/schema.py` with the saved-facts reader placed with observations if that simplifies dependencies |
| `website/newsletter_sources.py` | `newsletter/collection.py` |
| `website/newsletter_archive.py` | `newsletter/archive.py` |
| `support/newsletter_observations.py` + `support/newsletter_data.py` | `newsletter/observations.py` |
| `website/newsletter_homepage.py` | `newsletter/homepage.py` |
| `website/newsletter_newpages.py` | `newsletter/newpages.py` |
| `website/newsletter_layout.py` | `newsletter/layout.py` |
| `website/newsletter_subpage_layout.py` | `newsletter/subpages.py` |
| `website/newsletter_statistics.py` | `newsletter/statistics.py` |
| `website/newsletter_year_review.py` | `newsletter/annual.py` |

Keep the shared issue/calendar contract dependency-safe, wiki projection in
updater, and HTML/assets in webservice. Grouping all newsletter code into one
cross-layer package would recreate coupling. Its two updater files and two
CSS/JS files do not need extra folders merely for symmetry.

### T05 — Reorganize other real domains and flatten artificial layers

- [x] **Where:** `backend/support/article_*.py`,
  `backend/website/article_*.py`, `history_archive.py`, the status/execution
  modules, `backend/public.py`, `webservice/pages.py`, and
  `updater/edit_summary.py`.
  **What/how:** map imports and group cohesive article evidence and runtime
  state responsibilities, for example `backend/articles/` and
  `backend/runtime/`, only where this reduces cross-directory coupling. Move
  newsletter orchestration out of the large general website collector. Consider
  flattening `updater/support/`, which currently contains only `__init__.py`
  and `edit_summary.py`, into `updater/edit_summary.py`. Extract substantial HTTP
  or presentation responsibilities without creating one module per handler.
  Review top-level packages, scripts, assets, configuration, and tests together;
  adopt a single `aranami` namespace only if the import/deployment simplification
  outweighs the migration. The existing four ownership boundaries remain useful.
  **Done when:** a final old-to-new map explains ownership, cycles are absent,
  and old paths are removed. Avoid one/two-file folders created solely as
  layers, while retaining genuinely useful resource and ownership boundaries.

## P1 — Shared collection, processing, and storage

### T06 — Make schedule metadata complete and authoritative

- [x] **Where:** `shared/config.py::TASK_DEFINITIONS`,
  `backend/hub.py::run`, `backend/monitor.py`, and `/api/config` producers.
  **Evidence:** hub-created `newsletter-history`, `newsletter-daily`, and
  website refresh scheduling are outside the report-definition registry.
  **What/how:** centralize declarative schedule/freshness metadata, keeping
  destination publication settings distinct from dataset-refresh settings.
  Let runtime code bind callbacks; avoid a generic workflow engine. Ensure
  configuration identity includes settings that affect each consumer's data.
  **Done when:** configured and observed schedules agree across the hub,
  scheduler status, website, README, and restart behavior; T02 remains true.

### T07 — Extend source reuse only across equivalent observations

- [x] **Where:** `shared/sources/cycle.py`, `backend/hub.py`,
  `backend/server.py`, `backend/website/collect.py`, and report dispatch.
  **Evidence:** the ContextVar cycle shares membership and top-list payloads
  only in one sequential pass; independent scheduler jobs have separate cycles.
  **What/how:** use T01 to locate repeated requests. Give each shared dataset one
  backend collection owner and pass prepared results to due consumers, or reuse
  a durable observation where its freshness permits. Keep keys explicit: wiki,
  project, time/cutoff, query parameters, and interpretation version. Prefer
  extending current ownership/locking to adding a global cache or broker.
  **Done when:** equivalent work within the chosen sharing scope has one
  collection, while distinct freshness requirements remain distinct. Concurrent
  callers cannot reuse partial/failed observations; preview and live state stay
  isolated, and one output failure does not invalidate another output.

### T08 — Consolidate top-pageviews decoding and ranking inputs

- [x] **Where:** `shared/sources/pageviews.py`,
  `backend/website/pageviews.py::_daily_articles`, and
  `shared/sources/cycle.py::read_top_pageviews`.
  **Evidence:** report and website paths independently decode/validate the top
  payload. The cycle shares raw JSON, then deep-copies and reparses it.
  **What/how:** provide one typed complete top-list source result with its date,
  source rank, title, and view count. Share validation and normalization, then
  let consumers apply their selection/presentation policies. Retain daily/monthly
  selectors, verified article exclusions, null site ranks, the limiter, and the
  separate report readiness/history contract. The dashboard's bounded lookback
  must not become a report fallback.
  **Done when:** one fetch and parse serves identical observations; report and
  dashboard fixture results agree on common rows without changing retry policy.

### T09 — Centralize application identity and User-Agent configuration

- [x] **Where:** `shared/sources/pageviews.py::_USER_AGENT`,
  `shared/sources/summaries.py`, `shared/sources/wikiwho.py`,
  `updater/jobs/pexbot.py::_refresh`, and Pywikibot runtime configuration.
  **Evidence:** four adapters build Aranami headers with two contact URLs.
  **What/how:** define one version/contact identity in the existing shared
  configuration/deployment area and use it in custom HTTP clients. Integrate
  Pywikibot identity through its documented configuration, preserving its
  framework identification. Keep endpoint-specific timeouts and retry rules
  where required; a generic HTTP wrapper is not the goal.
  **Done when:** mocked outgoing requests carry consistent identity, no duplicate
  header strings remain, and imports do not initialize network clients.

### T10 — Use native query-to-Polars capabilities where they simplify code

- [x] **Where:** `shared/sources/quarry/query.py::QueryFrame.collect`, `_fetch`,
  `Replica`, and replica-adapter callers.
  **Evidence:** `_fetch` manually collects all rows into Python tuples before
  constructing a frame; `QueryFrame` is a small custom deferred query wrapper.
  **What/how:** evaluate `polars.read_database` with SQLAlchemy selectable
  objects, bound parameters, and schema overrides using the locked libraries.
  Remove manual materialization or the wrapper only when native behavior is
  simpler and preserves supported timing/contracts. Measure engine creation
  and connection reuse before changing pool ownership. Keep SQLAlchemy Core
  read-only queries and eager source-adapter results.
  **Done when:** typed empty results, bytes/title decoding, nulls, parameter
  binding, query execution count, connection closure, and memory behavior are
  verified. No raw SQL, ORM persistence, or new writable database is introduced.

### T11 — Reduce repeated durable reads and unchanged rewrites

- [x] **Where:** `backend/support/article_milestones.py::merge_save`,
  `backend/website/article_milestones.py::enrich_milestones`, related history
  stores, and newsletter persistence.
  **Evidence:** `merge_save` loads the same file before taking the lock and
  again under the lock, then rewrites a complete Parquet dataset. Enrichment
  can invoke it repeatedly within one pass.
  **What/how:** perform authoritative reads and merges once under the owning
  lock. Skip writes only when facts and required observation/progress metadata
  are unchanged. Measure batching opportunities without losing the existing
  per-lookup recovery checkpoint. Preserve tombstones and alias conflict checks.
  **Done when:** unchanged/overlapping passes have fewer reads and writes while
  interruption, concurrency, rejoining scope, and enrichment recovery remain
  correct; every retained fact and source timestamp is preserved.

### T12 — Give public newsletter requests one canonical data path

- [x] **Where:** `backend/public.py::_newsletter_saved_facts`,
  `_newsletter_page`, `newsletter_homepage.enrich_newsletter_homepage`, and
  `website/collect.py::_dashboard_snapshot`.
  **Evidence:** routes can use durable observations, dated archives, or older
  dashboard-embedded newsletter facts with request-time enrichment.
  **What/how:** after T03/T14 migration, read current observations or the
  requested immutable daily archive directly. Remove dashboard facts fallback
  and request-time enrichment; retain only availability/status metadata in the
  dashboard where sufficient. Current observations and historical archives have
  different purposes and must not be collapsed by overwriting old dates.
  **Done when:** latest/dated/annual views, missing acquisition, unavailable
  dates, and corrupt data have explicit results; every HTTP path remains
  read-only and makes zero upstream collection requests.

### T13 — Consolidate duplicated persistence primitives

- [x] **Where:** `newsletter_observations.py::_encoded`/`_write`,
  `newsletter_archive.py::_encoded`/`_atomic_json`,
  `shared/support/files.py`, and other owning stores.
  **Evidence:** JSON encoding, hashing, bounds, temporary writes, and locking
  recur; parent-directory synchronization differs between the two writers.
  **What/how:** use an existing suitable primitive or directly consolidate the
  duplicated implementation. Keep domain schemas, retention, selectors, and
  conflict rules with their owner. Decide durability guarantees explicitly;
  do not replace them with a generic repository layer.
  **Done when:** interruption before/after selection, disk errors, concurrent
  writers, conflicting immutable captures, and corrupt digests preserve the
  last complete data and fail visibly.

## P1 — Legacy and defensive-code elimination

### T14 — Convert retained legacy formats before deleting their readers

- [x] **Where:** `newsletter_observations.load_observation`,
  `newsletter_archive._date_directory`/`_read_record`/`_read_feature`/
  `needs_conversion`/`migrate_from`, newsletter validators, and
  `backend/support/article_milestones.py::load`,
  `article_history.py::read_versions`, `article_data.py::migrate_legacy`,
  `project_assessments.py::migrate_snapshots`, and
  `shared/reports.py::ReportStore.migrate_legacy`.
  **Evidence:** legacy digest selectors, ISO date folders, schema-1 rendered
  newsletters, pre-0.7.11 fields/spelling, and a milestone `_LEGACY_SCHEMA`
  remain readable in normal execution.
  **What/how:** use T03 to determine which formats exist. Preserve originals,
  convert supported facts once, compare catalogs/identities/timestamps and
  digests, then remove obsolete normal-reader branches and callers. If an old
  rendered record lacks facts required by the new schema, preserve its readable
  historical output through an explicit archive contract; do not invent facts
  or silently discard the date. Retire conversion commands only after migration
  and recovery requirements are satisfied.
  **Done when:** all retained quarterly/annual observations and article evidence
  remain accessible, normal reads use the canonical schema, and no request or
  startup performs an implicit migration.

### T15 — Retire deployment compatibility against verified predecessors

- [x] **Where:** `scripts/toolforge/deploy-source.sh`, `drain-hub.py`,
  `validate-source.py::_expected_sources`, administration docs, and deployment
  fixtures.
  **Evidence:** real-directory `app/` migration,
  `ARANAMI_LEGACY_IDLE_CONFIRMED`, and exact `0.7.10`/`.postN` drain exceptions
  remain in current scripts.
  **What/how:** once T03 proves active and required recovery generations support
  the current protocol, delete these branches, overrides, old-layout fixtures,
  and instructions together. Test upgrade from the actual supported predecessor.
  **Done when:** current promotion/recovery works without historical version
  exceptions; locks, immutable generations, pinned environments, process-specific
  drain acknowledgements, and single-publisher termination remain enforced.

### T16 — Replace optional capability probing with an explicit reader contract

- [x] **Where:** `backend/website/collect.py::WikiReader`,
  `shared/sources/wiki.py`, `newsletter_sources._collect_issue`,
  `newsletter_homepage._HomepageEvidence.at`, `collect_newsletter_homepage`,
  and `articles.archive.DailyHistoryArchive._snapshot`.
  **Evidence:** production paths accept `read: object`, probe `sources`/`at`
  with `getattr`/`callable`, and construct a `SimpleNamespace` adapter.
  `WikiReader` also combines raw requests, title resolution, history, caching,
  and provenance inside the website collector.
  **What/how:** move low-level read-only wiki access to the source boundary;
  retain per-pass evidence ownership in the backend. Check the pinned
  Pywikibot's high-level capabilities before replacing direct `simple_request`
  calls; retain one documented narrow adapter for genuine library gaps.
  Inventory callers, use one existing concrete reader or a typing
  protocol describing real requirements, and call its methods directly. Update
  test doubles to the real contract. Remove unsupported capability fallback
  branches rather than replacing them with wrappers. Missing wiki evidence
  remains a valid domain result, separate from a missing reader method.
  **Done when:** direct calls cover source cutoff, exact revisions, provenance,
  missing/hidden pages, Chinese title conversion, redirects, batch resolution,
  and annual/monthly history correctly.

### T17 — Remove repeated internal validation and broad recovery branches

- [x] **Where:** newsletter schema/collection/archive/renderers,
  `backend/support/*status.py`, `shared/reports.py`, source adapters, report
  jobs, and deployment/bootstrap code.
  **What/how:** trace boundary validation through each call chain. Replace
  repeated `.get` defaults, impossible null/type branches, and capability checks
  on already-validated internal objects with direct access. Review every broad
  `except Exception`, silent suppression, fallback, and schema branch by the
  actual contract. Keep catches only where they implement a named external,
  integrity, or operational outcome; programming errors must remain diagnosable.
  For example, the catch in `source_cycle.read_top_pageviews` evicts a cached
  payload and re-raises: assess it with T08, not as silent suppression.
  Audit nested `getattr` on preloaded page revisions in `updater/jobs/new_pages`
  against Pywikibot's actual contract. In `collect_dashboard`, resolve shared
  membership failure once rather than passing partially initialized state to
  each consumer; preserve independent output isolation and diagnostic tracebacks.
  **Done when:** a concise removal/retention record names each changed branch,
  its former purpose, and the test proving the new behavior. Independently
  loaded JSON is still validated; unknown dates/counts and unavailable source
  data still differ from empty/zero. No new fallback layer replaces deletions.

### T18 — Prove remaining legacy/dead-code removals across the repository

- [x] **Where:** all source packages, scripts, CLI options, environment keys,
  configuration, assets, fixtures, dependencies, and documentation.
  **What/how:** combine references, actual imports, dynamic callback resolution,
  manifests, CLI/service entry points, and T03 runtime evidence. Remove verified
  dead code and unused options together with their dependents. Historical HOT
  wikitext parsing remains needed for retained monthly observations unless an
  equivalent source replaces it; a “legacy” name is not proof of disuse.
  **Done when:** old paths/options have no executable references, current entry
  points remain covered, and deletion candidates have recorded evidence rather
  than assumptions based on names or file age.

## P2 — Constants, processing, tests, and documentation

### T19 — Remove redundant version metadata and bootstrap duplication

- [x] **Where:** `shared/deployment.py::project_version`,
  the removed `shared/version.json`, `scripts/upload_toolforge.py`,
  `scripts/toolforge/validate-source.py`, `setup-env.sh`, and
  `source-files.json`.
  **Evidence:** TOML says `0.7.13`; tracked JSON says `0.7.7`. The uploader
  regenerates JSON, and both upload and server validation require TOML, so this
  does not establish an incorrect deployed version. Upload/validation also
  duplicate package, script, suffix, and path-policy definitions.
  **What/how:** make required TOML the version authority and remove the redundant
  JSON/fallback once supported launch modes are confirmed. Share the bootstrap
  payload contract through one small standard-library implementation or
  declarative definition. Keep bootstrap independent of application imports.
  **Done when:** source, archive, setup receipt, running services, and tag agree;
  missing/extra/private files, unsafe paths, and corrupted payloads are rejected
  at their trust boundaries. Structure edits have one payload-contract owner.

### T20 — Consolidate facts and formatting constants by meaning

- [x] **Where:** newsletter `_STAT_KEYS`, grade definitions, issue/calendar
  data, `updater/services/newsletter.py::_link`, and
  `newsletter_editorial.py::_literal`/`_link`.
  **Evidence:** statistic-key tuples and escaping maps repeat, but link
  functions differ in colon, label, and apostrophe behavior.
  **What/how:** place fact keys with the schema and shared calendar definitions
  with their existing contract. Consolidate literal-link formatting only after
  defining required differences. Reuse assessment definitions where semantics
  match; keep source aliases, display labels, ordering, and colors distinct when
  they express different rules. Avoid an all-project constants dumping ground.
  **Done when:** delimiters, apostrophes, file/category links, localized labels,
  and every supported grade render correctly from one appropriate owner.

### T21 — Simplify tabular transformations using native Polars

- [x] **Where:** newsletter statistics/annual analysis, article milestone and
  scope merging, Pageviews ranking, comparison preparation, and updater
  projections.
  **What/how:** identify Python loops/UDFs used for joins, grouping, ranking,
  deduplication, or aggregation; replace proven cases with expressions. Combine
  independent expressions and use lazy scans where measured file-backed work
  benefits. Keep parser/request-boundary loops and clear small scalar mappings.
  **Done when:** schemas, stable ordering, provenance, null/zero distinctions,
  and complete retained results match fixtures, with measured resource results.
  No pandas round trips or imposed lazy source API are introduced.

### T22 — Review dependencies by their actual consumers

- [x] **Where:** `pyproject.toml`, `uv.lock`, source imports, Jinja usage,
  SQLAlchemy drivers, Uvicorn options, and test clients.
  **What/how:** record runtime/test consumers, including dynamic drivers and
  extras. Remove only packages made unnecessary by the refactor; use supported
  native capabilities when simpler. No unused dependency was proven in this
  audit: PyMySQL is selected through `mysql+pymysql`, while SQLAlchemy, tqdm,
  MarkupSafe, and the `httpx2` test client have consumers.
  **Done when:** clean locked installation, imports, query and HTTP tests, and
  Toolforge setup pass. Retain Python 3.15, `pywikibot[mwoauth]`, and
  `tool.uv.package = false`.

### T23 — Restructure tests around behavior and close CI coverage gaps

- [x] **Where:** flat `tests/toolforge/test_newsletter*.py`, deployment tests,
  `tests/unit/test_source_deployment.py`, browser checks, and
  `.github/workflows/validate.yml`.
  **Evidence:** the Toolforge directory mixes pure transformations, rendering,
  HTTP behavior, browser fixtures, and deployment; source tests assert some
  historical package names and implementation details. CI runs Python suites
  but not the documented Node tests or browser interface checker.
  **What/how:** group substantial suites by domain (newsletter, web, deployment)
  and test level. Preserve unittest discoverability when nesting. Merge repeated
  fixture loading and equivalent boundary cases with existing unittest tools;
  replace topology assertions with current import/inertness contracts. Delete
  obsolete behavior tests only with the associated implementation. Add Node
  tests to CI and a reproducible browser job or explicit release gate.
  **Done when:** a before/after discovery inventory explains moved/deleted tests.
  Publication conflicts, failed saves, immutable histories, midnight captures,
  previews, nulls, localization, stale responses, native forms, accessibility,
  and source-markup preservation remain meaningfully covered.

### T24 — Rewrite current documentation and verify artifact cleanup

- [x] **Where:** README, all `docs/`, CONTRIBUTING, scoped instructions, and
  inventoried local/server artifacts.
  **What/how:** keep product results/UTC triggers in README, commands in usage,
  deployment/recovery in administration, checks in development, and ownership
  contracts in architecture. Consolidate duplicated newsletter explanations;
  put reader guidance and source/storage contracts in their proper documents.
  Prefer positive, actionable instructions and link rather than repeat. Retain
  audited wiki contracts, null semantics, attribution/licenses, and UTC rules.
  After T03, delete only exact verified obsolete local/server artifacts; preserve
  durable data, credentials, active worktrees, and required recovery sets.
  **Done when:** links, paths, commands, schedules, and configuration names agree
  with the implemented system; docs remain English. No broad wildcard cleanup
  substitutes for the inventory. Server cleanup is completed with T28/T29.

## Release gates — v0.8.0

### T25 — Complete offline verification of the final architecture

- [x] **Where:** all changed code and the reorganized suites.
  **What/how:** run the commands below, adapting discovery paths after T23.
  Consult the Python environment skill/configured IDE interpreter before running
  the toolchain. Fix refactor regressions and distinguish external test blockers
  from application failures. Repeat targeted checks after subsequent changes.
  **Done when:** actual results, test discovery, and any unmet gates are recorded;
  searches find no executable old-path references or unsupported fallbacks.

```sh
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/unit
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/integration
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/toolforge -p 'test_*.py'
uv lock --check
uv run ruff check --no-fix .
uv run ruff format --check .
uv run python scripts/upload_toolforge.py --dry-run
node --test tests/toolforge/test_live.cjs
node --test tests/toolforge/test_newsletter.cjs
for script in scripts/toolforge/*.sh; do sh -n "$script"; done
```

### T26 — Verify outcomes, efficiency, and the web interface

- [x] **Where:** report generation, shared collection/storage, scheduler,
  HTTP routes, templates, localization, and interface fixtures.
  **What/how:** compare T01 results for equivalent inputs; verify one collection
  per declared shared observation, no upstream work for public requests, fewer
  unchanged writes, and correct independent outcomes. Render synthetic pages in
  all three locales and run the documented 320–1920-pixel interface checks;
  inspect screenshots, copying/date selection, no-JavaScript forms, and focus.
  Use Toolforge previews for necessary live source checks. Treat exact midnight
  trigger tests and observed production scheduling as separate evidence.
  **Done when:** core report contents and retained data remain correct, approved
  behavior changes are documented, efficiency claims have counts, and visible
  timestamps retain UTC meaning without repeated labels.

### T27 — Finalize one consistent release identity

- [x] **Where:** `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, payload metadata,
  release workflow, and version-bearing documentation.
  **What/how:** use sequential `0.8.0.devN` only for needed test deployments.
  Perform the required final cleanup pass, then publish base `0.8.0`; maintain
  one evolving changelog section under `## Until 0.9` with an explicit UTC
  timestamp and consolidated completed outcomes. Keep formal release versions
  immutable. Verify tag `v0.8.0` matches the tested source and payload digest.
  Commit cohesive Conventional Commits and merge the task branch into `trunk`
  after required verification. Review release contents before pushing existing
  local history; do not accidentally include unrelated unpushed work.
  **Done when:** source, lock, changelog, tag/release artifact, and deployment
  identity agree. The changelog does not claim uncompleted cleanup or deployment.

### T28 — Deploy using the verified current Toolforge contract

- [x] **Where:** uploader, setup/validation scripts, `aranami-web`,
  `aranami-hub`, and the selected immutable source/environment generation.
  **What/how:** deploy only when execution is authorized and T03/T25–T27 gates
  are met. Validate the complete staged payload/environment before promotion;
  preserve working data and credentials. Replace the web service and verify
  availability, then drain the old hub and confirm termination before starting
  its replacement. Preserve deliberately paused/missing services and the tested
  recovery generation. Apply only inventoried service/configuration changes.
  **Done when:** actual server version/digest, selected paths, receipt, service
  health, single publisher, read-only web behavior, and recovery evidence are
  recorded. A source dry run is not deployment evidence.

### T29 — Verify production schedules/data and finish selective cleanup

- [ ] **Where:** deployed services, saved status, schedules, reports, newsletter
  archives, diagnostics, and T03's exact cleanup inventory.
  **What/how:** inspect observed executions and next UTC runs, report outcomes,
  snapshot identities/timestamps, newsletter date availability, and public
  pages. Verify at the next relevant boundary when a midnight or daily cycle
  has not yet occurred; report that check as pending rather than simulated
  production success. Remove obsolete jobs/files only after their replacement
  is healthy and no live/manual process or required recovery uses them.
  **Done when:** deployment and cleanup are independently evidenced, retained
  data remains readable, and the final report lists changes, validation,
  deployed identity, and any genuine remaining issues or pending observations.

## Official references used for this plan

- [MediaWiki API etiquette](https://www.mediawiki.org/wiki/API:Etiquette):
  grouped reads, identifiable User-Agent, appropriate caching, and rate/lag
  handling inform T07–T09. Preserve endpoint-specific operational rules.
- [Pywikibot FAQ](https://doc.wikimedia.org/pywikibot/stable/faq.html) and
  [page generators](https://doc.wikimedia.org/pywikibot/stable/api_ref/pywikibot.pagegenerators.html):
  use supported high-level reads and preloading for content batches.
- [Polars stable API](https://docs.pola.rs/api/python/stable/reference/index.html),
  [database reads](https://docs.pola.rs/api/python/stable/reference/api/polars.read_database.html),
  and [Pandas migration guidance](https://docs.pola.rs/user-guide/migration/pandas/):
  evaluate native I/O, explicit keys, expressions, null semantics, and combined
  contexts in T10/T21. Check compatibility with the locked release before use.

These references support implementation choices; they do not prove which local
code is unused or guarantee an exact historical observation from a live query.
