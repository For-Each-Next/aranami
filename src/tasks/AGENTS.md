# Task instructions

Tasks acquire read-only source observations and prepare reusable facts.
Callers supply concrete wiki/project scope, periods and settings. Tasks return
complete typed results without choosing publication destinations or formatting
HTML and report wikitext.

- Put audited wiki-specific extraction and interpretation in `zhwiki` or
  `enwiki`. Chinese newsletter workflows belong in `zhwiki/newsletter`.
  Generic acquisition adapters belong in `shared.sources`.
- Use Pywikibot to normalize titles/namespaces and build canonical page URLs.
  Verify actual source templates, documentation, aliases and invoked modules.
  Chinese and English semantics must not be inferred from one another.
- Parse nested source wikitext with `mwparserfromhell`. Preserve revision IDs,
  actual observation times, explicit empty results and unknown values.
- Keep schemas, validation and stores in `data`. A task can supply a builder to
  a locked data writer; the writer must recheck existing immutable data before
  invoking the external query. Never import runtime or either output package.
- Use Polars-native expressions for joins, grouping, filtering, aggregation,
  sorting and reshaping. Preserve eager adapter contracts. Ordinary Python is
  appropriate for small mappings and external request/parser boundaries.
- Use the central wiki/project assessment profile; do not duplicate aliases,
  rank maps, article groups or grade colors in a task.
- Keep complete results even when a frontend displays a subset. Save reusable
  observations through the data interface before disposable caches expire.
- Temporary historical-format readers belong in `zhwiki/legacy` and feed the
  normal schemas. Rendering and future calculations must not depend on their
  acquisition format. Follow the temporary transition guide for retirement.
- No scheduler loops, publication calls or HTTP handlers belong here.

See [Architecture](../../docs/architecture.md),
[Source adapters](../shared/sources/AGENTS.md), the
[Polars API](https://docs.pola.rs/api/python/stable/reference/index.html) and
[mwparserfromhell](https://mwparserfromhell.readthedocs.io/en/latest/).
