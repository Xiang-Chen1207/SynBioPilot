/* HDAC2 demo contracts. Synthetic transport and DOM only; no server or research. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const rootPath = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(rootPath, "scientist/web/index.html"), "utf8");
const source = fs.readFileSync(path.join(rootPath, "scientist/web/hdac2-demo.js"), "utf8");
const contracts = require("../scientist/web/hdac2-demo.js");
let checks = 0;
async function check(name, action) {await action(); checks++; process.stdout.write("PASS " + name + "\n");}
const copy = value => JSON.parse(JSON.stringify(value));
function deferred() {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};}

class Element {
  constructor(tag, attributes = "") {
    this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.dataset = {};
    this.listeners = new Map(); this.hidden = false; this.disabled = false; this.textContent = ""; this._value = "";
    this.classList = {toggle: (key, active) => {this.attributes["class:" + key] = active;}};
    const value = attributes.match(/\bvalue="([^"]*)"/); if (value) this._value = value[1];
  }
  get value() {return this._value;}
  set value(value) {this._value = value;}
  append(...children) {this.children.push(...children); if (this.tagName === "SELECT" && !this._value) this._value = this.children[0]?.value || "";}
  replaceChildren(...children) {this.children = []; if (this.tagName === "SELECT") this._value = ""; this.append(...children);}
  setAttribute(key, value) {this.attributes[key] = value;}
  addEventListener(name, action) {if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(action);}
  async fire(name) {for (const action of this.listeners.get(name) || []) await action({preventDefault() {}});}
  click() {this.clicked = true;}
}
function environment(protocol = "http:", handler = () => {throw new Error("Unexpected fetch");}) {
  const elements = {};
  for (const match of html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) elements[match[3]] = new Element(match[1], match[2]);
  elements.demoResultFilter.value = "all";
  const created = [], calls = [];
  const root = {document: {
    getElementById(id) {return elements[id] || null;},
    createElement(tag) {const element = new Element(tag); created.push(element); return element;},
  }, location: {protocol}, fetch: async (url, options) => {calls.push({url, options}); return handler(url, options);}};
  const sandbox = {window: root, TextEncoder, URL, Blob, setTimeout};
  vm.createContext(sandbox); vm.runInContext(source, sandbox);
  return {root, elements, created, calls};
}
function response(value, ok = true) {return {ok, status: ok ? 200 : 400, json: async () => copy(value)};}
const catalog = {
  schema_version: 1, id: "hdac2_h20", target: {gene: "HDAC2", pdb_id: "4LXZ", fixed_target: true},
  source: {kind: "repository_report", repository: "SYNTHETIC repository", commit: "SYNTHETIC", readme_url: "https://example.invalid/SYNTHETIC", usage_url: "https://example.invalid/SYNTHETIC-usage"},
  local_execution: {status: "not_run", executed: false},
  models: [{id: "DrugCLIP", label: "DrugCLIP", execution_mode: "SYNTHETIC cached scoring", score_scope: "within_model_only"}],
  scenarios: ["common50", "capacity1000"].map(id => ({id, label: id, molecule_count: id === "common50" ? 50 : 1000, selection_note: "SYNTHETIC source report",
    reported_benchmark: {source_kind: "repository_report", scope: "four_model_aggregate", successful: 4, attempted: 4,
      online_seconds: 0.01, preparation_seconds: 1, verified_locally: false, partial_cache_reuse: true,
      online_timing_excludes: ["model_loading", "encoding", "conformer_generation", "warmup"]}})),
  resources: [], limitations: ["SYNTHETIC limitation"],
};
function analysis(rows = [{row_number: 2, model: "DrugCLIP", compound_id: "SYNTHETIC_A", status: "ok", score: 0.8, rank: 1, error: null, error_redacted: false}]) {
  return {schema_version: 1, demo_id: "hdac2_h20", status: "ok", accepted: rows.length, rejected: 0,
    provenance: {kind: "user_imported_not_verified", verified: false}, local_execution: {status: "not_run", executed: false},
    summary: {rows_observed: rows.length, successful: rows.filter(row => row.status === "ok").length, failed: rows.filter(row => row.status === "failed").length, models_observed: 1},
    by_model: [{model: "DrugCLIP", observed: rows.length, accepted: rows.length, rejected: 0, successful: rows.filter(row => row.status === "ok").length,
      failed: rows.filter(row => row.status === "failed").length, rows, displayed: rows.length, omitted: 0}], validation_issues: [], validation_issue_count: 0, issues_truncated: false};
}
function textOf(element) {return [element.textContent, ...element.children.map(textOf)].join(" ");}

(async () => {
  await check("demo navigation and script order are additive", () => {
    assert.match(html, /data-view="demo"/); assert.match(html, /id="view-demo" data-section="demo"/);
    assert.ok(html.indexOf('src="hdac2-demo.js"') < html.indexOf('src="app.js"'));
    for (const id of ["view-research", "view-collaboration", "view-experiments", "experimentFile", "researchForm"]) assert.match(html, new RegExp('id="' + id + '"'));
  });
  await check("all mapped fields have explicit labels and bounded input", () => {
    for (const id of ["demoFile", "demoCsvText", "demoColumnModel", "demoColumnCompound", "demoColumnScore", "demoColumnStatus", "demoColumnError", "demoResultModel", "demoResultFilter"]) assert.match(html, new RegExp('for="' + id + '"'));
    assert.match(html, /id="demoCsvText"[^>]+maxlength="500000"/);
  });
  await check("CSV byte limit includes multibyte text", () => {
    assert.equal(contracts.inputIssue("model,compound_id\nSYNTHETIC,A\n"), "");
    assert.match(contracts.inputIssue("中".repeat(170000)), /exceeds/);
    assert.match(contracts.inputIssue(" "), /Choose a CSV file/);
    assert.equal(contracts.byteLength("中文"), 6);
  });
  await check("header suggestions preserve exact quoted names and stay bounded", () => {
    assert.deepEqual(contracts.csvHeaders('\uFEFF"model name","compound,identifier","a""b",status\nA,B,C,ok'), ["model name", "compound,identifier", 'a"b', "status"]);
    assert.deepEqual(contracts.csvHeaders("model,compound_id,score,status"), ["model", "compound_id", "score", "status"]);
    assert.equal(contracts.csvHeaders(Array.from({length: 70}, (_, index) => "field" + index).join(",")).length, 64);
  });
  await check("row filtering retains backend ranks without cross-model sorting", () => {
    const rows = [{model: "A", status: "ok", rank: 7, score: 0.1}, {model: "A", status: "failed", rank: null, score: null}];
    assert.deepEqual(contracts.visibleRows({rows}, "failed"), [rows[1]]);
    assert.equal(contracts.visibleRows({rows}, "ok")[0].rank, 7);
    assert.equal(contracts.visibleRows({rows: Array(130).fill(rows[0])}).length, 100);
  });
  await check("download report keeps prediction and unverified provenance", () => {
    const report = contracts.reportForDownload(analysis(), true);
    assert.equal(report.synthetic, true); assert.equal(report.local_execution.status, "not_run");
    assert.equal(report.provenance.kind, "user_imported_not_verified"); assert.equal(report.interpretation, "computational_prediction_only");
    assert.equal(report.provenance.target_association, "not_verified"); assert.equal(report.payload_scope, "bounded_api_preview");
    assert.match(report.export_note, /preview of at most 100 rows per model/);
    assert.match(report.export_note, /Use the original CSV for the complete score table/);
    assert.match(report.export_note, /verification status: pending/);
    assert.equal(report.analysis.by_model[0].model, "DrugCLIP"); assert.equal(report.csv_text, undefined);
  });
  await check("source and user strings never become HTML", () => {
    assert.doesNotMatch(source, /\.innerHTML|\.outerHTML|insertAdjacentHTML/);
    assert.match(source, /element\.textContent = str\(content\)/);
    assert.doesNotMatch(source, /\/api\/projects|\/runs|\/research|setInterval/);
  });
  await check("file preview remains disconnected without fixture fallback", async () => {
    const env = environment("file:"); await env.root.Hdac2Demo.load();
    assert.equal(env.calls.length, 0); assert.match(textOf(env.elements.demoConnection), /disconnected/);
    await env.elements.demoSynthetic.fire("click"); await env.elements.demoImportForm.fire("submit");
    assert.equal(env.calls.length, 0); assert.match(textOf(env.elements.demoImportError), /disconnected/);
    assert.equal(env.elements.demoDownload.disabled, true); assert.equal(env.elements.demoBenchmark.children.length, 0);
  });
  await check("synthetic example is submitted to the same API with explicit mapping", async () => {
    const env = environment("http:", (url, options) => response(options.method === "GET" ? catalog : analysis()));
    await env.root.Hdac2Demo.load(); await env.elements.demoSynthetic.fire("click");
    assert.equal(env.calls.length, 1); assert.match(env.elements.demoCsvText.value, /SYNTHETIC/);
    await env.elements.demoImportForm.fire("submit");
    const sent = JSON.parse(env.calls[1].options.body);
    assert.equal(env.calls[1].url, "/api/demos/hdac2_h20/inspect-scores");
    assert.equal(sent.columns.compound_id, "compound_id"); assert.equal(sent.csv_text, contracts.SYNTHETIC_CSV);
    assert.match(textOf(env.elements.demoResultCaption), /SYNTHETIC/); assert.equal(env.elements.demoDownload.disabled, false);
  });
  await check("repeated view loads preserve imported state and do not refetch", async () => {
    const env = environment("http:", (url, options) => response(options.method === "GET" ? catalog : analysis()));
    await env.root.Hdac2Demo.load(); await env.elements.demoSynthetic.fire("click"); await env.elements.demoImportForm.fire("submit");
    const results = env.elements.demoResults.children[0], input = env.elements.demoCsvText.value;
    await env.root.Hdac2Demo.load(); await env.root.Hdac2Demo.load();
    assert.equal(env.calls.length, 2); assert.equal(env.elements.demoCsvText.value, input);
    assert.equal(env.elements.demoResults.children[0], results); assert.equal(env.elements.demoDownload.disabled, false);
  });
  await check("changing column mapping preserves synthetic provenance", async () => {
    const env = environment("http:", (url, options) => response(options.method === "GET" ? catalog : analysis()));
    await env.root.Hdac2Demo.load(); await env.elements.demoSynthetic.fire("click");
    env.elements.demoColumnModel.value = "SYNTHETIC_mapped_model"; await env.elements.demoColumnModel.fire("input");
    await env.elements.demoImportForm.fire("submit");
    assert.match(textOf(env.elements.demoResultCaption), /SYNTHETIC/);
    assert.equal(JSON.parse(env.calls[1].options.body).columns.model, "SYNTHETIC_mapped_model");
  });
  await check("the first observed model is selected when other models are empty", async () => {
    const result = analysis(); result.by_model[0].model = "SPRINT"; result.by_model[0].rows[0].model = "SPRINT";
    result.by_model.unshift({model: "DrugCLIP", observed: 0, accepted: 0, rejected: 0, successful: 0, failed: 0, rows: [], omitted: 0});
    const env = environment("http:", (url, options) => response(options.method === "GET" ? catalog : result));
    await env.root.Hdac2Demo.load(); await env.elements.demoSynthetic.fire("click"); await env.elements.demoImportForm.fire("submit");
    assert.equal(env.elements.demoResultModel.value, "SPRINT"); assert.match(textOf(env.elements.demoResults), /SYNTHETIC_A/);
  });
  await check("an old analysis response cannot overwrite changed input", async () => {
    const pending = deferred();
    const env = environment("http:", (url, options) => options.method === "GET" ? response(catalog) : pending.promise);
    await env.root.Hdac2Demo.load(); await env.elements.demoSynthetic.fire("click");
    const attempt = env.elements.demoImportForm.fire("submit");
    env.elements.demoCsvText.value = "changed,SYNTHETIC"; await env.elements.demoCsvText.fire("input");
    pending.resolve(response(analysis())); await attempt;
    assert.equal(env.elements.demoDownload.disabled, true); assert.equal(env.elements.demoResultsControls.hidden, true);
    assert.doesNotMatch(textOf(env.elements.demoResultCaption), /user_imported/); assert.equal(env.elements.demoAnalyze.disabled, false);
  });
  await check("an older response cannot end the busy state of a newer analysis", async () => {
    const first = deferred(), second = deferred(); let postCount = 0;
    const env = environment("http:", (url, options) => options.method === "GET" ? response(catalog) : (++postCount === 1 ? first.promise : second.promise));
    await env.root.Hdac2Demo.load(); await env.elements.demoSynthetic.fire("click");
    const oldAttempt = env.elements.demoImportForm.fire("submit");
    await env.elements.demoColumnScore.fire("input");
    const newAttempt = env.elements.demoImportForm.fire("submit");
    first.resolve(response(analysis())); await oldAttempt;
    assert.equal(env.elements.demoAnalyze.disabled, true); assert.equal(env.elements.demoDownload.disabled, true);
    const latest = analysis(); latest.by_model[0].rows[0].compound_id = "SYNTHETIC_NEW";
    second.resolve(response(latest)); await newAttempt;
    assert.equal(env.elements.demoAnalyze.disabled, false); assert.match(textOf(env.elements.demoResults), /SYNTHETIC_NEW/);
    assert.doesNotMatch(textOf(env.elements.demoResults), /SYNTHETIC_A/);
  });
  await check("a late file read cannot overwrite a newer paste", async () => {
    const pending = deferred(), env = environment("file:"); await env.root.Hdac2Demo.load();
    env.elements.demoFile.files = [{name: "SYNTHETIC.csv", size: 200, text: () => pending.promise}];
    const reading = env.elements.demoFile.fire("change");
    env.elements.demoCsvText.value = "model,compound_id\nSYNTHETIC,new"; await env.elements.demoCsvText.fire("input");
    pending.resolve(contracts.SYNTHETIC_CSV); await reading;
    assert.equal(env.elements.demoCsvText.value, "model,compound_id\nSYNTHETIC,new"); assert.equal(env.elements.demoAnalyze.disabled, false);
  });
  await check("oversize files are rejected before reading", async () => {
    const env = environment("file:"); await env.root.Hdac2Demo.load(); let read = false;
    env.elements.demoFile.files = [{size: 500001, text() {read = true; return "SYNTHETIC";}}];
    await env.elements.demoFile.fire("change"); assert.equal(read, false); assert.match(env.elements.demoImportError.textContent, /limit/);
  });
  await check("rendered rows are capped and suspicious source text is literal", async () => {
    const rows = Array.from({length: 140}, (_, index) => ({row_number: index + 2, model: "DrugCLIP", compound_id: '<img src=x onerror="SYNTHETIC">', status: index ? "ok" : "failed", score: index ? 0.8 : null, rank: index ? index : null, error: index ? null : "SYNTHETIC failure"}));
    const env = environment("http:", (url, options) => response(options.method === "GET" ? catalog : analysis(rows)));
    await env.root.Hdac2Demo.load(); await env.elements.demoSynthetic.fire("click"); await env.elements.demoImportForm.fire("submit");
    const table = env.created.find(element => element.tagName === "TBODY"); assert.equal(table.children.length, 100);
    assert.equal(table.children[0].children[1].textContent, '<img src=x onerror="SYNTHETIC">');
    env.elements.demoResultFilter.value = "failed"; await env.elements.demoResultFilter.fire("change");
    assert.match(textOf(env.elements.demoResults), /SYNTHETIC failure/);
  });
  await check("connection errors never populate source benchmark numbers", async () => {
    const env = environment("http:", () => {throw new Error("SYNTHETIC offline");}); await env.root.Hdac2Demo.load();
    assert.match(textOf(env.elements.demoConnection), /Cannot connect/); assert.equal(env.elements.demoBenchmark.children.length, 0);
    await env.root.Hdac2Demo.load(); assert.equal(env.calls.length, 1);
  });
  process.stdout.write(checks + " HDAC2 demo UI contracts passed\n");
})().catch(error => {process.stderr.write(error.stack + "\n"); process.exitCode = 1;});

