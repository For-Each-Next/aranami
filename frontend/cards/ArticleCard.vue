<script setup>
import { CdxCard } from "@wikimedia/codex";
</script>

<template>
  <CdxCard class="article-card-body" icon="ARANAMI_ICON" aria-labelledby="ARANAMI_HEADING">
    <template #title>ARANAMI_TITLE</template>
    <template #description>ARANAMI_DESCRIPTION</template>
    <template #supporting-text>ARANAMI_SUPPORTING_TEXT</template>
  </CdxCard>
</template>

<jinja name="title">
<h3 id="article-{{ 'reviews' if section.is_review else section.key }}-{{ card_index }}"><span class="article-summary"><bdi lang="zh">{{ card.link }}</bdi></span></h3> {% include "includes/assessment_chips.html" %}
</jinja>

<jinja name="description">
{% if card.extract is not none %}<p class="article-extract"><bdi lang="{{ card.extract_language }}">{{ card.extract }}</bdi></p>{% endif %}
</jinja>

<jinja name="supporting-text">
{% if card.review_link %}
      <ul class="card-meta review-meta" role="list">
        <li>{{ card.review_link }}</li>
        {% for date in card.review_dates %}
        <li><time datetime="{{ date.value }}" title="{{ date.label }}">{{ date.elapsed }}</time></li>
        {% if date.participants %}<li><span class="review-participants">{{ date.participants }}</span></li>{% endif %}
        {% endfor %}
      </ul>
      {% else %}
      <p class="card-meta">{% if card.pageviews_url %}<time datetime="{{ card.date.value }}">{{ card.date.label }}</time>{{ card.date_separator }}<a href="{{ card.pageviews_url }}" class="pageviews-number">{{ card.views }}</a>{% if card.site_rank %} · <a class="site-rank" href="{{ card.site_rank_url }}">{{ card.site_rank }}</a>{% endif %}{% else %}{{ card.scheduled }}{% if card.date %}<time datetime="{{ card.date.value }}">{{ card.date.label }}</time>{% endif %}{% if card.separator %} · {% endif %}{% if card.kind %}{{ card.kind }}{% endif %}{% if card.date_note %}<abbr class="date-note-marker" title="{{ card.date_note }}" aria-label="{{ card.date_note }}">†</abbr>{% endif %}{% endif %}</p>
      {% endif %}
</jinja>

<jinja name="icon">
{% if section.is_review %}<a class="review-icon-link cdx-card__icon" href="{{ card.review_icon_source }}" title="{{ card.review_icon_label }}">{% if card.review_icon %}<img class="review-icon" src="{{ card.review_icon }}" width="20" height="20" alt="{{ card.review_icon_label }}">{% else %}<svg class="review-icon" width="20" height="20" viewBox="0 0 20 20" role="img" aria-label="{{ card.review_icon_label }}" focusable="false"><title>{{ card.review_icon_label }}</title><use href="{{ asset_url('report-icons.svg') }}#{{ card.review_icon_symbol }}"></use></svg>{% endif %}</a>{% else %}<span class="article-rank cdx-card__icon" aria-hidden="true"></span>{% endif %}
</jinja>

<jinja name="heading">
article-{{ 'reviews' if section.is_review else section.key }}-{{ card_index }}
</jinja>
