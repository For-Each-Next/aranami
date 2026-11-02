# Saved data and Parquet fields

Use this contract when adding a dataset, changing its fields, or reading saved
source observations. Files under `reports/data/` preserve collected facts for
reuse across reports. Their owning readers return validated, explicitly typed
Polars frames; a damaged durable file fails explicitly instead of becoming an
empty replacement.

Generated report text and newsletter drafts belong under `reports/records/`.
Execution outcomes belong in `state/`, diagnostics in `logs/`, and disposable
downloads, staging files, and retry budgets in `cache/` or a temporary directory.
Removing a cache must not remove source evidence.
See [Architecture](../architecture.md) for ownership and
[Administration](../administration.md) for recovery and cleanup.

## Choose a path

Paths are relative to the Toolforge runtime home. Keep dataset names readable;
do not add schema suffixes such as `-v1` or `-v2`, content hashes, or run UUIDs to
data filenames. Schema versions, parser contracts, digests, and observation
times belong in validated metadata or the declared fields that identify them.

| Dataset | Active location | Retention |
|---|---|---|
| Daily project assessments | `reports/data/assessments/<wiki>/<project>/assessments-YYYYMMDD.parquet` | Immutable observations of completed UTC dates |
| Talk history | `reports/data/articles/history.parquet` | Every retained talk revision and parser contract |
| Listing-time authorship | `reports/data/articles/authorship.parquet` | Successful historical attribution observations |
| Article milestones | `reports/data/articles/milestones.parquet` | All retained recognition events and their evidence |
| Project scope | `reports/data/articles/scope.parquet` | Membership observations and absent-page tombstones |
| Prepared newsletter facts | `reports/data/newsletter/observations/current.json` | Current complete facts, with original captures in `YYYYMMDD/` folders |

Use a date folder such as `20260808/` when several files form one daily
observation. A single daily table can put that date directly in its readable
filename, as assessments do. Current accumulating tables use their dataset
name without an invented date or generation suffix. Encode unsafe characters
in wiki and project path components; titles must never become arbitrary paths.

## Daily assessment fields

`assessments-20260808.parquet` describes the completed UTC date 2026-08-08.
Its collection boundary is 24:00 on that date, equivalent to
2026-08-09 00:00 UTC. Preserve the actual collection time separately; a delayed
capture must not claim to have been observed exactly at midnight. Reuse the
first complete saved observation, and leave unobserved historical dates absent.

The table preserves each page's observed identity and assessments, including
nonarticle pages. Retained titles and namespaces provide original links when a
page moves or leaves the project. Resolve current talk pages and revision
references through their owning source adapters when needed.

| Field | Polars dtype | Null and value contract |
|---|---|---|
| `page_id` | `Int64` | Required, positive, unique within this wiki/project/date |
| `page_title` | `String` | Observed database title without a namespace prefix |
| `page_namespace` | `Int64` | Observed nonnegative namespace ID |
| `pa_class` | `String` | Nullable; the observed project quality assessment |
| `pa_importance` | `String` | Nullable; the observed project importance assessment |
| `page_is_redirect` | `Int64` | Observed redirect flag, either 0 or 1 |

An unknown assessment is null. Do not substitute an empty string, zero, or a
display label for missing source evidence. A page's identity is its numeric ID;
a title change does not create a different member.
Older three-column files remain readable with their original checksums and
without invented identity fields. Migrated historical observations may have
null identity or redirect fields when that fact was not retained. Never fill
historical gaps using current page metadata or rewrite a completed snapshot.

The Parquet file embeds its JSON manifest under the metadata key `aranami`;
there is no companion JSON selector or generation directory. It records:

| Metadata field | Contract |
|---|---|
| `schema`, `application_version`, `family` | Storage schema, producing application version, and `project-assessments` dataset family |
| `report_key`, `target` | Matching dataset identity, source wiki, and exact project name |
| `generation` | Actual observation timestamp identity, retained inside metadata |
| `calculated_at`, `started_at` | Original UTC capture time, including any delay after the scheduled boundary |
| `metadata.project`, `metadata.snapshot_date` | Project and completed UTC date matching the filename |
| `metadata.scheduled_for` | Midnight immediately after the completed date |
| `metadata.article_totals` | Article-only count and class/importance counts; null when only legacy assessment rows survived |
| `publication.state` | Production or preview storage observation; collecting source data does not establish a wiki save |
| `rows`, `sha256` | Row count and digest of canonical sorted observations |

`article_totals` contains `count`, `class`, and `importance`, with optional
`nonarticles` (count and class aggregates) and `redirects` (excluded page count)
in newer observations. Mainspace disambiguation, set-index, and redirect-grade
pages count as nonarticles. The actual redirect flag excludes redirects from
both page totals, even when their assessment grade is stale. Each aggregate list
contains `{value, count}` objects; `value` may be null for an unassessed article.
These aggregates exclude nonarticle namespaces while the table retains all
project page IDs. Do not infer article-only totals from legacy tables without
namespace evidence.

## Article history fields

`history.parquet` preserves complete source snapshots. Its unique key is
`(talk_page_id, revision_id, contract)`. `read_versions` returns all retained
versions; `load` selects the latest snapshot for each talk page. An empty
`events` list is a confirmed empty history, not an unobserved page.

| Field | Polars dtype | Meaning |
|---|---|---|
| `page_id` | `Int64` | Required positive article ID |
| `title` | `String` | Required original article title |
| `talk_page_id` | `Int64` | Required positive source talk-page ID |
| `revision_id` | `Int64` | Required positive observed talk revision |
| `observed_at` | `Datetime(us, UTC)` | Required actual observation time |
| `contract` | `String` | Required parser/extraction contract identity |
| `events` | `List[Struct]` | Required complete list of extracted events |

Each `events` struct has these fields:

| Field | Polars dtype | Meaning and missing-value semantics |
|---|---|---|
| `kind` | `String` | Required: `quality`, `dyk`, `news`, `history`, or `review` |
| `date` | `Date` | Nullable source event date; never the collection date |
| `grade` | `String` | Quality grade; null when inapplicable or unobserved |
| `action` | `String` | Required event action, such as `added`, `removed`, or `appeared`; review actions are `nominated`, `passed`, `failed`, or `pending` |
| `parameter` | `String` | Required source-template parameter identity |
| `oldid` | `Int64` | Nullable positive revision associated with the event |
| `nominator` | `String` | Nullable explicitly recorded nominator |
| `main_author` | `Boolean` | Nullable explicit source claim about that nominator |
| `nomination_link` | `String` | Nullable original discussion anchor |
| `contributors` | `List[String]` | Nullable recorded DYK contributors; names are not inferred from edit history |
| `action_type` | `String` | Event/review type; required for review rows and nullable for compatible older observations |
| `question` | `String` | Nullable original DYK question text |

All top-level history fields are required. Do not drop earlier source revisions
when selecting the latest one for a consumer. Changes to extraction semantics
must use a distinct `contract` and preserve the original evidence.

## Listing-time authorship fields

`authorship.parquet` stores successful listing-time calculations, keyed by
`(page_id, date, grade, action, contract)`. Every top-level field is required.
Missing or failed calculations remain absent rather than becoming empty
successful records. The owning module is `backend.articles.authorship_store`.

| Field | Polars dtype | Meaning |
|---|---|---|
| `page_id` | `Int64` | Positive article ID |
| `date` | `Date` | Confirmed listing date |
| `grade` | `String` | `FA`, `FL`, `GA`, `A`, `AL`, or `Bplus` |
| `action` | `String` | `added` |
| `contract` | `String` | Attribution calculation contract |
| `listing_revision` | `Int64` | Positive revision selected at the listing boundary |
| `cutoff` | `Datetime(us, UTC)` | Midnight immediately following the listing date |
| `creation` | `Datetime(us, UTC)` | Actual article creation before `cutoff` |
| `fetched_at` | `Datetime(us, UTC)` | Evidence retrieval time, at or after `cutoff` |
| `authors` | `List[String]` | All tied leading authors, sorted and nonempty |
| `contributors` | `List[Struct]` | Complete nonempty contributor statistics |

Each contributor contains `editor: String`, `name: String`, `tokens: UInt32`,
and `share: Float64`. Only `name` is nullable. `editor` is the stable upstream
editor identity; `tokens` is positive; `share` is finite, greater than zero,
at most one, and agrees with that contributor's fraction of the listing's
total tokens. The `authors` list must match the names tied for the largest
token count. Computed authorship stays distinct from source nomination credits.

## Article milestone fields

`milestones.parquet` stores events across all dates, including incomplete and
undated events. The unique key is `event_id`. The owning
`backend.articles.milestone_store` module derives identity and coverage;
callers must not manufacture `complete` flags or treat exhausted retries as
successful evidence.

| Field | Polars dtype | Meaning and missing-value semantics |
|---|---|---|
| `event_id` | `String` | Required canonical event identity derived from article, kind, date or parameter, grade/type, and action |
| `aliases` | `List[String]` | Earlier undated identities retained after their date becomes known; empty when none |
| `page_id` | `Int64` | Required positive article ID |
| `title` | `String` | Required observed article title |
| `talk_page_id` | `Int64` | Nullable source talk-page ID |
| `revision_id` | `Int64` | Nullable source talk revision |
| `date` | `Date` | Nullable actual event date |
| `kind` | `String` | Required: `quality`, `dyk`, `news`, `history`, or `review` |
| `grade` | `String` | Nullable recognition grade |
| `action` | `String` | Required event action |
| `parameter` | `String` | Nullable original parameter identity, retained for undated-event disambiguation |
| `nominator` | `String` | Nullable source nomination credit |
| `main_author` | `Boolean` | Nullable explicit source claim about the nominator |
| `nomination_link` | `String` | Nullable source discussion link or anchor |
| `contributors` | `List[String]` | Nullable explicitly recorded DYK contributors |
| `authors` | `List[String]` | Nullable computed listing-time authors |
| `sources` | `List[Struct]` | Exact supporting revisions; empty when not yet available |
| `lookup_state` | `String` | Required lookup outcome: `pending`, `blocked`, or `unresolved` |
| `reason` | `String` | Nullable bounded underlying-cause identifier |
| `lookup_cursor` | `String` | Nullable resumable evidence-search position |
| `assessment_observed_at` | `Datetime(us, UTC)` | Nullable observation of a current grade; never substituted for its listing date |
| `date_status` | `String` | Coverage of the event date |
| `nominator_status` | `String` | Coverage of source nomination credit |
| `authors_status` | `String` | Coverage of computed authorship |
| `contributors_status` | `String` | Coverage of recorded DYK contributors |
| `nomination_status` | `String` | Coverage of the discussion link |
| `main_author_status` | `String` | Coverage of the source main-author claim |
| `source_status` | `String` | Coverage of revision or current-assessment evidence |
| `status` | `String` | Derived overall completeness |
| `updated_at` | `Datetime(us, UTC)` | Required actual inventory update time |
| `action_type` | `String` | Normalized event or review type; required for review events |
| `question` | `String` | Nullable original DYK question |
| `question_status` | `String` | Coverage of the DYK question |
| `occurred_at` | `Datetime(us, UTC)` | Nullable precise source event instant |
| `oldid` | `Int64` | Nullable revision associated with the event |

Each `sources` struct contains required `title: String`, `revision_id: Int64`,
and `url: String`: the source title, positive revision, and exact Chinese
Wikipedia evidence URL. Coverage fields use `known` or `not_applicable` when
supported; otherwise they retain `pending`, `blocked`, or `unresolved`.
`status` becomes `complete` only when every applicable required field has
evidence. A null boolean is unknown, while `false` is an observed negative.

## Project scope fields

`scope.parquet` has one row per positive, unique `page_id`. Only complete,
nonempty project membership observations advance it. The owning module is
`backend.articles.scope`.

| Field | Polars dtype | Meaning and missing-value semantics |
|---|---|---|
| `page_id` | `Int64` | Required article identity |
| `last_seen` | `Datetime(us, UTC)` | Nullable last confirmed presence |
| `first_out_of_scope` | `Datetime(us, UTC)` | Nullable start of confirmed absence; cleared when the page returns |
| `observed_at` | `Datetime(us, UTC)` | Required time of the latest complete membership observation |
| `purged_at` | `Datetime(us, UTC)` | Nullable tombstone after at least 365 days of confirmed absence |

Publish tombstones before pruning the article stores. Every article writer
checks them inside its write lock so an old collector cannot restore purged
records. Reappearance before pruning resets the absence grace period.

## Read and write safely

Use each owning store's reader and merge writer. Declare dtypes explicitly,
including on empty frames, preserve unique keys, and validate the full schema
and semantic constraints before selecting a replacement. Use Polars expressions
for joins, aggregation, filtering, and ranking; do not round-trip through
Pandas or row dictionaries for tabular processing.

Write a complete replacement to cache or temporary staging on the same
filesystem, flush it, validate it, and atomically select the final path under
the owning write lock. Current data may advance only after successful
persistence. Immutable daily observations and original rendered records must
not be overwritten by retries. Preserve original observation and source times
when reusing data.

Missing files may produce explicitly typed first-use frames where the owning
contract allows it. Existing malformed, unsafe, or oversized durable files
must remain recoverable and fail visibly. Preserved originals from retired
layouts remain in `backups/report-layout/`. See
[Newsletter collection](newsletter.md) for retained observations and JSON archives.
