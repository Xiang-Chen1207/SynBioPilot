"""Offline demo/read-only API checks; never launch research or model inference."""
import json
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from scientist.api import create_app
from scientist.demo_handoff import target_screening_checklist
from scientist.models import ResearchBrief, ScientistSettings, TargetPackage
from scientist.store import ProjectStore


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Demo must not make real HTTP requests or execute a research job")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    store = ProjectStore(tmp_path / "workspace")
    service = SimpleNamespace(store=store, shutdown=lambda: None, submit_research=forbidden,
                              submit_compute=forbidden)
    with TestClient(create_app(ScientistSettings(), service=service)) as client:
        yield client, store


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_demo_reference_does_not_create_projects_or_execution(demo_client):
    client, store = demo_client
    before = snapshot(store.root)
    response = client.get("/api/demos")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["demos"][0]["id"] == "hdac2_h20"
    card = client.get("/api/demos/hdac2_h20").json()
    assert card["local_execution"]["status"] == "not_run"
    assert client.get("/api/demos/not-a-demo").status_code == 404
    assert before == snapshot(store.root)


def test_demo_script_is_served_at_the_html_relative_url(demo_client):
    client, _ = demo_client
    response = client.get("/hdac2-demo.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "Hdac2Demo" in response.text
    assert '<script src="hdac2-demo.js" defer>' in client.get("/").text


def test_stateless_import_never_modifies_research_or_saves_csv(demo_client):
    client, store = demo_client
    pid = store.create_project("SYNTHETIC demo API test")["project_id"]
    store.create_run(pid, ResearchBrief(disease="SYNTHETIC"))
    before = snapshot(store.root)
    payload = {"csv_text": "model,id,value,state\nDrugCLIP,SYNTHETIC_A,0.1,ok\nSPRINT,SYNTHETIC_A,,failed\n",
               "columns": {"model": "model", "compound_id": "id", "score": "value", "status": "state"}}
    response = client.post("/api/demos/hdac2_h20/inspect-scores", json=payload)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    report = response.json()
    assert report["accepted"] == 2 and report["summary"]["failed"] == 1
    assert report["provenance"]["kind"] == "user_imported_not_verified"
    assert report["local_execution"]["status"] == "not_run"
    assert before == snapshot(store.root)
    json.dumps(report, allow_nan=False)
    assert client.post("/api/demos/unknown/inspect-scores", json=payload).status_code == 404


def test_demo_import_requires_explicit_typed_mapping(demo_client):
    client, _ = demo_client
    for columns in ({}, {"model": "m", "compound_id": "i", "score": "s", "status": "t", "url": "evil"}):
        assert client.post("/api/demos/hdac2_h20/inspect-scores", json={
            "csv_text": "x\n", "columns": columns}).status_code == 422
    assert client.post("/api/demos/hdac2_h20/inspect-scores", json={
        "csv_text": "x" * 500_001,
        "columns": {"model": "m", "compound_id": "i", "score": "s", "status": "t"},
    }).status_code == 422
    response = client.post("/api/demos/hdac2_h20/inspect-scores", json={
        "csv_text": "wrong\n", "columns": {"model": "m", "compound_id": "i", "score": "s", "status": "t"},
    })
    assert response.status_code == 200 and response.json()["status"] == "invalid"


def test_cross_origin_demo_post_is_rejected(demo_client):
    client, _ = demo_client
    assert client.post("/api/demos/hdac2_h20/inspect-scores", json={},
                       headers={"origin": "https://example.invalid"}).status_code == 403


@pytest.mark.parametrize("payload", [
    {"csv_text": "SYNTHETIC_PRIVATE_INPUT" * 30_000,
     "columns": {"model": "m", "compound_id": "i", "score": "s", "status": "t"}},
    {"csv_text": "SYNTHETIC_PRIVATE_INPUT", "columns": "SYNTHETIC_PRIVATE_INPUT"},
    {"csv_text": "SYNTHETIC_PRIVATE_INPUT", "columns": {"SYNTHETIC_PRIVATE_INPUT": "private"}},
    {"csv_text": {"SYNTHETIC_PRIVATE_INPUT": "private"}, "columns": {}},
])
def test_demo_validation_response_never_echoes_input_or_unknown_keys(demo_client, payload):
    client, _ = demo_client
    response = client.post("/api/demos/hdac2_h20/inspect-scores", json=payload)
    assert response.status_code == 422
    assert "SYNTHETIC_PRIVATE_INPUT" not in response.text
    assert "input" not in response.json()["detail"][0]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("diagnostic", [
    "Cannot read /mnt/shared/private.csv", "Cannot read /opt/shared/input.csv",
    "Cannot read ./data/private.csv", "Cannot read ../data/private.csv",
    "Cannot read data/private.csv", r"Cannot read \\server\share\private.csv",
    "Cannot read '/mnt/my folder/private.csv'",
    r"Cannot read 'C:\work\study.csv'", "path:/mnt/shared/input.csv",
])
def test_demo_error_diagnostics_hide_absolute_relative_and_unc_paths(demo_client, diagnostic):
    client, _ = demo_client
    response = client.post("/api/demos/hdac2_h20/inspect-scores", json={
        "csv_text": "model,id,score,status,error\nPSICHIC,SYNTHETIC,,failed," + diagnostic + "\n",
        "columns": {"model": "model", "compound_id": "id", "score": "score",
                    "status": "status", "error": "error"},
    })
    assert response.status_code == 200 and diagnostic not in response.text
    row = next(group for group in response.json()["by_model"] if group["model"] == "PSICHIC")["rows"][0]
    assert row["error_redacted"] is True and row["score"] is None


def make_package(root):
    paths = {}
    for kind in ("protein_pdb", "sequence_fasta", "reference_ligand_sdf"):
        file = root / kind
        file.write_text("SYNTHETIC presence fixture, not valid structure\n")
        paths[kind] = str(file)
    return TargetPackage(package_id="pkg_synthetic", status="ready", chain="A", **paths,
                         model_readiness={m: {"ready": True} for m in
                                          ("drugclip", "sprint", "psichic", "pharmaconet")})


def test_checklist_only_checks_presence_not_caches_or_structure(tmp_path):
    package = make_package(tmp_path)
    before = package.model_dump()
    result = target_screening_checklist(package, tmp_path)
    assert result["all_inputs_recorded_ready"] is True
    assert result["demo_cache_compatibility"] == "not_checked"
    assert all(m["execution"] == "not_run" and m["features"] == "not_checked" for m in result["models"])
    assert str(tmp_path) not in json.dumps(result)
    assert package.model_dump() == before


@pytest.mark.parametrize("kind", ["protein_pdb", "sequence_fasta", "reference_ligand_sdf"])
def test_checklist_missing_or_foreign_files_not_ready(tmp_path, kind):
    scoped = tmp_path / "project"
    scoped.mkdir()
    package = make_package(scoped)
    setattr(package, kind, str(tmp_path / "outside"))
    (tmp_path / "outside").write_text("do not read me")
    result = target_screening_checklist(package, scoped)
    assert not result["all_inputs_recorded_ready"]
    assert next(f for f in result["files"] if f["kind"] == kind)["present"] is False


def test_checklist_symlinks_cannot_escape_project(tmp_path):
    scoped = tmp_path / "project"
    scoped.mkdir()
    package = make_package(scoped)
    outside = tmp_path / "external"
    outside.write_text("not public")
    link = scoped / "link"
    link.symlink_to(outside)
    package.protein_pdb = str(link)
    assert target_screening_checklist(package, scoped)["all_inputs_recorded_ready"] is False


def test_legacy_ready_package_does_not_invent_model_checks(tmp_path):
    package = make_package(tmp_path)
    package.model_readiness = {}
    result = target_screening_checklist(package, tmp_path)
    assert not result["all_inputs_recorded_ready"]
    package.model_readiness = {"drugclip": {"ready": "true"}}
    assert not target_screening_checklist(package, tmp_path)["all_inputs_recorded_ready"]


def test_scoped_checklist_endpoint_is_read_only_and_not_file_route(demo_client):
    client, store = demo_client
    pid = store.create_project("SYNTHETIC checklist")["project_id"]
    run = store.create_run(pid, ResearchBrief(disease="SYNTHETIC"))
    run.target_packages = [make_package(store.run_dir(pid, run.run_id))]
    store.save_run(run)
    before = snapshot(store.root)
    base = f"/api/projects/{pid}/runs/{run.run_id}/target-packages"
    response = client.get(base + "/pkg_synthetic/screening-checklist")
    assert response.status_code == 200
    assert response.json()["scope"] == "recorded_readiness_and_local_file_presence_only"
    assert response.headers["cache-control"] == "no-store"
    assert client.get(base + "/foreign_pkg/screening-checklist").status_code == 404
    assert client.get(base + "/pkg_synthetic/protein_pdb").status_code == 200
    assert before == snapshot(store.root)


def test_isolated_preview_uses_real_inspector_but_blocks_execution(tmp_path):
    from scripts.preview_hdac2_workbench import preview_app
    with TestClient(preview_app(tmp_path / "isolated")) as client:
        assert "只读演示预览" in client.get("/").text
        assert client.get("/api/projects").json() == []
        assert client.post("/api/projects", json={"name": "must not be saved"}).status_code == 403
        assert client.post("/api/projects/p_test/runs", json={}).status_code == 403
        assert client.get("/hdac2-demo.js").status_code == 200
        report = client.post("/api/demos/hdac2_h20/inspect-scores", json={
            "csv_text": "m,i,s,t\nDrugCLIP,SYNTHETIC,1,success\n",
            "columns": {"model": "m", "compound_id": "i", "score": "s", "status": "t"},
        }).json()
        assert report["accepted"] == 1
        assert report["provenance"]["target_association"] == "not_verified"
        assert client.get("/api/projects").json() == []

