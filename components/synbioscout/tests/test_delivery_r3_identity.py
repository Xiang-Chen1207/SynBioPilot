"""SYNTHETIC R3 early local identity and reservation contracts, entirely offline."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from test_core_identity_preparation import background_row, setup_core
from test_research import source

from scientist import identity_prepare
from scientist.budgets import BudgetExceeded
from scientist.models import CandidateRecord, ExecutionStatus
from scientist.tools import ToolResult


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("R3 identity contract forbids actual model/process/network access")
    monkeypatch.setattr(requests.sessions.Session,"request",blocked)
    monkeypatch.setattr(requests.api,"request",blocked)
    monkeypatch.setattr(socket,"create_connection",blocked)
    monkeypatch.setattr(socket.socket,"connect",blocked)
    monkeypatch.setattr(socket.socket,"connect_ex",blocked)
    for name in ("run","Popen","call","check_call","check_output"): monkeypatch.setattr(subprocess,name,blocked)
    for name in ("system","popen"): monkeypatch.setattr(os,name,blocked)
    original = Path.open
    def guarded(path,*args,**kwargs):
        if "inference" in path.parts or path.name in {"auth.json","config.toml"}:
            raise AssertionError("R3 identity contract must not read inference/auth/config")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"open",guarded)


def early_engine(tmp_path,monkeypatch):
    engine,clock,calls,queries = setup_core(tmp_path,monkeypatch)
    engine.run.brief.task_intent = "candidate_assessment"
    engine.run.candidates = []
    engine.run.evidence = []
    engine.state.hypotheses = []
    engine.candidate_core_active = True
    engine.background_package = SimpleNamespace(receipt={"package_id":"SYNTHETIC_R3","version":"1",
        "manifest_sha256":"a"*64})
    return engine,clock,calls,queries


def prepare(engine,rows):
    return identity_prepare.prepare_core_identities(engine,rows,local_only=True)


def test_early_local_preparation_uses_real_verifier_and_current_receipts_without_network(tmp_path,monkeypatch):
    engine,clock,calls,queries = early_engine(tmp_path,monkeypatch)
    row = background_row()
    verify = identity_prepare.verified_background_identity
    starts=[]
    def timed(value):
        starts.append(clock.now)
        result=verify(value)
        clock.now += 11
        return result
    monkeypatch.setattr(identity_prepare,"verified_background_identity",timed)
    before=clock.now
    prepare(engine,[row])
    candidate=engine.run.candidates[0]
    assert starts and clock.now-before == 11
    assert candidate.identity_status == "resolved" and candidate.identity_revision == 1
    assert candidate.decision == "hold" and candidate.role == "unknown" and not candidate.evidence_ids
    assert not calls and not queries and engine.ledger.counts["http_attempts"] == engine.ledger.counts["tool_calls"] == 0
    assert identity_prepare.core_identity_ready(engine.run)
    observation=next(o for o in candidate.identity_history if o.get("call_id","").startswith("background_identity:"))
    assert observation["acquisition"] == "background_reuse" and observation["package_sha256"] == "a"*64
    assert observation["source_sha256"] == row["source_sha256"] and observation["source_retrieved_at"] == row["source"]["retrieved_at"]
    before_repeat=engine.run.model_dump_json()
    prepare(engine,[row])
    assert engine.run.model_dump_json() == before_repeat and len(starts) == 1


def test_actual_completed_identity_removes_only_its_60_seconds_and_keeps_observed_model_latency(tmp_path,monkeypatch):
    engine,clock,_,queries = early_engine(tmp_path,monkeypatch)
    engine.call_durations=[133.148,217.101]
    pending=engine.candidate_core_reserve("semantic_review")
    assert pending["seconds"] == pytest.approx(217.101+60+5)
    verify=identity_prepare.verified_background_identity
    def timed(row):
        result=verify(row)
        clock.now += 11
        return result
    monkeypatch.setattr(identity_prepare,"verified_background_identity",timed)
    prepare(engine,[background_row()])
    # Actual elapsed identity work is in the same ledger; it is not refunded.
    clock.now += 350.264
    remaining=engine.ledger.deadline-clock.now
    assert remaining == pytest.approx(489.736-11)
    ready=engine.candidate_core_reserve("semantic_review")
    assert ready["seconds"] == pytest.approx(217.101+5)
    assert ready["calls"] == pending["calls"] and ready["output_bytes"] == pending["output_bytes"]
    assert engine.latency_allowance() == 217.101 and not queries
    engine.ledger.preflight(0,required_seconds=217.101,reserve=ready,stage="SYNTHETIC semantic stage")
    with pytest.raises(BudgetExceeded,match="Wall-time"):
        engine.ledger.preflight(0,required_seconds=217.101,reserve=pending,stage="SYNTHETIC original redundant identity reserve")


@pytest.mark.parametrize("change",["different_run","changed_structure","missing_receipts","ambiguous_name","new_draft_name"])
def test_identity_readiness_cannot_come_from_manifest_or_stale_or_ambiguous_state(tmp_path,monkeypatch,change):
    engine,_,_,queries = early_engine(tmp_path,monkeypatch)
    prepare(engine,[background_row()])
    candidate=engine.run.candidates[0]
    names=None
    if change == "different_run": engine.run.run_id="r_synthetic_other_run"
    elif change == "changed_structure": candidate.smiles="CCO"
    elif change == "missing_receipts": candidate.identity_history=[]
    elif change == "ambiguous_name":
        engine.run.candidates.append(CandidateRecord(candidate_id="other",name="SL"))
        names=["SL"]
    else: names=["SL","SYNTHETIC additional molecule"]
    assert not identity_prepare.core_identity_ready(engine.run,candidate_names=names)
    reserve=engine.candidate_core_reserve("semantic_review",candidate_names=names)
    assert reserve["seconds"] == pytest.approx(engine.latency_allowance()+60+5)
    if change == "changed_structure":
        old_history=copy.deepcopy(candidate.identity_history)
        identity_prepare.prepare_core_identities(engine,[background_row()])
        assert candidate.identity_status != "resolved" and not queries
        assert candidate.identity_history[:len(old_history)] == old_history


def test_unknown_local_identity_does_not_mark_a_completed_attempt_or_block_bounded_fallback(tmp_path,monkeypatch):
    engine,clock,calls,queries = early_engine(tmp_path,monkeypatch)
    prepare(engine,[])
    candidate=engine.run.candidates[0]
    assert not identity_prepare.core_identity_ready(engine.run)
    assert not any(o.get("core_preparation") for o in candidate.identity_history)
    assert not queries and not calls
    identity_prepare.prepare_core_identities(engine,[])
    assert queries == ["Synthetic lactate"]
    assert engine.ledger.counts["tool_calls"] == engine.ledger.counts["http_attempts"] == 1
    assert candidate.identity_status == "resolved" and candidate.decision == "hold"
    assert engine.ledger.deadline-clock.now >= engine.critical_reserve()["seconds"]


def test_bounded_network_failure_keeps_shared_counters_and_protected_review_time(tmp_path,monkeypatch):
    engine,clock,calls,queries=early_engine(tmp_path,monkeypatch)
    engine.call_durations=[217.101]
    engine.ledger.deadline=clock.now+280
    prepare(engine,[])
    execute=engine.registry.execute
    def unavailable(request):
        execute(request)  # Transparent transport stub debits the actual shared ledger.
        return ToolResult(tool_name="pubchem",provider="pubchem",parameters=request.arguments,status="unavailable",
            retrieved_at="SYNTHETIC",identity={"status":"unavailable"})
    engine.registry.execute=unavailable
    identity_prepare.prepare_core_identities(engine,[])
    assert 1 <= len(queries) <= 4
    assert engine.ledger.counts["tool_calls"] == engine.ledger.counts["http_attempts"] == len(queries)
    assert not calls and engine.ledger.counts["llm_calls"] == 0
    assert not identity_prepare.core_identity_ready(engine.run)
    assert engine.ledger.deadline-clock.now >= engine.critical_reserve()["seconds"]


@pytest.mark.parametrize("bad",["checksum","conflicting_forms"])
def test_local_conflict_does_not_guess_or_release_identity_reserve(tmp_path,monkeypatch,bad):
    engine,_,_,queries = early_engine(tmp_path,monkeypatch)
    row=background_row()
    rows=[row]
    if bad == "checksum": row["source_sha256"]="f"*64
    else: rows.append(background_row(smiles="CCO",cid="99999"))
    prepare(engine,rows)
    assert not queries and not identity_prepare.core_identity_ready(engine.run)
    assert engine.run.candidates[0].identity_status != "resolved"
    identity_prepare.prepare_core_identities(engine,rows)
    assert not queries  # A detected local contradiction is not permission for alias fishing.
    assert engine.candidate_core_reserve("semantic_review")["seconds"] == pytest.approx(engine.latency_allowance()+65)


def test_local_verification_past_hard_deadline_cannot_release_review_budget(tmp_path,monkeypatch):
    engine,clock,_,queries = early_engine(tmp_path,monkeypatch)
    verify=identity_prepare.verified_background_identity
    def delayed(row):
        result=verify(row)
        clock.now=engine.ledger.deadline+.001
        return result
    monkeypatch.setattr(identity_prepare,"verified_background_identity",delayed)
    with pytest.raises(BudgetExceeded,match="Wall-time"):
        prepare(engine,[background_row()])
    assert not queries
    with pytest.raises(BudgetExceeded): engine.ledger.preflight(0,stage="SYNTHETIC after deadline")


@pytest.mark.parametrize("field",["text","url"])
def test_changed_current_identity_source_restores_pending_allowance(tmp_path,monkeypatch,field):
    engine,_,_,queries=early_engine(tmp_path,monkeypatch)
    prepare(engine,[background_row()])
    candidate=engine.run.candidates[0]
    record=next(s for s in engine.run.sources if s.source_id in candidate.identity_source_ids)
    setattr(record,field,getattr(record,field)+"SYNTHETIC_changed")
    assert not identity_prepare.core_identity_ready(engine.run)
    assert engine.candidate_core_reserve("semantic_review")["seconds"] == pytest.approx(engine.latency_allowance()+65)
    old_history=copy.deepcopy(candidate.identity_history)
    identity_prepare.prepare_core_identities(engine,[background_row()])
    assert candidate.identity_status != "resolved" and not queries
    assert candidate.identity_history[:len(old_history)] == old_history


def test_local_verification_exceeding_60_second_share_is_not_completed(tmp_path,monkeypatch):
    engine,clock,_,queries=early_engine(tmp_path,monkeypatch)
    verify=identity_prepare.verified_background_identity
    def delayed(row):
        result=verify(row)
        clock.now += 60.001
        return result
    monkeypatch.setattr(identity_prepare,"verified_background_identity",delayed)
    with pytest.raises(TimeoutError):
        prepare(engine,[background_row()])
    assert clock.now < engine.ledger.deadline and not queries
    assert not identity_prepare.core_identity_ready(engine.run)
    assert engine.run.candidates[0].identity_status != "resolved"


class SynthesisResponses:
    def __init__(self): self.inputs=[]
    def complete(self,system,user,schema):
        payload=json.loads(user)
        self.inputs.append((schema["title"],payload))
        if schema["title"] == "ReviewDraft":
            return {"reviews":[{"evidence_id":e["evidence_id"],"verdict":"supported_by_source","reason":"SYNTHETIC exact source only"} for e in payload["evidence"]],
                "target_links":[],"molecule_links":[],"candidate_roles":[]}
        return {"evidence":[{"key":"e1","claim":"SYNTHETIC Synthetic lactate source observation.",
            "source_id":"synthetic:paper","quote":"SYNTHETIC Synthetic lactate changed the recorded cellular marker.",
            "location":"SYNTHETIC paragraph","polarity":"mixed","study_system":"SYNTHETIC cells",
            "condition":"SYNTHETIC recorded assay","endpoint":"SYNTHETIC cellular marker","limitations":["SYNTHETIC only"]}],
            "targets":[],"candidates":[{"name":"SL","evidence_keys":["e1"],"target_relations":[],
                "rationale":"SYNTHETIC newly reviewed evidence draft","uncertainties":["SYNTHETIC new scope unknown"],
                "role":"unknown","role_reason":"SYNTHETIC role pending","role_evidence_keys":[]}],
            "assessment_proposals":[],"unresolved_questions":[]}


def test_real_synthesis_merge_keeps_exact_prepared_identity_but_new_science_and_no_raw_identity_body(tmp_path,monkeypatch):
    engine,_,_,queries=early_engine(tmp_path,monkeypatch)
    row=background_row()
    payload=json.loads(row["source"]["text"])
    payload["SYNTHETIC_identity_only"]="IDENTITY_BODY_NOT_CLINICAL"*100
    row["source"]["text"]=json.dumps(payload)
    row["source_sha256"]=hashlib.sha256(row["source"]["text"].encode()).hexdigest()
    prepare(engine,[row])
    prepared=engine.run.candidates[0].model_copy(deep=True)
    engine.run.sources.append(source("synthetic:paper","SYNTHETIC Synthetic lactate changed the recorded cellular marker."))
    responses=SynthesisResponses()
    engine.legacy._synthesize(engine.run,responses,engine.directory)
    candidate=next(c for c in engine.run.candidates if c.entity_id==prepared.entity_id)
    for key in ("candidate_id","entity_id","smiles","inchikey","pubchem_cid","identity_revision","identity_history","identity_source_ids"):
        assert getattr(candidate,key) == getattr(prepared,key)
    assert candidate.identity_status == "resolved" and candidate.evidence_ids
    assert candidate.rationale == "SYNTHETIC newly reviewed evidence draft" and candidate.role_status == "needs_review"
    assert "SYNTHETIC new scope unknown" in candidate.uncertainties
    assert candidate.user_name_declarations == prepared.user_name_declarations
    assert identity_prepare.core_identity_ready(engine.run,candidate_names=["SL"])
    assert "IDENTITY_BODY_NOT_CLINICAL" not in json.dumps(responses.inputs[0])
    before=engine.run.model_dump_json()
    identity_prepare.prepare_core_identities(engine,[row])
    assert engine.run.model_dump_json() == before and not queries


def test_merge_never_attaches_one_prepared_identity_to_multiple_alias_drafts(tmp_path,monkeypatch):
    engine,_,_,_=early_engine(tmp_path,monkeypatch)
    prepare(engine,[background_row()])
    prepared=copy.deepcopy(engine.run.candidates)
    drafts=[CandidateRecord(candidate_id="draft_a",name="SL"),CandidateRecord(candidate_id="draft_b",name="Synthetic lactate")]
    engine.run.candidates=drafts
    identity_prepare.reuse_prepared_candidates(engine.run,prepared)
    assert all(c.identity_status != "resolved" for c in drafts)
    assert not identity_prepare.core_identity_ready(engine.run,candidate_names=[c.name for c in drafts])


def test_omitted_specified_candidate_and_terminal_history_are_preserved(tmp_path,monkeypatch):
    engine,_,_,queries=early_engine(tmp_path,monkeypatch)
    prepare(engine,[background_row()])
    prepared=copy.deepcopy(engine.run.candidates)
    engine.run.candidates=[CandidateRecord(candidate_id="draft_unrelated",name="SYNTHETIC different compound")]
    identity_prepare.reuse_prepared_candidates(engine.run,prepared)
    assert any(c.entity_id==prepared[0].entity_id and c.identity_revision==1 for c in engine.run.candidates)
    assert engine.run.candidates[0].identity_status != "resolved"
    engine.run.status=ExecutionStatus.PARTIAL
    before=engine.run.model_dump_json()
    prepare(engine,[background_row()])
    identity_prepare.reuse_prepared_candidates(engine.run,prepared)
    assert engine.run.model_dump_json() == before and not queries


def test_normal_co_path_prepares_local_identity_before_extraction_and_binds_final_review(tmp_path,monkeypatch):
    import test_delivery_r2_chain as fixture
    clock=fixture.ThreadClock()
    fixture.install_chain_fixture(monkeypatch,clock,boundary_delay=.015)
    verify=identity_prepare.verified_background_identity
    verifications=[]
    def timed(row):
        result=verify(row)
        verifications.append(clock.monotonic())
        clock.advance(11)
        return result
    monkeypatch.setattr(identity_prepare,"verified_background_identity",timed)
    complete=fixture.AssessmentWorker.complete
    boundaries=[]
    def observe(worker,system,user,schema):
        co=worker.control.co
        if schema["title"] in {"SynthesisDraft","ReviewDraft","CandidateAssessmentReviewDraft"}:
            boundaries.append({"stage":schema["title"],"ready":identity_prepare.core_identity_ready(co.run),
                "revision":co.run.candidates[0].identity_revision if co.run.candidates else None,
                "entity":co.run.candidates[0].entity_id if co.run.candidates else None})
        return complete(worker,system,user,schema)
    monkeypatch.setattr(fixture.AssessmentWorker,"complete",observe)
    run,control,_,offline=fixture.run_chain(tmp_path,clock,public=False,delays={"AnchorDraft":133.148,
        "SynthesisDraft":217.101,"ReviewDraft":217.101,"CandidateAssessmentReviewDraft":90})
    assert [row["stage"] for row in boundaries] == ["SynthesisDraft","ReviewDraft","CandidateAssessmentReviewDraft"]
    assert all(row["ready"] and row["revision"] == 1 for row in boundaries)
    assert len({row["entity"] for row in boundaries}) == 1 and len(verifications) == 1
    assert fixture.valid_evaluations(run) and run.usage["candidate_assessment_status"] == "completed_review"
    assert len(control.calls) == 4 and not offline.online_queries
    assert run.usage["http_attempts"] == run.usage["tool_calls"] == 0
    assert clock.monotonic()-1000 == pytest.approx(133.148+217.101+217.101+90+.015+11)
    assert not run.co_scientist.hypotheses and not run.co_scientist.contracts

