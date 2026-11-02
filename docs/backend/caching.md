# Disposable caches

Use a cache to avoid repeating source downloads or parsing. A cache may be
deleted or rebuilt without losing collected evidence or changing a report
for identical source inputs. Durable article stores, assessment observations,
newsletter archives, and report results belong under `reports/`; they do not
use the cache API.

## Declare a table's contract

Each Parquet cache owner declares one `shared.support.cache.ParquetCache`
with its exact ordered schema, extraction contract, maximum row count, and
maximum compressed and decoded table size. Use a stable descriptive filename
without a schema suffix. Settings that identify distinct datasets, such as a
destination wiki and report title, still belong in the cache identity.

The file embeds a JSON object under the Parquet metadata key `aranami.cache`:

```json
{"schema": 1, "contract": "article-extracts:2"}
```

Change the owner's contract when the meaning of cached facts changes, even
when the columns stay the same. Do not rename the file or add a converter.
Missing or different metadata causes a cache miss, including an earlier file
that has the expected columns but no contract. This invalidation is independent
of the application version: unrelated releases keep compatible cache data.

## Read and replace

`read(path)` returns a frame with the declared schema. Missing, unsafe,
unreadable, incompatible, or oversized files return an empty frame for the
owner to rebuild. A read never creates, repairs, or deletes files. It checks
the compressed file size before decoding, reads at most one row beyond the
capacity, and checks the decoded frame's estimated size before accepting it.
These are cache acceptance limits, not a hard limit on decoder allocation.
Metadata and rows come from the same open file, so atomic replacement cannot
mix generations. `read(None)` supports a memory-only collector.

`write(path, frame)` checks schema and capacity, writes a complete temporary
file beside the destination, and replaces it atomically. Failed writes leave
the previous cache selected and remove temporary files. Files use owner-only
permissions; symbolic links and nonregular destinations are rejected.
Optional cache writers catch filesystem, bounds, and Polars errors so complete
source results remain usable. Existing domain-specific locks and conflict
checks still govern caches that merge concurrent observations.

## Keep freshness with the owner

The shared layer validates storage. Each owner validates row meanings,
uniqueness, source identity, nulls, retention, and refresh/retry policy:

| Owner | Evidence required before reuse |
|---|---|
| DYK and quality dates | Actual talk revision and template-alias fingerprint |
| Review sources | Fresh current revision checks and the parsed-source contract |
| Revision text | Freshly selected exact revision and matching source SHA-1 |
| Article introductions | Title/script identity, observation time, and retry interval |
| Anniversary sources | Completed display date, exact source/redirect evidence, and retained window |
| Report membership | Destination identity and digest of the current report text |
| Pageviews | Consistent daily observations and coverage; reports still fetch fresh inputs |

Raw revision text is independent of parsing rules. A parser change invalidates
derived facts, while unchanged verified source text remains reusable. Expired
observations, failed requests, and confirmed empty results keep their distinct
domain meanings. Clearing a cache never turns an unknown observation into zero.

## Upgrade and cleanup

Existing filenames with schema suffixes are unused and can be removed from
the disposable cache directory after active work stops. Unsuffixed files
without the embedded contract rebuild on first use. No migration of cache
contents is required. Preserve `reports/`, `state/`, and retained originals
under `backups/report-layout/`; retired layout converters are no longer shipped.

JSON website snapshots, completion checkpoints, and in-memory HTTP caches
retain their existing configuration and freshness contracts. See
[Architecture](../architecture.md#logs-caches-and-dry-runs) for runtime paths
and [Saved data](data-storage.md) for durable storage.
