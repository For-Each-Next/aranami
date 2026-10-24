# Aranami application development

Development (this guide) is for people contributing application features,
fixes, tests, and documentation. It explains how to prepare a local workspace, find the
right component, verify a change offline, and prepare a source payload.
For cross-component design and contracts, read [Architecture](architecture.md).

Use the development computer for offline implementation and verification;
live collection, credentials, and publication belong on Toolforge. Deployment
and process management are in [Administration](administration.md).

## Prepare the workspace

Use a recent uv release with Python 3.15 support. From the repository root:

```shell
uv sync --locked
```

The project runs directly from `src/`.
Keep `tool.uv.package = false` and the locked `pywikibot[mwoauth]`
dependency. Runtime credentials and Wiki Replica configuration are supplied
by the existing Toolforge account, not the source payload.

Before editing, read [AGENTS.md](../AGENTS.md) and any scoped instructions
in the affected directories. Preserve unrelated work in a dirty worktree.
Use a focused change and add a regression test for changed behavior.

## Find the right component

| Change | Start here |
|---|---|
| Source collection, parsing, report facts, comparisons, and caches | `src/backend/services/` |
| Scheduling, execution, logs, status, and publication storage | `src/backend/` and `src/backend/support/` |
| Website datasets and maintenance definitions | `src/backend/website/` |
| Read-only website HTTP endpoints and saved-log reads | `src/backend/public.py` and `src/backend/support/public_data.py` |
| Wiki destination selection, formatting, summaries, and intentional edits | `src/updater/` |
| Website presentation, localization, public assets, and saved-log display | `src/webservice/` |
| Low-level read-only adapters, configuration, generic mechanics, and storage primitives | `src/shared/` |
| Validated source upload and Toolforge launchers | `scripts/upload_toolforge.py` and `scripts/toolforge/` |

The backend prepares and retains facts. The updater reads its edit
destinations, renders backend data, and performs intentional publication.
Python presentation functions display saved backend outputs. Keep source
collection, data processing, and runtime file writes in the backend.
The [Architecture](architecture.md#runtime-and-architecture) describes
the complete ownership rules.

Routine destinations and UTC triggers are defined once in
`src/shared/config.py`. Setup validates its serialized contract and the
generated version metadata. Renderers use the backend-supplied configuration
and observed schedule. Maintenance definitions live in
`src/backend/website/maintenance.json`.

## Implement and test a change

Document public Python APIs and non-obvious private contracts using Google
docstrings. Use native Polars expressions for tabular operations and
explicit schemas, including empty results. Preserve null observations and
measured zeroes. Keep source-wiki interpretation separate by wiki.

Before changing a template call or extractor mapping, verify the actual
template, redirects, documentation, and invoked module/configuration on
that wiki. Record audited revisions when the contract needs evidence, and
verify each wiki's semantics independently.
See [wikitext and template contracts](architecture.md#wikitext-templates-and-site-semantics).

Tests use prepared data, fake wiki objects, temporary directories, and mocked
network boundaries, keeping verification offline and credential-free.
Cover the failure path as well as the happy path;
for persistence or publication changes, include storage-before-save,
unconfirmed writes, stale generations, and preview isolation.

Run a targeted unittest module while iterating. For example:

```shell
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/unit -p 'test_enwp_key_articles.py'
```

The public source APIs are `backend.run_once(...)` for an immediate pass
and `backend.run(...)` for an embedded recurring wiki monitor. Both default
to live publication. Inspect their contracts with fake dependencies in
offline tests; submit real previews only through the
[Toolforge preview workflow](usage.md#preview-selected-routines).
`python -m backend` is the normal central-hub entry point and also manages
website updates. `python -m backend.public` serves saved public data and
Python-rendered pages independently. Imports and command-line help remain inert.

The public website uses Flask routing and Waitress for production serving.
Use `backend.public.create_app` and its Flask test client for offline route
checks; preserve GET/HEAD-only behavior, security headers, bounded reads, and
inert imports. Keep the production debugger and automatic reloader disabled.

If embedding the recurring monitor, `run(dry=True,
run_immediately=False)` returns a background scheduler. Stop it with
`scheduler.shutdown(wait=True)` before changing output mode or clearing
caches. A fresh monitor normally checks all routines immediately, and
reusing an active monitor does not queue another startup pass.

## Verification and releases

Run the relevant suites, then the complete offline checks before handing
off a material change:

```shell
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/unit
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/integration
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/toolforge -p 'test_*.py'
uv lock --check
uv run ruff check --no-fix .
uv run ruff format --check .
uv run python scripts/upload_toolforge.py --dry-run
```

The Python suites include import and dependency-boundary checks, exact
fake-SSH source uploads, complete atomic report bundles, concurrency,
failed saves, completion/readiness rules, preview isolation, and
independent website refreshes. Socket tests need permission to bind local
loopback ports; a sandbox denial is not an application failure.

Presentation fixtures in `tests/toolforge/` cover localization, escaped
HTML, ordering, pagination, and observed status using the same Python suite.

Check each changed launcher with `sh -n scripts/toolforge/<name>.sh`.
For website changes, check desktop and narrow layouts, long titles and
chips, maintenance sorting/pagination, real source dates and site ranks,
and scheduling states in all three interface languages. Page requests remain
read-only, including when snapshots are missing or invalid.
For live status changes, also verify one-second local ticks, five-second
server synchronization, running and completed timers, hidden next-execution
rows during running work, optional structured progress, open popovers and focus,
hidden-tab pause/resume, and recovery from failed or malformed responses.
Check the log page's compact server state, version, and uptime independently
of routine cards and log contents. Dashboard content and native controls must
still work with JavaScript disabled. `/api/live` reads only saved status and
renders fresh fragments independently of the full-page cache and website datasets.
Run `node --test tests/toolforge/test_live.cjs` for clock drift, synchronization,
failure recovery, and DOM-preservation regressions.

HOT v2 Lua and CSS belong to the wiki, not this source repository. Keep any
paste artifacts or rendering harnesses in temporary directories. Python
regressions verify per-list JSON fields, date arrays, assessment scope, optional
site ranks, and escaped values using synthetic prepared data. Preview the
module and TemplateStyles on-wiki before enabling the new publisher.
See the [Pageviews rendering contract](architecture.md#pageviews) for data
ownership, supported ratings, and the installation dependency.

The uploader's dry run validates the exact source payload without
contacting Toolforge. Add `--archive <new-file.tar>` to retain that payload;
the archive path must not already exist. This is preparation, not an
authorization to deploy. Promotion and recovery follow
[Administration](administration.md).

Only the user selects major and minor versions. Apply the repository's
version/suffix rules, record completed material and documentation changes
in the active `CHANGELOG.md` section with explicit UTC timestamps, and
keep one cohesive version decision. Before a formal release, remove
temporary scaffolding and consolidate superseded details. Formal versions
are immutable; release tags must match the project version and publish
the validated source archive. Use Conventional Commits for requested Git
commits scoped to the requested changes.

## Update the right documentation

Keep all files in `docs/` in English and lead with outcomes and use cases.

- [Usage](usage.md): terminal users inspecting reports, running immediate
  work, and previewing changes.
- [Administration](administration.md): maintainers uploading source,
  operating processes, managing runtime folders, and recovering service.
- Development (this guide): contributors preparing, changing, and
  testing code.
- [Architecture](architecture.md): system-level developers preserving
  ownership, storage, lifecycle, and domain contracts.

Keep the root README focused on the tool's results and UTC schedules.
Update the documentation index and links when moving content. Link to each
guide's authoritative procedure rather than duplicating it elsewhere.
