<?php
/** Read installed metadata and routine activity for the Toolforge dashboard. */
declare(strict_types=1);

const READ_LIMIT = 65536;
const ACTIVITY_LOG_LIMIT = 91;
const RECENT_ACTIVITY_LIMIT = 12;
const ROUTINES = [
    'dyks' => 'DYK report',
    'new_pages' => 'New-page report',
    'assessment_lists' => 'Assessment lists',
    'pageviews' => 'Pageviews report',
    'enwp_key_articles' => 'English article comparisons',
    'pexbot' => 'PexBot reports',
];

/** Escape a value for HTML text or a quoted attribute. */
function html(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Check a directory without following a symlink at that path. */
function regularDirectory(string $path): bool
{
    $stat = @lstat($path);
    return $stat !== false && ($stat['mode'] & 0170000) === 0040000;
}

/** Read a bounded prefix or tail of a regular file without following symlinks. */
function readRegularFile(string $path, bool $tail = false, int $limit = READ_LIMIT): ?string
{
    $stat = @lstat($path);
    if ($stat === false || ($stat['mode'] & 0170000) !== 0100000) {
        return null;
    }
    $stream = @fopen($path, 'rb');
    if ($stream === false) {
        return null;
    }
    $opened = fstat($stream);
    if ($opened === false || ($opened['mode'] & 0170000) !== 0100000
        || $opened['dev'] !== $stat['dev'] || $opened['ino'] !== $stat['ino']) {
        fclose($stream);
        return null;
    }
    $offset = $tail ? max(0, $opened['size'] - $limit) : 0;
    if ($offset > 0 && fseek($stream, $offset) !== 0) {
        fclose($stream);
        return null;
    }
    $content = stream_get_contents($stream, $limit);
    fclose($stream);
    if ($content === false) {
        return null;
    }
    if ($offset > 0) {
        // The first line can begin before the bounded tail.
        $newline = strpos($content, "\n");
        $content = $newline === false ? '' : substr($content, $newline + 1);
    }
    return $content;
}

/** Read the installed distribution metadata, refusing ambiguous installations. */
function installedVersion(string $toolDirectory): ?string
{
    $library = $toolDirectory . '/pyvenv/lib';
    if (!regularDirectory($toolDirectory . '/pyvenv') || !regularDirectory($library)) {
        return null;
    }
    $versions = [];
    foreach (glob($library . '/python*/site-packages/aranami-*.dist-info/METADATA') ?: [] as $path) {
        $distribution = dirname($path);
        $packages = dirname($distribution);
        if (!regularDirectory($distribution) || !regularDirectory($packages)
            || !regularDirectory(dirname($packages))) {
            continue;
        }
        $metadata = readRegularFile($path);
        if ($metadata === null) {
            continue;
        }
        $headers = preg_split('/\r?\n\r?\n/', $metadata, 2)[0];
        if (preg_match('/^Name: aranami\r?$/m', $headers) !== 1
            || preg_match('/^Version: ([0-9][0-9A-Za-z.!+_-]*)\r?$/m', $headers, $match) !== 1) {
            continue;
        }
        $versions[] = $match[1];
    }
    return count($versions) === 1 ? $versions[0] : null;
}

/** Return the date in a valid daily log filename. */
function logDate(string $filename): ?string
{
    if (preg_match('/\Aaranami\.([0-9]{4})-([0-9]{2})-([0-9]{2})\.log\z/', $filename, $parts) !== 1
        || !checkdate((int) $parts[2], (int) $parts[3], (int) $parts[1])) {
        return null;
    }
    return $parts[1] . '-' . $parts[2] . '-' . $parts[3];
}

/** Keep hundredths below a minute and whole seconds for longer runs. */
function formatDuration(float $seconds): string
{
    $hundredths = (int) round($seconds * 100);
    if ($hundredths < 6000) {
        return number_format($hundredths / 100, 2, '.', '') . ' sec';
    }
    $wholeSeconds = (int) round($seconds);
    return intdiv($wholeSeconds, 60) . ' min ' . ($wholeSeconds % 60) . ' sec';
}

/** Recognize validated routine results and legacy lifecycle records. */
function parseEvent(string $line): ?array
{
    if (preg_match('/\A([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}Z) ([A-Z]+) \[aranami\.jobs\.([a-z_]+)\.job_run\]: (.+)\z/', rtrim($line, "\r\n"), $parts) !== 1
        || !isset(ROUTINES[$parts[3]])) {
        return null;
    }
    $time = DateTimeImmutable::createFromFormat('!Y-m-d H:i:s\Z', $parts[1], new DateTimeZone('UTC'));
    if ($time === false || $time->format('Y-m-d H:i:s\Z') !== $parts[1]) {
        return null;
    }
    $event = [
        'timestamp' => $parts[1],
        'routine' => $parts[3],
        'action' => ROUTINES[$parts[3]],
        'duration' => null,
        'dry' => null,
    ];
    if (str_starts_with($parts[4], 'Routine result: ')) {
        $result = json_decode(substr($parts[4], strlen('Routine result: ')), true, 16);
        if (!is_array($result) || ($result['schema'] ?? null) !== 1
            || ($result['routine'] ?? null) !== $parts[3]
            || !is_bool($result['dry'] ?? null)
            || !is_string($result['status'] ?? null)
            || !array_key_exists('updated', $result)
            || ($result['updated'] !== null && (!is_int($result['updated']) || $result['updated'] < 0))
            || (!is_int($result['duration_seconds'] ?? null) && !is_float($result['duration_seconds'] ?? null))
            || !in_array($parts[2], ['INFO', 'WARNING', 'ERROR'], true)) {
            return null;
        }
        $statuses = [
            'updated' => ['Updated', 'success'],
            'no_update' => ['NoUpdate', 'unchanged'],
            'failed' => ['Failed', 'failure'],
            'deferred' => ['Deferred', 'deferred'],
            'unknown' => ['Result unavailable', 'unknown'],
        ];
        $seconds = (float) $result['duration_seconds'];
        if (!isset($statuses[$result['status']]) || !is_finite($seconds)
            || $seconds < 0 || $seconds >= PHP_INT_MAX / 100
            || ($result['status'] === 'updated' && ($result['updated'] === null || $result['updated'] === 0))
            || ($result['status'] === 'no_update' && $result['updated'] !== 0)) {
            return null;
        }
        [$status, $tone] = $statuses[$result['status']];
        return array_replace($event, [
            'status' => $status, 'tone' => $tone, 'start' => false,
            'dry' => $result['dry'], 'updated' => $result['updated'],
            'duration' => formatDuration($seconds),
        ]);
    }
    if ($parts[2] === 'INFO' && preg_match('/\ARoutine started \(dry=(True|False)\)\.\z/', $parts[4], $start) === 1) {
        return array_replace($event, ['status' => 'Outcome pending', 'tone' => 'pending', 'start' => true,
            'dry' => $start[1] === 'True']);
    }
    if (preg_match('/\ARoutine (finished in|failed after|deferred after) ([0-9]+(?:\.[0-9]+)?) seconds\.\z/', $parts[4], $outcome) !== 1) {
        return null;
    }
    $outcomes = [
        'finished in' => ['INFO', 'Result unavailable', 'unknown'],
        'failed after' => ['ERROR', 'Failed', 'failure'],
        'deferred after' => ['WARNING', 'Deferred', 'deferred'],
    ];
    [$level, $status, $tone] = $outcomes[$outcome[1]];
    $seconds = (float) $outcome[2];
    if ($parts[2] !== $level || !is_finite($seconds) || $seconds >= PHP_INT_MAX / 100) {
        return null;
    }
    $event['duration'] = formatDuration($seconds);
    return $event + ['status' => $status, 'tone' => $tone, 'start' => false];
}

/** List regular, valid daily logs newest first without following directory links. */
function dailyLogs(string $directory): array
{
    if (!regularDirectory($directory)) {
        return [];
    }
    $filenames = [];
    foreach (@scandir($directory) ?: [] as $filename) {
        if (logDate($filename) === null) {
            continue;
        }
        $stat = @lstat($directory . '/' . $filename);
        if ($stat !== false && ($stat['mode'] & 0170000) === 0100000) {
            $filenames[] = $filename;
        }
    }
    rsort($filenames, SORT_STRING);
    return $filenames;
}

/** Collect each routine's last observed event and recent outcomes from bounded tails. */
function dashboardActivity(string $directory): array
{
    $logs = dailyLogs($directory);
    $latest = [];
    $recent = [];
    $dryRuns = [];
    // Matching preview starts remain known across the UTC midnight boundary.
    foreach (array_reverse(array_slice($logs, 0, ACTIVITY_LOG_LIMIT)) as $filename) {
        $content = readRegularFile($directory . '/' . $filename, true);
        if ($content === null) {
            $dryRuns = [];
            continue;
        }
        // A start in an omitted prefix cannot safely describe a later outcome.
        $stat = @lstat($directory . '/' . $filename);
        if ($stat === false || $stat['size'] > READ_LIMIT) {
            $dryRuns = [];
        }
        foreach (preg_split('/\r?\n/', $content) as $line) {
            $event = parseEvent($line);
            if ($event === null || substr($event['timestamp'], 0, 10) !== logDate($filename)) {
                continue;
            }
            $routine = $event['routine'];
            if ($event['start']) {
                $dryRuns[$routine] = $event['dry'];
            } elseif ($event['dry'] === null) {
                $event['dry'] = $dryRuns[$routine] ?? null;
            }
            if (!$event['start']) {
                unset($dryRuns[$routine]);
            }
            if ($event['dry'] === true) {
                continue;
            }
            $event['filename'] = $filename;
            $latest[$routine] = $event;
            if (!$event['start']) {
                $recent[] = $event;
                if (count($recent) > RECENT_ACTIVITY_LIMIT) {
                    array_shift($recent);
                }
            }
        }
    }
    return ['latest' => $latest, 'recent' => array_reverse($recent), 'log_count' => count($logs)];
}
