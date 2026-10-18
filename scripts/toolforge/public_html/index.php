<?php
/** Show the installed Aranami version and its latest routine outcome and duration. */
declare(strict_types=1);

const TOOL_DIRECTORY = '/data/project/aranami';
const LOG_DIRECTORY = TOOL_DIRECTORY . '/logs';
const READ_LIMIT = 65536;
const ACTIVITY_LOG_LIMIT = 91;

header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'");

$method = $_SERVER['REQUEST_METHOD'] ?? '';
if (!in_array($method, ['GET', 'HEAD'], true)) {
    http_response_code(405);
    header('Allow: GET, HEAD');
    header('Content-Type: text/plain; charset=UTF-8');
    echo "Method not allowed.\n";
    exit;
}

header('Content-Type: text/html; charset=UTF-8');
if ($method === 'HEAD') {
    exit;
}

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
function readRegularFile(string $path, bool $tail = false): ?string
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
    $offset = $tail ? max(0, $opened['size'] - READ_LIMIT) : 0;
    if ($offset > 0 && fseek($stream, $offset) !== 0) {
        fclose($stream);
        return null;
    }
    $content = stream_get_contents($stream, READ_LIMIT);
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
function installedVersion(): ?string
{
    $library = TOOL_DIRECTORY . '/pyvenv/lib';
    if (!regularDirectory(TOOL_DIRECTORY . '/pyvenv') || !regularDirectory($library)) {
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

/** Format elapsed seconds as minutes and seconds rounded to hundredths. */
function formatDuration(float $seconds): string
{
    $hundredths = (int) round($seconds * 100);
    $minutes = intdiv($hundredths, 6000);
    $remaining = number_format(($hundredths % 6000) / 100, 2, '.', '');
    return ($minutes > 0 ? $minutes . ' min ' : '') . $remaining . ' sec';
}

/** Recognize a named routine outcome and elapsed time from its lifecycle record. */
function parseAction(string $line): ?array
{
    if (preg_match('/\A([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}Z) ([A-Z]+) \[aranami\.jobs\.([a-z_]+)\.job_run\]: Routine (finished in|failed after|deferred after) ([0-9]+(?:\.[0-9]+)?) seconds\.\z/', rtrim($line, "\r\n"), $parts) !== 1) {
        return null;
    }
    $names = [
        'dyks' => 'DYK report',
        'new_pages' => 'New-page report',
        'assessment_lists' => 'Assessment lists',
        'pageviews' => 'Pageviews report',
        'enwp_key_articles' => 'English article comparisons',
        'pexbot' => 'PexBot reports',
    ];
    $outcomes = [
        'finished in' => ['INFO', 'Completed', 'in'],
        'failed after' => ['ERROR', 'Failed', 'after'],
        'deferred after' => ['WARNING', 'Deferred', 'after'],
    ];
    $outcome = $outcomes[$parts[4]];
    $seconds = (float) $parts[5];
    if (!isset($names[$parts[3]]) || $parts[2] !== $outcome[0]
        || !is_finite($seconds) || $seconds >= PHP_INT_MAX / 100) {
        return null;
    }
    return [
        'timestamp' => $parts[1],
        'level' => $parts[2],
        'action' => $names[$parts[3]],
        'status' => $outcome[1],
        'preposition' => $outcome[2],
        'duration' => formatDuration($seconds),
    ];
}

/** Find the latest routine outcome in bounded tails of recent daily logs. */
function latestActivity(): ?array
{
    if (!regularDirectory(LOG_DIRECTORY)) {
        return null;
    }
    $filenames = [];
    foreach (@scandir(LOG_DIRECTORY) ?: [] as $filename) {
        if (logDate($filename) === null) {
            continue;
        }
        $stat = @lstat(LOG_DIRECTORY . '/' . $filename);
        if ($stat !== false && ($stat['mode'] & 0170000) === 0100000) {
            $filenames[] = $filename;
        }
    }
    rsort($filenames, SORT_STRING);
    foreach (array_slice($filenames, 0, ACTIVITY_LOG_LIMIT) as $filename) {
        $content = readRegularFile(LOG_DIRECTORY . '/' . $filename, true);
        if ($content === null) {
            continue;
        }
        $lines = preg_split('/\r?\n/', $content);
        for ($index = count($lines) - 1; $index >= 0; $index--) {
            $action = parseAction($lines[$index]);
            if ($action !== null) {
                return ['filename' => $filename] + $action;
            }
        }
    }
    return null;
}

$version = installedVersion();
$activity = latestActivity();
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>Aranami</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width: 56rem; margin: 3rem auto; padding: 0 1.25rem; line-height: 1.6; }
    h1 { margin-bottom: 0.25rem; font-size: 2rem; letter-spacing: -0.03em; }
    h2 { margin-top: 2rem; font-size: 1.25rem; }
    .intro, footer { opacity: 0.75; }
    a { color: #1767b5; text-underline-offset: 0.2em; }
    a:hover { text-decoration-thickness: 0.15em; }
    .activity { padding: 1.25rem; border: 1px solid #8885; border-radius: 0.5rem; }
    .activity p:first-child { margin-top: 0; }
    .activity p:last-child { margin-bottom: 0; }
    .message { white-space: pre-wrap; overflow-wrap: anywhere; }
    code { overflow-wrap: anywhere; }
    footer { margin-top: 2rem; font-size: 0.9rem; }
    @media (prefers-color-scheme: dark) { a { color: #8bc3ff; } }
    @media (max-width: 36rem) { body { margin-top: 1.5rem; } }
  </style>
</head>
<body>
  <main>
    <h1>Aranami</h1>
    <p class="intro">Aranami is a tool updating on-wiki reports primarily for <a href="https://zh.wikipedia.org/wiki/WikiProject:%E7%94%B5%E5%AD%90%E6%B8%B8%E6%88%8F">WikiProject Video games on the Chinese Wikipedia</a>.</p>
    <p>Installed version: <strong><?= html($version ?? 'Unavailable') ?></strong></p>
    <h2>Latest action</h2>
<?php if ($activity === null): ?>
    <p>No recent action is available.</p>
<?php else: ?>
    <div class="activity">
      <p><time datetime="<?= html(str_replace(' ', 'T', $activity['timestamp'])) ?>"><?= html(str_replace('Z', ' UTC', $activity['timestamp'])) ?></time></p>
      <p class="message"><?= html($activity['action']) ?> — <?= html($activity['status']) ?> <?= html($activity['preposition']) ?> <?= html($activity['duration']) ?></p>
      <p><a href="/logs/<?= html(rawurlencode($activity['filename'])) ?>">Read the daily log</a></p>
    </div>
<?php endif; ?>
    <p><a href="/logs/">Browse all daily logs</a></p>
    <footer>This page refreshes every 30 seconds. All times are UTC.</footer>
  </main>
</body>
</html>
