# Aranami administration

Administration (this guide) is for system administrators and Toolforge
maintainers. Use it to upload a validated source payload, operate the backend and website,
understand runtime folders, and recover from a failed replacement.
Terminal users running reports or previews should use the
[Usage](usage.md); local contribution and verification are in
[Development](development.md).

Aranami runs only on Toolforge. One Python 3.15 backend hub manages wiki
and website data; one PHP webservice displays saved outputs. The source
deployment does not install a wheel or create separate report workers.

## Upload local src (source code) to Toolforge

Use this four-step workflow to upload the local `src/` and run the new
deployment. Detailed validation and recovery are explained below.

1. On the development computer, open a terminal in this repository and
   validate the source payload:

   ```shell
   cd /path/to/Aranami
   uv sync --locked
   uv run python scripts/upload_toolforge.py --dry-run
   ```

   Replace `/path/to/Aranami` with your actual local checkout. Complete
   the [offline checks](development.md#verification-and-releases) before
   proceeding with a changed application.

2. From that same local repository, upload:

   ```shell
   uv run python scripts/upload_toolforge.py
   ```

   This uploads all four `src/` packages (`backend`, `updater`,
   `webservice`, and `shared`) together with locked dependency metadata,
   launchers, and generated manifests. It stages the new generation;
   it does not replace the running application. Do not use `scp` to copy
   individual source files into active `app/`.

3. Connect to Toolforge, become the tool account, and run the exact
   promotion command printed by the uploader:

   ```shell
   ssh <maintainer>@login.toolforge.org
   become aranami
   cd /data/project/aranami
   ```

   The printed command names that upload's unique staging directory.
   Promotion validates it, prepares the environment, and replaces source
   only after stopping the old applications. It leaves them stopped.

4. Run the [start commands](#start-the-applications), then
   [inspect operation](#inspect-operation). The normal hub startup checks
   due work immediately and enables live wiki publication.

## Maintain on Toolforge

Prepare and validate source on the development computer. Run promotion
and job-management commands on the Toolforge bastion as the `aranami`
tool account, using existing maintainer access:

```shell
ssh <maintainer>@login.toolforge.org
become aranami
cd /data/project/aranami
```

`/data/project/aranami` is the convenient user-facing tool-home path. On
this account its physical location is
`/mnt/nfs/labstore-secondary-tools-project/aranami`. Toolforge may expose
that physical path as `HOME`. Deployment validates resolved paths so
both names refer to the same account; do not replace the alias, change
ownership, or move runtime data to force a particular spelling.

Keep credentials, `.my.cnf`, Pywikibot configuration, runtime data, and
environments outside `app/`. The launcher changes to the tool home and
sets `PYTHONPATH` to the deployed `app/src`, `PYWIKIBOT_DIR` to the existing
account configuration, and the bytecode cache below `cache/`.

## Runtime folder architecture

The following paths are relative to the tool home:

| Location | Purpose and retention |
|---|---|
| `app/` | Complete validated active source payload; never write runtime data here |
| `app-staging-<id>/` | Unique uploaded generation awaiting promotion |
| `app.previous/` | Previous complete source generation retained for recovery |
| `pyvenv-3.15` | Stable link to the selected locked Python 3.15 environment |
| `deployment-envs/` | Isolated environments prepared before promotion |
| `deployment-backups/` | Transaction-specific job definitions, receipts, routing, and older generations |
| `active-deployment.json` | Receipt matching the selected source and environment |
| `deployment.lock/` | In-progress deployment guard; inspect an interrupted transaction before removing it |
| `python/` | Managed Python 3.15.0 interpreter |
| `uv-bootstrap/` | Dependency setup tooling, separate from the applications |
| `reports/` | Durable complete result bundles, manifests, Parquet data, and generated wikitext |
| `state/` | Observed routine, schedule, and per-destination publication metadata |
| `cache/` | Disposable source data, completion evidence, bytecode, and website snapshots |
| `logs/` | Natural-language UTC daily logs only: `aranami.YYYY-MM-DD.log` |
| `dry-run/` | Preview summaries, exact proposed text, and isolated result bundles |
| `public_html` | Link to `app/src/webservice/public_html`; private PHP helpers remain outside the public tree |

The authoritative metadata files are `state/routine-status.json`,
`state/schedule-status.json`, and `state/report-updates.json`. Do not
derive status from diagnostic log text or move these files into `logs/`.
Preserve state and durable reports during normal source replacement.
Deleting disposable caches requires later revalidation and collection;
it is not a routine deployment step.

Toolforge's `python3.13` image bootstraps uv and the managed Python 3.15.0
interpreter. The hub explicitly executes `pyvenv-3.15/bin/python`, so
application code runs on Python 3.15. The website uses the `php8.4` image.

## Browse and view files

After connecting and becoming the tool account, browse files directly in
the terminal. You do not need an editor just to read them:

```shell
cd /data/project/aranami
pwd
ls -lah
ls -lah app/src/backend state reports
less app/src/shared/config.py
less app/pyproject.toml
```

`pwd` shows the current directory; `pwd -P` shows its physical resolved
path. `ls -lah` includes hidden names and readable sizes. `less` lets you
scroll without changing the file; press `q` to exit. The source files above
show the configured routines and deployed application version.

View the latest lines of today's UTC log, or follow new messages:

```shell
tail -n 80 "logs/aranami.$(date -u +%F).log"
tail -f "logs/aranami.$(date -u +%F).log"
```

Press `Ctrl-C` to stop following. A file appears after the backend first
logs on that date. Around midnight UTC, reopen the newly dated file.
The [web log viewer](https://aranami.toolforge.org/logs/) is a read-only
alternative for browsing saved dates and searching messages.

Inspect formatted status metadata with `jq`:

```shell
jq . state/routine-status.json
jq . state/schedule-status.json
jq . state/report-updates.json
```

If `jq` is unavailable, use `less` on the same files. A missing file means
there is no saved observation yet, not a successful run. Never edit these
snapshots to change displayed status.

To inspect a complete retained result, first identify its actual safe
report key and generation; do not use a wiki title as a filesystem path:

```shell
ls reports/
less reports/REPORT_KEY/latest.json
ls -lah reports/REPORT_KEY/GENERATION/
less reports/REPORT_KEY/GENERATION/manifest.json
less reports/REPORT_KEY/GENERATION/report.wikitext
```

Replace `REPORT_KEY` with an exact directory listed by `ls reports/`, and
`GENERATION` with the selected generation from that key's `latest.json`.
The manifest describes named Parquet tables, source/calculation times,
application version, and publication outcome. Parquet is binary, not a
text file. In a Python 3.15 Toolforge job configured like the
[preview example](usage.md#preview-selected-routines), use the validated
reader to inspect tables without changing their data:

```python
from shared.reports import ReportStore

bundle = ReportStore().read("REPORT_KEY")
if bundle is not None:
    for name, table in bundle.tables.items():
        print(name, table.schema)
        print(table.head(10))
```

Replace `REPORT_KEY` with the existing key identified above. Pass
`previous=True` to `read` for the retained predecessor, or construct
`ReportStore(dry=True)` for preview bundles. The reader validates hashes
and schemas; do not open Parquet in `less` or modify bundles by hand.

Do not display, paste, or attach credential files while inspecting the
account. In particular, keep `user-password.py`, `user-password.cfg`,
`.my.cnf`, replica credentials, and OAuth secrets private. Avoid broad
commands that print every hidden file or recursively dump account contents.

Keep private credential directories owner-only (`700`, or `2700` where
Toolforge retains directory group inheritance). Use `600` for writable
OAuth/Pywikibot and Kubernetes configuration, and `400` for read-only
private keys. Verify ownership and modes without displaying file contents.
Do not change `.my.cnf` as though it were a regular file: its platform-managed
target may enforce `440` and reject permission changes. That mode permits
only the tool owner and its tool group to read it, not other users.
The hub starts with `umask 077` so newly created runtime files and private
directories do not gain group or other-user access. Keep credentials outside
`public_html`; source replacement must never upload or overwrite them.

## Validate and upload source

On the development computer, use a recent uv release with Python 3.15
support, install the locked environment, and complete
[offline verification](development.md#verification-and-releases):

```shell
uv sync --locked
uv run python scripts/upload_toolforge.py --dry-run
```

The dry run inspects the exact payload without contacting Toolforge. It
includes backend, updater, shared Python code, public assets, private PHP
helpers, dependency metadata, launchers, and generated version, routine,
and hash manifests. It excludes credentials, runtime data, and tests.
Source symlinks and missing required files fail validation. Add
`--archive <new-file.tar>` to retain an inspected payload; the archive path
must not already exist.

Upload with the same command without `--dry-run`:

```shell
uv run python scripts/upload_toolforge.py
```

The uploader creates a unique `app-staging-<id>/` directory and leaves
active applications untouched. `TOOLFORGE_SSH_TARGET` overrides the
maintainer login, and `TOOLFORGE_KNOWN_HOSTS` selects an existing verified
known-hosts file. Keep SSH host verification enabled. Retain the printed
staging path, content digest, and exact promotion command.

## Promote the uploaded generation

On the bastion as the tool account, run the promotion command printed by
the uploader, using its exact staging path. Its general form is:

```shell
sh /data/project/aranami/app-staging-<id>/scripts/toolforge/deploy-source.sh /data/project/aranami/app-staging-<id>
```

The deployment validates source paths and hashes, then builds a new
isolated environment and validates configuration and imports in a one-off
Toolforge job while existing applications continue running. Promotion
requires a success receipt for that exact source/environment generation;
the setup job finishing alone is not sufficient proof.

Only after setup succeeds does deployment stop the Aranami jobs, wait for
their pods to disappear, and replace the complete source directory,
environment link, and website routing. It leaves both applications
stopped after validation. Start them explicitly after reviewing the result.

Do not edit individual files inside the active source tree or mix files
from different uploads. Keep the source, selected environment, generated
metadata, and active receipt from the same generation.

## Start the applications

Run on the bastion as the tool account:

```shell
toolforge jobs run aranami-hub --image python3.13 --continuous --mount=all --command 'exec sh /data/project/aranami/app/scripts/toolforge/run-hub.sh'
toolforge jobs run aranami-logs --image php8.4 --webservice --mount=all
```

Despite the retained `aranami-logs` job name, this PHP webservice serves
the complete website as well as the log viewer.

Keep the leading `exec` in the hub command. It replaces Toolforge's outer
shell so Python receives the container's stop signal, records the shutdown
request, and logs completion after cleanup. A forced kill or an expired
container termination grace period cannot guarantee the final log entry.

The hub enables live wiki publication. By default it checks routines and
due website outputs on startup, then follows the configured schedules and
refresh intervals. Valid completion evidence skips current daily work;
saved website outputs retain their successful refresh times across restarts.
Do not add `--no-run-immediately` when the intention is to deploy and run
due work immediately. Use that option on the `run-hub.sh` command only
when startup should wait for scheduled callbacks.

Without a valid website snapshot, PHP shows a preparing state until
collection succeeds. A frontend request never initiates collection.

## Prepare Chinese conversion tables on Toolforge

Use this workflow to prepare reproducible upstream data for regional
article-title display. Fork all three sources independently:

| Layer | Forked sources | Override priority |
|---|---|---|
| Software | MediaWiki Chinese tables, converter definitions, and license at one commit | Base |
| Local site | Chinese Wikipedia `MediaWiki:Conversiontable/*`, including referenced subpages | Overrides software |
| Video games | `Module:CGroup/Games` and its verified Item contract | Overrides local site and software |

Games-specific rules from
[Module:CGroup/Games](https://zh.wikipedia.org/wiki/Module:CGroup/Games)
take precedence over MediaWiki's default Chinese conversion tables. The
[architecture contract](architecture.md#regional-article-title-conversion-planned)
defines extraction, variant selection, and unchanged article identities.
This is preparation guidance: Aranami does not yet ship a conversion-table
importer or activate these files for title rendering.

Run acquisition and updates only on Toolforge as the `aranami` account,
using `curl` and `jq`. Keep the inputs in the tool home's disposable cache,
outside active source, credentials, and public files. Do not update tables
from the development computer or during a website request.

Create one isolated candidate and resolve an immutable MediaWiki commit:

```shell
cd /data/project/aranami
mkdir -p cache/chinese-conversion
conversion_stage=$(mktemp -d /data/project/aranami/cache/chinese-conversion/staging.XXXXXX)
mkdir -p "$conversion_stage/software" "$conversion_stage/site" "$conversion_stage/games"
conversion_agent='Aranami conversion maintenance (https://aranami.toolforge.org/)'
curl --fail --silent --show-error --max-time 60 --retry 2 \
  --user-agent "$conversion_agent" \
  --output "$conversion_stage/software/mediawiki-revision.json" \
  'https://api.github.com/repos/wikimedia/mediawiki/commits?path=includes/Languages/Data/ZhConversion.php&per_page=1'
conversion_commit=$(jq -er '.[0].sha | select(test("^[0-9a-f]{40}$"))' "$conversion_stage/software/mediawiki-revision.json")
```

Stop if either command fails or the commit is empty. Fetch the default
tables, converter fallback/rule definitions, and license from that same
commit:

```shell
curl --fail --silent --show-error --max-time 60 --retry 2 \
  --user-agent "$conversion_agent" \
  --output "$conversion_stage/software/ZhConversion.php" \
  "https://raw.githubusercontent.com/wikimedia/mediawiki/$conversion_commit/includes/Languages/Data/ZhConversion.php"
curl --fail --silent --show-error --max-time 60 --retry 2 \
  --user-agent "$conversion_agent" \
  --output "$conversion_stage/software/ZhConverter.php" \
  "https://raw.githubusercontent.com/wikimedia/mediawiki/$conversion_commit/includes/Language/Converters/ZhConverter.php"
curl --fail --silent --show-error --max-time 60 --retry 2 \
  --user-agent "$conversion_agent" \
  --output "$conversion_stage/software/LanguageConverter.php" \
  "https://raw.githubusercontent.com/wikimedia/mediawiki/$conversion_commit/includes/Language/LanguageConverter.php"
curl --fail --silent --show-error --max-time 60 --retry 2 \
  --user-agent "$conversion_agent" \
  --output "$conversion_stage/software/COPYING" \
  "https://raw.githubusercontent.com/wikimedia/mediawiki/$conversion_commit/COPYING"
```

Treat the PHP files as source data; do not `include`, `require`, or execute
them. If upstream paths or declarations change, review the new format and
adapt the importer before proceeding. Preserve the upstream license and
attribution rather than treating imported data as Aranami's CC0 source.

Fork the local site's conversion pages, including their raw content and
revision identities. Start the read-only namespace listing with:

```shell
curl --fail --silent --show-error --max-time 60 --retry 2 \
  --user-agent "$conversion_agent" --get \
  --data-urlencode 'action=query' \
  --data-urlencode 'format=json' \
  --data-urlencode 'formatversion=2' \
  --data-urlencode 'generator=allpages' \
  --data-urlencode 'gapnamespace=8' \
  --data-urlencode 'gapprefix=Conversiontable/' \
  --data-urlencode 'gaplimit=50' \
  --data-urlencode 'prop=revisions' \
  --data-urlencode 'rvprop=ids|timestamp|content' \
  --data-urlencode 'rvslots=main' \
  --output "$conversion_stage/site/pages-001.json" \
  'https://zh.wikipedia.org/w/api.php'
```

If a response contains `continue`, repeat the request with every returned
continuation field and save each subsequent response under a new numbered
filename. Do not compile or activate a partial listing. Select the parents
for `zh-hans`, `zh-hant`, `zh-cn`, `zh-tw`, `zh-hk`, `zh-mo`, `zh-sg`, and
`zh-my` and the conversion subpages they actually reference. Resolve any
redirects or further references through revision-bearing read-only API
requests. Follow the pinned converter's traversal and override semantics;
distinguish a confirmed absent/empty local override from a failed request.

Capture Games and its Item constructor through the read-only wiki API. The
response retains the precise revision IDs and their corresponding content:

```shell
curl --fail --silent --show-error --max-time 60 --retry 2 \
  --user-agent "$conversion_agent" --get \
  --data-urlencode 'action=query' \
  --data-urlencode 'format=json' \
  --data-urlencode 'formatversion=2' \
  --data-urlencode 'prop=revisions' \
  --data-urlencode 'rvprop=ids|timestamp|content' \
  --data-urlencode 'rvslots=main' \
  --data-urlencode 'redirects=1' \
  --data-urlencode 'titles=Module:CGroup/Games|Module:CGroup/core' \
  --output "$conversion_stage/games/cgroup-revisions.json" \
  'https://zh.wikipedia.org/w/api.php'
```

Keep all three forks together in the candidate manifest while retaining
their separate provenance. Article-local NoteTA/title overrides need a
separate implementation before claiming exact per-article parity.

Reject API errors, missing pages/slots, empty Games sources, or unsupported
rule syntax. Keep the source/history links and applicable upstream attribution
with the compiled data. When the importer is implemented, validate all
supported variants and Games-over-default conflicts before activation.
Include the 生化危机 / 惡靈古堡 / 生化危機 example, a title with several
Games matches such as 合金装备 和平行者, a one-way rule, a repeated phrase,
overlapping short/full-title rules, duplicate definitions, Latin text with
no applicable rule, and unchanged article/Pageviews URLs. Merge rules into
each target's table in the verified layer/source order, then apply all
non-overlapping longest matches without reconverting replacement output.

Record each layer's UTC fetch time, commit and revision IDs, SHA-256 hashes,
rule counts, and the conversion implementation version in the candidate manifest.
Activate only a complete validated generation through an atomic runtime
selector, retaining its predecessor for rollback. Refresh affected display
labels and rendered pages after selection; do not relabel source
observation timestamps as new collection. A failed fetch, parse, or
validation leaves the selected generation and website data intact. Without
a validated generation, keep canonical article titles visible. Do not add
a schedule until the updater and its failure handling have been deployed.

## Inspect operation

```shell
toolforge jobs list
toolforge jobs show aranami-hub
toolforge jobs logs aranami-hub
toolforge jobs logs aranami-logs
```

Inspect the [website](https://aranami.toolforge.org/) and
[saved daily logs](https://aranami.toolforge.org/logs/). The hub also serves
local read-only diagnostic `/healthz` and `/dashboard.json` endpoints at
`127.0.0.1:8765` by default. PHP reads the shared snapshot directly and
does not depend on those endpoints.

The website shows observed routine results and actual next execution
times, not predictions calculated from cron. Missing or stale metadata
must remain unknown. Per-destination metadata distinguishes a confirmed
edit from no change, failure, deferral, or an unconfirmed delegated write.
Use the terminal [inspection workflow](usage.md#inspect-results-and-logs)
for the individual runtime files.

After replacement, confirm that the intended jobs remain running, the
hub logs show data preparation/publication or a valid current-data skip,
website snapshots are readable, and no unexpected duplicate workers exist.
Retain recovery artifacts until the new generation is verified.

## Stop or restart

Restart the existing definitions without replacing source:

```shell
toolforge jobs restart aranami-hub
toolforge jobs restart aranami-logs
```

The hub's configured startup behavior applies again. For maintenance,
delete the two job definitions and inspect their remaining pods:

```shell
toolforge jobs delete aranami-hub
toolforge jobs delete aranami-logs
kubectl get pods -l 'app.kubernetes.io/name in (aranami-hub,aranami-logs)'
```

Wait until no matching pods remain before changing active source,
environments, or caches. Use the [start commands](#start-the-applications)
to recreate the definitions. Do not run a second continuous hub alongside
the existing one. Toolforge's [jobs guide][toolforge-jobs] documents job
creation, inspection, restart, and deletion.

## Recovery and cleanup

Promotion prints its exact transaction directory under
`deployment-backups/`. It contains the previous job definitions in
`jobs.yaml`, the successful setup receipt, and replaced routing or
environment references. The immediate prior source remains in
`app.previous/`; an existing public directory is retained as
`public_html.previous/` when replaced by the managed link. Later
promotions archive older predecessors in their own transaction directory.
Each generation retains its independent environment under
`deployment-envs/`.

A failed setup leaves existing applications running. If an error occurs
after promotion, deployment restores the previous source, environment
link, and routing when safe, and leaves applications stopped. If shutdown
could not be confirmed, inspect the job/pod state before recovery; do not
assume rollback was safe. Review the deployment output and retained files
before restarting.

For manual recovery after a completed deployment, stop both applications
and confirm their pods have disappeared. Select matching source,
environment, routing, and active-deployment receipt from the same
generation, then review the saved job definitions before restoring service.
Do not overwrite credentials or durable outputs to recover application code.

Cleanup must name the exact obsolete generation or disposable artifact.
Preserve account configuration, `reports/`, `state/`, and `logs/`; keep
the current source/environment and a verified recovery generation.
Never recursively delete the tool home, broad runtime directories, or an
unresolved alias. Remove an interrupted `deployment.lock/` only after
confirming that no deployment/setup process remains and the generation
is consistent.

[toolforge-jobs]: https://wikitech.wikimedia.org/wiki/Help:Toolforge/Running_jobs
