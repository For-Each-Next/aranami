<?php
/** Validate private dashboard snapshots before presenting them on the homepage. */
declare(strict_types=1);

const SNAPSHOT_READ_LIMIT = 8388608;

/** Accept an actual ISO calendar date, optionally allowing an unresolved date. */
function validDate(mixed $value, bool $nullable = false): bool
{
    if ($value === null) {
        return $nullable;
    }
    if (!is_string($value) || preg_match('/\A([0-9]{4})-([0-9]{2})-([0-9]{2})\z/', $value, $parts) !== 1) {
        return false;
    }
    return checkdate((int) $parts[2], (int) $parts[3], (int) $parts[1]);
}

/** Accept an explicit UTC timestamp without silently correcting invalid dates. */
function validUtcTime(mixed $value): bool
{
    if (!is_string($value)) {
        return false;
    }
    $text = preg_replace('/Z\z/', '+00:00', $value);
    $time = DateTimeImmutable::createFromFormat('Y-m-d\TH:i:sP', $text);
    return $time !== false && str_ends_with($text, '+00:00')
        && $time->format('Y-m-d\TH:i:sP') === $text;
}

/** Restrict snapshot links to public Wikipedia or Wikidata HTTPS pages. */
function validWikiUrl(mixed $value): bool
{
    if (!is_string($value) || strlen($value) > 4096 || preg_match('/[\x00-\x20\\\\]/', $value) === 1) {
        return false;
    }
    $url = parse_url($value);
    return is_array($url) && ($url['scheme'] ?? null) === 'https'
        && (preg_match('/\A[a-z0-9-]+\.wikipedia\.org\z/', $url['host'] ?? '') === 1
            || ($url['host'] ?? '') === 'www.wikidata.org')
        && !isset($url['user']) && !isset($url['pass']) && !isset($url['port'])
        && (str_starts_with($url['path'] ?? '', '/wiki/') || ($url['path'] ?? '') === '/w/index.php');
}

/** Accept a bounded list of observations using the given record validator. */
function validRecords(mixed $value, callable $validate): bool
{
    if ($value === null) {
        return true;
    }
    if (!is_array($value) || !array_is_list($value) || count($value) > 10000) {
        return false;
    }
    foreach ($value as $row) {
        if (!is_array($row) || !$validate($row)) {
            return false;
        }
    }
    return true;
}

/** Validate a measured count while preserving null as an unavailable observation. */
function validCount(mixed $value): bool
{
    return is_int($value) && $value >= 0;
}

/** Validate one dated Chinese main-page article observation. */
function validArticle(array $row): bool
{
    return is_string($row['title'] ?? null) && $row['title'] !== ''
        && validWikiUrl($row['url'] ?? null) && array_key_exists('date', $row)
        && validDate($row['date'], true)
        && (!isset($row['date_kind']) || in_array($row['date_kind'], ['appearance', 'window_start', 'observed', 'scheduled'], true));
}

/** Validate a supported recognized-quality badge independently of language labels. */
function validQualityType(mixed $value): bool
{
    return in_array($value, ['FA', 'FL', 'GA', 'recommended'], true);
}

/** Validate one Wikipedia identity without deriving a domain from a language code. */
function validQualitySite(array $row): bool
{
    return is_string($row['site'] ?? null) && $row['site'] !== ''
        && is_string($row['language'] ?? null) && $row['language'] !== ''
        && validQualityType($row['status'] ?? null);
}

/** Accept a Chinese companion only when its title and URL are both known. */
function validChineseCompanion(array $row): bool
{
    if (!array_key_exists('zh_title', $row) || !array_key_exists('zh_url', $row)) {
        return false;
    }
    if ($row['zh_title'] === null || $row['zh_url'] === null) {
        return $row['zh_title'] === null && $row['zh_url'] === null;
    }
    return is_string($row['zh_title']) && $row['zh_title'] !== ''
        && validWikiUrl($row['zh_url'])
        && (parse_url($row['zh_url'], PHP_URL_HOST) === 'zh.wikipedia.org');
}

/** Validate a recognized article and its optional same-item Chinese companion. */
function validQualityArticle(array $row): bool
{
    if (!validQualitySite($row) || !is_string($row['title'] ?? null) || $row['title'] === ''
        || !is_string($row['item'] ?? null) || preg_match('/\AQ[1-9][0-9]*\z/', $row['item']) !== 1
        || !validWikiUrl($row['url'] ?? null) || !validChineseCompanion($row)
        || (array_key_exists('zh_class', $row) && $row['zh_class'] !== null
            && (!is_string($row['zh_class']) || $row['zh_class'] === '' || strlen($row['zh_class']) > 64
                || preg_match('/[\x00-\x1f\x7f]/', $row['zh_class']) === 1))) {
        return false;
    }
    $host = parse_url($row['url'], PHP_URL_HOST);
    return preg_match('/\A[a-z0-9-]+\.wikipedia\.org\z/', $host) === 1
        && (($row['site'] === 'zhwiki' || $row['language'] === 'zh')
            ? $row['site'] === 'zhwiki' && $row['language'] === 'zh' && $host === 'zh.wikipedia.org'
            : $host !== 'zh.wikipedia.org');
}

/** Accept the fixed official daily Chinese all-access pageviews endpoint. */
function validPageviewsSourceUrl(mixed $value, ?string $date = null): bool
{
    if (!is_string($value) || preg_match('~\Ahttps://wikimedia\.org/api/rest_v1/metrics/pageviews/top/zh\.wikipedia\.org/all-access/([0-9]{4})/([0-9]{2})/([0-9]{2})\z~', $value, $parts) !== 1) {
        return false;
    }
    $observedDate = $parts[1] . '-' . $parts[2] . '-' . $parts[3];
    return validDate($observedDate) && ($date === null || $date === $observedDate);
}

/** Validate the bounded filtered ranking and its exact daily coverage. */
function validPageviews(mixed $value, string $today): bool
{
    if (!is_array($value) || !validDate($value['date'] ?? null)
        || $value['date'] >= $today
        || $value['date'] < (new DateTimeImmutable($today, new DateTimeZone('UTC')))->modify('-7 days')->format('Y-m-d')
        || ($value['limit'] ?? null) !== 10 || ($value['scope'] ?? null) !== 'site_top_1000'
        || !validPageviewsSourceUrl($value['source_url'] ?? null, $value['date'])
        || !is_array($value['items'] ?? null) || !array_is_list($value['items']) || count($value['items']) > 10) {
        return false;
    }
    $titles = [];
    $previousViews = null;
    foreach ($value['items'] as $index => $item) {
        if (!is_array($item) || !is_string($item['title'] ?? null) || trim($item['title']) === ''
            || isset($titles[$item['title']]) || !validWikiUrl($item['url'] ?? null)
            || parse_url($item['url'], PHP_URL_HOST) !== 'zh.wikipedia.org'
            || !validCount($item['views'] ?? null) || ($item['rank'] ?? null) !== $index + 1
            || ($previousViews !== null && $item['views'] > $previousViews)) {
            return false;
        }
        $titles[$item['title']] = true;
        $previousViews = $item['views'];
    }
    return true;
}

/** Validate bounded, distinct foreign-edition links for a ranked article. */
function validPopularArticle(array $row): bool
{
    if (!validQualityArticle($row) || !validCount($row['language_count'] ?? null)
        || $row['language_count'] === 0 || !is_array($row['articles'] ?? null)
        || !array_is_list($row['articles']) || count($row['articles']) !== min($row['language_count'], 500)) {
        return false;
    }
    $sites = [];
    foreach ($row['articles'] as $article) {
        if (!is_array($article) || !validQualityArticle($article) || $article['item'] !== $row['item']
            || $article['language'] === 'zh' || isset($sites[$article['site']])) {
            return false;
        }
        $sites[$article['site']] = true;
    }
    return isset($sites[$row['site']]);
}

/** Load a bounded regular snapshot, returning null for missing or invalid data. */
function projectDashboard(string $path): ?array
{
    if (!regularDirectory(dirname($path)) || !regularDirectory(dirname($path, 2))) {
        return null;
    }
    $content = readRegularFile($path, false, SNAPSHOT_READ_LIMIT + 1);
    if ($content === null || strlen($content) > SNAPSHOT_READ_LIMIT) {
        return null;
    }
    $data = json_decode($content, true, 32);
    if (!is_array($data) || !is_array($data['period'] ?? null)
        || !is_string($data['period']['label'] ?? null)
        || !validDate($data['period']['start'] ?? null) || !validDate($data['period']['end'] ?? null)
        || $data['period']['start'] >= $data['period']['end']
        || !validUtcTime($data['updated_at'] ?? null)
        || !array_key_exists('quality', $data) || !is_array($data['homepage'] ?? null)) {
        return null;
    }
    foreach (['featured', 'dyk', 'good', 'news', 'history'] as $name) {
        if (!array_key_exists($name, $data['homepage']) || !validRecords($data['homepage'][$name], 'validArticle')) {
            return null;
        }
    }
    $scheduleStart = $data['period']['end'];
    $scheduleEnd = (new DateTimeImmutable($scheduleStart, new DateTimeZone('UTC')))->modify('+30 days')->format('Y-m-d');
    if (array_key_exists('upcoming', $data['homepage']) && !validRecords($data['homepage']['upcoming'], static fn (array $row): bool =>
        validArticle($row) && validDate($row['date']) && ($row['date_kind'] ?? null) === 'scheduled'
        && in_array($row['kind'] ?? null, ['fa', 'fl', 'ga'], true)
        && $row['date'] >= $scheduleStart && $row['date'] < $scheduleEnd)) {
        return null;
    }
    $today = (new DateTimeImmutable($data['period']['end'], new DateTimeZone('UTC')))->modify('-1 day')->format('Y-m-d');
    if (array_key_exists('pageviews', $data) && $data['pageviews'] !== null && !validPageviews($data['pageviews'], $today)) {
        return null;
    }
    $quality = $data['quality'];
    if ($quality !== null && (!is_array($quality) || !validCount($quality['total'] ?? null)
        || !validCount($quality['languages'] ?? null) || !is_string($quality['scope'] ?? null)
        || !validUtcTime($quality['observed_at'] ?? null)
        || !is_array($quality['counts'] ?? null) || !validRecords($quality['counts'], static fn (array $row): bool =>
            validQualitySite($row) && validCount($row['count'] ?? null))
        || (array_key_exists('language_totals', $quality) && (!is_array($quality['language_totals'])
            || !validRecords($quality['language_totals'], static fn (array $row): bool =>
                is_string($row['site'] ?? null) && $row['site'] !== ''
                && is_string($row['language'] ?? null) && $row['language'] !== ''
                && validCount($row['count'] ?? null))))
        || !is_array($quality['items'] ?? null) || !validRecords($quality['items'], 'validQualityArticle')
        || (array_key_exists('recent_items', $quality) && (!is_array($quality['recent_items'])
            || !validRecords($quality['recent_items'], static fn (array $row): bool =>
                validQualityArticle($row) && validUtcTime($row['badge_observed_at'] ?? null))))
        || (array_key_exists('popular_items', $quality) && (!is_array($quality['popular_items'])
            || count($quality['popular_items']) > 10 || !validRecords($quality['popular_items'], 'validPopularArticle'))))) {
        return null;
    }
    if (!is_array($data['sources'] ?? null) || !validRecords($data['sources'], static fn (array $row): bool =>
        is_string($row['label'] ?? null) && (validWikiUrl($row['url'] ?? null) || validPageviewsSourceUrl($row['url'] ?? null)))
        || !is_array($data['errors'] ?? null) || !validRecords($data['errors'], static fn (array $row): bool =>
            is_string($row['section'] ?? null) && is_string($row['message'] ?? null))) {
        return null;
    }
    return $data;
}
