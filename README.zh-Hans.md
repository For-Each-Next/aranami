# Aranami

[English][1] · [繁體中文][2] · [简体中文][3]

Aranami（<span lang="ja">荒波</span>，意为“狂澜”）维护维基百科专题报告，
主要服务于中文维基百科的电子游戏专题。
它整理页面列表、条目评级、入选记录和浏览量，帮助专题成员发现新页面、
了解条目质量和读者关注的内容，并对照英文与中文维基百科的条目。

Aranami 在 [Wikimedia PAWS][4] 上运行，
可以定时或手动更新报告，也可以先预览修改。

## 定时任务

下表列出六项定时任务。时间均为 UTC，表示任务的计划开始时间；
`HH` 表示每小时。

| 任务 | 运行时间（UTC） | 更新内容 |
|---|---|---|
| 新条目推荐 | 每小时 `HH:00` | 在[新条目推荐][5]中汇总已入选和候选的 DYK 条目，更新评级，并更新季度累计入选条目计数。 |
| 新页面关键词筛选 | 每日 `00:07` | 在[关键词筛选列表][6]中，根据关键词，筛选每日可能的电子游戏新页面（及由重定向改写的页面），并更新专题评级图示以便核查。 |
| 评级列表 | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新[甲级（A）][7]、[甲级列表（AL）][8]、[甲级评审（ACC）][9]、[乙上级（Bplus）][10]、[乙上级评审（BPAN）][11]和[专题同侪评审（PPR）][12]列表中的条目、评级和评审标记。 |
| 热门条目 | 每小时 `HH:59` | 在[热门条目][13]中，按各统计周期更新浏览量排名及条目评级。 |
| 英文条目对照 | 每日 `23:24` | 更新[英文重要条目（极高、高重要度）][14]和[英文优质条目（典范、优良）][15]报告，列出对应的中文条目。 |
| PexBot 报告刷新 | 每日 `00:00` | 请求 PexBot 刷新[数据库报告][16]范围内已订阅的报告页面。 |

对于按日更新的报告，重新运行脚本时，会尝试补全之前的页面。

## 文档

[文档目录（英文）][17] 汇总使用说明和开发资料：

- [使用指南][18]：在 PAWS 上安装和运行、设置报告，以及使用公开接口。
- [设计与开发指南][19]：组件分工、数据约定、测试和构建流程。
- [变更记录][20]：功能、运行方式和文档的修改记录。

## 许可证

Aranami 以 [CC0 1.0 Universal][21] 发布。

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
