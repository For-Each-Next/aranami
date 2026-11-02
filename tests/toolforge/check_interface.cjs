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
const assetSourceRoot = path.resolve(__dirname, "../../dist/webservice/public_html");
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
    const assetRoot = fs.mkdtempSync(path.join(os.tmpdir(), "aranami-browser-assets-"));
    try {
        // A later build may replace dist while this matrix is still running.
        fs.cpSync(assetSourceRoot, assetRoot, {recursive: true});
        await checkInterface(assetRoot);
    } finally {
        fs.rmSync(assetRoot, {recursive: true, force: true});
    }
}

async function checkInterface(assetRoot) {
    let pendingStartupScripts;
    const server = http.createServer((request, response) => {
        const requestURL = new URL(request.url, "http://localhost");
        const locale = locales.includes(requestURL.searchParams.get('lang')) ? requestURL.searchParams.get('lang') : 'en';
        const pathname = requestURL.pathname === '/maintenance/' ? `/${locale}/maintenance.html` : requestURL.pathname;
        const filename = (["/components.js", "/navigation.js", "/live.js", "/newsletter.js", "/styles.css", "/newsletter.css", "/codex.css", "/codex-license.html"].includes(pathname) || pathname.startsWith("/assets/"))
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
            window.aranamiCspViolations = [];
            addEventListener('securitypolicyviolation', event => window.aranamiCspViolations.push({directive: event.effectiveDirective, blocked: event.blockedURI}));
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
        let accordionForcedColorsChecked = false;
        const checkAccordionChevron = async () => {
            const icon = await evaluate(`(() => {
                const accordion = document.querySelector('details.cdx-accordion');
                const summary = accordion.querySelector('summary');
                const opened = accordion.open;
                const initial = getComputedStyle(summary, '::before').transform;
                accordion.open = !opened;
                getComputedStyle(summary, '::before').transform;
                for (const animation of summary.getAnimations({subtree: true})) animation.finish();
                const style = getComputedStyle(summary, '::before');
                const value = {asset: style.maskImage !== 'none' ? style.maskImage : style.backgroundImage,
                    visible: parseFloat(style.width) > 0 && parseFloat(style.height) > 0,
                    rotates: style.transform !== initial};
                accordion.open = opened;
                getComputedStyle(summary, '::before').transform;
                for (const animation of summary.getAnimations({subtree: true})) animation.finish();
                return value;
            })()`);
            assert.match(icon.asset, /\/codex-select\.svg["')]/, 'Accordion chevron must use the local SVG');
            assert.equal(icon.asset.includes('data:'), false, 'Accordion chevron must obey image CSP');
            assert.ok(icon.visible && icon.rotates, 'Accordion chevron must remain visible and rotate on expansion');
            if (!accordionForcedColorsChecked) {
                await call('Emulation.setEmulatedMedia', {features: [{name: 'forced-colors', value: 'active'}]}, sessionId);
                try {
                    const forced = await evaluate(`(() => {
                        const style = getComputedStyle(document.querySelector('details.cdx-accordion > summary'), '::before');
                        return {asset: style.maskImage, color: style.backgroundColor, adjustment: style.forcedColorAdjust};
                    })()`);
                    assert.match(forced.asset, /\/codex-select\.svg["')]/);
                    assert.notEqual(forced.color, 'rgba(0, 0, 0, 0)', 'Forced-color chevron must retain its system foreground');
                    assert.equal(forced.adjustment, 'none');
                    accordionForcedColorsChecked = true;
                } finally {
                    await call('Emulation.setEmulatedMedia', {features: []}, sessionId);
                }
            }
        };
        const checkSemantics = async () => {
            assert.deepEqual(await evaluate('window.aranamiCspViolations || []'), [], 'page violated its content security policy');
            if (await evaluate("Boolean(document.querySelector('details.cdx-accordion'))")) await checkAccordionChevron();
            const document = await evaluate(`(() => ({
                main: document.querySelectorAll('main#main').length,
                titles: document.querySelectorAll('h1').length,
                mainTitle: Boolean(document.querySelector('main h1')),
                skipTarget: document.querySelector('a.skip')?.getAttribute('href'),
                unnamedNavigation: [...document.querySelectorAll('nav')].filter(nav => !nav.getAttribute('aria-label') && !nav.getAttribute('aria-labelledby')).length,
                brokenContents: [...document.querySelectorAll('.page-toc a[href^="#"]')].filter(link => !document.getElementById(link.hash.slice(1))).map(link => link.hash),
                unlabelledControls: [...document.querySelectorAll('input:not([type=hidden]), select')].filter(control => !control.labels?.length && !control.getAttribute('aria-label') && !control.getAttribute('aria-labelledby')).map(control => control.outerHTML),
                forms: [...document.forms].map(form => ({method: form.method, action: form.getAttribute('action'), submit: Boolean(form.querySelector('button[type=submit], input[type=submit]')), issueSelection: form.hasAttribute('data-newsletter-issue-form'), dateSelection: form.matches('.newsletter-date-controls'), pageSize: form.matches('.page-size[data-auto-submit]')}))
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
                assert.ok(form.submit || form.issueSelection || form.dateSelection || form.pageSize, "form needs a native submit control or enhanced selection navigation");
            }
        };
        const checkMaintenancePaginationStatus = async () => {
            const visible = await evaluate(`(() => [...document.querySelectorAll('.cdx-table-pager__center > div')]
                .filter(status => status.getBoundingClientRect().height > 0 && getComputedStyle(status).display !== 'none')
                .map(status => ({text: status.textContent.trim(), fits: status.scrollWidth <= status.clientWidth + 1})))()`);
            assert.equal(visible.length, 1, 'exactly one responsive article-count label must be visible');
            assert.ok(visible[0].text && visible[0].fits, 'article count must remain readable within the pager');
        };
        const checkMaintenanceControls = async (scriptsEnabled = true) => {
            const controls = await evaluate(`(() => {
                const table = document.querySelector('.maintenance-table');
                const navigation = document.querySelector('.maintenance-navigation');
                const worklist = document.querySelector('.maintenance-worklist');
                const caption = document.querySelector('.cdx-table__header__caption');
                const search = document.querySelector('.maintenance-search');
                const initial = Object.fromEntries(new FormData(search));
                const originalUrl = location.href;
                const submit = (form, button) => {
                    if (!${scriptsEnabled}) {
                        const data = new FormData(form, button);
                        return {values: Object.fromEntries(data), unique: [...data.keys()].length === new Set(data.keys()).size};
                    }
                    let result;
                    form.addEventListener('submit', event => {
                        event.preventDefault();
                        const data = new FormData(form, event.submitter);
                        result = {values: Object.fromEntries(data), unique: [...data.keys()].length === new Set(data.keys()).size};
                    }, {once: true});
                    button.click();
                    return result;
                };
                const sorts = [...table.querySelectorAll('.cdx-table__table__sort-button')].map(button => {
                    const form = button.form;
                    const submitted = submit(form, button);
                    return { ...submitted, method: form.method, action: form.getAttribute('action'),
                        state: button.closest('th').getAttribute('aria-sort'),
                        icon: button.querySelector('use').getAttribute('href'),
                        native: button.tagName === 'BUTTON' && button.type === 'submit' && !button.disabled,
                        visible: button.getBoundingClientRect().height > 0 };
                });
                const query = search.querySelector('input[type=search]');
                const previousQuery = query.value;
                query.value = 'Game 0 & <title>';
                const searched = submit(search, search.querySelector('button[type=submit]'));
                query.value = previousQuery;
                const pager = document.querySelector('.cdx-table-pager');
                const limit = pager.querySelector('select[name=limit]');
                const sizeForm = limit.form;
                const sizeButton = sizeForm.querySelector('button[type=submit]');
                const previousLimit = limit.value;
                let sizeSubmission;
                limit.value = '20';
                let automatic = false;
                if (${scriptsEnabled}) {
                    sizeForm.addEventListener('submit', event => {
                        event.preventDefault();
                        sizeSubmission = Object.fromEntries(new FormData(sizeForm, event.submitter));
                    }, {once: true});
                    limit.dispatchEvent(new Event('change', {bubbles: true}));
                    automatic = Boolean(sizeSubmission);
                    if (!automatic) sizeButton?.click();
                } else sizeSubmission = Object.fromEntries(new FormData(sizeForm, sizeButton));
                limit.value = previousLimit;
                const paged = {
                    regions: ['start', 'center', 'end'].every(region => pager.querySelector('.cdx-table-pager__' + region)),
                    sizes: [...limit.options].map(option => option.value),
                    size: limit.value,
                    sizeSubmission, automatic, submitVisible: Boolean(sizeButton?.getBoundingClientRect().height > 0),
                    status: pager.querySelector('.cdx-table-pager__center').textContent.trim(),
                    controls: [...pager.querySelectorAll('.cdx-table-pager__end [data-page]')].map(control => ({
                        direction: control.dataset.page, disabled: control.disabled === true,
                        label: control.getAttribute('aria-label'), href: control.getAttribute('href'),
                        icon: Boolean(control.querySelector('svg use')), visible: control.getBoundingClientRect().height > 0,
                    })),
                };
                return {initial, sorts, searched, paged, unchangedUrl: location.href === originalUrl,
                    menuOutside: !worklist.contains(navigation) && navigation.contains(document.querySelector('.maintenance-menu')),
                    menuAbove: navigation.getBoundingClientRect().bottom <= worklist.getBoundingClientRect().top,
                    caption: caption.textContent.trim(), nativeCaption: table.caption.textContent.trim(),
                    visibleCaption: caption.getBoundingClientRect().height > 0,
                    searchInHeader: Boolean(search.closest('.cdx-table__header')),
                    oldControls: Boolean(document.querySelector('.maintenance-controls, .assessment-filter, .genre-filter, select[name=sort], input[name=order][type=radio]'))};
            })()`);
            assert.equal(controls.oldControls, false, 'removed maintenance panel controls returned');
            assert.ok(controls.menuOutside && controls.menuAbove, 'category menu must sit outside and above the table');
            assert.ok(controls.visibleCaption && controls.caption, 'current category needs a visible table caption');
            assert.equal(controls.caption, controls.nativeCaption, 'visible category differs from accessible table caption');
            assert.equal(controls.searchInHeader, true, 'title search must sit in the table header');
            assert.deepEqual(controls.sorts.map(sort => sort.values.sort), ['title', 'class', 'importance', 'oldest']);
            for (const sort of controls.sorts) {
                const active = sort.values.sort === controls.initial.sort;
                const expectedOrder = active ? (controls.initial.order === 'asc' ? 'desc' : 'asc')
                    : ['class', 'importance'].includes(sort.values.sort) ? 'desc' : 'asc';
                assert.equal(sort.values.order, expectedOrder, 'clicked sort submitter did not send its next direction');
                assert.equal(sort.values.task, controls.initial.task, 'sorting changed the category');
                assert.equal(sort.values.page, undefined, 'changing sort must reset pagination');
                assert.equal(sort.unique, true, 'sorting submitted duplicate query fields');
                assert.equal(sort.method, 'get');
                assert.equal(sort.action, '/maintenance/');
                assert.ok(sort.native && sort.visible, 'sort control must remain a visible native submit button');
                assert.equal(sort.state, active ? (controls.initial.order === 'asc' ? 'ascending' : 'descending') : 'none');
                assert.ok(sort.icon.endsWith('#sort-' + (active ? controls.initial.order : 'none')), 'sort arrow differs from active ordering');
            }
            assert.equal(controls.searched.values.q, 'Game 0 & <title>');
            assert.equal(controls.searched.values.task, controls.initial.task);
            assert.equal(controls.searched.values.sort, controls.initial.sort);
            assert.equal(controls.searched.values.order, controls.initial.order);
            assert.equal(controls.searched.values.page, undefined, 'title search must reset pagination');
            assert.equal(controls.searched.values.regex, undefined, 'header search must submit a literal title query');
            assert.equal(controls.searched.unique, true);
            assert.equal(controls.unchangedUrl, true, 'intercepted native forms unexpectedly navigated');
            assert.ok(controls.paged.regions && controls.paged.status, 'Codex pager needs size, status, and navigation regions');
            assert.deepEqual(controls.paged.sizes, ['10', '20', '50']);
            assert.equal(controls.paged.size, '10');
            assert.equal(controls.paged.automatic, scriptsEnabled, 'page size changes should auto-submit only after enhancement');
            assert.equal(controls.paged.submitVisible, !scriptsEnabled, 'page size submit button must remain available without JavaScript');
            assert.equal(controls.paged.sizeSubmission.limit, '20');
            assert.equal(controls.paged.sizeSubmission.page, undefined, 'changing page size must reset pagination');
            assert.equal(controls.paged.sizeSubmission.task, controls.initial.task);
            assert.equal(controls.paged.sizeSubmission.sort, controls.initial.sort);
            assert.equal(controls.paged.sizeSubmission.order, controls.initial.order);
            assert.deepEqual(controls.paged.controls.map(control => control.direction), ['first', 'previous', 'next', 'last']);
            for (const control of controls.paged.controls) {
                assert.ok(control.label && control.icon && control.visible, 'pager control needs a visible icon and localized name');
                if (['first', 'previous'].includes(control.direction)) {
                    assert.equal(control.disabled, true, 'first and previous must be disabled on the first page');
                    assert.equal(control.href, null);
                } else {
                    assert.equal(control.disabled, false);
                    const url = new URL(control.href, 'https://aranami.example');
                    assert.equal(url.pathname, '/maintenance/');
                    assert.equal(url.searchParams.get('task'), controls.initial.task);
                    assert.equal(url.searchParams.get('sort'), controls.initial.sort);
                    assert.equal(url.searchParams.get('order'), controls.initial.order);
                    assert.equal(url.searchParams.get('page'), control.direction === 'next' ? '2' : '11');
                }
            }
            await evaluate(`(() => {
                const menu = document.querySelector('.maintenance-menu');
                menu.open = false;
                menu.querySelector('summary').focus();
            })()`);
            await press('Enter', 13);
            assert.equal(await evaluate("document.querySelector('.maintenance-menu').open"), true, 'category menu must open from the keyboard');
            await press('Tab', 9);
            assert.equal(await evaluate("document.activeElement === document.querySelector('.maintenance-menu a')"), true, 'category links must follow the menu trigger in keyboard order');
            if (scriptsEnabled) {
                const menuLabel = await evaluate("document.documentElement.lang.toLowerCase() + '-maintenance-menu-' + innerWidth");
                const shot = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false}, sessionId);
                fs.writeFileSync(path.join(outputRoot, menuLabel + '.png'), Buffer.from(shot.data, 'base64'));
            }
            const menu = await evaluate(`(() => {
                const menu = document.querySelector('.maintenance-menu');
                const list = menu.querySelector('ul');
                const links = [...list.querySelectorAll('a')];
                const current = links.filter(link => link.getAttribute('aria-current') === 'page');
                const urls = links.map(link => new URL(link.href));
                const added = [];
                // Exercise a full catalogue even when the saved fixture has only two categories.
                while (list.children.length < 40) {
                    const item = list.firstElementChild.cloneNode(true);
                    item.querySelector('a').textContent = 'Articles needing cleanup and additional reliable references';
                    list.append(item);
                    added.push(item);
                }
                const last = list.lastElementChild.querySelector('a');
                last.focus();
                const box = list.getBoundingClientRect();
                const labelStyle = getComputedStyle(last);
                const result = {current: current.length, label: current[0]?.textContent.trim(),
                    links: urls.every(url => url.pathname === '/maintenance/' && url.searchParams.has('task') && url.searchParams.get('page') === '1'),
                    contained: box.left >= 0 && box.right <= innerWidth + 1 && box.bottom <= innerHeight + 1,
                    width: box.width, minimumWidth: Math.min(256, innerWidth - 64),
                    readable: last.getBoundingClientRect().height <= parseFloat(labelStyle.lineHeight) * 4
                        + parseFloat(labelStyle.paddingTop) + parseFloat(labelStyle.paddingBottom),
                    scrollable: list.scrollHeight > list.clientHeight && list.scrollTop > 0};
                added.forEach(item => item.remove());
                list.scrollTop = 0;
                menu.querySelector('summary').focus();
                return result;
            })()`);
            assert.equal(menu.current, 1, 'category menu must identify one current category');
            assert.equal(menu.label, controls.caption);
            assert.ok(menu.links, 'category links must reset pagination');
            assert.ok(menu.contained && menu.scrollable, 'long category menu must scroll within the viewport');
            assert.ok(menu.width >= menu.minimumWidth - 1 && menu.readable, 'category menu must retain enough width for readable long labels');
            await press('Enter', 13);
            assert.equal(await evaluate("document.querySelector('.maintenance-menu').open"), false, 'category menu must close from the keyboard');
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
        const checkLanguageMenu = async (keyboard = true) => {
            const trigger = await evaluate(`(() => {
                scrollTo(0, 0);
                const menu = document.querySelector('.language-menu');
                menu.open = false;
                const summary = menu.querySelector('summary');
                const box = summary.getBoundingClientRect();
                return {x: box.left + box.width / 2, y: box.top + box.height / 2,
                    width: box.width, height: box.height, narrow: innerWidth < 640,
                    name: summary.getAttribute('aria-label'),
                    expectedName: menu.closest('nav').getAttribute('aria-label'),
                    labelHidden: getComputedStyle(summary.querySelector('.language-label')).display === 'none',
                    chevronHidden: getComputedStyle(summary, '::after').display === 'none'};
            })()`);
            assert.ok(trigger.name && trigger.name === trigger.expectedName, 'language trigger lost its accessible name');
            assert.equal(trigger.labelHidden, trigger.narrow, 'language text must be hidden only on narrow headers');
            assert.equal(trigger.chevronHidden, trigger.narrow, 'narrow language trigger must show only its icon');
            assert.ok(trigger.width >= 44 && trigger.height >= 44, 'language trigger needs a usable touch target');
            const activate = async () => {
                const {x, y} = trigger;
                await call('Input.dispatchMouseEvent', {type: 'mousePressed', x, y, button: 'left', clickCount: 1}, sessionId);
                await call('Input.dispatchMouseEvent', {type: 'mouseReleased', x, y, button: 'left', clickCount: 1}, sessionId);
            };
            await activate();
            const language = await evaluate(`(() => {
                const menu = document.querySelector('.language-menu');
                const panel = menu.querySelector('ul').getBoundingClientRect();
                const summary = menu.querySelector('summary').getBoundingClientRect();
                const links = [...menu.querySelectorAll('a[href]')];
                return {open: menu.open, count: links.length,
                    belowTrigger: panel.top >= summary.bottom,
                    withinViewport: panel.left >= 0 && panel.right <= innerWidth + 1 && panel.top >= 0 && panel.bottom <= innerHeight,
                    reachable: links.every(link => {
                        const box = link.getBoundingClientRect();
                        return box.width >= 120 && box.height >= 44 && link.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2));
                    })};
            })()`);
            assert.ok(language.open && language.count === 3, 'pointer click must open all language choices');
            assert.ok(language.belowTrigger && language.withinViewport, 'language choices must open below the trigger inside the viewport');
            assert.ok(language.reachable, 'language choices are clipped, too narrow, or covered');
            if (keyboard) {
                const size = await evaluate('({width: innerWidth, locale: document.documentElement.lang})');
                const shot = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false}, sessionId);
                fs.writeFileSync(path.join(outputRoot, `${size.locale}-language-${size.width}.png`), Buffer.from(shot.data, 'base64'));
            }
            await activate();
            assert.equal(await evaluate("document.querySelector('.language-menu').open"), false, 'second click must close language choices');
            if (!keyboard) return;
            await evaluate("document.querySelector('.language-menu summary').focus()");
            await press('ArrowUp', 38);
            assert.equal(await evaluate(`(() => {
                const menu = document.querySelector('.language-menu');
                return menu.open && document.activeElement === [...menu.querySelectorAll('a[href]')].at(-1);
            })()`), true, 'ArrowUp should open the last language choice');
            await press('Escape', 27);
            assert.equal(await evaluate("!document.querySelector('.language-menu').open && document.activeElement === document.querySelector('.language-menu summary')"), true, 'Escape must close language choices and restore focus');
            await activate();
            await evaluate("document.querySelector('h1').click()");
            assert.equal(await evaluate("document.querySelector('.language-menu').open"), false, 'outside click must close language choices');
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
            await evaluate("document.querySelector('.newsletter-code').open = true; document.querySelector('[data-newsletter-wikitext]').focus()");
            await press("Tab", 9);
            assert.equal(await evaluate("document.activeElement.matches('[data-newsletter-copy]:focus-visible')"), true, "Copy must be reachable with visible keyboard focus");
            await press("Tab", 9);
            assert.equal(await evaluate("document.activeElement.matches('.newsletter-copy-controls a:first-of-type:focus-visible')"), true, "View newsletter page must be reachable with visible keyboard focus");
            await press("Tab", 9);
            assert.equal(await evaluate("document.activeElement.matches('.newsletter-copy-controls a:nth-of-type(2):focus-visible')"), true, "Edit newsletter page must be reachable with visible keyboard focus");
        };
        const checkNewsletterDateChange = async () => {
            await call("Fetch.enable", {patterns: [{urlPattern: `${base}/newsletter/*`, resourceType: "Document", requestStage: "Request"}]}, sessionId);
            try {
                const navigation = wait("Fetch.requestPaused", sessionId);
                const selection = await evaluate(`(async () => {
                    const input = document.querySelector('#newsletter-data-date');
                    const form = input.form;
                    let submissions = 0;
                    form.addEventListener('submit', () => { submissions++; });
                    const invalid = [];
                    for (const value of ['2025-12-31', '2027-01-01']) {
                        input.value = value;
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        await new Promise(resolve => requestAnimationFrame(resolve));
                        invalid.push({valid: input.validity.valid, submissions});
                    }
                    input.value = input.value === input.min ? input.max : input.min;
                    const values = Object.fromEntries(new FormData(form));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    return {invalid, submissions, values, noApplyButtons: [...document.querySelectorAll('.newsletter-controls button[type=submit], .newsletter-date-controls button[type=submit]')].every(button => button.getBoundingClientRect().height === 0)};
                })()`);
                const request = await navigation;
                // Keep the issue replacement in place while exercising native GET navigation.
                await call("Fetch.fulfillRequest", {requestId: request.requestId, responseCode: 204}, sessionId);
                assert.ok(selection.invalid.every(change => !change.valid && change.submissions === 0), "out-of-range date changes must not submit requests");
                assert.equal(selection.submissions, 1, "a valid date change must submit once automatically");
                assert.equal(selection.noApplyButtons, true, "newsletter changes should not require apply buttons");
                assert.equal(request.request.method, "GET", "date selection must remain read-only");
                const requested = new URL(request.request.url);
                assert.equal(requested.pathname, "/newsletter/");
                assert.deepEqual(Object.fromEntries(requested.searchParams), selection.values, "date navigation lost the current issue, date, or language");
                assert.equal(await evaluate("document.querySelector('[data-newsletter-wikitext]').value.startsWith('Fetched issue ')"), true, "intercepted date selection replaced the current document");
            } finally {
                await call("Fetch.disable", {}, sessionId);
            }
        };
        const checkNewsletterAccordion = async () => {
            const result = await evaluate(`(() => {
                const draft = document.querySelector('[data-newsletter-wikitext]');
                const accordion = draft.closest('details');
                const expected = '\\n{{模板|標題}}\\n' + '長條目名稱'.repeat(100) + '\\n</textarea><script>literal</script>\\n';
                const closed = !accordion.open;
                accordion.querySelector('summary').click();
                const identifiers = [...document.querySelectorAll('[id]')].map(element => element.id);
                return {closed, opened: accordion.open, literal: draft.value === expected,
                    readonly: draft.readOnly, native: draft.tagName === 'TEXTAREA',
                    visible: draft.getBoundingClientRect().height > 0,
                    fits: draft.scrollHeight <= draft.clientHeight + 1,
                    safe: draft.children.length === 0 && ![...document.scripts].some(script => script.textContent === 'literal'),
                    unique: new Set(identifiers).size === identifiers.length};
            })()`);
            assert.ok(result.closed && result.opened && result.visible, `native newsletter disclosure failed: ${JSON.stringify(result)}`);
            assert.ok(result.literal && result.readonly && result.native && result.safe && result.unique, `newsletter source semantics failed: ${JSON.stringify(result)}`);
            assert.equal(result.fits, true, 'expanded newsletter textarea should autosize to wrapped source');
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
                const dialogShells = await evaluate(`(() => [...document.querySelectorAll('dialog.page-updates')].map(dialog => ({
                    id: dialog.id,
                    codex: dialog.classList.contains('cdx-dialog')
                        && Boolean(dialog.querySelector(':scope > .cdx-dialog__header .cdx-dialog__header__title'))
                        && Boolean(dialog.querySelector(':scope > .cdx-dialog__body')),
                    hidden: !dialog.open && getComputedStyle(dialog).display === 'none'
                        && dialog.getBoundingClientRect().width === 0,
                })))()`);
                assert.ok(dialogShells.every(dialog => dialog.codex), `popup is missing its Codex dialog shell: ${JSON.stringify(dialogShells)}`);
                assert.ok(dialogShells.every(dialog => dialog.hidden), `closed popup is visible: ${JSON.stringify(dialogShells)}`);
                assert.equal(await evaluate("document.querySelectorAll('button.cdx-button:not([data-codex-enhanced]):not(#tool-status button):not(#live-server-status button):not(.page-updates button)').length"), 0, "Codex Vue controls were not initialized");
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
                    await checkMaintenancePaginationStatus();
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
                    assert.equal(await evaluate("document.querySelectorAll('#tool-status, #backlog').length"), 0, "dashboard must keep report monitoring and maintenance on their own pages");
                    const cards = await evaluate(`(() => [...document.querySelectorAll('.article-card-body')].map(card => ({
                        tag: card.tagName,
                        heading: card.querySelector('h3')?.id,
                        label: card.getAttribute('aria-labelledby'),
                        link: Boolean(card.querySelector('h3 a[href]')),
                        assessmentsOutsideHeading: !card.querySelector('h3 .assessment-chips')
                    })))()`);
                    for (const card of cards) {
                        assert.equal(card.tag, "ARTICLE", "dashboard card must retain article semantics");
                        assert.ok(card.heading && card.heading === card.label, "card must be named by its own title");
                        assert.ok(card.link && card.assessmentsOutsideHeading, "article title and assessment metadata must remain separate");
                    }
                    if ([320, 390, 1440].includes(width)) {
                        await checkTools();
                        await checkLanguageMenu();
                    }
                }
                if (surface === "newsletter") {
                    await checkNewsletterControlHeights();
                    await checkNewsletterAccordion();
                    const calendar = await evaluate(`(() => {
                        const input = document.querySelector('#newsletter-data-date');
                        const box = input.getBoundingClientRect();
                        return {type: input.type, visible: !input.hidden,
                            metadataAbsent: !document.querySelector('[data-newsletter-metadata]'),
                            noApplyButtons: [...document.querySelectorAll('.newsletter-controls button[type=submit], .newsletter-date-controls button[type=submit]')].every(button => button.getBoundingClientRect().height === 0),
                            count: document.querySelectorAll('input[name="date"]').length,
                            duplicate: !!document.querySelector('[name="feature_date"]'),
                            minimum: input.min, maximum: input.max,
                            withinViewport: box.left >= 0 && box.right <= innerWidth + 1};
                    })()`);
                    assert.equal(calendar.type, "date");
                    assert.equal(calendar.visible, true);
                    assert.equal(calendar.metadataAbsent, true, "removed draft metadata is still displayed");
                    assert.equal(calendar.noApplyButtons, true, "automatic issue and date selection should not show apply buttons");
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
                                while (!document.querySelector('[data-newsletter-wikitext]')?.value.startsWith('Fetched issue ')) {
                                    if (performance.now() > deadline) throw new Error('Issue selection did not replace draft content');
                                    await new Promise(resolve => requestAnimationFrame(resolve));
                                }
                                const draft = document.querySelector('[data-newsletter-wikitext]');
                                const accordion = draft.closest('details');
                                accordion.open = true;
                                await new Promise(resolve => requestAnimationFrame(resolve));
                                const copy = document.querySelector('[data-newsletter-copy]');
                                const [view, edit] = document.querySelector('.newsletter-copy-controls').querySelectorAll('a');
                                copy.click();
                                await Promise.resolve();
                                const requested = new URL(request.url, location.href);
                                return {sameDocument: document.documentElement === page,
                                    stableSelect: document.querySelector('select[name=issue]') === select && document.activeElement === select,
                                    requestedIssue: requested.searchParams.get('issue'), selectedIssue: other.value,
                                    readOnly: request.method === 'GET', noDate: !requested.searchParams.has('date'),
                                    noApplyButtons: [...document.querySelectorAll('.newsletter-controls button[type=submit], .newsletter-date-controls button[type=submit]')].every(button => button.getBoundingClientRect().height === 0),
                                    calendarReady: document.querySelector('#newsletter-data-date').type === 'date' && !document.querySelector('#newsletter-data-date').hidden,
                                    copyReady: !copy.hidden && copied === draft.value && writes === 1,
                                    autosized: draft.scrollHeight <= draft.clientHeight + 1,
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
                        assert.equal(switched.noApplyButtons, true, "issue update restored redundant apply buttons");
                        assert.ok(switched.calendarReady && switched.copyReady, "replacement draft controls were not initialized");
                        assert.equal(switched.autosized, true, "replacement draft should autosize after expansion");
                        assert.ok(switched.wikiReady && switched.buttonWeights, "replacement drafts need independent Copy, View, and Edit actions");
                        assert.equal(switched.noDestination, true, "standalone newsletter destination should be removed");
                        assert.equal(switched.plainText, true, "draft text should not contain syntax highlighting spans");
                        await checkNewsletterControlHeights();
                        await checkNewsletterKeyboardActions(true);
                        await checkNewsletterDateChange();
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
                    assert.equal(await evaluate("document.querySelectorAll('.backlog-task .cdx-thumbnail').length"), 0, "maintenance cards must not force thumbnails");
                    const icons = await evaluate(`(async () => {
                        const images = [...document.querySelectorAll('.backlog-task img.maintenance-icon')];
                        await Promise.all(images.map(image => { image.loading = 'eager'; return image.decode(); }));
                        return images.map(image => ({loaded: image.naturalWidth > 0,
                            leading: image.classList.contains('cdx-card__icon'),
                            width: image.getBoundingClientRect().width}));
                    })()`);
                    assert.ok(icons.length > 0 && icons.every(image => image.loaded && image.leading && image.width === 22), "maintenance template images must load as compact Codex icons");
                }
                if (surface === "maintenance" && (width === 320 || width === 1440)) {
                    await checkMaintenanceControls();
                }
                if (surface === "reports") {
                    const typography = await evaluate(`(() => [...document.querySelectorAll('.routine-row')].map(card => {
                        const title = card.querySelector('.cdx-card__text__title');
                        const description = card.querySelector('.cdx-card__text__description');
                        const supporting = card.querySelector('.cdx-card__text__supporting-text');
                        const text = element => {
                            const style = getComputedStyle(element);
                            return {size: parseFloat(style.fontSize), leading: parseFloat(style.lineHeight)};
                        };
                        return {title: text(title.querySelector('h3')), description: text(description.querySelector('.routine-status')), supporting: text(supporting.querySelector('li')),
                            descriptionGap: description.getBoundingClientRect().top - title.getBoundingClientRect().bottom,
                            supportingGap: supporting.getBoundingClientRect().top - description.getBoundingClientRect().bottom};
                    }))()`);
                    assert.ok(typography.length > 0 && typography.every(card => card.title.size === 16 && card.description.size === 16 && card.supporting.size === 14), `status-card text must use Codex title, description, and supporting sizes: ${JSON.stringify(typography)}`);
                    assert.ok(typography.every(card => [card.title, card.description, card.supporting].every(text => text.leading === 22)), `status-card text must share Codex's 22px leading: ${JSON.stringify(typography)}`);
                    assert.ok(typography.every(card => Math.abs(card.descriptionGap - 4) < 1 && Math.abs(card.supportingGap - 8) < 1), `status-card spacing must retain Codex's 4px description and 8px supporting gaps: ${JSON.stringify(typography)}`);
                    const statuses = await evaluate(`(() => [...document.querySelectorAll('.routine-status')].map(status => {
                        const link = status.querySelector('a.status-label.status-link');
                        const dialog = link && document.getElementById(link.getAttribute('aria-controls'));
                        return {linked: Boolean(link && status.textContent.trim() === link.textContent.trim()),
                            weight: link && getComputedStyle(link).fontWeight,
                            target: Boolean(dialog?.matches('dialog[id^=report-details-]') && document.querySelector('#tool-status').contains(dialog))};
                    }))()`);
                    assert.ok(statuses.length > 0 && statuses.every(status => status.linked && status.target), 'the status itself must open its report dialog without a separate details label');
                    assert.ok(statuses.every(status => status.weight === '400'), 'status links should use normal font weight');
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
                    const unusedHeight = await evaluate(`(() => {
                        const dialog = document.querySelector('dialog[open]');
                        return dialog.clientHeight - dialog.querySelector('.cdx-dialog__header').offsetHeight
                            - dialog.querySelector('.cdx-dialog__body').offsetHeight;
                    })()`);
                    assert.ok(unusedHeight <= 2, "dialog must fit its content without unused vertical space");
                    const tableLayout = await evaluate(`(() => {
                        const region = document.querySelector('dialog[open] .table-scroll');
                        const table = region.querySelector('table');
                        const bounds = table.getBoundingClientRect();
                        const headers = [...table.querySelectorAll('thead th')];
                        return {viewport: region.clientWidth, width: bounds.width,
                            codex: table.classList.contains('cdx-table__table') && Boolean(region.closest('.cdx-table')),
                            rows: [...table.querySelectorAll('tr')].map(row => {
                                const cells = [...row.children];
                                return {width: row.getBoundingClientRect().width,
                                    fills: Math.abs(cells[0].getBoundingClientRect().left - bounds.left) <= 1
                                        && Math.abs(cells.at(-1).getBoundingClientRect().right - bounds.right) <= 1,
                                    aligned: cells.length === headers.length && cells.every((cell, index) =>
                                        Math.abs(cell.getBoundingClientRect().left - headers[index].getBoundingClientRect().left) <= 1)};
                            })};
                    })()`);
                    assert.equal(tableLayout.codex, true, "report details must use a Codex table");
                    assert.ok(tableLayout.width >= tableLayout.viewport - 1, `report table does not fill its available width: ${JSON.stringify(tableLayout)}`);
                    assert.ok(tableLayout.rows.length > 1 && tableLayout.rows.every(row => row.fills && row.aligned && Math.abs(row.width - tableLayout.width) <= 1), `report rows do not span aligned table columns: ${JSON.stringify(tableLayout)}`);
                    if (width >= 1120) {
                        assert.ok(Math.abs(tableLayout.width - tableLayout.viewport) <= 1, `desktop report table does not match its available width: ${JSON.stringify(tableLayout)}`);
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
                    const checked = await evaluate(`(() => {
                        const panel = document.querySelector('#tool-status');
                        const link = document.querySelector('#routine-pageviews .routine-meta a[aria-haspopup=dialog]:has(time)');
                        const dialog = link && document.getElementById(link.getAttribute('aria-controls'));
                        if (!link || !dialog) return {available: false};
                        const historyLink = panel.querySelector('.logs-table a[aria-controls="' + dialog.id + '"]');
                        const metadata = link.closest('li');
                        const following = metadata.nextElementSibling;
                        const checkedAt = link.querySelector('time').getAttribute('datetime');
                        const stageTimes = [...dialog.querySelectorAll('time')].map(time => time.getAttribute('datetime'));
                        link.focus(); link.click();
                        return {available: true, id: dialog.id, sameRun: Boolean(historyLink),
                            atomic: panel.contains(dialog) && panel.contains(document.querySelector('#recent-events')),
                            timestamp: stageTimes.includes(checkedAt), observedLabel: metadata.textContent.trim(), nextLabel: following.textContent.trim(),
                            opened: dialog.open && dialog.contains(document.activeElement)};
                    })()`);
                    assert.equal(checked.available, true, 'fixture needs a check timestamp linked to saved execution history');
                    assert.equal(checked.id, 'job-history-' + '0'.repeat(32), 'check timestamp did not target its exact saved execution');
                    assert.ok(checked.sameRun && checked.atomic && checked.timestamp, 'card check time and history must share the same live execution record');
                    assert.equal(checked.opened, true, 'check timestamp did not open and focus its execution dialog');
                    const labels = {en: ['Checked at ', 'Next check '], 'zh-hans': ['检查时间', '下次检查'], 'zh-hant': ['檢查時間', '下次檢查']};
                    assert.ok(checked.observedLabel.startsWith(labels[locale][0]) && checked.nextLabel.startsWith(labels[locale][1]), 'status metadata must describe check times');
                    await press('Escape', 27);
                    assert.equal(await evaluate("new Promise(resolve => requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.matches('.routine-meta a:has(time)'))))"), true, 'closing execution history must restore focus to the check timestamp');
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
                        await evaluate("document.querySelector('.cdx-table-pager').scrollIntoView({block: 'center'})");
                        const pager = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-maintenance-pager-${width}.png`), Buffer.from(pager.data, "base64"));
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
        let asynchronousMaintenance = 0;
        for (const [locale, width] of [['en', 1440], ['zh-hant', 320]]) {
            const label = `${locale}/maintenance/${width}/asynchronous`;
            try {
                await call('Emulation.setDeviceMetricsOverride', {width, height: 936, deviceScaleFactor: 1, mobile: false}, sessionId);
                const loaded = wait('Page.loadEventFired', sessionId);
                await Promise.all([call('Page.navigate', {url: `${base}/maintenance/?task=references&sort=oldest&order=asc&lang=${locale}`}, sessionId), loaded]);
                const behavior = await evaluate(`(async () => {
                    const originalFetch = window.fetch;
                    const source = await (await originalFetch(location.href)).text();
                    const page = document.documentElement;
                    const outside = document.querySelector('.footer-uptime');
                    const outsideValue = outside.textContent;
                    const menu = document.querySelector('.maintenance-menu');
                    const originalPushState = history.pushState;
                    let pushes = 0;
                    history.pushState = function(...args) { pushes++; return originalPushState.apply(this, args); };
                    const requests = [];
                    const snapshots = [];
                    let delayed;
                    let delayNext = false;
                    let sequence = 0;
                    const responseFor = (value, marker) => {
                        const url = new URL(value, location.href);
                        const state = Object.fromEntries(url.searchParams);
                        const response = new DOMParser().parseFromString(source, 'text/html');
                        const root = response.querySelector('#maintenance-browser');
                        const table = root.querySelector('.maintenance-worklist');
                        table.dataset.testResponse = marker;
                        table.querySelector('.cdx-table__header__caption').textContent = 'Category ' + state.task;
                        table.querySelector('caption').textContent = 'Category ' + state.task;
                        table.querySelector('tbody th a').textContent = 'Fetched row ' + marker;
                        for (const form of root.querySelectorAll('form')) {
                            const sort = form.querySelector('.cdx-table__table__sort-button');
                            for (const input of [...form.querySelectorAll('input[type=hidden]')]) {
                                if (!sort || input.name !== 'sort') input.remove();
                            }
                            for (const [name, value] of Object.entries(state)) {
                                if (name === 'page' || sort && ['sort', 'order'].includes(name) || form.querySelector('[name="' + name + '"]')) continue;
                                const input = response.createElement('input');
                                input.type = 'hidden'; input.name = name; input.value = value; form.append(input);
                            }
                            if (sort) {
                                const column = form.querySelector('input[name=sort]').value;
                                const current = column === state.sort;
                                sort.value = current ? (state.order === 'asc' ? 'desc' : 'asc') : ['class', 'importance'].includes(column) ? 'desc' : 'asc';
                                form.closest('th').setAttribute('aria-sort', current ? (state.order === 'asc' ? 'ascending' : 'descending') : 'none');
                            }
                        }
                        root.querySelector('#maintenance-query').setAttribute('value', state.q || '');
                        for (const option of root.querySelectorAll('#maintenance-limit option')) option.toggleAttribute('selected', option.value === (state.limit || '10'));
                        for (const link of root.querySelectorAll('.maintenance-menu a')) {
                            const target = new URL(link.href, location.href).searchParams.get('task');
                            const destination = new URL(url); destination.searchParams.set('task', target); destination.searchParams.set('page', '1');
                            link.setAttribute('href', destination.href);
                            if (target === state.task) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
                        }
                        for (const link of root.querySelectorAll('.cdx-table-pager a')) {
                            const destination = new URL(url);
                            destination.searchParams.set('page', link.dataset.page === 'next' ? String(Number(state.page || 1) + 1) : '11');
                            link.setAttribute('href', destination.href);
                        }
                        root.querySelector('.maintenance-source').dataset.testResponse = marker;
                        return new Response(response.documentElement.outerHTML, {headers: {'Content-Type': 'text/html; charset=utf-8'}});
                    };
                    window.fetch = (url, options) => {
                        const request = {url: String(url), method: options?.method || 'GET', signal: options?.signal, marker: String(++sequence)};
                        requests.push(request);
                        if (delayNext) {
                            delayNext = false;
                            return new Promise(resolve => { delayed = () => resolve(responseFor(url, request.marker)); });
                        }
                        return Promise.resolve(responseFor(url, request.marker));
                    };
                    const settle = async marker => {
                        const deadline = performance.now() + 5000;
                        while (document.querySelector('.maintenance-worklist')?.dataset.testResponse !== marker
                            || document.querySelector('.maintenance-worklist').hasAttribute('aria-busy')) {
                            if (performance.now() > deadline) throw new Error('Maintenance async response did not settle: ' + marker);
                            await new Promise(resolve => requestAnimationFrame(resolve));
                        }
                    };
                    const act = async (name, action) => {
                        const next = String(sequence + 1); action(); await settle(next);
                        snapshots.push({name, url: location.href, focus: document.activeElement.id || document.activeElement.dataset.page
                            || (document.activeElement.matches('.maintenance-menu summary') ? 'menu' : document.activeElement.closest('form')?.querySelector('input[name=sort]')?.value),
                            source: document.querySelector('.maintenance-source').dataset.testResponse === next});
                    };
                    try {
                        await act('sort', () => { const button = [...document.querySelectorAll('.cdx-table__table__sort-button')].find(button => button.form.querySelector('input[name=sort]').value === 'class'); button.focus(); button.click(); });
                        const sortedURL = location.href;
                        await act('task', () => { menu.open = true; const link = menu.querySelector('a:not([aria-current])'); link.focus(); link.click(); });
                        const taskURL = location.href;
                        await act('page', () => { const link = document.querySelector('[data-page=next]'); link.focus(); link.click(); });
                        await act('search', () => { const input = document.querySelector('#maintenance-query'); input.focus(); input.value = 'Game <&>'; input.form.requestSubmit(); });
                        await act('size', () => { const select = document.querySelector('#maintenance-limit'); select.focus(); select.value = '20'; select.dispatchEvent(new Event('change', {bubbles: true})); });
                        const historyAfterActions = pushes;
                        await act('back', () => history.back());
                        await act('forward', () => history.forward());
                        const historyAfterTraversal = pushes;
                        delayNext = true;
                        const slow = [...document.querySelectorAll('.cdx-table__table__sort-button')].find(button => button.form.querySelector('input[name=sort]').value === 'title');
                        slow.focus(); slow.click();
                        const stale = requests.at(-1);
                        await act('race', () => { const button = [...document.querySelectorAll('.cdx-table__table__sort-button')].find(button => button.form.querySelector('input[name=sort]').value === 'importance'); button.focus(); button.click(); });
                        const latest = document.querySelector('.maintenance-worklist');
                        delayed(); await new Promise(resolve => setTimeout(resolve, 50));
                        return {snapshots, sameDocument: document.documentElement === page, outsidePreserved: document.querySelector('.footer-uptime') === outside && outside.textContent === outsideValue,
                            menuPreserved: document.querySelector('.maintenance-menu') === menu, caption: document.querySelector('.cdx-table__header__caption').textContent,
                            readOnly: requests.every(request => request.method === 'GET'), pushed: historyAfterActions,
                            historyStable: historyAfterTraversal === historyAfterActions, sortedURL, taskURL,
                            staleIgnored: document.querySelector('.maintenance-worklist') === latest && stale.signal.aborted,
                            controlsReady: document.querySelector('.page-size').hasAttribute('data-auto-submit') && !document.querySelector('button.cdx-button:not([data-codex-enhanced]):not(noscript button)'),
                            noErrors: window.aranamiStartupErrors.length === 0 && window.aranamiCspViolations.length === 0};
                    } finally { window.fetch = originalFetch; history.pushState = originalPushState; }
                })()`);
                assert.ok(behavior.sameDocument && behavior.outsidePreserved && behavior.menuPreserved, 'async maintenance navigation replaced the surrounding page');
                assert.ok(behavior.readOnly && behavior.controlsReady && behavior.noErrors, 'replacement maintenance controls did not initialize cleanly');
                assert.equal(behavior.pushed, 5, 'successful maintenance actions must each add one history entry');
                assert.ok(behavior.historyStable && behavior.staleIgnored, 'history traversal or a stale response overwrote the current list');
                assert.ok(behavior.snapshots.every(snapshot => snapshot.source), 'table and source metadata must update together');
                const states = Object.fromEntries(behavior.snapshots.map(snapshot => [snapshot.name, snapshot]));
                assert.equal(new URL(states.sort.url).searchParams.get('sort'), 'class');
                assert.equal(new URL(states.sort.url).searchParams.get('order'), 'desc');
                assert.equal(states.sort.focus, 'class');
                assert.equal(new URL(states.task.url).searchParams.get('task'), 'cleanup');
                assert.equal(states.task.focus, 'menu');
                assert.equal(behavior.caption, 'Category cleanup');
                assert.equal(new URL(states.page.url).searchParams.get('page'), '2');
                assert.equal(states.page.focus, 'next');
                assert.equal(new URL(states.search.url).searchParams.get('q'), 'Game <&>');
                assert.equal(states.search.focus, 'maintenance-query');
                assert.equal(new URL(states.size.url).searchParams.get('limit'), '20');
                assert.equal(states.size.focus, 'maintenance-limit');
                assert.equal(states.back.url, states.search.url);
                assert.equal(states.forward.url, states.size.url);
                assert.equal(new URL(states.race.url).searchParams.get('sort'), 'importance');
                asynchronousMaintenance++;
            } catch (error) {
                failures.push({label, error: error.message});
                console.error(`${label}: ${error.message}`);
            }
        }
        let maintenanceFallbacks = 0;
        for (const mode of ['http-error', 'empty', 'malformed']) {
            const label = 'en/maintenance/fallback/' + mode;
            try {
                const loaded = wait('Page.loadEventFired', sessionId);
                await Promise.all([call('Page.navigate', {url: `${base}/maintenance/?task=references&lang=en`}, sessionId), loaded]);
                await evaluate(`(() => {
                    window.maintenanceFallbackMarker = true;
                    window.fetch = async () => new Response(${JSON.stringify(mode === 'malformed' ? '<div id="maintenance-browser">invalid maintenance content</div>' : '')}, {
                        status: ${mode === 'http-error' ? 503 : 200}, headers: {'Content-Type': 'text/html'}
                    });
                })()`);
                const fallback = wait('Page.loadEventFired', sessionId);
                await evaluate("[...document.querySelectorAll('.cdx-table__table__sort-button')].find(button => button.form.querySelector('input[name=sort]').value === 'class').click()");
                await fallback;
                const native = await evaluate("({reloaded: !window.maintenanceFallbackMarker, sort: new URL(location.href).searchParams.get('sort'), available: Boolean(document.querySelector('#maintenance-browser .maintenance-table'))})");
                assert.ok(native.reloaded && native.available, 'failed async response must fall back to a usable native page');
                assert.equal(native.sort, 'class', 'native fallback lost the requested sort');
                maintenanceFallbacks++;
            } catch (error) {
                failures.push({label, error: error.message});
                console.error(`${label}: ${error.message}`);
            }
        }
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
                    controls: [...document.querySelectorAll('form input:not([type=hidden]), form select, form button')].filter(control => !control.closest('[hidden]')).every(control => (!control.disabled || control.closest('.cdx-table-pager__end')) && control.getBoundingClientRect().height > 0),
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
                if (surface === "home") await checkLanguageMenu(false);
                if (surface === "maintenance") {
                    await checkMaintenancePaginationStatus();
                    await checkMaintenanceControls(false);
                }
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
                    await checkNewsletterAccordion();
                    assert.equal(await evaluate("[...document.querySelectorAll('.newsletter-controls button[type=submit], .newsletter-date-controls button[type=submit]')].filter(button => button.getBoundingClientRect().height > 0).length"), 2, "issue and date forms need visible submit buttons without JavaScript");
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
        const result = { browser: browserVersion, inputCapabilities, stableInitializations, checked, asynchronousMaintenance, maintenanceFallbacks, withoutJavaScript, widths, locales, surfaces, failures };
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
