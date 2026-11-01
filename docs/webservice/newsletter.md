# Newsletter drafts

Use the read-only **Newsletter drafts** tool to prepare the electronic-games
WikiProject's quarterly newsletter. It provides copyable Chinese wikitext for
manual review and publication on Wikipedia. Collection runs on Toolforge;
opening the tool never fetches source pages or publishes edits.

## Prepare an issue

Open **Tools → Newsletter drafts**, or `/newsletter/`. The issue menu selects
the latest completed issue or the current draft and loads it in place. The
issue submit button stays visible after loading. Native form submission works
without JavaScript. The default advances on January 1,
April 1, July 1, and October 1 at `00:00 UTC`. Daily collection runs at that time
and retries failures hourly while retaining the previous complete result.

Ordinary issues contain the main overview, quality changes, announcements,
and blank feature and interview drafts. January has a combined annual feature
instead of a separate interview. For example, issue `2027-01` reports
October–December 2026 in its regular pages and all of 2026 in its annual feature.
All pages use the current newsletter banner. Feature and interview prose stays
editorial work; the generator never copies a previous author's article.

Announcements combine homepage featured articles, featured lists, and good
articles under `典優內容`. Entries show the appearance date and content type in
chronological order. Homepage lists with no entries use `* （無）`.

Use **Copy** to copy the draft, **View newsletter page** to read its destination,
and **Edit newsletter page** to open the wiki editor. The edit link prefills a
source-linked summary such as `[[toolforge:aranami/newsletter/|2026年10月30日资料]]`
using the draft's data date, including the annual feature's own date. Undated
drafts use `简讯资料`. Both links work without JavaScript and leave copying
to the separate button.
Review and preview the wikitext before saving it on Wikipedia. Code blocks
also support native selection and copying. The annual feature includes two
blue primary buttons for editors to add comments, using the newsletter's
reflection preload. Introduction and data sections have no invitation
buttons. Personal prose remains blank. Website buttons, the date input and
the issue selector use the same 32-pixel Codex medium height.

Use **Data date** to select a retained daily draft. For example, `2026-10-30`
means data through October 30, normally captured after October 31's update.
The actual capture time is shown separately. Missing dates are unavailable;
each available date has its own saved facts. The website generates every page
from those facts when requested, using the current formatting rules. Formatting
changes therefore apply to older dates without changing their observations or
capture timestamps. Missing annual figures use complete placeholder sentences.
Partial-year daily growth uses the elapsed days through the displayed
observation date; completed years use their full calendar length.

## January's annual feature

The feature follows January 2026's revision 91016278. Beneath its introduction,
the four data sections retain their original order and level-four headings:
`內容評審`, `新章推薦`, `条目报告`, and `榮譽表彰`.
The second label is an intentional correction to the reference. No extra data headings are
added. Supported quality milestones, credits, DYK lists, article statistics,
and award gains fill the corresponding slots. Missing observations use natural
placeholder sentences. Introductions immediately before partial annual figures
and lists carry an inline date notice, such as
`{{Memo|截至2026年10月31日|inline:info}}`. The template's second parameter
uses the verified `inline:` prefix so following content stays on the same line
([Memo documentation, revision 93779044](https://zh.wikipedia.org/w/index.php?title=Template:Memo/doc&oldid=93779044)).
Comparisons state figures and plain
increases, decreases, or unchanged totals. Quarterly counts are never
substituted for annual data.

The quality review leads with annual high-quality totals, grouping FA+FL as
`典範內容` and A+AL as `甲級內容`, followed by GA and Bplus. Known-zero groups
are omitted. The comparison and chart precede the distinct grade lists,
subject-distribution placeholder, and centered author table. Author lists put
each template parameter and article on its own line, with a plain count summary.

Credits prioritize recorded nominators for every quality grade. Explicit
non-main-author declarations remain marked `非主編` beside the article.
Listing-time text attribution is separate: saved WikiWho evidence identifies
original contributors of the raw wikitext tokens surviving at the selected
listing revision, with tied leading contributors retained. When the nominator
differs from those contributors, a small inline notice after the article names
them. Listings without a recorded nominator use the saved leading contributors.
Missing authorship remains unknown, and usernames have no authorship qualifier.
DYK contributors are retained in the database from explicit author fields in
supported DYKEntry nominations or archives; annual drafts omit these credits.
A DYKtalk date alone supplies no contributor identity. Quality category lists
follow ascending listing dates and omit categories with no entries. Editor
rows rank by total article count, then FA, GA, and Bplus counts; collapsible
titles show each editor's grade breakdown. Editors tied on all four counts
share a rank cell; subsequent ranks include all tied editors. Quality and DYK
groups follow listing dates. Editor lists group FA/FL together, then A/AL,
GA, and Bplus, with dates and title ties within each grade.
Each dated article ends with an invisible MMDD comment such as `<!-- 0904 -->`
to help edit the copied draft. Quarterly quality lists use the same markers.
Announcement homepage lists keep visible dates without these comments.
Unknown first appearance dates have no marker.

Game works in the DYK list use five release-year groups: 2009 and earlier,
2010–2019, 2020 through the year before the reviewed year, the reviewed year,
and `其他`. Thus the 2026 review uses 2020–2025 and 2026;
the 2027 review uses 2020–2026 and 2027. Direct article categories matching
`\d{4}年[电電]子[游遊][戏戲]` establish game years within the supported 1900–2099
range; the earliest matching year takes precedence. Supported release dates in
recognized game infoboxes provide a fallback. Known future releases belong to
the game group's `其他` band.
Each group label includes its observed article count, including the game total
and each release-year group. Missing observations leave the count blank.

Current membership in the `虚构角色` project supplies `角色與元素`; `公司` or
`传记` supplies `公司與人物`; `音樂` supplies `音樂`. Dated games take priority,
followed by those three subject groups in that order, so each article appears
once. These current membership observations retain their own collection time
and do not assert historical project membership. Subjects without a verified
group, including games with unknown release years and no other match, appear
under `待分類`. Development/industry and other subject categories remain blank
without supported classification evidence.
Known B+, GA, A, FA, and featured-list assessments
receive quality icons. Closed years require a retained boundary assessment;
current grades are not used as historical evidence.

The annual feature keeps the reference's numerical introductions for quality
promotions, DYK, article totals and growth, popularity, revisions, and awards.
It uses `Graph:Chart` for annual quality recognitions, cumulative DYK totals, and
assessment pies for the reviewed year and its predecessor. Charts have no
companion data tables. Generated parameters use `| key = value`, and chart
lists separate values with a comma and a space.
Each assessment legend includes the grade and count,
such as `典范级：25`, in regular weight. Grades representing at least 5% show
their share to one decimal inside the pie, in regular black text. Smaller
slices have no percentage label. Legends contain only grades and counts.
Each pie is at most 400 pixels wide including its legend and padding.
Both pies share one outer container, with their introduction and date notice
before it.
On narrow screens, each chart scrolls inside its own container.
Historical series
come from the reference images' description pages, with provenance retained
by collection. Current observations extend the supported series; missing
current values never become chart zeroes.

The annual recognition chart selects articles with a promotion during each
year and counts each once under its grade at that year's end or the current
observation cutoff. Thus an article promoted from A to FA in the same year
appears only in FA. Later delisting or demotion does not change earlier years.
Its layers rise
from FA+FL through A+AL and GA to Bplus; the A layer disappears when all its
values are zero. The chart retains native `Graph:Chart` parameters and native
hover details without expanded SVG. The legend follows the layer order, and
x-axis labels contain only years. Narrow screens scroll within the container.
Unknown event dates do not receive an
inferred year, and missing historical observations produce no chart.

Current-year DYK points use completed quarter ends such as March 31, June 30,
and September 30. Each point counts distinct articles making their first DYK
appearance since January 1, added to the historical baseline. A separate
current-day point includes milestones already observed today, capped at
December 31. Repeated displays and earlier-year subjects do not increase
counts, and the year-end point appears only once.
The DYK trend floats on the right in a captioned image frame.

Monthly pageview winners come from exact calendar-month reports in the popular
articles page history. Month labels link to the permanent revision used for
that month's result when its source revision is known; older observations
without that metadata keep plain labels. Annual pageviews and revision counts supply the top 25;
the popularity list links to the top-200 report. Current rolling pageview data
is marked provisional and states its actual interval. Annual total pageviews
use whole, comma-grouped units of ten thousand, such as `6,775万`. The January
feature continues to refresh after January 1, allowing reports completed on January
2 or 3 to appear in the latest draft. Saved earlier date selections retain
their original observations.

The annual ACG ranking includes all positive score gains and cumulative totals.
Its introductory summary counts scoring editors, first-time scorers, and those
new scorers reaching ten points for their first award. A green `N` marks every
first-time scorer, including those below ten points. Community honors remain
blank for manual contributions. Quarterly awards keep their top-five ranking
with ties and their existing ten-point newcomer threshold.
