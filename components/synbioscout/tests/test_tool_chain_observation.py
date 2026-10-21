"""Full coordinator integration using synthetic LLMs and actual offline tool execution."""
import copy
import io
import json
import time
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_coscientist import Controller, CoSources, Worker
from test_tool_plans import (
    OfflineStack,
    activity,
    chembl,
    molecule,
    protein,
    pubchem,
)
from test_tool_plans import (
    forbid_real_network as offline_network_guard,
)

from scientist.api import create_app
from scientist.budgets import RunLedger
from scientist.coscientist import CoScientistEngine
from scientist.exports import export_research
from scientist.models import ResearchBrief, ResearchBudget, ScientistSettings
from scientist.presentation import result_document
from scientist.reports import render_report
from scientist.research import ResearchEngine
from scientist.sources import BiomedicalSources
from scientist.store import ProjectStore
from scientist.tool_plans import ToolScheduler
from scientist.tools import ToolRegistry, ToolRequest

forbid_real_network = offline_network_guard


class PlannedWorker(Worker):
    def complete(self, system, user, schema):
        result = super().complete(system, user, schema)
        if schema["title"] == "GenerationDraft":
            result["tool_requests"] = [{"name": "chembl", "arguments_json": json.dumps({
                "resource": "activity", "molecule_name": "Illustrative compound", "limit": 1}),
                "gap": "SYNTHETIC: assay context is missing", "hypothesis_ids": []}]
        return result


class PlannedController(Controller):
    def factory(self, settings, directory, max_calls):
        return PlannedWorker(settings, directory, max_calls, self)


class ActualIdentitySources(CoSources):
    def __init__(self, http):
        super().__init__()
        self.http = http
        self.identities = BiomedicalSources(http)

    def resolve_target(self, symbol, species):
        return self.identities.resolve_target(symbol, species)

    def resolve_molecule(self, name):
        return self.identities.resolve_molecule(name)

    def database_source(self, *args, **kwargs):
        return self.identities.database_source(*args, **kwargs)


def run_chain(tmp_path, monkeypatch, *, max_tools=12):
    stack = OfflineStack(tmp_path, monkeypatch)
    # Core chemical preparation precedes the legacy protein resolver.
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    stack.enqueue("/uniprotkb/search", {"results": [protein(gene="FIXTURE1")]})
    stack.enqueue("/molecule.json", chembl("molecule", [molecule()]))
    stack.enqueue("/activity.json", chembl("activity", [activity()]))
    store = ProjectStore(tmp_path / "workspace")
    project = store.create_project("SYNTHETIC tool chain")["project_id"]
    brief = ResearchBrief(disease="Fixture disease", research_mode="co_scientist",
                          budget=ResearchBudget(max_llm_calls=16, max_tool_calls=max_tools, max_hypotheses=4,
                                                max_papers=8, max_input_tokens=500000,
                                                max_output_tokens=100000))
    run = store.create_run(project, brief)
    control = PlannedController()
    engine = ResearchEngine(store, ScientistSettings(workspace=str(store.root), llm={"backend": "codex"}),
                            sources=ActualIdentitySources(stack.http), llm_factory=control.factory)
    result = engine.execute(project, run.run_id)
    return store, result, stack, control


def test_coordinator_executes_and_persists_identity_plan_chain_and_reuses_supervisor_resolution(tmp_path, monkeypatch):
    store, run, stack, _ = run_chain(tmp_path, monkeypatch)
    assert run.status.value == "completed", run.errors
    co = run.co_scientist
    assert co.budget["http_attempts"] == len(stack.network) == 4
    assert co.budget["tool_calls"] == len(co.tool_calls)
    assert {p["origin"] for p in co.tool_plans} == {"core_identity", "supervisor_identity", "model_gap", "deterministic_validation"}
    model = [p for p in co.tool_plans if p["origin"] == "model_gap"]
    assert len(model) == 2 and model[1]["status"] == "skipped" and model[1]["duplicate_of"] == model[0]["plan_id"]
    assert model[0]["status"] == "ok" and model[0]["hypothesis_ids"]
    calls = {r["call_id"]: r for r in co.tool_calls}
    first, resolution, experiment = [calls[i] for i in model[0]["call_ids"]]
    assert first["plan_id"] != model[0]["plan_id"]  # reference to saved supervisor identity
    assert resolution["parent_call_id"] == first["call_id"]
    assert experiment["parent_call_id"] == resolution["call_id"]
    assert experiment["parameters"]["molecule_chembl_id"] == "CHEMBL101"
    assert experiment["http_trace"][0]["parameters"]["limit"] == 1
    for call in co.tool_calls:
        events = [e for e in co.events if e.operation_id == call["call_id"]]
        assert [e.operation_status for e in events] == ["running", call["status"]]
        assert events[0].input_artifact_ids == ([call["plan_id"]] if call["plan_id"] else [])
    saved = json.loads((store.run_dir(run.project_id, run.run_id) / "tool_plans.json").read_text())
    assert saved == co.tool_plans
    # Tool observations never supply measured production or numerical PKPD.
    assert all(h.chassis.status == "unknown" for h in co.hypotheses)
    assert all(n.value is None for c in co.contracts for n in c.parameter_needs)


def test_tool_budget_failure_is_durable_and_stops_before_paid_generation(tmp_path, monkeypatch):
    _, run, stack, control = run_chain(tmp_path, monkeypatch, max_tools=1)
    assert run.status.value == "partial"
    assert len(stack.network) == run.co_scientist.budget["http_attempts"] == 1
    assert run.co_scientist.budget["tool_calls"] == 1
    failed = run.co_scientist.tool_calls[-1]
    assert failed["status"] == "budget_exhausted" and failed["request_count"] == 0
    assert run.co_scientist.tool_plans[-1]["status"] == "budget_exhausted"
    assert not run.co_scientist.hypotheses and not run.co_scientist.contracts
    assert not any(name == "GenerationDraft" for name, _, _ in control.requests)
    assert any(e.operation_id == failed["call_id"] and e.operation_status == "budget_exhausted"
               for e in run.co_scientist.events)


def test_budget_failure_after_experiment_keeps_records_and_stops_before_reflection(tmp_path, monkeypatch):
    _, run, stack, control = run_chain(tmp_path, monkeypatch, max_tools=4)
    assert run.status.value == "partial"
    assert len(stack.network) == run.co_scientist.budget["http_attempts"] == 4
    assert run.co_scientist.hypotheses and not run.co_scientist.contracts
    assert any(t["parameters"].get("resource") == "activity" and t["records"] for t in run.co_scientist.tool_calls)
    assert run.co_scientist.tool_calls[-1]["status"] == "budget_exhausted"
    assert not any(name == "ReflectionDraft" for name, _, _ in control.requests)


def test_product_json_and_zip_export_only_public_tool_fields_without_rewriting_history(tmp_path, monkeypatch):
    store, run, _, _ = run_chain(tmp_path, monkeypatch)
    call = next(t for t in run.co_scientist.tool_calls if t["parameters"].get("resource") == "activity")
    call["raw_prompt"] = "PRIVATE_TOOL_SENTINEL"
    call["records"][0]["provider_payload"] = {"secret": "PRIVATE_PROVIDER_SENTINEL"}
    call["http_trace"][0]["headers"] = {"Authorization": "PRIVATE_HEADER_SENTINEL"}
    source_id = call["sources"][0]["source_id"]
    for source in run.sources:
        if source.source_id == source_id:
            source.metadata["private_prompt"] = "PRIVATE_SOURCE_SENTINEL"
            source.text += " PRIVATE_SOURCE_TEXT_SENTINEL"
    run.co_scientist.tool_plans[0]["raw_prompt"] = "PRIVATE_PLAN_SENTINEL"
    original = copy.deepcopy(run.model_dump())
    document = result_document(run)
    assert "PRIVATE_" not in json.dumps(document)
    assert document["co_scientist"]["tool_calls"][-3]["call_id"]  # all calls remain navigable
    for format_name in ("json", "zip"):
        data, _, _ = export_research(store, run, format_name)
        if format_name == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                assert "tool_plans.json" in archive.namelist()
                data = b"\n".join(archive.read(name) for name in archive.namelist() if name.endswith(".json"))
        assert b"PRIVATE_" not in data
    assert run.model_dump() == original


def test_invalid_parameter_keys_and_unknown_tool_names_cannot_leak_through_tool_events(tmp_path, monkeypatch):
    store, run, stack, _ = run_chain(tmp_path, monkeypatch)
    legacy = ResearchEngine(store, ScientistSettings(workspace=str(store.root)), sources=ActualIdentitySources(stack.http))
    engine = CoScientistEngine(legacy)
    engine.run, engine.state = run, run.co_scientist
    engine.ledger = RunLedger(run.brief.budget, time.monotonic() + 30)
    from scientist.coscientist import BoundedSources
    engine.sources = BoundedSources(legacy.sources, engine)
    engine.registry = ToolRegistry(legacy.sources)
    engine.scheduler = ToolScheduler(engine.planned_tool, engine.state.tool_plans, engine.persist)
    before = len(stack.network)
    result, _ = engine.tool(ToolRequest(name="chembl", arguments={"resource": "activity",
        "molecule_chembl_id": "CHEMBL101", "raw_prompt=PRIVATE_KEY_SENTINEL": "not public",
        "/Users/PRIVATE_PATH_SENTINEL": "not public"}))
    assert result.status == "invalid" and len(stack.network) == before
    result, _ = engine.tool(ToolRequest(name="ghp_privatecredential123", arguments={}))
    assert result.status == "invalid" and len(stack.network) == before
    doc = result_document(run)
    assert "PRIVATE_" not in json.dumps(doc)
    assert "ghp_privatecredential123" not in json.dumps(doc)
    assert "unrecognized_field" in json.dumps(doc["co_scientist"]["events"])


def test_legacy_diagnostic_tool_events_are_projected_without_rewriting_original(tmp_path, monkeypatch):
    store, run, _, _ = run_chain(tmp_path, monkeypatch)
    event = next(e for e in run.co_scientist.events if e.kind == "tool")
    event.summary = "raw_prompt=PRIVATE_EVENT_SENTINEL"
    event.title = "ghp_privatecredential123"
    event.input_summary = "/Users/PRIVATE_PATH_SENTINEL"
    event.artifact_ids = ["/Users/PRIVATE_REFERENCE_SENTINEL"]
    run.co_scientist.agents[0].task = "authorization=PRIVATE_TASK_SENTINEL"
    original = copy.deepcopy(run.model_dump())
    assert "PRIVATE_" not in json.dumps(result_document(run))
    for format_name in ("json", "zip"):
        data, _, _ = export_research(store, run, format_name)
        if format_name == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                data = b"\n".join(archive.read(name) for name in archive.namelist() if name.endswith(".json"))
        assert b"PRIVATE_" not in data and b"ghp_privatecredential123" not in data
    assert run.model_dump() == original


def assert_all_public_exits_hide(client, store, run, sentinel):
    endpoint = f"/api/projects/{run.project_id}/runs/{run.run_id}"
    before = (store.run_dir(run.project_id, run.run_id) / "run.json").read_bytes()
    result = client.get(endpoint + "/results")
    assert result.status_code == 200 and sentinel not in result.text
    for format_name in ("json", "markdown", "csv", "zip"):
        response = client.get(endpoint + "/export", params={"format": format_name})
        assert response.status_code == 200
        if format_name == "zip":
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert {"report.md", "research.json"} <= set(archive.namelist())
                for name in archive.namelist():
                    assert sentinel.encode() not in archive.read(name), name
        else:
            assert sentinel.encode() not in response.content, format_name
    assert sentinel not in render_report(run)
    assert (store.run_dir(run.project_id, run.run_id) / "run.json").read_bytes() == before


@pytest.mark.parametrize("sentinel", ["ghp_privatecredential123", "unregistered_private_sentinel"])
def test_invalid_json_proposal_does_not_echo_unregistered_name_in_any_public_exit(tmp_path, monkeypatch, sentinel):
    class InvalidProposalWorker(PlannedWorker):
        def complete(self, system, user, schema):
            result = super().complete(system, user, schema)
            if schema["title"] == "GenerationDraft":
                result["tool_requests"] = [{"name": sentinel, "arguments_json": "{not valid JSON",
                                            "gap": "SYNTHETIC missing context", "hypothesis_ids": []}]
            return result

    monkeypatch.setattr(PlannedController, "factory", lambda self, settings, directory, max_calls:
                        InvalidProposalWorker(settings, directory, max_calls, self))
    store, run, stack, _ = run_chain(tmp_path, monkeypatch)
    assert run.status.value == "completed", run.errors
    assert len(stack.network) == 2  # fixed identities only; no rejected query reaches HTTP
    assert run.warnings and all(sentinel not in warning for warning in run.warnings)
    settings = ScientistSettings(workspace=str(store.root), llm={"backend": "none"})
    with TestClient(create_app(settings, service=SimpleNamespace(store=store, shutdown=lambda: None))) as client:
        assert_all_public_exits_hide(client, store, run, sentinel)


@pytest.mark.parametrize("co_present", [True, False])
def test_historical_invalid_proposal_warning_is_projected_in_all_exits_without_rewriting_run(tmp_path, monkeypatch, co_present):
    store, run, _, _ = run_chain(tmp_path, monkeypatch)
    sentinel = "unregistered_private_sentinel"
    run.warnings.append("Invalid structured tool proposal; not executed: " + sentinel)
    if not co_present:
        run.co_scientist = None
    store.save_run(run)
    original = copy.deepcopy(run.model_dump())
    settings = ScientistSettings(workspace=str(store.root), llm={"backend": "none"})
    with TestClient(create_app(settings, service=SimpleNamespace(store=store, shutdown=lambda: None))) as client:
        assert_all_public_exits_hide(client, store, run, sentinel)
    assert run.model_dump() == original and sentinel in store.load_run(run.project_id, run.run_id).warnings[-1]

