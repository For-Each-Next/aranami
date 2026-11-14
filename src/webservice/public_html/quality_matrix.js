/** Combine saved matrix statistics and keep every report view in one cohort. */

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
    const maximum = Math.max(0, ...points.values()) || 1;
    return {
        pages, measured,
        quantiles: [.025, .16, .5, .84, .975].map(p => histogramQuantile(histogram, p)),
        density: [...points].sort((a, b) => a[0] - b[0]).map(([x, y]) => [x, y / maximum]),
    };
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

/** Keep the large lower-grade catalog as light records until a row is shown. */
export function catalogRows() {
    const form = document.querySelector("[data-quality-matrix]");
    if (!form) return [];
    const data = JSON.parse(form.dataset.metadata);
    return (data.extra || []).map(values => Object.fromEntries(["page_id", "title", "grade", "importance", "listed_date", "listed_oldid", "prose_characters", "source_bytes", "authors", "author_ranks", "title_label", "date_label", "authorship"].map((key, index) => [key, values[index]]))).map(article => ({
        article, metadata: data,
        dataset: {pageId: String(article.page_id), title: article.title, grade: article.grade, importance: article.importance, year: article.listed_date?.slice(0, 4) || "", measured: article.prose_characters === null ? "0" : "1", cohort: "0", authorRanks: JSON.stringify(article.author_ranks || [])},
        cells: [null, article.title_label, data.grades.findIndex(item => item.value === article.grade), data.importance.findIndex(item => item.value === article.importance), article.listed_date, (article.authors || []).join(", "), article.prose_characters, article.source_bytes].map(value => ({dataset: {sort: value === null ? "" : String(value)}, textContent: value === null ? "" : String(value)})),
    }));
}

/** Materialize only visible table rows, with escaped text and exact links. */
export function articleElement(record) {
    if (!record.article) return record;
    if (record.element) return record.element;
    const article = record.article, metadata = record.metadata, row = node("tr");
    row.setAttribute("data-analysis-row", ""); Object.assign(row.dataset, record.dataset);
    const wikiLink = (title, label) => {const link = node("a", label); link.href = `https://zh.wikipedia.org/wiki/${encodeURIComponent(title.replaceAll(" ", "_"))}`; return link;};
    for (const cell of record.cells) {const item = node("td", cell.textContent); Object.assign(item.dataset, cell.dataset); row.append(item);}
    row.cells[1].replaceChildren(wikiLink(article.title, article.title_label));
    row.cells[2].textContent = metadata.grades.find(item => item.value === article.grade)?.label || article.grade;
    row.cells[3].textContent = metadata.importance.find(item => item.value === article.importance)?.label || article.importance;
    row.cells[4].textContent = article.date_label || metadata.labels.unknown_listing;
    if (article.listed_oldid && article.date_label) {const link = node("a", article.date_label); link.href = `https://zh.wikipedia.org/w/index.php?oldid=${article.listed_oldid}`; row.cells[4].replaceChildren(link);}
    row.cells[5].replaceChildren();
    for (const name of article.authors || []) {if (row.cells[5].childNodes.length) row.cells[5].append(document.createTextNode("、")); row.cells[5].append(wikiLink(`User:${name}`, name));}
    const details = node("button", "ⓘ", "analysis-authorship-info"); details.type = "button"; details.setAttribute("aria-label", `${article.title_label} · ${metadata.labels.contributors}`); details.setAttribute("aria-haspopup", "dialog");
    details.addEventListener("click", () => {
        let dialog = document.getElementById("quality-catalog-authorship");
        if (!dialog) {dialog = node("dialog", undefined, "ui-dialog page-updates analysis-authorship-dialog"); dialog.id = "quality-catalog-authorship"; dialog.setAttribute("aria-labelledby", "quality-catalog-authorship-heading"); document.body.append(dialog);}
        const header = node("header", undefined, "ui-dialog__header"), heading = node("h3", details.getAttribute("aria-label"), "ui-dialog__header__title"), close = node("button", "×", "ui-button ui-dialog__header__close-button"), body = node("div", undefined, "ui-dialog__body");
        heading.id = "quality-catalog-authorship-heading"; close.type = "button"; close.addEventListener("click", () => dialog.close()); header.append(heading, close); body.append(node("p", metadata.labels.authorship_note));
        const proof = article.authorship || {available: false};
        if (!proof.available) body.append(node("p", metadata.labels.authorship_unavailable, "notice"));
        else {
            const boundary = node("p", metadata.labels.until), date = node("a", proof.range_end?.date || "—"); date.href = proof.range_end.url; boundary.append(date);
            if (proof.visualization_url) {const visual = node("a", metadata.labels.visualization); visual.href = proof.visualization_url; boundary.append(document.createTextNode(" · "), visual);}
            body.append(boundary);
            const table = node("table", undefined, "analysis-editor-table"), head = node("thead"), headRow = node("tr"), tbody = node("tbody");
            for (const label of [metadata.labels.contributors, metadata.labels.editor_share, metadata.labels.editor_bytes]) headRow.append(node("th", label));
            head.append(headRow);
            for (const editor of proof.editors) {const line = node("tr"), name = node("th"); if (editor.url) {const link = node("a", editor.name); link.href = editor.url; name.append(link);} else name.textContent = editor.name; line.append(name, node("td", editor.share_label), node("td", editor.bytes_label)); tbody.append(line);}
            table.append(head, tbody); body.append(table);
        }
        dialog.replaceChildren(header, body); details.focus(); dialog.showModal();
    });
    row.cells[5].append(details);
    row.cells[6].textContent = format(article.prose_characters);
    row.cells[7].textContent = article.source_bytes === null ? "—" : (article.source_bytes / 1000).toLocaleString(document.documentElement.lang, {minimumFractionDigits: 1, maximumFractionDigits: 1});
    for (const column of [0, 6, 7]) row.cells[column].classList.add("analysis-number");
    if (article.prose_characters !== null) {if (document.documentElement.lang === "en") row.cells[6].append(document.createTextNode(" ")); row.cells[6].append(node("small", metadata.labels.words));}
    if (article.source_bytes !== null) row.cells[7].append(document.createTextNode(" "), node("small", "kB"));
    record.element = row; return row;
}

/** Apply checkbox and date choices without any network requests. */
export function initializeQualityMatrix() {
    const form = document.querySelector("[data-quality-matrix]");
    if (!form) return;
    const data = JSON.parse(form.dataset.metadata);
    const inputs = [...form.querySelectorAll("[data-matrix-cell]")];
    const rows = document.querySelector("[data-quality-analysis]")?.analysisRows || [...document.querySelectorAll("[data-analysis-row]")];
    const from = form.querySelector("[data-matrix-from]");
    const to = form.querySelector("[data-matrix-to]");
    const unknown = form.querySelector("[data-matrix-unknown]");
    const grades = data.grades;
    const importance = data.importance;
    const key = (grade, level) => `${grade}:${level}`;
    const global = form.querySelector("[data-matrix-all]");
    const parents = [
        [global, () => true],
        ...[...form.querySelectorAll("[data-matrix-grade]")].map(input => [input, cell => cell.dataset.grade === input.dataset.matrixGrade]),
        ...[...form.querySelectorAll("[data-matrix-importance]")].map(input => [input, cell => cell.dataset.importance === input.dataset.matrixImportance]),
    ];
    const yearSvg = document.querySelector(".analysis-year-columns");
    const yearTable = document.querySelector(".analysis-year-data");
    const yearLabel = yearTable?.tHead.rows[0].cells[0].textContent;
    const totalLabel = yearTable?.tHead.rows[0].cells[yearTable.tHead.rows[0].cells.length - 1].textContent;
    const authorRows = [...document.querySelectorAll("[data-matrix-author]")];
    const authorCounts = new Map(authorRows.map(row => {
        const link = row.cells[2].querySelector("a");
        return [row, link.textContent.replace(/[\d,]+/, "{count}")];
    }));

    function renderPie(dimension, options, selected, total) {
        const pie = document.querySelector(`[data-analysis-pie="${dimension === "grade" ? "class" : "importance"}"]`);
        if (!pie) return;
        pie.querySelectorAll("a").forEach(element => element.remove());
        const body = pie.closest("figure").querySelector("tbody");
        body.replaceChildren();
        let start = -Math.PI / 2;
        for (const option of options) {
            const count = selected.filter(cell => cell[dimension] === option.value).reduce((sum, cell) => sum + cell.pages, 0);
            if (!count) continue;
            const share = count / Math.max(1, total), end = start + share * 2 * Math.PI;
            const link = chartLink(dimension, option.value, option.label, undefined, true);
            const slice = share === 1 ? svgNode("circle", {cx: 130, cy: 130, r: 112, fill: option.color}) : svgNode("path", {
                d: `M 130 130 L ${130 + 112 * Math.cos(start)} ${130 + 112 * Math.sin(start)} A 112 112 0 ${share > .5 ? 1 : 0} 1 ${130 + 112 * Math.cos(end)} ${130 + 112 * Math.sin(end)} Z`, fill: option.color,
            });
            slice.setAttribute("class", "analysis-pie-slice");
            slice.append(svgNode("title", {}, `${option.label}: ${count} (${(share * 100).toFixed(1)}%)`));
            link.append(slice); pie.append(link); start = end;
            const row = node("tr"), heading = node("th"), label = chartLink(dimension, option.value, option.label);
            heading.scope = "row"; label.textContent = option.label;
            const caption = node("span", undefined, "analysis-assessment-label"), swatch = svgNode("svg", {viewBox: "0 0 12 12", width: 12, height: 12, class: "analysis-swatch", "aria-hidden": "true"});
            swatch.append(svgNode("rect", {width: 12, height: 12, fill: option.color})); caption.append(swatch, label); heading.append(caption);
            const statistics = combineCells(selected.filter(cell => cell[dimension] === option.value));
            const maximum = Math.max(1, ...statistics.density.map(point => point[0]));
            const mini = svgNode("svg", {viewBox:"-10 0 1020 60", preserveAspectRatio:"none", class:"analysis-mini-violin", "data-analysis-violin":option.value, role:"img", "aria-label":`${option.label}: ${format(statistics.quantiles[2])}`});
            mini.append(svgNode("title", {}, `${statistics.measured}/${statistics.pages} · ${format(statistics.quantiles[2])}`), svgNode("line", {x1:0,x2:1000,y1:30,y2:30,class:"analysis-violin-track"}));
            if (statistics.density.length) {
                const upper = statistics.density.map(([x,y]) => `${x / maximum * 1000} ${30 - y * 24}`), lower = [...statistics.density].reverse().map(([x,y]) => `${x / maximum * 1000} ${30 + y * 24}`);
                mini.append(svgNode("path", {d:`M ${[...upper,...lower].join(" L ")} Z`,fill:option.color,class:"analysis-violin-density"}));
                const median = statistics.quantiles[2] / maximum * 1000;
                mini.append(svgNode("line", {x1:median,x2:median,y1:17,y2:43,class:"analysis-violin-median"}));
            }
            const miniCell = node("td", undefined, "analysis-mini-cell"), miniLink = chartLink(dimension, option.value, option.label);
            miniLink.dataset.chartMeasured = "1"; miniLink.append(mini); miniCell.append(miniLink);
            const [open, close] = document.documentElement.lang.startsWith("zh") ? ["（", "）"] : [" (", ")"];
            row.append(heading, node("td", `${format(count)}${open}${(share * 100).toFixed(1)}%${close}`, "analysis-number"), miniCell);
            body.append(row);
        }
    }

    function renderYears(selected) {
        if (!yearSvg || !yearTable) return;
        const years = [];
        for (let year = Math.max(2009, Number(from.value)); year <= Number(to.value); year++) {
            const cells = selected.filter(cell => cell.year === year);
            years.push({year, pages: cells.reduce((sum, cell) => sum + cell.pages, 0), grades: grades.map(grade => cells.filter(cell => cell.grade === grade.value).reduce((sum, cell) => sum + cell.pages, 0))});
        }
        const step = Math.max(1, Math.ceil(Math.max(0, ...years.map(year => year.pages)) / 4));
        const width = Math.max(600, 72 + years.length * 46), slot = (width - 64) / Math.max(1, years.length);
        yearSvg.setAttribute("viewBox", `0 0 ${width} 300`); yearSvg.setAttribute("width", width);
        yearSvg.querySelectorAll("g, line, text").forEach(element => element.remove());
        for (let index = 0; index <= 4; index++) {
            yearSvg.append(svgNode("line", {x1: 48, x2: width - 16, y1: 248 - index * 55, y2: 248 - index * 55, class: "analysis-year-grid"}), svgNode("text", {x: 38, y: 248 - index * 55, "text-anchor": "end", "dominant-baseline": "middle", class: "analysis-axis-label"}, format(index * step)));
        }
        const head = node("tr"); head.append(node("th", yearLabel));
        for (const grade of grades) head.append(node("th", grade.label));
        head.append(node("th", totalLabel, "analysis-total-column")); yearTable.tHead.replaceChildren(head); yearTable.tBodies[0].replaceChildren();
        for (const [index, year] of years.entries()) {
            const x = 48 + (index + .5) * slot, group = svgNode("g", {"data-analysis-year": year.year, "aria-label": `${year.year}: ${year.pages}`});
            let offset = 0;
            for (const [position, grade] of grades.entries()) {
                const count = year.grades[position], height = count / (step * 4) * 220;
                if (count) {
                    const link = chartLink("grade", grade.value, `${year.year} · ${grade.label}`, year.year, true);
                    const rectangle = svgNode("rect", {x: x - 16, y: 248 - offset - height, width: 32, height, fill: grade.color});
                    rectangle.append(svgNode("title", {}, `${year.year} · ${grade.label}: ${count}`)); link.append(rectangle); group.append(link);
                }
                offset += height;
            }
            group.append(svgNode("text", {x, y: 242 - offset, "text-anchor": "middle", class: "analysis-column-total"}, format(year.pages)), svgNode("text", {x, y: 270, "text-anchor": "middle", class: "analysis-axis-label"}, year.year)); yearSvg.append(group);
            const row = node("tr"); row.append(node("th", year.year));
            for (const count of year.grades) row.append(node("td", count));
            row.append(node("td", format(year.pages), "analysis-total-column")); yearTable.tBodies[0].append(row);
        }
    }

    function render() {
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
        for (const row of document.querySelectorAll("[data-matrix-page]")) row.dataset.cohort = ids.has(row.dataset.matrixPage) ? "1" : "0";
        for (const row of authorRows) {
            const link = row.cells[2].querySelector("a"), dialog = document.getElementById(link.getAttribute("aria-controls"));
            const count = [...dialog.querySelectorAll("[data-matrix-page]")].filter(item => item.dataset.cohort !== "0").length;
            row.dataset.cohort = count ? "1" : "0"; row.cells[2].dataset.sort = String(count); link.textContent = authorCounts.get(row).replace("{count}", String(count));
        }
        for (const [input, matches] of parents) {
            const children = inputs.filter(matches), checked = children.filter(item => item.checked).length;
            input.checked = checked === children.length; input.indeterminate = checked > 0 && checked < children.length;
        }
        const total = selected.reduce((sum, cell) => sum + cell.pages, 0);
        const totalNode = document.querySelector("[data-matrix-total]"); if (totalNode) totalNode.textContent = format(total);
        renderPie("grade", grades, selected, total); renderPie("importance", importance, selected, total); renderYears(selected);
        document.dispatchEvent(new Event("quality-cohort-change"));
    }
    for (const [input, matches] of parents) input.addEventListener("change", () => {inputs.filter(matches).forEach(child => {child.checked = input.checked;}); render();});
    for (const input of [...inputs, from, to, unknown]) input.addEventListener("change", render);
    form.addEventListener("submit", event => event.preventDefault());
    render();
}
