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
fetches available history once and saves each missing daily report in order
through the latest eligible complete day. It retries incomplete or
unavailable data hourly and waits for the next cycle once current.
Confirmed completion survives server restarts.

English comparisons and database reports refresh current state on their
daily schedules.
Database reports already edited by PexBot that UTC day are not requested
again. Manual runs revalidate destinations rather than bypassing checks.

The website's five datasets refresh independently, by default 24 hours
after each successful collection. Failed refreshes retain the last valid
data and retry hourly. Website visits are read-only.

## How it works

One Python 3.15 backend hub fetches source data, parses source pages,
calculates changes, and manages stored results, caches, logs, and status.
The wiki updater formats prepared data and replaces only the configured
`<!-- aranami ... -->` ranges on selected destinations, preserving surrounding
text.

Complete report tables and metadata are retained before publication,
including review targets and changes used in edit summaries. A separate
read-only Flask website renders saved backend data as complete HTML pages,
served by Waitress.

Logs are ordinary, readable English diagnostics showing what data was
collected and which wiki pages were checked or edited. Structured status
is stored separately: routine and per-page starts, finishes, durations,
outcomes, and confirmed edit counts. The website displays these saved
observations.
The dashboard advances running times locally every second and synchronizes
routine/page statuses every five seconds, keeping status popups in place.
Execution times use whole seconds, with two decimal places for subsecond results.
While a routine is running, its card shows the current execution and available
progress rather than a next-execution row. The log viewer includes a compact
server-state display, uptime, and source version.

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

Aranami runs only on Toolforge; the local repository is for development,
testing, and preparing deployments. Runtime files stay outside the uploaded
source tree. The [administrator guide](docs/administration.md) covers the
runtime layout, validated deployment, and recovery workflow.

## License

[CC0 1.0 Universal](LICENSE).

[dyk]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新条目推荐
[new-pages]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新进条目/关键词筛选
[assessments]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目
[pageviews]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/热门条目
[english]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科重要条目
[database]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告
