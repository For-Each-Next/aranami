# Frontend components

Use Wikimedia Codex components to maintain a consistent interface across
the dashboard, maintenance lists, newsletter, report monitor, logs, About,
and error pages. Pages render useful HTML before browser scripts load.

## Build and verify

Install locked development dependencies with `npm ci --ignore-scripts`, edit
`frontend/*.ts`, the components in `frontend/cards/*.vue`, or page
templates, then run `npm run build:web` before presentation tests.
The complete output is `dist/webservice/{public_html,templates}`. Commit only
authored inputs; `dist/` is disposable and ignored by Git.
`npm run check:web` checks strict TypeScript and compares a fresh build with
these files, including generated component templates. CI builds first, then
runs this check and the Node/Chromium behavior suites.

`dist/webservice/build.json` records SHA-256 hashes for every frontend source,
build script, package manifest/lockfile, TypeScript configuration, authored
asset/template, and output file. The uploader rejects missing or stale builds
and modified output. It maps the verified files into the existing
`src/webservice/` archive paths; source components and compiler dependencies
stay on the development computer.

Node.js 24 or later, TypeScript, esbuild, Vue 3, and Codex are development
dependencies pinned by `package-lock.json`. Toolforge receives the generated
JavaScript and CSS with its immutable Python source generation; it does not
install npm packages or compile frontend sources.

Templates identify authored assets with `asset_url(name)`, which adds the
file's SHA-256 digest to its URL. Matching digests permit immutable browser
caching; fixed or mismatched URLs revalidate through ETags. Dynamic HTML and
API responses remain uncached by browsers, so a deployment selects its new
asset URLs while saved status stays current. The backend reuses bounded
asset bytes and compressed representations within each source generation.

## Component ownership

Codex supplies cards, fields, labels, selects, text inputs, checkboxes, radio
groups, messages, tables, badges, and buttons. Use its supported CSS markup
for server-rendered components, following [ADR 05][css-components]. Local CSS
owns the page grid, article-specific layout, responsive overflow, and native
navigation/dialog adapters. Keep the upstream stylesheet unchanged.
Use Codex's platform font stacks: system sans-serif for interface text,
serif for page and section headings, and monospace for newsletter source.
These use installed fonts without downloading a webfont.
Load `styles.css` first so local Select and Accordion icon overrides apply
before Codex loads, without transient data-URI requests blocked by the
production content security policy. Accordion chevrons use the local SVG
mask, retain their expansion rotation, and use a system color in forced-color
mode.

`frontend/components.ts` enhances maintenance and newsletter buttons with
Vue Codex components. Its maintenance controller updates saved tables in place
for sorting, categories, title searches, and pagination, preserving URL history
and keyboard focus. Failed requests fall back to native navigation. Only those
pages load the component bundle. Use Codex's
supported package entry and Vue's runtime-only build for these render functions.
Each component owns its small host; do not mount Vue
over the page root or a region owned by live-status reconciliation.
Newsletter issue changes validate the response, unmount the old components,
replace the saved result, enhance the inserted controls, and initialize copy
handlers. Stale or invalid responses must not create component instances.

Report dialogs compile `CdxDialog` into a shared server-rendered shell with
Codex headers, close buttons, and scrollable bodies. A checked build adapter
replaces Vue's backdrop and focus sentinels with native HTML dialog behavior;
closed dialogs stay hidden and fragment links remain usable without scripts.
Report tables use Codex's CSS markup, fill the dialog body, and scroll
horizontally on narrow screens. `frontend/live.ts` owns those regions and
preserves open dialogs and focused controls while reconciling saved status. Native disclosures keep
Tools, language selection, and the mobile contents list usable without scripts.
Report and log tables use row headers; execution stages retain ordered-list
semantics. Status messages use Codex's separate icon and content regions.
Download controls remain ordinary links even when styled as Codex buttons.
About presents Aranami's state, startup, versions, memory, and CPU in one
Codex-styled table with localized row headers. Live refresh retains its table
and state IDs; expired resource observations become unavailable while versions
and the observed startup remain visible.

Maintenance tables use native GET controls in Codex sortable column headers,
ordering the complete saved category before pagination. A category menu sits
above the table; title search shares its visible caption header. All controls
remain usable without scripts. The Codex table pager retains native navigation links
and a page-size submit button without JavaScript; enhancement submits size
changes immediately. Sort and pagination arrows use the local Codex SVG sprite.

Keep GET form names, IDs, labels, date bounds, and native validation intact.
Newsletter wikitext remains literal text. Use existing translated labels for
visible and accessible names. Follow Codex's [Field][field] and
[native-validation][validation] decisions when extending forms.

The Codex license and Vue's MIT notice are available at `/codex-license.html`.
Message icons use local, attributed SVG symbols so the site needs no data-URI
or remote-asset exceptions to its content security policy.
The report-icon sprite uses Codex icons under CC BY 4.0; preserve its embedded
source/author attribution and the About-page credit.

## Dashboard cards

Dashboard, report-status, and maintenance cards are authored
as actual `CdxCard` Vue single-file components in `frontend/cards/`. The local
build uses Vue's SFC compiler and server renderer to emit three small card templates
under `dist/webservice/templates/includes/generated/`. Edit the `.vue`
sources and rebuild; generated HTML is not an authoring surface.

Authored images live under `src/webservice/public_html/assets/`: `icons/`
contains interface icons, numbered medals, and their attribution notices;
`images/` contains the logo and browser artwork. Templates use content-hashed
asset URLs, and the build preserves this directory structure in the payload.

The report monitor uses the dashboard's responsive grid of normal outlined
cards, fitting up to three columns. Each card's title names the work, its
description shows a status label and progress, and its supporting
text puts execution and next scheduled times on separate lines. These fields
inherit Codex's card typography and spacing, including normal-sized progress
in the description and smaller supporting text. Only failures are highlighted.
Details links use the site's default link colors and inherit the surrounding
text size. All cards share the border hover and focus treatment.
Page and section introductions share the `page-description` helper style;
timestamps and compact status metadata retain their smaller text.

Each component declares Codex props and named slots. Its custom `<jinja
name="...">` blocks contain trusted server-side slot templates. The build
restores only explicit placeholders after rendering and leaves ordinary
Jinja autoescaping intact. A checked adapter promotes the known neutral
Card root to `article` and its text wrappers to `div` so native headings and
block content remain valid. Linked maintenance cards retain their anchor.
Unexpected upstream wrapper changes fail the build instead of silently
changing semantics. No Vue hydration markers, card JavaScript, compiler,
or Node runtime are included in the source deployment.

Maintenance cards use Codex's leading icon layout with `forceThumbnail` disabled.
Their 22-pixel icons match the Chinese Wikipedia maintenance templates and are
served locally with source revisions and attribution in the maintenance icon
credits. Text-only templates and unknown tasks render without an icon.

`NewsletterDraft.vue` also compiles actual `CdxAccordion` and `CdxTextArea`
components into a generated partial. Their native `details`, `summary`, heading,
and readonly `textarea` remain intact, with unique per-draft IDs. The escaped
source includes a compensating first newline for the HTML textarea parser;
leading source newlines remain visible and native textarea values use LF line
endings. Copy reads the textarea value, opening and selecting it when clipboard
access fails. CSS field sizing expands wrapped text, with a measured `rows`
fallback on disclosure and viewport changes. This requires no inline styles.

All dashboard article lists share this generated [Card][card] structure. Each
card is a native `article` labelled by its linked `h3` title beneath the
section's `h2`. Assessment InfoChips follow the title inline, outside the
heading element, and wrap naturally when needed. The full saved
introduction and supporting metadata remain readable without JavaScript.
Reviews show their attributed icon in Codex's leading icon region, with
the review type retained in its accessible label. Showcase icons use the saved
featured/good appearance kind, including scheduled appearances; assessment
chips continue to show the current grade independently. Every DYK card uses
the same DYK icon. These attributed Commons images link to their source pages.
Popularity cards show numbered gold, silver, and bronze medals for ranks 1–3
and matching white medals for 4–6, retaining the ordered list's observed rank values.

Supporting metadata uses semantic lists with CSS pipe separators. Review
cards lead with the review link and a colon; popularity cards lead with their
observed date and a colon. Showcase cards put the appearance kind before its
date. Scheduled markers and date-evidence notes stay beside that date.
Separators follow their preceding item, so wrapping never adds a separator
prefix to a new line. Missing ranks and dates remain omitted.
English dashboard-card dates use `Oct 31` in the current UTC year and retain
the year otherwise, such as `Oct 31, 2025`. Full ISO dates remain available
on hover; other date displays keep their existing formats.
Review ages use compact `mo`, `d`, `h`, and `m` units in
English, retaining their fixed unit boundaries and full timestamps on hover.

Use a neutral card wrapper when article, review, source, and traffic links
have different destinations. A whole-card link is appropriate for a
maintenance category with one destination. Keep focus visible on individual
links, preserve complete metadata, and allow long text and chips to wrap on
narrow screens. Description and supporting-text slots use Codex's normal
spacing without an extra internal divider.


[css-components]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/05-adr-css-components.html
[field]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/06-adr-field-component.html
[validation]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/12-adr-native-constraint-validation.html
[card]: https://doc.wikimedia.org/codex/latest/components/demos/card.html
