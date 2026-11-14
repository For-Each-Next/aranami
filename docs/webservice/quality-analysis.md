# Current quality articles and contributors

The `/quality-analysis/` page combines the current Chinese Wikipedia Video
games quality cohort and its contributor statistics. Former
`/quality-authorship/` links redirect here while preserving language and the
B/BL selection. Only currently selected articles and lists count; historical
promotions and departed project members do not add author credits.

A leading quality-by-importance checkbox matrix selects any current article
class, from featured through unassessed. The default includes FA, FL, A, AL,
GA and Bplus. The inclusive year range defaults to 2002 through the current
year; unknown dates are included by default through a separate checkbox.
Matrix labels contain no counts. Every chart and both directories use the
same cohort. Table-local title, author and contribution-rank filters further
restrict the article directory without changing the cohort charts.

The unchecked listing-version checkbox uses prepared current-revision
attribution. Checking it selects prepared listing-time evidence. Both are
collected hourly in the background; changing the setting only reads saved
Parquet. Unknown evidence never falls back silently to the other version.

## Overview and article directory

The overview cards show total membership and the immutable report generation's
preparation time. Source observation times remain separate saved metadata.
Quality and importance pies include exact counts and percentages. Their colors
come from the assessment profile; importance colors follow Chinese
[Template:Importance/colour](https://zh.wikipedia.org/w/index.php?oldid=95173028).
Annual columns cover a continuous year range beginning in 2009, retaining
zero-count years. They describe the latest supported listing at each current
grade, not historical annual project totals. Unknown dates stay outside bars.

The article table shows ordinal, article with talk link, quality, importance,
listing date, main contributors, prose count and page size. Display titles use
spaces instead of underscores; original titles and page IDs retain their
identity. The ordinal column cannot be sorted and follows the displayed order
after sorting, filtering or pagination. Other missing observations sort last.
Table-local filters do not change the whole-cohort overview; the matrix does.
Clicking a chart opens the corresponding article list from saved rows. Annual
segments select both year and grade, independently of directory filters. No
source requests occur. The annual data table labels and bolds its total column.
The B/BL coverage notice lists every article whose attribution is still missing,
with article and talk links so those cases can be inspected.

Listing dates link to an explicit retained article oldid when available. Missing
oldids are resolved from the listing date through Wiki Replica revision
metadata. A separately retained review link appears beside the date. A date
without verified revision metadata remains unlinked until acquisition resolves
it; a talk revision is never substituted as an article oldid.

## Prose units and attribution

Prose is measured on the observed current article revision. Each Chinese
character counts as one unit; each Western word or continuous number block
counts as two. Spaces and punctuation are excluded. For example,
`PlayStation 5` counts as four. Shared extraction rules omit references and
other non-prose sections; list grades include readable template values.
Legacy word counts remain readable internally but are never displayed as these
new character units.

Miniature horizontal violin charts occupy the third column of the quality
and importance distribution tables, beside each count and percentage. They use
precomputed Gaussian density points and median marks; their accessible labels
retain measurements without adding a separate chart or explanatory paragraph. NA and Bottom importance groups are omitted, and A/AL groups
hide when their current count is zero. Central
68% and 95% intervals are linear empirical 16th–84th and 2.5th–97.5th
percentiles. They describe the measured current cohort rather than minimum
assessment requirements. Measured zero differs from an unknown measurement.
Density uses the normal-reference bandwidth over 129 positions between the
observed minimum and maximum. Constant samples remain a vertical mark; unknown
measurements add no density. These values are saved before HTTP rendering.
Page size uses known current SQL byte length even while prose is pending.

Main contributors retain the registered primary-author or at-least-33% rule.
The adjacent information dialog gives the actual saved attribution revision
endpoint date and editors whose attributable byte share is strictly greater than 10%.
Anonymous contributors link to their public contribution history. The endpoint date links to the exact revision and an XTools WikiWho
visualization; the first revision is not displayed. Missing endpoints remain unknown, and absence of attribution is distinct from an
observed result with no editor above the threshold.

Existing high-quality attribution can use a saved end-of-listing-day revision
that differs from the review template's explicit oldid. The dialog preserves
that actual proof range; it does not claim that the listing link and attribution
endpoint are the same revision. Author summaries use only the current cohort
and grade, including the verified AL-to-A review-source equivalence.

## Acquisition and cache reuse

The collector first reads current membership, grades, importance, revision IDs
and byte lengths from Wiki Replicas. Required revision boundaries and missing
creation metadata also use read-only SQL first. Content is unavailable on the
replicas, so missing current-page and historical-revision bodies are acquired
in batches of up to fifty exact revisions. Each body is checked against its
expected page and revision identity. Known SQL metadata does not cause an extra
page-info API request. Source APIs that provide no batch operation retain their
bounded per-article request.

Successful history ranges, cutoffs and attribution evidence remain reusable
while their relevant identities and boundaries are valid. An optional metadata
field is filled independently rather than invalidating an attribution proof.
A changed current revision or newly required character measurement requests only
the missing body facts. Partial histories resume their saved predecessor cursor;
unread candidates do not lose their next-pass priority. Presentation-only
importance colors are excluded from the existing B-history parser identity.

## Saved reports and lifecycle

The raw quality-analysis Parquet retains current membership and measurements.
`data.articles.current_quality` projects it with saved attribution into one
atomic ReportStore generation containing both B/BL variants, precomputed charts,
article details and author summaries. Startup prepares this projection from
saved facts before ordinary source dispatch. Bounded analysis and authorship
passes refresh it after their checkpoints. HTTP readers only validate, select
and serialize a variant; they never join, aggregate, acquire sources or write.

Analysis and all-grade authorship keep separate short-lived workers scheduled hourly
at :02 UTC while ordinary work is idle. The parent retains no article bodies or
worker frames. Each pass checkpoints bounded work and releases parser/native
memory on exit. Foreground work and the protected midnight window stop admission
of new source batches. A nonblocking collection lease prevents duplicate writers.

The authorship pass admits up to eighty history candidates within its overall
240-second budget, reserving approximately ninety seconds for talk history and
up to forty article attempts afterward. Existing successful evidence is skipped.
A completed history batch processes at most fifty selected revision bodies at a
time. Native Polars pools default to two threads unless explicitly configured.
Rendered HTML is bounded to eight MiB per entry and cached by saved generation,
locale, the compatibility B/BL mode and attribution basis.

## Browser pagination

Only the initial twenty article rows are emitted as table markup. Remaining
rows use a compact saved catalog; the browser creates the visible page and
prepares the preceding and following two pages during idle time. It evicts
other generated rows. Filtering, sorting, chart dialogs and adjacent-page
preloading do not contact upstream APIs or replicas. Chart dialogs likewise
materialize only their visible page and share the standard pagination footer. Author filters use saved
WikiWho contribution ranks: first, top two, top three or all; tied shares
receive the same minimum rank.

All site table footers share typography, spacing, record counts and navigation
button styling. Contributor tables display one total article count without
quality-subset parentheses. Three-column article-contribution dialogs use a
narrower width. Page-size values are plain text.
