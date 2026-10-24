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
viewer shows concise lifecycle events; downloads include full diagnostics.
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

## Routine names and UTC triggers

Use these identifiers in `tasks`:

| Routine | Result | UTC trigger |
|---|---|---|
| `dyks` | Completed DYK entries, nominees, and statistics | Every hour at `:00` |
| `new_pages` | Daily keyword-matched new/conversion entries | Daily at `00:07` |
| `assessment_lists` | Project assessment and review lists | Every hour at `:31` |
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
  --command 'env PYTHONPATH=/data/project/aranami/app/src PYWIKIBOT_DIR=/data/project/aranami PYTHONPYCACHEPREFIX=/data/project/aranami/cache/pycache /data/project/aranami/pyvenv-3.15/bin/python -c "from backend import run_once; run_once(dry=True, tasks=[\"new_pages\", \"pageviews\"])"'
toolforge jobs logs aranami-preview
```

`dry=True` suppresses wiki saves and PexBot requests. It writes Markdown summaries,
flat `.wikitext` proposals, and isolated bundles under `dry-run/reports/`, without
advancing production datasets, completion evidence, membership, or status.
After completion, inspect the artifacts and delete the definition before reuse:

```shell
ls -lt dry-run/
toolforge jobs delete aranami-preview
```

## Run an immediate update

This example performs live publication; run it only while that routine is idle:

```shell
toolforge jobs run aranami-manual --image python3.13 --mount=all \
  --command 'env PYTHONPATH=/data/project/aranami/app/src PYWIKIBOT_DIR=/data/project/aranami PYTHONPYCACHEPREFIX=/data/project/aranami/cache/pycache /data/project/aranami/pyvenv-3.15/bin/python -c "from backend import run_once; run_once(dry=False, tasks=[\"assessment_lists\"])"'
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
```

Missing, malformed, duplicate, reversed, or ambiguous required markers prevent
publication. Keep labeled-section tags outside them. Pageviews retains its
`<!-- report date YYYY-MM-DD -->` checkpoint. See the
[managed-range contracts](architecture.md#managed-ranges-and-on-wiki-configuration)
for settings; changing targets, schedules, or definitions requires deployment.

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

Each website dataset defaults to 24 hours after its last successful refresh.
Failures retain prior data and retry hourly; valid empty results count as refreshed.
Restarts preserve timestamps; page requests/status heartbeats never collect sources.

Maintainers can set `--recognized-interval-hours`, `--dyk-interval-hours`,
`--pageviews-interval-hours`, `--news-history-interval-hours`, and
`--backlog-interval-hours` independently. Reusable results in `reports/` survive
unsuccessful wiki saves; storage failure prevents publication. Retention and
cleanup belong to [Administration](administration.md#recovery-and-cleanup).
