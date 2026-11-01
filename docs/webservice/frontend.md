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

## Component ownership

Codex supplies cards, fields, labels, selects, text inputs, checkboxes, radio
groups, messages, tables, badges, and buttons. Use its supported CSS markup
for server-rendered components, following [ADR 05][css-components]. Local CSS
owns the page grid, article-specific layout, responsive overflow, and native
navigation/dialog adapters. Keep the upstream stylesheet unchanged.

`frontend/components.ts` enhances maintenance and newsletter buttons with
Vue Codex components. Each component owns its small host; do not mount Vue
over the page root or a region owned by live-status reconciliation.
Newsletter issue changes validate the response, unmount the old components,
replace the saved result, enhance the inserted controls, and initialize copy
handlers. Stale or invalid responses must not create component instances.

Report dialogs and live status use Codex CSS buttons with native HTML dialog
behavior. `frontend/live.ts` owns those regions and preserves open dialogs
and focused controls while reconciling saved status. Native disclosures keep
Tools, language selection, and the mobile contents list usable without scripts.

Keep GET form names, IDs, labels, date bounds, and native validation intact.
Newsletter wikitext remains literal text. Use existing translated labels for
visible and accessible names. Follow Codex's [Field][field] and
[native-validation][validation] decisions when extending forms.

The Codex license and Vue's MIT notice are available at `/codex-license.html`.
Message icons use local, attributed SVG symbols so the site needs no data-URI
or remote-asset exceptions to its content security policy.

[css-components]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/05-adr-css-components.html
[field]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/06-adr-field-component.html
[validation]: https://doc.wikimedia.org/codex/latest/using-codex/adrs/12-adr-native-constraint-validation.html
