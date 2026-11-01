<script setup>
import { CdxCard } from "@wikimedia/codex";
</script>

<template>
  <CdxCard class="backlog-task" url="ARANAMI_URL">
    <template #title>ARANAMI_TITLE</template>
    <template #supporting-text>ARANAMI_SUPPORTING_TEXT</template>
  </CdxCard>
</template>

<jinja name="title">
<h3 class="backlog-label">{{ task.label }}</h3>
</jinja>

<jinja name="supporting-text">
<span class="backlog-count">{{ task.count }}</span>
</jinja>

<jinja name="url">
{{ task.url }}
</jinja>
