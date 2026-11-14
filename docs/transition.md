# Historical-source retirement checklist

Use this checklist before removing a historical source adapter or its saved
inputs. It tracks remaining dependencies after the flat-storage rollout; source
promotion belongs in [Administration](administration.md), and completed launch
outcomes belong in [CHANGELOG](../CHANGELOG.md). Removal depends on verified
replacement coverage and remaining consumers.

The first formal project observation covers November 2, 2026, captured on
November 3. Later daily comparisons require two adjacent formal files.
Current queries cannot reconstruct earlier midnight membership. Preserve the
original observation times, identities and hashes of pre-cutover records.

The November 2 bridge publication is confirmed, and adjacent November 7–8
observations passed schema, digest and source-time validation for the parent
and configured taskforces. The one-off bridge command and old daily writer
are retired; historical observations remain available to normal readers.

## Remaining dependencies

| Component or input | Replacement and removal gate |
|---|---|
| Assessment compatibility view and `data/parsed/assessments--*` | Full daily `project_data` files serve current reports. Retain historical readers until all required earlier observations have a verified lossless replacement. The old daily writer is retired. |
| `tasks.zhwiki.legacy.homepage_displays` | Canonical homepage inventory must retain required source formats and dates, and initialization/recovery must work without source interpretation. |
| Temporary `legacy/quality-register.parquet` beside the milestone store | Accept through the canonical milestone writer, verify readback and retain `quality-register-import.json` before deleting staging. Interrupted acceptance must retry safely. |
| Packaged `tasks.zhwiki.legacy.newsletter_figures.json` | Canonical historical `figures.parquet` must cover required measures and periods; affected issues and archives must render with the packaged provider disabled. |
| Historical `HOT`/`HOT-v2` parser in `tasks.zhwiki.legacy.newsletter_popularity` | Validated saved period aggregates or detailed traffic must cover each dependent issue, including delayed and annual issues, without old-format acquisition. |
| Legacy sparse Pageviews cache fallback | Durable `daily`, `members` and `coverage` tables must cover required dates and populations; incomplete or empty traffic cannot establish replacement parity. |
| Old layout backups and duplicate source copies | Preserve unique facts and recovery originals until all consumers use validated canonical records and independent frozen issues. |

Article history, milestones, authorship evidence, prepared report bundles and
newsletter archives remain durable records. Their age or original writer does
not make them disposable. Automatic age-only evidence purges, including
365-day pruning, remain suspended.

## Verify historical coverage

Inspect `data/parsed/historical-import.json` and
`data/parsed/newsletter--historical--import-receipt.json` before deciding which
adapter can retire. The accepted initial import contained 109 figure rows and
33 popularity periods, including the 2024/2025 annual reports. February, April
and June 2024 lacked audited exact-month sources; unavailable periods remain
explicitly unknown. Current observations cannot supply those historical facts.

When missing coverage or recovery requires another import, use the selected
Toolforge generation and its managed interpreter from the runtime directory:

```shell
"$runtime_python" -m tasks.zhwiki.legacy.migrate --runtime /data/project/aranami
```

The command is idempotent and does not edit wiki pages. Verify canonical
figures, milestone data, popularity periods and their receipts after it runs.
Accepted temporary input may be removed only after canonical readback passes.
Never modify packaged migration inputs inside an immutable generation.

Newsletter providers expose the same structured facts to calculations and
renderers; [Newsletter acquisition](tasks/newsletter.md) defines their
selection and non-overlapping historical boundaries. Before switching a
provider, verify every dependent measure and period with the old provider
disabled. Include delayed drafts, January annual reviews and frozen issues.
Unknown values, measured zeroes and partial coverage must retain their meanings.

## Retire a dependency

1. Inventory exact source paths/titles, schemas, digests, periods, known source
   times and consumers. Preserve unknown revisions and times as unknown.
2. Validate replacement types, uniqueness, provenance, coverage and zero/missing
   cases. Compare equivalent figures against retained original evidence.
3. Commit canonical facts atomically, verify readback and retain an import
   receipt identifying source, replacement, counts and validation outcome.
   Failed writes must preserve accepted data and recoverable inputs.
4. Prepare and render dependent issues with the old adapter disabled. Verify
   historical periods, frozen facts, previews and restart recovery.
5. Remove exact redundant inputs, unused source code and its obsolete fixtures
   in a reviewed source change. Preserve credentials, durable datasets, active
   selectors/generations and independent archives.
6. Record completed outcomes in the changelog and update this inventory. When
   removal remains blocked, record the concrete consumer or coverage gap.

Delete this guide and its inbound links when every remaining dependency has
retired. Keep lasting storage and provider contracts in their owning guides.
Formal `1.0.0` publication remains a separate user decision.
