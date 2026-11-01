/** Build or verify the committed browser scripts and pinned Codex stylesheet. */
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";

const mode = process.argv[2];
if (mode !== "build" && mode !== "check") {
    throw new Error("Use web_assets.ts build or check.");
}
const root = fileURLToPath(new URL("../", import.meta.url));
const output = mkdtempSync(join(tmpdir(), "aranami-web-assets-"));
try {
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
        // Render functions need only Vue's runtime, never its compiler.
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
    const assets = readdirSync(output).filter(name => name.endsWith(".js"))
        .map(name => ({ name, source: join(output, name) }));
    assets.push({
        name: "codex.css",
        source: join(root, "node_modules/@wikimedia/codex/dist/codex.style.css"),
    });
    const stale: string[] = [];
    for (const { name, source } of assets) {
        const target = join(root, "src/webservice/public_html", name);
        if (mode === "build") copyFileSync(source, target);
        else if (!readFileSync(source).equals(readFileSync(target))) stale.push(name);
    }
    if (stale.length) {
        throw new Error(`Stale assets: ${stale.join(", ")}. Run npm run build:web.`);
    }
    console.log(`${mode === "build" ? "Built" : "Verified"} ${assets.length} browser assets.`);
} finally {
    rmSync(output, { recursive: true, force: true });
}
