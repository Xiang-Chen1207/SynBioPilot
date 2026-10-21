"""Local API contract tests; no model, database, GPU or external job is invoked."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from scientist import api, runtime
from scientist.models import (
    CandidateRecord,
    ExecutionStatus,
    ExperimentSpec,
    LLMSettings,
    ScientistSettings,
    TargetPackage,
)
from scientist.store import write_json
from target2lead.common import ROOT, load_config


class RecordingExecutor:
    def __init__(self):
        self.calls = []
        self.pending = []

    def submit(self, fn, *args, **kwargs):
        task = (fn, args, kwargs)
        self.calls.append(task)
        self.pending.append(task)
        return SimpleNamespace()

    def dispatch_next(self):
        fn, args, kwargs = self.pending.pop(0)
        return fn(*args, **kwargs)

    def shutdown(self, **kwargs):
        pass


class FakeEngine:
    def __init__(self):
        self.store = None
        self.calls = []
        self.on_running = None

    def execute(self, project_id, run_id, **kwargs):
        self.calls.append((project_id, run_id, kwargs))
        run = self.store.load_run(project_id, run_id)
        run.status = ExecutionStatus.RUNNING
        self.store.save_run(run)
        if self.on_running:
            self.on_running(project_id, run_id)
        run.status = ExecutionStatus.COMPLETED
        directory = self.store.run_dir(project_id, run_id)
        (directory / "report.md").write_text("# SYNTHETIC offline API fixture\n")
        run.report_path = str(
            (directory / "report.md").relative_to(self.store.project_dir(project_id))
        )
        self.store.save_run(run)
        return run


def test_lightweight_catalogs_exclude_heavy_compute(workbench):
    client = workbench.client
    response = client.get("/api/research-tools")
    assert response.status_code == 200
    catalog = response.json()["tools"]
    assert len(catalog) == 8 and all(t["read_only"] for t in catalog)
    assert {"chembl", "reactome_pathways", "quickgo_annotations"} <= {t["name"] for t in catalog}
    assert not {"drugclip", "docking", "md", "admet"}.intersection(t["name"] for t in catalog)
    contracts = client.get("/api/research-protocols").json()["protocols"]
    assert len(contracts) == 6 and all(c["stop_conditions"] for c in contracts)


def test_co_mode_default_does_not_request_target_preparation(workbench):
    client, service = workbench.client, workbench.service
    pid = project(workbench)
    response = client.post(f"/api/projects/{pid}/runs", json={
        "brief": {"disease": "offline fixture", "research_mode": "co_scientist"}})
    assert response.status_code == 202
    service.executor.dispatch_next()
    assert service.engine.calls[-1][2]["prepare_targets"] is False


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline API tests must not contact a service or launch a subprocess")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(runtime.subprocess, "run", forbidden)
    monkeypatch.setattr(runtime.subprocess, "Popen", forbidden)
    cfg = load_config(ROOT / "configs/keap1.json")
    cfg["run"]["output"] = str(tmp_path / "unused-base-output")
    cfg["run"]["python"] = sys.executable
    cfg["md"]["prepared_systems"] = {}
    base = tmp_path / "base.json"
    write_json(base, cfg)
    settings = ScientistSettings(
        workspace=str(tmp_path / "workspace"),
        target2lead_config=str(base),
        llm=LLMSettings(backend="none"),
    )
    engine = FakeEngine()
    service = runtime.JobService(settings, recover=False, engine=engine)
    engine.store = service.store
    service.executor.shutdown(wait=False)
    executor = RecordingExecutor()
    service.executor = executor
    app = api.create_app(settings, service=service)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            service=service,
            store=service.store,
            engine=engine,
            executor=executor,
            settings=settings,
            config=cfg,
        )


def project(workbench, name="SYNTHETIC API fixture project"):
    response = workbench.client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["project_id"]


def queued_run(workbench, project_id, **kwargs):
    body = {
        "brief": {"disease": "SYNTHETIC offline fixture disease"},
        "prepare_targets": False,
        **kwargs,
    }
    response = workbench.client.post(f"/api/projects/{project_id}/runs", json=body)
    assert response.status_code == 202
    return response.json()["run_id"]


def completed_run(workbench, project_id):
    run_id = queued_run(workbench, project_id)
    workbench.executor.dispatch_next()
    return run_id


def experiment_body(*, qc_passed=True, research_run_id="", candidate_id="", **kwargs):
    spec = ExperimentSpec(
        name="SYNTHETIC illustrative API assay",
        kind="concentration",
        endpoint="Recorded assay signal",
        unit="mg/L",
        qc_passed=qc_passed,
        research_run_id=research_run_id,
        candidate_id=candidate_id,
        condition="SYNTHETIC offline test condition",
        **kwargs,
    )
    return {
        "csv_text": "group,biological_replicate,technical_replicate,value,unit\n"
        "sample,s1,a,1,mg/L\nsample,s1,b,3,mg/L\nsample,s2,a,4,mg/L\n",
        "spec": spec.model_dump(mode="json"),
    }


def upload(workbench, project_id, body=None):
    return workbench.client.post(
        f"/api/projects/{project_id}/experiments", json=body or experiment_body()
    )


def test_health_home_and_project_creation(workbench):
    client = workbench.client
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mode"] == "local_research_not_clinical"
    home = client.get("/")
    assert home.status_code == 200
    assert "text/html" in home.headers["content-type"]
    assert "<html" in home.text.lower()
    assert client.get("/api/projects").json() == []
    pid = project(workbench)
    assert client.get("/api/projects").json()[0]["project_id"] == pid
    assert client.get(f"/api/projects/{pid}/runs").json() == []
    assert client.get(f"/api/projects/{pid}/memory").json() == []
    assert client.get("/api/doctor").json()["llm"]["backend"] == "none"


def test_async_research_status_is_queued_running_then_completed(workbench):
    pid = project(workbench)
    rid = queued_run(workbench, pid)
    endpoint = f"/api/projects/{pid}/runs/{rid}"
    assert workbench.engine.calls == []
    assert workbench.client.get(endpoint).json()["status"] == "queued"
    running_states = []
    workbench.engine.on_running = lambda p, r: running_states.append(
        workbench.client.get(f"/api/projects/{p}/runs/{r}").json()["status"]
    )
    workbench.executor.dispatch_next()
    assert running_states == ["running"]
    completed = workbench.client.get(endpoint).json()
    assert completed["status"] == "completed"
    assert workbench.client.get(f"/api/projects/{pid}/runs").json()[0]["run_id"] == rid
    assert workbench.engine.calls[0][2]["prepare_targets"] is False
    report = workbench.client.get(f"/api/projects/{pid}/artifacts/{completed['report_path']}")
    assert report.status_code == 200
    assert "SYNTHETIC" in report.text


def test_cross_origin_mutations_are_rejected_before_writes(workbench):
    response = workbench.client.post(
        "/api/projects",
        json={"name": "must not be created"},
        headers={"origin": "https://untrusted.example"},
    )
    assert response.status_code == 403
    assert workbench.store.list_projects() == []
    same_origin = workbench.client.post(
        "/api/projects",
        json={"name": "same origin fixture"},
        headers={"origin": "http://testserver"},
    )
    assert same_origin.status_code == 201
    assert len(workbench.store.list_projects()) == 1


def test_declared_oversized_request_is_rejected(workbench):
    response = workbench.client.post(
        "/api/projects", json={"name": "oversized fixture"}, headers={"content-length": "3000001"}
    )
    assert response.status_code == 413
    assert workbench.store.list_projects() == []


def test_artifact_path_cannot_escape_project_or_export_hidden_files(workbench, tmp_path):
    pid = project(workbench)
    outside = tmp_path / "outside-private.txt"
    outside.write_text("SYNTHETIC private fixture content must not escape its scope")
    directory = workbench.store.project_dir(pid)
    (directory / "escape.txt").symlink_to(outside)
    (directory / ".hidden.txt").write_text("SYNTHETIC hidden content")
    for relative in ["%2e%2e%2f%2e%2e%2f%2e%2e%2foutside-private.txt", "escape.txt"]:
        response = workbench.client.get(f"/api/projects/{pid}/artifacts/{relative}")
        assert response.status_code == 404
        assert outside.read_text() not in response.text
    hidden = workbench.client.get(f"/api/projects/{pid}/artifacts/.hidden.txt")
    assert hidden.status_code == 422


def test_real_experiment_upload_keeps_no_control_outcome_unclassified(workbench):
    pid = project(workbench)
    response = upload(workbench, pid)
    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "measured"
    assert result["outcome"] == "unclassified"
    assert result["groups"][0]["n_biological"] == 2
    assert result["groups"][0]["mean"] == 3
    assert result["condition_scope"]["condition"] == "SYNTHETIC offline test condition"
    eid = result["experiment_id"]
    listed = workbench.client.get(f"/api/projects/{pid}/experiments").json()
    assert listed[0]["experiment_id"] == eid
    assert Path(result["artifacts"]["plot"]).is_file()
    artifact = workbench.client.get(f"/api/projects/{pid}/artifacts/experiments/{eid}/summary.json")
    assert artifact.status_code == 200
    assert artifact.json() == result
    memory = workbench.client.get(f"/api/projects/{pid}/memory").json()[0]
    assert memory["kind"] == "measured_assay"
    assert memory["verified"] is False
    assert memory["metadata"]["experiment_id"] == eid


def test_qc_failed_experiment_is_recorded_without_promoting_candidate(workbench):
    pid = project(workbench)
    rid = completed_run(workbench, pid)
    run = workbench.store.load_run(pid, rid)
    run.candidates = [
        CandidateRecord(
            candidate_id="fixture_candidate",
            name="SYNTHETIC compound",
            identity_status="resolved",
            decision="hold",
        )
    ]
    workbench.store.save_run(run)
    response = upload(
        workbench,
        pid,
        experiment_body(
            qc_passed=False,
            research_run_id=rid,
            candidate_id="fixture_candidate",
            detection_limit=10,
        ),
    )
    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "qc_failed"
    assert result["outcome"] == "unclassified"
    persisted = workbench.client.get(f"/api/projects/{pid}/runs/{rid}").json()
    assert persisted["candidates"][0]["decision"] == "hold"
    assert any(result["experiment_id"] in item for item in persisted["warnings"])
    memory = workbench.client.get(f"/api/projects/{pid}/memory").json()[0]
    assert memory["metadata"]["qc_passed"] is False
    assert memory["source_run_id"] == rid


def test_invalid_experiment_returns_422_and_diagnostics_without_evidence_memory(workbench):
    pid = project(workbench)
    body = experiment_body()
    body["csv_text"] = (
        "group,biological_replicate,value,unit\nsample,s1,1,mg/L\nsample,s2,NaN,mg/L\n"
    )
    response = upload(workbench, pid, body)
    assert response.status_code == 422
    result = response.json()
    assert result["status"] == "invalid_data"
    assert result["n_input_rows"] == 2
    assert result["n_aggregated_rows"] is None
    assert any("Non-finite" in error for error in result["errors"])
    assert workbench.client.get(f"/api/projects/{pid}/memory").json() == []
    assert Path(result["artifacts"]["input"]).read_text() == body["csv_text"]
    assert json.loads(Path(result["artifacts"]["summary"]).read_text())["status"] == "invalid_data"


def test_experiment_rejects_wrong_candidate_and_pending_research(workbench):
    pid = project(workbench)
    rid = queued_run(workbench, pid)
    response = upload(workbench, pid, experiment_body(research_run_id=rid))
    assert response.status_code == 422
    assert "Research is running" in response.json()["detail"]
    workbench.executor.dispatch_next()
    response = upload(
        workbench, pid, experiment_body(research_run_id=rid, candidate_id="foreign_candidate")
    )
    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]
    assert workbench.client.get(f"/api/projects/{pid}/experiments").json() == []


def test_followup_accepts_project_feedback_and_rejects_cross_project_feedback(workbench):
    pid = project(workbench)
    rid = completed_run(workbench, pid)
    eid = upload(workbench, pid).json()["experiment_id"]
    other_pid = project(workbench, "SYNTHETIC other project")
    other_eid = upload(workbench, other_pid).json()["experiment_id"]
    endpoint = f"/api/projects/{pid}/runs/{rid}/followup"
    rejected = workbench.client.post(endpoint, json={"experiment_ids": [other_eid]})
    assert rejected.status_code == 422
    assert "does not belong to this project" in rejected.json()["detail"]
    assert len(workbench.store.list_runs(pid)) == 1
    accepted = workbench.client.post(
        endpoint,
        json={
            "experiment_ids": [eid, eid],
            "question": "SYNTHETIC follow-up question",
            "prepare_targets": False,
        },
    )
    assert accepted.status_code == 202
    child = accepted.json()
    assert child["status"] == "queued"
    assert child["parent_run_id"] == rid
    assert child["brief"]["experiment_ids"] == [eid]
    assert (
        "Follow-up question (not evidence): SYNTHETIC follow-up question"
        in child["brief"]["assumptions"]
    )
    assert workbench.store.load_run(pid, rid).brief.experiment_ids == []
    foreign_parent = workbench.client.post(
        f"/api/projects/{other_pid}/runs/{rid}/followup", json={}
    )
    assert foreign_parent.status_code == 404


def test_compute_preflight_block_persists_diagnostics_without_launch(workbench, monkeypatch):
    pid = project(workbench)
    rid = completed_run(workbench, pid)
    run = workbench.store.load_run(pid, rid)
    run.target_packages = [
        TargetPackage(
            package_id="fixture_ready_package",
            status="ready",
            chain="A",
            **{
                key: workbench.config["target"][key]
                for key in ("protein_pdb", "sequence_fasta", "reference_ligand_sdf")
            },
        )
    ]
    workbench.store.save_run(run)
    handoff = workbench.client.post(
        f"/api/projects/{pid}/compute",
        json={"run_id": rid, "package_id": "fixture_ready_package", "max_md_jobs": 1},
    )
    assert handoff.status_code == 201
    job = handoff.json()
    assert job["status"] == "handoff_ready"
    missing = {
        "ready": False,
        "checks": [{"name": "fixture.missing_checkpoint", "ready": False}],
        "warnings": [],
        "stages": ["prepare", "drugclip"],
    }
    monkeypatch.setattr(runtime, "compute_preflight", lambda *args, **kwargs: missing)
    monkeypatch.setattr(api, "compute_preflight", lambda *args, **kwargs: missing)
    endpoint = f"/api/projects/{pid}/compute/{job['compute_id']}"
    assert workbench.client.get(endpoint + "/preflight").json() == missing
    before = len(workbench.executor.calls)
    launched = workbench.client.post(endpoint + "/run", json={"until": "md", "resume": False})
    assert launched.status_code == 202
    blocked = launched.json()
    assert blocked["status"] == "blocked"
    assert blocked["preflight"] == missing
    assert len(workbench.executor.calls) == before
    assert workbench.client.get(endpoint).json()["job"]["status"] == "blocked"
    assert workbench.client.get(endpoint).json()["results"]["status"] == "blocked"
    assert workbench.client.get(f"/api/projects/{pid}/compute").json()[0]["status"] == "blocked"
    assert (Path(job["config_path"]).parent / "preflight.json").is_file()
    assert not (Path(job["config_path"]).parent / "worker.log").exists()


@pytest.mark.parametrize(
    "suffix", ["runs", "memory", "experiments", "compute", "artifacts/project.json"]
)
def test_unknown_project_resources_return_404(workbench, suffix):
    response = workbench.client.get(f"/api/projects/p_missing/{suffix}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Resource not found"


def test_unknown_run_compute_and_artifact_return_404(workbench):
    pid = project(workbench)
    for suffix in [
        "runs/r_missing",
        "compute/c_missing",
        "compute/c_missing/preflight",
        "artifacts/missing.txt",
    ]:
        response = workbench.client.get(f"/api/projects/{pid}/{suffix}")
        assert response.status_code == 404
    foreign_parent = workbench.client.post(
        f"/api/projects/{pid}/runs",
        json={
            "brief": {"disease": "SYNTHETIC fixture"},
            "parent_run_id": "r_missing",
            "prepare_targets": False,
        },
    )
    assert foreign_parent.status_code == 404
    assert workbench.store.list_runs(pid) == []

