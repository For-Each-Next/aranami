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
        'project.eyebrow' => ['en' => 'WIKIPROJECT VIDEO GAMES', 'zh-hans' => '电子游戏专题', 'zh-hant' => '電子遊戲專題'],
        'hero.intro' => ['en' => 'Follow recognized content across Wikipedia languages and the last 100 days of Chinese main-page appearances.', 'zh-hans' => '关注各语言维基百科的优质内容，以及中文首页最近100天的展示。', 'zh-hant' => '關注各語言維基百科的優質內容，以及中文首頁最近100天的展示。'],
        'nav.skip' => ['en' => 'Skip to the dashboard', 'zh-hans' => '跳至专题看板', 'zh-hant' => '跳至專題看板'],
        'nav.aria' => ['en' => 'Main navigation', 'zh-hans' => '主要导航', 'zh-hant' => '主要導覽'],
        'nav.quality' => ['en' => 'Across languages', 'zh-hans' => '跨语言认证', 'zh-hant' => '跨語言認證'],
        'nav.homepage' => ['en' => 'Chinese main page', 'zh-hans' => '中文首页', 'zh-hant' => '中文首頁'],
        'nav.project' => ['en' => 'Join the project', 'zh-hans' => '参与专题', 'zh-hant' => '參與專題'],
        'nav.logs' => ['en' => 'Project logs', 'zh-hans' => '项目日志', 'zh-hant' => '專案日誌'],
        'action.view' => ['en' => 'View', 'zh-hans' => '查看', 'zh-hant' => '查看'],
        'empty.section' => ['en' => 'This section is temporarily unavailable.', 'zh-hans' => '这一栏的数据暂时无法获取。', 'zh-hant' => '這一欄的資料暫時無法取得。'],
        'empty.period' => ['en' => 'No appearances are recorded for this period.', 'zh-hans' => '这段期间没有已记录的展示。', 'zh-hant' => '這段期間沒有已記錄的展示。'],
        'records.more' => ['en' => 'Show remaining records ({count})', 'zh-hans' => '查看其余{count}条记录', 'zh-hant' => '查看其餘{count}筆記錄'],
        'date.window_start' => ['en' => 'Already displayed at the start of this period', 'zh-hans' => '期初已在首页展示', 'zh-hant' => '期初已在首頁展示'],
        'date.observed' => ['en' => 'Observation date', 'zh-hans' => '观察日期', 'zh-hant' => '觀察日期'],
        'date.scheduled' => ['en' => 'Scheduled', 'zh-hans' => '预定', 'zh-hant' => '預定'],
        'badge.FA' => ['en' => 'Featured article', 'zh-hans' => '典范条目', 'zh-hant' => '典範條目'],
        'badge.FL' => ['en' => 'FL · Featured list', 'zh-hans' => '典表', 'zh-hant' => '典表'],
        'badge.GA' => ['en' => 'Good article', 'zh-hans' => '优良条目', 'zh-hant' => '優良條目'],
        'badge.recommended' => ['en' => 'Recommended article', 'zh-hans' => '推荐', 'zh-hant' => '推薦'],
        'badge.featured' => ['en' => 'Featured content', 'zh-hans' => '典范内容', 'zh-hant' => '典範內容'],
        'period.default' => ['en' => 'Last 100 days', 'zh-hans' => '最近100天', 'zh-hant' => '最近100天'],
        'snapshot.context' => ['en' => 'Current recognition across languages · Chinese main-page history', 'zh-hans' => '跨语言现有认证 · 中文首页展示记录', 'zh-hant' => '跨語言現有認證 · 中文首頁展示記錄'],
        'snapshot.none' => ['en' => 'No data snapshot is available yet', 'zh-hans' => '尚未获取数据快照', 'zh-hant' => '尚未取得資料快照'],
        'snapshot.updated' => ['en' => 'Data updated:', 'zh-hans' => '数据更新：', 'zh-hant' => '資料更新：'],
        'notice.preparing' => ['en' => 'Project data is being prepared. Please check again later.', 'zh-hans' => '专题数据准备中，请稍后再查看。', 'zh-hant' => '專題資料準備中，請稍後再查看。'],
        'notice.partial' => ['en' => 'Some sources are temporarily unavailable. Available records remain visible below.', 'zh-hans' => '部分来源暂时无法获取；可用记录仍列于下方。', 'zh-hant' => '部分來源暫時無法取得；可用記錄仍列於下方。'],
        'quality.heading' => ['en' => 'Across the wikis', 'zh-hans' => '他山之石', 'zh-hant' => '他山之石'],
        'quality.current' => ['en' => 'Current recognition', 'zh-hans' => '现有认证', 'zh-hant' => '目前認證'],
        'quality.intro' => ['en' => 'Recognized video-game articles across Wikipedia languages. Foreign articles include Chinese counterparts for comparison.', 'zh-hans' => '各语言维基百科的优质电子游戏条目；外语条目附上中文对照。', 'zh-hant' => '各語言維基百科的優質電子遊戲條目；外語條目附上中文對照。'],
        'quality.unavailable' => ['en' => 'Recognition data across languages is temporarily unavailable.', 'zh-hans' => '跨语言认证数据暂时无法获取。', 'zh-hant' => '跨語言認證資料暫時無法取得。'],
        'quality.empty' => ['en' => 'No recognized content is currently recorded.', 'zh-hans' => '目前没有已记录的认证内容。', 'zh-hant' => '目前沒有已記錄的認證內容。'],
        'quality.random' => ['en' => 'Random language', 'zh-hans' => '随机切换', 'zh-hant' => '隨機切換'],
        'quality.filter_error' => ['en' => 'This selection could not be loaded. Please try again.', 'zh-hans' => '暂时无法载入所选内容，请重试。', 'zh-hant' => '暫時無法載入所選內容，請重試。'],
        'quality.all_languages' => ['en' => 'All languages', 'zh-hans' => '所有语言', 'zh-hant' => '所有語言'],
        'quality.recent_intro' => ['en' => 'All languages · Newly observed badges', 'zh-hans' => '所有语言 · 新观察到的认证', 'zh-hant' => '所有語言 · 新觀察到的認證'],
        'quality.recent_dates' => ['en' => 'Observed in the last 30 days. Times show first detection, not promotion dates.', 'zh-hans' => '列出近30天新观察到的认证；时间为首次观察时间，并非授予日期。', 'zh-hant' => '列出近30天新觀察到的認證；時間為首次觀察時間，並非授予日期。'],
        'quality.recent_pending' => ['en' => 'New badge observations are not available yet.', 'zh-hans' => '近期认证观察记录尚未获取。', 'zh-hant' => '近期認證觀察記錄尚未取得。'],
        'quality.recent_empty' => ['en' => 'No new badges have been observed in this period.', 'zh-hans' => '这段期间尚未观察到新增认证。', 'zh-hant' => '這段期間尚未觀察到新增認證。'],
        'quality.recent_aria' => ['en' => 'Newly observed badges across all languages', 'zh-hans' => '各语言新增认证观察记录', 'zh-hant' => '各語言新增認證觀察記錄'],
        'quality.short.FA' => ['en' => 'FA', 'zh-hans' => '典范', 'zh-hant' => '典範'],
        'quality.short.FL' => ['en' => 'FL', 'zh-hans' => '典表', 'zh-hant' => '典表'],
        'quality.short.GA' => ['en' => 'GA', 'zh-hans' => '优良', 'zh-hant' => '優良'],
        'quality.short.recommended' => ['en' => 'Rec.', 'zh-hans' => '推荐', 'zh-hant' => '推薦'],
        'quality.sample' => ['en' => '{language} · {count} recognition records', 'zh-hans' => '{language} · {count}条认证记录', 'zh-hant' => '{language} · {count}筆認證記錄'],
        'quality.sample_status' => ['en' => '{language} · {status} · {count} recognition records', 'zh-hans' => '{language} · {status} · {count}条认证记录', 'zh-hant' => '{language} · {status} · {count}筆認證記錄'],
        'quality.progress' => ['en' => 'Showing {shown} of {total} records', 'zh-hans' => '已显示{shown}条，共{total}条', 'zh-hant' => '已顯示{shown}筆，共{total}筆'],
        'quality.show_more' => ['en' => 'Show {count} more', 'zh-hans' => '再显示{count}条', 'zh-hant' => '再顯示{count}筆'],
        'quality.sample_empty' => ['en' => 'No articles are recorded for this selection.', 'zh-hans' => '所选条件没有已记录的条目。', 'zh-hant' => '所選條件沒有已記錄的條目。'],
        'quality.table_aria' => ['en' => 'Recognized content: {language}', 'zh-hans' => '{language}认证内容', 'zh-hant' => '{language}認證內容'],
        'column.article' => ['en' => 'Article', 'zh-hans' => '条目', 'zh-hant' => '條目'],
        'column.language' => ['en' => 'Language', 'zh-hans' => '语言', 'zh-hant' => '語言'],
        'column.recognition' => ['en' => 'Recognition', 'zh-hans' => '认证', 'zh-hant' => '認證'],
        'quality.companion_unknown' => ['en' => '(Chinese version)', 'zh-hans' => '（中文版）', 'zh-hant' => '（中文版）'],
        'quality.companion_known' => ['en' => '(Chinese version, {grade})', 'zh-hans' => '（中文版，{grade}）', 'zh-hant' => '（中文版，{grade}）'],
        'quality.companion_missing' => ['en' => 'No Chinese article linked', 'zh-hans' => '未链接中文条目', 'zh-hant' => '未連結中文條目'],
        'quality.note' => ['en' => 'Recognition follows current Wikidata FA, FL, GA and recommended badges, which may lag changes on each Wikipedia.', 'zh-hans' => '认证以Wikidata现有典范、典表、优良、推荐标章为准，更新可能晚于各维基百科。', 'zh-hant' => '認證以Wikidata現有典範、典表、優良、推薦標章為準，更新可能晚於各維基百科。'],
        'quality.observed' => ['en' => 'Observed:', 'zh-hans' => '观察时间：', 'zh-hant' => '觀察時間：'],
        'counts.heading' => ['en' => 'Recognition by language', 'zh-hans' => '各语言认证统计', 'zh-hant' => '各語言認證統計'],
        'counts.unavailable' => ['en' => 'Recognized-article totals and counts are temporarily unavailable.', 'zh-hans' => '认证条目总数与统计暂时无法获取。', 'zh-hant' => '認證條目總數與統計暫時無法取得。'],
        'counts.total' => ['en' => 'Currently recognized articles', 'zh-hans' => '现有认证条目', 'zh-hant' => '目前已認證條目'],
        'counts.languages' => ['en' => 'Languages: {count}', 'zh-hans' => '{count}种语言', 'zh-hant' => '{count}種語言'],
        'counts.aria' => ['en' => 'Recognition badge counts by language', 'zh-hans' => '各语言认证标章统计', 'zh-hant' => '各語言認證標章統計'],
        'homepage.heading' => ['en' => 'Current highlights', 'zh-hans' => '动态热门', 'zh-hant' => '動態熱門'],
        'homepage.intro' => ['en' => 'Daily popular video-game project articles and the last 100 days of Chinese main-page appearances. Quality content also includes the next 30 days of scheduled appearances.', 'zh-hans' => '电子游戏专题条目的每日热度与最近100天中文首页展示记录；优质内容另列未来30天预定。', 'zh-hant' => '電子遊戲專題條目的每日熱度與最近100天中文首頁展示記錄；優質內容另列未來30天預定。'],
        'pageviews.heading' => ['en' => 'Popular articles', 'zh-hans' => '热门条目', 'zh-hant' => '熱門條目'],
        'pageviews.description' => ['en' => 'Latest available daily pageviews · Up to 10 articles', 'zh-hans' => '最新可用的每日浏览量 · 最多10篇', 'zh-hant' => '最新可用的每日瀏覽量 · 最多10篇'],
        'pageviews.count' => ['en' => 'Matching project articles: {count}', 'zh-hans' => '{count}篇符合条件的专题条目', 'zh-hant' => '{count}篇符合條件的專題條目'],
        'pageviews.views' => ['en' => '{count} views', 'zh-hans' => '{count}次浏览', 'zh-hant' => '{count}次瀏覽'],
        'pageviews.unavailable' => ['en' => 'Daily pageview data is temporarily unavailable.', 'zh-hans' => '每日浏览量数据暂时无法获取。', 'zh-hant' => '每日瀏覽量資料暫時無法取得。'],
        'pageviews.empty' => ['en' => 'No video-game project articles appeared in this day’s site top 1,000.', 'zh-hans' => '这天全站前1,000名中没有电子游戏专题条目。', 'zh-hant' => '這天全站前1,000名中沒有電子遊戲專題條目。'],
        'pageviews.coverage' => ['en' => 'Filtered from Chinese Wikipedia’s top 1,000 pages; fewer than 10 project articles may qualify.', 'zh-hans' => '从中文维基百科全站前1,000名筛选专题条目，因此可能不足10篇。', 'zh-hant' => '從中文維基百科全站前1,000名篩選專題條目，因此可能不足10篇。'],
        'pageviews.source' => ['en' => 'Wikimedia data', 'zh-hans' => '维基媒体数据', 'zh-hant' => '維基媒體資料'],
        'homepage.recognized' => ['en' => 'Quality content', 'zh-hans' => '优质内容', 'zh-hant' => '優質內容'],
        'homepage.recognized_description' => ['en' => 'Past 100 days and next 30 scheduled days', 'zh-hans' => '最近100天展示与未来30天预定', 'zh-hant' => '最近100天展示與未來30天預定'],
        'homepage.upcoming_heading' => ['en' => 'Next 30 days · Scheduled', 'zh-hans' => '未来30天 · 预定', 'zh-hant' => '未來30天 · 預定'],
        'homepage.past_heading' => ['en' => 'Last 100 days · Displayed', 'zh-hans' => '最近100天 · 已展示', 'zh-hant' => '最近100天 · 已展示'],
        'homepage.upcoming_empty' => ['en' => 'No appearances are currently scheduled for the next 30 days.', 'zh-hans' => '未来30天目前没有已安排的展示。', 'zh-hant' => '未來30天目前沒有已安排的展示。'],
        'homepage.recognized_count' => ['en' => 'Available appearances and scheduled records: {count}', 'zh-hans' => '{count}条已获取的展示与预定记录', 'zh-hant' => '{count}筆已取得的展示與預定記錄'],
        'homepage.dyk' => ['en' => 'New article highlights', 'zh-hans' => '新篇推荐', 'zh-hant' => '新篇推薦'],
        'homepage.dyk_description' => ['en' => '“Did you know?” (DYK) on the Chinese main page', 'zh-hans' => '中文首页的“你知道吗？”（DYK）', 'zh-hant' => '中文首頁的「你知道嗎？」（DYK）'],
        'homepage.news' => ['en' => 'In the news', 'zh-hans' => '新闻动态', 'zh-hant' => '新聞動態'],
        'homepage.history' => ['en' => 'On this day', 'zh-hans' => '历史上的今天', 'zh-hant' => '歷史上的今天'],
        'homepage.news_history' => ['en' => 'News and history', 'zh-hans' => '新闻旧闻', 'zh-hant' => '新聞舊聞'],
        'homepage.news_history_description' => ['en' => 'News, ongoing events, recent deaths and historical anniversaries', 'zh-hans' => '新闻动态、持续事件、最近逝世与历史上的今天', 'zh-hant' => '新聞動態、持續事件、最近逝世與歷史上的今天'],
        'homepage.partial' => ['en' => 'Source temporarily unavailable: {sections}.', 'zh-hans' => '{sections}的数据暂时无法获取。', 'zh-hant' => '{sections}的資料暫時無法取得。'],
        'homepage.count' => ['en' => 'Recorded appearances: {count}', 'zh-hans' => '{count}条展示记录', 'zh-hant' => '{count}筆展示記錄'],
        'homepage.note' => ['en' => 'Dates refer to main-page appearances or the current future schedule when marked “Scheduled”. “Already displayed at the start of this period” means the appearance began before the observation window.', 'zh-hans' => '日期为首页展示日期；标记“预定”的记录来自现有未来安排。“期初已在首页展示”表示该条目在观察期间开始前已登上首页。', 'zh-hant' => '日期為首頁展示日期；標記「預定」的記錄來自目前未來安排。「期初已在首頁展示」表示該條目在觀察期間開始前已登上首頁。'],
        'ops.heading' => ['en' => 'Aranami on-wiki report updates', 'zh-hans' => 'Aranami 站内报告更新状态', 'zh-hant' => 'Aranami 站內報告更新狀態'],
        'ops.purpose' => ['en' => 'These tasks update reports on Wikipedia.', 'zh-hans' => '这些工作负责更新维基百科站内报告。', 'zh-hant' => '這些工作負責更新維基百科站內報告。'],
        'ops.version_summary' => ['en' => 'Version {version} · Activity and logs', 'zh-hans' => '版本{version} · 工作记录与日志', 'zh-hant' => '版本{version} · 工作紀錄與日誌'],
        'ops.unavailable' => ['en' => 'Unavailable', 'zh-hans' => '未获取', 'zh-hant' => '未取得'],
        'ops.installed' => ['en' => 'Installed version:', 'zh-hans' => '已安装版本：', 'zh-hant' => '已安裝版本：'],
        'ops.browse_logs' => ['en' => 'Detailed Aranami logs', 'zh-hans' => 'Aranami详细日志', 'zh-hant' => 'Aranami詳細日誌'],
        'ops.aria' => ['en' => 'Aranami task activity', 'zh-hans' => 'Aranami工作状态表', 'zh-hant' => 'Aranami工作狀態表'],
        'ops.column_task' => ['en' => 'Task', 'zh-hans' => '工作', 'zh-hant' => '工作'],
        'ops.column_state' => ['en' => 'Latest result', 'zh-hans' => '最近结果', 'zh-hant' => '最近結果'],
        'ops.column_duration' => ['en' => 'Duration', 'zh-hans' => '耗时', 'zh-hant' => '耗時'],
        'ops.column_time' => ['en' => 'Time (UTC)', 'zh-hans' => '时间（UTC）', 'zh-hant' => '時間（UTC）'],
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
        'state.Outcome pending' => ['en' => 'Awaiting outcome', 'zh-hans' => '等待结果', 'zh-hant' => '等待結果'],
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
        'logs.project_page_title' => ['en' => 'Aranami project logs', 'zh-hans' => 'Aranami项目日志', 'zh-hant' => 'Aranami專案日誌'],
        'logs.project_heading' => ['en' => 'Project logs', 'zh-hans' => '项目日志', 'zh-hant' => '專案日誌'],
        'logs.project_intro' => ['en' => 'Major events from Aranami report runs and the dashboard background updater, grouped by UTC date.', 'zh-hans' => '汇总Aranami报告运行与看板后台更新的重要事件，按UTC日期分组。', 'zh-hant' => '彙整Aranami報告執行與看板背景更新的重要事件，依UTC日期分組。'],
        'logs.detailed_link' => ['en' => 'Detailed Aranami logs', 'zh-hans' => 'Aranami详细日志', 'zh-hant' => 'Aranami詳細日誌'],
        'logs.project_link' => ['en' => 'Shared project logs', 'zh-hans' => '项目主要日志', 'zh-hant' => '專案主要日誌'],
        'logs.project_empty' => ['en' => 'No daily project logs are available yet. They appear when a report run or dashboard update records its first event.', 'zh-hans' => '尚无项目每日日志；报告运行或看板更新记录首个事件后便会出现。', 'zh-hant' => '尚無專案每日日誌；報告執行或看板更新記錄首個事件後便會出現。'],
        'logs.project_footer' => ['en' => 'Project logs update during report runs and dashboard refreshes. Reload a daily log to see new entries.', 'zh-hans' => '项目日志随报告运行与看板更新而写入。重新载入每日日志可查看新记录。', 'zh-hant' => '專案日誌隨報告執行與看板更新而寫入。重新載入每日日誌可查看新紀錄。'],
        'logs.back' => ['en' => 'Back to the dashboard', 'zh-hans' => '返回专题看板', 'zh-hant' => '返回專題看板'],
        'logs.intro_before' => ['en' => 'Aranami updates on-wiki reports, primarily for', 'zh-hans' => 'Aranami主要为', 'zh-hant' => 'Aranami主要為'],
        'logs.project' => ['en' => 'WikiProject Video games on Chinese Wikipedia', 'zh-hans' => '中文维基百科电子游戏专题', 'zh-hant' => '中文維基百科電子遊戲專題'],
        'logs.intro_after' => ['en' => '.', 'zh-hans' => '更新站内报告。', 'zh-hant' => '更新站內報告。'],
        'logs.empty' => ['en' => 'No daily logs are available yet. Refresh after Aranami writes its first log.', 'zh-hans' => '尚无每日日志。Aranami写入第一份日志后，可刷新此页面查看。', 'zh-hant' => '尚無每日日誌。Aranami寫入第一份日誌後，可重新整理此頁面查看。'],
        'logs.column_date' => ['en' => 'Date (UTC)', 'zh-hans' => '日期（UTC）', 'zh-hant' => '日期（UTC）'],
        'logs.column_file' => ['en' => 'Log file', 'zh-hans' => '日志文件', 'zh-hant' => '日誌檔案'],
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
