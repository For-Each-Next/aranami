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
    'views' => 723, 'rank' => 1, 'site_rank' => 681, 'assessment_class' => 'GA', 'assessment_importance' => 'High'];
$maintenance = ['page_id' => 17, 'title' => 'Maintenance article', 'url' => 'https://zh.wikipedia.org/wiki/Maintenance',
    'assessment_class' => 'B', 'issues' => ['references'], 'oldest_issue_month' => '2020-03'];
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
    'homepage' => ['featured' => [], 'good' => [], 'upcoming' => [], 'dyk' => [], 'news' => [], 'history' => []],
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
        && !str_contains($html, '他山之石') && !str_contains($html, '<table'), 'Retired datasets, scripts, and table layouts are absent.');
    expectHomepage(str_contains($html, 'minmax(min(18em, 100%), 1fr)'), 'Sibling cards use a responsive eighteen-em minimum.');
    expectHomepage(str_contains($html, $state), 'Routine cards include visible localized retry text.');
    expectHomepage(str_contains($html, $next->format('Y-m-d H:i') . ' UTC'), 'Routine cards show the actual postponed next run.');
    expectHomepage(str_contains($html, '0.5.6.dev1'), 'The footer reads source deployment metadata.');
    $uptime = match ($language) {
        'en' => 'Aranami uptime: 2d 3h 4m',
        'zh-CN' => 'Aranami 已连续运行2天3小时4分钟',
        'zh-TW' => 'Aranami 已連續運行2天3小時4分鐘',
    };
    expectHomepage(str_contains($html, $uptime) && str_contains($html, $monitorStart->format('Y-m-d H:i') . ' UTC'),
        'The footer shows localized runtime since the recorded monitor restart.');
    expectHomepage(substr_count($html, '<footer>') === 1 && substr_count($html, 'class="footer-uptime"') === 1
        && substr_count($html, '0.5.6.dev1') === 1 && substr_count($html, 'href="/logs/"') === 1,
        'The uptime extends the single footer while preserving one version and run-log link.');
    expectHomepage(substr_count($html, 'href="/about/"') === 1, 'The footer links to the general-reader About page.');
    [$pageCount, $updated, $started, $finished, $duration] = match ($language) {
        'en' => ['Page update details (1)', 'Updated', 'Started', 'Finished', '5.000 sec'],
        'zh-CN' => ['页面更新详情（1）', '已更新', '开始', '结束', '5.000秒'],
        'zh-TW' => ['頁面更新詳情（1）', '已更新', '開始', '結束', '5.000秒'],
    };
    expectHomepage(preg_match('/<details class="page-updates">(.*?)<\/details>/s', $html, $updates) === 1,
        'A routine expands into the latest metadata for each destination page.');
    expectHomepage(str_contains($updates[1], $pageCount) && str_contains($updates[1], $updated)
        && str_contains($updates[1], $started . ':') && str_contains($updates[1], $finished . ':')
        && str_contains($updates[1], $duration) && str_contains($updates[1], 'zhwiki')
        && str_contains($updates[1], '&lt;Latest &amp; safe&gt;') && !str_contains($updates[1], '<Latest'),
        'Per-page outcomes, timestamps, duration, and escaped targets are localized: ' . $language);
    expectHomepage(str_contains($updates[1], $now->modify('-5 seconds')->format('Y-m-d H:i:s') . ' UTC')
        && str_contains($updates[1], $now->format('Y-m-d H:i:s') . ' UTC'),
        'Per-page start and finish timestamps retain UTC seconds.');
    expectHomepage(str_contains($html, 'class="backlog-task"') && str_contains($html, 'task=references'), 'Each maintenance task has its own worklist card.');
    preg_match('/href="(https:\/\/pageviews\.wmcloud\.org\/\?[^"]+)"/', $html, $match);
    expectHomepage(isset($match[1]), 'Observed views link to Pageviews Analysis.');
    parse_str(parse_url(html_entity_decode($match[1]), PHP_URL_QUERY), $parameters);
    expectHomepage($parameters === ['project' => 'zh.wikipedia.org', 'platform' => 'all-access', 'agent' => 'user',
        'start' => $day, 'end' => $day, 'pages' => $article['title']], 'View links safely encode the actual source settings, title, and day.');
    expectHomepage(preg_match('/<p class="card-meta site-rank">[^<]*681[^<]*<\/p>/', $html) === 1, 'Article-only site rank is visible plain text.');
    $worklist = renderHomepageFixture($private . '/public_html/maintenance/index.php', $language, ['task' => 'references']);
    expectHomepage(str_contains($worklist, 'class="article-card"') && !str_contains($worklist, '<table')
        && str_contains($worklist, 'sort=class') && str_contains($worklist, '2020-03'), 'Maintenance cards retain assessment sorting and actual category dates.');
}
foreach ([
    ['state' => 'stopped'], ['state' => 'missing'], ['started_at' => null],
    ['updated_at' => $now->modify('-121 seconds')->format('Y-m-d\TH:i:s\Z')],
] as $change) {
    file_put_contents($schedulePath, json_encode(array_replace($schedule, $change)));
    $html = renderHomepageFixture($private . '/public_html/index.php', 'en', ['uncached' => '1']);
    expectHomepage(str_contains($html, 'Aranami uptime: unavailable') && !str_contains($html, 'Aranami uptime: 2d'),
        'The rendered footer never claims continuous runtime for stopped, missing, stale, or unrecorded sessions.');
}
echo "Toolforge homepage render checks passed.\n";
