<?php
/** Browse bounded natural-language daily logs without interpreting runtime state. */
declare(strict_types=1);

require __DIR__ . '/i18n.php';

const LOG_MAX_FILE_BYTES = 128 * 1024 * 1024;
const LOG_SCAN_BYTES = 1024 * 1024;
const LOG_LINE_BYTES = 16 * 1024;
const LOG_MAX_LINES = 500;
const LOG_DISPLAY_BYTES = 256 * 1024;
const LOG_QUERY_BYTES = 1024;

/** Return the UTC date in one exact universal daily-log filename. */
function logFileDate(string $filename): ?string
{
    if (preg_match('/\Aaranami\.([0-9]{4})-([0-9]{2})-([0-9]{2})\.log\z/', $filename, $parts) !== 1
        || !checkdate((int) $parts[2], (int) $parts[3], (int) $parts[1])) {
        return null;
    }
    return $parts[1] . '-' . $parts[2] . '-' . $parts[3];
}

/** Recognize an existing log directory without following a directory symlink. */
function regularLogDirectory(string $directory): bool
{
    $stat = @lstat($directory);
    return $stat !== false && ($stat['mode'] & 0170000) === 0040000;
}

/** Return metadata for a bounded regular daily file without following symlinks. */
function regularLog(string $directory, string $filename): ?array
{
    if (logFileDate($filename) === null || !regularLogDirectory($directory)) {
        return null;
    }
    $stat = @lstat($directory . '/' . $filename);
    if ($stat === false || ($stat['mode'] & 0170000) !== 0100000
        || $stat['size'] < 0 || $stat['size'] > LOG_MAX_FILE_BYTES) {
        return null;
    }
    return $stat;
}

/** Open the validated file and refuse a replacement between checking and opening. */
function openDailyLog(string $directory, string $filename)
{
    $stat = regularLog($directory, $filename);
    if ($stat === null) {
        return null;
    }
    $stream = @fopen($directory . '/' . $filename, 'rb');
    if ($stream === false) {
        return null;
    }
    $opened = fstat($stream);
    if ($opened === false || ($opened['mode'] & 0170000) !== 0100000
        || $opened['dev'] !== $stat['dev'] || $opened['ino'] !== $stat['ino']
        || $opened['size'] > LOG_MAX_FILE_BYTES) {
        fclose($stream);
        return null;
    }
    return $stream;
}

/** List only universal daily logs, newest UTC date first. */
function availableLogs(string $directory): array
{
    $logs = [];
    if (!regularLogDirectory($directory)) {
        return $logs;
    }
    $entries = @scandir($directory);
    if ($entries === false) {
        return $logs;
    }
    foreach ($entries as $filename) {
        $stat = regularLog($directory, $filename);
        if ($stat !== null) {
            $logs[$filename] = $stat;
        }
    }
    krsort($logs, SORT_STRING);
    return $logs;
}

/** Validate one UTC date, literal UTF-8 text query, and optional download request. */
function logSelection(array $parameters, array $available): array
{
    if (array_diff(array_keys($parameters), ['date', 'q', 'download']) !== []) {
        throw new InvalidArgumentException('Unsupported log query.');
    }
    $date = $parameters['date'] ?? null;
    $query = $parameters['q'] ?? '';
    if (($date !== null && !is_string($date)) || !is_string($query)
        || strlen($query) > LOG_QUERY_BYTES || preg_match('//u', $query) !== 1
        || (isset($parameters['download']) && $parameters['download'] !== '1')) {
        throw new InvalidArgumentException('Invalid log query.');
    }
    if ($date === null) {
        $latest = array_key_first($available);
        $date = $latest === null ? null : logFileDate($latest);
    }
    $filename = $date === null ? null : 'aranami.' . $date . '.log';
    if ($filename !== null && logFileDate($filename) !== $date) {
        throw new InvalidArgumentException('Invalid UTC log date.');
    }
    return ['date' => $date, 'filename' => $filename, 'query' => trim($query),
        'download' => isset($parameters['download'])];
}

/** Read a bounded literal-text excerpt without loading a complete daily file. */
function readLogExcerpt(string $directory, string $filename, string $query = ''): ?array
{
    if (strlen($query) > LOG_QUERY_BYTES || preg_match('//u', $query) !== 1) {
        throw new InvalidArgumentException('Invalid log query.');
    }
    $stream = openDailyLog($directory, $filename);
    if ($stream === null) {
        return null;
    }
    try {
        $size = (int) fstat($stream)['size'];
        $start = max(0, $size - LOG_SCAN_BYTES);
        if (fseek($stream, $start) !== 0) {
            return null;
        }
        $remaining = $size - $start;
        if ($start > 0) {
            $discard = fgets($stream, min(LOG_LINE_BYTES + 1, $remaining + 1));
            $remaining -= $discard === false ? 0 : strlen($discard);
        }
        $lines = new SplQueue();
        $displayBytes = 0;
        $truncated = $start > 0;
        while ($remaining > 0) {
            $line = fgets($stream, min(LOG_LINE_BYTES + 1, $remaining + 1));
            if ($line === false) {
                break;
            }
            $remaining -= strlen($line);
            if ($query !== '' && stripos($line, $query) === false) {
                continue;
            }
            $lines->enqueue($line);
            $displayBytes += strlen($line);
            while ($lines->count() > LOG_MAX_LINES || $displayBytes > LOG_DISPLAY_BYTES) {
                $displayBytes -= strlen($lines->dequeue());
                $truncated = true;
            }
        }
        return ['lines' => array_map(
            static fn (string $line): string => rtrim($line, "\r\n"),
            iterator_to_array($lines)
        ), 'truncated' => $truncated];
    } finally {
        fclose($stream);
    }
}

/** Escape text for HTML content or a quoted attribute. */
function logEscape(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Present binary file sizes with useful precision. */
function readableLogSize(int $bytes): string
{
    $size = (float) $bytes;
    $units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    $unit = 0;
    while ($size >= 1024 && $unit < count($units) - 1) {
        $size /= 1024;
        $unit++;
    }
    return ($unit === 0 ? (string) $bytes : number_format($size, 1)) . ' ' . $units[$unit];
}

/** Return a localized error without exposing runtime filesystem paths. */
function logRequestError(int $status, string $message): void
{
    http_response_code($status);
    header('Content-Type: text/plain; charset=UTF-8');
    echo uiText($message) . "\n";
}

/** Serve the single date-and-text log browser or an exact full-file download. */
function serveLogs(string $directory = '/data/project/aranami/logs'): void
{
    header('Cache-Control: no-store');
    header('Vary: Accept-Language');
    header('Content-Language: ' . uiLanguageTag());
    header('X-Content-Type-Options: nosniff');
    header("Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET') {
        header('Allow: GET');
        logRequestError(405, 'http.method_not_allowed');
        return;
    }
    $logs = availableLogs($directory);
    try {
        $selection = logSelection($_GET, $logs);
    } catch (InvalidArgumentException) {
        logRequestError(400, 'logs.invalid_query');
        return;
    }
    $filename = $selection['filename'];
    if ($filename !== null && !isset($logs[$filename])) {
        logRequestError(404, 'logs.not_found');
        return;
    }
    if ($selection['download']) {
        $stream = $filename === null ? null : openDailyLog($directory, $filename);
        if ($stream === null) {
            logRequestError(404, 'logs.not_found');
            return;
        }
        $size = (int) fstat($stream)['size'];
        header('Content-Type: text/plain; charset=UTF-8');
        header('Content-Disposition: attachment; filename="' . $filename . '"');
        header('Content-Length: ' . $size);
        $output = fopen('php://output', 'wb');
        stream_copy_to_stream($stream, $output, $size);
        fclose($stream);
        fclose($output);
        return;
    }
    $excerpt = $filename === null ? null : readLogExcerpt($directory, $filename, $selection['query']);
    if ($filename !== null && $excerpt === null) {
        logRequestError(404, 'logs.not_found');
        return;
    }
    header('Content-Type: text/html; charset=UTF-8');
?>
<!doctype html>
<html lang="<?= logEscape(uiLanguageTag()) ?>">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title><?= logEscape(uiText('logs.page_title')) ?></title>
  <link rel="icon" href="/aranami.svg" type="image/svg+xml">
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; background: #fcfffe; color: #2d4b42; }
    body { max-width: 1100px; box-sizing: border-box; margin: 2rem auto; padding: 0 1.25rem; line-height: 1.6; }
    a { color: #316e5e; text-underline-offset: 0.2em; }
    .intro, footer { opacity: 0.75; }
    form { display: flex; flex-wrap: wrap; gap: 1rem; align-items: end; margin: 1.5rem 0; }
    label { display: grid; gap: 0.3rem; }
    select, input, button { box-sizing: border-box; max-width: 100%; padding: 0.5rem; font: inherit; }
    .search { flex: 1 1 18em; min-width: 0; }
    pre { padding: 1rem; border: 1px solid #d9ebe4; border-radius: 0.5rem; background: white; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 0.85rem; }
    table { width: 100%; border-collapse: collapse; margin: 2rem 0; }
    th, td { text-align: left; padding: 0.6rem 0.4rem; border-bottom: 1px solid #d9ebe4; }
    footer { margin-top: 2rem; }
    @media (max-width: 36rem) { .filename { display: none; } }
  </style>
</head>
<body>
<main>
  <a href="/"><?= logEscape(uiText('logs.back')) ?></a>
  <h1><?= logEscape(uiText('logs.heading')) ?></h1>
  <p class="intro"><?= logEscape(uiText('logs.intro')) ?></p>
<?php if ($logs === []): ?>
  <p><?= logEscape(uiText('logs.empty')) ?></p>
<?php else: ?>
  <form action="/logs/" method="get">
    <label><?= logEscape(uiText('logs.column_date')) ?>
      <select name="date">
<?php foreach ($logs as $name => $stat): $date = (string) logFileDate($name); ?>
        <option value="<?= logEscape($date) ?>"<?= $date === $selection['date'] ? ' selected' : '' ?>><?= logEscape($date) ?></option>
<?php endforeach; ?>
      </select>
    </label>
    <label class="search"><?= logEscape(uiText('logs.query')) ?>
      <input type="search" name="q" maxlength="256" value="<?= logEscape($selection['query']) ?>">
    </label>
    <button type="submit"><?= logEscape(uiText('logs.apply')) ?></button>
  </form>
  <p><a href="<?= logEscape(uiUrl('/logs/', ['date' => $selection['date'], 'download' => '1'])) ?>"><?= logEscape(uiText('logs.download')) ?></a>
    · <?= logEscape(readableLogSize((int) $logs[$filename]['size'])) ?></p>
  <p class="intro"><?= logEscape(uiText('logs.excerpt_note')) ?></p>
<?php if ($excerpt['lines'] === []): ?>
  <p><?= logEscape(uiText('logs.no_matches')) ?></p>
<?php else: ?>
  <pre><?= logEscape(implode("\n", $excerpt['lines'])) ?></pre>
<?php endif; ?>
<?php if ($excerpt['truncated']): ?>
  <p><?= logEscape(uiText('logs.truncated')) ?></p>
<?php endif; ?>
  <table>
    <thead><tr><th><?= logEscape(uiText('logs.column_date')) ?></th><th class="filename"><?= logEscape(uiText('logs.column_file')) ?></th><th><?= logEscape(uiText('logs.column_size')) ?></th></tr></thead>
    <tbody>
<?php foreach ($logs as $name => $stat): ?>
      <tr>
        <td><a href="<?= logEscape(uiUrl('/logs/', ['date' => logFileDate($name)])) ?>"><?= logEscape((string) logFileDate($name)) ?></a></td>
        <td class="filename"><code><?= logEscape($name) ?></code></td>
        <td><?= logEscape(readableLogSize((int) $stat['size'])) ?></td>
      </tr>
<?php endforeach; ?>
    </tbody>
  </table>
<?php endif; ?>
  <footer><?= logEscape(uiText('logs.footer')) ?></footer>
</main>
</body>
</html>
<?php
}
