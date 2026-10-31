# Aranami terminal usage

Use this guide to inspect results, preview reports, or run a selected routine
in the existing Toolforge environment. Aranami runs only on Toolforge; local
development and verification are offline. Account access, deployment, service
control, and recovery are in [Administration](administration.md).

## Inspect results and logs

As the `aranami` tool account on the Toolforge bastion:

```shell
cd /data/project/aranami
toolforge jobs list
toolforge jobs show aranami-hub
toolforge jobs logs aranami-hub
toolforge jobs logs aranami-web
```

The hub publishes reports; the web job serves the
[dashboard](https://aranami.toolforge.org/) and
[daily-log viewer](https://aranami.toolforge.org/logs/). Status shows recorded
outcomes, progress, timing, and next runs; missing/stale observations stay unknown.

```shell
ls -1 logs/aranami.*.log
tail -n 100 logs/aranami.YYYY-MM-DD.log
```

Choose a UTC date. Logs retain the current date and previous 29 dates. The web
viewer translates separately recorded major events into the selected language
and links related Wikipedia reports; downloads include full diagnostics.
Event records begin with this deployment. Older diagnostic files remain
downloadable, without inferred historical events. Search covers the recent
event window shown on the page.
The viewer defaults to newest first, with 25 events per page, a result filter,
and a chronological-order option. Download the selected day or use the daily
archive. Choose English, Simplified Chinese, or Traditional Chinese; that choice
lasts one year in the current browser. Article titles keep their source spelling.

Inspect backend-owned metadata read-only:

| File | Observation |
|---|---|
| `state/routine-status.json` | Routine starts, results, progress, duration, and confirmed saves |
| `state/schedule-status.json` | Monitor state, actual next executions, and resource observations |
| `state/report-updates.json` | Per-destination timing, outcome, and confirmed edits |

Complete results are under `reports/<report-key>/`; `latest.json` selects a
generation containing its manifest, Parquet tables, and `report.wikitext`.
Unknown edit counts are not zero. Do not modify saved observations or expose
credentials in diagnostics; see [runtime locations](administration.md#runtime-folder-architecture).

## Prepare a quarterly newsletter

Open `/newsletter/` for the automatically selected next quarterly issue.
The hub fetches source data before publication and refreshes the draft daily
as the reporting quarter progresses. The page provides separate editable
wikitext for the main overview, `特優內容`, and `宣告`.
All three pages keep the preceding issue's original wikitext design, including
their banners, headings, columns, navigation, and count-change styling.
For example, `/newsletter/?issue=2027-01` selects issue 29, covering October
through December 2026. The issue radios offer the latest completed issue and
the current draft. The default advances at exactly `00:00 UTC` on the first
day of January, April, July, and October. The hub also refreshes the completed
quarter at that boundary, including when its normal daily refresh is not due.
Edit each draft, copy it, and preview it at the linked Wikipedia destination
before saving. Manual selection and copying also work without JavaScript.
Edits in these boxes last only until the page is reloaded or another issue is
selected; the website does not save or publish them.

Requests use saved data only. Each draft states its actual observation cutoff;
an unfinished quarter uses completed UTC days rather than future quarter-end
values. New-page counts distinguish complete records from confirmed minimums
when some daily records are missing. Source revision links and observation
times remain available for checking the generated numbers. A failed refresh
retains the last successful draft. An issue without collected data shows its
collection state instead of empty templates. Authored features and interviews
are outside this generator.

## Routine names and UTC triggers

Use these identifiers in `tasks`:

| Routine | Result | UTC trigger |
|---|---|---|
| `dyks` | Completed DYK entries, nominees, and statistics | Every hour at `:00` |
| `new_pages` | Daily keyword-matched new/conversion entries | Daily at `00:07` |
| `assessment_lists` | Project assessment and review lists | Every hour at `:31` |
| `assessment_log` | Full project assessment snapshot and observed changes | Daily at `00:00:00` |
| `pageviews` | Rankings for each missing eligible date | Every hour at `:59` while behind/deferred |
| `enwp_key_articles` | Current English importance/quality comparisons | Daily at `23:24` |
| `pexbot` | Subscribed report refresh requests | Daily at `00:00` |

Triggers do not guarantee edits: unchanged outputs are not saved. New pages
retains 100 complete UTC dates through yesterday, then waits for its next daily
trigger. English comparisons and PexBot refresh current state, not daily history.

Pageviews publishes missing dates separately and chronologically. Yesterday
becomes eligible at the actual `18:00 UTC` boundary, first attempted at `18:59`.
Once current, it waits for `18:59` after the next readiness boundary. Previews and
failed saves do not establish completion; manual runs cannot bypass readiness.

## Preview selected routines

Submit a one-off job from the bastion as the tool account:

```shell
toolforge jobs run aranami-preview --image python3.13 --mount=all \
  --command 'tool_dir=/data/project/aranami; set -eu; . "$tool_dir/app/scripts/toolforge/runtime-env.sh"; export PYWIKIBOT_DIR="$tool_dir"; exec "$runtime_python" -c "from backend import run_once; run_once(dry=True, tasks=[\"new_pages\", \"pageviews\"])"'
toolforge jobs logs aranami-preview
```

`dry=True` suppresses wiki saves and PexBot requests. It writes Markdown summaries,
flat `.wikitext` proposals, and isolated bundles under `dry-run/reports/`, without
advancing production datasets, completion evidence, membership, or status.
The shared launcher guard pins source and Python to one validated generation,
so a later service update cannot change code underneath this one-off job.
After completion, inspect the artifacts and delete the definition before reuse:

```shell
ls -lt dry-run/
toolforge jobs delete aranami-preview
```

## Run an immediate update

This example performs live publication; run it only while that routine is idle:

```shell
toolforge jobs run aranami-manual --image python3.13 --mount=all \
  --command 'tool_dir=/data/project/aranami; set -eu; . "$tool_dir/app/scripts/toolforge/runtime-env.sh"; export PYWIKIBOT_DIR="$tool_dir"; exec "$runtime_python" -c "from backend import run_once; run_once(dry=False, tasks=[\"assessment_lists\"])"'
toolforge jobs logs aranami-manual
```

Ask the maintainer for a maintenance window if needed. Inspect results, then
delete the finished `aranami-manual` definition before reusing its name.

`run_once(date=None, dry=False, tasks=None, clear_cache=False)` does not change
recurring schedules. `tasks=None` selects all routines; `date` sets a report
anchor, not wall-clock readiness. Reserve `clear_cache=True` for deliberate
maintainer-led revalidation. Imports and `--help` do not execute reports.

The hub's `--dry` mode runs recurring previews without production website
collection or HTTP serving; see [startup options](administration.md#start-the-applications).
PexBot selects configured Chinese-Wikipedia roots, skips that UTC day's completed
refreshes, and waits 15 seconds between live attempts.

## Managed report settings

Preserve paired markers and text outside their managed ranges:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...retained daily records...
<!-- aranami end="new-pages" -->

<!-- aranami begin="page_views" history-days="800" -->
...popularity report...
<!-- aranami end="page_views" -->

<!-- aranami begin="assessment-log" days="30" -->
...retained assessment changes...
<!-- aranami end="assessment-log" -->
```

Missing, malformed, duplicate, reversed, or ambiguous required markers prevent
publication. Keep labeled-section tags outside them. Pageviews retains its
`<!-- report date YYYY-MM-DD -->` checkpoint. See the
[managed-range contracts](architecture.md#managed-ranges-and-on-wiki-configuration)
for settings; changing targets, schedules, or definitions requires deployment.

### Assessment change log

`assessment_log` runs daily at `00:00:00 UTC` and updates
`WikiProject:电子游戏/评级/日志`, ordering each day's subsections as renames,
reassessments, additions to the assessment set, and removals.
A removal means an existing page ID disappeared from the project's assessment
set; it does not establish that the page was deleted. Stable page IDs distinguish
renames from departures and arrivals. Dated sections use the previous UTC day:
the October 25 midnight observation reports October 24. The actual collection
timestamp remains in metadata; comparisons are not a
complete edit history. Main page links and assessment values are bold, with
simplified assessment labels and unbolded discussion/revision links. Entries
sort by numeric namespace and then title within each subsection.

Each UTC date retains one immutable full project PageAssessments snapshot as
Parquet through the shared report store, with all source columns available for
other work. Its `project-assessments` identity distinguishes the wiki, project,
and date. Capture precedes the destination read, so a wiki read or edit failure
does not discard that day's reusable source data. The manifest records the
actual observation time; a delayed run never claims it collected at midnight.
Repeated runs reuse that date's observation,
and a manual report anchor cannot create a historical snapshot from current data.

Other Toolforge workflows can call
`backend.services.project_assessments.read_snapshot(site, project, day)` to read
an existing date without querying sources. It returns a validated report bundle
or `None` when that date has no snapshot; the full assessment table is
`bundle.tables["members"]`. The corresponding `members.parquet` lives in that
date's `reports/<report-key>/<generation>/` bundle. Preview snapshots stay under
`dry-run/reports/` and cannot replace production observations.

Each new dated section starts with the total number of mainspace articles and
lists, followed by quality classes and importance levels in assessment order.
List counts follow the corresponding quality class; zero counts are omitted.

The `assessment-log` range retains 30 report dates by default, through yesterday;
`days` accepts 1 through 365. Repeated checks append within the same daily
sections. A newer daily snapshot with no changes adds its date heading and
`無變更`; repeated checks preserve existing entries without duplicating the day.
Unobserved dates are not filled. The first successful observation establishes
the current baseline without claiming changes or no changes, and repeating
that initial date does not add a no-change entry. Failed publication
and previews do not advance the live baseline. Unsaved dated events survive
retries, including a removal followed by a later restoration.

Creating the initial marker pair and backfilling several days of verified
historical changes is a separate, one-time wiki edit. This initialization is
not part of the recurring source task. Historical entries require actual
historical observations; the current assessment set alone cannot reconstruct
past removals. Keep the markers after initialization so normal updates preserve
all text outside the range.

### DYK chart data

The DYK routine updates completed entries, nominees, and the chart on
`WikiProject:电子游戏/新条目推荐`. Initialize its `Graph:Chart` parameters once
with paired markers around the existing comma-separated values:

```wikitext
|x=<!-- aranami begin="dyk-chart-x" -->2006-06-30, 2006-09-30<!-- aranami end="dyk-chart-x" -->
|y1=<!-- aranami begin="dyk-chart-y" -->0, 6<!-- aranami end="dyk-chart-y" -->
```

Keep each parameter's values inline, separated by a comma and a space.
The routine requires these ranges and the existing `dyk` and `dykn` ranges
before preparing source data. It preserves other
chart settings and surrounding text. Remove the obsolete Commons statistics
JSON comment during initialization; recurring updates no longer generate it.

### Pageviews data readiness

The routine checks that Wikimedia has published all 24 UTC hours of site-wide
human traffic, a positive daily total, the day's top list, and up to three
leading articles' daily observations. It then downloads fresh full histories.
Missing individual article records do not block a verified day at a fixed
percentage. A batch with no selected article's target-day observation is rejected.
Missing days try an earlier unpublished date; failed requests defer work.
Rejected batches leave reports and healthy caches unchanged; partial catch-up
continues hourly until current. Saved caches never substitute for fresh evidence.

After an accepted batch, gaps following an article's first observation may
contribute provisional zeroes; continuous leading absence stays missing.
Measured zeroes remain valid, HTTP failures are not zeroes, and averages include
calendar days. Gaps stay absent in raw histories and do not trigger same-day
retries or rewrite published reports. Existing `missing-percent` attributes are
preserved but do not override whole-day readiness for normal report runs.
See [Pageviews contracts](architecture.md#pageviews) for comparison and ranking rules.

## Website refresh and retained data

Content reviews refresh one hour after their last successful collection;
the other website datasets default to 24 hours.
Failures retain prior data and retry hourly; valid empty results count as refreshed.
Restarts preserve timestamps; page requests/status heartbeats never collect sources.

Maintainers can set `--reviews-interval-hours`, `--recognized-interval-hours`,
`--dyk-interval-hours`, `--pageviews-interval-hours`,
`--news-history-interval-hours`, and
`--backlog-interval-hours` independently. Reusable results in `reports/` survive
unsuccessful wiki saves; storage failure prevents publication. Retention and
cleanup belong to [Administration](administration.md#recovery-and-cleanup).

The content-review section precedes current highlights and lists FAC, FLC,
ACC, GAN, BPAN, PR, PPR, DYKC, then reassessment requests. Each type lists
oldest starts first, followed by undated requests. Community reviews come
from nomination pages even without talk-page candidate banners; project
reviews come from their talk categories. Cards resolve article-title variants
and redirects while keeping the original discussion anchor. A category-only
request has no start date until a relevant dated discussion exists.

Hourly refreshes check source revisions in batches and reuse unchanged
parses from `review-sources-v1.parquet`. Introductions share the existing
daily article-summary cache across review cards and highlights.
