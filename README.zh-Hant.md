# Aranami

[English](README.md) · [繁體中文](README.zh-Hant.md) · [简体中文](README.zh-Hans.md)

Aranami（<span lang="ja">荒波</span>，意為「狂瀾」）維護維基百科報告，主要服務於
中文維基百科的電子遊戲專題。在 [Wikimedia PAWS][1] 上執行，
即可發布報告、預覽變更，或收集資料進行一次性分析。

## 選擇介面

| 想做的事 | 介面 | 變更或回傳的結果 |
|---|---|---|
| 啟動例行監視器 | `aranami.run()` | 回傳正在執行的排程器；首次啟動時立即檢查所有報告，隨後按各項工作設定的 UTC 時間更新。 |
| 按同一排程持續預覽 | `aranami.run(dry=True)` | 回傳正在執行的排程器，每次觸發時將提案寫入 `dry-run/`；不編輯維基頁面，也不請求 PexBot 刷新。 |
| 立即執行報告 | `aranami.run_once(...)` | 不論排程時間為何，都立即執行所有工作一輪並回傳 `None`；使用 `dry=True` 預覽。 |
| 立即執行選定報告 | `aranami.run_once(tasks=["new_pages", "pageviews"], ...)` | 只執行選定工作一輪；`tasks="new_pages"` 選擇單一工作。 |
| 以指定日期執行 | `aranami.run_once(date=..., ...)` | 為依日期執行的工作設定 UTC 基準，並讀取目前的來源資料。 |
| 自訂一項工作的目標或設定 | `aranami.jobs.<routine>.run(...)` | 立即更新該工作的頁面；`dry=True` 時寫入預覽。 |
| 取得優質條目及入選日期 | `analyze_quality_contents(site)` | 回傳條目表格；不編輯維基頁面，也不自動匯出結果。 |
| 取得資料或處理傳入的文字 | 下列來源及支援介面 | 回傳資料、顯示預覽或轉換文字；會寫入檔案的例外另有說明。 |

## 在 PAWS 安裝並持續監視

將 wheel 與 [scripts/run_aranami.py](scripts/run_aranami.py) 上傳到 PAWS。
在含有 Aranami wheel 的目錄中執行：

```shell
python scripts/run_aranami.py
```

啟動腳本會從目前目錄或 `dist/` 中選擇最後修改時間最新的 `aranami-*.whl`，
安裝它及相依套件，並呼叫新版本的 `aranami.run(dry=False)`。
新監視器立即檢查所有例行工作，隨後按 UTC 排程持續執行，
發布有變更的維基頁面並請求 PexBot 刷新。啟動腳本持續執行，
直到按 Ctrl+C 或筆記本的停止按鈕中斷；停止時會等待正在執行的工作完成。
安裝成功後，會刪除其他最後修改時間相同或較早的本機 Aranami wheel。
安裝失敗時保留所有 wheel，並且不啟動例行工作。
若要自行選擇 wheel：

```shell
python scripts/run_aranami.py "path/to/aranami-<version>.whl"
```

若腳本與筆記本位於同一目錄，請省略 `scripts/`。在筆記本儲存格中執行：

```python
%run scripts/run_aranami.py
```

也可以將完整腳本貼到一個儲存格中。執行腳本檔案時，`logs/` 和 `cache/`
位於腳本旁；貼入儲存格時，則使用筆記本的目前目錄。
即使筆記本先前已匯入舊版本，啟動腳本仍會使用新安裝的版本。
PAWS 環境是暫時的，因此伺服器重新啟動後需要再次執行。
請參閱 [PAWS 使用說明][3]。

腳本的公開 Python 介面包括 `install_wheel(argv=None)` 和 `run()`。
前者安裝選定的 wheel 並刪除較舊的本機上傳檔案，後者持續監視已安裝的套件並正式發布。
匯入腳本時，請明確傳入安裝引數：

```python
from scripts.run_aranami import install_wheel, run

install_wheel([])  # 尋找並安裝最新上傳的版本。
# install_wheel(["path/to/aranami-<version>.whl"])  # 自行選擇上傳的檔案。
run()
```

## 啟動例行監視器

`aranami.run(*, dry=False, clear_cache=False)` 啟動套件內的監視器，
回傳 APScheduler 的 `BackgroundScheduler`。各項工作按下表設定的 UTC 時間執行。
呼叫會立即回傳。新啟動的正式監視器立即檢查並更新所有報告，隨後按排程執行。

```python
import aranami

scheduler = aranami.run()  # 啟動定時發布。
scheduler.print_jobs()  # 顯示各項工作及下次 UTC 執行時間。
# scheduler.shutdown(wait=True)  # 停止，並等待正在執行的工作完成。
```

使用 `aranami.run(dry=True)` 可按同一排程持續產生本機預覽。
它仍會讀取目前資料、更新可丟棄的快取並寫出提案，但不編輯維基頁面或請求 PexBot 刷新。
APScheduler 隨 wheel 安裝，無需額外的筆記本安裝儲存格。
監視器沿用 PAWS 的身分驗證及 Wiki Replica 存取權限。

只要 Python 程序或 PAWS 核心仍在執行，監視器就會繼續工作。
重新啟動核心會清除排程；重新匯入套件後，再次呼叫 `aranami.run()`。
以相同 `dry` 值重複呼叫會重用正在執行的排程器，不會再次觸發初始檢查。
要切換該值或清除快取，請先呼叫 `scheduler.shutdown(wait=True)`。
監視器執行期間切換模式或傳入 `clear_cache=True` 會拋出 `ValueError`。
已暫停的監視器需要呼叫 `scheduler.resume()` 才會恢復。
`clear_cache=True` 會在啟動新監視器前刪除可重建的 `cache/` 資料。
若在筆記本中直接匯入，且核心先前載入過舊 wheel，請先重新啟動核心。

每次實際執行的工作都會依實際 UTC 執行日期，
附加記錄到 `logs/aranami.YYYY-MM-DD.log`。工作失敗會寫入記錄，後續排程工作繼續執行。
未變更的頁面不會儲存。資料重用可能建立或更新 `cache/`。
試執行會寫入 `dry-run/aranami-*.md`，
並為每項擬議編輯建立連結的 `.wikitext` 檔案。
維基文字檔案包含完整的擬議頁面文字。
即使另一項工作失敗，先前的預覽與已完成的提案仍會保留。
PexBot 預覽項目會描述刷新動作；未請求刷新時，無法取得其產生的維基文字。

## 立即執行報告

`aranami.run_once(*, date=None, dry=False, tasks=None, clear_cache=False)`
會在呼叫時立即執行一輪，不論例行工作的排程時間為何，然後回傳 `None`。
它不會啟動監視器，也不會等待 cron 時間。

```python
import aranami

aranami.run_once(dry=True)  # 立即預覽所有工作。
aranami.run_once(tasks="new_pages")  # 立即發布新進條目的變更。
aranami.run_once(tasks=["dyks", "pageviews"], dry=True)  # 合併為一份預覽。
```

省略 `tasks` 時執行全部六項工作。可用名稱為 `dyks`、`new_pages`、
`assessment_lists`、`pageviews`、`enwp_key_articles` 和 `pexbot`。
某項工作失敗後，其餘選定的獨立工作仍會繼續；
等其餘工作和合併預覽完成後，才會集中拋出失敗資訊。
`clear_cache=True` 在開始這一輪前刪除可重建的快取；
請求清除快取前，請先停止正在執行的監視器。

### 自訂一項例行工作

匯入所需的例行工作並呼叫其 `run()`：

```python
from aranami.jobs import dyks, new_pages, pageviews

new_pages.run(dry=True)
# dyks.run()       # 只發布新條目推薦的變更。
# pageviews.run()  # 最多發布一天尚缺的熱門條目報告。
```

六項例行工作都回傳 `None`，並接受關鍵字引數 `dry=False` 和 `context=None`。
單獨呼叫會寫入自己的每日記錄，並在試執行模式下產生自己的預覽。
若傳入執行環境，則以該環境的 `dry` 值為準；
呼叫者需用 `context.write_report()` 寫出合併的預覽。

## 例行工作與 UTC 排程

下列六項獨立工作維護中文維基百科的電子遊戲專題報告。
新啟動的正式監視器立即檢查所有工作，隨後按各項 UTC 排程執行。
正式執行儲存有變更的頁面；`dry=True` 寫入本機提案。
下方每個固定目標各占一列，皆位於 `WikiProject:电子游戏/` 下。

預設目標和 UTC cron 排程定義於
[src/aranami/monitor.py](src/aranami/monitor.py) 的 `TASK_DEFINITIONS`。
修改此檔案後建立替換 wheel，即可更改這些預設值；工作模組使用同一組定義，
監視器據此產生 `SCHEDULES`。「執行安排」欄表示觸發時間，不代表執行時長，
也不受筆記本本地時區影響。`aranami.run_once()` 不受這些時間限制，立即執行。
每項工作最多同時執行一個執行個體，錯過的多個觸發時間合併為一次執行嘗試。

| 例行腳本 | 更新頁面 | [執行安排](src/aranami/monitor.py) | 說明 |
|---|---|---|---|
| [dyks.py](src/aranami/jobs/dyks.py) | [新条目推荐](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新条目推荐) | 每小時 `HH:00` | 刷新已入選條目、候選條目、評級和隱藏的年度統計。[（詳細說明）](#dyk) |
| [new_pages.py](src/aranami/jobs/new_pages.py) | [新进条目/关键词筛选](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新进条目/关键词筛选) | 每日 `00:07` | 列出截至 UTC 昨天符合關鍵字的新建頁面及由重定向改寫的頁面；預設保留 100 個日期，並刷新全部保留記錄中的評級圖示。[（詳細說明）](#新進條目) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/Bplus](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/Bplus) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（詳細說明）](#評級列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/BPAN](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/BPAN) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（詳細說明）](#評級列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/A](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/A) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（詳細說明）](#評級列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/AL](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/AL) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（詳細說明）](#評級列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/ACC](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/ACC) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（詳細說明）](#評級列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/PPR](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/PPR) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（詳細說明）](#評級列表) |
| [pageviews.py](src/aranami/jobs/pageviews.py) | [热门条目](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/热门条目) | 每小時 `HH:59` | UTC 18:00之後，嘗試更新昨日訪問量資料。每次最多補一個缺少的日期，避免集中發出大量請求。[（詳細說明）](#熱門條目) |
| [enwp_key_articles.py](src/aranami/jobs/enwp_key_articles.py) | [数据库报告/英文维基百科重要条目](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科重要条目) | 每日 `23:24` | 刷新英文極高／高重要度條目、中文對應條目、評級和數量。[（詳細說明）](#英文條目對照) |
| [enwp_key_articles.py](src/aranami/jobs/enwp_key_articles.py) | [数据库报告/英文维基百科优质条目](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科优质条目) | 每日 `23:24` | 刷新英文 FA／FL／GA 條目、中文對應條目、評級和數量。[（詳細說明）](#英文條目對照) |
| [pexbot.py](src/aranami/jobs/pexbot.py) | [数据库报告](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告) 下的訂閱頁面 | 每日 `00:00` | 請求 PexBot 刷新列出的報告頁面。[（詳細說明）](#pexbot) |

連結中的例行工作均在 `aranami.jobs` 下提供 `run(...)`。
以下分別說明各項工作的結果和目標設定。

### DYK

新條目推薦刷新已入選條目、候選條目、評級和隱藏的年度統計。
使用 `title` 可選擇其他報告目標。`statistics_title` 預設為
`c:Data:Zhwiki_WikiProject_Video_Games_DYK_Annual_Statistics.tab`。
它用於標示可複製的隱藏統計內容；例行工作不儲存該 Commons 頁面。

### 新進條目

新進條目列表預設保留截至 UTC 昨天的 100 個日期。
一次呼叫會補齊保留範圍內所有缺少日期，並重用已有每日記錄。
每次呼叫都會刷新所有保留頁面條目的評級圖示，包括較早日期的條目。
使用 `title` 可選擇其他目標。

每個新進條目日期還會搜尋該 UTC 日帶有 `mw-removed-redirect` 標籤的編輯。
符合關鍵字且目前為普通頁面的結果會加入同一列表，在討論連結後標註
` — 自重定向页改写`。同一頁面在同一天只列出一次，即使當天也新建過，
或多次由重定向改寫。此類頁面沿用新建頁面的命名空間和關鍵字規則。
已有每日記錄繼續重用；補齊缺少日期時會執行兩種搜尋。

每項末尾會附加 HTML 註解，例如
`<!-- 页面ID 12345 · 创建时间 2026-08-05 11:22:33 -->`。
建立時間取頁面最早修訂的 UTC 時間，由重定向改寫的頁面也採用這一規則。
已有記錄會在中繼資料可用時補齊缺少的註解；已儲存的註解會在後續更新中保留。
無法取得建立時間時標記為 `未知`。

### 評級列表

評級列表在重建時刷新條目、評級和設定的評審圖示。
使用 `lists=[(title, AssessmentList(...)), ...]` 選擇列表和目標頁面。

### 熱門條目

熱門條目每次呼叫只推進一個缺少的報告日期；若當天資料無法取得，留待後續呼叫重試。
昨日觀測資料在 UTC 18:00 後可嘗試處理，每小時 `HH:59` 的排程首次在
UTC 18:59 嘗試更新；此前仍可補齊較早缺少的日期。
排程工作允許延遲最多 3,500 秒開始執行。評級隨新每日報告產生而刷新。
使用 `title` 選擇其他目標，用 `settings=REPORT_SETTINGS` 選擇專題成員和排名週期。

### 英文條目對照

兩項英文報告刷新極高／高重要度條目或 FA／FL／GA 條目，
以及中文對應條目、評級和數量。評級在列表重建時刷新。
使用包含 `important` 和 `quality` 的 `targets` 選擇報告目標。

### PexBot

PexBot 刷新訂閱的報告頁面，產生內容由其自身控制。
使用 `prefixes` 覆寫允許的頁面根路徑，或用 `prefixes=[]` 停用刷新。
只支援中文維基百科。

### 使用指定日期

將 `datetime.date` 作為 `date` 傳給 `aranami.run_once()`，
可指定 UTC 基準日期；省略時使用目前 UTC 日期。選定工作仍在呼叫時立即執行。
對新進條目而言，它是不包含當日的截止日期：若要包含 10 月 1 日，請使用 10 月 2 日。

```python
from datetime import date

import aranami

aranami.run_once(date=date(2026, 10, 2), dry=True, tasks="new_pages")
```

直接呼叫工作模組時，可透過 `JobContext.today` 設定相同基準。
自訂工作或將多項直接呼叫合併為一份預覽時，可以使用執行環境：

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

這會補齊保留範圍內截至 `report_day` 的新進條目日期，
不會把執行範圍限制為單一日期記錄。可以將同一執行環境傳給數項工作，
收集成一份預覽。基準日期控制截止日期及統計，
不會重建歷史頁面文字或專題成員名單。評級列表、英文條目對照及 PexBot
不論基準日期為何，都使用目前的來源資料。
記錄及預覽的時間戳記仍使用實際執行時間。

對熱門條目排名而言，報告日期必須早於基準日期（`date` 或 `context.today`），
並符合實際呼叫開始時的 UTC 就緒限制：
昨日資料到 UTC 18:00 才能嘗試處理，此前仍可補較早的缺少日期。
指定基準日期不會繞過這一限制。進度日期只認目標頁面的
`<!-- report date YYYY-MM-DD -->` 註解。工作據此處理下一個缺少的日期，
不會直接跳到指定日期。若要建立某個確切日期的排名而不變更頁面，請使用一次性報告產生器：

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
    + timedelta(days=1),  # 不包含的結束日期；report.data_date 是 10 月 1 日。
    settings=REPORT_SETTINGS,
)
Path("pageviews-2026-10-01.wikitext").write_text(report.text, encoding="utf-8")
```

`build_report()` 回傳排名區段文字與領先條目的資料，
不編輯維基頁面，但可能更新 `cache/`。
明確呼叫的 `write_text()` 會在呼叫者的目前目錄建立指定檔案。
瀏覽量資料不完整或無法取得時，會拋出 `PageviewsUnavailableError`
或 `PageviewsDeferredError`。

## 一次性工具

### 取得優質條目及其入選日期

`aranami.services.quality_contents.analyze_quality_contents(site, *,
most_recent=True)` 列出英文或中文電子遊戲專題中目前的典範條目、
特色列表及優良條目。回傳內容包括標題、評級、重要度、
入選日期及其來源、文字長度和修訂版本 ID。
未知日期保持缺失；`most_recent=False` 選取最早有依據的成功入選事件，
而不是最近的一次。

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

# 下列匯出操作會明確在呼叫者的目前目錄建立檔案。
listing_dates.write_csv("quality-articles-zh.csv")
length_statistics(articles).write_csv("quality-lengths-zh.csv")
year_statistics(articles).write_csv("quality-listing-years-zh.csv")
```

英文專題請使用 `pywikibot.Site("en", "wikipedia")`。
這些工具不編輯維基頁面，也不自動匯出結果。
中文詞數計算可能在 `cache/words/` 下建立可丟棄的檔案。

| `aranami.services.quality_contents` 中的介面 | 回傳結果 |
|---|---|
| `analyze_quality_contents(site, *, most_recent=True)` | 含入選日期與測量值的條目列表。 |
| `length_statistics(frame)` | 按條目評級統計數量、正文長度中位數，以及中央 68%／95% 範圍。 |
| `year_statistics(frame)` | 按入選年份、評級及重要度統計條目數量；略過無法確定的日期。 |

### 取得每日瀏覽量、查詢資料與檢視模板

這些介面回傳或顯示資料，不編輯維基百科，也不自動匯出結果檔案。
瀏覽量範圍包含 `start`，不包含 `stop`；
缺失的觀測值與實際測得的零值分開處理。

| 公開介面 | 功能與回傳內容 |
|---|---|
| `aranami.sources.pageviews.fetch_data(site, page, start, stop)` | 回傳單一頁面的 `DailyPageviews(date, pageview)` 觀測值列表。 |
| `aranami.sources.pageviews.fetch_data_dataframe(site, page, start, stop)` | 回傳含 `page`、`date` 和 `pageview` 欄位的每日觀測值。 |
| `aranami.sources.pageviews.massive(site, pages, start, stop)` | 回傳多個頁面在相同日期範圍內的每日觀測值。 |
| `aranami.sources.quarry.Replica(project, extension=None)` | 選擇 Wiki Replica。`Replica.from_site(site, *, extension=None)` 選擇傳入網站的副本；`Replica.wikidata_terms()` 選擇 Wikidata 詞彙資料。 |
| `Replica.query(statement, *, parameters=None, schema_overrides=None)` | 回傳 `QueryFrame`；`.pipe(postprocessor)` 加入表格轉換，`.collect()` 讀取結果。連線屬性包括 `hostname`、`database`、`url` 和 `engine`。 |
| `aranami.sources.quarry.tables` | 提供自訂查詢使用的唯讀資料表定義；詳見下方對應目錄。 |
| `aranami.support.gtshow(frame, *, first=5, last=5)` | 在 JupyterLab 顯示精簡表格預覽；回傳 `None`，不建立檔案。 |
| `aranami.support.get_templates(text, template, site)` | 從傳入的維基文字回傳相符且可編輯的模板節點。修改節點只會變更記憶體中已解析的文字。 |
| `aranami.support.open_run_log(directory=None)` | 開啟記錄環境並提供記錄器；將每日 UTC 記錄附加到 `logs/` 或指定目錄。 |

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
# traffic.write_csv("daily-traffic.csv")  # 明確執行的選用匯出。
```

[Quarry 範例][7] 展示自訂條目查詢。
[Pageviews 範例][8] 結合條目選擇與瀏覽量收集。

## 設定與檔案

報告會取代指定 HTML 註解範圍內的內容，並保留周圍的頁面內容。
開頭註解上的設定可控制保留範圍、瀏覽量歷史、
缺失資料門檻或評級來源分類：

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...generated content...
<!-- aranami end="new-pages" -->
```

支援的標記及預設值見 [CONTRIBUTING.md][9]。
修改 [src/aranami/monitor.py](src/aranami/monitor.py) 中
`TASK_DEFINITIONS` 的 PexBot `prefixes`，然後建立替換 wheel，
即可選擇允許的訂閱根頁面。
`aranami.jobs.pexbot.PEXBOT_PREFIXES` 保留為這些預設值的相容別名。
將 `prefixes` 傳給該工作可只覆寫這次呼叫的範圍；空選擇會停用刷新。

未使用啟動腳本時，路徑相對於呼叫者的目前目錄：

| 位置 | 寫入來源與用途 |
|---|---|
| `logs/aranami.YYYY-MM-DD.log` | 所有例行工作及 `open_run_log()` 都會附加開始、完成、略過和失敗記錄。保留最近 90 個已完成 UTC 日期，以及當日記錄。 |
| `dry-run/aranami-*.md` 及連結的 `.wikitext` 檔案 | 完整或單獨的試執行，或明確呼叫 `JobContext.write_report()`，會寫入可供檢視的摘要及完整擬議頁面文字。 |
| `cache/` | 報告例行工作會重用下載的資料，並儲存本機條目身分快照，預覽時也一樣。中文詞數計算可能使用 `cache/words/`。沒有工作正在執行時可以刪除它；也可停止監視器後使用 `aranami.run_once(clear_cache=True)` 重建。 |
| 呼叫者指定的檔名 | 一次性工具回傳結果；呼叫者明確使用 `write_csv()`、`write_parquet()` 或 `write_text()` 才會建立匯出檔案。 |

熱門條目排名預設保留 800 天的瀏覽量歷史。
[熱門條目工作](src/aranami/jobs/pageviews.py) 定義 `DATA_READY_HOUR=18`：
實際呼叫在 UTC 18:00 或之後開始時，
才可嘗試處理昨日資料。每小時 `HH:59` 的排程使首個正常嘗試時間為 UTC 18:59。
18:00 前仍可補較早的缺少日期，每次呼叫最多推進一天。
正式執行、預覽和手動呼叫工作均遵循這一規則；指定基準日期不會繞過截止限制。
已有的 `lag-days` 屬性不再影響選定的報告日期。
若目標日期至少有 95% 的條目缺少資料，則等待之後再發布。
暫時性的請求失敗會重試；後續執行可以接續已完成的工作，
而不提前推進已發布日期。

## 其他公開介面參考

此目錄按匯入模組列出其餘公開類別和函式。
上方的主要入口涵蓋例行使用方式。同一列中的名稱具有所述的共同用途；
來源連結提供完整的引數契約。除非註明檔案效果，
這些介面只回傳值或修改傳入的記憶體物件，
不儲存維基頁面，也不匯出檔案。

### 執行與報告服務

| 模組 | 公開介面 | 結果或效果 |
|---|---|---|
| [`aranami.monitor`](src/aranami/monitor.py) | `TaskDefinition`、`TASK_DEFINITIONS`、`RoutineSchedule`、`SCHEDULES`、`start`、`clear_caches` | 在同一模組中定義預設目標頁面、UTC cron 參數和 PexBot 訂閱範圍，產生排程，並啟動或重用 `aranami.run()` 回傳的監視器。`RoutineSchedule.trigger()` 回傳相應的 UTC cron 觸發器。`clear_caches()` 在監視器停止後刪除可重建的快取。 |
| [`aranami.runner`](src/aranami/runner.py) | `run`、`run_once` | 由 `aranami` 同時匯出的定時及立即執行入口。 |
| [`aranami.jobs`](src/aranami/jobs/__init__.py) | `JobContext`, `ProposedEdit`, `job_run` | 共用執行環境、編輯提案及例行工作生命週期環境。`JobContext.publish()` 在正式模式儲存有變更的頁面，在試執行模式記錄本機提案。`write_report()` 回傳預覽路徑，正式執行時回傳 `None`；`start_task()`、`finish_task()` 和 `defer()` 記錄工作狀態。`job_run()` 提供執行環境並記錄工作活動。 |
| [`aranami.services.quality_milestones`](src/aranami/services/quality_milestones.py) | `QualityTarget`, `QualityMilestone`, `QualityProfile`; `normalize_status_code`, `normalize_action_name`, `normalize_result`, `is_target_action`, `is_success_result`, `choose_milestone`, `extract_history_milestones`, `extract_quality_milestone_from_wikitext` | 描述並從傳入文字擷取有依據的成功入選事件；回傳事件記錄，無法確定時不回傳結果。 |
| [`aranami.services.quality_profile`](src/aranami/services/quality_profile.py) | `MilestoneExtractor`, `QualityAnalysisProfile` | 描述某個維基的品質分析選項與日期擷取介面。 |
| [`aranami.services.enwiki.quality_dates`](src/aranami/services/enwiki/quality_dates.py), [`aranami.services.zhwiki.quality_dates`](src/aranami/services/zhwiki/quality_dates.py) | `extract_milestone(text, status, *, site, most_recent=True, aliases=None)` | 回傳適用於該維基的 `QualityMilestone` 或 `None`。 |
| [`aranami.services.zhwiki.dyk_dates`](src/aranami/services/zhwiki/dyk_dates.py) | `extract_dates` | 從傳入的討論頁文字回傳新條目推薦日期，包括未知值。 |
| [`aranami.services.zhwiki.dyks`](src/aranami/services/zhwiki/dyks.py) | `prepare_report`, `update_text`, `render_reports`, `build_statistics`, `article_members` | 回傳附條目身分的報告文字、更新後的新條目推薦頁面文字、產生的區段、按日期統計的數量，或成員名單。讀取來源資料時，可能更新可丟棄的新條目推薦快取。 |
| [`aranami.services.zhwiki.new_pages`](src/aranami/services/zhwiki/new_pages.py) | `update_text`, `matches_keywords`, `update_icons`, `record_dates`, `record_counts` | 回傳更新後的每日列表文字、關鍵字比對結果、更新後的圖示、已記錄日期，或條目／非條目數量。 |
| [`aranami.services.zhwiki.assessment_lists`](src/aranami/services/zhwiki/assessment_lists.py) | `ReviewInfo`, `AssessmentList`; `prepare_report`, `prepare_text`, `update_text`, `review_heading`, `article_members`, `article_titles` | 設定列表，並回傳附條目身分的報告文字、準備好的列表文字、評審錨點或成員名單。 |
| [`aranami.services.zhwiki.pageviews`](src/aranami/services/zhwiki/pageviews.py) | `ReportPeriod`, `TaskForce`, `ReportSettings`, `PageviewReport`; `current_data_date`, `report_periods`, `require_daily_observations`, `aggregate_views`, `build_report`, `update_text`; `PageviewsUnavailableError`, `PageviewsDeferredError` | 設定排名，並回傳日期、區間、瀏覽量總計或報告文字。`build_report` 在 `cache/` 下寫入可丟棄的完整快取／待完成檔案；`aggregate_views` 只有在傳入快取時才暫存資料。錯誤表示觀測值不可用或請求已延後。 |
| [`aranami.services.zhwiki.enwp_key_articles`](src/aranami/services/zhwiki/enwp_key_articles.py) | `ReportSpec`, `OldArticle`, `ReportData`; `prepare_reports`, `build_reports`, `build_enriched_rows`, `filter_report_rows`, `count_report_rows`, `normalize_en_pages`; `build_report_item_template`, `render_item`, `render_body`, `update_page_text`; `parse_item_id`, `parse_old_articles`, `build_item_to_zh_title`, `format_summary_article`, `encode_length`, `truncate_summary_parts`, `build_edit_summary`; `english_sort_key`, `heading_key`, `safe_text`, `escape_template_value`, `replace_marker_value`, `replace_body` | 準備英文／中文條目對照、篩選並統計資料列、產生報告文字、讀取舊成員名單，以及格式化摘要或單一文字值。只回傳值，不發布。 |
| [`aranami.services.zhwiki.pexbot`](src/aranami/services/zhwiki/pexbot.py) | `StreamEvent`; `require_zhwiki`, `subscribed_titles`, `parse_event` | 驗證網站、回傳指定範圍內的訂閱標題，或解碼進度訊息。這些輔助介面不請求刷新。 |

### 唯讀資料來源

| 模組 | 公開介面 | 回傳結果 |
|---|---|---|
| [`aranami.sources.wiki`](src/aranami/sources/wiki.py) | `read_pages`, `read_page_ids`, `template_aliases` | 預先載入的目前頁面，或模板標題及別名。 |
| [`aranami.sources.dyk`](src/aranami/sources/dyk.py) | `TalkPage`, `read_talk_pages` | 討論頁文字及其實際修訂版本 ID。 |
| [`aranami.sources.quarry.projects`](src/aranami/sources/quarry/projects.py) | `TagSpec`; `query_pages_by_wikiproject`, `category_members`, `latest_revisions`, `new_page_ids`, `redirect_converted_page_ids`, `page_creation_metadata`, `query_tags` | 專題評級、分類成員、修訂版本 ID、新建或由重定向改寫的頁面 ID、頁面身分及最早修訂的 UTC 時間，或附加的維護標籤。 |
| [`aranami.sources.quarry.quality`](src/aranami/sources/quarry/quality.py) | `fetch_quality_articles(site, *, project_title, classes)` | 選定的已評級條目，包括評級、重要度及顯示／排序標題。入選日期由 `analyze_quality_contents` 提供，此資料查詢不提供。 |
| [`aranami.sources.quarry.enwp`](src/aranami/sources/quarry/enwp.py) | `fetch_en_key_pages`, `fetch_wikidata_sitelinks`, `fetch_wikidata_labels`, `fetch_zh_page_states` | 英文重要條目、優先使用的連結標題／標籤，或中文條目狀態。 |

`aranami.sources` 匯出 `pageviews` 和 `quarry` 模組。
也可從 `aranami.sources.quarry` 匯入 `QueryFrame`，
從 `aranami.sources.pageviews` 匯入 `DailyPageviews`。

<details>
<summary>aranami.sources.quarry.tables 中的公開名稱</summary>

這些類別描述唯讀查詢欄位；單獨使用不會取得資料、變更維基頁面或建立檔案。
`Base` 保存資料表的中繼資料。
`StringDecoder`、`TextDecoder`、`EnumDecoder` 和 `BinaryDecoder`
將查詢值標準化；其 `process_bind_param` 和 `process_result_value`
回呼會回傳轉換後的值。

資料表對應包括：

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
`WbtTermInLang`, `WbtText` 和 `WbtTextInLang`。

欄位見[資料表定義](src/aranami/sources/quarry/tables.py)。

</details>

### 文字、記錄與快取支援

| 模組 | 公開介面 | 結果或檔案效果 |
|---|---|---|
| [`aranami.support.cache`](src/aranami/support/cache.py) | `clear_runtime_cache` | 刪除呼叫者的 `cache/` 目錄。 |
| [`aranami.support.dates`](src/aranami/support/dates.py) | `parse_date`, `parse_complete_date` | 回傳解析後的日期或 `None`；後者要求完整的日曆日期。 |
| [`aranami.support.templates`](src/aranami/support/templates.py) | `template_page`, `normalize_param_name`, `template_get_raw_param`, `template_get_param`, `normalize_oldid` | 回傳模板身分、可編輯或清理後的參數值、標準化名稱，或修訂版本 ID。 |
| [`aranami.support.wikitext`](src/aranami/support/wikitext.py) | `clean_value`, `get_templates`; 重新匯出下列範圍函式 | 回傳清理後的單一文字值或相符且可編輯的模板。 |
| [`aranami.support.regions`](src/aranami/support/regions.py) | `has_region`, `region_options`, `region_content`, `integer_option`, `managed_region`, `replace_by_tag` | 檢查具名註解範圍／設定，或回傳包裝／取代後的文字。 |
| [`aranami.support.prose`](src/aranami/support/prose.py) | `ProseProfile`, `extract_prose` | 設定擷取規則並回傳可讀的條目正文。 |
| [`aranami.support.words`](src/aranami/support/words.py) | `count_words` | 回傳英文單字或中文詞數；中文計算可能建立 `cache/words/` 檔案。 |
| [`aranami.support.edit_summary`](src/aranami/support/edit_summary.py) | `EditSummary`; `append`, `add_group`, `daily`, `membership`, `render`, `with_execution_time` | 建立編輯摘要字串，可加入選用說明、成員變更及經過時間。 |
| [`aranami.support.report_membership`](src/aranami/support/report_membership.py) | `MembershipReport`, `load_membership`, `save_membership`, `member_identifier`, `membership_changes` | 回傳附條目身分的報告文字、快取身分、舊資料列 ID，或新增／移除的標題列表。`save_membership` 寫入 `cache/report-membership-<hash>-v1.parquet`。 |
| [`aranami.support.dyk_cache`](src/aranami/support/dyk_cache.py) | `load`, `save` | 讀取／寫入可丟棄的 `cache/vg_dyks_talk_pages-v2.parquet`。 |
| [`aranami.support.pageviews_cache`](src/aranami/support/pageviews_cache.py) | `PageviewsCache`; `load`, `get`, `replace`, `save_pending`, `discard_pending`, `save` | 讀取或暫存每日歷史；儲存／丟棄方法會寫入或刪除 `cache/pageviews-<hash>-v1.parquet` 及帶日期的待完成檔案。 |

`aranami.support` 重新匯出 `get_templates`、`gtshow` 和 `open_run_log`。
後兩者也可從同名支援模組取得。

## 開發

Aranami 需要 Python 3.12 或更新版本，並使用 [`uv`][2]：

```shell
uv sync
uv run python -m unittest discover -s tests/unit -v
uv run python -m unittest discover -s tests/integration -v
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv build --wheel --clear
```

此儲存庫用於本機開發、測試及建立 wheel。
儲存庫規則見 [AGENTS.md][4] 和 [CONTRIBUTING.md][9]。
每次明確建立 wheel 前，請遞增其測試組建後綴並重新產生鎖定檔。
已完成的變更記錄於 [CHANGELOG.md][5]。

## 授權

Aranami 以 [CC0 1.0 Universal][6] 釋出。

[1]: https://wikitech.wikimedia.org/wiki/PAWS
[2]: https://docs.astral.sh/uv/
[3]: https://wikitech.wikimedia.org/wiki/PAWS/Python_with_Pip
[4]: AGENTS.md
[5]: CHANGELOG.md
[6]: LICENSE
[7]: tests/live/quarry_paws.py
[8]: tests/live/pageviews_paws.py
[9]: CONTRIBUTING.md
