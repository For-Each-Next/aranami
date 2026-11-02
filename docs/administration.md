# Aranami administration

Use this guide to deploy source on Toolforge, operate the hub and website,
and recover a failed replacement. For reports and previews, see
[Usage](usage.md); for local checks, see [Development](development.md).

## Deployment workflow

1. Complete [offline verification](development.md#verification-and-releases),
   then [validate and upload](#validate-and-upload-source) the complete payload.
2. [Connect as the tool account](#maintain-on-toolforge) and
   [promote the uploaded generation](#promote-the-uploaded-generation).
3. [Inspect operation](#inspect-operation). Use the
   [start commands](#start-the-applications) only for the first installation
   or deliberately missing services.

Normal updates replace application code and restart existing services; they
do not restart the host or delete and recreate all jobs. Uploads and dependency
preparation leave existing services running. The website is replaced first,
then the hub, while their job settings and published route remain configured.
Hub replacement resumes live publication; plan around active work. A service
that is absent or scaled to zero stays stopped.

## Maintain on Toolforge

Prepare source on the development computer. Run deployment and job commands
on the Toolforge bastion using existing maintainer access:

```shell
ssh <maintainer>@login.toolforge.org
become aranami
cd /data/project/aranami
```

The portable tool-home path above aliases
`/mnt/nfs/labstore-secondary-tools-project/aranami`; Toolforge may expose the
physical path as `HOME`. Launchers validate the selected generation and pin
`PYTHONPATH` and Python to its immutable source and environment. Runtime files
and bytecode stay outside the uploaded source.

Keep credentials, `.my.cnf`, Pywikibot configuration, and OAuth secrets outside
source and shared diagnostics. Inspect selected non-secret files, not account
dumps. Use owner-only credential directories (`700`, or `2700` with inherited
groups), `600` for writable private configuration, and `400` for read-only keys.
Preserve platform-managed `.my.cnf` permissions, which may be `440`.

## Runtime folder architecture

Paths below are relative to `/data/project/aranami`:

| Location | Purpose |
|---|---|
| `app` | Atomically replaced symlink selecting an immutable source generation |
| `app-staging-<id>/` | Uploaded source, retained at this path even after activation |
| `deployment-backups/source-<id>/` | Setup receipt, saved job settings, and previous source selection |
| `deployment-envs/source-<id>/` | Isolated locked Python environment for that generation |
| `deployment.lock/` | Concurrent-deployment guard; does not block service startup |
| `python/`, `uv-bootstrap/` | Managed Python 3.15.0 and setup tooling |
| `reports/data/` | Collected facts, current tables, and daily `YYYYMMDD` observations |
| `reports/records/` | Generated reports, publication metadata, and saved newsletter drafts |
| `reports/data/assessments/<wiki>/<project>/assessments-YYYYMMDD.parquet` | Immutable completed-day page identities, namespaces, redirect flags, quality, and importance |
| `reports/records/newsletter/` | Immutable daily newsletter facts, original editorial layouts, and date catalogs |
| `reports/data/articles/milestones.parquet` | Shared project review and homepage actions, roles, questions, and coverage |
| `reports/data/articles/history.parquet` | Durable versioned talk snapshots and recorded events |
| `reports/data/articles/authorship.parquet` | Durable listing-time contributor evidence and token statistics |
| `reports/data/articles/scope.parquet` | Confirmed project presence, 365-day absence clocks, and removal tombstones |
| `reports/data/newsletter/observations/` | Current prepared facts and immutable date-folder JSON observations |
| `backups/report-layout/` | Preserved originals from retired report layouts |
| `state/` | Routine, schedule, and per-destination observations |
| `cache/` | Disposable source data, completion evidence, bytecode, and website snapshots |
| `logs/` | UTC diagnostic logs and separate major-event records, retained for 30 dates |
| `dry-run/` | Preview summaries, exact proposals, and isolated result bundles |

Keep credentials, reports, state, and logs during replacement. Retain a
verified recovery generation with its own validator, receipt, and environment.
Clearing disposable caches forces later collection/revalidation;
it is not a normal deployment step. Inspect saved observations through the
[terminal workflow](usage.md#inspect-results-and-logs).

Parquet caches use stable filenames with embedded contracts. Earlier filenames
with schema suffixes are no longer read; compatible current files are reused
and incompatible files rebuild. Remove obsolete cache files only after active
work stops. See [Disposable caches](backend/caching.md) for the boundary
between disposable files and durable evidence.

See the [newsletter storage table](backend/newsletter.md#data-cache-and-archive-lifecycle)
and [Saved data and Parquet fields](backend/data-storage.md) for retained data.

PexBot action bundles keep only the latest disposable record per report under
`cache/pexbot/`, with isolated previews under `dry-run/cache/pexbot/`.
Temporary files are removed after use. The cached records retain request outcomes
for inspection; the live wiki history determines whether a refresh is due.
Clearing these caches does not remove routine or report status under `state/`.
Delegated requests continue to report an unknown edit count.

Both jobs use Toolforge's `python3.13` image to launch the managed Python
3.15.0 environment. Keep the image and application interpreter distinct.
The Uvicorn web job serves the FastAPI application, WebSocket status updates,
and assets from its pinned source generation. Debugging and automatic
reloading remain disabled. Replace the process after source changes so the
application, templates, and browser assets use the same deployment.

## Validate and upload source

On the development computer, install the locked environment and finish the
[offline checks](development.md#verification-and-releases):

```shell
uv sync --locked
npm ci --ignore-scripts
npm run build:web
uv run python scripts/upload_toolforge.py --dry-run
```

The dry run validates the exact source payload and generated version/hash
metadata without contacting Toolforge. It rejects symlinks and missing required
files, and excludes credentials, runtime data, and tests. Website files come
from `dist/webservice/`; its build receipt must match all current authored
inputs and output bytes. Missing, stale, or modified builds require
`npm run build:web` before upload. The archive retains the established
`src/webservice/` runtime layout and contains no frontend compiler or Node
dependencies. Optional
`--archive <new-file.tar.gz>` writes a reproducible gzip-compressed tar to a
new archive path. Uploads stream the same compressed format to Toolforge.

Upload without `--dry-run`:

```shell
uv run python scripts/upload_toolforge.py
```

Retain the printed staging path, digest, and promotion command. The unique
`app-staging-<id>/` upload does not change active jobs.
`TOOLFORGE_SSH_TARGET` overrides the maintainer login;
`TOOLFORGE_KNOWN_HOSTS` selects an existing verified known-hosts file.
Keep SSH host verification enabled.

## Promote the uploaded generation

As the tool account, use the exact command printed by the uploader:

```shell
sh /data/project/aranami/app-staging-<id>/scripts/toolforge/deploy-source.sh /data/project/aranami/app-staging-<id>
```

Promotion verifies source hashes, prepares a new isolated locked environment,
and validates configuration/imports in a one-off Toolforge job. It requires a
success receipt for that exact source/environment generation; job completion
alone is not proof.

After setup succeeds, promotion first asks the current hub to drain through
its pod-local read-only health protocol. The helper sends SIGUSR1, waits for
the same process to confirm every job finished, and only then allows pod
termination. Kubernetes' termination grace countdown starts after this wait.
The hub stays alive without scheduling work while the website is replaced.
Promotion then atomically switches `app` to the uploaded directory. Existing
processes keep their original code, templates, assets, and
Python environment until replaced. The website uses `toolforge jobs restart`;
promotion waits for its previous pods to disappear and the deployment to become
available. The hub is scaled to zero, its pods must finish terminating, and its
saved replica count is restored. This keeps exactly one publishing process.
Job definitions, routing, credentials, reports, state, caches, and logs survive.
Service replacement may briefly interrupt requests; this is not a guarantee
of zero downtime. Toolforge manages the host and container lifecycle.

Promotion requires `app` to select an immutable generation and the running hub
to advertise cooperative drain protocol 1. Each retained generation validates
its own payload and matching setup receipt; historical version exceptions and
operator idle overrides are not supported. Do not edit a selected generation
or reuse its upload token; prepare another upload instead.

If source selection fails after a confirmed drain, promotion restarts the
old source through a confirmed stop/start to resume scheduling. If recovery
also fails, inspect its health and saved deployment markers before retrying.

## Start the applications

For a first installation or a missing job definition, run as the tool account:

```shell
toolforge jobs run aranami-hub --image python3.13 --continuous --mount=all --mem 4Gi --command 'exec sh /data/project/aranami/app/scripts/toolforge/run-hub.sh'
toolforge jobs run aranami-web --image python3.13 --continuous --mount=all --port 8000 --publish --command 'exec sh /data/project/aranami/app/scripts/toolforge/run-web.sh'
```

Keep exactly one continuous hub and retain `exec` so Python receives stop
signals. The hub normally checks due work immediately, then follows the
[UTC triggers](usage.md#routine-names-and-utc-triggers). Use
`--no-run-immediately` only to wait for scheduled callbacks. Saved completion
evidence and website refresh times survive restarts.

The hub needs 4 GiB for concurrent preparation and Pageviews catch-up; inspect
`kubectl top pods` and quota before resizing. The separate read-only web job
[publishes port 8000][toolforge-publish] and never collects or publishes data.

## Inspect operation

```shell
toolforge jobs list
toolforge jobs show aranami-hub
toolforge jobs logs aranami-hub
toolforge jobs logs aranami-web
```

After replacement, confirm both jobs are running, no duplicate workers exist,
and the [website](https://aranami.toolforge.org/) and
[logs](https://aranami.toolforge.org/logs/) are readable. Hub logs should show
due work or valid current-data skips. The About page shows saved environment
and resource observations; the Reports page monitors report execution.
Missing or stale observations stay explicit.
See [Usage](usage.md#inspect-results-and-logs) for status files and diagnostics.

The hub also serves local read-only `/healthz` and `/dashboard.json` at
`127.0.0.1:8765`. The independent web job remains available while the hub is down.

## Stop or restart a service

Restart the website without replacing source:

```shell
toolforge jobs restart aranami-web
```

SIGTERM and SIGINT pause new scheduled work, skip queued website refreshes,
and wait for active jobs to persist their final outcomes before releasing
output locks. Daily logs record the shutdown request, each active job result,
and confirmation that all jobs stopped safely. Allow this drain to finish
before starting another publisher; forced termination cannot guarantee a
completion observation.

Restart the hub without overlapping publishing processes. Run these commands
in order and proceed only when each succeeds. The first command waits for
active jobs before Kubernetes begins terminating the pod:

```shell
kubectl exec deployment/aranami-hub -- python3 /data/project/aranami/app/scripts/toolforge/drain-hub.py
kubectl scale deployment aranami-hub --current-replicas=1 --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/name=aranami-hub --timeout=180s
kubectl get pods -l app.kubernetes.io/name=aranami-hub
kubectl scale deployment aranami-hub --current-replicas=0 --replicas=1
kubectl rollout status deployment/aranami-hub --timeout=180s
```

Confirm the pod listing is empty before restoring the replica. Leave it at
zero to pause the hub deliberately. Normal startup behavior applies after a
restart; the website stays independent. Use the promotion script for code
updates. Delete and recreate a job only when changing its definition requires
it, such as image, command, resources, or published port. See the
[Toolforge jobs guide][toolforge-jobs] for job management.

## Recovery and cleanup

Promotion prints its transaction directory under `deployment-backups/`, with
`jobs.yaml`, the source/environment setup receipt, and saved replica counts.
`app.before` records the prior relative source link; interpret its target
relative to the tool home, not the backup directory. Existing generations and environments are never
overwritten or removed by promotion.

Setup failure leaves existing services running. If cancellation cannot confirm
that the setup job exited, the deployment lock remains for inspection.
Failure before selection resumes services whose shutdown was confirmed.
After selection, a restart failure retains the new selection and both source
generations: another process may already have loaded the new code. Inspect jobs,
pods, and logs before retrying service replacement. A shutdown timeout never
starts a second publisher. Replica counts and `.stopped` markers in the backup
help identify incomplete replacement, but inspect actual cluster state too.

To select a retained generation for recovery, acquire `deployment.lock/` with
`mkdir`, then use its validated selector (replace `<id>` with the exact retained
token):

```shell
python3 /data/project/aranami/app-staging-<id>/scripts/toolforge/validate-source.py select /data/project/aranami/app-staging-<id>
```

Restart existing services as above and remove the lock when recovery finishes.
Selection validates the retained source against its original setup receipt;
it does not undo runtime data or wiki edits.

Verify the new generation before removing recovery artifacts. Limit cleanup to
exact obsolete paths; preserve credentials, `reports/`, `state/`, `logs/`, the
active generation, every generation still used by a process (including manual
jobs), and a verified recovery generation with its receipt/environment.
Preserve `reports/data/articles/`, all prepared observation generations, and
any retained layout backups as durable evidence. Retired layout converters
are no longer shipped. Existing
`reports/records/newsletter/` stores complete daily JSON observations.
Queries render the selected facts with the current formatting while retaining
their original source facts and capture times.
Remove a stale `deployment.lock/` only after confirming no deployment/setup
process remains and validating the selected generation.

[toolforge-jobs]: https://wikitech.wikimedia.org/wiki/Help:Toolforge/Running_jobs
[toolforge-publish]: https://wikitech.wikimedia.org/wiki/Help:Toolforge/Running_jobs#Exposing_continuous_jobs_to_the_internet
