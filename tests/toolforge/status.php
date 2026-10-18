<?php
/** Exercise dashboard status parsing with isolated metadata and daily-log fixtures. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/src/toolforge/status.php';

/** Fail visibly even when PHP's built-in assertions are disabled. */
function expectSame(mixed $expected, mixed $actual, string $description): void
{
    if ($expected !== $actual) {
        throw new RuntimeException($description . "\nExpected: " . var_export($expected, true)
            . "\nActual: " . var_export($actual, true));
    }
}

/** Create a fixture directory and its missing parent directories. */
function fixtureDirectory(string $path): string
{
    if (!mkdir($path, 0700, true) && !is_dir($path)) {
        throw new RuntimeException('Cannot create fixture: ' . $path);
    }
    return $path;
}

/** Remove fixture contents without traversing links. */
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

/** Construct one lifecycle line with the logger's actual UTC format. */
function eventLine(string $timestamp, string $routine, string $message, string $level = 'INFO'): string
{
    return "$timestamp" . "Z $level [aranami.jobs.$routine.job_run]: $message\n";
}

/** Construct a self-contained result from the versioned lifecycle contract. */
function resultLine(string $timestamp, string $routine, string $status, ?int $updated, bool $dry = false, array $overrides = []): string
{
    $result = array_replace([
        'schema' => 1, 'routine' => $routine, 'dry' => $dry,
        'status' => $status, 'updated' => $updated, 'duration_seconds' => 1.25,
    ], $overrides);
    return eventLine($timestamp, $routine, 'Routine result: ' . json_encode($result, JSON_THROW_ON_ERROR));
}

$fixture = fixtureDirectory(sys_get_temp_dir() . '/aranami-status-' . bin2hex(random_bytes(8)));
register_shutdown_function(static function () use ($fixture): void {
    removeFixture($fixture);
});

// Only recognized lifecycle records with matching severities become events.
foreach ([
    ['finished in', 'INFO', 'Result unavailable', 'unknown', '1.25', '1.25 sec'],
    ['failed after', 'ERROR', 'Failed', 'failure', '62.5', '1 min 3 sec'],
    ['deferred after', 'WARNING', 'Deferred', 'deferred', '0', '0.00 sec'],
] as [$verb, $level, $status, $tone, $seconds, $duration]) {
    $event = parseEvent(eventLine('2026-10-18 00:00:01', 'dyks', "Routine $verb $seconds seconds.", $level));
    expectSame($status, $event['status'] ?? null, 'Outcome status');
    expectSame($tone, $event['tone'] ?? null, 'Outcome appearance');
    expectSame($duration, $event['duration'] ?? null, 'Formatted elapsed time');
    expectSame(null, $event['dry'], 'A legacy outcome has no inferred publication mode');
    expectSame(false, $event['start'] ?? null, 'An outcome is not a start');
}
expectSame('59.99 sec', formatDuration(59.99), 'Short runs retain two decimal places');
expectSame('1 min 0 sec', formatDuration(59.999), 'Rounding carries into minutes');
expectSame('2 min 0 sec', formatDuration(119.99), 'Whole seconds carry into minutes');
expectSame('16 min 4 sec', formatDuration(963.79), 'Long runs use whole seconds');
foreach ([
    eventLine('2026-02-30 00:00:00', 'dyks', 'Routine finished in 1 seconds.'),
    eventLine('2026-10-18 24:00:00', 'dyks', 'Routine finished in 1 seconds.'),
    eventLine('2026-10-18 00:00:00', 'unknown', 'Routine finished in 1 seconds.'),
    eventLine('2026-10-18 00:00:00', 'dyks', 'Routine finished in 1 seconds.', 'ERROR'),
    eventLine('2026-10-18 00:00:00', 'dyks', 'Routine failed after 1 seconds.'),
    eventLine('2026-10-18 00:00:00', 'dyks', 'Routine deferred after 1 seconds.'),
    eventLine('2026-10-18 00:00:00', 'dyks', 'Routine finished in -1 seconds.'),
    eventLine('2026-10-18 00:00:00', 'dyks', 'Routine finished in ' . str_repeat('9', 400) . ' seconds.'),
    eventLine('2026-10-18 00:00:00', 'dyks', 'Routine started (dry=1).'),
    eventLine('2026-10-18 00:00:00', 'dyks', 'Routine started (dry=True).', 'WARNING'),
    eventLine('2026-10-18 00:00:00', 'dyks', 'An unrelated progress message.'),
    'Unstructured output',
] as $line) {
    expectSame(null, parseEvent($line), 'Malformed or unrecognized records are ignored');
}

foreach ([
    ['updated', 2, 'Updated'], ['no_update', 0, 'NoUpdate'],
    ['failed', 1, 'Failed'], ['deferred', 0, 'Deferred'],
    ['unknown', null, 'Result unavailable'],
] as [$status, $updated, $label]) {
    $event = parseEvent(resultLine('2026-10-18 00:00:01', 'dyks', $status, $updated));
    expectSame($label, $event['status'], 'Structured publication result');
    expectSame($updated, $event['updated'], 'Actual saved count is retained');
    expectSame(false, $event['dry'], 'Structured result identifies publication behavior');
}
foreach ([
    ['schema' => 2], ['schema' => '1'], ['routine' => 'pageviews'],
    ['dry' => 'False'], ['status' => 'completed'], ['updated' => -1],
    ['updated' => true], ['updated' => 0], ['updated' => null],
    ['duration_seconds' => -1], ['duration_seconds' => '1.25'],
] as $overrides) {
    expectSame(null, parseEvent(resultLine('2026-10-18 00:00:01', 'dyks', 'updated', 1, false, $overrides)),
        'Malformed, contradictory, or unsupported structured results are ignored');
}
expectSame(null, parseEvent(resultLine('2026-10-18 00:00:01', 'dyks', 'no_update', 1)),
    'No-update results cannot claim saved edits');
expectSame(null, parseEvent(eventLine('2026-10-18 00:00:01', 'dyks', 'Routine result: {invalid}')),
    'Malformed result JSON is ignored');
expectSame(null, parseEvent(str_replace('1.25', '1e309', resultLine('2026-10-18 00:00:01', 'dyks', 'updated', 1))),
    'Infinite result durations are ignored');

$logs = fixtureDirectory($fixture . '/midnight');
file_put_contents($logs . '/aranami.2026-10-17.log',
    eventLine('2026-10-17 23:59:55', 'dyks', 'Routine started (dry=False).')
    . eventLine('2026-10-17 23:59:56', 'pageviews', 'Routine started (dry=True).')
    . eventLine('2026-10-17 23:59:57', 'assessment_lists', 'Routine started (dry=False).')
    . eventLine('2026-10-17 23:59:58', 'new_pages', 'Routine started (dry=True).'));
file_put_contents($logs . '/aranami.2026-10-18.log',
    eventLine('2026-10-18 00:00:01', 'dyks', 'Routine finished in 2.5 seconds.')
    . eventLine('2026-10-18 00:00:02', 'pageviews', 'Routine deferred after 1 seconds.', 'WARNING')
    . eventLine('2026-10-18 00:00:03', 'assessment_lists', 'Routine failed after 3 seconds.', 'ERROR')
    . eventLine('2026-10-18 00:00:04', 'pexbot', 'Routine started (dry=False).')
    . eventLine('2026-10-18 00:00:05', 'enwp_key_articles', 'Routine finished in 4 seconds.')
    . eventLine('2026-10-19 00:00:06', 'dyks', 'Routine failed after 9 seconds.', 'ERROR'));
$activity = dashboardActivity($logs);
foreach ([
    'dyks' => ['Result unavailable', false],
    'assessment_lists' => ['Failed', false],
    'pexbot' => ['Outcome pending', false],
    'enwp_key_articles' => ['Result unavailable', null],
] as $routine => [$status, $dry]) {
    expectSame($status, $activity['latest'][$routine]['status'] ?? null, "$routine latest status");
    expectSame($dry, $activity['latest'][$routine]['dry'], "$routine publication behavior across UTC midnight");
}
expectSame(false, isset($activity['latest']['pageviews']), 'A preview outcome is hidden across UTC midnight');
expectSame(false, isset($activity['latest']['new_pages']), 'An unmatched preview start is hidden');
expectSame(['enwp_key_articles', 'assessment_lists', 'dyks'],
    array_column($activity['recent'], 'routine'), 'Recent results exclude previews, starts, and incorrect dates');
expectSame('aranami.2026-10-18.log', $activity['latest']['dyks']['filename'], 'An outcome links to its own daily log');
expectSame(2, $activity['log_count'], 'Both UTC daily logs are counted');
file_put_contents($logs . '/aranami.2026-10-18.log',
    eventLine('2026-10-18 00:00:07', 'dyks', 'Routine finished in 5 seconds.'), FILE_APPEND);
expectSame(null, dashboardActivity($logs)['latest']['dyks']['dry'], 'A completed start is consumed once');

// An omitted prefix may contain a newer start; older-day mode must not leak through.
$bounded = fixtureDirectory($fixture . '/bounded');
file_put_contents($bounded . '/aranami.2026-10-17.log',
    eventLine('2026-10-17 23:59:59', 'dyks', 'Routine started (dry=False).'));
$tail = eventLine('2026-10-18 00:00:02', 'dyks', 'Routine finished in 1 seconds.');
$largeLog = $bounded . '/aranami.2026-10-18.log';
file_put_contents($largeLog,
    eventLine('2026-10-18 00:00:01', 'dyks', 'Routine started (dry=True).')
    . str_repeat('x', READ_LIMIT + 100) . "\n" . $tail);
expectSame($tail, readRegularFile($largeLog, true), 'A bounded tail drops its partial first line');
expectSame(READ_LIMIT, strlen(readRegularFile($largeLog)), 'A prefix read is bounded');
expectSame('Result unavailable', dashboardActivity($bounded)['latest']['dyks']['status'], 'An omitted start never establishes update or no-update');

$results = fixtureDirectory($fixture . '/results');
file_put_contents($results . '/aranami.2026-10-18.log',
    eventLine('2026-10-18 01:00:00', 'dyks', 'Routine started (dry=False).')
    . eventLine('2026-10-18 01:00:01', 'pageviews', 'Routine started (dry=False).')
    . "2026-10-18 01:00:02Z INFO [aranami.jobs._execution.publish]: Saved: Custom destination.\n"
    . resultLine('2026-10-18 01:00:03', 'pageviews', 'no_update', 0)
    . resultLine('2026-10-18 01:00:04', 'dyks', 'updated', 2)
    . eventLine('2026-10-18 01:00:05', 'pexbot', 'Routine started (dry=False).')
    . "2026-10-18 01:00:06Z INFO [aranami.jobs.pexbot._refresh]: PexBot Custom report: end.\n"
    . eventLine('2026-10-18 01:00:07', 'pexbot', 'Routine finished in 1 seconds.')
    . resultLine('2026-10-18 01:00:08', 'enwp_key_articles', 'unknown', null)
    . resultLine('2026-10-18 01:00:09', 'new_pages', 'updated', 99, true)
    . eventLine('2026-10-18 01:00:10', 'assessment_lists', 'Routine started (dry=True).')
    . eventLine('2026-10-18 01:00:11', 'assessment_lists', 'Routine failed after 1 seconds.', 'ERROR')
    . resultLine('2026-10-18 01:00:12', 'dyks', 'no_update', 0, true)
    . eventLine('2026-10-18 01:00:13', 'dyks', 'Routine started (dry=True).'));
$outcomes = dashboardActivity($results);
expectSame('Updated', $outcomes['latest']['dyks']['status'], 'A later preview never replaces the latest live result');
expectSame(2, $outcomes['latest']['dyks']['updated'], 'Interleaved results retain the correct routine write count');
expectSame('NoUpdate', $outcomes['latest']['pageviews']['status'], 'A genuine no-op result is distinct from a successful edit');
expectSame('Result unavailable', $outcomes['latest']['pexbot']['status'], 'A legacy delegated end event does not prove an edit');
expectSame('Result unavailable', $outcomes['latest']['enwp_key_articles']['status'], 'Unverified structured outcomes stay unknown');
expectSame(false, isset($outcomes['latest']['new_pages']), 'Self-contained preview results are hidden without a start');
expectSame(false, isset($outcomes['latest']['assessment_lists']), 'Legacy preview failures are hidden');
expectSame(4, count($outcomes['recent']), 'Preview events are absent from recent results');
file_put_contents($largeLog, str_repeat('x', READ_LIMIT + 100) . "\n"
    . resultLine('2026-10-18 02:00:00', 'dyks', 'updated', 1));
expectSame('Updated', dashboardActivity($bounded)['latest']['dyks']['status'], 'A complete structured result survives a truncated start');

$recentLogs = fixtureDirectory($fixture . '/recent');
$records = '';
for ($index = 0; $index < 15; $index++) {
    $timestamp = '2026-10-18 01:00:' . sprintf('%02d', $index);
    $records .= eventLine($timestamp, 'dyks', 'Routine started (dry=False).')
        . eventLine($timestamp, 'dyks', "Routine finished in $index seconds.");
}
file_put_contents($recentLogs . '/aranami.2026-10-18.log', $records);
$recent = dashboardActivity($recentLogs)['recent'];
expectSame(12, count($recent), 'Recent outcomes are capped at twelve');
expectSame('14.00 sec', $recent[0]['duration'], 'Recent outcomes are newest first');
expectSame('3.00 sec', $recent[11]['duration'], 'Only the twelve latest outcomes remain');

// Distribution ambiguity and filesystem links never select an installation.
$tool = fixtureDirectory($fixture . '/installation');
expectSame(null, installedVersion($tool), 'An absent virtual environment has no version');
$packages = fixtureDirectory($tool . '/pyvenv/lib/python3.13/site-packages');
$distribution = fixtureDirectory($packages . '/aranami-0.5.0.post1.dist-info');
$metadata = $distribution . '/METADATA';
file_put_contents($metadata, "Name: aranami\r\nVersion: 0.5.0.post1\r\n\r\nVersion: body-text\r\n");
expectSame('0.5.0.post1', installedVersion($tool), 'Version comes from distribution headers');
$other = fixtureDirectory($packages . '/aranami-0.5.0.post2.dist-info');
file_put_contents($other . '/METADATA', "Name: aranami\nVersion: 0.5.0.post2\n\n");
expectSame(null, installedVersion($tool), 'Multiple installations have no selected version');
removeFixture($other);
rename($metadata, $fixture . '/metadata');
symlink($fixture . '/metadata', $metadata);
expectSame(null, readRegularFile($metadata), 'File reads refuse symlinks');
expectSame(null, installedVersion($tool), 'Distribution metadata cannot be a symlink');
$linkedTool = fixtureDirectory($fixture . '/linked-installation');
symlink($tool . '/pyvenv', $linkedTool . '/pyvenv');
expectSame(null, installedVersion($linkedTool), 'A virtual environment directory cannot be a symlink');

$regularLog = $recentLogs . '/aranami.2026-10-18.log';
symlink($regularLog, $recentLogs . '/aranami.2026-10-19.log');
file_put_contents($recentLogs . '/aranami.2026-02-30.log', $records);
file_put_contents($recentLogs . '/notes.log', $records);
expectSame(['aranami.2026-10-18.log'], dailyLogs($recentLogs), 'Only regular logs with valid UTC filenames are listed');
symlink($recentLogs, $fixture . '/linked-logs');
expectSame(false, regularDirectory($fixture . '/linked-logs'), 'Directory checks refuse symlinks');
expectSame([], dailyLogs($fixture . '/linked-logs'), 'The log directory cannot be a symlink');
expectSame(['latest' => [], 'recent' => [], 'log_count' => 0],
    dashboardActivity($fixture . '/absent-logs'), 'Missing log directories produce an empty dashboard');

echo "Toolforge status checks passed.\n";
