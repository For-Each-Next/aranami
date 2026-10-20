# Service instructions

These instructions apply to `src/backend/services/` in addition to the
parent package and repository-wide instructions.

## Responsibility boundaries

- Prepare reusable source facts and interpret wiki-specific input
  here. Keep domain scope such as keyword matching rules in services.
- Accept existing text and concrete settings from callers without
  selecting or loading publication destinations.
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

## Report inputs and outputs

- Parse non-trivial input wikitext with `mwparserfromhell`, preserving
  source facts and unknown values in structured results.
- Return reusable extraction results without reducing observations to a
  frontend's displayed subset. Report-specific table schemas and output
  projections belong to updater services.
- Keep wiki output rendering, publication proposals, and saves in
  `updater`. Services must not import a frontend or select its destinations.
- Receive concrete report choices from callers, including project names,
  ranking periods, task-force lists, and assessment/report definitions.
  Keep reusable configuration types and site-specific content rules here.
- Refer to the [mwparserfromhell documentation][2] for input parsing.

[1]: https://docs.pola.rs/api/python/stable/reference/index.html
[2]: https://mwparserfromhell.readthedocs.io/en/latest/
