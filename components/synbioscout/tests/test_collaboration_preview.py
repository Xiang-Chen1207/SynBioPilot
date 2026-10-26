"""The Stage 01 browser QA server must remain isolated from real execution."""

import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "docs" / "serial_optimization" / "fixtures"


@pytest.fixture(scope="module")
def preview_module():
    spec = importlib.util.spec_from_file_location("collaboration_preview",
                                               FIXTURE_DIRECTORY / "preview_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def document(route):
    status, mime, payload = route
    assert mime.startswith("application/json")
    return status, json.loads(payload)


def test_preview_page_marks_every_run_as_synthetic_and_assets_are_current(preview_module):
    preview = preview_module.PreviewData()
    for route in ("/", "/index.html"):
        status, mime, page = preview.route("GET", route)
        assert status == 200 and mime.startswith("text/html")
        assert b'id="synthetic-preview-banner"' in page
        assert "SYNTHETIC · 本地 UI 验收 · 无真实研究执行" in page.decode()
        assert "updated_at 每次 GET 加 1 秒" in page.decode()
    for route, name in (("/styles.css", "styles.css"), ("/app.js", "app.js"),
                        ("/assets/styles.css", "styles.css"), ("/assets/app.js", "app.js")):
        status, _mime, content = preview.route("GET", route)
        assert status == 200
        assert content == (preview_module.ROOT / "scientist" / "web" / name).read_bytes()


def test_preview_poll_only_changes_running_response_timestamp(preview_module):
    preview = preview_module.PreviewData()
    before = hashlib.sha256(preview_module.FIXTURES.read_bytes()).hexdigest()
    route = "/api/projects/p_synthetic_ui/runs/r_synthetic_running/results"
    _, first = document(preview.route("GET", route))
    _, second = document(preview.route("GET", route))
    first_stamp, second_stamp = first.pop("updated_at"), second.pop("updated_at")
    assert (datetime.fromisoformat(second_stamp) - datetime.fromisoformat(first_stamp)).total_seconds() == 1
    assert first == second
    for run_id in ("completed", "partial", "failed", "legacy"):
        route = f"/api/projects/p_synthetic_ui/runs/r_synthetic_{run_id}/results"
        assert preview.route("GET", route) == preview.route("GET", route)
    assert hashlib.sha256(preview_module.FIXTURES.read_bytes()).hexdigest() == before


def test_preview_mock_submit_returns_static_records_and_other_writes_are_forbidden(preview_module):
    preview = preview_module.PreviewData()
    before = json.dumps(preview.runs, sort_keys=True)
    status, project = document(preview.route("POST", "/api/projects"))
    assert status == 201 and project["project_id"] == "p_synthetic_ui"
    status, run = document(preview.route("POST", "/api/projects/p_synthetic_ui/runs"))
    assert status == 202 and run["run_id"] == "r_synthetic_running"
    assert run["presentation"]["fixture"]["synthetic"] is True
    for method, route in (("POST", "/api/projects/p_synthetic_ui/compute"),
                          ("POST", "/api/projects/p_synthetic_ui/experiments"),
                          ("POST", "/api/projects/real_project/runs"),
                          ("PUT", "/api/projects"), ("PATCH", "/api/projects"), ("DELETE", "/api/projects")):
        status, result = document(preview.route(method, route))
        assert status == 403 and "forbidden" in result["detail"]
    assert json.dumps(preview.runs, sort_keys=True) == before


def test_preview_does_not_serve_real_records_unknown_files_or_proxy_urls(preview_module):
    preview = preview_module.PreviewData()
    for route in ("/scientist-settings.json", "/../../scientist/store.py", "/assets/../store.py",
                  "/api/projects/real_project/runs", "/api/projects/p_synthetic_ui/runs/r_real/results",
                  "/proxy?url=https://example.org", "/api/projects/p_synthetic_ui/runs/r_synthetic_completed/export"):
        status, result = document(preview.route("GET", route))
        assert status == 404 and "no proxy" in result["detail"]
    for route in ("compute", "experiments", "memory"):
        assert document(preview.route("GET", f"/api/projects/p_synthetic_ui/{route}")) == (200, [])
    assert document(preview.route("GET", "/api/research-tools"))[1]["tools"] == []
    assert document(preview.route("GET", "/api/research-protocols"))[1]["protocols"] == []
    health = document(preview.route("GET", "/api/health"))[1]
    assert health["synthetic"] is True and health["real_research_executed"] is False


def test_preview_server_binds_only_loopback(preview_module):
    with preview_module.create_server(0) as server:
        assert server.server_address[0] == "127.0.0.1"
        assert server.preview_data.runs

