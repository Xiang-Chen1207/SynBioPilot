"""SYNTHETIC provider receipts using production inference/coordinator entry points.

Every subprocess/network entry is blocked or replaced by a transparent stub.
Only this test's temporary output/cache files are used; logs are never read.
"""
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
import requests

from scientist.budgets import RunLedger
from scientist.co_models import AgentState, CoScientistState
from scientist.coscientist import CoScientistEngine, role_system
from scientist.llm import InferenceError, StructuredLLM, serialized_request, strict_schema
from scientist.models import LLMSettings, ResearchBrief, ScientistSettings
from scientist.store import ProjectStore, digest

SCHEMA = {"type": "object", "properties": {"value": {"type": "integer"}},
          "required": ["value"], "additionalProperties": False}
PRIVATE = "PRIVATE_PROVIDER_SENTINEL"
SYSTEM = "SYNTHETIC system raw_prompt=" + PRIVATE
USER = "SYNTHETIC input api_key=" + PRIVATE
TITLE = "SYNTHETIC provider classification"
FIELDS = {"version", "category", "stage", "elapsed_seconds", "returncode", "signal",
          "output_exists", "output_is_file", "output_size_bytes", "output_stat_status", "origin", "backend"}


@pytest.fixture(autouse=True)
def prohibit_external_process_network_and_log_reads(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SYNTHETIC fixture forbids live process/network access")
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("system", "popen"):
        monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(requests.api, "request", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: "/SYNTHETIC/codex")
    original_text, original_bytes = Path.read_text, Path.read_bytes
    def guarded_text(path, *args, **kwargs):
        if path.suffix == ".log" or path.name in {"auth.json", "config.toml"}:
            raise AssertionError("Logs/auth/config must not be read")
        return original_text(path, *args, **kwargs)
    def guarded_bytes(path, *args, **kwargs):
        if path.suffix == ".log" or path.name in {"auth.json", "config.toml"}:
            raise AssertionError("Logs/auth/config must not be read")
        return original_bytes(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_bytes)


def install_process_stub(monkeypatch, tmp_path, *, returncode=0, body='{"value": 1}', mode="normal"):
    observed = {"calls": 0, "answer_reads": 0, "answer_stats": [], "answer_path": None}
    original_read, original_stat, original_open = Path.read_text, Path.stat, Path.open
    def open_file(path, *args, **kwargs):
        mode_value = args[0] if args else kwargs.get("mode", "r")
        if (path.name.endswith(".answer.json") and "r" in mode_value
                and (returncode or mode in {"timeout", "timeout_stat_error"})):
            raise AssertionError("Nonzero/timeout answer body must never be opened for reading")
        return original_open(path, *args, **kwargs)
    def read(path, *args, **kwargs):
        if path.name.endswith(".answer.json"):
            observed["answer_reads"] += 1
            if returncode or mode in {"timeout", "timeout_stat_error"}:
                raise AssertionError("Nonzero/timeout answer body must never be read")
            if mode == "read_error":
                raise OSError("secret read /Users/private/" + PRIVATE)
        return original_read(path, *args, **kwargs)
    def stat(path, *args, **kwargs):
        if path.name.endswith(".answer.json"):
            observed["answer_stats"].append(kwargs.get("follow_symlinks", True))
            if mode in {"stat_error", "timeout_stat_error"}:
                raise OSError("secret stat /Users/private/" + PRIVATE)
        return original_stat(path, *args, **kwargs)
    def run(args, **options):
        observed["calls"] += 1
        assert args[0] == "/SYNTHETIC/codex" and args[1] == "exec"
        assert "--ignore-user-config" in args and "--ephemeral" in args
        assert options["check"] is False and options["text"] is True
        output = Path(args[args.index("--output-last-message") + 1])
        observed["answer_path"] = output
        assert output.resolve().is_relative_to(tmp_path.resolve())
        schema_path = Path(args[args.index("--output-schema") + 1])
        expected_args = ["/SYNTHETIC/codex", "exec", "--model", "SYNTHETIC_OFFLINE_MODEL",
            "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "--sandbox", "read-only",
            "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
            "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
            "-c", "features.multi_agent=false", "-c", "project_doc_max_bytes=0",
            "--output-schema", str(schema_path), "--output-last-message", str(output),
            "--cd", str(output.parent), "-"]
        argv_bytes = json.dumps(args, ensure_ascii=False).encode()
        assert argv_bytes == json.dumps(expected_args, ensure_ascii=False).encode()
        assert json.loads(schema_path.read_text()) == strict_schema(SCHEMA)
        # Count the exact transmitted schema bytes, including UTF-8 and newline.
        assert serialized_request(LLMSettings(backend="codex"), SYSTEM, USER, SCHEMA) == (
            SYSTEM + "\n\n" + USER + schema_path.read_text())
        assert options["timeout"] == 10
        observed.update(argv_matches_prior_contract=True, argv_bytes_sha256=hashlib.sha256(argv_bytes).hexdigest(),
            parsed_schema_matches=True, timeout_seconds=options["timeout"],
            input_sha256=hashlib.sha256(options["input"].encode()).hexdigest())
        if mode == "launch_error":
            raise OSError("secret launch /Users/private/" + PRIVATE)
        if mode == "directory":
            output.mkdir()
        elif mode == "symlink":
            target = tmp_path / "SYNTHETIC_link_target.json"
            target.write_text(body or '{"value": 1}')
            output.symlink_to(target)
        elif mode == "unicode_error":
            output.write_bytes(b"\xff")
        elif body is not None:
            output.write_text(body)
        if mode in {"timeout", "timeout_stat_error"}:
            raise subprocess.TimeoutExpired(["SYNTHETIC", PRIVATE], options["timeout"], output=PRIVATE, stderr=PRIVATE)
        return SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(subprocess, "run", run)
    return observed


def make_route(tmp_path, route, *, backend="codex"):
    settings = LLMSettings(backend=backend, model="SYNTHETIC_OFFLINE_MODEL",
                           executable="SYNTHETIC_STUB", timeout_seconds=10)
    if route == "worker":
        worker = StructuredLLM(settings, tmp_path / "SYNTHETIC_worker", max_calls=3)
        return SimpleNamespace(worker=worker, engine=None,
            invoke=lambda: worker.complete(SYSTEM, USER, SCHEMA), settings=settings)
    store = ProjectStore(tmp_path / "SYNTHETIC_workspace")
    pid = store.create_project("SYNTHETIC provider receipts")["project_id"]
    run = store.create_run(pid, ResearchBrief(disease="SYNTHETIC disease", research_mode="co_scientist"))
    workers = []
    def factory(*args, **kwargs):
        worker = StructuredLLM(*args, **kwargs)
        workers.append(worker)
        return worker
    legacy = SimpleNamespace(store=store, settings=ScientistSettings(llm=settings, workspace=str(store.root)),
                             llm_factory=factory)
    engine = CoScientistEngine(legacy)
    engine.run, engine.directory = run, store.run_dir(pid, run.run_id)
    engine.sources = SimpleNamespace(search_count=0)
    engine.ledger = RunLedger(run.brief.budget, time.monotonic() + 60)
    engine.state = run.co_scientist = CoScientistState(
        agents=[AgentState(agent_id="supervisor", role="SYNTHETIC supervisor")])
    return SimpleNamespace(engine=engine, workers=workers, settings=settings,
        invoke=lambda: engine.invoke("supervisor", TITLE, SYSTEM, USER, SCHEMA))


def check_failure(route, error, category, stage, *, backend="codex"):
    receipt = error.failure_metadata
    assert set(receipt) == FIELDS
    assert receipt["version"] == 1 and receipt["origin"] == "provider_call"
    assert receipt["category"] == category and receipt["stage"] == stage and receipt["backend"] == backend
    assert type(receipt["elapsed_seconds"]) in (int, float)
    assert math.isfinite(receipt["elapsed_seconds"]) and receipt["elapsed_seconds"] >= 0
    for name in ("returncode", "signal", "output_size_bytes"):
        assert receipt[name] is None or type(receipt[name]) is int
    for name in ("output_exists", "output_is_file"):
        assert receipt[name] is None or type(receipt[name]) is bool
    assert PRIVATE not in json.dumps(receipt, allow_nan=False) and PRIVATE not in str(error)
    if route.engine:
        engine = route.engine
        failure = engine.state.budget_failures[-1]
        event = next(e for e in reversed(engine.state.events) if e.operation_status == "failed")
        assert failure["provider_failure"] == event.provider_failure == receipt
        assert PRIVATE not in json.dumps(failure, allow_nan=False)
        assert PRIVATE not in json.dumps(event.model_dump(), allow_nan=False)
        assert failure["operation_id"] == event.operation_id == "llm_001"
        assert failure["stage"] == event.title == TITLE
        assert engine.actual_calls == engine.ledger.counts["llm_calls"] == 1
        assert engine.ledger.counts["input_tokens_reserved"] == len(serialized_request(
            route.settings, role_system("supervisor") + "\n" + SYSTEM, USER, SCHEMA).encode())
        assert engine.ledger.counts["output_bytes_received"] == engine.ledger.counts["output_tokens_reserved"] == 0
    else:
        assert route.worker.calls == 1
    return receipt


@pytest.mark.parametrize("route_name", ["worker", "coordinator"])
@pytest.mark.parametrize("returncode,body,category,stage", [
    (2, None, "process_nonzero", "process"), (2, '{"value": 1}', "process_nonzero", "process"),
    (-9, '{"value": 1}', "process_nonzero", "process"),
    (0, None, "answer_missing", "output_stat"), (0, "", "answer_empty", "output_read"),
    (0, "  \n\t", "answer_empty", "output_read"),
    (0, "SYNTHETIC bad JSON " + PRIVATE, "json_invalid", "json_parse"),
    (0, '{"value": "' + PRIVATE + '"}', "schema_invalid", "schema_validation"),
])
def test_process_output_classification_and_shared_coordinator_accounting(tmp_path, monkeypatch, route_name, returncode, body, category, stage):
    observed = install_process_stub(monkeypatch, tmp_path, returncode=returncode, body=body)
    route = make_route(tmp_path, route_name)
    with pytest.raises(InferenceError) as failed:
        route.invoke()
    receipt = check_failure(route, failed.value, category, stage)
    assert observed["calls"] == 1
    assert receipt["returncode"] == returncode
    assert receipt["signal"] == (9 if returncode == -9 else None)
    assert receipt["output_exists"] is (body is not None)
    assert receipt["output_is_file"] is (body is not None)
    assert receipt["output_size_bytes"] == (len(body.encode()) if body is not None else None)
    assert receipt["output_stat_status"] == ("observed" if body is not None else "missing")
    assert observed["answer_stats"] and all(flag is False for flag in observed["answer_stats"])
    if returncode:
        assert observed["answer_reads"] == 0
    worker = route.workers[0] if route.engine else route.worker
    identity = {"backend": "codex", "model": route.settings.model, "base_url": "",
        "system": role_system("supervisor") + "\n" + SYSTEM if route.engine else SYSTEM,
        "user": USER, "schema": SCHEMA}
    assert not (worker.directory / (digest(identity) + ".json")).exists()


@pytest.mark.parametrize("route_name", ["worker", "coordinator"])
@pytest.mark.parametrize("body", [None, '{"value": ', '{"value": 1}'])
def test_timeout_preserves_partial_file_facts_without_reading_body(tmp_path, monkeypatch, route_name, body):
    observed = install_process_stub(monkeypatch, tmp_path, body=body, mode="timeout")
    route = make_route(tmp_path, route_name)
    with pytest.raises(InferenceError) as failed:
        route.invoke()
    receipt = check_failure(route, failed.value, "timeout", "process")
    assert observed["calls"] == 1 and observed["answer_reads"] == 0
    assert receipt["returncode"] is None and receipt["signal"] is None
    assert receipt["output_size_bytes"] == (len(body.encode()) if body is not None else None)
    if route.engine:
        assert route.engine.prior_timeout is True
        assert route.engine.state.budget_failures[-1]["reason"] == "provider_timeout"


@pytest.mark.parametrize("mode,category,stage", [
    ("launch_error", "launch_error", "process"), ("stat_error", "read_error", "output_stat"),
    ("read_error", "read_error", "output_read"), ("directory", "read_error", "output_stat"),
    ("symlink", "read_error", "output_stat"), ("unicode_error", "read_error", "output_read"),
])
def test_os_errors_and_nonregular_outputs_have_safe_structured_receipts(tmp_path, monkeypatch, mode, category, stage):
    observed = install_process_stub(monkeypatch, tmp_path, mode=mode)
    route = make_route(tmp_path, "coordinator")
    with pytest.raises(InferenceError) as failed:
        route.invoke()
    receipt = check_failure(route, failed.value, category, stage)
    if mode in {"directory", "symlink"}:
        assert receipt["output_exists"] is True and receipt["output_is_file"] is False
        assert observed["answer_reads"] == 0
    if mode == "stat_error":
        assert receipt["output_stat_status"] == "error" and receipt["output_exists"] is None
    assert observed["calls"] == 1


@pytest.mark.parametrize("primary", ["nonzero", "timeout"])
def test_secondary_stat_failure_does_not_replace_primary_process_failure(tmp_path, monkeypatch, primary):
    mode = "timeout_stat_error" if primary == "timeout" else "stat_error"
    observed = install_process_stub(monkeypatch, tmp_path, mode=mode, returncode=3 if primary == "nonzero" else 0)
    route = make_route(tmp_path, "coordinator")
    with pytest.raises(InferenceError) as failed:
        route.invoke()
    receipt = check_failure(route, failed.value, "timeout" if primary == "timeout" else "process_nonzero", "process")
    assert receipt["output_stat_status"] == "error"
    assert receipt["output_exists"] is receipt["output_is_file"] is receipt["output_size_bytes"] is None
    assert observed["answer_reads"] == 0


@pytest.mark.parametrize("mode", ["missing_executable", "lookup_error", "shape_prepare", "log_prepare", "path_prepare"])
def test_launch_and_preparation_failures_are_receipted_before_any_stub_process(tmp_path, monkeypatch, mode):
    observed = install_process_stub(monkeypatch, tmp_path)
    route = make_route(tmp_path, "coordinator")
    if mode == "missing_executable":
        monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    elif mode == "lookup_error":
        def lookup(*args, **kwargs):
            raise OSError("secret lookup /Users/" + PRIVATE)
        monkeypatch.setattr(shutil, "which", lookup)
    elif mode == "shape_prepare":
        from scientist import llm
        original = llm.write_json
        def write(path, value, **options):
            if path.name.endswith(".schema.json"):
                raise OSError("secret shape /Users/" + PRIVATE)
            return original(path, value, **options)
        monkeypatch.setattr(llm, "write_json", write)
    elif mode == "log_prepare":
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.suffix == ".log":
                raise OSError("secret log /Users/" + PRIVATE)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", open_file)
    else:
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path.name.endswith(".schema.json"):
                raise OSError("secret resolve /Users/" + PRIVATE)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(InferenceError) as failed:
        route.invoke()
    receipt = check_failure(route, failed.value, "launch_error",
        "executable_lookup" if mode in {"missing_executable", "lookup_error"} else "prepare")
    assert observed["calls"] == 0 and receipt["returncode"] is None


def test_success_keeps_output_metering_and_cache_hit_validates_before_zero_dispatch(tmp_path, monkeypatch):
    observed = install_process_stub(monkeypatch, tmp_path)
    worker_route = make_route(tmp_path / "worker", "worker")
    assert worker_route.invoke() == {"value": 1}
    assert worker_route.invoke() == {"value": 1}
    assert observed["calls"] == 1 and worker_route.worker.calls == worker_route.worker.cache_hits == 1
    route = make_route(tmp_path / "co", "coordinator")
    assert route.invoke() == {"value": 1}
    assert route.engine.state.budget_failures == []
    assert route.engine.ledger.counts["llm_calls"] == route.engine.actual_calls == 1
    assert route.engine.ledger.counts["output_bytes_received"] == len(json.dumps({"value": 1}).encode())
    assert route.engine.ledger.counts["output_tokens_reserved"] == len(json.dumps({"value": 1}).encode())


@pytest.mark.parametrize("cached_body,error_type", [('not JSON', json.JSONDecodeError),
    ('{"result": {"value": "bad"}}', jsonschema.ValidationError)])
def test_invalid_cache_is_validated_without_attempt_or_provider_failure_origin(tmp_path, monkeypatch, cached_body, error_type):
    observed = install_process_stub(monkeypatch, tmp_path)
    route = make_route(tmp_path, "worker")
    identity = {"backend": "codex", "model": route.settings.model, "base_url": "",
                "system": SYSTEM, "user": USER, "schema": SCHEMA}
    cached = route.worker.directory / (digest(identity) + ".json")
    cached.write_text(cached_body)
    with pytest.raises(error_type) as failed:
        route.invoke()
    assert observed["calls"] == route.worker.calls == route.worker.cache_hits == 0
    assert not route.worker.attempt_path.exists()
    assert not getattr(failed.value, "failure_metadata", None)


@pytest.mark.parametrize("backend", ["responses", "compatible"])
@pytest.mark.parametrize("raw,category,stage", [('SYNTHETIC ' + PRIVATE, "json_invalid", "json_parse"),
    ('{"value": "' + PRIVATE + '"}', "schema_invalid", "schema_validation")])
def test_api_schema_errors_have_backend_receipt_without_real_requests(tmp_path, monkeypatch, backend, raw, category, stage):
    monkeypatch.setattr(StructuredLLM, "_api", lambda *a, **k: raw)
    route = make_route(tmp_path, "coordinator", backend=backend)
    with pytest.raises(InferenceError) as failed:
        route.invoke()
    receipt = check_failure(route, failed.value, category, stage, backend=backend)
    assert receipt["returncode"] is None and receipt["output_stat_status"] == "not_checked"


def test_failure_metadata_allowlist_rejects_injected_private_fields_and_nonfinite_numbers(tmp_path):
    injected = {"version": 1, "category": "process_nonzero", "stage": "process", "origin": "provider_call",
        "backend": "codex", "returncode": 2, "signal": None, "elapsed_seconds": float("nan"),
        "output_exists": True, "output_is_file": True, "output_size_bytes": -1,
        "output_stat_status": "observed", "raw_prompt": PRIVATE, "headers": {"Authorization": PRIVATE},
        "command": "/Users/private/" + PRIVATE, "stderr": PRIVATE}
    failure = InferenceError("SYNTHETIC safe classification", failure_metadata=injected)
    clean = failure.failure_metadata
    assert set(clean).issubset(FIELDS)
    assert PRIVATE not in json.dumps(clean, allow_nan=False)
    assert clean.get("elapsed_seconds") is None or math.isfinite(clean["elapsed_seconds"])
    assert clean.get("output_size_bytes") is None or clean["output_size_bytes"] >= 0


def test_timeout_keyword_without_typed_timeout_or_receipt_never_sets_prior_timeout(tmp_path):
    route = make_route(tmp_path, "coordinator")
    worker = SimpleNamespace(calls=1, cache_hits=0)
    def fail(*args, **kwargs):
        raise InferenceError("SYNTHETIC timeout keyword without actual timeout event")
    worker.complete = fail
    route.engine.legacy.llm_factory = lambda *args, **kwargs: worker
    with pytest.raises(InferenceError):
        route.invoke()
    failure = route.engine.state.budget_failures[-1]
    assert failure["reason"] == "provider_failure" and failure["prior_timeout"] is False
    assert route.engine.prior_timeout is False
    assert "provider_failure" not in failure
    assert not route.engine.state.events[-1].provider_failure


@pytest.mark.parametrize("outcome", ["nonzero", "timeout", "success"])
def test_log_close_failure_cannot_override_primary_process_result(tmp_path, monkeypatch, outcome):
    observed = install_process_stub(monkeypatch, tmp_path, returncode=2 if outcome == "nonzero" else 0,
                                    mode="timeout" if outcome == "timeout" else "normal")
    route = make_route(tmp_path, "coordinator")
    original_open = Path.open
    closed = []
    class FailingClose:
        def __init__(self, stream):
            self.stream = stream
        def close(self):
            self.stream.close()
            closed.append(True)
            raise OSError("secret close /Users/private/" + PRIVATE)
        def __getattr__(self, name):
            return getattr(self.stream, name)
    def open_log(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return FailingClose(stream) if path.suffix == ".log" else stream
    monkeypatch.setattr(Path, "open", open_log)
    if outcome == "success":
        assert route.invoke() == {"value": 1}
        assert route.engine.state.budget_failures == []
        assert route.engine.ledger.counts["output_bytes_received"] == len(json.dumps({"value": 1}).encode())
    else:
        with pytest.raises(InferenceError) as failed:
            route.invoke()
        check_failure(route, failed.value, "timeout" if outcome == "timeout" else "process_nonzero", "process")
        assert observed["answer_reads"] == 0
    assert observed["calls"] == len(closed) == 1

