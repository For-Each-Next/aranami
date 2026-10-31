/* Check rendered synthetic pages at audit widths with native Chromium/CDP.
 * Usage: node tests/toolforge/check_interface.cjs FIXTURE_DIR OUTPUT_DIR
 * The local server reads only the fixture directory and public assets.
 */
"use strict";
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { once } = require("node:events");

const fixtureRoot = path.resolve(process.argv[2]);
const outputRoot = path.resolve(process.argv[3]);
const assetRoot = path.resolve(__dirname, "../../src/webservice/public_html");
const chromium = process.env.ARANAMI_CHROMIUM || "/Applications/Chromium.app/Contents/MacOS/Chromium";
const widths = [320, 390, 600, 601, 639, 640, 768, 943, 944, 1120, 1440, 1680, 1920];
const locales = ["en", "zh-hans", "zh-hant"];
const surfaces = ["home", "maintenance", "logs", "about", "error"];
const failures = [];
fs.mkdirSync(outputRoot, { recursive: true });

async function bounded(operation, label, milliseconds = 10000) {
    let deadline;
    try {
        return await Promise.race([operation, new Promise((_, reject) => {
            deadline = setTimeout(() => {
                const error = new Error(`${label} timed out after ${milliseconds} ms`);
                error.code = "CHECK_TIMEOUT";
                reject(error);
            }, milliseconds);
        })]);
    } finally { clearTimeout(deadline); }
}

async function main() {
    const server = http.createServer((request, response) => {
        const pathname = new URL(request.url, "http://localhost").pathname;
        const filename = (["/live.js", "/styles.css", "/codex.css", "/codex-select.svg", "/codex-select-disabled.svg", "/codex-license.html", "/favicon.svg", "/aranami.svg", "/report-icons.svg"].includes(pathname) || pathname.startsWith("/review-icons/"))
            ? path.join(assetRoot, pathname) : path.join(fixtureRoot, pathname);
        if (!filename.startsWith(fixtureRoot + path.sep) && !filename.startsWith(assetRoot + path.sep)) {
            response.writeHead(404).end(); return;
        }
        try {
            response.setHeader("Content-Security-Policy", "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
            response.setHeader("Content-Type", ({ ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png" })[path.extname(filename)] || "text/plain");
            response.end(fs.readFileSync(filename));
        } catch { response.writeHead(404).end(); }
    });
    server.listen(0, "127.0.0.1");
    await bounded(once(server, "listening"), "local fixture server startup");
    const base = `http://127.0.0.1:${server.address().port}`;
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), "aranami-browser-"));
    const browser = spawn(chromium, ["--headless", "--disable-gpu", "--no-first-run", "--disable-background-networking", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
    let socket;
    try {
        const endpoint = await new Promise((resolve, reject) => {
            let output = "";
            const deadline = setTimeout(() => reject(new Error("Chromium CDP startup timed out")), 15000);
            browser.stderr.on("data", chunk => {
                output += chunk.toString();
                const match = output.match(/DevTools listening on (ws:\/\/\S+)/);
                if (match) { clearTimeout(deadline); resolve(match[1]); }
            });
            browser.on("exit", code => { clearTimeout(deadline); reject(new Error(`Chromium exited ${code}: ${output.slice(-500)}`)); });
        });
        socket = new WebSocket(endpoint);
        await bounded(once(socket, "open"), "CDP WebSocket connection");
        let sequence = 0;
        const pending = new Map();
        const events = new Map();
        socket.addEventListener("message", event => {
            const message = JSON.parse(event.data);
            if (message.id) {
                const operation = pending.get(message.id);
                pending.delete(message.id);
                if (message.error) operation?.reject(new Error(JSON.stringify(message.error)));
                else operation?.resolve(message.result);
            } else {
                const key = `${message.sessionId}:${message.method}`;
                const operation = events.get(key);
                events.delete(key);
                operation?.resolve(message.params);
            }
        });
        socket.addEventListener("close", () => {
            const error = new Error("CDP WebSocket closed");
            error.code = "CDP_CLOSED";
            for (const operation of [...pending.values(), ...events.values()]) operation.reject(error);
            pending.clear();
            events.clear();
        });
        const call = async (method, params = {}, sessionId) => {
            const id = ++sequence;
            try {
                return await bounded(new Promise((resolve, reject) => {
                    if (socket.readyState !== WebSocket.OPEN) {
                        const error = new Error("CDP WebSocket is not open");
                        error.code = "CDP_CLOSED";
                        reject(error); return;
                    }
                    pending.set(id, { resolve, reject });
                    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
                }), `CDP ${method}`);
            } finally { pending.delete(id); }
        };
        const wait = async (method, sessionId) => {
            const key = `${sessionId}:${method}`;
            try {
                return await bounded(new Promise((resolve, reject) => events.set(key, { resolve, reject })), `CDP event ${method}`);
            } finally { events.delete(key); }
        };
        const browserVersion = (await call("Browser.getVersion")).product;
        const { targetId } = await call("Target.createTarget", { url: "about:blank" });
        const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
        await call("Page.enable", {}, sessionId);
        const evaluate = async expression => {
            const result = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId);
            if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
            return result.result.value;
        };
        const checkSemantics = async () => {
            const document = await evaluate(`(() => ({
                main: document.querySelectorAll('main#main').length,
                titles: document.querySelectorAll('h1').length,
                mainTitle: Boolean(document.querySelector('main h1')),
                skipTarget: document.querySelector('a.skip')?.getAttribute('href'),
                unnamedNavigation: [...document.querySelectorAll('nav')].filter(nav => !nav.getAttribute('aria-label') && !nav.getAttribute('aria-labelledby')).length,
                brokenContents: [...document.querySelectorAll('.page-toc a[href^="#"]')].filter(link => !document.getElementById(link.hash.slice(1))).map(link => link.hash),
                unlabelledControls: [...document.querySelectorAll('input:not([type=hidden]), select')].filter(control => !control.labels?.length && !control.getAttribute('aria-label') && !control.getAttribute('aria-labelledby')).map(control => control.outerHTML),
                forms: [...document.forms].map(form => ({method: form.method, action: form.getAttribute('action'), submit: Boolean(form.querySelector('button[type=submit], input[type=submit]'))}))
            }))()`);
            assert.equal(document.main, 1, "missing or duplicate main landmark");
            assert.equal(document.titles, 1, "missing or duplicate page heading");
            assert.equal(document.mainTitle, true, "page heading outside main landmark");
            assert.equal(document.skipTarget, "#main", "skip link does not reach main");
            assert.equal(document.unnamedNavigation, 0, "unnamed navigation landmark");
            assert.deepEqual(document.brokenContents, [], "contents links missing sections");
            assert.deepEqual(document.unlabelledControls, [], "form controls lack accessible labels");
            for (const form of document.forms) {
                assert.equal(form.method, "get", "read-only controls require native GET forms");
                assert.ok(form.action?.startsWith("/"), "form needs a local action");
                assert.equal(form.submit, true, "form needs a native submit control");
            }
        };
        const checkContents = async () => {
            const navigation = await evaluate(`(() => {
                const desktop = document.querySelector('.contents-desktop');
                const mobile = document.querySelector('.contents-mobile');
                const visible = element => element.getBoundingClientRect().height > 0;
                return {desktop: visible(desktop), mobile: visible(mobile), open: mobile.open};
            })()`);
            assert.notEqual(navigation.desktop, navigation.mobile, "exactly one contents navigation should be visible");
            if (navigation.mobile) {
                assert.equal(navigation.open, false, "mobile contents should start collapsed");
                const opened = await evaluate(`(() => {
                    const disclosure = document.querySelector('.contents-mobile');
                    disclosure.querySelector('summary').click();
                    return disclosure.open && [...disclosure.querySelectorAll('a')].every(link => link.getBoundingClientRect().height > 0);
                })()`);
                assert.equal(opened, true, "native mobile contents did not reveal links");
                const closed = await evaluate(`(() => {
                    const disclosure = document.querySelector('.contents-mobile');
                    disclosure.querySelector('summary').click();
                    return !disclosure.open;
                })()`);
                assert.equal(closed, true, "native mobile contents did not close");
            }
        };
        let checked = 0;
        matrix: for (const locale of locales) for (const width of widths) for (const surface of surfaces) {
            const label = `${locale}/${surface}/${width}`;
            try {
                await call("Emulation.setDeviceMetricsOverride", { width, height: 936, deviceScaleFactor: 1, mobile: false }, sessionId);
                const loaded = wait("Page.loadEventFired", sessionId);
                const [navigation] = await Promise.all([
                    call("Page.navigate", { url: `${base}/${locale}/${surface}.html` }, sessionId),
                    loaded,
                ]);
                assert.ok(!navigation.errorText, `navigation failed: ${navigation.errorText}`);
                await checkSemantics();
                const layout = await evaluate(`(() => {
                    const visible = element => { const box = element.getBoundingClientRect(); return box.width > 0 && box.height > 0 && getComputedStyle(element).display !== 'none'; };
                    return { viewport: innerWidth, page: document.documentElement.scrollWidth,
                        downloads: [...document.querySelectorAll('a[href*="download=1"]')].map(visible),
                        columns: [...document.querySelectorAll('table.log-files, table.logs-table')].map(table => ({ headers: table.querySelectorAll('thead th').length, cells: table.querySelector('tbody tr')?.children.length })),
                        overflow: [...document.querySelectorAll('main *')].filter(element => visible(element) && element.getBoundingClientRect().right > innerWidth + 1).slice(0, 8).map(element => ({ tag: element.tagName, class: element.className, right: element.getBoundingClientRect().right })) };
                })()`);
                assert.ok(layout.page <= layout.viewport + 1, JSON.stringify(layout));
                if (surface === "logs") {
                    assert.ok(layout.downloads.length >= 2, "selected-day and archive downloads missing");
                    assert.ok(layout.downloads.every(Boolean), "hidden log download");
                    for (const columns of layout.columns) assert.equal(columns.headers, columns.cells);
                }
                if (surface === "home") {
                    await checkContents();
                    const lists = await evaluate(`(() => [...document.querySelectorAll('ol.highlights')].map(list => ({
                        role: list.getAttribute('role'),
                        cards: [...list.children].map(card => ({ tag: card.tagName,
                            article: Boolean(card.querySelector('.article-summary a[href]')) }))
                    })))()`);
                    assert.ok(lists.length > 0, "missing article lists");
                    for (const list of lists) {
                        assert.equal(list.role, "list", "Safari list semantics");
                        for (const card of list.cards) {
                            assert.equal(card.tag, "LI", "article is not a semantic list item");
                            assert.equal(card.article, true, "article title link missing");
                        }
                    }
                    const opened = await evaluate(`(() => { const dialog = document.querySelector('dialog:has(.table-scroll)'); const button = document.querySelector('button[command="show-modal"][commandfor="' + dialog?.id + '"]'); if (!button) return false; button.focus(); button.click(); return document.querySelector('dialog[open]')?.contains(document.activeElement); })()`);
                    assert.equal(opened, true, "dialog initial focus");
                    await call("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, modifiers: 8 }, sessionId);
                    await call("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, modifiers: 8 }, sessionId);
                    assert.equal(await evaluate("document.querySelector('dialog[open]').contains(document.activeElement)"), true, `Shift+Tab escaped modal: ${await evaluate("document.activeElement.outerHTML.slice(0,400)")}`);
                    await call("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 }, sessionId);
                    await call("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 }, sessionId);
                    assert.equal(await evaluate("document.activeElement?.getAttribute('command')"), "close", "Tab did not wrap to Close");
                    await evaluate("document.querySelector('.brand').focus()");
                    assert.equal(await evaluate("document.querySelector('dialog[open]').contains(document.activeElement)"), true, "background accepted focus");
                    const modal = await evaluate("({page:document.documentElement.scrollWidth, width:innerWidth, dialog:document.querySelector('dialog[open]').getBoundingClientRect().width})");
                    assert.ok(modal.page <= modal.width + 1 && modal.dialog <= modal.width, JSON.stringify(modal));
                    if (width >= 1120) {
                        const report = await evaluate(`(() => {
                            const region = document.querySelector('dialog[open] .table-scroll');
                            const viewport = region.getBoundingClientRect();
                            return { width: region.clientWidth, content: region.scrollWidth,
                                fields: [...region.querySelectorAll('th, td')].map(element => {
                                    const box = element.getBoundingClientRect();
                                    return { text: element.textContent.slice(0,80), left: box.left - viewport.left, right: box.right - viewport.left };
                                }) };
                        })()`);
                        assert.ok(report.content <= report.width + 1, `desktop report needs horizontal scrolling: ${JSON.stringify(report)}`);
                        assert.ok(report.fields.every(field => field.left >= -1 && field.right <= report.width + 1), `desktop report fields outside viewport: ${JSON.stringify(report)}`);
                    }
                    if (width === 320 || width === 1440) {
                        const shot = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-details-${width}.png`), Buffer.from(shot.data, "base64"));
                    }
                    await call("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }, sessionId);
                    await call("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }, sessionId);
                    assert.equal(await evaluate("new Promise(resolve => requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.getAttribute('command') === 'show-modal')))"), true, "Escape or focus restoration");
                    assert.equal(await evaluate("new Promise(resolve => { document.activeElement.click(); document.querySelector('dialog[open] button[command=close]').click(); requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.getAttribute('command') === 'show-modal')); })"), true, "Close or focus restoration");
                }
                if (width === 320 || width === 1440) {
                    await evaluate("scrollTo(0, 0)");
                    const shot = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                    fs.writeFileSync(path.join(outputRoot, `${locale}-${surface}-${width}.png`), Buffer.from(shot.data, "base64"));
                    if (surface === "maintenance") {
                        await evaluate("document.querySelector('.maintenance-table').scrollIntoView()");
                        const rows = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-maintenance-rows-${width}.png`), Buffer.from(rows.data, "base64"));
                    }
                }
                checked++;
                if (checked % (widths.length * surfaces.length) === 0) console.log(`Checked ${checked} page cases; completed ${locale}.`);
            } catch (error) {
                failures.push({ label, error: error.message });
                console.error(`${label}: ${error.message}`);
                if (["CHECK_TIMEOUT", "CDP_CLOSED"].includes(error.code)) break matrix;
            }
        }
        let withoutJavaScript = 0;
        await call("Emulation.setScriptExecutionDisabled", { value: true }, sessionId);
        await call("Emulation.setDeviceMetricsOverride", { width: 390, height: 936, deviceScaleFactor: 1, mobile: false }, sessionId);
        for (const locale of locales) for (const surface of surfaces) {
            const label = `${locale}/${surface}/no-javascript`;
            try {
                const loaded = wait("Page.loadEventFired", sessionId);
                await Promise.all([
                    call("Page.navigate", { url: `${base}/${locale}/${surface}.html` }, sessionId),
                    loaded,
                ]);
                await checkSemantics();
                if (surface === "home") await checkContents();
                const available = await evaluate(`(() => ({
                    links: [...document.querySelectorAll('.global-navigation a')].every(link => link.getBoundingClientRect().height > 0),
                    controls: [...document.querySelectorAll('form input:not([type=hidden]), form select, form button')].every(control => !control.disabled && control.getBoundingClientRect().height > 0),
                    content: Boolean(document.querySelector('main')?.textContent.trim())
                }))()`);
                assert.equal(available.links, true, "navigation hidden without JavaScript");
                assert.equal(available.controls, true, "native controls unavailable without JavaScript");
                assert.equal(available.content, true, "page content depends on JavaScript");
                withoutJavaScript++;
            } catch (error) {
                failures.push({ label, error: error.message });
                console.error(`${label}: ${error.message}`);
            }
        }
        const result = { browser: browserVersion, checked, withoutJavaScript, widths, locales, surfaces, failures };
        fs.writeFileSync(path.join(outputRoot, "results.json"), JSON.stringify(result, null, 2));
        console.log(JSON.stringify(result, null, 2));
        assert.equal(failures.length, 0, "responsive interface checks failed");
    } finally {
        socket?.close();
        browser.kill("SIGTERM");
        if (browser.exitCode === null) {
            try { await bounded(once(browser, "exit"), "Chromium shutdown", 5000); }
            catch {
                browser.kill("SIGKILL");
                if (browser.exitCode === null) await bounded(once(browser, "exit"), "Chromium forced shutdown", 5000);
            }
        }
        server.closeAllConnections();
        server.close();
        await fs.promises.rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
