<?php
/** Render all dashboard languages from offline data and verify retained cards. */
declare(strict_types=1);

/** Fail an integration assertion even when native assertions are disabled. */
function expectHomepage(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

/** Remove only this disposable fixture without following links. */
function removeHomepageFixture(string $path): void
{
    if (is_link($path) || !is_dir($path)) {
        unlink($path);
        return;
    }
    foreach (new DirectoryIterator($path) as $entry) {
        if (!$entry->isDot()) {
            removeHomepageFixture($entry->getPathname());
        }
    }
    rmdir($path);
}

$fixture = sys_get_temp_dir() . '/aranami-homepage-' . bin2hex(random_bytes(8));
$private = $fixture . '/app/src/webservice';
mkdir($private . '/public_html/maintenance', 0700, true);
mkdir($fixture . '/app/src/shared', 0700);
mkdir($fixture . '/cache/toolforge', 0700, true);
mkdir($fixture . '/state', 0700);
register_shutdown_function(static function () use ($fixture): void {
    removeHomepageFixture($fixture);
});
$source = dirname(__DIR__, 2) . '/src/webservice';
foreach (['i18n.php', 'status.php', 'page-cache.php', 'project.php', 'assessment.php',
    'public_html/index.php', 'public_html/maintenance/index.php'] as $name) {
    $content = file_get_contents($source . '/' . $name);
    file_put_contents($private . '/' . $name, str_replace('/data/project/aranami', $fixture, $content));
}
$definition = ['pages' => ['report' => 'WikiProject:电子游戏/热门条目'],
    'prefixes' => [], 'hour' => '*', 'minute' => 59];
file_put_contents($fixture . '/app/src/shared/routines.json', json_encode([
    'schema' => 1, 'routines' => ['pageviews' => $definition],
]));
file_put_contents($fixture . '/app/src/shared/version.json', '{"schema":1,"version":"0.5.6.dev1"}');
$now = new DateTimeImmutable('now', new DateTimeZone('UTC'));
$stamp = $now->format('Y-m-d\TH:i:s\Z');
$day = $now->modify('-1 day')->format('Y-m-d');
$period = ['label' => $day, 'start' => $day, 'end' => $now->modify('+1 day')->format('Y-m-d')];
$metadata = [
    'config_id' => str_repeat('a', 64), 'refresh_interval_seconds' => 86400,
    'last_success_at' => $stamp, 'last_attempt_at' => $stamp, 'failed' => false,
    'period' => $period,
];
$article = ['title' => 'Game & Characters / 中文', 'url' => 'https://zh.wikipedia.org/wiki/Game',
    'extract' => '简介 <script>never execute</script> & 中文。第二句。',
    'extract_hant' => '簡介 <script>never execute</script> & 中文。第二句。',
    'extract_zh' => '原文遊戲与音乐，保留原始简介。',
    'views' => 723, 'rank' => 1, 'site_rank' => 681, 'assessment_class' => 'GA', 'assessment_importance' => 'High'];
$maintenance = ['page_id' => 17, 'title' => 'Maintenance article', 'url' => 'https://zh.wikipedia.org/wiki/Maintenance',
    'assessment_class' => 'B', 'assessment_importance' => 'High', 'issues' => ['references'],
    'oldest_issue_month' => '2020-03', 'oldest_issue_category' => '自2020年03月需补充来源的条目'];
$appearance = ['title' => 'Featured <&> game', 'url' => 'https://zh.wikipedia.org/wiki/Featured',
    'extract' => '这是保存的条目简介。第二句。',
    'extract_hant' => '這是儲存的條目簡介。第二句。',
    'extract_zh' => '原文遊戲与音乐，保留原始简介。',
    'date' => $day, 'kind' => 'fa', 'assessment_class' => 'FA', 'assessment_importance' => 'High'];
$scheduledDay = $now->modify('+2 days')->format('Y-m-d');
$backlog = ['observed_at' => $stamp, 'total' => 1, 'groups' => [[
    'key' => 'references', 'category' => '缺少来源的条目',
    'source_url' => 'https://zh.wikipedia.org/wiki/Category:References',
    'section_key' => 'sources', 'section_label' => ['en' => 'References', 'zh-hans' => '参考来源', 'zh-hant' => '參考來源'],
    'label' => ['en' => 'Add citations', 'zh-hans' => '补充来源', 'zh-hant' => '補充來源'],
    'count' => 1, 'items' => [$maintenance],
]]];
$snapshot = [
    'schema' => 2, 'period' => $period, 'updated_at' => $stamp,
    'reports' => array_fill_keys(['recognized', 'dyk', 'pageviews', 'news_history', 'backlog'], $metadata),
    'homepage' => ['featured' => [$appearance],
        'good' => [array_replace($appearance, ['title' => 'Good game', 'kind' => 'ga', 'assessment_class' => 'GA'])],
        'upcoming' => [array_replace($appearance, ['title' => 'Scheduled game', 'date' => $scheduledDay, 'date_kind' => 'scheduled'])],
        'dyk' => array_map(static fn (int $index): array => array_replace($appearance, ['title' => 'DYK game ' . $index, 'kind' => 'dyk']), range(1, 13)),
        'news' => array_map(static fn (int $index): array => array_replace($appearance, ['title' => 'News game ' . $index, 'kind' => 'news']), range(1, 7)),
        'history' => [array_replace($appearance, ['title' => 'Historic game', 'kind' => 'history'])]],
    'pageviews' => ['date' => $day, 'limit' => 10, 'scope' => 'site_top_1000', 'items' => [$article],
        'source_url' => 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/zh.wikipedia.org/all-access/' . str_replace('-', '/', $day)],
    'backlog' => $backlog, 'sources' => [], 'errors' => [],
];
file_put_contents($fixture . '/cache/toolforge/dashboard.json', json_encode($snapshot));
file_put_contents($fixture . '/state/routine-status.json', json_encode([
    'schema' => 1, 'updated_at' => $stamp, 'routines' => ['pageviews' => [
        'routine' => 'pageviews', 'status' => 'deferred', 'dry' => false, 'updated' => 0,
        'duration_seconds' => 12.5, 'started_at' => $now->modify('-13 seconds')->format('Y-m-d\TH:i:s\Z'),
        'finished_at' => $stamp, 'run_id' => str_repeat('a', 32),
    ]],
]));
$reportTitle = 'WikiProject:电子游戏/热门条目 <Latest & safe>';
$reportStarted = $now->modify('-5 seconds')->format('Y-m-d\TH:i:s\Z');
file_put_contents($fixture . '/state/report-updates.json', json_encode([
    'schema' => 1, 'updated_at' => $stamp, 'reports' => [
        hash('sha256', "zhwiki\n" . $reportTitle) => [
            'update_id' => str_repeat('c', 32), 'run_id' => str_repeat('a', 32),
            'routine' => 'pageviews', 'site' => 'zhwiki', 'title' => $reportTitle,
            'status' => 'updated', 'dry' => false, 'started_at' => $reportStarted,
            'finished_at' => $stamp, 'duration_seconds' => 5, 'confirmed_edits' => 1,
        ],
    ],
]));
$next = $now->modify('+2 days')->setTime(18, 59);
$monitorStart = $now->modify('-2 days -3 hours -4 minutes');
$schedule = [
    'schema' => 1, 'updated_at' => $stamp, 'state' => 'running',
    'started_at' => $monitorStart->format('Y-m-d\TH:i:s\Z'),
    'config_id' => str_repeat('b', 64), 'routines' => [
        'pageviews' => $definition + ['state' => 'scheduled', 'next_run_at' => $next->format('Y-m-d\TH:i:s\Z')],
    ],
];
$schedulePath = $fixture . '/state/schedule-status.json';
file_put_contents($schedulePath, json_encode($schedule));

/** Render one copied entry point in a fresh interpreter. */
function renderHomepageFixture(string $path, string $language, array $query = []): string
{
    $command = [PHP_BINARY, '-r',
        '$_SERVER["REQUEST_METHOD"]="GET"; $_SERVER["HTTP_ACCEPT_LANGUAGE"]=$argv[2]; $_SERVER["QUERY_STRING"]=""; $_GET=json_decode($argv[3],true); require $argv[1];',
        $path, $language, json_encode($query)];
    $pipes = [];
    $process = proc_open($command, [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    expectHomepage(is_resource($process), 'The fixture PHP process starts.');
    fclose($pipes[0]);
    $html = stream_get_contents($pipes[1]);
    $errors = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    expectHomepage(proc_close($process) === 0 && $errors === '', 'The renderer finishes without PHP errors: ' . $errors);
    return $html;
}

foreach (['en' => 'Waiting to retry', 'zh-CN' => '待重试', 'zh-TW' => '待重試'] as $language => $state) {
    $html = renderHomepageFixture($private . '/public_html/index.php', $language);
    expectHomepage(!str_contains($html, 'quality-data') && !str_contains($html, 'dashboard.js')
        && !str_contains($html, '他山之石') && substr_count($html, '<table>') === 1, 'Only the report-details dialog uses a table; retired datasets and scripts stay absent.');
    expectHomepage(str_contains($html, 'minmax(min(18em, 100%), 1fr)'), 'Sibling cards use a responsive eighteen-em minimum.');
    expectHomepage(str_contains($html, 'repeat(auto-fill,') && str_contains($html, 'list-style: decimal inside;')
        && !str_contains($html, 'entry-rank'), 'Native list markers number shared cards without stretching sparse groups.');
    expectHomepage(str_contains($html, $state), 'Routine cards include visible localized retry text.');
    expectHomepage(str_contains($html, $next->format('Y-m-d H:i') . ' UTC'), 'Routine cards show the actual postponed next run.');
    expectHomepage(str_contains($html, '0.5.6.dev1'), 'The footer reads source deployment metadata.');
    $uptime = match ($language) {
        'en' => 'Aranami (0.5.6.dev1) uptime: 2d 3h 4m',
        'zh-CN' => 'Aranami (0.5.6.dev1) 已连续运行2天3小时4分钟',
        'zh-TW' => 'Aranami (0.5.6.dev1) 已連續運行2天3小時4分鐘',
    };
    expectHomepage(str_contains($html, $uptime) && str_contains($html, $monitorStart->format('Y-m-d H:i') . ' UTC'),
        'The footer shows localized runtime since the recorded monitor restart.');
    expectHomepage(substr_count($html, '<footer>') === 1 && substr_count($html, 'class="footer-uptime"') === 1
        && substr_count($html, '0.5.6.dev1') === 1 && substr_count($html, 'href="/logs/"') === 1,
        'The uptime extends the single footer while preserving one version and run-log link.');
    expectHomepage(substr_count($html, 'href="/about/"') === 1, 'The footer links to the general-reader About page.');
    [$updated, $started, $finished, $duration] = match ($language) {
        'en' => ['Updated', 'Started', 'Finished', '5.000 sec'],
        'zh-CN' => ['已更新', '开始', '结束', '5.000秒'],
        'zh-TW' => ['已更新', '開始', '結束', '5.000秒'],
    };
    expectHomepage(preg_match('/<dialog class="page-updates"[^>]*>(.*?)<\/dialog>/s', $html, $updates) === 1,
        'A routine opens a dialog with the latest metadata for each destination page.');
    expectHomepage(preg_match('/<button class="status-link"[^>]+>' . preg_quote($state, '/') . '<\/button>.*?12\.50/s', $html) === 1
        && !str_contains($html, 'Page update details') && !str_contains($html, '页面更新详情')
        && str_contains($updates[1], $updated)
        && str_contains($updates[1], $started) && str_contains($updates[1], $finished)
        && str_contains($updates[1], $duration) && !str_contains($updates[1], 'zhwiki')
        && str_contains($updates[1], '&lt;Latest &amp; safe&gt;') && !str_contains($updates[1], '<Latest'),
        'Per-page outcomes, timestamps, duration, and escaped targets are localized: ' . $language);
    expectHomepage(str_contains($updates[1], '<table>') && substr_count($updates[1], 'scope="col"') === 5
        && str_contains($updates[1], 'href="https://zh.wikipedia.org/wiki/' . rawurlencode(str_replace(' ', '_', $reportTitle)) . '"')
        && str_contains($updates[1], '<form method="dialog">') && str_contains($html, 'src="/dialogs.js"'),
        'The modal has a linked page title, five table columns, a close control, and a local-only interaction script.');
    expectHomepage(str_contains($updates[1], $now->modify('-5 seconds')->format('Y-m-d H:i:s') . ' UTC')
        && str_contains($updates[1], $now->format('Y-m-d H:i:s') . ' UTC'),
        'Per-page start and finish timestamps retain UTC seconds.');
    expectHomepage(str_contains($html, 'class="backlog-task"') && str_contains($html, 'task=references'), 'Each maintenance task has its own worklist card.');
    preg_match('/href="(https:\/\/pageviews\.wmcloud\.org\/\?[^"]+)"/', $html, $match);
    expectHomepage(isset($match[1]), 'Observed views link to Pageviews Analysis.');
    parse_str(parse_url(html_entity_decode($match[1]), PHP_URL_QUERY), $parameters);
    expectHomepage($parameters === ['project' => 'zh.wikipedia.org', 'platform' => 'all-access', 'agent' => 'user',
        'start' => (new DateTimeImmutable($day))->modify('-29 days')->format('Y-m-d'), 'end' => $day,
        'pages' => $article['title']], 'View links encode thirty inclusive days ending on the observed day, with the exact source settings and title.');
    expectHomepage(preg_match('/<p class="card-meta"><a class="pageviews-number"[^>]+>[^<]+<\/a> · <span class="site-rank">[^<]*681[^<]*<\/span><\/p>/', $html) === 1,
        'Views and the plain article-only site rank share one metadata line.');
    expectHomepage(str_contains($html, ($language === 'zh-TW' ? '簡介' : '简介') . ' &lt;script&gt;never execute&lt;/script&gt; &amp; 中文。第二句。')
        && substr_count($html, 'class="article-extract"') === 24 && !str_contains($html, '<script>never execute')
        && str_contains($html, 'height: 100%;') && str_contains($html, 'margin-top: auto;')
        && str_contains($html, 'https://creativecommons.org/licenses/by-sa/4.0/'),
        'Every homepage section uses escaped cached introductions, small bottom metadata, and upstream attribution.');
    expectHomepage(substr_count($html, '<span class="extract-attribution">') === 4
        && preg_match_all('/UTC<\/time><span class="extract-attribution">/', $html) === 4
        && preg_match('/<footer>.*?CC BY-SA/s', $html) !== 1,
        'Each introduction section attributes its text immediately after the update time, rather than in the footer.');
    expectHomepage(str_contains($html, $language === 'zh-TW' ? '這是儲存的條目簡介。' : '这是保存的条目简介。'),
        'The interface selects its prepared simplified or traditional introduction.');
    if ($language === 'zh-CN') {
        expectHomepage(str_contains(html_entity_decode(strip_tags($html)), '简介摘自中文维基百科对应条目，以CC BY-SA 4.0协议发布'),
            'Simplified attribution preserves the requested full wording and linked license.');
        expectHomepage(str_contains(html_entity_decode(strip_tags($html)), (new DateTimeImmutable($day))->format('Y年n月j日') . '访问量位居全站前1,000名的专题条目'),
            'Popularity introduces the actual source date before the full requested project-article description.');
        expectHomepage(str_contains($html, '日浏览723次'), 'The count explicitly describes daily pageviews.');
    }
    expectHomepage(preg_match('/aria-labelledby="homepage-recognized".*?<ol class="card-grid article-list".*?<\/ol>/s', $html, $recognized) === 1,
        'Main-page showcases use the shared ordered card list.');
    $recognizedText = html_entity_decode(strip_tags($recognized[0]));
    $dateFormat = $language === 'en' ? 'F j, Y' : 'Y年n月j日';
    $scheduled = $language === 'en' ? '(Scheduled) ' : ($language === 'zh-CN' ? '（预定）' : '（預定）');
    expectHomepage(str_contains($recognizedText, $scheduled . (new DateTimeImmutable($scheduledDay))->format($dateFormat))
        && str_contains($recognizedText, $language === 'en' ? 'Featured content on the main page' : ($language === 'zh-CN' ? '典范内容展示' : '典範內容展示'))
        && !str_contains($recognized[0], 'content-kind'), 'Scheduled dates and appearance kinds occupy metadata rather than duplicate assessment chips.');
    expectHomepage(preg_match('/aria-labelledby="homepage-dyk".*?<ol class="card-grid article-list".*?<\/ol>/s', $html, $dyk) === 1
        && substr_count($dyk[0], 'class="article-card"') === 12 && !str_contains($dyk[0], 'DYK game 13'), 'DYK retains its twelve-card display limit.');
    expectHomepage(str_contains($html, 'News game 7') && !str_contains($html, '<details') && !str_contains($html, 'records.more'),
        'All supplied news/history cards appear directly without a remaining-records expander.');
    expectHomepage(!str_contains($html, '<small>') && !str_contains($html, '.footer-uptime { flex-basis:'),
        'The masthead and combined version/uptime have no secondary forced rows.');
    $worklist = renderHomepageFixture($private . '/public_html/maintenance/index.php', $language, ['task' => 'references']);
    expectHomepage(str_contains($worklist, 'class="article-card"') && !str_contains($worklist, '<table')
        && str_contains($worklist, 'sort=class') && str_contains($worklist, '2020-03')
        && str_contains($worklist, 'sort=random') && str_contains($worklist, 'order=desc')
        && str_contains($worklist, 'href="https://zh.wikipedia.org/wiki/Category:References"')
        && str_contains($worklist, 'Category:' . rawurlencode($maintenance['oldest_issue_category']))
        && str_contains($worklist, $language === 'en' ? 'March 2020' : '2020年3月')
        && !str_contains($worklist, '月份来自') && !str_contains($worklist, 'Months come from'),
        'Maintenance cards share numbering and inline chips, link tags and actual months, and expose random/reverse sorting.');
}

$genericChinese = renderHomepageFixture($private . '/public_html/index.php', 'zh');
expectHomepage(str_contains($genericChinese, '<html lang="zh">') && str_contains($genericChinese, '<bdi lang="zh">原文遊戲与音乐，保留原始简介。')
    && !str_contains($genericChinese, '这是保存的条目简介。') && !str_contains($genericChinese, '這是儲存的條目簡介。'),
    'A generic zh browser preference retains original mixed-script introductions without a simplified/traditional fallback.');
$withoutOriginal = $snapshot;
foreach ($withoutOriginal['homepage'] as &$rows) {
    foreach ($rows as &$row) {
        unset($row['extract_zh']);
    }
    unset($row);
}
unset($rows);
foreach ($withoutOriginal['pageviews']['items'] as &$row) {
    unset($row['extract_zh']);
}
unset($row);
file_put_contents($fixture . '/cache/toolforge/dashboard.json', json_encode($withoutOriginal));
$originalMissing = renderHomepageFixture($private . '/public_html/index.php', 'zh');
expectHomepage(!str_contains($originalMissing, 'class="article-extract"'), 'Unavailable original introductions are not replaced with converted text.');
file_put_contents($fixture . '/cache/toolforge/dashboard.json', json_encode($snapshot));
foreach ([
    ['state' => 'stopped'], ['state' => 'missing'], ['started_at' => null],
    ['updated_at' => $now->modify('-121 seconds')->format('Y-m-d\TH:i:s\Z')],
] as $change) {
    file_put_contents($schedulePath, json_encode(array_replace($schedule, $change)));
    $html = renderHomepageFixture($private . '/public_html/index.php', 'en', ['uncached' => '1']);
    expectHomepage(str_contains($html, 'Aranami (0.5.6.dev1) uptime: unavailable') && !str_contains($html, 'uptime: 2d'),
        'The rendered footer never claims continuous runtime for stopped, missing, stale, or unrecorded sessions.');
}
$snapshot['homepage']['news'] = [];
$snapshot['homepage']['history'] = [];
file_put_contents($fixture . '/cache/toolforge/dashboard.json', json_encode($snapshot));
$withoutNews = renderHomepageFixture($private . '/public_html/index.php', 'zh-CN');
expectHomepage(!str_contains($withoutNews, 'id="homepage-news_history"')
    && substr_count($withoutNews, 'class="extract-attribution"') === 3,
    'Confirmed empty news and history omit the complete section and its metadata.');
$snapshot['backlog']['groups'][] = array_replace($backlog['groups'][0], [
    'key' => 'empty', 'section_key' => 'empty', 'count' => 0, 'items' => [],
]);
file_put_contents($fixture . '/cache/toolforge/dashboard.json', json_encode($snapshot));
$withoutEmptyTask = renderHomepageFixture($private . '/public_html/index.php', 'zh-CN');
expectHomepage(!str_contains($withoutEmptyTask, 'task=empty') && !str_contains($withoutEmptyTask, 'id="backlog-section-empty"'),
    'Empty maintenance tasks and their empty containing sections are omitted.');
$snapshot['homepage'] = array_fill_keys(array_keys($snapshot['homepage']), []);
$snapshot['pageviews']['items'] = [];
$snapshot['backlog']['total'] = 0;
$snapshot['backlog']['groups'][0]['count'] = 0;
$snapshot['backlog']['groups'][0]['items'] = [];
file_put_contents($fixture . '/cache/toolforge/dashboard.json', json_encode($snapshot));
$empty = renderHomepageFixture($private . '/public_html/index.php', 'zh-CN');
foreach (['pageviews', 'recognized', 'dyk', 'news_history'] as $key) {
    expectHomepage(!str_contains($empty, 'id="homepage-' . $key . '"'), 'All confirmed-empty article sections are omitted: ' . $key);
}
expectHomepage(!str_contains($empty, 'id="backlog"'), 'An entirely empty maintenance catalogue is omitted.');
expectHomepage(!str_contains($empty, 'id="homepage"'), 'No empty parent heading remains when all article sections are empty.');
$snapshot['homepage']['news'] = null;
$snapshot['homepage']['dyk'] = null;
$snapshot['pageviews'] = null;
$snapshot['backlog'] = null;
file_put_contents($fixture . '/cache/toolforge/dashboard.json', json_encode($snapshot));
$unavailable = renderHomepageFixture($private . '/public_html/index.php', 'zh-CN');
foreach (['pageviews', 'dyk', 'news_history'] as $key) {
    expectHomepage(str_contains($unavailable, 'id="homepage-' . $key . '"'), 'Unavailable sources remain distinct from known empty: ' . $key);
}
expectHomepage(str_contains($unavailable, 'id="backlog"'), 'Unavailable maintenance data retains its explicit state.');
echo "Toolforge homepage render checks passed.\n";
