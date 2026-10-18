<?php
/** Verify exact log identities and filesystem boundaries for both public viewers. */
declare(strict_types=1);

require __DIR__ . '/status.php';
require dirname(__DIR__, 2) . '/src/toolforge/logs.php';

$directory = fixtureDirectory($fixture . '/project-logs');
foreach (['project', 'aranami'] as $kind) {
    $filename = $kind . '.2026-10-18.log';
    file_put_contents($directory . '/' . $filename, "One retained daily record.\n");
    expectSame('2026-10-18', logFileDate($filename, $kind), 'Each viewer recognizes its exact daily filename');
    expectSame(true, regularLog($directory, $filename, $kind) !== null, 'A real matching log is available');
    $other = $kind === 'project' ? 'aranami' : 'project';
    expectSame(null, logFileDate($filename, $other), 'Project and detailed filename allowlists do not overlap');
    expectSame(null, regularLog($directory, $filename, $other), 'A viewer cannot read the other log family');
    foreach ([
        $kind . '.2026-02-30.log', $kind . '.2026-10-18.log.gz',
        $kind . '.2026-10-18.log/extra', $kind . '.2026-10-18.log' . "\n",
        '../' . $filename, $filename . '%00', strtoupper($filename),
    ] as $invalid) {
        expectSame(null, logFileDate($invalid, $kind), 'Malformed dates and traversal-shaped filenames are rejected');
    }
    $linked = $kind . '.2026-10-19.log';
    symlink($directory . '/' . $filename, $directory . '/' . $linked);
    expectSame(null, regularLog($directory, $linked, $kind), 'Matching log symlinks are refused');
}
expectSame(null, logFileDate('project.2026-10-18.log', '.*'), 'The viewer family itself has a fixed allowlist');
$linkedDirectory = $fixture . '/linked-project-logs';
symlink($directory, $linkedDirectory);
expectSame(false, regularLogDirectory($linkedDirectory), 'Log directories cannot be symlinks');
expectSame(null, regularLog($linkedDirectory, 'project.2026-10-18.log', 'project'),
    'A log cannot be reached through a linked directory');
expectSame(null, regularLog($directory, 'project.2026-10-20.log', 'project'), 'Missing logs remain unavailable');

echo "Toolforge log viewer checks passed.\n";
