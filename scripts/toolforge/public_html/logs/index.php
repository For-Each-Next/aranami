<?php
/** List Aranami's daily logs and stream an explicitly selected log as text. */
declare(strict_types=1);

const LOG_DIRECTORY = '/data/project/aranami/logs';

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

/** Return the UTC date in an exact daily log filename, or null if invalid. */
function logDate(string $filename): ?string
{
    if (preg_match('/\Aaranami\.([0-9]{4})-([0-9]{2})-([0-9]{2})\.log\z/', $filename, $parts) !== 1) {
        return null;
    }
    if (!checkdate((int) $parts[2], (int) $parts[3], (int) $parts[1])) {
        return null;
    }
    return $parts[1] . '-' . $parts[2] . '-' . $parts[3];
}

/** Return metadata only for a regular file, without following symlinks. */
function regularLog(string $filename): ?array
{
    if (logDate($filename) === null) {
        return null;
    }
    $stat = @lstat(LOG_DIRECTORY . '/' . $filename);
    if ($stat === false || ($stat['mode'] & 0170000) !== 0100000) {
        return null;
    }
    return $stat;
}

/** Respond without disclosing local filesystem details. */
function logNotFound(string $method): void
{
    http_response_code(404);
    header('Content-Type: text/plain; charset=UTF-8');
    if ($method !== 'HEAD') {
        echo "Log not found.\n";
    }
    exit;
}

/** Escape a string for HTML text or a quoted attribute. */
function html(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Present log sizes using binary units while preserving a useful precision. */
function readableSize(int $bytes): string
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

if (array_key_exists('file', $_GET)) {
    $filename = $_GET['file'];
    if (!is_string($filename)) {
        logNotFound($method);
    }
    $stat = regularLog($filename);
    if ($stat === null) {
        logNotFound($method);
    }
    $stream = @fopen(LOG_DIRECTORY . '/' . $filename, 'rb');
    if ($stream === false) {
        logNotFound($method);
    }
    $opened = fstat($stream);
    // Refuse a file replaced between validation and opening, including symlinks.
    if ($opened === false || $opened['dev'] !== $stat['dev'] || $opened['ino'] !== $stat['ino']) {
        fclose($stream);
        logNotFound($method);
    }
    header('Content-Type: text/plain; charset=UTF-8');
    if ($method !== 'HEAD') {
        fpassthru($stream);
    }
    fclose($stream);
    exit;
}

header('Content-Type: text/html; charset=UTF-8');
if ($method === 'HEAD') {
    exit;
}

$logs = [];
$entries = @scandir(LOG_DIRECTORY);
if ($entries !== false) {
    foreach ($entries as $filename) {
        $stat = regularLog($filename);
        if ($stat !== null) {
            $logs[$filename] = $stat;
        }
    }
}
krsort($logs, SORT_STRING);
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aranami logs</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width: 56rem; margin: 3rem auto; padding: 0 1.25rem; line-height: 1.6; }
    h1 { margin-bottom: 0.25rem; font-size: 2rem; letter-spacing: -0.03em; }
    .intro, footer { opacity: 0.75; }
    a { color: #1767b5; text-underline-offset: 0.2em; }
    a:hover { text-decoration-thickness: 0.15em; }
    table { width: 100%; margin: 2rem 0; border-collapse: collapse; }
    th, td { padding: 0.8rem 0.5rem; border-bottom: 1px solid #8885; text-align: left; }
    th:last-child, td:last-child { text-align: right; white-space: nowrap; }
    code { overflow-wrap: anywhere; }
    footer { margin-top: 2rem; font-size: 0.9rem; }
    @media (prefers-color-scheme: dark) { a { color: #8bc3ff; } }
    @media (max-width: 36rem) { body { margin-top: 1.5rem; } .filename { display: none; } }
  </style>
</head>
<body>
  <main>
    <h1>Log</h1>
    <p>Aranami is a tool updating on-wiki reports primarily for <a href="https://zh.wikipedia.org/wiki/WikiProject:%E7%94%B5%E5%AD%90%E6%B8%B8%E6%88%8F">WikiProject Video games on the Chinese Wikipedia</a>.</p>
<?php if ($logs === []): ?>
    <p>No daily logs are available yet. Refresh after Aranami writes its first log.</p>
<?php else: ?>
    <table>
      <thead><tr><th scope="col">Date (UTC)</th><th class="filename" scope="col">Log file</th><th scope="col">Size</th></tr></thead>
      <tbody>
<?php foreach ($logs as $filename => $stat): ?>
        <tr>
          <td><a href="<?= html(rawurlencode($filename)) ?>"><?= html((string) logDate($filename)) ?></a></td>
          <td class="filename"><code><?= html($filename) ?></code></td>
          <td title="<?= html(number_format($stat['size'])) ?> bytes"><?= html(readableSize($stat['size'])) ?></td>
        </tr>
<?php endforeach; ?>
      </tbody>
    </table>
<?php endif; ?>
    <footer>Logs update as Aranami runs. Reload a log to see new entries.</footer>
  </main>
</body>
</html>
