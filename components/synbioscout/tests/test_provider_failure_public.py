"""SYNTHETIC content-free provider receipts across public views and downloads."""
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scientist.api import create_app
from scientist.co_models import AgentEvent, CoScientistState
from scientist.exports import export_research
from scientist.models import ExecutionStatus, LLMSettings, ResearchBrief, ScientistSettings
from scientist.provider_receipts import public_provider_failure
from scientist.runtime import JobService
from scientist.tool_public import public_budget, public_budget_failure, public_tool_event


def receipt(**changes):
    return {"version":1,"category":"process_nonzero","stage":"process","backend":"codex","origin":"provider_call",
        "elapsed_seconds":2.5,"returncode":-9,"signal":9,"output_exists":False,"output_is_file":False,
        "output_size_bytes":None,"output_stat_status":"missing",**changes}


def poisoned():
    return {**receipt(),"stdout":"PRIVATE_STDOUT_SENTINEL","stderr":"PRIVATE_STDERR_SENTINEL",
        "answer":"PRIVATE_ANSWER_SENTINEL","raw_prompt":"PRIVATE_PROMPT_SENTINEL",
        "exception_chain":{"api_key":"PRIVATE_KEY_SENTINEL"},"output_path":"/tmp/PRIVATE_PATH_SENTINEL"}


def test_same_allowlisted_receipt_in_event_budget_and_nullable_unknown_values():
    clean = receipt()
    raw = poisoned()
    assert public_provider_failure(raw) == clean
    assert public_tool_event({"provider_failure":raw})["provider_failure"] == clean
    assert public_budget_failure({"provider_failure":raw})["provider_failure"] == clean
    assert public_budget({"budget_failures":[{"provider_failure":raw}]})["budget_failures"][0]["provider_failure"] == clean
    unknown = receipt(category="launch_error",stage="executable_lookup",returncode=None,signal=None,
        output_exists=None,output_is_file=None,output_size_bytes=None,output_stat_status="not_checked")
    assert public_provider_failure(unknown) == unknown
    assert unknown["returncode"] is unknown["output_size_bytes"] is None


@pytest.mark.parametrize("value", [None,{}, {"message":"timeout PRIVATE_SENTINEL"},
    receipt(version=True),receipt(category="raw_prompt=PRIVATE_SENTINEL"),receipt(stage="stderr"),
    receipt(origin="cache"),receipt(backend="/tmp/PRIVATE_SENTINEL")])
def test_invalid_or_missing_receipt_is_omitted_and_diagnostic_text_does_not_classify(value):
    assert public_provider_failure(value) == {}
    assert "provider_failure" not in public_budget_failure({"reason":"provider_failure","provider_failure":value})
    assert "provider_failure" not in public_tool_event({"summary":"timeout occurred", "provider_failure":value})


def test_invalid_facts_are_not_coerced_and_signals_require_matching_returncode():
    invalid = receipt(elapsed_seconds=float("nan"),returncode=True,signal=9,output_exists="true",
        output_is_file=1,output_size_bytes=-1,output_stat_status="stderr=PRIVATE_SENTINEL")
    assert public_provider_failure(invalid) == {k:v for k,v in receipt().items()
        if k in {"version","category","stage","origin","backend"}}
    assert "signal" not in public_provider_failure(receipt(returncode=1,signal=9))
    assert "signal" not in public_provider_failure(receipt(returncode=-15,signal=9))
    assert "elapsed_seconds" not in public_provider_failure(receipt(elapsed_seconds=10**9+1))
    assert "elapsed_seconds" not in public_provider_failure(receipt(elapsed_seconds=10**1000))
    assert "output_size_bytes" not in public_provider_failure(receipt(output_size_bytes=10**18))
    assert "returncode" not in public_provider_failure(receipt(returncode=2**31))


def test_results_json_zip_and_markdown_match_and_do_not_rewrite_past_run(tmp_path):
    settings = ScientistSettings(workspace=str(tmp_path/"workspace"),llm=LLMSettings(backend="none"))
    service = JobService(settings)
    store = service.store
    project = store.create_project("SYNTHETIC safe provider receipt export")
    run = store.create_run(project["project_id"],ResearchBrief(disease="SYNTHETIC",research_mode="co_scientist"))
    run.status = ExecutionStatus.FAILED
    failure = {"operation_id":"llm_002","stage":"SYNTHETIC formal reflection","reason":"provider_failure",
        "blocked_before_provider":False,"provider_failure":poisoned()}
    run.co_scientist = CoScientistState(events=[AgentEvent(event_id="synthetic_event",sequence=1,
        agent_id="reflection",kind="llm",status="failed",title="SYNTHETIC formal reflection",
        operation_id="llm_002",operation_status="failed",created_at="",provider_failure=poisoned()),
        AgentEvent(event_id="synthetic_legacy_event",sequence=2,agent_id="reflection",kind="llm",status="failed",
            title="SYNTHETIC historical failure",operation_id="llm_001",created_at="")],
        budget_failures=[failure],budget={"budget_failures":[failure]},limitations=[])
    store.save_run(run)
    path = store.run_dir(run.project_id,run.run_id)/"run.json"
    original = path.read_bytes()
    with TestClient(create_app(settings,service=service)) as client:
        payload = client.get(f"/api/projects/{run.project_id}/runs/{run.run_id}/results").json()
    json_bytes,_,_ = export_research(store,run,"json")
    exported = json.loads(json_bytes)
    zip_bytes,_,_ = export_research(store,run,"zip")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        zipped = json.loads(archive.read("research.json"))
        for doc in (payload,exported,zipped):
            co = doc["co_scientist"]
            assert co["events"][0]["provider_failure"] == receipt()
            assert co["events"][0]["operation_id"] == "llm_002"
            assert "provider_failure" not in co["events"][1]
            assert co["budget_failures"][0]["provider_failure"] == receipt()
            assert co["budget"]["budget_failures"][0]["provider_failure"] == receipt()
            assert "PRIVATE_" not in json.dumps(doc)
        assert json.loads(archive.read("events.json"))[0]["provider_failure"] == receipt()
        assert json.loads(archive.read("budget.json"))["budget_failures"][0]["provider_failure"] == receipt()
        report = archive.read("report.md").decode()
        records = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```",report,re.DOTALL)]
        assert len(records) == 1
        assert records[0]["provider_failure"] == receipt()
        assert records[0]["operation_id"] == "llm_002"
        assert records[0]["stage"] == "SYNTHETIC formal reflection"
        assert "模型调用失败收据" in report and "PRIVATE_" not in report
    assert path.read_bytes() == original
    assert run.co_scientist.events[0].provider_failure["stdout"] == "PRIVATE_STDOUT_SENTINEL"


def test_legacy_export_never_invents_new_provider_fact_fields(tmp_path):
    service = JobService(ScientistSettings(workspace=str(tmp_path),llm=LLMSettings(backend="none")))
    store = service.store
    project = store.create_project("SYNTHETIC no receipt")
    run = store.create_run(project["project_id"],ResearchBrief(disease="SYNTHETIC",research_mode="co_scientist"))
    run.status = ExecutionStatus.FAILED
    run.co_scientist = CoScientistState(events=[AgentEvent(event_id="old",sequence=1,agent_id="supervisor",
        kind="llm",status="failed",title="SYNTHETIC old failure",summary="Codex inference failed",created_at="")],
        budget_failures=[{"reason":"provider_failure","stage":"SYNTHETIC old call"}])
    json_bytes,_,_ = export_research(store,run,"json")
    doc = json.loads(json_bytes)
    assert "provider_failure" not in doc["co_scientist"]["events"][0]
    assert "provider_failure" not in doc["co_scientist"]["budget_failures"][0]
    markdown,_,_ = export_research(store,run,"markdown")
    assert "模型调用失败收据" not in markdown.decode()


def test_javascript_and_python_projection_agree_on_public_json_cases():
    node = os.environ.get("SYNBIOSCOUT_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("node is required only for this cross-language public-schema check")
    app = Path(__file__).resolve().parents[1]/"scientist/web/app.js"
    cases = [None,{},poisoned(),receipt(returncode=0,signal=None),
        receipt(returncode=None,signal=None,output_exists=None,output_is_file=None,output_size_bytes=None),
        receipt(signal=15),receipt(category="timeout",stage="process",returncode=None,signal=None),
        receipt(category="bad PRIVATE_SENTINEL"),receipt(stage="bad PRIVATE_SENTINEL"),
        receipt(elapsed_seconds=-1,returncode=True,signal=True,output_exists="true",output_is_file=1,output_size_bytes=-1),
        {k:v for k,v in receipt().items() if k in {"version","category","stage","origin","backend"}}]
    code = r'''const fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync(process.argv[1],"utf8").replace(/\ninit\(\);\s*$/,"");
const context={};vm.createContext(context);vm.runInContext(source+"\nglobalThis.projectReceipt=publicProviderFailure;",context);
const cases=JSON.parse(fs.readFileSync(0,"utf8"));process.stdout.write(JSON.stringify(cases.map(context.projectReceipt)));'''
    result = subprocess.run([node,"-e",code,str(app)],input=json.dumps(cases),text=True,capture_output=True,check=True,timeout=20)
    assert json.loads(result.stdout) == [public_provider_failure(case) for case in cases]

