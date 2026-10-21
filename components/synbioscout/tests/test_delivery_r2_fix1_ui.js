/* R2 FIX1 saved SYNTHETIC cards and links; no browser, service or network. */
"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const root=path.resolve(__dirname,"..");
const filename=process.argv[2]||path.join(root,"docs/nac_asd_iteration/DELIVERY_R2_FIX1_SYNTHETIC_PUBLIC.json");
const fixture=JSON.parse(fs.readFileSync(filename,"utf8"));
class SyntheticNode {
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.ownText="";}
 set textContent(value){this.ownText=String(value);this.children=[];}
 get textContent(){return this.ownText+this.children.map(x=>x.textContent||"").join(" ");}
 append(...children){this.children.push(...children);}
 addEventListener(){}
}
const sandbox={document:{createElement:tag=>new SyntheticNode(tag)},Node:SyntheticNode,
 location:{protocol:"file:",href:"file:///SYNTHETIC/index.html"},fetch(){throw new Error("No network permitted");}};
vm.createContext(sandbox);
const source=fs.readFileSync(path.join(root,"scientist/web/app.js"),"utf8");
vm.runInContext(source.replace(/\ninit\(\);\s*$/,"")+"\nglobalThis.ui={publicCandidateEvaluation,publicCandidateEvaluationRecord,publicCandidateAssessment,assessmentDetails,artifactTarget,state};",sandbox);
const ui=sandbox.ui,normal=x=>JSON.parse(JSON.stringify(x));
let checks=0;
function check(name,fn){fn();checks++;process.stdout.write("PASS "+name+"\n");}
assert.equal(fixture.fixture.synthetic,true);
for(const run of fixture.runs){
 check(run.presentation.fixture.case+" current conclusion and rendering agree",()=>{
  const expected=run.presentation.fixture.expected_current_evaluations;
  assert.equal(run.candidate_evaluations.length,expected);
  assert.deepEqual(normal(run.candidate_evaluations.map(ui.publicCandidateEvaluation)),run.candidate_evaluations);
  assert.deepEqual(normal(run.candidate_evaluation_records.map(ui.publicCandidateEvaluationRecord)),run.candidate_evaluation_records);
  ui.state.selected=run;
  for(const raw of run.candidate_assessments){
   const card=ui.publicCandidateAssessment(raw),display=ui.assessmentDetails(raw).textContent;
   assert.equal(card.assessment_status,expected?"completed_review":"unreviewed");
   assert.equal(Boolean(card.validated_evaluation),Boolean(expected));
   if(expected){assert.match(display,/Independent assessment reviewed/);assert.match(display,/SYNTHETIC reviewed assessment/);}
   else {assert.match(display,/Independent conclusion unreviewed/);assert.doesNotMatch(display,/SYNTHETIC reviewed assessment/);}
  }
  for(const event of run.co_scientist.events)for(const ref of [...event.input_artifact_ids,...event.artifact_ids])
   assert.ok(ui.artifactTarget(ref,run),run.run_id+":"+ref);
 });
}
check("withdrawn conclusion retains an inspectable historical review without a final",()=>{
 const run=fixture.runs.find(r=>r.presentation.fixture.case==="new_positive_linked");
 const record=run.candidate_evaluation_records[0];
 assert.equal(record.recorded_status,"completed_review");assert.equal(record.status,"unreviewed");assert.equal(record.final,null);
 const ref="assessment_review:"+record.reviews[0].review_id;
 assert.equal(ui.artifactTarget(ref,run).item.assessment_status,"unreviewed");
});
check("ambiguous shared code cannot select the first of two entities",()=>{
 const run=fixture.runs.find(r=>r.presentation.fixture.case==="shared_alias");
 assert.equal(run.candidate_assessments.length,2);assert.equal(run.candidate_evaluations.length,0);
 assert.ok(run.candidate_assessments.every(c=>ui.publicCandidateAssessment(c).validated_evaluation===null));
});
process.stdout.write(checks+" R2 FIX1 UI contracts passed\n");

