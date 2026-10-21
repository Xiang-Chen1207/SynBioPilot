/* UI-only regression: public role chronology and review/execution separation. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
class PublicNode {
  constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={}; this.attrs={}; this.ownText=""; this.options=[]; }
  set textContent(v) { this.ownText=String(v); this.children=[]; }
  get textContent() { return this.ownText + this.children.map(n=>n.textContent || "").join(" "); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.ownText=""; this.children=nodes; }
  setAttribute(key,value) { this.attrs[key]=String(value); }
  addEventListener() {}
  querySelector(tag) { return this.children.find(n=>n.tagName===tag.toUpperCase()) || null; }
}
const nodes = Object.fromEntries(["agentNodeDetail","overviewContent","runStatus","resultTitle","resultSubtitle","run"].map(id=>[id,new PublicNode("div")]));
const sandbox = {document:{createElement:tag=>new PublicNode(tag),getElementById:id=>nodes[id] || null,querySelectorAll:()=>[]},
  Node:PublicNode, location:{protocol:"file:",href:"file:///SYNTHETIC/index.html"},
  fetch(){throw new Error("No network is permitted in this test");}};
vm.createContext(sandbox);
const source = fs.readFileSync(path.join(root,"scientist/web/app.js"),"utf8");
vm.runInContext(source.replace(/\ninit\(\);\s*$/,"")+"\nglobalThis.ui={state,roleRecordStatus,recordedStatus,renderCollaborationSelection,renderOverview,runStateBadge,runOptionLabel};",sandbox);
const ui=sandbox.ui;
ui.state.project="p_synthetic_ui"; ui.state.run="r_synthetic_completed";
const fixtures=JSON.parse(fs.readFileSync(path.join(root,"docs/serial_optimization/fixtures/collaboration_states.json"),"utf8"));
const completed=fixtures.runs.find(r=>r.run_id==="r_synthetic_completed");
const r3=JSON.parse(fs.readFileSync(path.join(root,"docs/nac_asd_iteration/DELIVERY_R3_SYNTHETIC_PUBLIC.json"),"utf8"));
const descendants = node => [node,...node.children.flatMap(descendants)];
let checks=0;
function check(name, fn) {fn();checks++;console.log("PASS "+name);}

check("terminal roles preserve recorded state but cannot look active",()=>{
  for(const status of ["completed","partial","failed","cancelled"]) {
    const agent={status:"running"}, node=ui.roleRecordStatus(agent,{status});
    assert.match(node.textContent,/Last recorded/);assert.equal(node.className,"status-badge historical");
    assert.equal(agent.status,"running");
  }
  assert.equal(ui.roleRecordStatus({status:"running"},{status:"running"}).textContent,"Role running");
  assert.equal(ui.roleRecordStatus({status:"completed"},{status:"partial"}).textContent,"Role completed");
});
check("call return and role completion are separately labelled",()=>{
  ui.state.selected=completed;
  assert.equal(ui.recordedStatus({kind:"llm",operation_status:"completed",status:"running"}).textContent,"Returned");
  assert.equal(ui.recordedStatus({status:"completed"}).textContent,"Role completed");
  assert.equal(ui.recordedStatus({kind:"llm",operation_status:"running"}).className,"status-badge historical");
});
check("a final optional skip cannot claim the whole role never executed",()=>{
  for (const invocations of [0,2]) {
    const agent={status:"skipped",invocations};
    const before=JSON.stringify(agent);
    assert.equal(ui.roleRecordStatus(agent,{status:"partial"}).textContent,"Last recorded: skipped");
    assert.doesNotMatch(ui.roleRecordStatus(agent,{status:"partial"}).textContent,/Not run/);
    assert.equal(JSON.stringify(agent),before);
  }
});
check("completed run is neutral and the long history label starts with its state",()=>{
  const badge=ui.runStateBadge("completed");
  assert.equal(badge.textContent,"Run ended");assert.equal(badge.className,"status-badge historical");
  assert.match(ui.runOptionLabel(completed),/^Run ended · 12\/10, 17:00 · SYNTHETIC/);
});
check("role history contains every saved event in sequence without changing the run",()=>{
  const run=structuredClone(completed);run.co_scientist.events.reverse();
  const before=JSON.stringify(run);ui.state.selected=run;ui.state.agent="generation";
  ui.renderCollaborationSelection();
  const history=descendants(nodes.agentNodeDetail).find(n=>n.id==="agentHistory");
  assert.equal(history.children.length,6);
  const numbers=history.children.map(row=>Number(row.textContent.match(/#(\d+)/)[1]));
  assert.deepEqual(numbers,[4,5,6,7,8,9]);
  assert.equal(history.tabIndex,0);
  assert.match(nodes.agentNodeDetail.textContent,/Model \/ tool calls · 2 linkable records/);
  assert.equal(JSON.stringify(run),before);
});
check("missing role history does not acquire invented steps or output",()=>{
  const run=structuredClone(completed);run.co_scientist.events=[];
  ui.state.selected=run;ui.state.agent="generation";ui.renderCollaborationSelection();
  assert.match(nodes.agentNodeDetail.textContent,/No public events were saved/);
  assert.equal(descendants(nodes.agentNodeDetail).some(n=>n.id==="agentHistory"),false);
});
check("new history surface keeps diagnostic strings out of visible and collapsed text",()=>{
  const run=structuredClone(completed);
  for(const e of run.co_scientist.events) {e.summary="/Users/private/SYNTHETIC_SECRET";e.input_summary="raw_prompt=SYNTHETIC_SECRET";}
  run.co_scientist.agents.find(a=>a.agent_id==="generation").task="authorization: bearer SYNTHETIC_SECRET";
  ui.state.selected=run;ui.state.agent="generation";ui.renderCollaborationSelection();
  assert.doesNotMatch(nodes.agentNodeDetail.textContent,/SYNTHETIC_SECRET/);
  assert.match(nodes.agentNodeDetail.textContent,/not public in this workbench/);
});
check("partial run with no reviewed evaluation cannot imply scientific completion",()=>{
  const run=r3.runs.find(r=>r.presentation.fixture.case==="single_call");ui.state.selected=run;ui.renderOverview(run);
  assert.match(nodes.overviewContent.textContent,/No independently reviewed candidate conclusions/);
  assert.match(nodes.overviewContent.textContent,/This run is partial/);
  assert.match(nodes.overviewContent.textContent,/SYNTHETIC/);
});
check("reviewed mixed findings remain scoped while workflow stays partial",()=>{
  const run=r3.runs.find(r=>r.presentation.fixture.case==="reviewed");ui.state.selected=run;ui.renderOverview(run);
  assert.match(nodes.overviewContent.textContent,/candidate conclusions independently reviewed/);
  assert.match(nodes.overviewContent.textContent,/scientific support, counterevidence and applicability/);
  assert.match(nodes.runStatus.textContent,/Partial/);
});
check("completed run without step records never says it is waiting to start",()=>{
  ui.state.selected=completed;ui.renderOverview(completed);
  assert.doesNotMatch(nodes.overviewContent.textContent,/waiting for research stages to be recorded/);
  assert.match(nodes.overviewContent.textContent,/No stage list was saved for this run/);
});
console.log(checks+" Wiki UI contracts passed");

