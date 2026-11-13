# Runtime instructions

`runtime` schedules and coordinates Aranami on Toolforge. It calls acquisition
and calculation tasks, retains their data, then delegates presentation and
publication. Keep wiki extraction rules and schemas in their owning packages.

- Target Python 3.15 and run from the immutable source generation selected by
  `/data/project/aranami/app`. Pin its `src` on PYTHONPATH at startup. Keep
  credentials and all runtime files outside the source generation.
- `python -m runtime` starts the single `aranami-hub` process through
  `run-hub.sh`. `python -m webservice.server` starts the separate read-only
  `aranami-web` service through `run-web.sh`. Do not add old import aliases.
- `runtime.run(dry=False)` starts continuous schedules. Repeated starts reuse
  an active monitor in the same mode. `run_once(date=None, dry=False, tasks=None)`
  coordinates selected immediate work without changing schedules.
- The hub owns lifecycle and shared source cycles. The monitor owns UTC
  dispatch, postponement and actual schedule observations. The runner contains
  no report implementations. Jobs coordinate typed task results and outputs.
- Use `shared.config` for scopes, destinations and independent cron triggers.
  Coalesce missed work; allow one active invocation per routine. Idle work waits
  outside workers and runs sequentially after ordinary work.
- Capture required daily inputs before dependent work. Midnight uses immutable
  daily membership; later hours may use temporary observations. Keep actual
  observation times separate from report anchors.
- A website failure does not undo a wiki result or vice versa. Preserve
  per-output success, retries and confirmation evidence independently.
- Imports, help, heartbeat and app construction must not query sources.
  Shutdown pauses dispatch and drains active workers before closing resources.
- Use existing Toolforge authentication, PYWIKIBOT_DIR and replica credentials.
  Provisioning does not belong in the application.
- Runtime files live under `/data/project/aranami`: `logs`, `state`, `cache`,
  `dry-run` and `reports`. Retain current UTC daily logs plus 29 previous dates.
- Preview runs never save wiki pages, request PexBot refreshes or advance
  production datasets, completion evidence or live status.

[Architecture](../../docs/architecture.md) defines the complete dependency and
storage contracts. [Jobs](jobs/AGENTS.md) covers a report pass.
