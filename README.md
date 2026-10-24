# <img src="src/webservice/public_html/aranami.svg" width="40" height="40" alt=""> Aranami

Aranami (荒波, “rough waves”) keeps Wikipedia project reports up to date
and helps editors find articles to improve. Its main focus is
[WikiProject Video games on Chinese Wikipedia](https://zh.wikipedia.org/wiki/WikiProject:电子游戏).

The [dashboard](https://aranami.toolforge.org/) brings together main-page
highlights, upcoming quality content, popular articles, maintenance
worklists, and report update results in English, Simplified Chinese, and
Traditional Chinese. The [About page](https://aranami.toolforge.org/about/)
includes the server monitor.

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

New pages and Pageviews fill missing daily reports and retain confirmed
completion across restarts. Pageviews uses an 18:00 UTC reporting boundary;
incomplete data retries hourly. PexBot reports already edited that UTC day
are not requested again.

The website's five datasets refresh independently every 24 hours after
success. Failures retain valid data and retry hourly; visits are read-only.

## How it works

One Python 3.15 hub collects data and retains complete reports before wiki
publication. The updater changes only configured `<!-- aranami ... -->`
ranges, preserving surrounding text. A separate website serves saved data,
live execution status, and readable logs. Aranami runs only on Toolforge;
this repository is for development, offline tests, and source deployments.

## Documentation

Choose the guide for your role:

- [Terminal user](docs/usage.md): run reports, inspect data, and create previews.
- [System administrator / maintainer](docs/administration.md): upload source
  files to Toolforge, promote deployments, stop, start, restart, and recover.
- [Application developer](docs/development.md): local setup, contribution
  workflow, tests, and release checks.
- [System-level developer](docs/architecture.md): component boundaries,
  data contracts, publication safety, scheduling, and engineering rules.

See the [documentation index](docs/README.md) and [change history](CHANGELOG.md).

## License

[CC0 1.0 Universal](LICENSE).

[dyk]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新条目推荐
[new-pages]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新进条目/关键词筛选
[assessments]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目
[pageviews]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/热门条目
[english]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科重要条目
[database]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告
