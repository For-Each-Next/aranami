<script setup>
import { CdxCard } from "@wikimedia/codex";
</script>

<template>
  <CdxCard class="routine-row" icon="ARANAMI_ICON" id="ARANAMI_ID" aria-labelledby="ARANAMI_HEADING">
    <template #title>ARANAMI_TITLE</template>
    <template #description>ARANAMI_DESCRIPTION</template>
    <template #supporting-text>ARANAMI_SUPPORTING_TEXT</template>
  </CdxCard>
</template>

<jinja name="title">
<h3 id="routine-{{ routine.identifier }}-heading">{% include "includes/routine_links.html" %}</h3>
</jinja>

<jinja name="description">
<p class="routine-status" data-status="{{ routine.status }}"><a class="status-label status-link" href="#{{ routine.popup_id }}" aria-controls="{{ routine.popup_id }}" aria-haspopup="dialog"{% if routine.progress %} data-progress-label="{{ routine.state }}" data-progress-fallback="{{ routine.fallback }}"{% endif %}>{{ routine.state }}</a></p>
          {% if routine.progress %}<p class="routine-progress" data-progress-active>{{ routine.progress }}</p>{% endif %}
</jinja>

<jinja name="supporting-text">
<ul class="routine-meta">
            <li>{% if routine.running %}{{ t.ops.execution_started }}{% else %}{{ t.ops.execution_finished }}{% endif %}{% if routine.observed.known and routine.history_popup_id %}<a href="#{{ routine.history_popup_id }}" aria-controls="{{ routine.history_popup_id }}" aria-haspopup="dialog">{% endif %}{% with time=routine.observed %}{% include "includes/time.html" %}{% endwith %}{% if routine.observed.known and routine.history_popup_id %}</a>{% endif %}{% with relative=routine.relative %}{% include "includes/relative.html" %}{% endwith %}</li>
            {% if not routine.running %}<li>{{ t.ops.next_run }}<span{% if routine.schedule_expires %} data-schedule-time{% endif %}>{{ routine.next_run }}{% with relative=routine.upcoming %}{% include "includes/relative.html" %}{% endwith %}</span></li>{% endif %}
          </ul>
</jinja>

<jinja name="icon">
<svg class="report-icon cdx-card__icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false"><use href="{{ asset_url('assets/icons/report-icons.svg') }}#{{ routine.icon }}"></use></svg>
</jinja>

<jinja name="after">
{% include "includes/report_details.html" %}
</jinja>

<jinja name="id">
routine-{{ routine.identifier }}
</jinja>

<jinja name="heading">
routine-{{ routine.identifier }}-heading
</jinja>
