# Updater instructions

These instructions apply to `src/updater/` in addition to the
repository-wide instructions.

## Wiki frontend boundary

- Turn backend-prepared data into report wikitext and intentional wiki
  publication. Keep source refresh cycles, data management, and scheduling
  in `backend`; updater has no standalone monitor or launcher.
- Request typed prepared data and refresh work from backend services.
  Read only selected edit destinations in the frontend; pass their text
  to backend services for input settings, retained-membership parsing,
  comparisons, and other data interpretation. Do not fetch source pages
  or templates, duplicate data analysis, or manage runtime files here.
  Report-specific projection into table schemas belongs here when it
  serves rendering.
- Keep one report task per module in `jobs/`. `jobs.__init__` provides
  `JobContext`, `ProposedEdit`, and `job_run` for publication coordination.
- `services/` projects prepared data into report rows and wikitext without
  selecting publication destinations. `support/` contains narrowly named
  formatting helpers. Backend services own published-membership caches
  and identity comparisons; updater formatters only turn metadata into text.
- Updater may consume backend data APIs and shared infrastructure. Backend
  data services and shared code must not import updater. The hub resolves
  updater callbacks at its orchestration boundary.
- Target Toolforge source deployment with Python 3.15. Runtime files belong
  under `/data/project/aranami`, outside the uploaded source tree. Preserve
  the existing authentication and replica configuration.
- Keep `dry=True` free of wiki saves and PexBot refresh requests. Preview
  output must not advance production datasets, publication membership,
  completion checkpoints, or live routine status.

## Layer instructions

- [Architecture guide](../../docs/architecture.md) defines cross-component
  ownership, lifecycle, storage, and status contracts.
- [Job instructions](jobs/AGENTS.md) cover publication and dry-run artifacts.
- [Service instructions](services/AGENTS.md) cover output projection and
  wikitext rendering.
