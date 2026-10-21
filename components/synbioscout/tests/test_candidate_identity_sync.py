"""SYNTHETIC current-run chemical identity regressions; no provider/network calls."""
from __future__ import annotations

import copy
import hashlib
import json

from rdkit import Chem

from scientist.co_models import CoScientistState, HypothesisCard
from scientist.evidence import decide_candidates
from scientist.identity import CandidateIdentitySync, declared_aliases, merge_identity_annotations
from scientist.models import (
    CandidateRecord,
    EvidenceRecord,
    ExecutionStatus,
    ResearchBrief,
    ResearchRun,
    SourceRecord,
)
from scientist.presentation import result_document
from scientist.store import digest
from scientist.tool_plans import ToolScheduler
from scientist.tools import ToolRequest, ToolResult


def candidate(name="Synthetic lactate (SL)", identifier="unresolved_synthetic"):
    return CandidateRecord(candidate_id=identifier, name=name, identity_status="needs_review",
        role="symptom_management", role_status="model_reviewed", decision="hold",
        decision_reasons=["Chemical identity needs resolution", "No clinical validation"],
        uncertainties=["Identity unresolved: earlier empty observation", "Strain and exposure unknown"],
        gate={"status": "unknown"}, admet={"status": "not_run"})


def run(candidates=None):
    state = CoScientistState(hypotheses=[HypothesisCard(hypothesis_id="h_synthetic", title="SYNTHETIC",
        claim="SYNTHETIC untested candidate", molecule_names=["Synthetic lactate"],
        chassis_name="SYNTHETIC L168", condition="unknown", endpoint="SYNTHETIC endpoint",
        prediction="SYNTHETIC measurement", validation_question="SYNTHETIC question",
        origin="user_supplied", inference_gaps=["PubChem identity was empty at generation", "Unknown exposure"])])
    return ResearchRun(run_id="r_synthetic", project_id="p_synthetic", created_at="fixture", updated_at="fixture",
                       status=ExecutionStatus.RUNNING, brief=ResearchBrief(disease="SYNTHETIC disease",
                       existing_candidates=["Synthetic lactate (SL)"]),
                       candidates=candidates if candidates is not None else [candidate()], co_scientist=state)


def resolved(query="Synthetic lactate", *, smiles="C[C@@H](O)C(=O)[O-].[Na+]", cid="12345", call="tool_002"):
    molecule = Chem.MolFromSmiles(smiles)
    canonical, key = Chem.MolToSmiles(molecule, isomericSmiles=True), Chem.MolToInchiKey(molecule)
    source = SourceRecord(source_id="pubchem:" + cid, provider="pubchem", title="SYNTHETIC identity",
                          url="https://pubchem.ncbi.nlm.nih.gov/compound/" + cid, retrieved_at="fixture",
                          evidence_scope="database")
    return ToolResult(tool_name="pubchem", provider="pubchem", parameters={"name": query},
        status="ok", retrieved_at="fixture", call_id=call, sources=[source],
        records=[{"name_query": query, "candidate_id": "mol_" + digest([canonical, key])[:16],
                  "smiles": canonical, "inchikey": key, "pubchem_cid": cid}],
        identity={"status": "resolved", "entity_type": "molecule", "query": query,
                  "selected_id": cid, "inchikey": key})


def request(query="Synthetic lactate"):
    return ToolRequest(name="pubchem", arguments={"name": query})


def test_failed_initial_later_explicit_alias_success_survives_budget_stop():
    current = run()
    sync = CandidateIdentitySync(current, current.co_scientist)
    hypothesis_before = current.co_scientist.hypotheses[0].model_dump()
    scientific_before = current.candidates[0].model_dump(include={"decision", "decision_reasons", "role", "role_status", "gate", "admet"})
    calls = []

    def execute(req, **metadata):
        calls.append(req)
        if len(calls) == 1:
            return ToolResult(tool_name="pubchem", provider="pubchem", parameters=req.arguments,
                status="empty", retrieved_at="fixture", call_id="tool_000",
                identity={"status": "empty", "entity_type": "molecule", "query": req.arguments["name"]})
        if len(calls) == 2:
            return resolved()
        return ToolResult(tool_name="pubchem", provider="none", parameters=req.arguments,
                          status="budget_exhausted", retrieved_at="fixture", call_id="tool_003")

    plans = []
    scheduler = ToolScheduler(execute, plans, bind_candidate=sync.bind, apply_identity=sync.apply)
    failed = scheduler.create(request("Synthetic lactate (SL)"), origin="supervisor_identity")
    scheduler.run(failed)
    assert failed["identity_application"]["status"] == "not_resolved"
    success = scheduler.create(request(), hypothesis_ids=["h_synthetic"])
    scheduler.run(success)
    duplicate = scheduler.create(request(), hypothesis_ids=["different_hypothesis"])
    before_duplicate = current.candidates[0].model_dump()
    assert scheduler.run(duplicate) is None
    assert len(calls) == 2 and duplicate["status"] == "skipped"
    assert duplicate["identity_application"]["status"] == "unchanged"
    assert current.candidates[0].model_dump() == before_duplicate
    scheduler.run(scheduler.create(request("SYNTHETIC unrelated molecule")))
    current.status = ExecutionStatus.PARTIAL
    entity = current.candidates[0]
    assert entity.identity_status == "resolved" and entity.identity_revision == 1
    assert entity.entity_id == "unresolved_synthetic" and entity.original_ids == ["unresolved_synthetic"]
    assert entity.pubchem_cid == "12345" and "@" in entity.smiles and "[Na+]" in entity.smiles
    assert entity.identity_form["components"] == entity.smiles.split(".")
    assert entity.aliases == ["Synthetic lactate", "SL"]
    assert entity.identity_source_ids == ["pubchem:12345"]
    assert entity.uncertainties == ["Strain and exposure unknown"]
    assert entity.identity_history[1]["historical_identity_gaps"] == ["Identity unresolved: earlier empty observation"]
    assert entity.model_dump(include=set(scientific_before)) == scientific_before
    assert current.co_scientist.hypotheses[0].model_dump() == hypothesis_before
    ref = current.co_scientist.entity_references[0]
    assert ref["candidate_id"] == entity.candidate_id and ref["identity_status"] == "resolved"
    assert ref["hypothesis_versions"] == [{"hypothesis_id": "h_synthetic", "version": 1,
                                         "historical_identity_gaps": ["PubChem identity was empty at generation"]}]


def test_only_unique_exact_recorded_aliases_bind_not_hypothesis_or_similar_name():
    current = run()
    sync = CandidateIdentitySync(current, current.co_scientist)
    before = current.model_dump()
    assert sync.bind(request("Synthetic lactat"))["status"] == "unlinked"
    # Hypothesis IDs alone grant no candidate relationship.
    application = sync.apply({"hypothesis_ids": ["h_synthetic"]}, request(), resolved())
    assert application["status"] == "unlinked" and current.model_dump() == before
    forged = {"candidate_link": {"status": "verified", "verification": "unique_exact_recorded_alias",
                                 "candidate_id": "unresolved_synthetic", "query": "Synthetic lactat"}}
    assert sync.apply(forged, request("Synthetic lactat"), resolved("Synthetic lactat"))["status"] == "unlinked"
    current.candidates.append(candidate("Synthetic lactate", "unresolved_other"))
    assert sync.bind(request())["status"] == "ambiguous"
    before = current.model_dump()
    assert sync.apply({"candidate_link": sync.bind(request())}, request(), resolved())["status"] == "unlinked"
    assert current.model_dump() == before


def test_conflict_and_ambiguous_observation_do_not_replace_existing_identity_or_promote():
    current = run()
    sync = CandidateIdentitySync(current, current.co_scientist)
    plan = {"candidate_link": sync.bind(request())}
    assert sync.apply(plan, request(), resolved())["status"] == "applied"
    before = current.candidates[0].model_dump(include={"candidate_id", "smiles", "inchikey", "pubchem_cid", "identity_revision", "decision", "role", "gate"})
    conflict = sync.apply(plan, request(), resolved(smiles="CCO", cid="99999", call="tool_004"))
    assert conflict["status"] == "conflict"
    assert current.candidates[0].identity_status == "needs_review"
    assert current.candidates[0].model_dump(include=set(before)) == before
    ambiguous = ToolResult(tool_name="pubchem", provider="pubchem", parameters={"name": "Synthetic lactate"},
        status="unavailable", retrieved_at="fixture", identity={"status": "ambiguous", "candidates": []}, call_id="tool_005")
    assert sync.apply(plan, request(), ambiguous)["status"] == "not_resolved"
    assert current.candidates[0].model_dump(include=set(before)) == before


def test_current_reference_updates_new_version_without_changing_historical_cards():
    current = run()
    sync = CandidateIdentitySync(current, current.co_scientist)
    original = copy.deepcopy(current.co_scientist.hypotheses[0].model_dump())
    updated = current.co_scientist.hypotheses[0].model_copy(deep=True, update={"version": 2, "parent_version": 1})
    current.co_scientist.hypotheses.append(updated)
    sync.apply({"candidate_link": sync.bind(request())}, request(), resolved())
    assert current.co_scientist.hypotheses[0].model_dump() == original
    assert current.co_scientist.entity_references[0]["hypothesis_versions"][0]["version"] == 2
    assert updated.origin == "user_supplied" and updated.status == "proposed"
    updated.molecule_names = []
    sync.refresh_references()
    assert current.co_scientist.entity_references[0]["hypothesis_versions"] == []


def test_initial_connector_uses_same_verifier_and_is_idempotent():
    current = run([candidate("Synthetic lactate")])
    sync = CandidateIdentitySync(current)
    observation = resolved()
    values = {k: v for k, v in observation.records[0].items() if k != "name_query"}
    assert sync.record_initial(current.candidates[0], values, observation.sources[0])["status"] == "applied"
    before = current.candidates[0].model_dump()
    assert sync.record_initial(current.candidates[0], values, observation.sources[0])["status"] == "unchanged"
    assert current.candidates[0].model_dump() == before
    corrupted = {**values, "inchikey": "MISMATCH"}
    assert sync.record_initial(current.candidates[0], corrupted, observation.sources[0])["status"] == "conflict"
    assert current.candidates[0].smiles == values["smiles"]


def test_supervisor_scheduler_then_initial_adapter_records_one_observation():
    current = run([candidate("Synthetic lactate")])
    sync = CandidateIdentitySync(current, current.co_scientist)
    observation = resolved()
    scheduler = ToolScheduler(lambda req, **kwargs: observation, [],
                              bind_candidate=sync.bind, apply_identity=sync.apply)
    scheduler.run(scheduler.create(request(), origin="supervisor_identity"))
    before = current.candidates[0].model_dump()
    values = {k: v for k, v in observation.records[0].items() if k != "name_query"}
    assert sync.record_initial(current.candidates[0], values, observation.sources[0])["status"] == "unchanged"
    assert current.candidates[0].model_dump() == before


def test_terminal_records_and_saved_old_json_remain_read_only(tmp_path):
    current = run()
    current.status = ExecutionStatus.PARTIAL
    legacy = current.model_dump(mode="json")
    for name in ("entity_id", "aliases", "identity_revision", "identity_source_ids", "identity_form", "identity_history"):
        legacy["candidates"][0].pop(name)
    legacy["co_scientist"].pop("entity_references")
    path = tmp_path / "old_run.json"
    path.write_text(json.dumps(legacy))
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = ResearchRun.model_validate_json(path.read_text())
    sync = CandidateIdentitySync(loaded, loaded.co_scientist)
    before = loaded.model_dump()
    assert sync.apply({"candidate_link": sync.bind(request())}, request(), resolved())["status"] == "read_only"
    sync.refresh_references()
    assert loaded.model_dump() == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_form_qualifiers_are_not_silently_expanded_and_bad_observation_is_rejected():
    assert declared_aliases("Synthetic lactate (SL)") == ["Synthetic lactate", "SL"]
    for label in ("Synthetic lactate (sodium)", "Synthetic lactate (R)", "Synthetic lactate (HCL)"):
        assert declared_aliases(label) == []
    current = run()
    sync = CandidateIdentitySync(current)
    invalid = resolved()
    invalid.records[0]["inchikey"] = "MISMATCH"
    outcome = sync.apply({"candidate_link": sync.bind(request())}, request(), invalid)
    assert outcome["status"] == "invalid_observation"
    assert current.candidates[0].smiles == "" and current.candidates[0].decision == "hold"


def test_identity_merge_preserves_verified_aliases_provenance_and_old_entity_ids():
    first, second = run([candidate("Synthetic lactate")]), run([candidate("Synthetic second alias", "old_second")])
    observation = resolved()
    values = {k: v for k, v in observation.records[0].items() if k != "name_query"}
    for current in (first, second):
        entity = current.candidates[0]
        CandidateIdentitySync(current).record_initial(entity, values, observation.sources[0])
    destination, incoming = first.candidates[0], second.candidates[0]
    assert merge_identity_annotations(destination, incoming)
    assert "Synthetic second alias" in destination.aliases and "old_second" in destination.original_ids
    assert destination.identity_source_ids == ["pubchem:12345"]
    assert destination.decision == "hold" and destination.role == "symptom_management"
    before = destination.model_dump()
    assert merge_identity_annotations(destination, incoming) and destination.model_dump() == before
    incoming.inchikey = "CONFLICT"
    assert not merge_identity_annotations(destination, incoming) and destination.model_dump() == before


def test_stable_entity_binding_survives_two_exact_aliases_resolving_to_same_chemical_id():
    current = run([candidate("Synthetic lactate", "first_entity"),
                   candidate("Synthetic second alias", "second_entity")])
    sync = CandidateIdentitySync(current)
    for query in ("Synthetic lactate", "Synthetic second alias"):
        req = request(query)
        assert sync.apply({"candidate_link": sync.bind(req)}, req, resolved(query))["status"] == "applied"
    assert current.candidates[0].candidate_id == current.candidates[1].candidate_id
    req = request("Synthetic second alias")
    before_first = current.candidates[0].model_dump()
    assert sync.apply({"candidate_link": sync.bind(req)}, req, resolved(req.arguments["name"]))["status"] == "unchanged"
    assert current.candidates[0].model_dump() == before_first


def with_reviewed_candidate_inputs(current):
    entity = current.candidates[0]
    current.sources = [SourceRecord(source_id="synthetic:study", provider="offline_fixture",
        title="SYNTHETIC accepted study", url="https://example.invalid/study", retrieved_at="fixture")]
    current.evidence = [EvidenceRecord(evidence_id="synthetic:ev", source_id="synthetic:study",
        claim="SYNTHETIC supported assay endpoint", quote="SYNTHETIC continuous quote",
        location="SYNTHETIC paragraph", polarity="supports", validated_span=True)]
    entity.evidence_ids = entity.role_evidence_ids = ["synthetic:ev"]
    entity.gate = {"gate_status": "success", "gate_class": "A1"}
    entity.admet = {"status": "success", "selection_pass": True}
    return entity


def test_late_identity_cannot_upgrade_priority_in_finalize_or_result_document():
    current = run()
    entity = with_reviewed_candidate_inputs(current)
    sync = CandidateIdentitySync(current, current.co_scientist)
    clinical = entity.model_dump(include={"role", "role_status", "gate", "admet"})
    assert sync.apply({"origin": "model_gap", "candidate_link": sync.bind(request())}, request(), resolved())["status"] == "applied"
    assert entity.identity_assessment_pending and entity.identity_priority_before_update == "hold"
    decide_candidates(current.candidates, current.evidence)  # Co finalization calls this.
    assert entity.decision == "hold"
    assert entity.model_dump(include=set(clinical)) == clinical
    current.status = ExecutionStatus.PARTIAL
    before = current.model_dump()
    presented = result_document(current)  # API, reports, table export reuse this decision path.
    assert presented["candidates"][0]["decision"] == "hold"
    assert presented["candidates"][0]["identity_assessment_pending"] is True
    assert "identity_assessment_fingerprint" not in presented["candidates"][0]
    assert presented["candidates"][0]["role"] == "symptom_management"
    assert presented["candidates"][0]["gate"] == clinical["gate"]
    assert current.model_dump() == before


def test_unreviewed_tool_evidence_cannot_release_identity_priority_guard():
    current = run()
    entity = with_reviewed_candidate_inputs(current)
    sync = CandidateIdentitySync(current)
    sync.apply({"candidate_link": sync.bind(request())}, request(), resolved())
    raw = current.evidence[0].model_copy(update={"evidence_id": "synthetic:raw", "validated_span": False})
    current.evidence.append(raw)
    entity.evidence_ids.append(raw.evidence_id)
    decide_candidates(current.candidates, current.evidence)
    assert entity.decision == "hold" and entity.identity_assessment_pending
    entity.gate["retrieved_at"] = "a new metadata timestamp"
    decide_candidates(current.candidates, current.evidence)
    assert entity.decision == "hold" and entity.identity_assessment_pending
    # A distinct accepted independent assessment is permitted to use existing rules.
    current.evidence[-1].validated_span = True
    decide_candidates(current.candidates, current.evidence)
    assert entity.decision == "prioritize" and not entity.identity_assessment_pending


def test_initial_identity_flow_allows_normal_scientific_decision_and_conflict_downgrades():
    current = run([candidate("Synthetic lactate")])
    entity = with_reviewed_candidate_inputs(current)
    sync = CandidateIdentitySync(current)
    observation = resolved()
    values = {k: v for k, v in observation.records[0].items() if k != "name_query"}
    sync.record_initial(entity, values, observation.sources[0])
    assert not entity.identity_assessment_pending
    decide_candidates(current.candidates, current.evidence)
    assert entity.decision == "prioritize"
    outcome = sync.apply({"candidate_link": sync.bind(request())}, request(),
                         resolved(smiles="CCO", cid="99999", call="tool_004"))
    assert outcome["status"] == "conflict"
    decide_candidates(current.candidates, current.evidence)
    assert entity.decision == "hold" and entity.identity_status == "needs_review"


def test_explicit_fresh_scientific_reassessment_can_release_guard_with_same_evidence():
    current = run()
    entity = with_reviewed_candidate_inputs(current)
    sync = CandidateIdentitySync(current)
    sync.apply({"candidate_link": sync.bind(request())}, request(), resolved())
    decide_candidates(current.candidates, current.evidence)
    assert entity.decision == "hold" and entity.identity_assessment_pending
    decide_candidates(current.candidates, current.evidence, scientific_reassessment=True)
    assert entity.decision == "prioritize" and not entity.identity_assessment_pending


def test_actual_coordinator_finalization_keeps_late_identity_priority_after_budget_stop(tmp_path, monkeypatch):
    from test_coscientist import CoSources

    from scientist.budgets import BudgetExceeded
    from scientist.coscientist import CoScientistEngine
    from scientist.models import ScientistSettings
    from scientist.research import ResearchEngine
    from scientist.store import ProjectStore

    def initial(engine):
        engine.run.candidates = [candidate()]
        with_reviewed_candidate_inputs(engine.run)
        engine.state.hypotheses = run().co_scientist.hypotheses

        def offline_result(req):
            query = req.arguments["name"]
            if query == "Synthetic lactate (SL)":
                return ToolResult(tool_name="pubchem", provider="pubchem", parameters=req.arguments,
                    status="empty", retrieved_at="fixture", identity={"status": "empty", "query": query})
            return resolved(query)

        # Exercise the production tool executor, plan callbacks, ledger, durable
        # finalization and result view; replace only the provider response.
        engine.registry.execute = offline_result
        engine.scheduler.run(engine.scheduler.create(request("Synthetic lactate (SL)"), origin="supervisor_identity"))
        engine.scheduler.run(engine.scheduler.create(request(), origin="model_gap", hypothesis_ids=["h_synthetic"]))
        raise BudgetExceeded("SYNTHETIC: critical review budget stop after identity lookup")

    monkeypatch.setattr(CoScientistEngine, "initial_research", initial)
    store = ProjectStore(tmp_path / "workspace")
    project = store.create_project("SYNTHETIC identity finalization")["project_id"]
    created = store.create_run(project, ResearchBrief(disease="SYNTHETIC disease", research_mode="co_scientist",
                              existing_candidates=["Synthetic lactate (SL)"]))
    engine = ResearchEngine(store, ScientistSettings(workspace=str(store.root), llm={"backend": "codex"}),
                            sources=CoSources())
    completed = engine.execute(project, created.run_id)
    entity = completed.candidates[0]
    assert completed.status == ExecutionStatus.PARTIAL
    assert completed.co_scientist.budget["tool_calls"] == 2
    assert entity.identity_status == "resolved" and entity.decision == "hold"
    assert entity.role == "symptom_management" and entity.gate["gate_class"] == "A1"
    assert all(h.origin == "user_supplied" and h.status == "proposed" for h in completed.co_scientist.hypotheses)
    assert completed.co_scientist.reviews == completed.co_scientist.contracts == []
    assert result_document(completed)["candidates"][0]["decision"] == "hold"
    assert store.load_run(project, created.run_id).candidates[0].decision == "hold"

