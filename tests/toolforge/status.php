<?php
/** Verify structured routine snapshots, metadata, and filesystem boundaries. */
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

$fixture = fixtureDirectory(sys_get_temp_dir() . '/aranami-status-' . bin2hex(random_bytes(8)));
register_shutdown_function(static function () use ($fixture): void {
    removeFixture($fixture);
});

// Read the producer's complete snapshot, independently of diagnostic log wording.
$logs = fixtureDirectory($fixture . '/structured');
$record = [
    'routine' => 'dyks', 'status' => 'updated', 'dry' => false, 'updated' => 2,
    'duration_seconds' => 1.25, 'started_at' => '2026-10-18T01:00:00.123456Z',
    'finished_at' => '2026-10-18T01:00:01.373456Z', 'run_id' => str_repeat('a', 32),
];
$snapshot = ['schema' => 1, 'updated_at' => '2026-10-18T01:00:02Z', 'routines' => ['dyks' => $record]];
$statusPath = $logs . '/aranami-status.json';
file_put_contents($statusPath, json_encode($snapshot, JSON_THROW_ON_ERROR));
$event = dashboardActivity($logs)['latest']['dyks'];
expectSame('Updated', $event['status'], 'The reported status is read directly');
expectSame('1.25 sec', $event['duration'], 'Reported duration retains hundredths');
expectSame($record['finished_at'], $event['timestamp'], 'Terminal events use the producer finish time');
foreach (['updated' => 'Updated', 'no_update' => 'NoUpdate', 'failed' => 'Failed',
    'deferred' => 'Deferred', 'unknown' => 'Result unavailable'] as $status => $label) {
    expectSame($label, routineStatusEvent('dyks', array_replace($record, ['status' => $status]))['status'],
        'All reported terminal states are supported');
}
$running = array_replace($record, ['status' => 'running', 'updated' => null,
    'duration_seconds' => null, 'finished_at' => null]);
expectSame('Running', routineStatusEvent('dyks', $running)['status'], 'Running state comes from an explicit start');
expectSame($record['started_at'], routineStatusEvent('dyks', $running)['timestamp'], 'Running events use start time');
expectSame(null, routineStatusEvent('dyks', $running)['duration'], 'Running durations remain unknown');
foreach ([
    ['routine' => 'pageviews'], ['dry' => true], ['status' => 'made_up'], ['run_id' => 'x'],
    ['updated' => -1], ['updated' => true], ['duration_seconds' => -1],
    ['duration_seconds' => INF], ['duration_seconds' => '1.25'],
    ['started_at' => '2026-02-30T00:00:00Z'], ['started_at' => '2026-10-18T01:00:00'],
    ['finished_at' => '2026-10-17T00:00:00Z'], ['finished_at' => null],
    ['status' => 'running'],
] as $invalid) {
    expectSame(null, routineStatusEvent('dyks', array_replace($record, $invalid)), 'Malformed producer records are rejected');
}
foreach (['updated', 'duration_seconds', 'started_at', 'finished_at', 'run_id'] as $required) {
    $incomplete = $record;
    unset($incomplete[$required]);
    expectSame(null, routineStatusEvent('dyks', $incomplete), 'Incomplete lifecycle records are rejected');
}
foreach (['not json', '{"schema":2}', json_encode(array_replace($snapshot, ['updated_at' => 'bad'])),
    json_encode(array_replace($snapshot, ['routines' => null]))] as $invalid) {
    file_put_contents($statusPath, $invalid);
    expectSame(['latest' => []], dashboardActivity($logs), 'Unavailable snapshots remain explicit missing observations');
}
file_put_contents($statusPath, str_repeat(' ', STATUS_READ_LIMIT + 1));
expectSame(['latest' => []], dashboardActivity($logs), 'Oversized snapshots are not read');
unlink($statusPath);
file_put_contents($logs . '/aranami.2026-10-18.log', '2026-10-18 01:00:00Z INFO [aranami.jobs.dyks.job_run]: Routine finished in 2 seconds.');
expectSame(['latest' => []], dashboardActivity($logs), 'Diagnostic logs never manufacture a routine state');
file_put_contents($fixture . '/status-target.json', json_encode($snapshot));
symlink($fixture . '/status-target.json', $statusPath);
expectSame(['latest' => []], dashboardActivity($logs), 'Snapshots cannot be reached through a symlink');
expectSame('1 min 0 sec', formatDuration(60.0), 'Long durations keep minutes and seconds');
expectSame(null, statusTimestamp('2026-10-18T25:00:00Z'), 'Invalid time components are rejected');
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

expectSame(['latest' => []],
    dashboardActivity($fixture . '/absent-logs'), 'Missing log directories produce an empty dashboard');

echo "Toolforge status checks passed.\n";
