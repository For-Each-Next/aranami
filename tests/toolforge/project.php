<?php
/** Verify snapshot availability, limits, URL restrictions, and unknown observations. */
declare(strict_types=1);

require __DIR__ . '/status.php';
require dirname(__DIR__, 2) . '/src/toolforge/project.php';

$snapshotDirectory = fixtureDirectory($fixture . '/cache/toolforge');
$snapshotPath = $snapshotDirectory . '/dashboard.json';
$data = [
    'period' => ['label' => '2026-10-18', 'start' => '2026-10-18', 'end' => '2026-10-19'],
    'updated_at' => '2026-10-18T00:00:00+00:00',
    'quality' => null,
    'homepage' => ['featured' => [], 'dyk' => null, 'good' => [], 'news' => [], 'history' => []],
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
$data['sources'][] = ['label' => 'Wikimedia daily top pageviews', 'url' => $dailyViews['source_url']];
file_put_contents($snapshotPath, json_encode($data));
expectSame($data, projectDashboard($snapshotPath), 'The exact official daily endpoint is allowed among audited sources');
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
$data['quality'] = [
    'total' => 1, 'languages' => 1, 'scope' => 'Video games',
    'observed_at' => '2026-10-18T00:00:00Z',
    'counts' => [['site' => 'enwiki', 'language' => 'en', 'status' => 'GA', 'count' => 1]],
    'items' => [['item' => 'Q1', 'site' => 'enwiki', 'language' => 'en', 'status' => 'GA',
        'title' => 'English title', 'url' => 'https://en.wikipedia.org/wiki/Example',
        'zh_title' => '中文條目', 'zh_url' => 'https://zh.wikipedia.org/wiki/Example']],
];
file_put_contents($snapshotPath, json_encode($data));
expectSame($data, projectDashboard($snapshotPath), 'Foreign content retains its Chinese companion link');
$recent = $data['quality']['items'][0] + ['badge_observed_at' => '2026-10-18T00:00:00Z'];
foreach ([[], [$recent], [array_replace($recent, ['badge_observed_at' => '2026-10-18T00:00:00+00:00'])]] as $observations) {
    $data['quality']['recent_items'] = $observations;
    file_put_contents($snapshotPath, json_encode($data));
    expectSame($data, projectDashboard($snapshotPath), 'Recent badge observations preserve empty baselines and explicit UTC times');
}
foreach ([null, '2026-02-30T00:00:00Z', '2026-10-18', '2026-10-18T08:00:00+08:00', 1] as $timestamp) {
    $invalid = $data;
    $invalid['quality']['recent_items'][0]['badge_observed_at'] = $timestamp;
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Badge observation times must be actual explicit UTC timestamps');
}
foreach ([null, 'GA', 'B', 'C'] as $grade) {
    $data['quality']['items'][0]['zh_class'] = $grade;
    file_put_contents($snapshotPath, json_encode($data));
    expectSame($data, projectDashboard($snapshotPath), 'Chinese companion classes retain recognized and ordinary project grades');
}
foreach ([false, 1, '', str_repeat('x', 65), "B\n"] as $grade) {
    $invalid = $data;
    $invalid['quality']['items'][0]['zh_class'] = $grade;
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Chinese companion classes require a bounded printable string or null');
}
$chinese = array_replace($data['quality']['items'][0], [
    'site' => 'zhwiki', 'language' => 'zh', 'title' => '中文條目', 'url' => 'https://zh.wikipedia.org/wiki/Example',
]);
expectSame(true, validQualityArticle($chinese), 'Chinese quality records use a consistent Chinese identity and domain');
$allLanguages = $data;
$allLanguages['quality']['recent_items'][] = $chinese + ['badge_observed_at' => '2026-10-18T00:00:01Z'];
file_put_contents($snapshotPath, json_encode($allLanguages));
expectSame($allLanguages, projectDashboard($snapshotPath), 'All-language observations can include Chinese recognized articles');
expectSame(false, validQualityArticle(array_replace($chinese, ['site' => 'enwiki'])),
    'Chinese quality records require the Chinese site identity');
expectSame(false, validPopularArticle($chinese + ['language_count' => 1, 'articles' => [$chinese]]),
    'The default across-wiki ranking counts foreign editions only');
$data['quality']['language_totals'] = [['site' => 'enwiki', 'language' => 'en', 'count' => 1]];
file_put_contents($snapshotPath, json_encode($data));
expectSame($data, projectDashboard($snapshotPath), 'Optional distinct article totals survive snapshot validation');
$popular = $data['quality']['items'][0];
$popular['language_count'] = 1;
$popular['articles'] = $data['quality']['items'];
$data['quality']['popular_items'] = [$popular];
file_put_contents($snapshotPath, json_encode($data));
expectSame($data, projectDashboard($snapshotPath), 'Ranked items retain their primary article and distinct foreign links');
foreach ([
    ['language_count' => 0], ['language_count' => 2], ['language_count' => '1'],
    ['articles' => null], ['articles' => []],
    ['articles' => [array_replace($popular['articles'][0], ['item' => 'Q2'])]],
    ['language_count' => 2, 'articles' => [$popular['articles'][0], $popular['articles'][0]]],
] as $changes) {
    $invalid = $data;
    $invalid['quality']['popular_items'][0] = array_replace($popular, $changes);
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Ranked items require consistent distinct same-item editions');
}
$invalid = $data;
$invalid['quality']['popular_items'] = array_fill(0, 11, $popular);
file_put_contents($snapshotPath, json_encode($invalid));
expectSame(null, projectDashboard($snapshotPath), 'The global article ranking is bounded to ten items');
foreach ([null, '1', -1, true] as $count) {
    $invalid = $data;
    $invalid['quality']['language_totals'][0]['count'] = $count;
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Distinct language totals require nonnegative integer counts');
}
foreach ([null, 'not a list', ['site' => 'enwiki', 'language' => 'en', 'count' => 1]] as $totals) {
    $invalid = $data;
    $invalid['quality']['language_totals'] = $totals;
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Present language totals must be a list of records');
}
$invalid = $data;
$invalid['quality']['counts'][0]['status'] = 'unsupported';
file_put_contents($snapshotPath, json_encode($invalid));
expectSame(null, projectDashboard($snapshotPath), 'Unsupported recognition is rejected');
expectSame(true, validQualityType('recommended'), 'Recommended is a supported common recognition tier');
foreach (['https://www.wikidata.org/wiki/Q1', 'https://zh.wikipedia.org/wiki/Example'] as $url) {
    $invalid = $data;
    $invalid['quality']['items'][0]['url'] = $url;
    file_put_contents($snapshotPath, json_encode($invalid));
    expectSame(null, projectDashboard($snapshotPath), 'Primary quality articles must belong to a foreign Wikipedia');
}
$invalid = $data;
$invalid['quality']['items'][0]['zh_url'] = 'https://en.wikipedia.org/wiki/Example';
file_put_contents($snapshotPath, json_encode($invalid));
expectSame(null, projectDashboard($snapshotPath), 'Chinese companion links must point to Chinese Wikipedia');
$invalid = $data;
$invalid['quality']['items'][0]['zh_title'] = null;
file_put_contents($snapshotPath, json_encode($invalid));
expectSame(null, projectDashboard($snapshotPath), 'Chinese companion fields must be known or null together');
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

echo "Toolforge project snapshot checks passed.\n";
