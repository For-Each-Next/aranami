<?php
/** Validate independent Chinese dashboard reports without fetching source data. */
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
        && parse_url($row['url'], PHP_URL_HOST) === 'zh.wikipedia.org'
        && validDate($row['date'], true) && validAssessments($row)
        && (!isset($row['date_kind']) || in_array($row['date_kind'], ['appearance', 'window_start', 'observed', 'scheduled'], true));
}

/** Accept optional recorded assessment labels while preserving unknown values. */
function validAssessments(array $row): bool
{
    foreach (['assessment_class', 'assessment_importance'] as $field) {
        $value = $row[$field] ?? null;
        if ($value !== null && (!is_string($value) || trim($value) === ''
            || strlen($value) > 192 || preg_match('/[\x00-\x1f\x7f]/', $value) === 1)) {
            return false;
        }
    }
    return true;
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
            || !validAssessments($item) || !validCount($item['views'] ?? null) || ($item['rank'] ?? null) !== $index + 1
            || (isset($item['site_rank']) && (!is_int($item['site_rank']) || $item['site_rank'] < 1 || $item['site_rank'] > 1000))
            || ($previousViews !== null && $item['views'] > $previousViews)) {
            return false;
        }
        $titles[$item['title']] = true;
        $previousViews = $item['views'];
    }
    return true;
}

/** Validate a bounded plain label or supported-language label map. */
function validMaintenanceLabel(mixed $value): bool
{
    if (is_string($value)) {
        return trim($value) !== '' && strlen($value) <= 800;
    }
    if (!is_array($value) || $value === [] || count($value) > 3
        || array_diff(array_keys($value), ['en', 'zh-hans', 'zh-hant']) !== []) {
        return false;
    }
    foreach ($value as $label) {
        if (!is_string($label) || trim($label) === '' || strlen($label) > 800) {
            return false;
        }
    }
    return true;
}

/** Validate complete Chinese maintenance lists with consistent distinct counts. */
function validBacklog(mixed $value): bool
{
    if (!is_array($value) || !validUtcTime($value['observed_at'] ?? null)
        || !validCount($value['total'] ?? null) || !is_array($value['groups'] ?? null)
        || !array_is_list($value['groups']) || count($value['groups']) > 64) {
        return false;
    }
    $groups = [];
    $articles = [];
    $referencedIssues = [];
    $issueTotal = 0;
    $sections = [];
    $configured = null;
    foreach ($value['groups'] as $group) {
        if (!is_array($group) || !is_string($group['key'] ?? null)
            || preg_match('/\A[a-z][a-z0-9_]{0,63}\z/', $group['key']) !== 1
            || isset($groups[$group['key']]) || !is_string($group['category'] ?? null)
            || trim($group['category']) === '' || !validWikiUrl($group['source_url'] ?? null)
            || parse_url($group['source_url'], PHP_URL_HOST) !== 'zh.wikipedia.org'
            || preg_match('~\A/wiki/(?:Category|Template):.+\z~u', rawurldecode(parse_url($group['source_url'], PHP_URL_PATH))) !== 1
            || !validCount($group['count'] ?? null) || !is_array($group['items'] ?? null)
            || !array_is_list($group['items']) || count($group['items']) > 10000
            || $group['count'] !== count($group['items'])) {
            return false;
        }
        $hasConfiguration = array_key_exists('section_key', $group)
            || array_key_exists('section_label', $group) || array_key_exists('label', $group);
        $configured ??= $hasConfiguration;
        if ($hasConfiguration !== $configured) {
            return false;
        }
        if ($hasConfiguration) {
            if (!is_string($group['section_key'] ?? null)
                || preg_match('/\A[a-z][a-z0-9_]{0,63}\z/', $group['section_key']) !== 1
                || !validMaintenanceLabel($group['section_label'] ?? null)
                || !validMaintenanceLabel($group['label'] ?? null)) {
                return false;
            }
            $section = $group['section_key'];
            if (isset($sections[$section]) && $sections[$section] !== $group['section_label']) {
                return false;
            }
            $sections[$section] = $group['section_label'];
            if (count($sections) > 12) {
                return false;
            }
        }
        $groups[$group['key']] = true;
        $issueTotal += $group['count'];
        $seen = [];
        foreach ($group['items'] as $item) {
            if (!is_array($item) || !is_int($item['page_id'] ?? null) || $item['page_id'] <= 0
                || isset($seen[$item['page_id']]) || !is_string($item['title'] ?? null)
                || trim($item['title']) === '' || !validWikiUrl($item['url'] ?? null)
                || parse_url($item['url'], PHP_URL_HOST) !== 'zh.wikipedia.org'
                || !validAssessments($item)) {
                return false;
            }
            $month = $item['oldest_issue_month'] ?? null;
            if ($month !== null && (!is_string($month) || !validDate($month . '-01'))) {
                return false;
            }
            if (array_key_exists('issues', $item)) {
                if (!is_array($item['issues']) || !array_is_list($item['issues'])
                    || count($item['issues']) > 64 || !in_array($group['key'], $item['issues'], true)) {
                    return false;
                }
                $seenIssues = [];
                foreach ($item['issues'] as $issue) {
                    if (!is_string($issue) || preg_match('/\A[a-z][a-z0-9_]{0,63}\z/', $issue) !== 1
                        || isset($seenIssues[$issue])) {
                        return false;
                    }
                    $seenIssues[$issue] = true;
                    $referencedIssues[$issue] = true;
                }
            }
            $seen[$item['page_id']] = true;
            $articles[$item['page_id']] = true;
        }
    }
    return $value['total'] === count($articles)
        && array_diff_key($referencedIssues, $groups) === []
        && (!array_key_exists('issue_total', $value)
            || (validCount($value['issue_total']) && $value['issue_total'] === $issueTotal));
}

/** Validate refresh metadata for one independent report. */
function validReportMetadata(mixed $value): bool
{
    return is_array($value) && is_string($value['config_id'] ?? null)
        && preg_match('/\A[0-9a-f]{64}\z/', $value['config_id']) === 1
        && (!array_key_exists('source_config_id', $value) || $value['source_config_id'] === null
            || (is_string($value['source_config_id']) && preg_match('/\A[0-9a-f]{64}\z/', $value['source_config_id']) === 1))
        && (is_int($value['refresh_interval_seconds'] ?? null) || is_float($value['refresh_interval_seconds'] ?? null))
        && is_finite((float) $value['refresh_interval_seconds']) && $value['refresh_interval_seconds'] > 0
        && array_key_exists('last_success_at', $value) && array_key_exists('last_attempt_at', $value)
        && ($value['last_success_at'] === null || validUtcTime($value['last_success_at']))
        && ($value['last_attempt_at'] === null || validUtcTime($value['last_attempt_at']))
        && ($value['last_success_at'] === null || ($value['last_attempt_at'] !== null
            && new DateTimeImmutable($value['last_success_at']) <= new DateTimeImmutable($value['last_attempt_at'])))
        && is_bool($value['failed'] ?? null);
}

/** Load only the migrated bounded snapshot contract. */
function projectDashboard(string $path): ?array
{
    if (!regularDirectory(dirname($path)) || !regularDirectory(dirname($path, 2))) {
        return null;
    }
    $content = readRegularFile($path, SNAPSHOT_READ_LIMIT + 1);
    if ($content === null || strlen($content) > SNAPSHOT_READ_LIMIT) {
        return null;
    }
    $data = json_decode($content, true, 32);
    if (!is_array($data) || ($data['schema'] ?? null) !== 2
        || array_key_exists('quality', $data)
        || !is_array($data['period'] ?? null) || !is_string($data['period']['label'] ?? null)
        || !validDate($data['period']['start'] ?? null) || !validDate($data['period']['end'] ?? null)
        || $data['period']['start'] >= $data['period']['end']
        || !validUtcTime($data['updated_at'] ?? null)
        || !is_array($data['reports'] ?? null) || !is_array($data['homepage'] ?? null)) {
        return null;
    }
    foreach (['recognized', 'dyk', 'pageviews', 'news_history', 'backlog'] as $name) {
        if (!validReportMetadata($data['reports'][$name] ?? null)) {
            return null;
        }
    }
    foreach (['featured', 'dyk', 'good', 'news', 'history', 'upcoming'] as $name) {
        if (!array_key_exists($name, $data['homepage']) || !validRecords($data['homepage'][$name], 'validArticle')) {
            return null;
        }
    }
    if ($data['homepage']['upcoming'] !== null) {
        $period = $data['reports']['recognized']['period'] ?? null;
        if (!is_array($period) || !validDate($period['start'] ?? null) || !validDate($period['end'] ?? null)
            || $period['start'] >= $period['end']) {
            return null;
        }
        $scheduleStart = $period['end'];
        $scheduleEnd = (new DateTimeImmutable($scheduleStart, new DateTimeZone('UTC')))->modify('+30 days')->format('Y-m-d');
        if (!validRecords($data['homepage']['upcoming'], static fn (array $row): bool =>
            validArticle($row) && validDate($row['date']) && ($row['date_kind'] ?? null) === 'scheduled'
            && in_array($row['kind'] ?? null, ['fa', 'fl', 'ga'], true)
            && $row['date'] >= $scheduleStart && $row['date'] < $scheduleEnd)) {
            return null;
        }
    }
    $views = $data['pageviews'] ?? null;
    if ($views !== null) {
        $success = $data['reports']['pageviews']['last_success_at'];
        if ($success === null || !validPageviews($views, substr($success, 0, 10))) {
            return null;
        }
    }
    if (($data['backlog'] ?? null) !== null && !validBacklog($data['backlog'])) {
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
