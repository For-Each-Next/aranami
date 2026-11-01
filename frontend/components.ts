/** Enhance Codex controls while retaining their server-rendered form contracts. */
import { createApp, h, type App } from "vue";
import { CdxButton, type ButtonAction, type ButtonWeight, type ButtonSize } from "@wikimedia/codex";

const applications = new WeakMap<HTMLElement, App>();

function variant<T extends string>(element: Element, prefix: string, values: readonly T[], fallback: T): T {
    return values.find(value => element.classList.contains(`${prefix}${value}`)) ?? fallback;
}

function enhance(root: ParentNode): void {
    for (const button of root.querySelectorAll<HTMLButtonElement>("button.cdx-button:not([data-codex-enhanced])")) {
        // Live snapshots reconcile these native controls in place; Vue must
        // never own DOM that the status renderer also updates.
        if (button.closest("#tool-status, #live-server-status, .page-updates")) continue;
        const host = button.ownerDocument.createElement("span");
        host.className = "codex-control";
        const attrs = Object.fromEntries([...button.attributes].map(attribute => [attribute.name, attribute.value]));
        const content = button.innerHTML;
        const app = createApp({
            render: () => h(CdxButton, {
                ...attrs,
                action: variant<ButtonAction>(button, "cdx-button--action-", ["default", "progressive", "destructive"], "default"),
                weight: variant<ButtonWeight>(button, "cdx-button--weight-", ["normal", "primary", "quiet"], "normal"),
                size: variant<ButtonSize>(button, "cdx-button--size-", ["small", "medium", "large"], "medium"),
                disabled: button.disabled,
                type: button.type,
                "data-codex-enhanced": "",
            }, { default: () => h("span", { innerHTML: content }) }),
        });
        app.mount(host);
        button.replaceWith(host);
        applications.set(host, app);
    }
}

function dispose(root: ParentNode): void {
    for (const host of root.querySelectorAll<HTMLElement>(".codex-control")) {
        applications.get(host)?.unmount();
        applications.delete(host);
    }
}

window.aranamiComponents = { enhance, dispose };
enhance(document);
