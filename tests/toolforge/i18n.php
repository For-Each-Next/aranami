<?php
/** Verify browser negotiation and complete interface translations. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/src/webservice/i18n.php';
require dirname(__DIR__, 2) . '/src/webservice/status.php';

/** Fail visibly even when the PHP runtime disables built-in assertions. */
function expectUiSame(mixed $expected, mixed $actual, string $description): void
{
    if ($expected !== $actual) {
        throw new RuntimeException($description . "\nExpected: " . var_export($expected, true)
            . "\nActual: " . var_export($actual, true));
    }
}

foreach ([
    ['', 'en'], ['fr,de;q=0.9', 'en'], ['*', 'en'],
    ['en-US,en;q=0.9', 'en'], ['EN-gb', 'en'],
    ['zh', 'zh'], ['zh-CN', 'zh-hans'], ['zh-SG', 'zh-hans'], ['zh-MY', 'zh-hans'],
    ['zh-Hans', 'zh-hans'], ['zh-Hans-TW', 'zh-hans'],
    ['zh-TW', 'zh-hant'], ['zh-HK', 'zh-hant'], ['zh-MO', 'zh-hant'],
    ['zh-Hant', 'zh-hant'], ['zh-Hant-CN', 'zh-hant'],
    ['en;q=0.3,zh-TW;q=0.9,zh-CN;q=0.8', 'zh-hant'],
    ['zh-TW;q=0.8,zh-CN;q=0.9', 'zh-hans'],
    ['fr;q=1,en;q=0.7,zh;q=0.5', 'en'],
    ['zh-TW;q=0.8,en;q=0.8', 'zh-hant'],
    ['en;q=0.8,zh-TW;q=0.8', 'en'],
    ['zh-HK;q=0,en;q=0.1', 'en'],
    ['en;q=0,zh-CN;q=0.5', 'zh-hans'],
    ['en;q=invalid,zh-TW;q=0.5', 'zh-hant'],
    ['en;q=1.5,zh;q=0.4', 'zh'],
    ['en;q=0.7;q=1,zh-TW;q=0.2', 'zh-hant'],
    ['zh-HK;q=0.000,en;q=0.001', 'en'],
    ['zh-CN;q=0.4, *;q=0.2', 'zh-hans'],
    ['../../en,zh-TW;q=0.2', 'zh-hant'],
] as [$header, $expected]) {
    expectUiSame($expected, selectUiLocale($header), 'Weighted browser preference: ' . $header);
}
expectUiSame('zh-Hans', uiLanguageTag('zh-hans'), 'Simplified HTML language');
expectUiSame('zh', uiLanguageTag('zh'), 'Generic Chinese stays unspecified');
expectUiSame('条目', uiTextFor('zh', 'column.article'), 'Generic Chinese uses authored Chinese interface copy');
expectUiSame('zh-Hant', uiLanguageTag('zh-hant'), 'Traditional HTML language');
expectUiSame('en', uiLanguageTag('en'), 'English HTML language');

foreach (uiCatalog() as $key => $translations) {
    expectUiSame(['en', 'zh-hans', 'zh-hant'], array_keys($translations), 'Every message has three locales: ' . $key);
    $expectedParameters = null;
    foreach ($translations as $locale => $message) {
        if (!is_string($message) || $message === '') {
            throw new RuntimeException('Empty interface message: ' . $key . ' / ' . $locale);
        }
        preg_match_all('/\{([a-z_]+)\}/', $message, $matches);
        $parameters = $matches[1];
        sort($parameters);
        $expectedParameters ??= $parameters;
        expectUiSame($expectedParameters, $parameters, 'Translation placeholders match: ' . $key);
    }
}
expectUiSame('12 articles', uiTextFor('en', 'backlog.count', ['count' => 12]), 'Plain text interpolation');
expectUiSame('Waiting to retry', uiTextFor('en', 'state.Deferred'), 'Deferred is described as waiting for a retry');
expectUiSame('待重试', uiTextFor('zh-hans', 'state.Deferred'), 'Simplified retry state');
expectUiSame('待重試', uiTextFor('zh-hant', 'state.Deferred'), 'Traditional retry state');

$_SERVER['HTTP_ACCEPT_LANGUAGE'] = 'zh-TW';
expectUiSame('1分3秒', uiDuration('1 min 3 sec'), 'Localized whole-second long duration');
expectUiSame('0.25秒', uiDuration('0.25 sec'), 'Localized short duration retains precision');
expectUiSame('—', uiDuration(null), 'Missing duration remains unknown');
expectUiSame('6分15秒…', uiRunningDuration('2026-10-18T00:00:00.000000Z', 1792281975), 'Running durations show elapsed whole minutes and seconds with an ellipsis');
expectUiSame('15秒…', uiRunningDuration('2026-10-18T00:00:00.000000Z', 1792281615), 'Short running durations use whole seconds');
expectUiSame('0秒…', uiRunningDuration('2026-10-18T00:00:00.000000Z', 1792281599), 'Clock skew never produces a negative running duration');
expectUiSame('/logs/', uiUrl('/logs/'), 'Log links leave interface selection to the browser');

foreach ([
    'dyks' => ['WikiProject:电子游戏/新条目推荐'],
    'new_pages' => ['WikiProject:电子游戏/新进条目/关键词筛选'],
    'assessment_lists' => [
        'WikiProject:电子游戏/认证条目/Bplus', 'WikiProject:电子游戏/认证条目/BPAN',
        'WikiProject:电子游戏/认证条目/A', 'WikiProject:电子游戏/认证条目/AL',
        'WikiProject:电子游戏/认证条目/ACC', 'WikiProject:电子游戏/认证条目/PPR',
    ],
    'pageviews' => ['WikiProject:电子游戏/热门条目'],
    'enwp_key_articles' => [
        'WikiProject:电子游戏/数据库报告/英文维基百科重要条目',
        'WikiProject:电子游戏/数据库报告/英文维基百科优质条目',
    ],
    'pexbot' => ['WikiProject:电子游戏/数据库报告'],
] as $routine => $targets) {
    $name = uiText('routine.' . $routine);
    ob_start();
    dashboardRoutineLinks($routine, $name, [$routine => ['pages' => $targets, 'prefixes' => []]]);
    $markup = ob_get_clean();
    preg_match_all('/<a href="([^"]+)" title="([^"]+)" aria-label="([^"]+)">([^<]+)<\/a>/', $markup, $links, PREG_SET_ORDER);
    expectUiSame(count($targets), count($links), 'Every configured report has one link: ' . $routine);
    foreach ($targets as $index => $title) {
        $link = $links[$index];
        expectUiSame('https://zh.wikipedia.org/wiki/' . $title, rawurldecode($link[1]), 'Report links match configured destinations');
        expectUiSame(true, str_contains($link[2], $name) && str_contains($link[2], $title), 'Report tooltips identify the localized job and destination');
        expectUiSame($link[2], $link[3], 'Report links have descriptive accessible names');
        expectUiSame(count($targets) === 1 ? $name : (string) ($index + 1), $link[4], 'Single reports link the name and multiple reports link numbers');
    }
    $expected = count($targets) === 1 ? $name : $name . '（' . implode(' ', range(1, count($targets))) . '）';
    expectUiSame($expected, strip_tags($markup), 'Multiple report links follow their job name inline');
}
ob_start();
dashboardRoutineLinks('unlisted', '<Unknown & routine>');
expectUiSame('&lt;Unknown &amp; routine&gt;', ob_get_clean(), 'Unconfigured routine names remain escaped plain text');

$uptime = ['state' => 'running', 'started_at' => '2026-10-16T08:56:00Z',
    'updated_at' => '2026-10-18T12:00:00Z'];
$clock = new DateTimeImmutable('2026-10-18T12:00:30Z');
foreach ([
    ['en', 'Aranami (0.6.0) uptime: 2d 3h 4m · since 2026-10-16 08:56 UTC', 'Aranami (0.6.0) uptime: unavailable'],
    ['zh-CN', 'Aranami (0.6.0) 已连续运行2天3小时4分钟（始于2026-10-16 08:56 UTC）', 'Aranami (0.6.0) 连续运行时间：未获取'],
    ['zh-TW', 'Aranami (0.6.0) 已連續運行2天3小時4分鐘（始於2026-10-16 08:56 UTC）', 'Aranami (0.6.0) 連續運行時間：未取得'],
] as [$language, $label, $unknown]) {
    $locale = selectUiLocale($language);
    expectUiSame($label, uiTextFor($locale, 'footer.uptime', [
        'version' => '0.6.0', 'days' => 2, 'hours' => 3, 'minutes' => 4, 'since' => '2026-10-16 08:56',
    ]), 'Continuous runtime and UTC start are localized');
    expectUiSame($unknown, uiTextFor($locale, 'footer.uptime_unknown', ['version' => '0.6.0']), 'Unavailable runtime has a localized explanation');
}
$version = ['version' => sourceVersion()];
$label = uiText('footer.uptime', $version + ['days' => 2, 'hours' => 3, 'minutes' => 4, 'since' => '2026-10-16 08:56']);
expectUiSame($label, dashboardUptime($uptime, $clock), 'Uptime is calculated from the session start rather than heartbeat time');
expectUiSame($label, dashboardUptime(array_replace($uptime, ['state' => 'paused']), $clock),
    'Pausing report scheduling does not reset a live monitor session');
foreach ([
    ['state' => 'missing'], ['state' => 'stopped'], ['state' => 'stale'],
    ['started_at' => null], ['started_at' => 'invalid'],
    ['started_at' => '2026-10-19T00:00:00Z'],
    ['updated_at' => '2026-10-18T11:58:29Z'],
    ['updated_at' => '2026-10-18T12:01:00Z'],
] as $change) {
    expectUiSame(uiText('footer.uptime_unknown', $version), dashboardUptime(array_replace($uptime, $change), $clock),
        'Missing, stopped, stale, malformed, and future observations do not invent continuous runtime');
}
expectUiSame(uiText('footer.uptime', $version + ['days' => 2, 'hours' => 3, 'minutes' => 5, 'since' => '2026-10-16 08:56']),
    dashboardUptime(array_replace($uptime, ['updated_at' => '2026-10-18T12:01:00Z']),
        new DateTimeImmutable('2026-10-18T12:01:30Z')),
    'Heartbeat updates advance elapsed time while retaining the original session start');
expectUiSame(uiText('footer.uptime', $version + ['days' => 0, 'hours' => 0, 'minutes' => 0, 'since' => '2026-10-18 12:00']),
    dashboardUptime(array_replace($uptime, ['started_at' => '2026-10-18T12:00:00Z']), $clock),
    'A newly recorded restart resets elapsed runtime without rounding up a minute');
echo "Toolforge interface language checks passed.\n";
