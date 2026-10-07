# Aranami

[English][1] · [繁體中文][2] · [简体中文][3]

Aranami（<span lang="ja">荒波</span>，意為「狂瀾」）協助維護
中文維基百科電子遊戲專題的報告，整理新進條目、條目評級、瀏覽量，
以及英文與中文維基百科的條目對照，方便專題成員掌握內容變化。

Aranami 在 [Wikimedia PAWS][4] 上
定時或立即更新報告，也能預覽修改。

## 定時工作

以下是六項工作的預定開始時間，均以 UTC 計算；`HH` 表示每小時。

| 工作 | 執行時間（UTC） | 更新內容 |
|---|---|---|
| 新條目推薦 | 每小時 `HH:00` | 更新[新條目推薦][5]的已入選條目、候選條目與評級，並更新季度累積入選條目計數。 |
| 新進條目 | 每日 `00:07` | 在[新進條目／關鍵字篩選][6]中，依關鍵字篩選每日可能與電子遊戲有關的新頁面（及由重定向改寫的頁面），並更新專題評級圖示，方便核查。 |
| 評級與評審名單 | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新[甲級（A）][7]、[甲級列表（AL）][8]、[甲級評審（ACC）][9]、[乙上級（Bplus）][10]、[乙上級評審（BPAN）][11]和[專題同儕評審（PPR）][12]六份名單，顯示目前評級及評審標記。 |
| 熱門條目 | 每小時 `HH:59` | 更新[熱門條目][13]的每日、每週、每月、每季及每年瀏覽量排名，以及各工作組的月度排名，顯示名次變化。 |
| 英文條目對照 | 每日 `23:24` | 更新[英文重要條目（極高、高重要度）][14]和[英文優質條目（典範、優良）][15]報告，列出對應的中文條目。 |
| PexBot 報告 | 每日 `00:00` | 請求 PexBot 更新[資料庫報告][16]下已訂閱的頁面，報告內容由 PexBot 產生。 |

對於按日更新的報告，重新執行腳本時，會嘗試補齊之前的頁面。

## 文件

[文件目錄（英文）][17] 彙整使用說明與開發資料：

- [使用指南][18]：安裝、執行、報告設定及程式介面。
- [開發指南][19]：設計、開發慣例、測試及 wheel 建置。
- [變更記錄][20]：各版本的功能、行為及文件變更。

## 授權

Aranami 以 [CC0 1.0 Universal][21] 釋出。

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
