/** Filter and sample saved quality records locally without reloading the dashboard. */
(() => {
  "use strict";

  /** Identify the same recognition consistently across filtering and sampling. */
  function qualityIdentity(item) {
    return JSON.stringify([item.item, item.site, item.status]);
  }

  /** Recognize a known Chinese counterpart or the Chinese article itself. */
  function qualityHasLocal(item) {
    return item.language === "zh" || (
      typeof item.zh_title === "string" && item.zh_title !== "" &&
      typeof item.zh_url === "string" && item.zh_url !== ""
    );
  }

  /** Select a complete filtered pool with one copy of each recognition. */
  function qualityPool(items, language = "", status = "", local = "") {
    const seen = new Set();
    return items.filter((item) => {
      if ((language && item.language !== language) || (status && item.status !== status)) return false;
      if (local === "exists" && !qualityHasLocal(item)) return false;
      if (local === "missing" && qualityHasLocal(item)) return false;
      const identity = qualityIdentity(item);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    });
  }

  /** Sample without replacement while leaving the complete source pool intact. */
  function randomQualityItems(items, limit = 10, random = Math.random) {
    const pool = qualityPool(items);
    const count = Math.min(Math.max(0, limit), pool.length);
    for (let index = 0; index < count; index += 1) {
      const selected = index + Math.floor(random() * (pool.length - index));
      [pool[index], pool[selected]] = [pool[selected], pool[index]];
    }
    return pool.slice(0, count);
  }

  /** Distinguish the initial recommendations from an explicit complete collection. */
  function qualityViewItems(data, state) {
    const pool = qualityPool(data.items, state.language, state.status, state.local);
    if (state.all || state.local === "exists" || state.local === "missing") return pool;
    if (state.random) return state.sample || randomQualityItems(pool);
    return state.language ? pool : data.defaults[state.status] || [];
  }

  // Keep the selection rules directly testable with Node's built-in test runner.
  if (typeof module === "object" && module.exports) {
    module.exports = { qualityIdentity, qualityHasLocal, qualityPool, randomQualityItems, qualityViewItems };
    return;
  }

  const panel = document.querySelector("#quality");
  const form = panel?.querySelector("[data-quality-actions]");
  const dataElement = panel?.querySelector("#quality-data");
  if (!form || !dataElement) return;

  let data;
  try {
    data = JSON.parse(dataElement.textContent);
  } catch {
    return;
  }
  const select = form.querySelector("select[name=language]");
  const localSelect = form.querySelector("select[name=local]");
  const table = panel.querySelector("[data-quality-table]");
  const body = table?.tBodies[0];
  const summary = panel.querySelector("[data-quality-label]");
  const empty = panel.querySelector("[data-quality-empty]");
  const scroll = panel.querySelector("[data-quality-scroll]");
  const pagination = panel.querySelector("[data-quality-pagination]");
  const progress = panel.querySelector("[data-quality-progress]");
  const more = panel.querySelector("[data-quality-show-more]");
  const all = form.querySelector("[data-quality-all]");
  if (!select || !localSelect || !body || !summary || !empty || !scroll || !pagination || !progress || !more || !all) return;

  const pageSize = 25;
  const numbers = new Intl.NumberFormat(document.documentElement.lang || "en");
  const initialPath = window.location.pathname;
  let state;
  let records = [];
  let shown = 0;

  /** Fill localized messages using plain text rather than HTML interpolation. */
  function message(template, values) {
    return Object.entries(values).reduce(
      (text, [key, value]) => text.replaceAll(`{${key}}`, value), template,
    );
  }

  /** Create an element whose supplied content is always plain text. */
  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }

  /** Render a linked title with its full value retained in a tooltip. */
  function titleLink(title, url, language) {
    const link = element("a");
    link.href = url;
    link.title = title;
    const text = element("bdi", "", title);
    text.lang = language;
    link.append(text);
    return link;
  }

  /** Render one validated record with the server's translated recognition and grade. */
  function row(item) {
    const result = element("tr");
    const language = data.languages[item.language] || item.language;
    const languageCell = element("td", "quality-language", language);
    languageCell.title = message(data.messages["quality.language_label"], { language, code: item.language });
    const english = document.documentElement.lang === "en";
    languageCell.append(element("span", "language-code", english ? ` (${item.language})` : `（${item.language}）`));
    const foreign = element("th");
    foreign.scope = "row";
    const title = element("span", "article-summary quality-article");
    title.append(titleLink(item.title, item.url, item.language));
    const badge = element("span", `recognition recognition-${item.status} assessment-chip`, item.label);
    badge.title = item.note;
    title.append(badge);
    foreign.append(title);
    const local = element("td");
    if (item.zh_title !== null && item.zh_url !== null) {
      const companion = element("span", "article-summary chinese-companion");
      companion.append(titleLink(item.zh_title, item.zh_url, "zh"));
      if (item.grade !== null) {
        const grade = element("span", "companion-grade assessment-chip", item.grade);
        grade.dataset.assessment = "class";
        grade.title = item.grade;
        companion.append(grade);
      }
      local.append(companion);
    } else {
      local.append(element("span", "companion-missing", data.messages["quality.companion_missing"]));
    }
    const identity = element("td", "quality-item");
    const itemLink = element("a", "", item.item);
    itemLink.href = `https://www.wikidata.org/wiki/${item.item}`;
    itemLink.title = item.item;
    identity.append(itemLink);
    result.append(languageCell, foreign, local, identity);
    return result;
  }

  /** Append only the next visible batch and update its accessible progress text. */
  function reveal() {
    const fragment = document.createDocumentFragment();
    const next = Math.min(shown + pageSize, records.length);
    records.slice(shown, next).forEach((item) => fragment.append(row(item)));
    const firstLink = fragment.querySelector("a");
    body.append(fragment);
    shown = next;
    progress.textContent = message(progress.dataset.template, {
      shown: numbers.format(shown), total: numbers.format(records.length),
    });
    more.hidden = shown >= records.length;
    more.textContent = message(more.dataset.template, {
      count: numbers.format(Math.min(pageSize, records.length - shown)),
    });
    if (more.hidden && document.activeElement === more) firstLink?.focus({ preventScroll: true });
  }

  /** Read supported selections from the URL for reloads and browser navigation. */
  function urlState() {
    const url = new URL(window.location.href);
    const language = url.searchParams.get("language") || "";
    const status = url.searchParams.get("status") || "";
    const requestedLocal = url.searchParams.get("local") || "";
    const local = ["exists", "missing"].includes(requestedLocal) ? requestedLocal : "";
    return {
      language: Object.hasOwn(data.languages, language) ? language : "",
      status: Object.hasOwn(data.statusLabels, status) ? status : "",
      local,
      all: url.searchParams.get("all") === "1",
      random: !local && url.searchParams.get("all") !== "1" && url.searchParams.get("random") === "1",
    };
  }

  /** Replace only the quality rows, retaining the language and scroll position. */
  function render(next, pushHistory = false) {
    state = next;
    select.value = state.language;
    localSelect.value = state.local || "";
    const statusInput = form.querySelector("input[name=status]");
    if (statusInput) statusInput.value = state.status;
    records = qualityViewItems(data, state);
    if (state.random) state.sample = records;
    body.replaceChildren();
    shown = 0;
    reveal();
    scroll.hidden = records.length === 0;
    empty.hidden = records.length !== 0;
    pagination.hidden = (!state.language && !state.all && !state.local) || state.random || records.length === 0;
    summary.hidden = !state.language;
    const language = data.languages[state.language] || state.language;
    summary.textContent = message(data.messages[state.status ? "quality.sample_status" : "quality.sample"], {
      language, status: data.statusLabels[state.status] || "", count: numbers.format(records.length),
    });
    table.setAttribute("aria-label", state.language
      ? message(data.messages["quality.table_aria"], { language }) : data.messages["quality.all_aria"]);

    const url = new URL(window.location.href);
    ["language", "status", "local", "random", "all"].forEach((key) => url.searchParams.delete(key));
    if (state.language) url.searchParams.set("language", state.language);
    if (state.status) url.searchParams.set("status", state.status);
    if (state.local) url.searchParams.set("local", state.local);
    if (state.random) url.searchParams.set("random", "1");
    if (state.all) url.searchParams.set("all", "1");
    const allUrl = new URL(window.location.pathname, window.location.origin);
    allUrl.searchParams.set("all", "1");
    if (state.status) allUrl.searchParams.set("status", state.status);
    allUrl.hash = "quality";
    all.href = allUrl.href;
    if (pushHistory) url.hash = "quality";
    const history = { ...window.history.state, aranamiQuality: state };
    if (pushHistory) window.history.pushState(history, "", url);
    else window.history.replaceState(history, "", url);
  }

  const initial = urlState();
  if (initial.random) initial.sample = data.sample;
  render(initial);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    render({ language: select.value, local: localSelect.value, status: state.status, random: false, all: true }, true);
  });
  select.addEventListener("change", () => {
    render({ language: select.value, local: localSelect.value, status: state.status, random: false, all: true }, true);
  });
  localSelect.addEventListener("change", () => {
    render({ language: select.value, local: localSelect.value, status: state.status, random: false, all: true }, true);
  });
  more.addEventListener("click", reveal);
  all.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    render({ language: "", local: "", status: state.status, random: false, all: true }, true);
  });
  window.addEventListener("popstate", (event) => {
    if (window.location.pathname === initialPath) render(event.state?.aranamiQuality || urlState());
  });
})();
