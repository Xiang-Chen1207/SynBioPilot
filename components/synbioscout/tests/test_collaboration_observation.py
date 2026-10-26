"""Stage 01 public observation contracts. All execution here uses offline fixtures."""

from __future__ import annotations

import copy
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import pytest
from test_api import workbench as offline_api_fixture
from test_coscientist import Controller, CoSources, Worker, execute

from scientist.co_models import AgentEvent
from scientist.models import ResearchRun, ScientistSettings
from scientist.presentation import result_document
from scientist.research import ResearchEngine
from scientist.tools import ToolResult

# Expose the fixture imported from the explicitly offline API test module.
workbench = offline_api_fixture

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = ROOT / "docs" / "serial_optimization" / "fixtures"


@pytest.fixture(scope="module")
def ui_fixtures():
    return json.loads((FIXTURE_DIRECTORY / "collaboration_states.json").read_text())


def run_from_view(document):
    return ResearchRun.model_validate({key: value for key, value in document.items()
                                      if key not in {"summary", "presentation", "candidate_assessments", "task_intent_receipt", "evidence_digest", "failure_records", "candidate_evaluation_records", "candidate_evaluations"}})


def test_historical_event_remains_readable_with_empty_observation_defaults():
    old = {"event_id": "old_1", "sequence": 1, "agent_id": "generation", "kind": "llm",
           "status": "completed", "title": "Saved operation", "created_at": "2026-01-01T00:00:00Z"}
    first, second = AgentEvent.model_validate(old), AgentEvent.model_validate(old)
    assert first.input_summary == first.operation_id == first.operation_status == ""
    assert first.input_artifact_ids == first.artifact_ids == []
    first.input_artifact_ids.append("separate_copy")
    assert second.input_artifact_ids == []
    assert first.model_dump(exclude_defaults=True)["title"] == old["title"]


def test_observations_distinguish_completed_parallel_call_from_active_role(tmp_path):
    _, _, run, _ = execute(tmp_path)
    events = run.co_scientist.events
    calls = defaultdict(list)
    for record in events:
        if record.kind == "llm":
            assert record.operation_id.startswith("llm_")
            calls[record.operation_id].append(record)
    assert calls
    assert all([record.operation_status for record in records] == ["running", "completed"]
               for records in calls.values())
    generation_completed = [record for record in events
                            if record.agent_id == "generation" and record.kind == "llm"
                            and record.operation_status == "completed"]
    assert len(generation_completed) == 2
    assert generation_completed[0].status == "running"
    assert generation_completed[1].status == "completed"
    assert len({record.operation_id for record in generation_completed}) == 2
    assert [record.sequence for record in events] == list(range(1, len(events) + 1))


def test_phase_handoffs_expose_public_inputs_and_saved_artifact_references(tmp_path):
    store, _, run, _ = execute(tmp_path)
    events = run.co_scientist.events
    start = next(record for record in events
                 if record.operation_id == "co_generation" and record.operation_status == "running")
    assert start.input_summary.startswith("疾病：")
    assert "anchor" in start.input_artifact_ids
    assert {e.evidence_id for e in run.evidence}.issubset(start.input_artifact_ids)
    end = next(record for record in events
               if record.operation_id == "co_generation" and record.operation_status == "completed")
    assert end.artifact_ids and all(":v1" in item for item in end.artifact_ids)
    reflection = next(record for record in events
                      if record.operation_id == "co_reflection" and record.operation_status == "completed")
    assert reflection.artifact_ids and all(item.startswith("review:") for item in reflection.artifact_ids)
    meta = next(record for record in events
                if record.operation_id == "co_meta" and record.operation_status == "completed")
    assert set(meta.artifact_ids) == {f"contract:{item.hypothesis_id}:v{item.version}"
                                     for item in run.co_scientist.contracts}
    saved = json.loads((store.run_dir(run.project_id, run.run_id) / "events.json").read_text())
    assert saved == [record.model_dump(mode="json") for record in events]
    forbidden = {"system", "prompt", "raw_prompt", "private_reasoning", "chain_of_thought"}
    assert not any(forbidden.intersection(record) for record in saved)


def test_tool_operation_status_maps_to_the_persisted_tool_result(tmp_path):
    _, _, run, _ = execute(tmp_path)
    assert run.co_scientist.tool_calls
    events = run.co_scientist.events
    for index, tool in enumerate(run.co_scientist.tool_calls):
        observations = [record for record in events if record.operation_id == f"tool_{index:03d}"]
        assert len(observations) == 2
        assert observations[0].operation_status == "running"
        assert observations[1].operation_status == tool["status"]
        assert set(observations[1].artifact_ids).issubset(s.source_id for s in run.sources)


def test_continuation_handoff_keeps_inherited_versions_as_inputs_only(tmp_path):
    class CurrentRoundWorker(Worker):
        def complete(self, system, user, schema):
            value = super().complete(system, user, schema)
            if schema["title"] == "EvolutionDraft":
                for card in value["revisions"]:
                    card["claim"] += " This continuation requires a same-condition measurement."
                    card["changes"] = ["SYNTHETIC current-round criticism narrows the measurement condition"]
            return value

    class CurrentRoundController(Controller):
        def factory(self, settings, directory, max_calls):
            return CurrentRoundWorker(settings, directory, max_calls, self)

    store, _, parent, _ = execute(tmp_path)
    parent_path = store.run_dir(parent.project_id, parent.run_id) / "run.json"
    original_parent = parent_path.read_bytes()
    brief = parent.brief.model_copy(deep=True)
    brief.continue_saved_hypotheses = True
    brief.budget.max_llm_calls = 6
    child = store.create_run(parent.project_id, brief, parent.run_id)
    controller = CurrentRoundController()
    engine = ResearchEngine(store, ScientistSettings(workspace=str(store.root)),
                            sources=CoSources(), llm_factory=controller.factory)
    result = engine.execute(child.project_id, child.run_id)
    assert result.status.value == "completed", result.errors
    events = result.co_scientist.events
    start = next(record for record in events
                 if record.operation_id == "co_evolution" and record.operation_status == "running")
    finish = next(record for record in events
                  if record.operation_id == "co_evolution" and record.operation_status == "completed")
    inherited = {f"{card.hypothesis_id}:v2" for card in parent.co_scientist.hypotheses if card.version == 2}
    revised = {f"{card.hypothesis_id}:v3" for card in result.co_scientist.hypotheses if card.version == 3}
    assert inherited and revised
    assert inherited.issubset(start.input_artifact_ids)
    assert set(finish.artifact_ids) == revised
    assert not inherited.intersection(finish.artifact_ids)
    assert inherited.issubset(f"{card.hypothesis_id}:v{card.version}" for card in result.co_scientist.hypotheses)
    assert parent_path.read_bytes() == original_parent


@pytest.mark.parametrize("case,expected", [
    ("valid", "reviewed"), ("wrong_version", "proposed"), ("wrong_verdict", "proposed"),
    ("unvalidated_span", "proposed"), ("no_valid_refs", "proposed"),
    ("duplicate_review", "proposed"), ("no_support", "needs_evidence"),
])
def test_results_api_keeps_same_version_review_and_source_record_separate(workbench, ui_fixtures, case, expected):
    run = run_from_view(ui_fixtures["runs"][1])
    hypothesis = run.co_scientist.hypotheses[0]
    hypothesis.status = "reviewed"
    review = run.co_scientist.reviews[0]
    review.verdict = "retain"
    if case == "wrong_version":
        review.version = 2
    elif case == "wrong_verdict":
        review.verdict = "withhold"
    elif case == "unvalidated_span":
        run.evidence[0].validated_span = False
    elif case == "no_valid_refs":
        review.valid_evidence_ids = []
    elif case == "duplicate_review":
        run.co_scientist.reviews.append(review.model_copy(deep=True))
    elif case == "no_support":
        hypothesis.evidence_ids = []
    project = workbench.store.create_project("SYNTHETIC observation API contract")
    run.project_id = project["project_id"]
    run.run_id = workbench.store.create_run(run.project_id, run.brief).run_id
    workbench.store.save_run(run)
    path = workbench.store.run_dir(run.project_id, run.run_id) / "run.json"
    before = path.read_bytes()
    base = f"/api/projects/{run.project_id}/runs/{run.run_id}"
    assert workbench.client.get(base).json()["co_scientist"]["hypotheses"][0]["status"] == "reviewed"
    response = workbench.client.get(base + "/results")
    assert response.status_code == 200
    view = response.json()
    assert view["co_scientist"]["hypotheses"][0]["status"] == expected
    assert view["presentation"]["derived_view"] is True
    assert view["presentation"]["original_record_unchanged"] is True
    assert path.read_bytes() == before


def test_synthetic_fixtures_are_explicit_valid_and_independent_of_real_projects(ui_fixtures):
    assert ui_fixtures["fixture"]["synthetic"] is True
    assert ui_fixtures["fixture"]["paid_model_calls"] == 0
    assert ui_fixtures["fixture"]["real_research_executed"] is False
    assert {run["presentation"]["fixture"]["scenario"] for run in ui_fixtures["runs"]} == {
        "running", "completed", "partial", "failed", "legacy"}
    for document in ui_fixtures["runs"]:
        run = run_from_view(document)
        assert document["presentation"]["fixture"]["label"] == "SYNTHETIC"
        assert run.project_id == "p_synthetic_ui" and run.run_id.startswith("r_synthetic_")
        assert "SYNTHETIC" in run.brief.disease
        assert all(source.metadata["synthetic"] for source in run.sources)
        assert all(source.url.startswith("https://example.invalid/") for source in run.sources)
        assert all("SYNTHETIC" in record.title for record in (run.co_scientist.events if run.co_scientist else []))
        if run.co_scientist:
            for tool in run.co_scientist.tool_calls:
                ToolResult.model_validate(tool)
            for contract in run.co_scientist.contracts:
                assert all(need.value is None for need in contract.parameter_needs)
        expected = result_document(run)
        assert expected["co_scientist"] == document["co_scientist"]
    assert ui_fixtures["runs"][3]["co_scientist"]["hypotheses"] == []
    assert ui_fixtures["runs"][4]["co_scientist"] is None


def test_synthetic_fixture_builder_is_deterministic_and_only_derives_views(ui_fixtures):
    spec = importlib.util.spec_from_file_location("collaboration_fixture_builder",
                                               FIXTURE_DIRECTORY / "build_collaboration_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build() == ui_fixtures
    before = copy.deepcopy(ui_fixtures)
    for document in ui_fixtures["runs"]:
        result_document(run_from_view(document))
    assert ui_fixtures == before

