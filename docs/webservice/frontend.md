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
Vue Codex components. Only those pages load the component bundle. Use Codex's
supported package entry and Vue's runtime-only build for these render functions.
Each component owns its small host; do not mount Vue
over the page root or a region owned by live-status reconciliation.
Newsletter issue changes validate the response, unmount the old components,
replace the saved result, enhance the inserted controls, and initialize copy
handlers. Stale or invalid responses must not create component instances.

Report dialogs and live status use Codex CSS buttons with native HTML dialog
behavior. `frontend/live.ts` owns those regions and preserves open dialogs
and focused controls while reconciling saved status. Native disclosures keep
Tools, language selection, and the mobile contents list usable without scripts.
Report and log tables use row headers; execution stages retain ordered-list
semantics. Status messages use Codex's separate icon and content regions.
Download controls remain ordinary links even when styled as Codex buttons.

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

Dashboard, report-status, and maintenance cards are authored as actual
`CdxCard` Vue single-file components in `frontend/cards/`. The local build
uses Vue's SFC compiler and server renderer to emit three small Jinja files
under `dist/webservice/templates/includes/generated/`. Edit the `.vue`
sources and rebuild; generated HTML is not an authoring surface.

The report monitor stacks normal outlined cards with space between them.
Each card's title names the work, its description shows status and progress,
and its supporting text gives the execution and next scheduled times.

Each component declares Codex props and named slots. Its custom `<jinja
name="...">` blocks contain trusted server-side slot templates. The build
restores only explicit placeholders after rendering and leaves ordinary
Jinja autoescaping intact. A checked adapter promotes the known neutral
Card root to `article` and its text wrappers to `div` so native headings and
block content remain valid. Linked maintenance cards retain their anchor.
Unexpected upstream wrapper changes fail the build instead of silently
changing semantics. No Vue hydration markers, card JavaScript, compiler,
or Node runtime are included in the source deployment.

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
the review type retained in its accessible label. Highlight cards
retain ordered-list semantics and their observed rank or list position.

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
