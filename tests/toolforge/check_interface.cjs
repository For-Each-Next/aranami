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
const widths = [320, 390, 600, 601, 639, 640, 641, 768, 943, 944, 1120, 1440, 1680, 1920];
const locales = ["en", "zh-hans", "zh-hant"];
const surfaces = ["home", "backlog", "maintenance", "reports", "logs", "about", "error", "newsletter"];
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
    let pendingStartupScripts;
    const server = http.createServer((request, response) => {
        const pathname = new URL(request.url, "http://localhost").pathname;
        const filename = (["/navigation.js", "/aranami-icon.png", "/aranami-wordmark.png", "/favicon.png", "/live.js", "/newsletter.js", "/styles.css", "/newsletter.css", "/codex.css", "/codex-select.svg", "/codex-select-disabled.svg", "/codex-license.html", "/favicon.svg", "/aranami.svg", "/report-icons.svg"].includes(pathname) || pathname.startsWith("/review-icons/"))
            ? path.join(assetRoot, pathname) : path.join(fixtureRoot, pathname);
        if (!filename.startsWith(fixtureRoot + path.sep) && !filename.startsWith(assetRoot + path.sep)) {
            response.writeHead(404).end(); return;
        }
        try {
            response.setHeader("Content-Security-Policy", "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
            response.setHeader("Content-Type", ({ ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png" })[path.extname(filename)] || "text/plain");
            const send = () => response.end(fs.readFileSync(filename));
            if (pendingStartupScripts && pathname.endsWith(".js")) pendingStartupScripts.push(send);
            else send();
        } catch { response.writeHead(404).end(); }
    });
    server.listen(0, "127.0.0.1");
    await bounded(once(server, "listening"), "local fixture server startup");
    const base = `http://127.0.0.1:${server.address().port}`;
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), "aranami-browser-"));
    // Headless Linux has no hardware mouse; select desktop input explicitly.
    // This is the headless Chromium setup used by Playwright's launcher.
    const desktopInput = "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4";
    const browser = spawn(chromium, ["--headless", desktopInput, "--disable-gpu", "--no-first-run", "--disable-background-networking", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
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
        await call("Network.enable", {}, sessionId);
        await call("Network.setCacheDisabled", {cacheDisabled: true}, sessionId);
        await call("Page.addScriptToEvaluateOnNewDocument", {source: `
            window.aranamiStartupErrors = [];
            addEventListener('error', event => window.aranamiStartupErrors.push(event.message));
        `}, sessionId);
        const evaluate = async expression => {
            const result = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId);
            if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
            return result.result.value;
        };
        const inputCapabilities = await evaluate(`({
            hover: matchMedia('(hover: hover)').matches,
            finePointer: matchMedia('(pointer: fine)').matches
        })`);
        assert.deepEqual(inputCapabilities, {hover: true, finePointer: true}, "interface checks require emulated desktop mouse input");
        const press = async (key, virtual, modifiers = 0) => {
            // Enter needs its character event for Chromium's native summary activation.
            await call("Input.dispatchKeyEvent", { type: "keyDown", key, code: key, windowsVirtualKeyCode: virtual, modifiers, ...(key === "Enter" ? { text: "\r" } : {}) }, sessionId);
            await call("Input.dispatchKeyEvent", { type: "keyUp", key, code: key, windowsVirtualKeyCode: virtual, modifiers }, sessionId);
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
                forms: [...document.forms].map(form => ({method: form.method, action: form.getAttribute('action'), submit: Boolean(form.querySelector('button[type=submit], input[type=submit]')), issueSelection: form.hasAttribute('data-newsletter-issue-form')}))
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
                assert.ok(form.submit || form.issueSelection, "form needs a native submit control or saved-issue navigation");
            }
        };
        const checkContents = async () => {
            const checkVertical = async selector => {
                const lists = await evaluate(`(() => [...document.querySelectorAll('${selector} ul')].map(list => {
                    const items = [...list.children].filter(item => item.tagName === 'LI');
                    const boxes = items.map(item => item.getBoundingClientRect());
                    return {display: getComputedStyle(list).display, items: items.every(item => getComputedStyle(item).display === 'block'), vertical: boxes.every((box, index) => index === 0 || box.top >= boxes[index - 1].bottom - 1)};
                }))()`);
                assert.ok(lists.length > 0 && lists.every(list => list.display === 'block' && list.items && list.vertical), "contents entries must flow vertically at every width");
            };
            const navigation = await evaluate(`(() => {
                const desktop = document.querySelector('.contents-desktop');
                const mobile = document.querySelector('.contents-mobile');
                const visible = element => element.getBoundingClientRect().height > 0;
                return {desktop: visible(desktop), mobile: visible(mobile), open: mobile.open};
            })()`);
            assert.notEqual(navigation.desktop, navigation.mobile, "exactly one contents navigation should be visible");
            if (navigation.desktop) await checkVertical('.contents-desktop');
            if (navigation.mobile) {
                assert.equal(navigation.open, false, "mobile contents should start collapsed");
                const opened = await evaluate(`(() => {
                    const disclosure = document.querySelector('.contents-mobile');
                    disclosure.querySelector('summary').click();
                    const summary = disclosure.querySelector('summary').getBoundingClientRect();
                    const menu = disclosure.querySelector('nav').getBoundingClientRect();
                    return {
                        visible: disclosure.open && [...disclosure.querySelectorAll('a')].every(link => link.getBoundingClientRect().height > 0),
                        anchored: menu.top >= summary.bottom && menu.top <= summary.bottom + 16,
                        onScreen: menu.top < innerHeight && menu.left >= 0 && menu.right <= innerWidth
                    };
                })()`);
                assert.equal(opened.visible, true, "native mobile contents did not reveal links");
                assert.equal(opened.anchored, true, "mobile contents menu must stay below its button");
                assert.equal(opened.onScreen, true, "mobile contents menu must stay within the viewport");
                await checkVertical('.contents-mobile');
                if ([320, 639, 641].includes(await evaluate('innerWidth')) && await evaluate("Boolean(document.querySelector('.review-cards'))")) {
                    const width = await evaluate('innerWidth');
                    const locale = await evaluate('document.documentElement.lang.toLowerCase()');
                    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
                    const shot = await call("Page.captureScreenshot", {format: "png", captureBeyondViewport: false}, sessionId);
                    fs.writeFileSync(path.join(outputRoot, `${locale}-contents-${width}.png`), Buffer.from(shot.data, 'base64'));
                }
                const closed = await evaluate(`(() => {
                    const disclosure = document.querySelector('.contents-mobile');
                    disclosure.querySelector('summary').click();
                    return !disclosure.open;
                })()`);
                assert.equal(closed, true, "native mobile contents did not close");
            }
        };
        const checkTools = async () => {
            const selector = '.global-navigation .navigation-tools';
            const isOpen = () => evaluate(`document.querySelector('${selector}').open`);
            const move = async (selector, edge = 'center') => {
                const point = await evaluate(`(() => {
                    const box = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();
                    return {x: box.left + box.width / 2, y: ${edge === 'gap' ? 'box.top - 2' : 'box.top + box.height / 2'}};
                })()`);
                await call('Input.dispatchMouseEvent', {type: 'mouseMoved', ...point}, sessionId);
                return point;
            };
            const click = async point => {
                await call('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...point}, sessionId);
                await call('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...point}, sessionId);
            };
            await evaluate("document.querySelector('h1').focus(); document.activeElement.blur()");
            await move('h1');
            await move(`${selector} summary`);
            assert.equal(await isOpen(), true, 'mouse hover did not open Tools');
            await move(`${selector} ul`, 'gap');
            assert.equal(await isOpen(), true, 'Tools closed while crossing its popup gap');
            await move(`${selector} a`);
            assert.equal(await isOpen(), true, 'Tools links cannot be reached by pointer');
            const links = await evaluate(`(() => [...document.querySelectorAll('.masthead nav a')].map(link => {
                const style = getComputedStyle(link);
                return {color: style.color, decoration: style.textDecorationLine};
            }))()`);
            assert.ok(links.every(link => link.color === 'rgb(32, 33, 34)' && link.decoration === 'none'), 'header links must stay near-black without underlines');
            await move('h1');
            assert.equal(await isOpen(), false, 'hover-only Tools stayed open after leaving');
            await evaluate("document.querySelector('.brand').focus()");
            await move(`${selector} summary`);
            await press('Escape', 27);
            assert.equal(await evaluate(`!document.querySelector('${selector}').open && document.activeElement === document.querySelector('.brand')`), true, 'Escape did not dismiss hover preview while preserving outside focus');
            await move('h1');
            const summary = await move(`${selector} summary`);
            await click(summary);
            assert.equal(await isOpen(), true, 'clicking a hover-open Tools menu should keep it open');
            await move('h1');
            assert.equal(await isOpen(), true, 'clicked Tools closed on pointer exit');
            await click(await move(`${selector} summary`));
            assert.equal(await isOpen(), false, 'second summary click did not close Tools');
            await move('h1');
            await move(`${selector} summary`);
            await evaluate(`document.querySelector('${selector} summary').focus()`);
            await press('ArrowDown', 40);
            await move('h1');
            assert.equal(await evaluate(`document.querySelector('${selector}').open && document.activeElement === document.querySelector('${selector} a')`), true, 'pointer exit hid keyboard-focused Tools links');
            await press('Escape', 27);
            assert.equal(await evaluate(`!document.querySelector('${selector}').open && document.activeElement === document.querySelector('${selector} summary')`), true, 'Escape did not dismiss Tools and restore summary focus');
            await press('Enter', 13);
            assert.equal(await isOpen(), true, 'keyboard activation did not open Tools');
            await press('Tab', 9);
            assert.equal(await evaluate(`document.activeElement === document.querySelector('${selector} a')`), true, 'Tab did not reach Tools links');
            assert.equal(await evaluate("getComputedStyle(document.activeElement).outlineStyle !== 'none'"), true, 'keyboard focus outline missing');
            await press('Tab', 9);
            assert.equal(await isOpen(), false, 'focus leaving Tools did not close it');
            await evaluate(`document.querySelector('${selector}').dispatchEvent(new PointerEvent('pointerenter', {pointerType: 'touch'}))`);
            assert.equal(await isOpen(), false, 'touch pointer unexpectedly opened Tools');
            await evaluate(`document.querySelector('${selector} summary').click()`);
            assert.equal(await isOpen(), true, 'native click no longer opens Tools');
            await click(await move('h1'));
            assert.equal(await isOpen(), false, 'outside click did not close Tools');
            await move('.language-menu summary');
            assert.equal(await evaluate("document.querySelector('.language-menu').open"), false, 'hover behavior leaked into language menu');
            await move('h1');
        };
        const checkNewsletterControlHeights = async () => {
            const controls = await evaluate(`(() => {
                const selectors = '.newsletter-controls select, .newsletter-controls button, .newsletter-date-selection input, .newsletter-date-selection button, .newsletter-copy-controls .cdx-button';
                const expected = 2 * parseFloat(getComputedStyle(document.documentElement).fontSize);
                return {expected, heights: [...document.querySelectorAll(selectors)].filter(control => control.getBoundingClientRect().height > 0).map(control => ({tag: control.tagName, height: control.getBoundingClientRect().height}))};
            })()`);
            assert.ok(controls.heights.length >= 4 && controls.heights.every(control => Math.abs(control.height - controls.expected) < 1), `newsletter controls must share the Codex medium height: ${JSON.stringify(controls)}`);
        };
        const checkNewsletterKeyboardActions = async () => {
            await evaluate("document.querySelector('.newsletter-text').focus()");
            await press("Tab", 9);
            assert.equal(await evaluate("document.activeElement.matches('[data-newsletter-copy]:focus-visible')"), true, "Copy must be reachable with visible keyboard focus");
            await press("Tab", 9);
            assert.equal(await evaluate("document.activeElement.matches('.newsletter-copy-controls a:first-of-type:focus-visible')"), true, "View newsletter page must be reachable with visible keyboard focus");
            await press("Tab", 9);
            assert.equal(await evaluate("document.activeElement.matches('.newsletter-copy-controls a:nth-of-type(2):focus-visible')"), true, "Edit newsletter page must be reachable with visible keyboard focus");
        };
        const initialControls = `(() => [...document.querySelectorAll('button, input:not([type=hidden]), select, summary, a.cdx-button, .brand img, main h1, .newsletter-text')].map(element => {
            const style = getComputedStyle(element);
            const box = element.getBoundingClientRect();
            return {tag: element.tagName, id: element.id, hidden: element.hidden,
                background: style.backgroundColor, color: style.color,
                border: style.borderColor, font: style.font,
                display: style.display, visibility: style.visibility,
                geometry: [box.x, box.y, box.width, box.height].map(value => Math.round(value * 100) / 100)};
        }))()`;
        let stableInitializations = 0;
        for (const locale of locales) for (const width of [320, 1440]) for (const surface of surfaces) {
            const label = `${locale}/${surface}/${width}/initial-paint`;
            try {
                await call("Emulation.setDeviceMetricsOverride", {width, height: 936, deviceScaleFactor: 1, mobile: false}, sessionId);
                pendingStartupScripts = [];
                const loaded = wait("Page.loadEventFired", sessionId);
                await call("Page.navigate", {url: `${base}/${locale}/${surface}.html`}, sessionId);
                await evaluate(`new Promise(resolve => {
                    const ready = () => {
                        if (document.readyState !== 'loading' && document.querySelector('main') && [...document.querySelectorAll('link[rel=stylesheet]')].every(link => link.sheet)) resolve();
                        else setTimeout(ready, 10);
                    };
                    ready();
                })`);
                const before = await evaluate(initialControls);
                const scripts = pendingStartupScripts;
                pendingStartupScripts = undefined;
                for (const send of scripts) send();
                await loaded;
                const after = await evaluate(initialControls);
                assert.deepEqual(after, before, "initialization changed control visibility, colors, typography, or layout");
                assert.deepEqual(await evaluate("window.aranamiStartupErrors"), [], "page initialization raised an error");
                stableInitializations++;
            } catch (error) {
                const scripts = pendingStartupScripts || [];
                pendingStartupScripts = undefined;
                for (const send of scripts) send();
                failures.push({label, error: error.message});
                console.error(`${label}: ${error.message}`);
            }
        }
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
                assert.deepEqual(await evaluate("window.aranamiStartupErrors"), [], "page initialization raised an error");
                await checkSemantics();
                await checkContents();
                const layout = await evaluate(`(() => {
                    const visible = element => { const box = element.getBoundingClientRect(); return box.width > 0 && box.height > 0 && getComputedStyle(element).display !== 'none'; };
                    return { viewport: innerWidth, page: document.documentElement.scrollWidth,
                        downloads: [...document.querySelectorAll('a[href*="download=1"]')].map(visible),
                        columns: [...document.querySelectorAll('table.log-files, table.logs-table')].map(table => ({ headers: table.querySelectorAll('thead th').length, cells: table.querySelector('tbody tr')?.children.length })),
                        overflow: [...document.querySelectorAll('main *')].filter(element => visible(element) && element.getBoundingClientRect().right > innerWidth + 1).slice(0, 8).map(element => ({ tag: element.tagName, class: element.className, right: element.getBoundingClientRect().right })) };
                })()`);
                assert.ok(layout.page <= layout.viewport + 1, JSON.stringify(layout));
                if (surface === "maintenance") {
                    const table = await evaluate(`(() => {
                        const table = document.querySelector('.maintenance-table');
                        const scroll = table.closest('.table-scroll');
                        const headers = [...table.querySelectorAll('thead th')];
                        const cells = [...table.querySelector('tbody tr').children];
                        const aligned = headers.length === cells.length && headers.every((header, index) => Math.abs(header.getBoundingClientRect().left - cells[index].getBoundingClientRect().left) < 1);
                        const before = scroll.scrollLeft;
                        scroll.scrollLeft = 60;
                        const scrollable = scroll.scrollWidth <= scroll.clientWidth || scroll.scrollLeft > before;
                        scroll.scrollLeft = 0;
                        return {display: getComputedStyle(table).display, headers: headers.every(header => header.getBoundingClientRect().height > 0), aligned, scrollable, contained: scroll.getBoundingClientRect().right <= innerWidth};
                    })()`);
                    assert.equal(table.display, "table", "worklist must retain table layout below 640 pixels");
                    assert.ok(table.headers && table.aligned, "worklist headers and cells lost column alignment");
                    assert.ok(table.scrollable && table.contained, "wide worklist must scroll locally within the viewport");
                }
                if (surface === "logs") {
                    assert.ok(layout.downloads.length >= 2, "selected-day and archive downloads missing");
                    assert.ok(layout.downloads.every(Boolean), "hidden log download");
                    for (const columns of layout.columns) assert.equal(columns.headers, columns.cells);
                }
                if (surface === "home") {
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
                    assert.equal(await evaluate("document.querySelectorAll('.page-content h3, #tool-status, #backlog').length"), 0, "dashboard sections should have peer h2 headings");
                    if (width === 320 || width === 1440) {
                        await checkTools();
                        await evaluate("document.querySelector('.language-menu summary').focus()");
                        await press("ArrowUp", 38);
                        const language = await evaluate(`new Promise(resolve => requestAnimationFrame(() => {
                            const menu = document.querySelector('.language-menu');
                            const links = [...menu.querySelectorAll('a[href]')];
                            const panel = menu.querySelector('ul').getBoundingClientRect();
                            resolve({open: menu.open, last: document.activeElement === links.at(-1),
                                visible: links.every(link => link.getBoundingClientRect().height > 0),
                                withinViewport: panel.left >= -1 && panel.right <= innerWidth + 1});
                        }))`);
                        assert.equal(language.open, true, "ArrowUp did not open language menu");
                        assert.equal(language.last, true, "ArrowUp from summary should focus last language");
                        assert.equal(language.visible, true, "open language choices are hidden");
                        assert.equal(language.withinViewport, true, "language menu exceeds viewport");
                        await press("Escape", 27);
                        assert.equal(await evaluate("!document.querySelector('.language-menu').open && document.activeElement === document.querySelector('.language-menu summary')"), true, "Escape should close language menu and restore summary focus");
                        assert.equal(await evaluate(`(() => {
                            const menu = document.querySelector('.language-menu');
                            menu.querySelector('summary').click();
                            const opened = menu.open;
                            document.querySelector('h1').click();
                            return opened && !menu.open;
                        })()`), true, "language menu click/outside dismissal failed");
                    }
                }
                if (surface === "newsletter") {
                    await checkNewsletterControlHeights();
                    const calendar = await evaluate(`(() => {
                        const input = document.querySelector('#newsletter-data-date');
                        const box = input.getBoundingClientRect();
                        return {type: input.type, visible: !input.hidden,
                            metadataAbsent: !document.querySelector('[data-newsletter-metadata]'),
                            issueSubmitHidden: document.querySelector('[data-newsletter-issue-form] button[type=submit]').hidden,
                            count: document.querySelectorAll('input[name="date"]').length,
                            duplicate: !!document.querySelector('[name="feature_date"]'),
                            minimum: input.min, maximum: input.max,
                            withinViewport: box.left >= 0 && box.right <= innerWidth + 1};
                    })()`);
                    assert.equal(calendar.type, "date");
                    assert.equal(calendar.visible, true);
                    assert.equal(calendar.metadataAbsent, true, "removed draft metadata is still displayed");
                    assert.equal(calendar.issueSubmitHidden, false, "issue submit button disappeared after initialization");
                    assert.equal(calendar.count, 1);
                    assert.equal(calendar.duplicate, false);
                    assert.equal(calendar.minimum, "2026-01-01");
                    assert.equal(calendar.maximum, "2026-12-31");
                    assert.equal(calendar.withinViewport, true);
                    if ([320, 640, 1440].includes(width)) {
                        const switched = await evaluate(`(async () => {
                            const form = document.querySelector('[data-newsletter-issue-form]');
                            const select = form.querySelector('select[name=issue]');
                            const other = [...select.options].find(option => option.value !== select.value);
                            const page = document.documentElement;
                            const originalFetch = window.fetch;
                            const source = await (await originalFetch(location.href)).text();
                            let request;
                            let copied;
                            let writes = 0;
                            const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
                            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => { copied = text; writes++; }}});
                            window.fetch = async (url, options) => {
                                request = {url: String(url), method: options?.method || 'GET'};
                                const response = new DOMParser().parseFromString(source, 'text/html');
                                for (const option of response.querySelectorAll('select[name=issue] option')) option.toggleAttribute('selected', option.value === other.value);
                                response.querySelector('input[name=issue]').value = other.value;
                                response.querySelector('[data-newsletter-wikitext]').textContent = 'Fetched issue ' + other.value + ' {{literal|<source>}}';
                                return new Response(response.documentElement.outerHTML, {headers: {'Content-Type': 'text/html'}});
                            };
                            try {
                                select.focus();
                                select.value = other.value;
                                select.dispatchEvent(new Event('change', {bubbles: true}));
                                const deadline = performance.now() + 5000;
                                while (!document.querySelector('[data-newsletter-wikitext]')?.textContent.startsWith('Fetched issue ')) {
                                    if (performance.now() > deadline) throw new Error('Issue selection did not replace draft content');
                                    await new Promise(resolve => requestAnimationFrame(resolve));
                                }
                                const draft = document.querySelector('[data-newsletter-wikitext]');
                                const copy = document.querySelector('[data-newsletter-copy]');
                                const [view, edit] = document.querySelector('.newsletter-copy-controls').querySelectorAll('a');
                                copy.click();
                                await Promise.resolve();
                                const requested = new URL(request.url, location.href);
                                return {sameDocument: document.documentElement === page,
                                    stableSelect: document.querySelector('select[name=issue]') === select && document.activeElement === select,
                                    requestedIssue: requested.searchParams.get('issue'), selectedIssue: other.value,
                                    readOnly: request.method === 'GET', noDate: !requested.searchParams.has('date'),
                                    submitHidden: form.querySelector('button[type=submit]').hidden,
                                    calendarReady: document.querySelector('#newsletter-data-date').type === 'date' && !document.querySelector('#newsletter-data-date').hidden,
                                    copyReady: !copy.hidden && copied === draft.textContent && writes === 1,
                                    wikiReady: [view, edit].every(link => link && !link.hidden && !link.hasAttribute('data-newsletter-copy') && new URL(link.href).origin === 'https://zh.wikipedia.org') && !new URL(view.href).search && new URL(edit.href).searchParams.get('action') === 'edit' && new URL(edit.href).searchParams.get('summary').startsWith('[[toolforge:aranami/newsletter/|'),
                                    buttonWeights: copy.classList.contains('cdx-button--weight-normal') && view?.classList.contains('cdx-button--weight-normal') && edit?.classList.contains('cdx-button--weight-primary'),
                                    noDestination: !document.querySelector('.newsletter-destination'),
                                    plainText: draft.children.length === 0};
                            } finally {
                                window.fetch = originalFetch;
                                if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard);
                                else delete navigator.clipboard;
                            }
                        })()`);
                        assert.equal(switched.sameDocument, true, "issue selection reloaded the page");
                        assert.equal(switched.stableSelect, true, "issue update lost selector or keyboard focus");
                        assert.equal(switched.requestedIssue, switched.selectedIssue, "wrong issue requested");
                        assert.ok(switched.readOnly && switched.noDate, "issue selection needs a fresh read-only date scope");
                        assert.equal(switched.submitHidden, false, "issue update hid the submit button");
                        assert.ok(switched.calendarReady && switched.copyReady, "replacement draft controls were not initialized");
                        assert.ok(switched.wikiReady && switched.buttonWeights, "replacement drafts need independent Copy, View, and Edit actions");
                        assert.equal(switched.noDestination, true, "standalone newsletter destination should be removed");
                        assert.equal(switched.plainText, true, "draft text should not contain syntax highlighting spans");
                        await checkNewsletterControlHeights();
                        await checkNewsletterKeyboardActions(true);
                        await call("Fetch.enable", {patterns: [{urlPattern: "https://zh.wikipedia.org/*", resourceType: "Document", requestStage: "Request"}]}, sessionId);
                        try {
                            const navigation = wait("Fetch.requestPaused", sessionId);
                            const target = await evaluate(`(() => {
                                window.newsletterNavigationProbe = {writes: 0, clipboard: Object.getOwnPropertyDescriptor(navigator, 'clipboard')};
                                Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async () => { window.newsletterNavigationProbe.writes++; }}});
                                const link = document.querySelector('.newsletter-copy-controls a');
                                link.click();
                                return link.href;
                            })()`);
                            const request = await navigation;
                            // A 204 response retains the fixture document and avoids an external request.
                            await call("Fetch.fulfillRequest", {requestId: request.requestId, responseCode: 204}, sessionId);
                            assert.equal(request.request.url, target, "Go to wiki page did not navigate to its native link destination");
                            assert.equal(request.request.method, "GET", "wiki navigation must remain read-only");
                            const writes = await evaluate(`(() => {
                                const probe = window.newsletterNavigationProbe;
                                if (probe.clipboard) Object.defineProperty(navigator, 'clipboard', probe.clipboard);
                                else delete navigator.clipboard;
                                delete window.newsletterNavigationProbe;
                                return probe.writes;
                            })()`);
                            assert.equal(writes, 0, "Go to wiki page must not write to the clipboard");
                        } finally {
                            await call("Fetch.disable", {}, sessionId);
                        }
                        await evaluate("document.querySelector('.newsletter-copy-controls').scrollIntoView({block: 'center'})");
                        await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
                        const actions = await call("Page.captureScreenshot", {format: "png", captureBeyondViewport: false}, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-newsletter-actions-${width}.png`), Buffer.from(actions.data, "base64"));
                    }
                }
                if (surface === "backlog") {
                    assert.ok(await evaluate("document.querySelectorAll('.backlog-section h2').length > 0"), "maintenance groups should use h2 headings");
                }
                if (surface === "maintenance" && (width === 320 || width === 1440)) {
                    const filters = await evaluate(`(() => {
                        const form = document.querySelector('.maintenance-controls');
                        const originalUrl = location.href;
                        let submissions = 0;
                        const onSubmit = event => { event.preventDefault(); submissions++; };
                        form.addEventListener('submit', onSubmit);
                        const states = [...form.querySelectorAll('.assessment-filter')].map(field => {
                            const [grade, endpoint] = field.querySelectorAll('select');
                            const initial = [grade.value, endpoint.value];
                            const unlimited = [grade, endpoint].every(control =>
                                control.options[0].value === '' && !control.required && !control.disabled && !control.hidden);
                            grade.value = grade.options[1].value;
                            grade.dispatchEvent(new Event('change', {bubbles: true}));
                            endpoint.value = endpoint.options[1].value;
                            endpoint.dispatchEvent(new Event('change', {bubbles: true}));
                            const range = grade.getBoundingClientRect().height > 0 && endpoint.getBoundingClientRect().height > 0;
                            [grade.value, endpoint.value] = initial;
                            return {unlimited, range};
                        });
                        const sort = form.querySelector('select[name=sort]');
                        const originalSort = sort.value;
                        sort.value = sort.options[0].value;
                        sort.dispatchEvent(new Event('change', {bubbles: true}));
                        sort.value = originalSort;
                        const selectedOrder = form.querySelector('input[name=order]:checked');
                        form.querySelector('input[name=order]:not(:checked)')?.click();
                        selectedOrder.checked = true;
                        form.removeEventListener('submit', onSubmit);
                        return {states, submissions, unchangedUrl: location.href === originalUrl};
                    })()`);
                    assert.equal(filters.states.length, 2, "quality and importance range controls missing");
                    assert.ok(filters.states.every(state => state.unlimited && state.range), "range controls must retain two optional endpoints");
                    assert.equal(filters.submissions, 0, "maintenance control changes submitted automatically");
                    assert.equal(filters.unchangedUrl, true, "maintenance control changes navigated automatically");
                }
                if (surface === "reports") {
                    const detailWeights = await evaluate(`(() => [...document.querySelectorAll('.status-link')].map(button => getComputedStyle(button).fontWeight))()`);
                    assert.ok(detailWeights.length > 0, "missing report details controls");
                    assert.ok(detailWeights.every(weight => weight === "400"), "report details controls should use normal font weight");
                    assert.equal(await evaluate("document.querySelectorAll('.page-content h2').length"), 2, "site reports needs status and run history sections");
                    const opened = await evaluate(`(() => { const dialog = document.querySelector('dialog:has(.table-scroll)'); const button = document.querySelector('a[aria-haspopup="dialog"][aria-controls="' + dialog?.id + '"]'); if (!button) return false; button.focus(); button.click(); return document.querySelector('dialog[open]')?.contains(document.activeElement); })()`);
                    assert.equal(opened, true, "dialog initial focus");
                    assert.equal(await evaluate("getComputedStyle(document.querySelector('dialog[open] table')).display"), "table", "report details must retain table columns at narrow widths");
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
                    assert.equal(await evaluate("new Promise(resolve => requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.matches('a[aria-haspopup=dialog]'))))"), true, "Escape or focus restoration");
                    assert.equal(await evaluate("new Promise(resolve => { document.activeElement.click(); document.querySelector('dialog[open] button[command=close]').click(); requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.matches('a[aria-haspopup=dialog]'))); })"), true, "Close or focus restoration");
                }
                if (surface === "reports" && (width === 320 || width === 1440)) {
                    const histories = await evaluate("[...document.querySelectorAll('dialog[id^=job-history-]')].map(dialog => dialog.id)");
                    assert.ok(histories.length > 0, "per-invocation history dialogs missing");
                    for (const id of histories) {
                        const opened = await evaluate(`(() => {
                            const dialog = document.getElementById(${JSON.stringify(id)});
                            const button = document.querySelector('a[aria-controls="' + dialog.id + '"][aria-haspopup="dialog"]');
                            button.focus(); button.click();
                            const bounds = dialog.getBoundingClientRect();
                            const stages = [...dialog.querySelectorAll('.job-stages li')];
                            return {open: dialog.open, focused: dialog.contains(document.activeElement),
                                stages: stages.length, dated: stages.every(stage => Boolean(stage.querySelector('time[datetime]')?.textContent.match(/^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{3}$/))),
                                withinViewport: bounds.left >= -1 && bounds.right <= innerWidth + 1,
                                contentFits: dialog.scrollWidth <= dialog.clientWidth + 1};
                        })()`);
                        assert.equal(opened.open, true, "job history trigger did not open its dialog");
                        assert.equal(opened.focused, true, "job history dialog initial focus missing");
                        assert.ok(opened.stages >= 2 && opened.dated, "job history stages need complete observation times");
                        assert.ok(opened.withinViewport && opened.contentFits, "job history dialog overflows viewport");
                        await press("Tab", 9, 8);
                        assert.equal(await evaluate("document.querySelector('dialog[open]').contains(document.activeElement)"), true, "Shift+Tab escaped job history modal");
                        await evaluate("document.querySelector('.brand').focus()");
                        assert.equal(await evaluate("document.querySelector('dialog[open]').contains(document.activeElement)"), true, "background accepted focus while job history modal is open");
                        const shot = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-${id}-${width}.png`), Buffer.from(shot.data, "base64"));
                        await press("Escape", 27);
                        assert.equal(await evaluate(`new Promise(resolve => requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.getAttribute('aria-controls') === ${JSON.stringify(id)})))`), true, "job history Escape did not restore trigger focus");
                        assert.equal(await evaluate(`new Promise(resolve => {
                            document.activeElement.click();
                            document.getElementById(${JSON.stringify(id)}).querySelector('button[command=close]').click();
                            requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.getAttribute('aria-controls') === ${JSON.stringify(id)}));
                        })`), true, "job history Close did not restore trigger focus");
                    }
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
        for (const locale of locales) for (const surface of surfaces) for (const width of surface === "newsletter" ? [320, 390, 640, 1440] : [390]) {
            const label = `${locale}/${surface}/${width}/no-javascript`;
            try {
                await call("Emulation.setDeviceMetricsOverride", {width, height: 936, deviceScaleFactor: 1, mobile: false}, sessionId);
                const loaded = wait("Page.loadEventFired", sessionId);
                await Promise.all([
                    call("Page.navigate", { url: `${base}/${locale}/${surface}.html` }, sessionId),
                    loaded,
                ]);
                await checkSemantics();
                await checkContents();
                const available = await evaluate(`(() => ({
                    links: [...document.querySelectorAll('.global-navigation a')].every(link => link.getBoundingClientRect().height > 0),
                    controls: [...document.querySelectorAll('form input:not([type=hidden]), form select, form button')].filter(control => !control.closest('[hidden]')).every(control => !control.disabled && control.getBoundingClientRect().height > 0),
                    content: Boolean(document.querySelector('main')?.textContent.trim())
                }))()`);
                assert.equal(available.links, true, "navigation hidden without JavaScript");
                assert.equal(available.controls, true, "native controls unavailable without JavaScript");
                assert.equal(available.content, true, "page content depends on JavaScript");
                const toolsAvailable = await evaluate(`(() => {
                    const menu = document.querySelector('.global-navigation .navigation-tools');
                    menu.querySelector('summary').click();
                    const opened = menu.open && menu.querySelector('a').getBoundingClientRect().height > 0;
                    menu.querySelector('summary').click();
                    return opened && !menu.open;
                })()`);
                assert.equal(toolsAvailable, true, "Tools disclosure unavailable without JavaScript");
                if (surface === "reports") {
                    const fallback = await evaluate(`(() => {
                        const link = document.querySelector('a[aria-haspopup="dialog"]');
                        const dialog = document.getElementById(link.getAttribute('aria-controls'));
                        link.click();
                        const visible = getComputedStyle(dialog).display !== 'none' && dialog.getBoundingClientRect().height > 0;
                        const close = dialog.querySelector('noscript a');
                        const closeVisible = close?.getBoundingClientRect().height > 0;
                        close?.click();
                        return visible && closeVisible && getComputedStyle(dialog).display === 'none';
                    })()`);
                    assert.equal(fallback, true, "report details need native fragment access and dismissal without JavaScript");
                }
                if (surface === "newsletter") {
                    await checkNewsletterControlHeights();
                    if ([320, 640, 1440].includes(width)) await checkNewsletterKeyboardActions(false);
                    assert.equal(await evaluate(`(() => {
                        return [...document.querySelectorAll('.newsletter-copy-controls')].every(controls => {
                            const links = [...controls.querySelectorAll('a')];
                            const [view, edit] = links.map(link => new URL(link.href));
                            return links.length === 2 && links.every(link => link.getBoundingClientRect().height > 0 && !link.hasAttribute('data-newsletter-copy') && new URL(link.href).origin === 'https://zh.wikipedia.org') && !view.search && edit.searchParams.get('action') === 'edit' && edit.searchParams.get('summary').startsWith('[[toolforge:aranami/newsletter/|');
                        });
                    })()`), true, "View and Edit must remain visible native links without JavaScript");
                }
                withoutJavaScript++;
            } catch (error) {
                failures.push({ label, error: error.message });
                console.error(`${label}: ${error.message}`);
            }
        }
        const result = { browser: browserVersion, inputCapabilities, stableInitializations, checked, withoutJavaScript, widths, locales, surfaces, failures };
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
