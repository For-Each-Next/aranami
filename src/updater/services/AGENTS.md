# Updater service instructions

These instructions apply to `src/updater/services/` in addition to the
parent package and repository-wide instructions.

## Output projection

- Consume backend-prepared data and caller-supplied report settings.
  Project complete results into report-specific tables and wikitext;
  keep source collection, reusable analysis, and refresh cycles in backend.
- Accept existing destination text from jobs. Do not select or load
  publication pages, create publication proposals, or save pages here.
- Keep project names, ranking periods, task-force lists, and destination
  choices supplied by jobs. Preserve complete results separately from
  display limits so other consumers can reuse them.
- Use Polars expressions for tabular filtering, joins, grouping,
  aggregation, sorting, and reshaping. Small scalar and mapping operations
  may use ordinary Python.
- Put edit-summary formatting, region labels, and publication membership
  prose in narrowly named updater support modules. Keep membership
  parsing/comparison, source facts, dates, and source prose extraction in
  backend data services. Do not load or write caches or runtime files.

## Wikitext rendering

- Build output templates with `mwparserfromhell` where appropriate;
  fixed text does not require a parser. Do not parse source templates or
  retained report facts here. Consume backend-prepared metadata and use
  shared comment-range mechanics to preserve all surrounding text.
- Render from the same prepared results that are retained for reuse.
  Preserve unknown values and measured zeroes, configured managed ranges,
  and text outside those ranges.
- Verify actual on-wiki template contracts before changing template calls.
  Keep wiki-specific formatting rules explicit.
- Refer to the [Polars Python API reference][1] and
  [mwparserfromhell documentation][2] when changing their operations.

[1]: https://docs.pola.rs/api/python/stable/reference/index.html
[2]: https://mwparserfromhell.readthedocs.io/en/latest/
