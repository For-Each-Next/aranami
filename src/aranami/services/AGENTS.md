# Service instructions

These instructions apply to `src/aranami/services/` in addition to the
parent package and repository-wide instructions.

## Data processing

- Process tabular data from the Quarry and Pageviews source adapters with
  Polars when it improves clarity or performance.
- Prefer Polars expressions for filtering, joins, grouping, aggregation,
  sorting, and reshaping instead of handwritten row loops.
- Do not force small scalar or mapping operations into a DataFrame when
  ordinary Python is clearer.
- Refer to the [Polars Python API reference][1] for supported data types,
  expressions, and operations.

## Wikitext and proposed edits

- Build or modify non-trivial Wikipedia wikitext with
  `mwparserfromhell` instead of assembling markup through repeated string
  concatenation.
- Parse existing wikitext and manipulate its nodes and templates whenever
  possible; simple fixed text does not require a parser.
- Represent a proposed edit as structured data containing the target site
  and page, wikitext, edit summary, and status tags.
- Services may construct proposed edits but must not save wiki pages.
  Intentional publication belongs to jobs.
- Refer to the [mwparserfromhell documentation][2] for
  wikitext construction.

[1]: https://docs.pola.rs/py-polars/html/reference/
[2]: https://mwparserfromhell.readthedocs.io/en/latest/
