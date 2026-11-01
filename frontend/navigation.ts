/* Enhance native navigation disclosures while keeping links usable offline. */
"use strict";
(() => {
    const menus = [...document.querySelectorAll<HTMLDetailsElement>(".navigation-tools")];
    const hoverOpened = new WeakSet<HTMLDetailsElement>();
    const close = (menu: HTMLDetailsElement, restoreFocus = false): void => {
        hoverOpened.delete(menu);
        menu.open = false;
        if (restoreFocus) menu.querySelector("summary")!.focus();
    };
    for (const menu of menus) {
        if (menu.closest(".global-navigation")) {
            menu.addEventListener("pointerenter", (event) => {
                if (event.pointerType !== "mouse" || !matchMedia("(hover: hover)").matches || menu.open) return;
                hoverOpened.add(menu);
                menu.open = true;
            });
            menu.addEventListener("pointerleave", () => {
                if (hoverOpened.has(menu) && !menu.contains(document.activeElement)) close(menu);
            });
            menu.querySelector("summary")!.addEventListener("click", (event) => {
                // The first activation keeps a hover preview open for click or keyboard use.
                if (menu.open && hoverOpened.has(menu)) {
                    event.preventDefault();
                    hoverOpened.delete(menu);
                }
            });
        }
        menu.addEventListener("toggle", () => {
            if (menu.open) {
                for (const other of menus) if (other !== menu) close(other);
            }
        });
        menu.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && menu.open) {
                event.preventDefault();
                close(menu, true);
            }
            if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            menu.open = true;
            const links = [...menu.querySelectorAll<HTMLAnchorElement>("a[href]")];
            if (!links.length) return;
            let index = links.indexOf(document.activeElement as HTMLAnchorElement);
            if (event.key === "Home") index = 0;
            else if (event.key === "End") index = links.length - 1;
            else if (index < 0) index = event.key === "ArrowDown" ? 0 : links.length - 1;
            else index = (index + (event.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
            links[index].focus();
        });
        menu.addEventListener("click", (event) => {
            if ((event.target as Element).closest("a")) close(menu);
        });
    }
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        for (const menu of menus) {
            if (menu.open && hoverOpened.has(menu)) {
                event.preventDefault();
                close(menu);
            }
        }
    });
    document.addEventListener("click", (event) => {
        for (const menu of menus) if (!menu.contains(event.target as Node | null)) close(menu);
    });
    document.addEventListener("focusin", (event) => {
        for (const menu of menus) if (!menu.contains(event.target as Node | null)) close(menu);
    });
})();
