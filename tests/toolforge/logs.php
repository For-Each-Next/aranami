<?php
/** Verify one bounded natural-language log browser and its exact downloads. */
declare(strict_types=1);

ob_start();
require __DIR__ . '/status.php';
ob_end_clean();
require dirname(__DIR__, 2) . '/src/webservice/logs.php';

$directory = fixtureDirectory($fixture . '/daily-logs');
$filename = 'aranami.2026-10-18.log';
$content = "2026-10-18 01:00:00Z INFO [backend.server]: Website refresh completed.\n"
    . "2026-10-18 01:01:00Z WARNING [backend.runner]: Deferred <script>alert('x')</script>.\n"
    . "2026-10-18 01:02:00Z INFO [updater]: Updated 中文页面.\n";
file_put_contents($directory . '/' . $filename, $content);
expectSame('2026-10-18', logFileDate($filename), 'Only exact universal log names identify a UTC date');
expectSame(true, regularLog($directory, $filename) !== null, 'A regular bounded log is available');
foreach ([
    'project.2026-10-18.log', 'aranami.2026-02-30.log', 'aranami.2026-10-18.log.gz',
    'aranami.2026-10-18.log/extra', $filename . "\n", '../' . $filename,
    $filename . '%00', strtoupper($filename), 'routine-status.json',
] as $invalid) {
    expectSame(null, logFileDate($invalid), 'Legacy names, metadata, invalid dates, and traversal are rejected');
}
$linked = 'aranami.2026-10-19.log';
symlink($directory . '/' . $filename, $directory . '/' . $linked);
expectSame(null, regularLog($directory, $linked), 'Log file symlinks are refused');
expectSame(null, openDailyLog($directory, $linked), 'Log file symlinks cannot be opened');
$linkedDirectory = $fixture . '/linked-daily-logs';
symlink($directory, $linkedDirectory);
expectSame(false, regularLogDirectory($linkedDirectory), 'Log directories cannot be symlinks');
expectSame(null, regularLog($linkedDirectory, $filename), 'Linked directories cannot expose logs');
expectSame(null, readLogExcerpt($directory, 'aranami.2026-10-20.log'), 'Missing files remain unavailable');

file_put_contents($directory . '/project.2026-10-17.log', '{"event":"retired"}');
file_put_contents($directory . '/routine-status.json', '{"routines":{}}');
$available = availableLogs($directory);
expectSame([$filename], array_keys($available), 'The browser lists only universal natural-language files');
$selection = logSelection([], $available);
expectSame('2026-10-18', $selection['date'], 'The latest available UTC date is selected by default');
expectSame($filename, $selection['filename'], 'Date selection resolves one exact filename');
expectSame(false, $selection['download'], 'Default views are bounded HTML excerpts');
expectSame('中文', logSelection(['q' => ' 中文 '], $available)['query'], 'Literal UTF-8 searches are retained');
foreach ([
    ['date' => '../private'], ['date' => '2026-02-30'], ['date' => ['2026-10-18']],
    ['q' => ['text']], ['q' => str_repeat('x', LOG_QUERY_BYTES + 1)],
    ['q' => "\xff"], ['file' => $filename], ['download' => 'yes'],
] as $invalid) {
    $rejected = false;
    try {
        logSelection($invalid, $available);
    } catch (InvalidArgumentException) {
        $rejected = true;
    }
    expectSame(true, $rejected, 'Malformed dates, queries, and obsolete parameters are rejected');
}
$excerpt = readLogExcerpt($directory, $filename, 'WEBSITE');
expectSame(1, count($excerpt['lines']), 'Literal queries match without ASCII case sensitivity');
expectSame(true, str_contains($excerpt['lines'][0], 'Website refresh completed.'), 'Matching messages remain natural text');
expectSame([], readLogExcerpt($directory, $filename, 'unmatched')['lines'], 'An absent query has no matching lines');
expectSame(true, str_contains(logEscape($content), '&lt;script&gt;'), 'Log content is escaped for HTML');

$_SERVER['REQUEST_METHOD'] = 'GET';
$_SERVER['HTTP_ACCEPT_LANGUAGE'] = 'en';
$_GET = ['date' => '2026-10-18', 'q' => 'Deferred'];
http_response_code(200);
ob_start();
serveLogs($directory);
$html = ob_get_clean();
expectSame(200, http_response_code(), 'A valid date-and-text query renders successfully');
expectSame(true, str_contains($html, 'name="date"') && str_contains($html, 'name="q"'), 'The browser exposes date and literal text controls');
expectSame(true, str_contains($html, '&lt;script&gt;'), 'Diagnostic HTML-shaped text stays escaped');
expectSame(false, str_contains($html, "<script>alert('x')</script>"), 'Diagnostic text cannot add script markup');
expectSame(false, str_contains($html, '/logs/aranami/'), 'The retired secondary log route is absent');
expectSame(false, str_contains($html, '{"event"'), 'Legacy event streams are neither listed nor interpreted');
expectSame(true, str_contains($html, '<h2>Recent logs</h2>'), 'The excerpt has a concise reader-facing heading');
expectSame(false, str_contains($html, 'download=1') || str_contains($html, '1 MiB')
    || str_contains($html, '500 matching'), 'Download metadata and excerpt implementation details are absent');
expectSame(false, str_contains($html, 'Follow data refreshes') || str_contains($html, 'Logs use UTC')
    || str_contains($html, '<footer>'), 'The log browser omits introductory and footer filler');

$_GET = ['date' => '2026-10-18', 'download' => '1'];
ob_start();
serveLogs($directory);
$download = ob_get_clean();
expectSame($content, $download, 'The download preserves exact complete UTF-8 log bytes');
$_GET = ['date' => '../private'];
ob_start();
serveLogs($directory);
ob_end_clean();
expectSame(400, http_response_code(), 'Invalid paths fail before opening any runtime file');
$_GET = ['date' => '2026-10-20'];
ob_start();
serveLogs($directory);
ob_end_clean();
expectSame(404, http_response_code(), 'Unavailable dates remain explicit missing files');
$_SERVER['REQUEST_METHOD'] = 'POST';
$_GET = [];
ob_start();
serveLogs($directory);
ob_end_clean();
expectSame(405, http_response_code(), 'The log browser accepts only read-only GET requests');

$large = 'aranami.2026-10-21.log';
expectSame(100, LOG_MAX_LINES, 'Recent logs show at most one hundred matching lines');
file_put_contents($directory . '/' . $large, str_repeat("one natural diagnostic\n", LOG_MAX_LINES + 10)
    . "Last visible diagnostic.\n");
$bounded = readLogExcerpt($directory, $large);
expectSame(LOG_MAX_LINES, count($bounded['lines']), 'Visible excerpts have a fixed line limit');
expectSame(true, $bounded['truncated'], 'Clipped excerpts retain truncation metadata');
expectSame('Last visible diagnostic.', $bounded['lines'][LOG_MAX_LINES - 1], 'A bounded excerpt retains the newest matching messages');
$tail = 'aranami.2026-10-22.log';
file_put_contents($directory . '/' . $tail, str_repeat("Earlier diagnostic.\n", 60000) . "Unique latest diagnostic.\n");
expectSame([], readLogExcerpt($directory, $tail, 'not present')['lines'], 'Scanning a large file remains bounded');
expectSame(['Unique latest diagnostic.'], readLogExcerpt($directory, $tail, 'Unique latest')['lines'], 'Text queries search the bounded end of a file');
$oversized = 'aranami.2026-10-23.log';
$stream = fopen($directory . '/' . $oversized, 'wb');
ftruncate($stream, LOG_MAX_FILE_BYTES + 1);
fclose($stream);
expectSame(null, regularLog($directory, $oversized), 'Oversized files cannot be browsed or downloaded');
expectSame(null, openDailyLog($directory, $oversized), 'Size bounds also apply when opening a download');

echo "Toolforge unified log viewer checks passed.\n";
