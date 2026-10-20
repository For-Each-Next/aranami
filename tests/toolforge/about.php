<?php
/** Verify the general-reader About page and safe method handling in every locale. */
declare(strict_types=1);

/** Fail a fixture assertion even when native assertions are disabled. */
function expectAbout(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

/** Remove this disposable copied source tree without following links. */
function removeAboutFixture(string $path): void
{
    if (is_link($path) || !is_dir($path)) {
        unlink($path);
        return;
    }
    foreach (new DirectoryIterator($path) as $entry) {
        if (!$entry->isDot()) {
            removeAboutFixture($entry->getPathname());
        }
    }
    rmdir($path);
}

$fixture = sys_get_temp_dir() . '/aranami-about-' . bin2hex(random_bytes(8));
$private = $fixture . '/app/src/webservice';
mkdir($private . '/public_html/about', 0700, true);
mkdir($fixture . '/app/src/shared', 0700);
register_shutdown_function(static function () use ($fixture): void {
    removeAboutFixture($fixture);
});
$source = dirname(__DIR__, 2) . '/src/webservice';
foreach (['i18n.php', 'status.php', 'public_html/about/index.php'] as $name) {
    $content = file_get_contents($source . '/' . $name);
    file_put_contents($private . '/' . $name, str_replace('/data/project/aranami', $fixture, $content));
}
file_put_contents($fixture . '/app/src/shared/version.json', '{"schema":1,"version":"0.6.0"}');

/** Render the actual copied entry point in an isolated PHP interpreter. */
function renderAboutFixture(string $path, string $language, string $method = 'GET'): array
{
    $command = [PHP_BINARY, '-r',
        '$_SERVER["REQUEST_METHOD"]=$argv[3]; $_SERVER["HTTP_ACCEPT_LANGUAGE"]=$argv[2]; require $argv[1];',
        $path, $language, $method];
    $pipes = [];
    $process = proc_open($command, [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    expectAbout(is_resource($process), 'The About fixture interpreter starts.');
    fclose($pipes[0]);
    $body = stream_get_contents($pipes[1]);
    $errors = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    expectAbout(proc_close($process) === 0 && $errors === '', 'The About renderer finishes without PHP errors: ' . $errors);
    return [$body, $errors];
}

foreach ([
    ['en', 'en', 'About Aranami', 'Discover articles', 'Find something to improve', 'Keep project reports up to date', 'Method not allowed.'],
    ['zh-CN', 'zh-Hans', '关于Aranami', '发现条目', '寻找改善方向', '保持专题报告更新', '不允许此请求方法。'],
    ['zh-TW', 'zh-Hant', '關於Aranami', '發現條目', '尋找改善方向', '保持專題報告更新', '不允許此請求方法。'],
] as [$language, $tag, $heading, $discover, $improve, $reports, $notAllowed]) {
    [$html] = renderAboutFixture($private . '/public_html/about/index.php', $language);
    expectAbout(str_contains($html, '<html lang="' . $tag . '">')
        && str_contains($html, '<h1>' . $heading . '</h1>')
        && str_contains($html, $discover) && str_contains($html, $improve) && str_contains($html, $reports),
        'The About page describes reader outcomes in the selected browser language.');
    expectAbout(substr_count($html, '<section ') === 3 && str_contains($html, 'href="/"')
        && str_contains($html, 'WikiProject:电子游戏') && str_contains($html, '0.6.0')
        && !str_contains($html, '/data/project/') && !str_contains($html, 'toolforge jobs')
        && !str_contains($html, '<script'), 'The About page stays reader-focused with safe navigation and source version.');
    [$head] = renderAboutFixture($private . '/public_html/about/index.php', $language, 'HEAD');
    expectAbout($head === '', 'HEAD sends no About page body.');
    [$post] = renderAboutFixture($private . '/public_html/about/index.php', $language, 'POST');
    expectAbout($post === $notAllowed . "\n", 'Unsupported About methods return localized plain text only.');
}
echo "Toolforge About page checks passed.\n";
