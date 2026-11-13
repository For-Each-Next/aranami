# Web UI design reference

This directory preserves a proposed visual design for future Python website
improvements. It is a reference artifact, not an active application, a replacement
template directory or a runtime dependency.

Open [the English preview](preview.html) or
[the Traditional Chinese preview](preview-zh-hant.html) in a browser. Both use
synthetic fixture data and the adjacent [stylesheet](styles.css); navigation
links illustrate the proposed route structure. No server or build is required
to inspect the page layout.

- [Desktop screenshot](desktop.png): English dashboard at 1440 CSS pixels.
- [Mobile screenshot](mobile.png): the same fixture at 390 CSS pixels.
- [Template source](page.html): Jinja-style layout and presentation slots.
- [Stylesheet](styles.css): responsive layout, cards, tables, navigation and focus styles.

The design retains the masthead, side navigation, contents links, cards,
metadata tables, missing-data notices and native disclosure controls. Offline
browser checks covered 175 combinations of routes, locales and widths. These
checks establish synthetic layout behavior, not production data coverage or
real-device accessibility.

Reuse the design deliberately through the existing Python website contracts
and verification workflow. Authored project assets use the repository's CC0
license; synthetic fixture names are illustrative.
