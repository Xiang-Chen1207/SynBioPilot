"""SYNTHETIC FIX1: a general endpoint result cannot support a named candidate."""
import csv
import io
import json
import zipfile

import pytest
from test_research_flow import draft, hypothesis, setup_flow, source

from scientist.co_models import HypothesisReview
from scientist.coscientist import ReflectionDraft
from scientist.exports import export_document, export_research, markdown_bytes, table_exports
from scientist.models import CandidateRecord, EvidenceRecord, ExecutionStatus
from scientist.presentation import result_document
from scientist.store import ProjectStore


def reviewed_case(*, explicit_entity=False, candidate_polarity="supports", candidate_condition="SYNTHETIC condition"):
    """Exercise real review adjudication; only model response and source are fixtures."""
    engine, flow = setup_flow()
    h = hypothesis()
    h.claim = "The associated endpoint changed under the recorded conditions."
    engine.state.hypotheses = [h]
    flow.register(h)
    engine.run.candidates = [CandidateRecord(candidate_id="synthetic_compound", name="Illustrative compound",
        identity_status="needs_review", role_status="needs_review", decision="hold")]
    engine.run.brief.existing_candidates = ["Illustrative compound"]
    generic_quote = "The associated endpoint changed under the recorded conditions."
    quote = "Illustrative compound changed the associated endpoint under the recorded conditions." if explicit_entity else generic_quote
    src = source(text=quote)
    engine.run.sources.append(src); flow.ingest([src])
    evidence = EvidenceRecord(evidence_id="synthetic_result", source_id=src.source_id, claim="SYNTHETIC endpoint observation",
        quote=quote, location="SYNTHETIC abstract result", polarity=candidate_polarity,
        condition=candidate_condition, endpoint=h.endpoint, study_system="SYNTHETIC assay")
    main = next(c for c in flow.claims_for(h) if c.kind == "main")
    query = flow.new_query("supplement", "support", "SYNTHETIC endpoint result",
        claim_ids=[main.claim_id], hypothesis_id=h.hypothesis_id, version=h.version,
        status="ok", source_ids=[src.source_id])
    main.query_ids.append(query.query_id)
    refs = [evidence]
    # A separate, applicable generic result lets main remain supported while the
    # candidate-specific citation alone is merely context or inapplicable.
    if explicit_entity and (candidate_polarity != "supports" or candidate_condition != h.condition):
        generic_source = source("pubmed:synthetic_generic", text=generic_quote, pmid="synthetic_generic", doi="10.0000/synthetic_generic")
        engine.run.sources.append(generic_source); flow.ingest([generic_source])
        refs.append(EvidenceRecord(evidence_id="synthetic_generic_result", source_id=generic_source.source_id,
            claim="SYNTHETIC generic endpoint", quote=generic_quote, location="SYNTHETIC abstract result", polarity="supports",
            condition=h.condition, endpoint=h.endpoint, study_system="SYNTHETIC assay"))
        query.source_ids.append(generic_source.source_id)
    response = ReflectionDraft(reviews=[HypothesisReview(hypothesis_id=h.hypothesis_id, version=h.version,
        verdict="retain", reason="SYNTHETIC narrow main endpoint review", valid_evidence_ids=[e.evidence_id for e in refs])],
        claim_reviews=[draft(c, [e.evidence_id for e in refs] if c.kind == "main" else [],
                            verdict="supported" if c.kind == "main" else "unknown") for c in flow.claims_for(h)],
        new_evidence=refs)
    engine.call = lambda *args, **kwargs: response
    original_claim = h.claim
    engine.review([h], "SYNTHETIC exact-version review")
    assert h.claim == original_claim
    assert flow.verify()["valid"]
    assert next(c for c in flow.claims_for(h) if c.kind == "intervention").state == "unknown"
    return engine, flow, h


def test_generic_supported_main_does_not_project_to_candidate():
    engine, flow, h = reviewed_case()
    assert flow.main_reviewed(h)
    assert engine.run.candidates[0].evidence_ids == []
    card = result_document(engine.run)["candidate_assessments"][0]
    assert card["review_associations"] == []
    assert card["evidence_ids"] == []
    assert card["scientific_status"] == "needs_evidence"


@pytest.mark.parametrize("explicit_entity,polarity,condition,expected", [
    (True, "supports", "SYNTHETIC condition", "source_claim_supported_pending_validation"),
    (True, "context", "SYNTHETIC condition", "needs_evidence"),
    (True, "mixed", "SYNTHETIC condition", "needs_evidence"),
    (True, "opposes", "SYNTHETIC condition", "needs_evidence"),
    (True, "supports", "other SYNTHETIC condition", "needs_evidence"),
])
def test_candidate_support_requires_its_own_applicable_supported_association(explicit_entity, polarity, condition, expected):
    engine, flow, h = reviewed_case(explicit_entity=explicit_entity, candidate_polarity=polarity, candidate_condition=condition)
    if polarity in {"context", "supports"}:
        assert flow.main_reviewed(h)  # generic support cannot rescue this candidate's context/mismatched condition
    card = result_document(engine.run)["candidate_assessments"][0]
    assert card["scientific_status"] == expected
    assert card["review_associations"]
    if polarity in {"mixed", "opposes"}:
        assert "synthetic_result" in card["opposing_evidence_ids"]
    assert engine.run.candidates[0].decision == "hold"
    assert engine.run.candidates[0].identity_status == "needs_review"


@pytest.mark.parametrize("explicit_entity,expected", [(False,"needs_evidence"),
    (True,"source_claim_supported_pending_validation")])
def test_public_result_and_all_export_formats_agree_without_rewriting_history(tmp_path, explicit_entity, expected):
    engine, _, _ = reviewed_case(explicit_entity=explicit_entity)
    store = ProjectStore(tmp_path)
    project = store.create_project("SYNTHETIC candidate projection regression")
    engine.run.project_id = project["project_id"]
    engine.run.run_id = store.create_run(project["project_id"], engine.run.brief).run_id
    engine.run.status = ExecutionStatus.COMPLETED
    store.save_run(engine.run)
    path = store.run_dir(engine.run.project_id, engine.run.run_id) / "run.json"
    before_bytes = path.read_bytes()
    before_object = engine.run.model_dump_json()
    document = result_document(engine.run)
    exported = export_document(engine.run)
    assert document["candidate_assessments"][0]["scientific_status"] == expected
    assert exported["candidate_assessments"][0]["scientific_status"] == expected
    assert next(csv.DictReader(io.StringIO(table_exports(engine.run)["candidates.csv"].decode("utf-8-sig"))))["scientific_status"] == expected
    markdown = markdown_bytes(engine.run).decode()
    assert "身份/科学状态：needs_review / " + expected in markdown
    archive_bytes, _, _ = export_research(store, engine.run, "zip")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert json.loads(archive.read("candidate_assessments.json"))[0]["scientific_status"] == expected
        assert json.loads(archive.read("research.json"))["candidate_assessments"][0]["scientific_status"] == expected
        assert next(csv.DictReader(io.StringIO(archive.read("candidates.csv").decode("utf-8-sig"))))["scientific_status"] == expected
        assert "身份/科学状态：needs_review / " + expected in archive.read("report.md").decode()
    assert path.read_bytes() == before_bytes
    assert engine.run.model_dump_json() == before_object


def test_prior_version_candidate_support_does_not_upgrade_current_generic_claim():
    engine, flow, old = reviewed_case(explicit_entity=True)
    old_snapshot = old.model_dump()
    latest = old.model_copy(deep=True, update={"version":2,"parent_version":1,"status":"proposed"})
    engine.state.hypotheses.append(latest); flow.register(latest)
    src = source("pubmed:synthetic_latest", text="The associated endpoint changed under the recorded conditions.",
        pmid="synthetic_latest", doi="10.0000/synthetic_latest")
    engine.run.sources.append(src); flow.ingest([src])
    evidence = EvidenceRecord(evidence_id="synthetic_latest_generic", source_id=src.source_id,
        claim="SYNTHETIC general endpoint", quote=src.text, location="SYNTHETIC result", polarity="supports",
        condition=latest.condition, endpoint=latest.endpoint, study_system="SYNTHETIC assay")
    main = next(c for c in flow.claims_for(latest) if c.kind == "main")
    query = flow.new_query("supplement", "support", "SYNTHETIC latest query", claim_ids=[main.claim_id],
        hypothesis_id=latest.hypothesis_id, version=latest.version, status="ok", source_ids=[src.source_id])
    main.query_ids.append(query.query_id)
    engine.call = lambda *args, **kwargs: ReflectionDraft(reviews=[HypothesisReview(hypothesis_id=latest.hypothesis_id,
        version=2,verdict="retain",reason="SYNTHETIC generic result",valid_evidence_ids=[evidence.evidence_id])],
        claim_reviews=[draft(c,[evidence.evidence_id] if c.kind == "main" else [],
            verdict="supported" if c.kind == "main" else "unknown") for c in flow.claims_for(latest)],new_evidence=[evidence])
    engine.review([latest], "SYNTHETIC current version")
    assert flow.main_reviewed(latest)
    assert engine.run.candidates[0].evidence_ids == ["synthetic_result"]  # historical candidate-bound citation remains
    card = result_document(engine.run)["candidate_assessments"][0]
    assert card["review_associations"] == []
    assert card["scientific_status"] == "needs_evidence"
    assert card["hypothesis_versions"] == [{"hypothesis_id":latest.hypothesis_id,"version":2,"review_state":"supported"}]
    assert old.model_dump() == old_snapshot

