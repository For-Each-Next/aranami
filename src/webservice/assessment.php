<?php
/** Render article assessment labels and organize saved maintenance lists. */
declare(strict_types=1);

/** Share one chip appearance across the dashboard and maintenance pages. */
function dashboardChipStyles(): void
{
    echo '.assessment-chip { display: inline-block; flex: 0 0 auto; max-width: 100%; white-space: normal; overflow-wrap: anywhere; padding: 0 5px; border: 1px solid #a8d9cb; border-radius: 9px; background: #edf9f4; color: #54595d; font-size: .75rem; font-weight: 400; line-height: 1.7; vertical-align: baseline; }';
}

/** Normalize known assessment codes and Chinese names without inferring grades. */
function dashboardAssessmentCode(string $value, string $dimension): ?string
{
    $classes = [
        'fa' => 'FA', '典范' => 'FA', '典範' => 'FA',
        'fl' => 'FL', '特色列表' => 'FL', '典表' => 'FL',
        'ga' => 'GA', '优良' => 'GA', '優良' => 'GA',
        'a' => 'A', '甲' => 'A', 'al' => 'AL', '甲表' => 'AL', '甲级列表' => 'AL', '甲級列表' => 'AL',
        'bplus' => 'Bplus', '乙上' => 'Bplus',
        'b' => 'B', '乙' => 'B', 'bl' => 'BL', '乙表' => 'BL', '乙级列表' => 'BL', '乙級列表' => 'BL',
        'c' => 'C', '丙' => 'C', 'cl' => 'CL', '丙表' => 'CL', '丙级列表' => 'CL', '丙級列表' => 'CL',
        'd' => 'D', '丁' => 'D',
        'start' => 'Start', '初' => 'Start',
        'stub' => 'Stub', '小作品' => 'Stub', 'list' => 'List', '列表' => 'List',
        'sl' => 'SL', 'stublist' => 'SL', '小列表' => 'SL',
        'substub' => 'Substub', '小小作品' => 'Substub',
        'disambig' => 'Disambig', '消歧义' => 'Disambig', '消歧義' => 'Disambig',
        'unassessed' => 'Unassessed', '未评级' => 'Unassessed', '未評級' => 'Unassessed',
    ];
    $importance = [
        'top' => 'Top', '极高' => 'Top', '極高' => 'Top',
        'high' => 'High', '高' => 'High', 'mid' => 'Mid', '中' => 'Mid',
        'low' => 'Low', '低' => 'Low', 'na' => 'NA', '不适用' => 'NA', '不適用' => 'NA',
        'bottom' => 'Bottom', '极低' => 'Bottom', '極低' => 'Bottom',
        'unknown' => 'Unknown', '未评级' => 'Unknown', '未評級' => 'Unknown',
    ];
    return ($dimension === 'class' ? $classes : $importance)[strtolower(trim($value))] ?? null;
}

/** Name recorded project assessments without inferring absent grades. */
function dashboardAssessmentLabel(string $value, string $dimension): string
{
    $code = dashboardAssessmentCode($value, $dimension);
    return $code === null ? $value : uiText('assessment.' . $dimension . '.' . $code);
}

/** Rank comparable grades, leaving unknown and non-applicable values unranked. */
function dashboardAssessmentRank(mixed $value, string $dimension): ?int
{
    if (!is_string($value)) {
        return null;
    }
    // Wikipedia:页面评级, revision 89490563: article/list counterparts share ranks.
    $classes = ['FA' => 10, 'FL' => 10, 'A' => 9, 'AL' => 9, 'GA' => 8,
        'Bplus' => 7, 'B' => 6, 'BL' => 6, 'C' => 5, 'CL' => 5, 'D' => 4,
        'Start' => 3, 'List' => 3, 'Stub' => 2, 'SL' => 2, 'Substub' => 1];
    $importance = ['Top' => 5, 'High' => 4, 'Mid' => 3, 'Low' => 2, 'Bottom' => 1];
    $code = dashboardAssessmentCode($value, $dimension);
    return ($dimension === 'class' ? $classes : $importance)[$code ?? ''] ?? null;
}

/** Show known quality and importance as chips outside the article link. */
function dashboardAssessmentChips(array $item): void
{
    $chips = '';
    foreach (['class', 'importance'] as $dimension) {
        $value = $item['assessment_' . $dimension] ?? null;
        if (!is_string($value) || trim($value) === '') {
            continue;
        }
        $label = dashboardAssessmentLabel($value, $dimension);
        $chips .= '<span class="assessment-chip" data-assessment="' . $dimension . '" title="' . html($label) . '">' . html($label) . '</span>';
    }
    if ($chips !== '') {
        echo '<span class="assessment-chips">' . $chips . '</span>';
    }
}

/** Resolve a saved configurable label in the current interface language. */
function dashboardMaintenanceLabel(mixed $label, string $fallback): string
{
    if (is_string($label)) {
        return $label;
    }
    if (is_array($label) && $label !== []) {
        return $label[uiLocale()] ?? $label['en'] ?? reset($label);
    }
    return $fallback;
}

/** Name a configured task, falling back to its source tag when unlabelled. */
function dashboardMaintenanceTaskLabel(array $group): string
{
    return dashboardMaintenanceLabel($group['label'] ?? null, $group['category']);
}

/** Name a configured section, using the maintenance label if unlabelled. */
function dashboardMaintenanceSectionLabel(array $groups): string
{
    return dashboardMaintenanceLabel($groups[0]['section_label'] ?? null, uiText('backlog.heading'));
}

/** Sort saved worklists with unranked observations last and stable article ties. */
function dashboardMaintenanceItems(array $items, string $sort = 'oldest', string $order = 'asc'): array
{
    usort($items, static function (array $left, array $right) use ($sort, $order): int {
        if (in_array($sort, ['oldest', 'class', 'importance'], true)) {
            $leftValue = $sort === 'oldest' ? ($left['oldest_issue_month'] ?? null)
                : dashboardAssessmentRank($left['assessment_' . $sort] ?? null, $sort);
            $rightValue = $sort === 'oldest' ? ($right['oldest_issue_month'] ?? null)
                : dashboardAssessmentRank($right['assessment_' . $sort] ?? null, $sort);
            if (($leftValue === null) !== ($rightValue === null)) {
                return $leftValue === null ? 1 : -1;
            }
            $comparison = $sort === 'oldest' ? strcmp($leftValue ?? '', $rightValue ?? '')
                : ($leftValue ?? 0) <=> ($rightValue ?? 0);
            if ($comparison !== 0) {
                return $order === 'asc' ? $comparison : -$comparison;
            }
        }
        $comparison = strcmp($left['title'], $right['title']);
        if ($sort === 'title' && $order === 'desc') {
            $comparison = -$comparison;
        }
        return $comparison ?: $left['page_id'] <=> $right['page_id'];
    });
    return $items;
}

/** Group saved lists in their configured section order. */
function dashboardMaintenanceSections(array $groups): array
{
    $sections = [];
    foreach ($groups as $group) {
        $sections[$group['section_key'] ?? 'other'][] = $group;
    }
    return $sections;
}
