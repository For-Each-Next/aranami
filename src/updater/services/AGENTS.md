# Wikitext projection instructions

Services turn complete prepared facts into report rows and wikitext. They
accept concrete report settings and original destination text from the caller.

- Do not choose or load destinations, collect sources, create publication
  proposals, save pages or manage runtime files.
- Keep source parsing, membership comparisons, dates and reusable calculations
  in tasks; keep validation and persistence in data. Read typed contracts
  without importing an acquisition implementation.
- Use Polars expressions for tabular projections; ordinary Python is clearer
  for small mappings. Preserve complete stored results independently of limits.
- Use central assessment metadata for aliases, labels, order and grade colors.
  A report may select its own displayed series without redefining grades.
- Build templates with `mwparserfromhell` where useful. Verify the actual wiki
  template contract before changing calls. Preserve unknown values, measured
  zeroes and all text outside paired managed ranges.
- Use updater summary and formatting helpers for output mechanics. Generic
  comment-range handling belongs in shared support.

See [Architecture](../../../docs/architecture.md), the
[Polars API](https://docs.pola.rs/api/python/stable/reference/index.html) and
[mwparserfromhell](https://mwparserfromhell.readthedocs.io/en/latest/).
