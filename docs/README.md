# Aranami documentation

See the [project overview](../README.md) for reports and UTC schedules.

| Audience | Guide | What it helps you do |
|---|---|---|
| Terminal user | [Usage](usage.md) | Inspect operation, understand UTC triggers, preview changes, and request immediate reports |
| System administrator or maintainer | [Administration](administration.md) | Upload source to Toolforge, start/stop/restart services, manage runtime folders, and recover |
| Application developer | [Development](development.md) | Prepare a workspace, change application code, run offline checks, and prepare a release |
| System-level developer | [Architecture](architecture.md) | Preserve component ownership, data/report contracts, storage, logging, status, and lifecycle behavior |

## Component guides

The top-level guides are entry points by audience. Detailed contracts live
under the component that owns the behavior:

| Component | Guide | What it helps you do |
|---|---|---|
| `webservice/` | [Newsletter drafts](webservice/newsletter.md) | Select issues and retained dates, copy drafts, and interpret annual data |
| `webservice/` | [Branding](webservice/branding.md) | Maintain the website's logo, icons, and shared visual style |
| `backend/` | [Newsletter collection](backend/newsletter.md) | Maintain source evidence, caches, daily archives, and history backfills |

Read [repository instructions](../AGENTS.md) before contributing and the
[changelog](../CHANGELOG.md) for completed changes. Documentation is in English;
Toolforge is the only runtime. HOT report Lua/CSS is maintained on-wiki; see
[its contract](architecture.md#pageviews).
