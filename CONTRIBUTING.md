# Contributing to Aranami

Aranami is developed, tested, and built on a development computer. Its
pure-Python wheel is installed on Wikimedia PAWS. PAWS supplies the existing
Wikipedia OAuth session and Wiki Replica credentials; do not add credential
prompts, a user/password interface, or secrets to the wheel.

## Documentation

Describe what each component does and the results it produces before
discussing technology. Keep the README focused on capabilities and usage,
and keep docstrings focused on behavior, inputs, outputs, and relevant side
effects. Include implementation details only when they explain a requirement
the reader needs to use or maintain the component correctly.

## Runtime and architecture

`aranami.run(dry=False)` starts a background monitor and returns its
scheduler. Define default target pages, per-routine UTC cron triggers, and
PexBot subscription roots together in
`src/aranami/monitor.py` through `TASK_DEFINITIONS`. Jobs read their defaults
from those definitions. The monitor uses independent
job contexts. A fresh live monitor queues every routine immediately to
check and update reports before their recurring UTC schedule. Repeated
starts reuse the active monitor without another initial pass; stop it with
`scheduler.shutdown(wait=True)` before changing its
output mode or clearing caches. `aranami.run(dry=True)` also monitors
continuously, writing previews at each scheduled time. The caller keeps
its process alive. Keep notebook detection, sleep loops, and dependency
installation out of runtime code. Dependencies belong in wheel metadata.

`aranami.run_once(date=None, dry=False, tasks=None)` executes immediately
without starting or changing schedules. The optional date is a UTC report
anchor; task names select independently runnable reports. Both entry points
default to live publication. Job-level APIs use the same `dry` keyword and
accept an optional `context`.

Keep dependencies flowing from the public API and runner through jobs,
services, sources, and support:

- The public API starts monitoring or requests an immediate pass. The
  monitor dispatches independently runnable jobs on their schedules; the
  runner coordinates selected jobs in a single pass and reports failures
  after the remaining selected jobs run.
- Jobs select destination pages, read their current text, choose report
  settings, and construct publication proposals. Shared execution
  infrastructure
  collects proposed edits and writes one complete-run Markdown dry-run report
  with plain wikitext companions.
- Services process supplied page content and return updated wikitext or report
  data. They do not select or read destination pages or construct publication
  proposals. Pass project selections, ranking periods, task-force lists, and
  other routine choices from jobs explicitly.
  Put site-specific report and extractor rules under `services/zhwiki/` or
  `services/enwiki/`. Keep domain scope, such as video-game keyword rules,
  in those services. Share mechanics only where their semantics agree.
- Sources provide read-only external access. Keep replica connections and
  SQLAlchemy Core selections inside the Quarry source adapter. Do not use
  raw SQL strings or write to replicas.
- Support modules have narrow names such as `logs`, `regions`, and
  `pageviews_cache`. Do not add generic `utils` or `helpers` containers.

For quality analysis, `services/quality_contents.py` coordinates replica
reads, wiki extraction, measurements, and Polars statistics using a profile
selected once for the supplied site. The English and Chinese
`quality_contents.py` modules own their project titles, grade and importance
names, template contracts, language, and extractor/prose choices. Inject
those values into the shared workflow and its source adapter; do not branch
on the wiki inside the per-article analysis loop or embed grade vocabularies
in the shared workflow or replica query.
`services/quality_milestones.py` interprets promotion history using the
verified profiles in `services/enwiki/` and `services/zhwiki/`. Keep those
wiki-specific action, result, template, and prose rules in services.

Reusable mechanics belong in support: `dates.py` parses dates,
`templates.py` reads template parameters, `wikitext.py` cleans scalar values,
`prose.py` extracts readable text using an explicit profile, and `words.py`
counts text by language. Support must not import services or choose quality
classes. Complete promotion dates use `parse_complete_date`; DYK's historical
partial-date handling uses `parse_date`. Preserve that distinction.

Quality analysis remains explicitly callable outside the routine runner.

PexBot refreshes are restricted to Chinese Wikipedia. Configure their
allowed page roots in `src/aranami/monitor.py` through
`TASK_DEFINITIONS["pexbot"].prefixes` before building a replacement wheel.
The job's `PEXBOT_PREFIXES` alias uses that definition. The service receives
these prefixes explicitly and contains no project-specific scope. A root
matches its exact page and slash-delimited subpages; a trailing slash
matches only descendants. Titles and namespace aliases are normalized by
Pywikibot. An empty tuple disables PexBot selection without querying its
subscription category. A manual invocation may override the wheel defaults:

```python
from aranami.jobs import pexbot

pexbot.run(prefixes=["WikiProject:电子游戏/数据库报告"], dry=True)
```

Other sites are rejected before category queries or refresh requests.
Dry runs record selected actions without contacting the refresh endpoint;
live refreshes require an explicit successful `end` stream event.

## Polars and Parquet

Read the [stable Polars Python API reference][1] and its [Pandas migration
guide][2]. Write native Polars operations rather than translating Pandas
idioms literally:

- Express selection, filtering, derived columns, joins, grouping, aggregation,
  ranking, and window calculations with native expressions. Combine independent
  expressions in a single context where possible.
- Keep keys in explicit columns. Avoid index emulation, mutable column
  assignment, row-wise `apply`, and conversions through Pandas.
- Keep explicit schemas, including empty results. A missing observation is
  `null`; a measured zero is valid data.
- Use lazy scans when they benefit file-backed work, and retain the existing
  eager contracts of source adapters.
- Iterate in Python at network-request and wikitext-parser boundaries, or
  for small scalar mappings. Do not implement tabular joins or aggregates
  with Python row loops.
- Save disposable tabular caches as Parquet and read, merge, deduplicate,
  and trim them with Polars.

## Logs, caches, and dry runs

All runtime paths are relative to the caller's current directory, never the
installed package or `site-packages`:

- `logs/aranami.YYYY-MM-DD.log` is the one universal file for a UTC date.
  All routines append to it, identifying their names, starts, completion,
  elapsed time, skips, and failures. Retain the latest 90 completed dates
  alongside the current day. Do not configure the root logger at import time.
- `cache/` is visible and disposable. Manual deletion or
  `aranami.run(clear_cache=True)` forces cached data to rebuild. Deleting it
  must not lose authoritative state or alter the report with identical
  upstream data. Cache replacements must be atomic; malformed snapshots
  should be ignored with a diagnostic.
- `dry-run/` holds one compact UTF-8 Markdown summary per run, with UTC
  start/end times, an overall status, and one task entry per routine.
  Group every PexBot refresh proposal under its single routine task.
  Include target site/page, edit summary, status tags, and a relative link
  to each proposal's flat `.wikitext` companion. Keep exact proposed text
  only in that companion, not embedded in the Markdown. Use the unique
  report stem and an edit number for filenames, never a wiki title as a
  path. Failed or deferred tasks make a mixed run `partly success`. The
  `scripts/run_aranami.py` launcher anchors output beside itself. External
  PexBot generation cannot be previewed without triggering writes, so
  record the proposed action and skip its endpoint.

The new-page routine retains the latest 100 complete UTC dates by default,
through yesterday. It fills absent dates, prunes older records, and reuses
existing daily records without duplication on hourly calls. Each missing
date combines page creations with revisions tagged `mw-removed-redirect`
in the same half-open UTC interval. Use replica revision tags for historical
dates; recent changes do not cover the full retained window. Deduplicate
the combined candidates by page ID before preloading their current text.
Keep the same namespace, non-redirect, and keyword filters for both sources,
and append ` — 自重定向页改写` to matching conversion entries.
End each resolved list item with
`<!-- 页面ID 12345 · 创建时间 2026-08-05 11:22:33 -->`, using the page's
earliest revision timestamp in UTC. Append it after the conversion note
when present. Enrich retained rows with missing metadata comments without
rerunning daily discovery, and preserve saved comments during icon refreshes
and later runs. Missing creation timestamps are `未知`; unresolved page
identities leave the row unchanged with a diagnostic.

Pageviews processes at most one missing report date per call.
Read its published checkpoint only from `<!-- report date YYYY-MM-DD -->`
and emit the same marker in generated reports. Ignore obsolete date comments
and ranking-template dates; fail before building without a valid checkpoint
or when recognized dates conflict.
Keep `DATA_READY_HOUR=18` in
`src/aranami/jobs/pageviews.py` as the routine's data-readiness rule.
Yesterday becomes eligible
when the actual invocation starts at or after 18:00 UTC; the hourly `HH:59`
schedule first attempts it at 18:59 UTC. Before 18:00, older missing dates
remain eligible. Apply the same cutoff to live, dry, and manual job
invocations, using `JobContext.started_at` rather than the chosen report
anchor for readiness. Report dates must also be before the requested anchor;
a supplied anchor cannot bypass the actual cutoff. Existing `lag-days`
attributes no longer affect the selected report date. If at least 95% of
queried articles have no
observation for that date, skip the edit and leave both the wiki date marker
and healthy cache unchanged so the next call retries. Explicit zeroes remain valid.
Its Parquet cache keeps an 800-day history by default, reuses sufficient
coverage, fetches only missing tails, and rebuilds histories that do not
cover the required report intervals. New articles receive their own history
fetch. Coverage metadata distinguishes an observed empty interval from an
interval that was never queried.

Keep the source adapter's one-attempt contract. The Pageviews routine skips
an article immediately after HTTP 504, excluding it from the current rankings
and availability check without changing its cached history or coverage.
Skipped articles remain eligible for requests in later invocations. Retry
502/503 responses up to three times; exhausted retries defer publication
until a later invocation. Preserve completed requests in a separate pending
Parquet checkpoint for the target date, leaving the healthy snapshot intact.
Resume pending work on the next attempt, commit it only after the global
availability guard passes, and discard it when the guard rejects that day.

## Managed ranges and on-wiki configuration

Use HTML comments to mark generated ranges. The canonical form is:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...generated daily records...
<!-- aranami end="new-pages" -->
```

Settings belong on the opening comment where practical. For example:

```wikitext
<!-- aranami begin="page_views" history-days="800" missing-percent="95" -->
...generated popularity report...
<!-- aranami end="page_views" -->
```

Preserve the attributes when replacing content and validate values before
requests or publication. Code supplies documented defaults. Current settings
include new-page retention, Pageviews history and availability threshold,
and assessment-list source categories.

Migrate legacy update comments during a successful rewrite. Keep functional
`<section begin="..."/>` and `<section end="..."/>` tags used by labeled
section transclusion, placing Aranami comments inside them. Refuse ambiguous,
missing, reversed, or duplicate markers instead of replacing a broad span.
Preserve all text outside the managed range.

## Wikitext, templates, and site semantics

Use `mwparserfromhell` to inspect and construct non-trivial wikitext, including
templates, links, headings, and HTML comments. Preserve editable parser
objects where possible instead of using regular expressions over nested
wiki syntax. Plain fixed text does not need unnecessary parsing.

Before adding or changing a template call, read that wiki's actual template
source, documentation, redirects, and invoked Lua module/configuration.
Record the source and revision used when a contract or extractor mapping
needs to be audited. Do not guess parameter names, action aliases, or
whether a template already emits list markup.

Use `pywikibot.Page(site, name, ns=...)` and the site's namespace/title rules
for template identity and talk-page conversion. Do not maintain regex lists
of translated namespace prefixes. On Chinese Wikipedia, `PJ:` can resolve
to the WikiProject namespace; it is not necessarily a Template title.

Keep Article history and DYK interpretation separate by wiki. Chinese rules
come from [zhwiki's Article history configuration][3]; English rules come
from [enwiki's configuration][4]. For example, English GAR can record a GA
listing, whereas Chinese GAR is a delisting review. Only accept action and
result spellings supported by the appropriate configuration. A rule for
one wiki is not evidence for another.

Unknown dates are `None` in Python and null in Polars. Sort them deliberately,
label them clearly in reports, and omit them from dated statistics. Do not
invent sentinel calendar dates such as year 8888.

Use high-level Pywikibot reads only for information replicas cannot supply,
such as current text. Preload collections of pages. Services return report
data or transformed text; jobs create structured edit proposals and save
pages with informative summaries and check that source text has not changed
during report construction.

Compose English edit summaries through `support.edit_summary.EditSummary`
with a maximum of 255 UTF-8 bytes. Chinese characters typically use three
bytes; shorten complete mentions or clauses without splitting wiki links.
Mark every article-title link with `«...»`, including optional Chinese
sitelinks in English-report summaries and Pageviews leaders. Preserve
complete delimiter pairs when shortening a summary.
At publication, end every live edit and dry-run proposal summary with
`Executed by 𝙰𝚛𝚊𝚗𝚊𝚖𝚒 in 21′53.95″.` using measured minutes, seconds,
and hundredths.
For durations below one minute, omit zero minutes and the leading seconds
zero, for example `Executed by 𝙰𝚛𝚊𝚗𝚊𝚖𝚒 in 7.12″.`.
Measure from the current routine's start until each proposal is ready,
before checking or saving its target. Direct publications use context
creation time. Reserve space for the complete suffix within the same
255-byte limit and replace an earlier suffix if the proposal is reused.
Dated summaries use compact English dates such as `15 Sep 2026` and begin
with `Updated for <date>.`. Pageviews groups daily, weekly, monthly,
seasonal (quarterly), and yearly periods under each leading title in a
`Hottest:` sentence, linking each title once. Join grouped periods with
`and`, including a serial comma for three or more periods, and separate
titles with semicolons. Keep places gained beside the corresponding period,
omitting movement when the leader remains first or has no previous rank.
If the complete summary and timing suffix exceed 255 bytes, omit yearly
first, then seasonal, and then the remaining longest periods as needed.
Do not add omission counts to this sentence. New-page summaries use a date
and matched article/non-article counts only when the latest daily list was
filled during the run. Reused latest records use `Updated class icons.`
instead. Mention older daily records filled during either kind of update.

Real-time summaries state the current item total, grouped additions and
removals, and elapsed time. Keep summaries factual, without instructions
or suggested actions. Store stable article page IDs in local Parquet
membership snapshots under `cache/`, never in generated report wikitext.
Use IDs to recognize renames, grade changes, and movement between DYK
candidate and completed lists. Validate each snapshot against the exact
destination text and advance it only after successful live publication.
DYK summaries distinguish `New nominee`, `Passed nominee`, and
`Failed nominee`. A repeat nominee passes when it gains a completion
date. A departing candidate absent from the completed list fails.
Unchanged historical completion dates cannot distinguish failed repeats
from delayed candidate cleanup, so they do not imply either outcome.
Keep removed historical completed entries separate from failed nominees.
Dry runs may seed the original membership but must not advance it to the
proposed report. Missing or stale snapshots fall back to title matching,
or existing Wikidata IDs for English reports, without changing the report
body. In real-time membership reports, read old ID comments only for
migration and remove them when rewriting generated rows. The dated
new-page list retains its requested ID and creation-time comments.
Both English key-article reports list English
additions and removals as `[[:en:Title]]` links and match English page IDs
across title changes.

## Verification and releases

Follow [AGENTS.md](AGENTS.md) and the scoped package instructions for complete
rules. Document Python modules and public callables with Google-style
docstrings. Use focused offline tests for query results, report behavior,
cache recovery, configuration, and publication boundaries. Never run a live
wiki-editing workflow as a local verification step.

Run the relevant unit and integration tests, then the required checks:

```shell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv build --wheel --clear
```

The standalone `scripts/run_aranami.py` launcher installs the newest local
Aranami wheel by modification time from the current directory and `dist/`,
then starts live monitoring through `aranami.run(dry=False)` in a fresh
process. A fresh monitor checks every routine immediately, then follows
the recurring UTC schedules. Keep the child process alive until an
interrupt, then call `scheduler.shutdown(wait=True)` so active jobs finish.
Forward interrupts from the launcher to the child's separate process
session and wait for its shutdown. Keep its main block to the two
calls `install_wheel()` and `run()`, with wheel discovery and cleanup in
private helpers. Match `aranami-*.whl` without
parsing or comparing filename versions. After successful installation,
keep the selected wheel and remove other local wheels with equal or earlier
modification times. Explicit selections preserve more recent uploads.
Use the active interpreter for both pip and a fresh monitor process so
notebook imports cannot keep an older package loaded. Anchor runtime output beside
the script, or in the notebook's current directory for pasted cells.

Before every explicit wheel build, advance to an unused `.devN` or `.postN`
version and regenerate `uv.lock`. Only the user chooses major/minor versions.
Update the evolving `CHANGELOG.md` section with a UTC timestamp before
handoff. Keep commits cohesive and use Conventional Commits.

GitHub Actions uses `ci.yml` for pull requests and pushes to `trunk`,
`release.yml` for `v<version>` tags, and reusable `validate.yml` for both.
Validation checks the lockfile, lint, formatting, and offline unit and
integration tests, then builds and retains a wheel. Test builds use distinct
suffixes derived from the workflow run and attempt; their version and lock
changes stay in the runner. Release tags must match the formal version in
`pyproject.toml`. Tagged releases keep that version and publish the verified
wheel as a GitHub Release asset with notes from its changelog section.

[1]: https://docs.pola.rs/api/python/stable/reference/index.html
[2]: https://docs.pola.rs/user-guide/migration/pandas/
[3]: https://zh.wikipedia.org/wiki/Module:Article_history/config
[4]: https://en.wikipedia.org/wiki/Module:Article_history/config
