<script setup>
import { CdxAccordion, CdxTextArea } from "@wikimedia/codex";
</script>

<template>
    <CdxAccordion class="newsletter-code" heading-level="h3">
        <template #title>ARANAMI_TITLE</template>
        <CdxTextArea
            id="ARANAMI_TEXT_AREA_ID"
            class="newsletter-text"
            model-value="ARANAMI_TEXT"
            aria-labelledby="ARANAMI_LABELLED_BY"
            lang="zh"
            dir="ltr"
            rows="8"
            spellcheck="false"
            data-newsletter-wikitext=""
            readonly
            autosize
        />
    </CdxAccordion>
</template>

<jinja name="title">
<span id="newsletter-code-label-{{ draft_index }}">{{ t.newsletter.wikitext }}</span>
</jinja>

<jinja name="text-area-id">
newsletter-text-{{ draft_index }}
</jinja>

<jinja name="labelled-by">
newsletter-heading-{{ draft_index }} newsletter-code-label-{{ draft_index }}
</jinja>

<jinja name="text">
{{ '\n' }}{{ draft.text }}
</jinja>
