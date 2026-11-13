# Saved data

Use the owning data readers to obtain validated Polars tables without
source requests. Durable facts live under `reports/data/`; prepared reports and
newsletter archives live under `reports/records/`. Deleting `cache/` must not
erase the evidence required to explain a retained result.

## ASCII filesystem paths

`data.paths.storage_key` maps logical identities to bounded ASCII path
components. Ordinary ASCII components retain their spelling. Non-ASCII,
oversized and reserved `target-` components use `target-` followed by the
full SHA-256 of their exact UTF-8 spelling. Apply this mapping once to a
logical identity. Metadata retains the original `report_key`, wiki, project
and destination title; hashes in directory names never replace those facts
or article page/revision identifiers.

Owner readers accept an unmigrated legacy path when the current ASCII path
is absent. Two locations for one logical identity fail visibly rather than
silently selecting different history. Existing immutable observations are
reused before acquisition, preserving their dates and original bytes.
Mutable report writers require explicit migration before writing an existing
legacy identity. Literal legacy `target-` names require owner-verified migration:
their old spelling can coincide with another identity's new physical path.
Public previews open existing ASCII physical selectors directly, and resolve
old non-ASCII file links through the same guarded mapping. Project identity
comes from validated saved metadata rather than reversing a directory hash.

Deploy this layout with all runtime writers stopped, including one-off jobs.
Inventory production reports, report locks and isolated preview equivalents;
record every file's original/final path, size and SHA-256 before renaming.
Rename components on the same filesystem, verify the complete final inventory
and retain a checkpointed rollback receipt outside the managed trees. Preserve
file contents, manifests, selectors and historical observations unchanged.
Restart services only after the new readers and mapped layout agree. If source
activation fails, reverse the renames and verify the original inventory before
restarting an older reader. Respect the protected midnight maintenance window.

## Project snapshots

```text
reports/data/projects/<wiki>/<project-component>/<YYYY>/<YYYYMMDD>.parquet
```

The filename names the completed UTC data day, captured at the following
midnight. January 1 captures belong to the preceding year's December 31 file.
Every new date runs a complete fresh query, independent of the preceding file.
A same-date retry reuses its first complete observation. See the
[retirement checklist](../transition.md) for remaining migration dependencies.

`tasks.project_data.capture` and `data.project_data.read_snapshot` take an explicit
**capture boundary date**, one day after the filename. This keeps collection
scheduling distinct from report labels. The acquisition task supplies a builder
to `data.project_data.ensure_snapshot`; storage requests rows only after locking
and rechecking an existing file. `read_snapshot` accepts the wiki
identity string and never creates a Site. Its result is a `ReportBundle` with
`tables["members"]`. Missing dates return `None`; malformed durable data fails.
`latest_snapshot` and `read_previous_snapshot` preserve gaps; daily comparisons
must require adjacent dates rather than treating those search helpers as proof
of daily continuity.

| Column | Polars dtype | Meaning |
|---|---|---|
| `page_id` | Int64 | Required positive subject ID, unique within wiki/project/file |
| `page_namespace` | Int64 | Observed nonnegative numeric namespace |
| `page_title` | String | Nonempty database title without namespace prefix |
| `rev_id` | Int64 | Positive current subject revision observed by the query |
| `talk_page_rev` | Int64 | Nullable current talk revision; not the assessment-changing edit |
| `pa_class` | String | Nullable exact class for this project |
| `pa_importance` | String | Nullable exact importance for this project |
| `talk_page_id` | Int64 | Nullable associated talk identity, paired with its revision |
| `page_is_redirect` | Int64 | Observed 0 or 1, used independently of possibly stale grade |
| `page_length` | Int64 | Nonnegative source byte length, not prose word count |
| `taskforces` | List[String] | Sorted unique configured task-force names; empty means confirmed none |

Parent task-force flags are joined by page ID from the same boundary's child
snapshots. Children retain their own project assessments; they do not inherit
the parent's grade. Required child failure prevents a complete parent capture.
The explicit configuration is acyclic. Alpha rejects empty project results
because the adapter cannot distinguish valid emptiness from unavailable source
state; it must not invent mass departures or false flags.

Parquet filenames in activity records open the website's read-only data viewer.
It shows original metadata, the column schema, total records and pages of 50
rows. Opening the dialog and moving between pages load only the requested page;
the same `/data/?file=<path>&page=<number>` links work without JavaScript.
Canonical files under `cache/`, `reports/data/` and `reports/records/` are
available while retained. Hidden paths, symlinks, previews and private runtime
files are excluded. Inspection validates storage metadata and project identity;
it does not replace a durable consumer's full content validation.

The embedded `aranami` manifest records schema/family, application version,
wiki/project identity, generation, row count, declared encoded columns and
canonical sorted-content SHA-256. `metadata.snapshot_date` is the filename day;
`metadata.boundary_date` is the capture day; `metadata.scheduled_for` is midnight.
`started_at`, `finished_at` and `calculated_at` preserve actual acquisition
times after any lock wait or child capture. `metadata.taskforce_projects`
records the flag universe.
The source contract identifies current replica observations; replica lag is
not measured or reconstructed by this schema. Readers must not claim exact
midnight historical state.

Class/importance are losslessly Enum-encoded on disk using a wiki/project
profile and decoded to String by the reader. `metadata.assessment_profile`
records the profile ID and actual declarations. Video games on zhwiki stores
its original simplified Chinese database labels, with code/long/short display
names defined separately. See [Assessment fields](assessments.md). Snapshot
filenames retain their completed-day spelling without schema suffixes;
project directories use the ASCII mapping described above.

Useful later fields include exact subject/talk revision timestamps, Wikidata
identity, content model and independently observed disambiguation state. They
are not in alpha 1's daily schema. Do not infer article creation, promotion
date, revision count or authorship from snapshot differences.

## Detailed Pageviews

The latest reusable traffic dataset uses complete atomic generations:

```text
reports/data/pageviews/<wiki>/<project-component>/
  latest.json
  pageview-data-YYYYMMDDHHMMSS/
    manifest.json
    daily.parquet
    members.parquet
    coverage.parquet
```

`data.pageviews.read_snapshot(wiki, project)` reads the selected
validated bundle without clients. `latest.json` also retains its predecessor;
consumers must read the selection rather than guess a generation path. Generation
names receive a numeric suffix when more than one is written in the same second.

| Table | Fields and contract |
|---|---|
| `daily` | `page_id`, `title`, `date`, `pageview`, `observed`; one actual nonnegative measurement per title/day, including measured zero |
| `members` | `page_id`, `title`, `pa_class`, `pa_importance`, `taskforces`; the frozen project population used for this acquisition |
| `coverage` | `title`, `start`, `stop`, `request_succeeded`, `observed_days`, `complete`; half-open requested ranges and validated observed coverage |

Dates use Polars Date; IDs/counts use Int64; flags use Boolean; taskforces use
List[String]. Missing dates remain absent. Provisional zeroes used in ranking
calculations never become daily measurements. Metadata/provenance retains
source intervals and daily project snapshot identities separately from traffic
acquisition and report publication times.

Acquisition checks each page's actual daily rows. Continuous retained history
from the required start permits fetching only the missing tail, or reusing an
already complete range. Internal gaps or insufficient leading history require
a full-window download. A failed/incomplete supplement preserves the previous
durable selection. Complete accepted data is persisted before wiki publication,
so a failed wiki save does not discard reusable traffic. Previews write only
under `dry-run/reports/data/`. Newsletter calculations read complete formal
calendar periods first, then retained historical period records. Temporary
old-format acquisition is used only for missing periods. Corruption is not a
reason to fall back silently.

## Supplementary evidence

| Dataset | Location and identity |
|---|---|
| Talk history | `reports/data/articles/history.parquet`; `(talk_page_id, revision_id, contract)` |
| Listing authorship | `reports/data/articles/authorship.parquet`; `(page_id, date, grade, action, contract)` |
| Current project assessments | `reports/data/articles/current-assessments.parquet`; unique `page_id` and actual observation time |
| Recognition/review events | `reports/data/articles/milestones.parquet`; canonical `event_id` with retained aliases |
| Scope ledger | `reports/data/articles/scope.parquet`; page identity, last seen, absence and prior tombstones |
| Current newsletter facts | `reports/data/newsletter/observations/current.json`, with original dated observations |
| Quarterly archive | `reports/records/newsletter/<issue>/YYYYMMDD/observation.json` |
| Annual feature archive | `reports/records/newsletter/<issue>/annual-feature/YYYYMMDD/metadata.json` |
| Historical popularity periods | `reports/data/newsletter/popularity-history/<year>/`; `periods.parquet` selected through `data.newsletter.popularity.PopularitySource` |
| Historical newsletter figures | `reports/data/newsletter/historical/audited-aggregates/<generation>/figures.parquet`; atomic `latest.json` selection |

Talk history preserves every retained source revision/parser contract and its
nested events. Confirmed empty events differ from unobserved history. Events
keep actual dates, actions, grades, source oldids, roles, discussion links,
questions and explicit contributors. Milestones retain per-field coverage
(`known`, `not_applicable`, `pending`, `blocked`, `unresolved`); only evidence
establishes completeness. Attribution retains the listing revision, cutoff,
creation/fetch times, contributor token counts, non-reference UTF-8 byte scores,
shares and tied leading authors. Nominators and authors are different roles.

The current project page list provides unique `page_id` primary keys for joins
from historical article records. A retained hourly assessment projection gates
the author directory to main-namespace, nonredirect FA/FL/A/AL/GA/Bplus members.
It replaces current membership after a complete source query, preserving raw
assessment labels and rejecting stale writes. Historical revision evidence stays
under `reports/data/` when an article is delisted or leaves current membership.

The `wikiwho-zh:listing-range-nonref-bytes-v4` contract uses WikiWho's
`range_rev_content/{article_title}/{start_rev_id}/{end_rev_id}/` endpoint with
the listing revision as its inclusive start and a following processed revision
as its exclusive end. If the listing is the latest processed revision, the
exact `rev_content/rev_id/` endpoint supplies that same snapshot. Token origins
retain history back to creation, independently checked against revision
metadata. Only the exact listing snapshot contributes; a later revision used
as a range bound never contributes text. This avoids downloading every
historical snapshot. The exact historical wikitext establishes reference and
bibliography-section exclusions; comment source remains eligible.
Missing provenance and responses over 32 MiB leave the new observation
unavailable rather than substituting current text. If exact source alignment or
reference exclusion is unavailable, complete API token bytes remain usable under
`wikiwho-zh:listing-range-token-bytes-v1`. This fallback includes references and
uses summed API token bytes as its denominator; it never claims a non-reference
source total. The scoring contract remains explicit in retained evidence.

`reports/data/articles/authorship-corrections.parquet` retains independently
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

`articles/history.parquet` also retains source-backed homepage appearances.
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

The website's mutable `cache/toolforge/consistency/<timestamp>.parquet`
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

`reports/data/articles/quality-author-activity.parquet` retains the latest
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
`reports/data/articles/current-authorship/b-class/` bundle. It retains current
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

Old `reports/data/assessments/<wiki>/<project>/assessments-YYYYMMDD.parquet`
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
