<?php
/** Present saved Chinese reports and live local routine scheduler snapshots. */
declare(strict_types=1);

require dirname(__DIR__) . '/i18n.php';
require dirname(__DIR__) . '/status.php';
require dirname(__DIR__) . '/page-cache.php';

if (extension_loaded('zlib')) {
    @ini_set('zlib.output_compression', '1');
}
header('Cache-Control: no-store');
header('Vary: Accept-Language, Accept-Encoding');
header('Content-Language: ' . uiLanguageTag());
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; img-src 'self'; script-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
$method = $_SERVER['REQUEST_METHOD'] ?? '';
if (!in_array($method, ['GET', 'HEAD'], true)) {
    http_response_code(405);
    header('Allow: GET, HEAD');
    header('Content-Type: text/plain; charset=UTF-8');
    echo uiText('http.method_not_allowed') . "\n";
    exit;
}
header('Content-Type: text/html; charset=UTF-8');
$snapshotPath = '/data/project/aranami/cache/toolforge/dashboard.json';
$pageCacheDirectory = '/data/project/aranami/cache/toolforge/pages';
$pageCacheInputs = [$snapshotPath, __FILE__, dirname(__DIR__) . '/page-cache.php',
    dirname(__DIR__) . '/i18n.php', dirname(__DIR__) . '/status.php',
    dirname(__DIR__) . '/project.php', dirname(__DIR__) . '/assessment.php',
    dirname(__DIR__, 2) . '/shared/version.json', dirname(__DIR__, 2) . '/shared/routines.json'];
$pageCacheOptionalInputs = ['/data/project/aranami/state/routine-status.json',
    '/data/project/aranami/state/schedule-status.json',
    '/data/project/aranami/state/report-updates.json'];
$pageCacheKey = homepageCacheRequest($method, $_GET, $_SERVER['QUERY_STRING'] ?? '')
    ? homepageCacheKey($pageCacheInputs, $pageCacheOptionalInputs) : null;
if ($pageCacheKey !== null) {
    header('X-Aranami-Cache: MISS');
    $cachedPage = readHomepageCache($pageCacheDirectory, uiLocale(), $pageCacheKey);
    if ($cachedPage !== null) {
        header('X-Aranami-Cache: HIT');
        echo $cachedPage;
        exit;
    }
}
require dirname(__DIR__) . '/project.php';
require dirname(__DIR__) . '/assessment.php';

/** Format observed counts without treating unavailable data as zero. */
function dashboardNumber(int $number): string
{
    return number_format($number, 0, '.', ',');
}

/** Render localized appearance dates, display kinds, and observation markers. */
function dashboardEntryMeta(array $item): void
{
    echo '<p class="card-meta">';
    if (($item['date'] ?? null) !== null) {
        if (($item['date_kind'] ?? '') === 'scheduled') {
            echo html(uiLocale() === 'en' ? '(' . uiText('date.scheduled') . ') ' : '（' . uiText('date.scheduled') . '）');
        }
        $date = new DateTimeImmutable($item['date']);
        echo '<time datetime="' . html($item['date']) . '">' . html($date->format(uiLocale() === 'en' ? 'F j, Y' : 'Y年n月j日')) . '</time>';
    }
    $kindKey = ['fa' => 'appearance.featured', 'fl' => 'appearance.featured', 'featured' => 'appearance.featured',
        'ga' => 'appearance.good', 'news' => 'homepage.news', 'history' => 'homepage.history'][$item['kind'] ?? ''] ?? null;
    if ($kindKey !== null) {
        echo ($item['date'] !== null && (uiLocale() === 'en' || in_array($item['kind'], ['news', 'history'], true)) ? ' · ' : '') . html(uiText($kindKey));
    }
    $noteKey = ['window_start' => 'date.window_start', 'observed' => 'date.observed'][$item['date_kind'] ?? ''] ?? null;
    if ($noteKey !== null) {
        $note = uiText($noteKey);
        echo '<abbr class="date-note-marker" title="' . html($note) . '" aria-label="' . html($note) . '">†</abbr>';
    }
    echo '</p>';
}

/** Display only saved plain text, with no preview requests during rendering. */
function dashboardArticleExtract(array $item): void
{
    $variant = uiLocale() === 'en' ? 'zh-hans' : uiLocale();
    $field = match ($variant) {
        'zh' => 'extract_zh',
        'zh-hant' => array_key_exists('extract_hant', $item) ? 'extract_hant' : 'extract',
        default => 'extract',
    };
    if (($item[$field] ?? null) !== null) {
        echo '<p class="article-extract"><bdi lang="' . html(uiLanguageTag($variant)) . '">' . html($item[$field]) . '</bdi></p>';
    }
}

/** Link the introduction license within the localized source attribution. */
function dashboardExtractAttribution(): void
{
    [$before, $after] = explode('{license}', uiText('extracts.attribution'), 2);
    echo html($before) . '<a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>' . html($after);
}

/** Render numbered article cards with assessments and a second metadata line. */
function dashboardEntryRows(array $items): void
{
    echo '<ol class="card-grid article-list">';
    foreach ($items as $item) {
        echo '<li class="article-card"><div class="article-card-body">';
        echo '<span class="article-summary"><a href="' . html($item['url']) . '" title="' . html($item['title']) . '"><bdi lang="zh">' . html($item['title']) . '</bdi></a> ';
        dashboardAssessmentChips($item);
        echo '</span>';
        dashboardArticleExtract($item);
        dashboardEntryMeta($item);
        echo '</div></li>';
    }
    echo '</ol>';
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

/** Show every supplied article while preserving unavailable states. */
function dashboardEntries(?array $items): void
{
    if ($items === null) {
        echo '<p class="empty">' . html(uiText('empty.section')) . '</p>';
    } elseif ($items === []) {
        echo '<p class="empty">' . html(uiText('empty.period')) . '</p>';
    } else {
        dashboardEntryRows($items);
    }
}

/** Render the observed daily project matches without implying full-site coverage. */
function dashboardPageviewsDescription(?array $observation): void
{
    [$dateBefore, $dateAfter] = explode('{date}', uiText('pageviews.description'), 2);
    echo html($dateBefore);
    if ($observation !== null) {
        $date = new DateTimeImmutable($observation['date']);
        $label = $date->format(uiLocale() === 'en' ? 'F j, Y' : 'Y年n月j日');
        echo '<time datetime="' . html($observation['date']) . '">' . html($label) . '</time>' . (uiLocale() === 'en' ? ' · ' : '');
    }
    [$before, $after] = explode('{top}', $dateAfter, 2);
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
}


/** Link daily counts to a thirty-day trend ending on the actual source date. */
function dashboardPageviews(?array $observation): void
{
    if ($observation === null) {
        echo '<p class="empty">' . html(uiText('pageviews.unavailable')) . '</p>';
        return;
    }
    if ($observation['items'] === []) {
        echo '<p class="empty">' . html(uiText('pageviews.empty')) . '</p>';
        return;
    }
    $trendStart = (new DateTimeImmutable($observation['date']))->modify('-29 days')->format('Y-m-d');
    echo '<ol class="card-grid article-list">';
    foreach ($observation['items'] as $item) {
        $url = 'https://pageviews.wmcloud.org/?' . http_build_query([
            'project' => 'zh.wikipedia.org', 'platform' => 'all-access', 'agent' => 'user',
            'start' => $trendStart, 'end' => $observation['date'],
            'pages' => $item['title'],
        ], '', '&', PHP_QUERY_RFC3986);
        echo '<li class="article-card" value="' . html((string) $item['rank']) . '"><div class="article-card-body"><span class="article-summary"><a href="' . html($item['url']) . '"><bdi lang="zh">' . html($item['title']) . '</bdi></a> ';
        dashboardAssessmentChips($item);
        echo '</span>';
        dashboardArticleExtract($item);
        echo '<p class="card-meta"><a class="pageviews-number" href="' . html($url) . '">'
            . html(uiText('pageviews.views', ['count' => dashboardNumber($item['views'])])) . '</a>';
        if (isset($item['site_rank'])) {
            echo ' · <span class="site-rank">' . html(uiText('pageviews.site_rank', ['rank' => dashboardNumber($item['site_rank'])])) . '</span>';
        }
        echo '</p></div></li>';
    }
    echo '</ol>';
}

$project = projectDashboard($snapshotPath);
$homepage = $project['homepage'] ?? null;
$pageviews = $project['pageviews'] ?? null;
$backlog = $project['backlog'] ?? null;
$recognized = dashboardMergedItems($homepage, ['featured' => uiText('section.featured'), 'good' => uiText('section.good')]);
$news = dashboardMergedItems($homepage, ['news' => uiText('homepage.news'), 'history' => uiText('homepage.history')]);
$upcoming = $homepage['upcoming'] ?? null;
if ($upcoming !== null) {
    usort($upcoming, static fn (array $left, array $right): int =>
        strcmp($left['date'], $right['date']) ?: strcmp($left['title'], $right['title']) ?: strcmp($left['kind'], $right['kind']));
}
$recognizedItems = $recognized['items'] === null && $upcoming === null
    ? null : array_merge($upcoming ?? [], $recognized['items'] ?? []);
$sections = [
    'pageviews' => [uiText('pageviews.heading'), '', null],
    'recognized' => [uiText('homepage.recognized'), uiText('homepage.recognized_description'), $recognizedItems],
    'dyk' => [uiText('homepage.dyk'), uiText('homepage.dyk_description'), $homepage['dyk'] ?? null],
    'news_history' => [uiText('homepage.news_history'), uiText('homepage.news_history_description'), $news['items']],
];
foreach (array_keys($sections) as $key) {
    $empty = match ($key) {
        'pageviews' => $pageviews !== null && $pageviews['items'] === [],
        'recognized' => $recognizedItems === [] && $recognized['missing'] === [] && $upcoming !== null,
        'news_history' => $news['items'] === [] && $news['missing'] === [],
        default => $sections[$key][2] === [],
    };
    if ($empty) {
        unset($sections[$key]);
    }
}
$activity = dashboardActivity('/data/project/aranami/state');
$reportUpdates = dashboardReportUpdates('/data/project/aranami/state');
$routineDefinitions = dashboardRoutines();
$schedule = dashboardSchedule('/data/project/aranami/state', null, $routineDefinitions);
$versionLabel = sourceVersion() ?? uiText('ops.unavailable');
if ($method === 'HEAD') {
    exit;
}
if ($pageCacheKey !== null && $project !== null) {
    ob_start();
} else {
    $pageCacheKey = null;
}
?>
<!doctype html>
<html lang="<?= html(uiLanguageTag()) ?>">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#edf9f4">
  <meta name="description" content="<?= html(uiText('page.description')) ?>">
  <link rel="icon" href="/aranami.svg" type="image/svg+xml">
  <title><?= html(uiText('page.title')) ?></title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, "Noto Sans TC", sans-serif; color: #202122; background: #edf9f4; line-height: 1.6; }
    * { box-sizing: border-box; }
    body { margin: 0; }
    a { color: #3366cc; text-decoration: none; overflow-wrap: anywhere; }
    a:hover { text-decoration: underline; }
    a:focus-visible, summary:focus-visible { outline: 3px solid #3366cc; outline-offset: 4px; }
    .shell { width: 100%; max-width: 1140px; margin: 0 auto; padding: 0 24px; }
    .skip { position: absolute; top: -5rem; left: 1rem; background: white; padding: .6rem; }
    .skip:focus { top: .5rem; }
    .masthead { border-top: 4px solid #76c2af; border-bottom: 1px solid #c1d1cc; }
    .masthead .shell { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 24px; padding-top: 18px; padding-bottom: 18px; }
    .brand { display: inline-flex; align-items: center; gap: .7rem; font-weight: 700; }
    .brand img { width: 36px; height: 36px; }
    h1, h2 { font-family: Georgia, "Noto Serif TC", "Songti SC", serif; font-weight: 400; line-height: 1.4; }
    h1 { margin: 0; font-size: 2rem; }
    h2 { margin: 0 0 16px; padding-bottom: .3rem; border-bottom: 1px solid #54595d; font-size: 1.8rem; }
    h3 { margin: 0 0 8px; font-size: 1.25rem; }
    main { padding-top: 24px; padding-bottom: 24px; }
    .section-flat { margin-bottom: 32px; min-width: 0; }
    .report-section, .backlog-section { margin-bottom: 24px; min-width: 0; }
    .report-description, .source-updated { color: #54595d; font-size: .875rem; }
    .report-description { margin: 0 0 12px; }
    .source-updated { margin: 10px 0; }
    <?php dashboardCardStyles(); ?>
    <?php dashboardChipStyles(); ?>
    .article-card::marker { content: ""; }
    .article-card-body { display: grid; grid-template-columns: 1.75rem minmax(0, 1fr); grid-template-rows: auto 1fr auto; height: 100%; }
    .article-summary { grid-column: 1 / -1; grid-row: 1; position: relative; padding-inline-start: 1.75rem; }
    .article-summary::before { content: counter(list-item) "."; position: absolute; inset-inline-start: 0; }
    .article-extract { grid-column: 1 / -1; grid-row: 2; align-self: start; margin: 10px 0 0; font-size: .875rem; color: #54595d; }
    .article-card-body .card-meta { grid-column: 2; grid-row: 3; margin-top: auto; padding-top: 12px; font-size: .75rem; }
    .date-note-marker { text-decoration: none; cursor: help; }
    .backlog-task { display: flex; flex-direction: column; gap: 8px; }
    .backlog-count { color: #54595d; font-size: .875rem; }
    .routine-card h3 { font-size: 1.1rem; }
    .routine-card p { margin: 8px 0 0; }
    .routine-meta { margin: 0; padding-inline-start: 1.25rem; }
    .routine-meta > li { margin-top: 8px; }
    .status-link { border: 0; padding: 0; background: none; font: inherit; color: #3366cc; text-decoration: underline; text-underline-offset: .15em; cursor: pointer; }
    .page-updates { box-sizing: border-box; width: min(68rem, calc(100% - 32px)); max-height: calc(100% - 32px); padding: 20px; border: 1px solid #c1d1cc; border-radius: 8px; color: #2d4b42; }
    .page-updates::backdrop { background: rgb(0 0 0 / .4); }
    .dialog-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 16px; margin-bottom: 16px; }
    .dialog-heading h3 { margin: 0; }
    .dialog-heading button { font: inherit; cursor: pointer; }
    .table-scroll { overflow-x: auto; }
    .page-updates table { width: 100%; min-width: 50rem; border-collapse: collapse; font-size: .875rem; }
    .page-updates th, .page-updates td { padding: 8px; border-bottom: 1px solid #c1d1cc; text-align: left; vertical-align: top; }
    .page-updates th:first-child { min-width: 12em; }
    .page-updates th[scope="row"] { font-weight: 400; }
    .page-updates th:not(:first-child), .page-updates td { white-space: nowrap; }
    .page-updates time { white-space: nowrap; }
    .notice { padding: 12px 16px; border: 1px solid #a8d9cb; border-radius: 8px; }
    .empty, .homepage-note { color: #54595d; }
    footer { border-top: 1px solid #c1d1cc; color: #54595d; font-size: .875rem; }
    .footer-inner { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 24px; padding-top: 12px; padding-bottom: 12px; }
    .footer-inner p { margin: 0; }
    @media (max-width: 480px) { .shell { padding-left: 16px; padding-right: 16px; } h2 { font-size: 1.5rem; } }
  </style>
</head>
<body>
  <a class="skip" href="#main"><?= html(uiText('nav.skip')) ?></a>
  <header class="masthead"><div class="shell">
    <div class="brand"><img src="/aranami.svg" width="36" height="36" alt=""><span>Aranami</span></div>
    <h1><?= html(uiText('page.heading')) ?></h1>
  </div></header>
  <main class="shell" id="main">
<?php if ($project === null): ?><p class="notice"><?= html(uiText('notice.preparing')) ?></p>
<?php elseif ($project['errors'] !== []): ?><p class="notice"><?= html(uiText('notice.partial')) ?></p><?php endif; ?>
<?php if ($sections !== []): ?>
    <section class="section-flat" id="homepage" aria-labelledby="homepage-heading">
      <h2 id="homepage-heading"><?= html(uiText('homepage.heading')) ?></h2>
<?php foreach ($sections as $key => [$title, $description, $items]): ?>
      <section class="report-section" aria-labelledby="homepage-<?= html($key) ?>">
        <h3 id="homepage-<?= html($key) ?>"><?= html($title) ?></h3>
        <p class="report-description"><?php if ($key === 'pageviews'): dashboardPageviewsDescription($pageviews); else: ?><?= html($description) ?><?php endif; ?></p>
<?php if ($key === 'pageviews'): dashboardPageviews($pageviews);
elseif ($key === 'dyk'): dashboardEntries($items === null ? null : array_slice($items, 0, 12));
else: dashboardEntries($items); endif;
$sourceTime = $project['reports'][$key]['last_success_at'] ?? null;
if ($sourceTime !== null): ?>
        <p class="source-updated"><?= html(uiText('snapshot.updated')) ?> <time datetime="<?= html($sourceTime) ?>"><?= html((new DateTimeImmutable($sourceTime))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i')) ?> UTC</time><span class="extract-attribution"> · <?php dashboardExtractAttribution(); ?></span></p>
<?php endif; ?>
      </section>
<?php endforeach; ?>
    </section>
<?php endif; ?>
<?php if ($backlog === null || $backlog['total'] > 0): ?>
    <section class="section-flat" id="backlog" aria-labelledby="backlog-heading">
      <h2 id="backlog-heading"><?= html(uiText('backlog.heading')) ?></h2>
<?php if ($backlog === null): ?><p class="empty"><?= html(uiText('backlog.unavailable')) ?></p>
<?php else: foreach (dashboardMaintenanceSections(array_filter($backlog['groups'], static fn (array $group): bool => $group['count'] > 0)) as $section => $groups): ?>
      <section class="backlog-section" aria-labelledby="backlog-section-<?= html($section) ?>">
        <h3 id="backlog-section-<?= html($section) ?>"><?= html(dashboardMaintenanceSectionLabel($groups)) ?></h3>
        <div class="card-grid">
<?php foreach ($groups as $group): ?>
          <a class="backlog-task" href="<?= html(uiUrl('/maintenance/', ['task' => $group['key'], 'page' => 1])) ?>"><span><?= html(dashboardMaintenanceTaskLabel($group)) ?></span><span class="backlog-count"><?= html(uiText('backlog.count', ['count' => dashboardNumber($group['count'])])) ?></span></a>
<?php endforeach; ?>
        </div>
      </section>
<?php endforeach; ?>
      <p class="homepage-note"><?= html(uiText('backlog.note')) ?></p>
      <p class="source-updated"><?= html(uiText('snapshot.updated')) ?> <time datetime="<?= html($backlog['observed_at']) ?>"><?= html((new DateTimeImmutable($backlog['observed_at']))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i')) ?> UTC</time></p>
<?php endif; ?>
    </section>
<?php endif; ?>
    <section class="section-flat" id="tool-status" aria-labelledby="tool-status-heading">
      <h2 id="tool-status-heading"><?= html(uiText('ops.heading')) ?></h2>
<?php if ($routineDefinitions === []): ?><p class="empty"><?= html(uiText('ops.schedule_missing')) ?></p><?php endif; ?>
      <div class="card-grid">
<?php foreach ($routineDefinitions as $id => $definition):
    $event = $activity['latest'][$id] ?? null;
    $stateLabel = uiText($event === null ? 'ops.no_record' : 'state.' . $event['status']);
    $stateEmoji = match ($event['status'] ?? null) {
        'Updated', 'NoUpdate' => '✅', 'Failed' => '❌', 'Running' => '⏳', 'Deferred' => '⏸️', default => '❔',
    };
    $duration = $event === null ? '—' : ($event['status'] === 'Running' ? uiRunningDuration($event['timestamp']) : uiDuration($event['duration']));
    $pageUpdates = array_values(array_filter($reportUpdates, static fn (array $record): bool => $record['routine'] === $id));
    usort($pageUpdates, static fn (array $left, array $right): int => strcmp($left['title'], $right['title']));
?>
        <article class="routine-card"><h3><?php dashboardRoutineLinks($id, uiText('routine.' . $id), $routineDefinitions); ?></h3>
          <ul class="routine-meta">
            <li>
              <span aria-hidden="true"><?= $stateEmoji ?></span>
<?php if ($pageUpdates !== []): ?>
              <button class="status-link" type="button" data-dialog="updates-<?= html($id) ?>" aria-haspopup="dialog" aria-controls="updates-<?= html($id) ?>"><?= html($stateLabel) ?></button>
<?php else: ?><?= html($stateLabel) ?><?php endif; ?> <?= html(uiText('ops.state_duration', ['duration' => $duration])) ?>
            </li>
            <li><?= html(uiText('ops.column_time')) ?>: <?php if ($event === null): ?>—<?php else: ?><time datetime="<?= html($event['timestamp']) ?>"><?= html((new DateTimeImmutable($event['timestamp']))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i')) ?> UTC</time><?php endif; ?></li>
            <li><?= html(uiText('ops.next_run')) ?>: <?= html(dashboardNextRun($schedule, $id)) ?></li>
          </ul>
<?php if ($pageUpdates !== []): ?>
          <dialog class="page-updates" id="updates-<?= html($id) ?>" aria-labelledby="updates-heading-<?= html($id) ?>">
            <div class="dialog-heading"><h3 id="updates-heading-<?= html($id) ?>"><?= html(uiText('ops.details', ['routine' => uiText('routine.' . $id)])) ?></h3><form method="dialog"><button autofocus><?= html(uiText('dialog.close')) ?></button></form></div>
            <div class="table-scroll" tabindex="0" role="region" aria-labelledby="updates-heading-<?= html($id) ?>">
            <table><thead><tr><th scope="col"><?= html(uiText('column.article')) ?></th><th scope="col"><?= html(uiText('ops.column_status')) ?></th><th scope="col"><?= html(uiText('ops.started')) ?></th><th scope="col"><?= html(uiText('ops.finished')) ?></th><th scope="col"><?= html(uiText('ops.column_duration')) ?></th></tr></thead><tbody>
<?php foreach ($pageUpdates as $record):
    $reportState = ['running' => 'Running', 'updated' => 'Updated', 'no_update' => 'NoUpdate', 'failed' => 'Failed', 'deferred' => 'Deferred', 'unconfirmed' => 'Result unavailable'][$record['status']];
    $reportHost = ['zhwiki' => 'zh.wikipedia.org', 'enwiki' => 'en.wikipedia.org'][$record['site']] ?? null;
    $reportUrl = $reportHost === null ? null : 'https://' . $reportHost . '/wiki/' . rawurlencode(str_replace(' ', '_', $record['title'])); ?>
              <tr><th scope="row"><?php if ($reportUrl !== null): ?><a href="<?= html($reportUrl) ?>"><?php endif; ?><?= html($record['title']) ?><?php if ($reportUrl !== null): ?></a><?php endif; ?></th>
              <td><?= html(uiText('state.' . $reportState)) ?></td>
<?php foreach (['started_at' => 'ops.started', 'finished_at' => 'ops.finished'] as $field => $label): ?>
              <td><?php if ($record[$field] === null): ?>—<?php else: ?><time datetime="<?= html($record[$field]) ?>"><?= html((new DateTimeImmutable($record[$field]))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s')) ?> UTC</time><?php endif; ?></td>
<?php endforeach; ?>
              <td><?= html($record['status'] === 'running' ? uiRunningDuration($record['started_at']) : uiDuration(number_format($record['duration_seconds'], 3, '.', '') . ' sec')) ?></td></tr>
<?php endforeach; ?>
            </tbody></table></div>
          </dialog>
<?php endif; ?>
        </article>
<?php endforeach; ?>
      </div>
    </section>
  </main>
  <footer><div class="shell footer-inner"><p class="footer-uptime"><?= html(dashboardUptime($schedule, null, $versionLabel)) ?></p><p><a href="/about/"><?= html(uiText('nav.about')) ?></a> · <a href="/logs/"><?= html(uiText('nav.logs')) ?></a> · <a href="https://zh.wikipedia.org/wiki/WikiProject:电子游戏"><?= html(uiText('nav.project')) ?> ↗</a></p></div></footer>
  <script src="/dialogs.js" defer></script>
</body>
</html>
<?php
if ($pageCacheKey !== null) {
    $renderedPage = ob_get_clean();
    if ($renderedPage !== false) {
        $responseCode = http_response_code();
        if (($responseCode === false || $responseCode === 200)
            && homepageCacheKey($pageCacheInputs, $pageCacheOptionalInputs) === $pageCacheKey) {
            writeHomepageCache($pageCacheDirectory, uiLocale(), $pageCacheKey, $renderedPage);
        }
        echo $renderedPage;
    }
}
