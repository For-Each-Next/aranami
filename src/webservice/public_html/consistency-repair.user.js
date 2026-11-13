// ==UserScript==
// @name         Aranami consistency corrections
// @namespace    https://aranami.toolforge.org/
// @version      0.9.2
// @description  Fill a saved talk-page lead correction and open its diff.
// @match        https://zh.wikipedia.org/w/index.php*
// @connect      aranami.toolforge.org
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @run-at       document-idle
// @downloadURL  https://aranami.toolforge.org/consistency-repair.user.js
// @updateURL    https://aranami.toolforge.org/consistency-repair.user.js
// @license      CC0-1.0
// ==/UserScript==

(() => {
    "use strict";

    const pageURL = new URL(location.href);
    const requested = pageURL.searchParams.getAll("aranami-repair");
    if (location.origin !== "https://zh.wikipedia.org" ||
            location.pathname !== "/w/index.php" || requested.length !== 1 ||
            !/^[a-f0-9]{64}$/.test(requested[0]) ||
            pageURL.searchParams.getAll("action").join() !== "edit" ||
            pageURL.searchParams.getAll("section").join() !== "0" ||
            ["oldid", "undo", "undoafter", "wpSection"].some(key => pageURL.searchParams.has(key))) {
        return;
    }
    const repairID = requested[0];
    if (document.documentElement.dataset.aranamiRepair === repairID) return;
    document.documentElement.dataset.aranamiRepair = repairID;

    const page = typeof unsafeWindow === "undefined" ? window : unsafeWindow;
    const config = key => page.mw?.config?.get(key);
    const normalizeTitle = value => typeof value === "string" ? value.replaceAll("_", " ").normalize("NFC") : "";
    const positiveID = value => Number.isSafeInteger(value) && value > 0;
    const language = String(config("wgUserLanguage") || document.documentElement.lang || "en");
    const traditional = /^(zh-(hant|tw|hk|mo))/.test(language);
    const chinese = language.startsWith("zh");
    const messages = chinese ? (traditional ? {
        loading: "Aranami：正在載入修正…",
        unavailable: "Aranami：這項修正已不可用。請回到維護資訊檢查頁重新選擇。",
        editor: "Aranami：請使用原始碼編輯器開啟討論頁第 0 節。",
        stale: "Aranami：討論頁已更新，這項修正需要重新檢查。",
        changed: "Aranami：編輯內容或摘要已變更，未填入修正。",
        opening: "Aranami：已填入修正，正在開啟差異。",
    } : {
        loading: "Aranami：正在载入修正…",
        unavailable: "Aranami：这项修正已不可用。请回到维护信息检查页重新选择。",
        editor: "Aranami：请使用源代码编辑器打开讨论页第 0 节。",
        stale: "Aranami：讨论页已更新，这项修正需要重新检查。",
        changed: "Aranami：编辑内容或摘要已变更，未填入修正。",
        opening: "Aranami：已填入修正，正在打开差异。",
    }) : {
        loading: "Aranami: loading the correction…",
        unavailable: "Aranami: this correction is unavailable. Choose it again from the maintenance checks.",
        editor: "Aranami: open talk-page section 0 in the source editor.",
        stale: "Aranami: the talk page has changed. This correction needs a fresh check.",
        changed: "Aranami: the text or summary changed; the correction was not filled.",
        opening: "Aranami: correction filled; opening the diff.",
    };

    let notice;
    /** Show a plain-text status only on an explicitly selected repair. */
    function show(message) {
        if (!notice) {
            notice = document.createElement("p");
            notice.id = "aranami-repair-status";
            notice.setAttribute("role", "status");
            (document.getElementById("editform") || document.getElementById("mw-content-text") || document.body).prepend(notice);
        }
        notice.textContent = message;
    }

    /** Select native section-zero controls and their unchanged base guard. */
    function editor() {
        const form = document.getElementById("editform");
        const textbox = document.getElementById("wpTextbox1");
        const summary = document.getElementById("wpSummary");
        const diff = document.getElementById("wpDiff");
        if (!form || !textbox || !summary || !diff || diff.disabled ||
                textbox.form !== form || summary.form !== form || diff.form !== form) return null;
        const field = name => form.elements.namedItem(name)?.value;
        const action = new URL(form.action, location.href);
        if (field("wpSection") !== "0" || !/^\d+$/.test(field("editRevId") || "") ||
                !/^\d{14}$/.test(field("wpEdittime") || "") ||
                action.origin !== location.origin || action.pathname !== "/w/index.php") return null;
        return {form, textbox, summary, diff, field};
    }

    /** Wait briefly for the source form, disconnecting the observer on exit. */
    function waitForEditor() {
        const ready = editor();
        if (ready) return Promise.resolve(ready);
        return new Promise((resolve, reject) => {
            const observer = new MutationObserver(() => {
                const found = editor();
                if (!found) return;
                observer.disconnect();
                clearTimeout(timer);
                resolve(found);
            });
            const timer = setTimeout(() => {
                observer.disconnect();
                reject(new Error("editor"));
            }, 12000);
            observer.observe(document.documentElement, {childList: true, subtree: true});
        });
    }

    /** Fetch only the fixed Aranami endpoint and validate its bounded record. */
    function fetchRepair() {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: "GET",
                url: "https://aranami.toolforge.org/api/consistency-repair?repair=" + repairID,
                anonymous: true,
                timeout: 15000,
                responseType: "json",
                onload(response) {
                    try {
                        if (response.status !== 200) throw new Error("unavailable");
                        const result = response.response || JSON.parse(response.responseText);
                        const repair = result?.repair;
                        const bytes = new TextEncoder();
                        if (result.schema !== 1 || repair?.id !== repairID ||
                                !positiveID(repair.revision_id) || !positiveID(repair.talk_page_id) ||
                                typeof repair.title !== "string" || repair.title.length > 512 ||
                                typeof repair.text !== "string" || typeof repair.original_text !== "string" ||
                                !repair.text.trim() || repair.text === repair.original_text ||
                                typeof repair.edit_summary !== "string" || !repair.edit_summary.trim() ||
                                bytes.encode(repair.text).length > 131072 ||
                                bytes.encode(repair.original_text).length > 131072 ||
                                bytes.encode(repair.edit_summary).length > 255 ||
                                typeof repair.timestamp !== "string" || !Number.isFinite(Date.parse(repair.timestamp))) {
                            throw new Error("unavailable");
                        }
                        resolve(repair);
                    } catch {
                        reject(new Error("unavailable"));
                    }
                },
                onerror: () => reject(new Error("unavailable")),
                ontimeout: () => reject(new Error("unavailable")),
                onabort: () => reject(new Error("unavailable")),
            });
        });
    }

    /** Preserve whitespace except CRLF and MediaWiki's appended newline. */
    function sameSource(actual, original) {
        const text = actual.replaceAll("\r\n", "\n");
        const saved = original.replaceAll("\r\n", "\n");
        return text === saved || (saved !== "" && text === saved + "\n");
    }

    /** Fill one verified lead, keeping native conflict fields, then view diff. */
    async function run() {
        try {
            if (config("wgNamespaceNumber") !== 1) throw new Error("editor");
            show(messages.loading);
            const controls = await waitForEditor();
            if (page.mw?.loader?.using) await page.mw.loader.using("jquery.textSelection");
            const selection = page.jQuery?.fn?.textSelection ? page.jQuery(controls.textbox) : null;
            const readText = () => selection ? selection.textSelection("getContents") : controls.textbox.value;
            const initialText = readText();
            const initialSummary = controls.summary.value;
            if (!/^\s*(?:\/\*\s*\*\/\s*)?$/.test(initialSummary)) throw new Error("changed");
            let repair = await fetchRepair();
            const sourceTime = new Date(repair.timestamp).toISOString().replace(/\D/g, "").slice(0, 14);
            if (normalizeTitle(config("wgPageName")) !== normalizeTitle(repair.title) ||
                    normalizeTitle(pageURL.searchParams.get("title")) !== normalizeTitle(repair.title) ||
                    normalizeTitle(new URL(controls.form.action, location.href).searchParams.get("title")) !== normalizeTitle(repair.title) ||
                    Number(controls.field("editRevId")) !== repair.revision_id ||
                    controls.field("wpEdittime") !== sourceTime ||
                    (config("wgCurRevisionId") != null && config("wgCurRevisionId") !== repair.revision_id) ||
                    (config("wgArticleId") != null && config("wgArticleId") !== repair.talk_page_id) ||
                    !sameSource(initialText, repair.original_text)) throw new Error("stale");
            if (editor()?.form !== controls.form || readText() !== initialText ||
                    controls.summary.value !== initialSummary) throw new Error("changed");

            if (selection) selection.textSelection("setContents", repair.text);
            else controls.textbox.value = repair.text;
            controls.textbox.dispatchEvent(new Event("input", {bubbles: true}));
            controls.textbox.dispatchEvent(new Event("change", {bubbles: true}));
            controls.summary.value = repair.edit_summary;
            controls.summary.dispatchEvent(new Event("input", {bubbles: true}));
            controls.summary.dispatchEvent(new Event("change", {bubbles: true}));
            repair = null;

            // Clear the explicit handoff before the native diff request.
            pageURL.searchParams.delete("aranami-repair");
            history.replaceState(history.state, "", pageURL.href);
            const formURL = new URL(controls.form.action, location.href);
            formURL.searchParams.delete("aranami-repair");
            controls.form.action = formURL.href;
            show(messages.opening);
            controls.diff.click();
        } catch (error) {
            show(messages[error.message] || messages.unavailable);
        }
    }
    void run();
})();
