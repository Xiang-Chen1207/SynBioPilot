"""Co-Scientist control-flow fixtures; none of these claims are biological results."""

import copy
import hashlib
import io
import json
import threading
import time
import zipfile
from types import SimpleNamespace

import pytest
from test_research import FakeLLM, FakeSources, default_draft

from scientist.budgets import BudgetExceeded, RunLedger
from scientist.co_models import (
    ChassisSnapshot,
    CoScientistState,
    HypothesisCard,
    ParameterNeed,
    ProblemAnchor,
)
from scientist.coscientist import CoScientistEngine, latest_cards
from scientist.exports import export_research
from scientist.http import HTTPClient
from scientist.llm import strict_schema
from scientist.models import ResearchBrief, ResearchBudget, ScientistSettings
from scientist.research import ResearchEngine
from scientist.store import ProjectStore


class CoSources(FakeSources):
    def __init__(self):
        super().__init__()
        self.http = SimpleNamespace(request_count=0, cache_hits=0)


class Worker:
    def __init__(self, settings, directory, max_calls, controller):
        self.settings, self.directory, self.max_calls = settings, directory, max_calls
        self.calls = self.cache_hits = 0
        self.controller = controller

    def complete(self, system, user, schema):
        self.calls += 1
        p = json.loads(user)
        name = schema["title"]
        with self.controller.lock:
            self.controller.requests.append((name, p, system))
        if name in ("SynthesisDraft", "ReviewDraft"):
            legacy = FakeLLM(
                self.settings, self.directory, self.max_calls, draft=default_draft(), verdicts={}
            )
            return legacy.complete(system, user, schema)
        if name == "AnchorDraft":
            return {
                "disease_english": "Fixture disease",
                "searches": ["fixture intervention", "fixture adverse"],
                "questions": ["What measurement is missing?"],
                "success_criterion": "Recorded cellular endpoint, not efficacy",
            }
        if name == "GenerationDraft":
            self.controller.barrier.wait(
                timeout=3
            )  # fails if workers are merely renamed serial calls
            view = p["view"]
            number = 1 if "机制" in view else 2
            return {
                "hypotheses": [
                    {
                        "hypothesis_id": "temporary",
                        "version": 1,
                        "parent_version": None,
                        "title": f"SYNTHETIC view {number}",
                        "claim": "The fixture endpoint merits measurement.",
                        "molecule_names": ["Illustrative compound"],
                        "target_symbols": ["FIXTURE1"],
                        "mechanism": f"fixture mechanism {number}",
                        "chassis_name": p["anchor"]["chassis_name"],
                        "condition": "synthetic condition",
                        "endpoint": "synthetic endpoint",
                        "evidence_ids": [p["accepted_evidence"][0]["evidence_id"]],
                        "opposing_evidence_ids": [],
                        "inference_gaps": ["No real production measurement"],
                        "prediction": "A fixture assay can distinguish models.",
                        "validation_question": "Measure the fixture endpoint under recorded conditions.",
                        "origin": "synthesis",
                        "status": "proposed",
                        "chassis": ChassisSnapshot().model_dump(),
                        "novelty": {
                            "status": "insufficient_search",
                            "closest_source_ids": [],
                            "difference": "",
                            "search_scope": "",
                        },
                        "changes": [],
                    }
                ],
                "tool_requests": [],
            }
        if name == "ReflectionDraft":
            result = {
                "reviews": [
                    {
                        "hypothesis_id": h["hypothesis_id"],
                        "version": h["version"],
                        "verdict": "retain",
                        "reason": "Synthetic review; no experimental validation.",
                        "objections": ["Production condition is unknown"],
                        "required_evidence": ["Need real measurement"],
                        "valid_evidence_ids": h["evidence_ids"],
                    }
                    for h in p["hypotheses"]
                ],
                "claim_reviews": [{
                    "claim_id": c["claim_id"], "hypothesis_id": c["hypothesis_id"], "version": c["version"],
                    "verdict": "supported" if c["kind"] == "main" else "unknown",
                    "evidence_ids": next(h["evidence_ids"] for h in p["hypotheses"] if h["hypothesis_id"] == c["hypothesis_id"]) if c["kind"] == "main" else [],
                    "reason": "SYNTHETIC per-claim review; no biological conclusion", "gaps": [],
                } for c in (p.get("literature_landscape") or {}).get("claims", [])],
                "new_evidence": [], "prior_work": [],
            }
            # The same transparent worker can exercise the saved stage baseline
            # without returning fields absent from that version's schema.
            return {key: value for key, value in result.items() if key in schema["properties"]}
        if name == "PairwiseReview":
            a, b = p["hypotheses"]
            return {
                "comparison_id": p["comparison_id"],
                "left_id": a["hypothesis_id"],
                "left_version": a["version"],
                "right_id": b["hypothesis_id"],
                "right_version": b["version"],
                "outcome": "tie",
                "reason": "Synthetic equal information",
                "criterion": p["criterion"],
                "evidence_ids": a["evidence_ids"],
            }
        if name == "EvolutionDraft":
            items = copy.deepcopy(p["hypotheses"])
            for h in items:
                h["claim"] = "Revised fixture scope: production is unknown and must be measured."
                h["version"] += 1
                h["parent_version"] = 1
                h["changes"] = ["Narrowed claim after explicit missing-production objection"]
            return {"revisions": items}
        if name == "MetaDraft":
            return {
                "contracts": [
                    {
                        "hypothesis_id": h["hypothesis_id"],
                        "version": h["version"],
                        "status": "human_review_needed",
                        "question": h["validation_question"],
                        "judgment": "SYNTHETIC test only",
                        "evidence_ids": h["evidence_ids"],
                        "opposing_evidence_ids": [],
                        "minimal_validation": "Record a valid assay.",
                        "resource_requirements": ["fixture assay"],
                        "remaining_unknowns": ["No real data"],
                        "parameter_needs": [
                            {
                                "variable": "measured_concentration",
                                "unit": "mg/L",
                                "why_needed": "Supply is unknown",
                                "value": None,
                                "data_class": "measurement_required",
                                "source_ids": [],
                            }
                        ],
                    }
                    for h in p["hypotheses"][:3]
                ],
                "common_gaps": ["No real biological conclusions from these fixtures"],
            }
        raise AssertionError(name)


class Controller:
    def __init__(self):
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(2)
        self.requests = []

    def factory(self, settings, directory, max_calls):
        return Worker(settings, directory, max_calls, self)


def execute(tmp_path, *, max_calls=16, disabled=False):
    store = ProjectStore(tmp_path / "workspace")
    pid = store.create_project("SYNTHETIC Co test")["project_id"]
    brief = ResearchBrief(
        disease="Fixture disease",
        research_mode="co_scientist",
        existing_candidates=["Illustrative compound"],
        budget=ResearchBudget(
            max_llm_calls=max_calls,
            max_hypotheses=4,
            max_papers=8,
            max_input_tokens=500000,
            max_output_tokens=100000,
        ),
    )
    run = store.create_run(pid, brief)
    control = Controller()
    settings = ScientistSettings(
        workspace=str(store.root), llm={"backend": "none" if disabled else "codex"}
    )
    engine = ResearchEngine(store, settings, sources=CoSources(), llm_factory=control.factory)
    result = engine.execute(pid, run.run_id, prepare_targets=True)
    return store, engine, result, control


def test_true_parallel_generation_and_versioned_research_contract(tmp_path):
    store, engine, run, control = execute(tmp_path)
    assert run.status.value == "completed", run.errors
    co = run.co_scientist
    assert len(co.agents) == 7
    assert [e.sequence for e in co.events] == list(range(1, len(co.events) + 1))
    generation_events = [e for e in co.events if e.agent_id == "generation" and e.kind == "llm"]
    assert [e.status for e in generation_events[:2]] == ["running", "running"]
    assert len(co.hypotheses) == 4 and len(latest_cards(co)) == 2
    assert {h.version for h in co.hypotheses} == {1, 2}
    assert all(h.origin == "user_supplied" for h in co.hypotheses)
    assert all(c.version == 2 and c.parameter_needs[0].value is None for c in co.contracts)
    assert len(co.comparisons) == 1 and co.comparisons[0].left_version == 1
    assert all(v["version"] == 2 for v in co.selected_versions)
    assert not run.target_packages  # even prepare_targets=True never enters GPU/structure chain
    assert all(h.chassis.status == "unknown" for h in co.hypotheses)
    assert all(
        "自我评分" not in str(p) for name, p, _ in control.requests if name == "ReflectionDraft"
    )
    assert all("private chain-of-thought" in system for _, _, system in control.requests)
    path = store.run_dir(run.project_id, run.run_id)
    for name in [
        "problem_anchor.json",
        "hypotheses.json",
        "events.json",
        "budget.json",
        "research_contract.md",
    ]:
        assert (path / name).is_file()
    before = (path / "run.json").read_bytes()
    engine.execute(run.project_id, run.run_id)
    assert (path / "run.json").read_bytes() == before  # cannot reset a terminal run's budget


def test_exhaustion_preserves_real_sources_and_does_not_invent_workers(tmp_path):
    _, _, run, _ = execute(tmp_path, max_calls=1)
    assert run.status.value == "partial"
    assert run.sources and not run.co_scientist.hypotheses and not run.co_scientist.contracts
    assert (
        next(a for a in run.co_scientist.agents if a.agent_id == "generation").status == "skipped"
    )
    assert run.co_scientist.budget["llm_calls"] == 1
    assert any("budget" in x.lower() for x in run.errors)


def test_disabled_model_keeps_retrieval_observations_not_supported_hypotheses(tmp_path):
    _, _, run, _ = execute(tmp_path, disabled=True)
    assert run.status.value == "partial" and run.sources
    assert not run.evidence and not run.co_scientist.contracts
    assert all(a.invocations == 0 for a in run.co_scientist.agents)


def test_conservative_chassis_gate_rejects_annotation_as_production(tmp_path):
    _, legacy, run, _ = execute(tmp_path)
    engine = CoScientistEngine(legacy)
    engine.run, engine.state = run, run.co_scientist
    h = run.co_scientist.hypotheses[0].model_copy(deep=True)
    h.chassis = ChassisSnapshot(
        status="direct_evidence",
        strain_match="exact",
        mode="native_synthesis",
        evidence_ids=h.evidence_ids,
        gaps=[],
    )
    h.chassis_name = "Different strain"
    h.evidence_ids += ["invented_evidence"]
    h.novelty.status = "potential_gap"
    h.novelty.closest_source_ids = ["invented_source"]
    result = engine.normalize_card(h)
    assert result.chassis_name == run.brief.chassis_name
    assert result.chassis.status == "unknown"
    assert "invented_evidence" not in result.evidence_ids
    assert result.novelty.status == "insufficient_search"


def test_numeric_pkpd_parameter_is_schema_forbidden():
    with pytest.raises(ValueError):
        ParameterNeed(variable="clearance", unit="1/h", why_needed="unmeasured", value=0.1)


def test_parallel_shared_call_budget_cannot_be_doubled():
    ledger = RunLedger(ResearchBudget(max_llm_calls=1), time.monotonic() + 5)
    results = []

    def charge():
        try:
            ledger.llm("system", "user", "schema")
            results.append("ok")
        except BudgetExceeded:
            results.append("budget")

    threads = [threading.Thread(target=charge) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["budget", "ok"]


def test_http_retries_and_cache_share_same_ledger(tmp_path, monkeypatch):
    ledger = RunLedger(ResearchBudget(max_http_requests=1), time.monotonic() + 5)
    http = HTTPClient(tmp_path / "cache", retries=2)
    http.before_request, http.on_cache_hit = ledger.request, ledger.cache
    response = SimpleNamespace(status_code=503)
    monkeypatch.setattr(http.session, "request", lambda *a, **kw: response)
    monkeypatch.setattr("scientist.http.time.sleep", lambda _: None)
    with pytest.raises(BudgetExceeded):
        http.get_json("https://example.org/fixture")
    assert http.request_count == ledger.counts["http_attempts"] == 1
    content = b'{"synthetic": true}'
    key = hashlib.sha256(
        json.dumps(["GET", "https://example.org/cached", None, None], sort_keys=True).encode()
    ).hexdigest()
    (http.cache_dir / (key + ".response")).write_bytes(content)
    assert http.get_json("https://example.org/cached") == {"synthetic": True}
    assert http.cache_hits == ledger.counts["cache_hits"] == 1


def test_co_exports_include_trace_contracts_but_no_nested_paper_text(tmp_path):
    store, _, run, _ = execute(tmp_path)
    payload, _, _ = export_research(store, run, "zip")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in [
            "hypotheses.json",
            "reviews.json",
            "events.json",
            "co_scientist.json",
            "research_contract.md",
        ]:
            assert name in archive.namelist()
        co = json.loads(archive.read("co_scientist.json"))
        for t in co["tool_calls"]:
            for s in t["sources"] + t["records"]:
                if "source_id" in s:
                    assert s["text"] == ""
        hashes = json.loads(archive.read("manifest.json"))["file_hashes"]
        assert all(hashlib.sha256(archive.read(n)).hexdigest() == h for n, h in hashes.items())
    assert any(t["sources"] and t["sources"][0]["text"] for t in run.co_scientist.tool_calls)


def test_historical_default_has_no_fabricated_agents():
    assert ResearchBrief(disease="fixture").research_mode == "standard"
    assert not CoScientistState().events
    assert HypothesisCard.model_fields["parent_version"].default is None
    assert ProblemAnchor(
        disease="d", phenotype="", species="s", chassis_name="c"
    ).success_criterion.startswith("待")


def test_strict_schema_preserves_title_named_scientific_field():
    from scientist.coscientist import GenerationDraft

    schema = strict_schema(GenerationDraft.model_json_schema())
    assert "title" in schema["$defs"]["HypothesisCard"]["properties"]

    def inspect(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for value in node.values():
                inspect(value)
        elif isinstance(node, list):
            for value in node:
                inspect(value)

    inspect(schema)


def test_saved_hypothesis_continuation_rechecks_without_retrieval_or_generation(tmp_path):
    store, _, parent, _ = execute(tmp_path)
    original = (store.run_dir(parent.project_id, parent.run_id) / "run.json").read_bytes()
    brief = parent.brief.model_copy(deep=True)
    brief.continue_saved_hypotheses = True
    brief.budget.max_llm_calls = 6
    child = store.create_run(parent.project_id, brief, parent.run_id)
    control = Controller()
    engine = ResearchEngine(
        store,
        ScientistSettings(workspace=str(store.root)),
        sources=CoSources(),
        llm_factory=control.factory,
    )
    result = engine.execute(child.project_id, child.run_id)
    assert result.status.value == "completed", result.errors
    assert (
        result.co_scientist.contracts and result.co_scientist.seed["parent_run_id"] == parent.run_id
    )
    assert not any(
        name in ("AnchorDraft", "SynthesisDraft", "ReviewDraft", "GenerationDraft")
        for name, _, _ in control.requests
    )
    assert result.co_scientist.budget["literature_queries"] == 0
    assert (store.run_dir(parent.project_id, parent.run_id) / "run.json").read_bytes() == original


def test_generation_cannot_self_approve_in_the_saved_result_view(tmp_path):
    from scientist.presentation import presented_run

    _, _, run, _ = execute(tmp_path)
    original = run.model_dump()
    run.co_scientist.reviews = []
    assert any(h.status == "reviewed" for h in run.co_scientist.hypotheses)
    view = presented_run(run)
    assert all(h.status != "reviewed" for h in view.co_scientist.hypotheses)
    assert any(h.status == "reviewed" for h in run.co_scientist.hypotheses)
    assert (
        original["co_scientist"]["hypotheses"][0]["claim"] == run.co_scientist.hypotheses[0].claim
    )

