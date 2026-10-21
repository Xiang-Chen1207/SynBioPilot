"""Transparent offline receipts and synthetic workers, never biomedical ground truth."""
import copy
import io
import json
import time
import zipfile
from types import SimpleNamespace

import pytest
from test_coscientist import execute
from test_research import LITERATURE_QUOTE, OPPOSING_QUOTE
from test_tool_http_identity import http_with_responses, response

from scientist.budgets import BudgetExceeded, RunLedger
from scientist.co_models import CoScientistState, HypothesisCard, ProblemAnchor
from scientist.coscientist import BoundedSources, CoScientistEngine
from scientist.exports import export_research
from scientist.flow_models import ClaimReviewDraft
from scientist.models import (
    EvidenceRecord,
    ResearchBrief,
    ResearchBudget,
    ResearchRun,
    ScientistSettings,
    SourceRecord,
)
from scientist.presentation import result_document
from scientist.research_flow import ResearchFlow, sha
from scientist.research_public import public_research_flow
from scientist.research_verify import verify_run
from scientist.sources import BiomedicalSources


def source(sid="pubmed:1", *, provider="europe_pmc", title="SYNTHETIC paper A", text=LITERATURE_QUOTE,
           doi="10.0000/synthetic-a", pmid="1", pmcid="", scope="abstract", metadata=None):
    return SourceRecord(source_id=sid, provider=provider, title=title,
        url="https://example.invalid/SYNTHETIC", retrieved_at="2026-10-12T00:00:00Z", text=text,
        doi=doi, pmid=pmid, pmcid=pmcid, evidence_scope=scope, metadata=metadata or {"synthetic": True})


def setup_flow(*, searches=8, source_impl=None):
    run = ResearchRun(run_id="r_synthetic", project_id="p_synthetic", created_at="", updated_at="",
        brief=ResearchBrief(disease="SYNTHETIC disease", research_mode="co_scientist",
            budget=ResearchBudget(max_searches=searches)))
    run.co_scientist = CoScientistState(anchor=ProblemAnchor(disease=run.brief.disease,
        phenotype="SYNTHETIC endpoint", species="Homo sapiens", chassis_name="SYNTHETIC strain"))
    legacy = SimpleNamespace(store=None, settings=ScientistSettings(llm={"backend": "none"}))
    engine = CoScientistEngine(legacy)
    engine.run, engine.state = run, run.co_scientist
    engine.persist = lambda: None
    engine.event = lambda *args, **kwargs: None
    engine.disease_english = run.brief.disease
    engine.ledger = RunLedger(run.brief.budget, time.monotonic() + 60)
    impl = source_impl or SimpleNamespace(http=SimpleNamespace(request_count=0, cache_hits=0))
    engine.sources = BoundedSources(impl, engine)
    engine.flow = ResearchFlow(engine)
    return engine, engine.flow


def hypothesis(suffix="1", version=1):
    return HypothesisCard(hypothesis_id="h_synthetic_" + suffix, version=version, title="SYNTHETIC hypothesis",
        claim="SYNTHETIC testable claim", molecule_names=["Illustrative compound"], mechanism="SYNTHETIC mechanism",
        chassis_name="SYNTHETIC strain", condition="SYNTHETIC condition", endpoint="SYNTHETIC endpoint",
        prediction="SYNTHETIC measurement", validation_question="SYNTHETIC minimal validation")


def attach(engine, h, *, sid="pubmed:1", quote=LITERATURE_QUOTE, polarity="supports", condition="SYNTHETIC condition",
           endpoint="SYNTHETIC endpoint", system="SYNTHETIC cells", scope="abstract"):
    s = source(sid, text=quote, scope=scope)
    e = EvidenceRecord(evidence_id="e_" + str(len(engine.run.evidence)), source_id=sid,
        claim="SYNTHETIC quoted claim", quote=quote, location="SYNTHETIC abstract paragraph 1",
        polarity=polarity, study_system=system, condition=condition, endpoint=endpoint, validated_span=True)
    engine.run.sources.append(s); engine.run.evidence.append(e); engine.flow.ingest([s])
    (h.opposing_evidence_ids if polarity == "opposes" else h.evidence_ids).append(e.evidence_id)
    return e


def draft(c, refs, *, version=None, verdict="supported"):
    return ClaimReviewDraft(claim_id=c.claim_id, hypothesis_id=c.hypothesis_id,
        version=version or c.version, verdict=verdict, evidence_ids=refs, reason="SYNTHETIC review")


@pytest.mark.parametrize("alias", ["10.0000/SYNTHETIC-A", "https://doi.org/10.0000/synthetic-a", "doi:10.0000/synthetic-a"])
def test_duplicate_doi_across_providers_preserves_original_snapshot_and_hash(alias):
    _, flow = setup_flow()
    a, b = source(), source("s2:1", provider="semantic_scholar", doi=alias, pmid="", text=OPPOSING_QUOTE)
    flow.ingest([a, b])
    assert len(flow.data.publications) == 1
    snapshots = flow.data.publications[0].snapshots
    assert {s.source_id for s in snapshots} == {a.source_id, b.source_id}
    assert {s.text_sha256 for s in snapshots} == {sha(a.text), sha(b.text)}


def test_transitive_pmid_pmcid_versions_and_full_text_are_one_study_with_separate_snapshots():
    _, flow = setup_flow()
    flow.ingest([source(pmcid="PMC1"), source("s2:a", doi="", pmid="", pmcid="PMC1"),
        source("arxiv:1v1", provider="arxiv", pmid="", doi="", metadata={"arxiv_id": "2601.00001v1"}),
        source("arxiv:1v2", provider="arxiv", pmid="", doi="", text=OPPOSING_QUOTE, metadata={"arxiv_id": "2601.00001v2"}),
        source(scope="full_text", text=LITERATURE_QUOTE + "\nSYNTHETIC full body.")])
    assert len(flow.data.publications) == 2  # disjoint journal/preprint IDs are not guessed equivalent
    assert {s.scope for s in flow.data.publications[0].snapshots} == {"abstract", "full_text"}
    assert {s.version for s in flow.data.publications[1].snapshots} == {"2601.00001v1", "2601.00001v2"}


def test_same_title_does_not_override_conflicting_stable_identifiers():
    _, flow = setup_flow()
    flow.ingest([source(), source("pubmed:2", doi="10.0000/synthetic-b", pmid="2")])
    assert len(flow.data.publications) == 2


@pytest.mark.parametrize("status", ["ok", "empty", "unavailable", "truncated"])
def test_actual_search_receipt_records_provider_result_and_count(status):
    class Provider:
        http = SimpleNamespace(request_count=0, cache_hits=0)
        def search_literature(self, query, limit):
            self.http.request_count += 1
            if status == "unavailable":
                raise RuntimeError("SYNTHETIC HTTP 500")
            if status == "empty":
                self.last_literature_scope = {"total_hits": 0}
                return []
            return [source(metadata={"search_scope": {"total_hits": 7 if status == "truncated" else 1}})]
    engine, flow = setup_flow(source_impl=Provider())
    try:
        engine.sources.search_literature("SYNTHETIC query", limit=2)
    except RuntimeError:
        pass
    q = flow.data.queries[-1]
    assert q.status == status and q.request_count == 1
    assert engine.sources.search_count == 1
    assert q.after_dedup == (1 if status in {"ok", "truncated"} else 0)


@pytest.mark.parametrize("maximum", [1, 2, 4, 8, 40])
def test_phase_quotas_do_not_raise_total_budget_and_record_all_claim_plans(maximum):
    engine, flow = setup_flow(searches=maximum)
    assert sum(v for k, v in flow.data.stage_limits.items() if k != "full_text") == maximum
    flow.compile_queries([hypothesis(), hypothesis("2")])
    assert len(flow.data.claims) == 12
    assert all({"support", "contrary"} <= {q.purpose for q in flow.data.queries if c.claim_id in q.claim_ids}
               for c in flow.data.claims if c.kind != "main")
    assert all(q.date_from and q.date_to for q in flow.data.queries if q.stage == "novelty")
    assert engine.run.brief.budget.max_searches == maximum


@pytest.mark.parametrize("bad", ["missing_source", "missing_quote", "missing_location", "other_claim", "other_version"])
def test_fake_citation_missing_span_and_cross_claim_version_never_upgrade(bad):
    engine, flow = setup_flow()
    h = hypothesis(); engine.state.hypotheses = [h]; flow.register(h)
    e = attach(engine, h)
    main = flow.claims_for(h)[0]
    refs = [e.evidence_id]
    review = draft(main, refs)
    if bad == "missing_source":
        engine.run.sources = []
    elif bad == "missing_quote":
        e.quote = "This fabricated source quotation never existed."
    elif bad == "missing_location":
        e.validated_span = False
    elif bad == "other_claim":
        h.evidence_ids = []
    else:
        review.version = 99
    flow.adjudicate([h], [review])
    assert not flow.main_reviewed(h)
    assert main.state == "unknown"


def test_mixed_support_opposition_and_scope_limits_are_preserved():
    engine, flow = setup_flow(); h = hypothesis(); engine.state.hypotheses = [h]; flow.register(h)
    a = attach(engine, h)
    b = attach(engine, h, sid="pubmed:2", quote=OPPOSING_QUOTE, polarity="opposes")
    main, *rest = flow.claims_for(h)
    flow.adjudicate([h], [draft(main, [a.evidence_id, b.evidence_id]), draft(rest[-2], [a.evidence_id])])
    assert main.state == "contested" and not flow.main_reviewed(h)
    assert {l.polarity for l in flow.data.links if l.claim_id == main.claim_id} == {"supports", "opposes"}
    assert rest[-2].state == "context_only"  # no production quote / exact strain
    assert flow.verify()["valid"]


@pytest.mark.parametrize("scope,condition,system", [("database", "SYNTHETIC condition", "SYNTHETIC strain"),
    ("abstract", "other condition", "SYNTHETIC strain"), ("abstract", "SYNTHETIC condition", "other strain")])
def test_chassis_or_condition_extrapolation_is_context_only(scope, condition, system):
    engine, flow = setup_flow(); h = hypothesis(); engine.state.hypotheses = [h]; flow.register(h)
    e = attach(engine, h, quote="Illustrative compound production was measured in other strain.",
        scope=scope, condition=condition, system=system)
    c = next(c for c in flow.claims_for(h) if c.kind == "chassis_supply")
    flow.adjudicate([h], [draft(c, [e.evidence_id])])
    assert c.state == "context_only"


def test_new_version_has_fresh_claims_and_explicit_reused_novelty():
    engine, flow = setup_flow(); old = hypothesis(); engine.state.hypotheses = [old]; flow.register(old)
    e = attach(engine, old); flow.compile_queries([old])
    flow.adjudicate([old], [draft(flow.claims_for(old)[0], [e.evidence_id])])
    new = old.model_copy(deep=True); new.version=2; new.parent_version=1; new.claim="SYNTHETIC narrowed question"
    flow.revision(old, new, []); engine.state.hypotheses.append(new)
    assert flow.main_reviewed(old) and not flow.main_reviewed(new)
    assert flow.claims_for(new)[0].state == "pending"
    assert flow.data.revisions and flow.data.revisions[0].before_sha256 != flow.data.revisions[0].after_sha256
    assert flow.data.novelty[-1].version == 2 and flow.data.novelty[-1].status == "insufficient_search"


def test_public_receipts_drop_arbitrary_private_fields_and_diagnostic_strings():
    _, flow = setup_flow(); flow.compile_queries([hypothesis()])
    raw = flow.data.model_dump(); raw["private_prompt"] = "PRIVATE_SENTINEL"
    raw["queries"][0]["headers"]={"Authorization":"PRIVATE_SENTINEL"}
    raw["queries"][0]["query"]="api_key=PRIVATE_SENTINEL"
    raw["library_status"]=[{"library":"papers", "private_prompt":"PRIVATE_SENTINEL"}]
    assert "PRIVATE_SENTINEL" not in json.dumps(public_research_flow(raw))


def test_full_synthetic_chain_and_external_verifier_exact_versions(tmp_path):
    store, _, run, _ = execute(tmp_path, max_calls=16)
    flow = run.co_scientist.research_flow
    assert verify_run(run)["valid"], verify_run(run)
    assert {q.stage for q in flow.queries} >= {"landscape", "supplement", "novelty", "feedback", "reuse"}
    assert flow.stage_usage["landscape"] <= 2 and flow.stage_usage["novelty"] > 0
    assert run.co_scientist.budget["literature_queries"] <= run.brief.budget.max_searches
    assert all(n.value is None for c in run.co_scientist.contracts for n in c.parameter_needs)
    assert (store.run_dir(run.project_id,run.run_id)/"research_flow_validation.json").is_file()
    tampered = run.model_copy(deep=True); tampered.co_scientist.research_flow.links[0].version = 99
    assert not verify_run(tampered)["valid"]
    tampered = run.model_copy(deep=True); tampered.co_scientist.research_flow.claim_reviews = []
    assert all(c["status"] == "needs_evidence" for c in result_document(tampered)["co_scientist"]["contracts"])


def test_export_receipts_have_no_full_text_or_private_fields(tmp_path):
    store, _, run, _ = execute(tmp_path)
    flow = run.co_scientist.research_flow
    flow.__dict__["private_prompt"]="PRIVATE_SENTINEL"
    flow.queries[0].query="api_key=PRIVATE_SENTINEL"
    for fmt in ("json", "markdown", "csv", "zip"):
        payload, _, _ = export_research(store, run, fmt)
        if fmt == "zip":
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                assert "research_flow.json" in z.namelist()
                for name in z.namelist():
                    assert b"PRIVATE_SENTINEL" not in z.read(name), name
                value=json.loads(z.read("research_flow.json"))
                assert all("text" not in s for p in value["publications"] for s in p["snapshots"])
        else:
            assert b"PRIVATE_SENTINEL" not in payload


def test_legacy_receipts_absent_are_not_backfilled(tmp_path):
    _, _, run, _ = execute(tmp_path)
    run.co_scientist.research_flow=None
    before=copy.deepcopy(run.model_dump())
    assert verify_run(run)["status"] == "not_recorded"
    assert result_document(run)["co_scientist"]["research_flow"] is None
    assert run.model_dump() == before


def test_europepmc_preserves_actual_hitcount_and_rejects_bad_collection():
    provider=BiomedicalSources(SimpleNamespace(get_json=lambda *a,**k: {"hitCount":9,"resultList":{"result":[{"pmid":"1","title":"SYNTHETIC","abstractText":LITERATURE_QUOTE}]}}))
    value=provider.search_literature("SYNTHETIC", limit=1)
    assert value[0].metadata["search_scope"]["total_hits"] == 9
    provider.http.get_json=lambda *a,**k: {"resultList":{"result":"invalid"}}
    with pytest.raises(RuntimeError):
        provider.search_literature("SYNTHETIC")


@pytest.mark.parametrize("spent", [0, 1])
def test_actual_http_chain_budget_exhausted_before_request_or_after_500(tmp_path, monkeypatch, spent):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, response(500, {}), max_requests=1)
    if not spent:
        ledger.request()  # fixture pre-spends the same shared ledger
    engine, flow = setup_flow(source_impl=BiomedicalSources(http))
    engine.ledger = ledger
    with pytest.raises(RuntimeError):
        engine.sources.search_literature("SYNTHETIC bounded query", limit=1)
    q = flow.data.queries[-1]
    assert q.status == "budget_exhausted" and q.request_count == spent
    assert len(calls) == spent and ledger.counts["http_attempts"] == 1


def test_actual_http_empty_result_retry_cache_and_scope_are_distinct(tmp_path, monkeypatch):
    payload={"hitCount":0,"resultList":{"result":[]}}
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, response(500, {}), response(200,payload))
    engine, flow = setup_flow(source_impl=BiomedicalSources(http)); engine.ledger=ledger
    assert engine.sources.search_literature("SYNTHETIC fixed query", limit=1) == []
    assert engine.sources.search_literature("SYNTHETIC fixed query", limit=1) == []
    a,b=flow.data.queries
    assert a.status == b.status == "empty" and a.total_hits == 0
    assert (a.request_count,a.cache_hits,b.request_count,b.cache_hits) == (2,0,0,1)
    assert len(calls)==2 and ledger.counts["http_attempts"]==2


def test_public_inventory_never_scans_another_project_or_private_source(tmp_path):
    engine, flow=setup_flow()
    private=source(); private.url="local://private"
    public=source(); public.url="https://pubmed.ncbi.nlm.nih.gov/1/"; public.text="SYNTHETIC disease public abstract"
    other_project=source(); other_project.text="PRIVATE_SENTINEL"
    previous=engine.run.model_copy(deep=True); previous.run_id="r_saved"; previous.status="completed"; previous.sources=[private,public]
    requested=[]
    engine.store=SimpleNamespace(list_runs=lambda pid: [{"run_id":"r_saved","status":"completed"}],
        load_run=lambda pid,rid: requested.append((pid,rid)) or previous)
    flow.inventory()
    assert requested == [("p_synthetic","r_saved")]
    assert all(s.url.startswith("https://") for s in engine.run.sources)
    assert flow.data.publications[0].snapshots[0].origin == "saved"
    assert flow.data.queries[-1].status == "reused" and flow.data.queries[-1].request_count==0


def test_new_evidence_cannot_override_an_existing_id_condition_or_system():
    engine,flow=setup_flow(); h=hypothesis(); engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h,condition="other condition",system="other system")
    original=e.model_dump()
    altered=e.model_copy(update={"condition":h.condition,"study_system":"SYNTHETIC cells"})
    c=next(c for c in flow.claims_for(h) if c.kind=="intervention")
    flow.adjudicate([h],[draft(c,[e.evidence_id])],[altered])
    assert c.state=="context_only" and e.model_dump()==original
    assert flow.data.links[0].condition=="other condition"
    assert flow.verify()["valid"]


@pytest.mark.parametrize("scope,condition", [("database","SYNTHETIC condition"),("abstract","other condition")])
def test_main_cannot_bypass_scope_and_condition_gates(scope,condition):
    engine,flow=setup_flow(); h=hypothesis(); engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h,scope=scope,condition=condition)
    c=flow.claims_for(h)[0]; flow.adjudicate([h],[draft(c,[e.evidence_id])])
    assert c.state=="context_only" and not flow.main_reviewed(h)


@pytest.mark.parametrize("field", ["review_version","review_evidence","paper_id","snapshot_id","condition","endpoint","location","scope"])
def test_external_verifier_rejects_tampered_review_and_snapshot_associations(field):
    engine,flow=setup_flow(); h=hypothesis(); engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h); flow.adjudicate([h],[draft(flow.claims_for(h)[0],[e.evidence_id])])
    assert flow.main_reviewed(h) and flow.verify()["valid"]
    if field=="review_version": flow.data.claim_reviews[0].version=99
    elif field=="review_evidence": flow.data.claim_reviews[0].valid_evidence_ids=["missing"]
    else: setattr(flow.data.links[0],field,"missing")
    assert not flow.main_reviewed(h) and not flow.verify()["valid"]


def test_default_supplement_order_covers_support_contrary_and_distinct_layers():
    engine,flow=setup_flow(); flow.compile_queries([hypothesis(str(i)) for i in range(6)])
    seen=[]
    def tool(request):
        q=flow.before_search(request.arguments["query"]); seen.append(q)
        flow.search_finished(q,[],3)
        return SimpleNamespace(call_id="fixture_call",request_count=0,cache_hits=0,sources=[],status="empty"),[]
    engine.tool=tool; flow.run_queries("supplement")
    assert len(seen)==3 and {q.purpose for q in seen}=={"support","contrary"}
    kinds=[next(c.kind for c in flow.data.claims if c.claim_id in q.claim_ids) for q in seen]
    assert kinds==["mechanism_direction","intervention","chassis_supply"]
    assert len({q.hypothesis_id for q in seen})==3


def test_memo_query_reuse_records_zero_new_http_and_cache_attempts():
    engine,flow=setup_flow(); flow.compile_queries([hypothesis()])
    engine.tool=lambda request: (SimpleNamespace(call_id="old_call",request_count=7,cache_hits=4,sources=[],status="ok"),[])
    flow.run_queries("novelty")
    q=next(q for q in flow.data.queries if q.stage=="novelty")
    assert q.status=="reused" and q.request_count==q.cache_hits==0 and q.reused_from=="old_call"


@pytest.mark.parametrize("error,reason", [(BudgetExceeded("exhausted"),"shared_budget"),(TimeoutError("deadline"),"deadline")])
def test_fulltext_budget_stop_is_distinct_from_provider_failure(error,reason):
    provider=SimpleNamespace(http=SimpleNamespace(request_count=0,cache_hits=0), full_text=lambda s: (_ for _ in ()).throw(error))
    _,flow=setup_flow(source_impl=provider)
    s=source(pmcid="PMC1",metadata={"is_open_access":"Y"})
    with pytest.raises(RuntimeError if isinstance(error,RuntimeError) else TimeoutError): flow.full_text(s)
    assert flow.data.queries[-1].status=="budget_exhausted" and flow.data.queries[-1].stop_reason==reason


def test_public_paper_metadata_whitelist_preserves_original_source_and_export_hash(tmp_path):
    store,_,run,_=execute(tmp_path)
    run.sources[0].metadata["private_prompt"]="PRIVATE_SENTINEL"
    original=copy.deepcopy(run.model_dump())
    assert "PRIVATE_SENTINEL" not in json.dumps(result_document(run))
    assert "PRIVATE_SENTINEL" not in export_research(store,run,"json")[0].decode()
    assert run.model_dump()==original


@pytest.mark.parametrize("claim", ["SYNTHETIC strain produces Illustrative compound; exposure needs measurement",
    "SYNTHETIC strain produces Illustrative compound and exposure needs measurement"])
def test_exposure_uncertainty_does_not_cancel_affirmative_production_scope(claim):
    engine,flow=setup_flow(); h=hypothesis(); h.claim=claim; engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h,quote="OTHER STRAIN production of Illustrative compound was measured.", system="OTHER STRAIN")
    c=flow.claims_for(h)[0]; flow.adjudicate([h],[draft(c,[e.evidence_id])])
    assert c.state=="context_only" and not flow.main_reviewed(h)


def test_context_polarity_cannot_be_promoted_to_main_support():
    engine,flow=setup_flow(); h=hypothesis(); engine.state.hypotheses=[h]; flow.register(h)
    e=attach(engine,h,polarity="context"); c=flow.claims_for(h)[0]
    flow.adjudicate([h],[draft(c,[e.evidence_id])])
    assert c.state=="context_only" and not flow.main_reviewed(h)


def test_top_level_source_diagnostics_do_not_enter_reuse_or_public_exports(tmp_path):
    _,flow=setup_flow(); s=source(); s.title="raw_prompt=PRIVATE_SENTINEL"
    assert "PRIVATE_SENTINEL" not in json.dumps(flow.reusable_source(s).model_dump())
    store,_,run,_=execute(tmp_path); run.sources[0].title=s.title
    for fmt in ("json","markdown","zip"):
        data,_,_=export_research(store,run,fmt)
        if fmt=="zip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                assert all(b"PRIVATE_SENTINEL" not in z.read(n) for n in z.namelist())
        else:
            assert b"PRIVATE_SENTINEL" not in data


def test_reused_source_body_does_not_restore_sensitive_text_after_projection():
    _,flow=setup_flow(); s=source(text="SYNTHETIC disease public abstract raw_prompt=PRIVATE_SENTINEL")
    value=flow.reusable_source(s)
    assert "PRIVATE_SENTINEL" not in value.text
    assert value.metadata["original_text_sha256"]==sha(s.text)


def test_feedback_sources_reach_only_corresponding_revision_claim_and_need_fresh_review():
    engine,flow=setup_flow(); old=hypothesis(); engine.state.hypotheses=[old]; flow.register(old)
    c=next(c for c in flow.claims_for(old) if c.kind=="chassis_supply")
    q=flow.new_query("feedback","recorded_review_gap","SYNTHETIC production query",hypothesis_id=old.hypothesis_id,
        version=1,claim_ids=[c.claim_id],source_ids=["pubmed:feedback"],status="ok")
    c.query_ids.append(q.query_id)
    new=old.model_copy(update={"version":2,"parent_version":1,"claim":"SYNTHETIC narrowed question"},deep=True)
    flow.revision(old,new,[]); engine.state.hypotheses.append(new)
    revised_chassis=next(c for c in flow.claims_for(new) if c.kind=="chassis_supply")
    intervention=next(c for c in flow.claims_for(new) if c.kind=="intervention")
    assert any(q.claim_ids==[revised_chassis.claim_id] and q.source_ids==["pubmed:feedback"] and q.status=="reused" for q in flow.data.queries)
    assert not any("pubmed:feedback" in q.source_ids and intervention.claim_id in q.claim_ids for q in flow.data.queries)
    assert revised_chassis.state=="pending" and not flow.main_reviewed(new)

