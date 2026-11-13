# Report coordination instructions

Each module coordinates one independently runnable report pass. It selects
concrete settings, requests typed task results, retains a complete proposal and
then delegates output to the updater. Jobs own no scheduler.

- Use `shared.config` defaults unless the caller supplies an override.
  Do not import another job module.
- Keep source reads, source/retained-text parsing, comparisons and tabular
  preparation in tasks. Keep schema validation and persistence in data.
  Keep output projection and wiki saves in updater.
- Pass original destination text to tasks when it contains settings or retained
  facts. Preserve the original baseline for save preconditions and retries.
- Retain complete results and proposals before publication. Storage failure
  blocks saves. A failed or unconfirmed save must not advance membership or
  completion. Record no-change, deferral and unknown edit counts explicitly.
- Preview execution retains one UTF-8 Markdown summary per pass under `dry-run`.
  Begin with `# Aranami dry run — YYYY-MM-DD UTC`, then observed start/end/status.
  List each routine once, with timing, diagnostics and proposed-edit metadata.
  Link flat UTF-8 `.wikitext` companions; keep reusable bundles separately in
  `dry-run/reports`. Group delegated PexBot requests under one routine.
- Never access Wiki Replica connections, SQLAlchemy statements or raw source
  responses directly. Use existing task and source-client lifetimes.

See [Architecture](../../../docs/architecture.md) and
[Updater](../../updater/AGENTS.md).
