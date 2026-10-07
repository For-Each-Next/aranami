# Aranami

[English][1] · [繁體中文][2] · [简体中文][3]

Aranami (<span lang="ja">荒波</span>, "rough waves") maintains Wikipedia
reports, primarily for WikiProject Video games on Chinese Wikipedia.
It brings together article discovery, project assessments, traffic rankings,
and comparisons with English Wikipedia.

Aranami runs on [Wikimedia PAWS][4]. It supports scheduled and immediate
report updates and previews of proposed changes.

## Scheduled tasks

Six independent tasks maintain the project's reports on these UTC schedules.
The times indicate recurring triggers; `HH` means every hour.

| Task | Schedule (UTC) | What Aranami maintains |
|---|---|---|
| DYK | Hourly at `HH:00` | Updates the [DYK report][5] with completed entries, active nominees, assessment grades, and quarterly cumulative counts of completed entries. |
| New pages | Daily at `00:07` | Uses keywords to identify each day's new pages that may relate to video games, including pages rewritten from redirects, in the [new-page report][6], and updates project assessment icons to help review them. |
| Assessment lists | Daily at `01:31`, `07:31`, `13:31`, `19:31` | Updates six assessment and review lists—[A-Class][7], [A-Class lists (AL)][8], [A-Class review (ACC)][9], [Bplus-Class][10], [Bplus-Class review (BPAN)][11], and [Project peer review (PPR)][12]—including grades and review icons. |
| Pageviews | Hourly at `HH:59` | Updates the [popularity rankings][13] for daily, weekly, monthly, seasonal, and yearly periods, plus monthly task-force rankings. Missing report dates advance one day at a time when traffic data is available. |
| English comparisons | Daily at `23:24` | Updates reports on [Top-importance and High-importance English articles][14] and [featured and good English content][15], listing their corresponding Chinese articles. |
| PexBot reports | Daily at `00:00` | Requests refreshes of subscribed [database reports][16] within the project's configured scope. |

For reports updated daily, rerunning the script attempts to complete earlier
report pages.

## Documentation

[Documentation index][17] brings together usage instructions and
development resources:

- [Usage guide][18]: PAWS installation and execution, report settings, and
  the public API.
- [Design and development guide][19]: component responsibilities, data
  contracts, tests, and build procedures.
- [Change history][20]: feature, behavior, and documentation changes.

## License

Aranami is released under [CC0 1.0 Universal][21].

[1]: README.md
[2]: README.zh-Hant.md
[3]: README.zh-Hans.md
[4]: https://wikitech.wikimedia.org/wiki/PAWS
[5]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新条目推荐
[6]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新进条目/关键词筛选
[7]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/A
[8]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/AL
[9]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/ACC
[10]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/Bplus
[11]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/BPAN
[12]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/PPR
[13]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/热门条目
[14]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科重要条目
[15]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科优质条目
[16]: https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告
[17]: docs/README.md
[18]: docs/usage.md
[19]: docs/development.md
[20]: CHANGELOG.md
[21]: LICENSE
