<?php
/** Serve major project logs and detailed Aranami logs through one private reader. */
declare(strict_types=1);

require __DIR__ . '/i18n.php';

/** Return the UTC date in an exact daily log filename, or null if invalid. */
function logFileDate(string $filename, string $kind): ?string
{
    if (!in_array($kind, ['project', 'aranami'], true)) {
        return null;
    }
    if (preg_match('/\A' . preg_quote($kind, '/') . '\.([0-9]{4})-([0-9]{2})-([0-9]{2})\.log\z/', $filename, $parts) !== 1) {
        return null;
    }
    if (!checkdate((int) $parts[2], (int) $parts[3], (int) $parts[1])) {
        return null;
    }
    return $parts[1] . '-' . $parts[2] . '-' . $parts[3];
}

/** Recognize existing log directories without following symlinks. */
function regularLogDirectory(string $directory): bool
{
    $stat = @lstat($directory);
    return $stat !== false && ($stat['mode'] & 0170000) === 0040000;
}

/** Return metadata only for a regular file, without following symlinks. */
function regularLog(string $directory, string $filename, string $kind): ?array
{
    if (logFileDate($filename, $kind) === null || !regularLogDirectory($directory)) {
        return null;
    }
    $stat = @lstat($directory . '/' . $filename);
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
        echo uiText('logs.not_found') . "\n";
    }
    exit;
}

/** Escape a string for HTML text or a quoted attribute. */
function logEscape(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Present log sizes using binary units while preserving a useful precision. */
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

/** List or stream an explicitly selected daily log from a trusted viewer. */
function serveLogs(string $kind, string $directory = '/data/project/aranami/logs'): void
{
    if (!in_array($kind, ['project', 'aranami'], true)) {
        throw new InvalidArgumentException('Unsupported log viewer.');
    }
    $isProject = $kind === 'project';
    $basePath = $isProject ? '/logs/' : '/logs/aranami/';
    header('Cache-Control: no-store');
    header('Vary: Accept-Language');
    header('Content-Language: ' . uiLanguageTag());
    header('X-Content-Type-Options: nosniff');
    header("Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'");

    $method = $_SERVER['REQUEST_METHOD'] ?? '';
    if (!in_array($method, ['GET', 'HEAD'], true)) {
        http_response_code(405);
        header('Allow: GET, HEAD');
        header('Content-Type: text/plain; charset=UTF-8');
        echo uiText('http.method_not_allowed') . "\n";
        exit;
    }

    if (array_key_exists('file', $_GET)) {
        $filename = $_GET['file'];
        if (!is_string($filename)) {
            logNotFound($method);
        }
        $stat = regularLog($directory, $filename, $kind);
        if ($stat === null) {
            logNotFound($method);
        }
        $stream = @fopen($directory . '/' . $filename, 'rb');
        if ($stream === false) {
            logNotFound($method);
        }
        $opened = fstat($stream);
        // Refuse a file replaced between validation and opening, including symlinks.
        if ($opened === false || ($opened['mode'] & 0170000) !== 0100000
            || $opened['dev'] !== $stat['dev'] || $opened['ino'] !== $stat['ino']) {
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
    $entries = regularLogDirectory($directory) ? @scandir($directory) : false;
    if ($entries !== false) {
        foreach ($entries as $filename) {
            $stat = regularLog($directory, $filename, $kind);
            if ($stat !== null) {
                $logs[$filename] = $stat;
            }
        }
    }
    krsort($logs, SORT_STRING);
?>
<!doctype html>
<html lang="<?= logEscape(uiLanguageTag()) ?>">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title><?= logEscape(uiText($isProject ? 'logs.project_page_title' : 'logs.page_title')) ?></title>
  <link rel="icon" href="/aranami.svg" type="image/svg+xml">
  <meta name="theme-color" content="#a8d9cb">
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, sans-serif; background: #fcfffe; color: #2d4b42; }
    body { max-width: 960px; box-sizing: border-box; margin: 3rem auto; padding: 0 1.25rem; line-height: 1.6; }
    h1 { margin-bottom: 0.25rem; font-size: 2rem; letter-spacing: -0.03em; }
    .intro, footer { opacity: 0.75; }
    a { color: #316e5e; text-underline-offset: 0.2em; }
    a:hover { text-decoration-thickness: 0.15em; }
    table { width: 100%; margin: 2rem 0; border-collapse: collapse; }
    th, td { padding: 0.8rem 0.5rem; border-bottom: 1px solid #d9ebe4; text-align: left; }
    thead { background: #edf9f4; }
    th:last-child, td:last-child { text-align: right; white-space: nowrap; }
    .modified { font-variant-numeric: tabular-nums; }
    .modified time span { display: inline-block; white-space: nowrap; }
    code { overflow-wrap: anywhere; }
    footer { margin-top: 2rem; font-size: 0.9rem; }
    @media (max-width: 36rem) { body { margin-top: 1.5rem; } .filename { display: none; } }
  </style>
</head>
<body>
  <main>
    <a href="<?= logEscape(uiUrl('/')) ?>">← <?= logEscape(uiText('logs.back')) ?></a>
    <h1><?= logEscape(uiText($isProject ? 'logs.project_heading' : 'logs.heading')) ?></h1>
<?php if ($isProject): ?>
    <p class="intro"><?= logEscape(uiText('logs.project_intro')) ?></p>
    <p><a href="/logs/aranami/"><?= logEscape(uiText('logs.detailed_link')) ?></a></p>
<?php else: ?>
    <p><?= logEscape(uiText('logs.intro_before')) ?> <a href="https://zh.wikipedia.org/wiki/WikiProject:%E7%94%B5%E5%AD%90%E6%B8%B8%E6%88%8F"><?= logEscape(uiText('logs.project')) ?></a><?= logEscape(uiText('logs.intro_after')) ?></p>
    <p><a href="/logs/"><?= logEscape(uiText('logs.project_link')) ?></a></p>
<?php endif; ?>
<?php if ($logs === []): ?>
    <p><?= logEscape(uiText($isProject ? 'logs.project_empty' : 'logs.empty')) ?></p>
<?php else: ?>
    <table>
      <thead><tr><th scope="col"><?= logEscape(uiText('logs.column_date')) ?></th><th class="filename" scope="col"><?= logEscape(uiText('logs.column_file')) ?></th><th scope="col"><?= logEscape(uiText('logs.column_modified')) ?></th><th scope="col"><?= logEscape(uiText('logs.column_size')) ?></th></tr></thead>
      <tbody>
<?php foreach ($logs as $filename => $stat): ?>
        <tr>
          <td><a href="<?= logEscape($basePath . rawurlencode($filename)) ?>"><?= logEscape((string) logFileDate($filename, $kind)) ?></a></td>
          <td class="filename"><code><?= logEscape($filename) ?></code></td>
          <td class="modified"><time datetime="<?= logEscape(gmdate('Y-m-d\TH:i:s\Z', $stat['mtime'])) ?>"><span><?= logEscape(gmdate('Y-m-d', $stat['mtime'])) ?></span> <span><?= logEscape(gmdate('H:i:s', $stat['mtime'])) ?></span></time></td>
          <td title="<?= logEscape(uiText('logs.bytes', ['count' => number_format($stat['size'])])) ?>"><?= logEscape(readableLogSize($stat['size'])) ?></td>
        </tr>
<?php endforeach; ?>
      </tbody>
    </table>
<?php endif; ?>
    <footer><?= logEscape(uiText($isProject ? 'logs.project_footer' : 'logs.footer')) ?></footer>
  </main>
</body>
</html>
<?php
}
