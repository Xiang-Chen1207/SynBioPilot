/* Requirement intake: no research POST until explicit confirmation. No HTML interpolation. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const fieldMap = {disease:"disease", phenotype:"phenotype", population:"population", research_goal:"researchGoal",
    existing_candidates:"existingCandidates", task_intent:"taskIntent", species:"species", chassis_name:"chassis",
    reference_chassis:"referenceChassis", delivery_context:"deliveryContext", conditions:"researchConditions",
    success_criterion:"successCriterion", target_hints:"hints", background_package_id:"backgroundPackageId",
    research_mode:"researchMode", engineering_allowed:"engineeringAllowed"};
  const fieldNames = {disease:"Disease", phenotype:"Phenotype / symptoms", existing_candidates:"Assessment inputs", population:"Population / study subjects",
    research_goal:"Research goal", task_intent:"Task direction"};
  const intents = {disease_exploration:"Explore candidates for a disease", candidate_assessment:"Assess candidate inputs", legacy:"Research with explicitly provided context"};
  const origins = {extracted:"Extracted from your text · Please confirm", unspecified:"Not specified", suggested:"System suggestion · Editable", user_edited:"Edited by you", user_retracted:"Explicitly retracted by you", manual_form:"From research settings · Please confirm"};
  const budgetMap = {max_targets:"maxTargets",literature_max_candidates:"maxCandidates",max_papers:"maxPapers",max_searches:"maxSearches",max_llm_calls:"maxCalls"};
  const state = {draft:null, epoch:0, busy:false, dirty:false, stale:false, pending:null, parseRequest:null, parseKnown:true, submitted:false, submissionBusy:false,touched:new Set()};
  let context;
  const node = (tag, cls, text) => {const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n;};
  const id = () => crypto.randomUUID().replaceAll("-", "");
  const display = value => Array.isArray(value) ? value.join(", ") : String(value ?? "");
  function savePointer() {
    $("restoreIntake").hidden=!(state.draft||state.parseRequest||state.pending);
    try {
      if(!state.draft&&!state.parseRequest&&!state.pending)sessionStorage.removeItem("scientist-intake");
      else sessionStorage.setItem("scientist-intake",JSON.stringify({draft_id:state.draft?.draft_id,request:state.parseRequest?.request_id,
        pending:state.pending ? {id:state.pending.id,draft_id:state.pending.confirmation.draft_id,prepare_targets:state.pending.payload.prepare_targets,name:state.pending.name} : null}));
    } catch (_) { /* Storage may be disabled; server receipts remain authoritative. */ }
  }
  function message(text, error=false) {
    $("intakeStatus").textContent=text; $("intakeStatus").hidden=!text;
    $("intakeStatus").className="callout intake-status"+(error?" amber":"");
    $("intakeStatus").setAttribute("role",error?"alert":"status");
  }
  function refreshControls() {
    const locked=Boolean(state.pending || state.submitted || state.submissionBusy);
    for(const n of $("researchForm").querySelectorAll("input, textarea, select")) n.disabled=locked;
    context.syncMode?.();
    // Keep the existing mode restriction; never enable automatic structures in Co mode.
    const co=$("researchMode").value==="co_scientist";
    $("downloadTargets").disabled=locked||co;$("backgroundPackageId").disabled=locked||!co;
    $("parseRequirement").disabled=state.busy||locked||!$("requirementText").value.trim();
    $("manualIntake").disabled=state.busy||locked;
    $("useManualFields").disabled=state.busy||locked;
    $("refineIntake").disabled=state.busy||locked||state.stale||!state.draft||!$("intakeReply").value.trim();
    $("rewriteIntake").disabled=state.busy||locked; $("cancelIntake").disabled=state.busy||locked;
    $("startResearch").disabled=state.busy||locked||!state.draft||state.stale||Boolean($("intakeReply").value.trim())||Boolean(state.draft?.questions?.length&&!state.dirty)||!$("confirmIntake").checked;
    $("checkIntake").disabled=state.busy; $("resumeIntake").disabled=state.busy;
    $("requirementCount").textContent=$("requirementText").value.length+" / 4000 characters";
    $("researchForm").setAttribute("aria-busy",String(state.busy));
    $("parseRequirement").textContent=state.busy&&!state.pending?"Understanding…":"Understand request →";
    $("startResearch").textContent=state.busy&&state.pending?"Checking and submitting…":"Confirm and start research →";
    $("intakeDraftState").textContent=(state.draft?.receipt?.synthetic?"SYNTHETIC · ":"")+(state.stale?"Request changed · Update draft":state.dirty?"Settings edited · Please confirm":state.submitted?"Submitted":state.draft?.questions?.length?"Key questions need clarification":"Awaiting confirmation");
    const callCount=Number($("maxCalls").value), minutes=Number($("maxMinutes").value);
    $("intakeResearchBudget").textContent=`Research limits: ${callCount} model calls / ${minutes} minutes / 300000 input and 96000 output UTF-8 bytes. Mode: ${$("researchMode").value}. Parsing calls are counted separately and do not change these limits.`;
  }
  function invalidate(source) {
    state.epoch++;
    $("confirmIntake").checked=false;
    if(source==="text") {state.stale=Boolean(state.draft);}
    else if(state.draft) state.dirty=true;
    if(state.draft) $("intakeDraftState").textContent="Changed · Please confirm again";
    refreshControls();
  }
  function applyBrief(brief) {
    for(const [key,element] of Object.entries(fieldMap)) {
      if(key==="engineering_allowed") $(element).checked=Boolean(brief[key]);
      else $(element).value=Array.isArray(brief[key])?brief[key].join(", "):brief[key]??"";
    }
    for(const [key,element] of Object.entries(budgetMap)) if(brief.budget?.[key]!=null) $(element).value=brief.budget[key];
    if(brief.budget?.timeout_seconds!=null) $("maxMinutes").value=brief.budget.timeout_seconds/60;
    $("downloadTargets").checked=false;
  }
  function manualChange(key, value) {
    state.touched.add(key);
    const original=$(fieldMap[key]); original.value=value;
    const summary=$("draftValue-"+key);if(summary)summary.textContent=key==="task_intent"?intents[value]:value||"Not specified";
    const source=$("draftOrigin-"+key); if(source)source.textContent=origins.user_edited;
    if(state.draft)state.draft.provenance[key]={kind:"user_edited",quote:""};
    updateSummary();
    invalidate("field");
  }
  function updateSummary() {
    $("intakeSummary").textContent=($("disease").value||"Disease not specified")+" · "+($("researchGoal").value||intents[$("taskIntent").value]);
  }
  function renderDraft() {
    const draft=state.draft; if(!draft)return;
    $("intakeDraft").hidden=false; $("intakeConfirm").hidden=false;
    $("intakeSummary").textContent=(draft.brief.disease||"Disease not specified")+" · "+(draft.brief.research_goal||intents[draft.brief.task_intent]);
    $("intakeFields").replaceChildren();
    for(const [key,title] of Object.entries(fieldNames)) {
      const field=node("div","field intake-slot"+(key==="task_intent"?" intake-direction":"")), label=node("label","",title), heading=node("div","intake-slot-heading");
      const input=node(key==="task_intent"?"select":key==="research_goal"?"textarea":"input");
      input.id="draftField-"+key; label.htmlFor=input.id;
      if(key==="task_intent") for(const [value,text] of Object.entries(intents)) {const option=node("option","",text);option.value=value;input.append(option);}
      if(key==="research_goal")input.rows=2;
      input.maxLength=key==="disease"?250:1000;
      input.value=Array.isArray(draft.brief[key])?draft.brief[key].join(", "):draft.brief[key]||"";
      input.placeholder="Not specified; you can leave this unknown";
      input.addEventListener(key==="task_intent"?"change":"input",()=>manualChange(key,input.value));
      input.hidden=true;
      const value=node("p","intake-slot-value",key==="task_intent"?intents[draft.brief[key]]:display(draft.brief[key])||"Not specified");value.id="draftValue-"+key;
      const edit=node("button","text-button","Edit");edit.type="button";edit.setAttribute("aria-label","Edit "+title);edit.setAttribute("aria-expanded","false");edit.setAttribute("aria-controls",input.id);
      edit.addEventListener("click",()=>{input.hidden=!input.hidden;value.hidden=!input.hidden;edit.textContent=input.hidden?"Edit":"Done";edit.setAttribute("aria-label",(input.hidden?"Edit ":"Finish editing ")+title);edit.setAttribute("aria-expanded",String(!input.hidden));if(!input.hidden)input.focus();});
      const kind=draft.provenance?.[key]?.kind||"unspecified";
      const source=node("span","field-help",kind==="unspecified"?"":origins[kind]||origins.unspecified);source.id="draftOrigin-"+key;
      heading.append(label,edit);field.append(heading,value,input,source); $("intakeFields").append(field);
    }
    const questions=$("intakeQuestions"); questions.replaceChildren(); questions.hidden=!draft.questions?.length;
    if(draft.questions?.length) {questions.append(node("h3","","Please clarify these questions"));const ul=node("ul");for(const q of draft.questions)ul.append(node("li","",q));questions.append(ul);}
    if(draft.excluded_candidates?.length) {questions.hidden=false;questions.append(node("p","","Excluded: "+draft.excluded_candidates.join(", ")+". These exclusions will also remain in the research goal."));}
    $("intakeReplyGroup").hidden=false;
    $("intakeReplyGroup").open=Boolean(draft.questions?.length);
    $("intakeReplySummary").textContent=draft.questions?.length?"Clarify the task direction":"Add details or corrections (optional)";
    const provenance=$("intakeProvenance"); provenance.replaceChildren();
    for(const [i,text] of draft.messages.entries())provenance.append(node("h3","","Request / follow-up "+(i+1)),node("p","intake-original",text));
    for(const [key,value] of Object.entries(draft.provenance||{})) if(value.quote)provenance.append(node("p","",(fieldNames[key]||key)+" · “"+value.quote+"”"));
    provenance.append(node("p","entity-id","Draft "+draft.draft_id+" · v"+draft.revision),node("p","entity-id","Submission ID "+draft.submission_id));
    provenance.append(node("p","field-help","Parsing call attempts: "+(draft.receipt?.model_calls??"Not recorded")+"; total for this draft chain: "+(draft.receipt?.total_model_calls??"Not recorded")+". Counted separately from the research budget; these are not costs or billed tokens."+(draft.receipt?.synthetic?" SYNTHETIC · Transparent offline model stub; no real model calls.":"")));
    refreshControls();
  }
  function accept(row) {
    if(/^[a-f0-9]{32}$/.test(row.request_id||"")) {$("intakeReceiptLink").href="/api/intake/drafts/"+row.request_id;$("intakeReceiptLink").hidden=false;}
    if(row.status==="failed") {message((row.error?.message||"Request parsing failed. Your input is preserved.")+" Model call attempts: "+(row.receipt?.model_calls??"Not yet confirmed")+", counted separately from the research budget. Research has not started.",true);return false;}
    if(row.status==="parsing") {message("The request is still being parsed or has no final receipt yet. Please check its status; model calls will not be resent automatically.");$("checkIntake").hidden=false;return false;}
    if(!row.brief)return false;
    state.draft=row; state.dirty=false;state.stale=false;state.submitted=row.status==="submitted";
    state.touched=new Set(Object.entries(row.provenance||{}).filter(([,value])=>["user_edited","manual_form"].includes(value.kind)).map(([key])=>key));
    applyBrief(row.brief);$("confirmIntake").checked=false;renderDraft();savePointer();
    message(row.mode==="manual"?"A draft is ready from your research settings. Review its fields before confirming the current request.":row.questions?.length?"The draft is saved. Please clarify the task direction or edit the confirmation card.":"The draft is ready. Review the extracted fields and unspecified items; confirmation creates the research project.");
    return true;
  }
  async function saveDraft() {
    const payload=context.buildPayload();
    const row=await context.api("/api/intake/drafts/"+state.draft.draft_id,"PATCH",{revision:state.draft.revision,brief:{...state.draft.brief,...payload.brief}});
    state.draft=row;state.dirty=false; savePointer(); return {row,payload};
  }
  async function parse(mode="parse", reply=false) {
    if(state.busy||state.pending||state.submitted)return;
    if(mode==="manual")$("manualResearch").open=true;
    const text=reply?$("intakeReply").value.trim():$("requirementText").value.trim();
    if(mode==="parse"&&!text)return;
    const manualValues=mode==="manual"?Object.fromEntries(Object.entries(fieldMap).map(([key,element])=>[key,key==="engineering_allowed"?$(element).checked:["existing_candidates","target_hints"].includes(key)?$(element).value.split(/[,，\n;]/).map(v=>v.trim()).filter(Boolean):$(element).value])):null;
    const manualBudget=mode==="manual"?{...Object.fromEntries(Object.entries(budgetMap).map(([key,element])=>[key,Number($(element).value)])),timeout_seconds:Number($("maxMinutes").value)*60}:null;
    const epoch=++state.epoch; state.busy=true;$("confirmIntake").checked=false;
    $("intakeReceiptLink").hidden=true;
    $("checkIntake").hidden=true;message(mode==="manual"?"Preparing an editable draft from research settings · model calls: 0…":"Parsing your request into a draft · research starts after confirmation. Each parsing attempt has a 45-second limit; retries use a new request.");refreshControls();
    let request;
    try {
      if(reply&&state.dirty)await saveDraft();
      if(epoch!==state.epoch)return;
      const parent=reply?{parent_draft_id:state.draft.draft_id,parent_revision:state.draft.revision}:{};
      const previous=state.parseRequest;
      const reuse=!state.parseKnown&&previous?.text===text&&previous?.mode===mode&&previous?.parent_draft_id===parent.parent_draft_id&&previous?.parent_revision===parent.parent_revision;
      request={request_id:reuse?previous.request_id:id(),text,mode,...parent};
      state.parseRequest=request;state.parseKnown=false;savePointer();
      const row=await context.api("/api/intake/drafts","POST",request);
      state.parseKnown=row.status!=="parsing";
      if(epoch!==state.epoch) {message("The earlier parsing result arrived after your input changed. Your new input was preserved; please parse it again.");return;}
      if(mode==="manual"&&row.brief) {
        row.brief={...row.brief,...manualValues,budget:{...row.brief.budget,...manualBudget}};
        for(const [key,value] of Object.entries(manualValues))if(value&&(Array.isArray(value)?value.length:true))row.provenance[key]={kind:["task_intent","research_mode"].includes(key)&&!state.touched.has(key)?"suggested":"manual_form",quote:""};
        try {context.buildPayload();row.questions=[];}catch(error){row.questions=[error.message];}
      }
      if(accept(row)) {
        if(mode==="manual")state.dirty=true;
        if(reply)$("intakeReply").value="";
        $("intakeDraft").scrollIntoView({block:"start",behavior:"instant"});
      }
      if(mode==="manual") {$("manualResearch").open=true;$("disease").focus();}
    } catch(error) {
      if(epoch!==state.epoch)return;
      message(error.message+" Your input is preserved.",true);
      if(request) {$("checkIntake").hidden=false; await checkParse(false);}
    } finally {state.busy=false;refreshControls();}
  }
  async function checkParse(allowApply=true) {
    if(!state.parseRequest)return;
    try {
      const row=await context.api("/api/intake/drafts/"+state.parseRequest.request_id);
      state.parseKnown=row.status!=="parsing";
      const input=state.parseRequest.parent_draft_id?$("intakeReply").value.trim():$("requirementText").value.trim();
      if(input!==state.parseRequest.text) {message("This receipt belongs to earlier input. Your current input was preserved; please parse the new request.");return;}
      if(allowApply)accept(row); else message(row.status==="parsing"?"The original request was accepted and is still being parsed. Please check its status; do not parse it again.":"The original request receipt was found. Select “Check request status” to restore the result.");
    } catch(error) {message(error.status===404?"The original parsing receipt has not been found. Your input is preserved; you may choose to parse again. The system will not retry automatically.":error.message,true);}
  }
  async function finish(projectId,runId,run=null) {
    if(!run)run=await context.api("/api/projects/"+encodeURIComponent(projectId)+"/runs/"+encodeURIComponent(runId)+"/results");
    const epoch=state.pending?.epoch??context.getSelection();
    state.submitted=true;state.pending=null;$("checkIntake").hidden=true;$("resumeIntake").hidden=true;
    if(state.draft)state.draft.status="submitted";savePointer();message("Research was submitted once. The draft and original request receipt are preserved.");
    await context.onSubmitted(projectId,run,epoch);
  }
  async function checkSubmission() {
    if(!state.pending)return;
    $("resumeIntake").hidden=true;
    try {
      const receipt=await context.api("/api/intake/submissions/"+state.pending.id);
      const submission=receipt.submissions;
      if(submission?.status==="completed") {await finish(submission.project_id,submission.run_id);return;}
      if(submission || receipt.projects?.status!=="completed") {message("The submission was accepted or its outcome is unknown. Please check this receipt and the project history; it will not start again.",true);}
      else {message("The separate project was found, but research has no acceptance receipt yet. You can continue the same submission with its original ID.");$("resumeIntake").hidden=false;}
      if(receipt.projects?.project_id) {
        let link=$("intakeHistory");if(!link){link=node("a","button secondary","View separate project history");link.id="intakeHistory";$("checkIntake").parentElement.append(link);}
        link.href="/?project="+encodeURIComponent(receipt.projects.project_id)+"#overview";link.target="_blank";link.rel="noopener";
      }
    } catch(error) {
      message(error.status===404?"This submission has no acceptance receipt yet. You can continue the same submission with the same ID; no duplicate task will be created.":"The submission outcome could not be confirmed. Please check its status later; do not create another research run.",true);
      $("resumeIntake").hidden=error.status!==404;
    }
    $("checkIntake").hidden=!state.pending;refreshControls();
  }
  async function sendPending() {
    const pending=state.pending;if(!pending)return;
    const project=await context.api("/api/projects","POST",{name:pending.name,request_id:pending.id,intake_confirmation:pending.confirmation});
    const run=await context.api("/api/projects/"+encodeURIComponent(project.project_id)+"/runs","POST",{
      ...pending.payload,request_id:pending.id,intake_confirmation:pending.confirmation});
    await finish(project.project_id,run.run_id,run);
  }
  async function submit() {
    if(state.busy||state.pending||state.submitted||!state.draft||state.stale||$("intakeReply").value.trim()||!$("confirmIntake").checked)return;
    if(state.draft.questions?.length&&!state.dirty){message("Please answer the key questions or edit the confirmation card first.",true);return;}
    state.busy=true;state.submissionBusy=true;refreshControls();$("researchError").hidden=true;
    try {
      const {row,payload}=await saveDraft();
      state.pending={id:row.submission_id,confirmation:{draft_id:row.draft_id,revision:row.revision,confirmed:true},
        payload:{brief:row.brief,prepare_targets:payload.prepare_targets},name:row.brief.disease.slice(0,200)+" · Separate research",epoch:context.getSelection()};
      savePointer();refreshControls();await sendPending();
    } catch(error) {
      message(error.message,true);$("confirmIntake").checked=false;
      if(state.pending)await checkSubmission();
    } finally {state.busy=false;state.submissionBusy=false;refreshControls();}
  }
  async function restore() {
    if(state.busy)return;
    let pointer;try{pointer=JSON.parse(sessionStorage.getItem("scientist-intake")||"null");}catch(_){return;}
    if(!pointer)return;
    state.busy=true;refreshControls();
    try {
      const row=await context.api("/api/intake/drafts/"+(pointer.pending?.draft_id||pointer.draft_id||pointer.request));
      $("requirementText").value=row.messages?.[0]||"";accept(row);
      if(pointer.pending&&row.brief) {
        state.pending={id:pointer.pending.id,confirmation:{draft_id:row.draft_id,revision:row.revision,confirmed:true},
          // Older recovery pointers used this fixed project suffix. Preserve
          // that name when retrying the same ID; new pointers store their name.
          payload:{brief:row.brief,prepare_targets:Boolean(pointer.pending.prepare_targets)},name:pointer.pending.name??row.brief.disease.slice(0,200)+" · 独立研究",epoch:context.getSelection()};
        await checkSubmission();
      } else if(row.status==="submitted")await finish(row.project_id,row.run_id);
      else if(row.status==="parsing") {state.parseRequest={request_id:row.request_id,text:row.messages.at(-1)||""};$("checkIntake").hidden=false;}
    } catch(error){message(error.message,true);} finally{state.busy=false;refreshControls();}
  }
  function reset(clear=false) {
    if(state.busy||state.pending)return;
    if(!clear&&state.draft?.messages?.length) {
      const edits=Object.entries(fieldNames).filter(([key])=>["user_edited","manual_form"].includes(state.draft.provenance?.[key]?.kind))
        .map(([key,title])=>"Settings edit: "+title+": "+(key==="task_intent"?intents[$(fieldMap[key]).value]:$(fieldMap[key]).value||"Cleared; not specified"));
      $("requirementText").value=[...state.draft.messages,...edits].join("\n");
    }
    state.epoch++;state.draft=null;state.dirty=false;state.stale=false;state.submitted=false;state.parseRequest=null;state.parseKnown=true;
    $("intakeDraft").hidden=true;$("intakeConfirm").hidden=true;$("checkIntake").hidden=true;$("resumeIntake").hidden=true;
    $("intakeReceiptLink").hidden=true;
    $("confirmIntake").checked=false;$("intakeReply").value="";
    if(clear) {
      state.touched.clear();
      $("requirementText").value="";
      for(const [key,element] of Object.entries(fieldMap)) {
        if(key==="research_mode")continue;
        if(key==="engineering_allowed")$(element).checked=false;
        else $(element).value=key==="task_intent"?"disease_exploration":"";
      }
    }
    if($("intakeHistory"))$("intakeHistory").hidden=true;
    message(clear?"The draft was cancelled. Research has not started.":"You can revise the original request and parse it again. The original draft receipt is preserved.");savePointer();refreshControls();$("requirementText").focus();
  }
  function init(options) {
    context=options;
    if(!$("requirementText"))return;
    $("taskIntent").value="disease_exploration";
    $("requirementText").addEventListener("input",()=>invalidate("text"));
    $("intakeReply").addEventListener("input",()=>{state.epoch++;$("confirmIntake").checked=false;refreshControls();});
    $("confirmIntake").addEventListener("change",refreshControls);
    $("parseRequirement").addEventListener("click",()=>parse());
    $("manualIntake").addEventListener("click",()=>parse("manual"));
    $("useManualFields").addEventListener("click",()=>parse("manual"));
    $("refineIntake").addEventListener("click",()=>parse("parse",true));
    $("rewriteIntake").addEventListener("click",()=>reset(false));
    $("cancelIntake").addEventListener("click",()=>reset(true));
    $("restoreIntake").addEventListener("click",restore);
    $("checkIntake").addEventListener("click",async()=>{if(state.busy)return;state.busy=true;refreshControls();try{if(state.pending)await checkSubmission();else await checkParse();}finally{state.busy=false;refreshControls();}});
    $("resumeIntake").addEventListener("click",async()=>{if(state.busy||!state.pending)return;state.busy=true;refreshControls();try{await sendPending();}catch(error){message(error.message,true);await checkSubmission();}finally{state.busy=false;refreshControls();}});
    for(const example of document.querySelectorAll("[data-intake-example]"))example.addEventListener("click",()=>{if(state.busy||state.pending)return;$("requirementText").value=example.dataset.intakeExample;invalidate("text");$("requirementText").focus();});
    for(const field of $("manualResearch").querySelectorAll("input, textarea, select"))field.addEventListener("input",()=>{
      const key=Object.keys(fieldMap).find(k=>fieldMap[k]===field.id), card=key&&$("draftField-"+key);
      if(key)state.touched.add(key);
      if(card) {card.value=field.value;$("draftOrigin-"+key).textContent=origins.user_edited;$("draftValue-"+key).textContent=key==="task_intent"?intents[field.value]:field.value||"Not specified";}
      if(key&&state.draft)state.draft.provenance[key]={kind:"user_edited",quote:""};
      updateSummary();
      invalidate("field");
    });
    $("manualResearch").addEventListener("change",refreshControls);
    try{const pointer=JSON.parse(sessionStorage.getItem("scientist-intake")||"null");$("restoreIntake").hidden=!(pointer?.draft_id||pointer?.request||pointer?.pending);}catch(_){}
    refreshControls();
    if(location.protocol==="file:"){state.submitted=true;message("This local file is not connected to the parsing service. Please open it through the local HTTP service.",true);refreshControls();return;}
    context.api("/api/intake/limits").then(value=>{
      if(!value.model_available)message("Model parsing is disabled. Fill in research settings, or configure model access in your local settings.");
    }).catch(()=>message("The parsing service is temporarily unavailable. Your input is preserved; once connected, you can parse the request or save a draft from research settings.",true));
  }
  window.ResearchIntake={init,submit,startNew:()=>{if(state.submitted)reset(true);}};
})();

