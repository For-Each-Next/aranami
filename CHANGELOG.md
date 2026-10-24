# Changelog

This file records major completed changes, grouped by package release line.

## Until 0.8

### 0.7.3 (2026-10-24 12:18 UTC)

Overview: Replaced the daily Pageviews percentage cutoff with whole-day
publication checks and made report failures understandable on the dashboard.

- Verified all 24 UTC site-wide hours, the daily top list, and leading article
  observations before publishing, allowing sparse individual article histories.
- Retained fresh downloads, chronological catch-up, source-failure retries, and
  missing-versus-zero observations without blocking verified days at 1% missing.
- Rejected malformed hourly responses as source failures rather than treating
  corrupt or duplicate observations as an unpublished day.
- Replaced exception names with localized explanations, unwrapped grouped HTTP
  failures, and retained safe failure causes for individual PexBot destinations.
- Updated operating contracts and covered whole-day readiness, sparse histories,
  grouped failures, and localized initial/live status views with offline tests.

### 0.7.2 (2026-10-24 11:28 UTC)

Overview: Simplified dashboard navigation and article lists, exposed report
failure details, removed obsolete code, and condensed the documentation.

- Removed the home TOC item from page templates without CSS hiding or a live-script
  workaround, preserving the brand link and in-page section anchors.
- Displayed localized report-update failures with observed target dates and safe
  exception identifiers, retaining generic fallbacks for older observations.
- Limited the dashboard's popular-article and DYK sections to six entries each.
- Made maintenance-category cards compact rows with left-aligned names and
  right-aligned article counts.
- Removed unused homepage preloading, retired job aliases, and obsolete interface
  messages and connection attributes while preserving saved-state readers.
- Shortened the guides and project overview, retaining essential commands and
  contracts, and recorded the permanent navigation fix in TODO.md.

### 0.7.1 (2026-10-24 10:47 UTC)

Overview: Corrected Pageviews new-entry trends, improved daily-report labels
and dashboard navigation, added an About-page server monitor, and simplified
generated wiki source.

- Kept continuous leading Pageviews absence unranked and marked first-period
  entries New, preserving zeroes for later gaps and matching previous periods.
- Displayed confirmed daily report dates for New pages and Pageviews on the
  website, including verified current reports and all three interface languages.
- Removed one JSON indentation level and added explicit end-invocation comments
  to generated HOT v2 lists while preserving their decoded report data.
- Moved dashboard navigation beneath the title and added a section directory
  linking only to visible report sections.
- Added a live About-page server monitor with saved runtime environment,
  CPU and memory usage, resource limits, and explicit stale observations.

### 0.7.0 (2026-10-24 09:40 UTC)

Overview: Moved the dashboard to Flask and Waitress, exposed live execution
progress, and introduced per-list JSON data for CSS-rendered wiki reports.

- Replaced the custom public HTTP server with a Flask application and production
  Waitress server, preserving routes, bounded downloads, and source isolation.
- Locked the web runtime dependencies, updated project metadata, and classified
  the operational application as Beta with production transport regression tests.
- Simplified routine result lines and labeled actual starts, finishes, and next
  scheduled executions, retaining detailed timings in the status popovers.
- Added task/date-specific running labels, separate observed phase counters, and
  log-page server status, with five-second sync and one-second local clocks.
- Replaced repeated HOT templates with per-list metadata and items, supplying
  Lua/CSS rendering with assessment badges, scoped lists, and rank-rise styling.
- Preserved actual task-force identities, including the upstream Minecraft name,
  independently of display headings and without changing project templates.
- Distinguished unavailable counts from measured zeroes, omitted daily averages,
  and emphasized daily/monthly site ranks on total views with multi-day averages.
- Consolidated deployment and Pageviews guidance, removed obsolete usage notes,
  and rewrote the guides around current responsibilities and supported workflows.
- Clarified that historical gaps wait for later report dates and raised the hub's
  recommended memory to 4 GiB after a confirmed 2 GiB catch-up memory kill.

## Until 0.7

### 0.6.4 (2026-10-24 06:59 UTC)

Overview: Unblocked historical Pageviews catch-up with provisional zeroes,
added cheap source-readiness probes, and reduced dashboard synchronization traffic.

- Probed daily source readiness before full article downloads while preserving
  endpoint coverage checks, chronological publication, and transport failures.
- Counted historical gaps as zero in totals and daily averages after validation,
  retaining observation flags and retrying missing measurements in later runs.
- Advanced dashboard clocks locally each second and synchronized status every
  fifteen seconds, preserving focus, popovers, freshness checks, and recovery.
- Removed the routine synchronization hint and hid next-execution times during
  active runs while retaining connection-failure warnings and saved schedules.
- Removed redundant validation of accepted batches and updated source contracts
  and verification guidance for provisional values and browser clocks.

### 0.6.3 (2026-10-24 05:24 UTC)

Overview: Simplified public run logs and status cards, corrected independent
report timing, added live dashboard updates, and enabled sequential Pageviews
catch-up with fresh full histories, daily-data guards, and earlier-endpoint retries.

- Displayed concise lifecycle summaries publicly while preserving detailed
  downloads, with thirty-calendar-day retention of UTC daily logs.
- Replaced expanding status details with native popups, one-line emoji and
  duration summaries, and four-character last-run and next-run labels.
- Added small localized relative timestamps in whole minutes or hours
  beside the retained UTC times, distinguishing past and future events.
- Refreshed routine and per-page outcomes, relative times, and continuous
  runtime every second from saved observations without initiating wiki work.
- Preserved open popups, keyboard focus, and scroll during updates, paused
  hidden tabs, and retried failed refreshes with an explicit connection notice.
- Displayed execution times as whole seconds with two decimals below one
  second, retaining full precision in stored measurements.
- Measured each report attempt independently of earlier reports and cooldowns,
  retained whole-routine totals, and waited for delegated requests to finish.
- Displayed queued reports as waiting to run, with no start time or elapsed
  timer until their individual preparation and publication actually began.
- Downloaded full Pageviews histories per run and fallback attempt, sharing
  only a successful batch across daily reports and retiring download checkpoints.
- Stopped downloads above 1% missing final-day records and retried one day
  earlier, preserving chronological saves and each report's actual data date.
- Failed when all unpublished endpoints failed coverage or an intermediate
  report exceeded the guard, preserving healthy data and confirmed progress.
- Accepted measured zeroes and sparse older histories without per-article gap
  warnings, retaining concise batch progress and source-failure diagnostics.
- Allowed guarded sequential result generations within one run without
  weakening stale-run protection, preview isolation, or source readiness.
- Included the optional browser script in validated source payloads while
  retaining complete server-rendered pages and native status popups.
- Added article totals and FA/FL/GA or Top/High counts to English-comparison
  edit summaries while retaining change details and bounded attribution.
- Raised the documented and suggested hub memory allocation to 2 GiB after
  confirming repeated out-of-memory restarts at Toolforge's 512 MiB default.
- Updated operating and architecture guides for log retention, reader summaries,
  live status, per-report timing, and API-only daily catch-up with Polars caches.

### 0.6.2 (2026-10-23 12:38 UTC)

Overview: Replaced the PHP website with Python-rendered HTML and CSS,
consolidated reusable runtime behavior, and removed obsolete code and
operational documentation.

- Replaced all PHP pages, helpers, fixtures, and runtime configuration with
  Python presentation, preserving dashboard, maintenance, status, and logs.
- Served validated saved data and bounded log downloads through a separate
  published Toolforge Python job without initiating collection or edits.
- Shared localization, cards, assessments, sorting, and native HTML status
  disclosures across pages without requiring browser JavaScript.
- Centralized safe file reads, HTTP responses, title sorting, replica text
  decoding, prose rules, and atomic Parquet replacement for their consumers.
- Removed unused wrappers, obsolete directories, and PHP ignore rules while
  retaining documented APIs and dynamically selected source collectors.
- Replaced retired routing, cache migration, and future conversion runbooks
  with current deployment instructions and complete offline Python checks.

### 0.6.1 (2026-10-21 06:28 UTC)

Overview: Unified article cards with cached introductions, simplified report
status and logs, improved maintenance navigation and sorting, protected
daily pageview additions from incomplete histories, and capped Pageviews
requests at fifty per second.

- Shared numbered cards with full-width introductions, title-aligned bottom
  metadata, and up to twelve popular and DYK articles.
- Displayed other supplied article lists directly without remaining-record
  expanders or truncated previews.
- Cached original, simplified, and traditional Page Previews introductions,
  keeping generic zh unchanged with independent caches and attribution.
- Placed introduction licensing after each source update time and omitted
  confirmed-empty content sections while retaining unavailable states.
- Preserved literal title spaces in summary requests to avoid empty upstream
  responses for underscore-equivalent paths.
- Combined daily view counts and site ranks, linking counts to thirty-day
  article trends ending on the actual source date.
- Added routine daily-history continuity checks, rejecting incomplete daily
  batches without replacing healthy data or advancing the report date.
- Capped article and top-list Pageviews requests at fifty per second across
  wiki reports, website refreshes, and retries with one shared allowance.
- Reused revision-backed DYK statuses and skipped page rendering when visible
  facts matched, avoiding edits solely to refresh hidden statistics.
- Added stable random pagination and reverse sorting, with maintenance tags
  and retained months linking to their actual wiki categories.
- Made status text open a modal table with linked destination titles and
  UTC timing, omitting redundant wiki labels beside Chinese report pages.
- Simplified the masthead, renamed showcases to 佳作展示, and combined the
  source version and continuous runtime in one footer line.
- Limited Recent logs to one hundred matching lines and exposed graceful
  shutdown logs by delivering Toolforge's stop signal directly to Python.
- Documented Toolforge-only software, local-site, and Games conversion forks,
  with layered overrides and multiple longest matches per title.

### 0.6.0 (2026-10-20 15:00 UTC)

Overview: Centralized Aranami's Toolforge data updates in a Python 3.15
backend hub, with wiki and web frontends, durable reusable results,
completion-aware scheduling, readable logs, and a responsive project website.

- Replaced wheel and PAWS deployment with validated source payloads,
  isolated setup, coordinated process replacement, and recoverable backups.
- Separated the backend hub, wiki formatting and publication in updater,
  and web presentation, retiring old imports and separate worker launchers.
- Moved source parsing, report comparisons, membership, and cache ownership
  to backend services; updater read only its publication destinations.
- Gave English importance and quality reports independent renderers for
  their different layouts, using backend-prepared change metadata.
- Shared exact successful membership and top-pageview reads within update
  cycles, while preserving each output's independent successes and retries.
- Targeted Python 3.15, used native lazy imports, and locked OAuth with
  Pywikibot while adapting transformations to native Polars expressions.
- Stored complete report tables, provenance, manifests, and generated text
  before publication, retaining the latest generation and its predecessor.
- Preserved calculated data after failed saves, isolated preview datasets,
  and prevented older runs from advancing newer publication checkpoints.
- Initialized retained New pages data without historical rediscovery and
  kept PexBot outcomes as delegated metadata with unknown edit counts.
- Skipped confirmed complete automatic daily work across restarts and
  exposed actual postponed schedules through separate live observations.
- Ran assessment lists hourly, kept the 18:00 UTC Pageviews daily boundary,
  and refreshed English comparisons and database reports without backfill.
- Replaced structured log events with standard natural-language terminal
  and daily logs, with a single date-selectable, searchable web log viewer.
- Recorded page targets, start/end times, durations, outcomes, and confirmed
  saves separately from logs, and displayed them in website status details.
- Refreshed five website reports independently with configurable intervals,
  persisted successes, hourly failure retries, and preserved source dates.
- Retired multilingual badge browsing while keeping Chinese highlights,
  upcoming content, DYK, popularity, maintenance, logs, and three locales.
- Replaced horizontal report rows with individual responsive cards and
  linked measured views to correctly dated Pageviews Analysis queries.
- Displayed plain site ranks and localized routine states with actual
  status times, measured durations, and scheduler freshness.
- Preserved known Pageviews observations across cache replacements and
  retained missing previous ranks as unknown without claiming new entries.
- Preserved complete dated and bilingual change details within 255 bytes,
  using the compact `([[toolforge:Aranami|Aranami]])` edit attribution.
- Replaced wheel verification and release artifacts with source checks,
  offline Python/PHP suites, and reviewed upload manifests.
- Added a reader-focused About page and rebuilt the English README with
  current schedules, component responsibilities, and the Toolforge layout.
- Organized English guides for terminal users, administrators, application
  developers, and system-level developers, including upload and restart.

## Until 0.6

### 0.5.5 (2026-10-20 12:40 UTC)

Overview: Matched Topviews article rankings, accelerated dashboard delivery,
clarified multilingual browsing and homepage headings, and documented the
planned source deployment and report redesign.

- Matched dashboard and Aranami site ranks to Topviews' article-only list,
  excluding non-article pages before numbering and retaining measured views.
- Enabled negotiated HTML compression while retaining the rendered-page cache.
- Removed totals from the four homepage panels and the maintenance heading.
- Rendered job states as labelled emoji and showed unfinished elapsed times
  with an ellipsis beside the status update time.
- Sorted language choices by distinct badged-article totals, highest first.
- Replaced the random-filter button with a styled display-all action that
  opens the complete multilingual collection in place.
- Added local-counterpart filters and linked Wikidata item IDs to
  multilingual article comparisons.
- Reused highlights for 12 hours and maintenance lists for 24 hours, and
  moved the highlights update time into its section heading.
- Added rating and importance sorting to maintenance lists, following
  Wikipedia's assessment tiers and keeping unranked observations last.
- Grouped the four translation maintenance tasks into a separate 翻译 block.
- Reordered edit-summary attribution to `(in duration by Aranami version)`,
  preserving its Toolforge link and complete UTF-8 byte budget.
- Consolidated TODO.md for source deployment, reusable reports, scheduling,
  website cards, ranking fixes, and consistent edit summaries.

### 0.5.4 (2026-10-18 08:58 UTC)

Overview: Simplified dashboard pages, made multilingual sampling immediate,
and added cached rendering, report links, and UTC log modification times.

- Removed the expanding gap between article titles and their chips while
  preserving title truncation and trailing date or view-count alignment.
- Added UTC modification timestamps to project and detailed Aranami log
  listings, using each file's actual metadata.
- Removed repeated section descriptions and article-row separators, and
  aligned homepage heading rules with a shared scrollbar gutter.
- Linked job names in the report-status table to their Wikipedia targets,
  numbered multiple destinations, and clarified run-log and job-status labels.
- Reused default homepage HTML for sixty seconds per locale, invalidated
  changed inputs, and kept filtered and random requests uncached.
- Reused Wikidata recognition data for 24 hours while refreshing other
  dashboard sources hourly and retaining safe stale-cache fallback.
- Added language selection and instant random samples of ten multilingual
  records, retaining complete language lists and server-rendered fallbacks.
- Limited the homepage's DYK preview to ten entries without an internal
  scrollbar while retaining the collected history.
- Prefixed future homepage appearance dates with a localized scheduled marker.

### 0.5.3 (2026-10-18 08:16 UTC)

Overview: Added configurable Chinese video-game maintenance worklists and
simplified the Toolforge dashboard, with compact article lists, cached
multilingual comparisons, direct routine states, and typed pageview ranks.

- Added 37 verified Chinese maintenance tasks in eight equal-height cards, with
  sortable fifty-article pages showing assessments, issues, and tag months.
- Defined maintenance groups and labels in a local configuration file,
  supporting category or template rules on articles or discussion pages.
- Flattened the dashboard, placed quality content and DYK on the left and
  popular articles and news history on the right, and added DYK scrolling.
- Kept every past and scheduled quality appearance visible and placed
  dates on the right of all compact article lists.
- Prioritized recent cross-wiki recognitions and filled the default ten
  records from existing articles without inventing observation dates.
- Reused valid cached recognitions after source failures, preserving
  observation times and displaying a cache notice.
- Used numbered recommendations, Chinese grade names, short importance
  labels, and one chip style for recognition and project assessments.
- Displayed multilingual comparisons in language, foreign-article, and
  local-article columns, and removed language statistics.
- Used normal article text, serif section headings, green backgrounds,
  blue links, and a single page heading without repeated navigation.
- Replaced status-log parsing with atomic live routine snapshots, guarded
  against stale writes and omitted previews without changing edit outcomes.
- Kept plain report states and times visible, with one version footer;
  showed site ranks in view-count tooltips and linked the dated Topviews list.
- Added site ranks from Wikimedia's daily and calendar-month API lists,
  counting only articles and omitting homepages and unsupported intervals.
- Added explicit daily/monthly slash rank parameters and inline Lua
  rendering, keeping report numbering outside the item module.
- Deferred reports until applicable top lists became available, logged
  the reason, and retained completed article requests for later retries.

### 0.5.2 (2026-10-18 06:35 UTC)

Overview: Added an independent Toolforge dashboard for Chinese main-page
highlights, daily pageviews, and multilingual quality content, with a
background updater and structured on-wiki report results.

- Added a light, responsive 960 px dashboard with automatic Simplified
  Chinese, Traditional Chinese, and English interfaces.
- Created a shared wave logo and SVG favicon for the project.
- Queried direct Wikidata video-game instances with uniform recognition
  badges and Chinese counterpart links with standard project grade codes.
- Compared successful badge queries to show 30 days of new observations
  across all languages without inventing promotion dates.
- Added language statistics, in-place filters,
  random switching, and expandable complete article lists.
- Retained 100 days of Chinese main-page displays and 30 days of upcoming
  quality content, using verified source dates and explicit carry-in labels.
- Added daily project pageviews from Wikimedia's site top-1,000 list,
  preserving measured dates and distinguishing unavailable data from zero.
- Added a separate hourly updater with automatic retries, atomic snapshots,
  early initial results, and preservation of the last saved data.
- Recorded structured routine results with actual save counts and
  durations, distinguishing updates, no-ops, and unconfirmed outcomes.
- Added a shared daily project log for Aranami and dashboard events,
  with detailed Aranami output available through a separate viewer.
- Moved web maintenance into `src/toolforge/` with a compatibility wrapper
  and a fixed upload payload independent of the Aranami wheel.

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
