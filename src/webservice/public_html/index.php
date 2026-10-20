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
header("Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
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

/** Show an appearance kind using the same chip style as assessments. */
function dashboardEntryKind(array $item): void
{
    $kind = $item['kind'] ?? '';
    if (in_array($kind, ['fa', 'fl', 'ga'], true)) {
        $label = dashboardAssessmentLabel(strtoupper($kind), 'class');
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
        echo '<span class="entry-date">';
        if (($item['date_kind'] ?? '') === 'scheduled') {
            echo html(uiText('date.scheduled')) . (uiLocale() === 'en' ? ' ' : '');
        }
        echo '<time datetime="' . html($item['date']) . '">' . html($item['date']) . '</time></span>';
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
    echo '<ul class="card-grid entry-list entry-compact' . ($numbered ? ' entry-numbered' : '') . '">';
    foreach ($items as $index => $item) {
        echo '<li class="article-card">';
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


/** Link measured views to the exact day, project, access, and human-agent dataset. */
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
    echo '<ol class="card-grid pageviews-list">';
    foreach ($observation['items'] as $item) {
        $url = 'https://pageviews.wmcloud.org/?' . http_build_query([
            'project' => 'zh.wikipedia.org', 'platform' => 'all-access', 'agent' => 'user',
            'start' => $observation['date'], 'end' => $observation['date'],
            'pages' => $item['title'],
        ], '', '&', PHP_QUERY_RFC3986);
        echo '<li class="article-card"><span class="entry-rank">' . html((string) $item['rank']) . '.</span> '
            . '<span class="article-summary"><a href="' . html($item['url']) . '"><bdi lang="zh">' . html($item['title']) . '</bdi></a> ';
        dashboardAssessmentChips($item);
        echo '</span><p class="card-meta"><a class="pageviews-number" href="' . html($url) . '">'
            . html(uiText('pageviews.views', ['count' => dashboardNumber($item['views'])])) . '</a></p>';
        if (isset($item['site_rank'])) {
            echo '<p class="card-meta site-rank">' . html(uiText('pageviews.site_rank', ['rank' => dashboardNumber($item['site_rank'])])) . '</p>';
        }
        echo '<time class="card-meta" datetime="' . html($observation['date']) . '">' . html($observation['date']) . '</time></li>';
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
    .brand { display: inline-flex; align-items: center; gap: .7rem; margin: 18px 0; font-weight: 700; }
    .brand img { width: 36px; height: 36px; }
    .brand small { display: block; font-weight: 400; }
    h1, h2 { font-family: Georgia, "Noto Serif TC", "Songti SC", serif; font-weight: 400; line-height: 1.4; }
    h1 { margin: 0 0 24px; font-size: 2rem; }
    h2 { margin: 0 0 16px; padding-bottom: .3rem; border-bottom: 1px solid #54595d; font-size: 1.8rem; }
    h3 { margin: 0 0 8px; font-size: 1.25rem; }
    main { padding-top: 24px; padding-bottom: 24px; }
    .section-flat { margin-bottom: 32px; min-width: 0; }
    .report-section, .backlog-section { margin-bottom: 24px; min-width: 0; }
    .report-description, .source-updated, .card-meta, .entry-meta, .entry-rank { color: #54595d; font-size: .875rem; }
    .report-description { margin: 0 0 12px; }
    .source-updated { margin: 10px 0; }
    .card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(18em, 100%), 1fr)); gap: 16px; margin: 0; padding: 0; list-style: none; }
    .article-card, .backlog-task, .routine-card { min-width: 0; padding: 16px; border: 1px solid #c1d1cc; border-radius: 8px; background: #fff; overflow-wrap: anywhere; }
    .article-summary { display: inline; min-width: 0; }
    .article-summary > a { overflow-wrap: anywhere; }
    .assessment-chips { display: inline-flex; flex-wrap: wrap; gap: 4px; max-width: 100%; vertical-align: baseline; }
    <?php dashboardChipStyles(); ?>
    .assessment-chip { flex-shrink: 0; }
    .entry-meta { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: 8px; }
    .entry-date { white-space: nowrap; }
    .card-meta { display: block; margin: 8px 0 0; }
    .date-note-marker { text-decoration: none; cursor: help; }
    .more { margin-top: 12px; }
    .more summary { cursor: pointer; color: #3366cc; padding-bottom: 8px; }
    .backlog-task { display: flex; flex-direction: column; gap: 8px; }
    .backlog-count { color: #54595d; font-size: .875rem; }
    .routine-card h3 { font-size: 1.1rem; }
    .routine-card p { margin: 8px 0 0; }
    .notice { padding: 12px 16px; border: 1px solid #a8d9cb; border-radius: 8px; }
    .empty, .homepage-note, .ops-note { color: #54595d; }
    footer { border-top: 1px solid #c1d1cc; color: #54595d; font-size: .875rem; }
    .footer-inner { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 24px; padding-top: 12px; padding-bottom: 12px; }
    .footer-inner p { margin: 0; }
    .footer-uptime { flex-basis: 100%; }
    @media (max-width: 480px) { .shell { padding-left: 16px; padding-right: 16px; } h2 { font-size: 1.5rem; } }
  </style>
</head>
<body>
  <a class="skip" href="#main"><?= html(uiText('nav.skip')) ?></a>
  <header class="masthead"><div class="shell">
    <a class="brand" href="/"><img src="/aranami.svg" width="36" height="36" alt=""><span>Aranami<small><?= html(uiText('brand.context')) ?></small></span></a>
    <h1><?= html(uiText('page.heading')) ?></h1>
  </div></header>
  <main class="shell" id="main">
<?php if ($project === null): ?><p class="notice"><?= html(uiText('notice.preparing')) ?></p>
<?php elseif ($project['errors'] !== []): ?><p class="notice"><?= html(uiText('notice.partial')) ?></p><?php endif; ?>
    <section class="section-flat" id="homepage" aria-labelledby="homepage-heading">
      <h2 id="homepage-heading"><?= html(uiText('homepage.heading')) ?></h2>
<?php foreach ($sections as $key => [$title, $description, $items]): ?>
      <section class="report-section" aria-labelledby="homepage-<?= html($key) ?>">
        <h3 id="homepage-<?= html($key) ?>"><?= html($title) ?></h3>
        <p class="report-description"><?php if ($key === 'pageviews'): dashboardPageviewsDescription($pageviews); else: ?><?= html($description) ?><?php endif; ?></p>
<?php if ($key === 'pageviews'): dashboardPageviews($pageviews);
elseif ($key === 'dyk'): dashboardEntries($items === null ? null : array_slice($items, 0, 10), true, true);
else: dashboardEntries($items, $key === 'recognized', $key === 'recognized'); endif;
$sourceTime = $project['reports'][$key]['last_success_at'] ?? null;
if ($sourceTime !== null): ?>
        <p class="source-updated"><?= html(uiText('snapshot.updated')) ?> <time datetime="<?= html($sourceTime) ?>"><?= html((new DateTimeImmutable($sourceTime))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i')) ?> UTC</time></p>
<?php endif; ?>
      </section>
<?php endforeach; ?>
    </section>
    <section class="section-flat" id="backlog" aria-labelledby="backlog-heading">
      <h2 id="backlog-heading"><?= html(uiText('backlog.heading')) ?></h2>
<?php if ($backlog === null): ?><p class="empty"><?= html(uiText('backlog.unavailable')) ?></p>
<?php else: foreach (dashboardMaintenanceSections($backlog['groups']) as $section => $groups): ?>
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
?>
        <article class="routine-card"><h3><?php dashboardRoutineLinks($id, uiText('routine.' . $id), $routineDefinitions); ?></h3>
          <p><?= html(uiText('ops.column_state')) ?>: <span aria-hidden="true"><?= $stateEmoji ?></span> <?= html($stateLabel) ?></p>
          <p><?= html(uiText('ops.column_time')) ?>: <?php if ($event === null): ?>—<?php else: ?><time datetime="<?= html($event['timestamp']) ?>"><?= html((new DateTimeImmutable($event['timestamp']))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i')) ?> UTC</time><?php endif; ?></p>
          <p><?= html(uiText('ops.column_duration')) ?>: <?= html($event === null ? '—' : ($event['status'] === 'Running' ? uiRunningDuration($event['timestamp']) : uiDuration($event['duration']))) ?></p>
          <p><?= html(uiText('ops.next_run')) ?>: <?= html(dashboardNextRun($schedule, $id)) ?></p>
<?php $pageUpdates = array_values(array_filter($reportUpdates, static fn (array $record): bool => $record['routine'] === $id));
    usort($pageUpdates, static fn (array $left, array $right): int => strcmp($left['title'], $right['title']));
    if ($pageUpdates !== []): ?>
          <details class="page-updates"><summary><?= html(uiText('ops.pages', ['count' => (string) count($pageUpdates)])) ?></summary><ul>
<?php foreach ($pageUpdates as $record):
    $reportState = ['running' => 'Running', 'updated' => 'Updated', 'no_update' => 'NoUpdate', 'failed' => 'Failed', 'deferred' => 'Deferred', 'unconfirmed' => 'Result unavailable'][$record['status']]; ?>
            <li><p><?= html($record['title']) ?> · <?= html($record['site']) ?></p>
              <p><?= html(uiText('state.' . $reportState)) ?></p>
<?php foreach (['started_at' => 'ops.started', 'finished_at' => 'ops.finished'] as $field => $label): ?>
              <p><?= html(uiText($label)) ?>: <?php if ($record[$field] === null): ?>—<?php else: ?><time datetime="<?= html($record[$field]) ?>"><?= html((new DateTimeImmutable($record[$field]))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s')) ?> UTC</time><?php endif; ?></p>
<?php endforeach; ?>
              <p><?= html(uiText('ops.column_duration')) ?>: <?= html($record['status'] === 'running' ? uiRunningDuration($record['started_at']) : uiDuration(number_format($record['duration_seconds'], 3, '.', '') . ' sec')) ?></p>
            </li>
<?php endforeach; ?>
          </ul></details>
<?php endif; ?>
        </article>
<?php endforeach; ?>
      </div>
    </section>
  </main>
  <footer><div class="shell footer-inner"><p><?= html(uiText('footer.title')) ?> · <?= html($versionLabel) ?></p><p><a href="/about/"><?= html(uiText('nav.about')) ?></a> · <a href="/logs/"><?= html(uiText('nav.logs')) ?></a> · <a href="https://zh.wikipedia.org/wiki/WikiProject:电子游戏"><?= html(uiText('nav.project')) ?> ↗</a></p><p class="footer-uptime"><?= html(dashboardUptime($schedule)) ?></p></div></footer>
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
