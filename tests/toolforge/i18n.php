<?php
/** Verify browser negotiation and complete interface translations. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/src/toolforge/i18n.php';
require dirname(__DIR__, 2) . '/src/toolforge/status.php';

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
    ['zh', 'zh-hans'], ['zh-CN', 'zh-hans'], ['zh-SG', 'zh-hans'],
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
    ['en;q=1.5,zh;q=0.4', 'zh-hans'],
    ['en;q=0.7;q=1,zh-TW;q=0.2', 'zh-hant'],
    ['zh-HK;q=0.000,en;q=0.001', 'en'],
    ['zh-CN;q=0.4, *;q=0.2', 'zh-hans'],
    ['../../en,zh-TW;q=0.2', 'zh-hant'],
] as [$header, $expected]) {
    expectUiSame($expected, selectUiLocale($header), 'Weighted browser preference: ' . $header);
}
expectUiSame('zh-Hans', uiLanguageTag('zh-hans'), 'Simplified HTML language');
expectUiSame('zh-Hant', uiLanguageTag('zh-hant'), 'Traditional HTML language');
expectUiSame('en', uiLanguageTag('en'), 'English HTML language');

$observedLanguages = explode(' ', 'ar ast az be bs ca ckb cs da de el en es fa fi fr gl he hu hy id it ja ka ko lt ml mr ms mzn nl no pam pl pt ro ru sh sl sq sr sv th tr uk uz vi zh');
foreach ($observedLanguages as $language) {
    foreach (['en', 'zh-hans', 'zh-hant'] as $locale) {
        $key = 'language.' . $language;
        expectUiSame(true, isset(uiCatalog()[$key][$locale]), 'Current Wikipedia language has a localized name: ' . $language . ' / ' . $locale);
        expectUiSame(false, uiTextFor($locale, $key) === $language, 'Current language names do not fall back to a code: ' . $language . ' / ' . $locale);
    }
}
expectUiSame('Asturian', uiTextFor('en', 'language.ast'), 'CLDR English language name');
expectUiSame('阿斯图里亚斯语', uiTextFor('zh-hans', 'language.ast'), 'CLDR Simplified Chinese language name');
expectUiSame('阿斯圖里亞文', uiTextFor('zh-hant', 'language.ast'), 'CLDR Traditional Chinese language name');

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
expectUiSame('他山之石', uiTextFor('zh-hans', 'quality.heading'), 'Simplified interface text');
expectUiSame('他山之石', uiTextFor('zh-hant', 'quality.heading'), 'Traditional interface text');
expectUiSame('Across the wikis', uiTextFor('en', 'quality.heading'), 'English interface text');
expectUiSame('Recorded appearances: 12', uiTextFor('en', 'homepage.count', ['count' => 12]), 'Plain text interpolation');
expectUiSame('Not completed yet', uiTextFor('en', 'state.Deferred'), 'Deferred is described as an incomplete run');
expectUiSame('暂未完成', uiTextFor('zh-hans', 'state.Deferred'), 'Simplified incomplete state');
expectUiSame('暫未完成', uiTextFor('zh-hant', 'state.Deferred'), 'Traditional incomplete state');

$_SERVER['HTTP_ACCEPT_LANGUAGE'] = 'zh-TW';
expectUiSame('1分3秒', uiDuration('1 min 3 sec'), 'Localized whole-second long duration');
expectUiSame('0.25秒', uiDuration('0.25 sec'), 'Localized short duration retains precision');
expectUiSame('—', uiDuration(null), 'Missing duration remains unknown');
expectUiSame('?language=ru', uiUrl('', ['language' => 'ru']), 'Article filters leave interface selection to the browser');
expectUiSame('/logs/', uiUrl('/logs/'), 'Log links leave interface selection to the browser');
expectUiSame('unlisted-code', uiLanguageName('unlisted-code'), 'Unknown language codes remain source data');

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
    dashboardRoutineLinks($routine, $name);
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

echo "Toolforge interface language checks passed.\n";
