"""SYNTHETIC explicit exploration admission; all providers are offline fixtures."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_api import project
from test_api import workbench as api_workbench
from test_coscientist import Controller, CoSources, Worker
from test_research import FakeLLM, default_draft, source
from test_research_flow import setup_flow

from scientist.context import task_context
from scientist.intent_policy import (
    admit_run_inputs,
    task_intent_receipt,
    validate_exploration_start,
)
from scientist.models import ResearchBrief, ResearchBudget, ResearchRun, ScientistSettings
from scientist.research import ResearchEngine
from scientist.store import ProjectStore, write_json

workbench = api_workbench


def exploration(**changes):
    return ResearchBrief(disease="SYNTHETIC disease", task_intent="disease_exploration", **changes)


@pytest.mark.parametrize("field,value", [
    ("existing_candidates", ["CANDIDATE_SENTINEL"]),
    ("background_package_id", "NAC_PACK_SENTINEL"),
    ("target_hints", ["TARGET_HINT_SENTINEL"]),
    ("assumptions", ["WETLAB_SENTINEL"]),
    ("experiment_ids", ["EXPERIMENT_SENTINEL"]),
    ("target_packages", [{"source": "TARGET_PACKAGE_SENTINEL"}]),
    ("continue_saved_hypotheses", True),
    ("allowed_precursors", ["PRECURSOR_SENTINEL"]),
    ("engineering_allowed", True),
    ("experimental_capabilities", ["WETLAB_CAPABILITY_SENTINEL"]),
    ("delivery_context", "DELIVERY_HINT_SENTINEL"),
    ("conditions", "WETLAB_CONDITION_SENTINEL"),
    ("chassis_name", "WETLAB_STRAIN_SENTINEL"),
])
def test_exploration_rejects_guided_brief_at_normal_api_before_dispatch(workbench, field, value):
    pid = project(workbench)
    before = len(workbench.executor.calls)
    result = workbench.client.post(f"/api/projects/{pid}/runs", json={"brief": {
        "disease": "SYNTHETIC disease", "research_mode": "co_scientist",
        "task_intent": "disease_exploration", field: value}})
    assert result.status_code == 422
    assert len(workbench.executor.calls) == before and not workbench.store.list_runs(pid)
    with pytest.raises(ValidationError):
        exploration(**{field: value})


def test_api_admits_neutral_exploration_and_guided_candidate_assessment(workbench):
    pid = project(workbench)
    for task in (
        {"task_intent": "disease_exploration", "population": "SYNTHETIC adults", "phenotype": "SYNTHETIC endpoints"},
        {"task_intent": "candidate_assessment", "existing_candidates": ["SYNTHETIC compound"],
         "assumptions": ["SYNTHETIC unverified background"]},
    ):
        result = workbench.client.post(f"/api/projects/{pid}/runs", json={"brief": {
            "disease": "SYNTHETIC disease", "research_mode": "co_scientist", **task}})
        assert result.status_code == 202
        assert result.json()["brief"]["task_intent"] == task["task_intent"]
    with pytest.raises(ValidationError, match="existing_candidates"):
        ResearchBrief(disease="SYNTHETIC disease", task_intent="candidate_assessment")


def test_parent_run_reuse_is_rejected_before_parent_read_and_dispatch(workbench, monkeypatch):
    pid = project(workbench)
    def forbidden(*args, **kwargs):
        pytest.fail("Exploration parent must be rejected before reading old run")
    monkeypatch.setattr(workbench.store, "load_run", forbidden)
    before = len(workbench.executor.calls)
    response = workbench.client.post(f"/api/projects/{pid}/runs", json={"brief": {
        "disease": "SYNTHETIC disease", "task_intent": "disease_exploration"},
        "parent_run_id": "OLD_RUN_SENTINEL"})
    assert response.status_code == 422 and len(workbench.executor.calls) == before
    with pytest.raises(ValueError, match="parent_run_id"):
        admit_run_inputs(exploration(), "OLD_RUN_SENTINEL")


def test_legacy_readback_is_guided_not_retroactively_exploration(tmp_path):
    original = ResearchRun(run_id="r_legacy", project_id="p_legacy", created_at="fixture", updated_at="fixture",
        brief=ResearchBrief(disease="SYNTHETIC disease", existing_candidates=["LEGACY_CANDIDATE"],
                            assumptions=["WETLAB_SENTINEL"])).model_dump(mode="json")
    original["brief"].pop("task_intent")
    path = tmp_path / "SYNTHETIC_legacy.json"
    path.write_text(json.dumps(original))
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = ResearchRun.model_validate_json(path.read_text())
    receipt = task_intent_receipt(loaded.brief)
    assert receipt["declared_intent"] == "legacy" and receipt["effective_intent"] == "candidate_assessment"
    assert receipt["intent_basis"] == "legacy_compatible_guided_inputs"
    assert receipt["historical_memory_allowed"] is True
    assert task_intent_receipt(ResearchBrief(disease="SYNTHETIC"))["effective_intent"] == "guided_unspecified"
    assert "WETLAB_SENTINEL" in json.dumps(task_context(loaded.brief))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_neutral_task_projection_excludes_schema_chassis_defaults_and_engineering():
    brief = exploration(population="SYNTHETIC children", phenotype="SYNTHETIC core and associated symptoms")
    context = task_context(brief, include_documents=True)
    assert context["population"] == brief.population and context["phenotype"] == brief.phenotype
    assert "chassis_name" not in context and "WCFS1" not in json.dumps(context)
    assert context["task_intent"]["historical_memory_allowed"] is False
    for field in ("target_hints", "existing_candidates", "experimental_capabilities", "allowed_precursors"):
        assert field not in context


def test_inventory_skips_all_saved_libraries_and_memory_before_read(tmp_path, monkeypatch):
    engine, flow = setup_flow()
    engine.run.brief = exploration(research_mode="co_scientist")
    def forbidden(*args, **kwargs):
        pytest.fail("Exploration cannot inspect previous packages, libraries or project runs")
    engine.store = SimpleNamespace(list_runs=forbidden, load_run=forbidden, search_memory=forbidden)
    monkeypatch.setattr("scientist.background.load_background_package", forbidden)
    monkeypatch.setattr("scientist.research_flow.Path.is_dir", forbidden)
    flow.inventory()
    assert not engine.background_package.sources and not engine.background_package.identities
    assert engine.run.sources == []
    assert flow.data.library_status[0]["status"] == "disease_exploration_saved_context_excluded"
    assert "recorded:source_inventory" in flow.data.checklist


def test_standard_plan_ignores_memory_before_any_model_request(tmp_path):
    store = ProjectStore(tmp_path)
    pid = store.create_project("SYNTHETIC planning")["project_id"]
    run = store.create_run(pid, exploration())
    def forbidden(*args, **kwargs):
        pytest.fail("SYNTHETIC_OLD_MEMORY must not be read by exploration")
    store.search_memory = forbidden
    llm = FakeLLM(None, None, 2, draft=default_draft(), verdicts={})
    engine = ResearchEngine(store, ScientistSettings(workspace=str(tmp_path)), sources=CoSources())
    engine._plan(run, llm)
    assert len(llm.requests) == 1 and llm.requests[0][1]["memory"] == []
    assert "WCFS1" not in json.dumps(llm.requests)


def test_standard_exploration_omits_default_chassis_from_every_model_request(tmp_path):
    store = ProjectStore(tmp_path)
    pid = store.create_project("SYNTHETIC standard exploration")["project_id"]
    run = store.create_run(pid, exploration(budget=ResearchBudget(max_targets=0)))
    def forbidden(*args, **kwargs):
        pytest.fail("Standard exploration cannot read previous memory")
    store.search_memory = forbidden
    calls = []
    draft = default_draft()
    draft["targets"] = []
    draft["evidence"] = draft["evidence"][:1]
    def factory(settings, directory, max_calls):
        worker = FakeLLM(settings, directory, max_calls, draft=draft, verdicts={})
        calls.append(worker)
        return worker
    engine = ResearchEngine(store, ScientistSettings(workspace=str(tmp_path)),
                            sources=CoSources(), llm_factory=factory)
    result = engine.execute(pid, run.run_id, prepare_targets=False)
    assert result.evidence and result.candidates
    assert {schema for schema, _ in calls[0].requests} == {"PlanDraft", "SynthesisDraft", "ReviewDraft"}
    assert "WCFS1" not in json.dumps(calls[0].requests)
    synthesis = next(payload for schema, payload in calls[0].requests if schema == "SynthesisDraft")
    assert synthesis["brief"]["task_intent"]["declared_intent"] == "disease_exploration"


@pytest.mark.parametrize("field", ["sources", "candidates", "parent_run_id", "assumptions", "co_scientist"])
def test_actual_co_entry_rejects_bypassed_preloads_before_provider(tmp_path, monkeypatch, field):
    from scientist.co_models import CoScientistState
    from scientist.models import CandidateRecord
    store = ProjectStore(tmp_path)
    pid = store.create_project("SYNTHETIC preload admission")["project_id"]
    run = store.create_run(pid, exploration(research_mode="co_scientist"))
    if field == "sources":
        run.sources = [source("synthetic:old", "OLD_RUN_SENTINEL")]
    elif field == "candidates":
        run.candidates = [CandidateRecord(candidate_id="old", name="OLD_CANDIDATE_SENTINEL")]
    elif field == "assumptions":
        run.brief = run.brief.model_copy(update={"assumptions": ["WETLAB_SENTINEL"]})
    elif field == "co_scientist":
        run.co_scientist = CoScientistState(seed={"parent_run_id": "OLD_RUN_SENTINEL"})
    else:
        run.parent_run_id = "OLD_RUN_SENTINEL"
    # Bypass model validation deliberately to exercise the execution admission.
    monkeypatch.setattr(store, "load_run", lambda *args: run)
    calls = []
    def factory(*args, **kwargs):
        calls.append(True)
        pytest.fail("Preloaded research must be rejected before provider construction")
    engine = ResearchEngine(store, ScientistSettings(workspace=str(tmp_path)), sources=CoSources(), llm_factory=factory)
    with pytest.raises(ValueError):
        engine.execute(pid, run.run_id)
    assert not calls
    with pytest.raises(ValueError):
        validate_exploration_start(run)


def test_every_model_and_query_excludes_old_hints_but_fresh_nac_retrieval_is_allowed(tmp_path, monkeypatch):
    sentinels = ["OLD_MEMORY_SENTINEL", "OLD_RUN_SENTINEL", "WETLAB_SENTINEL", "NAC_PACK_SENTINEL"]
    store = ProjectStore(tmp_path / "workspace")
    pid = store.create_project("SYNTHETIC isolated exploration")["project_id"]
    old = store.create_run(pid, ResearchBrief(disease="SYNTHETIC disease", assumptions=[sentinels[2]],
        existing_candidates=[sentinels[3]]))
    old.sources = [source("synthetic:old", sentinels[1])]
    store.save_run(old)
    store.add_memory(pid, kind="fixture", text=sentinels[0], source_run_id=old.run_id)
    old_path = store.run_dir(pid, old.run_id) / "run.json"
    old_hash = hashlib.sha256(old_path.read_bytes()).hexdigest()
    # Explicit exploration must ignore all local libraries, not merely this
    # project's recent sources. A distinct repository path keeps the test local.
    fixture_root = tmp_path / "synthetic_repository"
    for name, sentinel in (("papers", sentinels[1]), ("literature", sentinels[2])):
        write_json(fixture_root / name / "source.json", source("synthetic:library", sentinel).model_dump())
    write_json(fixture_root / "knowledge" / "candidate_pack" / "manifest.json", {"sentinel": sentinels[3]})
    monkeypatch.setattr("scientist.research_flow.__file__", str(fixture_root / "scientist" / "research_flow.py"))
    def forbidden(*args, **kwargs):
        pytest.fail("Saved memory/run inventory must not be consulted")
    monkeypatch.setattr(store, "search_memory", forbidden)
    monkeypatch.setattr(store, "list_runs", forbidden)
    broad_quote = "SYNTHETIC public broad disease result reports NAC changed a recorded assay endpoint."
    sources = CoSources()
    sources.papers = [source("fixture:paper:1", broad_quote)]
    draft = default_draft()
    draft["evidence"] = [draft["evidence"][0]]
    draft["evidence"][0]["quote"] = broad_quote
    draft["targets"] = []
    draft["candidates"][0]["name"] = "NAC"
    class FreshWorker(Worker):
        def complete(self, system, user, schema):
            if schema["title"] == "SynthesisDraft":
                self.calls += 1
                self.controller.requests.append((schema["title"], json.loads(user), system))
                return copy.deepcopy(draft)
            result = super().complete(system, user, schema)
            if schema["title"] == "GenerationDraft":
                for h in result["hypotheses"]:
                    h["molecule_names"], h["target_symbols"] = ["NAC"], []
            return result
    control = Controller()
    control.barrier = SimpleNamespace(wait=lambda **kwargs: None)
    control.factory = lambda settings, directory, max_calls: FreshWorker(settings, directory, max_calls, control)
    run = store.create_run(pid, exploration(research_mode="co_scientist", budget=ResearchBudget(
        max_targets=0, max_hypotheses=2, max_llm_calls=16, max_papers=12, max_searches=5,
        max_input_tokens=300000, max_output_tokens=96000, timeout_seconds=840, max_md_jobs_total=0)))
    engine = ResearchEngine(store, ScientistSettings(workspace=str(store.root)), sources=sources, llm_factory=control.factory)
    result = engine.execute(pid, run.run_id, prepare_targets=False)
    assert control.requests and sources.searches
    captured = json.dumps({"models": control.requests, "queries": sources.searches})
    assert all(sentinel not in captured for sentinel in sentinels)
    assert "WCFS1" not in captured
    assert control.requests[0][0] == "AnchorDraft" and "NAC" not in json.dumps(control.requests[0])
    assert any(c.name == "NAC" for c in result.candidates)
    assert any(broad_quote in json.dumps(payload) for _, payload, _ in control.requests[1:])
    assert result.brief.existing_candidates == [] and result.brief.task_intent == "disease_exploration"
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == old_hash

