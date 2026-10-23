# <img src="src/webservice/public_html/aranami.svg" width="40" height="40" alt=""> Aranami

Aranami (荒波, “rough waves”) keeps Wikipedia project reports up to date
and helps editors find articles to improve. Its main focus is
[WikiProject Video games on Chinese Wikipedia](https://zh.wikipedia.org/wiki/WikiProject:电子游戏).

The [dashboard](https://aranami.toolforge.org/) brings together main-page
highlights, upcoming quality content, popular articles, maintenance
worklists, and report update results. It supports English, Simplified
Chinese, and Traditional Chinese. The [About page](https://aranami.toolforge.org/about/)
introduces the tool to general readers.

## Scheduled tasks

These are UTC trigger times, not promises that an edit will be necessary.
A successful check can finish with **no update needed**.

| Report | Frequency and UTC trigger | Result |
|---|---|---|
| [New-article recommendations (DYK)][dyk] | Hourly, `HH:00` | Completed DYK entries, current nominees, assessments, and cumulative statistics. |
| [New pages][new-pages] | Daily, `00:07` | Keyword-selected new pages and pages rewritten from redirects, with assessments to help editors review them. |
| [Article assessments][assessments] | Hourly, `HH:31` | Six assessment and review lists: Bplus, BPAN, A, AL, ACC, and PPR. |
| [Pageview rankings][pageviews] | Daily cycle from `18:00`; normal first attempt `18:59`, retries at `HH:59` | Daily, weekly, monthly, seasonal, yearly, and monthly task-force rankings. |
| [English Wikipedia comparisons][english] | Daily, `23:24` | Important and high-quality English articles, their Chinese counterparts, and relevant changes. |
| [Database reports][database] | Daily, `00:00` | Delegated refresh requests for subscribed PexBot reports within the configured project scope. |

New pages skips further automatic work for the current UTC day after the
complete retained window through yesterday is confirmed. Missing retained
dates are filled when needed.

Pageviews uses an **18:00 UTC reporting boundary**, not midnight. It
processes at most one missing date per invocation, retries incomplete or
unavailable data hourly, and waits for the next cycle once current.
Confirmed completion survives server restarts.

English comparisons and database reports refresh current state on their
daily schedules; they do not generate missed historical daily reports.
Database reports already edited by PexBot that UTC day are not requested
again. Manual runs revalidate destinations rather than bypassing checks.

The website's five datasets refresh independently, by default 24 hours
after each successful collection. Failed refreshes retain the last valid
data and retry hourly. Visiting the website does not trigger collection
or Wikipedia edits.

## How it works

One Python 3.15 backend hub fetches source data, parses source pages,
calculates changes, and manages stored results, caches, logs, and status.
The wiki updater asks the backend for prepared data, formats it, and
publishes only to its selected destinations. It reads no other Wikipedia
pages and replaces only the configured `<!-- aranami ... -->` ranges.
Text outside those ranges is preserved.

Complete report tables and metadata are retained before publication,
including assessment review targets and change information used in edit
summaries. English importance and quality reports have separate renderers
for their different section structures. A separate read-only Python
webservice renders saved backend data as complete HTML pages.

Logs are ordinary, readable English diagnostics showing what data was
collected and which wiki pages were checked or edited. Structured status
is stored separately: routine and per-page starts, finishes, durations,
outcomes, and confirmed edit counts. The website reads those records;
it never parses log text to determine status.

Report destinations and UTC schedules are defined centrally in
[src/shared/config.py](src/shared/config.py). Maintenance worklists are
defined in [maintenance.json](src/backend/website/maintenance.json).
Backend `services` prepare report-specific data; backend `support`
provides execution, storage coordination, status, cache, and logging helpers.

## Documentation

Choose the guide for your role:

- [Terminal user](docs/usage.md): run reports, inspect data, and create previews.
- [System administrator / maintainer](docs/administration.md): upload source
  files to Toolforge, promote deployments, stop, start, restart, and recover.
- [Application developer](docs/development.md): local setup, contribution
  workflow, tests, and release checks.
- [System-level developer](docs/architecture.md): component boundaries,
  data contracts, publication safety, scheduling, and engineering rules.

See the [documentation index](docs/README.md) and [change history](CHANGELOG.md)
for the full reference.

## Toolforge layout

Aranami runs only on Toolforge; the local repository is for development,
testing, and preparing deployments. Runtime files stay outside the uploaded
source tree.

```text
/data/project/aranami/
├── app/                         Active validated source deployment
│   ├── src/backend/             Collection, preparation, scheduling, state
│   ├── src/updater/             Wiki rendering and publication
│   ├── src/webservice/          Python page rendering and static assets
│   ├── src/shared/              Configuration and low-level contracts
│   └── scripts/toolforge/       Deployment and startup commands
├── python/                      Managed Python 3.15 interpreter
├── deployment-envs/             Isolated, locked runtime environments
├── pyvenv-3.15 -> deployment-envs/<deployment-id>
├── active-deployment.json       Validated source/environment receipt
├── reports/                     Complete report generations and manifests
├── state/                       Routine, scheduler, and page-update metadata
├── cache/                       Disposable source data and website snapshots
├── logs/                        aranami.YYYY-MM-DD.log, in UTC
├── dry-run/                     Preview summaries, wikitext, and datasets
└── user-config.py, user-password.cfg, replica.my.cnf, …  Private credentials
```

Staged uploads and recoverable predecessor deployments are separate from
`app/`. The [administrator guide](docs/administration.md) explains the
validated upload and process-replacement workflow.

## License

[CC0 1.0 Universal](LICENSE).

[dyk]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新条目推荐
[new-pages]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新进条目/关键词筛选
[assessments]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目
[pageviews]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/热门条目
[english]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科重要条目
[database]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告
