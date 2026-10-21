/* SYNTHETIC DOM/HTTP contracts for the real intake controller + real payload builder. */
"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const root=path.resolve(__dirname,"..");
const source=fs.readFileSync(path.join(root,"scientist/web/intake.js"),"utf8");
const app=fs.readFileSync(path.join(root,"scientist/web/app.js"),"utf8");
const html=fs.readFileSync(path.join(root,"scientist/web/index.html"),"utf8");
const TEXT="我想了解NAC能不能改善自闭症患者的易激惹症状，先帮我分析文献证据";
const clone=x=>JSON.parse(JSON.stringify(x));
function draft({manual=false,population=""}={}) {return {draft_id:"a".repeat(32),request_id:"a".repeat(32),submission_id:"b".repeat(32),revision:1,
 status:manual?"needs_clarification":"ready",mode:manual?"manual":"parse",messages:[TEXT],questions:manual?["请填写疾病"]:[],excluded_candidates:[],receipt:{model_calls:manual?0:1,synthetic:true},
 provenance:{disease:{kind:"extracted",quote:"自闭症"},population:{kind:population?"extracted":"unspecified",quote:population}},
 brief:{disease:manual?"":"自闭症",phenotype:manual?"":"易激惹",existing_candidates:manual?[]:["NAC"],population,research_goal:manual?"":"分析文献证据",task_intent:manual?"disease_exploration":"candidate_assessment",research_mode:"co_scientist",species:"",chassis_name:"",chassis_id:"",reference_chassis:"",delivery_context:"",conditions:"",success_criterion:"",target_hints:[],background_package_id:"",engineering_allowed:false,
 budget:{max_targets:2,literature_max_candidates:12,max_papers:12,max_searches:6,max_llm_calls:16,timeout_seconds:3600,max_input_tokens:300000,max_output_tokens:96000}}};}
function harness(overrides={}) {
 const nodes={},calls=[],submitted=[],storage=new Map();let seq=0,stored=draft();
 class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.dataset={};this.attrs={};this._value="";this.checked=false;this.disabled=false;this.hidden=false;this.parentElement=null;this.ownText="";}
  set id(v){this._id=v;nodes[v]=this;}get id(){return this._id;}
  set value(v){this._value=String(v);}get value(){return this._value;}
  set textContent(v){this.ownText=String(v);this.children=[];}get textContent(){return this.ownText+this.children.map(n=>n.textContent).join(" ");}
  append(...ns){for(const n of ns){n.parentElement=this;this.children.push(n);}}
  replaceChildren(...ns){this.children=[];this.append(...ns);}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,fn){(this.listeners[k] ||= []).push(fn);}
  async fire(k){for(const fn of this.listeners[k]||[])await fn({target:this,preventDefault(){}});}
  querySelectorAll(){return Object.values(nodes).filter(n=>["INPUT","TEXTAREA","SELECT"].includes(n.tagName)&& (this.id==="researchForm"||advanced.has(n.id)));}
  closest(){return new Element("label");}focus(){document.activeElement=this;}scrollIntoView(){}
 }
 for(const m of html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){const n=new Element(m[1]);n.id=m[3];n.hidden=/\bhidden\b/.test(m[2]);n.disabled=/\bdisabled\b/.test(m[2]);n.value=m[2].match(/\bvalue="([^"]*)"/)?.[1]||"";}
 const advanced=new Set([...html.slice(html.indexOf('<details id="manualResearch"'),html.indexOf('<section id="intakeConfirm"')).matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));
 const recovery=new Element("div");recovery.append(nodes.checkIntake,nodes.resumeIntake);
 const examples=["研究自闭症",TEXT].map(text=>{const n=new Element("button");n.dataset.intakeExample=text;return n;});
 const document={createElement:t=>new Element(t),getElementById:id=>nodes[id]||null,querySelectorAll:s=>s==="[data-intake-example]"?examples:[],activeElement:null};
 const window={addEventListener(){},scrollTo(){}};
 const sandbox={document,window,Node:Element,location:{protocol:"http:",href:"http://synthetic.invalid/",hash:""},URL,crypto:{randomUUID:()=> (++seq).toString(16).padStart(32,"0")},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},fetch(){throw new Error("No actual fetch is allowed");}};
 vm.createContext(sandbox);vm.runInContext(source,sandbox);vm.runInContext(app.replace(/\ninit\(\);\s*$/,"")+"\nglobalThis.builder=buildResearchPayload;globalThis.configure=configureResearchMode;",sandbox);
 nodes.researchMode.value="co_scientist";sandbox.configure();
 async function api(url,method="GET",body) {
  calls.push({url,method,body:body===undefined?undefined:clone(body)});
  if(overrides.api){const answer=overrides.api(url,method,body);if(answer!==undefined)return await answer;}
  if(url==="/api/intake/limits")return {model_available:true};
  if(url==="/api/intake/drafts"&&method==="POST"){stored=draft({manual:body.mode==="manual",population:body.parent_draft_id?"成年人":""});stored.draft_id=body.request_id;stored.request_id=body.request_id;return clone(stored);}
  if(method==="PATCH"){stored={...stored,brief:clone(body.brief),revision:body.revision+1,questions:[],status:"ready"};return clone(stored);}
  if(url.startsWith("/api/intake/drafts/"))return clone(stored);
  if(url==="/api/projects"&&method==="POST")return {project_id:"p_synthetic_intake"};
  if(url.endsWith("/runs")&&method==="POST")return {run_id:"r_synthetic_intake",project_id:"p_synthetic_intake",brief:clone(body.brief),status:"partial"};
  if(url.endsWith("/results"))return {run_id:"r_synthetic_intake",project_id:"p_synthetic_intake",brief:stored.brief,status:"partial"};
  throw new Error("Unconfigured SYNTHETIC request: "+url);
 }
 window.ResearchIntake.init({api,buildPayload:sandbox.builder,getSelection:()=>7,onSubmitted:async(...args)=>submitted.push(args)});
 return {nodes,calls,submitted,examples,storage,ui:window.ResearchIntake,build:sandbox.builder};
}
async function parsed(h){h.nodes.requirementText.value=TEXT;await h.nodes.requirementText.fire("input");await h.nodes.parseRequirement.fire("click");}
let checks=0;async function check(name,fn){await fn();checks++;console.log("PASS "+name);}
(async()=>{
 await check("startup and examples never parse or start research",async()=>{const h=harness();await h.examples[1].fire("click");assert.equal(h.nodes.requirementText.value,TEXT);assert.equal(h.calls.filter(c=>c.method==="POST").length,0);});
 await check("parsing only creates a draft, keeps unknowns empty and requires confirmation",async()=>{const h=harness();await parsed(h);assert.equal(h.nodes["draftField-existing_candidates"].value,"NAC");assert.equal(h.nodes["draftField-population"].value,"");assert.equal(h.build().brief.species,"");assert.equal(h.build().brief.chassis_name,"");assert.equal(h.build().brief.reference_chassis,"");await h.ui.submit();assert.equal(h.calls.filter(c=>c.url==="/api/projects").length,0);});
 await check("manual card edits synchronize advanced fields and revoke confirmation",async()=>{const h=harness();await parsed(h);h.nodes.confirmIntake.checked=true;await h.nodes.confirmIntake.fire("change");h.nodes["draftField-population"].value="成年人";await h.nodes["draftField-population"].fire("input");assert.equal(h.nodes.population.value,"成年人");assert.equal(h.nodes.confirmIntake.checked,false);assert.equal(h.nodes.startResearch.disabled,true);h.nodes.phenotype.value="手动终点";await h.nodes.phenotype.fire("input");assert.equal(h.nodes["draftField-phenotype"].value,"手动终点");});
 await check("stale parse response cannot overwrite newly edited request",async()=>{let resolve;const wait=new Promise(r=>resolve=r);const h=harness({api:(url,method)=>url==="/api/intake/drafts"&&method==="POST"?wait:undefined});h.nodes.requirementText.value=TEXT;await h.nodes.requirementText.fire("input");const pending=h.nodes.parseRequirement.fire("click");h.nodes.requirementText.value="新需求";await h.nodes.requirementText.fire("input");resolve(draft());await pending;assert.equal(h.nodes.requirementText.value,"新需求");assert.equal(h.nodes.intakeDraft.hidden,true);assert.equal(h.nodes.startResearch.disabled,true);});
 await check("short reply references the same parent and saves manual changes before extraction",async()=>{const h=harness();await parsed(h);h.nodes["draftField-phenotype"].value="限定终点";await h.nodes["draftField-phenotype"].fire("input");h.nodes.intakeReply.value="只关注成年人";await h.nodes.intakeReply.fire("input");await h.nodes.refineIntake.fire("click");const parses=h.calls.filter(c=>c.url==="/api/intake/drafts"&&c.method==="POST");assert.equal(parses.length,2);assert.equal(parses[1].body.parent_draft_id,parses[0].body.request_id);assert.equal(parses[1].body.text,"只关注成年人");assert.equal(h.calls.find(c=>c.method==="PATCH").body.brief.phenotype,"限定终点");assert.equal(h.nodes.intakeReply.value,"");});
 await check("explicit confirmation produces one normal project/run handoff on double click",async()=>{const h=harness();await parsed(h);h.nodes.confirmIntake.checked=true;await h.nodes.confirmIntake.fire("change");await Promise.all([h.ui.submit(),h.ui.submit()]);const projects=h.calls.filter(c=>c.url==="/api/projects"),runs=h.calls.filter(c=>c.url.endsWith("/runs")&&c.method==="POST");assert.equal(projects.length,1);assert.equal(runs.length,1);assert.equal(projects[0].body.request_id,runs[0].body.request_id);assert.equal(runs[0].body.intake_confirmation.confirmed,true);assert.equal(runs[0].body.brief.budget.max_output_tokens,96000);assert.equal(runs[0].body.brief.chassis_id,"");assert.equal(h.submitted.length,1);});
 await check("unparsed supplemental text revokes confirmation and blocks submission",async()=>{const h=harness();await parsed(h);h.nodes.confirmIntake.checked=true;h.nodes.intakeReply.value="另外一个重要条件";await h.nodes.intakeReply.fire("input");assert.equal(h.nodes.confirmIntake.checked,false);h.nodes.confirmIntake.checked=true;await h.ui.submit();assert.equal(h.calls.filter(c=>c.url==="/api/projects").length,0);});
 await check("unknown submission checks receipts and never blindly repeats POST",async()=>{const h=harness({api:(url,method)=>{if(url.endsWith("/runs")&&method==="POST")throw new Error("SYNTHETIC lost response");if(url.startsWith("/api/intake/submissions/"))return {projects:{status:"completed",project_id:"p_synthetic_intake"},submissions:{status:"unknown"}};}});await parsed(h);h.nodes.confirmIntake.checked=true;await h.ui.submit();await h.ui.submit();assert.equal(h.calls.filter(c=>c.url.endsWith("/runs")&&c.method==="POST").length,1);assert.ok(h.calls.some(c=>c.url.startsWith("/api/intake/submissions/")&&c.method==="GET"));assert.equal(h.nodes.resumeIntake.hidden,true);assert.equal(h.submitted.length,0);});
 await check("lost response with completed receipt resumes by GET without resubmission",async()=>{const h=harness({api:(url,method)=>{if(url.endsWith("/runs")&&method==="POST")throw new Error("SYNTHETIC lost response");if(url.startsWith("/api/intake/submissions/"))return {submissions:{status:"completed",project_id:"p_synthetic_intake",run_id:"r_synthetic_intake"}};}});await parsed(h);h.nodes.confirmIntake.checked=true;await h.ui.submit();assert.equal(h.calls.filter(c=>c.url.endsWith("/runs")&&c.method==="POST").length,1);assert.equal(h.submitted.length,1);});
 await check("unknown project creation does not offer a blind continue action",async()=>{const h=harness({api:(url,method)=>{if(url==="/api/projects"&&method==="POST")throw new Error("SYNTHETIC unknown project");if(url.startsWith("/api/intake/submissions/"))return {projects:{status:"unknown"}};}});await parsed(h);h.nodes.confirmIntake.checked=true;await h.ui.submit();assert.equal(h.nodes.resumeIntake.hidden,true);assert.equal(h.calls.filter(c=>c.url.endsWith("/runs")&&c.method==="POST").length,0);});
 await check("unknown parse retry reuses the original id after a receipt check",async()=>{const h=harness({api:(url,method)=>{if(url==="/api/intake/drafts"&&method==="POST")throw new Error("SYNTHETIC unknown parse");if(url.startsWith("/api/intake/drafts/")&&method==="GET"){const e=new Error("not found");e.status=404;throw e;}}});await parsed(h);await h.nodes.parseRequirement.fire("click");const posts=h.calls.filter(c=>c.url==="/api/intake/drafts"&&c.method==="POST");assert.equal(posts.length,2);assert.equal(posts[0].body.request_id,posts[1].body.request_id);assert.ok(h.calls.some(c=>c.url.startsWith("/api/intake/drafts/")&&c.method==="GET"));});
 await check("failed model result preserves text and cannot masquerade as a parsed draft",async()=>{const h=harness({api:(url,method,body)=>url==="/api/intake/drafts"&&method==="POST"&&body.mode==="parse"?{status:"failed",error:{message:"SYNTHETIC timeout"}}:undefined});await parsed(h);assert.equal(h.nodes.requirementText.value,TEXT);assert.equal(h.nodes.intakeDraft.hidden,true);await h.nodes.manualIntake.fire("click");assert.equal(h.nodes["draftField-disease"].value,"");assert.equal(h.nodes.manualResearch.open,true);});
 await check("manual fallback preserves already entered advanced fields and budgets",async()=>{const h=harness();h.nodes.disease.value="手动疾病";h.nodes.population.value="手动人群";h.nodes.maxCalls.value="5";await h.nodes.useManualFields.fire("click");assert.equal(h.nodes["draftField-disease"].value,"手动疾病");assert.equal(h.nodes["draftField-population"].value,"手动人群");assert.equal(h.nodes.maxCalls.value,"5");assert.equal(h.calls.find(c=>c.method==="POST").body.mode,"manual");});
 await check("new research clears prior candidate and population fields before manual fallback",async()=>{const h=harness();await parsed(h);h.nodes.confirmIntake.checked=true;await h.ui.submit();h.ui.startNew();assert.equal(h.nodes.existingCandidates.value,"");assert.equal(h.nodes.population.value,"");assert.equal(h.nodes.chassis.value,"");await h.nodes.manualIntake.fire("click");assert.equal(h.nodes["draftField-existing_candidates"].value,"");assert.equal(h.nodes["draftField-population"].value,"");});
 await check("rewrite preserves the original description and explicit manual corrections",async()=>{const h=harness();await parsed(h);h.nodes["draftField-population"].value="成年人";await h.nodes["draftField-population"].fire("input");await h.nodes.rewriteIntake.fire("click");assert.match(h.nodes.requirementText.value,/我想了解NAC/);assert.match(h.nodes.requirementText.value,/Settings edit: Population \/ study subjects: 成年人/);assert.equal(h.nodes.intakeDraft.hidden,true);});
 await check("intake redraw preserves original mode-specific control restrictions",async()=>{const h=harness();await parsed(h);h.nodes.researchMode.value="standard";h.nodes.downloadTargets.checked=true;await h.nodes.researchMode.fire("input");assert.equal(h.nodes.backgroundPackageId.disabled,true);assert.equal(h.nodes.downloadTargets.disabled,false);assert.equal(h.nodes.downloadTargets.checked,true);h.nodes.researchMode.value="co_scientist";await h.nodes.researchMode.fire("input");assert.equal(h.nodes.downloadTargets.disabled,true);assert.equal(h.nodes.backgroundPackageId.disabled,false);});
 await check("manual fallback labels untouched direction as a suggestion, not a user fact",async()=>{const h=harness();h.nodes.disease.value="手动疾病";await h.nodes.manualIntake.fire("click");assert.match(h.nodes["draftOrigin-task_intent"].textContent,/System suggestion/);});
 await check("cancel removes the local recovery pointer without deleting server receipts",async()=>{const h=harness();await parsed(h);await h.nodes.cancelIntake.fire("click");assert.equal(h.nodes.restoreIntake.hidden,true);assert.equal(h.storage.has("scientist-intake"),false);assert.equal(h.calls.some(c=>c.method==="DELETE"),false);});
 await check("English labels and receipts preserve Chinese user input and quoted provenance",async()=>{
  const h=harness();await parsed(h);
  assert.equal(h.nodes.parseRequirement.textContent,"Understand request →");
  assert.equal(h.nodes.startResearch.textContent,"Confirm and start research →");
  assert.equal(h.nodes.requirementCount.textContent,TEXT.length+" / 4000 characters");
  assert.equal(h.nodes.intakeDraftState.textContent,"SYNTHETIC · Awaiting confirmation");
  assert.match(h.nodes.intakeFields.textContent,/Population \/ study subjects/);
  assert.match(h.nodes.intakeFields.textContent,/Assess specified candidates/);
  assert.equal(h.nodes["draftOrigin-disease"].textContent,"Extracted from your text · Please confirm");
  assert.equal(h.nodes.requirementText.value,TEXT);
  assert.equal(h.nodes["draftField-disease"].value,"自闭症");
  assert.equal(h.nodes["draftField-phenotype"].value,"易激惹");
  assert.ok(h.nodes.intakeProvenance.textContent.includes(TEXT));
  assert.ok(h.nodes.intakeProvenance.textContent.includes("“自闭症”"));
  assert.match(h.nodes.intakeProvenance.textContent,/Parsing call attempts: 1/);
  assert.match(h.nodes.intakeProvenance.textContent,/no real model calls/);
 });
 await check("legacy pending submission keeps its original project name after UI translation",async()=>{
  const h=harness({api:url=>url.startsWith("/api/intake/submissions/")?{projects:{status:"completed",project_id:"p_synthetic_intake"}}:undefined});
  h.storage.set("scientist-intake",JSON.stringify({draft_id:"a".repeat(32),pending:{id:"b".repeat(32),draft_id:"a".repeat(32),prepare_targets:false}}));
  await h.nodes.restoreIntake.fire("click");assert.equal(h.nodes.resumeIntake.hidden,false);
  await h.nodes.resumeIntake.fire("click");
  const projects=h.calls.filter(c=>c.url==="/api/projects"&&c.method==="POST");
  assert.equal(projects.length,1);assert.equal(projects[0].body.name,"自闭症 · 独立研究");
  assert.equal(projects[0].body.request_id,"b".repeat(32));
  assert.equal(h.calls.filter(c=>c.url.endsWith("/runs")&&c.method==="POST").length,1);
 });
 await check("new pending submission stores its English project name for exact recovery",async()=>{
  const h=harness({api:(url,method)=>{if(url.endsWith("/runs")&&method==="POST")throw new Error("SYNTHETIC response unknown");if(url.startsWith("/api/intake/submissions/"))return {projects:{status:"completed",project_id:"p_synthetic_intake"}};}});
  await parsed(h);h.nodes.confirmIntake.checked=true;await h.ui.submit();
  const pointer=JSON.parse(h.storage.get("scientist-intake"));
  assert.equal(pointer.pending.name,"自闭症 · Separate research");
  assert.equal(h.calls.find(c=>c.url==="/api/projects"&&c.method==="POST").body.name,pointer.pending.name);
  const recovered=harness({api:url=>url.startsWith("/api/intake/submissions/")?{projects:{status:"completed",project_id:"p_synthetic_intake"}}:undefined});
  recovered.storage.set("scientist-intake",JSON.stringify(pointer));
  await recovered.nodes.restoreIntake.fire("click");await recovered.nodes.resumeIntake.fire("click");
  const project=recovered.calls.find(c=>c.url==="/api/projects"&&c.method==="POST");
  assert.equal(project.body.name,pointer.pending.name);assert.equal(project.body.request_id,pointer.pending.id);
 });
 console.log(checks+" intake UI contracts passed");
})().catch(error=>{console.error(error);process.exitCode=1;});

