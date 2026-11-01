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

Daily issue eligibility comes from `Module:VGNL/data` (audited data revision
93292299 and module revision 92339407). A negative date year means cancelled;
a missing third date value means draft; a concrete day means published.
Publication dates never change the reporting interval. Missing future issues
extend the registry's quarterly sequence as drafts. January's annual review
becomes eligible on January 1 of the preceding year; other issues begin at
their quarter start. All eligible drafts refresh daily until publication,
including delayed issues after quarter rollover. Previously captured published
or cancelled issues remain frozen and readable.

The publication reader accepts bounded Lua literals without executing code.
Missing or malformed registry evidence fails the refresh and retains the prior
successful generation. Saved lifecycle metadata supplies HTTP defaults and
issue choices; requests never read the wiki. Immutable reporting-date archives
retain their first capture, while current observations can continue improving
through publication after the reporting period ends.

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

Hourly milestone acquisition supplements recorded talk-page history with the
dated Bplus register at `WikiProject:电子游戏/乙上级条目` (row contract verified
against revision 91951615). Only explicit promotion dates in its current section
become events, with the source title, revision, and permalink retained. Present
quality grades do not filter these observations. Later register absence never
removes a promotion or invents a withdrawal. Conflicting known dates require
review; a later promotion needs an explicitly recorded intervening removal.
Unknown current-assessment dates can be resolved without inventing authorship.
The atomic milestone merge preserves former quality and removal history.
Newsletter preparation consumes this saved Parquet evidence only.

Talk-history acquisition normally reuses an unchanged oldid with the current
parser contract. After each UTC month ends, the first subsequent acquisition
re-reads each current candidate talk page once, even when its oldid is unchanged.
The retained `observed_at` timestamp records that month's completed observation;
later passes in the same month reuse it. No month is added to source identities
or parser contracts, and individual transcluded template bodies are not tracked.
Successful batches of at most 200 pages are merged atomically, so interrupted
acquisition retries only unfinished pages. Failed or missing batches never
advance their observation dates. Rechecking the same oldid updates its observation
without duplicating that source version; earlier oldids remain retained.
Logs report reused pages, month-end rechecks, and saved batch progress.

Normal recognition acquisition also follows verified local main-namespace
redirects into the project, including section targets and bounded chains.
Redirect subjects with independent talks retain their own identities,
source revisions and historical recognitions. This positive association keeps
their evidence in scope; present assessments still come only from current
project members. Failed or incomplete discovery never advances removal clocks.

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

The `backend.newsletter` package owns collection, schema validation, source
layouts, quarterly/annual analysis, and storage. `shared.sources.wiki` owns
read-only wiki access and per-pass source evidence, with exact historical text
in `shared.sources.revisions`. Wiki projection remains in updater; webservice
owns the public HTML and assets.

`backend.newsletter.refresh.collect_newsletter` acquires sources
and validates complete newsletter facts before saving them through
`backend.newsletter.observations`. Each generation stores
`data_version`, `captured_at`, a SHA-256 `digest`, and nested `facts`. The
matching `current.json` is replaced only after its dated observation
has been written. Readers validate generation metadata, facts,
and digest together; a damaged durable observation fails rather than becoming
an empty replacement.

`backend.newsletter.schema.read_newsletter_data` validates and reads this
selected generation. It does not make MediaWiki requests. Wikitext generation
uses saved facts; request handlers never trigger acquisition. The website
snapshot retains newsletter availability; requests read the canonical current
observation or an immutable daily archive directly. Collection, durable data, and newsletter projection remain separate
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

The independent daily acquisition in `backend.newsletter.collection`
refreshes every started, unpublished issue selected by the publication plan,
including delayed drafts from earlier quarters. It uses an explicit exclusive
observation cutoff through the latest completed UTC day, capped at each
reporting period's end. Historical
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
Missing annual data uses complete placeholder sentences; partial statistics
carry inline cutoff-date markers. Annual popularity uses calendar-year
observations, never a rolling year relabeled as the reviewed year.
Only the two editor-comment invitations use buttons, both with the verified
reflection preload; introduction and data slots have no invitation buttons.
The invitations use `Template:Clickable button 2` revision 61200765 and
`Module:Clickable button 2` revision 61200823, with explicit blue primary
styling so their appearance does not depend on global Codex resource loading.
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
and Bplus, and order dates within each grade. Each article with a known date
ends with an invisible `<!-- 0904 -->`-style MMDD comment for editors.
Quarterly quality lists use the same date markers. Announcement homepage
lists keep visible dates without invisible comments. Unknown first
appearance dates remain unmarked.

Annual statistics retain the January 2026 reference's numerical prose for
promotions, DYK, article totals, net/daily growth, pageviews, revisions, and
award participation. Unknown figures use complete placeholder sentences.
Partial current figures carry a dated inline `Memo` notice. Comparisons
state figures and plain trends. The ACG summary counts scoring editors, first-time
scorers, and first-time scorers reaching the ten-point first award threshold.

`quality_year_ends` selects articles with a recorded quality promotion during
each year and classifies each once by its grade at that year's boundary,
retaining separate FA, FL, A, AL, GA, and Bplus values. This is an annual
promotion cohort, not the project's cumulative high-quality inventory.
Collection supplies the full saved Parquet inventory separately from the
current-membership event frame; chart preparation and rendering make no source
requests. Source acquisition, including Bplus evidence, updates the inventory
before these saved facts are consumed.
Repeated evidence counts once per article in each year's cohort. Same-year
promotions select one year-end grade, so an A article promoted to FA during
that year is counted as FA. A matching removal before that year's boundary
excludes the article; later removals do not change earlier years. Unknown
action dates have no inferred year; absent history
stays null. Annual lists and author credits likewise include only the latest
surviving promotion at the cutoff. A current or exact-boundary assessment can
exclude a conflicting final-year grade; missing assessments leave recorded
history intact. Current grades never rewrite earlier annual cohorts.
Legacy `quality_years` observations remain readable but are not interpreted
as year-end counts.

The quality chart uses native `Graph:Chart` parameters with `type=stackedrect`.
It stacks FA+FL, A+AL, GA, and Bplus from bottom to top, omitting the A layer
when its entire series is zero. The template's default legend follows the same
order in its native position. The
vertical axis starts at zero and reaches at least 50; higher observed totals
extend its upper limit to the next multiple of ten. Category
labels contain only years because native stacked charts do not support total
labels above bars or interpret line-break markup in labels.
Native hover shows each stacked group's value,
and native chart animation stays enabled. Generated wikitext contains no expanded
SVG, explicit Graph styles, or custom overlays. This contract was verified
against `Module:Graph` revision 95346885 and `Module:Graph/SVGRenderer` revision
95350043. The chart sits directly in one centered container, using native
responsive sizing with horizontal scrolling for remaining overflow.
Missing saved quality history produces no chart.

`backend.newsletter.statistics` retains audited quarterly cumulative DYK data from the
January 2026 feature's second image description and 2025/2024 assessment data
from its third image and January 2025 predecessor, credited to SuperGrey and
other contributors under CC BY-SA 4.0. Current milestones extend supported
series; exact year-end snapshots supply closed-year quality counts. Wikitext uses the
verified server-rendered `Graph:Chart` contract for bar, area, and pie charts,
without companion data tables or source prose. Assessment pies show only the
reviewed year and its predecessor. Legend entries include each grade and its
count in regular-weight text, without percentages. Native value labels stay
disabled with `showValues=false`: `Module:Graph/ChartRenderer` fixes them in
bold and applies an unconfigurable 3.5 percent threshold. Only exact shares
of at least five percent receive a one-decimal static inner label in regular
black text. Raw counts still determine native slice geometry and colors.
The complete pie, including its legend and padding, fits a 400-pixel frame.
The plot width subtracts the native legend width, 28 pixels of native padding,
and six pixels of text clearance from that budget; its height remains 250
pixels. Static label positions and radii follow that centered native geometry.
They never intercept native interaction.
The template call remains unexpanded, and no
custom SVG or hover targets are generated.
Both pies use one wrapping flex container with a single fixed-width div per
chart. The shared container scrolls when a single pie cannot fit.
This contract was verified against `Module:Graph` revision 95346885 and
`Module:Graph/ChartRenderer` revision 95358684. Source provenance and attribution
remain in the collection contract.

`dyk_new_quarters` retains completed quarter ends and cumulative first-time DYK
article counts since the reviewed year's January 1. Select each title's earliest
recorded DYK date before counting; repeat appearances and earlier-year subjects
do not increase these counts. Quarter ends must precede the exclusive observation
date. Missing histories remain null; observed empty histories retain zeroes.
`dyk_current` separately retains the latest inclusive UTC observation date and
cumulative first-time count, capped at December 31. It includes milestones
already observed that day. Missing history stays null; earlier saved facts may
omit this field. The DYK chart adds supported quarter counts to its historical
baseline, then appends the current point when its date is later. December 31
appears once, including after the fourth quarter is complete. The chart uses the
verified `Template:Image frame` content, align, width, and caption parameters
(revision 86263920) to float a 400-pixel thumbnail on the right. Its caption
contains the cutoff notice without a repeated introductory sentence in the text.

Monthly winners come from historical revisions of the project's popular
articles page. Only exact calendar-month intervals fill a month slot. Both
legacy HOT templates and HOT/v2 JSON retain their actual intervals, counts,
and ranked titles. Collection examines the first four UTC days after month
completion, retaining exact revision timestamps and text. This finds reports
published after midnight even when a later daily report overwrote them that
same day. The window is clamped to acquisition time; a completed month can
appear on the following month's first day as soon as its report exists.
Each month's optional `revision_id` identifies the exact matching historical
revision. Readers without revision iteration use bounded daily snapshots and
their immediate source URLs. It never comes from a reporting boundary or
accumulated provenance. Older
observations without this field remain valid and keep plain month labels.
Normal Pageviews collection also saves complete current- and previous-calendar-
year rankings from its existing fresh histories, without extra article requests.
Each newsletter collection also recalculates calendar-year rankings from the
validated daily Pageviews Parquet for its current article population. It selects
January 1 through the latest retained completed date before the acquisition
cutoff. Missing articles and truncated coverage keep project totals unknown;
explicit observed zeroes remain zero. Equally dated or newer recalculations
replace the saved annual results; missing or older daily data retains the
validated durable results. Website requests read saved facts without collecting
traffic or recalculating. Absent calendar-year results remain unavailable rather
than using rolling-year traffic. The introduction names only
the reviewed year, while an inline Memo preserves the actual provisional cutoff.
Unknown article totals leave the project total unknown, without suppressing
measured ranking entries. Bounded January 2/3 captures are revisited to find
delayed calendar-year wiki reports.
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

Normal observation and daily archive readers accept only canonical inline
facts and compact date folders. Explicit migration operations retain access to
older digest selectors, ISO date folders, and schema-1 rendered records in
preserved backups. Requests and ordinary startup never run those conversions.
Both newsletter stores use the shared atomic JSON writer: it flushes the file
before replacement and syncs the destination directory before returning.
