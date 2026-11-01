# <img src="src/webservice/public_html/assets/images/aranami.svg" width="40" height="40" alt=""> Aranami

Aranami (荒波, “rough waves”) keeps Wikipedia project reports up to date
and provides web tools to help readers and editors find articles to improve.
Its main focus is
[WikiProject Video games on Chinese Wikipedia](https://zh.wikipedia.org/wiki/WikiProject:电子游戏).

## Webservice

Use Aranami's three public tools:

- [Dashboard](https://aranami.toolforge.org/): current content reviews,
  main-page highlights, upcoming quality content, and popular articles.
- [Maintenance lists](https://aranami.toolforge.org/maintenance/): articles
  needing attention, with filters to find work that interests you.
- [Newsletter generator](https://aranami.toolforge.org/newsletter/): quarterly
  drafts with editable wikitext, blank editorial templates, and a January
  annual feature.

The dashboard refreshes content reviews hourly and other sections daily.
Review cards link to active discussions and show assessments, introductions,
and known start dates and durations. If a refresh fails, saved results remain
available while Aranami retries.

Newsletter drafts refresh at `00:00 UTC` daily until publication, including
issues delayed past the end of a quarter. The generator opens the earliest
unpublished issue and lets editors browse earlier drafts.

## On-wiki report updates

Aranami automatically updates the Wikipedia reports below. Use the
[Reports monitor](https://aranami.toolforge.org/reports/) to check results,
recent updates, and links to each on-wiki report.

These are UTC trigger times, not promises that an edit will be necessary.
A successful check can finish with **no update needed**.

| Report | Frequency and UTC trigger | Result |
|---|---|---|
| [New-article recommendations (DYK)][dyk] | Hourly, `HH:00` | Completed DYK entries, current nominees, assessments, and cumulative statistics. |
| [New pages][new-pages] | Daily, `00:07` | Keyword-selected new pages and pages rewritten from redirects, with assessments to help editors review them. |
| [Article assessments][assessments] | Daily, when idle after `00:00` | Six assessment and review lists: Bplus, BPAN, A, AL, ACC, and PPR. |
| [Assessment change log][assessment-log] | Daily, `00:00:00` | Removals, new assessments, reassessments, and renames grouped by observation date. |
| [Pageview rankings][pageviews] | Daily cycle from `18:00`; normal first attempt `18:59`, retries at `HH:59` | Daily, weekly, monthly, seasonal, yearly, and monthly task-force rankings. |
| [English Wikipedia comparisons][english] | Daily, when idle after `00:00`, after assessment capture | Important and high-quality English articles, their Chinese counterparts, and relevant changes. |
| [Database reports][database] | Daily, when idle after `00:00` | Refreshed PexBot maintenance reports for the project. |

New pages and Pageviews fill missing daily reports. Pageviews uses an 18:00 UTC
reporting boundary; incomplete data retries hourly. Tasks marked “when idle”
wait for other scheduled work. PexBot reports updated within the preceding
24 hours are not requested again.

See the [change history](CHANGELOG.md) for completed improvements.

## Documentation

- [Terminal usage](docs/usage.md): inspect results, preview reports, and run
  selected routines on Toolforge.
- [Administration](docs/administration.md): deploy, operate, and recover services.
- [Development](docs/development.md): prepare changes and verify them offline.
- [Architecture](docs/architecture.md): maintain component and data contracts,
  together with [repository instructions](AGENTS.md) and the scoped
  [backend](src/backend/AGENTS.md) and [updater](src/updater/AGENTS.md) rules.

## License

[CC0 1.0 Universal](LICENSE).

[dyk]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新条目推荐
[new-pages]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新进条目/关键词筛选
[assessments]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目
[assessment-log]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/评级/日志
[pageviews]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/热门条目
[english]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科重要条目
[database]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告
