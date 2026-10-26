"""Offline ChEMBL schema failures through the actual durable tool executor.

All entities/responses are SYNTHETIC. HTTPClient, shared RunLedger, provider
adapter, scheduler and CoScientistEngine atomic persistence are production code;
only requests.Session.request is replaced. No model or public provider is used.
"""
from __future__ import annotations

import copy
import json

import pytest
from test_tool_plans import (
    ACCESSION,
    ETHANOL_KEY,
    SPECIES,
    OfflineStack,
    activity,
    chembl,
    molecule,
    pubchem,
    target,
)
from test_tool_plans import forbid_real_network as offline_network_guard

from scientist.co_models import AgentState, CoScientistState
from scientist.coscientist import ROLES, BoundedSources, CoScientistEngine
from scientist.models import ExecutionStatus, ResearchBrief, ScientistSettings
from scientist.presentation import result_document
from scientist.research import ResearchEngine
from scientist.store import ProjectStore
from scientist.tool_plans import ToolScheduler
from scientist.tools import ToolRegistry, ToolRequest

forbid_real_network = offline_network_guard


def durable_stack(tmp_path, monkeypatch, *, retries=0):
    stack = OfflineStack(tmp_path, monkeypatch, retries=retries)
    store = ProjectStore(tmp_path / "workspace")
    project_id = store.create_project("SYNTHETIC nested-schema failure")["project_id"]
    run = store.create_run(project_id, ResearchBrief(disease="SYNTHETIC disease",
        research_mode="co_scientist", budget=stack.ledger.limits))
    run.status = ExecutionStatus.RUNNING
    legacy = ResearchEngine(store, ScientistSettings(workspace=str(store.root)), sources=stack.registry.sources)
    engine = CoScientistEngine(legacy)
    engine.run = run
    engine.state = run.co_scientist = CoScientistState(agents=[
        AgentState(agent_id=key, role=label, execution=execution)
        for key, (label, execution) in ROLES.items()])
    engine.ledger = stack.ledger
    engine.sources = BoundedSources(legacy.sources, engine)
    engine.registry = ToolRegistry(engine.sources, identity_sources=engine.sources.source)
    engine.scheduler = ToolScheduler(engine.planned_tool, engine.state.tool_plans, engine.persist)
    return stack, store, engine


def identity_row(resource):
    row = molecule() if resource == "molecule" else target()
    if resource == "target":
        row["target_components"][0]["component_type"] = "PROTEIN"
    return row


def explicit_activity(engine, resource):
    identifier = "CHEMBL101" if resource == "molecule" else "CHEMBL201"
    request = ToolRequest(name="chembl", arguments={"resource": "activity",
        resource + "_chembl_id": identifier, **({"species": SPECIES} if resource == "target" else {})})
    plan = engine.scheduler.create(request, origin="model_gap", gap_id="gap_synthetic_schema",
                                   reason="SYNTHETIC identity prerequisites must be verified")
    return plan, engine.scheduler.run(plan)


MALFORMED_IDENTITIES = [
    ("molecule", "molecule_structures", "SYNTHETIC_PROVIDER_SHAPE"),
    ("molecule", "molecule_structures", []),
    ("molecule", "molecule_structures", 7),
    ("molecule", "molecule_structures", False),
    ("molecule", "molecule_structures", {"standard_inchi_key": []}),
    ("molecule", "molecule_structures", {"canonical_smiles": {"bad": "shape"}}),
    ("molecule", "molecule_hierarchy", []),
    ("molecule", "molecule_hierarchy", "SYNTHETIC_PROVIDER_SHAPE"),
    ("molecule", "molecule_hierarchy", False),
    ("molecule", "molecule_hierarchy", {"parent_chembl_id": ["CHEMBL101"]}),
    ("molecule", "molecule_hierarchy", {"molecule_chembl_id": 101}),
    ("molecule", "molecule_synonyms", {}),
    ("molecule", "molecule_synonyms", "SYNTHETIC_PROVIDER_SHAPE"),
    ("molecule", "molecule_synonyms", False),
    ("molecule", "molecule_synonyms", ["SYNTHETIC_PROVIDER_SHAPE"]),
    ("molecule", "molecule_synonyms", [None]),
    ("molecule", "molecule_synonyms", [{"molecule_synonym": []}]),
    ("molecule", "molecule_synonyms", [{"syn_type": {"bad": "shape"}}]),
    ("target", "target_components", {}),
    ("target", "target_components", "SYNTHETIC_PROVIDER_SHAPE"),
    ("target", "target_components", False),
    ("target", "target_components", ["SYNTHETIC_PROVIDER_SHAPE"]),
    ("target", "target_components", [None]),
    ("target", "target_components", [{"accession": [ACCESSION], "component_type": "PROTEIN"}]),
    ("target", "target_components", [{"accession": ACCESSION, "component_type": False}]),
    ("target", "target_components", [{"accession": ACCESSION, "component_type": {"bad": "shape"}}]),
]


@pytest.mark.parametrize("resource,field,value", MALFORMED_IDENTITIES)
def test_malformed_nested_json_is_durable_failure_with_one_real_attempt(
        tmp_path, monkeypatch, resource, field, value):
    stack, store, engine = durable_stack(tmp_path, monkeypatch)
    row = identity_row(resource)
    row[field] = copy.deepcopy(value)
    stack.enqueue("/" + resource + ".json", chembl(resource, [row]))
    plan, result = explicit_activity(engine, resource)
    assert result is None  # experimental queries require validated identity prerequisites
    assert plan["status"] == "unavailable" and plan["stop_reason"] == "provider_schema"
    assert plan["call_ids"] == ["tool_000"]
    assert plan["identity"]["status"] == "unavailable" and plan["identity"]["selected_id"] == ""
    call = engine.state.tool_calls[0]
    assert call["status"] == "unavailable" and call["stop_reason"] == "provider_schema"
    assert call["records"] == call["sources"] == [] and call["record_count"] == 0
    assert call["identity"]["candidates"] == [] and call["identity"]["selected_id"] == ""
    assert call["plan_id"] == plan["plan_id"] and call["gap_id"] == "gap_synthetic_schema"
    assert call["http_trace"][0]["outcome"] == "ok"  # transport succeeded; nested schema did not
    assert call["http_trace"][0]["parameters"][resource + "_chembl_id"] == row[resource + "_chembl_id"]
    assert call["request_count"] == engine.state.budget["http_attempts"] == len(stack.network) == 1
    assert engine.state.budget["tool_calls"] == 1 and engine.state.budget["cache_hits"] == 0
    assert not engine.run.sources and not stack.responses
    assert "SYNTHETIC_PROVIDER_SHAPE" not in json.dumps(call["warnings"])
    events = [event for event in engine.state.events if event.operation_id == call["call_id"]]
    assert [event.operation_status for event in events] == ["running", "unavailable"]
    assert events[0].input_artifact_ids == [plan["plan_id"]]
    saved = store.load_run(engine.run.project_id, engine.run.run_id).co_scientist
    assert saved.tool_calls == engine.state.tool_calls and saved.tool_plans == engine.state.tool_plans
    assert saved.budget["http_attempts"] == saved.budget["tool_calls"] == 1
    assert saved.tool_plans == [plan]
    public = result_document(engine.run)
    public_call = public["co_scientist"]["tool_calls"][0]
    public_plan = public["co_scientist"]["tool_plans"][0]
    assert public_call["status"] == public_plan["status"] == "unavailable"
    assert public_call["identity"]["status"] == public_plan["identity"]["status"] == "unavailable"
    assert not public_call["identity"].get("selected_id") and not public_plan["identity"].get("selected_id")
    assert "SYNTHETIC_PROVIDER_SHAPE" not in json.dumps(public)


@pytest.mark.parametrize("resource,field", [
    ("molecule", "molecule_structures"), ("target", "target_components"),
])
@pytest.mark.parametrize("missing", [False, True])
def test_null_or_missing_required_nested_identity_stays_unresolved_and_preserves_records(
        tmp_path, monkeypatch, resource, field, missing):
    stack, _, engine = durable_stack(tmp_path, monkeypatch)
    row = identity_row(resource)
    if missing:
        del row[field]
    else:
        row[field] = None
    stack.enqueue("/" + resource + ".json", chembl(resource, [row]))
    plan, result = explicit_activity(engine, resource)
    assert result is None and plan["status"] == "unresolved"
    call = engine.state.tool_calls[0]
    assert call["status"] == "ok" and call["records"] == [row]  # low-level legacy record interface
    assert call["identity"]["status"] == "unresolved" and not call["identity"]["selected_id"]
    assert len(stack.network) == engine.state.budget["http_attempts"] == 1


@pytest.mark.parametrize("accession,component_type", [
    (None, None), (None, "PROTEIN"), (ACCESSION, None), (ACCESSION, "DNA"),
    (ACCESSION, "UNKNOWN"), (ACCESSION, ""), ("invalid_accession", "PROTEIN"), ("", "PROTEIN"),
])
def test_explicit_target_id_requires_complete_protein_component_before_activity(
        tmp_path, monkeypatch, accession, component_type):
    stack, _, engine = durable_stack(tmp_path, monkeypatch)
    row = identity_row("target")
    row["target_components"] = [{"accession": accession, "component_type": component_type}]
    stack.enqueue("/target.json", chembl("target", [row]))
    plan, result = explicit_activity(engine, "target")
    assert result is None and plan["status"] == "unresolved"
    call = engine.state.tool_calls[0]
    assert call["status"] == "ok" and call["records"] == [row]
    assert call["identity"]["status"] == "unresolved" and not call["identity"]["selected_id"]
    assert "valid UniProt accession" in call["identity"]["reason"]
    assert len(stack.network) == engine.state.budget["http_attempts"] == engine.state.budget["tool_calls"] == 1
    assert not any(request["url"].endswith("/activity.json") for request in stack.network)


def test_schema_failure_after_pubchem_preserves_verified_source_and_parent_trace(tmp_path, monkeypatch):
    stack, _, engine = durable_stack(tmp_path, monkeypatch)
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    row = identity_row("molecule")
    row["molecule_structures"] = "SYNTHETIC_PROVIDER_SHAPE"
    stack.enqueue("/molecule.json", chembl("molecule", [row]))
    plan = engine.scheduler.create(ToolRequest(name="chembl", arguments={
        "resource": "activity", "molecule_name": "SYNTHETIC alias"}), origin="model_gap")
    assert engine.scheduler.run(plan) is None and plan["status"] == "unavailable"
    first, failed = engine.state.tool_calls
    assert first["identity"]["status"] == "resolved" and first["sources"]
    assert failed["parent_call_id"] == first["call_id"]
    assert failed["identity"]["selected_id"] == "" and not failed["records"] and not failed["sources"]
    assert plan["call_ids"] == [first["call_id"], failed["call_id"]]
    assert len(engine.run.sources) == 1 and engine.run.sources[0].source_id == first["sources"][0]["source_id"]
    assert engine.state.budget["http_attempts"] == engine.state.budget["tool_calls"] == 2


def test_schema_failure_retains_retry_accounting_and_cache_is_another_failed_call(tmp_path, monkeypatch):
    stack, _, engine = durable_stack(tmp_path, monkeypatch, retries=1)
    row = identity_row("molecule")
    row["molecule_hierarchy"] = []
    stack.enqueue("/molecule.json", {}, status=500)
    stack.enqueue("/molecule.json", chembl("molecule", [row]))
    plan, result = explicit_activity(engine, "molecule")
    assert result is None and plan["status"] == "unavailable"
    first = engine.state.tool_calls[0]
    assert first["request_count"] == 2 and first["cache_hits"] == 0
    assert [attempt["outcome"] for attempt in first["http_trace"]] == ["http_error", "ok"]
    assert [attempt["retry_index"] for attempt in first["http_trace"]] == [0, 1]
    # A fresh scheduler models a later call against the same transport cache;
    # its durable tool call still costs a tool invocation, not an HTTP attempt.
    engine.scheduler = ToolScheduler(engine.planned_tool, engine.state.tool_plans, engine.persist)
    cached_plan, result = explicit_activity(engine, "molecule")
    assert result is None and cached_plan["status"] == "unavailable"
    cached = engine.state.tool_calls[1]
    assert cached["status"] == "unavailable" and cached["stop_reason"] == "provider_schema"
    assert cached["request_count"] == 0 and cached["cache_hits"] == 1 and not cached["records"]
    assert cached["http_trace"][0]["outcome"] == "cache_hit"
    assert len(stack.network) == engine.state.budget["http_attempts"] == 2
    assert engine.state.budget["tool_calls"] == 2 and engine.state.budget["cache_hits"] == 1


def test_unique_complete_nonparent_form_queries_its_original_id_without_parent_substitution(tmp_path, monkeypatch):
    stack = OfflineStack(tmp_path, monkeypatch)
    identifier, parent_id = "CHEMBL102", "CHEMBL101"
    row = molecule(identifier, parent=parent_id)
    record = activity()
    record["molecule_chembl_id"] = identifier
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    stack.enqueue("/molecule.json", chembl("molecule", [row]))
    stack.enqueue("/activity.json", chembl("activity", [record]))
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC exact chemical form")
    assert result.status == plan["status"] == "ok" and result.records == [record]
    identity_call = stack.calls[1]
    assert identity_call.identity["status"] == "resolved" and identity_call.identity["selected_id"] == identifier
    assert identity_call.identity["candidates"][0]["parent_id"] == parent_id
    assert identity_call.identity["candidates"][0]["inchikey"] == ETHANOL_KEY
    assert identity_call.records[0]["molecule_structures"]["canonical_smiles"] == "CCO"
    assert stack.network[1]["params"]["molecule_structures__standard_inchi_key"] == ETHANOL_KEY
    assert result.parameters["molecule_chembl_id"] == result.identity["molecule_chembl_id"] == identifier
    assert stack.network[2]["params"]["molecule_chembl_id"] == identifier
    assert all((request["params"] or {}).get("molecule_chembl_id") != parent_id for request in stack.network)
    assert len(stack.calls) == len(stack.network) == stack.ledger.counts["http_attempts"] == 3
    assert not stack.responses

