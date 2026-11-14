/* Exercise the authored userscript against native source-editor controls. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../src/webservice/public_html/consistency-repair.user.js"), "utf8");
const id = "a".repeat(64);
const original = "{{WikiProject Video games|class=GA}}\n<!-- keep -->\n{{Article history|currentstatus=GA}}\n";
const fixed = original.replace("|currentstatus=GA", "|currentstatus=GA|maindateGA=2026-11-02");
const payload = () => ({schema: 1, repair: {
  id, title: "Talk:Game", revision_id: 201, talk_page_id: 2,
  timestamp: "2026-11-02T14:00:00+00:00", original_text: original,
  text: fixed, edit_summary: "Update recorded homepage appearances.",
}});

async function run(options = {}) {
  const calls = {requests: [], diff: 0, preview: 0, save: 0, changes: [], notices: [], observers: 0, disconnected: 0};
  const location = new URL(options.url || `https://zh.wikipedia.org/w/index.php?title=Talk%3AGame&action=edit&section=0&aranami-check-job=${id}`);
  const fields = {wpSection: {value: "0"}, editRevId: {value: "201"}, wpEdittime: {value: "20261102140000"}};
  const form = {action: `https://zh.wikipedia.org/w/index.php?title=Talk%3AGame&action=submit&aranami-check-job=${id}`,
    elements: {namedItem: name => fields[name]}, prepend: notice => calls.notices.push(notice)};
  const control = value => ({form, value, dispatchEvent: event => calls.changes.push(event.type)});
  const controls = {
    editform: form, wpTextbox1: control(options.initialText ?? original), wpSummary: control(options.initialSummary ?? "/* */ "),
    wpDiff: {form, disabled: false, click: () => {calls.diff++;}},
    wpPreview: {form, click: () => {calls.preview++;}},
    wpSave: {form, click: () => {calls.save++;}},
  };
  Object.assign(fields, options.fields || {});
  if (options.formAction) form.action = options.formAction;
  if (options.wrongForm) controls.wpTextbox1.form = {};
  if (options.disabledDiff) controls.wpDiff.disabled = true;
  const config = {wgNamespaceNumber: 1, wgPageName: "Talk:Game", wgCurRevisionId: 201, wgArticleId: 2, wgUserLanguage: "en", ...options.config};
  let effectiveText = controls.wpTextbox1.value;
  const jquery = textbox => ({textSelection: (action, value) => {
    if (action === "getContents") return effectiveText;
    if (action === "setContents") {effectiveText = value; textbox.value = value;}
  }});
  jquery.fn = {textSelection() {}};
  const document = {
    documentElement: {dataset: {}, lang: "en"},
    body: {prepend: notice => calls.notices.push(notice)},
    getElementById: name => options.missingEditor ? null : controls[name],
    createElement: () => ({setAttribute() {}, textContent: ""}),
  };
  const context = {
    URL, TextEncoder, Event, location, document, window: {},
    history: {state: null, replaceState: (_state, _title, url) => {calls.replaced = url;}},
    unsafeWindow: {mw: {config: {get: key => config[key]}, loader: {using: async () => {}}}, ...(options.textSelection ? {jQuery: jquery} : {})},
    GM_xmlhttpRequest(request) {
      calls.requests.push(request);
      options.beforeResponse?.({controls, fields, config});
      if (options.requestFailure) request[options.requestFailure]();
      else request.onload({status: options.status ?? 200, response: options.payload ?? payload()});
    },
    MutationObserver: class {
      observe() {calls.observers++;}
      disconnect() {calls.disconnected++;}
    },
    setTimeout: callback => setImmediate(callback), clearTimeout: clearImmediate,
  };
  vm.runInNewContext(source, context);
  for (let turn = 0; turn < 5; turn++) await new Promise(resolve => setImmediate(resolve));
  return {calls, controls, fields, config};
}

test("only explicit current talk-page section-zero links activate", async () => {
  for (const tail of ["", `?title=Talk%3AGame&action=edit&section=1&aranami-check-job=${id}`,
    `?title=Talk%3AGame&action=edit&section=0&oldid=201&aranami-check-job=${id}`,
    `?title=Talk%3AGame&action=edit&section=0&aranami-check-job=${id}&aranami-check-job=${id}`,
    "?title=Talk%3AGame&action=edit&section=0&aranami-check-job=untrusted"]) {
    const {calls} = await run({url: "https://zh.wikipedia.org/w/index.php" + tail});
    assert.equal(calls.requests.length, 0);
    assert.equal(calls.preview, 0);
  }
  for (const options of [{config: {wgNamespaceNumber: 0}}, {missingEditor: true}, {wrongForm: true},
    {disabledDiff: true}, {fields: {wpSection: {value: "1"}}}]) {
    const {calls} = await run(options);
    assert.equal(calls.requests.length, 0);
    assert.equal(calls.preview, 0);
    assert.equal(calls.diff + calls.save, 0);
  }
});

test("ready repair fills complete code and summary, and opens only the native diff", async () => {
  for (const textSelection of [false, true]) {
    const {calls, controls, fields} = await run({textSelection});
    assert.equal(calls.requests.length, 1);
    assert.equal(calls.requests[0].url, `https://aranami.toolforge.org/api/consistency-repair?repair=${id}`);
    assert.equal(calls.requests[0].method, "GET");
    assert.equal(calls.requests[0].anonymous, true);
    assert.equal(controls.wpTextbox1.value, fixed);
    assert.equal(controls.wpSummary.value, payload().repair.edit_summary);
    assert.equal(fields.wpSection.value, "0");
    assert.equal(fields.editRevId.value, "201");
    assert.deepEqual(calls.changes, ["input", "change", "input", "change"]);
    assert.equal(calls.preview, 0);
    assert.equal(calls.diff, 1);
    assert.equal(calls.save, 0);
    assert.equal(new URL(calls.replaced).searchParams.has("aranami-check-job"), false);
    assert.equal(new URL(controls.editform.action).searchParams.has("aranami-check-job"), false);
  }
});

test("saved source guards reject stale heads, wrong destinations and modified drafts", async () => {
  for (const options of [
    {fields: {editRevId: {value: "202"}}},
    {fields: {wpEdittime: {value: "20261102150000"}}},
    {config: {wgCurRevisionId: 202}}, {config: {wgArticleId: 3}},
    {config: {wgPageName: "Talk:Other"}}, {initialText: original + "draft"},
    {initialText: original + " "}, {initialText: original + "\n\n"},
    {initialSummary: "My unfinished edit"},
    {beforeResponse: ({controls}) => {controls.wpTextbox1.value += "draft";}},
    {beforeResponse: ({controls}) => {controls.wpSummary.value = "draft";}},
  ]) {
    const {calls, controls} = await run(options);
    assert.equal(calls.changes.length, 0);
    assert.equal(calls.preview, 0);
    assert.equal(calls.diff + calls.save, 0);
    assert.notEqual(controls.wpTextbox1.value, fixed);
  }
});

test("only CRLF and one native appended newline are source equivalents", async () => {
  for (const initialText of [original.replaceAll("\n", "\r\n"), original + "\n"]) {
    const {calls, controls} = await run({initialText});
    assert.equal(calls.diff, 1);
    assert.equal(controls.wpTextbox1.value, fixed);
  }
});

test("invalid saved API responses and transport failures cannot fill or submit", async () => {
  const invalid = [null, {}, {...payload(), schema: 2}, ...[
    {id: "b".repeat(64)}, {revision_id: "201"}, {talk_page_id: 0}, {title: "Talk:Other"},
    {timestamp: "bad"}, {text: null}, {text: ""}, {text: original}, {original_text: "wrong"}, {edit_summary: ""},
    {edit_summary: "x".repeat(256)}, {text: "x".repeat(131073)},
  ].map(repair => ({schema: 1, repair: {...payload().repair, ...repair}}))];
  for (const value of invalid) {
    const {calls, controls} = await run({payload: value || {schema: 1}});
    assert.equal(calls.changes.length, 0);
    assert.equal(calls.preview, 0);
    assert.equal(calls.diff + calls.save, 0);
    assert.equal(controls.wpTextbox1.value, original);
  }
  for (const options of [{status: 404}, {requestFailure: "onerror"}, {requestFailure: "ontimeout"}, {requestFailure: "onabort"}]) {
    const {calls} = await run(options);
    assert.equal(calls.changes.length + calls.preview + calls.diff + calls.save, 0);
  }
});

test("the source has no automatic page-preview or save control", () => {
  assert.equal(source.includes("@version      0.9.4"), true);
  assert.equal(source.includes("const mw = window.mw;"), true);
  assert.equal(source.includes("wpDiff"), true);
  assert.equal(source.includes("wpPreview"), false);
  assert.equal(source.includes("wpSave"), false);
});

test("pretty talk URLs fetch the saved job and show the entire lead diff", async () => {
  for (const title of ["Talk:Game", "Talk:海岛大亨系列", "讨论:海岛大亨系列", "討論:海岛大亨系列"]) {
    const repair = {...payload().repair, title: title.replace(/^[^:]+:/, "Talk:")};
    const {calls, controls} = await run({
      url: `https://zh.wikipedia.org/wiki/${encodeURIComponent(title)}?action=edit&section=0&aranami-check-job=${id}`,
      config: {wgPageName: repair.title},
      formAction: `https://zh.wikipedia.org/w/index.php?title=${encodeURIComponent(title)}&action=submit`,
      payload: {schema: 1, repair},
    });
    assert.equal(calls.requests.length, 1);
    assert.equal(calls.requests[0].url, `https://aranami.toolforge.org/api/consistency-repair?repair=${id}`);
    assert.equal(controls.wpTextbox1.value, fixed);
    assert.equal(calls.diff, 1);
    assert.equal(calls.preview + calls.save, 0);
  }
});

test("URL titles cannot redirect a saved correction to a different page", async () => {
  for (const options of [
    {url: `https://zh.wikipedia.org/wiki/Talk:Other?action=edit&section=0&aranami-check-job=${id}`},
    {url: `https://zh.wikipedia.org/wiki/Talk:Game?title=Talk:Other&action=edit&section=0&aranami-check-job=${id}`},
    {url: `https://zh.wikipedia.org/wiki/Talk:%E0%A4?action=edit&section=0&aranami-check-job=${id}`},
    {url: `https://zh.wikipedia.org/w/index.php?title=Talk:Game&title=Talk:Other&action=edit&section=0&aranami-check-job=${id}`},
    {formAction: "https://zh.wikipedia.org/w/index.php?title=Talk:Other&action=submit"},
  ]) {
    const {calls, controls} = await run(options);
    assert.equal(controls.wpTextbox1.value, original);
    assert.equal(calls.changes.length + calls.diff + calls.preview + calls.save, 0);
  }
});
