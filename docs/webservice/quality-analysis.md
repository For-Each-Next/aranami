# Quality article analysis

The `/quality-analysis/` tool describes the current Chinese Wikipedia Video
games quality cohort: FA, FL, A, AL, GA and Bplus. Separate full-width sections
present two grade/importance pie charts, stacked listing-year columns and prose
comparisons, followed by every selected article. Class labels and charts use the
standard assessment colors. The pies include exact counts and percentages.
The article table supports local search, grade and importance filters, sorting
and pagination. All rows remain available without JavaScript. Table filters do
not change the whole-cohort summary charts.

An hourly idle routine, scheduled at :02 UTC after ordinary work, prepares the
report from shared project membership and retained Article history observations.
All active verified Article history calls on one talk page contribute events,
including separate DYK and quality histories.
Action numbers order events within one template; independent histories require
consistent dated evidence to establish the latest listing. Article bodies are
fetched in batches of five only when their exact revision or measurement contract
has changed. A talk-page change can update a listing date without downloading
unchanged article text. A page whose source revision has advanced remains pending
until a matching observation is available. Missing measurements and dates are
distinct from zero.

The background collector writes complete current membership, measurements and
precomputed grade, year, importance and coverage tables to a separate Parquet
report bundle under `reports/data/articles/quality-analysis/`. Atomic selection
keeps the last healthy generation available when collection fails. The web
service validates and reads this result; it performs no source requests or
statistical aggregation. Its bounded HTML cache is keyed by the saved generation
and language, so a completed background update invalidates older documents.

## Reading the results

- Annual stacked columns count the latest successful listing of each
  **currently selected** article at its current grade. They are not a census of past
  promotion. Unknown dates are excluded from bars and reported in coverage.
- Listing dates come from exact saved talk revisions. Date links open that
  evidence; editable historical oldid parameters are retained as metadata rather
  than treated as verified article ownership.
- Prose counts describe the observed **current article revision**, not its
  historical listing version. Chinese words use jieba precise segmentation with
  HMM enabled. Punctuation and whitespace are excluded. Shared prose rules omit
  references and other non-prose sections; list grades include readable template
  values. Counts are estimates under this documented extraction policy.
- Prose comparisons show FA, GA and Bplus separately, then Top, High, Mid and
  Low importance pooled from those three grades. FL, A and AL remain in the
  article table and listing totals but do not enter these comparisons. The
  overview's median and measurement coverage still describe the complete cohort.
- Central 68% and 95% intervals are linear empirical 16th–84th and
  2.5th–97.5th percentiles. They are descriptive ranges, not confidence intervals
  or minimum requirements for an assessment grade. Small groups can have equal
  endpoints. The table reports measured sample counts alongside total membership.

## Memory and lifecycle

Each analysis pass runs in a short-lived child process. The parent retains no
article bodies, analysis frames or captured worker output. The child checkpoints
small batches and exits after bounded work, releasing its parser, tokenizer and
native allocation pools. Shutdown and timeout handling reap the child before
releasing the routine. New ordinary work and midnight protection stop admission
of further source batches.

A separate nonblocking collection lease serializes scheduled and manual passes.
A second collector defers instead of downloading the same pending revisions;
web readers never acquire this writer lease.

Native Polars pools default to two threads in Toolforge processes, respecting an
explicit `POLARS_MAX_THREADS` override. Rendered pages are limited to 8 MiB and
32 entries, expire after 60 seconds and are swept by the web heartbeat even when
no request arrives. These are process-memory controls; the operating system can
retain file pages in its reclaimable filesystem cache.

The separate `quality-authorship` idle routine, also scheduled at :02 each hour,
uses the same disposable-worker lifecycle for B/BL history and author collection.
Its normal pass admits up to
80 range checks over at most 90 seconds, then 40 attribution attempts using the
remaining portion of its overall 240-second budget.
The verified banner extractor supports an explicit project class or the page
header's inherited shell class. Unchanged ambiguous histories remain cached;
new talk revisions, class changes or parser contracts reconsider them.
