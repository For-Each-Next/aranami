# Repository instructions

These instructions apply to the entire repository.

See [the architecture guide](docs/architecture.md) for AI/system-level
development contracts, including component ownership, per-wiki extractors,
verified template contracts, configurable HTML comment ranges,
Polars/Parquet processing, logs, state, and disposable caches. Use the
[development guide](docs/development.md) for practical contribution and
offline verification.

## Repository boundary

- This repository is only for developing, testing, and preparing Aranami source
  deployments on a development computer. Runtime targets Toolforge only.

## Scoped instructions

The root instructions remain in force throughout the repository.
[Backend instructions][1] add runtime, storage, and data-preparation rules
for `src/backend/`. [Updater instructions][7] cover wikitext rendering and
publication in `src/updater/`.

## Documentation focus

- Lead documentation with what the component does, when to use it, and the
  results the caller can expect.
- Keep technology and implementation details brief. Include them only when
  they help the reader use the component correctly or maintain its contract.
- Keep the README focused on what the tool does and its results, including
  the scheduled tasks and their UTC trigger times. Route terminal users to
  `docs/usage.md`, system administrators to `docs/administration.md`, human
  application developers to `docs/development.md`, and AI/system-level
  developers to `docs/architecture.md` and scoped instructions. Maintain
  files in `docs/` in English only.

## Python documentation

- Start every Python module, including each `__init__.py`, with a file-level
  docstring that describes its purpose and usage.
- Document every public class, function, and method. Also document private
  callables whose size, logic, side effects, or contract are not obvious.
- Follow the [Google Python docstring guide][2] and use triple double quotes.
- Put a one-sentence summary on the opening line and end it with punctuation.
  For a longer docstring, follow the summary with one blank line and then the
  detailed description.
- Use Google-style `Args:`, `Returns:`, `Yields:`, and `Raises:` sections when
  applicable. The parameter heading is `Args:`, not `Params:`.
- Describe behavior and semantics without repeating types already present in
  annotations.

## Polars conventions

- Use Polars for tabular data and Parquet for disposable tabular caches.
  Read the [stable Python API reference][5] and the [Pandas migration
  guide][6] when implementing or reviewing data transformations.
- Write Polars-native expressions with `select`, `with_columns`, `filter`,
  `join`, `group_by().agg()`, and window expressions. Combine independent
  expressions in one context instead of chaining trivial frame callbacks.
- Use explicit columns as keys; do not emulate Pandas indexes, mutable
  column assignment, row-wise `apply`, or conversions through Pandas.
- Preserve explicit schemas and distinguish null observations from zero.
  Prefer expressions to Python element or row UDFs for tabular calculations.
- Use lazy scans and deferred collection when they improve file-backed
  processing. Keep the established eager source-adapter contracts intact.
- Python iteration is appropriate at external-request and wikitext-parser
  boundaries, or for small scalar mappings; do not use it for joins,
  grouping, aggregation, ranking, or other native tabular operations.

## Change management

### Versioning

- Use a [Semantic Versioning 2.0.0][3] base version for release meaning.
- Classify each change from its actual effect. Material changes include
  features, bug fixes, behavior changes, database query changes, dependency
  changes, package structure changes, and public API changes.
- The user alone selects major and minor versions. Never infer or apply either
  bump without the user's explicit comment.
- Codex may automatically select patch versions for fixes, functions, and
  other backward-compatible work, and may manage intermediate build suffixes
  without asking. The user may override any automatic version decision.
- Before a base version is formally published, distinguish its test deployments
  with sequential PEP 440 `.devN` suffixes. Publishing the base removes the
  suffix; that formal version is then immutable.
- After a formal release, distinguish test deployments leading to the next patch
  with sequential `.postN` suffixes on the formal base. For example,
  `0.1.1.post1`, `0.1.1.post2`, and later test deployments lead to formal `0.1.2`.
- Validate a source payload with `uv run python scripts/upload_toolforge.py
  --dry-run`. Keep `tool.uv.package = false`; do not restore a wheel backend,
  PAWS launchers, compatibility imports, or separately installed OAuth extras.
- Target Python 3.15, use its native syntax where useful, and install the
  locked `pywikibot[mwoauth]` dependency for the source runtime.
- Before removing `.devN` to produce a formal base release, or replacing a
  post-release sequence with a new base version, perform a final cleanup pass.
  Remove temporary scaffolding and trivial details, consolidate overlapping
  implementation and documentation, and keep all material behavior and
  decisions intact.
- Keep one evolving `CHANGELOG.md` section for the active deployment version. Include
  its current suffix in the heading, such as `0.1.1.dev3` or `0.1.1.post2`,
  and replace it with the formal base or next patch version when published.
- Documentation-only, comment-only, and formatting-only changes do not change
  the base version. Identify source payloads by version and content digest.
- Use one base-version decision for one cohesive change; do not bump the base
  once per edit.

### Changelog

- Maintain `CHANGELOG.md` as the durable record of completed repository changes,
  including work that has not been committed to Git.
- Use UTC for every date and time recorded in `CHANGELOG.md`. Include the
  explicit `UTC` suffix on version timestamps.
- Group package versions by the next minor-version boundary and use
  `## Until <major.minor>` as the group heading. For example,
  `Until 4.1` contains the `4.0.x` versions.
- Give the active package version a
  `### <major.minor.patch>[.devN|.postN] (YYYY-MM-DD HH:MM UTC)` heading.
- Keep version groups and package versions newest first. Do not assign
  permanent sequential numbers to history entries.
- Put one concise `Overview:` paragraph immediately below each package version
  heading that describes the release as a whole.
- Follow the overview with concise, past-tense bullets covering only material
  completed outcomes. Keep each bullet to no more than two lines and omit
  minor implementation details.
- When more work, `.devN` builds, or `.postN` builds belong to an existing
  version, revise its overview and bullet list.
- Before finalizing the base version, consolidate overlapping bullets and
  remove transient build notes, superseded implementation details, and other
  trivia. Preserve user-visible changes, significant technical decisions,
  compatibility notes, and migration requirements.
- Record documentation-only, comment-only, and formatting-only work even though
  it does not require a package version bump.
- Add the changelog entry as part of the change before handing it off.

### Git commits

- Follow [Conventional Commits 1.0.0][4].
- Format the first line as `<type>[optional scope]: <description>`.
- Use lowercase types such as `feat`, `fix`, `docs`, `refactor`, `test`,
  `build`, `ci`, `perf`, `style`, `chore`, or `revert`.
- Use a short noun for an optional scope, for example
  `feat(quarry): add category query`.
- Mark a breaking change with `!` before the colon or a
  `BREAKING CHANGE: <description>` footer.
- Keep each commit cohesive. Use a body and Git-trailer-style footers when
  additional context or references are useful.

## Verification

Before handing off a material change, run relevant offline Python/PHP
suites, source import/boundary/upload checks, and the checks below:

```text
uv lock --check
uv run ruff check --no-fix .
uv run ruff format --check .
uv run python scripts/upload_toolforge.py --dry-run
```

[1]: src/backend/AGENTS.md
[2]: https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings
[3]: https://semver.org/
[4]: https://www.conventionalcommits.org/en/v1.0.0/
[5]: https://docs.pola.rs/api/python/stable/reference/index.html
[6]: https://docs.pola.rs/user-guide/migration/pandas/
[7]: src/updater/AGENTS.md
