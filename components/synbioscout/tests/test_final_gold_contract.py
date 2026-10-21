"""Offline contract tests; no genuine model judgement or external retrieval."""
import json
from copy import deepcopy
from types import SimpleNamespace

import jsonschema
import pytest
from test_delivery_r3_compact import (
    TransparentSynthesis,
    compact_case,
    legacy_equivalent,
    synthetic_sources,
)

from scientist.llm import InferenceError, StructuredLLM, strict_schema
from scientist.models import ResearchBrief, ResearchRun, ScientistSettings
from scientist.research import ResearchEngine, SynthesisDraft
from scientist.store import ProjectStore


def extraction(tmp_path, worker, value, *, intent="candidate_assessment"):
    run = ResearchRun(
        run_id="r_synthetic_final", project_id="p_synthetic_final",
        created_at="synthetic", updated_at="synthetic", status="running",
        brief=ResearchBrief(
            disease="SYNTHETIC syndrome", research_mode="co_scientist",
            task_intent=intent,
            existing_candidates=["Sentinel compound (SC)"] if intent == "candidate_assessment" else [],
        ), sources=synthetic_sources(value),
    )
    engine = ResearchEngine(
        ProjectStore(tmp_path), ScientistSettings(llm={"backend": "none"}),
        sources=SimpleNamespace(),
    )
    engine._synthesize(run, worker, tmp_path / "synthetic_artifacts")
    return run


class CaptureSchema(TransparentSynthesis):
    def complete(self, system, user, schema):
        if self.calls == 0:
            self.schema = deepcopy(schema)
        return super().complete(system, user, schema)


def test_normal_candidate_dispatch_rejects_legacy_but_history_stays_readable(tmp_path):
    value = compact_case(6)
    worker = CaptureSchema(value)
    extraction(tmp_path, worker, value)
    # Capture at the actual normal call, not a separately imported unused class.
    jsonschema.validate(value, strict_schema(worker.schema))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(legacy_equivalent(value), strict_schema(worker.schema))
    assert SynthesisDraft.model_validate(legacy_equivalent(value))
    assert "CandidateAssessmentProposal" not in worker.schema.get("$defs", {})
    assert worker.calls == 2  # real orchestration still asks for semantic review


@pytest.mark.parametrize("legacy", [False, True])
def test_real_structured_worker_validates_actual_dispatched_schema(tmp_path, monkeypatch, legacy):
    value = compact_case(2)
    wire = legacy_equivalent(value) if legacy else value
    settings = ScientistSettings(llm={"backend": "codex", "model": "SYNTHETIC-no-provider"})
    worker = StructuredLLM(settings.llm, tmp_path / "synthetic_transport", max_calls=2)
    calls = []

    def transport(_self, _system, user, shape, _key):
        payload = json.loads(user)
        calls.append(deepcopy(shape))
        if len(calls) == 1:
            return json.dumps(wire, ensure_ascii=False)
        return json.dumps({
            "reviews": [
                {"evidence_id": e["evidence_id"], "verdict": "supported_by_source",
                 "reason": "SYNTHETIC explicit transport fixture"}
                for e in payload["evidence"]
            ], "target_links": [], "molecule_links": [], "candidate_roles": [],
        })

    monkeypatch.setattr(StructuredLLM, "_codex", transport)
    if legacy:
        with pytest.raises(InferenceError) as caught:
            extraction(tmp_path, worker, value)
        assert caught.value.failure_metadata["category"] == "schema_invalid"
        assert len(calls) == worker.calls == 1
        assert not (tmp_path / "synthetic_artifacts" / "synthesis_draft.json").exists()
    else:
        run = extraction(tmp_path, worker, value)
        assert len(calls) == worker.calls == 2
        assert len(run.evidence) == 2
        assert len(run.candidate_evaluations) == 1
        assert run.candidate_evaluations[0].final is None
    assert "CandidateAssessmentProposal" not in calls[0].get("$defs", {})


def test_disease_exploration_keeps_its_existing_schema(tmp_path):
    value = compact_case(2)
    value["assessment_proposals"] = []
    worker = CaptureSchema(value)
    extraction(tmp_path, worker, value, intent="disease_exploration")
    assert worker.schema == SynthesisDraft.model_json_schema()


def test_cached_legacy_cannot_bypass_new_schema(tmp_path, monkeypatch):
    from scientist.compact_synthesis import CompactSynthesisDraft
    from scientist.store import digest, write_json

    settings = ScientistSettings(llm={"backend": "codex", "model": "SYNTHETIC"})
    worker = StructuredLLM(settings.llm, tmp_path / "synthetic_cache")
    shape = CompactSynthesisDraft.model_json_schema()
    identity = {"backend": "codex", "model": "SYNTHETIC", "base_url": settings.llm.base_url,
                "system": "synthetic", "user": "synthetic", "schema": shape}
    write_json(worker.directory / f"{digest(identity)}.json",
               {"result": legacy_equivalent(compact_case(1))})
    monkeypatch.setattr(worker, "_codex", lambda *_: pytest.fail("Cache test must not invoke a provider"))
    with pytest.raises(jsonschema.ValidationError):
        worker.complete("synthetic", "synthetic", shape)
    assert worker.calls == worker.cache_hits == 0


def test_multiple_candidates_keep_model_selected_evidence_separate():
    from scientist.compact_synthesis import CompactSynthesisDraft, expand_synthesis_draft

    value = compact_case(6)
    second = deepcopy(value["candidates"][0])
    second["name"] = "Other synthetic compound (OSC)"
    second["evidence_keys"] = ["e4", "e5", "e6"]
    value["candidates"][0]["evidence_keys"] = ["e1", "e2", "e3"]
    value["candidates"].append(second)
    other_proposal = deepcopy(value["assessment_proposals"][0])
    other_proposal["candidate_name"] = second["name"]
    other_proposal["findings"] = other_proposal["findings"][3:]
    value["assessment_proposals"][0]["findings"] = value["assessment_proposals"][0]["findings"][:3]
    value["assessment_proposals"].append(other_proposal)
    for row in value["evidence"][3:]:
        row["quote"] = row["quote"].replace("SC", "OSC")
        row["claim"] = row["claim"].replace("SC", "OSC")
    jsonschema.validate(value, strict_schema(CompactSynthesisDraft.model_json_schema()))
    expanded, receipt = expand_synthesis_draft(value)
    assert expanded.model_dump(mode="json") == legacy_equivalent(value)
    assert receipt["finding_count"] == 6
    assert [f.evidence_keys for f in expanded.assessment_proposals[0].findings] == [["e1"], ["e2"], ["e3"]]
    assert [f.evidence_keys for f in expanded.assessment_proposals[1].findings] == [["e4"], ["e5"], ["e6"]]

