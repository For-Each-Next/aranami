/** Add keyboard and pointer navigation to Matplotlib-generated SVG figures. */
const figures = new WeakMap();

/** Keep the visible rectangle inside the original figure at a bounded scale. */
function constrain(state) {
    const [x, y, width, height] = state.original;
    state.view[2] = Math.max(width / 16, Math.min(width, state.view[2]));
    state.view[3] = height * state.view[2] / width;
    state.view[0] = Math.max(x, Math.min(x + width - state.view[2], state.view[0]));
    state.view[1] = Math.max(y, Math.min(y + height - state.view[3], state.view[1]));
    state.svg.setAttribute("viewBox", state.view.join(" "));
}

/** Zoom around the current center, preserving the figure's aspect ratio. */
function zoom(state, factor) {
    const width = state.view[2], height = state.view[3];
    state.view[2] *= factor;
    state.view[3] *= factor;
    state.view[0] += (width - state.view[2]) / 2;
    state.view[1] += (height - state.view[3]) / 2;
    constrain(state);
}

/** Attach once to a host, looking up its latest replacement figure per event. */
function listen(host) {
    host.addEventListener("click", event => {
        const state = figures.get(host);
        if (state?.dragged) {
            state.dragged = false;
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        const button = event.target.closest("[data-chart-action]");
        if (!state || !button || !host.contains(button)) return;
        const action = button.dataset.chartAction;
        if (action === "in" || action === "out") zoom(state, action === "in" ? .8 : 1.25);
        else if (action === "reset") {
            state.view = [...state.original];
            constrain(state);
        } else if (action === "pan") {
            state.pan = !state.pan;
            button.setAttribute("aria-pressed", String(state.pan));
            state.svg.style.cursor = state.pan ? "grab" : "";
        }
    }, true);
    host.addEventListener("keydown", event => {
        const state = figures.get(host);
        if (!state || event.target !== state.svg) return;
        const moves = {ArrowLeft:[-.1, 0], ArrowRight:[.1, 0], ArrowUp:[0, -.1], ArrowDown:[0, .1]};
        if (moves[event.key]) {
            state.view[0] += state.view[2] * moves[event.key][0];
            state.view[1] += state.view[3] * moves[event.key][1];
            constrain(state);
        } else if (event.key === "+" || event.key === "=") zoom(state, .8);
        else if (event.key === "-") zoom(state, 1.25);
        else if (event.key === "0" || event.key === "Home") {
            state.view = [...state.original];
            constrain(state);
        } else return;
        event.preventDefault();
    });
    host.addEventListener("wheel", event => {
        const state = figures.get(host);
        if (!state?.pan || !state.svg.contains(event.target)) return;
        event.preventDefault();
        zoom(state, event.deltaY < 0 ? .8 : 1.25);
    }, {passive:false});
    host.addEventListener("pointerdown", event => {
        const state = figures.get(host);
        if (!state?.pan || event.button !== 0 || !state.svg.contains(event.target)) return;
        state.pointer = {id:event.pointerId, x:event.clientX, y:event.clientY, view:[...state.view]};
        state.dragged = false;
        host.setPointerCapture(event.pointerId);
    });
    host.addEventListener("pointermove", event => {
        const state = figures.get(host), pointer = state?.pointer;
        if (!pointer || event.pointerId !== pointer.id) return;
        const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
        if (Math.abs(dx) + Math.abs(dy) < 4 && !state.dragged) return;
        state.dragged = true;
        const box = state.svg.getBoundingClientRect();
        state.view[0] = pointer.view[0] - dx * pointer.view[2] / box.width;
        state.view[1] = pointer.view[1] - dy * pointer.view[3] / box.height;
        constrain(state);
    });
    for (const name of ["pointerup", "pointercancel"]) host.addEventListener(name, event => {
        const state = figures.get(host);
        if (state?.pointer?.id !== event.pointerId) return;
        state.pointer = undefined;
        if (host.hasPointerCapture(event.pointerId)) host.releasePointerCapture(event.pointerId);
    });
}

/** Restore localized controls after initial rendering or a filtered SVG swap. */
export function initializeQualityChartControls(root = document) {
    for (const host of root.querySelectorAll("[data-quality-chart]")) {
        const svg = host.querySelector("svg");
        if (!svg || figures.get(host)?.svg === svg) continue;
        const view = (svg.getAttribute("viewBox") || "").trim().split(/\s+/).map(Number);
        if (view.length !== 4 || !view.every(Number.isFinite) || view[2] <= 0 || view[3] <= 0) continue;
        if (!figures.has(host)) listen(host);
        figures.set(host, {svg, original:[...view], view:[...view], pan:false, dragged:false});
        svg.setAttribute("tabindex", "0");
        const labels = JSON.parse(host.dataset.qualityChartLabels || "{}");
        host.querySelector("[data-chart-controls]")?.remove();
        const controls = document.createElement("div");
        controls.className = "analysis-chart-controls";
        controls.dataset.chartControls = "";
        for (const [action, key, fallback] of [["in", "zoom_in", "+"], ["out", "zoom_out", "−"], ["pan", "pan", "Pan"], ["reset", "reset", "Reset"]]) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "ui-button";
            button.dataset.chartAction = action;
            button.textContent = labels[key] || fallback;
            if (action === "pan") button.setAttribute("aria-pressed", "false");
            controls.append(button);
        }
        host.prepend(controls);
    }
}
