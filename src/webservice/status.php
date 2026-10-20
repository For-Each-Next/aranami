<?php
/** Read source metadata, live scheduler state, and routine activity for the Toolforge dashboard. */
declare(strict_types=1);

const READ_LIMIT = 65536;
const STATUS_READ_LIMIT = 1048576;
const REPORT_STATUS_READ_LIMIT = 8388608;
const REPORT_STATUS_MAX_REPORTS = 1000;
/** Read bounded deployed configuration generated from shared Python definitions. */
function dashboardRoutines(?string $path = null): array
{
    $content = readRegularFile($path ?? __DIR__ . '/../shared/routines.json', READ_LIMIT + 1);
    if ($content !== null && strlen($content) > READ_LIMIT) {
        return [];
    }
    $data = $content === null ? null : json_decode($content, true, 16);
    if (!is_array($data) || ($data['schema'] ?? null) !== 1 || !is_array($data['routines'] ?? null)
        || count($data['routines']) > 32) {
        return [];
    }
    foreach ($data['routines'] as $key => $record) {
        if (!is_string($key) || preg_match('/\A[a-z][a-z0-9_]{0,63}\z/', $key) !== 1
            || !is_array($record) || !is_array($record['pages'] ?? null)
            || !is_array($record['prefixes'] ?? null) || count($record['pages']) + count($record['prefixes']) > 64) {
            return [];
        }
        foreach (array_merge(array_values($record['pages']), $record['prefixes']) as $title) {
            if (!is_string($title) || trim($title) === '' || strlen($title) > 800 || preg_match('/[\x00-\x1f\x7f]/', $title) === 1) {
                return [];
            }
        }
    }
    return $data['routines'];
}

/** Escape a value for HTML text or a quoted attribute. */
function html(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Render a localized job name with its configured Wikipedia report links. */
function dashboardRoutineLinks(string $routine, string $name, ?array $definitions = null): void
{
    $definition = ($definitions ?? dashboardRoutines())[$routine] ?? [];
    $pages = array_values($definition['pages'] ?? []);
    if ($pages === []) {
        $pages = $definition['prefixes'] ?? [];
    }
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

/** Read source deployment metadata without consulting installed distributions. */
function sourceVersion(?string $path = null): ?string
{
    $content = readRegularFile($path ?? __DIR__ . '/../shared/version.json', READ_LIMIT + 1);
    if ($content !== null && strlen($content) > READ_LIMIT) {
        return null;
    }
    $data = $content === null ? null : json_decode($content, true, 8);
    return is_array($data) && ($data['schema'] ?? null) === 1
        && is_string($data['version'] ?? null)
        && preg_match('/\A[0-9][0-9A-Za-z.!+_-]*\z/', $data['version']) === 1
        ? $data['version'] : null;
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
    if (is_string($value) && str_ends_with($value, '+00:00')) {
        $value = substr($value, 0, -6) . 'Z';
    }
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

/** Validate complete destination-update metadata without interpreting logs. */
function reportStatusEvent(string $key, mixed $record): ?array
{
    $fields = ['update_id', 'run_id', 'routine', 'site', 'title', 'status', 'dry',
        'started_at', 'finished_at', 'duration_seconds', 'confirmed_edits'];
    if (!is_array($record) || count($record) !== count($fields)
        || array_diff($fields, array_keys($record)) !== [] || ($record['dry'] ?? null) !== false
        || !is_string($record['update_id']) || preg_match('/\A[0-9a-f]{32}\z/', $record['update_id']) !== 1
        || !is_string($record['run_id']) || preg_match('/\A[0-9a-f]{32}\z/', $record['run_id']) !== 1
        || !is_string($record['routine']) || preg_match('/\A[a-z][a-z0-9_]{0,63}\z/', $record['routine']) !== 1
        || !is_string($record['site']) || preg_match('/\A[a-z][a-z0-9_]{0,63}\z/', $record['site']) !== 1
        || !is_string($record['title']) || trim($record['title']) === '' || strlen($record['title']) > 800
        || preg_match('/[\x00-\x1f\x7f]/', $record['title']) === 1
        || $key !== hash('sha256', $record['site'] . "\n" . $record['title'])
        || !in_array($record['status'], ['running', 'updated', 'no_update', 'failed', 'deferred', 'unconfirmed'], true)) {
        return null;
    }
    $started = statusTimestamp($record['started_at']);
    $edits = $record['confirmed_edits'];
    if ($started === null || ($edits !== null && (!is_int($edits) || $edits < 0))) {
        return null;
    }
    if ($record['status'] === 'running') {
        return $record['finished_at'] === null && $record['duration_seconds'] === null && $edits === null
            ? $record : null;
    }
    $finished = statusTimestamp($record['finished_at']);
    $seconds = $record['duration_seconds'];
    if ($finished === null || $finished < $started
        || (!is_int($seconds) && !is_float($seconds)) || !is_finite((float) $seconds)
        || $seconds < 0 || $seconds >= PHP_INT_MAX / 100
        || ($record['status'] === 'updated' && ($edits === null || $edits === 0))
        || (in_array($record['status'], ['no_update', 'deferred'], true) && $edits !== 0)
        || ($record['status'] === 'unconfirmed' && $edits !== null)) {
        return null;
    }
    return $record;
}

/** Read latest per-page outcomes from the backend's durable state directory. */
function dashboardReportUpdates(string $stateDirectory): array
{
    if (!regularDirectory($stateDirectory)) {
        return [];
    }
    $path = $stateDirectory . '/report-updates.json';
    $stat = @lstat($path);
    if ($stat === false || $stat['size'] > REPORT_STATUS_READ_LIMIT) {
        return [];
    }
    $content = readRegularFile($path, REPORT_STATUS_READ_LIMIT + 1);
    if ($content === null || strlen($content) > REPORT_STATUS_READ_LIMIT) {
        return [];
    }
    try {
        $snapshot = json_decode($content, true, 16, JSON_THROW_ON_ERROR);
    } catch (JsonException $exception) {
        return [];
    }
    if (!is_array($snapshot) || count($snapshot) !== 3 || ($snapshot['schema'] ?? null) !== 1
        || statusTimestamp($snapshot['updated_at'] ?? null) === null || !is_array($snapshot['reports'] ?? null)
        || count($snapshot['reports']) > REPORT_STATUS_MAX_REPORTS) {
        return [];
    }
    $reports = [];
    foreach ($snapshot['reports'] as $key => $record) {
        if (!is_string($key) || preg_match('/\A[0-9a-f]{64}\z/', $key) !== 1) {
            return [];
        }
        $event = reportStatusEvent($key, $record);
        if ($event === null) {
            return [];
        }
        $reports[$key] = $event;
    }
    return $reports;
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
    $path = $directory . '/routine-status.json';
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
    foreach ($snapshot['routines'] as $routine => $record) {
        if (!is_string($routine) || preg_match('/\A[a-z][a-z0-9_]{0,63}\z/', $routine) !== 1) {
            continue;
        }
        $event = routineStatusEvent($routine, $snapshot['routines'][$routine] ?? null);
        if ($event !== null) {
            $latest[$routine] = $event;
        }
    }
    return ['latest' => $latest];
}

/** Read actual scheduler predictions, suppressing stale or unavailable times. */
function dashboardSchedule(string $directory, ?DateTimeImmutable $now = null, ?array $definitions = null): array
{
    $unknown = ['state' => 'missing', 'routines' => [], 'updated_at' => null, 'started_at' => null];
    if (!regularDirectory($directory)) {
        return $unknown;
    }
    $content = readRegularFile($directory . '/schedule-status.json', STATUS_READ_LIMIT + 1);
    if ($content === null || strlen($content) > STATUS_READ_LIMIT) {
        return $unknown;
    }
    $data = json_decode($content, true, 16);
    $updated = statusTimestamp($data['updated_at'] ?? null);
    if (!is_array($data) || ($data['schema'] ?? null) !== 1
        || $updated === null || !in_array($data['state'] ?? null, ['running', 'paused', 'stopped'], true)
        || !is_string($data['config_id'] ?? null) || preg_match('/\A[0-9a-f]{64}\z/', $data['config_id']) !== 1
        || !is_array($data['routines'] ?? null) || count($data['routines']) > 32) {
        return $unknown;
    }
    $age = ($now ?? new DateTimeImmutable('now', new DateTimeZone('UTC')))->getTimestamp() - $updated->getTimestamp();
    if ($age < 0 || ($age > 120 && $data['state'] !== 'stopped')) {
        return ['state' => 'stale', 'routines' => [], 'updated_at' => $data['updated_at'], 'started_at' => null];
    }
    $routines = [];
    foreach ($definitions ?? dashboardRoutines() as $key => $definition) {
        $row = $data['routines'][$key] ?? null;
        if (!is_array($row) || !in_array($row['state'] ?? null, ['scheduled', 'paused', 'missing'], true)
            || !array_key_exists('next_run_at', $row)
            || ($row['next_run_at'] !== null && statusTimestamp($row['next_run_at']) === null)) {
            continue;
        }
        // A configuration change invalidates predictions from an older monitor.
        if (($row['pages'] ?? null) !== $definition['pages']
            || ($row['prefixes'] ?? null) !== $definition['prefixes']
            || ($row['hour'] ?? null) !== $definition['hour']
            || ($row['minute'] ?? null) !== $definition['minute']) {
            continue;
        }
        $routines[$key] = [
            'state' => $row['state'],
            'next_run_at' => $data['state'] === 'running' && $row['state'] === 'scheduled'
                ? $row['next_run_at'] : null,
        ];
    }
    $started = statusTimestamp($data['started_at'] ?? null);
    return [
        'state' => $data['state'], 'routines' => $routines, 'updated_at' => $data['updated_at'],
        'started_at' => $started !== null && $started <= $updated
            && in_array($data['state'], ['running', 'paused'], true) ? $data['started_at'] : null,
    ];
}

/** Show continuous monitor uptime only when a fresh snapshot confirms its start. */
function dashboardUptime(array $schedule, ?DateTimeImmutable $now = null, ?string $version = null): string
{
    $version ??= sourceVersion() ?? uiText('ops.unavailable');
    $clock = $now ?? new DateTimeImmutable('now', new DateTimeZone('UTC'));
    $started = statusTimestamp($schedule['started_at'] ?? null);
    $updated = statusTimestamp($schedule['updated_at'] ?? null);
    if (!in_array($schedule['state'] ?? null, ['running', 'paused'], true)
        || $started === null || $updated === null || $started > $updated || $updated > $clock
        || $clock->getTimestamp() - $updated->getTimestamp() > 120) {
        return uiText('footer.uptime_unknown', ['version' => $version]);
    }
    $minutes = (int) floor(((float) $clock->format('U.u') - (float) $started->format('U.u')) / 60);
    return uiText('footer.uptime', [
        'version' => $version,
        'days' => intdiv($minutes, 1440),
        'hours' => intdiv($minutes % 1440, 60),
        'minutes' => $minutes % 60,
        'since' => $started->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i'),
    ]);
}

/** Present an actual scheduler time or a localized missing-state explanation. */
function dashboardNextRun(array $schedule, string $routine): string
{
    $state = $schedule['state'];
    $row = $schedule['routines'][$routine] ?? null;
    if ($state === 'running' && ($row['state'] ?? null) === 'scheduled'
        && ($row['next_run_at'] ?? null) !== null) {
        return (new DateTimeImmutable($row['next_run_at']))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i') . ' UTC';
    }
    $label = match (true) {
        $state === 'stale' => 'ops.schedule_stale',
        $state === 'stopped' => 'ops.schedule_stopped',
        $state === 'paused' || ($row['state'] ?? null) === 'paused' => 'ops.schedule_paused',
        default => 'ops.schedule_missing',
    };
    return uiText($label);
}
