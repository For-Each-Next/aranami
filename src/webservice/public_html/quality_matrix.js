/** Combine saved matrix statistics and keep every report view in one cohort. */

const metadataRequests = new WeakMap();

/** Reuse the parsed catalog for both article controls and matrix filters. */
export function qualityMetadata(form = document.querySelector("[data-quality-matrix]")) {
    if (!form) return null;
    if (!form.qualityMetadata && form.dataset.metadata) {
        form.qualityMetadata = JSON.parse(form.dataset.metadata);
        form.qualityMetadata.authorshipIcon = form.dataset.authorshipIcon;
    }
    return form.qualityMetadata || null;
}

/** Fetch a saved catalog once, allowing a fresh attempt after any failure. */
export function loadQualityMetadata(form) {
    const saved = qualityMetadata(form);
    if (saved) return Promise.resolve(saved);
    if (metadataRequests.has(form)) return metadataRequests.get(form);
    const request = (async () => {
        const url = new URL(form.dataset.metadataUrl, location.href);
        if (url.origin !== location.origin) throw new Error("Invalid quality catalog URL");
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(url, {
                credentials: "same-origin", redirect: "error", signal: controller.signal,
                headers: {Accept: "application/json"},
            });
            if (!response.ok || (response.url && new URL(response.url).origin !== location.origin) ||
                    !response.headers.get("content-type")?.startsWith("application/json"))
                throw new Error("Quality catalog unavailable");
            const data = await response.json();
            if (!data || ![data.cells, data.extra, data.grades, data.importance].every(Array.isArray) ||
                    !data.labels || typeof data.labels !== "object" ||
                    !data.extra.every(row => Array.isArray(row) && row.length >= 13))
                throw new Error("Invalid quality catalog");
            data.authorshipIcon = form.dataset.authorshipIcon;
            form.qualityMetadata = data;
            return data;
        } finally {
            clearTimeout(timeout);
        }
    })();
    metadataRequests.set(form, request);
    request.catch(() => metadataRequests.delete(form));
    return request;
}

/** Include unknown dates independently of the inclusive year range. */
export function yearSelected(year, from, to, unknown) {
    return year === null || year === "" ? unknown : Number(year) >= from && Number(year) <= to;
}

/** Recover an exact linear quantile from a saved, counted length histogram. */
export function histogramQuantile(histogram, probability) {
    const values = [...histogram].sort((a, b) => a[0] - b[0]);
    const count = values.reduce((sum, pair) => sum + pair[1], 0);
    if (!count) return null;
    const position = (count - 1) * probability;
    const at = index => {
        let offset = 0;
        for (const [value, frequency] of values) {
            offset += frequency;
            if (index < offset) return value;
        }
        return null;
    };
    const low = at(Math.floor(position));
    return low + (at(Math.ceil(position)) - low) * (position % 1);
}

/** Add prepared cells, retaining missing length measurements separately. */
export function combineCells(cells) {
    const histogram = new Map();
    const points = new Map();
    let pages = 0, measured = 0;
    for (const cell of cells) {
        pages += cell.pages;
        measured += cell.measured;
        for (const item of cell.histogram) histogram.set(item.value, (histogram.get(item.value) || 0) + item.count);
        for (const item of cell.density) points.set(item.position, (points.get(item.position) || 0) + item.density);
    }
    const peakDensity = Math.max(0, ...points.values()) || 1;
    const observed = [...histogram].filter(([, count]) => count > 0).map(([value]) => value);
    const observedCount = observed.reduce((sum, value) => sum + histogram.get(value), 0);
    return {
        pages, measured,
        mean: observedCount ? observed.reduce((sum, value) => sum + value * histogram.get(value), 0) / observedCount : null,
        minimum: observed.length ? Math.min(...observed) : null,
        maximum: observed.length ? Math.max(...observed) : null,
        quantiles: [.025, .16, .5, .84, .975].map(p => histogramQuantile(histogram, p)),
        quartiles: [.25, .75].map(p => histogramQuantile(histogram, p)),
        density: [...points].sort((a, b) => a[0] - b[0]).map(([x, y]) => [x, y / peakDensity]),
    };
}

/** Use every observed catalog length, excluding empty histogram entries. */
export function proseLengthMaximum(cells) {
    let maximum = 25000;
    for (const cell of cells) {
        for (const item of cell.histogram) {
            if (item.count > 0) maximum = Math.max(maximum, item.value);
        }
    }
    return maximum;
}

/** Reserve 90% for 0–25,000 characters, then logarithmically reach the shared maximum. */
export function proseLengthPosition(characters, maximum) {
    if (characters <= 25000) return Math.max(0, characters / 25000 * 900);
    if (maximum <= 25000) return 900;
    return Math.min(1000, 900 + 100 * Math.log(characters / 25000) / Math.log(maximum / 25000));
}

/** Round prose measurements to two significant digits with integer labels. */
function proseLengthLabel(value) {
    if (value === null) return "—";
    const step = value > 0 ? 10 ** Math.max(0, Math.floor(Math.log10(value)) - 1) : 1;
    return (Math.round(value / step) * step).toLocaleString("en-US", {maximumFractionDigits: 0});
}

/** Give each percentile mark its own localized native tooltip. */
export function violinMarkerTitle(kind, value, labels) {
    const names = {minimum: "Minimum", p25: "Lower quartile", p50: "Median", p75: "Upper quartile", maximum: "Maximum", mean: "Mean"};
    const template = labels[`violin_${kind}`] || `${names[kind]}: {value} characters`;
    return template.replaceAll("{value}", proseLengthLabel(value));
}

/** Keep article counts exact while rounding the five length statistics for display. */
export function violinSummary(statistics, template) {
    const values = {
        pages: statistics.pages.toLocaleString("en-US"), measured: statistics.measured.toLocaleString("en-US"),
        minimum: proseLengthLabel(statistics.minimum), maximum: proseLengthLabel(statistics.maximum),
        p25: proseLengthLabel(statistics.quartiles[0]), p50: proseLengthLabel(statistics.quantiles[2]), p75: proseLengthLabel(statistics.quartiles[1]),
    };
    return template.replace(/\{(pages|measured|minimum|p25|p50|p75|maximum)\}/g, (_, key) => values[key]);
}

/** Keep year-table columns selected and the legend limited to plotted categories. */
export function qualityYearSummary(selected, grades, firstYear, lastYear) {
    const years = [];
    for (let year = Math.max(2009, firstYear); year <= lastYear; year++) {
        const cells = selected.filter(cell => cell.year === year);
        years.push({
            year,
            pages: cells.reduce((sum, cell) => sum + cell.pages, 0),
            grades: grades.map(grade => cells.filter(cell => cell.grade === grade.value).reduce((sum, cell) => sum + cell.pages, 0)),
        });
    }
    return {years, legend: grades.filter((_, index) => years.some(year => year.grades[index] > 0))};
}

const svgNamespace = "http://www.w3.org/2000/svg";
function svgNode(tag, attributes = {}, text) {
    const node = document.createElementNS(svgNamespace, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = String(text);
    return node;
}
function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    if (className) element.className = className;
    return element;
}
function chartLink(group, value, label, year, svg = false) {
    const link = svg ? svgNode("a") : node("a");
    link.setAttribute("href", "#analysis-articles");
    link.setAttribute("aria-haspopup", "dialog");
    link.setAttribute("aria-controls", "analysis-chart-articles");
    Object.assign(link.dataset, {chartGroup: group, chartValue: value, chartLabel: label});
    if (year !== undefined) link.dataset.chartYear = String(year);
    return link;
}
const format = value => value === null ? "—" : Math.round(value).toLocaleString(document.documentElement.lang);

const catalogRecords = new WeakMap();
const catalogPreparation = new WeakMap();
const catalogFields = ["page_id", "title", "grade", "importance", "listed_date", "listed_oldid", "prose_characters", "source_bytes", "authors", "title_label", "date_label", "authorship", "review_url", "main_contributors"];

/** Build one light sortable record without creating table elements. */
function catalogRecord(values, data, gradeOrder, importanceOrder) {
    const article = Object.fromEntries(catalogFields.map((key, index) => [key, values[index]]));
    return {
        article, metadata: data,
        dataset: {pageId: String(article.page_id), title: article.title, grade: article.grade, importance: article.importance, year: article.listed_date?.slice(0, 4) || "", measured: article.prose_characters === null ? "0" : "1", cohort: "0", mainAuthors: JSON.stringify(article.authorship?.available === true ? article.authors || [] : []), mainAuthorLabels: JSON.stringify(article.authorship?.available === true ? (article.main_contributors || []).map(author => author.label) : []), significantAuthors: JSON.stringify(article.authorship?.available === true ? article.authorship.editors.map(editor => editor.name) : [])},
        cells: [null, article.title_label, gradeOrder.get(article.grade) ?? -1, importanceOrder.get(article.importance) ?? -1, article.listed_date, (article.main_contributors || []).map(author => author.label).join(", "), article.prose_characters, article.source_bytes].map(value => ({dataset: {sort: value === null ? "" : String(value)}, textContent: value === null ? "" : String(value)})),
    };
}

/** Prepare catalog records in bounded batches while native controls remain responsive. */
export function prepareCatalogRows(form) {
    const data = qualityMetadata(form);
    if (!data) return Promise.resolve([]);
    if (catalogRecords.has(data)) return Promise.resolve(catalogRecords.get(data));
    if (catalogPreparation.has(data)) return catalogPreparation.get(data);
    const request = (async () => {
        const rows = [], extra = data.extra || [];
        const gradeOrder = new Map(data.grades.map((item, index) => [item.value, item.order ?? index]));
        const importanceOrder = new Map(data.importance.map((item, index) => [item.value, item.order ?? index]));
        for (let start = 0; start < extra.length; start += 256) {
            await new Promise(resolve => setTimeout(resolve, 0));
            if (form.isConnected === false) return [];
            for (const values of extra.slice(start, start + 256)) rows.push(catalogRecord(values, data, gradeOrder, importanceOrder));
        }
        catalogRecords.set(data, rows);
        return rows;
    })();
    catalogPreparation.set(data, request);
    request.finally(() => catalogPreparation.delete(data)).catch(() => {});
    return request;
}

/** Reuse prepared light records; direct callers can still read small inline catalogs. */
export function catalogRows() {
    const form = document.querySelector("[data-quality-matrix]");
    const data = form && qualityMetadata(form);
    if (!data) return [];
    if (catalogRecords.has(data)) return catalogRecords.get(data);
    const gradeOrder = new Map(data.grades.map((item, index) => [item.value, item.order ?? index]));
    const importanceOrder = new Map(data.importance.map((item, index) => [item.value, item.order ?? index]));
    return (data.extra || []).map(values => catalogRecord(values, data, gradeOrder, importanceOrder));
}

/** Build a fresh visible row with the same markup as the prepared article table. */
export function articleElement(record) {
    if (!record.article) return record.cloneNode(true);
    const article = record.article, metadata = record.metadata, row = node("tr");
    const labels = metadata.labels;
    const parenthesis = [labels.parenthesis_open || " (", labels.parenthesis_close || ")"];
    row.setAttribute("data-analysis-row", ""); Object.assign(row.dataset, record.dataset);
    const wikiLink = (title, label) => {const link = node("a", label); link.href = `https://zh.wikipedia.org/wiki/${encodeURIComponent(title.replaceAll(" ", "_"))}`; return link;};
    for (const cell of record.cells) {const item = node("td", cell.textContent); Object.assign(item.dataset, cell.dataset); row.append(item);}
    const talk = node("span", undefined, "analysis-talk");
    talk.append(document.createTextNode(parenthesis[0]), wikiLink(`Talk:${article.title}`, labels.talk || "talk"), document.createTextNode(parenthesis[1]));
    row.cells[1].replaceChildren(wikiLink(article.title, article.title_label), document.createTextNode(" "), talk);
    for (const [column, options, value] of [[2, metadata.grades, article.grade], [3, metadata.importance, article.importance]]) {
        const assessment = options.find(item => item.value === value);
        const label = node("span", undefined, "analysis-assessment-label");
        const icon = svgNode("svg", {viewBox: "0 0 12 12", width: 12, height: 12, class: "analysis-swatch", "aria-hidden": "true"});
        icon.append(svgNode("rect", {width: 12, height: 12, fill: assessment?.article_color || assessment?.color || "#72989c"}));
        label.append(icon, document.createTextNode(assessment?.article_label || assessment?.label || value));
        row.cells[column].replaceChildren(label);
    }
    row.cells[4].replaceChildren();
    if (article.listed_date) {
        const time = node("time", article.date_label);
        time.dateTime = article.listed_date;
        if (article.listed_oldid) {
            const link = node("a");
            link.href = `https://zh.wikipedia.org/w/index.php?oldid=${article.listed_oldid}`;
            link.title = labels.listing_evidence || "";
            link.append(time);
            row.cells[4].append(link);
        } else row.cells[4].append(time);
    } else row.cells[4].append(node("span", labels.unknown_listing, "context"));
    if (article.review_url) {
        const review = node("span", undefined, "analysis-review"), link = node("a", labels.review || "review");
        link.href = article.review_url;
        review.append(document.createTextNode(parenthesis[0]), link, document.createTextNode(parenthesis[1]));
        row.cells[4].append(document.createTextNode(" "), review);
    }
    renderMainContributors(row.cells[5], article, labels);
    const popupId = `quality-article-${article.page_id}-authorship`;
    const details = node("a", undefined, "analysis-authorship-info");
    details.href = `#${popupId}`; details.title = metadata.labels.authorship_details.replace("{title}", article.title_label);
    details.setAttribute("aria-label", details.title); details.setAttribute("aria-haspopup", "dialog"); details.setAttribute("aria-controls", popupId);
    const icon = svgNode("svg", {width:16,height:16,viewBox:"0 0 20 20","aria-hidden":"true",focusable:"false"});
    icon.append(svgNode("use", {href: metadata.authorshipIcon})); details.append(icon);
    details.addEventListener("click", event => {
        if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); event.stopPropagation();
        let dialog = document.querySelector("[data-catalog-authorship]");
        if (!dialog) {dialog = node("dialog", undefined, "ui-dialog page-updates analysis-authorship-dialog"); dialog.setAttribute("data-catalog-authorship", ""); document.body.append(dialog);}
        dialog.id = popupId; dialog.setAttribute("aria-labelledby", `${popupId}-heading`);
        const header = node("header", undefined, "ui-dialog__header"), heading = node("h3", details.getAttribute("aria-label"), "ui-dialog__header__title"), close = node("button", "×", "ui-button ui-dialog__header__close-button"), body = node("div", undefined, "ui-dialog__body");
        heading.id = `${popupId}-heading`; close.type = "button"; close.addEventListener("click", () => dialog.close()); header.append(heading, close); body.append(node("p", metadata.labels.authorship_note));
        renderAuthorshipDetails(body, article.authorship, metadata.labels);
        dialog.replaceChildren(header, body); details.focus(); dialog.showModal();
    });
    row.cells[5].append(document.createTextNode(" "), details);
    row.cells[6].textContent = article.prose_characters === null ? "—" : Math.round(article.prose_characters).toLocaleString("en-US");
    row.cells[7].textContent = article.source_bytes === null ? "—" : (article.source_bytes / 1000).toLocaleString("en-US", {minimumFractionDigits: 1, maximumFractionDigits: 1});
    for (const column of [0, 6, 7]) row.cells[column].classList.add("analysis-number");
    if (article.prose_characters !== null) {if (document.documentElement.lang === "en") row.cells[6].append(document.createTextNode(" ")); row.cells[6].append(node("small", metadata.labels.words));}
    if (article.source_bytes !== null) row.cells[7].append(document.createTextNode(" "), node("small", "kB"));
    return row;
}

/** Show qualified main contributors without turning missing evidence into none. */
export function renderMainContributors(cell, article, labels) {
    cell.replaceChildren();
    if (article.authorship?.available !== true) {
        cell.append(node("span", labels.main_unavailable, "context"));
        return;
    }
    const contributors = article.main_contributors || [];
    if (!contributors.length) {
        cell.append(node("span", labels.no_main_contributors, "context"));
        return;
    }
    for (const contributor of contributors) {
        if (cell.childNodes.length) cell.append(document.createTextNode("、"));
        if (contributor.url) {
            const link = node("a", contributor.label);
            link.href = contributor.url;
            cell.append(link);
        } else cell.append(node("span", contributor.label));
    }
}

/** Distinguish unavailable evidence from a saved result with no qualifying editor. */
export function renderAuthorshipDetails(body, proof, labels) {
    if (proof?.available !== true) {
        body.append(node("p", labels.authorship_unavailable, "notice"));
        return;
    }
    const boundary = node("p");
    if (proof.range_end?.url) {
        const date = node("a", proof.range_end.date || "—");
        date.href = proof.range_end.url;
        boundary.append(document.createTextNode(labels.until), date);
    }
    if (proof.visualization_url) {
        const visual = node("a", labels.visualization);
        visual.href = proof.visualization_url;
        if (boundary.childNodes.length) boundary.append(document.createTextNode(" · "));
        boundary.append(visual);
    }
    if (boundary.childNodes.length) body.append(boundary);
    const table = node("table", undefined, "analysis-editor-table");
    const head = node("thead"), headRow = node("tr"), tbody = node("tbody");
    for (const label of [labels.contributors, labels.editor_share, labels.editor_bytes])
        headRow.append(node("th", label));
    head.append(headRow);
    for (const editor of proof.editors) {
        const line = node("tr"), name = node("th");
        if (editor.url) {
            const link = node("a", editor.name);
            link.href = editor.url;
            name.append(link);
        } else name.textContent = editor.name;
        line.append(name, node("td", editor.share_label), node("td", editor.bytes_label));
        tbody.append(line);
    }
    if (!proof.editors.length) {
        const row = node("tr"), message = node("td", labels.no_top_editors);
        message.colSpan = 3;
        row.append(message);
        tbody.append(row);
    }
    table.append(head, tbody);
    body.append(table);
}

/** Apply checkbox and date choices without any network requests. */
export function initializeQualitySelection(form) {
    if (!form || form.hasAttribute("data-selection-enhanced")) return;
    form.setAttribute("data-selection-enhanced", "");
    const cells = [...form.querySelectorAll("[data-matrix-cell]")];
    const parents = [
        [form.querySelector("[data-matrix-all]"), () => true],
        ...[...form.querySelectorAll("[data-matrix-grade]")].map(input => [input, cell => cell.dataset.grade === input.dataset.matrixGrade]),
        ...[...form.querySelectorAll("[data-matrix-importance]")].map(input => [input, cell => cell.dataset.importance === input.dataset.matrixImportance]),
    ].filter(([input]) => input);
    const sync = () => {
        for (const [input, matches] of parents) {
            const children = cells.filter(matches), checked = children.filter(child => child.checked).length;
            input.checked = children.length > 0 && checked === children.length;
            input.indeterminate = checked > 0 && checked < children.length;
        }
    };
    const changed = () => {
        sync();
        form.dataset.qualitySelectionChanged = "1";
        form.dispatchEvent(new Event("quality-selection-change"));
    };
    for (const [input, matches] of parents) input.addEventListener("change", () => {
        for (const child of cells.filter(matches)) child.checked = input.checked;
        changed();
    });
    for (const input of [...cells, ...form.querySelectorAll("[data-matrix-from], [data-matrix-to], [data-matrix-unknown]")]) input.addEventListener("change", changed);
    form.addEventListener("submit", event => event.preventDefault());
    sync();
}

/** Apply the latest native selection once its complete catalog is ready. */
export function initializeQualityMatrix() {
    const form = document.querySelector("[data-quality-matrix]");
    if (!form || form.hasAttribute("data-matrix-enhanced")) return;
    initializeQualitySelection(form);
    const data = qualityMetadata(form);
    if (!data) return;
    form.setAttribute("data-matrix-enhanced", "");
    const inputs = [...form.querySelectorAll("[data-matrix-cell]")];
    const rows = document.querySelector("[data-quality-analysis]")?.analysisRows || [...document.querySelectorAll("[data-analysis-row]")];
    const from = form.querySelector("[data-matrix-from]");
    const to = form.querySelector("[data-matrix-to]");
    const unknown = form.querySelector("[data-matrix-unknown]");
    const grades = data.grades;
    const importance = data.importance;
    const key = (grade, level) => `${grade}:${level}`;
    const yearTable = document.querySelector(".analysis-year-data");
    const yearLabel = yearTable?.tHead.rows[0].cells[0].textContent;
    const totalLabel = yearTable?.tHead.rows[0].cells[yearTable.tHead.rows[0].cells.length - 1].textContent;
    const authorRows = [...document.querySelectorAll("[data-matrix-author]")];
    const authorCounts = new Map(authorRows.map(row => {
        const link = row.cells[2].querySelector("a");
        return [row, link.textContent.replace(/[\d,]+/, "{count}")];
    }));

    /** Read only passive, same-document SVG generated by the chart endpoint. */
    function chartSVG(value) {
        if (typeof value !== "string" || value.length > 4 * 1024 * 1024) throw new Error("Invalid chart");
        const parsed = new DOMParser().parseFromString(value, "image/svg+xml");
        const svg = parsed.documentElement;
        if (svg.localName !== "svg" || parsed.querySelector("parsererror, script, foreignObject, iframe, object, embed, image")) throw new Error("Invalid chart SVG");
        for (const element of [svg, ...svg.querySelectorAll("*")]) {
            for (const attribute of element.attributes) {
                if (/^on/i.test(attribute.name) || (attribute.localName === "href" && !attribute.value.startsWith("#"))) throw new Error("Unsafe chart SVG");
            }
        }
        return document.importNode(svg, true);
    }

    function renderPieTable(dimension, options, selected, total, lengthCharts) {
        const host = document.querySelector(`[data-quality-chart="${dimension === "grade" ? "grades" : "importance"}"]`);
        const body = host?.closest("figure").querySelector("tbody");
        if (!body) return;
        const entries = [];
        for (const option of options) {
            const count = selected.filter(cell => cell[dimension] === option.value).reduce((sum, cell) => sum + cell.pages, 0);
            if (!count) continue;
            const row = node("tr"), heading = node("th"), label = chartLink(dimension, option.value, option.label);
            heading.scope = "row";
            const caption = node("span", undefined, "analysis-assessment-label"), swatch = svgNode("svg", {viewBox: "0 0 12 12", width: 12, height: 12, class: "analysis-swatch", "aria-hidden": "true"});
            swatch.append(svgNode("rect", {width: 12, height: 12, fill: option.color})); caption.append(swatch, label); heading.append(caption);
            const miniCell = node("td", undefined, "analysis-mini-cell"), chartKey = `${dimension}:${option.value}`;
            miniCell.dataset.qualityLength = chartKey;
            miniCell.append(lengthCharts.get(chartKey) || document.createTextNode("—"));
            const countCell = node("td", undefined, "analysis-number"), countLabel = node("span", undefined, "analysis-count");
            countLabel.append(node("span", format(count)), node("small", `${(count / Math.max(1, total) * 100).toFixed(1)}%`)); countCell.append(countLabel);
            row.append(heading, countCell, miniCell); entries.push(row);
        }
        body.replaceChildren(...entries);
    }

    function renderYearTable(selected) {
        if (!yearTable) return;
        const selectedGrades = grades.filter(grade => inputs.some(input => input.checked && input.dataset.grade === grade.value));
        const {years, legend} = qualityYearSummary(selected, selectedGrades, Number(from.value), Number(to.value));
        const legendList = document.querySelector(".analysis-legend");
        if (legendList) legendList.replaceChildren(...legend.map(grade => {
            const item = node("li"), swatch = svgNode("svg", {viewBox:"0 0 12 12",width:12,height:12,"aria-hidden":"true"});
            swatch.append(svgNode("rect",{width:12,height:12,fill:grade.color})); item.append(swatch,document.createTextNode(" " + grade.label)); return item;
        }));
        const head = node("tr"); head.append(node("th", yearLabel));
        for (const grade of selectedGrades) head.append(node("th", grade.label));
        head.append(node("th", totalLabel, "analysis-total-column")); yearTable.tHead.replaceChildren(head); yearTable.tBodies[0].replaceChildren();
        for (const year of years) {
            const row = node("tr"); row.append(node("th", year.year));
            for (const count of year.grades) row.append(node("td", count));
            row.append(node("td", format(year.pages), "analysis-total-column")); yearTable.tBodies[0].append(row);
        }
    }

    let chartSequence = 0, chartTimer, chartController;
    /** Keep the previous plots until the latest complete Matplotlib response arrives. */
    function requestCharts(selected, chosen, total) {
        if (!data.chart_generation || !data.chart_url || typeof fetch !== "function") return;
        const request = ++chartSequence;
        chartController?.abort();
        clearTimeout(chartTimer);
        const url = new URL(data.chart_url, location.href);
        if (url.origin !== location.origin || url.pathname !== "/quality-analysis/charts/") return;
        const status = document.querySelector("[data-quality-chart-status]");
        Object.entries({generation:data.chart_generation,basis:data.authorship_basis || "current",cells:[...chosen].sort().join(","),from:from.value,to:to.value,unknown:unknown.checked ? "1" : "0"}).forEach(([name,value]) => url.searchParams.set(name,value));
        form.dataset.qualityChartsLoading = "1";
        chartTimer = setTimeout(async () => {
            chartController = new AbortController();
            try {
                let response;
                for (let attempt = 0; attempt < 4; attempt++) {
                    response = await fetch(url, {credentials:"same-origin",redirect:"error",signal:chartController.signal,headers:{Accept:"application/json"}});
                    if (response.status !== 429) break;
                    await new Promise(resolve => setTimeout(resolve, 250 * (attempt + 1)));
                    if (request !== chartSequence) return;
                }
                if (!response.ok || !response.headers.get("Content-Type")?.startsWith("application/json")) throw new Error("Charts unavailable");
                const result = await response.json();
                if (request !== chartSequence || !form.isConnected) return;
                if (result.generation !== data.chart_generation || !result.lengths || typeof result.lengths !== "object") throw new Error("Stale chart generation");
                const charts = new Map([["grades",chartSVG(result.grades_svg)],["importance",chartSVG(result.importance_svg)],["years",chartSVG(result.years_svg)]]);
                const lengths = new Map(Object.entries(result.lengths).map(([key,value]) => [key,chartSVG(value)]));
                for (const [key, svg] of charts) document.querySelector(`[data-quality-chart="${key}"]`)?.replaceChildren(svg);
                renderPieTable("grade", grades, selected, total, lengths);
                renderPieTable("importance", importance, selected, total, lengths);
                renderYearTable(selected);
                if (status) status.textContent = "";
                document.dispatchEvent(new Event("quality-charts-replaced"));
            } catch (error) {
                if (request === chartSequence && error.name !== "AbortError" && status) status.textContent = data.chart_failed || "Charts could not update.";
            } finally {
                if (request === chartSequence) {delete form.dataset.qualityChartsLoading; chartController = void 0;}
            }
        }, 150);
    }

    function render(updateCharts = true) {
        if (!from.checkValidity() || !to.checkValidity() || Number(from.value) > Number(to.value)) return;
        const chosen = new Set(inputs.filter(input => input.checked).map(input => key(input.dataset.grade, input.dataset.importance)));
        const selected = data.cells.filter(cell => chosen.has(key(cell.grade, cell.importance)) && yearSelected(cell.year, Number(from.value), Number(to.value), unknown.checked));
        const undated = data.cells.filter(cell => cell.year === null && chosen.has(key(cell.grade, cell.importance))).reduce((sum, cell) => sum + cell.pages, 0);
        const undatedLabel = document.querySelector("[data-undated-count]");
        if (undatedLabel) undatedLabel.textContent = undatedLabel.dataset.label.replace("{count}", format(undated));
        const ids = new Set();
        for (const row of rows) {
            const keep = chosen.has(key(row.dataset.grade, row.dataset.importance)) && yearSelected(row.dataset.year, Number(from.value), Number(to.value), unknown.checked);
            row.dataset.cohort = keep ? "1" : "0"; if (keep) ids.add(row.dataset.pageId);
        }
        form.qualitySelectedIds = ids;
        for (const row of document.querySelectorAll("[data-matrix-page]")) row.dataset.cohort = ids.has(row.dataset.matrixPage) ? "1" : "0";
        for (const row of authorRows) {
            const link = row.cells[2].querySelector("a"), dialog = document.getElementById(link.getAttribute("aria-controls"));
            const pages = row.qualityPageIds ||= row.dataset.pageIds ? JSON.parse(row.dataset.pageIds).map(String) : null;
            const count = pages ? pages.filter(id => ids.has(id)).length :
                [...(dialog?.querySelectorAll("[data-matrix-page]") || [])].filter(item => item.dataset.cohort !== "0").length;
            row.dataset.cohort = count ? "1" : "0"; row.cells[2].dataset.sort = String(count); link.textContent = authorCounts.get(row).replace("{count}", String(count));
        }
        const total = selected.reduce((sum, cell) => sum + cell.pages, 0);
        const totalNode = document.querySelector("[data-matrix-total]"); if (totalNode) totalNode.textContent = format(total);
        if (updateCharts) requestCharts(selected, chosen, total);
        document.dispatchEvent(new Event("quality-cohort-change"));
    }
    form.addEventListener("quality-selection-change", () => render());
    render(form.dataset.qualitySelectionChanged === "1");
}
