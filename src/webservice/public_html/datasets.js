function datasetURL(value) {
    const url = new URL(value, location.href);
    return url.origin === location.origin && url.pathname === "/data/"
        ? url
        : void 0;
}
function initializeDatasets() {
    let dialog;
    let opener;
    let controller;
    let requestNumber = 0;
    async function load(url, control, opening) {
        const request = ++requestNumber;
        controller?.abort();
        controller = new AbortController();
        control.setAttribute("aria-busy", "true");
        dialog?.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(url, {
                signal: controller.signal,
                credentials: "same-origin",
                redirect: "error",
                headers: { Accept: "text/html" },
            });
            if (
                !response.ok ||
                (response.url && !datasetURL(response.url)) ||
                response.headers.get("Content-Type")?.split(";")[0].trim() !==
                    "text/html"
            )
                throw new Error("Saved data unavailable");
            const page = new DOMParser().parseFromString(
                await response.text(),
                "text/html",
            );
            if (request !== requestNumber) return;
            const viewer = page.querySelector("#dataset-viewer");
            const template = page.querySelector("#dataset-dialog-template");
            if (
                !viewer ||
                !template ||
                viewer.dataset.file !== url.searchParams.get("file") ||
                !Number.isSafeInteger(Number(viewer.dataset.page)) ||
                Number(viewer.dataset.page) < 1 ||
                viewer.querySelector("script, iframe, object, embed")
            )
                throw new Error("Invalid saved-data response");
            if (opening) {
                const previous = dialog;
                dialog = void 0;
                previous?.close();
                previous?.remove();
                dialog = template.content.firstElementChild?.cloneNode(true);
                if (dialog?.nodeName !== "DIALOG")
                    throw new Error("Missing saved-data dialog");
                opener = control;
                const opened = dialog;
                dialog.addEventListener("close", () => {
                    if (dialog !== opened) return;
                    ++requestNumber;
                    controller?.abort();
                    if (opener?.isConnected) opener.focus();
                });
                document.body.append(dialog);
            }
            if (!dialog) throw new Error("Missing saved-data dialog");
            const body = dialog.querySelector("[data-dataset-content]");
            if (!body) throw new Error("Missing saved-data content");
            const identities = /* @__PURE__ */ new Map();
            for (const element of [
                viewer,
                ...viewer.querySelectorAll("[id]"),
            ]) {
                identities.set(element.id, `dataset-dialog-${element.id}`);
                element.id = identities.get(element.id);
            }
            for (const element of viewer.querySelectorAll(
                "[aria-labelledby]",
            )) {
                const label = element.getAttribute("aria-labelledby");
                element.setAttribute(
                    "aria-labelledby",
                    identities.get(label) || label,
                );
            }
            body.replaceChildren(viewer);
            window.aranamiComponents?.enhance(body);
            if (opening) dialog.showModal();
            else {
                viewer.tabIndex = -1;
                viewer.focus({ preventScroll: true });
                dialog.querySelector(".ui-dialog__body")?.scrollTo(0, 0);
            }
        } catch {
            if (request === requestNumber) location.assign(url.href);
        } finally {
            control.removeAttribute("aria-busy");
            if (request === requestNumber)
                dialog?.removeAttribute("aria-busy");
        }
    }
    document.addEventListener("click", (event) => {
        const control = event.target.closest(
            "a[data-dataset-link],#dataset-dialog a[data-dataset-page]",
        );
        if (
            !control ||
            event.defaultPrevented ||
            event.ctrlKey ||
            event.metaKey ||
            event.shiftKey ||
            event.altKey ||
            event.button > 0 ||
            typeof fetch !== "function" ||
            typeof DOMParser !== "function" ||
            typeof AbortController !== "function" ||
            typeof HTMLDialogElement === "undefined" ||
            typeof HTMLDialogElement.prototype.showModal !== "function"
        )
            return;
        const url = datasetURL(control.href);
        if (!url) return;
        event.preventDefault();
        void load(url, control, control.hasAttribute("data-dataset-link"));
    });
}
export { initializeDatasets };
