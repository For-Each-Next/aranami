# Repository instructions

These instructions apply to the entire repository.

## Repository boundary

- This repository is only for developing, testing, and building the Aranami
  wheel on a development computer.

## Scoped instructions

The root instructions remain in force throughout the repository.
[Aranami package instructions][1] add runtime, storage, architecture, and
layer-specific rules for `src/aranami/`.

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
- Before a base version is formally published, distinguish its test wheels
  with sequential PEP 440 `.devN` suffixes. Publishing the base removes the
  suffix; that formal version is then immutable.
- After a formal release, distinguish test wheels leading to the next patch
  with sequential `.postN` suffixes on the formal base. For example,
  `0.1.1.post1`, `0.1.1.post2`, and later test wheels lead to formal `0.1.2`.
- Build wheels with `uv build --wheel --clear` so `dist/` contains no stale
  artifacts. Before every explicit build, advance to the next unused `.devN`
  or `.postN` number and regenerate `uv.lock`. Every wheel build gets a
  distinct version, including a rebuild of identical contents.
- Before removing `.devN` to produce a formal base release, or replacing a
  post-release sequence with a new base version, perform a final cleanup pass.
  Remove temporary scaffolding and trivial details, consolidate overlapping
  implementation and documentation, and keep all material behavior and
  decisions intact.
- Keep one evolving `CHANGELOG.md` section for the active wheel version. Include
  its current suffix in the heading, such as `0.1.1.dev3` or `0.1.1.post2`,
  and replace it with the formal base or next patch version when published.
- Documentation-only, comment-only, and formatting-only changes do not change
  the base version, but every wheel build still advances its suffix.
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
  completed outcomes.
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

Before handing off a material change, run the relevant checks:

```text
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv build --wheel --clear
```

[1]: src/aranami/AGENTS.md
[2]: https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings
[3]: https://semver.org/
[4]: https://www.conventionalcommits.org/en/v1.0.0/
