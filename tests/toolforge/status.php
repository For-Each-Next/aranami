<?php
/** Verify structured routine snapshots, metadata, and filesystem boundaries. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/src/webservice/status.php';

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
$statusPath = $logs . '/routine-status.json';
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
file_put_contents($logs . '/aranami.2026-10-18.log', '2026-10-18 01:00:00Z INFO [updater.jobs.dyks.job_run]: Routine finished in 2 seconds.');
expectSame(['latest' => []], dashboardActivity($logs), 'Diagnostic logs never manufacture a routine state');
file_put_contents($fixture . '/status-target.json', json_encode($snapshot));
symlink($fixture . '/status-target.json', $statusPath);
expectSame(['latest' => []], dashboardActivity($logs), 'Snapshots cannot be reached through a symlink');
expectSame('1 min 0 sec', formatDuration(60.0), 'Long durations keep minutes and seconds');
expectSame(null, statusTimestamp('2026-10-18T25:00:00Z'), 'Invalid time components are rejected');
// Only source deployment metadata establishes the footer version.
$metadata = $fixture . '/version.json';
expectSame(null, sourceVersion($metadata), 'Missing source metadata has no version');
file_put_contents($metadata, '{"schema":1,"version":"0.5.6.dev1"}');
expectSame('0.5.6.dev1', sourceVersion($metadata), 'Version comes from the deployed source manifest');
file_put_contents($metadata, '{"schema":2,"version":"0.5.6.dev1"}');
expectSame(null, sourceVersion($metadata), 'Unsupported source metadata is rejected');
unlink($metadata);
symlink($fixture . '/absent', $metadata);
expectSame(null, sourceVersion($metadata), 'Source metadata cannot follow a symlink');

expectSame(['latest' => []],
    dashboardActivity($fixture . '/absent-logs'), 'Missing log directories produce an empty dashboard');

// Scheduler observations are independent of terminal results and use actual predictions.
$definition = ['pages' => ['report' => 'WikiProject:电子游戏/热门条目'],
    'prefixes' => [], 'hour' => '*', 'minute' => 59];
$definitions = ['pageviews' => $definition];
$schedule = ['schema' => 1, 'updated_at' => '2026-10-18T12:00:00Z',
    'started_at' => '2026-10-16T08:56:00Z',
    'state' => 'running', 'config_id' => str_repeat('a', 64),
    'routines' => ['pageviews' => $definition + ['state' => 'scheduled', 'next_run_at' => '2026-10-19T18:59:00Z']]];
$schedulePath = $logs . '/schedule-status.json';
$now = new DateTimeImmutable('2026-10-18T12:00:30Z');
file_put_contents($schedulePath, json_encode($schedule));
$observed = dashboardSchedule($logs, $now, $definitions);
expectSame('2026-10-19 18:59 UTC', dashboardNextRun($observed, 'pageviews'), 'Actual postponed scheduler predictions are shown directly');
expectSame($schedule['started_at'], $observed['started_at'], 'A fresh observation retains the actual monitor start');
$heartbeat = array_replace($schedule, ['updated_at' => '2026-10-18T12:00:30Z']);
file_put_contents($schedulePath, json_encode($heartbeat));
expectSame($schedule['started_at'], dashboardSchedule($logs, new DateTimeImmutable('2026-10-18T12:01:00Z'), $definitions)['started_at'],
    'A later heartbeat preserves the monitor start rather than becoming a restart');
$restarted = array_replace($heartbeat, ['started_at' => '2026-10-18T12:00:30Z']);
file_put_contents($schedulePath, json_encode($restarted));
expectSame($restarted['started_at'], dashboardSchedule($logs, new DateTimeImmutable('2026-10-18T12:01:00Z'), $definitions)['started_at'],
    'A fresh monitor restart selects its new recorded start');
$legacy = $schedule;
unset($legacy['started_at']);
file_put_contents($schedulePath, json_encode($legacy));
expectSame(null, dashboardSchedule($logs, $now, $definitions)['started_at'], 'An older snapshot never invents a monitor start');
foreach (['invalid', '2026-02-30T00:00:00Z', '2026-10-19T00:00:00Z', '2026-10-16T08:56:00+08:00'] as $start) {
    file_put_contents($schedulePath, json_encode(array_replace($schedule, ['started_at' => $start])));
    $observed = dashboardSchedule($logs, $now, $definitions);
    expectSame(null, $observed['started_at'], 'Invalid, future, or non-UTC start observations cannot establish uptime');
    expectSame('scheduled', $observed['routines']['pageviews']['state'], 'Unavailable uptime does not discard valid scheduler predictions');
}
foreach (['paused', 'stopped'] as $state) {
    file_put_contents($schedulePath, json_encode(array_replace($schedule, ['state' => $state])));
    $observed = dashboardSchedule($logs, $now, $definitions);
    expectSame(null, $observed['routines']['pageviews']['next_run_at'], 'Non-running schedulers never display a predicted execution');
    expectSame($state, $observed['state'], 'Paused and stopped states remain distinct');
    expectSame($state === 'paused' ? $schedule['started_at'] : null, $observed['started_at'],
        'A paused live monitor retains uptime while a stopped monitor does not claim continuity');
}
file_put_contents($schedulePath, json_encode($schedule));
expectSame('stale', dashboardSchedule($logs, new DateTimeImmutable('2026-10-18T12:02:01Z'), $definitions)['state'],
    'Expired heartbeat observations suppress old next-run times');
expectSame(null, dashboardSchedule($logs, new DateTimeImmutable('2026-10-18T12:02:01Z'), $definitions)['started_at'],
    'A stale observation cannot establish continuous runtime');
$changed = $definitions;
$changed['pageviews']['minute'] = 58;
expectSame([], dashboardSchedule($logs, $now, $changed)['routines'], 'Configuration changes invalidate old scheduler predictions');
file_put_contents($schedulePath, str_repeat(' ', STATUS_READ_LIMIT + 1));
expectSame('missing', dashboardSchedule($logs, $now, $definitions)['state'], 'Scheduler reads keep their existing strict byte bound');
// Page-level update state comes from backend metadata, independently of diagnostics.
$reportState = fixtureDirectory($fixture . '/report-state');
$reportPath = $reportState . '/report-updates.json';
$report = [
    'update_id' => str_repeat('b', 32), 'run_id' => str_repeat('a', 32),
    'routine' => 'pageviews', 'site' => 'zhwiki', 'title' => 'Wikipedia:报告',
    'status' => 'updated', 'dry' => false,
    'started_at' => '2026-10-20T18:59:00.000000Z',
    'finished_at' => '2026-10-20T18:59:02.500000Z',
    'duration_seconds' => 2.5, 'confirmed_edits' => 1,
];
$reportKey = hash('sha256', $report['site'] . "\n" . $report['title']);
$reportSnapshot = ['schema' => 1, 'updated_at' => '2026-10-20T18:59:02.500000Z',
    'reports' => [$reportKey => $report]];
file_put_contents($reportPath, json_encode($reportSnapshot, JSON_THROW_ON_ERROR));
expectSame([$reportKey => $report], dashboardReportUpdates($reportState), 'Latest per-page state retains the exact target and measured outcome');
foreach (['updated' => 1, 'no_update' => 0, 'failed' => null, 'deferred' => 0, 'unconfirmed' => null] as $status => $edits) {
    $outcome = array_replace($report, ['status' => $status, 'confirmed_edits' => $edits]);
    expectSame($outcome, reportStatusEvent($reportKey, $outcome), 'Per-page terminal states preserve explicit confirmed counts');
}
$runningReport = array_replace($report, ['status' => 'running', 'finished_at' => null,
    'duration_seconds' => null, 'confirmed_edits' => null]);
expectSame($runningReport, reportStatusEvent($reportKey, $runningReport), 'A started report does not imply a finished edit');
foreach ([
    ['site' => '../zhwiki'], ['title' => ''], ['title' => "Bad\nTitle"], ['title' => str_repeat('A', 801)],
    ['routine' => '../pageviews'], ['update_id' => 'bad'], ['run_id' => str_repeat('g', 32)],
    ['dry' => true], ['status' => 'preview'], ['status' => 'success'], ['confirmed_edits' => 0],
    ['confirmed_edits' => true], ['confirmed_edits' => -1], ['duration_seconds' => INF],
    ['duration_seconds' => -1], ['started_at' => '2026-02-30T00:00:00Z'],
    ['finished_at' => '2026-10-20T18:58:59Z'], ['status' => 'running'],
    ['status' => 'no_update', 'confirmed_edits' => null], ['status' => 'unconfirmed', 'confirmed_edits' => 1],
] as $invalidReport) {
    expectSame(null, reportStatusEvent($reportKey, array_replace($report, $invalidReport)), 'Invalid page-update metadata is rejected');
}
foreach (array_keys($report) as $required) {
    $incompleteReport = $report;
    unset($incompleteReport[$required]);
    expectSame(null, reportStatusEvent($reportKey, $incompleteReport), 'Every page-update metadata field is required');
}
expectSame(null, reportStatusEvent(str_repeat('f', 64), $report), 'Report keys must identify the exact destination');
foreach ([
    'not json', '{}',
    json_encode(array_replace($reportSnapshot, ['schema' => 2])),
    json_encode(array_replace($reportSnapshot, ['updated_at' => 'bad'])),
    json_encode(array_replace($reportSnapshot, ['reports' => [$report]])),
    json_encode(array_replace($reportSnapshot, ['reports' => [$reportKey => array_replace($report, ['dry' => true])]])),
] as $invalidSnapshot) {
    file_put_contents($reportPath, $invalidSnapshot);
    expectSame([], dashboardReportUpdates($reportState), 'Malformed per-page snapshots cannot establish status');
}
file_put_contents($reportPath, str_repeat(' ', REPORT_STATUS_READ_LIMIT + 1));
expectSame([], dashboardReportUpdates($reportState), 'Per-page metadata reads enforce a fixed byte bound');
unlink($reportPath);
file_put_contents($reportState . '/aranami.2026-10-20.log', 'Updated report Wikipedia:报告 successfully.');
expectSame([], dashboardReportUpdates($reportState), 'Diagnostic log text never manufactures page-update metadata');
file_put_contents($fixture . '/report-target.json', json_encode($reportSnapshot, JSON_THROW_ON_ERROR));
symlink($fixture . '/report-target.json', $reportPath);
expectSame([], dashboardReportUpdates($reportState), 'Per-page snapshots cannot follow symlinks');
symlink($reportState, $fixture . '/report-state-link');
expectSame([], dashboardReportUpdates($fixture . '/report-state-link'), 'Per-page state directories cannot follow symlinks');
expectSame([], dashboardReportUpdates($fixture . '/absent-state'), 'Missing per-page observations remain unknown');

echo "Toolforge status checks passed.\n";
