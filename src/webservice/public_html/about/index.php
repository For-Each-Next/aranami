<?php
/** Explain Aranami's purpose to readers without requiring technical knowledge. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/i18n.php';
require dirname(__DIR__, 2) . '/status.php';

header('Cache-Control: no-store');
header('Vary: Accept-Language');
header('Content-Language: ' . uiLanguageTag());
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'");
$method = $_SERVER['REQUEST_METHOD'] ?? '';
if (!in_array($method, ['GET', 'HEAD'], true)) {
    http_response_code(405);
    header('Allow: GET, HEAD');
    header('Content-Type: text/plain; charset=UTF-8');
    echo uiText('http.method_not_allowed') . "\n";
    exit;
}
header('Content-Type: text/html; charset=UTF-8');
if ($method === 'HEAD') {
    exit;
}
?>
<!doctype html>
<html lang="<?= html(uiLanguageTag()) ?>">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="<?= html(uiText('about.intro')) ?>">
  <link rel="icon" href="/aranami.svg" type="image/svg+xml">
  <title><?= html(uiText('nav.about')) ?></title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; color: #202122; background: #edf9f4; line-height: 1.7; }
    * { box-sizing: border-box; }
    body { margin: 0; border-top: 4px solid #76c2af; }
    .shell { max-width: 50rem; margin: auto; padding: 1.5rem; }
    a { color: #3366cc; overflow-wrap: anywhere; }
    a:focus-visible { outline: 3px solid #3366cc; outline-offset: 4px; }
    h1, h2 { line-height: 1.4; font-weight: 500; }
    h1 { margin-top: 0; }
    section { margin-top: 2rem; }
    footer { border-top: 1px solid #c1d1cc; color: #54595d; }
    nav { display: flex; flex-wrap: wrap; gap: 1.2rem; }
  </style>
</head>
<body>
  <header class="shell"><nav aria-label="<?= html(uiText('nav.about')) ?>"><a href="/"><?= html(uiText('nav.dashboard')) ?></a><a href="https://zh.wikipedia.org/wiki/WikiProject:电子游戏"><?= html(uiText('nav.project')) ?> ↗</a></nav></header>
  <main class="shell">
    <h1><?= html(uiText('nav.about')) ?></h1>
    <p><?= html(uiText('about.intro')) ?></p>
<?php foreach (['discover', 'improve', 'reports'] as $section): ?>
    <section aria-labelledby="<?= $section ?>-heading">
      <h2 id="<?= $section ?>-heading"><?= html(uiText('about.' . $section . '_heading')) ?></h2>
      <p><?= html(uiText('about.' . $section)) ?></p>
    </section>
<?php endforeach; ?>
    <p><?= html(uiText('about.freshness')) ?></p>
  </main>
  <footer><div class="shell">Aranami · <?= html(sourceVersion() ?? uiText('ops.unavailable')) ?></div></footer>
</body>
</html>
