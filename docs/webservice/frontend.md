# Website development

Django serves Aranami's multilingual pages and saved-data APIs. Use this guide
when changing the dashboard, maintenance, newsletter, reports, logs or About
pages. Requests read retained observations; background acquisition remains in
tasks coordinated by runtime.

[Quality analysis](quality-analysis.md) documents the saved quality charts,
article directory, hourly background work and memory bounds.

## Sources and rendering

`src/webservice/server.py` constructs isolated Django URL patterns and views.
`asgi.py` routes HTTP through Django and retains bounded read-only WebSocket
subscriptions and explicit service startup/shutdown observations. Construction,
imports and HTTP requests do not start jobs or write runtime files.
`settings.py` configures the read-only site without database, session, admin or
authentication applications. Uvicorn remains the Toolforge ASGI entry point.

Django's supported Jinja2 backend renders authored HTML from `templates/`;
`public_html/` holds authored CSS, plain JavaScript modules and fixed artwork.
Both local checks and source deployments read these same directories. There is
no frontend compilation, generated-template tree, npm installation or browser
framework. The source uploader validates the complete reviewed file inventory
and content hashes, including the templates and their imported modules.

Use Python 3.15 with `uv sync --locked`. Node.js 24 or later is needed only for
browser checks. Run `node --test tests/toolforge/test_*.cjs` to exercise the
browser behavior without npm dependencies.

## Native controls and DOM ownership

Templates own semantic cards, tables, GET forms, links, disclosures and native
`dialog` elements. Local CSS owns appearance, responsive wrapping and scrolling.
Preserve focus visibility, labels, contrast, keyboard access and forced-color
support. Use platform fonts without external requests. Retained icon artwork
keeps its original source and license attribution.

JavaScript enhances sorting, pagination, copying and fragment navigation through
browser APIs. Write purpose comments and JSDoc for non-obvious helpers, with
four-space indentation and named functions. Module entry points load using
`type="module"`; standalone live-status, newsletter and redirect scripts remain
independent. Preserve supported native navigation when scripts fail.

On fragment replacement, validate the response, reject stale requests, replace
only the owned region, initialize native controls and restore focus. History
owns its table and matching execution dialogs independently of live status.
Editor tables reorder existing escaped cells and preserve missing values last;
each dialog resets its own sorting and pagination when opened.

Native dialogs keep focus trapping, Escape/close behavior, background inertness,
focus restoration and closed-state hiding. Live status preserves open dialogs
while updating saved facts. Tools, language and contents disclosures support
mouse, keyboard and touch. Stable section headings expose hover/focus copy
controls without shifting title text. Keep ASCII fragment IDs across locales,
localize only the label, and omit the language query from copied links.

## Saved-data presentation

Reports presents only on-wiki reports, grouped by configured routine with
destination links, observed schedules and independent execution results.
Its recent history excludes source collections and covers the last 168 hours,
with ten records per page. Each response includes only that page's rows and
details; enhanced pagination replaces the table and its matching dialogs in
place, preserving surrounding report cards, focus and browser history. Native
GET links remain available without JavaScript. Live status refreshes report
cards separately, preserving the selected history page. Publication results
retain their table headers and horizontal scrolling on narrow screens.
Recorded source and acquisition notes belong in the status dialogs. About-page
version information uses one source version and its recorded UTC build time;
process restarts do not change that timestamp. Older builds without metadata
omit the update time.

Server status lists active website refresh and data-maintenance collections
owned by the current scheduler process. It shows the named work, actual source
being read, measured counters when available, and start time. Completed runs,
previous process owners and unrelated wiki reports do not appear as current
background work. Missing status remains unknown; expired scheduler heartbeats
replace activity with an explicit stale notice, even if the web process remains
reachable. The existing read-only live protocol refreshes this row.

Completed daily cards show the confirmed report date; hourly cards show the
latest successful check hour, including checks that needed no wiki edit.
Active cards describe the observed work and show its start time. Completed
cards show the last execution time and the next observed schedule. Retain the
last confirmed coverage across later failures without implying that the failed
check succeeded. Grouped report destinations remain individually linked;
assessment lists use compact `a`–`f` labels with full destination titles.
Execution history combines the UTC start date and time in one column and
renders task names as ordinary text. Stage descriptions link source pages,
page IDs and documented database tables, and use sentences for destinations.

Project snapshot dates identify completed days; capture and traffic dates are
separate facts. Do not relabel a midnight membership snapshot as a fresh evening
assessment when Pageviews arrives later. API/HTML renderers consume prepared
facts or read-only owner APIs, never raw source clients. Unknown data differs
from confirmed empty data; a failed refresh retains the last healthy result.

Cards keep article headings, complete descriptions and semantic metadata lists.
Homepage cards omit class and importance chips; other views retain their
assessment presentation. Multiple links retain independent destinations and
focus targets. Review nomination/response dates and distinct participant counts
require evidence; nominator-only replies are not responses. Popularity's site
rank is distinct from project rank. Showcase icons reflect saved appearance
kind, independently of current class. Display limits never trim collected facts.

Homepage highlights order all types strictly by descending display date and
show full-year labels. Explicit featured/good assignments and anniversary slots
in the next two UTC days can appear alongside actual displays. Upcoming DYK
slots use only closed-passed nominations, retaining other projects' queue
positions before filtering to this project. Their source-order and adaptive
spacing projection is an estimate, anchored to the latest DYK display revision;
the actual bot can choose a different order or interval. Every unshown item
uses the same localized scheduled prefix, including those estimates. Scheduled
facts do not establish actual appearances, and expired ordinary candidates do
not automatically become future highlights.

Ordinary review cards show the actual latest identified comment independently
of non-nominator response evidence. The opening comment and later comments by
its author can be the latest comment. Older saved rows can display their
recorded response as a fallback only when latest-comment fields are absent;
an explicit unknown latest time remains unknown. Keep every known start,
latest comment, participant/comment count and full section source length
visible, including a single participant and a single comment. Counts and size
remain visible without known timestamps, and unavailable observations remain
unfilled. Display source length as decimal `kB` with an exact-byte tooltip.

DYK, GAN, FAC and FLC links describe voting as under review or ended rather
than naming the review area. Elapsed deadlines establish only that voting
ended. Explicit DYK closing results identify selected or unsuccessful entries;
bot qualification alone never establishes selection. Closed entries remain
visible while their nomination remains on the current source page. Successful
collection removes cards absent from that source without deleting their
retained nomination history.

GAN uses seven days and FAC uses fourteen days, respecting explicit source
periods when they differ. DYK permits a four-day period plus a conditional
three-day extension; FLC permits fourteen days plus a conditional fourteen.
Without a verified early closure, these conditional reviews remain active
through the possible extension. Printed optional FLC extension dates do not
prove activation, and the application does not independently adjudicate voter
eligibility or infer successful selection from a vote tally.

Active-discussion cards use a semantic article shell with separate links from
the subject to its page and from the topic to its original talk-section
anchor. Display the parent subject above the normal-weight topic name, with
the opening excerpt on the following line and a `0.5em` gap when that excerpt
is present. The subject remains bold. Assessment chips appear only in the
full discussion view.
Subject and topic titles use ordinary link colors. Legacy records resolve only known
Chinese Wikipedia talk namespaces to their corresponding subject namespaces.
Embedded files never enter excerpts;
retain their contribution to the full topic's UTF-8 source size. Supporting
text contains only start/latest-comment indicators, participant/comment counts
and measured section size with a nonbreaking space before `kB`. A one-comment
topic retains all known indicators, including the identical first/last times.
Latest-comment times link to their verified comment anchors when available.
The discussion icon adds a blue plus for a new reply: more than one recorded
comment and the latest comment within 24 hours. An amber flame marks a hot
discussion: at least three participants and ten comments in the whole section,
with a comment in the last seven days. Both marks appear when both conditions
hold. Use the server render clock shared by elapsed labels; missing or future
timestamps do not establish either state. Counts describe the saved section,
not the number of comments during either time window. Localized accessible
tooltips explain these criteria; the shapes distinguish states without color.
Keep those links independent; preserve author/time tooltips to the
minute while retaining exact source timestamps. Opening excerpts consume
revision-pinned rendered comments from DiscussionTools, retaining external-link
labels and expanded template prose. A complete opening comment has no added
ellipsis; only an actually truncated comment gets a continuation mark.
Overlapping DiscussionTools trees share identical comment observations; collapse
those repetitions and leave conflicting IDs unknown. Pages without recent
signed topics do not need rendered-comment requests.

The dashboard shows topics with comments within seven elapsed days and links
to `/discussions/`. Order its topics by subject type: articles, drafts,
projects/portals, categories/templates, then other subjects. Within each type,
newest comments lead regardless of assessment grade, with stable subject,
topic and source-link ties. All subject types remain eligible.
The dedicated discussion page shows twenty-eight elapsed days in exclusive
article groups (0–7, over 7–14, over 14–21 and over 21–28 days), then drafts,
projects/portals, categories/templates and other subjects. Keep all eight
headings, including confirmed empty groups; unavailable observations remain
a separate state. Within every group, newest comments lead regardless of
assessment grade, with stable subject/topic/source-link ties. Classes from the
shared profile identify content types; legacy unclassified rows fall back to
known subject namespaces. Both discussion views omit assessment chips and
link verified latest-comment times to their original comments.
Recognized reviews appear only as content reviews: exclude matching saved
section or verified comment URLs from both general-discussion views. A bare
talk-page review link must not hide unrelated topics on that page.
Ordering and grouping never rewrite unknown assessments.

Maintenance filters and sorting apply to the complete saved selection before
10/20/50-row pagination. Preserve title/page-ID ties, unknown-last behavior,
assessment hierarchy and URL history. Category selection and title search use
native forms; enhancement replaces results in place and ignores stale requests.

The redirect worklist can explicitly check displayed talk pages for restored
project banners through Wikipedia's API. Confirmed matches are hidden for the
browser visit; failures leave findings visible and saved evidence unchanged.
Historical text is a sampled revision, linked to its source when available.

Newsletter source stays literal, escaped text in autosizing readonly controls.
Copying must not reinterpret wikitext. Keep a native fallback when Clipboard
fails, date bounds, issue identities, labeled controls and source-linked edit
URLs. [Newsletter drafts](newsletter.md) defines editorial behavior.

## FA/GA editor statistics

The Tools menu links to `/quality-authorship/`, a directory of editors with
retained experience writing FA, FL, A, AL, GA and Bplus video-game articles. Its
introduction helps newer editors find potential mentors and links to
human-checked year-end reviews and earlier content introductions.
Only articles whose latest saved project assessment is FA, FL, A, AL, GA or
Bplus contribute to these totals. The current project page list supplies unique
`page_id` keys; historical records join through those identities. Delisted,
departed and redirected articles retain their history but leave the directory.
Hourly project collection saves current eligibility under `reports/data/`;
missing eligibility makes the directory unavailable until collection succeeds.
The native table shows editor and talk links, independent all-article and FA/FL
counts such as `44 articles (7 featured)`, and the latest observed public edit.
The two counts open separate native dialogs with the complete and featured-only
selections. Article, grade and listing-date columns are sortable; their default
order is newest listing first regardless of grade. Editor, contribution-total
and latest-activity columns are also sortable. Row numbers are not sortable.
Sorting resets pagination, and opening either dialog restores its default order
and first page. Native tables and fragment links work without JavaScript.

Each registered primary author with the greatest retained contribution receives
one whole article count. Every other editor reaching at least 33% of the
eligible content also receives one count, including a qualifying runner-up.
An article may therefore count for multiple authors. Scores use the final
revision of the listing's UTC day. References are excluded when reliable exact
alignment is available; otherwise the retained API-byte fallback includes them.
In exact-source scoring, comments count and whitespace remains in the eligible
total, with unknown ownership because WikiWho omits separators.
Anonymous and unassigned bytes remain in the denominator. Each editor/article
appears once at its strongest retained grade and latest listing of that grade.
Nominations alone do not establish authorship. The directory reads the current
v4 byte evidence and preserves older contracts for their original consumers.

A compact icon after a shared article title identifies the other qualifying
registered editors in a localized, escaped tooltip and accessible label. Article
labels follow the selected Chinese conversion table; canonical titles, URLs and
review fragments keep their source spelling. Listing dates link only retained,
verified review anchors. Latest activity links to the editor's contributions and
remains a separately saved public-edit observation; missing dates stay unknown.
Page requests never query Wikipedia or the replicas.

The optional `Count B-Class` control includes current B articles and B-class
lists with `?count_b=1`; the default omits them. The selected total describes
developed articles and links separately to its high-quality subset, for example
`47 articles (45 high-quality | 7 featured)`. Zero subsets are omitted, and both
nonzero subset links share one pair of parentheses.
The switch updates saved results immediately without reloading the page. It
retains focus, locale and URL state; superseded responses cannot replace the
latest choice. Failure retains the prior results and restores its switch state.
Direct GET links switch views without JavaScript; no Apply button is rendered. Sorting and pagination
are restored on refreshed tables. Main-page highlights refresh hourly; newsletter
facts retain their independent daily cadence.

B/BL listing dates use the first talk revision of the latest continuous
literal B/BL banner range, including page creation when fully visible.
Authorship uses the exact article revision at or before that UTC timestamp.
The same greatest-contributor or 33% rule applies. Dates link to the entry talk
revision. Parquet retains exact talk identities, timestamps, article cutoffs
and contributor evidence; unchanged talk/parser keys reuse saved results even
after article edits. Changed talk identities invalidate reuse. This operational
listing date does not assert a formally reviewed effective-class promotion.
A collected/total count distinguishes pending history and revisions from observed
authorship; missing revision evidence never claims that an article has no
authors. Current project membership removes departed, redirected or reclassified
pages, while their successful old revision observations remain durable.

## Localization and live status

About-page resources are sampled and streamed once per second. A small current
sample remains separate from execution history; readers accept only fresh
samples from the exact running hub boot. The socket watcher shares file checks
across subscribers, with one-second HTTP fallback on server-status panels.

The Memory value shows the hub container's working set against its allocation:
`memory.current - inactive_file` from cgroup v2, clamped to zero. This excludes
inactive filesystem cache while retaining active file pages, anonymous memory
and kernel allocations. Missing counters display as unknown. The calculation
does not reclaim caches or change the container's memory limit.

Support English, Simplified Chinese and Traditional Chinese. Query language
precedes the one-year cookie and Accept-Language. Preserve canonical source
titles; regional OpenCC variants apply to saved extracts, not identity. Propagate
locale through live requests and links. Visible times use UTC; machine values
retain offsets and hover details retain complete instants.

English relative ages use `m`, `h`, `d`, `w`, `mo` and `yr`. Simplified and
Traditional Chinese use localized words for minutes, hours, days, weeks,
months and years. Each locale retains its past/future wording. Initial HTML
and live clocks share whole-unit
boundaries: weeks contain seven days, and years contain 365.25 days split into
twelve equal months. Sub-minute ages use just-now or soon labels. Review and
discussion tooltips retain the full UTC time to the minute, and machine
timestamps retain their original precision. Execution durations retain seconds.

Parquet filenames in activity details open a metadata and data dialog. Each
page requests up to 50 rows only after the file or page is selected; ordinary
GET links work without scripts, preserving modified clicks and restored focus.
Preview reads use bounded lazy Parquet slices
and verify storage metadata, schema and actual row counts from one open file.
They do not validate a complete dataset's content digest or establish accepted
collector data. Production caches and retained report-data roots are public;
preview storage, hidden paths and symbolic links are excluded. Replaced or
expired disposable files can become unavailable while their activity remains.

API activity verbs open request forms rather than executing endpoints.
MediaWiki links prefill API Sandbox fragments from retained public selectors;
missing request parameters are not reconstructed. Other APIs use their primary
reference pages when a suitable request form is unavailable.

The read-only live protocol is shared by WebSocket and HTTP. Each full update
carries `schema`, a process `instance`, increasing `sequence`, saved `store_id`
and `revision`, `observed_at`, and localized HTML. Transport order is independent
of the wall clock and saved-store resets. Validate the envelope and all regions
before applying a snapshot, reject older responses, and preserve focus and open
dialogs. A heartbeat proves connectivity; it does not refresh saved evidence.

One `webservice.live.LiveFeed` per application watches saved status, loads each
changed generation once and shares rendered views by locale. Each connection
retains at most the newest pending update, so a slow reader cannot accumulate
history or delay others. The watcher stops when the final subscriber leaves.
An unchanged file is reread periodically to recompute freshness. Construction,
subscriptions and HTTP fallback never collect source data or write runtime files.

HTTP verifies visible pages every 30 seconds and supports conditional requests;
reconnect uses bounded backoff and obtains a complete snapshot. Hidden tabs stop
transport work. Failed reads preserve the last accepted view and expose an
availability warning. Keep the monotonic server-time anchor for clocks, apply
the saved service/dataset freshness policies, and never infer task progress from
elapsed time. Completed durations remain fixed. Dynamic responses are no-store;
exact digest assets can use immutable caching and ETags. Allow the canonical
secure WebSocket origin in CSP.

## Verify changes

Run Python and browser unit suites, then render synthetic fixtures and
inspect Chromium results as described in [Development](../development.md).
Cover all locales and widths from 320 to 1920 CSS pixels, long titles, missing
observations, narrow tables, native fallback, copying, sorting, history and
modal focus. Browser automation complements screenshot review; it does not
prove real-device touch or screen-reader behavior.

Historical Codex and Vue notices remain at `/codex-license.html`; local icons retain their
credit tables. [Branding](branding.md) owns artwork and attribution.
