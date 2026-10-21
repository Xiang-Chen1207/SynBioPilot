"""Product views and downloads use real fixture records, never synthetic efficacy."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scientist import exports
from scientist.api import create_app
from scientist.exports import export_research, spreadsheet_safe
from scientist.models import (
    CandidateRecord,
    EvidenceRecord,
    ExecutionStatus,
    LLMSettings,
    ResearchBrief,
    ScientistSettings,
    SourceRecord,
    TargetPackage,
)
from scientist.presentation import result_document
from scientist.runtime import JobService


@pytest.fixture
def workbench(tmp_path):
    settings = ScientistSettings(workspace=str(tmp_path / "workspace"), llm=LLMSettings(backend="none"))
    service = JobService(settings)
    store = service.store
    project = store.create_project("SYNTHETIC product contract fixture")
    run = store.create_run(project["project_id"], ResearchBrief(disease="SYNTHETIC disease"))
    run.status = ExecutionStatus.PARTIAL
    run.sources = [SourceRecord(source_id="fixture_source", provider="local_fixture", title="SYNTHETIC",
        url="https://example.org/fixture", retrieved_at=run.created_at,
        text="A biomarker was associated with the condition. No intervention was tested.")]
    run.evidence = [EvidenceRecord(evidence_id="ev_marker", claim="A biomarker was associated with the condition",
        source_id="fixture_source", quote="A biomarker was associated with the condition.", location="fixture",
        polarity="context", validated_span=True),
        EvidenceRecord(evidence_id="ev_intervention", claim="SYNTHETIC intervention fixture only",
            source_id="fixture_source", quote="No intervention was tested.", location="fixture",
            polarity="context", validated_span=True)]
    run.candidates = [CandidateRecord(candidate_id="marker", name="=FORMULA_FIXTURE()",
        identity_status="resolved", evidence_ids=["ev_marker"], routes=["literature"],
        rationale="biomarker association; no intervention"),
        CandidateRecord(candidate_id="intervention", name="SYNTHETIC candidate fixture",
            role="therapeutic_candidate", role_status="model_reviewed", role_evidence_ids=["ev_intervention"],
            identity_status="resolved", evidence_ids=["ev_intervention"], routes=["literature"])]
    directory = store.run_dir(run.project_id, run.run_id)
    target = directory / "target"
    target.mkdir()
    pdb = target / "protein.pdb"
    pdb.write_text("SYNTHETIC fixture content; not a real protein")
    secret = directory / ".env"
    secret.write_text("FIXTURE_TOKEN=never-export-this")
    (directory / "inference").mkdir()
    (directory / "inference" / "private.log").write_text("private fixture token")
    outside = tmp_path / "outside.sdf"
    outside.write_text("outside fixture")
    run.target_packages = [TargetPackage(package_id="fixture_package", status="needs_review",
        protein_pdb=str(pdb), reference_ligand_sdf=str(outside))]
    store.save_run(run)
    with TestClient(create_app(settings, service=service)) as client:
        yield client, store, run, pdb


def prefix(run):
    return f"/api/projects/{run.project_id}/runs/{run.run_id}"


def test_result_view_separates_markers_and_preserves_original(workbench):
    client, store, run, _ = workbench
    before = (store.run_dir(run.project_id, run.run_id) / "run.json").read_bytes()
    result = client.get(prefix(run) + "/results")
    assert result.status_code == 200
    payload = result.json()
    assert payload["summary"]["therapeutic_candidate_count"] == 1
    assert payload["summary"]["other_molecule_count"] == 1
    assert payload["candidates"][0]["role"] == "biomarker"
    assert payload["candidates"][0]["role_status"] == "legacy_inferred"
    assert payload["candidates"][0]["decision"] == "hold"
    assert payload["presentation"]["original_record_unchanged"] is True
    assert (store.run_dir(run.project_id, run.run_id) / "run.json").read_bytes() == before


def test_role_review_cannot_use_another_candidates_evidence(workbench):
    _, _, run, _ = workbench
    run.candidates[1].role_evidence_ids = ["ev_marker"]
    payload = result_document(run)
    assert payload["summary"]["reviewed_therapeutic_count"] == 0
    assert payload["candidates"][1]["role"] == "unknown"
    assert payload["candidates"][1]["role_status"] == "needs_review"


@pytest.mark.parametrize("format_name,mime", [("markdown", "text/markdown"), ("json", "application/json"),
                                             ("csv", "text/csv"), ("zip", "application/zip")])
def test_export_formats_are_downloadable(workbench, format_name, mime):
    client, _, run, _ = workbench
    response = client.get(prefix(run) + "/export", params={"format": format_name})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(mime)
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert response.content


def test_zip_is_allowlisted_portable_and_fingerprinted(workbench):
    client, _, run, pdb = workbench
    data = client.get(prefix(run) + "/export", params={"format": "zip"}).content
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert "report.md" in names and "candidates.csv" in names and "evidence.csv" in names
        assert all(".env" not in name and "inference" not in name and "outside" not in name for name in names)
        exported = json.loads(archive.read("research.json"))
        package = exported["target_packages"][0]
        assert package["protein_pdb"] == "target_files/fixture_package/protein.pdb"
        assert package["reference_ligand_sdf"] == ""
        assert archive.read(package["protein_pdb"]) == pdb.read_bytes()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["omitted_files"][0]["reason"] == "file_unavailable_or_outside_project"
        for name, expected in manifest["file_hashes"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
        assert b"never-export-this" not in b"".join(archive.read(name) for name in names)


@pytest.mark.parametrize("value", ["=HYPERLINK(1)", "+cmd", "-cmd", "@sum", "\tcommand"])
def test_csv_escapes_formula_strings(value):
    assert spreadsheet_safe(value).startswith("'")


def test_csv_escapes_model_or_user_text(workbench):
    client, _, run, _ = workbench
    response = client.get(prefix(run) + "/export", params={"format": "csv"})
    assert "'=FORMULA_FIXTURE()" in response.content.decode("utf-8-sig")


def test_unknown_export_format_and_missing_resources_are_rejected(workbench):
    client, store, run, _ = workbench
    assert client.get(prefix(run) + "/export", params={"format": "secret"}).status_code == 422
    assert client.get(f"/api/projects/{run.project_id}/runs/missing/export").status_code == 404
    with pytest.raises(ValueError, match="Export format"):
        export_research(store, run, "unsupported")


def test_health_identifies_native_product(workbench):
    client, _, _, _ = workbench
    value = client.get("/api/health").json()
    assert value["service"] == "synbioscout_ai_scientist"
    from scientist import __version__
    assert value["version"] == __version__
    assert len(value["workspace_id"]) == 16


def test_zip_metadata_redacts_host_paths_without_changing_history(workbench):
    client, store, run, _ = workbench
    run.target_packages[0].metadata = {"sources": [{"raw_file": "/Users/private-owner/project/raw/a.json",
        "url": "https://example.org/source", "sha256": "fixturehash"}]}
    store.save_run(run)
    data = client.get(prefix(run) + "/export", params={"format": "zip"}).content
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        data = archive.read("target_files/fixture_package/metadata.json")
        assert b"/Users/private-owner" not in data
        assert b"https://example.org/source" in data and b"fixturehash" in data
        assert b"/Users/private-owner" not in archive.read("research.json")
    assert store.load_run(run.project_id, run.run_id).target_packages[0].metadata["sources"][0]["raw_file"].startswith("/Users/")


def test_archive_limit_counts_metadata_and_core_records(workbench, monkeypatch):
    client, store, run, _ = workbench
    run.target_packages[0].metadata = {"large_fixture": "x" * 30_000}
    store.save_run(run)
    monkeypatch.setattr(exports, "MAX_ARCHIVE_INPUT_BYTES", 15_000)
    response = client.get(prefix(run) + "/export", params={"format": "zip"})
    assert response.status_code == 422
    assert "total export limit" in response.json()["detail"]


def test_export_keeps_evidence_and_source_hash_without_full_paper_text(workbench):
    client, store, run, _ = workbench
    document = client.get(prefix(run) + "/export", params={"format": "json"}).json()
    source = document["sources"][0]
    assert source["text"] == ""
    assert source["metadata"]["retrieved_text_sha256"] == hashlib.sha256(run.sources[0].text.encode()).hexdigest()
    assert document["evidence"][0]["quote"] == run.evidence[0].quote
    assert store.load_run(run.project_id, run.run_id).sources[0].text == run.sources[0].text
    assert client.get(prefix(run) + "/results").json()["sources"][0]["text"] == run.sources[0].text


def test_different_chassis_cannot_silently_use_wcfs1_compute_model(workbench):
    client, store, run, _ = workbench
    service = client.app.state.service
    service.settings.target2lead_config = str(Path(__file__).parents[1] / "configs" / "keap1.json")
    run.brief.chassis_name = "Lactiplantibacillus plantarum L168"
    store.save_run(run)
    response = client.post(f"/api/projects/{run.project_id}/compute", json={
        "run_id": run.run_id, "package_id": "fixture_package", "max_md_jobs": 1})
    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]
    assert service.list_compute(run.project_id) == []

