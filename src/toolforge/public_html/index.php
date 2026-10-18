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
    return in_array($status, ['FA', 'FL', 'GA', 'recommended'], true) ? uiText('badge.' . $status) : $status;
}

/** Render a foreign article with its same-item Chinese counterpart. */
function dashboardQualityTitle(array $item): void
{
    $language = $item['language'] ?? 'en';
    echo '<a href="' . html($item['url']) . '"><bdi lang="' . html($language) . '">' . html($item['title']) . '</bdi></a>';
    if ($language === 'zh') {
        return;
    }
    if (isset($item['zh_title'], $item['zh_url'])) {
        $grade = $item['zh_class'] ?? null;
        echo '<a class="chinese-companion" href="' . html($item['zh_url']) . '"><span lang="zh"><bdi>'
            . html($item['zh_title']) . '</bdi></span>'
            . html(uiText($grade === null ? 'quality.companion_unknown' : 'quality.companion_known', ['grade' => $grade ?? ''])) . '</a>';
    } else {
        echo '<span class="companion-missing">' . html(uiText('quality.companion_missing')) . '</span>';
    }
}

/** Render one compact recognition link per foreign Wikipedia edition. */
function dashboardQualityLanguages(array $articles): void
{
    echo '<ul class="quality-languages">';
    foreach ($articles as $article) {
        echo '<li><a class="recognition recognition-' . html($article['status']) . '" href="'
            . html($article['url']) . '" title="' . html(dashboardLanguage($article['language'])
                . ' · ' . $article['title'] . ' · ' . dashboardStatus($article['status'])) . '">'
            . html($article['language']) . ' · ' . html(uiText('quality.short.' . $article['status'])) . '</a></li>';
    }
    echo '</ul>';
}

/** Render primary homepage subjects with their observed display dates. */
function dashboardEntryRows(array $items): void
{
    echo '<ul class="entry-list">';
    foreach ($items as $item) {
        echo '<li><a href="' . html($item['url']) . '">' . html($item['title']) . '</a>';
        echo '<span class="entry-meta">';
        if (($item['date'] ?? null) !== null) {
            echo '<time datetime="' . html($item['date']) . '">' . html($item['date']) . '</time>';
        }
        if (($item['date_kind'] ?? '') === 'window_start') {
            echo '<span class="date-note">' . html(uiText('date.window_start')) . '</span>';
        } elseif (($item['date_kind'] ?? '') === 'observed') {
            echo '<span class="date-note">' . html(uiText('date.observed')) . '</span>';
        } elseif (($item['date_kind'] ?? '') === 'scheduled') {
            echo '<span class="content-kind scheduled">' . html(uiText('date.scheduled')) . '</span>';
        }
        $kind = ['fa' => 'badge.FA', 'fl' => 'badge.FL', 'ga' => 'badge.GA', 'featured' => 'badge.featured', 'news' => 'homepage.news', 'history' => 'homepage.history'][$item['kind'] ?? ''] ?? null;
        if ($kind !== null) {
            echo '<span class="content-kind">' . html(uiText($kind)) . '</span>';
        }
        echo '</span></li>';
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

/** Bound the initial list and keep unavailable and empty observations distinct. */
function dashboardEntries(?array $items, int $preview = 5, string $emptyMessage = 'empty.period'): void
{
    if ($items === null) {
        echo '<p class="empty">' . html(uiText('empty.section')) . '</p>';
    } elseif ($items === []) {
        echo '<p class="empty">' . html(uiText($emptyMessage)) . '</p>';
    } else {
        dashboardEntryRows(array_slice($items, 0, $preview));
        if (count($items) > $preview) {
            echo '<details class="more"><summary>' . html(uiText('records.more', ['count' => dashboardNumber(count($items) - $preview)])) . '</summary>';
            dashboardEntryRows(array_slice($items, $preview));
            echo '</details>';
        }
    }
}

/** Render the observed daily project matches without implying full-site coverage. */
function dashboardPageviews(?array $observation): void
{
    if ($observation === null) {
        echo '<p class="empty">' . html(uiText('pageviews.unavailable')) . '</p>';
        return;
    }
    echo '<p class="pageviews-date"><time datetime="' . html($observation['date']) . '">'
        . html($observation['date']) . '</time> · UTC</p>';
    if ($observation['items'] === []) {
        echo '<p class="empty">' . html(uiText('pageviews.empty')) . '</p>';
    } else {
        echo '<ol class="pageviews-list" role="list">';
        foreach ($observation['items'] as $item) {
            echo '<li><span class="pageviews-rank" aria-hidden="true">' . html((string) $item['rank'])
                . '.</span><a href="' . html($item['url']) . '"><bdi lang="zh">' . html($item['title'])
                . '</bdi></a><span class="pageviews-number">'
                . html(uiText('pageviews.views', ['count' => dashboardNumber($item['views'])])) . '</span></li>';
        }
        echo '</ol>';
    }
    echo '<p class="homepage-card-note pageviews-coverage">' . html(uiText('pageviews.coverage'))
        . ' <a href="' . html($observation['source_url']) . '">' . html(uiText('pageviews.source')) . '</a></p>';
}

$project = projectDashboard('/data/project/aranami/cache/toolforge/dashboard.json');
$period = $project['period'] ?? null;
$periodLabel = $period['label'] ?? uiText('period.default');
$updatedAt = $project['updated_at'] ?? null;
$updatedLabel = $updatedAt === null ? null : (new DateTimeImmutable($updatedAt))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i');
$quality = $project['quality'] ?? null;
$qualityObservedAt = $quality['observed_at'] ?? null;
$qualityObservedLabel = $qualityObservedAt === null ? null : (new DateTimeImmutable($qualityObservedAt))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i');
$homepage = $project['homepage'] ?? null;
$pageviews = $project['pageviews'] ?? null;
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
$languageCounts = [];
foreach ($quality['counts'] ?? [] as $row) {
    $language = $row['language'];
    $languageCounts[$language] ??= ['FA' => 0, 'FL' => 0, 'GA' => 0, 'recommended' => 0];
    $languageCounts[$language][$row['status']] += $row['count'];
}
$languageTotals = [];
foreach ($quality['language_totals'] ?? [] as $row) {
    $languageTotals[$row['language']] = ($languageTotals[$row['language']] ?? 0) + $row['count'];
}
foreach ($languageCounts as $language => $distribution) {
    // Older snapshots lack distinct totals; keep their badge-count ordering until refreshed.
    $languageTotals[$language] ??= array_sum($distribution);
}
uksort($languageCounts, static fn (string $left, string $right): int =>
    ($languageTotals[$right] <=> $languageTotals[$left]) ?: strcmp($left, $right));
$requestedLanguage = $_GET['language'] ?? '';
$selectedLanguage = is_string($requestedLanguage) && isset($languageCounts[$requestedLanguage]) ? $requestedLanguage : '';
$requestedStatus = $_GET['status'] ?? '';
$selectedStatus = is_string($requestedStatus) && in_array($requestedStatus, ['FA', 'FL', 'GA', 'recommended'], true) ? $requestedStatus : '';
if (($_GET['random'] ?? null) === '1') {
    $candidates = array_values(array_filter(array_keys($languageCounts), static fn (string $language): bool =>
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
$popularItems = $quality['popular_items'] ?? null;
$qualityItems = array_values(array_filter($quality['items'] ?? [], static fn (array $item): bool =>
    $item['language'] === $selectedLanguage && ($selectedStatus === '' || $item['status'] === $selectedStatus)));
$homepageSections = [
    'pageviews' => [uiText('pageviews.heading'), uiText('pageviews.description')],
    'recognized' => [uiText('homepage.recognized'), uiText('homepage.recognized_description')],
    'dyk' => [uiText('homepage.dyk'), uiText('homepage.dyk_description')],
    'news_history' => [uiText('homepage.news_history'), uiText('homepage.news_history_description')],
];
$homepageColumns = [['pageviews', 'recognized', 'news_history'], ['dyk']];
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
    :root { color-scheme: light; font-family: system-ui, -apple-system, "Noto Sans TC", "Microsoft JhengHei", sans-serif; --green: #a8d9cb; --mint: #edf9f4; --paper: #ffffff; --pale: #fcfffe; --ink: #2d4b42; --muted: #5d766c; --line: #d9ebe4; --link: #316e5e; }
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--ink); background: var(--pale); line-height: 1.7; }
    a { color: var(--link); text-underline-offset: .2em; text-decoration-thickness: 1px; overflow-wrap: anywhere; }
    a:hover { text-decoration-thickness: 2px; }
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
    nav { display: flex; gap: 1.1rem; flex-wrap: wrap; font-size: .82rem; }
    nav a { text-decoration: none; }
    .hero { padding-top: 27px; padding-bottom: 28px; }
    .eyebrow { margin: 0 0 .4rem; font-size: .7rem; letter-spacing: .15em; font-weight: 700; }
    h1 { margin: 0; font-size: clamp(1.8rem, 5vw, 2.45rem); line-height: 1.35; letter-spacing: .025em; }
    .intro { margin: .65rem 0 0; font-size: .86rem; color: var(--muted); }
    main { padding-top: 20px; padding-bottom: 26px; }
    .update-line { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .3rem 1rem; margin-bottom: 18px; font-size: .72rem; color: var(--muted); }
    .update-line p { margin: 0; }
    .notice { margin: 0 0 18px; padding: 12px 16px; background: var(--mint); border: 1px solid var(--green); border-left-width: 4px; border-radius: 8px; font-size: .8rem; }
    .project-layout { display: grid; grid-template-columns: minmax(0, 1fr) 254px; gap: 18px; align-items: start; }
    .primary, aside { min-width: 0; }
    .panel { background: var(--paper); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 18px; overflow: hidden; }
    .panel-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: .6rem; padding: 14px 16px; background: var(--mint); border-bottom: 1px solid var(--line); }
    .section-title { display: flex; align-items: center; gap: .6rem; }
    .section-number { font-size: .67rem; color: #326b5a; border: 1px solid var(--green); width: 26px; height: 26px; display: inline-grid; place-items: center; border-radius: 50%; }
    h2 { margin: 0; font-size: .98rem; letter-spacing: .02em; }
    .section-aside { font-size: .69rem; color: var(--muted); }
    .panel-body { padding: 15px 16px; }
    .empty { margin: 0; padding: 17px 16px; font-size: .79rem; color: var(--muted); }
    .panel-body > .empty { padding: 0; }
    .section-intro { margin: 0; padding: 12px 16px; font-size: .75rem; color: var(--muted); border-bottom: 1px solid var(--line); }
    .quality-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 18px; padding: 10px 16px; border-bottom: 1px solid var(--line); font-size: .74rem; }
    .quality-actions a { display: inline-block; padding: 4px 0; }
    .sample-label { padding: 10px 16px; font-size: .71rem; color: var(--muted); margin: 0; }
    table { width: 100%; border-collapse: collapse; font-size: .77rem; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
    thead th { color: var(--muted); background: var(--mint); font-size: .69rem; font-weight: 650; }
    tbody th { font-weight: 500; }
    tbody tr:nth-child(even) { background: var(--pale); }
    tbody tr:last-child td, tbody tr:last-child th { border-bottom: 0; }
    .quality-table { table-layout: fixed; }
    .quality-table th:first-child { width: 48%; }
    .quality-table th:nth-child(2) { width: 18%; }
    .quality-table th:nth-child(3) { width: 34%; }
    .popular-table th:first-child { width: 44%; }
    .popular-table th:nth-child(2) { width: 56%; }
    .quality-languages { display: flex; flex-wrap: wrap; gap: 5px; padding: 0; margin: 6px 0; list-style: none; }
    .quality-languages a { white-space: nowrap; }
    .recognized-total { color: var(--muted); font-size: .71rem; }
    .quality-filter-status { margin: 0 0 14px; font-size: .77rem; padding: 9px 13px; border: 1px solid var(--green); border-radius: 6px; background: var(--mint); }
    .quality-pagination { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 11px 16px; color: var(--muted); font-size: .7rem; border-top: 1px solid var(--line); }
    .quality-pagination p { margin: 0; }
    .quality-pagination button { padding: 7px 11px; border: 1px solid var(--green); border-radius: 5px; color: var(--ink); background: var(--mint); font: inherit; cursor: pointer; }
    .homepage-period-title { margin: 0; padding: 9px 13px 5px; font-size: .7rem; color: var(--muted); background: var(--pale); }
    .quality-table .language-code { display: block; color: var(--muted); font-size: .65rem; }
    .chinese-companion { display: block; margin-top: 5px; font-size: .69rem; font-weight: 400; }
    .companion-missing { display: block; margin-top: 5px; color: var(--muted); font-size: .66rem; font-weight: 400; }
    .recognition { display: inline-block; padding: 2px 6px; border: 1px solid var(--green); border-radius: 4px; background: var(--mint); font-size: .66rem; line-height: 1.65; }
    .recognition-FL { background: #f4fcf8; }
    .recognition-GA { background: var(--pale); }
    .quality-note { margin: 0; padding: 11px 16px; font-size: .66rem; color: var(--muted); border-top: 1px solid var(--line); }
    .counts-scroll { max-height: 560px; overflow: auto; padding: 0 14px 10px; scrollbar-gutter: stable both-edges; }
    .counts-table { table-layout: fixed; font-size: .68rem; }
    .counts-table th, .counts-table td { padding: 8px 5px; }
    .counts-table thead th { position: sticky; top: 0; font-size: .65rem; }
    .counts-table .numeric { white-space: nowrap; overflow-wrap: normal; }
    .counts-table th:first-child { width: 36%; padding-left: 11px; }
    .counts-table .selected { background: var(--mint); }
    .numeric { text-align: right; font-variant-numeric: tabular-nums; }
    .count-note { margin: 0; padding: 11px 13px; font-size: .65rem; color: var(--muted); border-top: 1px solid var(--line); }
    h3 { margin: 0 0 9px; font-size: .82rem; }
    .homepage-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; align-items: start; }
    .homepage-column { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
    .homepage-card { min-width: 0; border: 1px solid var(--line); border-radius: 7px; overflow: hidden; background: var(--pale); }
    .homepage-card-header { display: flex; align-items: center; justify-content: space-between; gap: .6rem; background: var(--mint); padding: 10px 12px; border-bottom: 1px solid var(--line); }
    .homepage-card h3 { margin: 0; font-size: .81rem; }
    .homepage-card-header p { margin: 1px 0 0; color: var(--muted); font-size: .66rem; }
    .count { min-width: 27px; min-height: 27px; padding: 1px 7px; display: inline-grid; place-items: center; background: var(--mint); border: 1px solid var(--green); border-radius: 20px; font-size: .7rem; font-variant-numeric: tabular-nums; }
    .homepage-card-body { padding: 2px 12px 9px; }
    .homepage-card-body > .empty { padding: 11px 0 3px; }
    .homepage-card-note { margin: 8px 0 2px; color: var(--muted); font-size: .67rem; }
    .pageviews-date { margin: 8px 0 5px; color: var(--muted); font-size: .7rem; }
    .pageviews-list { margin: 0; padding: 0; list-style: none; }
    .pageviews-list li { display: grid; grid-template-columns: 1.25rem minmax(0, 1fr) auto; gap: 5px; align-items: baseline; padding: 8px 0; border-bottom: 1px solid var(--line); font-size: .77rem; }
    .pageviews-list li:last-child { border-bottom: 0; }
    .pageviews-rank, .pageviews-number { color: var(--muted); font-size: .65rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .pageviews-coverage { border-top: 1px solid var(--line); padding-top: 8px; }
    .entry-list { margin: 0; padding: 0; list-style: none; }
    .entry-list li { padding: 9px 0; border-bottom: 1px solid var(--line); font-size: .8rem; }
    .entry-list li:last-child { border-bottom: 0; }
    .entry-meta { display: flex; flex-wrap: wrap; align-items: center; gap: .35rem .6rem; color: var(--muted); font-size: .67rem; margin-top: 3px; }
    .date-note { padding-left: .6rem; border-left: 1px solid var(--green); }
    .content-kind { padding: 0 5px; background: var(--mint); border: 1px solid var(--line); border-radius: 3px; font-size: .63rem; }
    .more { margin-top: 7px; font-size: .72rem; }
    .more summary { cursor: pointer; color: var(--link); padding: 6px 0; }
    .homepage-note { margin: 12px 0 0; font-size: .68rem; color: var(--muted); }
    .ops { border: 1px solid var(--line); background: var(--paper); border-radius: 9px; overflow: hidden; margin-bottom: 14px; }
    .ops > summary { padding: 14px 16px; background: var(--mint); cursor: pointer; font-size: .79rem; font-weight: 650; }
    .summary-meta { float: right; font-size: .7rem; font-weight: 400; color: var(--muted); }
    .ops-intro { padding: 13px 16px; font-size: .73rem; border-top: 1px solid var(--line); }
    .ops-intro p { margin: 0 0 4px; }
    .ops-intro p:last-child { margin-bottom: 0; }
    .table-scroll { overflow-x: auto; }
    .ops table { font-size: .72rem; min-width: 580px; }
    .ops table small { display: block; color: var(--muted); font-size: .65rem; }
    .badge { display: inline-block; padding: 1px 5px; background: var(--mint); border: 1px solid var(--line); border-radius: 4px; font-size: .67rem; }
    .failure { border-style: dashed; font-weight: 750; }
    .timestamp { white-space: nowrap; font-variant-numeric: tabular-nums; }
    footer { background: var(--mint); border-top: 1px solid var(--green); color: var(--muted); }
    .footer-inner { padding-top: 15px; padding-bottom: 17px; display: flex; flex-wrap: wrap; justify-content: space-between; gap: .4rem 1rem; font-size: .69rem; }
    .footer-inner p { margin: 0; }
    @media (max-width: 760px) {
      .project-layout { grid-template-columns: minmax(0, 1fr); gap: 0; }
      .counts-scroll { max-height: 280px; }
      .counts-table { font-size: .75rem; }
      .counts-table th, .counts-table td { padding: 9px 12px; }
    }
    @media (max-width: 480px) {
      .shell { padding-left: 16px; padding-right: 16px; }
      .topline { min-height: 65px; }
      .brand { font-size: .94rem; }
      nav { gap: .65rem; font-size: .71rem; }
      nav .project-nav { display: none; }
      .hero { padding-top: 22px; padding-bottom: 23px; }
      .intro { font-size: .78rem; }
      .panel-header, .panel-body { padding: 13px; }
      h2 { font-size: .91rem; }
      .section-aside { font-size: .65rem; }
      .section-intro, .quality-actions, .sample-label { padding-left: 13px; padding-right: 13px; }
      .quality-actions a { min-height: 44px; padding-top: 8px; padding-bottom: 8px; }
      .quality-table th, .quality-table td { padding: 9px 8px; font-size: .72rem; }
      .quality-table th:first-child { width: 44%; }
      .quality-table th:nth-child(2) { width: 19%; }
      .quality-table th:nth-child(3) { width: 37%; }
      html[lang="en"] .quality-table th:first-child { width: 41%; }
      html[lang="en"] .quality-table th:nth-child(2) { width: 23%; }
      html[lang="en"] .quality-table th:nth-child(3) { width: 36%; }
      .recognition { font-size: .62rem; padding: 2px 5px; }
      .homepage-grid { grid-template-columns: minmax(0, 1fr); }
      .summary-meta { float: none; display: block; margin-left: 1rem; margin-top: 2px; }
    }
  </style>
</head>
<body>
  <a class="skip" href="#main"><?= html(uiText('nav.skip')) ?></a>
  <header class="masthead">
    <div class="shell">
      <div class="topline">
        <a class="brand" href="<?= html(uiUrl('/')) ?>"><img class="brand-mark" src="/aranami.svg" width="36" height="36" alt=""><span>Aranami<small><?= html(uiText('brand.context')) ?></small></span></a>
        <nav aria-label="<?= html(uiText('nav.aria')) ?>"><a href="#homepage"><?= html(uiText('nav.homepage')) ?></a><a href="#quality"><?= html(uiText('nav.quality')) ?></a><a class="project-nav" href="https://zh.wikipedia.org/wiki/WikiProject:电子游戏"><?= html(uiText('nav.project')) ?> ↗</a></nav>
      </div>
      <div class="hero"><p class="eyebrow"><?= html(uiText('project.eyebrow')) ?></p><h1><?= html(uiText('page.heading')) ?></h1><p class="intro"><?= html(uiText('hero.intro')) ?></p>
      </div>
    </div>
  </header>
  <main class="shell" id="main">
    <div class="update-line"><p><?= html(uiText('snapshot.context')) ?></p><p><?php if ($updatedAt === null): ?><?= html(uiText('snapshot.none')) ?><?php else: ?><?= html(uiText('snapshot.updated')) ?> <time datetime="<?= html($updatedAt) ?>"><?= html($updatedLabel) ?> UTC</time><?php endif; ?></p></div>
<?php if ($project === null): ?>
    <p class="notice"><?= html(uiText('notice.preparing')) ?></p>
<?php elseif ($errors !== []): ?>
    <p class="notice"><?= html(uiText('notice.partial')) ?></p>
<?php endif; ?>
    <section class="panel" id="homepage" aria-labelledby="homepage-heading">
      <div class="panel-header"><div class="section-title"><span class="section-number" aria-hidden="true">01</span><h2 id="homepage-heading"><?= html(uiText('homepage.heading')) ?></h2></div><span class="section-aside"><?= html($periodLabel) ?><?php if ($period !== null): ?> · UTC<?php endif; ?></span></div>
      <p class="section-intro"><?= html(uiText('homepage.intro')) ?></p>
      <div class="panel-body"><div class="homepage-grid">
<?php foreach ($homepageColumns as $column): ?>
        <div class="homepage-column">
<?php foreach ($column as $key): [$title, $description] = $homepageSections[$key]; $items = $homepageItems[$key] ?? null; ?>
        <section class="homepage-card" aria-labelledby="homepage-<?= html($key) ?>"><div class="homepage-card-header"><div><h3 id="homepage-<?= html($key) ?>"><?= html($title) ?></h3><p><?= html($description) ?></p></div><span class="count" aria-label="<?= html($items === null ? uiText('ops.unavailable') : uiText(match ($key) { 'recognized' => 'homepage.recognized_count', 'pageviews' => 'pageviews.count', default => 'homepage.count' }, ['count' => dashboardNumber(count($items))])) ?>"><?= $items === null ? '—' : dashboardNumber(count($items)) ?></span></div><div class="homepage-card-body">
<?php if ($key === 'recognized'): ?>
          <h4 class="homepage-period-title"><?= html(uiText('homepage.upcoming_heading')) ?></h4>
<?php dashboardEntries($upcomingItems, 5, 'homepage.upcoming_empty'); ?>
          <h4 class="homepage-period-title"><?= html(uiText('homepage.past_heading')) ?></h4>
<?php dashboardEntries($recognizedPastItems); ?>
<?php elseif ($key === 'pageviews'): dashboardPageviews($pageviews); else: dashboardEntries($items); endif; ?>
<?php if ($items !== null && ($homepageMissing[$key] ?? []) !== []): ?><p class="homepage-card-note"><?= html(uiText('homepage.partial', ['sections' => implode(uiText('format.list_separator'), $homepageMissing[$key])])) ?></p><?php endif; ?></div></section>
<?php endforeach; ?>
        </div>
<?php endforeach; ?>
      </div><p class="homepage-note"><?= html(uiText('homepage.note')) ?></p></div>
    </section>
    <p class="quality-filter-status" data-quality-status data-error="<?= html(uiText('quality.filter_error')) ?>" role="status" aria-live="polite" hidden></p>
    <div class="project-layout">
      <div class="primary">
        <section class="panel" id="quality" aria-labelledby="quality-heading">
          <div class="panel-header"><div class="section-title"><span class="section-number" aria-hidden="true">02</span><h2 id="quality-heading"><?= html(uiText('quality.heading')) ?></h2></div><span class="section-aside"><?= html(uiText('quality.current')) ?></span></div>
          <p class="section-intro"><?= html(uiText('quality.intro')) ?></p>
<?php if ($quality === null): ?>
          <p class="empty"><?= html(uiText('quality.unavailable')) ?></p>
<?php elseif ($languageCounts === []): ?>
          <p class="empty"><?= html(uiText('quality.empty')) ?></p>
<?php else: ?>
          <div class="quality-actions"><a data-quality-filter href="<?= html(uiUrl('', ['random' => '1', 'language' => $selectedLanguage]) . '#quality') ?>"><?= html(uiText('quality.random')) ?></a><?php if ($selectedLanguage !== ''): ?><a data-quality-filter href="<?= html(uiUrl('/') . '#quality') ?>"><?= html(uiText('quality.all_languages')) ?></a><?php endif; ?></div>
<?php if ($selectedLanguage === ''): ?>
          <p class="sample-label"><?= html(uiText('quality.popular_intro')) ?></p>
<?php if ($popularItems === null): ?>
          <p class="empty"><?= html(uiText('quality.popular_pending')) ?></p>
<?php elseif ($popularItems === []): ?>
          <p class="empty"><?= html(uiText('quality.empty')) ?></p>
<?php else: ?>
          <table class="quality-table popular-table" aria-label="<?= html(uiText('quality.popular_aria')) ?>"><thead><tr><th scope="col"><?= html(uiText('column.article')) ?></th><th scope="col"><?= html(uiText('quality.recognized_languages')) ?></th></tr></thead><tbody>
<?php foreach ($popularItems as $item): ?>
            <tr><th scope="row"><?php dashboardQualityTitle($item); ?></th><td><strong class="recognized-total"><?= html(uiText('quality.language_count', ['count' => dashboardNumber($item['language_count'])])) ?></strong><?php dashboardQualityLanguages(array_slice($item['articles'], 0, 6)); ?><?php if (count($item['articles']) > 6): ?><details class="more"><summary><?= html(uiText('quality.more_languages', ['count' => dashboardNumber(count($item['articles']) - 6)])) ?></summary><?php dashboardQualityLanguages(array_slice($item['articles'], 6)); ?></details><?php endif; ?></td></tr>
<?php endforeach; ?>
          </tbody></table>
<?php endif; ?>
<?php else: ?>
          <p class="sample-label"><?= html(uiText($selectedStatus === '' ? 'quality.sample' : 'quality.sample_status', ['language' => dashboardLanguage($selectedLanguage), 'status' => $selectedStatus === '' ? '' : dashboardStatus($selectedStatus), 'count' => dashboardNumber(count($qualityItems))])) ?></p>
<?php if ($qualityItems === []): ?>
          <p class="empty"><?= html(uiText('quality.sample_empty')) ?></p>
<?php else: ?>
          <table class="quality-table" id="quality-articles" data-quality-table aria-label="<?= html(uiText('quality.table_aria', ['language' => dashboardLanguage($selectedLanguage)])) ?>"><thead><tr><th scope="col"><?= html(uiText('column.article')) ?></th><th scope="col"><?= html(uiText('column.language')) ?></th><th scope="col"><?= html(uiText('column.recognition')) ?></th></tr></thead><tbody>
<?php foreach ($qualityItems as $item): ?>
            <tr><th scope="row"><?php dashboardQualityTitle($item); ?></th><td><?= html(dashboardLanguage($item['language'])) ?><span class="language-code"><?= html($item['language']) ?></span></td><td><span class="recognition recognition-<?= html($item['status']) ?>"><?= html(dashboardStatus($item['status'])) ?></span></td></tr>
<?php endforeach; ?>
          </tbody></table>
          <div class="quality-pagination"><p data-quality-progress data-template="<?= html(uiText('quality.progress')) ?>" aria-live="polite"><?= html(uiText('quality.progress', ['shown' => dashboardNumber(count($qualityItems)), 'total' => dashboardNumber(count($qualityItems))])) ?></p><button type="button" data-quality-show-more data-template="<?= html(uiText('quality.show_more')) ?>" aria-controls="quality-articles" hidden><?= html(uiText('quality.show_more', ['count' => '25'])) ?></button></div>
<?php endif; ?>
<?php endif; ?>
          <p class="quality-note"><?= html(uiText('quality.note')) ?><?php if ($qualityObservedAt !== null): ?><br><?= html(uiText('quality.observed')) ?> <time datetime="<?= html($qualityObservedAt) ?>"><?= html($qualityObservedLabel) ?> UTC</time><?php endif; ?></p>
<?php endif; ?>
        </section>
      </div>
      <aside aria-labelledby="counts-heading">
        <section class="panel"><div class="panel-header"><h2 id="counts-heading"><?= html(uiText('counts.heading')) ?></h2></div>
<?php if ($quality === null): ?>
          <p class="empty"><?= html(uiText('counts.unavailable')) ?></p>
<?php else: ?>
<?php if ($languageCounts !== []): ?>
          <div class="counts-scroll" role="region" aria-label="<?= html(uiText('counts.aria')) ?>" tabindex="0"><table class="counts-table"><thead><tr><th scope="col"><?= html(uiText('column.language')) ?></th><?php foreach (['FA', 'FL', 'GA', 'recommended'] as $status): ?><th scope="col" class="numeric"><?= html(uiText('quality.short.' . $status)) ?></th><?php endforeach; ?></tr></thead><tbody>
<?php foreach ($languageCounts as $language => $distribution): ?>
            <tr<?= $language === $selectedLanguage ? ' class="selected"' : '' ?>><th scope="row"><a data-quality-filter href="<?= html(uiUrl('', ['language' => $language]) . '#quality') ?>"<?= $language === $selectedLanguage && $selectedStatus === '' ? ' aria-current="true"' : '' ?>><?= html(dashboardLanguage($language)) ?></a></th><?php foreach (['FA', 'FL', 'GA', 'recommended'] as $status): ?><td class="numeric"><a data-quality-filter href="<?= html(uiUrl('', ['language' => $language, 'status' => $status]) . '#quality') ?>"<?= $language === $selectedLanguage && $status === $selectedStatus ? ' aria-current="true"' : '' ?>><?= html(dashboardNumber($distribution[$status])) ?></a></td><?php endforeach; ?></tr>
<?php endforeach; ?>
          </tbody></table></div>
<?php endif; ?>
          <p class="count-note"><?= html(uiText('counts.note')) ?></p>
<?php endif; ?>
        </section>
      </aside>
    </div>
    <details class="ops" id="tool-status"><summary><?= html(uiText('ops.heading')) ?><span class="summary-meta"><?= html(uiText('ops.version_summary', ['version' => $versionLabel])) ?></span></summary>
      <div class="ops-intro"><p><?= html(uiText('ops.purpose')) ?></p>

      </div>
      <div class="table-scroll" role="region" aria-label="<?= html(uiText('ops.aria')) ?>" tabindex="0"><table><thead><tr><th scope="col"><?= html(uiText('ops.column_task')) ?></th><th scope="col"><?= html(uiText('ops.column_state')) ?></th><th scope="col"><?= html(uiText('ops.column_duration')) ?></th><th scope="col"><?= html(uiText('ops.column_time')) ?></th></tr></thead><tbody>
<?php foreach (ROUTINES as $id => $name): $event = $activity['latest'][$id] ?? null; ?>
        <tr><th scope="row"><?= html($routineNames[$id]) ?></th><?php if ($event === null): ?><td><?= html(uiText('ops.no_record')) ?></td><td>—</td><td>—</td><?php else: ?><td><span class="badge <?= html($event['tone']) ?>"><?= html(uiText('state.' . $event['status'])) ?></span></td><td><?= html(uiDuration($event['duration'])) ?></td><td class="timestamp"><a href="/logs/aranami/<?= html(rawurlencode($event['filename'])) ?>"><time datetime="<?= html(str_replace(' ', 'T', $event['timestamp'])) ?>"><?= html(substr($event['timestamp'], 0, -1)) ?></time></a></td><?php endif; ?></tr>
<?php endforeach; ?>
      </tbody><tfoot><tr><td colspan="4"><?= html(uiText('ops.installed')) ?> <strong><?= html($versionLabel) ?></strong> · <a href="<?= html(uiUrl('/logs/aranami/')) ?>"><?= html(uiText('ops.browse_logs')) ?></a></td></tr></tfoot></table></div>
    </details>
  </main>
  <footer><div class="shell footer-inner"><p><?= html(uiText('footer.title')) ?></p><p><a href="<?= html(uiUrl('/logs/')) ?>"><?= html(uiText('nav.logs')) ?></a> · <a href="https://zh.wikipedia.org/wiki/WikiProject:电子游戏"><?= html(uiText('nav.project')) ?> ↗</a></p></div></footer>
</body>
</html>
