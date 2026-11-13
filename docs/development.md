# Developing Aranami

Use this guide to change source, verify it offline and prepare a source
deployment. Live source collection and wiki publication run only on Toolforge.
[Architecture](architecture.md) defines data contracts;
[Administration](administration.md) defines production operations.

Protect Toolforge's midnight collection from 23:59 UTC until 00:02 UTC.
Defer uploads, deployments, drains, restarts and disruptive maintenance until
the window ends. Local source changes and offline verification may continue.

## Repository layout

```text
.github/workflows/       CI and formal release automation
src/runtime/            Scheduling, lifecycle and report coordination
src/tasks/              Source acquisition and reusable calculations
src/tasks/zhwiki/        Audited Chinese wiki rules and newsletter workflows
src/data/               Schemas, stores, saved contracts and runtime records
src/data/caching/        Disposable cache implementations (source code)
src/shared/             Read-only adapters, configuration and common primitives
src/updater/            Wiki projection and intentional publication
src/webservice/         Django HTTP views, localization and saved-data readers
src/webservice/templates/ Authored server templates and shared HTML components
src/webservice/public_html/ Authored CSS, JavaScript and fixed assets
scripts/toolforge/      Runtime launch, validation, promotion and drain scripts
scripts/upload_toolforge.py  Source payload preparation and upload
tests/unit/              Isolated component contracts
tests/integration/       Cross-component offline workflows
tests/toolforge/         HTTP, presentation, browser and deployment fixtures
docs/                   Audience guides and owned component contracts
```

Root manifests stay discoverable by their tools: `pyproject.toml`, `uv.lock`,
and `.python-version`.
`.editorconfig` defines portable whitespace conventions. `README`, `LICENSE`,
`CONTRIBUTING`, `CHANGELOG` and `AGENTS` remain standard root entry points.
Generated `build/`, release archives in `dist/`, dependencies, IDE settings and
runtime folders are ignored. Upload payload staging uses a temporary directory.
Do not move machine-specific state into the application package.

## Prepare the workspace

Use Python 3.15 with uv and Polars 2. Node.js 24 or later runs the offline
browser tests; building and deploying pages needs only Python:

```shell
uv sync --locked
```

The project runs from `src`; keep `tool.uv.package = false` and the locked
`pywikibot[mwoauth]` dependency. No wheel backend or compatibility launcher is
needed. In PyCharm, select the project's `.venv`, mark `src` as Sources and
`tests` as Test Sources, and exclude `.venv`, `.cache`, `.ruff_cache`,
`node_modules`, `dist`, `build` and runtime data folders. Interpreter libraries
belong to the SDK, not project source roots. Use Google-style Python docstrings.
Local `.idea` settings remain untracked, preserving each developer's workspace.

## Implement a change

Start from `trunk` on an isolated task branch and merge back after verification.
Keep unrelated work intact. The user-selected 0.9.2 release consolidates the
unpublished alpha work after v0.8.8. Record completed work in the active changelog
section, with UTC timestamps. Formal 1.0.0 publication is a separate decision.

Daily schema/storage changes start in `data.project_data`; acquisition belongs
to `tasks.project_data`, and source selection to `runtime.project_observations`.
Keep the primary project capture ordinary at 00:00:00 UTC. Daily consumers,
including assessment logs and newsletter facts, use the shared idle executor
and read the committed Parquet snapshot. Declare idle collection jobs through
`CollectionDefinition.idle`; never block a worker while waiting for capture.
Use explicit Polars schemas and native joins, aggregations and expressions.
Eager source adapters remain eager; lazy scans
are appropriate for saved files. Preserve null, observed zero and observed
empty as different facts. Consult the [Polars API][polars] and
[migration guide][migration] when changing tabular calculations.

Test with temporary runtime directories, fake wiki objects, prepared frames
and mocked network boundaries. Imports and help must not collect. Cover first
baseline, adjacent comparisons, gaps, concurrent capture, failed storage,
preview isolation, lost wiki-save responses and restart recovery. Test source
and publication dates separately. Do not put live credentials in fixtures.

Website changes belong under `src/webservice/`. Django owns request routing,
responses and the configured Jinja2 template backend. Templates and static
assets are authored runtime files: no Node dependency installation, Vue,
TypeScript compilation or generated template copies are required.
Document JavaScript modules and non-obvious helpers with JSDoc comments; use
four-space indentation and plain browser APIs. Keep native GET forms, links,
disclosures and dialog fallbacks usable without scripting. See
[Frontend](webservice/frontend.md) for rendering and interaction contracts.

## Verification and releases

Run focused suites during implementation, then the required offline checks:

```shell
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/unit
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/integration
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python -m unittest discover -s tests/toolforge -p 'test_*.py'
uv lock --check
uvx --from ruff==0.16.0 ruff check --no-fix .
uvx --from ruff==0.16.0 ruff format --check .
uv run python scripts/upload_toolforge.py --dry-run
node --test tests/toolforge/test_*.cjs
```

Shell changes additionally require `sh -n` for each changed launcher. Local
socket tests require loopback permission. The source inventory in
`scripts/toolforge/source-files.json` must include added runtime modules.

For interface changes, render synthetic fixtures and verify desktop/narrow
layouts, all locales, long text, controls, focus and missing-data states:

```shell
PYTHONPATH=src PYWIKIBOT_NO_USER_CONFIG=2 uv run python tests/toolforge/test_presentation.py --render-fixtures /tmp/aranami-interface-fixtures
node tests/toolforge/check_interface.cjs /tmp/aranami-interface-fixtures /tmp/aranami-interface-results
```

The checker uses a fixed copy of authored assets and Chromium; set
`ARANAMI_CHROMIUM` when needed. Inspect screenshots as well as its JSON result.
Browser checks do not establish real-device or screen-reader behavior.

Commit cohesive changes with Conventional Commits. CI runs for `trunk` and
any `alpha/**` task branches. Formal release tags require a base version;
Toolforge alpha uploads use the version/digest source workflow without a formal
release tag. A successful dry run validates a payload, not a deployment.

[polars]: https://docs.pola.rs/api/python/stable/reference/index.html
[migration]: https://docs.pola.rs/user-guide/migration/pandas/
