# Job instructions

These instructions apply to `src/aranami/jobs/` in addition to the parent
package and repository-wide instructions.

## Job coordination

- Keep one independently runnable report task per module.
- Coordinate source adapters and services rather than placing reusable
  report workflows in a job.
- Do not import another report job module. Read shared destination defaults
  from `aranami.monitor`.
- If a genuinely report-specific tabular transformation remains in a job,
  prefer Polars expressions over handwritten row loops.
- If a job must manipulate non-trivial wikitext directly, use
  `mwparserfromhell` rather than repeated string concatenation.

## Publication and dry runs

- Own the actual target site and page, preload current destination text,
  and pass it to content services. Select concrete report settings here,
  including ranking periods and task-force lists. Use the default report
  destinations from `monitor.py` unless the caller overrides them.
- Construct structured proposed edits from service-returned text, with
  the target site/page, edit summary, status tags, and original text, before
  selecting publication or dry-run output.
- Intentional wiki edits must use high-level Pywikibot methods such as
  `Page.save()` and include an informative edit summary.
- Do not access Wiki Replica connections or SQLAlchemy statements directly.
  Consume typed source or service results.
- When `dry=True`, never call `Page.save()` or another write method.
  Write one UTF-8 Markdown report for the complete run under `dry-run/`.
- Begin the report with `# Aranami dry run — YYYY-MM-DD UTC`, followed by
  start, end, and overall status bullets. List each routine once beneath
  the status, with its outcome, UTC timing, concise diagnostics, and
  proposed-edit metadata. Group all PexBot refresh targets under its one
  routine task.
- Link each proposed edit's exact text in a flat UTF-8 `.wikitext`
  companion. Include target site/page, summary, and status tags beside
  the link; do not embed proposed wikitext in the Markdown summary. Use
  the unique report stem and edit number for filenames. Do not create a
  JSON manifest or nested per-edit artifacts.
- Refer to the [Polars Python API reference][1],
  [mwparserfromhell documentation][2], and [Pywikibot documentation][3]
  when the corresponding job-specific operation is necessary.

[1]: https://docs.pola.rs/api/python/stable/reference/index.html
[2]: https://mwparserfromhell.readthedocs.io/en/latest/
[3]: https://doc.wikimedia.org/pywikibot/stable/
