<?php
/** Verify snapshot availability, limits, URL restrictions, and unknown observations. */
declare(strict_types=1);

require __DIR__ . '/status.php';
require dirname(__DIR__, 2) . '/src/webservice/project.php';

$snapshotDirectory = fixtureDirectory($fixture . '/cache/toolforge');
$snapshotPath = $snapshotDirectory . '/dashboard.json';
$data = [
    'period' => ['label' => '2026-10-18', 'start' => '2026-10-18', 'end' => '2026-10-19'],
    'updated_at' => '2026-10-18T00:00:00+00:00',
    'schema' => 2,
    'reports' => array_fill_keys(['recognized', 'dyk', 'pageviews', 'news_history', 'backlog'], [
        'config_id' => str_repeat('a', 64), 'refresh_interval_seconds' => 86400,
        'last_success_at' => '2026-10-18T00:00:00Z', 'last_attempt_at' => '2026-10-18T00:00:00Z',
        'failed' => false,
    ]),
    'homepage' => ['featured' => [], 'dyk' => null, 'good' => [], 'news' => [], 'history' => [], 'upcoming' => null],
    'sources' => [], 'errors' => [],
];
file_put_contents($snapshotPath, json_encode($data));
expectSame($data, projectDashboard($snapshotPath), 'Known empty and unknown observations remain distinct');
$dailyViews = [
    'date' => '2026-10-17', 'limit' => 10, 'scope' => 'site_top_1000',
    'items' => [
        ['title' => '熱門遊戲', 'url' => 'https://zh.wikipedia.org/wiki/Example', 'views' => 1234, 'rank' => 1],
        ['title' => '另一遊戲', 'url' => 'https://zh.wikipedia.org/wiki/Other', 'views' => 0, 'rank' => 2],
    ],
    'source_url' => 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/zh.wikipedia.org/all-access/2026/10/17',
];
foreach ([null, array_replace($dailyViews, ['items' => []]), $dailyViews] as $views) {
    $data['pageviews'] = $views;
    file_put_contents($snapshotPath, json_encode($data));
    expectSame($data, projectDashboard($snapshotPath), 'Daily views preserve unavailable, measured empty, and zero-valued records');
}
$staleData = $data;
$staleData['updated_at'] = '2026-10-28T12:00:00Z';
$staleData['period'] = ['label' => 'New display window', 'start' => '2026-10-27', 'end' => '2026-10-29'];
file_put_contents($snapshotPath, json_encode($staleData));
expectSame($staleData, projectDashboard($snapshotPath), 'An outage preserves old measured pageviews against their actual successful source date');
$data['sources'][] = ['label' => 'Wikimedia daily top pageviews', 'url' => $dailyViews['source_url']];
file_put_contents($snapshotPath, json_encode($data));
expectSame($data, projectDashboard($snapshotPath), 'The exact official daily endpoint is allowed among audited sources');
foreach ([null, 1, 1000] as $siteRank) {
    $ranked = $dailyViews;
    $ranked['items'][0]['site_rank'] = $siteRank;
    expectSame(true, validPageviews($ranked, '2026-10-18'), 'Observed site ranks preserve their official bounds and missing values');
}
foreach ([0, 1001, '4', true, 1.5] as $siteRank) {
    $ranked = $dailyViews;
    $ranked['items'][0]['site_rank'] = $siteRank;
    expectSame(false, validPageviews($ranked, '2026-10-18'), 'Malformed or out-of-coverage site ranks are rejected');
}
$backlogItem = ['page_id' => 123, 'title' => '维护条目', 'url' => 'https://zh.wikipedia.org/wiki/Example'];
foreach ([[], ['assessment_class' => null, 'assessment_importance' => null],
    ['assessment_class' => '乙表', 'assessment_importance' => '低'],
    ['assessment_class' => 'New class', 'assessment_importance' => 'New importance']] as $assessment) {
    expectSame(true, validAssessments($assessment), 'Optional raw assessment observations are retained without guessing missing values');
}
foreach (['', ' ', 1, true, [], "B\n", str_repeat('x', 193)] as $invalidLabel) {
    foreach (['assessment_class', 'assessment_importance'] as $field) {
        expectSame(false, validAssessments([$field => $invalidLabel]), 'Assessment labels must be bounded nonempty plain strings');
        expectSame(false, validArticle(['title' => 'Game', 'url' => 'https://zh.wikipedia.org/wiki/Game', 'date' => null, $field => $invalidLabel]),
            'Dated article validation rejects malformed assessment metadata');
        $invalidViews = $dailyViews;
        $invalidViews['items'][0][$field] = $invalidLabel;
        expectSame(false, validPageviews($invalidViews, '2026-10-18'), 'Pageview validation rejects malformed assessment metadata');
    }
}
$backlogGroup = ['key' => 'unreferenced', 'category' => '缺少来源的条目',
    'source_url' => 'https://zh.wikipedia.org/wiki/Category:%E7%BC%BA%E5%B0%91%E6%9D%A5%E6%BA%90%E7%9A%84%E6%9D%A1%E7%9B%AE',
    'count' => 1, 'items' => [$backlogItem]];
$backlog = ['observed_at' => '2026-10-18T00:00:00Z', 'total' => 1, 'groups' => [$backlogGroup]];
foreach ([null, ['observed_at' => '2026-10-18T00:00:00Z', 'total' => 0,
    'groups' => [array_replace($backlogGroup, ['count' => 0, 'items' => []])]], $backlog] as $maintenance) {
    $data['backlog'] = $maintenance;
    file_put_contents($snapshotPath, json_encode($data));
    expectSame($data, projectDashboard($snapshotPath), 'Maintenance preserves unavailable, known empty, and complete lists');
}
unset($data['backlog']);
$shared = array_replace($backlogGroup, ['key' => 'references']);
expectSame(true, validBacklog(array_replace($backlog, ['groups' => [$backlogGroup, $shared]])),
    'A shared article counts once in the total and once within each issue list');
$detailedItem = $backlogItem + ['assessment_class' => '初', 'assessment_importance' => '低',
    'issues' => ['unreferenced', 'references'], 'oldest_issue_month' => '2020-03'];
$detailedGroups = [array_replace($backlogGroup, ['items' => [$detailedItem]]),
    array_replace($shared, ['items' => [$detailedItem]])];
$detailedBacklog = array_replace($backlog, ['groups' => $detailedGroups, 'issue_total' => 2]);
expectSame(true, validBacklog($detailedBacklog), 'Detailed lists retain assessments, all known issues, and an observed category month');
foreach ([['issues' => null], ['issues' => []], ['issues' => ['references']],
    ['issues' => ['unreferenced', 'missing_task']], ['issues' => ['unreferenced', 'unreferenced']],
    ['issues' => ['unreferenced', 1]], ['oldest_issue_month' => '2026-13'],
    ['oldest_issue_month' => '2026-01-01'], ['oldest_issue_month' => ''],
    ['assessment_class' => []], ['assessment_importance' => "低\n"]] as $change) {
    $invalid = $detailedBacklog;
    $invalid['groups'][0]['items'][0] = array_replace($detailedItem, $change);
    expectSame(false, validBacklog($invalid), 'Detailed maintenance records require valid metadata and existing distinct issue identities');
}
expectSame(false, validBacklog(array_replace($detailedBacklog, ['issue_total' => 3])), 'The total issue count matches the category workloads');
$configuredGroup = array_replace($backlogGroup, ['key' => 'translation_help',
    'source_url' => 'https://zh.wikipedia.org/wiki/Template:Proofreader_needed',
    'section_key' => 'local_tasks', 'section_label' => ['en' => 'Local tasks', 'zh-hans' => '本地任务'],
    'label' => '翻译校对']);
$configuredBacklog = array_replace($backlog, ['groups' => [$configuredGroup]]);
expectSame(true, validBacklog($configuredBacklog), 'Configured task names and template source links remain browsable');
foreach ([['section_key' => '<script>'], ['section_label' => ['fr' => 'Non pris en charge']],
    ['label' => ''], ['label' => ['en' => []]], ['section_label' => null]] as $changes) {
    expectSame(false, validBacklog(array_replace($backlog,
        ['groups' => [array_replace($configuredGroup, $changes)]])), 'Invalid configurable labels and identities are rejected');
}
expectSame(false, validBacklog(array_replace($backlog, ['groups' => [$configuredGroup, $backlogGroup]])),
    'Snapshots cannot mix configured and legacy group metadata');
foreach ([
    ['observed_at' => '2026-10-18'], ['total' => 2], ['total' => '1'], ['groups' => null],
    ['groups' => [$backlogGroup, $backlogGroup]],
    ['groups' => [array_replace($backlogGroup, ['count' => 2])]],
    ['groups' => [array_replace($backlogGroup, ['key' => '<script>'])]],
    ['groups' => [array_replace($backlogGroup, ['source_url' => 'https://en.wikipedia.org/wiki/Category:Example'])]],
    ['groups' => [array_replace($backlogGroup, ['source_url' => 'https://zh.wikipedia.org/wiki/Example'])]],
    ['groups' => [array_replace($backlogGroup, ['count' => 2, 'items' => [$backlogItem, $backlogItem]])]],
    ['groups' => [array_replace($backlogGroup, ['items' => [array_replace($backlogItem, ['page_id' => 0])]])]],
    ['groups' => [array_replace($backlogGroup, ['items' => [array_replace($backlogItem, ['url' => 'https://en.wikipedia.org/wiki/Example'])]])]],
] as $changes) {
    expectSame(false, validBacklog(array_replace($backlog, $changes)),
        'Maintenance rejects inconsistent counts, repeated identities, unsafe keys, and non-Chinese sources');
}
foreach ([
    ['date' => '2026-10-18'], ['date' => '2026-10-10'], ['limit' => 11], ['scope' => 'all_project_pages'],
    ['source_url' => 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/zh.wikipedia.org/all-access/2026/10/16'],
    ['source_url' => $dailyViews['source_url'] . '?redirect=evil'],
    ['items' => null], ['items' => array_fill(0, 11, $dailyViews['items'][0])],
    ['items' => [array_replace($dailyViews['items'][0], ['rank' => 2])]],
    ['items' => [array_replace($dailyViews['items'][0], ['views' => -1])]],
    ['items' => [array_replace($dailyViews['items'][0], ['views' => '1234'])]],
    ['items' => [array_replace($dailyViews['items'][0], ['url' => 'https://en.wikipedia.org/wiki/Example'])]],
    ['items' => [$dailyViews['items'][0], array_replace($dailyViews['items'][0], ['rank' => 2])]],
] as $changes) {
    $invalid = $data;
    $invalid['pageviews'] = array_replace($dailyViews, $changes);
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Daily views require exact coverage, date, source, ranks, and observed counts');
}
$invalid = $data;
$invalid['sources'][0]['url'] = 'https://evil.example/api/rest_v1/metrics/pageviews/top/zh.wikipedia.org/all-access/2026/10/17';
file_put_contents($snapshotPath, json_encode($invalid));
expectSame(null, projectDashboard($snapshotPath), 'Official endpoint support does not allow arbitrary API hosts');
$data['reports']['recognized']['period'] = $data['period'];
$scheduled = ['title' => 'Scheduled article', 'url' => 'https://zh.wikipedia.org/wiki/Example',
    'date' => '2026-10-19', 'date_kind' => 'scheduled', 'kind' => 'fa'];
foreach ([null, [], [$scheduled], [array_replace($scheduled, ['date' => '2026-11-17', 'kind' => 'ga'])]] as $upcoming) {
    $data['homepage']['upcoming'] = $upcoming;
    file_put_contents($snapshotPath, json_encode($data));
    expectSame($data, projectDashboard($snapshotPath), 'Optional schedules retain unknown, empty, and in-window observations');
}
foreach ([
    ['date' => null], ['date' => '2026-10-18'], ['date' => '2026-11-18'],
    ['kind' => 'news'], ['date_kind' => 'appearance'],
] as $changes) {
    $invalid = $data;
    $invalid['homepage']['upcoming'] = [array_replace($scheduled, $changes)];
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Schedules require a future 30-day date and recognized content kind');
}
$data['errors'][] = ['section' => 'dyk', 'message' => str_repeat('x', 70000)];
file_put_contents($snapshotPath, json_encode($data));
expectSame($data, projectDashboard($snapshotPath), 'Presentation snapshots may exceed the daily-log read bound');
$article = ['title' => '<script>alert(1)</script>', 'url' => 'https://zh.wikipedia.org/wiki/Example', 'date' => null];
$data['homepage']['dyk'] = [$article];
file_put_contents($snapshotPath, json_encode($data));
expectSame($data, projectDashboard($snapshotPath), 'Text is retained for HTML escaping by the renderer');
foreach (['javascript:alert(1)', 'https://evil.example/wiki/Example', 'https://user@zh.wikipedia.org/wiki/Example', 'https://zh.wikipedia.org:443/wiki/Example', "https://zh.wikipedia.org/wiki/Example\n"] as $url) {
    $invalid = $data;
    $invalid['homepage']['dyk'][0]['url'] = $url;
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'An unsafe or non-Wikipedia source URL is rejected');
}
foreach (['2026-02-30', '2026-13-01'] as $date) {
    $invalid = $data;
    $invalid['period']['start'] = $date;
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Invalid calendar dates do not reach the renderer');
}
file_put_contents($snapshotPath, str_repeat('x', SNAPSHOT_READ_LIMIT + 1));
expectSame(null, projectDashboard($snapshotPath), 'Oversized snapshots are rejected');
file_put_contents($snapshotPath, '{malformed');
expectSame(null, projectDashboard($snapshotPath), 'Malformed snapshots are unavailable');
unlink($snapshotPath);
expectSame(null, projectDashboard($snapshotPath), 'An absent snapshot is unavailable');
file_put_contents($fixture . '/snapshot.json', json_encode($data));
symlink($fixture . '/snapshot.json', $snapshotPath);
expectSame(null, projectDashboard($snapshotPath), 'Snapshots cannot be symlinks');

expectSame(false, validReportMetadata(['config_id' => 'invalid']), 'Incomplete refresh metadata is rejected');
$metadata = $data['reports']['dyk'];
foreach (['refresh_interval_seconds' => 0, 'config_id' => 'wrong', 'failed' => 'yes',
    'last_success_at' => '2026-10-19T00:00:00Z'] as $key => $value) {
    expectSame(false, validReportMetadata(array_replace($metadata, [$key => $value])), 'Report metadata retains validated timestamps and configuration identity');
}
echo "Toolforge project snapshot checks passed.\n";
