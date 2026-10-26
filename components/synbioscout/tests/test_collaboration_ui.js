/* Stage 01 buildless frontend contracts. No DOM, server, npm or research is invoked.
 * Run: /Users/SYNTHETIC/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node tests/test_collaboration_ui.js
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "scientist/web/app.js"), "utf8");
assert.match(source, /\ninit\(\);\s*$/);
const fixtures = JSON.parse(fs.readFileSync(path.join(root, "docs/serial_optimization/fixtures/collaboration_states.json"), "utf8"));
const sandbox = {
  document: new Proxy({}, {get() {throw new Error("Pure contract unexpectedly accessed the DOM");}}),
  location: {protocol: "file:", href: "file:///SYNTHETIC/index.html"},
  fetch() {throw new Error("Offline contracts must never fetch");},
};
vm.createContext(sandbox);
vm.runInContext(source.replace(/\ninit\(\);\s*$/, "") +
  "\nglobalThis.uiContracts = {collaborationModel, agentArtifacts, safeEventSummary, artifactTarget, syntheticRun, selectionCurrent, selectedEpoch, observationExport, publicResearchFlow, researchObservationCounts, publicBudget, publicBudgetFailure, publicProviderFailure, providerFailureFields, publicFailureRecords, failureDetailTitle, renderTimeline, publicEntityReference, publicToolPlan, publicToolObservation, publicIdentity, preserveView, candidateVisible, candidateAssessment, sourceScopeView, sourceScopeLabel, publicClaimRelation, publicCandidateAssessment, assessmentDetails, selectedBackgroundPackage, assessmentExecutionLabel, applyTaskIntent, publicTaskIntent, publicEvidenceDigest, api, state};", sandbox);
const ui = sandbox.uiContracts;
const normalize = value => JSON.parse(JSON.stringify(value));
const byScenario = Object.fromEntries(fixtures.runs.map(run => [run.presentation.fixture.scenario, run]));
let checks = 0;
function check(name, action) {action(); checks++; process.stdout.write("PASS " + name + "\n");}

check("historical runs have no invented collaboration", () => {
  assert.equal(ui.collaborationModel(null), null);
  assert.equal(ui.collaborationModel({status: "completed"}), null);
  assert.equal(ui.collaborationModel(byScenario.legacy), null);
  assert.equal(ui.collaborationModel({co_scientist: []}), null);
  assert.deepEqual(normalize(ui.agentArtifacts(byScenario.legacy, "generation")), {
    inputs: [], outputs: [], inputSummary: "No stage-specific input summary recorded for this role."});
});

check("terminal replay never treats saved running roles as active execution", () => {
  for (const status of ["completed", "partial", "failed", "cancelled"]) {
    const run = structuredClone(byScenario.running); run.status = status;
    const model = ui.collaborationModel(run);
    assert.equal(model.active, false);
    assert.equal(model.working.length, 0);
    assert.equal(model.agents.find(agent => agent.agent_id === "generation").status, "running");
    assert.equal(run.co_scientist.agents.find(agent => agent.agent_id === "generation").status, "running");
  }
});

check("parallel operation uses latest per-call status while role remains running", () => {
  const model = ui.collaborationModel(byScenario.running);
  assert.equal(model.active, true);
  assert.deepEqual(normalize(model.working.map(agent => agent.agent_id)), ["generation"]);
  const generation = model.operations.filter(event => event.agent_id === "generation" && event.kind === "llm");
  assert.equal(generation.length, 2);
  assert.equal(generation.find(event => event.operation_id === "llm_004").operation_status, "completed");
  assert.equal(generation.find(event => event.operation_id === "llm_004").status, "running");
  assert.equal(generation.find(event => event.operation_id === "llm_005").operation_status, "running");
  assert.equal(generation.filter(event => event.operation_status === "running").length, 1);
  const shuffled = structuredClone(byScenario.running);
  shuffled.co_scientist.events.reverse();
  assert.deepEqual(normalize(ui.collaborationModel(shuffled).operations), normalize(model.operations));
});

check("recorded handoffs deduplicate public references and do not guess old outputs", () => {
  const artifacts = ui.agentArtifacts(byScenario.completed, "generation");
  assert.deepEqual(normalize(artifacts.inputs), ["anchor", "e_synthetic_support", "e_synthetic_oppose"]);
  assert.deepEqual(normalize(artifacts.outputs), ["h_synthetic_1:v1", "h_synthetic_2:v1"]);
  assert.match(artifacts.inputSummary, /支持\/反证/);
  const old = structuredClone(byScenario.completed);
  for (const event of old.co_scientist.events) {delete event.input_artifact_ids; delete event.input_summary; delete event.artifact_ids;}
  assert.equal(ui.agentArtifacts(old, "generation").inputs.length, 0);
  assert.equal(ui.agentArtifacts(old, "generation").outputs.length, 0);
  assert.match(ui.agentArtifacts(old, "generation").inputSummary, /No stage-specific input summary recorded/);
  assert.equal(old.co_scientist.hypotheses.length, 3);
});

check("handoffs require an earlier producer and later consumer of the same reference", () => {
  const model = ui.collaborationModel(byScenario.completed);
  assert.ok(model.handoffs.some(link => link.from === "generation" && link.to === "reflection" && link.refs.includes("h_synthetic_1:v1")));
  assert.ok(model.handoffs.some(link => link.from === "reflection" && link.to === "evolution" && link.refs.includes("review:h_synthetic_1:v1")));
  assert.ok(model.handoffs.some(link => link.from === "evolution" && link.to === "meta_review" && link.refs.includes("h_synthetic_1:v2")));
  const old = structuredClone(byScenario.completed);
  for (const event of old.co_scientist.events) {delete event.input_artifact_ids; delete event.artifact_ids;}
  assert.equal(ui.collaborationModel(old).handoffs.length, 0);
  const future = {status: "completed", co_scientist: {events: [
    {sequence: 1, agent_id: "reflection", input_artifact_ids: ["h_future:v1"]},
    {sequence: 2, agent_id: "generation", artifact_ids: ["h_future:v1"]},
  ]}};
  assert.equal(ui.collaborationModel(future).handoffs.length, 0);
  future.co_scientist.events.push({sequence: 3, agent_id: "reflection", input_artifact_ids: ["h_future:v1", "h_future:v1"]});
  assert.deepEqual(normalize(ui.collaborationModel(future).handoffs), [{from: "generation", to: "reflection", refs: ["h_future:v1"]}]);
  future.co_scientist.events.push({sequence: 4, agent_id: "generation", input_artifact_ids: ["h_future:v1"]});
  assert.equal(ui.collaborationModel(future).handoffs.length, 1);
});

check("artifact navigation resolves saved versions, evidence, reviews and contracts", () => {
  const run = byScenario.completed;
  const expectations = {
    anchor: "anchor", s_synthetic_support: "source", e_synthetic_oppose: "evidence",
    "h_synthetic_1:v1": "hypothesis", "h_synthetic_1:v2": "hypothesis",
    "review:h_synthetic_1:v1": "hypothesis", "comparison:synthetic_pair": "comparison",
    "contract:h_synthetic_1:v2": "contract", "cluster:0": "cluster",
  };
  for (const [ref, kind] of Object.entries(expectations)) assert.equal(ui.artifactTarget(ref, run)?.kind, kind, ref);
  assert.equal(ui.artifactTarget("h_synthetic_1:v1", run).item.version, 1);
  assert.equal(ui.artifactTarget("h_synthetic_1:v2", run).item.version, 2);
  for (const ref of ["missing", "h_synthetic_1:v99", "review:h_synthetic_1:v2", "contract:h_synthetic_2:v1", "cluster:20"])
    assert.equal(ui.artifactTarget(ref, run), null, ref);
  const withoutVersion = structuredClone(run);
  withoutVersion.co_scientist.hypotheses = [];
  assert.equal(ui.artifactTarget("review:h_synthetic_1:v1", withoutVersion).kind, "review");
});

check("every synthetic handoff reference has a saved destination", () => {
  for (const run of fixtures.runs) for (const event of run.co_scientist?.events || []) {
    for (const ref of [...event.input_artifact_ids, ...event.artifact_ids])
      assert.ok(ui.artifactTarget(ref, run), run.run_id + ": " + ref);
  }
});

check("tool failures and skipped phases remain observations without fake outcomes", () => {
  const model = ui.collaborationModel(byScenario.partial);
  assert.equal(model.working.length, 0);
  assert.equal(model.operations.find(event => event.operation_id === "tool_000").operation_status, "empty");
  assert.equal(model.operations.find(event => event.operation_id === "tool_001").operation_status, "unavailable");
  assert.equal(model.operations.find(event => event.operation_id === "co_evolution").operation_status, "skipped");
  assert.equal(ui.agentArtifacts(byScenario.partial, "evolution").outputs.length, 0);
  assert.equal(byScenario.failed.co_scientist.hypotheses.length, 0);
});

check("safe summaries do not expand arbitrary provider payloads or prompt objects", () => {
  assert.equal(ui.safeEventSummary({summary: "公开动作摘要"}), "公开动作摘要");
  for (const summary of [undefined, null, "", "  ", {prompt: "PRIVATE_SENTINEL", stderr: "LOCAL_LOG_SENTINEL"}, ["PRIVATE_SENTINEL"]]) {
    const display = ui.safeEventSummary({summary});
    assert.match(display, /No public action summary recorded/);
    assert.doesNotMatch(display, /PRIVATE_SENTINEL|LOCAL_LOG_SENTINEL/);
  }
});

check("legacy diagnostic strings stay private and long public summaries are bounded", () => {
  for (const summary of ["Failed at /Users/SYNTHETIC/LOCAL_LOG_SENTINEL.txt", "Failed at /home/SYNTHETIC/log.txt",
    "Traceback: LOCAL_LOG_SENTINEL", "stderr: LOCAL_LOG_SENTINEL", "Authorization: Bearer PRIVATE_SENTINEL",
    "api_key=PRIVATE_SENTINEL", "raw_prompt=PRIVATE_SENTINEL", "C:\\SYNTHETIC\\LOCAL_LOG_SENTINEL.txt"]) {
    const display = ui.safeEventSummary({summary});
    assert.match(display, /not public in this workbench/);
    assert.doesNotMatch(display, /PRIVATE_SENTINEL|LOCAL_LOG_SENTINEL|\/Users\/|\/home\/|C:\\/);
  }
  const summary = "SYNTHETIC public observation ".repeat(100);
  const display = ui.safeEventSummary({summary});
  assert.ok(display.length < summary.length);
  assert.match(display, /public summary truncated/);
});

check("SYNTHETIC metadata remains explicit and real records are not relabelled", () => {
  for (const run of fixtures.runs) assert.equal(ui.syntheticRun(run), true);
  assert.equal(ui.syntheticRun({presentation: {derived_view: true}}), false);
  assert.equal(ui.syntheticRun(null), false);
  assert.equal(ui.syntheticRun({fixture: {synthetic: true}}), true);
  assert.equal(ui.syntheticRun({fixture: {synthetic: false}}), false);
});

check("observation export labels fixtures and excludes arbitrary private record fields", () => {
  const run = structuredClone(byScenario.completed);
  run.co_scientist.seed = {raw_prompt: "PRIVATE_SEED_SENTINEL"};
  run.co_scientist.events[0].raw_prompt = "PRIVATE_EVENT_SENTINEL";
  run.co_scientist.events[0].provider_payload = {stderr: "LOCAL_LOG_SENTINEL"};
  run.co_scientist.agents[0].secret = "PRIVATE_AGENT_SENTINEL";
  let exported, download = "", clicked = 0, revoked = "";
  sandbox.Blob = class {constructor(parts) {this.parts = parts;}};
  sandbox.URL = {
    createObjectURL(blob) {exported = JSON.parse(blob.parts.join("")); return "blob:SYNTHETIC-export";},
    revokeObjectURL(url) {revoked = url;},
  };
  sandbox.setTimeout = action => action();
  sandbox.document = {createElement(tag) {
    assert.equal(tag, "a");
    return {set download(value) {download = value;}, click() {clicked++;}};
  }};
  ui.observationExport(run);
  assert.equal(exported.kind, "public_collaboration_observation");
  assert.equal(exported.synthetic, true);
  assert.equal(exported.run_id, run.run_id);
  assert.equal(exported.events.length, run.co_scientist.events.length);
  assert.equal(exported.events[0].operation_id, "co_anchor");
  assert.doesNotMatch(JSON.stringify(exported), /PRIVATE_.*SENTINEL|LOCAL_LOG_SENTINEL|raw_prompt|provider_payload/);
  assert.equal(clicked, 1);
  assert.equal(download, run.run_id + "-collaboration.json");
  assert.equal(revoked, "blob:SYNTHETIC-export");
});

check("tool plan navigation retains unresolved candidates without selecting first", () => {
  const plan = ui.artifactTarget("plan_synthetic_ambiguous", byScenario.partial);
  assert.equal(plan.kind, "tool_plan");
  assert.equal(plan.item.identity.status, "ambiguous");
  assert.equal(plan.item.identity.selected_id, "");
  assert.equal(plan.item.identity.candidates.length, 2);
  assert.equal(ui.artifactTarget("plan_missing", byScenario.partial), null);
});

check("public tool export excludes prompts credentials provider objects and local diagnostics", () => {
  const record = {tool_name:"chembl", status:"invalid", call_id:"tool_003", plan_id:"plan_1", request_count:2, cache_hits:0,
    parameters:{resource:"activity", molecule_chembl_id:"CHEMBL1", raw_prompt:"PRIVATE_PARAM_SENTINEL", headers:{Authorization:"PRIVATE_AUTH"}},
    records:[{raw_prompt:"PRIVATE_ROW_SENTINEL", standard_units:"nM"}], provider_payload:{secret:"PRIVATE_PROVIDER_SENTINEL"},
    identity:{status:"ambiguous", selected_id:"", candidates:[{id:"CHEMBL1", raw_prompt:"PRIVATE_ID_SENTINEL"},{id:"CHEMBL2"}]},
    http_trace:[{endpoint:"https://www.ebi.ac.uk/chembl/api/data/activity.json", method:"GET", retry_index:1, http_status:500,
      parameters:{limit:1, token:"PRIVATE_TOKEN"}, headers:{Authorization:"PRIVATE_HEADER"}, stderr:"LOCAL_LOG_SENTINEL"}],
    warnings:["Shared request budget exhausted", "stderr: LOCAL_LOG_SENTINEL"]};
  const result = ui.publicToolObservation(record), serialized = JSON.stringify(result);
  assert.doesNotMatch(serialized, /PRIVATE_|LOCAL_LOG|raw_prompt|Authorization|provider_payload/);
  assert.equal(result.record_count, 1);
  assert.equal(result.request_count, 2);
  assert.equal(result.http_trace[0].retry_index, 1);
  assert.equal(result.identity.selected_id, "");
});

check("public plans retain gap origin and calls while dropping arbitrary intent fields", () => {
  const result = ui.publicToolPlan({plan_id:"plan_1", gap_id:"gap_1", origin:"model_gap", status:"unresolved", reason:"Need assay context",
    raw_prompt:"PRIVATE_PLAN_SENTINEL", request:{name:"chembl", arguments:{resource:"activity", molecule_name:"Example", url:"https://private.invalid", secret:"PRIVATE_ARGUMENT"}},
    call_ids:["tool_000"], hypothesis_ids:["h_synthetic_1"], identity:{status:"ambiguous", selected_id:""}});
  assert.equal(result.gap_id, "gap_1");
  assert.equal(result.origin, "model_gap");
  assert.deepEqual(Array.from(result.call_ids), ["tool_000"]);
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE_|private.invalid|raw_prompt|secret/);
});

check("legacy tool calls do not gain guessed identity or HTTP trace", () => {
  const result = ui.publicToolObservation({tool_name:"pubchem", status:"empty", records:[], sources:[]});
  assert.equal(result.identity.status, undefined);
  assert.equal(result.call_id, undefined);
  assert.equal(result.http_trace.length, 0);
});

check("poll redraw preserves file input, dynamic controls, stable focus and scroll", () => {
  let current, fileAssignments = 0;
  const focuses = [];
  const node = (tagName, id = "", data = {}) => ({
    tagName, id, dataset: {}, ...data,
    closest(selector) {
      if (selector === "[id]" && this.id) return this;
      if (selector === "article[id]" && this.tagName === "ARTICLE" && this.id) return this;
      return this.parentElement?.closest(selector) || null;
    },
    focus(options) {focuses.push({node: this, options: normalize(options)}); sandbox.document.activeElement = this;},
  });
  const file = node("INPUT", "experimentFile", {type: "file"});
  Object.defineProperty(file, "value", {
    get() {return "C:\\fakepath\\SYNTHETIC.csv";},
    set(value) {fileAssignments++; if (value) throw new Error("A browser file input cannot receive a nonempty value");},
  });
  const view = {
    id: "view-collaboration",
    contains(candidate) {return current.nodes.includes(candidate);},
    querySelectorAll(selector) {
      if (selector === "details") return [current.details];
      if (selector === "[id]") return current.nodes.filter(item => item.id);
      if (selector === "input[id], select[id], textarea[id]") return current.controls;
      if (selector === "[data-focus-key]") return [current.wrongButton, current.button];
      throw new Error("Unexpected fake-DOM query: " + selector);
    },
  };
  function tree(count, reset) {
    const panel = node("SECTION", "agentNodeDetail");
    const otherPanel = node("SECTION", "otherNodeDetail");
    const summary = node("SUMMARY", "", {textContent: "统一预算 · " + count});
    const details = node("DETAILS", "", {dataset: {uiKey: "co-budget"}, open: !reset,
      querySelector(selector) {assert.equal(selector, "summary"); return summary;}});
    summary.parentElement = details; details.parentElement = panel;
    const button = node("BUTTON", "", {dataset: {focusKey: "artifact-h_synthetic_1:v1"}, parentElement: panel});
    const wrongButton = node("BUTTON", "", {dataset: {focusKey: "artifact-h_synthetic_1:v1"}, parentElement: otherPanel});
    const select = node("SELECT", "stage-fixture", {type: "select-one", value: reset ? "prepare" : "md",
      options: [{value: "prepare"}, {value: "md"}]});
    const checkbox = node("INPUT", "resume-fixture", {type: "checkbox", checked: !reset, value: "on"});
    const scroll = node("DIV", "activityScroll", {scrollTop: reset ? 0 : 72, scrollLeft: reset ? 0 : 16});
    return {summary, details, button, wrongButton, select, checkbox, scroll,
      controls: [file, select, checkbox],
      nodes: [panel, otherPanel, details, summary, wrongButton, button, file, select, checkbox, scroll]};
  }
  current = tree(4, false);
  sandbox.document = {activeElement: current.summary,
    getElementById(id) {return current.nodes.find(item => item.id === id) || null;}};
  sandbox.window = {scrollX: 28, scrollY: 320,
    scrollTo(options) {this.scrollX = options.left; this.scrollY = options.top; this.lastScroll = normalize(options);}};
  const redraw = count => {
    current = tree(count, true);
    sandbox.window.scrollX = 0; sandbox.window.scrollY = 0;
  };
  ui.preserveView(view, () => redraw(5));
  assert.equal(fileAssignments, 0);
  assert.equal(file.value, "C:\\fakepath\\SYNTHETIC.csv");
  assert.equal(current.select.value, "md");
  assert.equal(current.checkbox.checked, true);
  assert.equal(current.details.open, true);
  assert.equal(sandbox.document.activeElement, current.summary);
  assert.equal(current.summary.textContent, "统一预算 · 5");
  assert.deepEqual(focuses.at(-1).options, {preventScroll: true});
  assert.equal(current.scroll.scrollTop, 72);
  assert.equal(current.scroll.scrollLeft, 16);
  assert.deepEqual(sandbox.window.lastScroll, {left: 28, top: 320, behavior: "instant"});
  sandbox.document.activeElement = current.button;
  ui.preserveView(view, () => redraw(6));
  assert.equal(sandbox.document.activeElement, current.button);
  assert.notEqual(sandbox.document.activeElement, current.wrongButton);
  assert.equal(current.details.open, true);
  assert.equal(current.select.value, "md");
  assert.equal(current.checkbox.checked, true);
  assert.equal(current.scroll.scrollTop, 72);
  assert.equal(current.scroll.scrollLeft, 16);
  assert.equal(fileAssignments, 0);
  assert.deepEqual(sandbox.window.lastScroll, {left: 28, top: 320, behavior: "instant"});
});

check("selection guards reject results after either project or run changes", () => {
  Object.assign(ui.state, {epoch: 3, selectionEpoch: 7, project: "p_fixture_a", run: "r_fixture_a"});
  assert.equal(ui.selectedEpoch(3, "p_fixture_a", "r_fixture_a"), true);
  assert.equal(ui.selectionCurrent(7, "p_fixture_a", "r_fixture_a"), true);
  assert.equal(ui.selectedEpoch(2, "p_fixture_a", "r_fixture_a"), false);
  assert.equal(ui.selectionCurrent(6, "p_fixture_a", "r_fixture_a"), false);
  ui.state.project = "p_fixture_b";
  assert.equal(ui.selectedEpoch(3, "p_fixture_a", "r_fixture_a"), false);
  assert.equal(ui.selectionCurrent(7, "p_fixture_a", "r_fixture_a"), false);
  ui.state.project = "p_fixture_a"; ui.state.run = "r_fixture_b";
  assert.equal(ui.selectedEpoch(3, "p_fixture_a", "r_fixture_a"), false);
  assert.equal(ui.selectionCurrent(7, "p_fixture_a", "r_fixture_a"), false);
});

check("research receipts resolve recorded claim/query versions only", () => {
  const run = byScenario.partial;
  assert.equal(ui.artifactTarget("research_flow", run).kind, "research_flow");
  for (const field of ["claims","queries","claim_reviews","novelty"]) {
    const item = run.co_scientist.research_flow[field][0], id = item.claim_id && field === "claims" ? item.claim_id : item.query_id || item.review_id || item.novelty_id;
    assert.equal(ui.artifactTarget(id, run).kind, "research_receipt");
  }
  assert.equal(ui.artifactTarget("claim:h_synthetic_1:v99:main", run), null);
  assert.equal(ui.artifactTarget("research_flow", byScenario.legacy), null);
});
check("research observation export excludes arbitrary private receipt fields", () => {
  const run = structuredClone(byScenario.partial), flow = run.co_scientist.research_flow;
  flow.private_prompt = "PRIVATE_SENTINEL"; flow.queries[0].headers = {Authorization:"PRIVATE_SENTINEL"};
  flow.queries[0].query = "api_key=PRIVATE_SENTINEL";
  const exported = {research_flow:normalize(ui.publicResearchFlow(flow))};
  assert.equal(JSON.stringify(exported).includes("PRIVATE_SENTINEL"), false);
  assert.equal(exported.research_flow.claims[0].version, 1);
});
check("research example keeps skipped novelty and contested evidence separate", () => {
  const flow = byScenario.partial.co_scientist.research_flow;
  assert.equal(flow.claims[0].state, "contested");
  assert.equal(flow.queries.find(q => q.stage === "novelty").status, "skipped");
  assert.equal(flow.novelty[0].status, "insufficient_search");
  assert.equal(ui.publicResearchFlow(byScenario.legacy.co_scientist?.research_flow), null);
});

check("SYNTHETIC counts separate ingested papers tool observations databases and executed plans", () => {
  const run = {sources:Array.from({length:12}, (_,i) => ({source_id:"paper:" + i,evidence_scope:"abstract"})).concat([{source_id:"db:1",evidence_scope:"database"},{source_id:"db:2",evidence_scope:"database"}]),
    evidence:Array.from({length:7}, (_,i) => ({source_id:"paper:" + i%4,validated_span:true})),
    co_scientist:{reviews:[],tool_calls:[{sources:[{source_id:"paper:12"},{source_id:"paper:13"},{source_id:"paper:14"}]}], research_flow:{
      publications:Array.from({length:15}, (_,i) => ({kind:"paper",snapshots:[{source_id:"paper:"+i}]})).concat([{kind:"database",snapshots:[{source_id:"db:1"}]},{kind:"database",snapshots:[{source_id:"db:2"}]}]),
      queries:Array.from({length:28}, (_,i) => ({stage:i < 4 ? "landscape" : i < 6 ? "full_text" : "supplement",status:i < 6 ? "ok" : "skipped",request_count:i < 6 ? 1 : 0}))}}};
  const before = JSON.stringify(run), counts = ui.researchObservationCounts(run);
  for (const [key, expected] of Object.entries({canonical_paper_count:15,database_group_count:2,ingested_paper_group_count:12,
    tool_observation_only_paper_group_count:3,accepted_citation_count:7,accepted_citation_source_count:4,formal_hypothesis_review_count:0,
    query_plan_count:28,executed_literature_query_count:4,executed_full_text_count:2,reused_query_count:0,unexecuted_query_count:22})) assert.equal(counts[key], expected, key);
  assert.equal(JSON.stringify(run), before);
  run.co_scientist.research_flow.queries = [{status:"reused",stage:"reuse"},{status:"ok",stage:"landscape"},{status:"unavailable",stage:"landscape",request_count:1}];
  const old = ui.researchObservationCounts(run);
  assert.equal(old.reused_query_count,1); assert.equal(old.unknown_execution_query_count,1); assert.equal(old.executed_literature_query_count,1);
});

check("public scope exports retain actual ranges stage quotas and publication identity", () => {
  const flow = {stage_limits:{landscape:2,full_text:2,raw_prompt:"PRIVATE_SENTINEL"},stage_usage:{landscape:1},
    publications:[{paper_id:"paper_synthetic",kind:"paper",snapshots:[{source_id:"paper:1",title:"SYNTHETIC paper",
      scope:"abstract",authors:["SYNTHETIC Author"],journal:"SYNTHETIC Journal",text_sha256:"SYNTHETIC_HASH",
      text:"PRIVATE_FULLTEXT_SENTINEL",raw_prompt:"PRIVATE_SENTINEL"}]}],
    queries:[{query_id:"q_synthetic",scope:"abstract",total_hits:2017,coverage:"bounded_subset",sort:"relevance",
      pagination:"first_bounded_page",hit_range_start:1,hit_range_end:6,has_more:true,coverage_gaps:["Later pages unread"],raw_prompt:"PRIVATE_SENTINEL"}],
    links:[{study_system:"SYNTHETIC mouse",condition:"SYNTHETIC strain and dose",endpoint:"SYNTHETIC endpoint",polarity:"opposes",quote:"PRIVATE_FULLTEXT_SENTINEL"}]};
  const doc = normalize(ui.publicResearchFlow(flow));
  assert.equal(doc.queries[0].total_hits,2017); assert.equal(doc.queries[0].has_more,true);
  assert.equal(doc.stage_limits.landscape,2); assert.equal(doc.stage_usage.landscape,1);
  assert.equal(doc.publications[0].snapshots[0].journal,"SYNTHETIC Journal");
  assert.equal(doc.links[0].polarity,"opposes");
  assert.doesNotMatch(JSON.stringify(doc),/PRIVATE_|raw_prompt/);
});

check("public budget failures preserve byte precheck while hiding diagnostics and prompts", () => {
  const failure = {reason:"input_reservation",stage:"reflection",required_bytes:80489,remaining_bytes:39451,
    remaining_seconds:86,required_calls:1,remaining_calls:9,inflight_output_bytes:12000,blocked_before_provider:true,prior_timeout:true,accounting_unit:"utf8_bytes",
    prompt:"PRIVATE_SENTINEL",stderr:"LOCAL_LOG_SENTINEL"};
  const budget = normalize(ui.publicBudget({input_tokens_reserved:260549,output_bytes_inflight:12000,limits:{max_input_tokens:300000,password:"PRIVATE_SENTINEL"},
    budget_failures:[failure],optional_skips:[{...failure,reason:"preserve_critical_review",reserved_calls:2}],provider_payload:{secret:"PRIVATE_SENTINEL"}}));
  assert.equal(budget.budget_failures[0].required_bytes,80489); assert.equal(budget.budget_failures[0].blocked_before_provider,true);
  assert.equal(budget.optional_skips[0].reserved_calls,2);
  assert.equal(budget.output_bytes_inflight,12000); assert.equal(budget.budget_failures[0].inflight_output_bytes,12000);
  assert.doesNotMatch(JSON.stringify(budget),/PRIVATE_|LOCAL_LOG|prompt|password|provider_payload/);
  assert.match(source,/Conservative input reservation \(UTF-8 bytes\)/);
});

check("budget actions deduplicate exact mirrors and retain distinct actions plus all byte facts", () => {
  const first = {receipt_id:"budget_action_0001",operation_id:"budget_action_0001",stage:"SYNTHETIC meta",reason:"Input reservation budget exhausted",
    required_bytes:71585,remaining_bytes:49392,reserved_bytes:0,remaining_seconds:114.094,
    required_output_bytes:12000,remaining_output_bytes:58617,reserved_output_bytes:0,blocked_before_provider:true,accounting_unit:"utf8_bytes",raw_prompt:"PRIVATE_SENTINEL"};
  const second = {...first,receipt_id:"budget_action_0002",operation_id:"budget_action_0002"};
  const co = {budget_failures:[first,second],optional_skips:[first],budget:{budget_failures:[first,second],optional_skips:[first]},events:[{budget_failure:first}]};
  const records = normalize(ui.publicFailureRecords(co));
  assert.equal(records.length,2);
  assert.deepEqual(records[0].record_types,["budget_failure","optional_skip","budget_event"]);
  assert.equal(records[0].required_bytes,71585); assert.equal(records[0].remaining_bytes,49392);
  assert.equal(records[0].reserved_output_bytes,0); assert.equal(records[0].accounting_unit,"utf8_bytes");
  assert.equal(records[1].receipt_id,"budget_action_0002");
  assert.doesNotMatch(JSON.stringify(records),/PRIVATE_|raw_prompt/);
  assert.match(ui.failureDetailTitle(records[0]),/Optional stage skipped.*budget_action_0001/);
  assert.equal(ui.artifactTarget("budget_action_0002",{co_scientist:co}).kind,"failure_receipt");
});

check("legacy budget mirrors preserve repeated identical actions without inventing identifiers", () => {
  const row = {stage:"SYNTHETIC repeated stage",reason:"shared_budget",required_bytes:71585,remaining_bytes:49392};
  const records = normalize(ui.publicFailureRecords({budget_failures:[row,row],budget:{budget_failures:[row,row]}}));
  assert.equal(records.length,2);
  assert.ok(records.every(record => !("receipt_id" in record) && !("operation_id" in record)));
  const changed = {...row,remaining_bytes:49391,receipt_id:"budget_action_0001"};
  assert.equal(ui.publicFailureRecords({budget_failures:[{...row,receipt_id:"budget_action_0001"},changed]}).length,2);
});

check("current entity references preserve historical gaps without revising scientific conclusions", () => {
  const ref = normalize(ui.publicEntityReference({entity_id:"old_candidate",candidate_id:"mol_synthetic",identity_revision:1,
    identity_status:"resolved",scientific_assessment:"Versioned scientific claims and priority have not been reassessed",
    hypothesis_versions:[{hypothesis_id:"h_synthetic",version:1,historical_identity_gaps:["PubChem unresolved in historical version"],raw_prompt:"PRIVATE_SENTINEL"}],
    raw_prompt:"PRIVATE_SENTINEL"}));
  assert.equal(ref.hypothesis_versions[0].version,1); assert.equal(ref.hypothesis_versions[0].historical_identity_gaps.length,1);
  assert.doesNotMatch(JSON.stringify(ref),/PRIVATE_|raw_prompt/);
});

check("assessment-input candidates on hold remain visible and preserve mixed evidence scope", () => {
  const candidate = {candidate_id:"synthetic_held",name:"SYNTHETIC compound",role:"unknown",decision:"hold"};
  const run = {candidate_assessments:[{candidate_id:candidate.candidate_id, origin:"user_supplied",scientific_status:"needs_evidence"}]};
  assert.equal(ui.candidateVisible(candidate, run), true);
  assert.equal(ui.candidateVisible(candidate, {candidate_assessments:[]}), false);
  assert.equal(ui.candidateAssessment(candidate,run).scientific_status,"needs_evidence");
  const card = normalize(ui.publicCandidateAssessment({...run.candidate_assessments[0],
    opposing_evidence_ids:["synthetic_mixed"],population_requested:"SYNTHETIC children", workflow_status:"completed",
    evidence_scope:[{evidence_id:"synthetic_mixed",polarity:"mixed",study_system:"SYNTHETIC children",endpoint:"core and associated separately",raw_prompt:"PRIVATE_SENTINEL",text:"FULL_TEXT_SENTINEL"}],
    review_associations:[{claim_id:"synthetic_claim",hypothesis_id:"synthetic_h",version:2,evidence_id:"synthetic_mixed",query_ids:["query_0001"],raw_prompt:"PRIVATE_SENTINEL"}],
    private_prompt:"PRIVATE_SENTINEL"}));
  assert.equal(card.workflow_status,"completed"); assert.equal(card.scientific_status,"needs_evidence");
  assert.deepEqual(card.opposing_evidence_ids,["synthetic_mixed"]);
  assert.equal(card.review_associations[0].version,2);
  assert.equal(card.evidence_scope[0].study_system,"SYNTHETIC children");
  assert.doesNotMatch(JSON.stringify(card),/PRIVATE_|FULL_TEXT|raw_prompt/);
});

check("candidate renderer and observation preserve entity-specific status despite supported general claim", () => {
  const previousDocument = sandbox.document, previousNode = sandbox.Node;
  class SyntheticNode {
    constructor(tag) {this.tagName = tag; this.dataset = {}; this.children = []; this.ownText = "";}
    set textContent(value) {this.ownText = String(value); this.children = [];}
    get textContent() {return this.ownText + this.children.map(child => child.textContent || "").join(" ");}
    append(...children) {this.children.push(...children);}
    addEventListener() {}
  }
  sandbox.Node = SyntheticNode;
  sandbox.document = {createElement:tag => new SyntheticNode(tag)};
  try {
    for (const [scientificStatus, expected] of [["needs_evidence", "Needs evidence"],
      ["source_claim_supported_pending_validation", "Specific claim supported by sources · validation pending"]]) {
      const assessment = {candidate_id:"synthetic_compound",name:"Illustrative compound",origin:"user_supplied",
        scientific_status:scientificStatus,workflow_status:"completed",decision:"hold",identity_status:"needs_review",
        evidence_ids:scientificStatus === "needs_evidence" ? [] : ["synthetic_bound_support"], opposing_evidence_ids:[],
        hypothesis_versions:[{hypothesis_id:"synthetic_h",version:1,review_state:"supported"}],
        evidence_scope:[],review_associations:[],unknowns:["SYNTHETIC candidate-specific gaps"],next_questions:["SYNTHETIC next question"]};
      const exported = normalize(ui.publicCandidateAssessment(assessment));
      assert.equal(exported.scientific_status,scientificStatus);
      assert.equal(exported.hypothesis_versions[0].review_state,"supported");
      const rendered = ui.assessmentDetails(exported).textContent;
      assert.ok(rendered.includes(expected));
      if (scientificStatus === "needs_evidence") assert.doesNotMatch(rendered,/Specific claim supported by sources/);
      assert.match(rendered,/SYNTHETIC candidate-specific gaps/);
      assert.match(rendered,/SYNTHETIC next question/);
    }
  } finally {sandbox.document = previousDocument; sandbox.Node = previousNode;}
});

check("background package activation is explicit and Co-Scientist only", () => {
  assert.equal(ui.selectedBackgroundPackage("co_scientist", "  SYNTHETIC-v1  "), "SYNTHETIC-v1");
  assert.equal(ui.selectedBackgroundPackage("co_scientist", ""), "");
  assert.equal(ui.selectedBackgroundPackage("standard", "SYNTHETIC-v1"), "");
  assert.throws(() => ui.selectedBackgroundPackage("co_scientist", "../SYNTHETIC"), /Background-package IDs/);
});

check("user full-name declarations remain separate from official aliases in public identity references", () => {
  const ref = normalize(ui.publicEntityReference({name:"SL",aliases:[],user_name_declarations:[
    {label:"Synthetic lactate (SL)",full_name:"Synthetic lactate",abbreviation:"SL",source:"brief.existing_candidates",source_index:0,run_id:"r_synthetic",raw_prompt:"PRIVATE_SENTINEL"},
    {full_name:"UNVERIFIED",source:"provider",source_index:0,secret:"PRIVATE_SENTINEL"}]}));
  assert.deepEqual(ref.aliases,[]);
  assert.equal(ref.user_name_declarations.length,1);
  assert.equal(ref.user_name_declarations[0].full_name,"Synthetic lactate");
  assert.equal(ref.user_name_declarations[0].source,"brief.existing_candidates");
  assert.doesNotMatch(JSON.stringify(ref),/PRIVATE_|raw_prompt|UNVERIFIED/);
});

check("saved full text excerpts and reuse counts retain their acquisition scope", () => {
  assert.match(ui.sourceScopeLabel({evidence_scope:"full_text",metadata:{local_excerpt_only:true}}),/saved contiguous excerpt.*Acquisition not recorded.*excerpt scope/);
  assert.equal(ui.sourceScopeLabel({evidence_scope:"abstract"}),"Abstract only");
  const flow = normalize(ui.publicResearchFlow({library_status:[{library:"SYNTHETIC background",reused_records:7,
    newly_acquired_records:2,selected_records:6,observed_records:9,raw_prompt:"PRIVATE_SENTINEL"}]}));
  assert.equal(flow.library_status[0].reused_records,7);
  assert.equal(flow.library_status[0].selected_records,6);
  assert.doesNotMatch(JSON.stringify(flow),/PRIVATE_|raw_prompt/);
});

check("full text original window and acquisition facts stay distinct in source labels", () => {
  const source = {evidence_scope:"abstract",text:"x".repeat(2262),metadata:{original_evidence_scope:"full_text",local_excerpt_only:true,
    excerpt_start:11889,excerpt_end:14151,acquisition_origin:"background_reuse",raw_prompt:"PRIVATE_SENTINEL"}};
  const scope = normalize(ui.sourceScopeView(source));
  assert.deepEqual(scope,{original_scope:"full_text",current_scope:"local_excerpt",local_excerpt:true,
    original_window:{start:11889,end:14151},acquisition:"reused",acquisition_origin:"background_reuse",fresh_fulltext:false});
  assert.match(ui.sourceScopeLabel(source),/Original Full text.*contiguous excerpt.*11889, 14151.*Reused saved excerpt/);
  assert.equal(ui.sourceScopeView({evidence_scope:"full_text"}).fresh_fulltext,false);
  assert.equal(ui.sourceScopeView({evidence_scope:"full_text",metadata:{acquisition_origin:"current_run_retrieval"}}).fresh_fulltext,false);
  assert.equal(ui.sourceScopeView({evidence_scope:"full_text",metadata:{acquisition_origin:"current_run_full_text"}}).fresh_fulltext,true);
  assert.equal(ui.sourceScopeView({evidence_scope:"database",metadata:{acquisition:"background_reuse"}}).acquisition,"reused");
  assert.doesNotMatch(JSON.stringify(scope),/PRIVATE_|raw_prompt/);
});

check("claim relation public fields retain scope and legacy-null status", () => {
  const relation = {schema_version:2,claim_id:"c_synthetic",hypothesis_id:"h_synthetic",version:1,evidence_id:"ev_synthetic",
    source_id:"s_synthetic",entity_id:"entity_synthetic",relation:"supports",study_system:"SYNTHETIC cells",condition:"SYNTHETIC condition",endpoint:"SYNTHETIC endpoint",raw_prompt:"PRIVATE_SENTINEL"};
  const flow = normalize(ui.publicResearchFlow({claims:[{claim_id:"c_synthetic",relation_schema_version:2,assessment_scope:"source_observation"}],
    links:[{claim_relation:relation},{claim_relation:null},{}],claim_reviews:[{evidence_relations:[relation],rejected_relation_ids:["bad_relation"],relation_schema_version:2,claim_scope:"source_observation"}]}));
  assert.equal(flow.claims[0].assessment_scope,"source_observation");
  assert.equal(flow.links[0].claim_relation.entity_id,"entity_synthetic");
  assert.equal(flow.links[1].claim_relation,null); assert.equal("claim_relation" in flow.links[2],false);
  assert.deepEqual(flow.claim_reviews[0].rejected_relation_ids,["bad_relation"]);
  assert.equal(flow.claim_reviews[0].evidence_relations[0].schema_version,2);
  assert.doesNotMatch(JSON.stringify(flow),/PRIVATE_|raw_prompt/);
});

check("narrow observation support remains separate from candidate efficacy in public assessment", () => {
  const relation = {claim_id:"c_synthetic",claim_kind:"main",hypothesis_id:"h_synthetic",version:1,review_id:"r_synthetic",
    evidence_id:"e_synthetic",source_id:"s_synthetic",query_ids:[],verdict:"supported",polarity:"mixed",
    relation_schema_version:2,assessment_scope:"source_observation",claim_relation:"supports",relation_basis:"exact_source_report_only",raw_prompt:"PRIVATE_SENTINEL"};
  const card = normalize(ui.publicCandidateAssessment({scientific_status:"needs_evidence",decision:"hold",observation_support:[relation,
    {...relation,relation_schema_version:1},{...relation,assessment_scope:"inference"}]}));
  assert.equal(card.scientific_status,"needs_evidence"); assert.equal(card.decision,"hold");
  assert.equal(card.observation_support.length,1); assert.equal(card.observation_support[0].polarity,"mixed");
  assert.equal(card.observation_support[0].relation_basis,"exact_source_report_only");
  assert.doesNotMatch(JSON.stringify(card),/PRIVATE_|raw_prompt/);
  assert.match(source,/Support applies to the cited source observation.*Whole-hypothesis assessment and clinical applicability are evaluated separately/);
});

check("explicit intent rejects guided inputs and keeps legacy behavior", () => {
  const neutral = {disease:"SYNTHETIC disease", existing_candidates:[], chassis_name:"Lactiplantibacillus plantarum WCFS1"};
  assert.equal(ui.applyTaskIntent(neutral,"disease_exploration").chassis_name,"");
  for (const guidance of [{existing_candidates:["SYNTHETIC compound"]}, {background_package_id:"SYNTHETIC_pack"}, {target_hints:["GENE"]}, {conditions:"supplied"}, {chassis_name:"OTHER"}]) {
    assert.throws(() => ui.applyTaskIntent({...neutral,...guidance},"disease_exploration"), /Disease exploration/);
  }
  assert.throws(() => ui.applyTaskIntent(neutral,"candidate_assessment"), /Enter a candidate name/);
  assert.equal(ui.applyTaskIntent({...neutral,existing_candidates:["SYNTHETIC compound"]},"candidate_assessment").task_intent,"candidate_assessment");
  assert.equal(ui.applyTaskIntent(neutral,"legacy").chassis_name,neutral.chassis_name);
  assert.equal(neutral.chassis_name,"Lactiplantibacillus plantarum WCFS1");
});
check("not_run is never labeled an existing ADMET prediction", () => {
  for (const value of [undefined,{}, {status:"not_run"}, {status:"not_run", model:"SYNTHETIC"}]) assert.equal(ui.assessmentExecutionLabel(value,"admet"),"Not run");
  assert.doesNotMatch(ui.assessmentExecutionLabel({status:"unknown"},"admet"), /Prediction recorded/);
  assert.match(ui.assessmentExecutionLabel({status:"completed",score:0.2},"admet"), /Prediction recorded/);
  assert.equal(ui.assessmentExecutionLabel({status:"not_run",class:"pass"},"gate"),"Not run");
});
check("public evidence digest preserves strata and coverage without private payload", () => {
  const digest = ui.publicEvidenceDigest({version:"evidence-digest-1",raw_prompt:"PRIVATE_SENTINEL",
    counts:{selected_publications:1,polarity_citations:{mixed:1,opposes:1}},
    reference_coverage:[{reference_id:"ref",title:"SYNTHETIC",selection_status:"not_selected",citation_status:"unreviewed_or_unaccepted",raw_prompt:"PRIVATE_SENTINEL"}],
    publications:[{publication_id:"pub",source_design:"randomized_controlled_trial",source_ids:["src"],accepted_citations:[{evidence_id:"mixed",polarity:"mixed",condition:"SYNTHETIC condition",endpoint:"SYNTHETIC endpoint",quote:"FULL_TEXT_SENTINEL"}]}]});
  assert.equal(digest.reference_coverage[0].selection_status,"not_selected");
  assert.equal(digest.publications[0].accepted_citations[0].polarity,"mixed");
  assert.equal(digest.counts.polarity_citations.opposes,1);
  assert.doesNotMatch(JSON.stringify(digest), /PRIVATE_|raw_prompt|FULL_TEXT_/);
  const gaps=ui.publicCandidateAssessment({scoped_claim_gaps:[{claim_id:"c",state:"unknown",gaps:["scoped"]}],recorded_gap_notes:[{scope_status:"recorded_scope_unconfirmed",text:"old note"}]});
  assert.equal(gaps.recorded_gap_notes[0].scope_status,"recorded_scope_unconfirmed");
  assert.equal(gaps.scoped_claim_gaps[0].state,"unknown");
});

check("provider receipt projection preserves null facts and rejects text-based inference", () => {
  const raw = {version:1,category:"process_nonzero",stage:"process",backend:"codex",origin:"provider_call",elapsed_seconds:2.5,
    returncode:-9,signal:9,output_exists:false,output_is_file:false,output_size_bytes:null,output_stat_status:"missing",
    stdout:"PRIVATE_STDOUT_SENTINEL",answer:"PRIVATE_ANSWER_SENTINEL",raw_prompt:"PRIVATE_PROMPT_SENTINEL"};
  const expected = {version:1,category:"process_nonzero",stage:"process",backend:"codex",origin:"provider_call",elapsed_seconds:2.5,
    returncode:-9,signal:9,output_exists:false,output_is_file:false,output_size_bytes:null,output_stat_status:"missing"};
  assert.deepEqual(normalize(ui.publicProviderFailure(raw)),expected);
  assert.deepEqual(normalize(ui.publicProviderFailure({message:"timeout stderr PRIVATE_SENTINEL"})),{});
  assert.deepEqual(normalize(ui.publicProviderFailure({...raw,category:"stderr PRIVATE_SENTINEL"})),{});
  assert.deepEqual(normalize(ui.publicProviderFailure({...raw,origin:"cache"})),{});
  assert.equal(ui.publicProviderFailure({...raw,returncode:null,signal:null,output_exists:null}).returncode,null);
  assert.equal(ui.publicProviderFailure({...raw,returncode:0,signal:9}).signal,undefined);
  assert.equal(ui.publicProviderFailure({...raw,elapsed_seconds:Infinity}).elapsed_seconds,undefined);
  assert.equal(ui.publicProviderFailure({...raw,returncode:true}).returncode,undefined);
  assert.equal(ui.publicProviderFailure({...raw,output_size_bytes:-1}).output_size_bytes,undefined);
  assert.equal(ui.publicProviderFailure({...raw,output_stat_status:"PRIVATE_SENTINEL"}).output_stat_status,undefined);
  const failure = {stage:"SYNTHETIC reflection",operation_id:"llm_002",reason:"provider_failure",provider_failure:raw};
  assert.deepEqual(normalize(ui.publicBudgetFailure(failure)).provider_failure,expected);
  assert.match(ui.failureDetailTitle(failure),/^Model call failed.*llm_002/);
  assert.match(ui.failureDetailTitle({stage:"SYNTHETIC reflection",reason:"input_reservation",message:"timeout"}),/^Budget stop/);
  assert.equal(ui.providerFailureFields({provider_failure:{}}).provider_failure,undefined);
});

check("provider event and budget observation export share exactly the safe receipt", () => {
  const previousDocument = sandbox.document, previousURL = sandbox.URL, previousBlob = sandbox.Blob, previousTimeout = sandbox.setTimeout;
  const raw = {version:1,category:"answer_missing",stage:"output_stat",backend:"codex",origin:"provider_call",elapsed_seconds:1.25,
    returncode:0,signal:null,output_exists:false,output_is_file:false,output_size_bytes:null,output_stat_status:"missing",stderr:"PRIVATE_SENTINEL"};
  const safe = normalize(ui.publicProviderFailure(raw));
  const failure = {operation_id:"llm_002",stage:"SYNTHETIC invocation",reason:"provider_failure",provider_failure:raw};
  const run = {run_id:"synthetic_provider_failure",status:"failed",fixture:{synthetic:true},co_scientist:{events:[
    {event_id:"new",sequence:1,agent_id:"supervisor",kind:"llm",status:"failed",operation_id:"llm_002",provider_failure:raw},
    {event_id:"old",sequence:2,agent_id:"supervisor",kind:"llm",status:"failed",operation_id:"llm_001",provider_failure:{}}],
    budget_failures:[failure],budget:{budget_failures:[failure]}}};
  let exported;
  sandbox.document = {createElement() {return {click(){}};}};
  sandbox.Blob = class {constructor(parts) {this.parts=parts;}};
  sandbox.URL = {createObjectURL(blob) {exported=JSON.parse(blob.parts.join(""));return "blob:SYNTHETIC";},revokeObjectURL(){}};
  sandbox.setTimeout = action => action();
  try {
    ui.observationExport(run);
    assert.deepEqual(exported.events[0].provider_failure,safe);
    assert.deepEqual(exported.budget_failures[0].provider_failure,safe);
    assert.deepEqual(exported.budget.budget_failures[0].provider_failure,safe);
    assert.equal(exported.events[0].operation_id,exported.budget_failures[0].operation_id);
    assert.equal(exported.events[1].provider_failure,undefined);
    assert.doesNotMatch(JSON.stringify(exported),/PRIVATE_|stderr/);
  } finally {sandbox.document=previousDocument;sandbox.URL=previousURL;sandbox.Blob=previousBlob;sandbox.setTimeout=previousTimeout;}
});

check("timeline details show safe execution facts without manufacturing legacy receipts", () => {
  const previousDocument = sandbox.document, previousSelected = ui.state.selected;
  class SyntheticNode {
    constructor(tag) {this.tagName=tag;this.dataset={};this.children=[];this.ownText="";}
    set textContent(value) {this.ownText=String(value);this.children=[];}
    get textContent() {return this.ownText+this.children.map(child=>child.textContent||"").join(" ");}
    append(...children) {this.children.push(...children);}
    replaceChildren(...children) {this.children=children;}
    addEventListener() {}
    querySelector(tag) {return this.children.find(child=>child.tagName===tag);}
  }
  const timeline = new SyntheticNode("section"), filter={value:"all"};
  sandbox.document={createElement:tag=>new SyntheticNode(tag),getElementById:id=>id==="agentTimeline"?timeline:filter};
  ui.state.selected={status:"failed",co_scientist:{events:[
    {event_id:"new",sequence:1,kind:"llm",status:"failed",title:"SYNTHETIC failure",operation_id:"llm_002",
      provider_failure:{version:1,category:"process_nonzero",stage:"process",backend:"codex",origin:"provider_call",returncode:1,output_exists:false,output_size_bytes:null,stderr:"PRIVATE_SENTINEL"}},
    {event_id:"old",sequence:2,kind:"llm",status:"failed",title:"SYNTHETIC legacy",summary:"Codex inference failed",operation_id:"llm_001"}]}};
  try {
    ui.renderTimeline();
    assert.match(timeline.textContent,/process_nonzero/);
    assert.match(timeline.textContent,/"returncode": 1/);
    assert.match(timeline.textContent,/"output_size_bytes": null/);
    assert.doesNotMatch(timeline.textContent,/PRIVATE_|stderr/);
    const oldRow=timeline.children.find(child=>child.tagName==="ol").children.find(child=>child.id==="activity-old");
    assert.doesNotMatch(oldRow.textContent,/provider_failure|returncode|output_exists/);
  } finally {sandbox.document=previousDocument;ui.state.selected=previousSelected;}
});

(async () => {
  for (const method of ["GET", "POST"]) await assert.rejects(ui.api("/api/SYNTHETIC", method, method === "POST" ? {} : undefined), /unconnected local-file preview/);
  checks++;
  process.stdout.write("PASS file preview prevents both read and write API requests\n");
  process.stdout.write(checks + " UI contracts passed\n");
})().catch(error => {process.stderr.write(error.stack + "\n"); process.exitCode = 1;});

