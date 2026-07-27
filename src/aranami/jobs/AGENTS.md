# Job instructions

These instructions apply to `src/aranami/jobs/` in addition to the parent
package and repository-wide instructions.

## Job coordination

- Keep one independently runnable report task per module.
- Coordinate source adapters and services rather than placing reusable
  report workflows in a job.
- Do not import another job module.
- If a genuinely report-specific tabular transformation remains in a job,
  prefer Polars expressions over handwritten row loops.
- If a job must manipulate non-trivial wikitext directly, use
  `mwparserfromhell` rather than repeated string concatenation.

## Publication and dry runs

- Consume structured proposed edits containing the target site and page,
  wikitext, edit summary, and status tags before selecting output.
- Intentional wiki edits must use high-level Pywikibot methods such as
  `Page.save()` and include an informative edit summary.
- Do not access Wiki Replica connections or SQLAlchemy statements directly.
  Consume typed source or service results.
- When `dry_run=True`, never call `Page.save()` or another write method.
  Write one UTF-8 Markdown report for the complete run under `dry-run/`.
- Give each proposed edit a clearly labeled report section containing its
  target site and page, edit summary, status tags, and proposed wikitext in
  a fenced code block. Do not create a JSON manifest or nested per-edit
  artifacts.
- Refer to the [Polars Python API reference][1],
  [mwparserfromhell documentation][2], and [Pywikibot documentation][3]
  when the corresponding job-specific operation is necessary.

[1]: https://docs.pola.rs/py-polars/html/reference/
[2]: https://mwparserfromhell.readthedocs.io/en/latest/
[3]: https://doc.wikimedia.org/pywikibot/stable/
