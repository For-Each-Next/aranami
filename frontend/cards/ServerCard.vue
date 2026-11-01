<script setup>
import { CdxCard } from "@wikimedia/codex";
</script>

<template>
  <CdxCard class="server-card" aria-labelledby="ARANAMI_HEADING">
    <template #title>ARANAMI_TITLE</template>
    <template #description>ARANAMI_DESCRIPTION</template>
  </CdxCard>
</template>

<jinja name="title">
<h3 id="server-group-{{ group.key }}">{{ group.label }}</h3>
</jinja>

<jinja name="description">
{% if group.key == 'application' %}
<div class="cdx-table"><div class="cdx-table__table-wrapper"><table class="cdx-table__table server-metrics"><caption class="visually-hidden">{{ group.label }}</caption><tbody>
<tr><th scope="row">{{ t.server.state }}</th><td><span id="server-state"{% if server.expires %} data-schedule-time{% endif %} data-stale-text="{{ t.server.stale }}">{{ server.state }}</span>{% if server.started %}<span class="server-start">{{ server.opening }}{{ t.server.started }}{% with time=server.started %}{% include "includes/time.html" %}{% endwith %}{{ server.separator }}{% with relative=server.started_relative %}{% include "includes/relative.html" %}{% endwith %}{{ server.closing }}</span>{% endif %}</td></tr>
{% for row in group.rows %}<tr><th scope="row">{{ row.label }}</th><td>{{ row.value }}</td></tr>{% endfor %}
</tbody></table></div></div>
{% else %}
{% for row in group.rows %}<p class="server-value"{% if row.fresh %} data-schedule-time{% endif %}{% if row.stale %} data-stale-text="{{ row.stale }}"{% endif %}>{{ row.value }}</p>{% endfor %}
{% endif %}
</jinja>

<jinja name="heading">
server-group-{{ group.key }}
</jinja>
