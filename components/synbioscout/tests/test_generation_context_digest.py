"""SYNTHETIC compact Generation context retains polarity and formal source trace."""
import json

import pytest
from test_research_flow import attach, draft, hypothesis, setup_flow, source

from scientist.co_models import ChassisSnapshot
from scientist.exports import export_document, markdown_bytes
from scientist.models import CandidateRecord, EvidenceRecord, ResearchBrief
from scientist.presentation import result_document


def long_context():
    engine, _ = setup_flow()
    engine.run.candidates = [CandidateRecord(candidate_id="synthetic_candidate",name="Illustrative compound")]
    h = hypothesis()
    engine.state.hypotheses = [h]
    for number,polarity in enumerate(("supports","mixed","opposes")):
        quote = f"Illustrative compound has a SYNTHETIC {polarity} finding for distinct endpoint {number}."
        # Continuous quote appears well outside the prefix window. Formal review
        # must still retain its actual offset and surrounding original characters.
        text = ("合成来源背景，尚非人体疗效。" * 500) + quote + ("合成连续后文，条件未外推。" * 500)
        src = source(f"pubmed:synthetic_{number}",pmid=f"synthetic_{number}",doi=f"10.0000/synthetic-{number}",text=text,
            metadata={"publication_types":{"pubType":["Randomized Controlled Trial"]}})
        engine.run.sources.append(src)
        e = EvidenceRecord(evidence_id=f"synthetic_{polarity}",source_id=src.source_id,claim=f"SYNTHETIC claim {number}",
            quote=quote,location=f"SYNTHETIC result paragraph {number}",polarity=polarity,study_system="SYNTHETIC children",
            condition="SYNTHETIC adjunctive treatment",endpoint=f"SYNTHETIC distinct endpoint {number}",
            limitations=["SYNTHETIC study-specific limitation"],validated_span=True)
        engine.run.evidence.append(e)
        if polarity != "opposes":
            h.evidence_ids.append(e.evidence_id)
        if polarity in {"opposes","mixed"}:
            h.opposing_evidence_ids.append(e.evidence_id)
    return engine,h


def test_generation_keeps_every_quote_and_its_separate_scoped_polarity():
    engine,h = long_context()
    before = engine.run.model_dump_json()
    generation = engine.context(generation=True)
    quotes = {e["evidence_id"]:e for e in generation["accepted_evidence"]}
    scopes = {e["evidence_id"]:e for p in generation["evidence_digest"]["publications"] for e in p["accepted_citations"]}
    assert set(quotes) == set(scopes) == {e.evidence_id for e in engine.run.evidence}
    for record in engine.run.evidence:
        assert quotes[record.evidence_id]["quote"] == record.quote
        assert quotes[record.evidence_id]["source_id"] == record.source_id
        for field in ("polarity","study_system","condition","endpoint","location","limitations"):
            assert scopes[record.evidence_id][field] == getattr(record,field)
    assert {e["polarity"] for e in scopes.values()} == {"supports","mixed","opposes"}
    assert all(s["windows"] == [] and "retained for formal review" in s["context_coverage"] for s in generation["sources"])
    formal = engine.context([h])
    assert {e["evidence_id"] for e in formal["accepted_evidence"]} == set(quotes)
    original = {s.source_id:s for s in engine.run.sources}
    for item in formal["sources"]:
        src = original[item["source_id"]]
        assert item["windows"]
        for window in item["windows"]:
            assert window["text"] == src.text[window["start"]:window["end"]]
        for e in engine.run.evidence:
            if e.source_id == src.source_id:
                assert any(e.quote in window["text"] for window in item["windows"])
    assert engine.run.model_dump_json() == before


def test_long_source_generation_context_bytes_decrease_without_shortening_citations():
    engine,h = long_context()
    formal = engine.context([h])
    generation = engine.context(generation=True)
    size = lambda payload: len(json.dumps(payload,ensure_ascii=False).encode("utf-8"))
    assert size(generation) < size(formal) * 0.5
    assert sum(len(e["quote"]) for e in generation["accepted_evidence"]) == sum(len(e.quote) for e in engine.run.evidence)
    assert generation["evidence_digest"]["counts"]["polarity_citations"] == {"supports":1,"opposes":1,"mixed":1,"context":0}


def test_missing_chassis_cannot_become_exact_production_support():
    engine,flow = setup_flow()
    engine.state.anchor.chassis_name = ""
    h = hypothesis()
    h.chassis_name = ""
    h.chassis = ChassisSnapshot(status="direct_evidence",strain_match="exact",mode="native_synthesis")
    e = attach(engine,h,quote="Illustrative compound production was measured under the recorded conditions.",system="unspecified system")
    h.chassis.evidence_ids = [e.evidence_id]
    engine.state.hypotheses = [h]
    normalized = engine.normalize_card(h)
    assert normalized.chassis_name == ""
    assert normalized.chassis.status == "unknown"
    assert normalized.chassis.strain_match == "unknown"
    assert normalized.chassis.evidence_ids == []
    engine.state.hypotheses = [normalized]
    flow.register(normalized)
    claim = next(c for c in flow.claims_for(normalized) if c.kind == "chassis_supply")
    flow.adjudicate([normalized],[draft(claim,[e.evidence_id])])
    assert claim.state == "context_only"
    assert not flow.scope_supports(normalized,claim,[e])


@pytest.mark.parametrize("intent,candidates,expected", [("legacy",[],"guided_unspecified"),
    ("legacy",["SYNTHETIC candidate"],"candidate_assessment"),
    ("candidate_assessment",["SYNTHETIC candidate"],"candidate_assessment"),
    ("disease_exploration",[],"disease_exploration")])
def test_public_intent_receipt_and_markdown_do_not_relabel_old_runs_as_blind_discovery(intent,candidates,expected):
    engine,_ = setup_flow()
    engine.run.brief = ResearchBrief(disease="SYNTHETIC",research_mode="co_scientist",task_intent=intent,existing_candidates=candidates)
    before = engine.run.model_dump_json()
    receipt = result_document(engine.run)["task_intent_receipt"]
    assert receipt["declared_intent"] == intent and receipt["effective_intent"] == expected
    assert receipt["discovery_claim"] == "Candidate origins are recorded with the retrieval and generation history of each run"
    assert receipt["historical_memory_allowed"] is (intent != "disease_exploration")
    assert export_document(engine.run)["task_intent_receipt"] == receipt
    text = markdown_bytes(engine.run).decode()
    assert f"任务意图：{intent}" in text and "候选来源与任务意图分别记录" in text
    assert engine.run.model_dump_json() == before

