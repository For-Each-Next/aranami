# Service instructions

These instructions apply to `src/aranami/services/` in addition to the
parent package and repository-wide instructions.

## Responsibility boundaries

- Keep report coordination and wiki-specific interpretation in services.
- Keep domain scope such as keyword matching rules in services. Accept
  destination content from jobs rather than selecting or loading update
  pages here.
- Put reusable date parsing, template-value access, prose extraction, and
  language word counting in narrowly named support modules.
- Pass wiki-specific profiles to generic support functions. Support must
  not import services or select quality classes, actions, or results.

## Data processing

- Process tabular data from the Quarry and Pageviews source adapters with
  Polars when it improves clarity or performance.
- Prefer Polars expressions for filtering, joins, grouping, aggregation,
  sorting, and reshaping instead of handwritten row loops.
- Do not force small scalar or mapping operations into a DataFrame when
  ordinary Python is clearer.
- Refer to the [Polars Python API reference][1] for supported data types,
  expressions, and operations.

## Wikitext and job inputs

- Build or modify non-trivial Wikipedia wikitext with
  `mwparserfromhell` instead of assembling markup through repeated string
  concatenation.
- Parse existing wikitext and manipulate its nodes and templates whenever
  possible; simple fixed text does not require a parser.
- Accept existing page text, process it, and return updated text. Services
  may also return destination-free report data for jobs to consume.
- Keep actual update page titles and reads, publication proposals, and
  saves in jobs. Services must not select or load destination pages.
- Receive concrete report choices from jobs, including project names,
  ranking periods, task-force lists, and assessment/report definitions.
  Keep reusable configuration types and site-specific content rules here.
- Refer to the [mwparserfromhell documentation][2] for
  wikitext construction.

[1]: https://docs.pola.rs/api/python/stable/reference/index.html
[2]: https://mwparserfromhell.readthedocs.io/en/latest/
