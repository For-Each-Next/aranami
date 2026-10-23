# Aranami terminal usage

Usage (this guide) is for terminal users inspecting Aranami's operation,
running a selected routine, or previewing an update. Aranami maintains wiki reports
and a read-only website from one backend hub on Toolforge. It does not run
as an application on the development computer.

Use the existing Toolforge tool-account environment. For account access,
source uploads, starting/stopping services, and recovery, see the
[Administration](administration.md). Application contribution is in
[Development](development.md); system ownership and contracts
are in [Architecture](architecture.md).

## Inspect results and logs

As the `aranami` tool account on the Toolforge bastion:

```shell
cd /data/project/aranami
toolforge jobs list
toolforge jobs show aranami-hub
toolforge jobs logs aranami-hub
toolforge jobs logs aranami-web
```

`aranami-hub` prepares data and publishes wiki reports. `aranami-web`
serves the [website](https://aranami.toolforge.org/) and saved data,
including the [daily-log viewer](https://aranami.toolforge.org/logs/).
The website shows routine outcomes and actual scheduled next executions;
missing, stale, paused, or stopped scheduling metadata remains explicit.

All routines append readable diagnostics to one UTC file per day:

```shell
ls -1 logs/aranami.*.log
tail -n 100 logs/aranami.YYYY-MM-DD.log
```

Replace `YYYY-MM-DD` with the required UTC date. The current date and the
latest 90 completed dates are retained. Messages describe the data being
prepared, the page being edited, counts, completion, skips, and failures.
They are not a machine-readable status database.

Backend metadata is separate:

| File | Observation |
|---|---|
| `state/routine-status.json` | Actual routine starts/results, measured duration, and confirmed save counts |
| `state/schedule-status.json` | Monitor's actual next executions and running/paused/stopped state |
| `state/report-updates.json` | Per-destination update times, duration, outcome, and confirmed edits |

Inspect these files read-only when diagnosing a displayed status. An
unknown delegated edit count is not zero. Preview work does not update
live status, and automatic current-data skips do not invent new outcome
timestamps.

## Routine names and UTC triggers

Use the routine identifiers below with the `tasks` argument:

| Routine | Result | UTC trigger |
|---|---|---|
| `dyks` | Completed DYK entries, active nominees, and statistics | Every hour at `:00` |
| `new_pages` | Retained daily keyword-matched new/conversion entries | Daily at `00:07` |
| `assessment_lists` | Project assessment and review lists | Every hour at `:31` |
| `pageviews` | Popularity rankings for the next eligible report date | Every hour at `:59` while behind/deferred |
| `enwp_key_articles` | Current English importance and quality comparisons | Daily at `23:24` |
| `pexbot` | Refresh requests for subscribed PexBot reports | Daily at `00:00` |

These are dispatch times, not a promise that each trigger creates an edit.
Unchanged outputs are not saved. English comparisons and PexBot describe
current state on their daily refreshes, rather than a historical daily
window. Website datasets have independent refresh intervals.

New pages retains 100 complete UTC dates through yesterday by default.
Once the whole retained window is verified complete, automatic work waits
for the next daily trigger. Pageviews processes at most one missing report
date per call. Yesterday becomes eligible at the actual 18:00 UTC
readiness boundary; its hourly schedule first attempts that day at 18:59.
Once current, it waits for 18:59 following the next readiness boundary.
Failed saves and previews do not establish completion. Missing or invalid
disposable evidence causes on-wiki revalidation. Manual runs always
revalidate and cannot bypass data readiness with a chosen date.

## Preview selected routines

Submit real previews as one-off Toolforge jobs, not from the development
computer. The existing runtime supplies authentication and the locked
Python 3.15 environment. From the bastion as the tool account:

```shell
toolforge jobs run aranami-preview --image python3.13 --mount=all \
  --command 'env PYTHONPATH=/data/project/aranami/app/src PYWIKIBOT_DIR=/data/project/aranami PYTHONPYCACHEPREFIX=/data/project/aranami/cache/pycache /data/project/aranami/pyvenv-3.15/bin/python -c "from backend import run_once; run_once(dry=True, tasks=[\"new_pages\", \"pageviews\"])"'
toolforge jobs logs aranami-preview
```

The job runs from the tool home. `dry=True` suppresses wiki saves and
delegated PexBot requests. It writes one compact Markdown summary per run,
flat `.wikitext` companions containing exact proposals, and complete
isolated bundles under `dry-run/reports/`. It does not advance production
datasets, publication membership, completion evidence, or live status.

After the job finishes, inspect its logs and preview artifacts:

```shell
ls -lt dry-run/
toolforge jobs delete aranami-preview
```

Delete only the finished job definition before reusing that name. Do not
delete the job while it is still producing the preview. A PexBot preview
describes the proposed refresh action without calling the external writer.

## Run an immediate update

The same one-shot API accepts `dry=False` for live publication. For
example, submit one assessment pass:

```shell
toolforge jobs run aranami-manual --image python3.13 --mount=all \
  --command 'env PYTHONPATH=/data/project/aranami/app/src PYWIKIBOT_DIR=/data/project/aranami PYTHONPYCACHEPREFIX=/data/project/aranami/cache/pycache /data/project/aranami/pyvenv-3.15/bin/python -c "from backend import run_once; run_once(dry=False, tasks=[\"assessment_lists\"])"'
toolforge jobs logs aranami-manual
```

This command can edit wiki pages. Use it only when a live update is
intended. Avoid overlapping an automatic invocation of the same routine;
ask the maintainer to arrange a maintenance window when needed. Inspect
the result and delete the finished `aranami-manual` definition before
reusing its name.

The API is `run_once(date=None, dry=False, tasks=None,
clear_cache=False)`. `tasks=None` selects all routines. The optional date
sets the report anchor, not the wall clock or Pageviews readiness.
`clear_cache=True` clears disposable source caches and should be reserved
for deliberate maintainer-led revalidation. A one-shot pass does not
start, stop, or rewrite recurring schedules.

Do not create another continuous hub to request one immediate update.
The normal service runs `run-hub.sh`, which starts `python -m backend`.
Its default startup checks due work immediately. `--no-run-immediately`
waits for scheduled callbacks; `--dry` runs recurring previews without
production website collection or HTTP serving. Imports and `--help` do
not execute reports.

PexBot actions are limited to Chinese Wikipedia and configured
slash-delimited report roots; an empty root list disables selection.
Reports already edited by PexBot during the actual current UTC day are
skipped. A delegated response does not prove a confirmed local edit count.

## Managed report settings

On-wiki opening comments can supply supported settings. Preserve the
paired markers and text outside them:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...retained daily records...
<!-- aranami end="new-pages" -->

<!-- aranami begin="page_views" history-days="800" missing-percent="95" -->
...popularity report...
<!-- aranami end="page_views" -->
```

Malformed, missing, reversed, duplicate, or ambiguous required markers
fail before publication. Keep labeled-section transclusion tags outside
the managed ranges. The Pageviews checkpoint remains
`<!-- report date YYYY-MM-DD -->`. Source categories for assessment lists
also use supported opening-marker settings. Backend services validate and
interpret settings; the updater only renders within the managed ranges.

Changing target titles, UTC schedules, subscription roots, or maintenance
catalogue definitions requires a source change and validated deployment.
See [Development](development.md) rather than editing deployed source
in place.

## Website refresh and retained data

The website displays saved recognized content, DYK, popularity,
news/history, and maintenance datasets. Each defaults to 24 hours after
its own successful refresh. A restart reuses validated saved results and
timestamps. Failures retain the previous successful data and retry hourly;
a valid empty result counts as refreshed. Neither page requests nor
status heartbeats collect wiki data.

The hub supports independent `--recognized-interval-hours`,
`--dyk-interval-hours`, `--pageviews-interval-hours`,
`--news-history-interval-hours`, and `--backlog-interval-hours` options.
Ask the maintainer to change the service command when these intervals need
adjustment; changing one does not reset the others.

Complete reusable wiki results live in `reports/`, independently of
publication success. A storage failure prevents publication, while an
unsuccessful wiki save retains the calculated result. New-page historical
initialization preserves saved records and unknown facts without repeating
historical discovery. Do not modify result manifests, state snapshots, or
cache files to force a status. Runtime folder retention and safe cleanup
are covered by [Administration](administration.md).
