/* SYNTHETIC public candidate assessment contracts; no DOM service or network. */
"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.resolve(__dirname,"../scientist/web/app.js"),"utf8");
class SyntheticNode {
  constructor(tag) {this.tagName=tag; this.children=[]; this.dataset={}; this.ownText="";}
  set textContent(value) {this.ownText=String(value); this.children=[];}
  get textContent() {return this.ownText + this.children.map(x => x.textContent || "").join(" ");}
  append(...values) {this.children.push(...values);}
  addEventListener() {}
}
const sandbox={document:{createElement:tag => new SyntheticNode(tag)},Node:SyntheticNode,
  location:{protocol:"file:",href:"file:///SYNTHETIC/index.html"},fetch(){throw new Error("offline fixture must not fetch");}};
vm.createContext(sandbox);
vm.runInContext(source.replace(/\ninit\(\);\s*$/,"")+"\nglobalThis.checks={publicCandidateEvaluation,publicCandidateEvaluationRecord,publicCandidateAssessment,artifactTarget,assessmentDetails,state};",sandbox);
const ui=sandbox.checks, normalize=x=>JSON.parse(JSON.stringify(x));
let count=0;
function check(name,fn){fn();count++;process.stdout.write("PASS "+name+"\n");}
const evaluation={schema_version:1,assessment_id:"a_synthetic",version:1,candidate_id:"c_synthetic",entity_id:"m_synthetic",identity_revision:2,
  identity_fingerprint:"a".repeat(64),task_fingerprint:"b".repeat(64),conclusion_fingerprint:"c".repeat(64),evidence_fingerprint:"d".repeat(64),
  status:"completed_review",review_id:"ar_synthetic",review_operation_id:"llm_synthetic_review",conclusion:"SYNTHETIC limited assessment; benefit remains unknown.",scientific_status:"needs_evidence",
  findings:[{finding_id:"f_synthetic",statement:"SYNTHETIC mixed observation",evidence_ids:["e_synthetic_mixed"],scope:{study_system:"SYNTHETIC human",condition:"SYNTHETIC adjunct",endpoint:"SYNTHETIC associated endpoint"}}],
  supporting_evidence_ids:[],opposing_evidence_ids:["e_synthetic_mixed"],unknowns:["SYNTHETIC delivery and exposure remain unknown"],next_questions:["SYNTHETIC verify narrower endpoint"]};
const record={...evaluation,recorded_status:"completed_review",final:evaluation,reviews:[{...evaluation,verdict:"accept",reason:"SYNTHETIC scope reviewed",item_reviews:[{finding_id:"f_synthetic",verdict:"accept",evidence_ids:["e_synthetic_mixed"],reason:"SYNTHETIC exact scope"}]}]};
const assessment={candidate_id:"c_synthetic",entity_id:"m_synthetic",identity_revision:2,name:"SYNTHETIC compound",origin:"user_supplied",
  scientific_status:"needs_evidence",decision:"hold",assessment_status:"completed_review",validated_evaluation:evaluation};
check("independent completion does not require or invent hypotheses",()=>{
  const run={candidate_evaluations:[evaluation],candidate_evaluation_records:[record],co_scientist:{hypotheses:[],reviews:[],contracts:[]}};
  assert.equal(ui.artifactTarget("assessment:a_synthetic:v1",run).kind,"candidate_evaluation");
  assert.equal(ui.artifactTarget("assessment_review:ar_synthetic",run).kind,"candidate_evaluation_review");
  assert.equal(ui.artifactTarget("assessment:a_synthetic:v2",run),null);
  assert.equal(ui.artifactTarget("assessment_review:missing",run),null);
  assert.equal(ui.publicCandidateAssessment(assessment).scientific_status,"needs_evidence");
});
check("unreviewed and stale identity records do not gain a final conclusion",()=>{
  assert.equal(ui.publicCandidateEvaluation({...evaluation,status:"ready_for_review"}),null);
  const row=ui.publicCandidateEvaluationRecord({...record,version:2});
  assert.equal(row.final,null); assert.equal(row.status,"unreviewed");
  const card=ui.publicCandidateAssessment({...assessment,identity_revision:3});
  assert.equal(card.validated_evaluation,null); assert.equal(card.assessment_status,"unreviewed");
  assert.equal(ui.publicCandidateAssessment({rationale:"SYNTHETIC old rationale",assessment_status:"completed_review"}).assessment_status,"unreviewed");
});
check("public evaluation export drops unknown and sensitive fields at every level",()=>{
  const dirty=structuredClone(record);dirty.raw_prompt="PRIVATE_SENTINEL";dirty.draft={conclusion:"PRIVATE_DRAFT"};
  dirty.final.raw_prompt="PRIVATE_SENTINEL";dirty.final.findings[0].scope.metadata={secret:"PRIVATE_SENTINEL"};
  dirty.reviews[0].raw_payload="PRIVATE_SENTINEL";dirty.reviews[0].item_reviews[0].raw_prompt="PRIVATE_SENTINEL";
  const clean=normalize(ui.publicCandidateEvaluationRecord(dirty));
  assert.doesNotMatch(JSON.stringify(clean),/PRIVATE_|raw_prompt|raw_payload|metadata|draft/);
  assert.equal(clean.final.conclusion,evaluation.conclusion);
  assert.equal(ui.publicCandidateEvaluation({...evaluation,conclusion:"authorization: PRIVATE_SENTINEL"}),null);
});
check("scope text is preserved to 2000 characters without broadening findings",()=>{
  const row=structuredClone(evaluation);row.findings[0].scope.condition="SYNTHETIC "+"x".repeat(1490);
  assert.equal(ui.publicCandidateEvaluation(row).findings[0].scope.condition,row.findings[0].scope.condition);
});
check("candidate renderer labels independent review separately from scientific uncertainty",()=>{
  ui.state.selected={candidate_evaluations:[evaluation],candidate_evaluation_records:[record]};
  const displayed=ui.assessmentDetails(assessment).textContent;
  assert.match(displayed,/Independent assessment reviewed/);assert.match(displayed,/Needs evidence/);
  assert.match(displayed,/SYNTHETIC limited assessment/);assert.match(displayed,/SYNTHETIC adjunct/);
  assert.match(displayed,/Unperformed hypothesis expansion/);
  const old=ui.assessmentDetails({...assessment,validated_evaluation:null}).textContent;
  assert.match(old,/Independent conclusion unreviewed/);assert.doesNotMatch(old,/SYNTHETIC limited assessment/);
});
if (process.argv[2]) {
  const payload=JSON.parse(fs.readFileSync(process.argv[2],"utf8"));
  check("saved nonempty fixture is lossless through UI evaluation projection",()=>{
    const run=payload.runs[0]; assert.ok(run.candidate_evaluations.length);
    assert.deepEqual(normalize(run.candidate_evaluations.map(ui.publicCandidateEvaluation)),run.candidate_evaluations);
    assert.deepEqual(normalize(run.candidate_evaluation_records.map(ui.publicCandidateEvaluationRecord)),run.candidate_evaluation_records);
    for(const event of run.co_scientist.events) for(const ref of [...event.artifact_ids,...event.input_artifact_ids])
      assert.ok(ui.artifactTarget(ref,run),ref);
  });
}
process.stdout.write(count+" DELIVERY_R2 UI contracts passed\n");

