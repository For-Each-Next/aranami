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
const assetSourceRoot = path.resolve(__dirname, "../../src/webservice/public_html");
const interfaceMessages = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../src/webservice/messages.json"), "utf8"));
const chromium = process.env.ARANAMI_CHROMIUM || "/Applications/Chromium.app/Contents/MacOS/Chromium";
const widths = [320, 390, 600, 601, 639, 640, 641, 768, 943, 944, 1120, 1440, 1680, 1920];
const locales = ["en", "zh-hans", "zh-hant"];
const selectedSurfaces = process.env.ARANAMI_CHECK_SURFACES?.split(",");
const surfaces = ["home", "discussions", "quality-authorship", "quality-analysis", "backlog", "checks", "redirects", "maintenance", "consistency", "quarterly", "reports", "reports2", "logs", "dataset", "about", "contentcredits", "maintenancecredits", "codex", "gpl", "lgpl", "error", "newsletter"].filter(surface => !selectedSurfaces || selectedSurfaces.includes(surface));
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
        // Keep one fixed copy while source edits or other checks run.
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
        const attribution = {
            '/checks/': 'checks',
            '/discussions/': 'discussions',
            '/quality-analysis/': requestURL.searchParams.get('count_b') === '1' ? 'quality-analysis-b' : 'quality-analysis',
            '/quality-authorship/': requestURL.searchParams.get('count_b') === '1' ? 'quality-authorship-b' : 'quality-authorship',
            '/reports/': requestURL.searchParams.get('page') === '2' ? 'reports2' : 'reports',
            '/data/': requestURL.searchParams.get('page') === '2' ? 'dataset2' : 'dataset',
            '/redirects/': 'redirects',
            '/assets/icons/credits.html': 'contentcredits',
            '/assets/icons/maintenance/credits.html': 'maintenancecredits',
            '/codex-license.html': 'codex',
            '/assets/icons/gpl-2.0.html': 'gpl',
            '/assets/icons/lgpl-2.1.html': 'lgpl',
        }[requestURL.pathname];
        const pathname = attribution ? `/${locale}/${attribution}.html` : requestURL.pathname === '/maintenance/' ? `/${locale}/maintenance.html` : requestURL.pathname;
        const filename = ((pathname.endsWith(".js") || pathname.endsWith(".css")) || pathname.startsWith("/assets/"))
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
                const accordion = document.querySelector('details.ui-accordion');
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
                        const style = getComputedStyle(document.querySelector('details.ui-accordion > summary'), '::before');
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
            if (await evaluate("Boolean(document.querySelector('details.ui-accordion'))")) await checkAccordionChevron();
            const document = await evaluate(`(() => ({
                main: document.querySelectorAll('main#main').length,
                titles: document.querySelectorAll('h1').length,
                mainTitle: Boolean(document.querySelector('main h1')),
                skipTarget: document.querySelector('a.skip')?.getAttribute('href'),
                unnamedNavigation: [...document.querySelectorAll('nav')].filter(nav => !nav.getAttribute('aria-label') && !nav.getAttribute('aria-labelledby')).length,
                brokenContents: [...document.querySelectorAll('.page-toc a[href^="#"]')].filter(link => !document.getElementById(link.hash.slice(1))).map(link => link.hash),
                unlabelledControls: [...document.querySelectorAll('input:not([type=hidden]), select')].filter(control => !control.labels?.length && !control.getAttribute('aria-label') && !control.getAttribute('aria-labelledby')).map(control => control.outerHTML),
                forms: [...document.forms].map(form => ({method: form.method, action: form.getAttribute('action'), submit: Boolean(form.querySelector('button[type=submit], input[type=submit]')), issueSelection: form.hasAttribute('data-newsletter-issue-form'), dateSelection: form.matches('.newsletter-date-controls'), pageSize: form.matches('.page-size[data-auto-submit]'), qualityFilter: form.hasAttribute('data-quality-authorship-filter') || form.hasAttribute('data-quality-analysis-filter') || form.hasAttribute('data-quality-matrix') || form.hasAttribute('data-authorship-basis')}))
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
                assert.ok(form.submit || form.issueSelection || form.dateSelection || form.pageSize || form.qualityFilter, "form needs a native submit control or enhanced selection navigation");
            }
        };
        const checkMaintenancePaginationStatus = async () => {
            const visible = await evaluate(`(() => [...document.querySelectorAll('.ui-table-pager__center > div')]
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
                const caption = document.querySelector('.ui-table__header__caption');
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
                const sorts = [...table.querySelectorAll('.ui-table__table__sort-button')].map(button => {
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
                const pager = document.querySelector('.ui-table-pager');
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
                    regions: ['start', 'center', 'end'].every(region => pager.querySelector('.ui-table-pager__' + region)),
                    sizes: [...limit.options].map(option => option.value),
                    size: limit.value,
                    sizeSubmission, automatic, submitVisible: Boolean(sizeButton?.getBoundingClientRect().height > 0),
                    status: pager.querySelector('.ui-table-pager__center').textContent.trim(),
                    controls: [...pager.querySelectorAll('.ui-table-pager__end [data-page]')].map(control => ({
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
                    searchInHeader: Boolean(search.closest('.ui-table__header')),
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
            assert.ok(controls.paged.regions && controls.paged.status, 'native pager needs size, status, and navigation regions');
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
            // An open popup can cover the heading as its number of links grows.
            // Move to a verified outside hit target before testing pointer exit.
            const leaveTools = async () => {
                const point = await evaluate(`(() => {
                    const points = [{x: 1, y: innerHeight - 2}, {x: innerWidth - 2, y: innerHeight - 2}];
                    return points.find(point => {
                        const target = document.elementFromPoint(point.x, point.y);
                        return target && !target.closest('${selector}') && !target.closest('a, button, input, select, summary');
                    });
                })()`);
                assert.ok(point, 'Tools exit needs an outside noninteractive hit target');
                await call('Input.dispatchMouseEvent', {type: 'mouseMoved', ...point}, sessionId);
                return point;
            };
            await evaluate("document.querySelector('h1').focus(); document.activeElement.blur()");
            await leaveTools();
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
            assert.ok(links.every(link => link.color === 'rgb(39, 52, 55)' && link.decoration === 'none'), 'header links must stay near-black without underlines');
            await leaveTools();
            assert.equal(await isOpen(), false, 'hover-only Tools stayed open after leaving');
            await evaluate("document.querySelector('.brand').focus()");
            await move(`${selector} summary`);
            await press('Escape', 27);
            assert.equal(await evaluate(`!document.querySelector('${selector}').open && document.activeElement === document.querySelector('.brand')`), true, 'Escape did not dismiss hover preview while preserving outside focus');
            await leaveTools();
            const summary = await move(`${selector} summary`);
            await click(summary);
            assert.equal(await isOpen(), true, 'clicking a hover-open Tools menu should keep it open');
            await leaveTools();
            assert.equal(await isOpen(), true, 'clicked Tools closed on pointer exit');
            await click(await move(`${selector} summary`));
            assert.equal(await isOpen(), false, 'second summary click did not close Tools');
            await leaveTools();
            await move(`${selector} summary`);
            await evaluate(`document.querySelector('${selector} summary').focus()`);
            await press('ArrowDown', 40);
            await leaveTools();
            assert.equal(await evaluate(`document.querySelector('${selector}').open && document.activeElement === document.querySelector('${selector} a')`), true, 'pointer exit hid keyboard-focused Tools links');
            await press('Escape', 27);
            assert.equal(await evaluate(`!document.querySelector('${selector}').open && document.activeElement === document.querySelector('${selector} summary')`), true, 'Escape did not dismiss Tools and restore summary focus');
            await press('Enter', 13);
            assert.equal(await isOpen(), true, 'keyboard activation did not open Tools');
            await press('Tab', 9);
            assert.equal(await evaluate(`document.activeElement === document.querySelector('${selector} a')`), true, 'Tab did not reach Tools links');
            assert.equal(await evaluate("getComputedStyle(document.activeElement).outlineStyle !== 'none'"), true, 'keyboard focus outline missing');
            const linkCount = await evaluate(`document.querySelectorAll('${selector} a').length`);
            for (let index = 1; index < linkCount; index++) {
                await press('Tab', 9);
                assert.equal(await evaluate(`document.querySelector('${selector}').open && document.activeElement === document.querySelectorAll('${selector} a')[${index}]`), true, 'Tab did not reach every Tools link');
            }
            await press('Tab', 9);
            assert.equal(await isOpen(), false, 'focus leaving Tools did not close it');
            await evaluate(`document.querySelector('${selector}').dispatchEvent(new PointerEvent('pointerenter', {pointerType: 'touch'}))`);
            assert.equal(await isOpen(), false, 'touch pointer unexpectedly opened Tools');
            await evaluate(`document.querySelector('${selector} summary').click()`);
            assert.equal(await isOpen(), true, 'native click no longer opens Tools');
            await click(await leaveTools());
            assert.equal(await isOpen(), false, 'outside click did not close Tools');
            await move('.language-menu summary');
            assert.equal(await evaluate("document.querySelector('.language-menu').open"), false, 'hover behavior leaked into language menu');
            await leaveTools();
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
                const selectors = '.newsletter-controls select, .newsletter-controls button, .newsletter-date-selection input, .newsletter-date-selection button, .newsletter-copy-controls .ui-button';
                const expected = 2 * parseFloat(getComputedStyle(document.documentElement).fontSize);
                return {expected, heights: [...document.querySelectorAll(selectors)].filter(control => control.getBoundingClientRect().height > 0).map(control => ({tag: control.tagName, height: control.getBoundingClientRect().height}))};
            })()`);
            assert.ok(controls.heights.length >= 4 && controls.heights.every(control => Math.abs(control.height - controls.expected) < 1), `newsletter controls must share the standard height: ${JSON.stringify(controls)}`);
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
        const initialControls = `(() => [...document.querySelectorAll('button, input:not([type=hidden]), select, summary, a.ui-button, .brand img, main h1, .newsletter-text')].filter(element => !element.closest('[data-redirect-controls], [data-execution-history], [data-quality-authorship-table], [data-quality-authorship-filter], [data-quality-analysis-filter], [data-quality-matrix], [data-authorship-basis], [data-quality-analysis]') && !element.matches('[data-redirect-hide], .heading-copy')).map(element => {
            const style = getComputedStyle(element);
            const box = element.getBoundingClientRect();
            return {tag: element.tagName, id: element.id, hidden: element.hidden,
                background: style.backgroundColor, color: style.color,
                border: style.borderColor, font: style.font,
                display: style.display, visibility: style.visibility,
                geometry: [box.x, box.y, box.width, box.height].map(value => Math.round(value * 100) / 100)};
        }))()`;
        const headingGeometry = `(() => [...document.querySelectorAll('main .page-heading h1, main h2[id]')].filter(heading => !heading.closest('dialog, .ui-card, .article-card')).map(heading => {
            const box = heading.getBoundingClientRect();
            const walker = document.createTreeWalker(heading, NodeFilter.SHOW_TEXT);
            let textLeft = null;
            let text;
            while ((text = walker.nextNode())) {
                const start = text.textContent.search(/\\S/);
                if (start < 0 || text.parentElement.closest('.heading-copy')) continue;
                const range = document.createRange();
                range.setStart(text, start);
                range.setEnd(text, start + 1);
                textLeft = Math.round(range.getBoundingClientRect().left * 100) / 100;
                break;
            }
            return {id: heading.id, geometry: [box.x, box.y, box.width, box.height].map(value => Math.round(value * 100) / 100), textLeft};
        }))()`;
        const assertHeadingAlignment = async () => {
            const headings = await evaluate(headingGeometry);
            assert.ok(headings.every(heading => heading.textLeft !== null && Math.abs(heading.textLeft - heading.geometry[0]) < 1), 'section text must remain at its original content left edge: ' + JSON.stringify(headings));
            const gutters = await evaluate(`(() => [...document.querySelectorAll('a.heading-copy')].map(button => {
                const heading = button.closest('h1, h2');
                const box = button.getBoundingClientRect();
                const title = heading.getBoundingClientRect();
                return {id: heading.id, left: box.left, right: box.right, titleLeft: title.left, width: box.width};
            }))()`);
            assert.ok(gutters.every(control => control.left >= -0.5 && control.right <= control.titleLeft + 0.5 && control.width >= 16), 'section-copy controls must fit the left margin without covering titles: ' + JSON.stringify(gutters));
        };
        const clickHeadingControl = async selector => {
            const target = await evaluate(`(() => {
                const control = document.querySelector(${JSON.stringify(selector)});
                control.focus();
                control.scrollIntoView({block: 'center'});
                const box = control.getBoundingClientRect();
                const x = box.left + box.width / 2, y = box.top + box.height / 2;
                const style = getComputedStyle(control);
                return {x, y, hittable: document.elementFromPoint(x, y) === control,
                    visible: style.opacity === '1', unselectable: style.userSelect === 'none',
                    native: control.tagName === 'A' && Boolean(control.getAttribute('href'))};
            })()`);
            assert.ok(target.hittable && target.visible && target.unselectable && target.native,
                'heading copy must remain pointer-accessible and excluded from text selection: ' + JSON.stringify(target));
            await call('Input.dispatchMouseEvent', {type: 'mouseMoved', x: target.x, y: target.y}, sessionId);
            await call('Input.dispatchMouseEvent', {type: 'mousePressed', x: target.x, y: target.y, button: 'left', clickCount: 1}, sessionId);
            await call('Input.dispatchMouseEvent', {type: 'mouseReleased', x: target.x, y: target.y, button: 'left', clickCount: 1}, sessionId);
            await evaluate('new Promise(resolve => requestAnimationFrame(resolve))');
        };
        const checkHeadingCopy = async (selector, fragment) => {
            const setup = await evaluate(`(async () => {
                const original = location.href;
                history.replaceState(null, '', '/about/?view=recent&lang=' + document.documentElement.lang);
                const heading = document.querySelector(${JSON.stringify(selector)});
                const label = heading.getAttribute('aria-label');
                heading.textContent = label;
                await new Promise(resolve => requestAnimationFrame(resolve));
                window.headingCopyValues = [];
                Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async value => window.headingCopyValues.push(value)}});
                return {original, expected: '[[toolforge:aranami/about/?view=recent' + ${JSON.stringify(fragment ? `#${fragment}` : "")} + '|' + label + ']]'};
            })()`);
            const control = selector + ' > a.heading-copy';
            await clickHeadingControl(control);
            const copied = await evaluate(`(() => {
                const control = document.querySelector(${JSON.stringify(control)});
                const before = window.headingCopyValues.length;
                let nativeModifiedClick = false;
                document.addEventListener('click', event => {
                    nativeModifiedClick = !event.defaultPrevented;
                    event.preventDefault();
                }, {once: true});
                control.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, ctrlKey: true}));
                return {values: [...window.headingCopyValues], busy: control.hasAttribute('aria-busy'),
                    nativeModifiedClick: nativeModifiedClick && window.headingCopyValues.length === before,
                    nativeHref: control.getAttribute('href')};
            })()`);
            assert.deepEqual(copied.values, [setup.expected], 'heading control copied a localized or unsafe target');
            assert.ok(!copied.busy && copied.nativeModifiedClick, 'copying must keep native modified-click behavior and release busy state');
            assert.equal(copied.nativeHref, fragment ? '#' + fragment : '/about/?view=recent');
            await evaluate(`Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async () => { throw new Error('Denied'); }}})`);
            await clickHeadingControl(control);
            const fallback = await evaluate(`(() => {
                const dialog = document.querySelector('.heading-copy-dialog');
                const input = dialog.querySelector('input');
                return {open: dialog.open, selected: input.readOnly && input.value === ${JSON.stringify(setup.expected)} && input.selectionStart === 0 && input.selectionEnd === input.value.length,
                    nativeHref: dialog.querySelector('a').getAttribute('href'),
                    noCardControls: !document.querySelector('dialog h2 .heading-copy, .ui-card .heading-copy')};
            })()`);
            assert.ok(fallback.open && fallback.selected && fallback.noCardControls, 'heading manual fallback must retain selected source and omit dialog/card controls');
            assert.equal(fallback.nativeHref, copied.nativeHref);
            await press('Escape', 27);
            await evaluate('new Promise(resolve => requestAnimationFrame(resolve))');
            assert.equal(await evaluate(`document.activeElement === document.querySelector(${JSON.stringify(control)})`), true, 'manual copy dialog must restore focus to its native link');
            await evaluate(`history.replaceState(null, '', ${JSON.stringify(setup.original)})`);
        };
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
                const beforeHeadings = await evaluate(headingGeometry);
                assert.ok(beforeHeadings.every(heading => heading.textLeft !== null && Math.abs(heading.textLeft - heading.geometry[0]) < 1), 'native headings must remain aligned with their content before JavaScript');
                const before = await evaluate(initialControls);
                const scripts = pendingStartupScripts;
                pendingStartupScripts = undefined;
                for (const send of scripts) send();
                await loaded;
                const after = await evaluate(initialControls);
                assert.deepEqual(after, before, "initialization changed control visibility, colors, typography, or layout");
                const afterHeadings = await evaluate(headingGeometry);
                // Article pagination above the author section changes its page
                // position; heading-copy enhancement must keep its own geometry.
                const stableHeadings = headings => headings.map(heading => {
                    if (surface !== 'quality-analysis' || heading.id !== 'quality-authors') return heading;
                    const [x, , width, height] = heading.geometry;
                    return {...heading, geometry: [x, 0, width, height]};
                });
                assert.deepEqual(stableHeadings(afterHeadings), stableHeadings(beforeHeadings), 'section-copy controls changed heading layout');
                await assertHeadingAlignment();
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
                await assertHeadingAlignment();
                const dialogShells = await evaluate(`(() => [...document.querySelectorAll('dialog.page-updates')].map(dialog => ({
                    id: dialog.id,
                    codex: dialog.classList.contains('ui-dialog')
                        && Boolean(dialog.querySelector(':scope > .ui-dialog__header .ui-dialog__header__title'))
                        && Boolean(dialog.querySelector(':scope > .ui-dialog__body')),
                    hidden: !dialog.open && getComputedStyle(dialog).display === 'none'
                        && dialog.getBoundingClientRect().width === 0,
                })))()`);
                assert.ok(dialogShells.every(dialog => dialog.codex), `popup is missing its native dialog shell: ${JSON.stringify(dialogShells)}`);
                assert.ok(dialogShells.every(dialog => dialog.hidden), `closed popup is visible: ${JSON.stringify(dialogShells)}`);
                assert.equal(await evaluate("document.querySelectorAll('[data-v-app], .codex-control, script[src*=codex]').length"), 0, "native pages must not mount a browser framework");
                await checkContents();
                const layout = await evaluate(`(() => {
                    const visible = element => { const box = element.getBoundingClientRect(); return box.width > 0 && box.height > 0 && getComputedStyle(element).display !== 'none'; };
                    return { viewport: innerWidth, page: document.documentElement.scrollWidth,
                        archives: [...document.querySelectorAll('.log-download a.filename')].map(link => ({
                            visible: visible(link),
                            inline: !link.hasAttribute('download') && !new URL(link.href).searchParams.has('download')
                                && new URL(link.href).searchParams.has('file'),
                        })),
                        columns: [...document.querySelectorAll('table.log-files, table.logs-table')].map(table => ({ headers: table.querySelectorAll('thead th').length, cells: table.querySelector('tbody tr:not([hidden])')?.children.length })),
                        overflow: [...document.querySelectorAll('main *')].filter(element => visible(element) && element.getBoundingClientRect().right > innerWidth + 1).slice(0, 8).map(element => ({ tag: element.tagName, class: element.className, right: element.getBoundingClientRect().right })) };
                })()`);
                assert.ok(layout.page <= layout.viewport + 1, JSON.stringify(layout));
                if (surface === "redirects") {
                    const evidence = await evaluate(`(() => {
                        const rows = [...document.querySelectorAll('[data-redirect-id]')];
                        const row = rows[0];
                        const button = row.querySelector('[data-redirect-hide]');
                        const before = !row.hidden;
                        button.click();
                        const hidden = row.hidden;
                        const stored = JSON.parse(localStorage.getItem('aranami-redirects-hidden-v1'));
                        document.querySelector('#redirect-show-hidden').click();
                        const shown = !row.hidden;
                        button.click();
                        const restored = !row.hidden && !JSON.parse(localStorage.getItem('aranami-redirects-hidden-v1')).includes(row.dataset.redirectId);
                        document.querySelector('#redirect-show-hidden').click();
                        return {count: rows.length, before, hidden, stored: stored.includes(row.dataset.redirectId), shown, restored,
                            evidence: row.querySelectorAll('.redirect-evidence a[href*="oldid="]').length,
                            next: Boolean(document.querySelector('.redirect-pagination a[rel=next]'))};
                    })()`);
                    assert.equal(evidence.count, 10, 'redirect worklist must paginate saved findings');
                    assert.ok(evidence.before && evidence.hidden && evidence.stored && evidence.shown && evidence.restored, 'personal redirect hiding must be reversible and retained');
                    assert.equal(evidence.evidence, 3, 'redirect findings need current, article-content and banner revisions');
                    assert.equal(evidence.next, true, 'redirect pagination must have a native next link');
                }
                if (surface === "consistency" || surface === "quarterly") {
                    const evidence = await evaluate(`(() => ({
                        tables: document.querySelectorAll('.maintenance-table').length,
                        findings: document.querySelectorAll('.consistency-findings article.consistency-card').length,
                        talk: document.querySelectorAll('.consistency-findings .consistency-talk a').length,
                        sources: document.querySelectorAll('.consistency-findings a[href*="oldid="]').length,
                        dated: [...document.querySelectorAll('.consistency-findings time')].every(time => Boolean(time.dateTime)),
                        native: [...document.querySelectorAll('.consistency-worklist form')].every(form => form.method === 'get'),
                        container: document.querySelector('.consistency-findings').tagName,
                        direct: [...document.querySelector('.consistency-findings').children].every(card => card.matches('article.consistency-card') && card.getAttribute('aria-labelledby') === card.querySelector('h3').id),
                        listTags: document.querySelectorAll('.consistency-history-cards ul, .consistency-history-cards ol, .consistency-history-cards li').length,
                        ordinals: [...document.querySelectorAll('.consistency-ordinal')].map(ordinal => ordinal.textContent),
                    }))()`);
                    assert.equal(evidence.tables, 0, 'consistency is independent from maintenance tables');
                    assert.equal(evidence.container, surface === 'consistency' ? 'DIV' : 'UL', 'homepage findings use direct article cards');
                    assert.equal(evidence.findings, surface === "quarterly" ? 1 : 10, 'every saved finding is shown');
                    assert.equal(evidence.talk, surface === "quarterly" ? 1 : 10, 'each article exposes its talk page');
                    assert.equal(evidence.sources, surface === "quarterly" ? 2 : 20, 'each finding exposes both exact evidence revisions');
                    assert.ok(evidence.dated && evidence.native, 'consistency evidence lacks accessible dates or native controls');
                    if (surface === "consistency") {
                        const handoff = await evaluate(`(() => {
                            const install = document.querySelector('#consistency-userscript a');
                            const actions = [...document.querySelectorAll('.consistency-review-action')];
                            const target = new URL(actions[0].href);
                            return {install: new URL(install.href).pathname,
                                actions: actions.length, origin: target.origin, path: target.pathname,
                                title: decodeURIComponent(target.pathname.slice('/wiki/'.length)).replaceAll('_', ' '), action: target.searchParams.get('action'),
                                section: target.searchParams.get('section'), id: /^[1-9][0-9]{0,18}$/.test(target.searchParams.get('aranami-check-job'))
                                    && Number.isSafeInteger(Number(target.searchParams.get('aranami-check-job'))),
                                fields: [...target.searchParams.keys()].sort(),
                                inactive: !document.getElementById('aranami-repair-status')};
                        })()`);
                        assert.deepEqual(handoff, {install: '/consistency-repair.user.js', actions: 1,
                            origin: 'https://zh.wikipedia.org', path: '/wiki/Talk:Game_01', title: 'Talk:Game 01', action: 'edit', section: '0', id: true,
                            fields: ['action', 'aranami-check-job', 'section'], inactive: true},
                        'ready repairs link the current talk lead and install script without caller code or automatic page edits');
                        const adjacent = await evaluate(`(() => {
                            const action = document.querySelector('.consistency-review-action');
                            const group = action.parentElement;
                            const copy = action.previousElementSibling;
                            const bounds = group.getBoundingClientRect();
                            const target = action.getBoundingClientRect();
                            const button = copy.getBoundingClientRect();
                            return {shared: group.matches('.consistency-copy-controls') && copy.matches('[data-copy-target]') && action.nextElementSibling.matches('[data-copy-status]'),
                                contained: target.left >= bounds.left - 1 && target.right <= bounds.right + 1 && target.bottom <= bounds.bottom + 1,
                                desktopRow: innerWidth <= 640 || Math.abs(target.top - button.top) < 1,
                                label: action.textContent};
                        })()`);
                        assert.deepEqual(adjacent, {shared: true, contained: true, desktopRow: true, label: interfaceMessages['consistency.prefill'][locale]},
                            'auto-fill sits beside Copy before its status, with contained mobile wrapping');
                        const userscriptSource = fs.readFileSync(path.join(assetRoot, 'consistency-repair.user.js'), 'utf8');
                        const nativeDiff = await evaluate(`(async () => {
                            const form = document.createElement('form');
                            form.id = 'editform';
                            form.hidden = true;
                            form.action = 'https://zh.wikipedia.org/w/index.php?title=Talk%3AGame%2001&action=submit';
                            form.innerHTML = '<textarea id="wpTextbox1" name="wpTextbox1"></textarea><input id="wpSummary" name="wpSummary"><input name="wpSection" value="0"><input name="editRevId" value="201"><input name="wpEdittime" value="20261102140000"><button id="wpDiff" name="wpDiff">Diff</button><button id="wpPreview" name="wpPreview">Preview</button><button id="wpSave" name="wpSave">Save</button>';
                            document.body.append(form);
                            const original = '<!-- keep -->\\n{{Article history|currentstatus=GA}}\\n \\t\\n\\n';
                            const fixed = original.replace('|currentstatus=GA', '|currentstatus=GA|maindateGA=2026-10-30');
                            const summary = 'Update recorded homepage appearances.';
                            // MediaWiki strips section-boundary whitespace before filling the native textarea.
                            form.querySelector('#wpTextbox1').value = original.trimEnd();
                            form.querySelector('#wpSummary').value = '/* */ ';
                            const id = new URL(document.querySelector('.consistency-review-action').href).searchParams.get('aranami-check-job');
                            const config = {wgNamespaceNumber: 1, wgPageName: 'Talk:Game_01', wgCurRevisionId: 201, wgArticleId: 101, wgUserLanguage: document.documentElement.lang};
                            const requests = [];
                            let clicks = 0;
                            let previews = 0;
                            let saves = 0;
                            let cleared = false;
                            form.addEventListener('submit', event => event.preventDefault());
                            form.querySelector('#wpDiff').addEventListener('click', event => {event.preventDefault(); clicks++;});
                            form.querySelector('#wpPreview').addEventListener('click', event => {event.preventDefault(); previews++;});
                            form.querySelector('#wpSave').addEventListener('click', event => {event.preventDefault(); saves++;});
                            const handoffURL = new URL('https://zh.wikipedia.org/wiki/Talk:Game_01?action=edit&section=0&aranami-check-job=' + id);
                            new Function('location', 'history', 'unsafeWindow', 'GM_xmlhttpRequest', ${JSON.stringify(userscriptSource)})(handoffURL,
                                {state: null, replaceState: (_state, _title, url) => {cleared = !new URL(url).searchParams.has('aranami-check-job');}},
                                {mw: {config: {get: key => config[key]}, loader: {using: async () => {}}}},
                                request => {requests.push(request.url); request.onload({status: 200, response: {schema: 1, repair: {
                                    id, title: 'Talk:Game 01', revision_id: 201, talk_page_id: 101, timestamp: '2026-11-02T14:00:00Z', original_text: original, text: fixed, edit_summary: summary,
                                }}});});
                            await new Promise(resolve => setTimeout(resolve, 20));
                            const result = {clicks, previews, saves, requests: requests.length, source: form.querySelector('#wpTextbox1').value === fixed,
                                summary: form.querySelector('#wpSummary').value === summary, section: form.elements.wpSection.value,
                                revision: form.elements.editRevId.value, cleared};
                            form.remove();
                            delete document.documentElement.dataset.aranamiRepair;
                            return result;
                        })()`);
                        assert.deepEqual(nativeDiff, {clicks: 1, previews: 0, saves: 0, requests: 1, source: true, summary: true, section: '0', revision: '201', cleared: true},
                            'the userscript fills real native source controls and clicks the diff while preserving section and conflict guards');
                        assert.ok(evidence.direct, 'homepage cards retain their labeled article semantics');
                        assert.equal(evidence.listTags, 0, 'homepage cards have no list wrappers');
                        assert.deepEqual(evidence.ordinals, Array.from({length: 10}, (_, index) => `${index + 1}.`), 'homepage card headings retain ordered numbering');
                        const copied = await evaluate(`(async () => {
                            let value;
                            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => {value = text;}}});
                            const button = document.querySelector('[data-copy-target]');
                            const source = document.getElementById(button.dataset.copyTarget);
                            button.click();
                            await Promise.resolve();
                            return {literal: value === source.value, expected: source.value === ' | maindateGA = 2026-10-30', ready: !button.disabled, readonly: source.readOnly};
                        })()`);
                        assert.ok(copied.literal && copied.expected && copied.ready && copied.readonly, 'homepage parameter snippets copy literal source');
                    }
                }
                if (surface === "about") {
                    const credits = await evaluate(`(() => { const list = document.querySelector('.about-credits'); return {tag: list.tagName, items: list.querySelectorAll(':scope > li').length, cards: list.querySelectorAll('.ui-card').length}; })()`);
                    assert.deepEqual(credits, {tag: 'UL', items: 6, cards: 0}, 'the credits overview is an ordinary six-item list');
                    if ([320, 1440].includes(width)) {
                        await checkHeadingCopy('#server-status-heading', 'server-status-heading');
                        await checkHeadingCopy('main .page-heading h1', '');
                    }

                    const work = await evaluate(`(() => {
                        const row = document.querySelector('#server-work');
                        return {live: Boolean(row.closest('#live-server-status')), count: row.querySelectorAll('li').length,
                            expires: Boolean(row.querySelector('[data-observation-expires]')), text: row.textContent};
                    })()`);
                    assert.ok(work.live && work.expires, 'current work must share live status and expire with its owner');
                    assert.equal(work.count, 2, 'current work must show concurrent website and database collection');
                    assert.ok(work.text.includes('CurrentWebsiteSourceWithLongUnbrokenIdentifier') && work.text.includes('3') && work.text.includes('12'), 'current work must retain source and measured progress');
                    if ([320, 1440].includes(width)) {
                        await evaluate("document.querySelector('#server-work').scrollIntoView({block: 'center'})");
                        const shot = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false}, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-server-work-${width}.png`), Buffer.from(shot.data, 'base64'));
                        await evaluate('scrollTo(0, 0)');
                    }

                }
                if (surface === "contentcredits") {
                    const medals = await evaluate(`[...document.querySelectorAll('.attribution-card a[href^="/assets/icons/medal-"]')].map(link => link.textContent)`);
                    assert.deepEqual(medals, Array.from({length: 6}, (_, index) => 'Rank ' + (index + 1) + ' medal'));
                }
                if (surface === "contentcredits" || surface === "maintenancecredits") {
                    const attribution = await evaluate(`(() => ({
                        cards: document.querySelectorAll('.attribution-card').length,
                        thumbnails: document.querySelectorAll('.attribution-thumbnail').length,
                        loaded: [...document.querySelectorAll('.attribution-thumbnail img')].every(img => img.complete && img.naturalWidth > 0),
                        authors: document.querySelectorAll('.attribution-details dt').length,
                        categories: document.querySelectorAll('#template-correspondence + .table-scroll a[href*="Category:"]').length,
                    }))()`);
                    const expected = surface === "maintenancecredits" ? 23 : 13;
                    assert.equal(attribution.cards, expected);
                    assert.equal(attribution.thumbnails, expected);
                    assert.equal(attribution.authors, expected * 3);
                    assert.ok(attribution.loaded, 'credit thumbnails must load');
                    if (surface === "maintenancecredits") assert.ok(attribution.categories >= 30, 'template correspondence must link configured categories');
                    if ([320, 1440].includes(width)) {
                        for (const id of surface === "maintenancecredits" ? ['attribution-images', 'template-correspondence'] : ['attribution-images']) {
                            await evaluate(`document.getElementById(${JSON.stringify(id)}).scrollIntoView()`);
                            const shot = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false}, sessionId);
                            fs.writeFileSync(path.join(outputRoot, `${locale}-${surface}-${id}-${width}.png`), Buffer.from(shot.data, 'base64'));
                        }
                        await evaluate('window.scrollTo(0, 0)');
                    }
                }
                if (surface === "quarterly") {
                    const metrics = await evaluate(`(() => ({
                        count: document.querySelectorAll('.consistency-metrics dd').length,
                        unknown: document.querySelectorAll('.consistency-metrics .consistency-unknown').length,
                        zero: [...document.querySelectorAll('.consistency-metrics dd')].some(value => value.textContent === '0'),
                    }))()`);
                    assert.equal(metrics.count, 12, 'quarterly suggestions expose every expected metric');
                    assert.equal(metrics.unknown, 1, 'unknown evidence stays explicitly unknown');
                    assert.ok(metrics.zero, 'observed zero must remain a numeric zero');
                }
                if (surface === "maintenance") {
                    await checkMaintenancePaginationStatus();
                    const table = await evaluate(`(() => {
                        const table = document.querySelector('.maintenance-table');
                        const scroll = table.closest('.table-scroll');
                        const headers = [...table.querySelectorAll('thead th')];
                        const cells = [...table.querySelector('tbody tr:not([hidden])').children];
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
                    assert.ok(layout.archives.length >= 1, "segment reading links missing");
                    assert.ok(layout.archives.every(link => link.visible && link.inline), "archive filenames must open inline without forced downloads");
                    const caption = await evaluate(`(() => {
                        const caption = document.querySelector('section[aria-labelledby="log-archive"] caption');
                        const style = getComputedStyle(caption);
                        return {hidden: caption.classList.contains('visually-hidden') && style.position === 'absolute' && style.clipPath !== 'none',
                            headings: document.querySelectorAll('h2#log-archive').length};
                    })()`);
                    assert.ok(caption.hidden && caption.headings === 1, "archive needs one visible heading and an accessible hidden caption");
                    for (const columns of layout.columns) assert.equal(columns.headers, columns.cells);
                    if (width < 600) assert.ok(await evaluate("[...document.querySelectorAll('.logs-table')].every(table => table.getBoundingClientRect().width >= 600)"), "narrow log tables must keep readable columns inside their scroll region");
                }
                if (surface === "discussions") {
                    const groups = await evaluate(`(() => [...document.querySelectorAll('section[id^="discussion-"]')].map(section => ({
                        id: section.id,
                        title: section.querySelector('h2')?.textContent.trim(),
                        subjects: [...section.querySelectorAll('.discussion-card .article-summary a')].map(link => link.textContent),
                        latest: [...section.querySelectorAll('.discussion-card')].map(card => [...card.querySelectorAll('time')].at(-1)?.dateTime),
                    })))()`);
                    assert.deepEqual(groups.map(group => group.id), [
                        'discussion-week-1', 'discussion-week-2', 'discussion-week-3', 'discussion-week-4',
                        'discussion-drafts', 'discussion-projects', 'discussion-categories-templates', 'discussion-other',
                    ], 'monthly discussions need the eight ordered groups');
                    assert.ok(groups.every(group => group.title && group.subjects.length), 'each synthetic group must display its heading and saved discussions');
                    for (const group of groups) {
                        const instants = group.latest.map(value => Date.parse(value));
                        assert.ok(instants.every((value, index) => Number.isFinite(value) && (!index || instants[index - 1] >= value)), 'each discussion group must order latest comments from new to old');
                    }
                    assert.deepEqual(groups[0].subjects, ['Newer stub article', 'Older featured article'], 'latest comment time must order article cards independently of assessment grade');
                    assert.deepEqual(groups[3].subjects, ['Fourth-week article'], 'the final article group must include the exact twenty-eight-day boundary');
                    assert.equal(await evaluate("document.querySelectorAll('.discussion-card .assessment-chips').length"), 0, 'discussion cards must omit assessment chips on the dedicated page');
                }
                if (surface === "home") {
                    const metadata = await evaluate(`(() => ({
                        reviewAges: [...document.querySelectorAll('#reviews .review-cards time, #discussions .review-cards time')].map(time => ({
                            text: time.textContent.trim(), value: time.dateTime, title: time.title,
                        })),
                        dates: [...document.querySelectorAll('.popularity-meta > .card-meta-lead')].map(lead => {
                            const time = lead.querySelector('time');
                            return {value: time.dateTime, title: time.title, text: time.textContent.trim(),
                                lead: lead.textContent.trim(), separator: getComputedStyle(lead, '::after').content};
                        }),
                        participants: [...document.querySelectorAll('.review-participants')].map(item => ({
                            mark: item.querySelector('[aria-hidden="true"]').textContent.trim(),
                            meaning: item.querySelector('.visually-hidden').textContent,
                            title: item.title,
                        })),
                    }))()`);
                    await call('Input.dispatchMouseEvent', {type: 'mouseMoved', x: 0, y: 0}, sessionId);
                    const discussion = await evaluate(`(() => {
                        const card = [...document.querySelectorAll('#discussions .discussion-card')].find(card => card.querySelector('.discussion-topic').textContent.includes('LongUnbrokenDiscussionTopicIdentifier'));
                        const subject = card.querySelector('.article-summary a');
                        const topic = card.querySelector('.discussion-topic a');
                        const summary = card.querySelector('.discussion-opening');
                        const reference = document.createElement('a');
                        reference.hidden = true;
                        document.body.append(reference);
                        const progressive = getComputedStyle(reference).color;
                        reference.remove();
                        const footer = card.querySelector('.metadata-items');
                        return {tag: card.tagName, cardLink: card.hasAttribute('href'),
                            subjectUrl: subject?.href, topicUrl: topic?.href,
                            linkCount: card.querySelectorAll('a').length,
                            nestedLinks: card.querySelectorAll('a a').length,
                            title: card.querySelector('h3').textContent,
                            topic: card.querySelector('.discussion-topic').textContent,
                            blueSubject: getComputedStyle(subject).color === progressive,
                            blueTopic: getComputedStyle(topic).color === progressive,
                            subjectBold: Number(getComputedStyle(subject).fontWeight) >= 600,
                            topicRegular: Number(getComputedStyle(topic).fontWeight) < 600,
                            summaryGap: summary.getBoundingClientRect().top - card.querySelector('.discussion-topic').getBoundingClientRect().bottom,
                            expectedGap: parseFloat(getComputedStyle(summary).fontSize) * 0.5,
                            assessments: card.querySelectorAll('[data-assessment]').length,
                            footer: footer.textContent,
                            size: footer.querySelector('.discussion-size').textContent,
                            order: footer.lastElementChild.className || footer.lastElementChild.firstElementChild.className};
                    })()`);
                    assert.equal(discussion.tag, 'ARTICLE', 'discussion must use the semantic article shell');
                    assert.ok(!discussion.cardLink && discussion.linkCount === 3 && discussion.nestedLinks === 0, 'discussion must contain separate subject, topic and newest-comment links');
                    assert.ok(discussion.subjectUrl.endsWith('/WikiProject:%E7%94%B5%E5%AD%90%E6%B8%B8%E6%88%8F'), 'discussion subject must link to its article');
                    assert.ok(discussion.topicUrl.endsWith('#Sources'), 'discussion topic must link to its exact talk section');
                    assert.equal(discussion.title, locale === 'zh-hant' ? 'WikiProject:電子遊戲' : 'WikiProject:电子游戏');
                    assert.ok(discussion.topic.includes('LongUnbrokenDiscussionTopicIdentifier'), 'discussion topic needs its own linked row');
                    assert.ok(discussion.blueSubject && discussion.blueTopic, 'discussion subject and topic must use the normal link color');
                    assert.ok(discussion.subjectBold && discussion.topicRegular, 'article subject remains bold and discussion topic uses regular link text');
                    assert.ok(Math.abs(discussion.summaryGap - discussion.expectedGap) < 1, 'discussion summary needs a half-em gap below its topic');
                    assert.equal(discussion.assessments, 0, 'homepage discussions omit assessment chips');
                    assert.equal(discussion.size, '35\u00a0kB', 'discussion size must use SI kilobytes and a nonbreaking space');
                    assert.equal(discussion.order, 'discussion-size', 'section size follows participants');
                    assert.ok(!/Review|评审区|評審區|讨论区|討論區/.test(discussion.footer), 'discussion footer contains only indicators');
                    const activity = await evaluate(`(() => [...document.querySelectorAll('#discussions .discussion-activity')].map(icon => ({
                        state: icon.dataset.discussionActivity, meaning: icon.getAttribute('aria-label'),
                        tooltip: icon.title, role: icon.getAttribute('role'),
                        symbol: icon.querySelector('use').getAttribute('href').split('#').at(-1),
                        width: icon.querySelector('use').getBBox().width,
                        hidden: icon.querySelector('svg').getAttribute('aria-hidden'),
                    })))()`);
                    assert.deepEqual(activity.map(icon => icon.state), ['recent', 'recent-hot', 'hot', 'ordinary'], 'discussion indicators follow latest activity within their page type');
                    for (const icon of activity) {
                        assert.equal(icon.symbol, icon.state === 'ordinary' ? 'speech-bubbles' : 'speech-bubbles-' + icon.state);
                        assert.ok(icon.width > 0, 'discussion activity SVG must paint its external symbol');
                        assert.ok(icon.meaning && icon.meaning === icon.tooltip, 'activity tooltip must expose the same accessible criteria');
                        assert.equal(icon.role, 'img');
                        assert.equal(icon.hidden, 'true');
                    }
                    const ageUnits = new Set();
                    const expectedAgeUnits = {
                        en: ['m', 'h', 'd', 'w', 'mo', 'yr'],
                        'zh-hans': ['分钟', '小时', '日', '周', '月', '年'],
                        'zh-hant': ['分鐘', '小時', '日', '週', '月', '年'],
                    }[locale];
                    const agePattern = {
                        en: /^\d+(mo|yr|m|h|d|w) ago$/,
                        'zh-hans': /^\d+(分钟|小时|日|周|月|年)前$/,
                        'zh-hant': /^\d+(分鐘|小時|日|週|月|年)前$/,
                    }[locale];
                    for (const age of metadata.reviewAges) {
                        const match = age.text.match(agePattern);
                        assert.ok(match, 'card ages must use English abbreviations or the selected Chinese units');
                        ageUnits.add(match[1]);
                        assert.match(age.value, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
                        assert.ok(age.title.includes(age.value.slice(0, 16).replace('T', ' ')), 'age tooltip must retain its absolute source time to the minute');
                    }
                    assert.deepEqual([...ageUnits].sort(), [...expectedAgeUnits].sort(), 'review fixtures must cover all six localized age units');
                    const soloReview = await evaluate(`(() => {
                        const card = document.querySelector('#reviews .article-card-body');
                        const times = [...card.querySelectorAll('time')];
                        return {times: times.map(time => time.dateTime),
                            latest: times.at(-1).title,
                            latestUrl: times.at(-1).closest('a').href,
                            participants: card.querySelector('.review-participants').textContent,
                            size: card.querySelector('.review-section-size').textContent,
                            last: card.querySelector('.metadata-items').lastElementChild.firstElementChild.className};
                    })()`);
                    assert.equal(soloReview.times.length, 2, 'one-comment reviews retain both start and latest indicators');
                    assert.equal(soloReview.times[0], soloReview.times[1], 'one observed comment can supply both timestamps');
                    assert.ok(soloReview.latest.includes('OpeningEditor') && soloReview.latest.includes(interfaceMessages['reviews.replied_action'][locale]), 'latest comment tooltip must identify the actual author and action');
                    assert.ok(soloReview.latestUrl.endsWith('#c-Solo'), 'latest comment needs its independently observed anchor');
                    assert.ok(soloReview.participants.includes('☺︎ 1'), 'solo review must retain its known participant count');
                    assert.equal(soloReview.size, '35\u00a0kB');
                    assert.equal(soloReview.last, 'review-section-size', 'ordinary review section size follows the other indicators');
                    const showcaseTypes = await evaluate("[...document.querySelectorAll('section[aria-labelledby=homepage-recognized] .homepage-type')].map(type => ({tag: type.tagName, text: type.textContent, title: type.closest('.article-card-body').querySelector('h3 a').textContent}))");
                    const actualTypeLabels = Object.entries(interfaceMessages).filter(([key]) => key.startsWith('homepage.type.')).map(([, labels]) => labels[locale]);
                    assert.ok(showcaseTypes.length && showcaseTypes.every(type => type.tag === 'SPAN' && (type.title === 'Scheduled game'
                        ? type.text === interfaceMessages['homepage.scheduled_type.tfa'][locale]
                        : actualTypeLabels.includes(type.text))), 'showcase types must distinguish upcoming generic labels from actual homepage appearances');
                    assert.equal(showcaseTypes.find(type => type.title === 'Scheduled game')?.text,
                        interfaceMessages['homepage.scheduled_type.tfa'][locale], 'future featured content must use its generic localized type');
                    assert.equal(showcaseTypes.find(type => type.title === 'Good game')?.text,
                        interfaceMessages['homepage.type.tga'][locale], 'observed good content must retain its actual homepage type');
                    assert.ok(metadata.dates.length > 0, 'popularity dates missing');
                    for (const date of metadata.dates) {
                        assert.match(date.value, /^\d{4}-\d{2}-\d{2}$/);
                        assert.equal(date.title, date.value, 'popularity must retain its full source date');
                        assert.equal(date.text, date.lead, 'popularity colon must come from CSS');
                        assert.ok(!date.text.includes(date.value.slice(0, 4)), 'popularity date must omit the year');
                        assert.equal(date.separator, JSON.stringify(locale === 'en' ? ':' : '\uff1a'));
                    }
                    for (const participant of metadata.participants) {
                        assert.ok(participant.mark.startsWith('\u263a\ufe0e '), 'participant mark must use text smiley presentation');
                        assert.ok(participant.meaning && participant.meaning === participant.title, 'participant meaning must stay accessible');
                    }
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
                        labels: (card.getAttribute('aria-labelledby') || '').split(/\\s+/),
                        link: Boolean(card.querySelector('h3 a[href]')),
                        assessments: card.querySelectorAll('[data-assessment]').length
                    })))()`);
                    for (const card of cards) {
                        assert.equal(card.tag, "ARTICLE", "dashboard cards use semantic article shells");
                        assert.ok(card.heading && card.labels.includes(card.heading), "card must be named by its own title");
                        assert.equal(card.labels.length, 1, "cards must be named by their subject heading");
                        assert.ok(card.link, "article title links must remain visible");
                        assert.equal(card.assessments, 0, "homepage cards omit assessment chips");
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
                                    buttonWeights: copy.classList.contains('ui-button--weight-normal') && view?.classList.contains('ui-button--weight-normal') && edit?.classList.contains('ui-button--weight-primary'),
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
                if (surface === "dataset") {
                    const dataset = await evaluate(`(() => ({
                        sections: document.querySelectorAll('.section-flat').length,
                        previewRows: document.querySelectorAll('[aria-labelledby="dataset-preview"] tbody tr').length,
                        schemaRows: document.querySelectorAll('[aria-labelledby="dataset-schema"] tbody tr').length,
                        source: document.body.textContent.includes('20261102.parquet') && document.body.textContent.includes('2026-11-03 00:01:00'),
                        forms: document.querySelectorAll('form').length,
                        scrollRegions: [...document.querySelectorAll('.table-scroll')].every(region => region.tabIndex === 0 && region.getAttribute('role') === 'region')
                    }))()`);
                    assert.equal(dataset.sections, 3, 'dataset details need metadata, schema and bounded preview sections');
                    assert.equal(dataset.previewRows, 3, 'dataset preview must retain the supplied bounded rows');
                    assert.equal(dataset.schemaRows, 13, 'dataset schema must describe every saved column');
                    assert.ok(dataset.source && dataset.scrollRegions, 'dataset tables must retain source identity and keyboard scrolling');
                    assert.equal(dataset.forms, 0, 'dataset reading does not offer source acquisition or write actions');
                    if ([390, 1440].includes(width)) {
                        const lazy = await evaluate(`(async () => {
                            const before = location.href;
                            const originalFetch = window.fetch;
                            const requests = [];
                            window.fetch = (url, options) => {
                                if (new URL(url, location.href).pathname === '/data/') requests.push(String(url));
                                return originalFetch(url, options);
                            };
                            const opener = document.createElement('a');
                            opener.href = document.querySelector('[data-dataset-page]').href.replace('page=2', 'page=1');
                            opener.textContent = '20261102.parquet';
                            opener.setAttribute('data-dataset-link', '');
                            opener.setAttribute('aria-haspopup', 'dialog');
                            document.body.append(opener);
                            const beforeOpen = requests.length;
                            opener.click();
                            const settle = async predicate => {
                                const until = performance.now() + 5000;
                                while (!predicate() && performance.now() < until) await new Promise(resolve => setTimeout(resolve, 20));
                                if (!predicate()) throw new Error('Dataset dialog did not settle');
                            };
                            await settle(() => document.querySelector('#dataset-dialog[open] [data-page="1"]'));
                            const dialog = document.getElementById('dataset-dialog');
                            const firstRows = dialog.querySelectorAll('section:last-of-type tbody tr').length;
                            dialog.querySelector('[data-dataset-page]').click();
                            await settle(() => dialog.querySelector('[data-page="2"]'));
                            const secondRows = dialog.querySelectorAll('section:last-of-type tbody tr').length;
                            const constrained = dialog.getBoundingClientRect().width <= innerWidth;
                            dialog.close();
                            await settle(() => document.activeElement === opener);
                            window.fetch = originalFetch;
                            opener.remove();
                            return {beforeOpen, requests: requests.length, firstRows, secondRows, constrained, unchangedURL: location.href === before};
                        })()`);
                        assert.deepEqual(lazy, {beforeOpen: 0, requests: 2, firstRows: 3, secondRows: 3, constrained: true, unchangedURL: true}, 'saved Parquet dialogs fetch only opened pages, preserve navigation and restore focus');
                    }

                }
                if (surface === "checks") {
                    assert.equal(await evaluate("new URL(document.querySelector('#consistency-userscript a').href).pathname"), '/consistency-repair.user.js', 'checks offer the Tampermonkey install link');
                    const checks = await evaluate(`(() => ({groups: document.querySelectorAll('.consistency-group').length, findings: document.querySelectorAll('.consistency-findings article.consistency-card').length, tables: document.querySelectorAll('.maintenance-table').length, timestamp: Boolean(document.querySelector('.consistency-section > .source-updated time[datetime]')), navigation: Boolean(document.querySelector('.global-navigation .navigation-tools a[href="/checks/"]'))}))()`);
                    assert.deepEqual(checks, {groups: 2, findings: 2, tables: 0, timestamp: true, navigation: true}, 'the separate check tool groups issue lists and retains its own update time');
                }
                if (surface === "backlog") {
                    assert.equal(await evaluate("document.querySelectorAll('#backlog-section-consistency').length"), 0, "consistency must not become a maintenance count card");
                    assert.equal(await evaluate("document.querySelectorAll('.consistency-section').length"), 0, "maintenance categories do not duplicate the check tool");
                    assert.ok(await evaluate("document.querySelectorAll('.backlog-section h2').length > 0"), "maintenance groups should use h2 headings");
                    assert.equal(await evaluate("document.querySelectorAll('.backlog-task .ui-thumbnail').length"), 0, "maintenance cards must not force thumbnails");
                    const icons = await evaluate(`(async () => {
                        const images = [...document.querySelectorAll('.backlog-task img.maintenance-icon')];
                        await Promise.all(images.map(image => { image.loading = 'eager'; return image.decode(); }));
                        return images.map(image => ({loaded: image.naturalWidth > 0,
                            leading: image.classList.contains('ui-card__icon'),
                            width: image.getBoundingClientRect().width}));
                    })()`);
                    assert.ok(icons.length > 0 && icons.every(image => image.loaded && image.leading && image.width === 22), "maintenance template images must load as compact credited icons");
                }
                if (surface === "maintenance" && (width === 320 || width === 1440)) {
                    await checkMaintenanceControls();
                }
                if (surface === "quality-analysis") {
                    const metrics = await evaluate(`(() => {
                        const host = document.querySelector('[data-quality-analysis]');
                        const matrix = document.querySelector('[data-quality-matrix]');
                        return {rows: host.querySelectorAll('[data-analysis-row]').length,
                            records: host.analysisRows.length, enhanced: host.hasAttribute('data-analysis-enhanced'),
                            unknown: matrix.querySelector('[data-matrix-unknown]').checked,
                            countsHidden: !matrix.querySelector('[data-matrix-count], [data-matrix-grade-count]'),
                            from: matrix.querySelector('[data-matrix-from]').value,
                            firstYear: Number(document.querySelector('[data-analysis-year]').dataset.analysisYear),
                            pies: document.querySelectorAll('[data-analysis-pie]').length,
                            footer: Boolean(host.querySelector('.ui-table-pager__start') && host.querySelector('.ui-table-pager__center') && host.querySelector('.ui-table-pager__end')),
                            sizesUnlinked: !host.querySelector('tbody td:last-child a'),
                            fits: document.documentElement.scrollWidth <= innerWidth + 1};
                    })()`);
                    assert.deepEqual(metrics, {rows: 20, records: 48, enhanced: true, unknown: true, countsHidden: true, from: '2002', firstYear: 2009, pies: 2, footer: true, sizesUnlinked: true, fits: true});
                    if ([320, 1440].includes(width)) {
                        const interaction = await evaluate(`(() => {
                            const host = document.querySelector('[data-quality-analysis]'), form = document.querySelector('[data-quality-matrix]');
                            const original = window.fetch; let requests = 0;
                            window.fetch = (...args) => {requests++; return original(...args);};
                            const total = () => Number(document.querySelector('[data-matrix-total]').textContent.replaceAll(',', ''));
                            const initial = total();
                            for (const code of ['B','BL','C','CL']) form.querySelector('[data-matrix-grade="'+code+'"]').click();
                            const expanded = total() > initial;
                            const from = form.querySelector('[data-matrix-from]'); from.value = '2026'; from.dispatchEvent(new Event('change'));
                            const unknown = form.querySelector('[data-matrix-unknown]'); unknown.click();
                            const ranged = host.analysisRows.filter(row => row.dataset.cohort === '1').every(row => row.dataset.year === '2026');
                            const chart = document.querySelector('[data-chart-year="2026"][data-chart-value="B"]');
                            chart.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                            const dialog = document.getElementById('analysis-chart-articles');
                            const expected = host.analysisRows.filter(row => row.dataset.cohort === '1' && row.dataset.grade === 'B' && row.dataset.year === '2026').length;
                            const exact = dialog.open && dialog.querySelectorAll('[data-chart-article]').length === expected && expected > 0;
                            dialog.close(); from.value = '2002'; from.dispatchEvent(new Event('change')); unknown.click();
                            form.querySelector('[data-matrix-all]').click();
                            if (total() !== 48) form.querySelector('[data-matrix-all]').click();
                            host.querySelector('[data-analysis-next]').click();
                            const visible = [...host.querySelectorAll('[data-analysis-row]')];
                            const pagination = visible.length === 20 && visible.every((row,index) => Number(row.cells[0].textContent) === 21 + index);
                            const headings = [...host.querySelectorAll('thead th')].slice(-2).map(item => item.textContent);
                            const shortHeaders = document.documentElement.lang === 'en' || headings.join('/') === '正文/大小';
                            const compactUnits = visible.filter(row => row.dataset.measured === '1').every(row => row.cells[6].querySelector('small') && (document.documentElement.lang === 'en' || !row.cells[6].textContent.includes(' 字')));
                            const oldImportance = host.analysisRows.map(row => row.dataset.importance);
                            host.analysisRows.forEach(row => row.dataset.importance = 'High');
                            document.querySelector('[data-chart-group="importance"][data-chart-value="High"]').dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true}));
                            const firstChartPage = dialog.querySelectorAll('[data-chart-article]').length === 20;
                            dialog.querySelector('[data-chart-next]').click();
                            const chartPagination = firstChartPage && dialog.querySelectorAll('[data-chart-article]').length === 20 && dialog.querySelector('[data-chart-article] td').textContent === '21';
                            dialog.close(); host.analysisRows.forEach((row,index) => row.dataset.importance = oldImportance[index]);
                            const author = host.querySelector('[data-analysis-author]'); author.value = 'Author_B'; author.dispatchEvent(new Event('input'));
                            const rank = host.querySelector('[data-analysis-author-rank]'); rank.value = '1'; rank.dispatchEvent(new Event('change'));
                            const first = host.querySelectorAll('[data-analysis-row]').length;
                            rank.value = '2'; rank.dispatchEvent(new Event('change'));
                            const second = host.querySelectorAll('[data-analysis-row]').length;
                            host.querySelector('[data-analysis-reset]').click();
                            const totalsOnly = [...document.querySelectorAll('[data-matrix-author]')].every(row => row.cells[2].querySelectorAll('a').length === 1);
                            const defaultGrades = JSON.parse(form.dataset.metadata).grades.filter(item => item.checked).map(item => item.value);
                            for (const input of form.querySelectorAll('[data-matrix-cell]')) input.checked = defaultGrades.includes(input.dataset.grade);
                            form.querySelector('[data-matrix-cell]').dispatchEvent(new Event('change'));
                            window.fetch = original;
                            return {expanded,ranged,exact,pagination,shortHeaders,compactUnits,chartPagination,rank:second > first,totalsOnly,requests};
                        })()`);
                        assert.deepEqual(interaction, {expanded:true,ranged:true,exact:true,pagination:true,shortHeaders:true,compactUnits:true,chartPagination:true,rank:true,totalsOnly:true,requests:0});
                        for (const section of ['distribution','years']) {
                            await evaluate(`document.getElementById('analysis-${section}').scrollIntoView()`);
                            const shot = await call('Page.captureScreenshot', {format:'png',captureBeyondViewport:false},sessionId);
                            fs.writeFileSync(path.join(outputRoot, `${locale}-quality-analysis-${section}-${width}.png`), Buffer.from(shot.data,'base64'));
                        }
                        await evaluate('window.scrollTo(0,0)');
                    }
                }
                if (surface === "quality-authorship") {
                    const directory = await evaluate(`(() => {
                        const host = document.querySelector('[data-quality-authorship-table]');
                        const rows = [...host.querySelectorAll('tbody tr:not([hidden])')];
                        const ids = [...document.querySelectorAll('[id]')].map(element => element.id);
                        return {enhanced: host.hasAttribute('data-table-enhanced'),
                            uniqueIds: ids.length === new Set(ids).size,
                            columns: host.querySelectorAll('thead th').length,
                            rows: rows.length,
                            grades: rows[0].children[2].textContent,
                            whole: rows[1].children[2].textContent,
                            sortable: host.querySelectorAll("thead th button").length,
                            numberSortable: Boolean(host.querySelector("thead th:first-child button")),
                            contributionLinks: rows[0].children[2].querySelectorAll("a[aria-haspopup=dialog]").length,
                            unknown: !rows[2].children[3].querySelector('time'),
                            links: rows.every(row => row.children[1].querySelectorAll('a').length === 2),
                            normalCopy: (() => {
                                const section = document.querySelector('section[aria-labelledby="quality-authors"]');
                                const intro = document.querySelector("main .page-content > p");
                                const accuracy = intro.nextElementSibling;
                                return [intro, accuracy].every(paragraph => paragraph.tagName === 'P' &&
                                    !paragraph.hasAttribute('class') && getComputedStyle(paragraph).fontSize === getComputedStyle(section).fontSize) &&
                                    accuracy.querySelectorAll('a').length === 3 &&
                                    accuracy.querySelector('a').href === 'https://www.mediawiki.org/wiki/WikiWho';
                            })(),
                            inlineAnnotations: rows.every(row => {
                                const opening = document.documentElement.lang === 'en' ? ' (' : '（';
                                const editor = row.children[1].firstElementChild;
                                const active = row.children[3].querySelector('a');
                                const age = row.children[3].querySelector('.context');
                                return editor.nextSibling.textContent === opening && (!active || (
                                    active.nextSibling === age && age.textContent.startsWith(opening) &&
                                    getComputedStyle(age).marginInlineStart === '0px' &&
                                    getComputedStyle(active).marginInlineEnd === '0px'));
                            }),
                            sharedTarget: rows[1].querySelector('a[aria-haspopup=dialog]').getAttribute('aria-controls')};
                    })()`);
                    assert.ok(directory.enhanced && directory.links && directory.unknown, 'quality-author directory requires enhanced tables, separate talk links and unknown activity');
                    assert.ok(directory.uniqueIds, 'separate author tables must retain unique caption and pager IDs');
                    assert.ok(directory.inlineAnnotations, 'talk and date annotations require adjacent localized parentheses without layout gaps');
                    assert.ok(directory.normalCopy, 'intro and accuracy copy require ordinary body paragraphs with inline source links');
                    assert.equal(directory.columns, 4, 'quality-author directory needs the four requested columns');
                    assert.equal(directory.rows, 3);
                    assert.match(directory.grades, /12.*6/);
                    assert.match(directory.whole, /1/);
                    assert.doesNotMatch(directory.whole, /½|⅓/);
                    assert.equal(directory.sortable, 3);
                    assert.equal(directory.numberSortable, false);
                    assert.equal(directory.contributionLinks, 2, 'total and featured counts need separate dialog links');
                    const editorSorting = await evaluate(`(async () => {
                        const host = document.querySelector('[data-quality-authorship-table]');
                        const headers = [...host.querySelectorAll('thead th')];
                        headers[1].querySelector('button').click();
                        await new Promise(requestAnimationFrame);
                        const firstEditor = host.querySelector('tbody tr:not([hidden])').children[1].textContent;
                        headers[3].querySelector('button').click();
                        await new Promise(requestAnimationFrame);
                        const unknownLast = ![...host.querySelectorAll('tbody tr:not([hidden])')].at(-1).children[3].querySelector('time');
                        headers[2].querySelector('button').click();
                        await new Promise(requestAnimationFrame);
                        const fewestFirst = host.querySelector('tbody tr:not([hidden])').children[2].textContent;
                        headers[2].querySelector('button').click();
                        await new Promise(requestAnimationFrame);
                        return {firstEditor, unknownLast, fewestFirst};
                    })()`);
                    assert.match(editorSorting.firstEditor, /Author A/);
                    assert.ok(editorSorting.unknownLast, 'unknown activity remains last in either sort direction');
                    assert.doesNotMatch(editorSorting.fewestFirst, /12/, 'contribution sorting must use numeric counts');
                    for (const id of ['quality-author-1', 'quality-author-1-featured', directory.sharedTarget]) {
                        const opened = await evaluate(`(() => {
                            const dialog = document.getElementById(${JSON.stringify(id)});
                            const trigger = document.querySelector('a[aria-controls="' + dialog.id + '"]');
                            trigger.focus(); trigger.click();
                            const table = dialog.querySelector('table');
                            const bounds = dialog.getBoundingClientRect();
                            return {open: dialog.open, focused: dialog.contains(document.activeElement),
                                enhanced: Boolean(dialog.querySelector('[data-table-enhanced]')),
                                columns: table.querySelectorAll('thead th').length,
                                rows: table.querySelectorAll('tbody tr:not([hidden])').length,
                                shared: Boolean(dialog.querySelector('.quality-authorship-coauthors')?.getAttribute('title').includes('Author B')),
                                firstDate: table.querySelector('tbody tr time')?.getAttribute('datetime'),
                                featuredOnly: [...table.querySelectorAll('tbody tr:not([hidden])')].every(row => /^(FA|FL)$/.test(row.children[2].textContent.trim())),
                                sortable: table.querySelectorAll('thead th button').length,
                                dates: [...table.querySelectorAll('tbody tr:not([hidden])')].every(row => /^\\d{4}-\\d{2}-\\d{2}$/.test(row.children[3].querySelector('time')?.getAttribute('datetime'))),
                                viewport: bounds.left >= -1 && bounds.right <= innerWidth + 1,
                                fits: dialog.scrollWidth <= dialog.clientWidth + 1};
                        })()`);
                        assert.ok(opened.open && opened.focused && opened.enhanced && opened.dates, 'quality-author article dialog must open with focus and dated rows');
                        assert.equal(opened.columns, 4);
                        assert.equal(opened.rows, id === 'quality-author-1' ? 10 : id === 'quality-author-1-featured' ? 6 : 1);
                        assert.equal(opened.sortable, 3);
                        if (id === 'quality-author-1') assert.equal(opened.firstDate, '2026-10-12', 'newest listing must lead regardless of grade');
                        if (id === 'quality-author-1-featured') assert.ok(opened.featuredOnly, 'featured link includes FA and FL only');
                        assert.ok(opened.viewport && opened.fits, 'quality-author dialog must fit the viewport at every width');
                        if (id === 'quality-author-1') {
                            const remaining = await evaluate(`new Promise(resolve => {
                                const host = document.querySelector('#quality-author-1 [data-quality-authorship-table]');
                                [...host.querySelectorAll('a[aria-label], button')].find(button => button.getAttribute('aria-label') === host.dataset.next).click();
                                requestAnimationFrame(() => {
                                    const rows = [...host.querySelectorAll('tbody tr:not([hidden])')];
                                    const result = {count: rows.length, first: rows[0].children[0].textContent.trim()};
                                    host.querySelectorAll('thead th')[2].querySelector('button').click();
                                    requestAnimationFrame(() => {
                                        result.sortedCount = host.querySelectorAll('tbody tr:not([hidden])').length;
                                        result.sortedGrade = host.querySelector('tbody tr:not([hidden])').children[2].textContent.trim();
                                        resolve(result);
                                    });
                                });
                            })`);
                            assert.deepEqual(remaining, {count: 2, first: '11', sortedCount: 10, sortedGrade: 'FA'}, 'sorting a later article page must reset the pager and sort grades by rank');
                        }
                        if (id === directory.sharedTarget) assert.ok(opened.shared, 'shared articles need a tooltip identifying other qualifying authors');
                        if (width === 320 || width === 1440) {
                            const shot = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false}, sessionId);
                            fs.writeFileSync(path.join(outputRoot, `${locale}-${id}-${width}.png`), Buffer.from(shot.data, 'base64'));
                        }
                        await press('Tab', 9, 8);
                        assert.equal(await evaluate("document.querySelector('dialog[open]').contains(document.activeElement)"), true, 'Shift+Tab escaped the quality-author modal');
                        await press('Escape', 27);
                        assert.equal(await evaluate(`new Promise(resolve => requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.getAttribute('aria-controls') === ${JSON.stringify(id)})))`), true, 'quality-author Escape must restore contribution-link focus');
                        assert.equal(await evaluate(`new Promise(resolve => {
                            document.activeElement.click();
                            document.getElementById(${JSON.stringify(id)}).querySelector('button[command=close]').click();
                            requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.getAttribute('aria-controls') === ${JSON.stringify(id)}));
                        })`), true, 'quality-author Close must restore contribution-link focus');
                    }
                }
                if (surface === "quality-authorship" && [320, 1440].includes(width)) {
                    const toggle = await evaluate(`(async () => {
                        const input = document.querySelector('[name=count_b]');
                        const form = input.form;
                        const results = document.querySelector('[data-quality-authorship-results]');
                        window.__qualityToggleMarker = {};
                        const marker = window.__qualityToggleMarker;
                        const settle = async () => {
                            const end = performance.now() + 6000;
                            while (results.hasAttribute('aria-busy')) {
                                if (performance.now() > end) throw new Error('Toggle update timed out');
                                await new Promise(resolve => setTimeout(resolve, 20));
                            }
                        };
                        input.focus(); input.click(); await settle();
                        const included = results.dataset.countB === '1' && input.checked &&
                            new URL(location.href).searchParams.get('count_b') === '1' &&
                            document.querySelectorAll('[data-table-enhanced]').length > 0;
                        const focusRetained = document.activeElement === input;
                        const languages = [...document.querySelectorAll('.language-switcher a[hreflang]')]
                            .every(link => new URL(link.href).searchParams.get('count_b') === '1');
                        input.click(); await settle();
                        const excluded = results.dataset.countB === '0' && !input.checked;
                        input.click(); input.click(); await settle();
                        const latestWins = results.dataset.countB === '0' && !input.checked;
                        const originalFetch = window.fetch;
                        window.fetch = async () => new Response('', {status: 503, headers: {'content-type': 'text/html'}});
                        try { input.click(); await settle(); }
                        finally { window.fetch = originalFetch; }
                        const failedSafely = !input.checked && results.dataset.countB === '0' &&
                            Boolean(form.querySelector('[data-quality-authorship-status]').textContent);
                        return {included, excluded, latestWins, languages, focusRetained, failedSafely,
                            noReload: marker === window.__qualityToggleMarker,
                            switchRole: input.getAttribute('role'), noApplyButton: !form.querySelector('[type=submit]')};
                    })()`);
                    assert.deepEqual(toggle, {included: true, excluded: true, latestWins: true, languages: true,
                        focusRetained: true, failedSafely: true, noReload: true, switchRole: 'switch', noApplyButton: true},
                        'B-Class toggle must update instantly, preserve focus, reject stale responses and retain results on failure');
                }
                if (surface === "reports" || surface === "reports2") {
                    assert.equal(await evaluate("document.querySelectorAll('.page-content h2').length"), 2, "reports shows on-wiki updates and report history");
                    assert.equal(await evaluate("document.querySelectorAll('#dataset-status, #publication-status, .service-panel').length"), 0, "reports must omit collection and service telemetry");
                    assert.equal(await evaluate("document.querySelectorAll('.routine-row').length"), 7, "each on-wiki report needs a destination card");
                    const execution = await evaluate(`(() => {
                        const table = document.querySelector('#recent-events').parentElement.querySelector('table');
                        const row = table.querySelector('tbody tr:not([hidden])');
                        return {columns: table.querySelectorAll('thead th').length,
                            resultLinked: Boolean(row.children[2].querySelector('a[aria-haspopup="dialog"]')),
                            contentLinked: Boolean(row.children[1].querySelector('a')),
                            contentIsPlain: row.children[1].textContent === row.children[1].textContent.trim(),
                            contentWeight: getComputedStyle(row.children[1]).fontWeight,
                            started: row.children[0].querySelector('time')?.textContent,
                            stages: document.querySelector('.job-stages').tagName};
                    })()`);
                    assert.equal(execution.columns, 4, "execution table needs start datetime, content, result and duration");
                    const pager = await evaluate(`(() => {
                        const host = document.querySelector('[data-execution-history]');
                        const status = host.querySelector('.history-pagination span');
                        return {rows: host.querySelectorAll('tbody tr:not([hidden])').length, page: Number(host.dataset.page),
                            total: Number(host.dataset.total), status: status.textContent};
                    })()`);
                    assert.equal(pager.rows, 10, 'execution history must show ten records');
                    assert.equal(pager.page, surface === 'reports2' ? 2 : 1, 'history must preserve the native selected page');
                    assert.ok(pager.total > 20 && pager.status.includes(surface === 'reports2' ? '2' : '1'), 'history status must use the selected server offset');

                    assert.ok(execution.resultLinked && !execution.contentLinked, "only the result opens execution details");
                    assert.ok(execution.contentIsPlain, "execution content must not repeat secondary metadata");
                    assert.equal(execution.contentWeight, "400", "execution content uses normal font weight");
                    assert.match(execution.started, /^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$/, "execution start includes its date and time");
                    const tables = await evaluate(`([...document.querySelectorAll('.publication-table')].map(table => {
                        const bounds = table.getBoundingClientRect();
                        return {width: bounds.width, container: table.parentElement.clientWidth,
                            body: table.tBodies[0]?.getBoundingClientRect().width,
                            rows: [...table.tBodies[0]?.rows || []].map(row => row.getBoundingClientRect().width)};
                    }))`);
                    assert.ok(tables.length > 0 && tables.every(table => table.width >= table.container - 1 && Math.abs(table.body - table.width) < 2 && table.rows.every(row => Math.abs(row - table.width) < 2)), "status tables, bodies, and rows must fill their container: " + JSON.stringify(tables));
                    assert.equal(execution.stages, "UL", "execution stages use an unordered list");
                    const histories = await evaluate("[...document.querySelectorAll('dialog[id^=job-history-]')].map(dialog => dialog.id)");
                    assert.ok(histories.length > 0, "per-invocation history dialogs missing");
                    for (const id of histories) {
                        const opened = await evaluate(`(() => {
                            const dialog = document.getElementById(${JSON.stringify(id)});
                            const button = document.querySelector('[data-execution-history] a[aria-controls="' + dialog.id + '"][aria-haspopup="dialog"]');
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
                    const statusDialog = await evaluate(`(() => {
                        const report = document.getElementById('report-details-pageviews');
                        document.querySelector('.routine-row a[aria-controls="' + report.id + '"]').click();
                        const stages = [...report.querySelectorAll('.job-stages li')];
                        return {open: report.open, focus: report.contains(document.activeElement),
                            actions: stages.length, table: Boolean(report.querySelector('table')),
                            nested: report.querySelectorAll('a[aria-haspopup="dialog"]').length};
                    })()`);
                    assert.ok(statusDialog.open && statusDialog.focus && statusDialog.actions >= 2 && statusDialog.table, "report status must show recorded actions and per-page results in one focused dialog");
                    assert.equal(statusDialog.nested, 0, "report status must avoid nested dialogs");
                    await press("Escape", 27);
                    assert.equal(await evaluate("new Promise(resolve => requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.getAttribute('aria-controls') === 'report-details-pageviews')))"), true, "closing report status must return focus to its card");
                }
                if (width === 320 || width === 1440) {
                    await evaluate("scrollTo(0, 0)");
                    if (surface === 'quality-authorship') await evaluate("[...document.querySelectorAll('[data-quality-authorship-table] .ui-table__table-wrapper')].filter(table => !table.closest('dialog')).forEach(table => table.scrollLeft = 0)");
                    const shot = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                    fs.writeFileSync(path.join(outputRoot, `${locale}-${surface}-${width}.png`), Buffer.from(shot.data, "base64"));

                    if (surface === "home") {
                        await evaluate("document.querySelector('#discussions').scrollIntoView()");
                        const discussionShot = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-discussion-${width}.png`), Buffer.from(discussionShot.data, "base64"));
                    }
                    if (surface === "maintenance") {
                        await evaluate("document.querySelector('.maintenance-table').scrollIntoView()");
                        const rows = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-maintenance-rows-${width}.png`), Buffer.from(rows.data, "base64"));
                        await evaluate("document.querySelector('.ui-table-pager').scrollIntoView({block: 'center'})");
                        const pager = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
                        fs.writeFileSync(path.join(outputRoot, `${locale}-maintenance-pager-${width}.png`), Buffer.from(pager.data, "base64"));
                    }
                }

                if ((surface === 'reports' || surface === 'reports2') && (width === 320 || width === 1440)) {
                    const initialPage = surface === 'reports2' ? 2 : 1;
                    const selectedPage = initialPage === 1 ? 2 : 1;
                    await evaluate(`(() => {
                        const host = document.querySelector('[data-execution-history]');
                        const url = new URL(host.dataset.url, location.href);
                        url.searchParams.set('page', String(host.dataset.page));
                        history.replaceState(null, '', url);
                        window.reportPaginationDocument = document;
                        window.reportPaginationStatus = document.getElementById('tool-status');
                    })()`);
                    await call('Fetch.enable', {patterns: [{urlPattern: `${base}/reports/*`, resourceType: 'Fetch', requestStage: 'Request'}]}, sessionId);
                    try {
                        const selectPage = async (action, expectedPage) => {
                            const fixture = fs.readFileSync(path.join(fixtureRoot, locale, expectedPage === 2 ? 'reports2.html' : 'reports.html'), 'utf8');
                            const expected = await evaluate(`(() => {
                                const page = new DOMParser().parseFromString(${JSON.stringify(fixture)}, 'text/html');
                                const root = page.querySelector('#report-history');
                                return {rows: root.querySelectorAll('[data-execution-history] tbody tr').length,
                                    results: [...root.querySelectorAll('[data-execution-history] a[aria-controls]')].map(link => link.getAttribute('aria-controls')),
                                    dialogs: [...root.querySelectorAll('dialog[id^=job-history-]')].map(dialog => dialog.id)};
                            })()`);
                            const loading = wait('Fetch.requestPaused', sessionId);
                            await evaluate(action);
                            const request = await loading;
                            const url = new URL(request.request.url);
                            assert.ok(['Fetch', 'XHR'].includes(request.resourceType), 'execution pagination must use an in-place request');
                            assert.equal(request.request.method, 'GET', 'execution pagination must remain read-only');
                            assert.equal(url.searchParams.get('lang'), locale, 'execution pagination lost language');
                            assert.equal(url.searchParams.get('page'), String(expectedPage), 'execution pagination selected the wrong page');
                            await call('Fetch.fulfillRequest', {
                                requestId: request.requestId,
                                responseCode: 200,
                                responseHeaders: [{name: 'Content-Type', value: 'text/html; charset=utf-8'}],
                                body: Buffer.from(fixture).toString('base64'),
                            }, sessionId);
                            const state = await evaluate(`(async () => {
                                const deadline = performance.now() + 5000;
                                let root;
                                while (!(root = document.querySelector('#report-history'))
                                    || Number(root.querySelector('[data-execution-history]')?.dataset.page) !== ${expectedPage}
                                    || root.hasAttribute('aria-busy')) {
                                    if (performance.now() > deadline) throw new Error('Report history response did not settle');
                                    await new Promise(resolve => requestAnimationFrame(resolve));
                                }
                                return {sameDocument: window.reportPaginationDocument === document,
                                    sameStatus: window.reportPaginationStatus === document.getElementById('tool-status'),
                                    focused: document.activeElement === root || root.contains(document.activeElement),
                                    rows: root.querySelectorAll('[data-execution-history] tbody tr').length,
                                    results: [...root.querySelectorAll('[data-execution-history] a[aria-controls]')].map(link => link.getAttribute('aria-controls')),
                                    dialogs: [...root.querySelectorAll('dialog[id^=job-history-]')].map(dialog => dialog.id),
                                    enhanced: root.hasAttribute('data-history-enhanced')};
                            })()`);
                            assert.ok(state.sameDocument && state.sameStatus, 'execution pagination must preserve the document and live tool-status DOM');
                            assert.ok(state.enhanced, 'replacement history must restore native pagination');
                            if (action.startsWith('history.')) assert.ok(state.focused, 'browser history must keep focus inside the replaced execution history');
                            assert.deepEqual({rows: state.rows, results: state.results, dialogs: state.dialogs}, expected,
                                'replacement history must include its matching table rows and execution dialogs');
                            const dialog = await evaluate(`(() => {
                                const link = document.querySelector('#report-history a[aria-haspopup="dialog"]');
                                link.focus();
                                link.click();
                                return {id: link.getAttribute('aria-controls'), open: document.getElementById(link.getAttribute('aria-controls')).open};
                            })()`);
                            assert.ok(dialog.open && expected.dialogs.includes(dialog.id), 'fetched execution details must open their matching dialog');
                            await press('Escape', 27);
                            assert.equal(await evaluate(`new Promise(resolve => requestAnimationFrame(() => resolve(!document.querySelector('dialog[open]') && document.activeElement?.getAttribute('aria-controls') === ${JSON.stringify(dialog.id)})))`), true,
                                'fetched execution Escape must restore the opener before navigating history');
                        };
                        await selectPage(`(() => {
                            const host = document.querySelector('[data-execution-history]');
                            const label = host.dataset[${JSON.stringify(surface === 'reports2' ? 'prev' : 'next')}];
                            [...host.querySelectorAll('a[aria-label], button')].find(button => button.getAttribute('aria-label') === label).click();
                        })()`, selectedPage);
                        await selectPage('history.back()', initialPage);
                        await selectPage('history.forward()', selectedPage);
                    } finally { await call('Fetch.disable', {}, sessionId); }
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
        let reportHistoryRaces = 0;
        let reportHistoryFallbacks = 0;
        try {
            const loaded = wait('Page.loadEventFired', sessionId);
            await Promise.all([call('Page.navigate', {url: `${base}/reports/?lang=en&page=1`}, sessionId), loaded]);
            const fixture = fs.readFileSync(path.join(fixtureRoot, 'en', 'reports2.html'), 'utf8');
            const race = await evaluate(`(async () => {
                const originalFetch = window.fetch;
                const originalPage = document;
                const outside = document.getElementById('tool-status');
                const requests = [];
                let delayed;
                window.fetch = (url, options) => {
                    requests.push({url: String(url), signal: options.signal});
                    const response = () => new Response(${JSON.stringify(fixture)}, {headers: {'Content-Type': 'text/html'}});
                    if (requests.length === 1) return new Promise(resolve => { delayed = () => resolve(response()); });
                    return Promise.resolve(response());
                };
                const select = page => {
                    history.pushState(null, '', '/reports/?lang=en&page=' + page);
                    dispatchEvent(new PopStateEvent('popstate'));
                };
                const settle = async condition => {
                    const deadline = performance.now() + 5000;
                    while (!condition()) {
                        if (performance.now() > deadline) throw new Error('Report history race did not settle');
                        await new Promise(resolve => requestAnimationFrame(resolve));
                    }
                };
                try {
                    select(3);
                    await settle(() => requests.length === 1);
                    select(2);
                    await settle(() => Number(document.querySelector('[data-execution-history]')?.dataset.page) === 2
                        && !document.querySelector('#report-history').hasAttribute('aria-busy'));
                    const current = document.querySelector('[data-execution-history]');
                    delayed();
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    return {requests: requests.map(request => new URL(request.url).searchParams.get('page')),
                        aborted: requests[0].signal.aborted,
                        sameDocument: originalPage === document,
                        sameStatus: outside === document.getElementById('tool-status'),
                        sameSelection: current === document.querySelector('[data-execution-history]'),
                        page: Number(current.dataset.page)};
                } finally { window.fetch = originalFetch; }
            })()`);
            assert.deepEqual(race, {requests: ['3', '2'], aborted: true, sameDocument: true, sameStatus: true, sameSelection: true, page: 2},
                'a delayed execution page must not replace a newer selection');
            reportHistoryRaces++;
        } catch (error) {
            failures.push({label: 'en/reports/asynchronous-race', error: error.message});
            console.error('en/reports/asynchronous-race: ' + error.message);
        }
        for (const mode of ['http-error', 'malformed']) {
            const label = 'en/reports/fallback/' + mode;
            try {
                const loaded = wait('Page.loadEventFired', sessionId);
                await Promise.all([call('Page.navigate', {url: `${base}/reports/?lang=en&page=1`}, sessionId), loaded]);
                await evaluate(`(() => {
                    window.reportHistoryFallbackMarker = true;
                    window.fetch = async () => new Response('<div id="report-history">invalid report history</div>', {
                        status: ${mode === 'http-error' ? 503 : 200}, headers: {'Content-Type': 'text/html'}
                    });
                })()`);
                const fallback = wait('Page.loadEventFired', sessionId);
                await evaluate(`(() => {
                    const host = document.querySelector('[data-execution-history]');
                    [...host.querySelectorAll('a[aria-label], button')].find(button => button.getAttribute('aria-label') === host.dataset.next).click();
                })()`);
                await fallback;
                const native = await evaluate(`({reloaded: !window.reportHistoryFallbackMarker,
                    page: new URL(location.href).searchParams.get('page'),
                    available: Boolean(document.querySelector('#report-history [data-execution-history] table'))})`);
                assert.deepEqual(native, {reloaded: true, page: '2', available: true}, 'invalid execution history responses must fall back to a usable requested page');
                reportHistoryFallbacks++;
            } catch (error) {
                failures.push({label, error: error.message});
                console.error(label + ': ' + error.message);
            }
        }
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
                        table.querySelector('.ui-table__header__caption').textContent = 'Category ' + state.task;
                        table.querySelector('caption').textContent = 'Category ' + state.task;
                        table.querySelector('tbody th a').textContent = 'Fetched row ' + marker;
                        for (const form of root.querySelectorAll('form')) {
                            const sort = form.querySelector('.ui-table__table__sort-button');
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
                        for (const link of root.querySelectorAll('.ui-table-pager a')) {
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
                        await act('sort', () => { const button = [...document.querySelectorAll('.ui-table__table__sort-button')].find(button => button.form.querySelector('input[name=sort]').value === 'class'); button.focus(); button.click(); });
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
                        const slow = [...document.querySelectorAll('.ui-table__table__sort-button')].find(button => button.form.querySelector('input[name=sort]').value === 'title');
                        slow.focus(); slow.click();
                        const stale = requests.at(-1);
                        await act('race', () => { const button = [...document.querySelectorAll('.ui-table__table__sort-button')].find(button => button.form.querySelector('input[name=sort]').value === 'importance'); button.focus(); button.click(); });
                        const latest = document.querySelector('.maintenance-worklist');
                        delayed(); await new Promise(resolve => setTimeout(resolve, 50));
                        return {snapshots, sameDocument: document.documentElement === page, outsidePreserved: document.querySelector('.footer-uptime') === outside && outside.textContent === outsideValue,
                            menuPreserved: document.querySelector('.maintenance-menu') === menu, caption: document.querySelector('.ui-table__header__caption').textContent,
                            readOnly: requests.every(request => request.method === 'GET'), pushed: historyAfterActions,
                            historyStable: historyAfterTraversal === historyAfterActions, sortedURL, taskURL,
                            staleIgnored: document.querySelector('.maintenance-worklist') === latest && stale.signal.aborted,
                            controlsReady: document.querySelector('.page-size').hasAttribute('data-auto-submit') && !document.querySelector('[data-v-app]'),
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
                await evaluate("[...document.querySelectorAll('.ui-table__table__sort-button')].find(button => button.form.querySelector('input[name=sort]').value === 'class').click()");
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
                    controls: [...document.querySelectorAll('form input:not([type=hidden]), form select, form button')].filter(control => !control.closest('[hidden]')).every(control => (!control.disabled || control.closest('.ui-table-pager__end')) && control.getBoundingClientRect().height > 0),
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
                if (surface === "about") {
                    const credits = await evaluate(`(() => { const list = document.querySelector('.about-credits'); return {tag: list.tagName, items: list.querySelectorAll(':scope > li').length, cards: list.querySelectorAll('.ui-card').length}; })()`);
                    assert.deepEqual(credits, {tag: 'UL', items: 6, cards: 0}, 'the credits overview is an ordinary six-item list');
                }
                if (surface === "contentcredits") {
                    const medals = await evaluate(`[...document.querySelectorAll('.attribution-card a[href^="/assets/icons/medal-"]')].map(link => link.textContent)`);
                    assert.deepEqual(medals, Array.from({length: 6}, (_, index) => 'Rank ' + (index + 1) + ' medal'));
                }
                if (surface === "contentcredits" || surface === "maintenancecredits") {
                    const attribution = await evaluate(`(() => ({
                        cards: document.querySelectorAll('.attribution-card').length,
                        thumbnails: document.querySelectorAll('.attribution-thumbnail').length,
                        loaded: [...document.querySelectorAll('.attribution-thumbnail img')].every(img => img.complete && img.naturalWidth > 0),
                        authors: document.querySelectorAll('.attribution-details dt').length,
                        categories: document.querySelectorAll('#template-correspondence + .table-scroll a[href*="Category:"]').length,
                    }))()`);
                    const expected = surface === "maintenancecredits" ? 23 : 13;
                    assert.equal(attribution.cards, expected);
                    assert.equal(attribution.thumbnails, expected);
                    assert.equal(attribution.authors, expected * 3);
                    assert.ok(attribution.loaded, 'credit thumbnails must load');
                    if (surface === "maintenancecredits") assert.ok(attribution.categories >= 30, 'template correspondence must link configured categories');
                    if ([320, 1440].includes(width)) {
                        for (const id of surface === "maintenancecredits" ? ['attribution-images', 'template-correspondence'] : ['attribution-images']) {
                            await evaluate(`document.getElementById(${JSON.stringify(id)}).scrollIntoView()`);
                            const shot = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
                            fs.writeFileSync(path.join(outputRoot, `${locale}-${surface}-${id}-${width}.png`), Buffer.from(shot.data, 'base64'));
                        }
                        await evaluate('window.scrollTo(0, 0)');
                    }
                }
                if (surface === "quarterly") {
                    const metrics = await evaluate(`(() => ({
                        count: document.querySelectorAll('.consistency-metrics dd').length,
                        unknown: document.querySelectorAll('.consistency-metrics .consistency-unknown').length,
                        zero: [...document.querySelectorAll('.consistency-metrics dd')].some(value => value.textContent === '0'),
                    }))()`);
                    assert.equal(metrics.count, 12, 'quarterly suggestions expose every expected metric');
                    assert.equal(metrics.unknown, 1, 'unknown evidence stays explicitly unknown');
                    assert.ok(metrics.zero, 'observed zero must remain a numeric zero');
                }
                if (surface === "maintenance") {
                    await checkMaintenancePaginationStatus();
                    await checkMaintenanceControls(false);
                }
                if (surface === "quality-analysis") {
                    const native = await evaluate(`(() => {
                        const host = document.querySelector('[data-quality-analysis]');
                        return {rows: host.querySelectorAll('[data-analysis-row]:not([hidden])').length,
                            controlsHidden: host.querySelector('[data-analysis-controls]').hidden,
                            bplus: Boolean(host.querySelector('[data-grade="Bplus"]')),
                            pies: document.querySelectorAll('[data-analysis-pie]').length,
                            noMethod: !document.getElementById('analysis-method'),
                            columns: document.querySelectorAll('[data-analysis-year]').length,
                            proseGroups: document.querySelectorAll('.analysis-mini-violin').length,
                            pageFits: document.documentElement.scrollWidth <= innerWidth + 1};
                    })()`);
                    assert.equal(native.rows, 20);
                    assert.equal(native.pies, 2);
                    assert.ok(native.noMethod);
                    assert.ok(native.columns >= 17);
                    assert.equal(native.proseGroups, 11);
                    assert.ok(native.controlsHidden && native.bplus && native.pageFits);
                }
                if (surface === "quality-authorship") {
                    const native = await evaluate(`(() => {
                        const host = document.querySelector('[data-quality-authorship-table]');
                        return {rows: host.querySelectorAll('tbody tr:not([hidden])').length,
                            native: !host.querySelector('[data-v-app]'),
                            articles: document.querySelector('#quality-author-1 tbody').rows.length,
                            pageFits: document.documentElement.scrollWidth <= innerWidth + 1};
                    })()`);
                    assert.deepEqual(native, {rows: 3, native: true, articles: 12, pageFits: true}, 'native quality-author tables must retain all evidence without JavaScript');
                }
                if (surface === "reports" || surface === "reports2" || surface === "quality-authorship" || surface === "quality-analysis") {
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
        const result = { browser: browserVersion, inputCapabilities, stableInitializations, checked, reportHistoryRaces, reportHistoryFallbacks, asynchronousMaintenance, maintenanceFallbacks, withoutJavaScript, widths, locales, surfaces, failures };
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
