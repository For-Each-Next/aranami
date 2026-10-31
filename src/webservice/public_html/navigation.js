/* Enhance native navigation disclosures while keeping links usable offline. */
"use strict";
(() => {
    const menus = [...document.querySelectorAll(".navigation-tools")];
    const close = (menu, restoreFocus = false) => {
        menu.open = false;
        if (restoreFocus) menu.querySelector("summary").focus();
    };
    for (const menu of menus) {
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
            const links = [...menu.querySelectorAll("a[href]")];
            if (!links.length) return;
            let index = links.indexOf(document.activeElement);
            if (event.key === "Home") index = 0;
            else if (event.key === "End") index = links.length - 1;
            else if (index < 0) index = event.key === "ArrowDown" ? 0 : links.length - 1;
            else index = (index + (event.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
            links[index].focus();
        });
        menu.addEventListener("click", (event) => {
            if (event.target.closest("a")) close(menu);
        });
    }
    document.addEventListener("click", (event) => {
        for (const menu of menus) if (!menu.contains(event.target)) close(menu);
    });
    document.addEventListener("focusin", (event) => {
        for (const menu of menus) if (!menu.contains(event.target)) close(menu);
    });
    for (const field of document.querySelectorAll(".assessment-filter")) {
        const [operation, grade, endpoint] = field.querySelectorAll("select");
        const endpointLabel = field.querySelector(`label[for="${endpoint.id}"]`);
        const update = () => {
            const isRange = operation.value === "range";
            endpoint.hidden = !isRange;
            endpoint.disabled = !isRange;
            endpoint.required = isRange;
            endpointLabel.hidden = !isRange;
            grade.required = operation.value !== "eq";
        };
        operation.addEventListener("change", update);
        update();
    }
})();
