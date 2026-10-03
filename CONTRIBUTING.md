# Contributing to Aranami

Aranami is developed, tested, and built on a development computer. Its
pure-Python wheel is installed on Wikimedia PAWS. PAWS supplies the existing
Wikipedia OAuth session and Wiki Replica credentials; do not add credential
prompts, a user/password interface, or secrets to the wheel.

## Runtime and architecture

`aranami.run()` executes the routine once and returns. The caller may invoke
it hourly; the package must not run a scheduler, background daemon, sleep
loop, or notebook callback. Dependency installation belongs in the wheel's
metadata, not in runtime code or a setup notebook.

Keep dependencies flowing from the public API and runner through jobs,
services, sources, and support:

- The public API exposes the single-run entry point; the runner coordinates
  independently runnable jobs and reports failures after remaining jobs run.
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

The former temporary quality-analysis and character workflows remain
explicitly callable; they are not part of the routine runner.

PexBot refreshes are restricted to Chinese Wikipedia. Configure their
allowed page roots in `src/aranami/config.py` through `PEXBOT_PREFIXES`
before building a replacement wheel. The subscription service receives
these prefixes explicitly and contains no project-specific scope. A root
matches its exact page and slash-delimited subpages; a trailing slash
matches only descendants. Titles and namespace aliases are normalized by
Pywikibot. An empty tuple disables PexBot selection without querying its
subscription category. A manual invocation may override the wheel defaults:

```python
from aranami.jobs import pexbot

pexbot.run(prefixes=["WikiProject:电子游戏/数据库报告"], dry_run=True)
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
existing daily records without duplication on hourly calls.

Pageviews processes at most one missing report date per call. The default
publication lag is two days. If at least 95% of queried articles have no
observation for that date, skip the edit and leave both the wiki date marker
and healthy cache unchanged so the next call retries. Explicit zeroes remain valid.
Its Parquet cache keeps an 800-day history by default, reuses sufficient
coverage, fetches only missing tails, and rebuilds histories that do not
cover the required report intervals. New articles receive their own history
fetch. Coverage metadata distinguishes an observed empty interval from an
interval that was never queried.

Treat gateway timeouts as failed requests, never as missing observations or
zeroes. Keep the source adapter's one-attempt contract; the routine may retry
502/503/504 responses up to three times. Exhausted retries defer publication
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
<!-- aranami begin="page_views" history-days="800" lag-days="2" missing-percent="95" -->
...generated popularity report...
<!-- aranami end="page_views" -->
```

Preserve the attributes when replacing content and validate values before
requests or publication. Code supplies documented defaults. Current settings
include new-page retention, Pageviews history/lag/availability threshold,
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

Before every explicit wheel build, advance to an unused `.devN` or `.postN`
version and regenerate `uv.lock`. Only the user chooses major/minor versions.
Update the evolving `CHANGELOG.md` section with a UTC timestamp before
handoff. Keep commits cohesive and use Conventional Commits.

[1]: https://docs.pola.rs/api/python/stable/reference/index.html
[2]: https://docs.pola.rs/user-guide/migration/pandas/
[3]: https://zh.wikipedia.org/wiki/Module:Article_history/config
[4]: https://en.wikipedia.org/wiki/Module:Article_history/config
