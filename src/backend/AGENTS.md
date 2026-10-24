# Backend instructions

These instructions apply to `src/backend/` in addition to the
repository-wide instructions.

## Runtime boundary

- Target Toolforge source deployment using Python 3.15 exclusively.
- Run from `/data/project/aranami/app/src` on PYTHONPATH, with the existing
  account authentication and runtime data outside the uploaded source tree.
- `python -m backend` starts the central hub for on-wiki and website
  updates. Keep one `aranami-hub` background job through `run-hub.sh`.
  `python -m backend.public` serves saved data and Python-rendered pages through
  the separate `aranami-web` job and `run-web.sh` launcher. Do not restore
  retired launchers or import aliases.
- `backend.run(dry=False)` starts the report monitor defined in
  `monitor.py` and returns its scheduler. Use shared configuration and
  independent per-routine UTC cron triggers, coalesce missed executions,
  and limit each job to one active instance. Repeated starts reuse an
  active monitor in the same mode.
- `backend.run(dry=True)` also runs the schedules continuously, writing
  previews at each trigger without wiki edits or PexBot refresh requests.
- `backend.run_once(date=None, dry=False, tasks=None)` executes selected
  tasks immediately without starting or changing schedules. The optional
  date sets the UTC report anchor. Both public entry points default to live
  publication. Job-level APIs use the same `dry` keyword and accept an
  optional `context`.
- Keep sleep loops and notebook-specific callbacks out of runtime code.
  A lightweight event-driven schedule heartbeat must make no source reads.
- Runtime data belongs under the caller's current directory, set to
  `/data/project/aranami` by the documented Toolforge job command:
  `logs/` for natural-language UTC daily logs, `state/` for observed
  routine/schedule/report metadata, `cache/` for disposable cached data,
  `dry-run/` for proposed-edit artifacts, and `reports/` for durable results.
- Append all runs on one UTC date to one file under `logs/` and retain
  the current UTC date and previous 29 dates, pruning older regular daily files.
- Keep `logs/`, `cache/`, and `dry-run/` visible; do not rename them to
  hidden paths.
- Treat `cache/` as an optimization only. Deleting it must never lose
  authoritative data or change the final report.
- Never write runtime data into the source tree or `site-packages`.
- Use the runtime's existing Pywikibot authentication and Wiki Replica
  configuration. Use Toolforge's configured `PYWIKIBOT_DIR` and working-directory `.my.cnf`.
  Keep credential provisioning outside the runtime package.

## Package architecture

Keep dependencies flowing down through these layers:

- `backend.__init__` is the report API and exports `run` and `run_once`.
- `hub.py` owns process lifecycle and delegates both on-wiki routines and
  website refreshes. A wiki task's cycle can refresh due website reports
  using the same read-only source data. An hourly website callback covers
  periods without wiki work. Keep output success, source timestamps, and
  retries independent; a failed output must not roll back another output.
- `server.py` serves local read-only diagnostics. `public.py` reads validated
  snapshots/status/logs and passes saved facts to `webservice` renderers.
  Only this HTTP orchestration layer imports the presentation package.
  Share HTTP response handling through `support/http.py` and
  bounded file readers through `shared.support.files`. Requests must not
  initialize clients, collect wiki data, write files, or trigger publication.
- `runner.py` starts monitoring or coordinates an immediate selected pass.
  It contains no report implementation or scheduling loop.
- `monitor.py` owns scheduler lifecycle, postponement, and actual schedule
  snapshots. Read destinations and UTC triggers from `shared.config`.
  Resolve callbacks lazily; scheduled jobs do not share a `JobContext`.
  Services return scheduling advice without mutating the scheduler.
- `services/` prepares reusable source facts and extraction results from
  read-only sources and caller-supplied settings or existing text. Own
  source/retained-report parsing, identity comparisons, and cache
  progression here rather than in frontend wrappers. Keep
  report projection schemas, output formatting, edit proposals, and
  publication in the `updater` frontend.
- `updater` contains report jobs, output projection services, and
  formatting helpers. The hub delegates wiki formatting and publication
  to those jobs; updater owns no scheduler.
- `website/` owns read-only website collection, data processing, refresh
  state, and the maintenance catalogue. Keep presentation in `webservice`.
- Read-only sources and generic infrastructure live in `shared`.
  Shared code must not import backend or presentation code.
- `support/` contains narrowly named cross-cutting helpers such as paths,
  caching, runtime execution/storage, logging, state, and rate limiting.
- Do not create generic dumping grounds named `utils`, `common`, or
  `helpers`. Add a domain-named module to the appropriate layer.
- Services and support must not import `updater`, `hub`, or `runner`.
  Resolve updater callbacks lazily at the orchestration boundary.

## Layer instructions

The package instructions remain in force throughout `src/backend/`. These
directories add rules for their own layer:

- [Architecture guide](../../docs/architecture.md) defines system-wide
  ownership, data, publication, and observed-state contracts.
- [Source adapter instructions][1] cover read-only sources and preloading.
- [Service instructions][2] cover Polars data preparation and complete
  reusable report values.
- [Updater instructions][3] cover wikitext formatting, intentional
  publication, and dry-run artifacts in the wiki frontend.

[1]: ../shared/sources/AGENTS.md
[2]: services/AGENTS.md
[3]: ../updater/AGENTS.md
