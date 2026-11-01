/** Build or verify the disposable website payload from authored sources. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { buildSync, transformSync } from "esbuild";
import { buildCardTemplates } from "./card_templates.ts";

const mode = process.argv[2];
if (mode !== "build" && mode !== "check") throw new Error("Use web_assets.ts build or check.");
const root = fileURLToPath(new URL("../", import.meta.url));
const destination = join(root, "dist/webservice");
const output = mkdtempSync(join(tmpdir(), "aranami-web-assets-"));
const staged = join(output, "webservice");

/** Enumerate regular authored files without following links outside a tree. */
function files(directory: string): string[] {
    assert.ok(lstatSync(directory).isDirectory(), `Expected a regular build directory: ${directory}`);
    return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
        const path = join(directory, entry.name);
        if (entry.isDirectory()) return files(path);
        if (!entry.isFile()) throw new Error(`Unsupported build input: ${path}`);
        return [path];
    }).sort();
}

function hashes(paths: string[], base: string): Record<string, string> {
    return Object.fromEntries(paths.sort().map(path => {
        assert.ok(lstatSync(path).isFile(), `Expected a regular build file: ${path}`);
        return [relative(base, path).split("\\").join("/"),
            createHash("sha256").update(readFileSync(path)).digest("hex")];
    }));
}

function inputs(): Record<string, string> {
    return hashes([
        ...files(join(root, "frontend")),
        ...files(join(root, "src/webservice/public_html")),
        ...files(join(root, "src/webservice/templates")),
        ...["scripts/web_assets.ts", "scripts/card_templates.ts", "package.json", "package-lock.json", "tsconfig.json"].map(path => join(root, path)),
    ], root);
}

try {
    const sourceHashes = inputs();
    const compiled = spawnSync(process.execPath, [
        join(root, "node_modules/typescript/bin/tsc"),
        "--project", join(root, "tsconfig.json"), "--outDir", output,
    ], { cwd: root, stdio: "inherit" });
    if (compiled.error) throw compiled.error;
    if (compiled.status !== 0) throw new Error("TypeScript compilation failed.");
    buildSync({
        entryPoints: [join(root, "frontend/components.ts")],
        outfile: join(output, "components.js"),
        bundle: true,
        minify: true,
        format: "iife",
        target: "es2022",
        alias: { vue: "vue/dist/vue.runtime.esm-bundler.js" },
        legalComments: "inline",
        banner: { js: "/*! Includes Wikimedia Codex (GPL-2.0-or-later) and Vue (MIT). See /codex-license.html. */" },
        define: {
            "process.env.NODE_ENV": '"production"',
            __VUE_OPTIONS_API__: "false",
            __VUE_PROD_DEVTOOLS__: "false",
            __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: "false",
        },
    });
    for (const tree of ["public_html", "templates"]) {
        cpSync(join(root, "src/webservice", tree), join(staged, tree), { recursive: true });
    }
    const scripts = ["components.js", "live.js", "navigation.js", "newsletter.js"];
    for (const name of scripts) {
        const source = readFileSync(join(output, name), "utf8");
        const code = name === "components.js" ? source : transformSync(source, {
            loader: "js", target: "es2022", minify: true, legalComments: "inline",
        }).code;
        writeFileSync(join(staged, "public_html", name), code);
    }
    copyFileSync(join(root, "node_modules/@wikimedia/codex/dist/codex.style.css"), join(staged, "public_html/codex.css"));
    const templates = await buildCardTemplates(root, output);
    const templateRoot = join(staged, "templates/includes/generated");
    mkdirSync(templateRoot, { recursive: true });
    for (const { name, source } of templates) copyFileSync(source, join(templateRoot, name));
    assert.deepEqual(inputs(), sourceHashes, "Source changed during the build; run npm run build:web again.");
    const receipt = { schema: 1, inputs: sourceHashes, outputs: hashes(files(staged), staged) };
    if (mode === "build") {
        // Replace only disposable website output; leave release archives and
        // other dist artifacts intact. The receipt is written last.
        if (existsSync(join(root, "dist"))) assert.ok(lstatSync(join(root, "dist")).isDirectory(), "dist must be a regular directory.");
        rmSync(destination, { recursive: true, force: true });
        cpSync(staged, destination, { recursive: true });
        writeFileSync(join(destination, "build.json"), JSON.stringify(receipt, null, 2) + "\n");
    } else {
        const actual = hashes(files(destination).filter(path => path !== join(destination, "build.json")), destination);
        assert.deepEqual(actual, receipt.outputs, "Website output is stale or modified; run npm run build:web.");
        assert.deepEqual(JSON.parse(readFileSync(join(destination, "build.json"), "utf8")), receipt,
            "Website build receipt is stale; run npm run build:web.");
    }
    console.log(`${mode === "build" ? "Built" : "Verified"} dist/webservice: ${Object.keys(receipt.outputs).length} files, including ${scripts.length + 1} browser assets and ${templates.length} Codex component templates.`);
} finally {
    rmSync(output, { recursive: true, force: true });
}
