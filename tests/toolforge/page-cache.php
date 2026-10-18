<?php
/** Verify bounded homepage reuse, input invalidation, and safe cache failures. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/src/toolforge/status.php';
require dirname(__DIR__, 2) . '/src/toolforge/page-cache.php';

/** Fail visibly even when built-in assertions are disabled. */
function expectSame(mixed $expected, mixed $actual, string $description): void
{
    if ($expected !== $actual) {
        throw new RuntimeException($description . "\nExpected: " . var_export($expected, true)
            . "\nActual: " . var_export($actual, true));
    }
}

/** Remove disposable fixtures without traversing links. */
function removeFixture(string $path): void
{
    if (is_link($path) || !is_dir($path)) {
        unlink($path);
        return;
    }
    foreach (new DirectoryIterator($path) as $entry) {
        if (!$entry->isDot()) {
            removeFixture($entry->getPathname());
        }
    }
    rmdir($path);
}

$fixture = sys_get_temp_dir() . '/aranami-page-cache-' . bin2hex(random_bytes(8));
mkdir($fixture . '/cache/toolforge', 0700, true);
mkdir($fixture . '/logs', 0700);
register_shutdown_function(static function () use ($fixture): void {
    removeFixture($fixture);
});
$directory = $fixture . '/cache/toolforge/pages';
$snapshot = $fixture . '/cache/toolforge/dashboard.json';
$status = $fixture . '/logs/aranami-status.json';
$template = $fixture . '/template.php';
file_put_contents($snapshot, '{"snapshot":1}');
file_put_contents($template, '<?php // template');
$inputs = [$snapshot, $template];
$optional = [$status];
$key = homepageCacheKey($inputs, $optional);
expectSame(true, is_string($key), 'A regular snapshot and missing optional status produce a key');
expectSame(true, homepageCacheRequest('GET', [], ''), 'An ordinary GET can reuse HTML');
foreach ([['HEAD', [], ''], ['POST', [], ''], ['GET', ['language' => 'en'], 'language=en'],
    ['GET', ['random' => '1'], 'random=1'], ['GET', ['fragment' => 'quality'], 'fragment=quality'],
    ['GET', [], '&']] as [$method, $query, $queryString]) {
    expectSame(false, homepageCacheRequest($method, $query, $queryString),
        'HEAD, filtered, random, fragment, and nonempty query requests bypass the cache');
}

$html = '<!doctype html><html lang="en"><body>Cached content.</body></html>';
expectSame(null, readHomepageCache($directory, 'en', $key, 1000), 'Missing caches render normally');
foreach (['en', 'zh-hans', 'zh-hant'] as $locale) {
    expectSame(true, writeHomepageCache($directory, $locale, $key, $html . $locale, 1000),
        'Each supported locale has its own writable cache');
    expectSame($html . $locale, readHomepageCache($directory, $locale, $key, 1059),
        'Locale contents remain separate throughout their sixty-second lifetime');
}
expectSame(3, count(glob($directory . '/*')), 'Cache cardinality is fixed to three locale files');
expectSame([], glob($directory . '/.homepage-*.tmp'), 'Atomic replacement leaves no staging files');
expectSame(null, readHomepageCache($directory, 'en', $key, 1060), 'Entries expire at sixty seconds');
expectSame(null, readHomepageCache($directory, 'en', $key, 999), 'Future timestamps are rejected');
expectSame(false, writeHomepageCache($directory, '../outside', $key, $html, 1000),
    'Unsupported locales cannot choose filesystem paths');
expectSame(null, readHomepageCache($directory, 'fr', $key, 1000), 'Unsupported locales miss safely');
expectSame(true, writeHomepageCache($directory, 'en', $key, 'Replacement', 1001),
    'An existing locale file can be replaced atomically');
expectSame('Replacement', readHomepageCache($directory, 'en', $key, 1001),
    'Readers receive the complete replacement');

// Atomic producer replacement must invalidate even with equal size and mtime.
$replacement = $snapshot . '.new';
$mtime = filemtime($snapshot);
file_put_contents($replacement, '{"snapshot":2}');
touch($replacement, $mtime);
rename($replacement, $snapshot);
$changedKey = homepageCacheKey($inputs, $optional);
expectSame(false, $key === $changedKey, 'Snapshot inode replacements invalidate cached HTML');
expectSame(null, readHomepageCache($directory, 'en', $changedKey, 1001),
    'An input identity mismatch never returns cached content');
$key = $changedKey;
file_put_contents($status, '{"routines":{}}');
$changedKey = homepageCacheKey($inputs, $optional);
expectSame(false, $key === $changedKey, 'New routine status files invalidate cached HTML');
$key = $changedKey;
touch($status, filemtime($status) + 1);
expectSame(false, $key === homepageCacheKey($inputs, $optional), 'Routine status mtime changes invalidate HTML');
$key = homepageCacheKey($inputs, $optional);
file_put_contents($template, '<?php // changed template');
expectSame(false, $key === homepageCacheKey($inputs, $optional), 'PHP helper edits invalidate HTML');
unlink($snapshot);
expectSame(null, homepageCacheKey($inputs, $optional), 'Unavailable required snapshots cannot hit the cache');
symlink($template, $snapshot);
expectSame(null, homepageCacheKey($inputs, $optional), 'Input symlinks cannot establish cache identity');
unlink($snapshot);
file_put_contents($snapshot, '{}');
$key = homepageCacheKey($inputs, $optional);

$path = homepageCachePath($directory, 'en');
file_put_contents($path, 'malformed cached response');
expectSame(null, readHomepageCache($directory, 'en', $key, 1000), 'Malformed files are ordinary misses');
file_put_contents($path, str_repeat('x', PAGE_CACHE_READ_LIMIT + 1));
expectSame(null, readHomepageCache($directory, 'en', $key, 1000), 'Oversized stored responses are not served');
expectSame(false, writeHomepageCache($directory, 'en', $key, str_repeat('x', PAGE_CACHE_READ_LIMIT), 1000),
    'Responses exceeding the total file bound are not written');
unlink($path);
$outside = $fixture . '/outside';
file_put_contents($outside, 'Preserve this file');
symlink($outside, $path);
expectSame(null, readHomepageCache($directory, 'en', $key, 1000), 'Cache reads refuse file symlinks');
expectSame(false, writeHomepageCache($directory, 'en', $key, $html, 1000), 'Cache writes refuse file symlinks');
expectSame('Preserve this file', file_get_contents($outside), 'Refused cache writes preserve symlink targets');
unlink($path);
mkdir($path);
expectSame(false, writeHomepageCache($directory, 'en', $key, $html, 1000),
    'Invalid destination types fall back to rendering');
rmdir($path);
rename($directory, $directory . '-real');
symlink($directory . '-real', $directory);
expectSame(null, readHomepageCache($directory, 'en', $key, 1000), 'Cache directories cannot be symlinks');
expectSame(false, writeHomepageCache($directory, 'en', $key, $html, 1000), 'Writes refuse linked cache directories');
expectSame(false, writeHomepageCache($fixture . '/missing/parent/pages', 'en', $key, $html, 1000),
    'Unavailable cache parents are a safe write failure');

echo "Toolforge page cache checks passed.\n";
