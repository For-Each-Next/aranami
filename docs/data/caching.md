# Disposable caches

Caches avoid repeated downloads and parsing. Removing them must not erase
retained project snapshots, detailed Pageviews, article evidence, newsletter
archives or publication baselines. Those belong under `reports/` and have
strict durable readers described in [Saved data](storage.md).

Each tabular cache owner declares a `shared.support.cache.ParquetCache` with
an ordered schema, extraction contract, row limit and compressed/decoded byte
limits. Give each cache identity a descriptive directory and name its files
with UTC timestamps in `YYYYMMDDTHHMMSS.parquet` format, for example
`cache/pageviews/zh.wikipedia.org/observations/20261110T143025.parquet`. Embedded
`aranami.cache` metadata identifies the extraction contract, for example
`{"schema":1,"contract":"article-extracts:2"}`.
Changing semantics changes the contract; unrelated application versions do not
invalidate compatible facts. Do not add migration code for disposable caches.
Name internal cache and configuration identities for their source semantics.
Keep schema revisions in explicit fields rather than arbitrary name suffixes;
actual external API versions and verified wiki revision IDs remain intact.

Parquet remains the default for temporary tables. Consider Arrow IPC only
after benchmarking representative consumers on Toolforge NFS; a change must
preserve exact schemas, extraction contracts, bounds and atomic generations.

Dynamic project and destination names use the shared `cache_key` helper:
an ASCII cache family followed by the full SHA-256 digest of the ordered
logical identities. For example, hourly membership uses
`cache/hourly-projects/<digest>/20261110T140000.parquet`. Report membership
and disposable PexBot action records follow the same identity rule. Unicode
source titles remain unchanged in table contents and target metadata. Template
aliases already use ASCII URL encoding; other fixed cache names stay unchanged.
Old title-based cache directories rebuild on demand without migration or
deletion. Durable project observations, report records and their logical keys
retain their existing storage paths.

Every Parquet writer also embeds an `aranami.storage` envelope with schema
version `1`, the semantic contract, a generation identity, UTC `written_at`,
row count and physical column declarations. `written_at` records the start of
serialization, separately from source observation or capture times. Owner
metadata remains intact. Readers validate the envelope when present; older
durable files remain readable with unknown file-write times. Never infer those
times from filesystem metadata or the date encoded in a filename.

`read` performs bounded validation and returns a typed miss for unavailable,
unsafe, incompatible or damaged cache files without mutating them. Metadata
and rows come from the same open file. `write` validates, stages and atomically
replaces complete data; failure preserves the old file. Cache acceptance limits
are not a hard bound on decoder allocation. The owner remains responsible for
uniqueness, freshness, concurrency and null semantics.

Pass a directory to `ParquetCache.read` and `write` for timestamped generations.
Reads select the newest filename without loading other tables. A successful
write retires older generations; a failed write preserves the selected file.
Writes within one second atomically replace that second's file. Exact Parquet
paths remain available for owners with a fixed observation bucket. Temporary
staging files use a hidden `.tmp` suffix and never become selected generations.
Former fixed or hashed cache filenames are ignored and rebuild on demand.
Activity links identify the file used at that time; a retired cache generation
is unavailable in the viewer. Durable evidence remains under `data/`.

| Owner | Reuse requirement |
|---|---|
| Hourly project/category observations | Exact UTC hour; complete page lists, replica revisions and category coverage |
| DYK/quality dates | Exact talk revision and alias/parser contract |
| Review sources | Fresh revision check and matching parsed-source contract |
| Raw revision text | Fresh SQL ownership/visibility or API metadata, with verified cached content hashes |
| Article introductions | Title/script identity, freshness and retry interval |
| Taiwan game titles | Exact `NoteTA` game-group and transcluded dependency revisions; checked hourly |
| Article authorship attempts | Unresolved listing identity and most recent attempt time |
| Report membership | Destination identity and current managed-body digest |
| Pageviews | Actual dated-row continuity; durable selected facts take precedence |

The website collector refreshes Taiwan display labels after its ordinary
reports. It asks Chinese Wikipedia's parser to apply
[`Module:CGroup/Games`](https://zh.wikipedia.org/wiki/Module:CGroup/Games)
and [`Module:CGroup/Games-zh`](https://zh.wikipedia.org/wiki/Module:CGroup/Games-zh)
through `NoteTA` with `variant=zh-tw`. Labels
and every transcluded dependency revision are checkpointed under the ASCII
directory `data/hourly/game-title-conversions/`. Unchanged revisions
reuse covered titles; new titles and changed sources trigger bounded batches.
Metadata-only origin revision checks before and after parsing reject
mixed-source observations without replica-lag ambiguity.
A failed optional refresh retains verified prior labels. The HTTP layer only
reads this cache and falls back to local script conversion for uncovered
titles. Canonical titles, page IDs, source URLs and anchors stay unchanged.

Hourly page and category files name the completed UTC hour, such as
`2026111400.parquet` written at 01:00, and retain the newest two periods.
Chinese wpvg lists live directly under `data/hourly`, alongside named child
project lists and `categories-<hour>.parquet`. Daily Chinese page lists remain
permanent under `data/archive`. Full revision text lives in the sharded
`data/revisions` store; only acquisition and parsing use it. See
[Saved data](storage.md) for schemas, retention and the complete fileset map.
Category coverage distinguishes complete project-page links from additional
global assessment-list matches. Missing coverage cannot establish emptiness.

Active collector histories record completed local Parquet reads, committed writes
and verified unchanged content. Failed writes cannot emit a successful write.
Public records contain relative filenames; HTTP reads with no collector observer
do not create activity records. Consecutive template-alias stages are condensed
only for display, preserving the underlying source evidence.

Continuous Pageviews may be supplemented; incomplete or gapped history needs a
full request. Coverage sentinels alone do not establish observations. The cache
is an optimization alongside the durable detailed dataset, not its replacement.
Raw source text is independent of parser versions; extracted facts are not.

Template alias lookup uses a wiki/template-specific Parquet observation for up
to 24 hours. It queries canonical and redirect metadata from Wiki Replicas,
without downloading unused template shells or invoked modules. Alias changes
can occur without editing the template, so expiry is independent of its
revision ID. Expired-source failures remain unavailable; cache-write failures
can still return the complete newly acquired aliases. Preview jobs use their
separate cache directory.

Only clear caches after stopping active writers. Retired fixed and encoded
filenames are unused and may be removed after inspection. Keep `reports/`,
`state/`, credentials and recovery originals intact. JSON website snapshots,
completion checkpoints and memory caches retain their own bounded freshness
contracts. A failed source observation never becomes a measured zero.

Hourly article attribution keeps retry order under
`cache/article-authorship-attempts/`. Each pass tries unattempted listings
first, followed by the least recently attempted failures, so unavailable recent
articles cannot block older history. Every attempted listing checkpoints its
retry time before progress callbacks; throttling still ends the pass. These
timestamps survive process restarts but do not establish successful attribution.
Deleting them restarts retry ordering without losing durable authorship evidence.

Shared source passes retain bounded text per wiki under
`cache/revision-contents/`, with previews under `dry-run/cache/`. Each reuse
checks exact revision ownership and visibility through Wiki Replicas and
verifies the retained text hash. Missing replica metadata or a replica failure
requires a fresh API read; hidden revisions never reuse old bodies. Source
selection uses indexed SQL for current revisions, historical cutoffs, display
intervals and bounded talk archives. Parser changes do not expire raw text.

Discussion activity and review revision checks use batched SQL page metadata.
Review comment anchors retain complete exact-revision results, including
confirmed empty discussions, under `review-comment-links`. Failed source reads
are retried; they never become cached empty results. Canonical title resolution
uses SQL for existing direct titles, keeping MediaWiki conversion/redirect
resolution for titles requiring it. Destination precondition checks and saves
still use fresh Pywikibot reads to protect against concurrent edits and lag.
