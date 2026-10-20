<?php
/** Browse saved Chinese project maintenance details in bounded pages. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/i18n.php';

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

$task = $_GET['task'] ?? null;
$requestedPage = $_GET['page'] ?? '1';
$sort = $_GET['sort'] ?? 'oldest';
$order = $_GET['order'] ?? (in_array($sort, ['class', 'importance'], true) ? 'desc' : 'asc');
if (!is_string($task) || preg_match('/\A[a-z][a-z0-9_]*\z/', $task) !== 1
    || !is_string($requestedPage) || preg_match('/\A[1-9][0-9]*\z/', $requestedPage) !== 1
    || !is_string($sort) || !in_array($sort, ['oldest', 'title', 'class', 'importance'], true)
    || !is_string($order) || !in_array($order, ['asc', 'desc'], true)) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=UTF-8');
    if ($method !== 'HEAD') {
        echo uiText('backlog.not_found') . "\n";
    }
    exit;
}

require dirname(__DIR__, 2) . '/status.php';
require dirname(__DIR__, 2) . '/project.php';
require dirname(__DIR__, 2) . '/assessment.php';

$snapshot = projectDashboard('/data/project/aranami/cache/toolforge/dashboard.json');
$backlog = $snapshot['backlog'] ?? null;
if ($backlog === null) {
    http_response_code(503);
    header('Content-Type: text/plain; charset=UTF-8');
    if ($method !== 'HEAD') {
        echo uiText('backlog.unavailable') . "\n";
    }
    exit;
}

$selected = null;
$groupsByKey = [];
foreach ($backlog['groups'] as $group) {
    $groupsByKey[$group['key']] = $group;
    if ($group['key'] === $task) {
        $selected = $group;
    }
}
if ($selected === null) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=UTF-8');
    if ($method !== 'HEAD') {
        echo uiText('backlog.not_found') . "\n";
    }
    exit;
}

$perPage = 50;
$pages = max(1, intdiv($selected['count'] + $perPage - 1, $perPage));
// Compare lengths before casting so even enormous positive page values clamp.
$page = strlen($requestedPage) > strlen((string) $pages) ? $pages : min((int) $requestedPage, $pages);
$offset = ($page - 1) * $perPage;
$orderedItems = dashboardMaintenanceItems($selected['items'], $sort, $order);
$items = array_slice($orderedItems, $offset, $perPage);
$sortParameters = $sort === 'oldest' && $order === 'asc' ? [] : ['sort' => $sort, 'order' => $order];
$titleSortUrl = uiUrl('/maintenance/', ['task' => $task, 'sort' => 'title', 'order' => $sort === 'title' && $order === 'asc' ? 'desc' : 'asc']);
$monthSortUrl = uiUrl('/maintenance/', ['task' => $task, 'sort' => 'oldest', 'order' => $sort === 'oldest' && $order === 'asc' ? 'desc' : 'asc']);
$classSortUrl = uiUrl('/maintenance/', ['task' => $task, 'sort' => 'class', 'order' => $sort === 'class' && $order === 'desc' ? 'asc' : 'desc']);
$importanceSortUrl = uiUrl('/maintenance/', ['task' => $task, 'sort' => 'importance', 'order' => $sort === 'importance' && $order === 'desc' ? 'asc' : 'desc']);
$heading = dashboardMaintenanceTaskLabel($selected);
$updated = (new DateTimeImmutable($backlog['observed_at']))->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i');
header('Content-Type: text/html; charset=UTF-8');
if ($method === 'HEAD') {
    exit;
}
?>
<!doctype html>
<html lang="<?= html(uiLanguageTag()) ?>">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#edf9f4">
  <link rel="icon" href="/aranami.svg" type="image/svg+xml">
  <title><?= html($heading . ' · ' . uiText('backlog.heading') . ' · Aranami') ?></title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, "Noto Sans TC", "Microsoft JhengHei", sans-serif; color: #202122; background: #edf9f4; line-height: 1.7; }
    * { box-sizing: border-box; }
    body { margin: 0; }
    a { color: #3366cc; text-decoration: none; overflow-wrap: anywhere; }
    a:hover { color: #2a4b8d; text-decoration: none; }
    a:focus-visible { outline: 3px solid #3366cc; outline-offset: 4px; }
    .shell { width: 100%; max-width: 1140px; margin: 0 auto; padding: 0 24px; }
    header { background: #edf9f4; border-top: 4px solid #76c2af; border-bottom: 1px solid #d9ebe4; }
    header .shell { padding-top: 20px; padding-bottom: 24px; }
    .back { display: inline-block; margin-bottom: 16px; font-size: .85rem; }
    .context { margin: 0 0 4px; color: #54595d; font-size: .85rem; }
    h1 { margin: 0; font-family: Georgia, "Noto Serif TC", "Noto Serif SC", "Songti SC", serif; font-size: clamp(1.7rem, 5vw, 2.2rem); font-weight: 400; line-height: 1.4; }
    .metadata { display: flex; flex-wrap: wrap; gap: 4px 24px; margin-top: 12px; font-size: .85rem; color: #54595d; }
    .metadata p { margin: 0; }
    main { padding-top: 24px; padding-bottom: 32px; }
    .maintenance-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(18em, 100%), 1fr)); gap: 16px; margin: 0; padding: 0; list-style: none; }
    .article-card { min-width: 0; padding: 16px; background: #fff; border: 1px solid #c1d1cc; border-radius: 8px; overflow-wrap: anywhere; }
    .article-card h2 { margin: 0 0 10px; font-size: 1.1rem; }
    .article-card p { margin: 8px 0; }
    .number { color: #54595d; font-size: .85rem; font-weight: 400; }
    .assessment-chips { display: inline-flex; flex-wrap: wrap; gap: 4px; }
    <?php dashboardChipStyles(); ?>
    .issues { display: flex; flex-wrap: wrap; gap: 3px 14px; margin: 0; padding: 0; list-style: none; }
    .unknown { color: #54595d; }
    .empty { margin: 0 0 24px; color: #54595d; }
    .pagination { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; margin-top: 24px; font-size: .9rem; }
    .pagination p { margin: 0; }
    .sort-controls { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-bottom: 20px; }
    footer { margin-top: 24px; padding-top: 16px; border-top: 1px solid #d9ebe4; font-size: .8rem; color: #54595d; }
    footer p { margin: 0; }
    footer p + p { margin-top: 8px; }
    @media (max-width: 640px) {
      .sort-controls { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-bottom: 20px; }
      .shell { padding-right: 16px; padding-left: 16px; }

    }
  </style>
</head>
<body>
  <header>
    <div class="shell">
      <a class="back" href="<?= html(uiUrl('/#backlog')) ?>"><?= html(uiText('backlog.back')) ?></a>
      <p class="context"><?= html(uiText('backlog.heading')) ?> · Aranami</p>
      <h1><?= html($heading) ?></h1>
      <div class="metadata">
        <p><?= html(uiText('backlog.count', ['count' => number_format($selected['count'], 0, '.', ',')])) ?></p>
        <p><?= html(uiText('snapshot.updated')) ?> <time datetime="<?= html($backlog['observed_at']) ?>"><?= html($updated) ?> UTC</time></p>
      </div>
    </div>
  </header>
  <main class="shell">
    <nav class="sort-controls" aria-label="<?= html(uiText('backlog.sort')) ?>"><a href="<?= html($titleSortUrl) ?>"><?= html(uiText('column.article')) ?><?= $sort === 'title' ? ($order === 'asc' ? ' ↑' : ' ↓') : '' ?></a><a href="<?= html($classSortUrl) ?>"><?= html(uiText('backlog.column.class')) ?><?= $sort === 'class' ? ($order === 'asc' ? ' ↑' : ' ↓') : '' ?></a><a href="<?= html($importanceSortUrl) ?>"><?= html(uiText('backlog.column.importance')) ?><?= $sort === 'importance' ? ($order === 'asc' ? ' ↑' : ' ↓') : '' ?></a><a href="<?= html($monthSortUrl) ?>"><?= html(uiText('backlog.column.oldest')) ?><?= $sort === 'oldest' ? ($order === 'asc' ? ' ↑' : ' ↓') : '' ?></a></nav>
    <?php if ($items === []): ?>
      <p class="empty"><?= html(uiText('backlog.empty')) ?></p>
    <?php else: ?>
      <ol class="maintenance-cards" start="<?= html((string) ($offset + 1)) ?>" aria-label="<?= html($heading) ?>">
<?php foreach ($items as $index => $item): ?>
        <li class="article-card">
          <h2><span class="number"><?= html((string) ($offset + $index + 1)) ?>.</span> <a href="<?= html($item['url']) ?>"><bdi lang="zh"><?= html($item['title']) ?></bdi></a></h2>
          <p><?= html(uiText('backlog.column.assessment')) ?>:
<?php if (($item['assessment_class'] ?? null) !== null || ($item['assessment_importance'] ?? null) !== null): dashboardAssessmentChips($item);
else: ?><span class="unknown">—</span><?php endif; ?>
          </p>
          <p><?= html(uiText('backlog.column.issues')) ?>:</p>
<?php if (($item['issues'] ?? []) === []): ?><span class="unknown">—</span>
<?php else: ?><ul class="issues">
<?php foreach ($item['issues'] as $issue): ?><li><a href="<?= html(uiUrl('/maintenance/', ['task' => $issue, 'page' => 1])) ?>"><?= html(dashboardMaintenanceTaskLabel($groupsByKey[$issue])) ?></a></li><?php endforeach; ?>
          </ul><?php endif; ?>
          <p><?= html(uiText('backlog.column.oldest')) ?>:
<?php if (($item['oldest_issue_month'] ?? null) !== null): ?><time datetime="<?= html($item['oldest_issue_month']) ?>"><?= html($item['oldest_issue_month']) ?></time>
<?php else: ?><span class="unknown">—</span><?php endif; ?>
          </p>
        </li>
<?php endforeach; ?>
      </ol>
    <?php endif; ?>
    <nav class="pagination" aria-label="<?= html(uiText('nav.backlog')) ?>">
      <?php if ($page > 1): ?>
        <a rel="prev" href="<?= html(uiUrl('/maintenance/', ['task' => $task, 'page' => $page - 1] + $sortParameters)) ?>"><?= html(uiText('backlog.previous')) ?></a>
      <?php endif; ?>
      <p><?= html(uiText('backlog.page', ['page' => $page, 'pages' => $pages])) ?></p>
      <?php if ($page < $pages): ?>
        <a rel="next" href="<?= html(uiUrl('/maintenance/', ['task' => $task, 'page' => $page + 1] + $sortParameters)) ?>"><?= html(uiText('backlog.next')) ?></a>
      <?php endif; ?>
    </nav>
    <footer>
      <p><?= html(uiText('backlog.oldest_note')) ?></p>
      <p><a href="<?= html($selected['source_url']) ?>"><?= html(uiText('backlog.source')) ?>: <bdi lang="zh"><?= html($selected['category']) ?></bdi></a></p>
    </footer>
  </main>
</body>
</html>
