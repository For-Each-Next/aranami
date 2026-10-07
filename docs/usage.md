# Aranami usage

[Project overview](../README.md) · [Documentation index](README.md)

Aranami (<span lang="ja">荒波</span>, "rough waves") maintains Wikipedia
reports, primarily for WikiProject Video games on Chinese Wikipedia.
Run it on [Wikimedia PAWS][1]
to publish reports, preview changes, or collect data for one-time analysis.

## Choose an interface

| What you want to do | Interface | What changes or comes back |
|---|---|---|
| Start the routine monitor | `aranami.run()` | Returns a running scheduler that checks each report immediately on a fresh start, then updates it at its scheduled UTC time. |
| Start with the next scheduled run | `aranami.run(run_immediately=False)` | Returns a running scheduler that first runs each routine at its next UTC trigger. |
| Keep previewing on the same schedule | `aranami.run(dry=True)` | Returns a running scheduler that writes proposals under `dry-run/` at each scheduled time, without wiki edits or PexBot refresh requests. |
| Run reports immediately | `aranami.run_once(...)` | Runs all routines once, regardless of their schedule, and returns `None`. Use `dry=True` for a preview. |
| Run selected reports immediately | `aranami.run_once(tasks=["new_pages", "pageviews"], ...)` | Runs only the selected routines once; `tasks="new_pages"` selects one routine. |
| Run with a chosen date | `aranami.run_once(date=..., ...)` | Sets a UTC anchor for date-dependent routines; reads current source data. |
| Customize one routine's destinations or settings | `aranami.jobs.<routine>.run(...)` | Updates that routine immediately, or writes a preview when `dry=True`. |
| Get quality articles and listing dates | `analyze_quality_contents(site)` | Returns an article table; no wiki edits or automatic result export. |
| Fetch data or process supplied text | The source and support interfaces below | Returns data, displays a preview, or transforms text; file-writing exceptions are identified below. |

## Install and monitor on PAWS

Upload the wheel and [scripts/run_aranami.py](../scripts/run_aranami.py) to PAWS.
Run this from a directory containing Aranami wheels:

```shell
python scripts/run_aranami.py
```

The launcher installs the most recently modified `aranami-*.whl` from the
current directory or `dist/`, installs its dependencies, and calls
`aranami.run()` with the installed version and the launcher's configuration.
By default, a fresh monitor checks every routine immediately, then keeps
running on its UTC schedule. It publishes changed wiki pages and requests
PexBot refreshes. The launcher stays active until you interrupt it with
Ctrl+C or the notebook's stop
button; shutdown waits for active jobs to finish. After installation, it
removes other local Aranami wheels with equal or earlier modification times.
It retains all wheels if installation fails and does not start the routines.
Set `DRY_RUN` and `RUN_IMMEDIATELY` near the top of the script before running:

```python
DRY_RUN = False  # True writes previews instead of publishing.
RUN_IMMEDIATELY = True  # False waits for the next scheduled times.
```

To choose a wheel yourself:

```shell
python scripts/run_aranami.py "path/to/aranami-<version>.whl"
```

If the script is beside the notebook, omit `scripts/`. In a notebook cell:

```python
%run scripts/run_aranami.py
```

You can also paste the complete script into one cell. File execution places
`logs/` and `cache/` beside the script; a pasted cell uses the
notebook's current directory. The launcher uses the newly installed version
even if the notebook previously imported an older one. PAWS environments are
temporary, so run it again after a server restart. See [PAWS guidance][3].

The script's `main()` installs a wheel and starts its schedules.
Its `install_wheel(argv=None)` installs the selected wheel and removes older
local uploads; `run_schedule()` monitors the installed package using those
configuration constants. When importing the script, pass installation
arguments explicitly:

```python
from scripts.run_aranami import install_wheel, run_schedule

install_wheel([])  # Discover and install the newest upload.
# install_wheel(["path/to/aranami-<version>.whl"])  # Select an upload.
run_schedule()
```

## Start the routine monitor

`aranami.run(*, dry=False, clear_cache=False, run_immediately=True)` starts
the packaged monitor and returns an APScheduler `BackgroundScheduler`.
Each routine runs at its own UTC time in the table below. The call returns
immediately. On a fresh start, `run_immediately=True` checks all routines
immediately before continuing on those schedules. Use `run_immediately=False` to wait for each
routine's next scheduled time. This option applies to live and preview modes.

```python
import aranami

scheduler = aranami.run()  # Start scheduled publication.
scheduler.print_jobs()  # Show jobs and their next UTC run times.
# scheduler.shutdown(wait=True)  # Stop and wait for any active jobs.
```

Use `aranami.run(dry=True)` to keep building local previews on the same
schedule, including an immediate first preview by default. It continues
fetching current data, updating disposable caches, and writing proposals
while making no wiki edits or PexBot refresh requests.
APScheduler is installed with the wheel; no notebook setup cell is required.
The monitor reuses PAWS authentication and Wiki Replica access.

The monitor remains active while its Python process or PAWS kernel stays
alive. Restarting the kernel removes its schedule; call `aranami.run()`
again after importing the package. Repeated calls with the same `dry` value
reuse the running scheduler without another initial pass, regardless of
`run_immediately`. To change the output mode or clear the cache, first call
`scheduler.shutdown(wait=True)`. Changing modes or passing
`clear_cache=True` while the monitor is running raises `ValueError`.
A paused monitor stays paused until you call `scheduler.resume()`.
`clear_cache=True` removes disposable `cache/` data before starting a new
monitor. For direct notebook imports, restart the kernel if it had loaded a
previous wheel.

Every executed job appends to `logs/aranami.YYYY-MM-DD.log`, using the actual
UTC execution date. A job failure is logged and later scheduled jobs remain
active. Unchanged pages are not saved. Data reuse may create or update
`cache/`. A dry run writes
`dry-run/aranami-*.md` and a linked `.wikitext` file for each proposed edit.
The wikitext files contain complete proposed page text. Earlier previews and
completed proposals remain available if another routine fails. PexBot preview
entries describe refreshes; their generated wikitext is unavailable without
requesting the refresh.

## Run reports immediately

`aranami.run_once(*, date=None, dry=False, tasks=None, clear_cache=False)`
executes once at the time you call it, regardless of the routine schedule,
then returns `None`. It does not start the monitor or wait for cron times.

```python
import aranami

aranami.run_once(dry=True)  # Preview all routines now.
aranami.run_once(tasks="new_pages")  # Publish new-page changes now.
aranami.run_once(
    tasks=["dyks", "pageviews"], dry=True
)  # One combined preview.
```

Omitting `tasks` runs all six routines. Accepted names are `dyks`,
`new_pages`, `assessment_lists`, `pageviews`, `enwp_key_articles`, and
`pexbot`. Independent selected routines continue after a failure; failures
are raised together after the remaining routines and combined preview
finish. `clear_cache=True` removes disposable cache data before the pass;
stop a running monitor before requesting cache removal.

### Customize one routine

Import the routine you need and call its `run()`:

```python
from aranami.jobs import dyks, new_pages, pageviews

new_pages.run(dry=True)
# dyks.run()       # Publish only DYK changes.
# pageviews.run()  # Publish at most one missing popularity-report day.
```

All six routines return `None`. All accept keyword arguments `dry=False`
and `context=None`. A standalone call writes its own daily log and, in dry-run
mode, its own preview. With a supplied context, the context's `dry` value
takes precedence and the caller writes the combined preview with
`context.write_report()`.

## Routine jobs and UTC schedule

These six independent routines maintain Chinese Wikipedia's video-game
project reports. By default, a fresh monitor checks all six immediately
in live or preview mode, then follows their individual UTC schedules.
Live calls save changed pages;
`dry=True` writes local proposals. Each fixed destination below has its
own row under `WikiProject:电子游戏/`.

Default pages and UTC cron schedules are defined in `TASK_DEFINITIONS` in
[src/aranami/monitor.py](../src/aranami/monitor.py). Edit it and build a
replacement wheel to change those defaults. The job modules use the same
definitions from which the monitor derives `SCHEDULES`.
The schedule column gives trigger times, rather than execution duration;
the notebook's local timezone has no effect. `aranami.run_once()` runs
immediately regardless of these times. Each routine permits one active
instance and combines missed triggers into one attempted run.

| Routine | Updated page | [Schedule](../src/aranami/monitor.py) | Notes |
|---|---|---|---|
| [dyks.py](../src/aranami/jobs/dyks.py) | [新条目推荐](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新条目推荐) | Hourly at `HH:00` | Refreshes completed DYK entries, candidates, assessment grades, and quarterly cumulative counts of completed DYK entries. [(Details)](#dyk) |
| [new_pages.py](../src/aranami/jobs/new_pages.py) | [新进条目/关键词筛选](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新进条目/关键词筛选) | Daily at `00:07` | Lists keyword-matched page creations and redirect conversions through yesterday UTC; retains 100 dates by default, fills gaps, and skips complete date windows. [(Details)](#new-pages) |
| [assessment_lists.py](../src/aranami/jobs/assessment_lists.py) | [A-Class](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/A) | Daily at `01:31`, `07:31`, `13:31`, `19:31` | Updates the list. [(Details)](#assessment-lists) |
| [assessment_lists.py](../src/aranami/jobs/assessment_lists.py) | [A-Class lists (AL)](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/AL) | Daily at `01:31`, `07:31`, `13:31`, `19:31` | Updates the list. [(Details)](#assessment-lists) |
| [assessment_lists.py](../src/aranami/jobs/assessment_lists.py) | [A-Class review (ACC)](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/ACC) | Daily at `01:31`, `07:31`, `13:31`, `19:31` | Updates the list. [(Details)](#assessment-lists) |
| [assessment_lists.py](../src/aranami/jobs/assessment_lists.py) | [Bplus-Class](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/Bplus) | Daily at `01:31`, `07:31`, `13:31`, `19:31` | Updates the list. [(Details)](#assessment-lists) |
| [assessment_lists.py](../src/aranami/jobs/assessment_lists.py) | [Bplus-Class review (BPAN)](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/BPAN) | Daily at `01:31`, `07:31`, `13:31`, `19:31` | Updates the list. [(Details)](#assessment-lists) |
| [assessment_lists.py](../src/aranami/jobs/assessment_lists.py) | [Project peer review (PPR)](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/PPR) | Daily at `01:31`, `07:31`, `13:31`, `19:31` | Updates the list. [(Details)](#assessment-lists) |
| [pageviews.py](../src/aranami/jobs/pageviews.py) | [热门条目](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/热门条目) | Hourly at `HH:59` | After 18:00 UTC, attempts to update yesterday's pageview data. Catches up at most one missing day per call to avoid a burst of requests. [(Details)](#pageviews) |
| [enwp_key_articles.py](../src/aranami/jobs/enwp_key_articles.py) | [Top-importance and High-importance English articles](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科重要条目) | Daily at `23:24` | Refreshes Top-importance and High-importance English articles, their Chinese counterparts, assessment grades, and counts. [(Details)](#english-comparisons) |
| [enwp_key_articles.py](../src/aranami/jobs/enwp_key_articles.py) | [Featured and good English content](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科优质条目) | Daily at `23:24` | Refreshes English featured articles, featured lists, and good articles, their Chinese counterparts, assessment grades, and counts. [(Details)](#english-comparisons) |
| [pexbot.py](../src/aranami/jobs/pexbot.py) | Subscribed pages under [数据库报告](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告) | Daily at `00:00` | Requests PexBot to refresh the listed report pages. [(Details)](#pexbot) |

All linked routines expose `run(...)` under `aranami.jobs`. The following
sections describe their results and destination settings.

### DYK

DYK refreshes completed entries, candidates, assessment grades, and quarterly
cumulative counts of completed DYK entries. Pass `title` to choose another
report destination.
Edit summaries label nomination changes as `New nominee`, `Passed nominee`,
or `Failed nominee`, followed by the linked article titles.
Its `statistics_title` defaults to
`c:Data:Zhwiki_WikiProject_Video_Games_DYK_Annual_Statistics.tab`. It labels
the copyable hidden payload of quarterly cumulative counts of completed
DYK entries; the routine does not save that Commons page.

### New pages

The new-page list retains 100 UTC dates by default, through yesterday. One
call fills all missing dates in that window while reusing existing daily
records. If every retained date is present, the call leaves the report
unchanged. When filling gaps, it also refreshes assessment icons and missing
metadata for retained entries, including earlier dates. Pass `title` to
choose another destination.

Each new-page date also searches edits tagged `mw-removed-redirect` on
that UTC day. Keyword-matched pages that are currently normal pages enter
the same list with ` — 自重定向页改写` after their discussion link. A page
appears once per date even if it was also created or converted repeatedly
that day. Converted pages follow the same namespace and keyword rules as
creations. Existing daily records are reused; missing dates receive both
searches.

Each item ends with an HTML comment such as
`<!-- 页面ID 12345 · 创建时间 2026-08-05 11:22:33 -->`.
The timestamp is the page's earliest revision time in UTC, including for
redirect conversions. Retained rows receive missing comments when their
metadata is available, and saved comments persist across later updates.
An unavailable creation time is labeled `未知`.

### Assessment lists

Assessment lists refresh their entries, grades, and configured review
icons when rebuilt. Select lists and destinations with
`lists=[(title, AssessmentList(...)), ...]`.

### Pageviews

Pageviews advances by one missing report date per call and retries later
if the day's data is unavailable. Yesterday's observations become
eligible at 18:00 UTC; the regular hourly `HH:59` schedule first attempts
them at 18:59 UTC. Earlier missing dates remain eligible before that
cutoff. A scheduled attempt may start up to 3,500 seconds late.
Assessment grades refresh when a new daily popularity report is built.
Pass `title` to choose another destination and `settings=REPORT_SETTINGS`
to choose project membership and ranking periods.

### English comparisons

The importance report lists Top-importance and High-importance English
articles in alphabetical sections and refreshes their counters.
The quality report lists featured and good English content: featured articles
(FA), featured lists (FL), and good articles (GA). It groups all three classes
by the year of their latest English promotion or listing, with the newest
dates first. Dated entries end with `<!-- YYYY-MM-DD -->`; unresolved dates
appear under `年份未知`. Same-day entries use English title sort keys.
The routine replaces only the quality page's managed `body` range, preserving
its manually maintained description and counters. Both reports refresh their
Chinese counterparts and assessment grades. Choose destinations with `targets`
containing both `important` and `quality`.

Quality summaries distinguish `GA listed` and `GA delisted` from
`FA prompted`, `FL prompted`, `FA removed`, and `FL removed`. A change within
the quality list includes the previous English class, such as `(from GA)`.
Importance changes do not produce quality-summary events. Dates come from the
existing English Article history and GA extractors. A disposable Parquet cache
stores resolved and unresolved dates by class, discussion revision, and
template aliases; unchanged discussions reuse the cache without content reads.

### PexBot

PexBot refreshes subscribed report pages and controls its own generated
content. Pass `prefixes` to override allowed page roots, or `prefixes=[]`
to disable refreshes. Only Chinese Wikipedia is supported.

### Use a chosen date

Pass a `datetime.date` as `date` to `aranami.run_once()` to choose its UTC
anchor; omitting it uses the current UTC date. The selected routines run
immediately. For new pages, this is the exclusive cutoff: to include
October 1, use October 2.

```python
from datetime import date

import aranami

aranami.run_once(date=date(2026, 10, 2), dry=True, tasks="new_pages")
```

Direct routine calls can set the same anchor through `JobContext.today`.
Use a context when customizing a routine or combining its preview with other
direct calls:

```python
from datetime import date, timedelta

import pywikibot

from aranami.jobs import JobContext, new_pages

report_day = date(2026, 10, 1)
context = JobContext(
    site=pywikibot.Site("zh", "wikipedia"),
    today=report_day + timedelta(days=1),
    dry=True,
)
try:
    new_pages.run(context=context)
finally:
    preview_path = context.write_report()
    print(preview_path)
```

This fills the retained new-page dates through `report_day`, rather than
limiting the run to a single daily record. You can pass the same context to
several routines to collect one preview. The anchor controls date cutoffs and
statistics; it does not reconstruct historical page text or project
membership. Assessment lists, English comparisons, and PexBot use current
sources regardless of the anchor. Log and preview timestamps still use the
actual execution time.

For popularity rankings, report dates must be before the anchor (`date` or
`context.today`) and eligible at the actual invocation's UTC start time.
Yesterday becomes eligible at 18:00 UTC; older missing dates can still be
processed before then. A chosen anchor does not bypass that limit.
The routine takes the next missing date from the
destination page's `<!-- report date YYYY-MM-DD -->` checkpoint; it does not
jump to the chosen date. Only this date comment is recognized. To build
an exact day's rankings without changing a page, use the one-time report
builder:

```python
from datetime import date, timedelta
from pathlib import Path

import pywikibot

from aranami.jobs.pageviews import REPORT_SETTINGS
from aranami.services.zhwiki.pageviews import build_report

report_day = date(2026, 10, 1)
report = build_report(
    pywikibot.Site("zh", "wikipedia"),
    report_day
    + timedelta(days=1),  # Exclusive end; report.data_date is October 1.
    settings=REPORT_SETTINGS,
)
Path("pageviews-2026-10-01.wikitext").write_text(report.text, encoding="utf-8")
```

`build_report()` returns ranking-section text and leader metadata, makes no
wiki edits, and may update `cache/`. The explicit `write_text()` creates the
named file in the caller's current directory. Incomplete or unavailable
traffic data raises `PageviewsUnavailableError` or `PageviewsDeferredError`.

## One-time tools

### Get quality articles and their listing dates

`aranami.services.quality_contents.analyze_quality_contents(site, *,
most_recent=True)` lists current featured articles, featured lists, and good
articles in the English or Chinese video-game project. It returns titles,
grades, importance, listing dates and their sources, text lengths, and
revision IDs. Unknown dates remain missing; `most_recent=False` selects the
earliest supported successful promotion instead of the latest.

```python
import pywikibot

from aranami.services.quality_contents import (
    analyze_quality_contents,
    length_statistics,
    year_statistics,
)

articles = analyze_quality_contents(pywikibot.Site("zh", "wikipedia"))
listing_dates = articles.select(
    "article_title", "quality_status", "importance", "listed_date"
)
print(listing_dates)

# These exports explicitly create files in the caller's current directory.
listing_dates.write_csv("quality-articles-zh.csv")
length_statistics(articles).write_csv("quality-lengths-zh.csv")
year_statistics(articles).write_csv("quality-listing-years-zh.csv")
```

Use `pywikibot.Site("en", "wikipedia")` for the English project. These tools
make no wiki edits or automatic result exports. Chinese word counting may
create disposable files under `cache/words/`.

| Interface in `aranami.services.quality_contents` | Returned result |
|---|---|
| `analyze_quality_contents(site, *, most_recent=True)` | Article list with listing dates and measurements. |
| `length_statistics(frame)` | Counts, median prose length, and central 68%/95% ranges by article grade. |
| `year_statistics(frame)` | Article counts by listing year, grade, and importance; omits unresolved dates. |

### Fetch daily traffic, query data, and inspect templates

These interfaces return data or display it. They do not edit Wikipedia or
export result files automatically. Traffic ranges include `start` and exclude
`stop`; missing observations are distinct from measured zeroes.

| Public interface | What it does and returns |
|---|---|
| `aranami.sources.pageviews.fetch_data(site, page, start, stop)` | Returns a list of `DailyPageviews(date, pageview)` observations for one page. |
| `aranami.sources.pageviews.fetch_data_dataframe(site, page, start, stop)` | Returns daily observations with `page`, `date`, and `pageview` columns. |
| `aranami.sources.pageviews.massive(site, pages, start, stop)` | Returns daily observations for multiple pages over the same interval. |
| `aranami.sources.quarry.Replica(project, extension=None)` | Selects a Wiki Replica. `Replica.from_site(site, *, extension=None)` selects the supplied site's replica; `Replica.wikidata_terms()` selects Wikidata terms. |
| `Replica.query(statement, *, parameters=None, schema_overrides=None)` | Returns a `QueryFrame`; `.pipe(postprocessor)` adds a table transformation and `.collect()` reads the results. Connection properties are `hostname`, `database`, `url`, and `engine`. |
| `aranami.sources.quarry.tables` | Provides read-only table definitions for custom queries; see the mapping catalog below. |
| `aranami.support.gtshow(frame, *, first=5, last=5)` | Displays a compact table preview in JupyterLab; returns `None` and creates no file. |
| `aranami.support.get_templates(text, template, site)` | Returns matching editable template nodes from supplied wikitext. Mutating a node changes only its in-memory parsed text. |
| `aranami.support.open_run_log(directory=None)` | Opens a logging context and yields a logger; appends the daily UTC log in `logs/` or the chosen directory. |

```python
from datetime import date

import pywikibot

from aranami.sources.pageviews import massive
from aranami.support import gtshow

traffic = massive(
    pywikibot.Site("en", "wikipedia"),
    ["Video game", "Minecraft"],
    date(2026, 9, 1),
    date(2026, 10, 1),
)
gtshow(traffic)
# traffic.write_csv("daily-traffic.csv")  # Explicit optional export.
```

The [Quarry example][7] shows a custom article query. The [Pageviews example][8]
combines article selection and traffic collection.

## Configuration and files

Reports replace content inside marked HTML comments and preserve surrounding
page content. Settings on the opening comment control retention, traffic
history, missing-data thresholds, or assessment categories:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...generated content...
<!-- aranami end="new-pages" -->
```

See the [development guide][9] for supported markers and defaults.
Edit the PexBot
`prefixes` in `TASK_DEFINITIONS` in
[src/aranami/monitor.py](../src/aranami/monitor.py) and build
a replacement wheel to choose allowed subscription roots.
`aranami.jobs.pexbot.PEXBOT_PREFIXES` remains a compatibility alias for these
defaults. Pass `prefixes` to the routine for a single-call override. An empty
selection disables refreshes.

Unless using the launcher, paths are relative to the caller's current
directory:

| Location | What writes there and why |
|---|---|
| `logs/aranami.YYYY-MM-DD.log` | All routines and `open_run_log()` append starts, completion, skips, and failures. The latest 90 completed UTC dates are retained alongside today. |
| `dry-run/aranami-*.md` and linked `.wikitext` files | A full or standalone dry run, or explicit `JobContext.write_report()`, writes reviewable summaries and exact proposed page text. |
| `cache/` | Report routines reuse downloaded data and save local article-identity snapshots, including during previews. Chinese word counting may use `cache/words/`. Delete it while no jobs are active, or use `aranami.run_once(clear_cache=True)` with the monitor stopped to rebuild. |
| Caller-chosen filenames | One-time tools return results; the caller's explicit `write_csv()`, `write_parquet()`, or `write_text()` creates exports. |

Popularity rankings retain 800 days of traffic history by default.
The [Pageviews job](../src/aranami/jobs/pageviews.py) defines
`DATA_READY_HOUR=18`: yesterday becomes eligible when
the actual invocation starts at or after 18:00 UTC. With the hourly `HH:59`
schedule, the first regular attempt is at 18:59 UTC. Before 18:00, older
gaps can still advance one date per invocation. This applies to live runs,
previews, and manual job calls; a supplied date anchor cannot bypass the
cutoff. Existing `lag-days` attributes no longer affect the selected report
date.
If at least 95% of articles lack data for the target day,
publication waits. Temporary request failures are retried; later runs can
resume completed work without advancing the published date.

## Additional public interface reference

This catalog lists the remaining public classes and functions by import
module. The primary entry points above cover routine use. Names in the same
row share the described purpose; the source links contain their full
argument contracts. Unless a file effect is stated, these interfaces return
values or modify supplied in-memory objects without saving wiki pages or
exporting files.

### Execution and report services

| Module | Public interfaces | Result or effect |
|---|---|---|
| [`aranami.monitor`](../src/aranami/monitor.py) | `TaskDefinition`, `TASK_DEFINITIONS`, `RoutineSchedule`, `SCHEDULES`, `start`, `clear_caches` | Defines default target pages, UTC cron fields, and PexBot subscription prefixes in one module; derives schedules and starts or reuses the monitor returned by `aranami.run()`. `RoutineSchedule.trigger()` returns its UTC cron trigger. `clear_caches()` deletes disposable caches after the monitor has stopped. |
| [`aranami.runner`](../src/aranami/runner.py) | `run`, `run_once` | Public scheduled and immediate entry points also exported by `aranami`. |
| [`aranami.jobs`](../src/aranami/jobs/__init__.py) | `JobContext`, `ProposedEdit`, `job_run` | Shared run context, an edit proposal, and a routine lifecycle context. `JobContext.publish()` saves changed pages in live mode or records local proposals in dry-run mode. `write_report()` returns a preview path or `None` for a live run; `start_task()`, `finish_task()`, and `defer()` record routine status. `job_run()` yields the context and logs routine activity. |
| [`aranami.services.quality_milestones`](../src/aranami/services/quality_milestones.py) | `QualityTarget`, `QualityMilestone`, `QualityProfile`; `normalize_status_code`, `normalize_action_name`, `normalize_result`, `is_target_action`, `is_success_result`, `choose_milestone`, `extract_history_milestones`, `extract_quality_milestone_from_wikitext` | Describes and extracts supported successful promotions from supplied text; returns milestone records or no result when unresolved. |
| [`aranami.services.quality_profile`](../src/aranami/services/quality_profile.py) | `MilestoneExtractor`, `QualityAnalysisProfile` | Describes a wiki's quality-analysis choices and date-extraction interface. |
| [`aranami.services.enwiki.quality_dates`](../src/aranami/services/enwiki/quality_dates.py), [`aranami.services.zhwiki.quality_dates`](../src/aranami/services/zhwiki/quality_dates.py) | `extract_milestone(text, status, *, site, most_recent=True, aliases=None)` | Returns a wiki-specific `QualityMilestone` or `None`. |
| [`aranami.services.zhwiki.dyk_dates`](../src/aranami/services/zhwiki/dyk_dates.py) | `extract_dates` | Returns DYK dates from supplied talk-page text, including unknown values. |
| [`aranami.services.zhwiki.dyks`](../src/aranami/services/zhwiki/dyks.py) | `prepare_report`, `update_text`, `render_reports`, `build_statistics`, `article_members`, `nomination_changes` | Returns report text with article identities, updated DYK page text, rendered sections, dated counts, membership, or nomination outcomes. Source reads can update the disposable DYK cache. |
| [`aranami.services.zhwiki.new_pages`](../src/aranami/services/zhwiki/new_pages.py) | `update_text`, `matches_keywords`, `update_icons`, `record_dates`, `record_counts` | Returns updated daily-list text, keyword matches, refreshed icons, recorded dates, or article/non-article counts. |
| [`aranami.services.zhwiki.assessment_lists`](../src/aranami/services/zhwiki/assessment_lists.py) | `ReviewInfo`, `AssessmentList`; `prepare_report`, `prepare_text`, `update_text`, `review_heading`, `article_members`, `article_titles` | Configures lists and returns report text with article identities, prepared list text, review anchors, or membership. |
| [`aranami.services.zhwiki.pageviews`](../src/aranami/services/zhwiki/pageviews.py) | `ReportPeriod`, `TaskForce`, `ReportSettings`, `PageviewReport`; `current_data_date`, `report_periods`, `require_daily_observations`, `aggregate_views`, `build_report`, `update_text`; `PageviewsUnavailableError`, `PageviewsDeferredError` | Configures rankings and returns dates, intervals, traffic totals, or report text. `build_report` writes disposable healthy/pending files under `cache/`; `aggregate_views` stages data only when supplied a cache. Errors identify unavailable observations or deferred requests. |
| [`aranami.services.enwiki.quality_listing_dates`](../src/aranami/services/enwiki/quality_listing_dates.py) | `listing_dates(pages, *, site=None)` | Returns nullable English quality listing dates by article ID, refreshing disposable revision-keyed caches only for changed discussions. |
| [`aranami.services.zhwiki.enwp_key_articles`](../src/aranami/services/zhwiki/enwp_key_articles.py) | `ReportSpec`, `OldArticle`, `ReportData`; `prepare_reports`, `build_reports`, `build_enriched_rows`, `filter_report_rows`, `count_report_rows`, `normalize_en_pages`; `build_report_item_template`, `render_item`, `render_body`, `render_quality_body`, `update_page_text`; `parse_item_id`, `parse_old_articles`, `build_item_to_zh_title`, `format_summary_article`, `encode_length`, `truncate_summary_parts`, `build_edit_summary`; `english_sort_key`, `heading_key`, `safe_text`, `escape_template_value`, `replace_marker_value`, `replace_body` | Prepares English/Chinese article comparisons, filters and counts rows, renders alphabetical importance or dated quality sections, reads previous membership and English classes, and formats summaries or scalar text. Returns values without publication; quality preparation can refresh its disposable date cache. |
| [`aranami.services.zhwiki.pexbot`](../src/aranami/services/zhwiki/pexbot.py) | `StreamEvent`; `require_zhwiki`, `subscribed_titles`, `parse_event` | Validates the site, returns scoped subscription titles, or decodes progress messages. These helpers do not request refreshes. |

### Read-only data sources

| Module | Public interfaces | Returned result |
|---|---|---|
| [`aranami.sources.wiki`](../src/aranami/sources/wiki.py) | `read_pages`, `read_page_ids`, `template_aliases` | Preloaded current pages or template titles and aliases. |
| [`aranami.sources.dyk`](../src/aranami/sources/dyk.py) | `TalkPage`, `read_talk_pages` | Talk-page text with its actual revision ID. |
| [`aranami.sources.quarry.projects`](../src/aranami/sources/quarry/projects.py) | `TagSpec`; `query_pages_by_wikiproject`, `category_members`, `latest_revisions`, `new_page_ids`, `redirect_converted_page_ids`, `page_creation_metadata`, `query_tags` | Project assessments, category membership, revision IDs, created or redirect-converted page IDs, page identities with first-revision UTC times, or attached maintenance tags. |
| [`aranami.sources.quarry.quality`](../src/aranami/sources/quarry/quality.py) | `fetch_quality_articles(site, *, project_title, classes)` | Selected assessed articles with grade, importance, and display/sort titles. Listing dates are supplied by `analyze_quality_contents`, not this metadata query. |
| [`aranami.sources.quarry.enwp`](../src/aranami/sources/quarry/enwp.py) | `fetch_en_key_pages`, `fetch_en_talk_revisions`, `fetch_wikidata_sitelinks`, `fetch_wikidata_labels`, `fetch_zh_page_states` | English key articles, nullable talk-page revision identities, preferred linked titles/labels, or Chinese article states. |

`aranami.sources` exports the `pageviews` and `quarry` modules. `QueryFrame`
is also importable from `aranami.sources.quarry`; `DailyPageviews` is
importable from `aranami.sources.pageviews`.

<details>
<summary>Public names in aranami.sources.quarry.tables</summary>

These classes describe read-only query fields; they do not fetch data, change
wiki pages, or create files by themselves. `Base` holds the table metadata.
`StringDecoder`, `TextDecoder`, `EnumDecoder`, and `BinaryDecoder` normalize
query values; their `process_bind_param` and `process_result_value` callbacks
return converted values.

The table mappings are:

`Actor`, `Archive`, `Block`, `BlockTarget`, `BotPasswords`, `Category`,
`Categorylinks`, `ChangeTag`, `ChangeTagDef`, `Collation`, `Comment`,
`Content`, `ContentModels`, `Existencelinks`, `Externallinks`, `File`,
`Filearchive`, `Filerevision`, `Filetypes`, `Image`, `Imagelinks`, `Interwiki`,
`IpChanges`, `IpblocksRestrictions`, `Iwlinks`, `Job`, `L10nCache`, `Langlinks`,
`Linktarget`, `LogSearch`, `Logging`, `Objectcache`, `Oldimage`, `Page`,
`PageAssessments`, `PageAssessmentsProjects`, `PageProps`, `PageRestrictions`,
`Pagelinks`, `ProtectedTitles`, `Querycache`, `QuerycacheInfo`, `Querycachetwo`,
`Recentchanges`, `Redirect`, `Revision`, `Searchindex`, `SiteIdentifiers`,
`SiteStats`, `Sites`, `SlotRoles`, `Slots`, `Templatelinks`, `Text`, `Updatelog`,
`Uploadstash`, `User`, `UserAutocreateSerial`, `UserFormerGroups`, `UserGroups`,
`UserNewtalk`, `UserProperties`, `Watchlist`, `WatchlistExpiry`, `WatchlistLabel`,
`WatchlistLabelMember`, `WbChanges`, `WbChangesSubscription`, `WbIdCounters`,
`WbItemsPerSite`, `WbPropertyInfo`, `WbtItemTerms`, `WbtPropertyTerms`,
`WbtTermInLang`, `WbtText`, and `WbtTextInLang`.

See the [table definitions](../src/aranami/sources/quarry/tables.py) for fields.

</details>

### Text, logging, and cache support

| Module | Public interfaces | Result or file effect |
|---|---|---|
| [`aranami.support.cache`](../src/aranami/support/cache.py) | `clear_runtime_cache` | Removes the caller's `cache/` directory. |
| [`aranami.support.dates`](../src/aranami/support/dates.py) | `parse_date`, `parse_complete_date` | Returns a parsed date or `None`; the latter requires a complete calendar date. |
| [`aranami.support.templates`](../src/aranami/support/templates.py) | `template_page`, `normalize_param_name`, `template_get_raw_param`, `template_get_param`, `normalize_oldid` | Returns template identities, editable or cleaned parameter values, normalized names, or revision IDs. |
| [`aranami.support.wikitext`](../src/aranami/support/wikitext.py) | `clean_value`, `get_templates`; reexports the region functions below | Returns cleaned scalar text or matching editable templates. |
| [`aranami.support.regions`](../src/aranami/support/regions.py) | `has_region`, `region_options`, `region_content`, `integer_option`, `managed_region`, `replace_by_tag` | Inspects named comment ranges/settings or returns wrapped/replaced text. |
| [`aranami.support.prose`](../src/aranami/support/prose.py) | `ProseProfile`, `extract_prose` | Configures extraction and returns readable article prose. |
| [`aranami.support.words`](../src/aranami/support/words.py) | `count_words` | Returns an English word or Chinese token count; Chinese counting may create `cache/words/` files. |
| [`aranami.support.edit_summary`](../src/aranami/support/edit_summary.py) | `EditSummary`; `append`, `add_group`, `daily`, `membership`, `render`, `with_execution_time` | Builds an edit-summary string with optional clauses, membership changes, and elapsed time. |
| [`aranami.support.report_membership`](../src/aranami/support/report_membership.py) | `MembershipReport`, `load_membership`, `save_membership`, `member_identifier`, `membership_changes` | Returns report text with article identities, cached identities, legacy row IDs, or added/removed title lists. `save_membership` writes `cache/report-membership-<hash>-v1.parquet`. |
| [`aranami.support.dyk_cache`](../src/aranami/support/dyk_cache.py) | `load`, `save` | Reads/writes disposable `cache/vg_dyks_talk_pages-v2.parquet`. |
| [`aranami.support.pageviews_cache`](../src/aranami/support/pageviews_cache.py) | `PageviewsCache`; `load`, `get`, `replace`, `save_pending`, `discard_pending`, `save` | Reads or stages daily histories; save/discard methods write or remove `cache/pageviews-<hash>-v1.parquet` and dated pending files. |

`aranami.support` reexports `get_templates`, `gtshow`, and `open_run_log`.
The latter two are also available from the same-named support modules.

## Development

See the [development guide](development.md) for local setup, verification,
wheel building, and engineering conventions. Completed changes are recorded
in [CHANGELOG.md][5].

## License

Aranami is released under [CC0 1.0 Universal][6].

[1]: https://wikitech.wikimedia.org/wiki/PAWS
[3]: https://wikitech.wikimedia.org/wiki/PAWS/Python_with_Pip
[5]: ../CHANGELOG.md
[6]: ../LICENSE
[7]: ../tests/live/quarry_paws.py
[8]: ../tests/live/pageviews_paws.py
[9]: development.md
