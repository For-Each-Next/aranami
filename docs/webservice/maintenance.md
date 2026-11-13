# Maintenance worklists

Use `/maintenance/` for saved category worklists and `/checks/` for
disagreements between wiki sources. The header's Tools menu links to the checks.
The website lists evidence for an editor to review. It
does not correct articles, templates or project pages automatically. Background
collection supplies the results; opening or filtering a page makes no wiki
source requests.

## Redirect recovery

Use `/redirects/`, also linked from Tools, to review current mainspace redirects
whose talk pages carry deletion-history notices and formerly carried the Video
games project banner. The scanner resolves current redirects to the banner and
deletion templates through Wiki Replicas, including indirect aliases. A database
join selects deletion-template talk pages whose current subjects are redirects,
in batches of fifty ordered by talk-page ID. Pywikibot preloads current talks
and subjects in bounded groups of fifty and excludes talks that still have the
banner. Historical revisions are read only for qualifying pages. The scanner
never follows the subject redirect to another article when checking identity.

Each match links the observed redirect revision, the historical talk revision
containing the banner, and historical article content when found. The excerpt
removes leading templates, comments and reference markup for quick reading.
Missing historical article content remains explicitly unavailable. Follow the
revision links and check the latest wiki state before making a manual edit.

The scanner samples the newest ten and oldest five talk revisions. For matching
pages, it searches the newest twenty and, if needed, oldest five article
revisions for nonredirect content. These samples can miss banners or articles
that occur only in intermediate history. A completed scan describes the saved
deletion-template selection and these history samples, not all past project
redirects.

Results appear as each candidate is saved; the visible page checks for changes
every thirty seconds. Select ten or twenty rows per page. Hide removes a result
from view in this browser, with Show hidden and Restore all controls to recover
it. These preferences stay in browser storage and do not alter the shared
worklist. Without JavaScript, revision links and pagination remain usable.

Collection runs separately on Toolforge while other scheduled work is idle,
starting candidate checks at least 1.2 seconds apart to target up to fifty pages
per minute. Serial API requests are
at least 0.2 seconds apart, including failed attempts. Slow responses, history
reads and server backoff can reduce throughput; source failures retain the
five-minute retry delay. New scheduled work can start immediately; the scanner
yields before its next source request and resumes after the other work finishes.
It checkpoints each completed candidate and resumes after shutdown. Saved
page-ID cursors resume the database selection. Older API
cursors remain readable, and switching acquisition preserves queued pages,
checked IDs and retained matches. An incomplete scan with no matches does not
establish that no relevant redirects exist. A completed pass remains a dated
worklist and does not automatically start another sweep. Once per UTC day,
the collector removes findings whose subjects now have a Video games
PageAssessments entry, including redirect-class assessments. Failed checks
retain the prior worklist and retry later. Title links keep `redirect=no`;
the title and sampled-content links enable the project assessor gadget.

## Article tasks

The overview groups configured maintenance categories and templates. Select a
task, search titles, filter assessments or importance, and sort the complete
saved selection before pagination. Counts describe distinct project pages;
one page can belong to several tasks. Missing or failed observations remain
unavailable rather than becoming a zero count.

Task imagery follows verified wiki templates. The maintenance credits page
uses semantic cards with thumbnails, original author/source links and license
attribution. Its template correspondence table includes the configured main
category links, including multiple categories for composite tasks. Text-only
templates use the literal `{{...}}` symbol.

## Consistency findings

The independent checks page groups findings by issue type in cards, with an
issue list and evidence links in each group. Its update time and coverage remain
separate from category maintenance. These findings never enter the category
worklist's totals or table.

| Worklist | What the evidence establishes |
|---|---|
| Nomination banners | A current FA, FL, GA, DYK or peer-review discussion names a project article whose talk page lacks the corresponding verified banner |
| Recognition templates | A project FA, FL or GA assessment disagrees with the article's award-template presence |
| Project review history | A current A, AL or Bplus article lacks a matching successful action in its literal Article history template |
| Homepage history | A proven FA/GA, DYK, ITN or today-in-history appearance lacks the corresponding recorded talk-page date |
| Missing project-list entries | A certified article is absent from its manual project list |
| Extra project-list entries | A listed article does not match that list's observed certified class |
| Quarterly statistics | The basic article expansion challenge lacks a completed quarter's static statistics row |

Project lists are checked in quality order: FA/FL, A/AL, GA, then Bplus.
The sources are the pages reached through `PJ:VG/FA`, `PJ:VG/ACC`,
`WikiProject:VG/GA` and `PJ:VG/BPCA`. BPCA is the certified Bplus list;
nomination pages are separate evidence.

Project review history checks current mainspace, nonredirect members using
their stable page IDs and normalized assessments. A and AL require a
successful WAR action; Bplus requires BPAR. The existing Article history
extractor supplies the local success vocabulary. A successful action with an
unknown date satisfies this check: the worklist asks for the missing action,
without inventing dates or authors. Other grades and standalone nomination or
project-list records do not establish this history. Missing source pages,
failed reads, malformed calls and multiple active history templates leave
coverage unavailable instead of creating a false missing-record finding.

Each finding retains the target, relevant article/talk links, expected metadata,
source and checked revisions when known, observation time and proven event date.
Pywikibot normalizes source titles and builds page links using the collector's
existing site instance. A missing page, failed request or unsupported source
layout produces an unavailable check, never an inferred metadata defect.

GA display-report rows marked never displayed do not establish an appearance.
Future appearances are not past events. Repeated DYK/ITN/anniversary dates must
cover the proven event. FA/GA date fields use the latest appearance as the
active value and retain earlier dates in a date-only comment. The live
template accepts one active date; a later display does not replace an
earlier retained appearance. Legacy comma-separated dates remain readable.
Verified legacy dated notices can satisfy the equivalent history evidence.
Optional history links and revision parameters are not invented requirements.
The recent DYK, ITN and anniversary scans remain bounded; a clean result
does not establish complete coverage of those historical appearances.

Homepage-history findings combine missing fields into numbered semantic article
cards, ordered by the latest affected display. Each card provides the parameter
changes to copy and a compact disclosure containing every retained appearance
and its evidence. Current correct values and populated notice slots are kept.
The overview renders ten recent articles and links to the complete paginated
worklist; history cards do not use nested list wrappers.
FA/GA keep repeated dates in one field, for example
`maindateFA = <!-- 2018-10-30, 2022-07-23 --> 2026-10-13`.
Only comments containing complete dates and separators establish older
appearances; editorial comments are preserved without becoming date evidence.
Malformed or unknown values withhold a replacement. DYK, ITN and anniversary
entries use fields numbered from one. Copied code aligns keys and separates
groups with
blank lines; an unknown DYK hook keeps an empty entry placeholder. Known
historical hooks, article revisions and permanent
links are included without replacing unknown observations with invented values.
Verified discussion-archive questions can fill a missing hook when the article
and dated round match unambiguously. Migrating an unnumbered group copies all
known companions into a free numbered slot and names the old keys to remove;
existing numbered slots remain intact.

Standalone DYK notices can produce a complete Article history suggestion when
no active history call exists. A removal count appears only when the known
legacy dates, hooks and populated metadata can be preserved safely. The site
never changes wiki text. Copy source remains selectable without Clipboard API
access.

Multiple active history templates require manual review for that article.
The collector retains its evidence and marks affected checks incomplete while
withholding suggested code; other articles continue refreshing normally.

All appearance observations are retained in the shared durable article-history
Parquet alongside talk-page snapshots, with distinct source identities. The
mutable timestamped `consistency/` projection keeps the page's current findings;
refreshing or rebuilding it does not erase older appearances. The initial scan
inventories every dated FA/GA archive page through the previous UTC day and
retains a separate wiki-wide archive with stable subject IDs and exact source
revisions. Daily passes append newly completed dates. Future schedules,
including tomorrow and the following day, remain temporary dashboard facts;
they never become past appearances in either durable ledger. Other homepage
source scans retain their documented bounds.

The dated archive reader accepts verified introduction transclusions,
historical simplified/traditional roots and project-namespace aliases,
the old `/s` and `/摘要` positional subject wrappers, direct introductions,
and redirects to introduction pages. Direct prose must explicitly mark its
first article-bearing subject in bold; image captions, conversion metadata
and later formula links do not become subjects. A corrected source may
retain an unlinked bold title: the Cleopatra display source still names
its subject even though a later editor disputed its GA selection. Award
status and a recorded homepage display remain separate observations.

Daily sources can reuse another dated source. The reader follows at most
eight references, rejects cycles and retains the outer source's display
date and revision. Existing text from the same inventory is reused before
another read. Unsupported or ambiguous sources fail the inventory without
advancing its coverage or replacing its previous complete generation.

These historical contracts were audited on 9 November 2026 against all
12,201 completed canonical daily sources then present: 4,829 featured-slot
and 7,372 good-slot sources. Alternate dated roots were redirects to the
same canonical dates. Reference revisions include the
[former featured root](https://zh.wikipedia.org/w/index.php?oldid=52642498),
[featured summary wrapper](https://zh.wikipedia.org/w/index.php?oldid=52777241),
[good summary wrapper](https://zh.wikipedia.org/w/index.php?oldid=31145059),
[bold formula lead](https://zh.wikipedia.org/w/index.php?oldid=19446787),
[line-boundary bold lead](https://zh.wikipedia.org/w/index.php?oldid=10868644),
[dated source reuse](https://zh.wikipedia.org/w/index.php?oldid=22181271),
and [the delinked title](https://zh.wikipedia.org/w/index.php?oldid=92425867),
corroborated by its
[display-day source](https://zh.wikipedia.org/w/index.php?oldid=15181986).
The temporary format adapter lives under `tasks.zhwiki.legacy`; its output
uses the normal archive schema and its acquisition rules do not reach HTML
or report rendering.

## Review a prepared talk-header repair

Install the Tampermonkey script linked on `/checks/`, then select **View changes**
on a ready homepage-history finding. The link opens that talk page's source
editor at section 0. The script reads the saved correction from Aranami, fills
the complete fixed header and edit summary, and opens Wikipedia's diff. Saving
is a separate manual action in the wiki editor.

The query carries an opaque repair ID, not wikitext or an arbitrary endpoint.
The read-only API returns only a validated ready proposal and its source title,
revision and timestamp. The script checks these against the current editor and
original text, including existing drafts. Changed or unavailable observations
remain untouched. It supports the native textarea and WikiEditor's textSelection
integration and never invokes Preview or Save.

Collection retains one bounded exact header and repair plan per checked talk
revision. Runtime delegates span-based projection to updater before saving the
website snapshot. The web layer only reads that result. Missing or ambiguous
destinations, targets outside section 0 and legacy notices whose metadata cannot
be preserved do not receive an automatic action. Parameter snippets remain
available for manual review. The compact website snapshot keeps complete source
and output pairs within its eight MiB limit, omitting unavailable action links.
The separate Parquet cache retains every acquired repair input.

## Quarterly fill suggestions

The challenge checker recognizes static completed-quarter rows and ignores the
automatic today row. Missing rows show the fields to fill: six quality groups,
Top/High counts, total, Start-or-better percentage, quality index and English
comparison count.

Suggested Chinese counts require an exact quarter-end project observation.
FA/FL/A/AL share the top group; GA/Bplus share the next group; B/BL, C/CL,
Start/List and Stub/SL/Unassessed form the remaining groups. Their quality
weights are 10, 8, 7, 5, 3 and 1. Only Top/High importance contributes.

Suggestions identify their saved data date and actual observation time. They
are snapshot-derived figures; the wiki's live category-count module can differ.
Absent historical membership or an independent English count stays unknown.
Current counts cannot fill an earlier quarter, and an unknown is never rendered
as an observed zero.

## Freshness and availability

Consistency checks run in the background on their own hourly freshness cycle.
Daily collection uses the committed project observation; later hourly checks
may use temporary current membership. Each check reports its own coverage.
The last complete dataset remains visible if a later refresh fails. Follow the
source links before editing: wiki pages can change after collection.
