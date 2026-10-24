# Aranami terminal usage

Usage (this guide) is for terminal users inspecting Aranami's operation,
running a selected routine, or previewing an update. Aranami maintains wiki reports
and a read-only website from one backend hub on Toolforge. Local development
and verification remain offline.

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
The website shows routine outcomes and actual scheduled next executions,
hiding the next-execution row while a routine runs. Its execution timestamp
uses the start while running and the finish after completion. Missing, stale, paused,
or stopped scheduling metadata remains explicit.
Running cards display available progress supplied by the backend, such as the
current report date or processed article count. Live status synchronizes every
five seconds while visible; running times advance locally every second.

All routines append readable diagnostics to one UTC file per day:

```shell
ls -1 logs/aranami.*.log
tail -n 100 logs/aranami.YYYY-MM-DD.log
```

Replace `YYYY-MM-DD` with the required UTC date. The current date and the
previous 29 UTC calendar dates are retained. Messages describe the data being
prepared, the page being edited, counts, completion, skips, and failures.
The public log viewer shows concise starts, results, and server lifecycle
events, omitting per-article fetch failures and other diagnostic noise.
It also shows compact server state and uptime from saved schedule observations,
alongside the source version, with the same live synchronization. Daily downloads
retain the full diagnostics for troubleshooting.

Backend metadata is separate:

| File | Observation |
|---|---|
| `state/routine-status.json` | Actual routine starts/results, optional active progress, measured duration, and confirmed save counts |
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
| `pageviews` | Separate daily popularity rankings for all missing eligible dates | Every hour at `:59` while behind/deferred |
| `enwp_key_articles` | Current English importance and quality comparisons | Daily at `23:24` |
| `pexbot` | Refresh requests for subscribed PexBot reports | Daily at `00:00` |

These are dispatch times, not a promise that each trigger creates an edit.
Unchanged outputs are not saved. English comparisons and PexBot describe
current state on their daily refreshes, rather than a historical daily
window. Website datasets have independent refresh intervals.

New pages retains 100 complete UTC dates through yesterday by default.
Once the whole retained window is verified complete, automatic work waits
for the next daily trigger.

Pageviews saves each missing day's report in order through the latest eligible
ready day. For example, a report on the 15th catches up with separate saves
for the 16th, 17th, and 18th; if only data through the 17th passes validation,
it saves the 16th and 17th and retries the 18th later. Each report keeps its
actual data date. The [Pageviews settings](#pageviews-data-readiness) below
describe readiness checks, zero-filled gaps, and retries.
Yesterday becomes eligible at the actual 18:00 UTC readiness boundary;
the hourly schedule first attempts that day at 18:59. Once current, the
routine waits for 18:59 following the next readiness boundary.
Failed saves and previews do not establish completion. Missing or invalid
disposable evidence causes on-wiki revalidation. Manual runs always
revalidate and cannot bypass data readiness with a chosen date.

## Preview selected routines

Submit real previews as one-off Toolforge jobs. The existing runtime supplies
authentication and the locked Python 3.15 environment. From the bastion as
the tool account:

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

Keep the job active until preview production finishes, then delete its
definition before reusing the name. A PexBot preview
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
intended and the same routine is idle; ask the maintainer to arrange a
maintenance window when needed. Inspect the result and delete the finished
`aranami-manual` definition before
reusing its name.

The API is `run_once(date=None, dry=False, tasks=None,
clear_cache=False)`. `tasks=None` selects all routines. The optional date
sets the report anchor, not the wall clock or Pageviews readiness.
`clear_cache=True` clears disposable source caches and should be reserved
for deliberate maintainer-led revalidation. A one-shot pass does not
start, stop, or rewrite recurring schedules.

Use the one-shot API for an immediate update. The single continuous service
runs `run-hub.sh`, which starts `python -m backend`.
Its default startup checks due work immediately. `--no-run-immediately`
waits for scheduled callbacks; `--dry` runs recurring previews without
production website collection or HTTP serving. Imports and `--help` do
not execute reports.

PexBot actions are limited to Chinese Wikipedia and configured
slash-delimited report roots; an empty root list disables selection.
Reports already edited by PexBot during the actual current UTC day are
skipped. A delegated response does not prove a confirmed local edit count.
After a live refresh request, a fifteen-second cooldown precedes the next
report attempt, including its history check. A skipped report consumes that
pending cooldown but does not schedule another wait. Card timing covers the
whole routine; popup rows measure each report's own attempt without prior
reports or cooldowns.

## Managed report settings

On-wiki opening comments can supply supported settings. Preserve the
paired markers and text outside them:

```wikitext
<!-- aranami begin="new-pages" days="100" -->
...retained daily records...
<!-- aranami end="new-pages" -->

<!-- aranami begin="page_views" history-days="800" missing-percent="1" -->
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
Follow [Development](development.md) and the validated deployment workflow.

### Pageviews data readiness

Pageviews first checks the candidate day's top list and up to three leading
articles' single-day records. An unavailable date tries an earlier unpublished
endpoint before full downloads; a transport failure defers the invocation.
Once readiness passes, each Pageviews run or earlier-endpoint retry downloads
full article histories again; catch-up dates share only the successful batch's fresh
responses. A download stops immediately when more than 1% of selected
articles lack its final day's daily record, then retries the previous
unpublished endpoint. The full-history coverage check remains required
after probes.
Failed attempts leave the report and healthy cache unchanged. A shorter
successful catch-up does not mark the routine current; hourly retries
continue for the remaining dates. Exactly 1% missing is allowed and measured
zeroes are valid.
After a batch passes, missing records in successful histories contribute zero
to report calculations. Daily averages include every calendar day in the period.
Saved rankings distinguish provisional zeroes from actual observations; raw
cached histories keep the gaps absent. Normal downloads for later report dates
can fill those gaps and incorporate recovered counts in future reports and
comparisons. Gaps alone do not trigger same-day retries or rewrite published
historical reports. HTTP failures are not zeroes.
Historical gaps are summarized at batch level. The maximum effective
`missing-percent` is 1, including when a stored attribute is larger.

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
historical discovery. Treat result manifests, state snapshots, and cache files
as backend-owned observations and inspect them read-only. Runtime folder
retention and safe cleanup are covered by [Administration](administration.md).
