"""SYNTHETIC R2 FIX1 current conclusions across all public formats; no models."""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import shutil
import subprocess
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_candidate_conclusion_review import (
    assessment_case,
    complete_assessment,
    review_response,
    synthetic_operation,
)

from scientist.api import create_app
from scientist.assessment_review import (
    apply_assessment_review,
    bind_assessment_identities,
    public_validated_evaluations,
    store_assessment_proposals,
)
from scientist.exports import export_document, export_research, markdown_bytes, table_exports
from scientist.models import ExecutionStatus, LLMSettings, ScientistSettings
from scientist.presentation import result_document
from scientist.research_flow import source_observation_statement
from scientist.runtime import JobService
from scientist.store import ProjectStore, write_json

STAMP = "2026-10-16T00:00:00+08:00"
INITIAL_CONCLUSION = "SYNTHETIC reviewed assessment of the original recorded endpoint. Applicability to the requested use remains uncertain."


def _configure(run, case):
    run.project_id = "p_synthetic_ui"
    run.run_id = "r_synthetic_r2_fix1_" + case
    run.created_at = run.updated_at = STAMP
    run.brief.disease = "SYNTHETIC R2 FIX1 · " + case


def _decorate(run):
    run.status = ExecutionStatus.PARTIAL
    valid = public_validated_evaluations(run)
    run.usage.update(candidate_assessment_status="completed_review" if valid else "needs_review", hypothesis_expansion_status="not_run")
    if run.co_scientist.events:
        event = run.co_scientist.events[-1]
        event.created_at = STAMP
        event.input_artifact_ids = [f"assessment:{r.assessment_id}:v{r.version}" for r in run.candidate_evaluations]
        event.artifact_ids = ["assessment_review:" + review.review_id for r in run.candidate_evaluations for review in r.reviews]
    return run


def persist_case(tmp_path, run):
    store = ProjectStore(tmp_path / run.run_id / "SYNTHETIC_workspace")
    write_json(store.root / "projects" / run.project_id / "project.json", {
        "project_id":run.project_id,"name":"SYNTHETIC R2 FIX1 public cases","created_at":STAMP})
    write_json(store.project_dir(run.project_id) / "research" / run.run_id / "run.json", run.model_dump(mode="json"))
    return store


def evidence_change_case(*, initial="opposes", added="supports", linked=True, unrelated=False, case=None):
    """Use actual proposal/binding/review helpers; mutate only synthetic evidence."""
    run, proposal, keys = assessment_case(polarity=initial)
    case = case or f"{initial}_{added}_{'linked' if linked else 'unlinked'}{'_unrelated' if unrelated else ''}"
    _configure(run, case)
    quote = ("SC did not improve the recorded associated clinical endpoint compared with placebo."
             if initial == "opposes" else "SC improved the recorded associated clinical endpoint compared with placebo.")
    run.evidence[0].quote = quote
    run.evidence[0].claim = "SYNTHETIC trial records the endpoint observation for SC."
    run.evidence[0].endpoint = "SYNTHETIC associated clinical endpoint"
    run.sources[0].text = "SYNTHETIC Methods: 31 human children were enrolled and randomized to SC or placebo. " + quote
    proposal["findings"][0]["statement"] = source_observation_statement(run.sources[0].source_id,quote)
    proposal["findings"][0]["scope"]["endpoint"] = run.evidence[0].endpoint
    proposal["conclusion"] = INITIAL_CONCLUSION
    assert len(complete_assessment(run, proposal, keys)) == 1
    _decorate(run)
    old_record = run.candidate_evaluations[0].model_dump_json()
    if added is not None:
        compound = "Different compound" if unrelated else "SC"
        quote = {
            "supports": f"{compound} improved the recorded associated endpoint compared with placebo.",
            "opposes": f"{compound} did not improve the recorded associated endpoint compared with placebo.",
            "mixed": f"{compound} showed mixed findings for the recorded associated endpoint compared with placebo.",
            "context": f"{compound} was measured in the recorded associated endpoint study without an intervention finding.",
        }[added]
        source = run.sources[0].model_copy(deep=True,update={"source_id":"synthetic:new_evidence", "title":"SYNTHETIC independent follow-up",
            "text":"SYNTHETIC Methods: 40 human children were enrolled and randomized. " + quote})
        evidence = run.evidence[0].model_copy(deep=True,update={"evidence_id":"ev_synthetic_new", "source_id":source.source_id,
            "claim":f"SYNTHETIC follow-up records the observation for {compound}.","quote":quote,"polarity":added})
        run.sources.append(source)
        run.evidence.append(evidence)
        if linked:
            run.candidates[0].evidence_ids.append(evidence.evidence_id)
    assert run.candidate_evaluations[0].model_dump_json() == old_record
    return run, old_record


def alias_case(kind="ambiguous", *, case=None):
    run, proposal, _keys = assessment_case()
    _configure(run, case or "alias_" + kind)
    first = run.candidates[0]
    first.name = "SYNTHETIC Entity Alpha"
    second = first.model_copy(deep=True, update={"candidate_id":"mol_synthetic_beta", "entity_id":"entity_synthetic_beta",
        "name":"SYNTHETIC Entity Beta", "smiles":"CC(=O)O", "inchikey":"QTBSBXVTEAMEQO-UHFFFAOYSA-N", "pubchem_cid":"176"})
    run.candidates.append(second)
    run.brief.existing_candidates = [c.name for c in run.candidates]
    original_source, original_evidence = run.sources[0], run.evidence[0]
    run.sources, run.evidence = [], []
    proposals, mapping = [], {}
    for index, candidate in enumerate(run.candidates):
        label = "SC" if kind == "ambiguous" else candidate.name if kind == "full_name" else "PubChem CID " + candidate.pubchem_cid
        quote = f"{label} and placebo did not differ on the recorded clinical endpoint, while blood marker M was higher."
        source = original_source.model_copy(deep=True, update={"source_id":f"synthetic:alias_{index}", "title":"SYNTHETIC coded intervention study",
            "text":"SYNTHETIC Methods: 31 human children were enrolled and randomized. " + quote})
        evidence = original_evidence.model_copy(deep=True,update={"evidence_id":f"ev_synthetic_alias_{index}","source_id":source.source_id,
            "quote":quote,"claim":f"SYNTHETIC trial records mixed endpoint observations for {label}."})
        run.sources.append(source)
        run.evidence.append(evidence)
        candidate.evidence_ids = [evidence.evidence_id]
        row = deepcopy(proposal)
        row["candidate_name"] = candidate.name
        row["conclusion"] = INITIAL_CONCLUSION
        row["findings"][0].update(statement=source_observation_statement(source.source_id,quote),evidence_keys=[evidence.evidence_id])
        proposals.append(row)
        mapping[evidence.evidence_id] = evidence.evidence_id
    store_assessment_proposals(run,proposals,mapping)
    bind_assessment_identities(run)
    response = review_response(run)
    for index, review in enumerate(response["reviews"]): review["review_id"] = f"review_synthetic_alias_{index}"
    operation = synthetic_operation(run) if response["reviews"] else ""
    apply_assessment_review(run,response,operation_id=operation)
    return _decorate(run)


def csv_rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def assert_public_formats(store, run, expected_count):
    before = run.model_dump_json()
    path = store.run_dir(run.project_id,run.run_id) / "run.json"
    original_bytes = path.read_bytes()
    document = result_document(run)
    exported = export_document(run)
    assert len(public_validated_evaluations(run)) == expected_count
    assert len(document["candidate_evaluations"]) == expected_count
    assert exported["candidate_evaluations"] == document["candidate_evaluations"]
    for card in document["candidate_assessments"]:
        assert card["assessment_status"] == ("completed_review" if expected_count else "unreviewed")
        assert (card["validated_evaluation"] is not None) is bool(expected_count)
    records = document["candidate_evaluation_records"]
    assert sum(r["final"] is not None for r in records) == expected_count
    tables = table_exports(run)
    assert len(csv_rows(tables["candidate_evaluations.csv"])) == expected_count
    for candidate in csv_rows(tables["candidates.csv"]):
        assert candidate["assessment_status"] == ("completed_review" if expected_count else "unreviewed")
        assert bool(candidate["conclusion"]) is bool(expected_count)
    markdown = markdown_bytes(run).decode()
    assert (INITIAL_CONCLUSION in markdown) is bool(expected_count)
    packed,_,_ = export_research(store,run,"zip")
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert archive.read("candidate_evaluations.csv") == tables["candidate_evaluations.csv"]
        assert json.loads(archive.read("candidate_evaluations.json")) == document["candidate_evaluations"]
        assert json.loads(archive.read("candidate_evaluation_records.json")) == records
        assert json.loads(archive.read("research.json"))["candidate_evaluations"] == document["candidate_evaluations"]
        assert (INITIAL_CONCLUSION in archive.read("report.md").decode()) is bool(expected_count)
    settings = ScientistSettings(workspace=str(store.root),llm=LLMSettings(backend="none"))
    service = JobService(settings)
    with TestClient(create_app(settings,service=service)) as client:
        response = client.get(f"/api/projects/{run.project_id}/runs/{run.run_id}/results")
        assert response.status_code == 200 and response.json()["candidate_evaluations"] == document["candidate_evaluations"]
    assert run.model_dump_json() == before and path.read_bytes() == original_bytes
    return document


@pytest.mark.parametrize("initial",["supports","opposes"])
@pytest.mark.parametrize("added",["supports","opposes","mixed","context"])
@pytest.mark.parametrize("linked",[True,False])
def test_new_related_evidence_withdraws_current_conclusion_symmetrically_without_rewriting_history(tmp_path,initial,added,linked):
    run, old_record = evidence_change_case(initial=initial,added=added,linked=linked)
    result = assert_public_formats(persist_case(tmp_path,run),run,0)
    assert result["summary"]["candidate_assessment_status"] == "needs_review"
    assert run.candidate_evaluations[0].model_dump_json() == old_record
    assert run.candidate_evaluations[0].final is not None and len(run.candidate_evaluations[0].reviews) == 1


@pytest.mark.parametrize("linked",[True,False])
def test_truly_unrelated_followup_keeps_original_review(tmp_path,linked):
    run, old_record = evidence_change_case(linked=linked,unrelated=True)
    assert_public_formats(persist_case(tmp_path,run),run,1)
    assert run.candidate_evaluations[0].model_dump_json() == old_record


@pytest.mark.parametrize("kind,expected",[("ambiguous",0),("full_name",2),("canonical_id",2)])
def test_shared_code_abstains_but_explicit_unique_identity_can_be_reviewed(tmp_path,kind,expected):
    run = alias_case(kind)
    assert_public_formats(persist_case(tmp_path,run),run,expected)
    if not expected:
        assert all(record.final is None for record in run.candidate_evaluations)


def test_saved_fix1_ui_scenarios_have_consistent_current_cards_and_clickable_history():
    node = os.environ.get("SYNBIOSCOUT_TEST_NODE") or shutil.which("node")
    if not node: pytest.skip("Set SYNBIOSCOUT_TEST_NODE for the offline JS check")
    subprocess.run([node,str(Path(__file__).with_name("test_delivery_r2_fix1_ui.js"))],check=True,capture_output=True,text=True)


def test_fix1_preview_downloads_all_saved_cases_and_forbids_every_write():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("synthetic_r2_fix1_preview_test",root/"docs/nac_asd_iteration/preview_delivery_r2_fix1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.PreviewData()
    assert len(data.runs) == 10
    for run_id,document in data.runs.items():
        path = "/api/projects/p_synthetic_ui/runs/" + run_id + "/export"
        status,_,body = data.route("GET",path+"?format=json")
        assert status == 200
        assert json.loads(body)["candidate_evaluations"] == document["candidate_evaluations"]
        assert data.route("GET",path+"?format=../../private")[0] == 422
    for method in ("POST","PUT","PATCH","DELETE"):
        assert data.route(method,"/api/projects/p_synthetic_ui/runs")[0] == 403


