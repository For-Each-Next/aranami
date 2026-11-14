/** Keep keyboard navigation on Matplotlib-generated SVG figures. */
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
}

/** Restore keyboard navigation after rendering or a filtered SVG swap. */
export function initializeQualityChartControls(root = document) {
    for (const host of root.querySelectorAll("[data-quality-chart]")) {
        const svg = host.querySelector("svg");
        if (!svg || figures.get(host)?.svg === svg) continue;
        const view = (svg.getAttribute("viewBox") || "").trim().split(/\s+/).map(Number);
        if (view.length !== 4 || !view.every(Number.isFinite) || view[2] <= 0 || view[3] <= 0) continue;
        if (!figures.has(host)) listen(host);
        figures.set(host, {svg, original:[...view], view:[...view]});
        svg.setAttribute("tabindex", "0");
    }
}
