<?php
/** Reuse bounded homepage HTML while its local input files remain unchanged. */
declare(strict_types=1);

const PAGE_CACHE_TTL = 60;
const PAGE_CACHE_READ_LIMIT = 8388608;

/** Cache only ordinary GET requests without any query parameters. */
function homepageCacheRequest(string $method, array $query, string $queryString): bool
{
    return $method === 'GET' && $query === [] && $queryString === '';
}

/** Identify regular inputs, including atomic replacements and absent optional files. */
function homepageCacheKey(array $requiredPaths, array $optionalPaths = []): ?string
{
    $identity = [];
    foreach (array_merge($requiredPaths, $optionalPaths) as $path) {
        if (!regularDirectory(dirname($path)) || !regularDirectory(dirname($path, 2))) {
            return null;
        }
        clearstatcache(true, $path);
        $stat = @lstat($path);
        if ($stat === false && in_array($path, $optionalPaths, true)) {
            $identity[$path] = null;
            continue;
        }
        if ($stat === false || ($stat['mode'] & 0170000) !== 0100000) {
            return null;
        }
        $identity[$path] = array_intersect_key($stat, array_flip(['dev', 'ino', 'size', 'mtime', 'ctime']));
        if (in_array(basename($path), ['version.json', 'routines.json'], true)) {
            $content = readRegularFile($path, READ_LIMIT + 1);
            if ($content === null || strlen($content) > READ_LIMIT) {
                return null;
            }
            $identity[$path]['content'] = hash('sha256', $content);
        }
        if (basename($path) === 'schedule-status.json') {
            $identity[$path]['scheduler_state'] = dashboardSchedule(dirname($path))['state'];
        }
    }
    return hash('sha256', serialize($identity));
}

/** Select one of three fixed cache files without accepting caller-controlled paths. */
function homepageCachePath(string $directory, string $locale): ?string
{
    return in_array($locale, ['en', 'zh-hans', 'zh-hant'], true)
        ? $directory . '/homepage-' . $locale . '.cache' : null;
}

/** Check the private cache directory and optionally create its final component. */
function homepageCacheDirectory(string $directory, bool $create = false): bool
{
    if (!regularDirectory(dirname($directory)) || !regularDirectory(dirname($directory, 2))) {
        return false;
    }
    clearstatcache(true, $directory);
    if (regularDirectory($directory)) {
        return true;
    }
    return $create && @lstat($directory) === false && @mkdir($directory, 0700)
        && regularDirectory($directory);
}

/** Read a recent, bounded HTML response matching the current input identity. */
function readHomepageCache(string $directory, string $locale, string $key, ?int $now = null): ?string
{
    $path = homepageCachePath($directory, $locale);
    if ($path === null || !homepageCacheDirectory($directory)) {
        return null;
    }
    $content = readRegularFile($path, PAGE_CACHE_READ_LIMIT + 1);
    if ($content === null || strlen($content) > PAGE_CACHE_READ_LIMIT) {
        return null;
    }
    [$metadata, $html] = array_pad(explode("\n", $content, 2), 2, '');
    if (preg_match('/\Aaranami-page-1 ([0-9]{1,12}) ([0-9a-f]{64})\z/', $metadata, $parts) !== 1
        || $html === '' || !hash_equals($key, $parts[2])) {
        return null;
    }
    $age = ($now ?? time()) - (int) $parts[1];
    return $age >= 0 && $age < PAGE_CACHE_TTL ? $html : null;
}

/** Atomically replace a bounded cache entry, treating filesystem failures as misses. */
function writeHomepageCache(string $directory, string $locale, string $key, string $html, ?int $now = null): bool
{
    $path = homepageCachePath($directory, $locale);
    $content = 'aranami-page-1 ' . ($now ?? time()) . ' ' . $key . "\n" . $html;
    if ($path === null || $html === '' || strlen($content) > PAGE_CACHE_READ_LIMIT
        || preg_match('/\A[0-9a-f]{64}\z/', $key) !== 1
        || !homepageCacheDirectory($directory, true)) {
        return false;
    }
    clearstatcache(true, $path);
    $existing = @lstat($path);
    if ($existing !== false && ($existing['mode'] & 0170000) !== 0100000) {
        return false;
    }
    try {
        $temporary = $directory . '/.homepage-' . $locale . '-' . bin2hex(random_bytes(8)) . '.tmp';
    } catch (Exception $exception) {
        return false;
    }
    $stream = @fopen($temporary, 'xb');
    if ($stream === false) {
        return false;
    }
    try {
        if (@fwrite($stream, $content) !== strlen($content) || !@fflush($stream)) {
            return false;
        }
        fclose($stream);
        $stream = null;
        return @rename($temporary, $path);
    } finally {
        if (is_resource($stream)) {
            fclose($stream);
        }
        @unlink($temporary);
    }
}
