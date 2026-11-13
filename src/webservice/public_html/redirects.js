(() => {
    const browser = document.querySelector("#redirect-browser");
    if (!browser) return;
    const storageKey = "aranami-redirects-hidden-v1";
    const controls = browser.querySelector("[data-redirect-controls]");
    const showHidden = browser.querySelector("#redirect-show-hidden");
    const warning = browser.querySelector("[data-redirect-refresh-error]");
    let hidden = /* @__PURE__ */ new Set();
    let restored = /* @__PURE__ */ new Set();
    let timer;
    let active;
    let stopped = false;
    try {
        const stored = JSON.parse(localStorage.getItem(storageKey) ?? "[]");
        if (Array.isArray(stored))
            hidden = new Set(
                stored.filter(
                    (id) =>
                        typeof id === "string" && /^[1-9]\d{0,19}$/.test(id),
                ),
            );
    } catch {}
    function applyHiding() {
        for (const row of browser.querySelectorAll("[data-redirect-id]")) {
            const id = row.dataset.redirectId;
            const isHidden = hidden.has(id);
            row.hidden =
                restored.has(row.dataset.talkId) ||
                (isHidden && !showHidden.checked);
            row.classList.toggle("redirect-personally-hidden", isHidden);
            const button = row.querySelector("[data-redirect-hide]");
            button.hidden = false;
            button.disabled = false;
            const label = isHidden
                ? browser.dataset.restoreLabel
                : browser.dataset.hideLabel;
            button.textContent = label;
            button.setAttribute(
                "aria-label",
                `${label}: ${row.querySelector("h2").textContent.trim()}`,
            );
        }
    }
    async function checkBanners() {
        const button = browser.querySelector("[data-redirect-check]");
        const error = browser.querySelector("[data-redirect-check-error]");
        const done = browser.querySelector("[data-redirect-check-done]");
        button.disabled = true;
        error.hidden = true;
        done.hidden = true;
        try {
            const aliases = JSON.parse(browser.dataset.bannerAliases || "[]");
            if (
                !Array.isArray(aliases) ||
                !aliases.length ||
                aliases.length > 256 ||
                !aliases.every(
                    (alias) =>
                        typeof alias === "string" &&
                        alias.startsWith("Template:"),
                )
            )
                throw new Error("Missing banner aliases.");
            const ids = [...browser.querySelectorAll("[data-talk-id]")].map(
                (row) => row.dataset.talkId,
            );
            if (
                ids.some((id) => !/^[1-9]\d{0,19}$/.test(id)) ||
                ids.length > 20
            )
                throw new Error("Invalid page IDs.");
            const found = /* @__PURE__ */ new Set();
            for (
                let offset = 0;
                offset < aliases.length && ids.length;
                offset += 50
            ) {
                let continuation = {};
                let complete = false;
                const seen = /* @__PURE__ */ new Set();
                for (let count = 0; count < 20; count++) {
                    const query = new URLSearchParams({
                        action: "query",
                        format: "json",
                        formatversion: "2",
                        origin: "*",
                        prop: "templates",
                        pageids: ids.join("|"),
                        tllimit: "max",
                        tltemplates: aliases
                            .slice(offset, offset + 50)
                            .join("|"),
                        ...continuation,
                    });
                    const response = await fetch(
                        `https://zh.wikipedia.org/w/api.php?${query}`,
                        {
                            credentials: "omit",
                            signal: AbortSignal.timeout(1e4),
                        },
                    );
                    if (!response.ok)
                        throw new Error("Wikipedia request failed.");
                    const data = await response.json();
                    if (data.error || !Array.isArray(data.query?.pages))
                        throw new Error("Invalid Wikipedia response.");
                    for (const page of data.query.pages) {
                        if (page.missing || !ids.includes(String(page.pageid)))
                            throw new Error("Page unavailable.");
                        if (
                            page.templates !== void 0 &&
                            !Array.isArray(page.templates)
                        )
                            throw new Error("Invalid templates.");
                        seen.add(String(page.pageid));
                        if (
                            Array.isArray(page.templates) &&
                            page.templates.length
                        )
                            found.add(String(page.pageid));
                    }
                    if (!data.continue) {
                        complete = true;
                        break;
                    }
                    continuation = data.continue;
                }
                if (!complete || ids.some((id) => !seen.has(id)))
                    throw new Error("Wikipedia response incomplete.");
            }
            restored = found;
            applyHiding();
            done.hidden = false;
        } catch {
            error.hidden = false;
        } finally {
            button.disabled = false;
        }
    }
    function saveHiding() {
        try {
            localStorage.setItem(storageKey, JSON.stringify([...hidden]));
        } catch {}
        applyHiding();
    }
    function schedule() {
        clearTimeout(timer);
        if (!stopped && !document.hidden)
            timer = setTimeout(() => void refresh(), 3e4);
    }
    async function refresh() {
        if (stopped || document.hidden || active) return;
        const results = browser.querySelector("#redirect-results");
        if (results.contains(document.activeElement)) {
            schedule();
            return;
        }
        const request = new AbortController();
        active = request;
        const timeout = setTimeout(() => request.abort(), 1e4);
        try {
            const response = await fetch(location.href, {
                cache: "no-store",
                credentials: "same-origin",
                signal: request.signal,
                headers: { Accept: "text/html" },
            });
            if (
                !response.ok ||
                !response.headers.get("Content-Type")?.startsWith("text/html")
            )
                throw new Error("Saved redirect findings are unavailable.");
            const document2 = new DOMParser().parseFromString(
                await response.text(),
                "text/html",
            );
            const replacement = document2.querySelector(
                '#redirect-results[data-redirect-schema="1"]',
            );
            if (!replacement)
                throw new Error("Invalid saved redirect response.");
            if (stopped || request.signal.aborted) return;
            if (
                results.dataset.identity !== replacement.dataset.identity &&
                !results.contains(window.document.activeElement)
            ) {
                results.replaceWith(replacement);
                window.aranamiComponents?.enhance(replacement);
                applyHiding();
            }
            warning.hidden = true;
        } catch {
            if (!stopped && !document.hidden) warning.hidden = false;
        } finally {
            clearTimeout(timeout);
            active = void 0;
            schedule();
        }
    }
    browser.addEventListener("click", (event) => {
        const button = event.target.closest("[data-redirect-hide]");
        if (!button) return;
        const row = button.closest("[data-redirect-id]");
        const id = row.dataset.redirectId;
        if (hidden.has(id)) hidden.delete(id);
        else hidden.add(id);
        saveHiding();
        if (row.hidden) showHidden.focus({ preventScroll: true });
    });
    browser
        .querySelector("[data-redirect-restore-all]")
        .addEventListener("click", () => {
            hidden.clear();
            saveHiding();
        });
    browser
        .querySelector("[data-redirect-check]")
        .addEventListener("click", () => void checkBanners());
    showHidden.addEventListener("change", applyHiding);
    window.addEventListener("storage", (event) => {
        if (event.key !== storageKey) return;
        try {
            const ids = JSON.parse(event.newValue ?? "[]");
            if (Array.isArray(ids)) {
                hidden = new Set(
                    ids.filter(
                        (id) =>
                            typeof id === "string" &&
                            /^[1-9]\d{0,19}$/.test(id),
                    ),
                );
                applyHiding();
            }
        } catch {}
    });
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            clearTimeout(timer);
            active?.abort();
        } else schedule();
    });
    window.addEventListener("pagehide", () => {
        stopped = true;
        clearTimeout(timer);
        active?.abort();
    });
    window.addEventListener("pageshow", () => {
        stopped = false;
        schedule();
    });
    controls.hidden = false;
    showHidden.disabled = false;
    browser.querySelector("[data-redirect-restore-all]").disabled = false;
    browser.querySelector("[data-redirect-check]").disabled = false;
    applyHiding();
    schedule();
})();
