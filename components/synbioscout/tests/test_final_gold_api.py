"""Normal intake/confirmation/API -> full transparent engine -> nonempty exports."""
import csv
import io
import json
import uuid
import zipfile
from copy import deepcopy
from types import SimpleNamespace

import pytest
import test_delivery_r3_chain as r3
from fastapi.testclient import TestClient
from test_final_gold_matrix import source_fixture
from test_intake import TEXT, FakeParser

from scientist.api import create_app
from scientist.exports import export_research
from scientist.models import ScientistSettings
from scientist.research import ResearchEngine
from scientist.store import ProjectStore


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    r3.guard.__wrapped__(monkeypatch)


def exercise(tmp_path, monkeypatch):
    brief, sources = source_fixture()
    clock = r3.ThreadClock()
    control = r3.R3Controller(brief, sources, clock, kind="public")
    r3.install_r3(monkeypatch, control)
    store = ProjectStore(tmp_path / "SYNTHETIC_final_api")
    settings = ScientistSettings(workspace=str(store.root), llm={"backend": "codex", "model": "gpt-6.1-sol"})
    offline = r3.OfflineR3Sources(clock)
    engine = ResearchEngine(store, settings, sources=offline, llm_factory=control.factory)
    submits = []

    def submit(project_id, confirmed_brief, **kwargs):
        submits.append(confirmed_brief.model_dump(mode="json"))
        assert confirmed_brief == brief
        assert kwargs.get("prepare_targets") is False and not kwargs.get("parent_run_id")
        created = store.create_run(project_id, confirmed_brief)
        return engine.execute(project_id, created.run_id, prepare_targets=False)

    parser = FakeParser()
    service = SimpleNamespace(store=store, submit_research=submit, shutdown=lambda: None)
    with TestClient(create_app(settings, service=service, intake_parser=parser)) as client:
        response = client.post("/api/intake/drafts", json={"request_id": uuid.uuid4().hex, "text": TEXT})
        assert response.status_code == 200, response.text
        draft = response.json()
        assert not store.list_projects() and not submits
        assert draft["brief"]["existing_candidates"] == ["NAC"]
        assert not draft["brief"]["background_package_id"] and not draft["brief"]["population"]
        # Submit the historical scenario through the confirmation endpoint.
        # WetLab and package fields come from the scenario; parser extraction has separate tests.
        edited = client.patch("/api/intake/drafts/" + draft["draft_id"],
                              json={"revision": draft["revision"], "brief": brief.model_dump(mode="json")})
        assert edited.status_code == 200, edited.text
        current = edited.json()
        proof = {"draft_id": current["draft_id"], "revision": current["revision"], "confirmed": True}
        stale = {**proof, "revision": draft["revision"]}
        project_body = {"name": "SYNTHETIC FINAL GOLD API chain", "request_id": current["submission_id"],
                        "intake_confirmation": proof}
        rejected = client.post("/api/projects", json={**project_body, "intake_confirmation": stale})
        assert rejected.status_code == 409 and not store.list_projects()
        project = client.post("/api/projects", json=project_body)
        assert project.status_code == 201, project.text
        pid = project.json()["project_id"]
        assert client.post("/api/projects", json=project_body).json()["project_id"] == pid
        body = {"brief": current["brief"], "prepare_targets": False,
                "request_id": current["submission_id"], "intake_confirmation": proof}
        response = client.post(f"/api/projects/{pid}/runs", json=body)
        assert response.status_code == 202, response.text
        rid = response.json()["run_id"]
        duplicate = client.post(f"/api/projects/{pid}/runs", json=body)
        assert duplicate.status_code == 202 and duplicate.json()["run_id"] == rid
        assert len(submits) == len(store.list_projects()) == len(store.list_runs(pid)) == 1
        run = store.load_run(pid, rid)
        r3.assert_complete(run, control, offline)
        document = client.get(f"/api/projects/{pid}/runs/{rid}/results").json()
        assert len(document["candidate_evaluations"]) == 1
        exports = {}
        for kind in ("json", "markdown", "csv", "zip"):
            response = client.get(f"/api/projects/{pid}/runs/{rid}/export", params={"format": kind})
            assert response.status_code == 200
            exports[kind] = response.content
            expected = export_research(store, run, kind)[0]
            if kind == "zip":
                # ZIP member timestamps can cross a 2-second boundary between
                # two exports. Compare every scientific file byte-for-byte,
                # rather than nondeterministic archive header timestamps.
                with zipfile.ZipFile(io.BytesIO(exports[kind])) as actual_zip, zipfile.ZipFile(io.BytesIO(expected)) as expected_zip:
                    assert actual_zip.namelist() == expected_zip.namelist()
                    assert all(actual_zip.read(name) == expected_zip.read(name) for name in actual_zip.namelist())
            else:
                assert exports[kind] == expected
        evaluation = document["candidate_evaluations"][0]
        assert evaluation["opposing_evidence_ids"] and evaluation["supporting_evidence_ids"]
        assert evaluation["unknowns"] and evaluation["scientific_status"] == "mixed_evidence"
        assert json.loads(exports["json"])["candidate_evaluations"] == document["candidate_evaluations"]
        row = next(csv.DictReader(io.StringIO(exports["csv"].decode("utf-8-sig"))))
        assert row["conclusion"] == evaluation["conclusion"]
        assert row["assessment_status"] == "completed_review"
        assert evaluation["review_id"] in exports["markdown"].decode()
        assert evaluation["conclusion"] in exports["markdown"].decode()
        with zipfile.ZipFile(io.BytesIO(exports["zip"])) as archive:
            assert archive.read("report.md") == exports["markdown"]
            assert json.loads(archive.read("research.json"))["candidate_evaluations"] == document["candidate_evaluations"]
            evaluations = list(csv.DictReader(io.StringIO(archive.read("candidate_evaluations.csv").decode("utf-8-sig"))))
            assert len(evaluations) == 1
            assert len(json.loads(evaluations[0]["findings"])) == 5
        receipt = client.get("/api/intake/submissions/" + current["submission_id"]).json()
        return document, exports, {
            "synthetic": True, "actual_model_calls": 0, "actual_http": 0,
            "parser_fixture_calls": len(parser.calls), "research_fixture_calls": len(control.calls),
            "projects": 1, "runs": 1, "submission_calls": len(submits),
            "stale_confirmation_rejected": True, "repeat_confirmation_same_ids": True,
            "manual_brief_equals_original_scenario": submits[0] == brief.model_dump(mode="json"),
            "accepted_evidence": len(run.evidence), "evaluations": 1, "trial_findings": 5,
            "all_four_exports_match_normal_api": True,
            "usage": deepcopy(run.usage), "intake_submission": receipt,
        }


def test_confirm_once_then_review_nonempty_result_and_all_formats(tmp_path, monkeypatch):
    exercise(tmp_path, monkeypatch)

