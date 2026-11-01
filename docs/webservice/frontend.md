# Frontend components

Use Wikimedia Codex components to maintain a consistent interface across
the dashboard, maintenance lists, newsletter, report monitor, logs, About,
and error pages. Pages render useful HTML before browser scripts load.

## Build and verify

Install locked development dependencies with `npm ci --ignore-scripts`, edit
`frontend/*.ts` or the Jinja templates, then run `npm run build:web`.
Commit the generated assets in `src/webservice/public_html/` with the sources.
`npm run check:web` checks strict TypeScript and compares a fresh build with
the committed files. CI runs this check and the Node/Chromium behavior suites.

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
Load `styles.css` first so local Select icon overrides apply before Codex
loads, without transient data-URI requests blocked by the production content
security policy.

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

All dashboard article lists share the CSS-only [Card][card] structure. Each
card is a native `article` labelled by its linked `h3` title beneath the
section's `h2`. Assessment InfoChips form a separate row; the full saved
introduction and supporting metadata remain readable without JavaScript.
Reviews show their type and attributed icon above the title. Highlight cards
retain ordered-list semantics and their observed rank or list position.

Use a neutral card wrapper when article, review, source, and traffic links
have different destinations. A whole-card link is appropriate for a
maintenance category with one destination. Keep focus visible on individual
links, preserve complete metadata, and allow long text and chips to wrap on
narrow screens. The common footer spacing and divider separate supporting
facts from article descriptions without adding interactive behavior.

[css-components]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/05-adr-css-components.html
[field]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/06-adr-field-component.html
[validation]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/12-adr-native-constraint-validation.html
[card]: https://doc.wikimedia.org/codex/latest/components/demos/card.html
