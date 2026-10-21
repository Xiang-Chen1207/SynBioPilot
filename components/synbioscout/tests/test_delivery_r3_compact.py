"""SYNTHETIC R3 compact-contract controls, never the discarded 17188-byte answer.

The size fixture is constructed here from explicit artificial source records.
No historical run, inference path, prompt, answer or provider is read.
"""
import importlib
import json
from copy import deepcopy
from types import SimpleNamespace

import jsonschema
import pytest

from scientist.assessment_review import bind_assessment_identities, public_validated_evaluations
from scientist.models import ResearchBrief, ResearchRun, ScientistSettings, SourceRecord
from scientist.research import ResearchEngine, SynthesisDraft
from scientist.research_flow import source_observation_statement
from scientist.store import ProjectStore


def json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def compact_module():
    return importlib.import_module("scientist.compact_synthesis")


def compact_case(count=6):
    """New artificial observations with mixed/opposing/context evidence retained."""
    evidence = []
    for index in range(count):
        evidence.append({"key": f"e{index + 1}", "source_id": f"synthetic:r3:{index + 1}",
            "claim": f"The synthetic source reports observation {index + 1} for SC.",
            "quote": f"SYNTHETIC SC observation {index + 1}: the measured marker changed without an established clinical effect.",
            "location": "SYNTHETIC results 原文位置",
            "polarity": ("supports", "opposes", "mixed", "context")[index % 4],
            "study_system": "Homo sapiens children / SYNTHETIC 人群",
            "condition": "SYNTHETIC oral adjunctive trial / 合成记录条件",
            "endpoint": "SYNTHETIC associated symptom and marker endpoints / 合成终点",
            "limitations": ["SYNTHETIC observations; no clinical recommendation or production inference."]})
    return {"evidence": evidence, "targets": [], "candidates": [{
        "name": "Sentinel compound (SC)", "evidence_keys": [e["key"] for e in evidence],
        "target_relations": [], "rationale": "SYNTHETIC source-bound candidate assessment.",
        "role": "unknown", "role_reason": "SYNTHETIC role requires independent review.", "role_evidence_keys": [],
        "uncertainties": ["SYNTHETIC clinical applicability remains unknown."]}],
        "unresolved_questions": ["SYNTHETIC: what additional evidence is needed?"],
        "assessment_proposals": [{"schema_version": 2, "candidate_name": "Sentinel compound (SC)",
            "conclusion": "The supplied synthetic sources report mixed endpoint observations. Clinical applicability remains unknown.",
            "scientific_status": "mixed_evidence", "findings": [
                {"finding_key": f"f{index + 1}", "evidence_key": e["key"], "projection": "source_observation"}
                for index, e in enumerate(evidence)],
            "unknowns": ["SYNTHETIC clinical applicability and delivery remain unknown."],
            "next_questions": ["SYNTHETIC: which study can resolve the stated scope limits?"]}]}


def legacy_equivalent(compact):
    """Independent expected literal expansion, with no inferred scientific text."""
    legacy = deepcopy(compact)
    by_key = {e["key"]: e for e in compact["evidence"]}
    for proposal in legacy["assessment_proposals"]:
        proposal.pop("schema_version")
        findings = []
        for reference in proposal["findings"]:
            evidence = by_key[reference["evidence_key"]]
            findings.append({"finding_key": reference["finding_key"],
                "statement": source_observation_statement(evidence["source_id"], evidence["quote"]),
                "evidence_keys": [evidence["key"]],
                "scope": {key: evidence[key] for key in ("study_system", "condition", "endpoint")}})
        proposal["findings"] = findings
    return legacy


def synthetic_17188_pair():
    """A sized OLD SHAPE fixture, not evidence about the unread real response."""
    compact = compact_case()
    target = 17188
    remaining = target - json_bytes(legacy_equivalent(compact))
    assert remaining > 0
    if remaining % 2:
        compact["assessment_proposals"][0]["conclusion"] += " "
        remaining -= 1
    # Each added quote byte is represented twice in the old wire shape, once
    # in evidence and once in the explicitly selected source-observation finding.
    for evidence in compact["evidence"]:
        amount = min(remaining // 2, 1080 - len(evidence["quote"]))
        evidence["quote"] += "x" * amount
        remaining -= amount * 2
    assert remaining == 0
    legacy = legacy_equivalent(compact)
    assert json_bytes(legacy) == target
    return compact, legacy


def synthetic_sources(compact):
    return [SourceRecord(source_id=e["source_id"], provider="synthetic", title="SYNTHETIC R3 trial",
        url="https://example.invalid/synthetic-r3", retrieved_at="2026-10-16T00:00:00Z", evidence_scope="abstract",
        text="SYNTHETIC Methods: 31 human children were enrolled and randomized to SC or placebo. " + e["quote"])
        for e in compact["evidence"]]


def test_artificial_old_17188_shape_compacts_below_12000_without_claiming_real_answer_contents():
    compact, legacy = synthetic_17188_pair()
    assert json_bytes(legacy) == 17188
    assert json_bytes(compact) < 12000
    assert all(e["claim"].startswith("The synthetic") for e in compact["evidence"])
    expanded, receipt = compact_module().expand_synthesis_draft(compact)
    assert expanded.model_dump(mode="json") == SynthesisDraft.model_validate(legacy).model_dump(mode="json")
    assert receipt["schema_version"] == 2
    assert receipt["wire_bytes"] == json_bytes(compact)
    assert receipt["expanded_bytes"] == json_bytes(expanded.model_dump(mode="json"))
    assert receipt["growth_bytes"] == receipt["expanded_bytes"] - receipt["wire_bytes"] > 0
    assert receipt["evidence_count"] == receipt["finding_count"] == 6


def test_scope_quotes_polarities_and_all_model_prose_survive_exactly_once_on_wire():
    compact = compact_case()
    before = deepcopy(compact)
    expanded, _ = compact_module().expand_synthesis_draft(compact)
    assert compact == before
    result = expanded.model_dump(mode="json")
    assert result == legacy_equivalent(compact)
    assert {e["polarity"] for e in result["evidence"]} == {"supports", "opposes", "mixed", "context"}
    wire = json.dumps(compact, ensure_ascii=False)
    for e in compact["evidence"]:
        assert wire.count(e["quote"]) == 1
    proposal = result["assessment_proposals"][0]
    for key in ("conclusion", "scientific_status", "unknowns", "next_questions"):
        assert proposal[key] == compact["assessment_proposals"][0][key]
    assert not any("review" in key for key in result)


def test_utf8_receipt_counts_multibyte_and_expansion_not_character_count():
    compact = compact_case(1)
    compact["evidence"][0]["quote"] += " 合成中文观察 🧪 " * 30
    expanded, receipt = compact_module().expand_synthesis_draft(compact)
    assert receipt["wire_bytes"] == json_bytes(compact) > len(json.dumps(compact, ensure_ascii=False))
    assert receipt["expanded_bytes"] == json_bytes(expanded.model_dump(mode="json"))
    assert receipt["growth_bytes"] == receipt["expanded_bytes"] - receipt["wire_bytes"]


def test_expansion_preserves_unselected_opposition_without_inventing_a_finding():
    compact = compact_case(2)
    assert compact["evidence"][1]["polarity"] == "opposes"
    compact["assessment_proposals"][0]["findings"] = compact["assessment_proposals"][0]["findings"][:1]
    expanded, receipt = compact_module().expand_synthesis_draft(compact)
    assert expanded.evidence[1].polarity == "opposes"
    assert len(expanded.assessment_proposals[0].findings) == 1
    assert expanded.assessment_proposals[0].findings[0].evidence_keys == ["e1"]
    assert receipt["evidence_count"] == 2 and receipt["finding_count"] == 1


def test_local_expansion_growth_is_separate_from_received_and_accepted_output_bytes():
    from scientist.budgets import RunLedger
    from scientist.models import ResearchBudget

    compact, _ = synthetic_17188_pair()
    ledger = RunLedger(ResearchBudget(), 10**12)
    # A provider returned the compact JSON. Local expansion is not a second
    # provider output; the next real request must account for its expanded data.
    ledger.output(json.dumps(compact, ensure_ascii=False), call_allowance=12000)
    before = ledger.snapshot()
    expanded, receipt = compact_module().expand_synthesis_draft(compact)
    assert json_bytes(expanded.model_dump(mode="json")) > 12000
    assert receipt["growth_bytes"] > 0
    assert ledger.snapshot() == before
    assert before["output_tokens_reserved"] == before["output_bytes_received"] == receipt["wire_bytes"]


@pytest.mark.parametrize("change", ["missing_evidence", "duplicate_evidence", "foreign_key", "duplicate_finding",
    "repeated_reference", "wrong_candidate", "foreign_candidate_key", "duplicate_candidate_key", "foreign_role_key",
    "role_key_outside_candidate", "foreign_target_key", "provided_scope", "provided_statement", "unknown_projection",
    "wrong_version", "float_version", "string_version", "bool_version", "mixed_versions", "reused_across_proposals"])
def test_invalid_compact_reference_contract_fails_without_mutating_input(change):
    compact = compact_case(2)
    proposal = compact["assessment_proposals"][0]
    first = proposal["findings"][0]
    if change == "missing_evidence": compact["evidence"] = []
    elif change == "duplicate_evidence": compact["evidence"].append(deepcopy(compact["evidence"][0]))
    elif change == "foreign_key": first["evidence_key"] = "foreign"
    elif change == "duplicate_finding": proposal["findings"][1]["finding_key"] = first["finding_key"]
    elif change == "repeated_reference": proposal["findings"][1]["evidence_key"] = first["evidence_key"]
    elif change == "wrong_candidate": proposal["candidate_name"] = "Different entity"
    elif change == "foreign_candidate_key": compact["candidates"][0]["evidence_keys"].append("foreign")
    elif change == "duplicate_candidate_key": compact["candidates"][0]["evidence_keys"].append("e1")
    elif change == "foreign_role_key": compact["candidates"][0]["role_evidence_keys"] = ["foreign"]
    elif change == "role_key_outside_candidate":
        compact["candidates"][0]["evidence_keys"] = ["e1"]
        compact["candidates"][0]["role_evidence_keys"] = ["e2"]
        proposal["findings"] = [first]
    elif change == "foreign_target_key":
        compact["targets"] = [{"gene_symbol": "SYNTHETIC", "desired_direction": "unknown", "rationale": "SYNTHETIC",
            "related_cells": [], "pathways": [], "evidence_keys": ["foreign"], "uncertainties": []}]
    elif change == "provided_scope": first["scope"] = {"study_system": "Wrong species", "condition": "Different", "endpoint": "Different"}
    elif change == "provided_statement": first["statement"] = "Invented treatment claim"
    elif change == "unknown_projection": first["projection"] = "clinical_efficacy"
    elif change == "wrong_version": proposal["schema_version"] = 3
    elif change == "float_version": proposal["schema_version"] = 2.0
    elif change == "string_version": proposal["schema_version"] = "2"
    elif change == "bool_version": proposal["schema_version"] = True
    elif change == "mixed_versions": compact["assessment_proposals"].append(legacy_equivalent(compact)["assessment_proposals"][0])
    elif change == "reused_across_proposals":
        compact["candidates"].append({**deepcopy(compact["candidates"][0]), "name": "Different entity"})
        compact["assessment_proposals"].append({**deepcopy(proposal), "candidate_name": "Different entity"})
    before = deepcopy(compact)
    with pytest.raises(ValueError):
        compact_module().expand_synthesis_draft(compact)
    assert compact == before


def test_legacy_shape_stays_legacy_and_empty_old_records_get_no_new_findings():
    legacy = legacy_equivalent(compact_case(1))
    expanded, receipt = compact_module().expand_synthesis_draft(legacy)
    assert expanded.model_dump(mode="json") == legacy
    assert receipt["schema_version"] == 1
    no_assessment = {k: deepcopy(v) for k, v in legacy.items() if k != "assessment_proposals"}
    expanded, receipt = compact_module().expand_synthesis_draft(no_assessment)
    assert expanded.assessment_proposals == [] and receipt["schema_version"] == 1


def test_legacy_and_compact_branches_validate_under_compatible_synthesis_schema():
    compact = compact_case(2)
    schema = SynthesisDraft.model_json_schema()
    jsonschema.validate(compact, schema)
    jsonschema.validate(legacy_equivalent(compact), schema)
    assert schema["title"] == "SynthesisDraft"
    compact_module().CompactSynthesisDraft.model_validate(compact)


class TransparentSynthesis:
    """Fixed explicit responses, no provider call and no scientific judgement."""
    def __init__(self, value):
        self.value, self.calls, self.payloads = value, 0, []

    def complete(self, _system, user, schema):
        self.calls += 1
        payload = json.loads(user)
        self.payloads.append((schema["title"], payload))
        if schema["title"] == "SynthesisDraft":
            return deepcopy(self.value)
        assert schema["title"] == "ReviewDraft"
        return {"reviews": [{"evidence_id": e["evidence_id"], "verdict": "supported_by_source",
            "reason": "SYNTHETIC transparent role review only."} for e in payload["evidence"]],
            "target_links": [], "molecule_links": [], "candidate_roles": []}


def run_synthetic_extraction(tmp_path, compact):
    run = ResearchRun(run_id="r_synthetic_r3", project_id="p_synthetic_r3", created_at="synthetic", updated_at="synthetic",
        status="running", brief=ResearchBrief(disease="SYNTHETIC syndrome", research_mode="co_scientist",
            task_intent="candidate_assessment", existing_candidates=["Sentinel compound (SC)"]),
        sources=synthetic_sources(compact))
    engine = ResearchEngine(ProjectStore(tmp_path), ScientistSettings(llm={"backend": "none"}), sources=SimpleNamespace())
    worker = TransparentSynthesis(compact)
    engine._synthesize(run, worker, tmp_path / "SYNTHETIC_R3_artifacts")
    return run, worker


def test_normal_extraction_passes_all_expanded_evidence_to_independent_review_and_leaves_draft_unreviewed(tmp_path):
    compact = compact_case(4)
    run, worker = run_synthetic_extraction(tmp_path, compact)
    assert worker.calls == 2
    assert len(run.evidence) == 4 and len(run.candidate_evaluations) == 1
    reviewed = worker.payloads[1][1]["evidence"]
    assert {e["polarity"] for e in reviewed} == {"supports", "opposes", "mixed", "context"}
    assert [e["quote"] for e in reviewed] == [e["quote"] for e in compact["evidence"]]
    assert len(run.candidate_evaluations[0].draft.findings) == 4
    assert run.candidate_evaluations[0].final is None
    assert public_validated_evaluations(run) == []


@pytest.mark.parametrize("wrong", ["entity", "species", "endpoint"])
def test_reference_expansion_cannot_waive_existing_entity_and_scope_gates(tmp_path, wrong):
    compact = compact_case(1)
    if wrong == "entity":
        compact["candidates"][0]["name"] = "Different compound"
        compact["assessment_proposals"][0]["candidate_name"] = "Different compound"
    elif wrong == "species":
        compact["evidence"][0]["study_system"] = "rat"
    else:
        compact["evidence"][0]["endpoint"] = "Core symptoms"
    run, _ = run_synthetic_extraction(tmp_path, compact)
    candidate = run.candidates[0]
    candidate.identity_status = "resolved"
    candidate.smiles, candidate.inchikey, candidate.pubchem_cid = "CCO", "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "702"
    assert bind_assessment_identities(run) == []
    assert public_validated_evaluations(run) == []


def test_oversized_actual_followup_is_partial_after_admitted_compact_output(tmp_path, monkeypatch):
    """Budget boundary fixture, not a claim that the normal six-source input grows this large."""
    from test_budget_scheduling import setup_engine

    from scientist.coscientist import CoScientistEngine
    from scientist.research import ReviewDraft

    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    compact, _ = synthetic_17188_pair()
    run = engine.run
    run.brief.task_intent = "candidate_assessment"
    run.sources = synthetic_sources(compact)
    run.evidence = []
    run.co_scientist = None
    engine.store.save_run(run)
    attempts = []

    def factory(*_args, **_kwargs):
        attempts.append("SYNTHETIC compact provider")
        worker = SimpleNamespace(calls=0, cache_hits=0)

        def complete(*_args):
            assert len(attempts) == 1, "The oversized actual request must not reach a provider"
            worker.calls += 1
            return deepcopy(compact)

        worker.complete = complete
        return worker

    engine.legacy.llm_factory = factory

    def boundary_fixture(self):
        raw = self.invoke("supervisor", "SYNTHETIC compact extraction", "SYNTHETIC", "{}",
            SynthesisDraft.model_json_schema(), output_cap=12000)
        expanded, receipt = compact_module().expand_synthesis_draft(raw)
        self.synthesis_expansion_receipt = receipt
        assert receipt["wire_bytes"] < 12000 and receipt["expanded_bytes"] < 36000
        # Independently enlarged later source context, not compact expansion:
        # its actual UTF-8 request exceeds the whole original 300000-byte limit.
        payload = {"expanded_synthesis": expanded.model_dump(mode="json"),
                   "synthetic_additional_source_context": "合成原文" * 30000}
        self.call("reflection", "SYNTHETIC oversized actual follow-up", ReviewDraft, payload, output_cap=12000)

    monkeypatch.setattr(CoScientistEngine, "initial_research", boundary_fixture)
    result = engine.execute(run.project_id, run.run_id)
    assert result.status.value == "partial"
    assert len(attempts) == result.usage["llm_calls"] == 1
    assert result.usage["output_bytes_received"] == result.usage["output_tokens_reserved"] == json_bytes(compact)
    assert result.usage["input_tokens_reserved"] < 300000
    assert result.usage["output_bytes_inflight"] == 0
    failure = next(row for row in result.co_scientist.budget_failures if row.get("reason") == "Input reservation budget exhausted")
    assert failure["required_bytes"] > 300000 and failure["blocked_before_provider"] is True
    assert failure["accounting_unit"] == "utf8_bytes"
    assert not result.evidence and not result.candidate_evaluations
    assert public_validated_evaluations(result) == []


def test_measured_growth_updates_next_reserve_without_changing_provider_output_count(tmp_path, monkeypatch):
    from test_budget_scheduling import setup_engine

    from scientist.coscientist import EvidenceLLM

    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    engine.run.brief.task_intent = "candidate_assessment"
    compact, _ = synthetic_17188_pair()
    _, receipt = compact_module().expand_synthesis_draft(compact)
    before = engine.candidate_core_reserve("semantic_review")
    counts = dict(engine.ledger.counts)
    EvidenceLLM(engine).note_synthesis_expansion(receipt)
    after = engine.candidate_core_reserve("semantic_review")
    assert after["bytes"] - before["bytes"] == receipt["growth_bytes"]
    assert after["output_bytes"] == before["output_bytes"] == 12000
    assert engine.ledger.counts == counts

