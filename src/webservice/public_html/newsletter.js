"use strict";

// Color common wikitext constructs without interpreting source as HTML.
function highlightWikitext(source) {
    const text = source.textContent;
    const fragment = document.createDocumentFragment();
    const syntax = /<!--[\s\S]*?(?:-->|$)|\[\[[^\n]*?(?:\]\]|$)|\{\{\{?[^{}\n|]*|\}\}\}?|^={2,6}[^\n]*={2,6}[ \t]*$|<\/?[A-Za-z][^>\n]*>|'{2,5}|[|=]/gm;
    let previous = 0;
    for (const match of text.matchAll(syntax)) {
        fragment.append(document.createTextNode(text.slice(previous, match.index)));
        const token = document.createElement("span");
        const value = match[0];
        let kind = "delimiter";
        if (value.startsWith("<!--")) kind = "comment";
        else if (value.startsWith("[[")) kind = "link";
        else if (value.startsWith("{{") || value.startsWith("}}")) kind = "template";
        else if (value.startsWith("<")) kind = "tag";
        else if (value.startsWith("'")) kind = "emphasis";
        else if (value.startsWith("==")) kind = "heading";
        token.className = `newsletter-token-${kind}`;
        token.textContent = value;
        fragment.append(token);
        previous = match.index + value.length;
    }
    fragment.append(document.createTextNode(text.slice(previous)));
    source.replaceChildren(fragment);
}

for (const source of document.querySelectorAll("[data-newsletter-wikitext]")) {
    highlightWikitext(source);
}

// Dates are UTC observations; use a Sunday-first calendar in every locale.
function initializeCalendar(calendar) {
    const input = document.getElementById("newsletter-data-date");
    const dates = JSON.parse(calendar.dataset.dates);
    const available = new Set(dates);
    const first = dates[0].slice(0, 7);
    const last = dates[dates.length - 1].slice(0, 7);
    const locale = document.documentElement.lang || "en";
    const selected = calendar.querySelector("[data-calendar-selected]");
    const monthLabel = calendar.querySelector("[data-calendar-month]");
    const previous = calendar.querySelector("[data-calendar-previous]");
    const next = calendar.querySelector("[data-calendar-next]");
    const weekdays = calendar.querySelector("[data-calendar-weekdays]");
    const days = calendar.querySelector("[data-calendar-days]");
    const label = new Intl.DateTimeFormat(locale, {month: "long", year: "numeric", timeZone: "UTC"});
    const weekday = new Intl.DateTimeFormat(locale, {weekday: "short", timeZone: "UTC"});
    let month = (input.value || dates[dates.length - 1]).slice(0, 7);

    const headings = document.createElement("tr");
    for (let index = 0; index < 7; index++) {
        const heading = document.createElement("th");
        heading.scope = "col";
        heading.textContent = weekday.format(new Date(Date.UTC(2026, 10, 1 + index)));
        headings.append(heading);
    }
    weekdays.append(headings);

    function render() {
        const start = new Date(`${month}-01T00:00:00Z`);
        const count = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
        monthLabel.textContent = label.format(start);
        previous.disabled = month <= first;
        next.disabled = month >= last;
        days.replaceChildren();
        let row;
        const length = Math.ceil((start.getUTCDay() + count) / 7) * 7;
        for (let index = 0; index < length; index++) {
            if (index % 7 === 0) {
                row = document.createElement("tr");
                days.append(row);
            }
            const cell = document.createElement("td");
            row.append(cell);
            const day = index - start.getUTCDay() + 1;
            if (day < 1 || day > count) continue;
            const value = `${month}-${String(day).padStart(2, "0")}`;
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = String(day);
            button.disabled = !available.has(value);
            button.setAttribute("aria-label", value);
            if (input.value === value) button.setAttribute("aria-pressed", "true");
            button.addEventListener("click", () => {
                input.value = value;
                selected.textContent = value;
                calendar.open = false;
                calendar.querySelector("summary").focus();
                render();
            });
            cell.append(button);
        }
    }

    function move(offset) {
        const value = new Date(`${month}-01T00:00:00Z`);
        value.setUTCMonth(value.getUTCMonth() + offset);
        month = value.toISOString().slice(0, 7);
        render();
    }
    previous.addEventListener("click", () => move(-1));
    next.addEventListener("click", () => move(1));
    calendar.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            calendar.open = false;
            calendar.querySelector("summary").focus();
        }
    });
    render();
    input.type = "hidden";
    input.hidden = true;
    calendar.hidden = false;
}

const calendar = document.querySelector("[data-newsletter-calendar]");
if (calendar) initializeCalendar(calendar);

// Copy literal wikitext, preserving the same text beneath the highlighting.
for (const button of document.querySelectorAll("[data-newsletter-copy]")) {
    const source = document.getElementById(button.dataset.newsletterCopy);
    const status = button.parentElement.querySelector("[data-newsletter-status]");
    if (!source || !status) continue;
    button.hidden = false;
    button.addEventListener("click", async () => {
        status.textContent = "";
        button.disabled = true;
        try {
            if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
            await navigator.clipboard.writeText(source.textContent);
            status.textContent = button.dataset.copySuccess;
        } catch {
            source.parentElement.focus();
            const range = document.createRange();
            range.selectNodeContents(source);
            const selection = document.getSelection();
            selection?.removeAllRanges();
            selection?.addRange(range);
            status.textContent = button.dataset.copyFailure;
        } finally {
            button.disabled = false;
        }
    });
}
