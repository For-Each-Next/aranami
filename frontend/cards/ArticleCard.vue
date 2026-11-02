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
      <div class="card-meta review-meta">
      <span class="card-meta-lead">{{ card.review_link }}{% if card.review_dates %}{{ ': ' if language_tag == 'en' else '\uff1a' }}{% endif %}</span>
      {% if card.review_dates %}
      <ul class="metadata-items" role="list">
        {% for date in card.review_dates %}
        <li><time datetime="{{ date.value }}" title="{{ date.label }}">{{ date.elapsed }}</time></li>
        {% if date.participants %}<li><span class="review-participants">{{ date.participants }}</span></li>{% endif %}
        {% endfor %}
      </ul>
      {% endif %}
      </div>
      {% elif card.pageviews_url %}
      <div class="card-meta popularity-meta">
      <span class="card-meta-lead"><time datetime="{{ card.date.value }}" title="{{ card.date.value }}">{{ card.date.label }}</time>{{ card.date_separator }}</span>
      <ul class="metadata-items" role="list">
        <li><a href="{{ card.pageviews_url }}" class="pageviews-number">{{ card.views }}</a></li>
        {% if card.site_rank %}<li><a class="site-rank" href="{{ card.site_rank_url }}">{{ card.site_rank }}</a></li>{% endif %}
      </ul>
      </div>
      {% else %}
      <ul class="card-meta metadata-items" role="list">
        {% if section.key == 'recognized' and card.kind %}<li>{{ card.kind }}</li>{% endif %}
        {% if card.date or card.scheduled or card.date_note %}<li>{{ card.scheduled }}{% if card.date %}<time datetime="{{ card.date.value }}" title="{{ card.date.value }}">{{ card.date.label }}</time>{% endif %}{% if card.date_note %}<abbr class="date-note-marker" title="{{ card.date_note }}" aria-label="{{ card.date_note }}">†</abbr>{% endif %}</li>{% endif %}
        {% if section.key != 'recognized' and card.kind %}<li>{{ card.kind }}</li>{% endif %}
      </ul>
      {% endif %}
</jinja>

<jinja name="icon">
{% if section.is_review %}<a class="review-icon-link cdx-card__icon" href="{{ card.review_icon_source }}" title="{{ card.review_icon_label }}">{% if card.review_icon %}<img class="review-icon" src="{{ card.review_icon }}" width="20" height="20" alt="{{ card.review_icon_label }}">{% else %}<svg class="review-icon" width="20" height="20" viewBox="0 0 20 20" role="img" aria-label="{{ card.review_icon_label }}" focusable="false"><title>{{ card.review_icon_label }}</title><use href="{{ asset_url('assets/icons/report-icons.svg') }}#{{ card.review_icon_symbol }}"></use></svg>{% endif %}</a>{% elif card.highlight_icon %}<a class="highlight-icon-link cdx-card__icon" href="{{ card.highlight_icon.source }}" title="{{ card.highlight_icon.label }}"><img class="highlight-icon" src="{{ card.highlight_icon.url }}" width="20" height="20" alt="{{ card.highlight_icon.label }}"></a>{% elif card.pageviews_url %}<img class="article-medal cdx-card__icon" src="{{ asset_url('assets/icons/medal-' ~ card.rank ~ '.svg') }}" width="22" height="22" alt="" aria-hidden="true">{% else %}<span class="article-rank cdx-card__icon" aria-hidden="true"></span>{% endif %}
</jinja>

<jinja name="heading">
article-{{ 'reviews' if section.is_review else section.key }}-{{ card_index }}
</jinja>
