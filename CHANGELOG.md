# Changelog

This file records major completed changes, grouped by package release line.

## Until 0.9

### 0.8.4 (2026-11-01 14:57 UTC)

Overview: Unified the frontend with Wikimedia Codex and TypeScript, simplified
public documentation, and kept newsletter drafts current until publication
with corrected annual traffic, source links, and editable layouts.

- Refreshed started issues daily until recorded publication, retaining
  delayed drafts across quarter rollovers and freezing closed issues.
- Selected the earliest unpublished issue from saved publication metadata
  while keeping retained issues available without live website source reads.
- Recalculated annual traffic from validated daily Pageviews observations,
  including same-date corrections, while preserving unknown totals.
- Linked homepage dates to their call pages and saved anniversary revisions,
  linking each daily DYK supply archive once per list.
- Added cutoff-date notices before incomplete introductions, preserving
  authored notes when reusing drafts for later issues.
- Restored editable quality images and example announcements, and linked
  each draft banner to the automatically generated wikitext.
- Corrected count-scaled new-article pies and simplified annual chart
  containers while retaining measured rankings and clear list introductions.
- Relabeled the newsletter website's Chinese feature headings as
  `專題報導` and `专题报道`.
- Focused the README on three public web tools and monitored on-wiki reports,
  removing internal workflows and administrative guidance.
- Removed the Logs status panel, public storage totals, and About's logs
  link, preserving footer uptime and the report monitor.
- Built the frontend with Codex cards, fields, tables, notices, and Vue 3
  controls, retaining native forms, calendars, dialogs, and readable saved data.
- Moved browser behavior to strict TypeScript and verified locally built,
  pinned frontend assets during CI without adding server build dependencies.
- Consolidated post-0.8.3 work into one release and removed superseded
  GitHub workflow records, artifacts, and caches.
- Deployed v0.8.4 to Toolforge and verified the exact source, both services,
  frontend bundle, public pages, and removals at 2026-11-01 14:57 UTC.

### 0.8.3 (2026-11-01 11:54 UTC)

Overview: Corrected annual newsletter statistics and credits, refined report
history and assessment counts, and scheduled daily reports around other hub work.

- Prepared calendar-year Pageviews rankings from existing downloads and reused
  them for annual drafts, with year-only prose and dated provisional coverage.
- Excluded delisted and superseded quality promotions from annual lists,
  totals, and author credits while preserving historical evidence.
- Used Graph:Chart's built-in stacked-chart legend and a zero-to-fifty
  vertical range that expands for larger observed cohorts.
- Titled the stacked chart `歷年新入選、晉級高品質條目`.
- Matched the prior issue's data-summary credit style with `User:Example`.
- Used primary contributors directly when nominators are unknown and limited
  attribution notices to explicit disagreements.
- Removed the repeated DYK chart introduction and kept its cutoff in the caption.
- Prefixed annual sentences with their coverage notices and indented author
  lists with unnamed article parameters for simpler wikitext editing.
- Limited the Reports page to the newest twenty execution records and
  retained their stage dialogs in all three interface languages.
- Scheduled assessment lists, English comparisons, and PexBot daily after
  midnight UTC, waiting for other hub work and running one at a time.
- Skipped PexBot refreshes when the bot had edited a report within the
  preceding 24 hours, including edits before midnight.
- Added gray signed changes to assessment-log introduction counts using
  prior observed totals, preserving daily deltas across failed saves.
- Consolidated Git history into version-tagged releases, preserving release
  snapshots and archiving other histories for recovery.
- Removed the superseded v0.8.0 refactor plan.
- Deployed v0.8.3 to Toolforge and verified its payload, both services,
  public version, and chart title at 2026-11-01 11:54 UTC.

### 0.8.2 (2026-11-01 10:28 UTC)

Overview: Refined annual newsletter statistics, contributor credits, navigation
controls, and readable wikitext.

- Kept the issue submit button visible after JavaScript initialization and
  issue changes, alongside the data-date controls.
- Stabilized initial button styling and copy-control layout, and removed
  obsolete assessment-filter initialization.
- Spaced chart parameters around pipes and equals signs, and separated all
  chart list values with a comma and a space.
- Pointed newsletter edit-summary links to the tool's trailing-slash path.
- Reorganized annual quality statistics, article lists, subject placeholders,
  and author tables around the January 2026 reference structure.
- Grouped featured and A-class content in annual totals, omitted zero-count
  groups, and linked Bplus counts to `PJ:VG/BPCA`.
- Used natural missing-data sentences and factual comparisons, with inline
  Memo date notices in the introductions to incomplete figures and lists.
- Kept the two annual assessment pies in one container with a shared introduction.
- Centered author tables and made collapsible article lists easier to edit.
- Removed the draft JSON panels and their report-data generation code.
- Prioritized recorded nominators, with differing WikiWho authorship shown
  beside each affected article.
- Aligned partial-year daily growth with actual observation dates and kept
  observed empty rankings distinct from unavailable data.

### 0.8.1 (2026-11-01 08:57 UTC)

Overview: Simplified newsletter announcements with combined homepage recognition
lists and cleaner draft text.

- Combined featured and good homepage appearances under `典優內容`, with
  dated content-type labels and bulleted empty lists.
- Removed generated explanations about undated records from newsletter drafts.
- Added spaces after commas in newsletter chart data for clearer wikitext.

### 0.8.0 (2026-11-01 08:34 UTC)

Overview: Reorganized newsletter and article evidence ownership, consolidated
shared observations and persistence, and simplified source deployment contracts.

- Grouped newsletter and article preparation into cohesive backend packages,
  moved wiki reading to shared sources, and removed obsolete import paths.
- Shared typed Pageviews decoding and application identity, and avoided
  unchanged milestone rewrites while preserving recovery checkpoints.
- Centralized collection schedules and freshness alongside report triggers,
  preserving actual capture times and independent publication outcomes.
- Restricted ordinary reads to canonical retained formats and kept explicit
  historical conversion commands with their preserved source evidence.
- Made source TOML authoritative, shared the bootstrap payload contract, and
  required immutable generations and process-specific draining for promotion.
- Removed request-time newsletter enrichment and copied dashboard facts,
  retaining read-only access to canonical observations and immutable archives.
- Added discoverable domain suites and Node/Chromium release gates covering
  localization, native forms, accessibility and narrow layouts.
- Made headless interface checks select and verify desktop mouse capabilities
  consistently on Linux and macOS.

## Until 0.8

### 0.7.14 (2026-11-01 08:04 UTC)

Overview: Separated newsletter viewing and editing, with dated source
attribution prefilled for manual wiki edits.

- Added distinct view and edit links beside each newsletter's copy control
  in all three interface languages.
- Prefilled edit summaries with the draft's data date and newsletter-tool
  attribution, preserving independently dated annual observations.

### 0.7.13 (2026-11-01 07:24 UTC)

Overview: Refined native newsletter charts, historical monthly coverage and
editing controls, with compact pies and independent copying and navigation.
Documented the source audit and implementation plan for the v0.8.0 refactor.

- Rendered ordered native quality stacks with plain year labels, short grade
  names, animation and hover, without expanded SVG or custom hover targets.
- Counted each annual promotion once under its year-end grade, preserving
  milestone evidence and keeping legacy observations readable.
- Reused unchanged talk revisions within each UTC month and checkpointed
  month-end rechecks so interrupted acquisition resumes safely.
- Limited complete pies to 400 pixels and placed regular black percentages
  inside slices of at least 5%, retaining plain grade/count legends.
- Recovered delayed calendar-month winners from bounded revision history,
  retaining exact permalinks and leaving unpublished reports unavailable.
- Separated Copy from Go to wiki page and standardized newsletter controls
  at 32 pixels, including the date input and issue selector.
- Styled comment invitations as blue primary actions while retaining their
  section preload links and keyboard focus.
- Removed invisible announcement date comments while retaining visible dates
  and date markers in quality and annual article lists.
- Added an evidence-based v0.8.0 TODO covering architecture, consolidation,
  legacy removal, verification, and deployment prerequisites.

### 0.7.12 (2026-11-01 05:39 UTC)

Overview: Expanded historical milestone coverage and annual recognition charts,
refined daily trend observations and draft date markers, and corrected dated
assessment-log status and narrow-screen layouts.

- Counted all six quality grades from saved milestone Parquet, preserving
  earlier recognitions after later delisting or demotion.
- Centered stacked annual charts with grouped grade colors, yearly totals
  and keyboard-accessible hover details for each underlying grade.
- Collected redirected article histories and explicit Bplus register dates
  into the milestone inventory with original identities and source evidence.
- Appended the observed current day to completed-quarter DYK points while
  keeping the final year-end point unique.
- Replaced month boundary comments with MMDD suffixes on dated draft items
  and added the requested annual data-summary attribution placeholder.
- Kept pie counts in regular-weight grade/count legends and removed the
  module's duplicate bold slice labels.
- Confirmed assessment-log report dates only after saved or verified
  unchanged dated sections, matching the new-page status presentation.
- Kept contents menus vertical at every width and anchored narrow dropdowns
  correctly, with locally scrollable maintenance tables below 640 pixels.

### 0.7.11 (2026-11-01 04:51 UTC)

Overview: Rebuilt newsletter drafts around shared evidence, independent daily
facts, annual charts and source-linked rankings. Refined responsive navigation,
regional Chinese content, maintenance filters, report status and resource
presentation. Separated durable data from caches, retired temporary workflows,
and added logged job draining before shutdown and deployment.

- Saved independent daily newsletter facts and rendered selected observations
  on request, with year-long January features and quarterly editorial drafts.
- Unified milestone dates, nomination roles, questions and provenance across
  DYK reports, reviews and newsletters, with listing-time author attribution.
- Preserved recorded nominators separately from computed authorship and
  retained tied leading contributors without inventing missing evidence.
- Added annual historical charts, monthly winners, provisional view/edit
  rankings and ACG participation summaries while retaining statistical prose.
- Rendered two assessment pies with grade/count labels and black values,
  framed quarterly DYK trends on the right, and removed redundant source prose.
- Linked monthly popularity labels to their exact source revisions and
  displayed annual pageview totals in whole, comma-grouped ten-thousands.
- Classified DYK subjects by exact database project names, including 音乐,
  and grouped games by observed release years without guessing unknowns.
- Ordered editor credits by grade and date, merged tied ranks and retained
  full annual award rankings with first-score and first-award summaries.
- Used one native newsletter calendar with saved-date bounds, in-place issue
  changes, literal copying and accessible editing invitations.
- Split source acquisition from draft rendering and retained complete daily
  archives, historical revisions and original migration evidence.
- Separated collected data and generated records into readable dated paths,
  timestamped generations and validated enum-backed Parquet assessments.
- Retained comparison predecessors atomically, reused English snapshots only
  when live text matched, and kept PexBot actions in latest-only caches.
- Preserved shared records for 365 days after confirmed project removal and
  prevented delayed observations from restoring purged data.
- Cached exact historical text only after revision/hash checks and scheduled
  English comparisons after the daily assessment snapshot at midnight.
- Recovered interrupted executions as failed while preserving active owners,
  confirmed report dates and unknown finish times or edit counts.
- Added permanent revision links for confirmed report saves and assessment
  summaries separating articles, nonarticles, project totals and redirects.
- Fixed narrow-screen contents, adopted the Codex language icon and native
  names, and used browser Taiwan/Hong Kong variants for article content.
- Displayed uptime across pages, unified duration formats, added filesystem
  capacity and refined review activity, assessment tooltips and rank links.
- Rebuilt maintenance filters with open assessment bounds, genre and size
  controls, other tags and ten-row default pagination.
- Drained jobs before shutdown and deployment pod deletion, retained final
  status/logs and required a process-specific completion acknowledgement.
- Preserved validation of older immutable source generations and resumed the
  old hub safely when source selection failed after draining.
- Removed temporary jobs, task state/logs, inspection pages, implementation
  and workflow rules while preserving durable evidence and report archives.
- Consolidated component guides and retained source, storage, publication and
  runtime lifecycle contracts in their owning documentation.

### 0.7.10 (2026-10-31 15:40 UTC)

Overview: Refined the website around Codex navigation and controls, introduced
a new Aranami identity, and improved reviews, maintenance filters, report
execution history, and newsletter drafts.

- Replaced the site branding with a wave logo and image wordmark, added a
  language menu and mobile contents button, and simplified page headings.
- Moved About to the footer and content attribution to a credits section;
  linked the Wikipedia project and added live server status to About.
- Removed repeated UTC labels from website dates, headings, and tooltips
  while retaining UTC conversion and explicit machine-readable timestamps.
- Converted the two raster review icons to vectors, added icon descriptions,
  and preserved upstream attribution and licenses.
- Clarified review start, response, and participant labels and corrected
  nested discussion dates, including 豐川祥子's review.
- Unified review links and separated their metadata with CSS bars; used
  365.25 / 12-day months for opening ages and inclusive comment windows.
- Added regular-expression title searches and shared inclusive assessment
  comparisons, with a sorting menu, direction radios, and plain grade labels.
- Retained independent per-job execution history with measured durations and
  dialogs showing observed preparation, storage, and publication stages.
- Reduced wide-screen status separators, made history task controls look
  like text links, and linked stage page titles without forced line breaks.
- Simplified history to the latest 100 executions without filters or paging,
  with bulleted stage details and millisecond timestamps.
- Added an issue menu that updates drafts in place and a Sunday-first calendar
  with unavailable dates disabled and only the selected date shaded.
- Simplified newsletter labels and plain wikitext displays while preserving
  full copying from compact boxes, daily selection, and no-JavaScript use.
- Added a primary copy-and-open action for each newsletter edit page beside
  the secondary copy button, with localized date-based edit summaries.
- Displayed zero milestone counts and compacted completed empty months while
  preserving later corrections and backfilled assessment changes.
- Listed per-grade milestone totals, including zeros, while a quarter is open;
  reserved the no-change summary for complete, observed empty quarters.

### 0.7.9 (2026-10-31 13:43 UTC)

Overview: Reorganized the dashboard around content, dedicated maintenance and
report pages, and clearer review activity. Added retained daily newsletter
drafts with date selection, clearer assessments, and highlighted copying.

- Displayed review starts and responses with relative times, complete UTC
  tooltips, and distinct participant counts, hiding nominator-only follow-ups.
- Moved maintenance and report status into dedicated pages, flattened dashboard
  headings, and replaced narrated logs with concise report history.
- Moved raw execution-log downloads behind an About-page link and removed
  event narration and its presentation code.
- Simplified footer links, report controls, Chinese schedule labels, and the
  startup and uptime message.
- Combined article and list counts at matching assessment levels in monthly
  prose and pie charts, while retaining the separate source classes.
- Counted registered articles without a project class as unassessed and kept
  each pie-chart JSON slice on one line.
- Displayed generated drafts as selectable, read-only code with wikitext
  highlighting, preserving the original text for copying.
- Renamed the newsletter tool to “Newsletter drafts” in all three locales.
- Retained daily draft facts and original wikitext with actual capture times,
  preserving history through later updates and disposable-cache clearing.
- Added a localized date calendar for saved drafts, with explicit unavailable
  dates and separate data-through and generation times.

### 0.7.8 (2026-10-31 12:28 UTC)

Overview: Improved quarterly newsletter drafts with daily collection, retained
article history, and verified historical homepage evidence. Prepared complete
quarterly draft layouts, grade counts, and charts from internal statistics
and talk-page news notices.
Simplified editing and navigation, maintenance labels, and report details.

- Refreshed newsletter drafts at `00:00 UTC` through the previous completed day,
  closing completed quarters at their boundary and retrying failures hourly.
- Collected featured, good, news, and anniversary appearances across each issue,
  preserving dated source evidence and using talk-page ITN notices for news.
- Backfilled DYK, ITN, anniversary notices, and FA/FL/GA/A/Bplus milestones from
  verified article histories, retaining undated events separately.
- Retained versioned talk-page history in durable Parquet archives and refreshed
  changed sources hourly, preserving prior evidence through incomplete reads.
- Cached 100 completed days of unfiltered anniversary sources with historical
  redirect provenance, matching primary articles by resolved project page IDs.
- Simplified draft editing, corrected localized Copy controls, and preserved
  generated links, literal apostrophes, and all four newsletter contents links.
- Kept all three reporting months in copyable drafts using collected figures,
  without source-version lists or new-page collection-gap annotations.
- Restored monthly new-article grades and the quarterly pie chart using
  current or retained historical assessments, with explicit unknown grades.
- Preserved the initial issue's section order and nine quality placeholders,
  retained empty sections, and removed excess whitespace before categories.
- Kept generated drafts reusable as later issue layouts, including empty
  quality lists, homepage sections, charts, and awards.
- Reused internal assessment observations for newsletter counts and quality
  changes instead of rereading published assessment lists and logs.
- Unified every website page around the dashboard's left contents panel,
  with a collapsible contents control on narrow screens.
- Opened saved issues directly from the selector and retained ordinary issue
  links for use without JavaScript.
- Moved Newsletter and Run logs into the native Tools menu while preserving
  localized labels and current-page highlighting.
- Ordered critical issues as deletion discussions, speedy deletion, copyright
  validation, and notability, with concise labels in all three languages.
- Separated report status text from its Details action, removed status dots,
  and renamed assessment lists to recognized content.
- Consolidated newsletter usage guidance while retaining detailed architecture
  contracts for historical evidence and cache recovery.

### 0.7.7 (2026-10-31 10:33 UTC)

Overview: Refined the multilingual dashboard with Codex components, clearer
review and status information, and deletion-related maintenance worklists.
Added automatically populated quarterly newsletter drafts backed by wiki sources.

- Added Codex controls and assessment chips, consistent time formats, and
  a new wave favicon.
- Replaced review numbering with locally stored review-type icons linked to
  their Commons sources, with retained author and license notices.
- Added AfD, speedy-deletion, and copyright worklists under Critical issues,
  using verified wiki categories and the existing project scope.
- Simplified existing copy, showed complete introductions, and consolidated
  their attribution in the footer.
- Kept system details on Logs, made archive filenames download links, and
  removed the About status panel and redundant notices.
- Condensed report status into rows with accessible detail dialogs and
  compact page counts.
- Kept a single Dashboard navigation link for its maintenance and report sections.
- Used “改為” for new assessment transitions without rewriting existing entries.
- Automatically populated the next issue's overview, quality changes, and
  announcements throughout the quarter, with dated sources and copy controls.
- Reused the preceding issue's original wikitext layout, navigation, icons,
  columns, and count-change styling while updating collected issue data.
- Preserved both subpages' original source around generated report bodies,
  and selected issues through radios that advance at the UTC quarter boundary.
- Refreshed the completed quarter at `00:00 UTC` on each quarter's first day,
  queuing behind active collection instead of waiting for the daily interval.
- Distinguished observed periods and incomplete new-page counts, and removed
  empty template output and authored feature/interview sections from the generator.

### 0.7.6 (2026-10-31 07:06 UTC)

Overview: Added current content reviews, redesigned the webservice with Codex
styles, and introduced immutable source deployments on Toolforge.

- Added cached hourly review cards from nomination pages and project categories,
  preserving successful observations through failed refreshes and restarts.
- Rebuilt navigation, localized reports, filters, dialogs, and log controls
  for accessible desktop and narrow-screen use.
- Pinned services to immutable source and Python environments, with atomic
  promotion, individual replacement, and retained generations for recovery.
- Preserved job settings and runtime data during legacy deployment migration,
  requiring confirmed hub termination before starting its replacement.
- Corrected assessment report dates and daily totals, repaired existing wiki
  dates, and retained synchronized publication state.
- Maintained DYK chart dates and counts in inline managed ranges, replacing
  the obsolete generated statistics comment.

### 0.7.5 (2026-10-25 12:07 UTC)

Overview: Added reusable daily project assessment snapshots and a Chinese
video-game change log with durable membership comparisons. Defined the
branch and merge workflow for repository tasks.

- Retained complete project PageAssessments in daily Parquet snapshots at
  the `00:00 UTC` trigger, preserving actual collection times for reuse.
- Recorded assessment changes in natural sentences with bold page links and
  simplified rating labels in `WikiProject:电子游戏/评级/日志`.
- Preserved removals and no-change dates through failed saves and restorations,
  reusing shared queries and storage with isolated previews.
- Logged newer unchanged daily observations as `無變更`, without duplicate
  headings, conflicting markers, or invented initial/gap-date observations.
- Added localized status labels and offline coverage for membership changes,
  publication recovery, managed ranges, and first-observation baselines.
- Initialized the wiki's managed range through October 24 as a separate
  action, retaining the complete current project baseline.
- Required a new branch for each repository task and a merge into `trunk`
  after completion and verification.

### 0.7.4 (2026-10-24 16:30 UTC)

Overview: Rebuilt the read-only website with FastAPI, WebSocket status pushes,
and Jinja2 templates, with compact server details and resilient live updates.

- Replaced Flask with FastAPI routing, responses, autoescaping Jinja2 templates,
  and isolated application contexts; removed unused web dependencies.
- Preserved read-only saved-data routes, bounded downloads, security headers,
  native popovers, localized pages, and isolated source/runtime storage.
- Replaced Waitress with Uvicorn and added bounded, read-only status pushes,
  heartbeat detection, automatic reconnects, and independent HTTP verification.
- Resynchronized on window focus after sleep, recovered overdue requests, and
  distinguished network, response, clock, and rendering failures.
- Included templates in verified source deployments and covered framework
  errors, application isolation, transport recovery, and stale-response races.
- Condensed About into the tool description and contact links, with compact
  server facts, observed startup, sample timestamps, and relative ages.
- Applied narrow-screen layouts through 600 pixels and relative day labels
  while preserving permanent startup facts when resource samples expired.
- Generated separate structured major-event logs and displayed localized
  lifecycle/results as natural-sentence paragraphs with Wikipedia links.
- Displayed completed popup durations as `12′05.30″`, `5.30″`, or `0.44″`
  across all locales, with padded seconds and correct minute rollover.
- Kept running timers in whole seconds, using `12′05″…` or `5″…`.
- Styled clickable routine status labels as blue text without underlines.

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
