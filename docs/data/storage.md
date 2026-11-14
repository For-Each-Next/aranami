# Saved data

Collection always precedes analysis and publication. Collectors query page
identities, fetch missing exact revisions in batches, parse metadata, and save
complete calculated results. Wiki projections and HTTP views read those saved
Parquet results without source API/SQL requests or raw revision parsing. Wiki
publication still verifies and saves its destination through MediaWiki.

## Runtime tree and retention

This service serves Chinese Wikipedia's Video games project. Wiki/project
identities remain in metadata; they do not add directory levels. Assuming the
clock is **2026-11-14 01:05:03 UTC**, the latest midnight observation describes
November 13 and the latest completed hourly observation describes 00:00–01:00:

```text
data/
├── archive/                         permanent Chinese wpvg page lists
│   ├── 20261105.parquet
│   ├── 20261106.parquet
│   └── 20261113.parquet
├── daily/                           daily report data and other page lists
│   ├── enwiki-20261113.parquet
│   ├── acg-20261113.parquet
│   ├── pokemon-20261113.parquet
│   ├── minecraft-20261113.parquet
│   ├── nintendo-20261113.parquet
│   ├── sega-20261113.parquet
│   ├── square-enix-20261113.parquet
│   ├── mihoyo-20261113.parquet
│   ├── pageviews.parquet            prepared web traffic report
│   ├── newsletter.parquet           prepared collection envelope
│   ├── newsletter-traffic.parquet   annual/monthly totals prepared after 19:00
│   ├── pageviews-<identity>/        detailed daily traffic, two generations
│   │   ├── latest.json
│   │   └── pageview-data-<time>/
│   │       ├── daily.parquet
│   │       ├── members.parquet
│   │       ├── coverage.parquet
│   │       └── manifest.json
│   ├── <report-family>-<identity>/  prepared wiki reports, two generations
│   ├── newsletter/                 retained newsletter evidence
│   │   ├── observations/current.parquet
│   │   ├── observations/<day>/observation-<time>.parquet
│   │   ├── historical/
│   │   └── popularity/
│   └── newsletter-drafts/<issue>/
│       ├── catalog.parquet
│       ├── <day>/observation.parquet
│       └── annual-feature/<day>/metadata.parquet
├── hourly/
│   ├── 2026111323.parquet           latest two completed wpvg hours
│   ├── 2026111400.parquet
│   ├── <project>-2026111400.parquet additional Chinese project membership
│   ├── categories-2026111400.parquet
│   ├── article-history.parquet     complete retained parsed history
│   ├── article-authorship.parquet
│   ├── article-milestones.parquet
│   ├── current-assessments.parquet
│   ├── scope.parquet
│   ├── dashboard.parquet           ready-to-render dashboard
│   ├── reviews.parquet
│   ├── recognized.parquet
│   ├── dyk.parquet
│   ├── news_history.parquet
│   ├── backlog.parquet
│   ├── consistency/                prepared findings and repair proposals
│   └── <analysis-family>-<identity>/
└── revisions/
    ├── 00/                         revision_id % 256 == 0
    ├── 01/
    │   ├── 257.wikitext
    │   └── 257.json
    ├── …
    └── FF/                         revision_id % 256 == 255
```

This is a naming example, not a claim that every illustrated date or revision
exists. Additional report tables are enumerated by each generation's manifest.
The identity suffix is the full SHA-256 of the logical report identity; the
manifest retains its readable wiki, project and destination. Unknown project
names use a bounded `project-<digest>-<day>.parquet` name. Date-bearing page-list
files sit directly in their owning directory.

Only the complete **Chinese wpvg** daily page list belongs in `archive`.
Those files are historical evidence and are never pruned. English full project
lists and other projects belong in `daily`. `daily` and `hourly` describe update
cadence, not disposability: complete parsed history, milestones and attribution
proofs remain durable. Temporary hourly page/category observations retain the
latest two periods. Mutable report bundles retain current and previous complete
generations. Failed writes preserve the prior selection.

Raw revisions use uppercase hexadecimal shards `00`–`FF`; `0x` adds no value,
and a separate index is unnecessary. Both subject and talk text use this store.
A successful midnight pass prunes raw records absent from **both** the current
and preceding consecutive daily Chinese page lists. Missing either list blocks
pruning. Historical parsed evidence and permanent page-list archives are not
part of this cleanup. Preview data stays under `dry-run/data/`.

## Page-list schema

Both daily and hourly page lists store the following physical columns. The data
reader can expose legacy internal aliases to existing calculations; those
aliases do not change the on-disk schema.

| Column | Stored dtype | Meaning |
|---|---|---|
| `page_id` | Int64 | Positive subject identity, unique within a file |
| `page_namespace` | Int64 | Numeric namespace |
| `page_title` | String | Database title without namespace prefix |
| `page_rev_latest` | nullable Int64 | Exact observed subject revision |
| `page_rev_timestamp` | nullable Datetime(us, UTC) | Timestamp of that revision |
| `talk_page_id` | nullable Int64 | Associated talk-page identity |
| `talk_page_latest` | nullable Int64 | Exact observed talk revision |
| `pa_class` | nullable Enum | Original project class, preserving extra labels |
| `pa_importance` | nullable Enum | Original project importance |
| `pa_taskforces` | nullable List[Enum] | Sorted configured taskforce names |
| `page_is_redirect` | nullable Boolean | `true`/`false`; historical unknown is null |
| `page_length` | nullable Int64 | Source byte length, independent of prose metrics |

Taskforce Enum vocabulary is saved in `metadata.taskforce_projects` (daily) or
`aranami.taskforce_projects` (hourly). An empty list means confirmed no taskforce;
null means not observed. Class/importance profiles and their Enum declarations
are also retained. Unknown historical revision IDs remain null; migration never
substitutes today's revisions or creates an observation for a missing day.

The parent and its configured child projects are captured with **one SQL
statement**, joining subject pages, current revision timestamps, corresponding
talk pages and project assessments. Child assessments remain their own values.
Taskforce membership joins by stable page ID, so subject renames retain it.
An empty/unavailable parent query cannot create false mass departures.

At midnight, a separate replica heartbeat readiness check must reach the UTC
boundary before source capture. A failed/lagged collection retries promptly;
dependent reports wait and the website retains its last complete data. Actual
query start/finish and scheduled midnight are separate metadata: delayed
replicas cannot reconstruct the exact historical midnight state. Hourly scans
accept lag and record the available state. At 01:00, the hourly label is `00`.
At 00:00, the daily label is yesterday. Midnight hourly data can represent the
same facts as that daily file; it need not duplicate source requests.

The embedded `aranami` manifest records schema, family, application version,
logical identity, generation, rows, encoded columns and a sorted-content digest.
The storage envelope adds the actual write time. A same-day retry reuses the
first complete file. Readers reject corrupt durable files and preserve gaps.

## Revision and report keys

Source metadata decides whether content needs fetching. Reuse the stored body
for an unchanged revision; batch only missing IDs. Revision metadata JSON
contains `schema`, `wiki`, `revision_id`, `page_id`, `title`, `timestamp`,
`parent_id`, `sha1`, `source_bytes`, `status`, and versioned `analyses`.
Unavailable/suppressed revisions retain an explicit unavailable outcome and no
old body. Available bodies are checked against their saved digest.

The initial text-statistics analysis records `raw_characters`,
`raw_han_characters` and `source_bytes`. Domain parsers then save article history,
prose, listing and attribution evidence in their owned Parquet tables. Parser
contract changes can reparse retained bodies without downloading them again.
Routine newsletter updates reuse already accepted history, including oldid
87963056, until SQL reports a changed talk identity/revision or evidence is absent.

Every newly calculated report records contributing page inputs with columns
`wiki`, `project`, `page_id`, `page_latest`, `talk_page_id`, `talk_page_latest`.
The composite key includes `page_id` and whichever subject/talk revisions the
calculation uses; talk identity is retained as well. Aggregate reports retain
these input rows alongside their summary tables. Config/parser versions and
report periods supplement revision keys. Removed pages leave current cohorts;
unique historical events remain retained.

## Fileset consumers and table schemas

| Fileset | Main schema/identity | Used by |
|---|---|---|
| `archive/<day>.parquet` | Page-list schema above | Daily comparisons, assessment logs, Pageviews populations, historical scope and raw revision retention |
| `daily/enwiki-<day>.parquet` | Same page-list schema | English quality/importance comparisons; supplemental SQL provides item IDs and current review categories |
| `daily/<project>-<day>.parquet` | Same page-list schema | Taskforce ranking and cross-project membership calculations |
| `hourly/<hour>.parquet`, `<project>-<hour>.parquet` | Same page-list schema | Incremental subject/talk collection and current project analyses |
| `hourly/categories-<hour>.parquet` | `page_id`, `page_namespace`, `page_title`, `page_latest`, `article_page_id`, `name`, `categories_complete` | Current maintenance and assessment consistency collection |
| `revisions/<shard>/<rev>.*` | Exact text and metadata described above | Collectors/parsers only; never report or HTTP rendering |
| `hourly/article-history.parquet` | Talk identity/revision, parser contract and nested parsed history | Newsletter recognition/history and listing dates |
| `hourly/article-authorship.parquet` | Page/listing date, grade, action, revision ranges, contributors and contract | Quality author statistics and newsletter credits |
| `hourly/article-milestones.parquet` | Canonical event ID, page, date, event kind and source aliases | Recognition, reviews and newsletter event lists |
| `hourly/current-assessments.parquet`, `scope.parquet` | Page identity, observations, prior state/tombstones | Incremental collection and cohort selection |
| `daily/pageviews-<identity>/*/daily.parquet` | `page_id`, `title`, `date`, `pageview`, `observed` | Traffic ranking and newsletter period totals |
| `…/members.parquet` | `page_id`, `title`, `pa_class`, `pa_importance`, `taskforces` | Frozen traffic population and group calculations |
| `…/coverage.parquet` | `title`, `start`, `stop`, `request_succeeded`, `observed_days`, `complete` | Incremental missing-tail detection and traffic completeness |
| `daily/newsletter-traffic.parquet` | Prepared annual rankings, nullable totals and complete monthly winners | Newsletter collection reads values calculated by the after-19:00 Pageviews pass |
| Prepared web/newsletter `.parquet` | One `payload: String` containing validated precomputed nested values | Dashboard, quarterly/annual newsletter HTML and wikitext projection |
| Prepared report/analysis bundles | Named typed tables with exact schemas in `manifest.json`; `source-pages` preserves revision keys | Wiki publication, saved web analyses and retry/recovery |

Payload decoding and presentation formatting do not recalculate source facts.
Catalogue/selection metadata can use JSON where it contains no report facts.
Detailed article contracts are declared by their `data.articles` owners; a
manifest lists every table, dtype, row count and digest for its generation.
The read-only data viewer previews Parquet under `data/archive`, `data/daily`,
`data/hourly` and disposable `cache`, excluding raw revisions, private state,
symlinks and previews.

Pageviews acquisition **and calculation wait until 19:00 UTC**, including
historical catch-up. Daily data is retained independently of wiki publication;
continuous history fetches only a missing tail, while gaps require a full
window. Readiness still checks site-hour coverage and leading article probes.
No current page list substitutes for an absent historical population.

## Migration and recovery

Deployment drains writers, runs `python -m data.migration`, verifies its receipt,
then promotes the source and restarts the services. Migration copies existing
files, upgrades page schemas, converts prepared newsletter/web JSON to Parquet,
and records original/final hashes in `state/data-layout-v2.json`. Old files are
preserved for rollback. Successful converted files resume without duplicate
work. If rollback has advanced an old source, it can replace only its unchanged
migration copy; conflicting newer data requires explicit reconciliation.
Migration never invents historical values or refetches old revisions merely to
fill an unknown field. Protect the midnight maintenance window.

`data/hourly/authorship-corrections.parquet` retains independently
proved reference-cleanup corrections alongside unchanged raw observations.
A correction binds the exact page/revision, raw contract, original fetch time
and eligible-byte denominator. Proofs retain the actual origin and parent
revisions, exact source/token hashes and the versioned
`wikiwho-zh:reference-cleanup-single-insertion-v1` rule.

The rule requires every old non-reference character to survive in order within
unique exact prefix/suffix blocks, allowing at most one inserted span while
reference bytes decrease. Conversions, deletions, reordered paragraphs,
ambiguous repeated matches and incomplete token snapshots establish no repair.
Stable token IDs project only proved unchanged characters into the scored
revision. New text, unverified origins and unowned whitespace keep their raw
meaning. Anonymous contributors retain their own identities and bytes.

The derived reader transfers only the currently eligible bytes between proved
contributors and recomputes shares, greatest contributors and the existing
literal 33% threshold. It preserves raw token counts; an editor recovered only
through a proof has zero raw tokens rather than an invented token count.
Neither the raw evidence file nor its authorship contract is rewritten.
The ledger distinguishes partial, complete and unavailable audit coverage;
readers label absent audits pending. Missing proof does not establish that
raw WikiWho provenance is accurate. An accepted partial proof corrects its
covered characters while other origins remain raw and unverified.

Background audits verify the fresh source reproduces all retained raw byte and
token totals before accepting a proof. Origin reviews are bounded, paced and
checkpointed independently. A deadline or ordinary-work callback yields between
requests; active requests retain their adapter timeout. Source failures retain
successful proofs, and HTTP readers never acquire or infer corrections.

Original token counts remain provenance; attributable non-reference bytes
determine contributor scores. `eligible_bytes` retains the complete eligible
source size, including comments and whitespace, and supplies each share's
denominator. WikiWho omits separator whitespace ownership, so the difference
between this total and summed contributor bytes remains unattributed instead
of being assigned to a nearby author. Greatest scores identify leading authors;
the directory counts those primary authors and any additional contributor
reaching one third, using one whole article for each qualified registered editor.

Older token-count observations retain their original contract and statistics.
The reader adds nullable byte scores and creation-revision IDs after validating
the old schema and metadata. The next successful write upgrades storage without
discarding old observations. Existing v3 observations keep their byte-based
validation and zero byte shares, while `eligible_bytes` stays unknown. Neither
v3 scores nor earlier raw-token counts satisfy the current contracts; collection
requests current byte evidence independently. Complete current API-byte fallback
evidence is accepted without repeatedly requesting an unalignable listing.
Recovery to an older source reader needs a compatible data backup because it
cannot read the upgraded schema.

Alpha suspends new automatic 365-day scope purges while historical dependencies
are migrated. Existing tombstones continue to protect against stale writers.
These tables provide facts absent from project snapshots and remain durable.
Use their declared owner schemas and merge writers rather than handwritten
file mutations. [Newsletter collection](../tasks/newsletter.md) defines report cutoffs.

`article-history.parquet` also retains source-backed homepage appearances.
Physical rows distinguish talk snapshots from homepage evidence; public talk
readers keep their established schema. Source-page revisions never occupy
talk-revision fields. Both writers share the same lock and preserve the other
row kind. Older talk-only files remain readable and are upgraded atomically on
the next write. Page IDs preserve identity through title changes, while earlier
dates, hooks, revisions and departed members remain retained.
The mixed-row schema requires its matching readers. Reverting to an older
source generation alone does not downgrade this file; recovery must retain the
new reader or restore a verified compatible data copy without losing newer facts.

`articles/homepage-archives.parquet` retains the initial wiki-wide inventory
of dated FA/GA archive sources and their stable article IDs. A locked daily
refresh appends completed display dates and atomically commits its coverage
date with the typed source revisions and actual observations. Source failures
preserve the previous complete file; damaged files fail validation. Future
schedules never enter this archive. Project-specific appearances are joined
on page ID into the canonical article-history ledger.

History refreshes retain the revision IDs from their fresh replica query and
fetch those exact bodies in API batches. Returned page and revision identities
must match before parsing; missing or hidden bodies leave prior snapshots
intact. Completed-day snapshot IDs do not establish the current talk revision.

The website's mutable `data/hourly/consistency/<timestamp>.parquet`
stores typed current findings, check outcomes and source links, with optional suggestion facts and
the canonical history fingerprint. It is rebuilt by background collection and
reused by the saved dashboard; HTTP reads never collect or rewrite it. Its
expiry and checked talk revisions remain independent of the immutable midnight
project files. A failed refresh retains the last accepted projection.

Historical figure rows identify `kind`, `date`, `grade`, `count`, source title,
nullable exact revision/URL, license, coverage, audit date and source notes.
Historical aggregates do not reconstruct page-level membership. Import receipts
retain the source digest, row count and coverage. Temporary input is deleted
only after the canonical generation and receipt validate. The
[retirement checklist](../transition.md) covers historical import checks.

`NewsletterFigureSource.read(metric, through)` is the common provider contract.
An issue can select temporary audited metadata or a formal structured source;
prepared facts retain provider identity and provenance. The normal renderer
consumes saved series. Existing archives without those fields use an explicit
read-only compatibility provider. See [retirement](../transition.md).

Historical popularity records identify exact monthly/yearly start and end dates,
ranked items, nullable population/view totals, original source title/revision/URL,
actual observation time and provisional status. Formal complete daily coverage
can supersede a period; incomplete new observations do not discard a verified
historical record. `tasks.zhwiki.newsletter.popularity` coordinates acquisition;
`data.newsletter.popularity` validates and retains the periods.

## Quality-author activity

`data/hourly/quality-author-activity.parquet` retains the latest
publicly attributed Chinese Wikipedia edit for registered editors identified
by the directory's listing-time contribution threshold. Rows contain the
canonical account name,
nullable UTC `last_active` and actual UTC `observed_at`. The hourly article
evidence collection refreshes these observations; a failed refresh keeps the
previous complete observations. HTTP reads never create this file.

The quality-author directory joins validated recognition events to current v4
listing-time evidence on page ID, date, grade and action. A registered editor
qualifies as a greatest contributor or when `bytes * 100 >= eligible_bytes * 33`;
the denominator includes comments,
whitespace and anonymous contributions. Unknown whitespace ownership never
increases an editor's numerator. Each qualified editor receives one whole
article count, including every editor in a qualifying pair or trio. Anonymous
identities are omitted without reallocating their bytes.

Native Polars operations deduplicate each editor/article at its strongest
retained grade and latest listing of that grade, then sort article details by
listing date descending. Totals and per-grade counts are integers. Details retain
all qualifying registered coauthor names and nullable verified nomination links
for presentation. Older evidence is preserved under its original contract and
recollected in bounded passes before entering current statistics. Missing
current evidence differs from a confirmed empty selection.

Optional B/BL credits use a separate
`data/hourly/current-authorship/b-class/` bundle. It retains current
membership, the first talk revision of the latest continuous B/BL banner range,
its exact UTC assessment timestamp, the article cutoff revision and timestamp,
and successful
attribution keyed by `(page_id, revision_id)`. A separate B-listings report
retains parsed talk ranges and resumable exact-parent scan progress in Parquet.

Only the article revision at or before the saved assessment instant contributes
to B/BL statistics; its UTC timestamp supplies the displayed B-Class date. Later
subject edits reuse that cutoff. An unchanged talk head reuses saved proof. A
new head scans only the newer revisions in batches against the last completed
head, retaining the original assessment through a continuous range. Demotion
and re-entry start a new range. Parser or talk identity changes require fresh
proof; hidden or incomplete history remains pending. Old
current-revision evidence stays durable and can be reused only when its exact
oldid matches a proved cutoff. Membership clocks remain actual observations,
independently of historical listing timestamps.

Each bounded attempt checkpoints independently with durable retry order,
throttling and foreground-priority checks. Historical high-quality evidence
remains independent. Registered editors use the same greatest-contributor or
literal 33% rule. Collected/total coverage counts pages with listing-time evidence,
independently of credited editors. Website requests never acquire attribution.

Current evidence may retain null principal authors when a leading editor cannot
be identified. Known contributor scores remain usable by the directory; they do
not make a qualifying runner-up the principal author. Validation compares the
principal-author list to the exact known leaders with null-safe equality, and
older contracts still require their original nonnull author list.

## Legacy observations and safe writes

Old `data/daily/assessments/<wiki>/<project>/assessments-YYYYMMDD.parquet`
files retain their original completed-day meaning and hashes. The compatibility
reader maps its completed-day argument to the next capture boundary for new
files. Old three-column rows keep unknown identities; do not fill them from
current queries. Preserved `backups/report-layout/` remains recovery evidence.

All writers validate schemas, unique identities and semantic constraints before
selection. Write on the same filesystem, flush, verify and atomically select
under the owner lock. Daily snapshots are immutable; current datasets select
new generations. Storage failure blocks publication when data is required.
Keep damaged durable files recoverable and visible; only disposable cache
corruption may become a cache miss. Retire legacy data only after validated
replacement, zero remaining dependencies and historical rendering checks.

## Observability

Use `data.runtime.status.read_status(runtime, as_of=...)` to read current
observations. The reader returns schema 1 with `availability`, `store_id`,
`revision`, `written_at`, `as_of` and four keyed domains. Missing, corrupt and
unreadable files return distinct availability values, null identity/revision and
empty domains; absence is never a successful or zero-valued observation. Reading
creates no directories, initializes no wiki clients and changes no saved files.

| Domain | Identity and measured facts |
|---|---|
| `services` | Scheduler or web service; process boot identity, lifecycle, actual start and heartbeat, saved schedules, optional measured resources |
| `executions` | Unique run; task, trigger, report/collection kind, actual start/end, measured duration, progress, bounded stages and outcome |
| `datasets` | Family/wiki/project identity; accepted generation, optional digest, source observation time, commit time, measured date coverage and freshness policy |
| `publications` | Destination identity; unique attempt and parent run, actual attempt timing, outcome, confirmed save count/revision and retained last confirmed save |

Service lifecycle is running until shutdown is requested, draining while active
workers finish, and stopped after scheduler shutdown completes. Optional
`stopping_at` and `stopped_at` retain the request and completion times across
heartbeats. Older records remain readable; a stopped record's final observation
provides its completion time when the explicit field is absent.

Execution outcomes are running, succeeded, failed, deferred and interrupted.
Publication outcomes are queued, running, saved, unchanged, failed, deferred and
unconfirmed. Only an actual comparison confirms unchanged. A registered target
that was never attempted is deferred with zero saves but no invented start or
duration. A save exception preserves an unknown edit count. A lost process has
a detection time, not an invented completion time. Safe failure identifiers and
bounded observed counts are saved; exception messages and credentials are not.

Execution progress may include a bounded stable `work` identifier describing
its current dataset or maintenance step. It is optional for older records.
Collection owners bind actual source activity, retain at most 64 stages and
rate-limit source updates to one per second within each named work step.
Nested work clears obsolete counters/source facts when returning to its parent.

Execution progress and stages may retain a bounded `source` description with
provider, dataset kind, public wiki/target and observed date. Older records
without this description remain readable. Source adapters emit actual read
boundaries; repeated reads of the same kind are sampled to avoid per-article
status writes. Destination checks precede comparisons, and submission stages
occur only immediately before an actual wiki save. An unchanged comparison
finishes without a submission stage. Optional structured references preserve
individual titles and page IDs without splitting commas inside titles.
Validated local-file reads retain a safe relative filename and recorded source
observation/capture times; missing creation metadata remains unknown.

Writers call `save_status` for non-controlling observations; the stricter
`update_status` validates and merges a transaction. `record_dataset` is called
only after the owning store accepts a generation. Reusing the same generation
advances neither dataset status nor its activity event. A collection may fail
while one of its independent datasets remains accepted. None of these records
permits publication or replaces durable report completion evidence.

Saved dashboard snapshots also include per-pass `collection.attempted` and
`collection.failed` counts. These count only reports attempted in that pass;
retained errors from earlier passes remain in section metadata. A cached check
therefore succeeds with zero attempts even while an older section is unavailable.
Older snapshots without these optional counts remain readable.

The operational status snapshot lives at `state/observability/status.json`,
bounded to 64 MiB.
Concurrent processes merge under a file lock; replacement fsyncs the file and
parent directory. Owner sequences reject stale writes and completed executions
cannot return to running. The reader normalizes equivalent UTC timestamp
spellings before ordering. All active executions survive pruning. Completed
executions retain at most 64 stages each for 168 hours after completion or
interruption detection, relative to the newest accepted envelope write. The
oldest completed records expire sooner if the 8,192-record or 64 MiB safety
bound is reached; other current domains are preserved. At most 1,000 destination
records retain their latest attempt and last confirmed save. Dataset and service
bounds reject growth rather than silently discarding current identities.

Freshness is derived for each read. Midnight data requires coverage through the
preceding completed UTC day. Pageviews requires that day at or after 19:00 UTC;
before then the previous available day remains current. Interval policies use
the actual observation timestamp and an explicit maximum age. Missing coverage
or an unspecified policy yields unknown. Service heartbeats older than 90
seconds are stale; a saved stopped lifecycle remains an explicit observation of
shutdown. These checks never substitute receipt time for source time.

Preview writers select `dry-run/state/observability/status.json` and isolated
execution leases. A clean observability reset creates a new `store_id` and may
remove diagnostics/activity logs after their writers stop. It must preserve
`reports/`, publication checkpoints, completion markers and historical facts.
There is no parser or migration path for former status or log formats.

## Activity and diagnostics

`data.runtime.events` defines typed activity and the correlation context;
`data.runtime.logs` attaches shared logging handlers; `data.runtime.log_store`
owns safe append, rotation, retention and read-only queries. Activity records
describe explicit service, execution, dataset and publication transitions.
Diagnostics retain readable UTC messages and bounded tracebacks. Neither stream
is a substitute for the current status snapshot or a publication receipt.

| Stream | Runtime filename | Record bound | Segment bound |
|---|---|---|---|
| Activity | `logs/activity/YYYYMMDD.NNN.jsonl` | 4 KiB | 8 MiB |
| Diagnostics | `logs/diagnostics/YYYYMMDD.NNN.log` | 64 KiB | 16 MiB |

Dates are UTC and segment numbers start at `000`. Both streams share the current
date plus 29 preceding dates and a 512 MiB total budget. A process lock covers
each complete append, rotation and retention pass. Writers reject unsafe paths;
an append either retains its complete record or records a dropped write.
Oversized diagnostics include a truncation marker. A busy day rotates to another
segment instead of silently stopping after the first file fills.

Every activity record has a schema version, event identity, UTC time, process
`boot_id`, and applicable `run_id`, `parent_run_id` and `attempt_id`. Context is
isolated between concurrent jobs. Publication events retain actual confirmation,
including unknown edit counts, and dataset events follow durable acceptance.
The same IDs appear in diagnostic lines and saved status.

Logging failures do not interrupt collection or publication. A bounded stderr
fallback reports the failure; the next successful append records how many writes
were dropped. Even a closed stderr cannot turn a logging failure into task
failure. Investigate these notices rather than interpreting absent activity as
proof that work did not run.

Readers enumerate the two streams independently and open existing regular files
only. Activity filtering uses UTC date, run identity and literal case-insensitive
text before the display limit. A response scans at most 8 MiB and displays at
most 100 events or 256 KiB, reporting truncation explicitly. An incomplete final
line is omitted; a malformed complete line is unreadable evidence, not an empty
result. Downloads select one bounded segment. Reads never initialize directories,
repair files or create source clients.

### Article statistics selection metadata

The `current-quality` report includes a one-row `selection.parquet` table
with a validated JSON payload. It retains all current article-grade rows,
both listing-time and current-revision contributor projections, default
views and additive `(grade, importance, year)` cells. Each cell stores page
and measured counts, an exact counted length histogram and Gaussian density
sums on a shared 129-point grid. Null years remain unknown. Article rows
retain `(page_id, article_revision_id, talk_page_id, talk_revision_id)` and
ranked contributor names. The browser combines these prepared cells only.

The `current-authorship` report now also retains a `targets` table with
`page_id: Int64`, `revision_id: Int64`, `title: String`, `grade: String` and
`observed_at: Datetime(us, UTC)`. Listing and current collectors share immutable
`(page_id, revision_id)` evidence and persist fair retry order. Unchanged
revisions reuse saved attribution. Older reports receive an empty targets
table and empty contributor ranks until the hourly collector prepares them.
