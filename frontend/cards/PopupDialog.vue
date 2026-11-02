<script setup>
import { CdxDialog } from "@wikimedia/codex";
</script>

<template>
  <CdxDialog :open="true" :use-close-button="true" title="ARANAMI_TITLE" close-button-label="ARANAMI_CLOSE" class="page-updates">
    ARANAMI_BODY
  </CdxDialog>
</template>

<jinja name="title">
{{ dialog_title }}
</jinja>

<jinja name="close">
{{ t.ops.close }}
</jinja>

<jinja name="body">
{{ caller() }}
</jinja>
