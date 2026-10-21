"""Offline role guards; all scientific statements below are synthetic fixtures."""
import copy
import json

import pytest
from pydantic import ValidationError
from rdkit import Chem

from scientist.evidence import classify_legacy_role, decide_candidates
from scientist.models import (
    CandidateRecord,
    EvidenceRecord,
    ResearchBrief,
    ScientistSettings,
    SourceRecord,
)
from scientist.research import ResearchEngine
from scientist.store import ProjectStore, digest


def record(text, *, evidence_id="fixture:ev", validated=True, polarity="supports"):
    return EvidenceRecord(
        evidence_id=evidence_id, claim=text, source_id="fixture:source", quote=text,
        location="Synthetic fixture paragraph", polarity=polarity, validated_span=validated,
        limitations=["Synthetic test only; no real experimental result."],
    )


def source(text):
    return SourceRecord(
        source_id="fixture:source", provider="offline_fixture", title="Synthetic role fixture",
        url="https://example.invalid/fixture", retrieved_at="2026-01-01T00:00:00Z", text=text,
    )


def candidate(**updates):
    payload = {
        "candidate_id": "fixture:mol", "name": "Arbitrary synthetic name",
        "identity_status": "resolved", "routes": ["literature"],
        "evidence_ids": ["fixture:ev"], "gate": {"gate_status": "success", "gate_class": "A1"},
    }
    return CandidateRecord(**{**payload, **updates})


def test_historical_contracts_load_without_silently_assigning_roles():
    old = CandidateRecord.model_validate({"candidate_id": "old", "name": "old"})
    assert old.role == "unknown" and old.role_status == "unclassified"
    assert old.role_evidence_ids == [] and old.role_reason == ""
    brief = ResearchBrief.model_validate({"disease": "Synthetic disease"})
    assert brief.population == brief.delivery_context == ""
    assert "科研用途" in brief.research_goal


@pytest.mark.parametrize("field", ["population", "delivery_context", "research_goal"])
def test_new_brief_context_has_bounded_input(field):
    with pytest.raises(ValidationError):
        ResearchBrief(disease="Synthetic disease", **{field: "x" * 1001})


@pytest.mark.parametrize("role", ["biomarker", "risk_factor", "metabolic_context", "compound_class"])
def test_context_roles_never_become_therapeutic_priority_from_positive_gate(role):
    c = candidate(role=role, role_status="model_reviewed", role_evidence_ids=["fixture:ev"])
    decide_candidates([c], [record("Synthetic observation supports the recorded contextual association.")])
    assert c.decision == "hold"
    assert "not a therapeutic shortlist" in c.decision_reasons[0]


@pytest.mark.parametrize("role", ["therapeutic_candidate", "symptom_management"])
def test_reviewed_intervention_role_is_required_for_research_prioritization(role):
    c = candidate(role=role, role_status="model_reviewed", role_evidence_ids=["fixture:ev"])
    decide_candidates([c], [record("Synthetic intervention changed the fixture assay endpoint.")])
    assert c.decision == "prioritize"
    assert any("experiments" in reason for reason in c.decision_reasons)


def test_context_only_role_evidence_cannot_support_therapeutic_prioritization():
    c = candidate(
        role="therapeutic_candidate", role_status="model_reviewed", role_evidence_ids=["fixture:ev"],
        evidence_ids=["fixture:ev", "fixture:other"],
    )
    decide_candidates([c], [
        record("Synthetic intervention context, without supportive endpoint evidence.", polarity="context"),
        record("Another synthetic descriptive claim unrelated to the intervention role.", evidence_id="fixture:other"),
    ])
    assert c.decision == "exploratory"


@pytest.mark.parametrize("updates", [
    {},
    {"role": "therapeutic_candidate", "role_status": "legacy_inferred", "role_evidence_ids": ["fixture:ev"]},
    {"role": "therapeutic_candidate", "role_status": "needs_review", "role_evidence_ids": ["fixture:ev"]},
    {"role": "therapeutic_candidate", "role_status": "model_reviewed", "role_evidence_ids": ["not_attached"]},
])
def test_unknown_unreviewed_or_unattached_roles_cannot_be_prioritized(updates):
    c = candidate(**updates)
    decide_candidates([c], [record("Synthetic association changed a recorded endpoint.")])
    assert c.decision == "exploratory"


@pytest.mark.parametrize("role,text", [
    ("biomarker", "The arbitrary molecule is a diagnostic biomarker in the synthetic study."),
    ("risk_factor", "The arbitrary molecule is a risk factor in this synthetic association."),
    ("risk_factor", "The arbitrary molecule has potential risk associations in this synthetic study."),
    ("metabolic_context", "The molecule belongs to metabolic context, not intervention evidence."),
    ("compound_class", "This label describes a class of compounds, not a single molecule."),
    ("symptom_management", "The arbitrary molecule is investigated for symptom management."),
    ("therapeutic_candidate", "This arbitrary therapeutic candidate was investigated in a controlled trial."),
    ("biomarker", "该任意分子是合成测试中的生物标志物，不是治疗药物。"),
    ("risk_factor", "该任意分子属于合成测试中的风险相关代谢物。"),
    ("metabolic_context", "该任意分子只有代谢关联，尚无干预证据。"),
    ("compound_class", "该标签描述一类分子，不是单一化合物。"),
    ("symptom_management", "该任意分子仅用于症状管理，没有核心疾病改善证据。"),
    ("symptom_management", "该任意分子属于症状终点研究，主要针对 comorbid symptoms。"),
    ("therapeutic_candidate", "该任意分子作为治疗候选进入合成干预研究。"),
])
def test_legacy_role_inference_is_name_independent_and_never_mutates_input(role, text):
    c = candidate(rationale=text)
    before = c.model_dump()
    displayed = classify_legacy_role(c, [record(text)], [source(text)])
    assert displayed.role == role
    assert displayed.role_status == "legacy_inferred"
    assert displayed.role_evidence_ids == ["fixture:ev"]
    assert c.model_dump() == before and displayed is not c
    displayed.evidence_ids.append("display_only")
    assert c.evidence_ids == ["fixture:ev"]


@pytest.mark.parametrize("case", [
    "no_evidence", "unattached", "invalid_span", "missing_source", "changed_quote",
    "rationale_only", "unrelated_full_document", "conflicting_descriptions",
])
def test_legacy_role_inference_requires_explicit_attached_accepted_descriptions(case):
    description = "The synthetic molecule is a diagnostic biomarker."
    c = candidate(rationale=description)
    records, sources = [record(description)], [source(description)]
    if case == "no_evidence":
        records = []
    elif case == "unattached":
        records = [record(description, evidence_id="other")]
    elif case == "invalid_span":
        records = [record(description, validated=False)]
    elif case == "missing_source":
        sources = []
    elif case == "changed_quote":
        sources = [source("Another synthetic source passage, not the cited text.")]
    elif case in {"rationale_only", "unrelated_full_document"}:
        text = "An arbitrary observation was reported in a synthetic cellular assay."
        records = [record(text)]
        sources = [source(text + (" " + description if case == "unrelated_full_document" else ""))]
    elif case == "conflicting_descriptions":
        text = "The synthetic molecule is a diagnostic biomarker and a therapeutic candidate."
        c.rationale, records, sources = text, [record(text)], [source(text)]
    displayed = classify_legacy_role(c, records, sources)
    assert displayed.role == "unknown"
    assert displayed.role_status != "model_reviewed"


def synthesis_draft(role="therapeutic_candidate"):
    text = "The illustrative therapeutic candidate changed the synthetic assay endpoint."
    return {
        "evidence": [{
            "key": "e1", "source_id": "fixture:source", "claim": text, "quote": text,
            "location": "Synthetic fixture", "polarity": "supports", "study_system": "fixture",
            "condition": "fixture", "endpoint": "fixture", "limitations": ["Synthetic fixture"],
        }],
        "targets": [],
        "candidates": [{
            "name": "Arbitrary synthetic name", "evidence_keys": ["e1"], "target_relations": [],
            "rationale": text, "uncertainties": [], "role": role,
            "role_reason": "Synthetic proposed role", "role_evidence_keys": ["e1"],
        }],
        "unresolved_questions": [],
    }


class RoleLLM:
    def __init__(self, mode="confirmed", draft=None):
        self.mode, self.draft = mode, draft or synthesis_draft()
        self.calls = 0

    def complete(self, system, user, schema):
        self.calls += 1
        payload = json.loads(user)
        if schema["title"] == "SynthesisDraft":
            return copy.deepcopy(self.draft)
        assert schema["title"] == "ReviewDraft"
        eid = payload["evidence"][0]["evidence_id"]
        response = {"reviews": [{
            "evidence_id": eid,
            "verdict": "unsupported" if self.mode == "rejected_evidence" else "supported_by_source",
            "reason": "Synthetic claim review only.",
        }]}
        if self.mode != "omitted":
            role = self.draft["candidates"][0]["role"]
            response["candidate_roles"] = [{
                "name": "Arbitrary synthetic name", "role": "biomarker" if self.mode == "different_role" else role,
                "role_supported": self.mode != "unsupported",
                "evidence_ids": ["unattached"] if self.mode == "unrelated" else [eid],
                "reason": "Independent synthetic role review, not laboratory validation.",
            }]
            if self.mode == "duplicate_review":
                response["candidate_roles"].append(copy.deepcopy(response["candidate_roles"][0]))
        return response


def synthesize(tmp_path, mode="confirmed", draft=None):
    store = ProjectStore(tmp_path / "workspace")
    pid = store.create_project("Synthetic role unit test")["project_id"]
    run = store.create_run(pid, ResearchBrief(disease="Synthetic disease"))
    draft = draft or synthesis_draft()
    run.sources = [source(draft["evidence"][0]["quote"])]
    engine = ResearchEngine(store, ScientistSettings(), sources=object())
    llm = RoleLLM(mode, draft)
    engine._synthesize(run, llm, store.run_dir(pid, run.run_id))
    return run, llm, engine


def test_synthesis_reuses_existing_review_call_to_validate_role(tmp_path):
    run, llm, _ = synthesize(tmp_path)
    c = run.candidates[0]
    assert llm.calls == 2  # Synthesis plus existing evidence/link review; no additional role call.
    assert c.role == "therapeutic_candidate" and c.role_status == "model_reviewed"
    assert c.role_evidence_ids == c.evidence_ids == [run.evidence[0].evidence_id]


@pytest.mark.parametrize("mode", [
    "omitted", "unsupported", "unrelated", "rejected_evidence", "different_role", "duplicate_review",
])
def test_roles_require_matching_independent_review_and_accepted_attached_evidence(tmp_path, mode):
    run, _, _ = synthesize(tmp_path, mode)
    c = run.candidates[0]
    assert c.role == "unknown" and c.role_status == "needs_review"
    assert c.role_evidence_ids == []
    assert any("separate review" in reason for reason in c.uncertainties)


def test_role_review_cannot_use_evidence_not_proposed_for_its_role(tmp_path):
    draft = synthesis_draft()
    draft["candidates"][0]["role_evidence_keys"] = []
    run, _, _ = synthesize(tmp_path, draft=draft)
    assert run.candidates[0].role == "unknown"


def test_compound_class_is_not_resolved_into_an_arbitrary_single_molecule(tmp_path):
    run, _, engine = synthesize(tmp_path, draft=synthesis_draft("compound_class"))
    engine._resolve_identities(run)
    assert run.candidates[0].identity_status == "not_single_compound"
    assert run.candidates[0].smiles == ""


def test_old_synthesis_and_review_fixtures_remain_readable_and_unclassified(tmp_path):
    draft = synthesis_draft()
    for field in ["role", "role_reason", "role_evidence_keys"]:
        draft["candidates"][0].pop(field)
    run, llm, _ = synthesize(tmp_path, "omitted", draft)
    assert run.candidates[0].role == "unknown"
    assert llm.calls == 2


def test_conflicting_alias_roles_stay_unknown_after_a_third_alias_is_resolved(tmp_path):
    run, _, engine = synthesize(tmp_path)
    first = run.candidates[0]
    run.candidates = [
        first,
        first.model_copy(deep=True, update={"name": "Second synthetic alias", "role": "risk_factor"}),
        first.model_copy(deep=True, update={"name": "Third synthetic alias"}),
    ]

    class SharedIdentitySources:
        def resolve_molecule(self, name):
            key = Chem.MolToInchiKey(Chem.MolFromSmiles("CCO"))
            identity = {"candidate_id": "mol_" + digest(["CCO", key])[:16], "smiles": "CCO",
                        "inchikey": key, "pubchem_cid": "100001"}
            identity_source = source("Synthetic chemical identity record only.").model_copy(
                update={"source_id": "pubchem:100001", "provider": "pubchem"}
            )
            return identity, identity_source

    engine.sources = SharedIdentitySources()
    engine._resolve_identities(run)
    assert len(run.candidates) == 1
    assert run.candidates[0].role == "unknown" and run.candidates[0].role_status == "needs_review"
    assert run.candidates[0].role_evidence_ids == []

