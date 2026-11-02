# Aranami application development

Use this guide to prepare changes, verify them offline, and produce a source
payload. Read [AGENTS.md](../AGENTS.md), applicable scoped instructions, and the
relevant [Architecture contracts](architecture.md) first. Live collection,
credentials, and publication belong on Toolforge; see [Usage](usage.md) for
runtime commands and [Administration](administration.md) for deployment.

## Prepare the workspace

Use uv with Python 3.15 support and Node.js 24 or later:

```shell
uv sync --locked
npm ci --ignore-scripts
```

Source runs directly from `src/`; keep `tool.uv.package = false` and the locked
`pywikibot[mwoauth]` dependency. Preserve unrelated work. Implement a focused
change with regression coverage; docstring, Polars, version, and changelog
rules are maintained in [AGENTS.md](../AGENTS.md), not duplicated here.

## Find the right component

| Change | Start here |
|---|---|
| Source facts, parsing, comparisons, caches | `src/backend/services/` |
| Disposable Parquet cache contracts | `src/shared/support/cache.py`; [cache guide](backend/caching.md) |
| Shared article history, recognition, credits, retention | `src/backend/articles/` |
| Scheduling, execution, status, storage | `src/backend/`, `src/backend/support/` |
| Website collection/maintenance definitions | `src/backend/website/` |
| Read-only HTTP and saved-data readers | `src/backend/public.py`, `src/backend/support/public_data.py` |
| Wiki destinations, formatting, publication | `src/updater/` |
| HTML, localization, public assets | `src/webservice/` |
| Read-only adapters/configuration/primitives | `src/shared/` |
| Source deployment and launchers | `scripts/upload_toolforge.py`, `scripts/toolforge/` |

Keep preparation/persistence in backend, intentional wiki writes in updater,
and saved-data presentation in webservice. `src/shared/config.py` is the single
schedule/target definition. See [ownership contracts](architecture.md#runtime-and-architecture).

## Implement and test a change

Edit browser behavior and Vue components under `frontend/`, with authored
assets and page templates under `src/webservice/`. Run `npm run build:web`
before Python or browser presentation tests. It assembles the complete
website in ignored `dist/webservice/`, including compiled scripts, Codex CSS,
generated component templates, and a source/output hash receipt. Commit the
authored inputs; generated output stays outside Git.
`npm run check:web` type-checks and reproduces the build to verify its exact
contents. CI builds before validation. Node.js 24 or later is needed only
for development; Toolforge serves the prepared files from Python.
See [Frontend components](webservice/frontend.md) for component ownership and
the native form, dialog, and live-refresh contracts.

Use prepared data, fake wiki objects, temporary directories, and mocked network
boundaries; local tests stay offline and credential-free. Cover failures as
well as success, especially storage-before-save, unconfirmed writes, concurrent
generations, null versus observed zero, and preview isolation. Verify templates
and extractor mappings against each wiki's actual sources/configuration before
changing them; see [wiki contracts](architecture.md#wikitext-templates-and-site-semantics).

Run focused tests while iterating, for example:

```shell
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/unit -p 'test_enwp_key_articles.py'
```

Test `backend.run_once`/`run` with fake dependencies: both default to live
publication. Imports and command-line help must stay inert. Real previews use
the [Toolforge workflow](usage.md#preview-selected-routines), never local live
clients. For HTTP and WebSocket checks use `backend.public.create_app` and
FastAPI's test client, as in `tests/toolforge/test_public.py`;
preserve read-only routes, security headers, bounds, and disabled debugging.

## Verification and releases

Before handing off a material change, run the relevant suites and full checks:

```shell
npm run build:web
npm run check:web
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/unit
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/integration
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/toolforge -p 'test_*.py'
uv lock --check
uv run ruff check --no-fix .
uv run ruff format --check .
uv run python scripts/upload_toolforge.py --dry-run
```

These include import/boundary, source-upload, persistence, publication, and
presentation checks. Socket tests need local-loopback permission; sandbox
denial is not an application defect. Check changed launchers with
`sh -n scripts/toolforge/<name>.sh`.

Use the [Wikimedia Codex style guide](https://doc.wikimedia.org/codex/latest/style-guide/overview.html)
for website design, including color, typography, spacing, controls, and
accessible interaction. The webservice renders Jinja templates with local CSS
and native HTML controls. Keep appearance consistent with Codex across the
dashboard, maintenance, logs, About, and error pages.

Follow [Frontend components](webservice/frontend.md) for Codex component,
stylesheet, and attribution contracts, and [Branding](webservice/branding.md)
for logos and icons.

For website changes, verify desktop/narrow layouts, long/escaped text, sorting
and pagination, observed dates/ranks, all three locales, and missing/stale data.
Keep native controls usable without JavaScript. Live-status changes also cover
timers, running/completed states, progress, dialog focus, hidden-tab behavior,
and failed/malformed response recovery:

```shell
node --test tests/toolforge/test_live.cjs
node --test tests/toolforge/test_newsletter.cjs
```

Newsletter checks cover literal copying, the shared native date picker,
unavailable-date responses, and issue changes loading in place, including failed and
stale responses. Retain native form submission and date selection when
JavaScript is unavailable.
Consult the [newsletter interface](webservice/newsletter.md),
[collection contracts](backend/newsletter.md), and
[branding guide](webservice/branding.md) when changing those components.

Render synthetic pages and check all three locales at fourteen widths from
320 to 1920 CSS pixels:

```shell
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python tests/toolforge/test_presentation.py --render-fixtures /tmp/aranami-interface-fixtures
node tests/toolforge/check_interface.cjs /tmp/aranami-interface-fixtures /tmp/aranami-interface-results
```

Set `ARANAMI_CHROMIUM` to the Chromium executable when it differs from the
macOS default. The checker uses native Node/CDP and synthetic data, without
external services or npm dependencies. Each run snapshots the built assets
so later rebuilds cannot change pages during verification. It writes a JSON
result and sample screenshots, checking overflow, log download visibility/column alignment,
and modal focus, background inertness, dismissal, and restoration. Browser
and local-loopback permission are required. Inspect the screenshots as well.
Focused 320- and 1440-pixel cases also verify Tools hover, click, and keyboard
controls, language-menu keyboard focus, comparison-range endpoints, and each
report invocation's history dialog in all locales. Calendar cases check the
native date input, shared annual/quarter selection, saved-date bounds,
missing captures, and in-place issue changes.
These effective-width checks do not establish real-device touch behavior,
Safari/Firefox behavior, screen-reader speech, forced colors, or browser zoom.

Deployment tests use fake Toolforge/Kubernetes commands and temporary source
generations. Cover current-generation upgrades, pinned source/environment paths, staged
setup failures, individual service replacement, missing/paused jobs, and
shutdown/restart failures without contacting Toolforge. Keep old generations
available to running processes and require confirmed hub termination before
starting its replacement.

HOT v2 Lua/TemplateStyles belong to the wiki. Preview both on-wiki before
enabling publication; keep temporary paste/rendering artifacts outside this
repository. Python tests verify the [Pageviews contract](architecture.md#pageviews).

Uploader dry runs validate the exact payload without contacting Toolforge.
`--archive <new-file.tar.gz>` retains the reproducible gzip-compressed tar
at a previously nonexistent path; this
does not authorize deployment. Follow [Administration](administration.md) for
promotion/recovery and [AGENTS.md](../AGENTS.md) for version and changelog rules.

Keep documentation in English, led by purpose and expected results. Update the
guide that owns the behavior, link to it elsewhere, and keep README focused on
tool results and UTC schedules rather than repeating operational procedures.
