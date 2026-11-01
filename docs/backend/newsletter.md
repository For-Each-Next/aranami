# Newsletter collection

Use these contracts when changing newsletter collection, source evidence, or
retained observations. Collection saves structured facts; queries render them
as wikitext for manual publication. The
[newsletter interface](../webservice/newsletter.md) explains issue selection,
annual data, and copying.

Quarterly calendar identities live in `shared.newsletter`; `issue=2027-01`
selects issue 29 for October–December 2026. Backend owns source
facts and storage; `updater.services.newsletter` projects validated facts into
wikitext; webservice owns HTML presentation. The public controller reads saved
observations without collecting sources, publishing wiki text, or writing
runtime data. See [Architecture](../architecture.md) for component boundaries.

## Data, cache, and archive lifecycle

All paths below are relative to the runtime home unless a configured cache
directory is shown. Clear disposable caches only after stopping active work;
the next collection rebuilds them without losing retained report evidence.

| Location | Retained data | Effect of clearing |
|---|---|---|
| `<cache_directory>/newsletter-revisions-v1.parquet` | Raw Chinese Wikipedia text keyed by exact revision ID | Safe: source text is fetched again after fresh historical revision checks |
| `cache/today-in-history-v1.parquet` | Daily OTD source evidence for the last 100 completed UTC dates | Safe: historical sources are fetched again; saved reports remain intact |
| `reports/data/articles/milestones.parquet` | Project reviews and homepage appearances, source links, attribution, and per-field coverage | Durable inventory: unknown fields remain incomplete |
| `reports/data/articles/history.parquet` | Versioned talk snapshots and nested recorded events | Durable evidence: never clear as a cache |
| `reports/data/articles/authorship.parquet` | Listing revisions, all contributor token/share statistics, and tied leading authors | Durable evidence: preserve successful observations |
| `reports/data/newsletter/observations/current.json` and `YYYYMMDD/` archives | Current prepared facts and immutable earlier observations | Durable data consumed by newsletter generation |
| `backups/report-layout/` | Original legacy evidence after validated layout migration | Durable recovery copies, not active stores |
| `reports/records/newsletter/` | Independent daily JSON facts and date catalogs | Durable history: deleting it loses retained date selections |

The revision-text cache avoids downloading unchanged source bodies. Every
lookup first selects historical revision metadata at the requested cutoff and
matches its fresh SHA-1 against the cached text. Hidden or unavailable content
never falls back to a cached body. Because only raw text is cached, parsing
changes do not require invalidation.

Entries expire thirty days after their last use. Reads allow at most 5,000
rows, 16 MiB on disk and in the loaded frame, and 1,000,000 bytes per source
text. Saved entries fit an 8 MiB estimated budget, newest uses first. Invalid
or corrupt files become cache misses. Atomic writes are best effort after
successful fact collection and daily archival; cache-write failures do not
discard complete report facts. Dry runs use their separate configured cache
directory and cannot replace production cache entries.

## Acquisition and saved-data boundary

`backend.website.newsletter_sources.refresh_newsletter_data` acquires sources
and validates complete newsletter facts before saving them through
`backend.support.newsletter_observations`. Each generation stores
`data_version`, `captured_at`, a SHA-256 `digest`, and nested `facts`. The
matching `current.json` is replaced only after its dated observation
has been written. Readers validate generation metadata, facts,
and digest together; a damaged durable observation fails rather than becoming
an empty replacement.

`backend.website.newsletter.read_newsletter_data` validates and reads this
selected generation. It does not make MediaWiki requests. Wikitext generation
uses saved facts; request handlers never trigger acquisition. The website
snapshot is a presentation cache, not the sole durable source for newsletter
facts. Collection, durable data, and newsletter projection remain separate
boundaries even when the hub coordinates them in one scheduled pass.

Migration merges every retained legacy talk revision into the shared article
history store under the store's source-version keys. It leaves the original
`reports/newsletter-history-v1.parquet` byte file untouched. Existing daily
archives retain their original facts and capture times.
Earlier `reports/newsletter-data-v2/history.parquet` and `authorship.parquet`
are also preserved migration sources, while the active shared stores live
under `reports/data/articles/`. The newsletter-specific directory now
holds prepared observations rather than owning the common article database.

## Daily observations

Daily collection uses fresh milestone records and page lists to retain each
issue's first successful observation for the inclusive data date
`observation_end - 1 day`, skipping unelapsed quarters. Each
`reports/records/newsletter/<issue>/YYYYMMDD/observation.json` is a complete,
independent snapshot with validated facts, actual capture time, and a digest.
Today's file requires no previous history file, delta, or predecessor to read
or render. An atomic, locked catalog lists available dates; it is an index,
not a chain between snapshots. Nested records remain JSON rather than tabular
caches. A failed archive write prevents replacing the latest newsletter
presentation while independent reports continue normally. Missing historical
days remain absent instead of being reconstructed from later data.

New schema-2 captures contain no generated wikitext, rendered-page objects,
page artifacts, or output manifests. Query handlers render only the selected
snapshot's facts using the current updater renderer, without source requests
or writes. Formatting changes therefore apply to historical views while
their source facts and capture timestamps stay fixed. Legacy schema-1 facts
remain readable after validating their retained JSON evidence; generated
output files are not required. The explicit report-layout migration writes
facts-only records and preserves the original evidence in its backup.
With the hub and web service stopped, the layout workflow also converts
schema-1 histories already in the active directory. It validates a staged
facts-only copy, moves the original to
`backups/report-layout/newsletter-wikitext/`, and selects the new archive.
`state/newsletter-structured.json` records its checkpoint; interrupted work
resumes from the preserved source even if disposable staging has been cleared.
Conversion runs only through explicit maintenance, never a page request or
normal startup.

January's annual feature has a separate daily stream under
`<issue>/annual-feature/YYYYMMDD/metadata.json`, with complete annual facts and
its own capture index. The interface shares one standard date picker across
annual and quarterly pages. The annual range is January 1–December 31 of the
reviewed year: 365 dates, or 366 in a leap year, once actually captured.
Main, quality, announcements, and ordinary editorial pages remain bounded by
their reporting quarter, which has 90, 91, or 92 days. The annual stream does
not make a whole quarterly issue available in January of the reviewed year.

`save_feature(issue, year_review, generated_at=...)` stores the first
actual annual capture for a completed date. `read_feature` and `feature_dates`
only read retained observations. `save(...)` handles annual facts before the
separate quarterly availability check. The query frontend calls
`render_annual_feature`; storage never imports rendering code.
Missing historical days remain absent. The website's `feature_date`
selection is independent of the quarterly `date` selection.

## Source cutoffs and quarterly facts

The independent daily acquisition in `backend.website.newsletter_sources`
fetches sources for the current publication quarter and its successor. It prepares the next
issue while the reporting quarter is still in progress, with an explicit
exclusive observation cutoff through the latest completed UTC day. Historical
reads, DYK, quality revisions, awards, curated new pages, and homepage
appearances use that same cutoff. Quarterly completion is separate from data
availability. The overview retains content links even when the pages have not
been created; January links the annual feature without a separate interview.
Supplemental current article totals use a fresh
membership count with their own observation timestamp, rather than a historical
quarter-end label. Assessment totals use internal project snapshots and current
membership rather than parsing the assessment log or recognized-content pages.
Retain each internal snapshot's actual collection time; a delayed observation
must not be labeled as an exact quarter-end count. Reuse saved overview counts
only for the same issue and observation boundary when no newer source exists.
Missing daily records retain their confirmed minimum count and coverage gaps
internally. Copied drafts retain all three quarter months, using the registered
counts available at collection time without minimum-count or gap annotations.
Unelapsed months therefore have zero registered articles in the draft without
changing their unknown observation state in saved facts.
Join curated registered titles to internal assessment observations for monthly
grade counts and the quarterly pie chart. Prefer exact titles, then batched
canonical titles and page IDs across renames. Current assessments apply only to
open quarters; closed quarters require their exact retained boundary snapshot
and its actual collection time. Registered articles without a valid project
class count as unassessed, including subjects absent from the project snapshot.
Older saved unknown-class counts also render as unassessed; this reporting
category does not assert that a project banner is present.
Copied grade summaries combine AL/A, BL/B, CL/C, List/Start, and SL/Stub at
their common assessment level, retaining the separate source classes in saved
facts. Featured articles and featured lists keep their distinct designations.
Pie-chart JSON keeps each slice object on one line.
Grade totals reconcile to registered counts even when
daily coverage is incomplete, and missing historical observations omit the
chart rather than borrowing later grades.
Ordinary features and interviews provide empty editorial scaffolds. January
uses independently prepared facts for the preceding calendar year, never
quarterly counts relabeled as annual. Its structure follows the January 2026
feature at revision 91016278: the introduction contains exactly four ordered
level-four data sections, `內容評審`, `新章推薦`, `条目报告`, and `榮譽表彰`.
The `新章推薦` label is an intentional correction to the reference.
Missing data stays blank in copied wikitext; partial annual statistics carry
a provisional marker and rolling pageview figures state their actual dates.
Only the two editor-comment invitations use buttons, both with the verified
reflection preload; introduction and data slots have no invitation buttons.
Annual ACG awards include all positive score gains and mark first-year scorers
with `N`; quarterly awards retain fifth-place ties and the ten-point newcomer
threshold. Community honors remain blank for editors. Unsupported DYK subject
classifications leave their reference categories blank, without assigning
unknown subjects to `其他`. Annual DYK game years prefer direct article
categories matching `\d{4}年[电電]子[游遊][戏戲]`, taking the earliest
supported year (1900–2099), with explicit infobox release dates as a fallback.
These categories and dates come from article revisions at the annual cutoff.
The five nested game bands end with `其他` for known years outside the four
dated bands. Current project membership places remaining articles under
`角色與元素` (`虚构角色`), `公司與人物` (`传记` or `公司`), or `音樂`
(`音乐`). Music membership uses the exact database project name `音乐`;
other spellings are ignored during acquisition and grouping.
The priority is fictional character, biography, company, then music,
after dated games. A single PageAssessments join intersects these projects with
`电子游戏`; current article names come from the already collected project
membership. It reads no topic pages, talk metadata, or full outside-project
membership lists. All overlapping memberships are retained in priority order.
Each article appears once;
unmatched subjects and games without a known year remain under `待分類`.
The saved `projects` list distinguishes unavailable membership (null) from
observed absence (empty), and `dyk_projects_observed_at` records its actual
collection time separately from historical article metadata. Backfills retain
these observations; existing daily draft archives remain unchanged.
Quality and DYK category lists follow milestone dates, earliest first, with
stable title ties. Editor article lists group FA/FL together, then A/AL, GA,
and Bplus, and order dates within each grade. Month boundaries carry invisible
`<!-- 1月 -->`-style comments for editors.

Annual statistics retain the January 2026 reference's numerical prose for
promotions, DYK, article totals, net/daily growth, pageviews, revisions, and
award participation. Unknown variables remain blank. Partial current figures
carry a provisional marker. The ACG summary counts scoring editors, first-time
scorers, and first-time scorers reaching the ten-point first award threshold.

`newsletter_statistics` contains audited historical data from the January
2026 feature's three image descriptions, credited to SuperGrey and other
contributors under CC BY-SA 4.0. Graph 1 supplies annual GA additions from
2008–2025; Graph 2 supplies quarterly cumulative DYK counts from 2006–2025;
Graph 3 and its January 2025 predecessor supply 2025/2024 assessment counts.
Current prepared milestones extend supported series; an exact year-end
assessment snapshot supplies closed-year quality counts. Wikitext uses the
verified server-rendered `Graph:Chart` contract for bar, area, and pie charts,
without companion data tables or source prose. Assessment pies show only the
reviewed year and its predecessor. Legend entries include each grade and its
count; numeric slice labels use black text through
`showValues=fontcolor:#000000`, verified against `Module:Graph` revision
95334515 and `Module:Graph/SVGRenderer` revision 95338958. Source provenance
and attribution remain in the collection contract.

`dyk_new_quarters` retains completed quarter ends and cumulative first-time DYK
article counts since the reviewed year's January 1. Select each title's earliest
recorded DYK date before counting; repeat appearances and earlier-year subjects
do not increase these counts. Quarter ends must precede the exclusive observation
date. Missing histories remain null; observed empty histories retain zeroes.
The DYK chart adds each supported quarterly count to its historical baseline,
without adding an incomplete observation-day point. The trend chart uses the
verified `Template:Image frame` content, align, width, and caption parameters
(revision 86263920) to float a 400-pixel thumbnail on the right.

Monthly winners come from historical revisions of the project's popular
articles page. Only exact calendar-month intervals fill a month slot. Both
legacy HOT templates and HOT/v2 JSON retain their actual intervals, counts,
and ranked titles. Each month's optional `revision_id` comes directly from
the original wiki reader's source URL immediately after that historical read.
It never comes from a reporting boundary or accumulated provenance. Older
observations without this field remain valid and keep plain month labels.
The latest rolling annual report can fill the current
provisional draft, with its actual start/end printed explicitly. Bounded
January 2/3 captures are revisited to find delayed calendar-year reports.
Annual total pageviews render as whole, comma-grouped units of ten thousand;
saved observations retain the exact measured integer counts.
Annual revision counts use one read-only replica aggregation over current
project main-namespace articles and the exact half-open UTC year interval;
creation edits and all available revision metadata count. Full rankings stay saved
while copied drafts display their first 25 entries. January issue collection
continues after January 1 so late report updates can appear in the latest
view; earlier saved daily observations remain immutable.

Introduction and editor commentary remain editorial work. Historical queries
render the current page structure using only selected saved observations.
