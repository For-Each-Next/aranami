<script setup>
import { CdxCard } from "@wikimedia/codex";
</script>

<template>
  <CdxCard class="backlog-task" url="ARANAMI_URL" icon="ARANAMI_ICON" :force-thumbnail="false">
    <template #title>ARANAMI_TITLE</template>
    <template #supporting-text>ARANAMI_SUPPORTING_TEXT</template>
  </CdxCard>
</template>

<jinja name="icon">
{% if task.image %}<img class="maintenance-icon cdx-card__icon" src="{{ asset_url(task.image) }}" alt="" width="22" height="22" loading="lazy">{% endif %}
</jinja>

<jinja name="title">
<h3 class="backlog-label">{{ task.label }}</h3>
</jinja>

<jinja name="supporting-text">
<span class="backlog-count">{{ task.count }}</span>
</jinja>

<jinja name="url">
{{ task.url }}
</jinja>
