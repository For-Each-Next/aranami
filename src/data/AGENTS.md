# Data instructions

Data modules validate and retain reusable facts. Their readers serve runtime,
tasks and both outputs without network requests or source-format extraction.

- Own schemas, identities, provenance, immutable snapshots, current selectors,
  report bundles, newsletter archives, publication records and runtime state.
  `caching` contains disposable cache implementations; runtime cache files stay
  under the external `cache/` directory.
- Import shared primitives only, not tasks, runtime, updater or webservice.
  A writer may invoke a caller-supplied builder under its documented lock;
  it must not construct a source client or choose a source itself.
- Use explicit Polars schemas and Parquet for tabular values. Preserve raw
  assessment labels, nulls, observed zeroes and empty values. Validate saved
  Enum declarations before restoring reader-returned String columns.
- Use bounded safe reads, identity/content validation, same-filesystem staging,
  fsync and atomic replacement. Failed writes leave the previous complete
  generation selected. Reject corrupt durable data rather than treating it as
  missing or empty.
- Immutable daily files are first-complete-write wins. Current generations
  retain a predecessor and reject stale replacements. Keep source observation,
  completed-day labels and publication times distinct.
- Deleting disposable caches must not lose evidence or alter the final report.
  Unique historical facts are durable even when their extraction code retires.
- Preview paths must be isolated from all production selectors, evidence and
  completion state. Never write into the source tree or installed packages.
- Do not add storage or query behavior to output renderers. Both the collector
  and HTTP reader use the same saved-data validation contract.

See [Architecture](../../docs/architecture.md),
[Storage](../../docs/data/storage.md) and [Caches](../../docs/data/caching.md).
