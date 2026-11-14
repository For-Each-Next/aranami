# Operating Aranami on Toolforge

Use this guide to deploy a verified source generation, replace services safely
and recover without losing data. Aranami's runtime is exclusively Toolforge;
local machines prepare source and run offline tests. [Usage](usage.md) covers
report commands, and [Development](development.md) covers verification.

## Maintain on Toolforge

Use existing maintainer access and the tool account:

```shell
ssh <maintainer>@login.toolforge.org
become aranami
cd /data/project/aranami
```

The portable tool path can resolve to Toolforge's physical NFS home. Keep
credentials, `.my.cnf`, Pywikibot configuration and OAuth secrets outside source.
Do not print account dumps. Preserve platform-managed credential permissions.
The launchers pin the selected source and its Python 3.15 environment for each
process lifetime, including manual jobs.

## Runtime folder architecture

| Path relative to tool home | Purpose |
|---|---|
| `app` | Atomic symlink selecting an immutable uploaded generation |
| `app-staging-<id>/` | Source payload; its path remains immutable after activation |
| `deployment-envs/source-<id>/` | Locked Python environment for that generation |
| `deployment-backups/source-<id>/` | Validation receipt, former selection and saved job settings |
| `deployment.lock/` | Promotion serialization; do not remove while setup/promotion is active |
| `python/`, `uv-bootstrap/` | Managed interpreter and setup tooling |
| `data/archive/<day>.parquet`, `data/raw/<project>-<day>.parquet` | Immutable full daily project observations |
| `data/raw/pageviews-*--*` | Latest/previous detailed traffic generations and coverage |
| `data/parsed/` | Durable history, authorship, milestones and scope evidence |
| `data/parsed/newsletter--*` | Current and original prepared newsletter facts |
| `data/parsed/` | Prepared report bundles and independent dated newsletter archives |
| `data/parsed/assessments--*`, `backups/report-layout/` | Retained legacy observations and recovery originals |
| `data/revisions/00/` through `FF/` | Revision text and metadata, sharded by revision ID |
| `data/webservice/` | Pre-rendered quality-analysis presets for direct HTTP delivery |
| `state/observability/status.json` | Versioned service, execution, dataset and publication observations |
| `state/` | Completion checkpoints, publication membership and active execution locks |
| `cache/` | Disposable source and presentation optimizations |
| `logs/activity/` | Versioned UTC activity segments correlated by service/run/attempt |
| `logs/diagnostics/` | Readable UTC diagnostic segments with bounded tracebacks |
| `dry-run/` | Isolated preview source data, results and proposed edits |

Daily captures use the preceding completed UTC date in their filenames. New dates
require fresh complete queries. The [retirement checklist](transition.md)
tracks historical-source dependencies and verification before removal.
The scheduling boundary and actual query time remain separately recorded.

## Validate and upload source

Complete the [offline checks](development.md#verification-and-releases) on the
development computer:

```shell
uv sync --locked
uv run python scripts/upload_toolforge.py --dry-run
```

The validator checks source inventory, version and content hashes, rejecting
symlinks and private/runtime files. It packages authored Django templates,
styles and browser scripts directly under `src/webservice/`; no frontend build
or Node dependencies are needed. `--archive <new-file.tar.gz>` optionally retains
the same reproducible compressed payload without network access.
Packaging records a UTC build time in `deployment.json`; repackaging preserves
it and the About page displays it independently of process restarts. An
unpackaged checkout or an older source generation without that field omits it.

Upload the verified payload:

```shell
uv run python scripts/upload_toolforge.py
```

Retain its version, manifest digest, unique staging path and printed promotion
command. Uploading does not select the source or restart jobs.
`TOOLFORGE_SSH_TARGET` overrides the configured maintainer;
`TOOLFORGE_KNOWN_HOSTS` can select an existing verified host file. Keep host
verification enabled. The user-selected 0.9.2 source consolidates the earlier
unpublished alpha development. A deployment remains identified by its version
and exact content digest, independently of release tags.

## Promote the uploaded generation

Run the exact uploader-provided command as the tool account:

```shell
sh /data/project/aranami/app-staging-<id>/scripts/toolforge/deploy-source.sh /data/project/aranami/app-staging-<id>
```

Promotion verifies the payload, builds an isolated locked environment, and runs
setup/import validation in a one-off Toolforge job. A matching success receipt
is mandatory. Setup failure leaves existing services running.

After successful setup, the current hub receives a cooperative SIGUSR1 drain
request. The process stops scheduling and confirms its active jobs have finished
before Kubernetes termination begins. The script atomically selects the new
source, replaces the webservice first, then terminates the old hub pods and
restores its saved replica count. It never overlaps publishing hubs. Missing or
deliberately paused services remain stopped. Job definitions, routes, credentials,
reports, logs and state survive. Requests may be briefly interrupted.

Every retained process stays pinned to its own source/environment. Do not edit
selected generations or reuse upload tokens. Keep a validated recovery generation
and every generation still used by an active process.

## Start the applications

Only for initial installation or deliberately missing definitions:

```shell
toolforge jobs run aranami-hub --image python3.13 --continuous --mount=all --mem 4Gi --command 'exec sh /data/project/aranami/app/scripts/toolforge/run-hub.sh'
toolforge jobs run aranami-web --image python3.13 --continuous --mount=all --port 8000 --publish --command 'exec sh /data/project/aranami/app/scripts/toolforge/run-web.sh'
```

The image label is distinct from the managed Python 3.15 interpreter. Keep one
hub, preserve `exec` signal handling, and inspect resource use before resizing.
Normal startup recovers due work; `--no-run-immediately` waits for triggers.
Daily captures never backfill absent historical dates from current queries.
The web job reads saved data even while the hub is stopped; debugging/reload
remain disabled.

## Inspect operation

```shell
toolforge jobs list
toolforge jobs show aranami-hub
toolforge jobs logs aranami-hub
toolforge jobs logs aranami-web
```

Confirm both services, one hub process, the public website, API configuration,
version observations and expected UTC schedules. Verify complete adjacent
project files and their manifests before expecting daily comparisons.
Compare `/api/config` with the promoted version after each rollout: ready pods
alone do not prove which source they selected. If the web process selected the
previous generation, verify that the new `app` selector is visible inside its
pod, restart `aranami-web`, and repeat the version check. Inspect the hub's
source identity independently and use the normal drain procedure for its
recovery. Keep the prior generation until these checks pass.
Detailed Pageviews is written only after an accepted traffic acquisition.
Diagnostic Python processes must set `PYTHONPYCACHEPREFIX` to the tool's
`cache/pycache` directory, or set `PYTHONDONTWRITEBYTECODE=1`, before importing
selected source. `kubectl exec` starts a fresh environment and does not inherit
variables exported by the running launcher. Generated bytecode inside a source
generation fails later deployment validation; keep all diagnostic caches outside
the immutable generation.

The hub's local read-only diagnostics are `/healthz` and `/dashboard.json` at
`127.0.0.1:8765`. Public Reports reads saved execution evidence, not log guesses.
Check service heartbeat age, task outcome, accepted dataset coverage and each
destination's publication result separately. A failed refresh can leave useful
older data available; a successful collection does not establish a wiki save.
The log streams retain 30 UTC dates within a combined 512 MiB ceiling. Segment
rotation prevents a busy day from silently stopping activity recording.

## Stop or restart a service

Restart the website with `toolforge jobs restart aranami-web`. For the hub,
drain and stop the current process before restoring its replica:

```shell
kubectl exec deployment/aranami-hub -- python3 /data/project/aranami/app/scripts/toolforge/drain-hub.py
kubectl scale deployment aranami-hub --current-replicas=1 --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/name=aranami-hub --timeout=180s
kubectl get pods -l app.kubernetes.io/name=aranami-hub
kubectl scale deployment aranami-hub --current-replicas=0 --replicas=1
kubectl rollout status deployment/aranami-hub --timeout=180s
```

Proceed only after each command succeeds; confirm the pod listing is empty
before restoring the replica. Leave zero replicas to pause deliberately.
SIGTERM/SIGINT also pause dispatch and drain jobs, but forced termination may
leave incomplete observations. Use source promotion for code changes, and change
job definitions only when their image/command/resources/route must change.

## Recovery and cleanup

A promotion transaction retains `jobs.yaml`, setup evidence, previous source
selection in `app.before`, saved replica counts and stop markers. Interpret
`app.before` relative to tool home. If setup cancellation cannot confirm the
job exited, inspect the retained deployment lock before retrying.

A failure after source selection retains the new selection and both generations:
a process may already have loaded the new source. Inspect actual jobs/pods,
receipts and logs before further replacement. A shutdown timeout must never
start a second publisher. To select a retained generation, acquire
`deployment.lock/`, then invoke its validated selector:

```shell
python3 /data/project/aranami/app-staging-<id>/scripts/toolforge/validate-source.py select /data/project/aranami/app-staging-<id>
```

Restart services through the safe sequence and release the lock after recovery.
Source selection does not undo runtime data or wiki edits. Remove only exact
obsolete paths after verifying no running process uses them. Never clear daily
project files, detailed traffic, newsletter archives or article evidence as
caches. Alpha suspends new automatic evidence purges; 2026 facts remain needed
by newsletters into 2027 and by delayed issues afterward. Legacy retirement
requires verified replacements and zero remaining consumers, not a date alone.
