# Aranami branding

Use the horizontal logo in the site header and the standalone wave for browser
and touch icons. Keep the transparent background and the accessible name
“Aranami”. The artwork belongs to this CC0-1.0 project.

The built-in ImageGen tool generated the artwork in
`src/webservice/public_html/assets/images/`. Use `aranami-wordmark.png` for
the header, `aranami-icon.png` for the standalone wave, and `favicon.png`
for its 32-pixel browser variant. The images were cropped and reduced for web use.

The design prompt requested a compact abstract curling ocean wave with an A
suggested by its negative space, a blue `#3366cc` and navy `#202122` palette,
and the exact word “Aranami” in clear humanist sans-serif lettering. It called
for a transparent, tightly framed horizontal logo with simple geometry,
without a badge, extra words, shadows, texture, or fine details. A second
ImageGen prompt extracted the matching standalone wave, retaining its shape
and transparency and removing the lettering.

The interface follows the [Wikimedia Codex style guide][codex], with locally
bundled CSS and Vue components, neutral surfaces, accessible contrast, native
controls, and a collapsible contents button on small screens. See
[Frontend components](frontend.md) for build and component contracts.

Content-review icons follow Chinese Wikipedia's `Module:PJ:VG/RVW`
(revision 92100885). Preserve each icon's Commons file-page link and the source
and license notices in `src/webservice/public_html/assets/icons/credits.html`.

Header navigation uses near-black text without underlines, with visible focus
outlines and current-page markers. The Tools menu opens on mouse hover; clicking
keeps it open, and clicking again closes it. Keyboard and touch users can use
the native disclosure, which also works without JavaScript.

[codex]: https://doc.wikimedia.org/codex/latest/style-guide/overview.html
