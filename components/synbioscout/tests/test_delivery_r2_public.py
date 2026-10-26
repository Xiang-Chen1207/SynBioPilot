"""SYNTHETIC independent assessment API/export tests; no real inference or HTTP."""

import csv
import importlib.util
import io
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scientist.api import create_app
from scientist.assessment_models import CandidateAssessmentProposal, CandidateAssessmentReviewDraft
from scientist.assessment_review import (
    BINDING_FIELDS,
    apply_assessment_review,
    bind_assessment_identities,
    public_validated_evaluations,
    store_assessment_proposals,
)
from scientist.co_models import AgentEvent, AgentState, CoScientistState
from scientist.exports import export_document, export_research, markdown_bytes, table_exports
from scientist.models import (
    CandidateRecord,
    EvidenceRecord,
    ExecutionStatus,
    LLMSettings,
    ResearchBrief,
    ScientistSettings,
    SourceRecord,
)
from scientist.presentation import result_document
from scientist.research_flow import source_observation_statement
from scientist.runtime import JobService
from scientist.store import ProjectStore, write_json

STAMP = "2026-10-16T00:00:00+08:00"
CONCLUSION = "SYNTHETIC sources report mixed findings within the recorded adjunctive endpoint. No benefit or delivery is established."


def evaluation_run(tmp_path):
    """Real store + real pure proposal/binding/review gates; responses are explicit fixtures."""
    store = ProjectStore(tmp_path / "SYNTHETIC_workspace")
    project_id, run_id = "p_synthetic_ui", "r_synthetic_delivery_r2"
    write_json(
        store.root / "projects" / project_id / "project.json",
        {"project_id": project_id, "name": "SYNTHETIC independent assessment", "created_at": STAMP},
    )
    brief = ResearchBrief(
        disease="SYNTHETIC condition",
        task_intent="candidate_assessment",
        research_mode="co_scientist",
        existing_candidates=["Illustrative compound"],
        phenotype="SYNTHETIC associated endpoint",
        population="SYNTHETIC adults",
        chassis_name="SYNTHETIC untested strain",
        chassis_id="",
        reference_chassis="",
        delivery_context="SYNTHETIC delivery unknown",
    )
    run = store.create_run(project_id, brief)
    run.run_id, run.created_at, run.updated_at = run_id, STAMP, STAMP
    run.status = ExecutionStatus.RUNNING
    write_json(
        store.project_dir(project_id) / "research" / run_id / "run.json",
        run.model_dump(mode="json"),
    )
    for i, polarity in enumerate(("mixed", "opposes")):
        quote = (
            "SYNTHETIC Illustrative compound showed mixed associated endpoint results."
            if i == 0
            else "SYNTHETIC Illustrative compound did not change the associated endpoint."
        )
        prefix = (
            "Methods: The trial recruited human volunteers to compare Illustrative compound with placebo. "
            if i == 0
            else "SYNTHETIC stored Results excerpt; study population is not established in this window. "
        )
        body = prefix + quote
        source = SourceRecord(
            source_id=f"synthetic:source_{i}",
            provider="europe_pmc",
            title=f"SYNTHETIC source {i}",
            url=f"https://example.invalid/synthetic/{i}",
            retrieved_at=STAMP,
            text=body,
            metadata={
                "synthetic": True,
                "local_excerpt_only": True,
                "original_evidence_scope": "full_text",
                "excerpt_start": 100,
                "excerpt_end": 100 + len(body),
                "acquisition_origin": "background_reuse",
                "raw_prompt": "PRIVATE_SENTINEL",
            },
        )
        run.sources.append(source)
        run.evidence.append(
            EvidenceRecord(
                evidence_id=f"e_synthetic_{i}",
                source_id=source.source_id,
                claim=quote,
                quote=quote,
                location="SYNTHETIC exact result sentence",
                polarity=polarity,
                validated_span=True,
                study_system="human volunteers",
                condition="SYNTHETIC adjunctive setting",
                endpoint="SYNTHETIC associated endpoint",
                limitations=["SYNTHETIC only; not a clinical result"],
            )
        )
    run.candidates = [
        CandidateRecord(
            candidate_id="c_synthetic",
            entity_id="m_synthetic",
            name="Illustrative compound",
            identity_status="resolved",
            identity_revision=2,
            smiles="CCO",
            inchikey="SYNTHETIC-IDENTITY",
            pubchem_cid="999999999",
            identity_source_ids=["synthetic:identity"],
            role_status="needs_review",
            decision="hold",
            rationale="SYNTHETIC legacy draft; not an independently reviewed conclusion",
            evidence_ids=[e.evidence_id for e in run.evidence],
            uncertainties=["SYNTHETIC old exposure uncertainty"],
        ),
        CandidateRecord(
            candidate_id="c_other",
            entity_id="m_other",
            name="Other SYNTHETIC compound",
            decision="hold",
        ),
    ]
    proposal = CandidateAssessmentProposal(
        candidate_name=run.candidates[0].name,
        conclusion=CONCLUSION,
        scientific_status="needs_evidence",
        findings=[
            {
                "finding_key": f"finding_{i}",
                "statement": source_observation_statement(e.source_id, e.quote),
                "evidence_keys": [e.evidence_id],
                "scope": {
                    key: getattr(e, key) for key in ("study_system", "condition", "endpoint")
                },
            }
            for i, e in enumerate(run.evidence)
        ],
        unknowns=[
            "SYNTHETIC mechanism, production, exposure and clinical applicability remain unknown"
        ],
        next_questions=["SYNTHETIC independently verify the narrower endpoint and exposure"],
    )
    records = store_assessment_proposals(
        run, [proposal], {e.evidence_id: e.evidence_id for e in run.evidence}
    )
    assert len(records) == 1
    record = records[0]
    assert bind_assessment_identities(run) == records, record.validation_errors
    assessment_ref = f"assessment:{record.assessment_id}:v{record.version}"
    operation_id = "llm_synthetic_review"
    run.co_scientist = CoScientistState(
        agents=[
            AgentState(
                agent_id="reflection",
                role="Reflection",
                status="completed",
                task="SYNTHETIC independent conclusion review",
                invocations=1,
            )
        ],
        events=[
            AgentEvent(
                event_id="event_synthetic_draft",
                sequence=1,
                agent_id="supervisor",
                kind="artifact",
                status="completed",
                title="SYNTHETIC candidate proposal and identity binding",
                summary="SYNTHETIC draft only",
                created_at=STAMP,
                artifact_ids=[assessment_ref],
                input_artifact_ids=[e.evidence_id for e in run.evidence],
            ),
            AgentEvent(
                event_id="event_synthetic_review",
                sequence=2,
                agent_id="reflection",
                kind="llm",
                status="completed",
                title="独立候选结论审查",
                summary="SYNTHETIC transparent review response; no paid model called",
                created_at=STAMP,
                operation_id=operation_id,
                operation_status="completed",
                input_artifact_ids=[assessment_ref],
                artifact_ids=["assessment_review:review_synthetic"],
            ),
        ],
    )
    review = {
        **{key: getattr(record, key) for key in BINDING_FIELDS},
        "review_id": "review_synthetic",
        "verdict": "accept",
        "reason": "SYNTHETIC bounded conclusion and every finding independently reviewed",
        "item_reviews": [
            {
                "finding_id": f.finding_id,
                "verdict": "accept",
                "evidence_ids": f.evidence_ids,
                "applicability": f.applicability,
                "reason": "SYNTHETIC exact source scope",
            }
            for f in record.draft.findings
        ],
        "supporting_evidence_ids": record.draft.supporting_evidence_ids,
        "opposing_evidence_ids": record.draft.opposing_evidence_ids,
    }
    apply_assessment_review(
        run, CandidateAssessmentReviewDraft(reviews=[review]), operation_id=operation_id
    )
    assert len(public_validated_evaluations(run)) == 1
    run.status = ExecutionStatus.PARTIAL
    run.usage.update(
        candidate_assessment_status="completed_review", hypothesis_expansion_status="not_run"
    )
    store.save_run(run)
    return store, run


def rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_nonempty_results_api_markdown_csv_zip_share_reviewed_conclusion_and_keep_history(tmp_path):
    store, run = evaluation_run(tmp_path)
    original = run.model_dump_json()
    path = store.run_dir(run.project_id, run.run_id) / "run.json"
    before = path.read_bytes()
    document, exported = result_document(run), export_document(run)
    evaluation = document["candidate_evaluations"][0]
    assert evaluation["findings"][0]["applicability"] == "source_scope_only"
    assert evaluation["findings"][1]["applicability"] == "population_uncertain"
    assert evaluation["findings"][1]["scope_limitations"]
    card = document["candidate_assessments"][0]
    assert (
        card["assessment_status"] == "completed_review"
        and card["scientific_status"] == "needs_evidence"
    )
    assert card["decision"] == "hold" and card["workflow_status"] == "partial"
    assert card["hypothesis_versions"] == [] and card["hypothesis_expansion_status"] == "not_run"
    assert document["candidate_assessments"][1]["validated_evaluation"] is None
    assert card["opposing_evidence_ids"] == ["e_synthetic_0", "e_synthetic_1"]
    assert "SYNTHETIC old exposure uncertainty" in " ".join(card["unknowns"])
    assert exported["candidate_evaluations"] == document["candidate_evaluations"]
    assert all(
        s["metadata"]["source_scope"]["original_window"] is None for s in exported["sources"]
    )
    tables = table_exports(run)
    for row in (rows(tables["candidates.csv"])[0], rows(tables["candidate_evaluations.csv"])[0]):
        assert row["conclusion"] == CONCLUSION and row["review_id"] == evaluation["review_id"]
        assert row["identity_fingerprint"] == evaluation["identity_fingerprint"]
    markdown = markdown_bytes(run).decode()
    assert (
        CONCLUSION in markdown
        and "completed_review / needs_evidence" in markdown
        and "假说扩展执行状态：未执行" in markdown
    )
    packed, _, _ = export_research(store, run, "zip")
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert archive.read("candidate_evaluations.csv") == tables["candidate_evaluations.csv"]
        assert (
            json.loads(archive.read("candidate_evaluations.json"))
            == document["candidate_evaluations"]
        )
        assert (
            json.loads(archive.read("research.json"))["candidate_evaluation_records"]
            == document["candidate_evaluation_records"]
        )
        assert CONCLUSION in archive.read("report.md").decode()
        assert b"PRIVATE_SENTINEL" not in b"".join(
            archive.read(name) for name in archive.namelist()
        )
    settings = ScientistSettings(workspace=str(store.root), llm=LLMSettings(backend="none"))
    service = JobService(settings)
    with TestClient(create_app(settings, service=service)) as client:
        response = client.get(f"/api/projects/{run.project_id}/runs/{run.run_id}/results")
        assert response.status_code == 200
        assert response.json()["candidate_evaluations"] == document["candidate_evaluations"]
    assert run.model_dump_json() == original and path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    ["identity", "task", "review_version", "operation", "duplicate_review", "missing_review"],
)
def test_stale_or_missing_review_never_projects_as_a_final_conclusion(tmp_path, change):
    _, run = evaluation_run(tmp_path)
    record = run.candidate_evaluations[0]
    if change == "identity":
        run.candidates[0].identity_revision += 1
    elif change == "task":
        run.brief.phenotype = "SYNTHETIC another endpoint"
    elif change == "review_version":
        record.reviews[0].version += 1
    elif change == "operation":
        run.co_scientist.events[-1].operation_status = "failed"
    elif change == "duplicate_review":
        record.reviews.append(record.reviews[0].model_copy(deep=True))
    else:
        record.reviews = []
    original = run.model_dump_json()
    document = result_document(run)
    assert document["candidate_evaluations"] == []
    assert document["candidate_assessments"][0]["validated_evaluation"] is None
    assert document["candidate_evaluation_records"][0]["final"] is None
    assert document["summary"]["candidate_assessment_status"] == "needs_review"
    assert CONCLUSION not in markdown_bytes(run).decode()
    assert rows(table_exports(run)["candidate_evaluations.csv"]) == []
    assert run.model_dump_json() == original


def test_legacy_rationale_and_accepted_quotes_do_not_invent_independent_review(tmp_path):
    _, run = evaluation_run(tmp_path)
    run.candidate_evaluations = []
    result = result_document(run)
    assert result["candidate_evaluation_records"] == result["candidate_evaluations"] == []
    assert result["candidate_assessments"][0]["assessment_status"] == "unreviewed"


def test_python_and_js_project_same_nonempty_scopes_receipts_and_event_targets(tmp_path):
    _, run = evaluation_run(tmp_path)
    node = os.environ.get("SYNBIOSCOUT_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for explicit cross-language UI contract")
    payload = tmp_path / "SYNTHETIC_public.json"
    payload.write_text(json.dumps({"runs": [result_document(run)]}, ensure_ascii=False))
    subprocess.run(
        [node, str(Path(__file__).with_name("test_delivery_r2_ui.js")), str(payload)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_preview_is_read_only_and_reads_only_saved_synthetic_exports():
    root = Path(__file__).resolve().parents[1]
    path = root / "docs/nac_asd_iteration/preview_delivery_r2.py"
    spec = importlib.util.spec_from_file_location("synthetic_r2_preview_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.PreviewData()
    assert (
        data.route("GET", "/api/projects/p_synthetic_ui/runs/r_synthetic_delivery_r2/results")[0]
        == 200
    )
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert data.route(method, "/api/projects/p_synthetic_ui/runs")[0] == 403
    status, _, body = data.route(
        "GET", "/api/projects/p_synthetic_ui/runs/r_synthetic_delivery_r2/export?format=json"
    )
    assert status == 200 and json.loads(body)["candidate_evaluations"]
    assert (
        data.route(
            "GET",
            "/api/projects/p_synthetic_ui/runs/r_synthetic_delivery_r2/export?format=../../secret",
        )[0]
        == 422
    )

