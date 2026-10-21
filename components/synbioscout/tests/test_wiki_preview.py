"""The UI preview has no writable route or connection to real research data."""
import importlib.util
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def preview():
    spec = importlib.util.spec_from_file_location("wiki_preview", ROOT / "docs/ui_wiki_2026-10-19/preview.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PreviewData()


def test_preview_rejects_all_writes_without_mutating_fixture():
    data = preview()
    before = json.dumps(data.runs, sort_keys=True)
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
        for route in ("/api/projects", "/api/projects/p_synthetic_ui/runs", "/api/projects/p_synthetic_ui/compute"):
            assert data.route(method, route)[0] == 403
    assert json.dumps(data.runs, sort_keys=True) == before


def test_running_preview_only_changes_timestamp_and_works_on_python39():
    data = preview()
    first, second = data.result("r_synthetic_running"), data.result("r_synthetic_running")
    assert first.pop("updated_at") != second.pop("updated_at")
    assert first == second
    for run_id in data.runs:
        assert data.runs[run_id]["presentation"]["fixture"]["synthetic"] is True
        if run_id != "r_synthetic_running":
            assert data.result(run_id) == data.runs[run_id]


def test_preview_rejects_unknown_files_real_records_and_proxy_routes():
    data = preview()
    for path in ("/../scientist/api.py", "/baseline/../preview.py", "/api/projects/real/runs", "/proxy?url=https://example.org"):
        assert data.route("GET", path)[0] == 404
    assert data.route("GET", "/api/projects/p_synthetic_error/runs")[0] == 503
    assert json.loads(data.route("GET", "/api/projects/p_synthetic_empty/runs")[2]) == []
    for path in ("/", "/baseline/"):
        assert b"SYNTHETIC" in data.route("GET", path)[2]


def test_preview_exports_are_the_original_saved_synthetic_bytes():
    data = preview()
    with zipfile.ZipFile(ROOT / "docs/nac_asd_iteration/DELIVERY_R3_CASE_DOWNLOADS.zip") as archive:
        for run_id in ("r_synthetic_r3_single_call", "r_synthetic_r3_reviewed"):
            for kind in ("json", "markdown", "csv", "zip"):
                status, _, payload = data.route("GET", f"/api/projects/p_synthetic_ui/runs/{run_id}/export?format={kind}")
                assert status == 200
                assert payload == archive.read(run_id + "/" + kind)

