/** Compile authored Codex Vue components into small, inert Jinja templates. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { transformSync } from "esbuild";
import { createSSRApp } from "vue";
import { compileScript, parse } from "vue/compiler-sfc";
import { renderToString } from "vue/server-renderer";

const components = {
    ArticleCard: "article_card.html",
    RoutineCard: "routine_card.html",
    ServerCard: "server_card.html",
    MaintenanceCard: "maintenance_card.html",
    NewsletterDraft: "newsletter_draft.html",
};

function replaceOnce(source: string, token: string, value: string): string {
    assert.equal(source.split(token).length, 2, `Expected one ${token}`);
    return source.replace(token, () => value);
}

/** Keep Codex's classes while allowing native headings and block slots. */
function semanticShell(html: string, slots: Map<string, string>): string {
    let shell = html.replace(/<!--(?:v-if|\[|\])-->/g, "");
    const icon = '<span class="cdx-icon cdx-icon--medium cdx-card__icon">'
        + '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" '
        + 'viewBox="0 0 20 20" aria-hidden="true"><g>ARANAMI_ICON</g></svg></span>';
    if (slots.has("icon")) shell = replaceOnce(shell, icon, "ARANAMI_ICON");
    const linked = shell.startsWith('<a href="ARANAMI_URL"');
    if (!linked) {
        assert.match(shell, /^<span class="cdx-card /);
        assert.ok(shell.endsWith("</span>"));
        shell = shell.replace(/^<span /, "<article ").slice(0, -7) + "</article>";
    }
    const wrappers = ["cdx-card__text", "cdx-card__text__title"];
    if (slots.has("description")) wrappers.push("cdx-card__text__description");
    if (slots.has("supporting-text")) wrappers.push("cdx-card__text__supporting-text");
    for (const name of wrappers) {
        shell = replaceOnce(shell, `<span class="${name}">`, `<div class="${name}">`);
    }
    assert.equal((shell.match(/<span\b/g) ?? []).length, 0, "Unexpected Codex wrapper");
    assert.equal((shell.match(/<\/span>/g) ?? []).length, wrappers.length);
    shell = shell.replaceAll("</span>", "</div>");
    return restoreSlots(shell, slots, linked ? "</a>" : "</article>");
}

/** Restore only explicit source placeholders without decoding rendered HTML. */
function restoreSlots(shell: string, slots: Map<string, string>, closingTag?: string): string {
    for (const [name, content] of slots) {
        if (name === "after") {
            assert.ok(closingTag && shell.endsWith(closingTag));
            shell = shell.slice(0, -closingTag.length) + content + closingTag;
        } else {
            shell = replaceOnce(shell, `ARANAMI_${name.replaceAll("-", "_").toUpperCase()}`, content);
        }
    }
    assert.doesNotMatch(shell, /ARANAMI_[A-Z_]+/, "Unresolved server slot");
    return shell;
}

/** Keep native Accordion/TextArea markup compatible with the strict CSP. */
function newsletterShell(html: string, slots: Map<string, string>): string {
    const shell = replaceOnce(
        html.replace(/<!--(?:v-if|\[|\])-->/g, ""),
        '<div class="cdx-text-area cdx-text-area--status-default newsletter-text" style="">',
        '<div class="cdx-text-area cdx-text-area--status-default newsletter-text">',
    );
    assert.doesNotMatch(shell, /\sstyle=/, "Unexpected inline component style");
    return restoreSlots(shell, slots);
}

/** Render every authored Codex source and retain only its server template. */
export async function buildCardTemplates(root: string, output: string): Promise<Array<{ name: string; source: string }>> {
    const templates = [];
    for (const [component, name] of Object.entries(components)) {
        const filename = join(root, "frontend/cards", `${component}.vue`);
        const { descriptor, errors } = parse(readFileSync(filename, "utf8"), { filename });
        assert.deepEqual(errors, []);
        assert.equal(descriptor.styles.length, 0, "Card layout belongs in shared CSS");
        const slots = new Map(descriptor.customBlocks.map(block => {
            assert.equal(block.type, "jinja");
            assert.equal(typeof block.attrs.name, "string");
            return [block.attrs.name as string, block.content.trim()] as const;
        }));
        assert.equal(slots.size, descriptor.customBlocks.length, "Duplicate server slot");
        const compiled = compileScript(descriptor, {
            id: `aranami-${component}`,
            inlineTemplate: true,
            templateOptions: { ssr: true },
        });
        // Use the installed packages through public exports; the temporary
        // module lives outside node_modules and never ships to Toolforge.
        const code = transformSync(compiled.content, { loader: "ts", format: "esm" }).code
            .replace(/from "([^"]+)"/g, (_match, module: string) => `from ${JSON.stringify(import.meta.resolve(module))}`);
        const modulePath = join(output, `${component}.mjs`);
        writeFileSync(modulePath, code);
        const vue = await import(pathToFileURL(modulePath).href);
        const rendered = await renderToString(createSSRApp(vue.default));
        const html = component === "NewsletterDraft"
            ? newsletterShell(rendered, slots)
            : semanticShell(rendered, slots);
        const source = join(output, name);
        writeFileSync(source, `{# Generated from frontend/cards/${component}.vue; run npm run build:web. #}\n${html}\n`);
        templates.push({ name, source });
    }
    return templates;
}
