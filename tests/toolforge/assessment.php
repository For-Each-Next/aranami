<?php
/** Verify semantic assessment sorting, stable pagination, and missing values. */
declare(strict_types=1);

require dirname(__DIR__, 2) . '/src/webservice/assessment.php';

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

// Article/list counterparts share quality levels from WP:ASSESS revision 89490563.
$levels = [['FA', 'FL', '典范', '典範', '特色列表'], ['A', 'AL', '甲', '甲级列表', '甲級列表'],
    ['GA', '优良', '優良'], ['Bplus', '乙上'], ['B', 'BL', '乙', '乙级列表'],
    ['C', 'CL', '丙', '丙級列表'], ['D', '丁'], ['Start', 'List', '初', '列表'],
    ['Stub', 'SL', 'StubList', '小作品', '小列表'], ['Substub', '小小作品']];
foreach ($levels as $index => $aliases) {
    foreach ($aliases as $alias) {
        if (dashboardAssessmentRank($alias, 'class') !== 10 - $index) {
            throw new RuntimeException('Unexpected class rank: ' . $alias);
        }
    }
}
$importanceLevels = [['Top', '极高', '極高'], ['High', '高'], ['Mid', '中'], ['Low', '低'], ['Bottom', '极低', '極低']];
foreach ($importanceLevels as $index => $aliases) {
    foreach ($aliases as $alias) {
        if (dashboardAssessmentRank($alias, 'importance') !== 5 - $index) {
            throw new RuntimeException('Unexpected importance rank: ' . $alias);
        }
    }
}
foreach ([null, '', 'Unassessed', '未评级', 'Disambig', 'NA', '不适用', 'custom'] as $value) {
    foreach (['class', 'importance'] as $dimension) {
        if (dashboardAssessmentRank($value, $dimension) !== null) {
            throw new RuntimeException('Unranked observations must stay unknown.');
        }
    }
}
$assessed = [
    ['page_id' => 9, 'title' => 'Unknown B', 'assessment_class' => null, 'assessment_importance' => null],
    ['page_id' => 1, 'title' => 'A', 'assessment_class' => 'FA', 'assessment_importance' => 'Top'],
    ['page_id' => 3, 'title' => 'A', 'assessment_class' => 'FL', 'assessment_importance' => '极高'],
    ['page_id' => 2, 'title' => 'A', 'assessment_class' => '典范', 'assessment_importance' => 'TOP'],
    ['page_id' => 4, 'title' => 'B', 'assessment_class' => '乙', 'assessment_importance' => '中'],
    ['page_id' => 5, 'title' => 'C', 'assessment_class' => 'Stub', 'assessment_importance' => 'Low'],
    ['page_id' => 8, 'title' => 'Unknown A', 'assessment_class' => 'new-grade', 'assessment_importance' => 'NA'],
];
foreach (['class', 'importance'] as $sort) {
    foreach (['desc' => [1, 2, 3, 4, 5, 8, 9], 'asc' => [5, 4, 1, 2, 3, 8, 9]] as $order => $expected) {
        $actual = array_column(dashboardMaintenanceItems($assessed, $sort, $order), 'page_id');
        if ($actual !== $expected) {
            throw new RuntimeException('Assessment order must keep unranked records last and equivalent grades stable.');
        }
    }
}
// Sort the entire saved group before selecting a fifty-record page.
$many = [];
for ($index = 1; $index <= 101; $index++) {
    $many[] = ['page_id' => $index, 'title' => sprintf('%03d', $index),
        'assessment_class' => $index === 101 ? 'FA' : 'Stub'];
}
$ordered = dashboardMaintenanceItems($many, 'class', 'desc');
if (array_column(array_slice($ordered, 0, 50), 'page_id') !== array_merge([101], range(1, 49))
    || array_column(array_slice($ordered, 50, 50), 'page_id') !== range(50, 99)
    || array_column($many, 'page_id') !== range(1, 101)) {
    throw new RuntimeException('Global assessment ordering must preserve pagination and the saved input.');
}
echo "Toolforge maintenance sorting checks passed.\n";
