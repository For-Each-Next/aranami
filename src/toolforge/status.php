<?php
/** Read installed metadata and routine activity for the Toolforge dashboard. */
declare(strict_types=1);

const READ_LIMIT = 65536;
const STATUS_READ_LIMIT = 1048576;
const ROUTINES = [
    'dyks' => 'DYK report',
    'new_pages' => 'New-page report',
    'assessment_lists' => 'Assessment lists',
    'pageviews' => 'Pageviews report',
    'enwp_key_articles' => 'English article comparisons',
    'pexbot' => 'PexBot reports',
];
// Keep these destinations aligned with TASK_DEFINITIONS in aranami/monitor.py.
// PexBot selects subscribed descendants dynamically, so link its configured root.
const ROUTINE_REPORT_PAGES = [
    'dyks' => ['WikiProject:电子游戏/新条目推荐'],
    'new_pages' => ['WikiProject:电子游戏/新进条目/关键词筛选'],
    'assessment_lists' => [
        'WikiProject:电子游戏/认证条目/Bplus',
        'WikiProject:电子游戏/认证条目/BPAN',
        'WikiProject:电子游戏/认证条目/A',
        'WikiProject:电子游戏/认证条目/AL',
        'WikiProject:电子游戏/认证条目/ACC',
        'WikiProject:电子游戏/认证条目/PPR',
    ],
    'pageviews' => ['WikiProject:电子游戏/热门条目'],
    'enwp_key_articles' => [
        'WikiProject:电子游戏/数据库报告/英文维基百科重要条目',
        'WikiProject:电子游戏/数据库报告/英文维基百科优质条目',
    ],
    'pexbot' => ['WikiProject:电子游戏/数据库报告'],
];

/** Escape a value for HTML text or a quoted attribute. */
function html(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Render a localized job name with its configured Wikipedia report links. */
function dashboardRoutineLinks(string $routine, string $name): void
{
    $pages = ROUTINE_REPORT_PAGES[$routine] ?? [];
    if ($pages === []) {
        echo html($name);
        return;
    }
    $multiple = count($pages) > 1;
    if ($multiple) {
        echo html($name) . (uiLocale() === 'en' ? ' (' : '（');
    }
    foreach ($pages as $index => $title) {
        $number = (string) ($index + 1);
        $label = $multiple ? $number : $name;
        $description = ($multiple ? $number . ' · ' : '') . $name . ' · ' . $title;
        $url = 'https://zh.wikipedia.org/wiki/' . rawurlencode(str_replace(' ', '_', $title));
        echo ($multiple && $index > 0 ? ' ' : '') . '<a href="' . html($url)
            . '" title="' . html($description) . '" aria-label="' . html($description)
            . '">' . html($label) . '</a>';
    }
    if ($multiple) {
        echo uiLocale() === 'en' ? ')' : '）';
    }
}

/** Check a directory without following a symlink at that path. */
function regularDirectory(string $path): bool
{
    $stat = @lstat($path);
    return $stat !== false && ($stat['mode'] & 0170000) === 0040000;
}

/** Read a bounded regular file prefix without following symlinks. */
function readRegularFile(string $path, int $limit = READ_LIMIT): ?string
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
    $content = stream_get_contents($stream, $limit);
    fclose($stream);
    if ($content === false) {
        return null;
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

/** Parse an exact UTC timestamp with optional microseconds. */
function statusTimestamp(mixed $value): ?DateTimeImmutable
{
    if (!is_string($value)
        || preg_match('/\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\z/', $value) !== 1) {
        return null;
    }
    $format = str_contains($value, '.') ? '!Y-m-d\TH:i:s.u\Z' : '!Y-m-d\TH:i:s\Z';
    $timestamp = DateTimeImmutable::createFromFormat($format, $value, new DateTimeZone('UTC'));
    $errors = DateTimeImmutable::getLastErrors();
    return $timestamp !== false && ($errors === false
        || ($errors['warning_count'] === 0 && $errors['error_count'] === 0)) ? $timestamp : null;
}

/** Validate one reported routine state without deriving outcomes from diagnostics. */
function routineStatusEvent(string $routine, mixed $record): ?array
{
    $statuses = [
        'running' => 'Running', 'updated' => 'Updated', 'no_update' => 'NoUpdate',
        'failed' => 'Failed', 'deferred' => 'Deferred', 'unknown' => 'Result unavailable',
    ];
    if (!is_array($record) || ($record['routine'] ?? null) !== $routine
        || ($record['dry'] ?? null) !== false
        || !is_string($record['status'] ?? null) || !isset($statuses[$record['status']])
        || !is_string($record['run_id'] ?? null)
        || preg_match('/\A[0-9a-f]{32}\z/', $record['run_id']) !== 1
        || !array_key_exists('updated', $record)
        || ($record['updated'] !== null && (!is_int($record['updated']) || $record['updated'] < 0))
        || !array_key_exists('duration_seconds', $record) || !array_key_exists('finished_at', $record)) {
        return null;
    }
    $started = statusTimestamp($record['started_at'] ?? null);
    if ($started === null) {
        return null;
    }
    $running = $record['status'] === 'running';
    $duration = null;
    if ($running) {
        if ($record['updated'] !== null || $record['duration_seconds'] !== null || $record['finished_at'] !== null) {
            return null;
        }
    } else {
        $finished = statusTimestamp($record['finished_at']);
        $seconds = $record['duration_seconds'];
        if ($finished === null || $finished < $started
            || (!is_int($seconds) && !is_float($seconds)) || !is_finite((float) $seconds)
            || $seconds < 0 || $seconds >= PHP_INT_MAX / 100) {
            return null;
        }
        $duration = formatDuration((float) $seconds);
    }
    return [
        'routine' => $routine, 'status' => $statuses[$record['status']],
        'duration' => $duration, 'timestamp' => $record[$running ? 'started_at' : 'finished_at'],
    ];
}

/** Read the latest live states published by Aranami; logs remain diagnostics only. */
function dashboardActivity(string $directory): array
{
    $empty = ['latest' => []];
    if (!regularDirectory($directory)) {
        return $empty;
    }
    $path = $directory . '/aranami-status.json';
    $stat = @lstat($path);
    if ($stat === false || $stat['size'] > STATUS_READ_LIMIT) {
        return $empty;
    }
    $content = readRegularFile($path, STATUS_READ_LIMIT + 1);
    if ($content === null || strlen($content) > STATUS_READ_LIMIT) {
        return $empty;
    }
    try {
        $snapshot = json_decode($content, true, 16, JSON_THROW_ON_ERROR);
    } catch (JsonException $exception) {
        return $empty;
    }
    if (!is_array($snapshot) || ($snapshot['schema'] ?? null) !== 1
        || statusTimestamp($snapshot['updated_at'] ?? null) === null
        || !is_array($snapshot['routines'] ?? null)) {
        return $empty;
    }
    $latest = [];
    foreach (ROUTINES as $routine => $name) {
        $event = routineStatusEvent($routine, $snapshot['routines'][$routine] ?? null);
        if ($event !== null) {
            $latest[$routine] = $event;
        }
    }
    return ['latest' => $latest];
}
