<?php
/** Negotiate browser languages and translate the Toolforge web interface. */
declare(strict_types=1);

/** Return complete interface messages; article and source titles remain data. */
function uiCatalog(): array
{
    static $messages = [
        'page.title' => ['en' => 'Video games dashboard · Aranami', 'zh-hans' => '电子游戏专题看板 · Aranami', 'zh-hant' => '電子遊戲專題看板 · Aranami'],
        'page.description' => ['en' => 'Current recognized video-game content across Wikipedia languages and Chinese Wikipedia main-page appearances over the last 100 days.', 'zh-hans' => '电子游戏专题看板：各语言维基百科现有的优质内容，以及中文维基百科首页最近100天的展示记录。', 'zh-hant' => '電子遊戲專題看板：各語言維基百科現有的優質內容，以及中文維基百科首頁最近100天的展示記錄。'],
        'page.heading' => ['en' => 'Video games dashboard', 'zh-hans' => '电子游戏专题看板', 'zh-hant' => '電子遊戲專題看板'],
        'brand.context' => ['en' => 'Wikipedia · Video games', 'zh-hans' => '维基百科 · 电子游戏', 'zh-hant' => '維基百科 · 電子遊戲'],
        'nav.skip' => ['en' => 'Skip to the dashboard', 'zh-hans' => '跳至专题看板', 'zh-hant' => '跳至專題看板'],
        'nav.backlog' => ['en' => 'Maintenance', 'zh-hans' => '条目维护', 'zh-hant' => '條目維護'],
        'nav.project' => ['en' => 'Join the project', 'zh-hans' => '参与专题', 'zh-hant' => '參與專題'],
        'nav.logs' => ['en' => 'Run logs', 'zh-hans' => '运行日志', 'zh-hant' => '執行日誌'],
        'empty.section' => ['en' => 'This section is temporarily unavailable.', 'zh-hans' => '这一栏的数据暂时无法获取。', 'zh-hant' => '這一欄的資料暫時無法取得。'],
        'empty.period' => ['en' => 'No appearances are recorded for this period.', 'zh-hans' => '这段期间没有已记录的展示。', 'zh-hant' => '這段期間沒有已記錄的展示。'],
        'records.more' => ['en' => 'Show remaining records ({count})', 'zh-hans' => '查看其余{count}条记录', 'zh-hant' => '查看其餘{count}筆記錄'],
        'date.window_start' => ['en' => 'Already displayed at the start of this period', 'zh-hans' => '期初已在首页展示', 'zh-hant' => '期初已在首頁展示'],
        'date.observed' => ['en' => 'Observation date', 'zh-hans' => '观察日期', 'zh-hant' => '觀察日期'],
        'date.scheduled' => ['en' => 'Scheduled', 'zh-hans' => '预定', 'zh-hant' => '預定'],
        'badge.FA' => ['en' => 'Featured article', 'zh-hans' => '典范条目', 'zh-hant' => '典範條目'],
        'badge.FL' => ['en' => 'FL · Featured list', 'zh-hans' => '特色列表', 'zh-hant' => '特色列表'],
        'badge.GA' => ['en' => 'Good article', 'zh-hans' => '优良条目', 'zh-hant' => '優良條目'],
        'badge.recommended' => ['en' => 'Recommended article', 'zh-hans' => '推荐', 'zh-hant' => '推薦'],
        'badge.featured' => ['en' => 'Featured content', 'zh-hans' => '典范内容', 'zh-hant' => '典範內容'],
        'assessment.class.FA' => ['en' => 'Featured article', 'zh-hans' => '典范', 'zh-hant' => '典範'],
        'assessment.class.FL' => ['en' => 'Featured list', 'zh-hans' => '特色列表', 'zh-hant' => '特色列表'],
        'assessment.class.GA' => ['en' => 'Good article', 'zh-hans' => '优良', 'zh-hant' => '優良'],
        'assessment.class.A' => ['en' => 'A-Class', 'zh-hans' => '甲', 'zh-hant' => '甲'],
        'assessment.class.AL' => ['en' => 'A-Class list', 'zh-hans' => '甲级列表', 'zh-hant' => '甲級列表'],
        'assessment.class.Bplus' => ['en' => 'Bplus-Class', 'zh-hans' => '乙上', 'zh-hant' => '乙上'],
        'assessment.class.B' => ['en' => 'B-Class', 'zh-hans' => '乙', 'zh-hant' => '乙'],
        'assessment.class.BL' => ['en' => 'B-Class list', 'zh-hans' => '乙级列表', 'zh-hant' => '乙級列表'],
        'assessment.class.C' => ['en' => 'C-Class', 'zh-hans' => '丙', 'zh-hant' => '丙'],
        'assessment.class.CL' => ['en' => 'C-Class list', 'zh-hans' => '丙级列表', 'zh-hant' => '丙級列表'],
        'assessment.class.D' => ['en' => 'D-Class', 'zh-hans' => '丁', 'zh-hant' => '丁'],
        'assessment.class.Start' => ['en' => 'Start-Class', 'zh-hans' => '初', 'zh-hant' => '初'],
        'assessment.class.SL' => ['en' => 'Stub-Class list', 'zh-hans' => '小列表', 'zh-hant' => '小列表'],
        'assessment.class.Stub' => ['en' => 'Stub-Class', 'zh-hans' => '小作品', 'zh-hant' => '小作品'],
        'assessment.class.List' => ['en' => 'List-Class', 'zh-hans' => '列表', 'zh-hant' => '列表'],
        'assessment.class.Substub' => ['en' => 'Substub-Class', 'zh-hans' => '小小作品', 'zh-hant' => '小小作品'],
        'assessment.class.Disambig' => ['en' => 'Disambiguation', 'zh-hans' => '消歧义', 'zh-hant' => '消歧義'],
        'assessment.class.Unassessed' => ['en' => 'Unassessed', 'zh-hans' => '未评级', 'zh-hant' => '未評級'],
        'assessment.importance.Top' => ['en' => 'Top', 'zh-hans' => '极高', 'zh-hant' => '極高'],
        'assessment.importance.High' => ['en' => 'High', 'zh-hans' => '高', 'zh-hant' => '高'],
        'assessment.importance.Mid' => ['en' => 'Mid', 'zh-hans' => '中', 'zh-hant' => '中'],
        'assessment.importance.Low' => ['en' => 'Low', 'zh-hans' => '低', 'zh-hant' => '低'],
        'assessment.importance.Bottom' => ['en' => 'Bottom', 'zh-hans' => '极低', 'zh-hant' => '極低'],
        'assessment.importance.NA' => ['en' => 'Not applicable', 'zh-hans' => '不适用', 'zh-hant' => '不適用'],
        'assessment.importance.Unknown' => ['en' => 'Unassessed', 'zh-hans' => '未评级', 'zh-hant' => '未評級'],
        'snapshot.none' => ['en' => 'No data snapshot is available yet', 'zh-hans' => '尚未获取数据快照', 'zh-hant' => '尚未取得資料快照'],
        'snapshot.updated' => ['en' => 'Data updated:', 'zh-hans' => '数据更新：', 'zh-hant' => '資料更新：'],
        'notice.preparing' => ['en' => 'Project data is being prepared. Please check again later.', 'zh-hans' => '专题数据准备中，请稍后再查看。', 'zh-hant' => '專題資料準備中，請稍後再查看。'],
        'notice.partial' => ['en' => 'Some sources are temporarily unavailable. Available records remain visible below.', 'zh-hans' => '部分来源暂时无法获取；可用记录仍列于下方。', 'zh-hant' => '部分來源暫時無法取得；可用記錄仍列於下方。'],
        'quality.heading' => ['en' => 'Across the wikis', 'zh-hans' => '他山之石', 'zh-hant' => '他山之石'],
        'quality.unavailable' => ['en' => 'Recognition data across languages is temporarily unavailable.', 'zh-hans' => '跨语言认证数据暂时无法获取。', 'zh-hant' => '跨語言認證資料暫時無法取得。'],
        'quality.cached' => ['en' => 'Showing cached data.', 'zh-hans' => '当前显示缓存数据。', 'zh-hant' => '目前顯示快取資料。'],
        'quality.empty' => ['en' => 'No recognized content is currently recorded.', 'zh-hans' => '目前没有已记录的认证内容。', 'zh-hant' => '目前沒有已記錄的認證內容。'],
        'quality.language' => ['en' => 'Project language', 'zh-hans' => '计划语言', 'zh-hant' => '計畫語言'],
        'quality.filter' => ['en' => 'Filter', 'zh-hans' => '筛选', 'zh-hant' => '篩選'],
        'quality.all_languages' => ['en' => 'All languages', 'zh-hans' => '所有语言', 'zh-hant' => '所有語言'],
        'quality.show_all' => ['en' => 'Display all', 'zh-hans' => '显示全部', 'zh-hant' => '顯示全部'],
        'quality.local_any' => ['en' => 'Any', 'zh-hans' => '不限', 'zh-hant' => '不限'],
        'quality.local_exists' => ['en' => 'Exists', 'zh-hans' => '已存在', 'zh-hant' => '已存在'],
        'quality.local_missing' => ['en' => 'Missing', 'zh-hans' => '未存在', 'zh-hant' => '未存在'],
        'quality.all_aria' => ['en' => 'Recognized articles across all languages', 'zh-hans' => '各语言现有认证条目', 'zh-hant' => '各語言目前認證條目'],
        'quality.sample' => ['en' => '{language} · {count} recognition records', 'zh-hans' => '{language} · {count}条认证记录', 'zh-hant' => '{language} · {count}筆認證記錄'],
        'quality.sample_status' => ['en' => '{language} · {status} · {count} recognition records', 'zh-hans' => '{language} · {status} · {count}条认证记录', 'zh-hant' => '{language} · {status} · {count}筆認證記錄'],
        'quality.progress' => ['en' => 'Showing {shown} of {total} records', 'zh-hans' => '已显示{shown}条，共{total}条', 'zh-hant' => '已顯示{shown}筆，共{total}筆'],
        'quality.show_more' => ['en' => 'Show {count} more', 'zh-hans' => '再显示{count}条', 'zh-hant' => '再顯示{count}筆'],
        'quality.sample_empty' => ['en' => 'No articles are recorded for this selection.', 'zh-hans' => '所选条件没有已记录的条目。', 'zh-hant' => '所選條件沒有已記錄的條目。'],
        'quality.table_aria' => ['en' => 'Recognized content: {language}', 'zh-hans' => '{language}认证内容', 'zh-hant' => '{language}認證內容'],
        'column.article' => ['en' => 'Article', 'zh-hans' => '条目', 'zh-hant' => '條目'],
        'column.language' => ['en' => 'Language', 'zh-hans' => '语言', 'zh-hant' => '語言'],
        'column.local_article' => ['en' => 'Local article', 'zh-hans' => '本地条目', 'zh-hant' => '本地條目'],
        'column.wikidata_item' => ['en' => 'Wikidata item', 'zh-hans' => '维基数据项', 'zh-hant' => '維基資料項'],
        'quality.language_label' => ['en' => '{language} ({code})', 'zh-hans' => '{language}（{code}）', 'zh-hant' => '{language}（{code}）'],
        'quality.companion_missing' => ['en' => 'No Chinese article linked', 'zh-hans' => '未链接中文条目', 'zh-hant' => '未連結中文條目'],
        'quality.observed' => ['en' => 'Updated:', 'zh-hans' => '更新时间：', 'zh-hant' => '更新時間：'],
        'homepage.heading' => ['en' => 'Current highlights', 'zh-hans' => '动态热门', 'zh-hant' => '動態熱門'],
        'pageviews.heading' => ['en' => 'Popular articles', 'zh-hans' => '热门条目', 'zh-hant' => '熱門條目'],
        'pageviews.description' => ['en' => 'Pages in the site-wide daily {top}', 'zh-hans' => '全站日访问量{top}的页面', 'zh-hant' => '全站日瀏覽量{top}的頁面'],
        'pageviews.top' => ['en' => 'top 1,000', 'zh-hans' => '前1,000名', 'zh-hant' => '前1,000名'],
        'pageviews.views' => ['en' => '{count} views', 'zh-hans' => '{count}次浏览', 'zh-hant' => '{count}次瀏覽'],
        'pageviews.site_rank' => ['en' => 'Sitewide rank {rank}', 'zh-hans' => '全站{rank}名', 'zh-hant' => '全站{rank}名'],
        'pageviews.unavailable' => ['en' => 'Daily pageview data is temporarily unavailable.', 'zh-hans' => '每日浏览量数据暂时无法获取。', 'zh-hant' => '每日瀏覽量資料暫時無法取得。'],
        'pageviews.empty' => ['en' => 'No video-game project articles appeared in this day’s site top 1,000.', 'zh-hans' => '这天全站前1,000名中没有电子游戏专题条目。', 'zh-hant' => '這天全站前1,000名中沒有電子遊戲專題條目。'],
        'homepage.recognized' => ['en' => 'Quality content', 'zh-hans' => '优质内容', 'zh-hant' => '優質內容'],
        'homepage.recognized_description' => ['en' => 'Featured and good content recently shown on the main page', 'zh-hans' => '近期登上首页展示的典范、优良内容', 'zh-hant' => '近期登上首頁展示的典範、優良內容'],
        'homepage.dyk' => ['en' => 'New article highlights', 'zh-hans' => '新篇推荐', 'zh-hant' => '新篇推薦'],
        'homepage.dyk_description' => ['en' => '“Did you know?” (DYK) on the Chinese main page', 'zh-hans' => '中文首页的“你知道吗？”（DYK）', 'zh-hant' => '中文首頁的「你知道嗎？」（DYK）'],
        'homepage.news' => ['en' => 'In the news', 'zh-hans' => '新闻动态', 'zh-hant' => '新聞動態'],
        'homepage.history' => ['en' => 'On this day', 'zh-hans' => '历史上的今天', 'zh-hant' => '歷史上的今天'],
        'homepage.news_history' => ['en' => 'News and history', 'zh-hans' => '旧事新闻', 'zh-hant' => '舊事新聞'],
        'homepage.news_history_description' => ['en' => 'On this day · In the news', 'zh-hans' => '历史上的今天·新闻动态', 'zh-hant' => '歷史上的今天·新聞動態'],
        'homepage.partial' => ['en' => 'Source temporarily unavailable: {sections}.', 'zh-hans' => '{sections}的数据暂时无法获取。', 'zh-hant' => '{sections}的資料暫時無法取得。'],
        'backlog.heading' => ['en' => 'Article maintenance', 'zh-hans' => '条目维护', 'zh-hant' => '條目維護'],
        'backlog.count' => ['en' => '{count} articles', 'zh-hans' => '{count}篇', 'zh-hant' => '{count}篇'],
        'backlog.empty' => ['en' => 'No articles currently match this task.', 'zh-hans' => '目前没有这类待维护条目。', 'zh-hant' => '目前沒有這類待維護條目。'],
        'backlog.unavailable' => ['en' => 'The maintenance lists are temporarily unavailable.', 'zh-hans' => '维护列表暂时无法获取。', 'zh-hant' => '維護列表暫時無法取得。'],
        'backlog.source' => ['en' => 'Maintenance tag', 'zh-hans' => '维护标签', 'zh-hant' => '維護標籤'],
        'backlog.column.assessment' => ['en' => 'Assessment', 'zh-hans' => '评级·重要度', 'zh-hant' => '評級·重要度'],
        'backlog.column.class' => ['en' => 'Class', 'zh-hans' => '评级', 'zh-hant' => '評級'],
        'backlog.column.importance' => ['en' => 'Importance', 'zh-hans' => '重要度', 'zh-hant' => '重要度'],
        'backlog.column.issues' => ['en' => 'Maintenance issues', 'zh-hans' => '维护问题', 'zh-hant' => '維護問題'],
        'backlog.column.oldest' => ['en' => 'Earliest tagged month', 'zh-hans' => '最早标记月份', 'zh-hant' => '最早標記月份'],
        'backlog.sort' => ['en' => 'Sort maintenance articles', 'zh-hans' => '维护条目排序', 'zh-hant' => '維護條目排序'],
        'backlog.oldest_note' => ['en' => 'Months come from the earliest dated maintenance category currently present, rather than when the issue began. A dash means no month is recorded.', 'zh-hans' => '月份来自现存最早的维护分类，不代表问题实际出现时间；— 表示没有可确认的月份。', 'zh-hant' => '月份來自目前最早的維護分類，不代表問題實際出現時間；— 表示沒有可確認的月份。'],
        'backlog.back' => ['en' => 'Back to maintenance', 'zh-hans' => '返回条目维护', 'zh-hant' => '返回條目維護'],
        'backlog.page' => ['en' => 'Page {page} of {pages}', 'zh-hans' => '第{page}页，共{pages}页', 'zh-hant' => '第{page}頁，共{pages}頁'],
        'backlog.previous' => ['en' => 'Previous page', 'zh-hans' => '上一页', 'zh-hant' => '上一頁'],
        'backlog.next' => ['en' => 'Next page', 'zh-hans' => '下一页', 'zh-hant' => '下一頁'],
        'backlog.not_found' => ['en' => 'This maintenance list or page could not be found.', 'zh-hans' => '未找到这个维护列表或分页。', 'zh-hant' => '未找到這個維護列表或分頁。'],
        'backlog.note' => ['en' => 'Articles may appear under several tasks. Lists follow current maintenance tags, including dated categories.', 'zh-hans' => '同一条目可出现在多个列表；以现有维护标记为准，包含按月份归类的条目。', 'zh-hant' => '同一條目可出現在多個列表；以目前維護標記為準，包含按月份歸類的條目。'],
        'ops.heading' => ['en' => 'On-wiki report updates', 'zh-hans' => '站内报告更新状态', 'zh-hant' => '站內報告更新狀態'],
        'ops.unavailable' => ['en' => 'Unavailable', 'zh-hans' => '未获取', 'zh-hant' => '未取得'],
        'ops.installed' => ['en' => 'Installed version:', 'zh-hans' => '已安装版本：', 'zh-hant' => '已安裝版本：'],
        'ops.browse_logs' => ['en' => 'Detailed Aranami logs', 'zh-hans' => 'Aranami详细日志', 'zh-hant' => 'Aranami詳細日誌'],
        'ops.aria' => ['en' => 'Aranami task activity', 'zh-hans' => 'Aranami工作状态表', 'zh-hant' => 'Aranami工作狀態表'],
        'ops.column_task' => ['en' => 'Job', 'zh-hans' => '作业', 'zh-hant' => '作業'],
        'ops.column_state' => ['en' => 'Current status', 'zh-hans' => '当前状态', 'zh-hant' => '當前狀態'],
        'ops.column_duration' => ['en' => 'Execution time', 'zh-hans' => '执行时间', 'zh-hant' => '執行時間'],
        'ops.column_time' => ['en' => 'Status updated', 'zh-hans' => '状态更新时间', 'zh-hant' => '狀態更新時間'],
        'ops.no_record' => ['en' => 'No record', 'zh-hans' => '没有记录', 'zh-hant' => '沒有紀錄'],
        'routine.dyks' => ['en' => 'Did-you-know report', 'zh-hans' => '新条目推荐', 'zh-hant' => '新條目推薦'],
        'routine.new_pages' => ['en' => 'New-page report', 'zh-hans' => '新进条目', 'zh-hant' => '新進條目'],
        'routine.assessment_lists' => ['en' => 'Article assessments', 'zh-hans' => '条目评级', 'zh-hant' => '條目評級'],
        'routine.pageviews' => ['en' => 'Pageview rankings', 'zh-hans' => '浏览量排行', 'zh-hant' => '瀏覽量排行'],
        'routine.enwp_key_articles' => ['en' => 'English article comparisons', 'zh-hans' => '英文维基比较', 'zh-hant' => '英文維基比較'],
        'routine.pexbot' => ['en' => 'Database reports', 'zh-hans' => '数据库报告', 'zh-hant' => '資料庫報告'],
        'state.Updated' => ['en' => 'Updated', 'zh-hans' => '已更新', 'zh-hant' => '已更新'],
        'state.NoUpdate' => ['en' => 'No update needed', 'zh-hans' => '无需更新', 'zh-hant' => '無需更新'],
        'state.Result unavailable' => ['en' => 'Result unavailable', 'zh-hans' => '结果未明', 'zh-hant' => '結果未明'],
        'state.Failed' => ['en' => 'Failed', 'zh-hans' => '失败', 'zh-hant' => '失敗'],
        'state.Deferred' => ['en' => 'Not completed yet', 'zh-hans' => '暂未完成', 'zh-hant' => '暫未完成'],
        'state.Running' => ['en' => 'Running', 'zh-hans' => '运行中', 'zh-hant' => '執行中'],
        'section.featured' => ['en' => 'Featured content', 'zh-hans' => '典范内容', 'zh-hant' => '典範內容'],
        'section.good' => ['en' => 'Good articles', 'zh-hans' => '优良条目', 'zh-hant' => '優良條目'],
        'footer.title' => ['en' => 'Aranami · Video games dashboard', 'zh-hans' => 'Aranami · 电子游戏专题看板', 'zh-hant' => 'Aranami · 電子遊戲專題看板'],
        'format.list_separator' => ['en' => ', ', 'zh-hans' => '、', 'zh-hant' => '、'],
        'duration.seconds' => ['en' => '{seconds} sec', 'zh-hans' => '{seconds}秒', 'zh-hant' => '{seconds}秒'],
        'duration.minutes' => ['en' => '{minutes} min {seconds} sec', 'zh-hans' => '{minutes}分{seconds}秒', 'zh-hant' => '{minutes}分{seconds}秒'],
        'http.method_not_allowed' => ['en' => 'Method not allowed.', 'zh-hans' => '不允许此请求方法。', 'zh-hant' => '不允許此請求方法。'],
        'logs.not_found' => ['en' => 'Log not found.', 'zh-hans' => '未找到日志。', 'zh-hant' => '未找到日誌。'],
        'logs.page_title' => ['en' => 'Detailed Aranami logs', 'zh-hans' => 'Aranami详细日志', 'zh-hant' => 'Aranami詳細日誌'],
        'logs.heading' => ['en' => 'Detailed Aranami logs', 'zh-hans' => 'Aranami详细日志', 'zh-hant' => 'Aranami詳細日誌'],
        'logs.project_page_title' => ['en' => 'Aranami run logs', 'zh-hans' => 'Aranami运行日志', 'zh-hant' => 'Aranami執行日誌'],
        'logs.project_heading' => ['en' => 'Run logs', 'zh-hans' => '运行日志', 'zh-hant' => '執行日誌'],
        'logs.project_intro' => ['en' => 'Major events from Aranami report runs and the dashboard background updater, grouped by UTC date.', 'zh-hans' => '汇总Aranami报告运行与看板后台更新的重要事件，按UTC日期分组。', 'zh-hant' => '彙整Aranami報告執行與看板背景更新的重要事件，依UTC日期分組。'],
        'logs.detailed_link' => ['en' => 'Detailed Aranami logs', 'zh-hans' => 'Aranami详细日志', 'zh-hant' => 'Aranami詳細日誌'],
        'logs.project_link' => ['en' => 'Run logs', 'zh-hans' => '运行日志', 'zh-hant' => '執行日誌'],
        'logs.project_empty' => ['en' => 'No daily run logs are available yet. They appear when a report run or dashboard update records its first event.', 'zh-hans' => '尚无每日运行日志；报告运行或看板更新记录首个事件后便会出现。', 'zh-hant' => '尚無每日執行日誌；報告執行或看板更新記錄首個事件後便會出現。'],
        'logs.project_footer' => ['en' => 'Run logs update during report runs and dashboard refreshes. Reload a daily log to see new entries.', 'zh-hans' => '运行日志随报告运行与看板更新而写入。重新载入每日日志可查看新记录。', 'zh-hant' => '執行日誌隨報告執行與看板更新而寫入。重新載入每日日誌可查看新紀錄。'],
        'logs.back' => ['en' => 'Back to the dashboard', 'zh-hans' => '返回专题看板', 'zh-hant' => '返回專題看板'],
        'logs.intro_before' => ['en' => 'Aranami updates on-wiki reports, primarily for', 'zh-hans' => 'Aranami主要为', 'zh-hant' => 'Aranami主要為'],
        'logs.project' => ['en' => 'WikiProject Video games on Chinese Wikipedia', 'zh-hans' => '中文维基百科电子游戏专题', 'zh-hant' => '中文維基百科電子遊戲專題'],
        'logs.intro_after' => ['en' => '.', 'zh-hans' => '更新站内报告。', 'zh-hant' => '更新站內報告。'],
        'logs.empty' => ['en' => 'No daily logs are available yet. Refresh after Aranami writes its first log.', 'zh-hans' => '尚无每日日志。Aranami写入第一份日志后，可刷新此页面查看。', 'zh-hant' => '尚無每日日誌。Aranami寫入第一份日誌後，可重新整理此頁面查看。'],
        'logs.column_date' => ['en' => 'Date (UTC)', 'zh-hans' => '日期（UTC）', 'zh-hant' => '日期（UTC）'],
        'logs.column_file' => ['en' => 'Log file', 'zh-hans' => '日志文件', 'zh-hant' => '日誌檔案'],
        'logs.column_modified' => ['en' => 'Last modified (UTC)', 'zh-hans' => '最后修改时间（UTC）', 'zh-hant' => '最後修改時間（UTC）'],
        'logs.column_size' => ['en' => 'Size', 'zh-hans' => '大小', 'zh-hant' => '大小'],
        'logs.bytes' => ['en' => '{count} bytes', 'zh-hans' => '{count}字节', 'zh-hant' => '{count}位元組'],
        'logs.footer' => ['en' => 'Logs update as Aranami runs. Reload a log to see new entries.', 'zh-hans' => '日志随Aranami运行而更新。重新加载日志可查看新记录。', 'zh-hant' => '日誌隨Aranami運行而更新。重新載入日誌可查看新紀錄。'],
        'language.ar' => ['en' => 'Arabic', 'zh-hans' => '阿拉伯语', 'zh-hant' => '阿拉伯語'],
        'language.ast' => ['en' => 'Asturian', 'zh-hans' => '阿斯图里亚斯语', 'zh-hant' => '阿斯圖里亞文'],
        'language.az' => ['en' => 'Azerbaijani', 'zh-hans' => '阿塞拜疆语', 'zh-hant' => '亞塞拜然文'],
        'language.be' => ['en' => 'Belarusian', 'zh-hans' => '白俄罗斯语', 'zh-hant' => '白俄羅斯語'],
        'language.bg' => ['en' => 'Bulgarian', 'zh-hans' => '保加利亚语', 'zh-hant' => '保加利亞語'],
        'language.bs' => ['en' => 'Bosnian', 'zh-hans' => '波斯尼亚语', 'zh-hant' => '波士尼亞文'],
        'language.ca' => ['en' => 'Catalan', 'zh-hans' => '加泰罗尼亚语', 'zh-hant' => '加泰隆尼亞語'],
        'language.ckb' => ['en' => 'Central Kurdish', 'zh-hans' => '中库尔德语', 'zh-hant' => '中庫德文'],
        'language.cs' => ['en' => 'Czech', 'zh-hans' => '捷克语', 'zh-hant' => '捷克語'],
        'language.da' => ['en' => 'Danish', 'zh-hans' => '丹麦语', 'zh-hant' => '丹麥語'],
        'language.de' => ['en' => 'German', 'zh-hans' => '德语', 'zh-hant' => '德語'],
        'language.el' => ['en' => 'Greek', 'zh-hans' => '希腊语', 'zh-hant' => '希臘語'],
        'language.en' => ['en' => 'English', 'zh-hans' => '英语', 'zh-hant' => '英語'],
        'language.eo' => ['en' => 'Esperanto', 'zh-hans' => '世界语', 'zh-hant' => '世界語'],
        'language.es' => ['en' => 'Spanish', 'zh-hans' => '西班牙语', 'zh-hant' => '西班牙語'],
        'language.eu' => ['en' => 'Basque', 'zh-hans' => '巴斯克语', 'zh-hant' => '巴斯克語'],
        'language.fa' => ['en' => 'Persian', 'zh-hans' => '波斯语', 'zh-hant' => '波斯語'],
        'language.fi' => ['en' => 'Finnish', 'zh-hans' => '芬兰语', 'zh-hant' => '芬蘭語'],
        'language.fr' => ['en' => 'French', 'zh-hans' => '法语', 'zh-hant' => '法語'],
        'language.gl' => ['en' => 'Galician', 'zh-hans' => '加利西亚语', 'zh-hant' => '加利西亞文'],
        'language.he' => ['en' => 'Hebrew', 'zh-hans' => '希伯来语', 'zh-hant' => '希伯來語'],
        'language.hr' => ['en' => 'Croatian', 'zh-hans' => '克罗地亚语', 'zh-hant' => '克羅埃西亞語'],
        'language.hu' => ['en' => 'Hungarian', 'zh-hans' => '匈牙利语', 'zh-hant' => '匈牙利語'],
        'language.hy' => ['en' => 'Armenian', 'zh-hans' => '亚美尼亚语', 'zh-hant' => '亞美尼亞文'],
        'language.id' => ['en' => 'Indonesian', 'zh-hans' => '印度尼西亚语', 'zh-hant' => '印尼語'],
        'language.it' => ['en' => 'Italian', 'zh-hans' => '意大利语', 'zh-hant' => '義大利語'],
        'language.ja' => ['en' => 'Japanese', 'zh-hans' => '日语', 'zh-hant' => '日語'],
        'language.ka' => ['en' => 'Georgian', 'zh-hans' => '格鲁吉亚语', 'zh-hant' => '喬治亞文'],
        'language.ko' => ['en' => 'Korean', 'zh-hans' => '韩语', 'zh-hant' => '韓語'],
        'language.la' => ['en' => 'Latin', 'zh-hans' => '拉丁语', 'zh-hant' => '拉丁語'],
        'language.lt' => ['en' => 'Lithuanian', 'zh-hans' => '立陶宛语', 'zh-hant' => '立陶宛語'],
        'language.ml' => ['en' => 'Malayalam', 'zh-hans' => '马拉雅拉姆语', 'zh-hant' => '馬來亞拉姆文'],
        'language.mr' => ['en' => 'Marathi', 'zh-hans' => '马拉地语', 'zh-hant' => '馬拉地文'],
        'language.ms' => ['en' => 'Malay', 'zh-hans' => '马来语', 'zh-hant' => '馬來語'],
        'language.mzn' => ['en' => 'Mazanderani', 'zh-hans' => '马赞德兰语', 'zh-hant' => '馬贊德蘭文'],
        'language.nl' => ['en' => 'Dutch', 'zh-hans' => '荷兰语', 'zh-hant' => '荷蘭語'],
        'language.nn' => ['en' => 'Norwegian Nynorsk', 'zh-hans' => '新挪威语', 'zh-hant' => '新挪威語'],
        'language.no' => ['en' => 'Norwegian', 'zh-hans' => '挪威语', 'zh-hant' => '挪威語'],
        'language.pam' => ['en' => 'Pampanga', 'zh-hans' => '邦板牙语', 'zh-hant' => '潘帕嘉文'],
        'language.pl' => ['en' => 'Polish', 'zh-hans' => '波兰语', 'zh-hant' => '波蘭語'],
        'language.pt' => ['en' => 'Portuguese', 'zh-hans' => '葡萄牙语', 'zh-hant' => '葡萄牙語'],
        'language.ro' => ['en' => 'Romanian', 'zh-hans' => '罗马尼亚语', 'zh-hant' => '羅馬尼亞語'],
        'language.ru' => ['en' => 'Russian', 'zh-hans' => '俄语', 'zh-hant' => '俄語'],
        'language.sh' => ['en' => 'Serbo-Croatian', 'zh-hans' => '塞尔维亚-克罗地亚语', 'zh-hant' => '塞爾維亞克羅埃西亞文'],
        'language.simple' => ['en' => 'Simple English', 'zh-hans' => '简单英语', 'zh-hant' => '簡易英語'],
        'language.sk' => ['en' => 'Slovak', 'zh-hans' => '斯洛伐克语', 'zh-hant' => '斯洛伐克語'],
        'language.sl' => ['en' => 'Slovenian', 'zh-hans' => '斯洛文尼亚语', 'zh-hant' => '斯洛維尼亞語'],
        'language.sq' => ['en' => 'Albanian', 'zh-hans' => '阿尔巴尼亚语', 'zh-hant' => '阿爾巴尼亞文'],
        'language.sr' => ['en' => 'Serbian', 'zh-hans' => '塞尔维亚语', 'zh-hant' => '塞爾維亞語'],
        'language.sv' => ['en' => 'Swedish', 'zh-hans' => '瑞典语', 'zh-hant' => '瑞典語'],
        'language.ta' => ['en' => 'Tamil', 'zh-hans' => '泰米尔语', 'zh-hant' => '坦米爾語'],
        'language.th' => ['en' => 'Thai', 'zh-hans' => '泰语', 'zh-hant' => '泰語'],
        'language.tr' => ['en' => 'Turkish', 'zh-hans' => '土耳其语', 'zh-hant' => '土耳其語'],
        'language.uk' => ['en' => 'Ukrainian', 'zh-hans' => '乌克兰语', 'zh-hant' => '烏克蘭語'],
        'language.uz' => ['en' => 'Uzbek', 'zh-hans' => '乌兹别克语', 'zh-hant' => '烏茲別克文'],
        'language.vi' => ['en' => 'Vietnamese', 'zh-hans' => '越南语', 'zh-hant' => '越南語'],
        'language.zh' => ['en' => 'Chinese', 'zh-hans' => '中文', 'zh-hant' => '中文'],
    ];
    return $messages;
}

/** Map a supported browser language range, giving an explicit script priority. */
function uiRangeLocale(string $range): ?string
{
    $parts = explode('-', strtolower($range));
    if ($parts[0] === 'en' || $range === '*') {
        return 'en';
    }
    if ($parts[0] !== 'zh') {
        return null;
    }
    if (in_array('hant', $parts, true)) {
        return 'zh-hant';
    }
    if (in_array('hans', $parts, true)) {
        return 'zh-hans';
    }
    return array_intersect(['tw', 'hk', 'mo'], $parts) !== [] ? 'zh-hant' : 'zh-hans';
}

/** Select a supported browser language by quality, then header order. */
function selectUiLocale(string $acceptLanguage): string
{
    $preferences = [];
    foreach (array_slice(explode(',', substr($acceptLanguage, 0, 8192)), 0, 100) as $order => $entry) {
        $parameters = array_map('trim', explode(';', $entry));
        $range = strtolower(array_shift($parameters));
        if (preg_match('/\A(?:[a-z]{1,8}(?:-[a-z0-9]{1,8})*|\*)\z/', $range) !== 1) {
            continue;
        }
        $weight = 1.0;
        $qualitySeen = false;
        foreach ($parameters as $parameter) {
            if (preg_match('/\Aq\s*=/i', $parameter) !== 1) {
                continue;
            }
            if ($qualitySeen || preg_match('/\Aq\s*=\s*(0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)\z/i', $parameter, $match) !== 1) {
                $weight = 0.0;
                break;
            }
            $qualitySeen = true;
            $weight = (float) $match[1];
        }
        $locale = uiRangeLocale($range);
        if ($weight > 0 && $locale !== null) {
            $preferences[] = ['locale' => $locale, 'quality' => $weight, 'order' => $order];
        }
    }
    usort($preferences, static fn (array $left, array $right): int =>
        ($right['quality'] <=> $left['quality']) ?: ($left['order'] <=> $right['order']));
    return $preferences[0]['locale'] ?? 'en';
}

/** Resolve the browser language without persistent or URL overrides. */
function uiLocale(): string
{
    static $locale = null;
    return $locale ??= selectUiLocale($_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? '');
}

/** Return the standard HTML and Content-Language tag for this interface. */
function uiLanguageTag(?string $locale = null): string
{
    return ['zh-hans' => 'zh-Hans', 'zh-hant' => 'zh-Hant', 'en' => 'en'][$locale ?? uiLocale()] ?? 'en';
}

/** Translate plain interface text; callers escape it for its output context. */
function uiTextFor(string $locale, string $key, array $parameters = []): string
{
    $messages = uiCatalog();
    if (!isset($messages[$key])) {
        throw new InvalidArgumentException('Unknown interface message: ' . $key);
    }
    $message = $messages[$key][$locale] ?? $messages[$key]['en'];
    $replacements = [];
    foreach ($parameters as $name => $value) {
        $replacements['{' . $name . '}'] = (string) $value;
    }
    return strtr($message, $replacements);
}

/** Translate a message using the current browser or explicitly selected locale. */
function uiText(string $key, array $parameters = []): string
{
    return uiTextFor(uiLocale(), $key, $parameters);
}

/** Translate known language names while preserving unrecognized source codes. */
function uiLanguageName(string $language): string
{
    return isset(uiCatalog()['language.' . $language]) ? uiText('language.' . $language) : $language;
}

/** Link local views while leaving interface selection to the browser. */
function uiUrl(string $path, array $parameters = []): string
{
    return $parameters === [] ? $path : $path . '?' . http_build_query($parameters, '', '&', PHP_QUERY_RFC3986);
}

/** Localize display units without changing the status parser's duration precision. */
function uiDuration(?string $duration): string
{
    if ($duration === null) {
        return '—';
    }
    if (preg_match('/\A([0-9]+) min ([0-9]+) sec\z/', $duration, $parts) === 1) {
        return uiText('duration.minutes', ['minutes' => $parts[1], 'seconds' => $parts[2]]);
    }
    if (preg_match('/\A([0-9]+(?:\.[0-9]+)?) sec\z/', $duration, $parts) === 1) {
        return uiText('duration.seconds', ['seconds' => $parts[1]]);
    }
    return $duration;
}

/** Show a running job's measured elapsed time with an unfinished marker. */
function uiRunningDuration(string $startedAt, ?int $now = null): string
{
    $seconds = max(0, ($now ?? time()) - (new DateTimeImmutable($startedAt))->getTimestamp());
    return ($seconds >= 60
        ? uiText('duration.minutes', ['minutes' => intdiv($seconds, 60), 'seconds' => $seconds % 60])
        : uiText('duration.seconds', ['seconds' => $seconds])) . '…';
}
