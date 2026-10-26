"""SYNTHETIC shared-chain scheduling: real coordinator, transparent offline workers.

These checks concern admission/accounting and an honestly needs-evidence chain.
They are not research performance or efficacy measurements.
"""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests
from test_budget_scheduling import Clock, setup_engine
from test_coscientist import CoSources
from test_research import FakeLLM, default_draft

from scientist.budgets import BudgetExceeded, RunLedger
from scientist.coscientist import GenerationDraft, ReflectionDraft, latest_cards
from scientist.llm import serialized_request
from scientist.models import ResearchBrief, ResearchBudget, ScientistSettings
from scientist.research import ResearchEngine
from scientist.store import ProjectStore
from scientist.tool_public import public_budget, public_budget_failure


@pytest.fixture(autouse=True)
def offline_transport_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SYNTHETIC scheduling tests must not contact any provider")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr("scientist.llm.subprocess.run", forbidden)
    monkeypatch.setattr("scientist.llm.subprocess.Popen", forbidden)


def ledger_and_clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("scientist.budgets.time.monotonic", clock.monotonic)
    return RunLedger(ResearchBudget(max_llm_calls=16, timeout_seconds=840,
        max_input_tokens=300000, max_output_tokens=96000), clock.now + 840), clock


def test_asd_observed_review_time_gate_admits_expanded_time_without_spending_future_reserve(monkeypatch):
    # Replay only public timing/accounting values from the 2026-10-18 run.
    # This offline fixture checks scheduling with mocked model responses.
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    original = json.loads((root / "docs/nac_asd_iteration/PERFORMANCE_V2_API_SCENARIOS.json").read_text())["R2_disease_exploration"]
    expanded = json.loads((root / "docs/asd_time_budget_2026-10-18/ASD_ONLY_60_MIN_REQUEST.json").read_text())
    expected = copy.deepcopy(original)
    expected["brief"]["budget"]["timeout_seconds"] = 3600
    assert expanded == expected  # No new candidates, guidance, or non-time quota.

    clock = Clock()
    monkeypatch.setattr("scientist.budgets.time.monotonic", clock.monotonic)
    elapsed = 840 - 538.771
    future = {"seconds": 518.460, "calls": 3, "bytes": 155732, "output_bytes": 36000}
    request = {"required_calls": 1, "required_output_bytes": 12000,
               "required_seconds": 171.153, "reserve": future, "stage": "SYNTHETIC semantic review"}
    for payload, admitted in [(original, False), (expanded, True)]:
        limits = ResearchBrief.model_validate(payload["brief"]).budget
        ledger = RunLedger(limits, clock.now + limits.timeout_seconds - elapsed)
        ledger.counts.update(llm_calls=2, input_tokens_reserved=27345, output_tokens_reserved=9974)
        before = dict(ledger.counts)
        if admitted:
            details = ledger.reserve_request(27529, output_reservation_id="synthetic_review", **request)
            assert details["remaining_seconds"] == pytest.approx(3298.771)
            assert details["reserved_seconds"] == future["seconds"]
            assert ledger.counts["llm_calls"] == 3
            assert ledger.counts["input_tokens_reserved"] == 54874
            assert ledger.snapshot()["output_bytes_inflight"] == 12000
            ledger.release_output("synthetic_review")
        else:
            with pytest.raises(BudgetExceeded, match="Wall-time scheduling allowance"):
                ledger.reserve_request(27529, output_reservation_id="synthetic_review", **request)
            assert ledger.counts == before
            assert ledger.snapshot()["output_bytes_inflight"] == 0


def test_parallel_output_holds_admit_only_one_call_without_reusing_future_allowance(monkeypatch):
    ledger, _ = ledger_and_clock(monkeypatch)
    ledger.counts["output_tokens_reserved"] = 60000
    barrier = threading.Barrier(2)
    def reserve(identifier):
        barrier.wait(timeout=3)
        try:
            ledger.reserve_request(500, required_output_bytes=12000, output_reservation_id=identifier,
                reserve={"bytes": 1000, "calls": 2, "output_bytes": 24000, "seconds": 60})
            return identifier, None
        except BudgetExceeded as exc:
            return None, exc.details
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ["llm_synthetic_a", "llm_synthetic_b"]))
    admitted = [identifier for identifier, _ in results if identifier]
    blocked = [failure for _, failure in results if failure]
    assert len(admitted) == len(blocked) == 1
    assert ledger.counts["llm_calls"] == 1 and ledger.counts["input_tokens_reserved"] == 500
    assert ledger.snapshot()["output_bytes_inflight"] == 12000
    assert blocked[0]["inflight_output_bytes"] == 12000
    assert blocked[0]["reserved_output_bytes"] == 24000
    assert blocked[0]["blocked_before_provider"] is True
    assert public_budget(ledger.snapshot())["output_bytes_inflight"] == 12000
    assert public_budget_failure(blocked[0])["inflight_output_bytes"] == 12000
    output = json.dumps({"SYNTHETIC": "small bounded result"})
    ledger.output(output, reservation_id=admitted[0], call_allowance=12000, protected_output_bytes=24000)
    assert ledger.snapshot()["output_bytes_inflight"] == 0
    assert ledger.counts["output_tokens_reserved"] == 60000 + len(output.encode())


def test_oversized_output_preserves_other_inflight_and_future_reservations(monkeypatch):
    ledger, _ = ledger_and_clock(monkeypatch)
    ledger.counts["output_tokens_reserved"] = 60000
    for identifier in ("llm_a", "llm_b"):
        ledger.reserve_request(100, required_output_bytes=12000, output_reservation_id=identifier,
            reserve={"output_bytes": 12000})
    payload = json.dumps({"text": "中" * 4001}, ensure_ascii=False)
    try:
        with pytest.raises(BudgetExceeded) as failed:
            ledger.output(payload, reservation_id="llm_a", call_allowance=12000, protected_output_bytes=12000)
    finally:
        ledger.release_output("llm_a")
    assert ledger.counts["output_bytes_received"] == len(payload.encode())
    assert ledger.counts["output_tokens_reserved"] == 60000
    assert ledger.snapshot()["output_bytes_inflight"] == 12000
    assert failed.value.details["reserved_output_bytes"] == 12000
    assert failed.value.details["inflight_output_bytes"] == 12000
    ledger.release_output("llm_b")
    ledger.release_output("llm_b")
    assert ledger.snapshot()["output_bytes_inflight"] == 0


def test_failure_release_does_not_refund_input_or_model_attempt(monkeypatch):
    ledger, _ = ledger_and_clock(monkeypatch)
    ledger.reserve_request(1234, required_output_bytes=12000, output_reservation_id="llm_failed")
    before = dict(ledger.counts)
    try:
        raise TimeoutError("SYNTHETIC transparent provider timeout")
    except TimeoutError:
        ledger.release_output("llm_failed")
    ledger.release_output("llm_failed")
    assert ledger.counts == before
    assert ledger.snapshot()["output_bytes_inflight"] == 0
    assert ledger.counts["llm_calls"] == 1 and ledger.counts["input_tokens_reserved"] == 1234


@pytest.mark.parametrize("text", ["中文临界输入", "条件与终点必须保留。\n" * 27])
@pytest.mark.parametrize("difference", [-1, 0, 1])
def test_serialized_chinese_input_boundary_keeps_exact_future_bytes(monkeypatch, text, difference):
    ledger, _ = ledger_and_clock(monkeypatch)
    settings = ScientistSettings().llm
    schema = {"type": "object", "properties": {"适用条件": {"type": "string"}}, "required": ["适用条件"]}
    amount = len(serialized_request(settings, "SYNTHETIC", text, schema).encode())
    future = len(serialized_request(settings, "SYNTHETIC closure", text + "反证", schema).encode())
    ledger.counts["input_tokens_reserved"] = 300000 - amount - future - difference
    before = dict(ledger.counts)
    dispatched = []
    try:
        ledger.reserve_request(amount, required_output_bytes=12000, output_reservation_id="llm_boundary",
            reserve={"bytes": future, "calls": 1, "output_bytes": 12000})
        dispatched.append(True)
    except BudgetExceeded as exc:
        assert difference == -1
        assert exc.details["required_bytes"] == amount and exc.details["remaining_bytes"] == amount + future - 1
        assert exc.details["reserved_bytes"] == future and exc.details["blocked_before_provider"] is True
        assert ledger.counts == before and ledger.snapshot()["output_bytes_inflight"] == 0
    assert bool(dispatched) is (difference >= 0)
    ledger.release_output("llm_boundary")


def test_deadline_boundary_and_late_return_release_without_committing_output(monkeypatch):
    ledger, clock = ledger_and_clock(monkeypatch)
    ledger.deadline = clock.now + 90
    ledger.reserve_request(100, required_output_bytes=12000, required_seconds=30,
        reserve={"seconds": 60}, output_reservation_id="llm_boundary")
    ledger.release_output("llm_boundary")
    clock.now += .001
    before = dict(ledger.counts)
    with pytest.raises(BudgetExceeded, match="Wall-time"):
        ledger.reserve_request(100, required_output_bytes=12000, required_seconds=30,
            reserve={"seconds": 60}, output_reservation_id="llm_blocked")
    assert ledger.counts == before
    ledger.reserve_request(100, required_output_bytes=12000, output_reservation_id="llm_late")
    clock.now = ledger.deadline
    payload = '{"SYNTHETIC":"returned at deadline"}'
    try:
        with pytest.raises(BudgetExceeded):
            ledger.output(payload, reservation_id="llm_late", call_allowance=12000)
    finally:
        ledger.release_output("llm_late")
    assert ledger.counts["output_bytes_received"] == len(payload.encode())
    assert ledger.counts["output_tokens_reserved"] == 0 and ledger.snapshot()["output_bytes_inflight"] == 0


@pytest.mark.parametrize("failure", ["timeout", "oversized", "factory"])
def test_required_invoke_preserves_closure_and_releases_hold_before_applying_failure(tmp_path, monkeypatch, failure):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    evidence_before = [e.model_dump() for e in engine.run.evidence]
    hypotheses_before = [h.model_dump() for h in engine.state.hypotheses]
    workers = []
    returned = {"text": "中" * 5000}
    def factory(*args, **kwargs):
        if failure == "factory":
            raise OSError("SYNTHETIC worker construction failed")
        worker = SimpleNamespace(calls=0, cache_hits=0)
        def complete(*args):
            worker.calls += 1
            assert worker.deadline <= engine.ledger.deadline - 125
            if failure == "timeout":
                raise TimeoutError("SYNTHETIC required review timeout")
            return returned
        worker.complete = complete
        workers.append(worker)
        return worker
    engine.legacy.llm_factory = factory
    with pytest.raises((BudgetExceeded, TimeoutError, OSError)):
        engine.invoke("reflection", "SYNTHETIC required review", "system", "{}", {"type": "object"},
            reserve={"bytes": 1000, "calls": 1, "output_bytes": 12000, "seconds": 125}, output_cap=12000)
    assert engine.ledger.snapshot()["output_bytes_inflight"] == 0
    assert engine.ledger.counts["llm_calls"] == 1 and engine.ledger.counts["input_tokens_reserved"] > 0
    assert engine.ledger.counts["output_tokens_reserved"] == 0
    assert engine.ledger.counts["output_bytes_received"] == (len(json.dumps(returned, ensure_ascii=False).encode()) if failure == "oversized" else 0)
    assert [e.model_dump() for e in engine.run.evidence] == evidence_before
    assert [h.model_dump() for h in engine.state.hypotheses] == hypotheses_before
    assert not engine.state.contracts


def test_generation_with_preconsumed_budget_dispatches_only_one_view_before_review_closure(tmp_path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    engine.state.hypotheses = []
    engine.run.brief.budget.max_hypotheses = 2
    engine.registry = SimpleNamespace(catalog=list)
    control = SimpleNamespace(requests=[])
    engine.legacy.llm_factory = lambda settings, directory, max_calls: ChainWorker(settings, directory, max_calls, control)
    payloads = engine.generation_payloads()
    assert len(payloads) == 2
    one = engine.request_bytes("generation", GenerationDraft, payloads[0]) + engine.minimum_chain_reserve("generation", generation_outputs=1)["bytes"]
    two = sum(engine.request_bytes("generation", GenerationDraft, p) for p in payloads) + engine.minimum_chain_reserve("generation", generation_outputs=2)["bytes"]
    assert one < two and one < 300000
    remaining = min(two - 1, one + 1000)
    engine.ledger.counts["input_tokens_reserved"] = 300000 - remaining
    before = engine.ledger.counts["input_tokens_reserved"]
    engine.generate()
    assert [name for name, _ in control.requests] == ["GenerationDraft"]
    assert len(engine.state.hypotheses) == 1
    assert engine.state.optional_skips and engine.actual_calls == 1
    assert before < engine.ledger.counts["input_tokens_reserved"] <= 300000
    assert engine.ledger.snapshot()["output_bytes_inflight"] == 0


def test_required_formal_review_cannot_spend_the_following_meta_allowance(tmp_path, monkeypatch):
    engine, _, calls = setup_engine(tmp_path, monkeypatch)
    cards = latest_cards(engine.state)
    amount = engine.request_bytes("reflection", ReflectionDraft, engine.review_payload(cards))
    reserve = engine.minimum_chain_reserve("reflection")
    assert reserve["bytes"] > 0
    engine.ledger.counts["input_tokens_reserved"] = 300000 - amount - reserve["bytes"] + 1
    before = copy.deepcopy(engine.state.hypotheses)
    with pytest.raises(BudgetExceeded):
        engine.review(cards, "SYNTHETIC required review with insufficient closure")
    assert not calls and not engine.state.reviews and not engine.state.contracts
    assert engine.state.hypotheses == before
    assert engine.state.budget_failures[-1]["blocked_before_provider"] is True
    assert engine.ledger.snapshot()["output_bytes_inflight"] == 0


class ChainWorker:
    """Plain recorded outputs; it never marks scientific support as established."""
    def __init__(self, settings, directory, max_calls, control):
        self.settings, self.directory, self.max_calls, self.control = settings, directory, max_calls, control
        self.calls = self.cache_hits = 0

    def complete(self, system, user, schema):
        self.calls += 1
        payload, title = json.loads(user), schema["title"]
        self.control.requests.append((title, payload))
        if getattr(self.control, "clock", None) is not None:
            self.control.clock.now += self.control.delays.get(title, 0)
            if title == self.control.late_stage:
                self.control.clock.now = self.deadline
        if title == getattr(self.control, "timeout_stage", ""):
            raise TimeoutError("SYNTHETIC required stage timed out")
        if title in {"SynthesisDraft", "ReviewDraft"}:
            draft = default_draft()
            draft["targets"] = []
            draft["evidence"] = draft["evidence"][:1]
            return FakeLLM(self.settings, self.directory, self.max_calls, draft=draft, verdicts={}).complete(system, user, schema)
        if title == "AnchorDraft":
            return {"disease_english": "SYNTHETIC disease", "searches": ["SYNTHETIC disease intervention", "SYNTHETIC disease contrary"],
                "questions": ["SYNTHETIC what remains untested?"], "success_criterion": "SYNTHETIC endpoint measurement"}
        if title == "GenerationDraft":
            cited = [e["evidence_id"] for e in payload.get("accepted_evidence", [])]
            return {"hypotheses": [{"hypothesis_id": "temporary", "title": "SYNTHETIC single narrow question",
                "claim": "The recorded cellular endpoint needs independent confirmation.", "molecule_names": ["Illustrative compound"],
                "target_symbols": [], "mechanism": "SYNTHETIC unconfirmed mechanism", "chassis_name": payload["anchor"]["chassis_name"],
                "condition": "synthetic condition", "endpoint": "synthetic endpoint", "evidence_ids": cited,
                "inference_gaps": ["SYNTHETIC no clinical or strain-specific validation",
                    "SYNTHETIC_GENERATION_GROWTH " + "detail " * getattr(self.control, "generation_growth", 0)],
                "prediction": "SYNTHETIC independent measurements may disagree.",
                "validation_question": "SYNTHETIC repeat the narrow assay under recorded conditions."}], "tool_requests": []}
        if title == "ReflectionDraft":
            cards = payload["hypotheses"]
            return {"reviews": [{"hypothesis_id": h["hypothesis_id"], "version": h["version"], "verdict": "revise",
                "reason": "SYNTHETIC_REVIEW_GROWTH " + "detail " * getattr(self.control, "review_growth", 0),
                "valid_evidence_ids": h.get("evidence_ids", []),
                "required_evidence": ["SYNTHETIC independent measurement"]} for h in cards],
                "claim_reviews": [{"claim_id": c["claim_id"], "hypothesis_id": c["hypothesis_id"], "version": c["version"],
                    "verdict": "unknown", "evidence_ids": [], "reason": "SYNTHETIC not established", "gaps": []}
                    for c in (payload.get("literature_landscape") or {}).get("claims", [])],
                "new_evidence": [], "prior_work": []}
        if title == "EvolutionDraft":
            return {"revisions": []}
        if title == "MetaDraft":
            return {"contracts": [{"hypothesis_id": h["hypothesis_id"], "version": h["version"], "status": "needs_evidence",
                "question": "SYNTHETIC next independent assay?", "judgment": "SYNTHETIC incomplete support",
                "evidence_ids": h.get("evidence_ids", []), "minimal_validation": "SYNTHETIC repeat with controls",
                "remaining_unknowns": ["SYNTHETIC no efficacy or production conclusion"]} for h in payload["hypotheses"]],
                "common_gaps": ["SYNTHETIC offline only"]}
        raise AssertionError("Unexpected offline schema " + title)


def run_normal_chain(tmp_path, *, max_input=300000, max_hypotheses=1, assumption="", clock=None,
                     delays=None, timeout_stage="", late_stage="", generation_growth=0, review_growth=0):
    store = ProjectStore(tmp_path / "SYNTHETIC_chain")
    pid = store.create_project("SYNTHETIC minimal required chain")["project_id"]
    brief = ResearchBrief(disease="SYNTHETIC disease", research_mode="co_scientist", existing_candidates=["Illustrative compound"],
        assumptions=[assumption] if assumption else [], budget=ResearchBudget(max_llm_calls=16, timeout_seconds=840,
            max_input_tokens=max_input, max_output_tokens=96000, max_searches=5, max_papers=12, max_tool_calls=12,
            max_http_requests=28, max_targets=0, max_hypotheses=max_hypotheses, max_md_jobs_total=0, literature_max_candidates=2))
    run = store.create_run(pid, brief)
    control = SimpleNamespace(requests=[], clock=clock, delays=delays or {}, timeout_stage=timeout_stage,
        late_stage=late_stage, generation_growth=generation_growth, review_growth=review_growth)
    factory = lambda settings, directory, max_calls: ChainWorker(settings, directory, max_calls, control)
    engine = ResearchEngine(store, ScientistSettings(workspace=str(store.root)), sources=CoSources(), llm_factory=factory)
    return engine.execute(pid, run.run_id, prepare_targets=False), control


def test_one_card_normal_chain_calls_one_generation_formal_review_and_real_stub_meta(tmp_path):
    run, control = run_normal_chain(tmp_path)
    titles = [title for title, _ in control.requests]
    assert run.status.value == "completed", run.errors
    assert titles.count("GenerationDraft") == 1
    assert titles.count("ReflectionDraft") == titles.count("MetaDraft") == 1
    assert len(latest_cards(run.co_scientist)) == len(run.co_scientist.reviews) == len(run.co_scientist.contracts) == 1
    assert run.co_scientist.contracts[0].status == "needs_evidence"
    assert run.co_scientist.optional_skips
    assert run.usage["input_tokens_reserved"] <= 300000 and run.usage["output_tokens_reserved"] <= 96000
    assert run.usage["output_bytes_inflight"] == 0 and run.usage["llm_calls"] <= 16
    assert not any(c.state == "supported" for c in run.co_scientist.research_flow.claims)


def test_unaffordable_normal_chain_stops_without_a_static_or_unreviewed_contract(tmp_path):
    run, control = run_normal_chain(tmp_path, max_input=30000)
    assert run.status.value == "partial" and run.sources
    assert not run.co_scientist.contracts
    assert not any(title == "MetaDraft" for title, _ in control.requests)
    assert run.usage["input_tokens_reserved"] <= 30000
    assert run.usage["output_bytes_inflight"] == 0
    assert any(row.get("blocked_before_provider") for row in run.co_scientist.budget_failures)


def test_two_card_upper_limit_automatically_falls_back_before_second_generation_in_normal_chain(tmp_path):
    # Long supplied text is fully sent once to Anchor; later task windows are
    # compact. Its actual UTF-8 serialization consumes the fixed shared budget.
    background = "SYNTHETIC supplied background\n" + "资料说明\n" * 7000
    run, control = run_normal_chain(tmp_path, max_hypotheses=2, assumption=background)
    titles = [title for title, _ in control.requests]
    assert run.status.value == "completed", run.errors
    assert titles.count("GenerationDraft") == titles.count("ReflectionDraft") == titles.count("MetaDraft") == 1
    assert len(run.co_scientist.hypotheses) == len(run.co_scientist.reviews) == len(run.co_scientist.contracts) == 1
    assert run.brief.budget.max_hypotheses == 2
    assert (run.brief.budget.max_llm_calls, run.brief.budget.timeout_seconds,
            run.brief.budget.max_input_tokens, run.brief.budget.max_output_tokens) == (16, 840, 300000, 96000)
    blocked = next(row for row in run.co_scientist.optional_skips if row.get("stage") == "second_generation_view_plan")
    assert blocked["blocked_before_provider"] is True
    assert blocked["required_bytes"] + blocked["reserved_bytes"] > blocked["remaining_bytes"]
    assert run.usage["output_bytes_inflight"] == 0 and run.co_scientist.contracts[0].status == "needs_evidence"


def test_long_legal_generation_and_review_outputs_are_remeasured_before_meta(tmp_path):
    run, control = run_normal_chain(tmp_path, generation_growth=900, review_growth=900)
    titles = [title for title, _ in control.requests]
    assert run.status.value == "completed", run.errors
    assert titles.count("GenerationDraft") == titles.count("ReflectionDraft") == titles.count("MetaDraft") == 1
    assert len(run.co_scientist.reviews[0].reason) > 6000
    assert any(len(gap) > 6000 for gap in run.co_scientist.hypotheses[0].inference_gaps)
    assert run.usage["input_tokens_reserved"] <= 300000 and run.usage["output_tokens_reserved"] <= 96000
    assert run.usage["output_bytes_inflight"] == 0
    assert all(c.status == "needs_evidence" for c in run.co_scientist.contracts)


@pytest.mark.parametrize("scenario", ["slow_generation", "review_timeout", "late_meta"])
def test_time_failures_keep_only_completed_scientific_artifacts_and_release_holds(tmp_path, monkeypatch, scenario):
    clock = Clock()
    monkeypatch.setattr("scientist.budgets.time.monotonic", clock.monotonic)
    options = {"delays": {"GenerationDraft": 350}} if scenario == "slow_generation" else (
        {"timeout_stage": "ReflectionDraft"} if scenario == "review_timeout" else {"late_stage": "MetaDraft"})
    run, control = run_normal_chain(tmp_path, clock=clock, **options)
    titles = [title for title, _ in control.requests]
    assert run.status.value == "partial", run.errors
    assert run.evidence and run.co_scientist.hypotheses
    assert not run.co_scientist.contracts and run.usage["output_bytes_inflight"] == 0
    if scenario == "slow_generation":
        assert "ReflectionDraft" not in titles and "MetaDraft" not in titles
        assert any(row.get("blocked_before_provider") and row.get("remaining_seconds", 0) <
            row.get("required_seconds", 0) + row.get("reserved_seconds", 0) for row in run.co_scientist.budget_failures)
    elif scenario == "review_timeout":
        assert titles.count("ReflectionDraft") == 1 and "MetaDraft" not in titles
        assert run.co_scientist.reviews == []
        assert any(row.get("reason") == "provider_timeout" for row in run.co_scientist.budget_failures)
    else:
        assert titles.count("ReflectionDraft") == titles.count("MetaDraft") == 1
        assert len(run.co_scientist.reviews) == 1
        assert run.usage["output_bytes_received"] > run.usage["output_tokens_reserved"]

