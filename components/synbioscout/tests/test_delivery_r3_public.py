"""SYNTHETIC R3 output-admission fixtures and public format regressions."""
from __future__ import annotations

import copy
import csv
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scientist.api import create_app
from scientist.budgets import BudgetExceeded, RunLedger
from scientist.co_models import AgentEvent, AgentState, CoScientistState
from scientist.exports import export_document, export_research, markdown_bytes, table_exports
from scientist.models import (
    ExecutionStatus,
    ResearchBrief,
    ResearchBudget,
    ResearchRun,
    ScientistSettings,
)
from scientist.presentation import result_document
from scientist.store import ProjectStore, write_json
from scientist.tool_public import public_failure_records

STAMP = "2026-10-17T00:00:00+08:00"
PRIVATE = "SYNTHETIC_REJECTED_BODY_DO_NOT_EXPORT"
NEW_FIELDS = {"call_allowance","effective_available_output_bytes","output_limit_categories"}


def sized_body(size):
    row={"synthetic":PRIVATE+" · 中🧪"}
    row["synthetic"] += "x"*(size-len(json.dumps(row,ensure_ascii=False).encode()))
    body=json.dumps(row,ensure_ascii=False)
    assert len(body.encode()) == size
    return body


def persist(tmp_path,run):
    store=ProjectStore(tmp_path/run.run_id/"SYNTHETIC_workspace")
    write_json(store.root/"projects"/run.project_id/"project.json",{
        "project_id":run.project_id,"name":"SYNTHETIC R3 public admission cases","created_at":STAMP})
    write_json(store.project_dir(run.project_id)/"research"/run.run_id/"run.json",run.model_dump(mode="json"))
    return store


def output_case(kind):
    """Exercise RunLedger's acceptance boundary using synthetic call receipts."""
    limits=ResearchBudget(max_llm_calls=16,timeout_seconds=840,max_input_tokens=300000,max_output_tokens=96000)
    run=ResearchRun(run_id="r_synthetic_r3_"+kind,project_id="p_synthetic_ui",created_at=STAMP,updated_at=STAMP,
        status=ExecutionStatus.PARTIAL,brief=ResearchBrief(disease="SYNTHETIC R3 · "+kind,research_mode="co_scientist",budget=limits))
    if kind == "legacy_unknown":
        row={"receipt_id":"budget_action_0001","operation_id":"llm_synthetic_output","stage":"SYNTHETIC legacy output receipt",
            "reason":"Output reservation budget exhausted","required_output_bytes":17188,"remaining_output_bytes":93958,
            "reserved_output_bytes":24000,"blocked_before_provider":False,"accounting_unit":"utf8_bytes"}
        snapshot={"output_tokens_reserved":2042,"output_bytes_inflight":0}
    else:
        remaining,size,future,parallel = (93958,17188,24000,0) if kind == "single_call" else (10000,7000,2000,2000)
        ledger=RunLedger(limits,time.monotonic()+840)
        # Prior accepted bytes are generated through the same ledger in legal
        # <=12000-byte chunks. These are explicit synthetic receipt operations.
        prior=96000-remaining
        while prior:
            amount=min(12000,prior)
            ledger.reserve_request(1,required_output_bytes=amount,output_reservation_id="synthetic_prior")
            ledger.output(sized_body(amount),call_allowance=12000,reservation_id="synthetic_prior")
            prior-=amount
        ledger.reserve_request(1,required_output_bytes=1,output_reservation_id="synthetic_current")
        if parallel:
            ledger.reserve_request(1,required_output_bytes=parallel,output_reservation_id="synthetic_parallel")
        try:
            ledger.output(sized_body(size),call_allowance=12000,protected_output_bytes=future,reservation_id="synthetic_current")
        except BudgetExceeded as exc:
            row={**exc.details,"receipt_id":"budget_action_0001","operation_id":"llm_synthetic_output",
                "stage":"SYNTHETIC output acceptance · "+kind,"reason":str(exc)}
        else:
            raise AssertionError("Synthetic output must exceed the selected acceptance limit")
        ledger.release_output("synthetic_parallel")
        snapshot=ledger.snapshot()
    run.co_scientist=CoScientistState(agents=[AgentState(agent_id="supervisor",role="Supervisor",execution="orchestrator",
        status="partial",task="SYNTHETIC RunLedger acceptance fixture; no real model request")],
        budget_failures=[copy.deepcopy(row)],budget={**snapshot,"budget_failures":[copy.deepcopy(row)]},
        events=[AgentEvent(event_id="event_synthetic_output",sequence=1,agent_id="supervisor",kind="synthetic_budget",
            status="partial",title=row["stage"],summary="SYNTHETIC byte acceptance receipt; discarded body is not exported",
            created_at=STAMP,operation_id=row["operation_id"],operation_status="failed",artifact_ids=[row["receipt_id"]],
            budget_failure=copy.deepcopy(row))])
    run.usage=copy.deepcopy(snapshot)
    return run


def csv_rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


@pytest.mark.parametrize("kind",["single_call","future_parallel","legacy_unknown"])
def test_output_limit_facts_match_normal_api_report_csv_zip_without_body_or_legacy_inference(tmp_path,kind):
    run=output_case(kind)
    store=persist(tmp_path,run)
    original=run.model_dump_json()
    saved=store.run_dir(run.project_id,run.run_id)/"run.json"
    before=saved.read_bytes()
    expected=public_failure_records(run.co_scientist)
    assert len(expected)==1
    result=result_document(run)
    exported=export_document(run)
    for document in (result,exported): assert document["failure_records"]==expected
    row=expected[0]
    if kind=="single_call":
        assert {key:row[key] for key in NEW_FIELDS}=={"call_allowance":12000,"effective_available_output_bytes":12000,"output_limit_categories":["single_call"]}
        assert (row["required_output_bytes"],row["remaining_output_bytes"],row["reserved_output_bytes"])==(17188,93958,24000)
        assert result["co_scientist"]["budget"]["output_bytes_received"]==19230
        assert result["co_scientist"]["budget"]["output_tokens_reserved"]==2042
    elif kind=="future_parallel":
        assert row["output_limit_categories"]==["future_reserved","parallel"]
        assert row["effective_available_output_bytes"]==6000 and row["inflight_output_bytes"]==2000
        assert result["co_scientist"]["budget"]["output_bytes_inflight"]==0
    else:
        assert not NEW_FIELDS.intersection(row)
        assert "output_bytes_received" not in result["co_scientist"]["budget"]
    markdown=markdown_bytes(run)
    blocks=[json.loads(text) for text in re.findall(r"```json\n(.*?)\n```",markdown.decode(),flags=re.DOTALL)]
    assert next(item for item in blocks if isinstance(item,dict) and item.get("receipt_id"))==row
    tables=table_exports(run)
    rendered=csv_rows(tables["budget_failures.csv"])[0]
    for key in ("required_output_bytes","remaining_output_bytes","reserved_output_bytes"):
        assert int(rendered[key])==row[key]
    if kind=="legacy_unknown": assert all(rendered[key]=="" for key in NEW_FIELDS)
    else:
        assert int(rendered["call_allowance"])==row["call_allowance"]
        assert int(rendered["effective_available_output_bytes"])==row["effective_available_output_bytes"]
        assert json.loads(rendered["output_limit_categories"])==row["output_limit_categories"]
    packed,_,_=export_research(store,run,"zip")
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert archive.read("budget_failures.csv")==tables["budget_failures.csv"]
        assert json.loads(archive.read("research.json"))["failure_records"]==expected
        assert archive.read("report.md")==markdown
        assert PRIVATE.encode() not in b"".join(archive.read(name) for name in archive.namelist())
    settings=ScientistSettings(workspace=str(store.root),llm={"backend":"none"})
    with TestClient(create_app(settings,service=SimpleNamespace(store=store,shutdown=lambda:None))) as client:
        response=client.get(f"/api/projects/{run.project_id}/runs/{run.run_id}/results")
        assert response.status_code==200 and response.json()["failure_records"]==expected
    assert run.model_dump_json()==original and saved.read_bytes()==before


def test_saved_r3_ui_contracts_and_read_only_preview_downloads():
    node=os.environ.get("SYNBIOSCOUT_TEST_NODE") or shutil.which("node")
    if not node: pytest.skip("Set SYNBIOSCOUT_TEST_NODE for offline JS checking")
    subprocess.run([node,str(Path(__file__).with_name("test_delivery_r3_ui.js"))],check=True,capture_output=True,text=True)
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location("synthetic_r3_preview_test",root/"docs/nac_asd_iteration/preview_delivery_r3.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data=module.PreviewData()
    for run_id,document in data.runs.items():
        status,_,body=data.route("GET",f"/api/projects/p_synthetic_ui/runs/{run_id}/export?format=json")
        assert status==200 and json.loads(body)["failure_records"]==document["failure_records"]
    for method in ("POST","PUT","PATCH","DELETE"):
        assert data.route(method,"/api/projects/p_synthetic_ui/runs")[0]==403


def test_nonempty_transparent_chain_delivery_keeps_limited_review_consistent_across_saved_formats():
    directory=Path(__file__).resolve().parents[1]/"docs/nac_asd_iteration"
    fixture=json.loads((directory/"DELIVERY_R3_SYNTHETIC_PUBLIC.json").read_text())
    run=next(r for r in fixture["runs"] if r["presentation"]["fixture"]["case"]=="reviewed")
    evaluations=run["candidate_evaluations"]
    assert evaluations and evaluations[0]["scientific_status"] in {"mixed_evidence","needs_evidence"}
    assert evaluations[0]["opposing_evidence_ids"] and evaluations[0]["unknowns"]
    assert not run["co_scientist"]["hypotheses"] and not run["co_scientist"]["contracts"]
    with zipfile.ZipFile(directory/"DELIVERY_R3_CASE_DOWNLOADS.zip") as cases:
        exported=json.loads(cases.read(run["run_id"]+"/json"))
        assert exported["candidate_evaluations"]==evaluations
        candidate=csv_rows(cases.read(run["run_id"]+"/csv"))[0]
        assert candidate["assessment_status"]=="completed_review" and candidate["conclusion"]==evaluations[0]["conclusion"]
        markdown=cases.read(run["run_id"]+"/markdown").decode()
        assert evaluations[0]["conclusion"] in markdown and evaluations[0]["review_id"] in markdown
        with zipfile.ZipFile(io.BytesIO(cases.read(run["run_id"]+"/zip"))) as archive:
            assert json.loads(archive.read("candidate_evaluations.json"))==evaluations
            row=csv_rows(archive.read("candidate_evaluations.csv"))[0]
            assert json.loads(row["findings"])==evaluations[0]["findings"]
            assert json.loads(archive.read("research.json"))["candidate_evaluations"]==evaluations

