# Newsletter collection

The Chinese newsletter workflow prepares quarterly and annual facts for manual
wiki publication. It reads project snapshots plus retained event, traffic,
registration and award evidence. It saves complete observations before updating
the current draft. The website renders saved facts only; it never collects,
publishes or writes data. [Newsletter drafts](../webservice/newsletter.md)
explains editorial use.

## Issue lifecycle and cutoffs

`shared.newsletter` owns quarterly identities. Issue `2027-01` is issue 29,
covering October–December 2026, with a separate annual feature for all of 2026.
`Module:VGNL/data` supplies lifecycle evidence: a negative date year means
cancelled, a missing day means draft, and a concrete day means published.
The bounded Lua-literal reader never executes source code. Malformed registry
evidence preserves the previous complete result. Publication dates do not
change reporting intervals.

Daily acquisition refreshes every started unpublished issue, including delayed
issues after quarter end. January's annual feature becomes eligible on January
1 of the reviewed year; ordinary quarterly pages start at their quarter's
beginning. Published/cancelled captures freeze. Historical event reads use an
exclusive cutoff, capped at the period end. A current editorial enrichment
retains its own actual observation time and cannot masquerade as historical
quarter/year-end evidence.

## Project observations

Daily collection uses the committed project dataset. Files name the completed
day; the exact-boundary reader accepts the following capture date. Legacy
assessment readers instead accept the completed date, so pre-cutover lookup
explicitly translates boundary minus one. Missing or corrupt post-cutover
history never falls back to a different date or current grades. The
[retirement checklist](../transition.md) records the cutover boundary and
remaining dependencies.

Snapshot differences measure membership/assessment observations, not article
creation, actual promotion dates or revision counts. Annual endpoints require
the correct next-January-1 capture when available. Actual membership observation
timestamps remain separate from register reads and draft preparation times.

## Sources that remain independent

| Newsletter fact | Evidence |
|---|---|
| Stock counts, classes, importance | Committed project population at the selected boundary |
| Curated new-article counts | Register and year archive, restricted to completed dates within the issue |
| Quality/DYK/reviews | Exact talk histories, nomination/archive sources and durable milestones |
| Homepage appearances | Shared durable appearance evidence and saved homepage observations |
| Authorship | Retained listing-revision token attribution and explicit source roles |
| Popularity | Durable detailed daily Pageviews and exact calendar periods |
| Revision counts | Replica aggregation of available edits in the half-open year |
| Awards | Verified dated scoring source observations |

Retain article history, milestones and authorship across all required years.
A recorded nominator is not automatically a main author. Preserve explicit
non-main-author claims, all contributor token/share statistics and tied leading
authors. DYK contributor fields come from explicit supported nominations;
a talk-page date alone supplies no authorship.

Hourly article acquisition reads Article history, including Bplus promotions,
and associated redirects. Use exact talk revision/parser identities;
unchanged revisions reuse retained evidence across month boundaries. A changed
revision or parser/alias contract requires new processing.
Successful bounded batches survive interruption. Source dates remain unknown
when absent; current assessment dates never become promotion dates.

The new-article register can receive corrections after a quarter ends, so read
it at actual acquisition time for delayed drafts. Match exact titles, then
canonical titles/page IDs across moves, and join the selected assessments.
Keep registration and grade timestamps separate. Missing grades render as
unassessed without asserting a project banner. Counts reconcile to registered
articles; missing historical grades must not borrow later observations.

## Annual calculations

Annual quality cohorts count each article once at its surviving grade at that
year's boundary, using actual promotion/removal evidence. Later demotions do
not rewrite older cohorts. Missing dates remain outside dated counts. Current
or exact-boundary grades can gate the final year's cohort; older years retain
their own evidence. Keep FA/FL, A/AL, GA and Bplus distinctions in source facts.

DYK cumulative statistics count first appearances, not repeated nominations.
Completed quarter points and a separate current point extend an evidenced
historical baseline; December 31 appears once. Game-year classification uses
supported article categories at the cutoff, then verified infobox dates.
Unclassified subjects remain unclassified. Dated games precede observed
fictional-character, biography/company and exact `音乐` project memberships;
those current memberships retain independent timestamps.

Annual traffic uses January 1 through the last supported completed date, never
a rolling year. `data.pageviews` supplies measured daily rows and explicit
coverage; its annual calculation requires actual observations across the period.
Missing pages/days keep total coverage unknown while measured ranking entries
can remain useful. Complete formal periods take precedence over retained
historical periods; incomplete new observations cannot replace accepted facts.
Monthly winners from historical HOT/HOT-v2 reports must have exact calendar
intervals and retain their source revision. The temporary importer checks the
first four UTC days after month end for delayed publication and writes the
normal `data.newsletter.popularity` store. Accepted periods require no further
reads of the old format.

Annual ACG awards include all positive gains and mark every first-year scorer
with `N`; quarterly awards retain fifth-place ties and their ten-point newcomer
threshold. Community honors, feature prose and interview answers stay editorial.

## Persistence and reuse

`tasks.zhwiki.newsletter.refresh` coordinates acquisition;
its `collection` module prepares facts. `data.newsletter.observations` and
`data.newsletter.archive` persist them; `data.newsletter.schema` validates reads. Updater renders wikitext and webservice presents
it. Source facts live in `reports/data/newsletter/observations/`; immutable
quarterly archives live in `reports/records/newsletter/<issue>/YYYYMMDD/`.
Annual features have a separate `annual-feature/YYYYMMDD/` stream.

Every retained date is a complete independent JSON observation with digest,
actual capture time and validated inline facts. Reading it needs no predecessor,
source client or stored rendered page. Formatting can evolve without changing
its facts. Atomic catalogs index available dates; gaps remain unavailable.
Archive failure prevents replacing the current presentation. Annual and
quarterly availability are independent.

Raw revision text is a disposable exact-revision cache, refreshed against fresh
metadata/SHA-1 before reuse. Hidden content cannot use an old cached body.
Article history, milestones, authorship and newsletter archives are durable.
Alpha suspends new automatic article-evidence purges while dependencies are
migrated; existing tombstones remain effective.

## Verified wiki projection and migration

Newsletter figures use a common data-source interface. An issue can connect a
temporary historical provider while the formal source acquires the necessary
coverage. Provider selection belongs to preparation/configuration; calculations
and rendering consume the same structured facts. Delete temporary inputs only
after the formal provider covers their periods and verified outputs no longer
depend on them. See [Retirement checklist](../transition.md) for checkpoints.

Keep wiki-specific interpretation in source owners. The established reference
contracts include VGNL data revision 93292299/module 92339407;
January annual feature 91016278; October quality draft 93279502;
Memo documentation 93779044; Piechart module 91397381; and Graph modules
95346885/95350043/95358684. Re-audit current source, docs, aliases and emitted markup
before changing these template contracts; a remembered name is insufficient.

Retain the January feature's four ordered level-four data sections,
`內容評審`, `新章推薦`, `条目报告`, `榮譽表彰`. Use the current renderer for
all retained dates, explicit inline cutoff notices, native graph/pie contracts,
source-linked dates and blank editorial scaffolds. Unknown annual figures
remain complete placeholder sentences rather than fabricated totals.

Annual features can require evidence predating the daily series. Preserve
original hashes and unknown fields; the [retirement checklist](../transition.md)
tracks remaining historical-source dependencies and removal gates. Calendar
age alone does not establish that historical data is unused.
