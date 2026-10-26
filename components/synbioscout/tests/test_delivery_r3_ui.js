/* SYNTHETIC R3 public budgets and reviewed findings, with no browser/network. */
"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const root=path.resolve(__dirname,"..");
const fixture=JSON.parse(fs.readFileSync(process.argv[2]||path.join(root,"docs/nac_asd_iteration/DELIVERY_R3_SYNTHETIC_PUBLIC.json"),"utf8"));
class SyntheticNode {
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.ownText="";}
 set textContent(v){this.ownText=String(v);this.children=[];}
 get textContent(){return this.ownText+this.children.map(x=>x.textContent||"").join(" ");}
 append(...children){this.children.push(...children);}
 addEventListener(){}
}
const sandbox={document:{createElement:tag=>new SyntheticNode(tag)},Node:SyntheticNode,
 location:{protocol:"file:",href:"file:///SYNTHETIC/index.html"},fetch(){throw new Error("Offline public contract forbids networking");}};
vm.createContext(sandbox);
const source=fs.readFileSync(path.join(root,"scientist/web/app.js"),"utf8");
vm.runInContext(source.replace(/\ninit\(\);\s*$/,"")+"\nglobalThis.ui={publicBudget,publicBudgetFailure,publicFailureRecords,artifactTarget,publicCandidateEvaluation,publicCandidateEvaluationRecord,publicCandidateAssessment,assessmentDetails,state};",sandbox);
const ui=sandbox.ui,normal=x=>JSON.parse(JSON.stringify(x));
const cases=Object.fromEntries(fixture.runs.map(r=>[r.presentation.fixture.case,r]));
let count=0;
function check(name,fn){fn();count++;process.stdout.write("PASS "+name+"\n");}
for(const [name,run] of Object.entries(cases))check(name+" safe records and clickable action agree",()=>{
 assert.equal(run.presentation.fixture.synthetic,true);
 assert.deepEqual(normal(ui.publicFailureRecords(run.co_scientist)),run.failure_records);
 for(const row of run.failure_records)if(row.receipt_id)assert.equal(ui.artifactTarget(row.receipt_id,run).kind,"failure_receipt");
 assert.doesNotMatch(JSON.stringify(ui.publicFailureRecords(run.co_scientist)),/SYNTHETIC_REJECTED_BODY_DO_NOT_EXPORT/);
});
check("single call cap is explicit despite larger global remaining output",()=>{
 const run=cases.single_call,row=ui.publicFailureRecords(run.co_scientist)[0];
 assert.equal(row.required_output_bytes,17188);assert.equal(row.remaining_output_bytes,93958);assert.equal(row.reserved_output_bytes,24000);
 assert.equal(row.call_allowance,12000);assert.equal(row.effective_available_output_bytes,12000);
 assert.deepEqual(normal(row.output_limit_categories),["single_call"]);assert.equal(row.blocked_before_provider,false);
 const budget=ui.publicBudget(run.co_scientist.budget);assert.equal(budget.output_bytes_received,19230);assert.equal(budget.output_tokens_reserved,2042);
});
check("future and parallel restrictions remain separate from terminal released hold",()=>{
 const run=cases.future_parallel,row=ui.publicFailureRecords(run.co_scientist)[0];
 assert.equal(row.effective_available_output_bytes,6000);assert.equal(row.inflight_output_bytes,2000);assert.equal(row.reserved_output_bytes,2000);
 assert.deepEqual(normal(row.output_limit_categories),["future_reserved","parallel"]);
 assert.equal(ui.publicBudget(run.co_scientist.budget).output_bytes_inflight,0);
});
check("legacy absent cap and received count remain absent rather than inferred zero",()=>{
 const run=cases.legacy_unknown,row=ui.publicFailureRecords(run.co_scientist)[0];
 for(const key of ["call_allowance","effective_available_output_bytes","output_limit_categories"])assert.equal(Object.hasOwn(row,key),false);
 assert.equal(Object.hasOwn(ui.publicBudget(run.co_scientist.budget),"output_bytes_received"),false);
});
check("nonempty independent review retains mixed evidence and source limits",()=>{
 const run=cases.reviewed;ui.state.selected=run;
 assert.ok(run.candidate_evaluations.length);
 assert.deepEqual(normal(run.candidate_evaluations.map(ui.publicCandidateEvaluation)),run.candidate_evaluations);
 assert.deepEqual(normal(run.candidate_evaluation_records.map(ui.publicCandidateEvaluationRecord)),run.candidate_evaluation_records);
 const raw=run.candidate_assessments.find(c=>c.validated_evaluation),card=ui.publicCandidateAssessment(raw),evaluation=card.validated_evaluation;
 assert.equal(card.assessment_status,"completed_review");assert.ok(["mixed_evidence","needs_evidence"].includes(evaluation.scientific_status));
 assert.ok(evaluation.opposing_evidence_ids.length);assert.ok(evaluation.unknowns.length);assert.ok(evaluation.findings.every(f=>f.scope&&f.applicability));
 const displayed=ui.assessmentDetails(raw).textContent;assert.match(displayed,/Independent assessment reviewed/);assert.match(displayed,/Review status and evidence strength are reported separately/);
 for(const ref of ["assessment:"+evaluation.assessment_id+":v"+evaluation.version,"assessment_review:"+evaluation.review_id])assert.ok(ui.artifactTarget(ref,run));
});
process.stdout.write(count+" R3 UI contracts passed\n");

