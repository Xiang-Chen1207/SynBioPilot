"""SYNTHETIC output admission receipts; no provider, HTTP or historical answers.

Node, when available, evaluates only the local pure UI projection in a VM. All
provider entry points and every non-Node subprocess are blocked. Temporary
projects exist only to exercise the normal public export functions and API.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient
from test_budget_scheduling import setup_engine

from scientist.api import create_app
from scientist.budgets import BudgetExceeded, RunLedger
from scientist.co_models import AgentEvent, CoScientistState
from scientist.exports import table_exports
from scientist.models import ExecutionStatus, ResearchBrief, ResearchBudget, ScientistSettings
from scientist.store import ProjectStore
from scientist.tool_public import public_budget_failure, public_failure_records

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get("SYNBIOSCOUT_TEST_NODE") or shutil.which("node")
FALLBACK_NODE = Path("/Users/SYNTHETIC/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node")
if not NODE and FALLBACK_NODE.is_file():
    NODE = str(FALLBACK_NODE)
NEW_FIELDS = {"call_allowance", "effective_available_output_bytes", "output_limit_categories"}
PRIVATE = "SYNTHETIC_PRIVATE_REJECTED_BODY_DO_NOT_EXPORT"


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SYNTHETIC budget test forbids provider/network/private inference access")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("scientist.llm.StructuredLLM.complete", forbidden)
    original_popen = subprocess.Popen
    def local_node_only(args, *rest, **kwargs):
        if not (NODE and isinstance(args, (list, tuple)) and len(args) > 1 and args[0] == NODE and args[1] == "-e"):
            forbidden()
        return original_popen(args, *rest, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", local_node_only)
    original_open = Path.open
    def safe_open(path, *args, **kwargs):
        if "inference" in path.parts or path.name in {"auth.json", "config.toml"}:
            forbidden()
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", safe_open)


def ledger(monkeypatch, *, remaining=93958):
    monkeypatch.setattr("scientist.budgets.time.monotonic", lambda: 1000.0)
    result = RunLedger(ResearchBudget(max_output_tokens=96000), 1840.0)
    result.counts["output_tokens_reserved"] = 96000 - remaining
    result.counts["output_bytes_received"] = 96000 - remaining
    return result


def sized_wire(size, *, unicode=True):
    value = {"synthetic": "中🧪" if unicode else "SYNTHETIC"}
    padding = size - len(json.dumps(value, ensure_ascii=False).encode())
    assert padding >= 0
    value["synthetic"] += "x" * padding
    assert len(json.dumps(value, ensure_ascii=False).encode()) == size
    return value


def rejected(ledger, size, *, cap=12000, future=0, parallel=0):
    ledger.reserve_request(1, required_output_bytes=1, output_reservation_id="synthetic_current")
    if parallel:
        ledger.reserve_request(1, required_output_bytes=parallel, output_reservation_id="synthetic_other")
    before = ledger.snapshot()
    with pytest.raises(BudgetExceeded) as caught:
        ledger.output(json.dumps(sized_wire(size), ensure_ascii=False), call_allowance=cap,
            protected_output_bytes=future, reservation_id="synthetic_current")
    details = caught.value.details
    assert ledger.counts["output_tokens_reserved"] == before["output_tokens_reserved"]
    assert ledger.counts["output_bytes_received"] == before["output_bytes_received"] + size
    assert "synthetic_current" not in ledger.output_holds
    assert ledger.snapshot()["output_bytes_inflight"] == parallel
    return details


@pytest.mark.parametrize("unicode", [False, True])
@pytest.mark.parametrize("size", [12000, 12001])
def test_actual_utf8_boundary_retains_received_and_accepted_as_separate_facts(monkeypatch, unicode, size):
    book = ledger(monkeypatch)
    book.reserve_request(1, required_output_bytes=12000, output_reservation_id="self")
    value = sized_wire(size, unicode=unicode)
    serialized = json.dumps(value, ensure_ascii=False)
    if unicode:
        assert len(serialized) < len(serialized.encode())
    if size == 12000:
        book.output(serialized, call_allowance=12000, protected_output_bytes=24000, reservation_id="self")
        assert book.counts["output_tokens_reserved"] == 2042 + size
    else:
        with pytest.raises(BudgetExceeded) as caught:
            book.output(serialized, call_allowance=12000, protected_output_bytes=24000, reservation_id="self")
        receipt = caught.value.details
        assert receipt["call_allowance"] == receipt["effective_available_output_bytes"] == 12000
        assert receipt["output_limit_categories"] == ["single_call"]
        assert receipt["remaining_output_bytes"] == 93958 and receipt["reserved_output_bytes"] == 24000
        assert book.counts["output_tokens_reserved"] == 2042
    assert book.counts["output_bytes_received"] == 2042 + size
    assert book.snapshot()["output_bytes_inflight"] == 0


def test_public_17188_byte_shape_records_single_call_limit_without_reading_any_real_response(monkeypatch):
    book = ledger(monkeypatch)
    receipt = rejected(book, 17188, cap=12000, future=24000)
    assert receipt == {"required_output_bytes": 17188, "remaining_output_bytes": 93958,
        "reserved_output_bytes": 24000, "inflight_output_bytes": 0,
        "call_allowance": 12000, "effective_available_output_bytes": 12000,
        "output_limit_categories": ["single_call"], "blocked_before_provider": False,
        "accounting_unit": "utf8_bytes"}
    assert book.counts["output_bytes_received"] == 19230
    assert book.counts["output_tokens_reserved"] == 2042


@pytest.mark.parametrize("remaining,size,cap,future,parallel,effective,categories", [
    (8000, 9000, 12000, 0, 0, 8000, ["global"]),
    (8000, 12001, 12000, 0, 0, 8000, ["single_call", "global"]),
    (10000, 7000, 12000, 4000, 0, 6000, ["future_reserved"]),
    (10000, 7000, 12000, 0, 4000, 6000, ["parallel"]),
    (10000, 7000, 12000, 2000, 2000, 6000, ["future_reserved", "parallel"]),
    (20000, 13000, 12000, 5000, 4000, 11000, ["single_call", "future_reserved", "parallel"]),
    (8000, 9000, None, 0, 0, 8000, ["global"]),
])
def test_each_limiting_dimension_is_explicit_and_combinations_do_not_hide_constraints(
        monkeypatch, remaining, size, cap, future, parallel, effective, categories):
    book = ledger(monkeypatch, remaining=remaining)
    receipt = rejected(book, size, cap=cap, future=future, parallel=parallel)
    assert receipt["call_allowance"] == cap
    assert receipt["effective_available_output_bytes"] == effective
    assert receipt["output_limit_categories"] == categories
    assert receipt["required_output_bytes"] == size and receipt["remaining_output_bytes"] == remaining
    assert receipt["reserved_output_bytes"] == future and receipt["inflight_output_bytes"] == parallel
    assert receipt["accounting_unit"] == "utf8_bytes" and receipt["blocked_before_provider"] is False


def test_parallel_output_rejection_releases_only_its_own_reservation(monkeypatch):
    book = ledger(monkeypatch, remaining=16000)
    for name in ("first", "second"):
        book.reserve_request(1, required_output_bytes=8000, output_reservation_id=name)
    gate = threading.Event()
    def first():
        try:
            with pytest.raises(BudgetExceeded) as caught:
                book.output(json.dumps(sized_wire(8001), ensure_ascii=False), call_allowance=12000, reservation_id="first")
            return caught.value.details
        finally:
            gate.set()
    def second():
        assert gate.wait(timeout=5)
        assert book.snapshot()["output_bytes_inflight"] == 8000
        book.output("x" * 8000, call_allowance=12000, reservation_id="second")
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(first), pool.submit(second)
        receipt = one.result(timeout=5)
        two.result(timeout=5)
    assert receipt["output_limit_categories"] == ["parallel"]
    assert receipt["effective_available_output_bytes"] == 8000
    assert book.snapshot()["output_bytes_inflight"] == 0
    assert book.counts["output_tokens_reserved"] == 88000
    # Use wire size, including UTF-8: no returned bytes are silently accepted.
    assert book.counts["output_bytes_received"] - book.counts["output_tokens_reserved"] == receipt["required_output_bytes"]


@pytest.mark.parametrize("size", [12000, 12001])
def test_actual_invoke_links_exact_output_receipt_to_failed_event_and_releases_inflight(tmp_path, monkeypatch, size):
    engine, _clock, _calls = setup_engine(tmp_path, monkeypatch)
    value, inputs = sized_wire(size), []
    def factory(settings, directory, max_calls):
        worker = SimpleNamespace(calls=0, cache_hits=0)
        def complete(system, user, schema):
            worker.calls += 1
            inputs.append((system, user, schema))
            return value
        worker.complete = complete
        return worker
    engine.legacy.llm_factory = factory
    before = [e.model_dump() for e in engine.run.evidence]
    action = lambda: engine.invoke("supervisor", "SYNTHETIC stage reception", "system", "{}", {"type": "object"},
        output_cap=12000, reserve={"output_bytes": 24000})
    if size == 12000:
        assert action() == value
        assert not engine.state.budget_failures
        assert engine.ledger.counts["output_tokens_reserved"] == size
    else:
        with pytest.raises(BudgetExceeded):
            action()
        row = engine.state.budget_failures[-1]
        event = next(e for e in engine.state.events if e.operation_status == "failed")
        assert row["operation_id"] == event.operation_id
        assert event.budget_failure == public_budget_failure(row)
        assert row["call_allowance"] == row["effective_available_output_bytes"] == 12000
        assert row["output_limit_categories"] == ["single_call"]
        assert not row.get("provider_failure") and not event.provider_failure
        assert row["blocked_before_provider"] is False
        assert engine.ledger.counts["output_tokens_reserved"] == 0
    assert len(inputs) == engine.actual_calls == engine.ledger.counts["llm_calls"] == 1
    assert engine.ledger.counts["output_bytes_received"] == size
    assert engine.ledger.snapshot()["output_bytes_inflight"] == 0
    assert [e.model_dump() for e in engine.run.evidence] == before


def failure_row():
    return {"receipt_id": "budget_action_synthetic_001", "operation_id": "llm_002",
        "stage": "SYNTHETIC extraction admission", "reason": "Output reservation budget exhausted",
        "required_output_bytes": 17188, "remaining_output_bytes": 93958, "reserved_output_bytes": 24000,
        "inflight_output_bytes": 0, "call_allowance": 12000, "effective_available_output_bytes": 12000,
        "output_limit_categories": ["single_call"], "accounting_unit": "utf8_bytes",
        "blocked_before_provider": False, "prior_timeout": False,
        "answer": PRIVATE, "raw_prompt": PRIVATE, "headers": {"Authorization": PRIVATE},
        "pathsecret": "/private/" + PRIVATE, "exception_chain": [PRIVATE]}


def js_project(rows):
    if NODE is None:
        pytest.skip("No local Node runtime for pure offline UI projection")
    script = r'''const fs=require("node:fs"),vm=require("node:vm");const context={
fetch(){throw new Error("SYNTHETIC no network");},document:new Proxy({}, {get(){throw new Error("SYNTHETIC no DOM");}})};
vm.createContext(context);const src=fs.readFileSync(process.argv[1],"utf8").replace(/\ninit\(\);\s*$/,"");
vm.runInContext(src+"\nglobalThis.api={publicBudgetFailure,publicFailureRecords};",context);
const rows=JSON.parse(fs.readFileSync(0,"utf8"));
process.stdout.write(JSON.stringify(rows.map(row=>context.api.publicBudgetFailure(row))));'''
    result = subprocess.run([NODE, "-e", script, str(ROOT / "scientist/web/app.js")],
        input=json.dumps(rows, ensure_ascii=False), text=True, capture_output=True, check=True, timeout=20)
    return json.loads(result.stdout)


def test_strict_safe_numeric_enum_projection_and_python_javascript_parity():
    row = failure_row()
    row["output_limit_categories"] = ["single_call", PRIVATE, "single_call", "global", {}, "parallel"]
    safe = public_budget_failure(row)
    assert safe["output_limit_categories"] == ["single_call", "global", "parallel"]
    assert PRIVATE not in json.dumps(safe)
    assert js_project([row])[0] == safe


@pytest.mark.parametrize("bad", [-1, True, False, 1.25, "12000", [], {}, float("nan"), float("inf"), float("-inf")])
def test_invalid_byte_values_cannot_enter_public_output_receipts(bad):
    row = failure_row()
    row["call_allowance"] = row["effective_available_output_bytes"] = bad
    safe = public_budget_failure(row)
    assert "call_allowance" not in safe and "effective_available_output_bytes" not in safe


def test_legacy_absence_and_explicit_no_cap_remain_distinct_without_guessing_12000():
    legacy = {key: value for key, value in failure_row().items() if key not in NEW_FIELDS}
    clean = public_budget_failure(legacy)
    assert NEW_FIELDS.isdisjoint(clean)
    explicit = {**legacy, "call_allowance": None, "effective_available_output_bytes": 1000,
        "output_limit_categories": ["global"]}
    assert public_budget_failure(explicit)["call_allowance"] is None
    assert js_project([legacy, explicit]) == [clean, public_budget_failure(explicit)]


def test_normal_api_markdown_csv_zip_and_js_preserve_the_same_safe_output_failure(tmp_path):
    store = ProjectStore(tmp_path / "SYNTHETIC_budget_exports")
    pid = store.create_project("SYNTHETIC output budget receipts")["project_id"]
    run = store.create_run(pid, ResearchBrief(disease="SYNTHETIC disease", research_mode="co_scientist"))
    run.status = ExecutionStatus.PARTIAL
    row = failure_row()
    run.co_scientist = CoScientistState(budget_failures=[copy.deepcopy(row)],
        budget={"budget_failures": [copy.deepcopy(row)], "output_bytes_received": 19230,
                "output_tokens_reserved": 2042, "output_bytes_inflight": 0},
        events=[AgentEvent(event_id="event_synthetic", sequence=1, agent_id="supervisor", kind="llm",
            status="failed", title=row["stage"], created_at="SYNTHETIC", summary="SYNTHETIC output rejected",
            operation_id="llm_002", operation_status="failed", budget_failure=copy.deepcopy(row))])
    store.save_run(run)
    original = run.model_dump_json()
    expected = public_failure_records(run.co_scientist)
    assert len(expected) == 1
    settings = ScientistSettings(workspace=str(store.root), llm={"backend": "none"})
    with TestClient(create_app(settings, service=SimpleNamespace(store=store, shutdown=lambda: None))) as client:
        endpoint = f"/api/projects/{pid}/runs/{run.run_id}"
        api = client.get(endpoint + "/results").json()
        exported = client.get(endpoint + "/export", params={"format": "json"}).json()
        markdown = client.get(endpoint + "/export", params={"format": "markdown"}).content
        packed = client.get(endpoint + "/export", params={"format": "zip"}).content
    for value in (api, exported):
        assert value["failure_records"] == expected
        assert value["co_scientist"]["budget"]["output_bytes_received"] == 19230
        assert value["co_scientist"]["budget"]["output_tokens_reserved"] == 2042
    blocks = [json.loads(text) for text in re.findall(r"```json\n(.*?)\n```", markdown.decode(), flags=re.DOTALL)]
    assert next(item for item in blocks if item.get("receipt_id") == row["receipt_id"]) == expected[0]
    csv_data = table_exports(run)["budget_failures.csv"]
    csv_rows = list(csv.DictReader(io.StringIO(csv_data.decode("utf-8-sig"))))
    assert len(csv_rows) == 1
    for key in ("required_output_bytes", "remaining_output_bytes", "reserved_output_bytes", "inflight_output_bytes",
                "call_allowance", "effective_available_output_bytes"):
        assert int(csv_rows[0][key]) == expected[0][key]
    assert json.loads(csv_rows[0]["output_limit_categories"]) == expected[0]["output_limit_categories"]
    assert csv_rows[0]["accounting_unit"] == "utf8_bytes"
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert json.loads(archive.read("research.json"))["failure_records"] == expected
        assert archive.read("budget_failures.csv") == csv_data
        assert archive.read("report.md") == markdown
    assert js_project([row])[0] == public_budget_failure(row)
    assert PRIVATE not in json.dumps(api) + json.dumps(exported) + markdown.decode() + csv_data.decode("utf-8-sig")
    assert run.model_dump_json() == original

