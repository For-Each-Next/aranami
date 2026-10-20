# Shared infrastructure instructions

These modules provide read-only sources, source configuration, durable report
storage primitives and validated readers, and generic text/date mechanics.
Backend and presentation code depend on this layer. Shared code must not import
`backend`, `updater`, or `webservice`, initialize clients at import time,
select publication targets, or publish edits. Keep update workflows,
reusable data schemas, runtime logging and state, and website collection
in the backend. Keep wiki formatting/publication in the updater and web
rendering in the webservice.

Source-adapter rules are in [sources/AGENTS.md](sources/AGENTS.md).
Cross-component contracts are in the
[architecture guide](../../docs/architecture.md).
Runtime data belongs under the caller's working directory outside `app/`.
Use native Polars frame expressions and preserve measured zeroes and nulls.
Never introduce compatibility imports for retired application packages.
