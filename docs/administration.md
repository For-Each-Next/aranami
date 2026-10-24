# Aranami administration

Use this guide to deploy source on Toolforge, operate the hub and website,
and recover a failed replacement. For reports and previews, see
[Usage](usage.md); for local checks, see [Development](development.md).

## Deployment workflow

1. Complete [offline verification](development.md#verification-and-releases),
   then [validate and upload](#validate-and-upload-source) the complete payload.
2. [Connect as the tool account](#maintain-on-toolforge) and
   [promote the uploaded generation](#promote-the-uploaded-generation).
3. [Start both applications](#start-the-applications) and
   [inspect operation](#inspect-operation).

Uploads and dependency preparation leave existing jobs running. Activation
stops both jobs before replacing their shared source and environment; plan
around active work. Hub startup enables live wiki publication.

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
physical path as `HOME`. Launchers validate the deployment, use `app/src`
on `PYTHONPATH`, and keep runtime files and bytecode outside `app/`.

Keep credentials, `.my.cnf`, Pywikibot configuration, and OAuth secrets outside
source and shared diagnostics. Inspect selected non-secret files, not account
dumps. Use owner-only credential directories (`700`, or `2700` with inherited
groups), `600` for writable private configuration, and `400` for read-only keys.
Preserve platform-managed `.my.cnf` permissions, which may be `440`.

## Runtime folder architecture

Paths below are relative to `/data/project/aranami`:

| Location | Purpose |
|---|---|
| `app/`, `app-staging-<id>/` | Validated active source and uploaded candidate |
| `app.previous/`, `deployment-backups/` | Prior source and transaction recovery artifacts |
| `pyvenv-3.15`, `deployment-envs/` | Selected environment link and isolated locked environments |
| `active-deployment.json`, `deployment.lock/` | Source/environment receipt and deployment guard |
| `python/`, `uv-bootstrap/` | Managed Python 3.15.0 and setup tooling |
| `reports/` | Durable result bundles, manifests, Parquet tables, and wikitext |
| `state/` | Routine, schedule, and per-destination observations |
| `cache/` | Disposable source data, completion evidence, bytecode, and website snapshots |
| `logs/` | Current UTC date and previous 29 daily logs |
| `dry-run/` | Preview summaries, exact proposals, and isolated result bundles |

Preserve credentials, reports, state, and logs during replacement. Clearing
disposable caches forces later collection/revalidation; it is not a normal
deployment step. Inspect saved observations through the
[terminal workflow](usage.md#inspect-results-and-logs).

Both jobs use Toolforge's `python3.13` image to launch the managed Python
3.15.0 environment. Keep the image and application interpreter distinct.
The web job serves assets from `app/src/webservice/public_html/`; debugging
and automatic reloading remain disabled.

## Validate and upload source

On the development computer, install the locked environment and finish the
[offline checks](development.md#verification-and-releases):

```shell
uv sync --locked
uv run python scripts/upload_toolforge.py --dry-run
```

The dry run validates the exact source payload and generated version/hash
metadata without contacting Toolforge. It rejects symlinks and missing required
files, and excludes credentials, runtime data, and tests. Optional
`--archive <new-file.tar>` writes an inspected payload to a new archive path.

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

After setup succeeds, promotion stops the applications, waits for their pods
to disappear, and replaces the complete source, environment link, and active
receipt. It validates the result and leaves applications stopped. Keep active
source immutable; source metadata, environment, and receipt must match.

## Start the applications

Run as the tool account:

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
due work or valid current-data skips. The About page shows saved environment,
resource usage, and monitor state; missing or stale observations stay explicit.
See [Usage](usage.md#inspect-results-and-logs) for status files and diagnostics.

The hub also serves local read-only `/healthz` and `/dashboard.json` at
`127.0.0.1:8765`. The independent web job remains available while the hub is down.

## Stop or restart

Restart existing definitions without replacing source:

```shell
toolforge jobs restart aranami-hub
toolforge jobs restart aranami-web
```

Before changing active source, environments, or caches, stop both definitions
and wait until no matching pods remain:

```shell
toolforge jobs delete aranami-hub
toolforge jobs delete aranami-web
kubectl get pods -l 'app.kubernetes.io/name in (aranami-hub,aranami-web)'
```

Use the [start commands](#start-the-applications) to recreate them. Normal startup
behavior applies after a restart. See the [Toolforge jobs guide][toolforge-jobs]
for job management.

## Recovery and cleanup

Promotion prints its transaction directory under `deployment-backups/`, with
`jobs.yaml`, setup proof, and prior environment references. Immediate prior source
is in `app.previous/`; older predecessors may be in transaction directories.

Setup failure leaves existing applications running. A later failure restores
the prior source/environment/receipt when safe and leaves applications stopped.
If shutdown was not confirmed, inspect jobs and pods before recovery. For manual
recovery, stop both jobs, confirm no pods remain, then restore matching source,
environment, and receipt from one generation before starting the saved jobs.

Verify the new generation before removing recovery artifacts. Limit cleanup to
exact obsolete paths; preserve credentials, `reports/`, `state/`, `logs/`, the
active generation, and a verified recovery generation. Remove a stale
`deployment.lock/` only after confirming no deployment/setup process remains
and validating the selected generation.

[toolforge-jobs]: https://wikitech.wikimedia.org/wiki/Help:Toolforge/Running_jobs
[toolforge-publish]: https://wikitech.wikimedia.org/wiki/Help:Toolforge/Running_jobs#Exposing_continuous_jobs_to_the_internet
