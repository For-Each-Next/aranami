# Aranami

This repository preserves the final Aranami v0.9.3 source. The v0.9.2 archive
consolidated unpublished development after v0.8.8; v0.9.3 adds the corrected
consistency prefill workflow, direct pagination and memory display. Toolforge
operation remains independent of this read-only GitHub archive.

Aranami (荒波, “rough waves”) maintains Wikipedia project reports and provides
article-maintenance tools and newsletter drafts. It serves
[WikiProject Video games on Chinese Wikipedia](https://zh.wikipedia.org/wiki/WikiProject:电子游戏)
from Toolforge.

Daily reports share a completed-day observation of project membership and
assessments. Hourly reports can use newer observations. Saved article evidence
and daily Pageviews support later reports and newsletter drafts.

## Results

- [Dashboard](https://aranami.toolforge.org/): reviews, main-page appearances,
  upcoming recognized content and popular articles.
- [Discussions](https://aranami.toolforge.org/discussions/): active project-talk
  topics from the past 30 days, grouped by project, article and other pages.
- [Maintenance](https://aranami.toolforge.org/maintenance/): searchable, sortable
  saved article worklists from maintenance categories.
- [Maintenance checks](https://aranami.toolforge.org/checks/):
  metadata inconsistencies and quarterly update hints, grouped by issue type.
  The linked Tampermonkey script fills prepared talk-header repairs for a
  manual diff review.
- [Redirect recovery](https://aranami.toolforge.org/redirects/): redirects whose
  talk-page history contains a removed Video games project banner, with revision
  evidence and historical article excerpts for manual review.
- [FA/GA editor statistics](https://aranami.toolforge.org/quality-authorship/):
  experienced editors with estimated article credits by grade, listing evidence
  and latest observed public edit dates.
- [Quality article analysis](https://aranami.toolforge.org/quality-analysis/):
  current quality grades including Bplus, listing-year charts, prose-length
  ranges and a complete sortable article table, prepared hourly in the background.
- [Newsletter drafts](https://aranami.toolforge.org/newsletter/): copyable
  quarterly wikitext and a separate January annual review for manual publication.
- [Reports](https://aranami.toolforge.org/reports/): recorded attempts, confirmed
  updates and recent execution details.
- [Saved data](https://aranami.toolforge.org/data/): source-linked project
  captures with their original metadata, field schema and a bounded preview.

Saved results remain available during source failures. Unknown observations
are not converted to zeroes. Published and cancelled newsletter issues retain
their saved facts; delayed drafts can continue improving.

## UTC schedule

| Work | Trigger | Result |
|---|---|---|
| Project data | Every hour :00; daily at 00:00:00 | Shared page lists, replica revision IDs and categories; full daily snapshots for yesterday |
| Assessment log | Idle after 00:00 | Adjacent-day membership/class/importance changes |
| Newsletter facts | Idle after 00:00 and due retries | Eligible quarterly and annual draft data |
| DYK checks | Deferred every hour :00 | Current nominees and completed dates, with unchanged source parses reused |
| Article evidence | Every hour :00 | Retained recognition evidence and quality-author activity |
| Quality analysis | Every hour :02, idle after ordinary work | Cached article metrics and precomputed chart summaries |
| Quality authors | Every hour :02, idle after ordinary work | Resumable B/BL listing histories, article cutoffs and authorship |
| New pages | Idle after 00:00; hourly retries while behind | Created pages and redirect conversions selected by project keywords |
| Assessment lists / English comparisons | Idle after 00:00 | Quality, review and bilingual project reports |
| Pageviews | 19:00 normally, :00 retries while behind | Daily and longer-period project/task-force rankings |
| PexBot reports | Idle after 00:00 | Configured maintenance refreshes, with a 24-hour cooldown |
| Website | Every hour :00 | Reviews hourly and daily datasets on a new UTC boundary |
| Redirect recovery | Resumable background pass while scheduled work is idle | Replica discovery and preloaded current pages use batches of 50; saved matches appear incrementally |

The primary collection starts with page lists at every UTC hour, with the full
daily capture at 00:00:00. Source collection finishes before dependent reports
run. Reports reuse the committed daily or disposable hourly Parquet data.
Startup recovery does not shift subsequent hourly boundaries.
These are dispatch times, not guaranteed edit times. Pageviews waits for actual
19:00 readiness and uses the frozen midnight titles and assessments. Continuous
traffic histories receive only their missing tail; gapped histories are
refetched. Project snapshots always come from new complete queries.

## Documentation

[Usage](docs/usage.md) covers terminal operations;
[Administration](docs/administration.md) covers deployment and recovery;
[Development](docs/development.md) covers the source layout and offline checks;
[Architecture](docs/architecture.md) defines ownership, data and migration.
The [documentation index](docs/README.md) links component references.

The final source is retained on `trunk` and tagged `v0.9.3` in
[aranami-old](https://github.com/For-Each-Next/aranami-old).
[CHANGELOG](CHANGELOG.md) records the completed releases.
Historical records remain available through normal data readers after old-format
extractors retire. Source is licensed under [CC0 1.0 Universal](LICENSE);
upstream content and bundled assets retain their attributed licenses.
