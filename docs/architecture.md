# Aranami system architecture

Architecture (this guide) is for developers changing Aranami's architecture,
data ownership, or cross-component behavior. It defines the contracts a change
must preserve. Read the repository and applicable scoped `AGENTS.md` files
before making changes; they remain the authoritative engineering instructions.

For practical contribution and verification steps, use the
[Development](development.md). Terminal commands belong
in [Usage](usage.md), and deployment, process management, and
recovery belong in [Administration](administration.md).

Aranami is developed and tested locally and deployed from source only to
Toolforge. The backend requires Python 3.15. Existing wiki credentials and
Wiki Replica configuration remain outside the source tree.
The [Toolforge maintenance workflow](administration.md#maintain-on-toolforge)
covers validated uploads, environment updates, process replacement, and
recovery.

## Documentation boundaries

Describe what each component does and the results it produces before
discussing technology. Keep the README focused on scheduled tasks and results,
route terminal use to `docs/usage.md`, operations to
`docs/administration.md`, application contribution to
`docs/development.md`, and system-level contracts to Architecture
(this guide).
Keep docstrings focused on behavior, inputs, outputs, and relevant side
effects. Include implementation details only when they explain a contract
the reader needs to preserve. Maintain project documentation in English.

## Runtime and architecture

The source tree contains four components:

- `backend` owns the central hub, source collection, reusable data
  preparation, all input interpretation and comparisons, runtime storage,
  logs and status metadata, update scheduling, and the read-only data
  server. Its report API is `run` and `run_once`;
  `python -m backend` starts the hub.
- `updater` is the wiki frontend. It formats prepared report data as
  wikitext and publishes it through report jobs, projection services, and
  formatting helpers. It reads only its selected edit destinations and
  changes their managed ranges. It owns no source collection, data cache,
  file persistence, or scheduler.
- `webservice` owns Python page rendering and shared CSS. Its presentation
  functions consume prepared backend observations and render complete
  HTML for the dashboard, maintenance worklists, About page, and logs.
- `shared` provides low-level read-only sources, report-storage primitives,
  generic text and date mechanics, configuration, and source deployment
  metadata. The backend decides when to use these primitives and owns
  runtime workflows.

The backend delegates both kinds of updates from one hub process. Due wiki
and website work shares read-only source data within an update cycle, while
each output retains its own success state, source timestamps, and retry
rules. Keep data preparation in `backend`, including website processing
in `backend/website/`. Both frontends consume backend-prepared data;
formatting and publication belong to `updater`, and web rendering belongs
to `webservice`. A facade in the backend does not satisfy this boundary
if the real collection, parsing, comparison, or persistence still happens
in the frontend.

| Operation | Owner |
|---|---|
| Read source pages/templates, replicas, Pageviews, and Wikidata | Backend services through read-only shared adapters |
| Interpret source and retained-report facts, managed settings, identities, and differences | Backend services |
| Read the selected wiki edit destination and check it before saving | Updater job/publication adapter |
| Render prepared rows and difference metadata as wikitext or summary prose | Updater services and formatting support |
| Retain results, advance caches, write previews, logs, and status | Backend execution and domain services |
| Perform an intentional wiki save or delegated PexBot request | Updater publication adapter |
| Serve validated saved snapshots, status, logs, and fixed assets | Backend public HTTP server |
| Render saved data, statuses, and logs as HTML | Python webservice presentation |

Backend services accept concrete definitions and existing destination text
without selecting or loading the destination. They return typed facts,
rows, and metadata sufficient for rendering. The updater must not parse
source templates, infer old membership, perform identity matching, or
read and write data files. Shared managed-range mechanics may locate and
replace comment-delimited spans; interpreting their input settings and
content remains backend work. Keep backend data and support modules
independent of frontend types. Only HTTP orchestration in `backend.public`
imports `webservice` renderers; they receive saved facts and do not load
runtime data or source clients.

Shared code imports neither backend nor presentation code. Website requests
only read saved outputs and cannot trigger collection or publication.
Runtime files live under the tool home, never the uploaded source tree.
Python 3.15 lazy imports keep the public API light; loading or validating
sources must not initialize clients or execute reports. Application imports
resolve directly from the deployed `src` directory on `PYTHONPATH`.

`shared.config.TASK_DEFINITIONS` is the single definition of report targets
and UTC cron schedules, including PexBot subscription roots. Use
`routine_configuration()` for the serializable contract, without a second
generated copy of the schedules.
Version metadata comes from `pyproject.toml`, with a generated
`shared/version.json` for source deployment. The backend supplies both
contracts to renderers and the read-only `/api/config` endpoint. Version lookup requires
no installed distribution and also supplies application user agents.

## Scheduling and observed state

The hub owns process lifecycle and schedules report routines and website
refreshes together. Each wiki task's cycle may refresh due website reports
with the same read-only source data; an hourly callback handles website
refreshes between wiki runs. It delegates report execution through the
monitor, with coalescing and one active invocation per routine. Fresh
report-monitor starts normally check all routines; `run_immediately=False`
waits for triggers. Reusing an active monitor does not enqueue another pass. Stop
with `scheduler.shutdown(wait=True)` before changing report output mode or
clearing caches. Manual `run_once` calls do not alter recurring schedules.

Confirmed New pages and Pageviews completion creates a disposable,
configuration-scoped checkpoint. Automatic dispatch checks it before
creating clients or updating lifecycle observations, including after a
restart. Failed saves, incomplete work, and previews cannot establish
completion. Explicit manual jobs always revalidate the wiki. New pages
requires the complete retained window through yesterday and then waits for
the next 00:07 UTC trigger. Pageviews retains the actual-time 18:00 UTC
readiness boundary, sequential catch-up of missing days, and hourly :59 retries
when behind or deferred. When current, its monitor postpones it to the
18:59 trigger following the next readiness boundary. Jobs return scheduling
advice; only the monitor changes scheduler state.

The backend owns three separate observed-state snapshots:

- `state/routine-status.json`: routine-level starts, results, durations,
  and confirmed save counts.
- `state/schedule-status.json`: the monitor's actual next execution
  times, configuration identity, and running/paused/stopped state.
- `state/report-updates.json`: per-destination update starts, finishes,
  measured duration, publication outcome, and confirmed edit count.

`state/routine-status.json` is a bounded atomic schema-1 snapshot of actual
routine starts and results, with measured durations and confirmed save
counts. Older starts cannot overwrite newer results. Preview activity is
omitted. `state/schedule-status.json` separately records actual scheduler
next-run times, configuration identity, and running/paused/stopped state.
Scheduler events and a thirty-second local heartbeat refresh it without
source requests. The website treats missing or stale observations as unknown and
never calculates cron predictions. Manual one-shot work does not rewrite
the scheduler snapshot. Per-destination observations distinguish a
confirmed update, no change, failure, deferral, and an unconfirmed external
write; an unknown edit count must not become zero. Older attempts cannot
replace newer results. Preview activity does not update any live status.
Routine durations cover the whole invocation. Per-report timing begins
when that report's attempt starts, excluding earlier reports and delegated
request cooldowns. Registered destinations remain `queued` until their
individual work begins. While queued, `started_at` orders registrations
only: the website hides that timestamp and displays a localized waiting
label instead of elapsed time. Starting work replaces it with the actual
start and changes the state to `running`. Reset an attempt only while its
registered identity still owns the saved row,
so a superseded handle cannot overwrite a newer observation.
Never derive these observations by parsing human-readable log text.

## Reusable report results

Backend services provide reusable source facts and extraction results.
Updater services project those observations into report tables and
wikitext; updater jobs attach the results to publication proposals.
Backend execution stores required outputs before attempting a wiki save.
Storage failure prevents publication; failed or unconfirmed publication
preserves the complete calculated result. Source failures and deferrals leave the
last complete generation selected.

`shared.reports.ReportStore` writes `reports/<safe-report-key>/<uuid>/`
bundles containing a schema-1 JSON manifest, named Parquet tables, and
`report.wikitext`. Keys distinguish targets and never contain wiki titles.
The manifest records the validated `application_version`, run/generation
identity, UTC calculation/source times, report periods, provenance, and
publication state separately. Startup diagnostics also identify the
running source version; edit summaries intentionally do not. `latest.json`
atomically selects the newest complete generation and its predecessor.
A lock and generation ordering guard prevent older concurrent runs from
replacing newer results. Sequential results from the same run require the
exact predecessor generation still selected under that lock, allowing
daily catch-up without admitting a superseded run. Readers validate hashes,
schemas, identities, and row counts. Previews use only `dry-run/reports/`
and proposal companions.
Use `ReportStore.read(key)` or `read(key, previous=True)` to consume a
validated generation; it returns no bundle when none has been selected.

Retain complete Pageviews project/task-force rankings, current and previous
ranks, measured views, nullable site ranks, period bounds, and totals.
New pages retains daily records, page identities, creation dates, conversion
flags, assessments, and discovery counts. Historical initialization parses
retained wiki records once and preserves unknown fields; it never repeats
historical discovery just to fill storage. DYK retains completed entries,
nominees, known dates, assessments, and statistics. Assessment targets
retain their complete members and review links. Both English-comparison
reports retain prepared English/Chinese rows and selected lists. PexBot
retains source subscription/history facts and delegated action/result
metadata with an unknown edit count. English comparisons and PexBot
represent current state refreshed on their daily triggers, not invented
daily historical observations. Assessment lists refresh hourly at `:31`.

The normal publication lifecycle is: register the destination, obtain
backend-prepared data, render a proposal, retain the complete result,
recheck the destination, publish, then report the outcome to the backend.
The backend alone advances validated membership or completion evidence.
No-change validation is distinct from a confirmed save, and an external
PexBot response cannot establish an exact local edit count.

## Website collection and presentation

The hub's read-only website collector in `backend.website` persists five
independently configured results:
recognized Chinese content (including future appearances), DYK, popular
articles, news/history, and the full maintenance catalogue. Each defaults
to 24 hours after its own successful refresh. Due selection precedes Site
initialization and membership queries. Reusable reads are shared across
the due set; nothing due means no source requests. Each successful result,
including a valid empty result, advances independently. Failed reports
retain their validated previous data and actual successful timestamps and
retry hourly. Restarting never resets the refresh interval. The schema-2
snapshot remains bounded to 8 MiB across storage and HTTP.

The separate `backend.public` server reads `cache/toolforge/dashboard.json`,
routine/schedule/report observations, and saved daily logs from the runtime
directory. It validates each saved format through its owning backend
reader before rendering pages or exposing `/api/dashboard`, `/api/status`,
or `/api/logs`. Pages use normal links, GET forms, and native HTML controls;
the complete initial page works without JavaScript. An optional same-origin
script refreshes dashboard status through `/api/live`, which renders only
validated status and uptime fragments without reading website datasets.
Fixed CSS, JavaScript, and SVG assets come from `webservice/public_html/`.
Requests accept only GET and HEAD,
reject path traversal and unsafe files, and bound file sizes and concurrency.
The hub also exposes local read-only `/dashboard.json` and `/healthz`
diagnostics; the public server operates independently of those endpoints.
Omit confirmed-empty presentation sections, without confusing source
unavailability with zero items. Keep the saved observations and their
refresh metadata even when an empty section is not displayed.

Historical Chinese main-page appearances cover 100 UTC dates and upcoming
quality content covers 30 days. A carry-in subject is not assigned an
invented first-appearance date. Popularity preserves its actual measured
source date and bounded fallback to an older available day. Project
membership joins enrich assessments without per-article queries. All
retained datasets remain independent of requests and the status heartbeat.
English comparisons are on-wiki reports with reusable results.

Homepage cards reuse plain-text introductions from the stable
[Page Content Service summary endpoint][page-summaries], the source used
by Page Previews. The read-only shared adapter owns the REST request;
backend website preparation requests original `zh`, native `zh-hans`,
and `zh-hant` text with `Accept-Language`, normalizes up to three sentences,
and caps each
introduction at 360 characters plus an omission mark. Python rendering selects the
prepared text matching the browser preference. Generic `zh` keeps original
text without a simplified/traditional fallback; English uses simplified
introductions. Keep literal title spaces encoded as `%20` in REST
requests: underscore-equivalent upstream paths can have different cached
extracts. Article and Pageviews URLs are unchanged. Preparation escapes no
HTML for reuse: the renderer escapes all saved text on output. Display upstream
article links and CC BY-SA 4.0 attribution after each introduction
section's update time, separately from Aranami's
source license. Canonical titles and article identities remain unchanged.

The title/script-keyed `article-extracts-v2.parquet` cache belongs alongside the
private website report caches. A refresh pass shares it across popularity,
recognized content, DYK, and news/history, including repeated dated
appearances. Request each script once per distinct title, not once per
card or interface visit. Successful text and confirmed empty extracts last 24 hours.
Failed requests retain old text and wait at least an hour before another
attempt; script failures remain independent and do not fail an otherwise
valid report. Persist UTC epoch
seconds for source/attempt/use metadata, bound cache size and row count,
and drop observations unused for 30 days. Nothing due still means no
clients or source requests; cached report rows keep their introductions
across restarts. A changed introduction contract invalidates the four
affected report identities, without resetting maintenance data. Adding original
text reuses existing script observations and requests only missing variants.

Main sections and subsections stack vertically; only sibling article,
maintenance-tag, and routine cards form columns. Cards have an 18em minimum,
retain their normal column width in sparse groups, and fit one column on
narrow screens without horizontal overflow. Ordered article lists use
native list-item counters and shared styles across the dashboard and maintenance
pages. Homepage cards show titles and inline assessments, smaller
full-width introductions with equal card insets, and very small bottom-aligned
source metadata aligned with the title text. Popularity
and DYK display up to twelve articles, retaining shorter actual results.
Other supplied article lists render directly, without remaining-record
expanders or truncated previews.
Preserve article ordering, display limits, inline unsqueezed chips,
and catalogue subsection order. Maintenance pages sort the complete saved
group before fifty-row pagination, with stable page-ID ties and unknown
dates/grades last in both directions. Seeded random order stays stable across pages and
can be reversed. Maintenance tags link to their saved wiki sources; month
links use the exact retained category that supplied the earliest date.
Keep the 37 verified task definitions and their category/template evidence
in `backend/website/maintenance.json` and the collector. Page requests never
query wiki sources.

Popular titles link to Wikipedia. View counts link to Pageviews Analysis
with the actual article, Chinese Wikipedia, all-access, and user-agent
settings over thirty inclusive days ending on the actual source date.
The card's count still represents that single source day.
Display article-only site ranks as separate plain text,
never as project positions. Derive site rank from the full filtered daily
top list before joining membership; retain raw ranks as tie breakers,
measured zeroes, nulls, and the verified non-article exclusions.

Routine cards show localized state text, status time, measured/elapsed
duration, configured report links, and the actual next execution from the
monitor snapshot. Hide the next-execution row while that routine is running;
restore it after completion without changing the saved schedule.
An emoji, linked-style status button, and whole-routine
duration stay on one line. The button opens a native HTML popover with a
keyboard-accessible table of per-destination outcomes, linked page titles,
independent execution durations, and UTC timestamps. Close controls,
Escape, and outside clicks dismiss it without JavaScript. Chinese timestamp
labels use four characters followed by a fullwidth colon. Shared Python
presentation functions handle localization, safe markup, and presentation order.
Execution times use whole seconds, with two decimals for subsecond results;
completed durations round to the nearest second and active durations count
completed seconds. Stored measurements retain their original precision.
Last-run and next-run UTC times include small relative annotations in
whole minutes below an hour and whole hours thereafter, rounded down.
Future times say how long remains; unavailable schedule times have no
annotation. Running durations, relative times, and uptime advance locally
every second using a monotonic browser clock anchored to the last server
timestamp. Status and per-page outcomes synchronize every fifteen seconds
while visible, bypassing the full-page cache. Requests never overlap, time out after
ten seconds, and retry with delays up to thirty seconds after failures.
Hidden tabs pause polling and local ticks; returning immediately synchronizes.
Completed durations stay fixed and stale schedules expire locally. The browser
updates existing elements to preserve open popovers, focus, and scroll.
Failed refreshes retain the last received observations with an explicit notice;
healthy synchronization shows no notice.
Narrow screens scroll the popup table within its container. The footer combines
source version and continuous runtime including seconds in one line.
Missing, stale, paused, and stopped
schedules remain explicit. Preserve all three interface languages, logs,
Chinese highlights, DYK, popularity, and maintenance. Cache rendered views
and locales in bounded process memory for up to sixty seconds; saved-input
changes invalidate them without writing generated HTML to runtime files.
Log views and unseeded random maintenance views bypass the cache. Source
changes take effect when the validated deployment replaces the process.
Keep responses no-store, negotiate compression, and preserve original
source timestamps during failed-refresh fallback.

## Polars and Parquet

Read the [stable Polars Python API reference][1] and its [Pandas migration
guide][2]. Write native Polars operations rather than translating Pandas
idioms literally:

- Express selection, filtering, derived columns, joins, grouping, aggregation,
  ranking, and window calculations with native expressions. Combine independent
  expressions in a single context where possible.
- Keep keys in explicit columns. Avoid index emulation, mutable column
  assignment, row-wise `apply`, and conversions through Pandas.
- Keep explicit schemas, including empty results. A missing observation is
  `null`; a measured zero is valid data.
- Use lazy scans when they benefit file-backed work, and retain the existing
  eager contracts of source adapters.
- Iterate in Python at network-request and wikitext-parser boundaries, or
  for small scalar mappings. Do not implement tabular joins or aggregates
  with Python row loops.
- Save disposable tabular caches as Parquet and read, merge, deduplicate,
  and trim them with Polars.

## Logs, caches, and dry runs

All runtime paths are relative to the caller's current directory, never the
uploaded source tree or `site-packages`:

- `logs/aranami.YYYY-MM-DD.log` is the one universal file for a UTC date.
  All routines append natural-language diagnostics identifying the data
  being refreshed, destination being edited, counts, starts, completion,
  elapsed time, skips, and failures. Keep messages readable in a terminal;
  do not encode lifecycle JSON or internal metadata dumps in log lines.
  Retain the current UTC date and previous 29 calendar dates. Do not
  configure the root logger at import time. Backend logging owns terminal
  and file handlers; the website reads those saved messages.
  Log the hub's graceful shutdown reason before waiting for active work,
  then record its stopped state after scheduler, collector, and HTTP cleanup,
  while the daily handler remains open. Recent logs display at most one
  hundred matching lifecycle summaries: starts, updates, no-change outcomes,
  failures, and server stops. Hide individual source-request failures,
  progress, logger prefixes, and tracebacks from this reader view, while
  preserving raw daily downloads for diagnostics. Scan at most 8 MiB to
  find recent summaries without loading the whole file. Preview summaries
  remain explicitly marked and never imply live outcomes. Forced termination
  cannot guarantee a final record.
- `state/` holds the authoritative routine, schedule, and report-update
  observations described above. Keep it separate from diagnostic logs
  and disposable source caches.
- `cache/` is visible and disposable. Manual deletion or
  `backend.run(clear_cache=True)` forces cached data to rebuild. Deleting it
  must not lose authoritative state or alter the report with identical
  upstream data. Cache replacements must be atomic; malformed snapshots
  should be ignored with a diagnostic.
- `dry-run/` holds one compact UTF-8 Markdown summary per run, with UTC
  start/end times, an overall status, and one task entry per routine.
  Group every PexBot refresh proposal under its single routine task.
  Include target site/page, edit summary, status tags, and a relative link
  to each proposal's flat `.wikitext` companion. Keep exact proposed text
  only in that companion, not embedded in the Markdown. Use the unique
  report stem and an edit number for filenames, never a wiki title as a
  path. Failed or deferred tasks make a mixed run `partly success`.
  Toolforge job commands use `/data/project/aranami` as the working directory;
  all Python launchers set the shared source PYTHONPATH explicitly.
  External PexBot generation cannot be previewed without triggering writes, so
  record the proposed action and skip its endpoint.

## Daily report contracts

### New pages

The new-page routine retains the latest 100 complete UTC dates by default,
through yesterday. It fills absent dates, prunes older records, and reuses
existing daily records without duplication on repeated calls. Check the
date window before discovery or maintenance. If no retained date is
missing, leave the report unchanged without an icon-only edit. When gaps
are filled, refresh icons and enrich metadata across retained records.
Each missing date combines page creations with revisions tagged
`mw-removed-redirect` in the same half-open UTC interval. Use replica revision
tags for historical dates; recent changes do not cover the full retained
window. Deduplicate
the combined candidates by page ID before preloading their current text.
Keep the same namespace, non-redirect, and keyword filters for both sources,
and append ` — 自重定向页改写` to matching conversion entries.
End each resolved list item with
`<!-- 页面ID 12345 · 创建时间 2026-08-05 11:22:33 -->`, using the page's
earliest revision timestamp in UTC. Append it after the conversion note
when present. Enrich retained rows with missing metadata comments without
rerunning daily discovery, and preserve saved comments during icon refreshes
and later runs. Missing creation timestamps are `未知`; unresolved page
identities leave the row unchanged with a diagnostic.

### Pageviews

Use the Wikimedia Pageviews API exclusively for daily observations and
history, treating its returned measurements as authoritative. Do not read
Pageviews dumps, including Toolforge's shared files, or substitute dump
counts for API observations. Keep Polars processing and Parquet caching.

Pageviews processes missing report dates in order, publishing each day's list
separately. First probe the candidate day's top list and up to three leading
articles' single-day records. Missing source dates try an earlier endpoint
before any full-history requests; transport failures defer the invocation.
A valid top list containing only non-articles still proceeds to the full guard.
Probe observations never replace full histories. Reuse successful top lists
within the batch for publication. Download each article's full required history
through the latest eligible ready UTC day and validate that final day's coverage.
If missing daily records exceed the limit, stop that download,
shorten its end by one day, and download the full histories afresh. Discard
the failed attempt's responses; never resume prior downloads from memory
or disk across endpoint retries. Try only endpoints later than the published
checkpoint, and fail the task if none passes.

Once an endpoint passes, share only that successful batch's fresh responses
for daily analysis and saves from the next unpublished day through that
endpoint, in chronological order. For example, a checkpoint on the 15th and
an eligible target on the 18th produce separate reports for the 16th, 17th,
and 18th. If the 18th is unavailable but a fresh download through the 17th
passes, publish the 16th and 17th only. Do not skip intervening reports,
roll back the checkpoint, or label older observations with a newer date.
Stop at the first required source failure, failed/unconfirmed save,
or superseded result; resume from the last confirmed on-wiki marker on the
next call. A shortened endpoint leaves the routine behind and eligible for
hourly retries; it cannot establish current completion. The current incomplete
UTC day is never treated as a complete daily total.
Read its published checkpoint only from `<!-- report date YYYY-MM-DD -->`
and emit the same marker in generated reports. Fail before building without
a valid checkpoint or when recognized dates conflict.
Keep the Pageviews data-readiness rule at 18:00 UTC.
Yesterday becomes eligible
when the actual invocation starts at or after 18:00 UTC; the hourly `HH:59`
schedule first attempts it at 18:59 UTC. Before 18:00, older missing dates
remain eligible. Apply the same cutoff to live, dry, and manual job
invocations, using the context's actual `started_at` rather than the chosen report
anchor for readiness. Report dates must also be before the requested anchor;
a supplied anchor cannot bypass the actual cutoff. Reject a candidate
endpoint as soon as more than 1% of the full distinct article population
lacks that day's daily record. Count absence while downloading and stop
before the next request once the limit is crossed; do not use only the
processed prefix as the denominator. Exactly 1% missing is allowed.
Explicit zeroes are valid records, while empty responses and HTTP 504 count
as missing at this endpoint check. A failed guard prevents publication and
further top-list requests for that candidate. Failed
attempts leave the wiki marker and healthy snapshot unchanged, preserving
any earlier confirmed catch-up reports. If no later endpoint is usable,
mark the task failed. The next hourly invocation probes again and, once
ready, downloads full histories from the last published marker.

Fetch at least the configured 800-day history buffer by default, expanding
it for the required comparisons and catch-up dates. Saved Parquet coverage
never suppresses a request or fills missing fresh response dates in a report.
After endpoint validation, gaps in successful histories contribute provisional
zeroes to daily totals, ranks, and averages. Average denominators include all
calendar days. Bind this permission to the accepted batch's site, project,
membership, history bounds, and threshold; arbitrary prefetched or cached
data cannot enable it. Retain current/previous observation flags with rankings
so provisional zeroes do not inflate observed-article counts. Raw histories
never gain synthetic measurements. Later full downloads retry gaps and use
recovered values in future calculations without rewinding published reports.
Direct preparation without an accepted batch retains its strict daily guard
and null unavailable totals. HTTP 504 articles remain excluded and count
toward the failure limit. Keep successful snapshots atomically after the
required guards and top lists pass; retain known observations in those disposable
snapshots without treating them as fresh inputs. Do not create or read
pending request checkpoints. Log batch progress and concise endpoint-fallback
or final-failure summaries without per-article unknown-date warnings.
Individual HTTP attempt details belong at debug level.

Fetch site ranks from the official all-access [Pageviews top endpoint][pageviews-top]
for single UTC dates and complete calendar months. Fetch each distinct
current interval once, then join its titles onto
project and task-force rankings with Polars. Preserve the returned site
article order using the same Topviews title filter as the dashboard.
Exclude namespace pages, the full and namespace-stripped main-page
titles, and verified global false positives, then number the remaining
articles from one with Polars. Classify titles from shared site metadata
without reading individual pages. Emit that
article-only rank in [PJ:VG/HOT/item][hot-item]'s `top` parameter as `daily/<rank>`
or `monthly/<rank>`. These ranks cover
articles present in the API's top-1,000 pages; do not imply coverage of
articles outside that source list.
Omit `top` for yearly and all other unsupported intervals and for unlisted
titles. Do not request annual datasets from other sources or estimate
annual rankings from truncated monthly lists.
An applicable list must be fetched successfully before publication or
healthy-cache replacement. HTTP 404, empty or malformed results, and
transport failures defer the complete report. Log its date, required
period, and source failure reason through the job's existing deferral
handler; retry the same report date with fresh full article downloads at
the next invocation.
An available raw list containing only non-articles is a valid empty
article result and does not defer publication.
Keep the existing daily availability guard ahead of the top-list lookup.
The Chinese item wrapper invokes the [on-wiki report module][hot-module]. Python supplies
explicit `daily/<rank>` or `monthly/<rank>` parameters and ISO dates; report
list numbering remains outside the inline item wrapper. Missing previous
ranks remain null in results and empty in generated comparison parameters,
which does not establish a new entry. Rendering behavior is maintained on
the wiki. This repository does not ship or deploy a local Lua module.

Limit all Pageviews network attempts to fifty requests per second across
the hub process, spacing starts by at least twenty milliseconds. Article
observations, site top lists, website popularity reads, and retries share
this allowance. Cached observations consume no allowance, and idle time
does not permit a later burst.

Keep the source adapter's one-attempt contract. The Pageviews routine skips
an article immediately after HTTP 504, excluding it from rankings while
counting its absent daily record toward the one-percent limit. Preserve
its saved history and request it again on the next run. Retry 502/503
responses up to three times using the same full range; exhausted retries
defer publication until a later invocation. A later run starts all article
downloads afresh, including requests completed before a source failure.

## Managed ranges and on-wiki configuration

Use paired `aranami` HTML comments to mark generated ranges:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...generated daily records...
<!-- aranami end="new-pages" -->
```

Settings belong on the opening comment where practical. For example:

```wikitext
<!-- aranami begin="page_views" history-days="800" missing-percent="1" -->
...generated popularity report...
<!-- aranami end="page_views" -->
```

Preserve the attributes when replacing content and validate values before
requests or publication. Code supplies documented defaults. Current settings
include new-page retention, Pageviews history and availability threshold,
and assessment-list source categories.
Pageviews caps the availability threshold at 1%; larger legacy
`missing-percent` attributes remain preserved but cannot relax the guard.

Only `aranami` comments delimit managed ranges. Keep functional
`<section begin="..."/>` and `<section end="..."/>` tags used by labeled
section transclusion outside the Aranami comments. Refuse ambiguous, missing,
reversed, or duplicate markers instead of replacing a broad span. Preserve
all text outside the managed range.

## Wikitext, templates, and site semantics

Use `mwparserfromhell` to inspect and construct non-trivial wikitext, including
templates, links, headings, and HTML comments. Preserve editable parser
objects where possible instead of using regular expressions over nested
wiki syntax. Plain fixed text does not need unnecessary parsing.

Before adding or changing a template call, read that wiki's actual template
source, documentation, redirects, and invoked Lua module/configuration.
Record the source and revision used when a contract or extractor mapping
needs to be audited. Do not guess parameter names, action aliases, or
whether a template already emits list markup.

Use `pywikibot.Page(site, name, ns=...)` and the site's namespace/title rules
for template identity and talk-page conversion. Do not maintain regex lists
of translated namespace prefixes. On Chinese Wikipedia, `PJ:` can resolve
to the WikiProject namespace; it is not necessarily a Template title.

Keep Article history and DYK interpretation separate by wiki. Chinese rules
come from [zhwiki's Article history configuration][3]; English rules come
from [enwiki's configuration][4]. For example, English GAR can record a GA
listing, whereas Chinese GAR is a delisting review. Only accept action and
result spellings supported by the appropriate configuration. A rule for
one wiki is not evidence for another.

Unknown dates are `None` in Python and null in Polars. Sort them deliberately,
label them clearly in reports, and omit them from dated statistics. Do not
invent sentinel calendar dates such as year 8888.

Use high-level Pywikibot reads only for information replicas cannot supply,
such as current text. Preload collections of pages. Backend services
prepare data and interpret all source templates and retained report
membership; updater services render the prepared rows and change metadata.
Updater jobs create structured edit proposals, save pages with informative
summaries, and check that destination text has not changed during report
construction.

### Edit summaries

Compose English edit summaries through `updater.support.edit_summary.EditSummary`
with a maximum of 255 UTF-8 bytes. Chinese characters typically use three
bytes; shorten complete mentions or clauses without splitting wiki links.
Mark primary article-title links with `«...»`, including Pageviews leaders.
Use the centralized article-mention formatter for local and interwiki links.
Bilingual mentions are exactly `«[[:en:Title]]» ([[Chinese title]])`; omit
companions when absent and retain disambiguation parentheses and transition
annotations. Shorten complete optional clauses without splitting link,
guillemet, or parenthesis boundaries.
At publication, end every live edit and dry-run proposal summary with
exactly `([[toolforge:Aranami|Aranami]])`, with no trailing full stop.
Use `EditSummary.with_attribution(summary)` and reserve space for this
complete suffix within the same 255-byte limit. Keep version and measured
duration in backend metadata and logs, not edit summaries. Reusing a
proposal must not duplicate the attribution.
Dated summaries use compact English dates such as `15 Sep 2026` and begin
with `Updated for <date>.`. Pageviews groups daily, weekly, monthly,
seasonal (quarterly), and yearly periods under each leading title in a
`Top:` sentence, linking each title once. Join grouped periods with
`and`, including a serial comma for three or more periods, and separate
titles with semicolons. Keep places gained beside the corresponding period,
omitting movement when the leader remains first or has no previous rank.
If the complete summary and attribution exceed 255 bytes, omit yearly
first, then seasonal, and then the remaining longest periods as needed.
Do not add omission counts to this sentence. New-page summaries use a date
and matched article/non-article counts only when the latest daily list was
filled during the run. When only older dates were filled, use
`Updated class icons.` and mention the older daily records backfilled.

### Membership and DYK changes

Real-time summaries state the current item total and grouped additions and
removals. Keep summaries factual, without instructions
or suggested actions. Store stable article page IDs in local Parquet
membership snapshots under `cache/`, never in generated report wikitext.
Use IDs to recognize renames, grade changes, and movement between DYK
candidate and completed lists. Validate each snapshot against the exact
destination text and advance it only after successful live publication.
DYK summaries begin with separate counts for completed articles and active
nominees, such as `24 articles, 3 nominees.`. A repeat nominee appears in
both counts while listed in both ranges. Summaries distinguish
`New nominee`, `Passed nominee`, and `Failed nominee`. A repeat nominee
passes when it gains a completion date. A departing candidate absent from
the completed list fails.
Unchanged historical completion dates cannot distinguish failed repeats
from delayed candidate cleanup, so they do not imply either outcome.
Keep removed historical completed entries separate from failed nominees.
Reuse DYK talk-page states by revision ID. Once current completed/candidate
membership, assessments, and dates match the published items, keep the exact
destination text and skip page rendering. A new sampling date or count in the
hidden statistical comment alone does not justify a wiki edit; reusable
backend result tables may still record the latest observation.
Dry runs may seed the original membership but must not advance it to the
proposed report. Missing or stale snapshots fall back to title matching,
or existing Wikidata IDs for English reports, without changing the report
body. The dated new-page list retains its requested ID and creation-time
comments.
### English comparison reports

Both English key-article reports list English
additions and removals as `[[:en:Title]]` links and match English page IDs
across title changes.
The quality report mixes FA, FL, and GA in descending order of the latest
English promotion or listing date, grouped by year. Same-day entries use
English title sort keys; null dates form the final `年份未知` section.
Append an ISO date comment to each resolved item. Reuse the English quality
extractor and cache null results as well as resolved dates. Validate cached
dates against the current English class, talk-page ID, revision, and template
aliases, and preload only discussions needing refresh. Save the actual
revision fetched with each text snapshot. Quality summaries use the English
class with `listed`/`delisted` for GA and `prompted`/`removed` for FA and FL,
including the previous class when it changes. Refresh the quality page's
introduction counters and body through required paired `aranami` ranges:
`count_all` for the total, `count_fa`, `count_fl`, and `count_ga` for each
English class, and `body` for the list. Preserve all surrounding content.
The importance report retains its alphabetical sections and counter updates.
Both edit summaries start with comma-separated article totals and assessment
counts: `3,032 articles: 100 FA, 32 FL, 2,900 GA.` for quality, or
`1,234 articles: 34 Top, 1,200 High.` for importance. Actual change details
follow within the shared UTF-8 budget, preserving the attribution link.

These behaviors follow the concrete importance or quality report strategy,
not a report's name. The backend receives a data definition, including
selection criteria, membership range, and whether listing dates are
required. It returns comparison metadata; updater summary formatters only
turn that metadata into text.

## System-change review

Before changing a shared contract, trace every producer, persisted format,
reader, and failure path. Update the actual ownership boundary rather than
adding a wrapper around old behavior. Preserve complete source facts,
unknown observations, source timestamps, preview isolation, lifecycle
serialization, and storage-before-publication ordering.

Add offline regression coverage for affected readers and writers, including
empty results, stale or malformed files, concurrent ordering, failed saves,
and restarts where relevant. Exercise independent output failures without
rolling back another output. For public API or storage-schema changes,
update all callers and validation fixtures in the same cohesive change;
do not add retired compatibility imports.

Run the practical [verification workflow](development.md#verification-and-releases)
before handoff. Release and changelog authority remains in `AGENTS.md`;
deployment authority and recovery remain in [Administration](administration.md).

[1]: https://docs.pola.rs/api/python/stable/reference/index.html
[2]: https://docs.pola.rs/user-guide/migration/pandas/
[3]: https://zh.wikipedia.org/wiki/Module:Article_history/config
[4]: https://en.wikipedia.org/wiki/Module:Article_history/config
[hot-item]: https://zh.wikipedia.org/w/index.php?title=WikiProject:VG/HOT/item&oldid=91242270
[hot-module]: https://zh.wikipedia.org/w/index.php?title=Module:PJ:VG/HOT&oldid=95139258
[pageviews-top]: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/examples/project-metrics.html#most-viewed-pages
[page-summaries]: https://www.mediawiki.org/wiki/Page_Content_Service#/page/summary
