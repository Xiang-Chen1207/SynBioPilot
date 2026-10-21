"""Parent-review counterexamples through production functions and synthetic workers."""
import json
import subprocess
import sys

import pytest
import requests
from test_coscientist import Controller, CoSources, Worker, execute
from test_research_flow import attach, draft, hypothesis, setup_flow, source

from scientist.co_models import ResearchContract
from scientist.models import ExecutionStatus, ResearchBrief, ResearchBudget, ScientistSettings
from scientist.presentation import result_document
from scientist.research import ResearchEngine
from scientist.research_flow import supply_dependency
from scientist.research_verify import verify_run
from scientist.store import ProjectStore


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("SYNTHETIC parent-review tests must not make network requests")
    monkeypatch.setattr(requests.sessions.Session, "request", reject)


SUPPLY_WORDINGS = [
    "is producing", "produces", "produced", "has produced", "will produce", "production of",
    "synthesizes", "synthesises", "is synthesizing", "biosynthesizes", "biosynthesis of",
    "supplies", "is supplying", "provides", "secretes", "secretion of", "yields", "makes",
    "正在生产", "能够合成", "可以供给", "供应", "提供", "分泌", "产生", "生产能力为",
]


@pytest.mark.parametrize("wording", SUPPLY_WORDINGS)
def test_supply_word_families_cannot_borrow_another_strain(wording):
    engine, flow = setup_flow()
    h = hypothesis()
    h.claim = f"{h.chassis_name} {wording} Illustrative compound; exposure needs measurement"
    engine.state.hypotheses = [h]
    flow.register(h)
    e = attach(engine, h, quote="OTHER STRAIN production of Illustrative compound was measured.", system="OTHER STRAIN")
    main = flow.claims_for(h)[0]
    flow.adjudicate([h], [draft(main, [e.evidence_id])])
    assert supply_dependency(h.claim)
    assert main.state == "context_only" and not flow.main_reviewed(h)
    assert flow.verify()["valid"]  # valid receipt of abstention, not biological support


@pytest.mark.parametrize("claim", [
    "Illustrative compound is being produced by SYNTHETIC strain",
    "Illustrative compound was synthesized by SYNTHETIC strain",
    "Production of Illustrative compound by SYNTHETIC strain is unknown",
    "SYNTHETIC strain may produce Illustrative compound",
    "Whether SYNTHETIC strain supplies Illustrative compound remains unverified",
    "Illustrative compound由SYNTHETIC strain合成，暴露待验证",
    "SYNTHETIC strain的生产能力尚未验证",
    "SYNTHETIC strain可能合成Illustrative compound",
])
def test_passive_and_unknown_supply_remain_unproven_research_hypotheses(claim):
    engine, flow = setup_flow()
    h = hypothesis(); h.claim = claim; engine.state.hypotheses = [h]; flow.register(h)
    e = attach(engine, h, quote="OTHER STRAIN production of Illustrative compound was measured.", system="OTHER STRAIN")
    flow.adjudicate([h], [draft(flow.claims_for(h)[0], [e.evidence_id])])
    assert not flow.main_reviewed(h) and h.chassis.status == "unknown"
    assert flow.claims_for(h)[0].state == "context_only"


@pytest.mark.parametrize("quote,scope", [
    ("SYNTHETIC strain production of Illustrative compound was measured.", "database"),
    ("SYNTHETIC strain changed the endpoint with Illustrative compound.", "abstract"),
    ("Illustrative compound was supplied externally to SYNTHETIC strain.", "abstract"),
    ("SYNTHETIC strain production of Illustrative compound remains unknown.", "abstract"),
])
def test_database_no_production_external_feed_and_unknown_quotes_do_not_support_production(quote, scope):
    engine, flow = setup_flow(); h = hypothesis()
    h.claim = "SYNTHETIC strain synthesizes Illustrative compound"
    engine.state.hypotheses = [h]; flow.register(h)
    e = attach(engine, h, quote=quote, scope=scope, system=h.chassis_name)
    flow.adjudicate([h], [draft(flow.claims_for(h)[0], [e.evidence_id])])
    assert not flow.main_reviewed(h)


def test_exact_affirmative_strain_molecule_action_and_condition_can_pass_mechanical_scope():
    engine, flow = setup_flow(); h = hypothesis()
    h.claim = "SYNTHETIC strain is producing Illustrative compound"
    engine.state.hypotheses = [h]; flow.register(h)
    e = attach(engine, h, quote="SYNTHETIC strain synthesized Illustrative compound under recorded conditions.", system=h.chassis_name)
    flow.adjudicate([h], [draft(flow.claims_for(h)[0], [e.evidence_id])])
    assert flow.main_reviewed(h) and flow.verify()["valid"]
    assert h.chassis.status == "unknown"  # no automatic experimental/clinical upgrade


@pytest.mark.parametrize("wording", ["is producing", "synthesizes", "正在生产"])
def test_adjudication_to_review_contract_and_public_view_stops_borrowed_production(wording):
    engine, flow = setup_flow(); h = hypothesis()
    h.claim = f"SYNTHETIC strain {wording} Illustrative compound; exposure needs measurement"
    engine.state.hypotheses = [h]; flow.register(h)
    e = attach(engine, h, quote="OTHER STRAIN production of Illustrative compound was measured.", system="OTHER STRAIN")
    main = flow.claims_for(h)[0]
    payload = {"reviews": [{"hypothesis_id":h.hypothesis_id,"version":1,"verdict":"retain","reason":"SYNTHETIC over-approval","valid_evidence_ids":[e.evidence_id]}],
               "claim_reviews":[draft(main,[e.evidence_id]).model_dump()]}
    engine.call = lambda role, title, model, context, **kwargs: model.model_validate(payload)
    engine.review([h], "SYNTHETIC full review")
    assert h.status == "needs_evidence" and engine.state.reviews[0].verdict == "revise"
    engine.phase = lambda *args,**kwargs: None
    engine.finish = lambda *args,**kwargs: None
    contract = ResearchContract(hypothesis_id=h.hypothesis_id,version=1,status="human_review_needed",question="SYNTHETIC supply question",
        judgment="SYNTHETIC over-approval",minimal_validation="SYNTHETIC measure supply",evidence_ids=[e.evidence_id])
    engine.call = lambda role, title, model, context, **kwargs: model.model_validate({"contracts":[contract.model_dump()],"common_gaps":[]})
    engine.meta()
    assert engine.state.contracts[0].status == "needs_evidence"
    # Even a persisted old self-approval must be downgraded by the shared gate.
    h.status = "reviewed"; engine.state.reviews[0].verdict = "retain"
    flow.data.claims[0].state = "supported"; flow.data.claim_reviews[0].verdict = "supported"
    engine.state.contracts[0].status = "human_review_needed"
    original = engine.run.model_dump()
    view = result_document(engine.run)
    assert view["co_scientist"]["hypotheses"][0]["status"] == "needs_evidence"
    assert view["co_scientist"]["contracts"][0]["status"] == "needs_evidence"
    assert view["co_scientist"]["research_flow"]["claims"][0]["state"] != "supported"
    assert not verify_run(engine.run)["valid"] and engine.run.model_dump() == original


@pytest.fixture
def completed_run(tmp_path):
    return execute(tmp_path, max_calls=16)[2]


def test_complete_coordinator_records_both_query_directions(completed_run):
    flow = completed_run.co_scientist.research_flow
    queries = {q.query_id:q for q in flow.queries}
    claims = {c.claim_id:c for c in flow.claims}
    assert verify_run(completed_run)["valid"]
    assert all(qid in queries and c.claim_id in queries[qid].claim_ids for c in flow.claims for qid in c.query_ids)
    assert all(cid in claims and q.query_id in claims[cid].query_ids for q in flow.queries for cid in q.claim_ids)
    assert any(q.purpose=="revision_prior_search" for q in flow.queries)


@pytest.mark.parametrize("direction", ["query_to_claim", "claim_to_query"])
def test_deleting_either_direction_fails_cli_and_public_readiness(completed_run, tmp_path, direction):
    flow = completed_run.co_scientist.research_flow
    q = next(q for q in flow.queries if q.purpose=="revision_prior_search")
    c = next(c for c in flow.claims if c.claim_id in q.claim_ids)
    if direction=="query_to_claim": q.claim_ids.remove(c.claim_id)
    else: c.query_ids.remove(q.query_id)
    original = completed_run.model_dump()
    assert not verify_run(completed_run)["valid"]
    path = tmp_path/"synthetic.json"; path.write_text(completed_run.model_dump_json())
    checked = subprocess.run([sys.executable,"-m","scientist.research_verify",str(path)],capture_output=True,text=True,check=False)
    assert checked.returncode==1 and not json.loads(checked.stdout)["valid"]
    assert all(h["status"]!="reviewed" for h in result_document(completed_run)["co_scientist"]["hypotheses"])
    assert completed_run.model_dump()==original


@pytest.mark.parametrize("field", ["text", "condition", "endpoint"])
def test_canonical_claim_field_drift_fails_shared_machine_gate(completed_run, field):
    c = next(c for c in completed_run.co_scientist.research_flow.claims if c.kind=="main")
    setattr(c,field,"SYNTHETIC different value")
    assert not verify_run(completed_run)["valid"]
    view=result_document(completed_run)
    assert all(h["status"]!="reviewed" for h in view["co_scientist"]["hypotheses"])
    assert all(c["status"]=="needs_evidence" for c in view["co_scientist"]["contracts"])


@pytest.mark.parametrize("field,id_field", [("claims","claim_id"),("queries","query_id"),("claim_reviews","review_id")])
def test_duplicate_receipt_ids_are_rejected(completed_run, field, id_field):
    rows=getattr(completed_run.co_scientist.research_flow,field)
    rows.append(rows[0].model_copy(deep=True))
    result=verify_run(completed_run)
    assert not result["valid"] and "duplicate_"+id_field in result["errors"]


@pytest.mark.parametrize("frozen", ["SYNTHETIC disease","  synthetic   DISEASE  "])
def test_chinese_input_reuses_frozen_english_in_actual_inventory(frozen):
    engine,flow=setup_flow(); engine.run.brief.disease="合成疾病"; engine.run.brief.existing_candidates=[]
    engine.disease_english=frozen
    s=source(text="Recorded SYNTHETIC disease public abstract."); s.url="https://pubmed.ncbi.nlm.nih.gov/1/"
    saved=engine.run.model_copy(deep=True); saved.run_id="r_saved"; saved.status=ExecutionStatus.COMPLETED; saved.sources=[s]
    reads=[]
    from types import SimpleNamespace
    engine.store=SimpleNamespace(list_runs=lambda pid:[{"run_id":"r_saved","status":"completed"}],
        load_run=lambda pid,rid: reads.append((pid,rid)) or saved)
    assert flow.relevant(s)
    flow.inventory()
    assert reads==[(engine.run.project_id,"r_saved")]
    q=next(q for q in flow.data.queries if q.purpose=="saved_sources")
    assert q.status=="reused" and q.request_count==q.cache_hits==0
    assert flow.data.publications[0].snapshots[0].origin=="saved"
    assert engine.sources.search_count==engine.ledger.counts["http_attempts"]==0


def test_known_alias_and_unmatched_public_source_do_not_trigger_new_search():
    engine,flow=setup_flow(); engine.run.brief.disease="合成疾病"; engine.disease_english="SYNTHETIC disease"
    engine.disease_aliases=["SYNTHETIC synonym"]
    assert flow.relevant(source(text="SYNTHETIC synonym abstract"))
    assert not flow.relevant(source(text="unrelated record", title="unrelated title"))
    assert engine.sources.search_count==0


def test_full_coordinator_chinese_inventory_reuse_remains_old_source(tmp_path):
    store=ProjectStore(tmp_path/"workspace"); pid=store.create_project("SYNTHETIC Chinese reuse")["project_id"]
    brief=ResearchBrief(disease="合成疾病",research_mode="co_scientist",existing_candidates=[],
        budget=ResearchBudget(max_llm_calls=16,max_papers=8,max_input_tokens=500000,max_output_tokens=100000))
    saved=store.create_run(pid,brief); saved.status=ExecutionStatus.COMPLETED
    s=source("pubmed:stored", title="SYNTHETIC disease public stored source",text="SYNTHETIC disease public stored abstract.")
    s.url="https://pubmed.ncbi.nlm.nih.gov/1/"; saved.sources=[s]; store.save_run(saved)
    saved_bytes=(store.run_dir(pid,saved.run_id)/"run.json").read_bytes()
    class ChineseWorker(Worker):
        def complete(self,system,user,schema):
            result=super().complete(system,user,schema)
            if schema["title"]=="AnchorDraft": result["disease_english"]="SYNTHETIC disease"
            return result
    control=Controller()
    control.factory=lambda settings,directory,max_calls: ChineseWorker(settings,directory,max_calls,control)
    run=store.create_run(pid,brief)
    result=ResearchEngine(store,ScientistSettings(workspace=str(store.root),llm={"backend":"codex"}),
        sources=CoSources(),llm_factory=control.factory).execute(pid,run.run_id)
    assert result.status.value=="completed",result.errors
    q=next(q for q in result.co_scientist.research_flow.queries if q.purpose=="saved_sources")
    assert q.reused_from==saved.run_id and q.request_count==0
    assert result.co_scientist.budget["http_attempts"]==0
    assert (store.run_dir(pid,saved.run_id)/"run.json").read_bytes()==saved_bytes


@pytest.mark.parametrize("quote", [
    "SYNTHETIC strain was supplemented with commercially synthesized Illustrative compound.",
    "SYNTHETIC strain was cultured in medium containing chemically synthesized Illustrative compound.",
    "OTHER STRAIN produced Illustrative compound in experiments comparing it with SYNTHETIC strain.",
    "SYNTHETIC strain was compared with OTHER STRAIN that produced Illustrative compound.",
    "SYNTHETIC strain外加了化学合成的Illustrative compound。",
    "OTHER STRAIN合成Illustrative compound并与SYNTHETIC strain比较。",
    "SYNTHETIC strain converted Illustrative compound to another product.",
    "SYNTHETIC strain转化了Illustrative compound成为其他产物。",
])
def test_production_span_binds_actor_instead_of_same_sentence_cooccurrence(quote):
    engine,flow=setup_flow(); h=hypothesis(); h.claim="SYNTHETIC strain is producing Illustrative compound"
    engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h,quote=quote,system=h.chassis_name)
    flow.adjudicate([h],[draft(flow.claims_for(h)[0],[e.evidence_id])])
    assert not flow.main_reviewed(h) and flow.claims_for(h)[0].state=="context_only"


@pytest.mark.parametrize("quote", [
    "SYNTHETIC strain is producing measurable amounts of Illustrative compound.",
    "Illustrative compound was synthesized by SYNTHETIC strain.",
    "SYNTHETIC strain production of Illustrative compound was measured.",
    "SYNTHETIC strain合成了Illustrative compound。",
    "Illustrative compound由SYNTHETIC strain生产。",
])
def test_explicit_actor_bound_active_passive_nominal_chinese_span(quote):
    engine,flow=setup_flow(); h=hypothesis(); h.claim="SYNTHETIC strain synthesizes Illustrative compound"
    engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h,quote=quote,system=h.chassis_name)
    flow.adjudicate([h],[draft(flow.claims_for(h)[0],[e.evidence_id])])
    assert flow.main_reviewed(h),quote


def test_supported_child_without_unique_review_is_rejected_and_projected_conservatively(completed_run):
    flow=completed_run.co_scientist.research_flow
    child=next(c for c in flow.claims if c.kind=="chassis_supply")
    flow.claim_reviews=[r for r in flow.claim_reviews if r.claim_id!=child.claim_id]
    child.state="supported"
    assert not verify_run(completed_run)["valid"]
    view=result_document(completed_run)
    assert all(c["state"]!="supported" for c in view["co_scientist"]["research_flow"]["claims"])


def test_production_in_mechanism_layer_cannot_bypass_supply_gate():
    engine,flow=setup_flow(); h=hypothesis(); h.mechanism="SYNTHETIC strain synthesizes Illustrative compound"
    engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h,quote="OTHER STRAIN production of Illustrative compound was measured.",system="OTHER STRAIN")
    c=next(c for c in flow.claims_for(h) if c.kind=="mechanism_direction")
    flow.adjudicate([h],[draft(c,[e.evidence_id])])
    assert c.state=="context_only" and flow.verify()["valid"]


@pytest.mark.parametrize("wording", ["facilitates availability of", "has a provision of", "能形成", "可供获得"])
def test_explicit_strain_molecule_dependence_is_gated_without_relying_on_verb_list(wording):
    engine,flow=setup_flow(); h=hypothesis(); h.claim=f"SYNTHETIC strain {wording} Illustrative compound"
    engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h,quote="OTHER STRAIN production of Illustrative compound was measured.",system="OTHER STRAIN")
    flow.adjudicate([h],[draft(flow.claims_for(h)[0],[e.evidence_id])])
    assert not flow.main_reviewed(h)

