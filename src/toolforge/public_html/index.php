<?php
/** Present multilingual quality content and Chinese homepage display history. */
declare(strict_types=1);

require dirname(__DIR__) . '/i18n.php';

header('Cache-Control: no-store');
header('Vary: Accept-Language');
header('Content-Language: ' . uiLanguageTag());
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; script-src 'self'; connect-src 'self'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");

$method = $_SERVER['REQUEST_METHOD'] ?? '';
if (!in_array($method, ['GET', 'HEAD'], true)) {
    http_response_code(405);
    header('Allow: GET, HEAD');
    header('Content-Type: text/plain; charset=UTF-8');
    echo uiText('http.method_not_allowed') . "\n";
    exit;
}
header('Content-Type: text/html; charset=UTF-8');
require dirname(__DIR__) . '/status.php';
require dirname(__DIR__) . '/project.php';
require dirname(__DIR__) . '/assessment.php';

/** Format a measured count without turning missing data into zero. */
function dashboardNumber(int $number): string
{
    return number_format($number, 0, '.', ',');
}

/** Name known Wikipedia languages in the selected interface language. */
function dashboardLanguage(string $language): string
{
    return uiLanguageName($language);
}

/** Keep the supported quality recognitions distinct. */
function dashboardStatus(string $status): string
{
    return in_array($status, ['FA', 'FL', 'GA'], true)
        ? dashboardAssessmentLabel($status, 'class')
        : ($status === 'recommended' ? uiText('badge.recommended') : $status);
}

/** Render an article and its recognition together on one line. */
function dashboardQualityTitle(array $item): void
{
    $language = $item['language'] ?? 'en';
    $label = dashboardStatus($item['status']);
    $note = $label;
    if (isset($item['badge_observed_at'])) {
        $note .= ' · ' . uiText('date.observed') . ': ' . str_replace('T', ' ', substr($item['badge_observed_at'], 0, 16)) . ' UTC';
    }
    echo '<span class="article-summary quality-article"><a href="' . html($item['url']) . '" title="' . html($item['title']) . '"><bdi lang="' . html($language) . '">' . html($item['title']) . '</bdi></a>';
    echo '<span class="recognition recognition-' . html($item['status']) . ' assessment-chip" title="' . html($note) . '">' . html($label) . '</span></span>';
}

/** Render a same-item Chinese article and its grade in a separate cell. */
function dashboardQualityCompanion(array $item): void
{
    $local = ($item['language'] ?? '') === 'zh';
    $title = $item['zh_title'] ?? ($local ? $item['title'] : null);
    $url = $item['zh_url'] ?? ($local ? $item['url'] : null);
    if ($title !== null && $url !== null) {
        $grade = $item['zh_class'] ?? ($local ? $item['status'] : null);
        echo '<span class="article-summary chinese-companion"><a href="' . html($url) . '" title="' . html($title) . '"><bdi lang="zh">' . html($title) . '</bdi></a>';
        if ($grade !== null) {
            $label = dashboardAssessmentLabel($grade, 'class');
            echo '<span class="companion-grade assessment-chip" data-assessment="class" title="' . html($label) . '">' . html($label) . '</span>';
        }
        echo '</span>';
    } else {
        echo '<span class="companion-missing">' . html(uiText('quality.companion_missing')) . '</span>';
    }
}

/** Show an appearance kind using the same chip style as assessments. */
function dashboardEntryKind(array $item): void
{
    $kind = $item['kind'] ?? '';
    if (in_array($kind, ['fa', 'fl', 'ga'], true)) {
        $label = dashboardStatus(strtoupper($kind));
    } else {
        $key = ['featured' => 'badge.featured', 'news' => 'homepage.news', 'history' => 'homepage.history'][$kind] ?? null;
        $label = $key === null ? null : uiText($key);
    }
    if ($label !== null) {
        echo '<span class="content-kind assessment-chip" title="' . html($label) . '">' . html($label) . '</span>';
    }
}

/** Render a trailing date with labelled carry-in and observation markers. */
function dashboardEntryMeta(array $item): void
{
    echo '<span class="entry-meta">';
    if (($item['date'] ?? null) !== null) {
        echo '<span class="entry-date"><time datetime="' . html($item['date']) . '">' . html($item['date']) . '</time>';
        if (($item['date_kind'] ?? '') === 'scheduled') {
            echo html(uiText('date.scheduled'));
        }
        echo '</span>';
    }
    $noteKey = ['window_start' => 'date.window_start', 'observed' => 'date.observed'][$item['date_kind'] ?? ''] ?? null;
    if ($noteKey !== null) {
        $note = uiText($noteKey);
        echo '<abbr class="date-note-marker" title="' . html($note) . '" aria-label="' . html($note) . '">†</abbr>';
    }
    echo '</span>';
}

/** Render homepage subjects with optional numbering and trailing dates. */
function dashboardEntryRows(array $items, bool $numbered = false): void
{
    echo '<ul class="entry-list entry-compact' . ($numbered ? ' entry-numbered' : '') . '">';
    foreach ($items as $index => $item) {
        echo '<li>';
        if ($numbered) {
            echo '<span class="entry-rank">' . html((string) ($index + 1)) . '</span>';
        }
        echo '<span class="article-summary"><a href="' . html($item['url']) . '" title="' . html($item['title']) . '">' . html($item['title']) . '</a>';
        dashboardEntryKind($item);
        dashboardAssessmentChips($item);
        echo '</span>';
        dashboardEntryMeta($item);
        echo '</li>';
    }
    echo '</ul>';
}

/** Merge related sources chronologically while retaining missing observations. */
function dashboardMergedItems(?array $homepage, array $sections): array
{
    $merged = null;
    $missing = [];
    foreach ($sections as $key => $label) {
        $items = $homepage[$key] ?? null;
        if ($items === null) {
            $missing[] = $label;
            continue;
        }
        $merged ??= [];
        foreach ($items as $item) {
            $item['kind'] = match ($key) {
                'good' => 'ga',
                'featured' => in_array($item['kind'] ?? '', ['fa', 'fl'], true) ? $item['kind'] : 'featured',
                default => $key,
            };
            $merged[] = $item;
        }
    }
    if ($merged !== null) {
        usort($merged, static fn (array $left, array $right): int =>
            strcmp($right['date'] ?? '', $left['date'] ?? '') ?: strcmp($left['title'], $right['title']) ?: strcmp($left['kind'], $right['kind']));
    }
    return ['items' => $merged, 'missing' => $missing];
}

/** Show complete lists or a preview while preserving availability. */
function dashboardEntries(?array $items, bool $complete = false, bool $numbered = false): void
{
    if ($items === null) {
        echo '<p class="empty">' . html(uiText('empty.section')) . '</p>';
    } elseif ($items === []) {
        echo '<p class="empty">' . html(uiText('empty.period')) . '</p>';
    } else {
        if ($complete) {
            dashboardEntryRows($items, $numbered);
            return;
        }
        $preview = 5;
        dashboardEntryRows(array_slice($items, 0, $preview));
        if (count($items) > $preview) {
            echo '<details class="more"><summary>' . html(uiText('records.more', ['count' => dashboardNumber(count($items) - $preview)])) . '</summary>';
            dashboardEntryRows(array_slice($items, $preview));
            echo '</details>';
        }
    }
}

/** Render the observed daily project matches without implying full-site coverage. */
function dashboardPageviewsDescription(?array $observation): void
{
    [$before, $after] = explode('{top}', uiText('pageviews.description'), 2);
    echo html($before);
    if ($observation !== null) {
        $source = 'https://pageviews.wmcloud.org/topviews?' . http_build_query([
            'project' => 'zh.wikipedia.org', 'date' => $observation['date'],
        ], '', '&', PHP_QUERY_RFC3986);
        echo '<a href="' . html($source) . '">' . html(uiText('pageviews.top')) . '</a>';
    } else {
        echo html(uiText('pageviews.top'));
    }
    echo html($after);
    if ($observation !== null) {
        $date = new DateTimeImmutable($observation['date']);
        $format = uiLocale() === 'en' ? 'F j, Y' : 'Y年n月j日';
        $label = $date->format($format);
        echo '<time datetime="' . html($observation['date']) . '">' . html(uiLocale() === 'en' ? ' (' . $label . ')' : '（' . $label . '）') . '</time>';
    }
}

/** Render the observed project matches from the official daily site-wide list. */
function dashboardPageviews(?array $observation): void
{
    if ($observation === null) {
        echo '<p class="empty">' . html(uiText('pageviews.unavailable')) . '</p>';
        return;
    }
    if ($observation['items'] === []) {
        echo '<p class="empty">' . html(uiText('pageviews.empty')) . '</p>';
    } else {
        echo '<ol class="pageviews-list" role="list">';
        foreach ($observation['items'] as $item) {
            echo '<li><span class="pageviews-rank" aria-hidden="true">' . html((string) $item['rank'])
                . '.</span><span class="article-summary"><a href="' . html($item['url']) . '" title="' . html($item['title']) . '"><bdi lang="zh">' . html($item['title']) . '</bdi></a>';
            dashboardAssessmentChips($item);
            echo '</span><span class="pageviews-number"'
                . (isset($item['site_rank']) ? ' title="' . html(uiText('pageviews.site_rank', ['rank' => dashboardNumber($item['site_rank'])])) . '"' : '') . '>'
                . html(uiText('pageviews.views', ['count' => dashboardNumber($item['views'])])) . '</span></li>';
        }
        echo '</ol>';
    }
}

$project = projectDashboard('/data/project/aranami/cache/toolforge/dashboard.json');
$updatedAt = $project['updated_at'] ?? null;
$updatedLabel = $updatedAt === null ? null : (new DateTimeImmutable($updatedAt))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i');
$quality = $project['quality'] ?? null;
$qualityObservedAt = $quality['observed_at'] ?? null;
$qualityObservedLabel = $qualityObservedAt === null ? null : (new DateTimeImmutable($qualityObservedAt))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i');
$homepage = $project['homepage'] ?? null;
$pageviews = $project['pageviews'] ?? null;
$backlog = $project['backlog'] ?? null;
$homepageItems = $homepage ?? [];
$homepageItems['pageviews'] = $pageviews['items'] ?? null;
$homepageMissing = [];
foreach ([
    'recognized' => ['featured' => uiText('section.featured'), 'good' => uiText('section.good')],
    'news_history' => ['news' => uiText('homepage.news'), 'history' => uiText('homepage.history')],
] as $key => $sections) {
    $merged = dashboardMergedItems($homepage, $sections);
    $homepageItems[$key] = $merged['items'];
    $homepageMissing[$key] = $merged['missing'];
}
$recognizedPastItems = $homepageItems['recognized'] ?? null;
$upcomingItems = $homepage['upcoming'] ?? null;
if ($upcomingItems !== null) {
    usort($upcomingItems, static fn (array $left, array $right): int =>
        strcmp($left['date'], $right['date']) ?: strcmp($left['title'], $right['title']) ?: strcmp($left['kind'], $right['kind']));
}
$homepageItems['recognized'] = $recognizedPastItems === null && $upcomingItems === null
    ? null : array_merge($upcomingItems ?? [], $recognizedPastItems ?? []);
$errors = $project['errors'] ?? [];
$languages = array_fill_keys(array_column($quality['counts'] ?? [], 'language'), true);
$requestedLanguage = $_GET['language'] ?? '';
$selectedLanguage = is_string($requestedLanguage) && isset($languages[$requestedLanguage]) ? $requestedLanguage : '';
$requestedStatus = $_GET['status'] ?? '';
$selectedStatus = is_string($requestedStatus) && in_array($requestedStatus, ['FA', 'FL', 'GA', 'recommended'], true) ? $requestedStatus : '';
if (($_GET['random'] ?? null) === '1') {
    $candidates = array_values(array_filter(array_keys($languages), static fn (string $language): bool =>
        $language !== 'zh' && $language !== $selectedLanguage));
    if ($candidates === [] && $selectedLanguage !== '' && $selectedLanguage !== 'zh') {
        $candidates = [$selectedLanguage];
    }
    if ($candidates !== []) {
        $language = $candidates[random_int(0, count($candidates) - 1)];
        header('Location: ' . uiUrl('', ['language' => $language]) . '#quality', true, 303);
        exit;
    }
}
if ($method === 'HEAD') {
    exit;
}
$qualityItems = $selectedLanguage === '' ? selectQualityArticles($quality, $selectedStatus) : array_values(array_filter($quality['items'] ?? [], static fn (array $item): bool =>
    $item['language'] === $selectedLanguage && ($selectedStatus === '' || $item['status'] === $selectedStatus)));
$homepageSections = [
    'pageviews' => [uiText('pageviews.heading'), ''],
    'recognized' => [uiText('homepage.recognized'), uiText('homepage.recognized_description')],
    'dyk' => [uiText('homepage.dyk'), uiText('homepage.dyk_description')],
    'news_history' => [uiText('homepage.news_history'), uiText('homepage.news_history_description')],
];
$homepageColumns = [['recognized', 'dyk'], ['pageviews', 'news_history']];
$version = installedVersion('/data/project/aranami');
$versionLabel = $version ?? uiText('ops.unavailable');
$activity = dashboardActivity('/data/project/aranami/logs');
$routineNames = [];
foreach (ROUTINES as $id => $name) {
    $routineNames[$id] = uiText('routine.' . $id);
}
?>
<!doctype html>
<html lang="<?= html(uiLanguageTag()) ?>">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#edf9f4">
  <link rel="icon" href="/aranami.svg" type="image/svg+xml">
  <meta name="description" content="<?= html(uiText('page.description')) ?>">
  <title><?= html(uiText('page.title')) ?></title>
  <script src="/dashboard.js" defer></script>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, "Noto Sans TC", "Microsoft JhengHei", sans-serif; --green: #a8d9cb; --mint: #edf9f4; --paper: #ffffff; --pale: #edf9f4; --ink: #202122; --muted: #54595d; --line: #c1d1cc; --link: #3366cc; }
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--ink); background: var(--pale); font-size: 1rem; line-height: 1.6; }
    a { color: var(--link); text-decoration: none; overflow-wrap: anywhere; }
    a:hover { color: #2a4b8d; }
    a:focus-visible, summary:focus-visible, button:focus-visible, select:focus-visible, [tabindex]:focus-visible { outline: 3px solid var(--link); outline-offset: 4px; }
    button, select { font: inherit; }
    .shell { width: 100%; max-width: 960px; margin: 0 auto; padding-left: 24px; padding-right: 24px; }
    .skip { position: absolute; top: -5rem; left: 1rem; z-index: 2; background: var(--paper); padding: .6rem 1rem; }
    .skip:focus { top: .5rem; }
    .masthead { border-top: 4px solid #76C2AF; border-bottom: 1px solid var(--line); background: var(--mint); }
    .topline { display: flex; align-items: center; justify-content: space-between; gap: 1rem; min-height: 70px; border-bottom: 1px solid var(--line); }
    .brand { display: inline-flex; gap: .7rem; align-items: center; text-decoration: none; font-weight: 750; font-size: 1.05rem; letter-spacing: -.02em; }
    .brand-mark { display: block; width: 36px; height: 36px; flex: 0 0 36px; }
    .brand small { display: block; font-size: .68rem; font-weight: 500; letter-spacing: .06em; }
    .hero { padding-top: 27px; padding-bottom: 28px; }
    h1 { margin: 0; font-family: "Linux Libertine", "Noto Serif SC", "Noto Serif TC", "Songti SC", "Songti TC", SimSun, Georgia, "Times New Roman", serif; font-size: 2rem; font-weight: 400; line-height: 1.375; }
    main { padding-top: 20px; padding-bottom: 26px; }
    .update-line { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .3rem 1rem; margin-bottom: 18px; font-size: .875rem; color: var(--muted); }
    .update-line p { margin: 0; }
    .notice { margin: 0 0 18px; padding: 12px 16px; background: var(--mint); border: 1px solid var(--green); border-left-width: 4px; border-radius: 8px; font-size: .8rem; }
    .project-layout { min-width: 0; }
    .section-flat { margin-bottom: 26px; }
    .section-header { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: .6rem; margin-bottom: 14px; padding-bottom: .3rem; border-bottom: 1px solid var(--muted); }
    h2 { margin: 0; font-family: "Linux Libertine", "Noto Serif SC", "Noto Serif TC", "Songti SC", "Songti TC", SimSun, Georgia, "Times New Roman", serif; font-size: 1.8rem; font-weight: 400; line-height: 1.4; }
    .section-aside { font-size: .69rem; color: var(--muted); }
    .empty { margin: 0; padding: 12px 0; font-size: .79rem; color: var(--muted); }
    .section-intro { margin: 0 0 14px; font-size: 1rem; color: var(--muted); }
    .quality-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 18px; padding: 6px 0; font-size: 1rem; }
    .quality-actions a { display: inline-block; padding: 4px 0; }
    .sample-label { padding: 8px 0; font-size: .875rem; color: var(--muted); margin: 0; }
    table { width: 100%; border-collapse: collapse; font-size: 1rem; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
    thead th { color: var(--muted); background: var(--mint); font-size: .875rem; font-weight: 650; }
    tbody th { font-weight: 500; }
    tbody tr:last-child td, tbody tr:last-child th { border-bottom: 0; }
    .quality-scroll { overflow-x: auto; }
    .quality-table { table-layout: fixed; min-width: 680px; }
    .quality-table thead th:first-child { width: 22%; }
    .quality-table thead th:nth-child(2), .quality-table thead th:nth-child(3) { width: 39%; }
    .quality-table th, .quality-table td { white-space: nowrap; vertical-align: middle; }
    .quality-language { overflow: hidden; text-overflow: ellipsis; }
    .quality-filter-status { margin: 0 0 14px; font-size: .77rem; padding: 9px 13px; border: 1px solid var(--green); border-radius: 6px; background: var(--mint); }
    .quality-pagination { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 11px 0; color: var(--muted); font-size: .875rem; border-top: 1px solid var(--line); }
    .quality-pagination p { margin: 0; }
    .quality-pagination button { padding: 7px 11px; border: 1px solid var(--green); border-radius: 5px; color: var(--ink); background: var(--mint); font: inherit; cursor: pointer; }
    .quality-table .language-code { display: inline; color: var(--muted); }
    .companion-missing { color: var(--muted); font-size: .875rem; font-weight: 400; }
    .quality-updated { margin: 8px 0 0; font-size: .875rem; color: var(--muted); }
    .cache-notice { margin: 0 0 14px; font-size: .875rem; color: var(--muted); }
    h3 { margin: 0 0 9px; font-size: 1.25rem; font-weight: 600; }
    .homepage-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 24px; align-items: start; }
    .homepage-column { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
    .homepage-card { min-width: 0; }
    .homepage-card-header { display: flex; align-items: center; justify-content: space-between; gap: .6rem; padding: 8px 0; border-bottom: 1px solid var(--line); }
    .homepage-card h3 { margin: 0; }
    .homepage-card-header p { margin: 1px 0 0; color: var(--muted); font-size: .875rem; }
    .count { min-width: 27px; min-height: 27px; padding: 1px 7px; display: inline-grid; place-items: center; background: var(--mint); border: 1px solid var(--green); border-radius: 20px; font-size: .7rem; font-variant-numeric: tabular-nums; }
    .homepage-card-body { padding: 2px 0 0; }
    .homepage-card-body > .empty { padding: 11px 0 3px; }
    .homepage-card-note { margin: 8px 0 2px; color: var(--muted); font-size: .67rem; }
    .pageviews-list { margin: 0; padding: 0; list-style: none; }
    .pageviews-list li { display: grid; grid-template-columns: 1.25rem minmax(0, 1fr) auto; gap: 5px; align-items: baseline; padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 1rem; }
    .pageviews-list li:last-child { border-bottom: 0; }
    .article-summary { display: flex; align-items: baseline; gap: 5px; min-width: 0; overflow: hidden; }
    .article-summary > a { display: block; flex: 1 1 0; min-width: 2.5em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .assessment-chips { display: inline-flex; flex: 0 0 auto; gap: 3px; }
    <?php dashboardChipStyles(); ?>
    .article-summary > .assessment-chip, .article-summary .assessment-chips > .assessment-chip { flex: 0 1 auto; min-width: 1.75em; }
    .article-summary > .assessment-chips { flex: 0 1 auto; min-width: 2.8125rem; }
    .pageviews-rank, .pageviews-number { color: var(--muted); font-size: .875rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .entry-list { margin: 0; padding: 0; list-style: none; }
    .entry-list li { padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 1rem; }
    .entry-list li:last-child { border-bottom: 0; }
    .entry-compact li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 5px; align-items: baseline; }
    .entry-numbered li { grid-template-columns: 1.25rem minmax(0, 1fr) auto; }
    .entry-compact .entry-meta { flex-wrap: nowrap; white-space: nowrap; }
    .entry-rank { color: var(--muted); font-size: .875rem; font-variant-numeric: tabular-nums; }
    .entry-meta { display: flex; flex-wrap: wrap; align-items: center; gap: .35rem; color: var(--muted); font-size: .875rem; }
    .entry-date { white-space: nowrap; }
    .entries-scroll { max-height: 360px; overflow-y: auto; scrollbar-gutter: stable; padding-inline-end: 12px; }
    .entries-scroll .entry-meta { flex-wrap: nowrap; white-space: nowrap; padding-inline-end: 12px; }
    .date-note-marker { text-decoration: none; cursor: help; }
    .more { margin-top: 7px; font-size: .72rem; }
    .more summary { cursor: pointer; color: var(--link); padding: 6px 0; }
    .homepage-note { margin: 12px 0 0; font-size: .875rem; color: var(--muted); }
    .backlog-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr)); grid-auto-rows: 1fr; gap: 16px; align-items: stretch; }
    .backlog-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0; }
    .backlog-section { min-width: 0; padding: 12px; border: 1px solid var(--line); background: rgba(255, 255, 255, .35); }
    .backlog-section h3 { margin-bottom: 2px; }
    .backlog-task { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; min-width: 0; padding: 11px 0; border-bottom: 1px solid var(--line); font-size: 1rem; }
    .backlog-task:last-child { border-bottom: 0; }
    .backlog-count { flex: 0 0 auto; color: var(--muted); font-size: .875rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .ops { margin-bottom: 14px; }
    .ops-intro { margin: 0 0 14px; font-size: 1rem; }
    .ops-intro p { margin: 0 0 4px; }
    .ops-intro p:last-child { margin-bottom: 0; }
    .table-scroll { overflow-x: auto; }
    .ops table { min-width: 680px; }
    .ops table small { display: block; color: var(--muted); font-size: .875rem; }
    .timestamp { white-space: nowrap; font-variant-numeric: tabular-nums; }
    footer { background: var(--mint); border-top: 1px solid var(--green); color: var(--muted); }
    .footer-inner { padding-top: 15px; padding-bottom: 17px; display: flex; flex-wrap: wrap; justify-content: space-between; gap: .4rem 1rem; font-size: .69rem; }
    .footer-inner p { margin: 0; }
    @media (max-width: 640px) {
      .homepage-grid { grid-template-columns: minmax(0, 1fr); gap: 18px; }
    }
    @media (max-width: 480px) {
      .shell { padding-left: 16px; padding-right: 16px; }
      .topline { min-height: 65px; }
      .brand { font-size: .94rem; }
      .hero { padding-top: 22px; padding-bottom: 23px; }
      h2 { font-size: 1.5rem; }
      .section-aside { font-size: .65rem; }
      .quality-actions a { min-height: 44px; padding-top: 8px; padding-bottom: 8px; }
      .quality-table th, .quality-table td { padding: 9px 8px; }
    }
  </style>
</head>
<body>
  <a class="skip" href="#main"><?= html(uiText('nav.skip')) ?></a>
  <header class="masthead">
    <div class="shell">
      <div class="topline">
        <a class="brand" href="<?= html(uiUrl('/')) ?>"><img class="brand-mark" src="/aranami.svg" width="36" height="36" alt=""><span>Aranami<small><?= html(uiText('brand.context')) ?></small></span></a>
      </div>
      <div class="hero"><h1><?= html(uiText('page.heading')) ?></h1></div>
    </div>
  </header>
  <main class="shell" id="main">
    <div class="update-line"><p><?php if ($updatedAt === null): ?><?= html(uiText('snapshot.none')) ?><?php else: ?><?= html(uiText('snapshot.updated')) ?> <time datetime="<?= html($updatedAt) ?>"><?= html($updatedLabel) ?> UTC</time><?php endif; ?></p></div>
<?php if ($project === null): ?>
    <p class="notice"><?= html(uiText('notice.preparing')) ?></p>
<?php elseif ($errors !== []): ?>
    <p class="notice"><?= html(uiText('notice.partial')) ?></p>
<?php endif; ?>
    <section class="section-flat" id="homepage" aria-labelledby="homepage-heading">
      <div class="section-header"><h2 id="homepage-heading"><?= html(uiText('homepage.heading')) ?></h2></div>
      <div class="homepage-grid">
<?php foreach ($homepageColumns as $column): ?>
        <div class="homepage-column">
<?php foreach ($column as $key): [$title, $description] = $homepageSections[$key]; $items = $homepageItems[$key] ?? null; ?>
        <section class="homepage-card" aria-labelledby="homepage-<?= html($key) ?>"><div class="homepage-card-header"><div><h3 id="homepage-<?= html($key) ?>"><?= html($title) ?></h3><p><?php if ($key === 'pageviews'): dashboardPageviewsDescription($pageviews); else: ?><?= html($description) ?><?php endif; ?></p></div><span class="count" aria-label="<?= html($items === null ? uiText('ops.unavailable') : uiText(match ($key) { 'recognized' => 'homepage.recognized_count', 'pageviews' => 'pageviews.count', default => 'homepage.count' }, ['count' => dashboardNumber(count($items))])) ?>"><?= $items === null ? '—' : dashboardNumber(count($items)) ?></span></div><div class="homepage-card-body">
<?php if ($key === 'pageviews'): dashboardPageviews($pageviews); elseif ($key === 'dyk'): ?>
          <div class="entries-scroll" role="region" aria-labelledby="homepage-dyk" tabindex="0"><?php dashboardEntries($items, true, true); ?></div>
<?php else: dashboardEntries($items, $key === 'recognized', $key === 'recognized'); endif; ?>
<?php if ($items !== null && ($homepageMissing[$key] ?? []) !== []): ?><p class="homepage-card-note"><?= html(uiText('homepage.partial', ['sections' => implode(uiText('format.list_separator'), $homepageMissing[$key])])) ?></p><?php endif; ?></div></section>
<?php endforeach; ?>
        </div>
<?php endforeach; ?>
      </div>
    </section>
    <section class="section-flat" id="backlog" aria-labelledby="backlog-heading">
      <div class="section-header"><h2 id="backlog-heading"><?= html(uiText('backlog.heading')) ?></h2><?php if ($backlog !== null): ?><span class="section-aside"><?= html(uiText('backlog.total', ['count' => dashboardNumber($backlog['total'])])) ?></span><?php endif; ?></div>
      <p class="section-intro"><?= html(uiText('backlog.intro')) ?></p>
<?php if ($backlog === null): ?>
      <p class="empty"><?= html(uiText('backlog.unavailable')) ?></p>
<?php else: ?>
      <div class="backlog-cards">
<?php foreach (dashboardMaintenanceSections($backlog['groups']) as $section => $groups): ?>
      <section class="backlog-section" aria-labelledby="backlog-section-<?= html($section) ?>">
        <h3 id="backlog-section-<?= html($section) ?>"><?= html(dashboardMaintenanceSectionLabel($groups)) ?></h3>
        <div class="backlog-grid">
<?php foreach ($groups as $group): ?>
        <a class="backlog-task" href="<?= html(uiUrl('/maintenance/', ['task' => $group['key'], 'page' => 1])) ?>"><span><?= html(dashboardMaintenanceTaskLabel($group)) ?></span><span class="backlog-count" aria-label="<?= html(uiText('backlog.count', ['count' => dashboardNumber($group['count'])])) ?>"><?= html(dashboardNumber($group['count'])) ?></span></a>
<?php endforeach; ?>
        </div>
      </section>
<?php endforeach; ?>
      </div>
      <p class="homepage-note"><?= html(uiText('backlog.note')) ?></p>
      <p class="quality-updated"><?= html(uiText('quality.observed')) ?> <time datetime="<?= html($backlog['observed_at']) ?>"><?= html((new DateTimeImmutable($backlog['observed_at']))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i')) ?> UTC</time></p>
<?php endif; ?>
    </section>
    <p class="quality-filter-status" data-quality-status data-error="<?= html(uiText('quality.filter_error')) ?>" role="status" aria-live="polite" hidden></p>
    <div class="project-layout">
        <section class="section-flat" id="quality" aria-labelledby="quality-heading">
          <div class="section-header"><h2 id="quality-heading"><?= html(uiText('quality.heading')) ?></h2><span class="section-aside"><?= html(uiText('quality.current')) ?></span></div>
          <p class="section-intro"><?= html(uiText('quality.intro')) ?></p>
<?php if ($quality !== null && ($quality['stale'] ?? false)): ?>
          <p class="cache-notice" role="status"><?= html(uiText('quality.cached')) ?></p>
<?php endif; ?>
<?php if ($quality === null): ?>
          <p class="empty"><?= html(uiText('quality.unavailable')) ?></p>
<?php elseif ($languages === []): ?>
          <p class="empty"><?= html(uiText('quality.empty')) ?></p>
<?php else: ?>
          <div class="quality-actions"><a data-quality-filter href="<?= html(uiUrl('', ['random' => '1', 'language' => $selectedLanguage]) . '#quality') ?>"><?= html(uiText('quality.random')) ?></a><?php if ($selectedLanguage !== ''): ?><a data-quality-filter href="<?= html(uiUrl('/') . '#quality') ?>"><?= html(uiText('quality.all_languages')) ?></a><?php endif; ?></div>
<?php if ($selectedLanguage !== ''): ?>
          <p class="sample-label"><?= html(uiText($selectedStatus === '' ? 'quality.sample' : 'quality.sample_status', ['language' => dashboardLanguage($selectedLanguage), 'status' => $selectedStatus === '' ? '' : dashboardStatus($selectedStatus), 'count' => dashboardNumber(count($qualityItems))])) ?></p>
<?php endif; ?>
<?php if ($qualityItems === null): ?>
          <p class="empty"><?= html(uiText('quality.unavailable')) ?></p>
<?php elseif ($qualityItems === []): ?>
          <p class="empty"><?= html(uiText('quality.sample_empty')) ?></p>
<?php else: ?>
          <div class="quality-scroll" role="region" aria-label="<?= html(uiText('quality.all_aria')) ?>" tabindex="0"><table class="quality-table" id="quality-articles" data-quality-table aria-label="<?= html($selectedLanguage === '' ? uiText('quality.all_aria') : uiText('quality.table_aria', ['language' => dashboardLanguage($selectedLanguage)])) ?>"><thead><tr><th scope="col"><?= html(uiText('column.language')) ?></th><th scope="col"><?= html(uiText('column.article')) ?></th><th scope="col"><?= html(uiText('column.local_article')) ?></th></tr></thead><tbody>
<?php foreach ($qualityItems as $item): ?>
            <tr><td class="quality-language" title="<?= html(uiText('quality.language_label', ['language' => dashboardLanguage($item['language']), 'code' => $item['language']])) ?>"><?= html(dashboardLanguage($item['language'])) ?><span class="language-code"><?= html(uiLocale() === 'en' ? ' (' . $item['language'] . ')' : '（' . $item['language'] . '）') ?></span></td><th scope="row"><?php dashboardQualityTitle($item); ?></th><td><?php dashboardQualityCompanion($item); ?></td></tr>
<?php endforeach; ?>
          </tbody></table></div>
<?php if ($selectedLanguage !== ''): ?>
          <div class="quality-pagination"><p data-quality-progress data-template="<?= html(uiText('quality.progress')) ?>" aria-live="polite"><?= html(uiText('quality.progress', ['shown' => dashboardNumber(count($qualityItems)), 'total' => dashboardNumber(count($qualityItems))])) ?></p><button type="button" data-quality-show-more data-template="<?= html(uiText('quality.show_more')) ?>" aria-controls="quality-articles" hidden><?= html(uiText('quality.show_more', ['count' => '25'])) ?></button></div>
<?php endif; ?>
<?php endif; ?>
<?php if ($qualityObservedAt !== null): ?>
          <p class="quality-updated"><?= html(uiText('quality.observed')) ?> <time datetime="<?= html($qualityObservedAt) ?>"><?= html($qualityObservedLabel) ?> UTC</time></p>
<?php endif; ?>
<?php endif; ?>
        </section>
    </div>
    <section class="ops" id="tool-status" aria-labelledby="tool-status-heading"><div class="section-header"><h2 id="tool-status-heading"><?= html(uiText('ops.heading')) ?></h2></div>
      <div class="ops-intro"><p><?= html(uiText('ops.purpose')) ?></p>

      </div>
      <div class="table-scroll" role="region" aria-label="<?= html(uiText('ops.aria')) ?>" tabindex="0"><table><thead><tr><th scope="col"><?= html(uiText('ops.column_task')) ?></th><th scope="col"><?= html(uiText('ops.column_state')) ?></th><th scope="col"><?= html(uiText('ops.column_duration')) ?></th><th scope="col"><?= html(uiText('ops.column_time')) ?></th></tr></thead><tbody>
<?php foreach (ROUTINES as $id => $name): $event = $activity['latest'][$id] ?? null; ?>
        <tr><th scope="row"><?= html($routineNames[$id]) ?></th><?php if ($event === null): ?><td><?= html(uiText('ops.no_record')) ?></td><td>—</td><td>—</td><?php else: ?><td><span aria-hidden="true"><?= ['Running' => '⏳', 'Updated' => '✓', 'NoUpdate' => '—', 'Failed' => '⚠', 'Deferred' => '↷', 'Result unavailable' => '?'][$event['status']] ?></span> <?= html(uiText('state.' . $event['status'])) ?></td><td><?= html(uiDuration($event['duration'])) ?></td><td class="timestamp"><time datetime="<?= html($event['timestamp']) ?>"><?= html((new DateTimeImmutable($event['timestamp']))->format('Y-m-d H:i:s')) ?></time></td><?php endif; ?></tr>
<?php endforeach; ?>
      </tbody><tfoot><tr><td colspan="4"><?= html(uiText('ops.installed')) ?> <strong><?= html($versionLabel) ?></strong> · <a href="<?= html(uiUrl('/logs/aranami/')) ?>"><?= html(uiText('ops.browse_logs')) ?></a></td></tr></tfoot></table></div>
    </section>
  </main>
  <footer><div class="shell footer-inner"><p><?= html(uiText('footer.title')) ?></p><p><a href="<?= html(uiUrl('/logs/')) ?>"><?= html(uiText('nav.logs')) ?></a> · <a href="https://zh.wikipedia.org/wiki/WikiProject:电子游戏"><?= html(uiText('nav.project')) ?> ↗</a></p></div></footer>
</body>
</html>
