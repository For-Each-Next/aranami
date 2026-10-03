# Aranami

[English](README.md) · [繁體中文](README.zh-Hant.md) · [简体中文](README.zh-Hans.md)

Aranami（<span lang="ja">荒波</span>，意为“狂瀾”）维护维基百科报告，主要服务于中文维基百科的电子游戏专题。
在 [Wikimedia PAWS][1] 上运行它，可以发布报告、预览修改，或收集数据进行一次性分析。

## 选择接口

| 想要完成的工作 | 接口 | 修改内容或返回结果 |
|---|---|---|
| 启动例行监视器 | `aranami.run()` | 返回正在运行的调度器；首次启动时立即检查全部报告，随后按各项工作设定的 UTC 时间更新。 |
| 按同一计划持续预览 | `aranami.run(dry=True)` | 返回正在运行的调度器，每次触发时将提案写入 `dry-run/`；不会编辑维基页面或请求刷新 PexBot 报告。 |
| 立即运行报告 | `aranami.run_once(...)` | 无论计划时间为何，都立即运行全部工作一轮并返回 `None`；使用 `dry=True` 预览。 |
| 立即运行选定报告 | `aranami.run_once(tasks=["new_pages", "pageviews"], ...)` | 仅运行选定工作一轮；`tasks="new_pages"` 选择单项工作。 |
| 使用指定日期运行 | `aranami.run_once(date=..., ...)` | 为依赖日期的工作设置 UTC 基准日期；读取当前的来源数据。 |
| 自定义一项工作的目标或设置 | `aranami.jobs.<routine>.run(...)` | 立即更新该项工作的页面；设置 `dry=True` 时写入本地预览。 |
| 获取优质条目及其入选日期 | `analyze_quality_contents(site)` | 返回条目数据表；不会编辑维基页面或自动导出结果。 |
| 获取数据或处理提供的文本 | 下方的数据源和辅助接口 | 返回数据、显示预览或转换文本；会写入文件的接口在下方注明。 |

## 在 PAWS 上安装并持续监视

将 wheel 和 [scripts/run_aranami.py](scripts/run_aranami.py) 上传到 PAWS。
在包含 Aranami wheel 的目录中运行：

```shell
python scripts/run_aranami.py
```

启动脚本选择修改时间最新的 `aranami-*.whl`，查找范围为当前目录或 `dist/`。
它安装该 wheel 及其依赖，并调用新版本的 `aranami.run(dry=False)`。
新监视器立即检查全部例行工作，随后按 UTC 计划持续运行，
发布有变化的维基页面并请求 PexBot 刷新。启动脚本会持续运行，
直到按 Ctrl+C 或笔记本的停止按钮中断；停止时等待正在运行的工作完成。安装成功后，
脚本会删除修改时间相同或更早的其他本地 Aranami wheel。
如果安装失败，脚本会保留所有 wheel，并且不会启动例行工作。
要自行选择 wheel：

```shell
python scripts/run_aranami.py "path/to/aranami-<version>.whl"
```

如果脚本与笔记本放在同一目录，省略 `scripts/`。在笔记本单元格中运行：

```python
%run scripts/run_aranami.py
```

也可以将完整脚本粘贴到一个单元格中。直接执行文件时，`logs/` 和 `cache/`
位于脚本所在目录；粘贴到单元格中执行时，则使用笔记本的当前目录。
即使笔记本之前导入了旧版本，启动脚本也会使用新安装的版本。
PAWS 环境是临时的，因此服务器重启后需要再次运行脚本。参见 [PAWS 使用指引][3]。

脚本的公开 Python 接口包括 `install_wheel(argv=None)` 和 `run()`：
前者安装选中的 wheel 并删除较旧的本地上传文件；后者持续监视已安装的包并正式发布。
以导入方式使用脚本时，应明确传入安装参数：

```python
from scripts.run_aranami import install_wheel, run

install_wheel([])  # 查找并安装最新上传的 wheel。
# install_wheel(["path/to/aranami-<version>.whl"])  # 自行选择上传的 wheel。
run()
```

## 启动例行监视器

`aranami.run(*, dry=False, clear_cache=False)` 启动包内的监视器，
返回 APScheduler 的 `BackgroundScheduler`。各项工作按下表设定的 UTC 时间运行。
调用会立即返回。新启动的正式监视器立即检查并更新全部报告，随后按计划运行。

```python
import aranami

scheduler = aranami.run()  # 启动定时发布。
scheduler.print_jobs()  # 显示各项工作及其下次 UTC 运行时间。
# scheduler.shutdown(wait=True)  # 停止，并等待正在运行的工作完成。
```

使用 `aranami.run(dry=True)` 可按同一计划持续生成本地预览。
它仍会读取当前数据、更新可删除的缓存并写出提案，但不会编辑维基页面或请求刷新 PexBot 报告。
APScheduler 随 wheel 安装，无需额外的笔记本安装单元格。
监视器沿用 PAWS 的身份认证和 Wiki Replica 访问权限。

只要 Python 进程或 PAWS 内核仍在运行，监视器就会继续工作。
重启内核会清除其调度计划；重新导入包后，再次调用 `aranami.run()`。
以相同 `dry` 值重复调用会复用正在运行的调度器，不会再次触发初始检查。
要切换该值或清除缓存，请先调用 `scheduler.shutdown(wait=True)`。
监视器运行期间切换模式或传入 `clear_cache=True` 会抛出 `ValueError`。
已暂停的监视器需要调用 `scheduler.resume()` 才会恢复。
`clear_cache=True` 会在启动新监视器前删除可重新生成的 `cache/` 数据。
在笔记本中直接导入时，如果内核已加载旧 wheel，请先重启内核。

每次实际执行的工作都会追加记录到 `logs/aranami.YYYY-MM-DD.log`，
使用实际执行时的 UTC 日期。工作失败会写入日志，后续计划工作继续运行。
没有变化的页面不会保存。复用数据时可能创建或更新 `cache/`。
预览运行会写入 `dry-run/aranami-*.md`，
并为每项拟提交的编辑创建一个链接到的 `.wikitext` 文件。
维基文本文件包含完整的拟提交页面文本。
如果其他工作失败，之前的预览和已完成的修改提案仍会保留。
PexBot 预览项说明拟进行的刷新；只有请求刷新后才能获取其生成的维基文本。

## 立即运行报告

`aranami.run_once(*, date=None, dry=False, tasks=None, clear_cache=False)`
会在调用时立即运行一轮，无论例行工作的计划时间为何，然后返回 `None`。
它不会启动监视器，也不会等待 cron 时间。

```python
import aranami

aranami.run_once(dry=True)  # 立即预览全部工作。
aranami.run_once(tasks="new_pages")  # 立即发布新页面的修改。
aranami.run_once(tasks=["dyks", "pageviews"], dry=True)  # 合并为一份预览。
```

省略 `tasks` 时运行全部六项工作。可用名称为 `dyks`、`new_pages`、
`assessment_lists`、`pageviews`、`enwp_key_articles` 和 `pexbot`。
一项工作失败后，其余选定的独立工作仍会继续；
剩余工作完成并写出合并预览后，失败会一起报告。
`clear_cache=True` 在开始这一轮前删除可重新生成的缓存；
请求清除缓存前，请先停止正在运行的监视器。

### 自定义一项例行工作

导入需要的工作模块并调用其 `run()`：

```python
from aranami.jobs import dyks, new_pages, pageviews

new_pages.run(dry=True)
# dyks.run()       # 仅发布 DYK 的修改。
# pageviews.run()  # 最多发布一个缺失日期的热门条目报告。
```

六项例行工作都返回 `None`，都接受关键字参数 `dry=False` 和 `context=None`。
单独调用时，每项工作会写入自己的每日运行日志，并在预览模式下生成自己的预览。
如果提供了上下文，则以该上下文的 `dry` 值为准，
调用者通过 `context.write_report()` 写出合并的预览。

## 例行工作与 UTC 运行计划

下列六项独立工作维护中文维基百科的电子游戏专题报告。
新启动的正式监视器立即检查全部工作，随后按各项 UTC 计划运行。
正式运行保存有变化的页面；`dry=True` 写入本地修改提案。
下方每个固定目标单独占一行，均位于 `WikiProject:电子游戏/` 下。

默认目标和 UTC cron 计划定义于
[src/aranami/monitor.py](src/aranami/monitor.py) 的 `TASK_DEFINITIONS`。
修改此文件后构建替换 wheel，即可更改这些默认值；工作模块使用同一组定义，
监视器据此生成 `SCHEDULES`。“运行安排”列表示触发时间，不代表执行时长，
也不受笔记本本地时区影响。`aranami.run_once()` 不受这些时间限制，立即执行。
每项工作最多同时执行一个实例，错过的多个触发时间合并为一次执行尝试。

| 例行脚本 | 更新页面 | [运行安排](src/aranami/monitor.py) | 说明 |
|---|---|---|---|
| [dyks.py](src/aranami/jobs/dyks.py) | [新条目推荐](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新条目推荐) | 每小时 `HH:00` | 刷新已入选条目、候选条目、评级和隐藏的年度统计。[（详细说明）](#dyk) |
| [new_pages.py](src/aranami/jobs/new_pages.py) | [新进条目/关键词筛选](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/新进条目/关键词筛选) | 每日 `00:07` | 列出截至 UTC 昨天符合关键词的新建页面及由重定向改写的页面；默认保留 100 个日期，并刷新全部保留记录中的评级图标。[（详细说明）](#新页面) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/Bplus](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/Bplus) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（详细说明）](#评级列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/BPAN](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/BPAN) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（详细说明）](#评级列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/A](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/A) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（详细说明）](#评级列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/AL](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/AL) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（详细说明）](#评级列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/ACC](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/ACC) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（详细说明）](#评级列表) |
| [assessment_lists.py](src/aranami/jobs/assessment_lists.py) | [认证条目/PPR](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/认证条目/PPR) | 每日 `01:31`、`07:31`、`13:31`、`19:31` | 更新列表。[（详细说明）](#评级列表) |
| [pageviews.py](src/aranami/jobs/pageviews.py) | [热门条目](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/热门条目) | 每小时 `HH:59` | UTC 18:00之后，尝试更新昨日访问量资料。每次最多补一个缺失日期，避免集中发出大量请求。[（详细说明）](#热门条目) |
| [enwp_key_articles.py](src/aranami/jobs/enwp_key_articles.py) | [数据库报告/英文维基百科重要条目](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科重要条目) | 每日 `23:24` | 刷新英文极高／高重要度条目、中文对应条目、评级和数量。[（详细说明）](#英文条目对照) |
| [enwp_key_articles.py](src/aranami/jobs/enwp_key_articles.py) | [数据库报告/英文维基百科优质条目](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告/英文维基百科优质条目) | 每日 `23:24` | 刷新英文 FA／FL／GA 条目、中文对应条目、评级和数量。[（详细说明）](#英文条目对照) |
| [pexbot.py](src/aranami/jobs/pexbot.py) | [数据库报告](https://zh.wikipedia.org/wiki/WikiProject:电子游戏/数据库报告) 下的订阅页面 | 每日 `00:00` | 请求 PexBot 刷新列出的报告页面。[（详细说明）](#pexbot) |

链接中的例行工作均在 `aranami.jobs` 下提供 `run(...)`。
下面分别说明各项工作的结果和目标设置。

### DYK

DYK 刷新已入选条目、候选条目、评级和隐藏的年度统计。
使用 `title` 可选择其他报告目标。`statistics_title` 默认为
`c:Data:Zhwiki_WikiProject_Video_Games_DYK_Annual_Statistics.tab`。
它为可复制的隐藏统计内容标注名称；例行工作不会保存该 Commons 页面。

### 新页面

新页面列表默认保留截至 UTC 昨天的 100 个日期。
一次调用会补齐保留范围内全部缺失日期，并复用已有每日记录。
每次调用都会刷新全部保留页面条目的评级图标，包括较早日期的条目。
使用 `title` 可选择其他目标。

每个新页面日期还会搜索该 UTC 日带有 `mw-removed-redirect` 标签的编辑。
符合关键词且目前为普通页面的结果会加入同一列表，在讨论链接后标注
` — 自重定向页改写`。同一页面在同一天只列出一次，即使当天也新建过，
或多次由重定向改写。此类页面沿用新建页面的命名空间和关键词规则。
已有每日记录继续复用；补齐缺失日期时会执行两种搜索。

每项末尾会附加 HTML 注释，例如
`<!-- 页面ID 12345 · 创建时间 2026-08-05 11:22:33 -->`。
创建时间取页面最早修订的 UTC 时间，由重定向改写的页面也采用这一规则。
已有记录会在元数据可用时补齐缺少的注释；已保存的注释会在后续更新中保留。
无法取得创建时间时标记为 `未知`。

### 评级列表

评级列表在重建时刷新条目、评级和设定的评审图标。
使用 `lists=[(title, AssessmentList(...)), ...]` 选择列表和目标页面。

### 热门条目

热门条目每次调用只推进一个缺失报告日期；若当天数据不可用，留待后续调用重试。
昨日观测数据在 UTC 18:00 后可尝试处理，每小时 `HH:59` 的定时计划首次在
UTC 18:59 尝试更新；此前仍可补齐较早的缺失日期。
计划工作允许延迟最多 3,500 秒开始执行。评级随新每日报告生成而刷新。
使用 `title` 选择其他目标，用 `settings=REPORT_SETTINGS` 选择专题成员和排名周期。

### 英文条目对照

两项英文报告刷新极高／高重要度条目或 FA／FL／GA 条目，
以及中文对应条目、评级和数量。评级在列表重建时刷新。
使用包含 `important` 和 `quality` 的 `targets` 选择报告目标。

### PexBot

PexBot 刷新订阅的报告页面，其生成内容由自身控制。
使用 `prefixes` 覆盖允许的页面根标题，或用 `prefixes=[]` 关闭刷新。
仅支持中文维基百科。

### 使用指定日期

将 `datetime.date` 作为 `date` 传给 `aranami.run_once()`，
可指定 UTC 基准日期；省略时使用当前 UTC 日期。选定工作仍在调用时立即执行。
对于新页面，它是不包含在内的截止日期：要包含 10 月 1 日，请使用 10 月 2 日。

```python
from datetime import date

import aranami

aranami.run_once(date=date(2026, 10, 2), dry=True, tasks="new_pages")
```

直接调用工作模块时，可通过 `JobContext.today` 设置相同基准。
自定义工作或将多项直接调用合并为一份预览时，可以使用上下文：

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

这会补齐保留范围内截至 `report_day` 的新页面记录，而不是只处理一天。
可以将同一上下文传给多项工作，将预览合并为一份。
基准日期控制截止日期和统计；它不会重建历史页面文本或历史专题成员名单。
评级列表、英文条目对照和 PexBot 始终使用当前来源，不受基准日期影响。
日志和预览的时间戳仍使用实际执行时间。

对于热门条目排名，报告日期必须早于基准日期（`date` 或 `context.today`），
并符合实际调用开始时的 UTC 就绪限制：
昨日数据到 UTC 18:00 才能尝试处理，此前仍可补较早的缺失日期。
指定基准日期不会绕过这一限制。进度日期只识别目标页面的
`<!-- report date YYYY-MM-DD -->` 注释。工作据此处理下一个缺失日期，
不会直接跳到指定日期。要生成某一天的准确排名而不修改页面，可以使用一次性报告生成工具：

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
    + timedelta(
        days=1
    ),  # 不包含在内的结束日期；report.data_date 为 10 月 1 日。
    settings=REPORT_SETTINGS,
)
Path("pageviews-2026-10-01.wikitext").write_text(report.text, encoding="utf-8")
```

`build_report()` 返回排名区段的文本和排名首位条目的相关信息，不会编辑维基页面，
但可能更新 `cache/`。明确调用 `write_text()` 才会在调用者的当前目录创建上述文件。
浏览量数据不完整或不可用时，会抛出 `PageviewsUnavailableError` 或 `PageviewsDeferredError`。

## 一次性工具

### 获取优质条目及其入选日期

`aranami.services.quality_contents.analyze_quality_contents(site, *,
most_recent=True)` 列出英文或中文电子游戏专题中当前的特色条目、特色列表和优良条目。
它返回标题、质量等级、重要度、入选日期及其来源、文本长度和修订版本 ID。
未知日期保持缺失；`most_recent=False` 会选择受支持的最早成功入选记录，而非最新记录。

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

# 这些导出操作明确在调用者的当前目录创建文件。
listing_dates.write_csv("quality-articles-zh.csv")
length_statistics(articles).write_csv("quality-lengths-zh.csv")
year_statistics(articles).write_csv("quality-listing-years-zh.csv")
```

使用 `pywikibot.Site("en", "wikipedia")` 可分析英文专题。
这些工具不会编辑维基页面或自动导出结果。中文词数统计可能在 `cache/words/` 下创建可删除的缓存文件。

| `aranami.services.quality_contents` 中的接口 | 返回结果 |
|---|---|
| `analyze_quality_contents(site, *, most_recent=True)` | 包含入选日期和测量结果的条目列表。 |
| `length_statistics(frame)` | 按质量等级统计数量、正文长度中位数，以及中央 68%/95% 区间。 |
| `year_statistics(frame)` | 按入选年份、质量等级和重要度统计条目数量；排除未确定日期的记录。 |

### 获取每日浏览量、查询数据和检查模板

这些接口返回或显示数据，不会编辑维基百科或自动导出结果文件。
浏览量的日期范围包含 `start`，不包含 `stop`；缺失观测与实际观测到的零值不同。

| 公开接口 | 功能和返回结果 |
|---|---|
| `aranami.sources.pageviews.fetch_data(site, page, start, stop)` | 返回一个页面的 `DailyPageviews(date, pageview)` 每日观测列表。 |
| `aranami.sources.pageviews.fetch_data_dataframe(site, page, start, stop)` | 返回包含 `page`、`date` 和 `pageview` 列的每日观测数据。 |
| `aranami.sources.pageviews.massive(site, pages, start, stop)` | 返回多个页面在相同日期范围内的每日观测数据。 |
| `aranami.sources.quarry.Replica(project, extension=None)` | 选择 Wiki Replica。`Replica.from_site(site, *, extension=None)` 选择所提供站点的副本；`Replica.wikidata_terms()` 选择 Wikidata 词条数据。 |
| `Replica.query(statement, *, parameters=None, schema_overrides=None)` | 返回 `QueryFrame`；`.pipe(postprocessor)` 添加数据表转换，`.collect()` 读取结果。连接属性包括 `hostname`、`database`、`url` 和 `engine`。 |
| `aranami.sources.quarry.tables` | 提供自定义查询使用的只读数据表定义；参见下方的映射目录。 |
| `aranami.support.gtshow(frame, *, first=5, last=5)` | 在 JupyterLab 中显示精简的数据表预览；返回 `None`，不创建文件。 |
| `aranami.support.get_templates(text, template, site)` | 从提供的维基文本中返回匹配且可编辑的模板节点。修改节点只会改变内存中的已解析文本。 |
| `aranami.support.open_run_log(directory=None)` | 打开日志上下文并提供日志记录器；在 `logs/` 或指定目录中追加每日 UTC 日志。 |

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
# traffic.write_csv("daily-traffic.csv")  # 可选择明确导出。
```

[Quarry 示例][7] 展示自定义条目查询。[Pageviews 示例][8] 将条目选择与浏览量收集合并使用。

## 配置和文件

报告替换标记 HTML 注释内的内容，并保留周围的页面内容。
起始注释中的设置控制保留期限、浏览量历史、缺失数据阈值或评级分类：

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...generated content...
<!-- aranami end="new-pages" -->
```

支持的标记和默认值见 [CONTRIBUTING.md][9]。
修改 [src/aranami/monitor.py](src/aranami/monitor.py) 中
`TASK_DEFINITIONS` 的 PexBot `prefixes`，然后构建替换 wheel，
即可选择允许的订阅根页面。
`aranami.jobs.pexbot.PEXBOT_PREFIXES` 保留为这些默认值的兼容别名。
将 `prefixes` 传给该例行工作可仅覆盖这次调用的范围；空的选择会关闭刷新。

除使用启动脚本的情况外，路径均相对于调用者的当前目录：

| 位置 | 写入来源和用途 |
|---|---|
| `logs/aranami.YYYY-MM-DD.log` | 所有例行工作和 `open_run_log()` 追加开始、完成、跳过和失败记录。保留最近 90 个已完成的 UTC 日期和当天的日志。 |
| `dry-run/aranami-*.md` 和链接到的 `.wikitext` 文件 | 全部或单项预览运行，或明确调用 `JobContext.write_report()`，会写出可审阅的摘要和准确的拟提交页面文本。 |
| `cache/` | 报告例行工作复用下载的数据，并保存本地条目标识快照，预览时也会保存。中文词数统计可能使用 `cache/words/`。没有工作正在执行时可以删除该目录；也可停止监视器后使用 `aranami.run_once(clear_cache=True)` 重新生成缓存。 |
| 调用者指定的文件名 | 一次性工具返回结果；由调用者明确调用 `write_csv()`、`write_parquet()` 或 `write_text()` 创建导出文件。 |

热门条目排名默认保留 800 天的浏览量历史。
[热门条目工作](src/aranami/jobs/pageviews.py) 定义 `DATA_READY_HOUR=18`：
实际调用在 UTC 18:00 或之后开始时，
才可尝试处理昨日数据。每小时 `HH:59` 的计划使首个正常尝试时间为 UTC 18:59。
18:00 前仍可补较早的缺失日期，每次调用最多推进一天。
正式运行、预览和手动调用工作均遵循这一规则；指定基准日期不会绕过截止限制。
已有的 `lag-days` 属性不再影响选定的报告日期。
如果目标日期至少有 95% 的条目缺少数据，发布会等待。
临时请求失败时会重试；之后的运行可以从已完成的工作继续，而不推进已发布日期。

## 其他公开接口参考

此目录按导入模块列出其余公开类和函数。上方的主要入口涵盖例行使用。
同一行中的名称具有所描述的共同用途；来源链接包含完整的参数约定。
除注明文件操作的接口外，这些接口返回值或修改提供的内存对象，
不会保存维基页面或导出文件。

### 执行和报告服务

| 模块 | 公开接口 | 结果或影响 |
|---|---|---|
| [`aranami.monitor`](src/aranami/monitor.py) | `TaskDefinition`、`TASK_DEFINITIONS`、`RoutineSchedule`、`SCHEDULES`、`start`、`clear_caches` | 在同一模块中定义默认目标页面、UTC cron 参数和 PexBot 订阅范围，生成运行计划，并启动或复用 `aranami.run()` 返回的监视器。`RoutineSchedule.trigger()` 返回相应的 UTC cron 触发器。`clear_caches()` 在监视器停止后删除可重新生成的缓存。 |
| [`aranami.runner`](src/aranami/runner.py) | `run`、`run_once` | 由 `aranami` 同时导出的定时及立即运行入口。 |
| [`aranami.jobs`](src/aranami/jobs/__init__.py) | `JobContext`, `ProposedEdit`, `job_run` | 共用运行上下文、编辑提案和例行工作的生命周期上下文。`JobContext.publish()` 在正式模式下保存有变化的页面，在预览模式下记录本地提案。`write_report()` 返回预览路径，正式运行时返回 `None`；`start_task()`、`finish_task()` 和 `defer()` 记录工作状态。`job_run()` 提供上下文并记录工作活动。 |
| [`aranami.services.quality_milestones`](src/aranami/services/quality_milestones.py) | `QualityTarget`, `QualityMilestone`, `QualityProfile`; `normalize_status_code`, `normalize_action_name`, `normalize_result`, `is_target_action`, `is_success_result`, `choose_milestone`, `extract_history_milestones`, `extract_quality_milestone_from_wikitext` | 描述并从提供的文本中提取受支持的成功入选记录；返回里程碑记录，无法确定时不返回结果。 |
| [`aranami.services.quality_profile`](src/aranami/services/quality_profile.py) | `MilestoneExtractor`, `QualityAnalysisProfile` | 描述某个维基的质量分析选项和日期提取接口。 |
| [`aranami.services.enwiki.quality_dates`](src/aranami/services/enwiki/quality_dates.py), [`aranami.services.zhwiki.quality_dates`](src/aranami/services/zhwiki/quality_dates.py) | `extract_milestone(text, status, *, site, most_recent=True, aliases=None)` | 返回依据该维基规则提取的 `QualityMilestone` 或 `None`。 |
| [`aranami.services.zhwiki.dyk_dates`](src/aranami/services/zhwiki/dyk_dates.py) | `extract_dates` | 从提供的讨论页文本中返回 DYK 日期，包括未知值。 |
| [`aranami.services.zhwiki.dyks`](src/aranami/services/zhwiki/dyks.py) | `prepare_report`, `update_text`, `render_reports`, `build_statistics`, `article_members` | 返回包含条目标识的报告文本、更新后的 DYK 页面文本、生成的区段、按日期的统计数量或成员记录。读取来源数据时，可能更新可删除的 DYK 缓存。 |
| [`aranami.services.zhwiki.new_pages`](src/aranami/services/zhwiki/new_pages.py) | `update_text`, `matches_keywords`, `update_icons`, `record_dates`, `record_counts` | 返回更新后的每日列表文本、关键词匹配结果、刷新后的图标、记录日期或条目/非条目数量。 |
| [`aranami.services.zhwiki.assessment_lists`](src/aranami/services/zhwiki/assessment_lists.py) | `ReviewInfo`, `AssessmentList`; `prepare_report`, `prepare_text`, `update_text`, `review_heading`, `article_members`, `article_titles` | 配置列表，并返回包含条目标识的报告文本、准备好的列表文本、评审锚点或成员记录。 |
| [`aranami.services.zhwiki.pageviews`](src/aranami/services/zhwiki/pageviews.py) | `ReportPeriod`, `TaskForce`, `ReportSettings`, `PageviewReport`; `current_data_date`, `report_periods`, `require_daily_observations`, `aggregate_views`, `build_report`, `update_text`; `PageviewsUnavailableError`, `PageviewsDeferredError` | 配置排名，并返回日期、日期范围、浏览量总计或报告文本。`build_report` 在 `cache/` 下写入可删除的已验证数据和待完成数据文件；只有提供缓存时，`aggregate_views` 才暂存数据。错误表示观测数据不可用或请求暂缓。 |
| [`aranami.services.zhwiki.enwp_key_articles`](src/aranami/services/zhwiki/enwp_key_articles.py) | `ReportSpec`, `OldArticle`, `ReportData`; `prepare_reports`, `build_reports`, `build_enriched_rows`, `filter_report_rows`, `count_report_rows`, `normalize_en_pages`; `build_report_item_template`, `render_item`, `render_body`, `update_page_text`; `parse_item_id`, `parse_old_articles`, `build_item_to_zh_title`, `format_summary_article`, `encode_length`, `truncate_summary_parts`, `build_edit_summary`; `english_sort_key`, `heading_key`, `safe_text`, `escape_template_value`, `replace_marker_value`, `replace_body` | 准备英中文条目对照、筛选和统计数据行、生成报告文本、读取原有成员名单，并格式化摘要或标量文本。返回值，不进行发布。 |
| [`aranami.services.zhwiki.pexbot`](src/aranami/services/zhwiki/pexbot.py) | `StreamEvent`; `require_zhwiki`, `subscribed_titles`, `parse_event` | 验证站点、返回限定范围的订阅标题，或解析进度消息。这些辅助接口不会请求刷新。 |

### 只读数据源

| 模块 | 公开接口 | 返回结果 |
|---|---|---|
| [`aranami.sources.wiki`](src/aranami/sources/wiki.py) | `read_pages`, `read_page_ids`, `template_aliases` | 预加载的当前页面或模板标题及别名。 |
| [`aranami.sources.dyk`](src/aranami/sources/dyk.py) | `TalkPage`, `read_talk_pages` | 讨论页文本及其实际修订版本 ID。 |
| [`aranami.sources.quarry.projects`](src/aranami/sources/quarry/projects.py) | `TagSpec`; `query_pages_by_wikiproject`, `category_members`, `latest_revisions`, `new_page_ids`, `redirect_converted_page_ids`, `page_creation_metadata`, `query_tags` | 专题评级、分类成员、修订版本 ID、新建或由重定向改写的页面 ID、页面标识及最早修订的 UTC 时间，或附加的维护标签。 |
| [`aranami.sources.quarry.quality`](src/aranami/sources/quarry/quality.py) | `fetch_quality_articles(site, *, project_title, classes)` | 选定的已评级条目及其质量等级、重要度和显示/排序标题。入选日期由 `analyze_quality_contents` 提供，不由此元数据查询提供。 |
| [`aranami.sources.quarry.enwp`](src/aranami/sources/quarry/enwp.py) | `fetch_en_key_pages`, `fetch_wikidata_sitelinks`, `fetch_wikidata_labels`, `fetch_zh_page_states` | 英文重要条目、优先选择的对应标题/标签或中文条目状态。 |

`aranami.sources` 导出 `pageviews` 和 `quarry` 模块。
`QueryFrame` 也可以从 `aranami.sources.quarry` 导入；
`DailyPageviews` 可以从 `aranami.sources.pageviews` 导入。

<details>
<summary>aranami.sources.quarry.tables 中的公开名称</summary>

这些类描述只读查询字段；自身不会获取数据、修改维基页面或创建文件。
`Base` 保存数据表元数据。
`StringDecoder`、`TextDecoder`、`EnumDecoder` 和 `BinaryDecoder` 规范化查询值；
其 `process_bind_param` 和 `process_result_value` 回调返回转换后的值。

数据表映射如下：

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
`WbtTermInLang`, `WbtText`, `WbtTextInLang`。

字段说明见[数据表定义](src/aranami/sources/quarry/tables.py)。

</details>

### 文本、日志和缓存辅助接口

| 模块 | 公开接口 | 结果或文件操作 |
|---|---|---|
| [`aranami.support.cache`](src/aranami/support/cache.py) | `clear_runtime_cache` | 删除调用者的 `cache/` 目录。 |
| [`aranami.support.dates`](src/aranami/support/dates.py) | `parse_date`, `parse_complete_date` | 返回解析后的日期或 `None`；后者要求完整的日历日期。 |
| [`aranami.support.templates`](src/aranami/support/templates.py) | `template_page`, `normalize_param_name`, `template_get_raw_param`, `template_get_param`, `normalize_oldid` | 返回模板标识、可编辑或清理后的参数值、规范化名称或修订版本 ID。 |
| [`aranami.support.wikitext`](src/aranami/support/wikitext.py) | `clean_value`, `get_templates`；重新导出下方的区段函数 | 返回清理后的标量文本或匹配且可编辑的模板。 |
| [`aranami.support.regions`](src/aranami/support/regions.py) | `has_region`, `region_options`, `region_content`, `integer_option`, `managed_region`, `replace_by_tag` | 检查命名的注释范围和设置，或返回包裹/替换后的文本。 |
| [`aranami.support.prose`](src/aranami/support/prose.py) | `ProseProfile`, `extract_prose` | 配置提取规则，并返回可读的条目正文。 |
| [`aranami.support.words`](src/aranami/support/words.py) | `count_words` | 返回英文单词数或中文分词数；中文统计可能创建 `cache/words/` 文件。 |
| [`aranami.support.edit_summary`](src/aranami/support/edit_summary.py) | `EditSummary`; `append`, `add_group`, `daily`, `membership`, `render`, `with_execution_time` | 构建编辑摘要字符串，可包含附加说明、成员变化和耗时。 |
| [`aranami.support.report_membership`](src/aranami/support/report_membership.py) | `MembershipReport`, `load_membership`, `save_membership`, `member_identifier`, `membership_changes` | 返回包含条目标识的报告文本、缓存的标识、旧数据行 ID 或新增/移除的标题列表。`save_membership` 写入 `cache/report-membership-<hash>-v1.parquet`。 |
| [`aranami.support.dyk_cache`](src/aranami/support/dyk_cache.py) | `load`, `save` | 读写可删除的 `cache/vg_dyks_talk_pages-v2.parquet`。 |
| [`aranami.support.pageviews_cache`](src/aranami/support/pageviews_cache.py) | `PageviewsCache`; `load`, `get`, `replace`, `save_pending`, `discard_pending`, `save` | 读取或暂存每日历史数据；保存/丢弃方法会写入或移除 `cache/pageviews-<hash>-v1.parquet` 及带日期的待完成文件。 |

`aranami.support` 重新导出 `get_templates`、`gtshow` 和 `open_run_log`。
后两者也可以从同名的辅助模块导入。

## 开发

Aranami 需要 Python 3.12 或更新版本，并使用 [`uv`][2]：

```shell
uv sync
uv run python -m unittest discover -s tests/unit -v
uv run python -m unittest discover -s tests/integration -v
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv build --wheel --clear
```

此仓库用于本地开发、测试和构建 wheel。
仓库规则见 [AGENTS.md][4] 和 [CONTRIBUTING.md][9]。
每次明确构建前，都要递增 wheel 的测试构建后缀并重新生成锁文件。
已完成的修改记录在 [CHANGELOG.md][5] 中。

## 许可证

Aranami 以 [CC0 1.0 Universal][6] 发布。

[1]: https://wikitech.wikimedia.org/wiki/PAWS
[2]: https://docs.astral.sh/uv/
[3]: https://wikitech.wikimedia.org/wiki/PAWS/Python_with_Pip
[4]: AGENTS.md
[5]: CHANGELOG.md
[6]: LICENSE
[7]: tests/live/quarry_paws.py
[8]: tests/live/pageviews_paws.py
[9]: CONTRIBUTING.md
