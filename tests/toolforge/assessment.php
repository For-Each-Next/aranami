<?php
/** Verify stable maintenance sorting and unknown-date placement. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/src/toolforge/assessment.php';

$items = [
    ['page_id' => 4, 'title' => 'D', 'oldest_issue_month' => null],
    ['page_id' => 1, 'title' => 'B', 'oldest_issue_month' => '2020-01'],
    ['page_id' => 3, 'title' => 'A', 'oldest_issue_month' => '2018-05'],
    ['page_id' => 2, 'title' => 'A', 'oldest_issue_month' => '2018-05'],
    ['page_id' => 5, 'title' => 'AA'],
];
foreach ([
    ['oldest', 'asc', [2, 3, 1, 5, 4]],
    ['oldest', 'desc', [1, 2, 3, 5, 4]],
    ['title', 'asc', [2, 3, 5, 1, 4]],
    ['title', 'desc', [4, 1, 5, 2, 3]],
] as [$sort, $order, $expected]) {
    $actual = array_column(dashboardMaintenanceItems($items, $sort, $order), 'page_id');
    if ($actual !== $expected) {
        throw new RuntimeException('Unexpected maintenance order: ' . $sort . ' ' . $order);
    }
}
if (array_column($items, 'page_id') !== [4, 1, 3, 2, 5]
    || dashboardMaintenanceItems([]) !== []) {
    throw new RuntimeException('Sorting must preserve the saved input and accept empty lists.');
}
echo "Toolforge maintenance sorting checks passed.\n";
