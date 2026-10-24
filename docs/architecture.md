# Aranami system architecture

Use this guide when changing component boundaries, persisted data, or report
behavior. It records contracts that must survive an implementation change.
[AGENTS.md](../AGENTS.md) and scoped instructions define engineering and release
policy; [Development](development.md) covers offline verification,
[Usage](usage.md) covers commands, and [Administration](administration.md)
covers deployment and recovery.

Development is local; live collection and publication run only on Toolforge
with Python 3.15. Credentials and runtime files stay outside the source tree.

## Runtime and architecture

| Component | Owns |
|---|---|
| `backend` | Hub lifecycle, source interpretation, reusable preparation, scheduling, persistence, logs/status, and read-only HTTP |
| `updater` | Selected edit destinations, wikitext projection, summaries, and intentional publication |
| `webservice` | Python HTML presentation, localization, and public assets |
| `shared` | Read-only source adapters, configuration, generic mechanics, and storage primitives |

The hub delegates wiki and website work, sharing read-only inputs within a
cycle. Outputs retain independent success states, timestamps, and retries.
Backend services accept concrete definitions and existing destination text;
they do not select or load edit destinations. They return prepared facts and
differences, not frontend types. The updater checks destinations before saving
and owns neither collection nor runtime storage. Website presentation consumes
saved facts without loading files or initializing source clients.

Shared code imports neither backend nor frontends. Only HTTP orchestration in
`backend.public` imports webservice renderers. Website requests cannot collect,
publish, or write runtime data. Imports, validation, and command-line help
remain inert. Resolve source imports through deployed `src` on `PYTHONPATH`;
do not restore installed-package shims or retired launchers.

`shared.config.TASK_DEFINITIONS` is the sole definition of report destinations
and UTC schedules. `routine_configuration()` supplies its serialized form.
`pyproject.toml` supplies the version, with generated `shared/version.json` in
deployments. The backend exposes these contracts through `/api/config` and
passes them to renderers; no installed distribution is required.

## Scheduling and observed state

The monitor coalesces triggers and allows one active invocation per routine.
Fresh monitors normally check all routines; `run_immediately=False` waits for
triggers, and reusing an active monitor adds no startup pass. Stop with
`scheduler.shutdown(wait=True)` before changing output mode or clearing caches.
Manual `run_once` calls do not alter recurring schedules. Wiki cycles and an
hourly callback refresh due website reports; only the monitor changes schedules.

Confirmed New pages and Pageviews completion creates a disposable,
configuration-scoped checkpoint. Automatic dispatch checks it before creating
clients or updating lifecycle observations, including after restart. Manual
jobs revalidate the wiki. Failed saves, previews, and incomplete work cannot
establish completion. New pages requires the complete window through yesterday
and then waits for 00:07 UTC. Pageviews retries at hourly `:59` while behind;
when current, it waits for 18:59 after the next actual-time readiness boundary.

The backend persists three bounded, atomic snapshots:

| File | Observation |
|---|---|
| `state/routine-status.json` | Routine starts/results, measured durations, confirmed saves, and active progress |
| `state/schedule-status.json` | Actual next executions, configuration identity, monitor state/session, and optional runtime resources |
| `state/report-updates.json` | Per-destination attempts, publication outcomes, durations, and confirmed edits |

Older attempts cannot overwrite newer observations. Preview activity never
updates live state. Unknown edit counts remain null, not zero; confirmed saves,
no change, failure, deferral, and unconfirmed external writes remain distinct.
Routine timing covers the whole invocation. Destination timing starts at its
individual work boundary, excluding earlier reports and delegated cooldowns.
Registered destinations remain `queued`: their registration timestamp orders
attempts but is not displayed as execution time. Starting work replaces it
with the actual start. Reset or finish an attempt only while its registered
identity still owns the destination.

Optional routine `progress` contains `phase`, `report_date`, `current`, and
`total`. Supported phases are `preparing`, `publishing`, `pageviews_readiness`,
`pageviews_download`, and `pageviews_aggregate`. Counters are both null or
observed integers satisfying `0 <= current <= total <= 1_000_000_000`.
Updates add no source reads; article counters advance every hundred items and
at completion. Shared downloads retain the first unpublished report date,
not the candidate endpoint. Completion clears progress and may retain a
`report_date` established only by a confirmed save or verified unchanged report.

Failed observations may include `failure_date` for the attempted report and
`failure_reason` for a bounded public exception-class identifier. They never
replace confirmed `report_date` or expose arbitrary exception text. Missing
facts retain generic labels; new attempts and successful outcomes clear them.

Scheduler events and a thirty-second local heartbeat refresh schedule state
without source requests. Optional validated `runtime` records Python, OS,
architecture, cgroup memory use/limit, and observed CPU use/allocation. One fully
used core is 100%; unavailable resource files remain unknown. Manual one-shot
work does not rewrite this snapshot. Read structured state for status;
diagnostic logs are not lifecycle evidence.

## Reusable report results

Publication follows: register destination, prepare facts, render proposal,
retain the complete result, recheck destination, publish, record outcome.
Storage failure prevents publication. Failed or unconfirmed saves preserve
calculated results; source failures leave the last complete generation selected.
Only the backend advances membership or completion evidence. A no-change check
is not a save, and PexBot responses do not establish an exact local edit count.

`shared.reports.ReportStore` writes `reports/<safe-report-key>/<uuid>/` bundles
with a schema-1 manifest, named Parquet tables, and `report.wikitext`. Keys
distinguish targets without using wiki titles as paths. Manifests separate
application version, run/generation identity, UTC calculation/source times,
periods, provenance, and publication state. `latest.json` atomically selects
the newest complete generation and its predecessor. Locked ordering prevents
older runs replacing newer results; sequential results from one run require
the exact predecessor still selected. Readers validate hashes, schemas,
identities, and row counts. `read(key, previous=True)` selects the predecessor;
no selected bundle returns none. Previews use only `dry-run/reports/`.

Retain complete reusable facts, not just displayed rows:

- Pageviews: project/task-force rankings, current/previous ranks, views,
  nullable site ranks, periods, observation flags, and totals.
- New pages: daily records, page identities, creation dates, conversions,
  assessments, and discovery counts. Initialize from retained wiki records
  once, preserving unknowns without repeating historical discovery for storage.
- DYK and assessments: complete members/nominees, dates, assessments,
  statistics, and review links.
- English comparisons: prepared English/Chinese rows and selected lists.
- PexBot: subscriptions/history and delegated action/result metadata, with an
  unknown edit count. It and English comparisons represent refreshed current
  state, not invented daily history.

## Website collection and presentation

`backend.website` independently refreshes recognized content, DYK, popularity,
news/history, and the maintenance catalogue, normally 24 hours after each
success. Select due work before initializing a Site or querying membership;
nothing due means no source requests. Valid empty results count as success.
Failures retain validated data and successful timestamps and retry hourly;
restart does not reset intervals. The schema-2 snapshot is bounded to 8 MiB.

`backend.public` validates saved dashboard, status, and daily-log inputs through
their owning readers. Flask/Waitress serves GET/HEAD only, with bounded reads,
requests, and concurrency; reject unsafe paths/files and disable production
debugging/reloading. `create_app` creates routing and caches only. Shared HTTP
security/compression belongs in `backend.support.http`. The public application
is independent of the hub's local `/dashboard.json` and `/healthz` diagnostics.

Pages work with ordinary links, GET forms, and native controls without
JavaScript. `/api/live` reads saved status only and renders existing live
regions, never website datasets or logs. The dashboard owns routine cards;
About shows environment/resources; logs shows compact server state even without
saved logs. Fixed assets live in `webservice/public_html/`. Omit confirmed-empty
sections but preserve their saved observations; unavailable data is not empty.

Chinese main-page history covers 100 UTC dates and upcoming quality content
30 days. Never invent carry-in first-appearance dates. Popularity retains its
actual measured date and bounded fallback. Enrich assessments through membership
joins, not per-article queries. Article-only site rank comes from the complete
filtered daily top list before membership joins; preserve source order and
verified exclusions. It is distinct from project position. View counts link to
Pageviews Analysis for thirty inclusive days ending on the actual source date,
but the displayed count represents that single day.

Introductions use the [Page Content Service summary endpoint][page-summaries].
Prepare original `zh`, `zh-hans`, and `zh-hant` text with `Accept-Language`,
up to three sentences and 360 characters plus an omission mark. Generic `zh`
keeps original text; English uses simplified text. Encode literal title spaces
as `%20` in these REST requests; underscore-equivalent URLs may yield different
cached extracts. Renderers escape saved plain text and show linked upstream
CC BY-SA 4.0 attribution separately from the source-code license.

The title/script-keyed `article-extracts-v2.parquet` cache is shared across a
refresh pass. Request each script once per distinct title, not per card.
Successful/confirmed-empty text lasts 24 hours; failures retain old text and
wait at least an hour. Bound size and rows, retain UTC source/attempt/use times,
and expire unused entries after 30 days. Script failures do not fail otherwise
valid reports. Contract changes invalidate affected report identities, not
maintenance data.

Keep sections stacked, with responsive sibling-card columns and no horizontal
page overflow. Preserve ordering, inline assessments, source metadata, and
all three interface languages. Popularity/DYK show at most six actual rows;
other lists render supplied rows directly. Display limits do not trim collected
data. Maintenance sorts the complete group before fifty-row pagination, with
stable page-ID ties and unknown dates/grades last in either direction. Seeded
random order remains stable across pages. Use saved source/category links and
the verified definitions/evidence in `backend/website/maintenance.json`;
requests never query wiki sources.

Contents navigation sits beneath the dashboard title and links only to visible
sections. Routine cards use observed starts/finishes, configured report links,
and actual scheduled executions; hide next execution while running. Successful
daily cards show confirmed report dates. Active progress requires a fresh
monitor session containing the routine start; stale sessions restore generic
labels. Never extrapolate phases, counts, or dates from clocks or logs.
Native popovers expose per-destination outcomes, independent timing, and UTC
timestamps while preserving keyboard access and unknown states.

Live clocks tick each second from a monotonic server-time anchor. Visible pages
synchronize every five seconds, without overlapping requests, with a ten-second
timeout and failure backoff up to thirty seconds. Hidden tabs pause and resume
with immediate synchronization. Update existing elements to preserve popovers,
focus, and scroll. Completed durations stay fixed; stale state/resources expire
after 120 seconds. Failed refreshes retain prior observations with a notice.
Bound rendered-view caches to sixty seconds and invalidate on saved-input
changes; logs and unseeded random views bypass them. Keep responses no-store
and source timestamps intact. Source changes require process replacement.

## Polars and Parquet

Use native expressions with explicit keys/schemas and preserve null versus
observed zero, including empty results. Prefer lazy scans for file-backed work
without changing eager source-adapter contracts. Python iteration belongs at
request/parser boundaries or small scalar mappings, not joins or aggregation.
Disposable tabular caches use Parquet. See the [Polars API][1],
[Pandas migration guide][2], and repository conventions in [AGENTS.md](../AGENTS.md).

## Logs, caches, and dry runs

Runtime paths are relative to the Toolforge working directory, never uploaded
source or `site-packages`:

- `logs/aranami.YYYY-MM-DD.log`: shared UTC diagnostics, retaining today and
  29 previous dates. Initialize handlers at runtime. Record graceful shutdown
  before waiting and completion after cleanup; forced termination may omit it.
  The website shows at most 100 lifecycle summaries from an 8 MiB scan, not
  request details or tracebacks; raw downloads retain diagnostics.
- `state/`: authoritative observed metadata, separate from logs and caches.
- `cache/`: disposable optimization. Deletion must not lose durable state or
  change output for identical inputs. Replace atomically; reject malformed data.
- `dry-run/`: one concise Markdown run summary plus flat, uniquely named
  `.wikitext` companions and preview report bundles. Include UTC timings,
  per-routine results, targets, summaries, tags, and companion links. Mixed
  failed/deferred runs are `partly success`. Never call PexBot's write endpoint
  during a preview; record the proposed action instead.

## Daily report contracts

### New pages

Retain the latest 100 complete UTC dates by default, through yesterday. Fill
missing dates, prune older ones, and reuse existing records without duplication.
Check the date window before discovery; a complete window receives no icon-only
edit. Filling gaps also refreshes icons and retained metadata.

For each missing half-open UTC interval, combine creations and replica revisions
tagged `mw-removed-redirect`; recent changes cannot cover the retained window.
Deduplicate by page ID, preload text, and apply identical namespace,
non-redirect, and keyword filters. Mark conversions ` — 自重定向页改写`.
Resolved rows end with `<!-- 页面ID 12345 · 创建时间 2026-08-05 11:22:33 -->`
using the earliest revision's UTC timestamp. Preserve/enrich these comments
without rediscovery. Unknown creation times are `未知`; unresolved identities
leave rows unchanged with a diagnostic.

### Pageviews

Use the Wikimedia Pageviews API for authoritative observations; cached coverage
never suppresses fresh requests or fills absent response dates. Fetch at least
the configured 800-day history, expanding for comparisons/catch-up.

Read and emit the sole checkpoint `<!-- report date YYYY-MM-DD -->`; missing
or conflicting recognized dates fail before building. Publish missing dates
chronologically as separate reports, advancing only through consecutive
confirmed results. Stop on required source failure, failed/unconfirmed save,
or superseded generation; resume from the last confirmed wiki marker.

Yesterday becomes eligible only when the actual invocation starts at/after
18:00 UTC; earlier missing days remain eligible before then. Report dates must
also precede the requested anchor, which cannot bypass readiness. Apply this
to live, manual, and dry runs. Never use an incomplete UTC day's total.

Probe candidate top lists and up to three leading articles before downloading
full histories. Missing dates try an earlier endpoint; transport failures
defer. Probe observations never replace histories. Download a fresh complete
batch through the selected endpoint and reject it as soon as more than 1% of
the full distinct population lacks the final day's record. Exactly 1% is
allowed; observed zero is valid, while empty responses/HTTP 504 count missing.
Stop before the next request after crossing the limit. Discard failed responses,
shorten by one day, and download afresh; try only endpoints beyond the checkpoint.
No passing endpoint means failure without advancing marker/healthy snapshot.

Only the accepted batch supplies sequential analyses and saves through its
endpoint. A shortened endpoint leaves hourly retries due. Bind provisional-zero
permission to that batch's site, membership, project, history bounds, and
threshold. Gaps after an article's first actual observation may contribute
provisional zeroes to totals/ranks/averages; continuous leading absence remains
missing and unranked. Denominators include all calendar days. Preserve raw
observations and current/previous observation flags; synthetic values do not
increase observed-article counts. Direct preparation without an accepted batch
retains strict guards and null unavailable totals. Later normal downloads may
recover gaps without rewinding published reports or retrying solely for history.

Fetch each applicable official all-access [site top list][pageviews-top] once
for single UTC days or complete calendar months. Apply the dashboard's verified
article filter, preserve order, number from one, and join with Polars. Use shared
site metadata, not per-page reads. Site ranks cover only the API's top 1,000;
retain nulls and omit unsupported/unlisted JSON ranks. Missing, malformed, empty,
404, or transport-failed required lists defer the complete report and healthy
cache replacement. A valid list containing only non-articles is valid empty
article data. The daily availability guard remains mandatory.

Rank changes compare the immediately preceding non-overlapping interval of the
same duration, including task forces, never yesterday's overlapping list.
`previous_rank` is null when unknown and `-1` for New only when full-history
first observation falls in the current interval after continuous leading
absence. Unknown previous rank alone is insufficient. Emit `views: null` for
intervals with no actual observation, keeping observed zero distinct without
changing provisional-zero calculations. Unknown population totals remain null.

Python emits one `{{#invoke:PJ:VG/HOT/v2|main|data=...}}` per list, with headings
outside invocations. `metadata` contains Chinese project identity, duration,
inclusive `[year, month, day]` bounds, total pages, and total views. `items`
contain title, quality/importance assessments (`未评`/`未知` defaults), views,
rank, previous rank, and optional site rank. Use actual task-force identities:
`电子游戏/我的世界` has no `工作组` suffix, independent of its display heading.
Display limits do not trim retained tables. Escape wikitext delimiters without
changing decoded JSON. Start at `data= {`, indent nested members only, put each
metadata field and each item on its own line, keep arrays inline, separate
structural closing braces, and close `} <!-- #endinvoke -->}}`.

The wiki owns `Module:PJ:VG/HOT/v2` and its `styles.css`; install and preview
both before enabling publication, with `sanitized-css` for TemplateStyles.
Lua decodes canonical data directly, without alias/validation layers. It owns
assessment classes, project visibility, Pageviews links, and rank trends.
Daily rows omit averages; site ranks emphasize total views, not averages.
Task forces omit importance badges; `电子游戏/宝可梦工作组` also omits class
badges. Keep temporary wiki artifacts outside the repository/source payload.

All Pageviews attempts share a process-wide fifty-requests/second limit with
at least twenty milliseconds between starts, including retries and website
reads; idle time grants no burst. Keep the adapter's one-attempt contract.
HTTP 504 skips/excludes that article, counts toward the guard, and preserves
saved history. Retry 502/503 up to three times over the same full range;
exhaustion defers. Later runs start fresh. Save healthy snapshots atomically
only after required guards/lists pass. Log concise progress/fallback/failure
summaries; individual HTTP attempts belong at debug level.

## Managed ranges and on-wiki configuration

Only paired `aranami` comments delimit generated content, for example:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...generated daily records...
<!-- aranami end="new-pages" -->
```

Preserve opening attributes and outside text. Validate settings before requests
or publication; reject missing, duplicate, reversed, or ambiguous markers.
Keep functional `<section begin="..."/>`/`<section end="..."/>` tags outside
managed ranges. Settings include new-page retention, assessment categories,
and Pageviews `history-days`/`missing-percent`; cap the effective Pageviews
threshold at 1% without rewriting the stored attribute.

## Wikitext, templates, and site semantics

Use `mwparserfromhell` for non-trivial wikitext, preserving parser objects
rather than regex-parsing nested syntax. Before changing template calls or
extractor mappings, verify that wiki's source, documentation, redirects,
invoked modules/configuration, parameters, aliases, and emitted markup.
Record audited revisions where the contract needs evidence.

Use `pywikibot.Page` and local namespace/title rules for template identity
and talk-page conversion; Chinese `PJ:` may mean WikiProject, not Template.
Interpret Article history and DYK separately by wiki using the actual
[Chinese][3] and [English][4] configurations. English GAR can record GA listing;
Chinese GAR means delisting review. Accept only locally supported action/results.

Unknown dates remain Python `None`/Polars null, sort explicitly, and stay out
of dated statistics. Prefer replicas where sufficient and preload Pywikibot
reads. The backend interprets sources; updater rendering/publication must not
duplicate those decisions or overwrite a changed destination.

### Edit summaries

Use English `EditSummary` output capped at 255 UTF-8 bytes. Shorten whole
clauses/mentions, never split wiki links or `«...»`. Primary titles use the
central mention formatter; bilingual mentions are
`«[[:en:Title]]» ([[Chinese title]])`, preserving disambiguation/transitions.
Every live/dry summary ends exactly `([[toolforge:Aranami|Aranami]])`, without
a final full stop or duplicate attribution. Reserve suffix space through
`with_attribution`; versions and timings belong in metadata/logs.

Dated summaries begin `Updated for <date>.` with compact English dates.
Pageviews groups periods beneath each leader in a `Top:` sentence, links each
title once, and keeps places gained beside the period. Omit movement for
unchanged leaders/unknown previous ranks. If over budget, drop yearly, seasonal,
then remaining longest periods while preserving complete mentions. New-page
summaries show dates/counts only when the latest list was filled; older-only
backfills use `Updated class icons.` and describe older records.

### Membership and DYK changes

Membership snapshots use stable page IDs in Parquet, not wiki output, to track
renames, assessments, and DYK transitions. Validate against exact destination
text and advance only after confirmed publication. Dry runs may seed originals
but cannot advance proposed membership. Missing/stale snapshots use titles or
English Wikidata IDs without changing body semantics.

Summaries report current totals and grouped additions/removals, without advice.
DYK counts completed articles and active nominees separately, including repeat
nominees in both. A repeat passes only with a new completion date; a departing
candidate absent from completed entries fails. Unchanged historical completion
does not distinguish failed repeats from delayed cleanup. Keep removed historical
entries distinct from failed nominees. Cache talk-page state by revision ID.
Matching membership, assessments, and dates preserve exact published text;
sampling dates/statistical comments alone do not justify edits.

### English comparison reports

Match English page IDs through renames; additions/removals use `[[:en:Title]]`.
Quality combines FA/FL/GA by latest English promotion/listing date descending,
year groups, then English-title ties; null dates form final `年份未知`.
Resolved rows retain ISO date comments. Cache null and resolved dates against
class, talk-page ID/revision, and aliases; preload only stale discussions and
retain the revision actually fetched.

Quality updates required `count_all`, `count_fa`, `count_fl`, `count_ga`, and
`body` managed ranges; importance preserves alphabetical sections/counters.
Summaries begin with article and assessment totals, followed by changes within
the shared attribution budget. Use `listed`/`delisted` for GA and
`prompted`/`removed` for FA/FL, retaining previous class on transitions.
Behavior follows concrete report strategy/definition, not report names;
backend comparisons supply facts and updater formatters express them.

## System-change review

Trace producers, persisted formats, readers, and failure paths together.
Preserve unknowns, source times, output independence, preview isolation,
serialization, and storage-before-save ordering. Cover empty/malformed inputs,
concurrency, failed/unconfirmed saves, and restart behavior where relevant.
Update all callers/fixtures for contract changes, then run
[offline verification](development.md#verification-and-releases). Release
policy stays in [AGENTS.md](../AGENTS.md); deployment/recovery stays in
[Administration](administration.md).

[1]: https://docs.pola.rs/api/python/stable/reference/index.html
[2]: https://docs.pola.rs/user-guide/migration/pandas/
[3]: https://zh.wikipedia.org/wiki/Module:Article_history/config
[4]: https://en.wikipedia.org/wiki/Module:Article_history/config
[pageviews-top]: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/examples/project-metrics.html#most-viewed-pages
[page-summaries]: https://www.mediawiki.org/wiki/Page_Content_Service#/page/summary
