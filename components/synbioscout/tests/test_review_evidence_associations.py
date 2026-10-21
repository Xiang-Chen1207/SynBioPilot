"""SYNTHETIC R0 regression: new mixed findings survive exact-version review/meta.

These fixtures contain no claims about actual drugs, diagnoses or efficacy.
"""
import io
import json
import zipfile

import pytest
from test_research_flow import attach, draft, hypothesis, setup_flow, source

from scientist.candidate_assessments import assessment_cards
from scientist.closing_context import expand_closing_context
from scientist.co_models import HypothesisReview, ResearchContract
from scientist.coscientist import MetaDraft, ReflectionDraft
from scientist.exports import export_document, export_research, table_exports
from scientist.models import CandidateRecord, EvidenceRecord, ExecutionStatus
from scientist.presentation import result_document
from scientist.research_public import public_literature_source, public_research_flow
from scientist.store import ProjectStore
from scientist.tool_public import public_candidate_identity


def prepared():
    engine, flow = setup_flow()
    h = hypothesis(version=2)
    old = hypothesis(version=1)
    engine.state.hypotheses = [old, h]
    flow.register(old); flow.register(h)
    baseline = attach(engine, h, polarity="context")
    candidate = CandidateRecord(candidate_id="synthetic_candidate", name="Illustrative compound",
        identity_status="needs_review", role_status="needs_review", decision="hold", evidence_ids=[baseline.evidence_id])
    engine.run.candidates = [candidate]
    engine.run.brief.existing_candidates = [candidate.name]
    engine.run.brief.population = "SYNTHETIC children"
    engine.run.brief.phenotype = "SYNTHETIC core and associated endpoints"
    quote = "Illustrative compound changed the associated endpoint but did not change the core endpoint."
    new_source = source("pubmed:synthetic_mixed", text=quote, pmid="synthetic_mixed", doi="10.0000/synthetic-mixed")
    engine.run.sources.append(new_source); flow.ingest([new_source])
    new_evidence = EvidenceRecord(evidence_id="synthetic_new_mixed", source_id=new_source.source_id,
        claim="SYNTHETIC mixed endpoints", quote=quote, location="SYNTHETIC abstract results", polarity="mixed",
        study_system="SYNTHETIC children", condition="SYNTHETIC condition", endpoint="SYNTHETIC endpoint")
    intervention = next(c for c in flow.claims_for(h) if c.kind == "intervention")
    query = flow.new_query("supplement", "contrary", "SYNTHETIC compound trial", claim_ids=[intervention.claim_id],
        hypothesis_id=h.hypothesis_id, version=h.version, status="ok", source_ids=[new_source.source_id])
    intervention.query_ids.append(query.query_id)
    claims = [draft(c, [new_evidence.evidence_id] if c.kind == "intervention" else
                    [baseline.evidence_id] if c.kind == "main" else [], verdict="context_only" if c.kind == "main" else "unknown")
              for c in flow.claims_for(h)]
    return engine, flow, old, h, baseline, new_evidence, claims


def test_mixed_evidence_reaches_actual_meta_request_and_selected_contract_without_rewriting_cards():
    engine, flow, old, h, baseline, new_evidence, claims = prepared()
    before_old, before_current = old.model_dump(), h.model_dump()
    captured = []
    def call(role, title, schema, payload, **kwargs):
        captured.append((role, payload))
        if schema is ReflectionDraft:
            return ReflectionDraft(reviews=[HypothesisReview(hypothesis_id=h.hypothesis_id, version=h.version,
                verdict="revise", reason="SYNTHETIC mixed result requires endpoint separation",
                valid_evidence_ids=[baseline.evidence_id, new_evidence.evidence_id])],
                claim_reviews=claims, new_evidence=[new_evidence])
        assert schema is MetaDraft
        payload = expand_closing_context(payload)
        ids = {e["evidence_id"] for e in payload["accepted_evidence"]}
        assert new_evidence.evidence_id in ids
        assert any(a["evidence_id"] == new_evidence.evidence_id and a["version"] == 2
                   for a in payload["reviewed_evidence_associations"])
        return MetaDraft(contracts=[ResearchContract(hypothesis_id=h.hypothesis_id, version=h.version,
            status="needs_evidence", question="SYNTHETIC next endpoint question", judgment="Mixed, requires validation",
            evidence_ids=[new_evidence.evidence_id, "not_reviewed"], minimal_validation="SYNTHETIC endpoint audit")], common_gaps=[])
    engine.call = call
    engine.review([h], "SYNTHETIC review")
    assert new_evidence.evidence_id in engine.state.reviews[-1].valid_evidence_ids
    assert new_evidence.evidence_id in engine.run.candidates[0].evidence_ids
    assert engine.run.candidates[0].decision == "hold"
    assert engine.run.candidates[0].identity_status == "needs_review"
    assert h.evidence_ids == before_current["evidence_ids"]
    assert old.model_dump() == before_old
    assert h.claim == before_current["claim"]
    engine.meta()
    contract = engine.state.contracts[0]
    assert contract.evidence_ids == [new_evidence.evidence_id]
    assert contract.opposing_evidence_ids == [new_evidence.evidence_id]
    assert contract.status == "needs_evidence"
    assert "未支持" in " ".join(engine.state.reviews[-1].required_evidence)
    assert "缺少唯一同版本" not in " ".join(engine.state.reviews[-1].required_evidence)
    assert "需要逐主张支持/反证与研究系统、条件、终点适用性审查" not in h.inference_gaps
    assert new_evidence.evidence_id not in {e["evidence_id"] for e in engine.context([old])["accepted_evidence"]}
    assert flow.verify()["valid"]


@pytest.mark.parametrize("invalid", ["other_claim", "other_version", "ambiguous_candidate", "no_entity_in_quote"])
def test_attribution_never_guesses_another_claim_version_or_entity(invalid):
    engine, flow, old, h, _, new, claims = prepared()
    if invalid == "other_claim":
        flow.data.queries[-1].source_ids = []
    elif invalid == "other_version":
        claims[3].version = 99  # intervention
    elif invalid == "ambiguous_candidate":
        engine.run.candidates.append(engine.run.candidates[0].model_copy(update={"candidate_id": "other_synthetic_entity"}))
    else:
        new.quote = "The unrelated intervention changed one endpoint but not the other endpoint."
        engine.run.sources[-1].text = new.quote
        flow.ingest([engine.run.sources[-1]])
    flow.adjudicate([h], claims, [new])
    assert all(new.evidence_id not in c.evidence_ids for c in engine.run.candidates)
    assert old.evidence_ids == []
    if invalid in {"other_claim", "other_version"}:
        assert new.evidence_id not in flow.reviewed_evidence_ids(h)["evidence_ids"]


def test_gap_states_distinguish_unreviewed_missing_evidence_and_reviewed_not_supported():
    _engine, flow, _, h, _, new, claims = prepared()
    assert flow.main_review_state(h) == "unreviewed"
    flow.adjudicate([h], claims, [new])
    assert flow.main_review_state(h) == "reviewed_not_supported"
    assert "已完成同版本审查" in flow.main_review_gap(h)
    _engine2, flow2, _, h2, _, _, claims2 = prepared()
    for claim in claims2:
        claim.evidence_ids = []
    flow2.adjudicate([h2], claims2)
    assert flow2.main_review_state(h2) == "reviewed_evidence_missing"
    assert "已完成同版本审查" in flow2.main_review_gap(h2)


def test_finalized_run_cannot_be_readjudicated_and_public_assessment_does_not_mutate_it(tmp_path):
    engine, flow, _, h, _, new, claims = prepared()
    flow.adjudicate([h], claims, [new])
    engine.run.status = ExecutionStatus.COMPLETED
    before = engine.run.model_dump_json()
    with pytest.raises(ValueError, match="read-only"):
        flow.adjudicate([h], claims, [new])
    document = result_document(engine.run)
    card = document["candidate_assessments"][0]
    assert card["origin"] == "user_supplied" and card["decision"] == "hold"
    assert card["scientific_status"] == "needs_evidence"
    assert card["opposing_evidence_ids"] == [new.evidence_id]
    assert card["population_requested"] == "SYNTHETIC children"
    assert card["evidence_scope"][-1]["study_system"] == "SYNTHETIC children"
    assert "不等于" in card["status_note"]
    assert "全文" not in json.dumps(card["evidence_scope"], ensure_ascii=False)
    assert engine.run.model_dump_json() == before
    exported = export_document(engine.run)
    assert exported["candidate_assessments"] == document["candidate_assessments"]
    assert all(s["text"] == "" for s in exported["sources"])
    assert new.evidence_id in table_exports(engine.run)["candidates.csv"].decode("utf-8-sig")
    store = ProjectStore(tmp_path)
    project = store.create_project("SYNTHETIC candidate assessment export")
    engine.run.project_id = project["project_id"]
    data, _, _ = export_research(store, engine.run, "zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "candidate_assessments.json" in archive.namelist()
        assert b"SYNTHETIC children" in archive.read("candidate_assessments.json")


def test_unmatched_specified_candidate_remains_assessment_record_without_guessed_identity():
    engine, _, _, _, _, _, _ = prepared()
    engine.run.brief.existing_candidates.append("A different SYNTHETIC chemical salt")
    card = assessment_cards(engine.run)[-1]
    assert card["candidate_id"] == "" and card["identity_status"] == "unresolved"
    assert card["name"] == "A different SYNTHETIC chemical salt" and card["evidence_ids"] == []
    assert "A different SYNTHETIC chemical salt" in table_exports(engine.run)["candidates.csv"].decode("utf-8-sig")


def test_background_provenance_public_allowlists_keep_scope_but_drop_private_payloads():
    row = source().model_dump()
    row["metadata"].update({"acquisition_origin":"background_reuse", "background_package_id":"synthetic-v1",
        "background_manifest_sha256":"a"*64, "excerpt_start":200, "excerpt_end":400,
        "excerpt_location":"SYNTHETIC results paragraph", "excerpt_sha256":"b"*64,
        "inclusion_reason":"SYNTHETIC result-blind criteria", "evidence_level":"human_trial",
        "private_prompt":"PRIVATE_SENTINEL", "manifest":{"secret":"PRIVATE_SENTINEL"}})
    clean = public_literature_source(row)
    assert clean["metadata"]["excerpt_start"] == 200
    assert clean["metadata"]["background_manifest_sha256"] == "a"*64
    assert "PRIVATE_SENTINEL" not in json.dumps(clean)
    history = public_candidate_identity({"identity_history":[{"origin":"core_identity", "acquisition":"background_reuse",
        "source_sha256":"a"*64, "core_preparation":True, "package_id":"synthetic-v1", "run_id":"synthetic_run",
        "raw_prompt":"PRIVATE_SENTINEL", "source":{"text":"PRIVATE_SENTINEL"}}]})
    assert history["identity_history"][0]["core_preparation"] is True
    assert history["identity_history"][0]["source_sha256"] == "a"*64
    assert "PRIVATE_SENTINEL" not in json.dumps(history)
    flow = public_research_flow({"library_status":[{"library":"synthetic", "reused_records":7,
        "newly_acquired_records":2, "newly_selected_records":1, "selected_records":6,
        "version":"1.0.0", "manifest_sha256":"a"*64, "observed_records":9, "raw":"PRIVATE_SENTINEL"}]})
    assert flow["library_status"][0]["reused_records"] == 7
    assert flow["library_status"][0]["newly_selected_records"] == 1
    assert flow["library_status"][0]["manifest_sha256"] == "a"*64
    assert "PRIVATE_SENTINEL" not in json.dumps(flow)


def test_supported_new_evidence_is_not_downgraded_in_derived_view_for_empty_generator_citations():
    engine, flow, _, h, _, new, claims = prepared()
    h.evidence_ids = []
    main = next(c for c in flow.claims_for(h) if c.kind == "main")
    query = flow.data.queries[-1]
    query.claim_ids.append(main.claim_id); main.query_ids.append(query.query_id)
    new.polarity = "supports"
    new.quote = "Illustrative compound changed the recorded endpoint in the SYNTHETIC assay."
    engine.run.sources[-1].text = new.quote
    flow.ingest([engine.run.sources[-1]])
    for claim in claims:
        claim.evidence_ids = [new.evidence_id] if claim.claim_id == main.claim_id else []
        claim.verdict = "supported" if claim.claim_id == main.claim_id else "unknown"
    engine.call = lambda *args, **kwargs: ReflectionDraft(reviews=[HypothesisReview(hypothesis_id=h.hypothesis_id,
        version=h.version, verdict="retain", reason="SYNTHETIC supported narrow assay claim",
        valid_evidence_ids=[new.evidence_id])], claim_reviews=claims, new_evidence=[new])
    engine.review([h], "SYNTHETIC review")
    assert h.status == "reviewed" and not h.evidence_ids
    assert flow.main_reviewed(h)
    doc = result_document(engine.run)
    assert doc["co_scientist"]["hypotheses"][-1]["status"] == "reviewed"
    assert doc["candidate_assessments"][0]["scientific_status"] == "source_claim_supported_pending_validation"

