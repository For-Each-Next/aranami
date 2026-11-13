# Shared infrastructure instructions

Shared modules provide configuration, assessment profiles, read-only source
adapters and generic text/date/file mechanics. Higher layers depend on them.

- Never import data, tasks, runtime, updater or webservice. Imports must not
  initialize clients, choose publication targets or make network requests or write runtime files.
- Keep durable schemas and domain stores in data, audited wiki extractors in
  tasks, lifecycle in runtime, wiki projection/publication in updater and HTTP
  presentation in webservice.
- Assessment definitions belong to the wiki/project metadata registry. Raw
  PageAssessments labels remain distinct from codes, aliases and presentation.
- Runtime files belong under the caller's working directory outside source.
  Preserve zeroes and nulls, use native Polars expressions, and keep caches
  disposable. Do not add compatibility imports for retired packages.

[Source adapters](sources/AGENTS.md) defines read-only acquisition contracts.
[Architecture](../../docs/architecture.md) defines dependency boundaries.
