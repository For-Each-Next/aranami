# Aranami usage

[Project overview](../README.md) · [Documentation index](README.md)

Aranami (<span lang="ja">荒波</span>, "rough waves") maintains Wikipedia
reports, primarily for WikiProject Video games on Chinese Wikipedia.
[Toolforge][2] is the primary runtime for continuous scheduled report
maintenance. Follow the Toolforge workflow below to operate and update it.
[Wikimedia PAWS][1] is an optional environment for notebooks, manual reports,
and one-time analysis.

## Maintain on Toolforge

The `aranami` tool runs one continuous monitor. Aranami schedules all six
report routines inside that process using the [UTC schedules](#routine-jobs-and-utc-schedule).
Run a single monitor for these reports. Stop it before overlapping manual
report updates through PAWS or the Python API.

This workflow assumes an existing tool account, SSH access as a maintainer,
and configured wiki OAuth and Wiki Replica access. The deployment uses:

| Setting | Value |
|---|---|
| SSH login | `for-each-next@login.toolforge.org`, then `become aranami` |
| Tool home and working directory | `/data/project/aranami` |
| Job image and installed environment | `python3.13` and `/data/project/aranami/pyvenv` |
| Wiki configuration | Existing files in the tool home, selected by `PYWIKIBOT_DIR=/data/project/aranami` |
| Replica configuration | Readable `.my.cnf` in the working directory; the existing link to `replica.my.cnf` is supported |

Develop, test, and build wheels on the development computer. Toolforge runs
the installed wheel. The upload and setup scripts reuse existing credentials;
setup checks package installation and imports but does not configure or
validate authentication.

### Log in and check operation

In a local terminal:

```shell
ssh -t for-each-next@login.toolforge.org "become aranami"
```

On Toolforge, check the monitor and its recent output:

```shell
cd /data/project/aranami
toolforge jobs show aranami
toolforge jobs logs aranami
tail -n 80 "logs/aranami.$(date -u +%F).log"
```

Use the main project log for Aranami and dashboard lifecycle events, the
detailed application log for individual task progress and tracebacks, and
Toolforge job logs for process startup failures. Daily logs use UTC dates.
See the official [Toolforge jobs guide][10]
for job status, lifecycle, and log commands.

### Read the dashboard and logs

The [homepage](https://aranami.toolforge.org/) displays Chinese project
highlights, recognized video-game content across Wikipedia languages, and
article maintenance lists. Its flat, top-level sections are 动态热门,
条目维护, and 他山之石, in that order. 动态热门 combines 100 UTC dates of
Chinese main-page appearances,
the next 30 days of scheduled featured and good content, and daily
video-game pageviews. On desktop, featured and good content share the left
column with DYK; popular articles and 旧事新闻, the combined news and
anniversaries panel, occupy the right. Quality content and DYK use compact,
numbered rows like the popular-article list. DYK shows its first ten entries
without an internal scrollbar, with dates on the right and the same reserved
right gutter as the other panels. Scheduled appearances use a prefix such
as `预定2026-10-31`. Popular and DYK article titles are
followed by available current project grade and importance chips, such as
乙级列表 and 高; importance uses the short 极高/高/中/低 labels. Chinese
assessment names use the project's labels, including 初 and 丙, in chips
and counterpart grades. Recognition, assessment, and importance chips share
one appearance throughout the dashboard and maintenance lists.
Larger serif section headings and full-width bottom
rules distinguish the main sections.
All appearance lists place dates on the right, including lists without a
scroll container; observation and carry-in details remain in labelled
tooltip markers.
Upcoming content appears alongside past appearances, with its scheduled
date marked inline. Quality content
always shows every appearance without collapsing or a scrolling limit.
The page uses a light green background, black text, and blue links without
underlines, with a maximum width of 960 px and a mobile layout.
The interface selects Simplified Chinese, Traditional Chinese, or English
from the browser's language preferences. Article titles and links retain
their original language.
An always-visible table shows Aranami's on-wiki report updates: routine
outcomes, durations, and plain UTC timestamps. Its footer shows
the installed version and log link once. Tables use row rules without
alternating row colors. Article text uses the normal 16 px size, module
headings use 20 px, and chips use a smaller shared size.
Confirmed saves show as updated; successful runs without saves show as
no update needed. Aranami publishes each live start and outcome to
`logs/aranami-status.json`; the table reads that snapshot without parsing
diagnostic logs. Delegated refreshes without confirmed edit counts remain
unconfirmed. Preview activity is omitted. An interrupted process retains
its last reported state and time until the next run reports another state.

The multilingual section, 他山之石, directly queries Wikidata items with
`P31=Q7889` and reads their quality badges across other Wikipedia languages.
It applies the same FA, FL, GA, and recommended badge definitions to every
language, with FL labelled 特色列表 in Chinese. The default view shows ten
entries across all languages, placing
recently observed badge additions first and filling remaining places with
other currently recognized content. Each row has three columns: language
name with its code, the foreign article with a recognition chip, and the
Chinese counterpart with an assessment chip. Rows stay on one line;
long titles have full-title tooltips and narrow screens can scroll the
table horizontally. Recorded additions include their observation time in
the recognition chip's tooltip, indicating when the dashboard detected an item,
language, and badge combination, rather than a local promotion date.
The language dropdown limits the available collection. “随机筛选10条”
immediately samples up to ten records from that selection in place,
without another request. Language and recognition query parameters also
filter the full collection.
Chinese articles appear as counterpart links recorded on the same item,
with their known Chinese project assessment, including 乙 or 丙, shown
outside the title link; missing counterparts are marked explicitly.
Chinese is included in the all-language
view. Filters, random samples, and browser history update the section in
place. Language lists initially show 25 records and can expand to every
collected record without another request.
The updater compares successful badge results against a disposable
Parquet observation cache and retains additions for 30 days. The first
result establishes a baseline without marking existing badges as new;
deleting this cache resets the baseline and observation history. Failed
queries reuse a valid cached badge collection with a visible cache notice
and its original update time. Cached results never create observations or
change the comparison baseline. Without a valid cache, the section is
unavailable.
This exact type scope excludes game subclasses,
lists, and broader project topics unless they also carry `P31=Q7889`.
Badge data can lag local status changes and does not establish nomination
or promotion dates. DYK is collected from the Chinese main-page templates,
separately from recognized quality badges.

Daily pageviews use the [official Wikimedia all-access top-1,000 list](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/examples/project-metrics.html) for
Chinese Wikipedia, filtered to current project members. This coverage may
produce fewer than ten video-game entries. The card includes its measured
date in the description and links “top 1,000” to the readable Topviews list;
missing daily results may fall back within the last seven completed
dates. Unavailable data is never presented as zero views or assigned a
newer date. Hovering over an article's view count shows its original sitewide
rank for that observation, rather than its position within the filtered list.

The 条目维护 section provides Chinese project worklists, following the
grouped issue lists in the [English project backlog](https://en.wikipedia.org/wiki/Wikipedia:WikiProject_Video_games/Backlog)
and [cleanup report](https://bambots.brucemyers.com/cwb/bycat/Video_games.html).
Its 37 tasks are organized in eight equal-height cards, each at least
240 px wide when space permits, under notability, references, accuracy, content,
links, layout, style, and other maintenance. The categories are checked
against Chinese Wikipedia's actual templates, including expansion from
English and Japanese Wikipedia and translation proofreading.
The server selects current main-namespace project members and reads their
maintenance categories from the read-only Chinese Wikipedia replica. It
matches verified undated roots, monthly forms, and explicit aggregates.
The homepage shows grouped task categories and their article counts as links.
Maintenance counts use plain numbers without circular borders.
Opening a task loads a dedicated maintenance page, such as
`/maintenance/?task=unreferenced&page=1`, with 50 articles per page and
previous/next navigation. Each article row shows available project grade
and importance, all matching issues, and the earliest month found in its
current dated maintenance categories. A dash means no month is recorded;
this month does not establish when the issue began. The saved snapshot
contains every matching article; the maintenance page reads only the
requested slice without collecting source data. The overall article count
deduplicates articles with multiple issues.
Worklists default to the earliest tagged month first, with missing months
last. Click the article or month heading to change the sort and direction;
mobile pages expose the same links above the list. Pagination retains the
chosen order. Sorting uses only the saved snapshot and adds no database
requests.

Edit [maintenance.json](../src/toolforge/maintenance.json) locally to change
the maintenance catalogue. The file defines the section order and labels,
the task order and labels, and each task's matching rules. Labels may be a
plain string or a map using `en`, `zh-hans`, and `zh-hant` language keys.
The default configuration contains the 37 verified Chinese tasks.
For example, a task can match a template on an article:

```json
{
  "schema": 1,
  "sections": [
    {
      "key": "references",
      "label": "参考来源",
      "tasks": [
        {
          "key": "citation_help",
          "label": "补充引用",
          "rules": [
            {
              "kind": "template",
              "name": "Citation needed",
              "talk_page": false,
              "monthly": false
            }
          ]
        }
      ]
    }
  ]
}
```

Use `kind: "category"` for a category rule or `kind: "template"` for a
template inclusion rule. Set `talk_page` to `true` to inspect the associated
discussion page; matching records still link to the subject article.
Rules within one task are alternatives. Category rules with `monthly: true`
also recognize the exact `自YYYY年M月` form of the specified root; this
option is not used for template rules. Verify the actual Chinese template
or category before adding a rule. Arbitrary descendants are not included.
Keep stable task and section keys when renaming labels so existing links
continue to work. The file supports up to 12 sections, 64 tasks, and 16
rules per task. Invalid configurations fail validation instead of silently
changing the catalogue.

Upload the edited file with `sh src/toolforge/upload-web.sh`. The deployed
copy is `/data/project/aranami/toolforge/maintenance.json`; the next
background refresh reads it. Restart `aranami-dashboard` to refresh
immediately. The website has no configuration editor.

The separate `aranami-dashboard` background job refreshes data immediately
and then hourly, including the maintenance lists. Wikidata badge data is
reused for up to 24 hours. The default homepage reuses rendered content for
up to 60 seconds per interface language. Snapshot, routine-status, and template
changes invalidate that cache; filtered and random requests render afresh.
The browser does not refresh automatically.
It distinguishes an unavailable source from a measured empty result.

Open [the run log](https://aranami.toolforge.org/logs/) for daily
high-level events from Aranami and the dashboard server, including routine
outcomes, server lifecycle events, and complete, partial, or failed data
refreshes. These records use `logs/project.YYYY-MM-DD.log` and contain
structured fields without detailed progress messages or tracebacks.
The [detailed Aranami logs](https://aranami.toolforge.org/logs/aranami/)
remain available separately. Read a detailed day directly at
`https://aranami.toolforge.org/logs/aranami/aranami.YYYY-MM-DD.log`, replacing
`YYYY-MM-DD` with its UTC date. Each listing shows the files' latest
modification times in UTC, separately from their daily log dates. The viewers
read current files on each request; refresh the page for new output.
Both series retain today and the latest 90 completed UTC dates. These logs
are public. PexBot's `query-executing` messages
abbreviate query arguments to `...`; other progress and failure details
remain available.

To install or update the standalone tool, run this in a local terminal at the
repository root:

```shell
sh src/toolforge/upload-web.sh
```

The uploader installs the PHP pages in `/data/project/aranami/public_html/`,
the Python tool in `/data/project/aranami/toolforge/`, and the private
readers, launcher, and routing configuration beside them. Its setup adds
the routing include while preserving existing lighttpd configuration.
The previous `scripts/toolforge/upload-web.sh` command remains a wrapper.
The upload does not install a wheel or restart Aranami's editing monitor.
The dashboard reuses the installed Aranami package for replica access and
shared project logging. When these interfaces change, install the matching
wheel before restarting the dashboard updater.
Use `--dry-run` to check the payload without uploading.

Then, in the Toolforge terminal, start the viewer for the first time:

```shell
toolforge jobs run aranami-logs --image php8.4 --webservice --mount=all
toolforge jobs run aranami-dashboard --image python3.13 --continuous --mount=all --command '/bin/sh /data/project/aranami/run-dashboard.sh'
```

After subsequent uploads, restart `aranami-logs` and `aranami-dashboard`.
Check them with `toolforge jobs show <name>`. The updater writes detailed
source failures to `aranami-dashboard.err` and high-level UTC events
to the shared project log. It serializes refreshes, preserves the
last saved snapshot after a failed refresh, and retries automatically.
The first complete history and badge fetch can take longer than later
updates. These two jobs run independently of the `aranami` editing monitor.
See the official
[Toolforge webservice job guide][11] for these commands.

The launcher reuses `/data/project/aranami/pyvenv` and the tool's existing
Pywikibot and Wiki Replica configuration. Runtime presentation data belongs
in `cache/toolforge/dashboard.json`; tabular badge data uses Parquet in the
same disposable directory. For a manual read-only refresh, run
`pyvenv/bin/python -m toolforge collect` from the tool directory. The
background server's local `/healthz` and `/dashboard.json` endpoints use
`127.0.0.1:8765`; browser requests never start source collection.

If the normal bastion is unavailable, `TOOLFORGE_SSH_TARGET` may select the
documented development host. `TOOLFORGE_KNOWN_HOSTS` may point to a verified
known-hosts file; the uploader then enforces strict host-key checking.

### Upgrade the installed wheel

#### 1. Check the local wheel

In a local terminal at the repository root, with Python, uv, and OpenSSH
available:

```shell
uv run python scripts/upload_toolforge.py --dry-run
```

The uploader finds the single `dist/aranami-*.whl`, checks its filename
against its package metadata, and prints its version. Keep only the intended
wheel in `dist/`; zero or multiple wheels cause an error. Follow the
[development and release guide](development.md#verification-and-releases)
if a new wheel is needed. No filename or version edits are needed in the
upload or setup scripts.

#### 2. Stop the monitor

In the Toolforge terminal:

```shell
toolforge jobs list
toolforge jobs delete aranami
toolforge jobs delete aranami-dashboard
toolforge jobs delete aranami-logs
```

Stop the dashboard updater and every live or preview monitor using
`/data/project/aranami/pyvenv`.
If an `aranami-preview` job is present, stop it too:

```shell
toolforge jobs delete aranami-preview
```

Wait for all monitor processes to terminate before changing their shared
environment. An already absent job needs no action. Stop `aranami-logs`
for a coordinated wheel and dashboard deployment; it can remain running
when only the wheel changes.

#### 3. Upload and install

In the local terminal, upload the checked wheel and setup script:

```shell
uv run python scripts/upload_toolforge.py
sh src/toolforge/upload-web.sh
```

The wheel uploader sends only the wheel, setup script, and selected-wheel
manifest. The web uploader sends the dashboard, collector scripts, and
maintenance configuration. Neither command starts the monitor.

Back in the Toolforge terminal, install the uploaded wheel in a Python job:

```shell
(
  set -eu
  cd /data/project/aranami
  setup_job="aranami-setup-$(date -u +%Y%m%d%H%M%S)"
  printf 'Installation job: %s\n' "$setup_job"
  toolforge jobs run "$setup_job" \
    --image python3.13 --mount all --mem 1Gi --filelog --wait 1800 \
    --command '/bin/sh /data/project/aranami/setup-env.sh'
  tail -n 30 "$setup_job.out" "$setup_job.err"
)
```

The setup script reuses `/data/project/aranami/pyvenv`, or creates it if absent,
and installs the exact uploaded wheel and its dependencies, including OAuth
support. It reinstalls Aranami even for a same-version wheel and checks
dependencies and imports. Continue only after the job succeeds and prints
the expected version and `Environment ready`. Keep the printed installation
job name to find its `.out` and `.err` files if setup fails or times out.

#### 4. Start and verify the monitor

In the Toolforge terminal:

```shell
toolforge jobs run aranami \
  --image python3.13 --mount all --mem 2Gi --cpu 1 \
  --continuous --no-filelog \
  --command 'cd /data/project/aranami && umask 077 && exec env LC_ALL=C.UTF-8 LANG=C.UTF-8 LC_CTYPE=C.UTF-8 PYWIKIBOT_DIR=/data/project/aranami /data/project/aranami/pyvenv/bin/python -m aranami'
toolforge jobs show aranami
toolforge jobs run aranami-dashboard --image python3.13 --continuous --mount=all --command '/bin/sh /data/project/aranami/run-dashboard.sh'
toolforge jobs run aranami-logs --image php8.4 --webservice --mount all
```

This starts live wiki edits immediately, then follows the internal UTC
schedules. Toolforge restarts the continuous job if its process exits.
Each fresh process checks all routines immediately by default. Append
`--no-run-immediately` after `-m aranami` to begin at the next scheduled
times instead. Check individual task results in the application log:

```shell
tail -n 80 "/data/project/aranami/logs/aranami.$(date -u +%F).log"
```

### Stop, restart, or preview

To stop scheduled updates, use the [stop command](#2-stop-the-monitor).
To restart without changing the installed wheel, stop the monitor and repeat
the [start command](#4-start-and-verify-the-monitor); skip upload and setup.

For scheduled previews, stop the live monitor and add `--dry` after
`-m aranami` in the start command. This writes proposals under
`/data/project/aranami/dry-run/` without wiki edits or PexBot refresh requests.
It stays active and previews every routine immediately, then at its UTC
schedule. To resume live updates, stop the preview monitor and recreate the
job with the ordinary start command. Stop all live and preview monitors
before overlapping manual report runs or cache maintenance.

### Runtime files and recovery

The start command sets the working directory and `PYWIKIBOT_DIR` explicitly.
Keep these paths when changing job options: replica reads use `.my.cnf` in
the working directory, and runtime output goes under the same directory.

| Location in `/data/project/aranami/` | Maintenance |
|---|---|
| `logs/project.YYYY-MM-DD.log` | Read high-level Aranami and dashboard events here. Retains today and the latest 90 completed UTC dates. |
| `logs/aranami.YYYY-MM-DD.log` | Read routine starts, results, and failures here. Aranami retains today and the latest 90 completed UTC dates. |
| `logs/aranami-status.json` and `.lock` | Latest directly reported live routine states and their writer lock. The dashboard reads the JSON file; retain both during routine upgrades. |
| `cache/` | Reused data and recoverable checkpoints. Keep it during ordinary upgrades; remove it only with the monitor stopped when a rebuild is needed. |
| `dry-run/` | Preview summaries and linked `.wikitext` proposals for review. |
| `aranami-setup-*.out` and `.err` | Installation output and errors from each setup job. |
| `user-config.py`, optional password files, `.my.cnf`, and `replica.my.cnf` | Existing runtime configuration; routine wheel uploads do not replace these files. |

If setup fails, inspect the printed job's output and correct the reported
installation problem before restarting. A permissions error may require
transferring ownership with `take` as the tool account, as directed by the
setup script. Toolforge manages `replica.my.cnf`; leave its ownership and
permissions unchanged. Setup accepts an existing `.my.cnf` link to that file
but does not create it.

If the monitor fails to start, inspect `toolforge jobs logs aranami` and
check the image, environment, working directory, and wiki configuration.
If one routine fails, use the daily log to diagnose that task; other
scheduled routines remain active. Pageviews may defer a report until source
data is ready and retry later without requiring a restart.

## Optional: install and monitor on PAWS

Use PAWS for notebook work or manual execution. Stop the Toolforge monitor
before running overlapping report updates or another monitor.

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

## Choose a Python interface

These interfaces work with the installed package on Toolforge or PAWS.
Toolforge's continuous job uses `python -m aranami` to keep the monitor's
process alive. The Python API below is useful for manual calls and notebooks.

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
The monitor uses the runtime's existing wiki authentication and Wiki Replica
configuration.

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
the host or notebook's local timezone has no effect. `aranami.run_once()` runs
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

Daily and complete calendar-month sections fetch Wikimedia's site-wide
top-1,000 list. Listed articles receive `|top=daily/<rank>` or
`|top=monthly/<rank>` in
`PJ:VG/HOT/item`, separately from their project rank. The same monthly list
is reused for task forces. Site ranks count main-namespace articles only:
non-article pages such as `Wikipedia:首页` and the site's main page are
excluded before numbering articles from one. For example, September 1–30
qualifies, whereas
September 2–October 1 does not. Weekly, quarterly, yearly, and rolling
monthly sections omit `top`. Yearly rankings continue to use article
observations without fetching or waiting for annual site ranks.
Articles outside a successfully fetched list omit `top`. If an applicable
list is missing, empty, invalid, or cannot be fetched, the entire report
waits: its published date and healthy cache remain unchanged. Completed
article requests are retained for the next scheduled attempt. The log
identifies the report date, required period, failure reason, and retry.
For September 1–30, Aranami waits for September's monthly list before
updating that report, even when its daily observations are already ready.
The companion [Lua module source](../wiki/zhwiki/PJ_VG_HOT.lua) renders
`top=daily/55` as a “全站日间第55名” tooltip, or uses 月间 for monthly.
It uses the explicit period and the supplied `YYYY-MM-DD` dates without
inferring the tooltip period. The report owns each leading `#`; the module
returns inline content. Install the companion module on-wiki before
expecting the typed values to display there.

### English comparisons

The importance report lists Top-importance and High-importance English
articles in alphabetical sections and refreshes their counters.
The quality report lists featured and good English content: featured articles
(FA), featured lists (FL), and good articles (GA). It groups all three classes
by the year of their latest English promotion or listing, with the newest
dates first. Dated entries end with `<!-- YYYY-MM-DD -->`; unresolved dates
appear under `年份未知`. Same-day entries use English title sort keys.
The routine refreshes the quality page's total and FA, FL, and GA counters in
the introduction, along with its managed `body` range. All other page content
is preserved. Both reports refresh their Chinese counterparts and assessment
grades. Choose destinations with `targets` containing both `important` and
`quality`.

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

For each report, Aranami checks its revision history for the current UTC
day. If PexBot already edited it that day, Aranami skips the refresh even
when a human edited it more recently. The check uses the actual date,
independent of a supplied report date. Skips appear in the daily log and
preview notes; previews propose refreshes only for eligible reports.
If history cannot be read, that report is not refreshed and other reports
continue. Actual live refresh attempts are separated by 15 seconds,
including after failures; skipped reports do not add a delay.

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
| `aranami.sources.quarry.tables` | Provides [read-only table definitions](../src/aranami/sources/quarry/tables.py) for custom queries. |
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

Reports require paired `aranami` HTML comments around generated content and
preserve surrounding page content. Settings on the opening comment control
retention, traffic history, missing-data thresholds, or assessment categories:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...generated content...
<!-- aranami end="new-pages" -->
```

Each name below needs one ordered begin/end pair on its destination page:

| Report | Required range names |
|---|---|
| DYK | `dyk` for completed entries; `dykn` for nominees. |
| New pages | `new-pages`. |
| Assessment lists | `assessment-count`, `assessment-list`. |
| Pageviews | `page_views`. |
| English importance comparisons | `count_all`, `count_top`, `count_high`, `body`. |
| English quality comparisons | `count_all`, `count_fa`, `count_fl`, `count_ga`, `body`. |

Keep section-transclusion tags outside these comments. PexBot controls its
own report content and does not require Aranami ranges.

See the [development guide][9] for supported markers and defaults.
Edit the PexBot
`prefixes` in `TASK_DEFINITIONS` in
[src/aranami/monitor.py](../src/aranami/monitor.py) and build
a replacement wheel to choose allowed subscription roots.
Pass `prefixes` to the routine for a single-call override. An empty selection
disables refreshes.

Paths are relative to the caller's current directory. On Toolforge, the
documented job command uses `/data/project/aranami`; the PAWS launcher anchors
output beside itself:

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
cutoff.
If at least 95% of articles lack data for the target day,
publication waits. Temporary request failures are retried; later runs can
resume completed work without advancing the published date.

## Development

See the [development guide](development.md) for local setup, verification,
wheel building, and engineering conventions. Completed changes are recorded
in [CHANGELOG.md][5].

## License

Aranami is released under [CC0 1.0 Universal][6].

[1]: https://wikitech.wikimedia.org/wiki/PAWS
[2]: https://wikitech.wikimedia.org/wiki/Portal:Toolforge
[3]: https://wikitech.wikimedia.org/wiki/PAWS/Python_with_Pip
[5]: ../CHANGELOG.md
[6]: ../LICENSE
[7]: ../tests/live/quarry_paws.py
[8]: ../tests/live/pageviews_paws.py
[9]: development.md
[10]: https://wikitech.wikimedia.org/wiki/Help:Toolforge/Jobs_framework
[11]: https://wikitech.wikimedia.org/wiki/Help:Toolforge/Running_jobs#Creating_webservice_jobs
