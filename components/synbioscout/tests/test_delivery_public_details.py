"""SYNTHETIC replicas of public budget facts; no historical run/model/network access.

71585/49392 and the four skipped stages reproduce the recorded public failure
shape only. They are not a replay of the historical model's private request.
"""
import copy
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scientist.api import create_app
from scientist.co_models import CoScientistState
from scientist.models import (
    EvidenceRecord,
    ExecutionStatus,
    ResearchBrief,
    ScientistSettings,
    SourceRecord,
)
from scientist.presentation import result_document
from scientist.reports import render_report, source_scope_label
from scientist.source_scope import source_scope_view
from scientist.store import ProjectStore
from scientist.tool_public import public_budget_failure, public_failure_records


def budget_records(*, identified=False):
    values = [
        ("supplemental_queries", 0, 110859, 211.488, 132526, 2, 386.155),
        ("claim_novelty_queries", 0, 110859, 211.478, 132526, 2, 386.155),
        ("feedback_queries", 0, 49392, 114.122, 83585, 1, 195.577),
        ("hypothesis_revision", 73041, 49392, 114.103, 166801, 2, 386.155),
        ("总审与最多三个研究合同", 71585, 49392, 114.094, 0, 0, 0),
    ]
    records = []
    for index, (stage, required, remaining, seconds, reserved, calls, reserve_seconds) in enumerate(values):
        records.append({"stage": stage, "required_bytes": required, "remaining_bytes": remaining,
            "required_calls": int(required > 0), "remaining_calls": 11 if index < 2 else 10,
            "required_output_bytes": 12000 if required else 0,
            "remaining_output_bytes": 69834 if index < 2 else 58617,
            "required_seconds": 0 if index == 4 else 190.577 if index == 3 else 10,
            "remaining_seconds": seconds, "reserved_bytes": reserved, "reserved_calls": calls,
            "reserved_output_bytes": calls * 12000, "reserved_seconds": reserve_seconds,
            "accounting_unit": "utf8_bytes", "blocked_before_provider": True, "prior_timeout": False,
            "reason": "Input reservation budget exhausted" if index == 4 else "Wall-time scheduling allowance exhausted",
            "raw_prompt": "PRIVATE_SENTINEL", "stderr": "PRIVATE_SENTINEL",
            **({"receipt_id": f"budget_action_{index + 1:04d}", "operation_id": f"budget_action_{index + 1:04d}"} if identified else {})})
    return records


def synthetic_run(tmp_path, *, identified=False):
    store = ProjectStore(tmp_path / "SYNTHETIC_workspace")
    pid = store.create_project("SYNTHETIC delivery public facts")["project_id"]
    run = store.create_run(pid, ResearchBrief(disease="SYNTHETIC disease", research_mode="co_scientist"))
    run.status = ExecutionStatus.PARTIAL
    records = budget_records(identified=identified)
    run.co_scientist = CoScientistState(budget_failures=copy.deepcopy(records), optional_skips=copy.deepcopy(records[:4]),
        budget={"budget_failures": copy.deepcopy(records), "optional_skips": copy.deepcopy(records[:4])})
    quote = "SYNTHETIC continuous results with mixed endpoints and stated limitations."
    run.sources = [SourceRecord(source_id="synthetic:original_fulltext_excerpt", provider="europe_pmc",
        title="SYNTHETIC primary paper", url="https://example.invalid/SYNTHETIC", retrieved_at="SYNTHETIC",
        evidence_scope="abstract", text=quote + "x" * (2262-len(quote)), metadata={
            "synthetic": True, "not_a_real_publication": True, "original_evidence_scope": "full_text",
            "local_excerpt_only": True, "excerpt_start": 11889, "excerpt_end": 14151,
            "acquisition_origin": "background_reuse", "raw_prompt": "PRIVATE_SENTINEL"})]
    run.evidence = [EvidenceRecord(evidence_id="ev_SYNTHETIC", source_id=run.sources[0].source_id,
        claim="SYNTHETIC limited observation", quote=quote, location="SYNTHETIC source window",
        polarity="mixed", validated_span=True)]
    store.save_run(run)
    return store, run


def test_observed_budget_shape_and_four_skips_have_identical_public_api_md_json_zip_records(tmp_path):
    store, run = synthetic_run(tmp_path)
    path = store.run_dir(run.project_id, run.run_id) / "run.json"
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    original = run.model_dump_json()
    expected = public_failure_records(run.co_scientist)
    assert len([r for r in expected if "optional_skip" in r["record_types"]]) == 4
    assert any(r["required_bytes"] == 71585 and r["remaining_bytes"] == 49392 for r in expected)
    settings = ScientistSettings(workspace=str(store.root), llm={"backend": "none"})
    with TestClient(create_app(settings, service=SimpleNamespace(store=store, shutdown=lambda: None))) as client:
        endpoint = f"/api/projects/{run.project_id}/runs/{run.run_id}"
        public = client.get(endpoint + "/results").json()
        exported = client.get(endpoint + "/export", params={"format": "json"}).json()
        markdown = client.get(endpoint + "/export", params={"format": "markdown"}).content
        packed = client.get(endpoint + "/export", params={"format": "zip"}).content
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        zipped = json.loads(archive.read("research.json"))
        assert archive.read("report.md") == markdown
    report_rows = [json.loads(row) for row in re.findall(r"```json\n(.*?)\n```", markdown.decode(), re.DOTALL)]
    assert report_rows == public["failure_records"] == exported["failure_records"] == zipped["failure_records"] == expected
    for row in report_rows:
        assert row["blocked_before_provider"] is True and row["accounting_unit"] == "utf8_bytes"
        assert all(key in row for key in ("required_bytes", "remaining_bytes", "reserved_bytes", "reserved_calls", "reserved_output_bytes", "reserved_seconds"))
        assert "receipt_id" not in row and "operation_id" not in row
    assert all(stage in markdown.decode() for stage in ("supplemental_queries", "claim_novelty_queries", "feedback_queries", "hypothesis_revision"))
    assert "UTF-8 字节" in markdown.decode() and "PRIVATE_SENTINEL" not in markdown.decode()
    assert "PRIVATE_SENTINEL" not in json.dumps(public)
    assert run.model_dump_json() == original and hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_identified_failure_skip_event_mirrors_merge_only_with_exact_action_payload(tmp_path):
    _, run = synthetic_run(tmp_path, identified=True)
    value = run.co_scientist.model_dump()
    first = value["budget_failures"][0]
    value["events"] = [{"budget_failure": copy.deepcopy(first)}]
    rows = public_failure_records(value)
    assert len(rows) == 5
    assert rows[0]["record_types"] == ["budget_failure", "optional_skip", "budget_event"]
    assert {r["receipt_id"] for r in rows} == {f"budget_action_{i:04d}" for i in range(1, 6)}
    different_action = {**first, "receipt_id": "budget_action_0099", "operation_id": "budget_action_0099"}
    changed_payload = {**first, "remaining_bytes": first["remaining_bytes"]-1}
    value["budget_failures"].extend([different_action, changed_payload])
    assert len(public_failure_records(value)) == 7


def test_legacy_duplicates_within_one_list_are_not_mistaken_for_mirrored_actions():
    row = {"stage": "SYNTHETIC repeated stage", "required_bytes": 71585, "remaining_bytes": 49392}
    co = {"budget_failures": [row, row], "budget": {"budget_failures": [row, row]}, "optional_skips": [row]}
    result = public_failure_records(co)
    assert len(result) == 3  # Two budget actions; the legacy skip relationship is unknown.
    assert all("receipt_id" not in r and "operation_id" not in r for r in result)
    assert public_failure_records(None) == [] and public_failure_records({}) == []


def test_source_window_keeps_original_fulltext_scope_in_md_and_safe_json(tmp_path):
    _, run = synthetic_run(tmp_path)
    original = run.model_dump_json()
    expected = source_scope_view(run.sources[0])
    assert expected["original_scope"] == "full_text" and expected["current_scope"] == "local_excerpt"
    assert expected["original_window"] == {"start": 11889, "end": 14151}
    assert expected["acquisition"] == "reused" and expected["fresh_fulltext"] is False
    document = result_document(run)
    assert document["sources"][0]["metadata"]["source_scope"] == expected
    report = render_report(run)
    assert "原全文的已存连续摘录" in report and "[11889, 14151)" in report
    assert "复用材料 · 非本轮全文读取" in report
    assert "范围：abstract" not in report
    assert run.sources[0].evidence_scope == "abstract" and run.model_dump_json() == original


def test_failure_fields_and_source_scope_match_javascript_projection():
    node = os.environ.get("SYNBIOSCOUT_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node is needed only for cross-language projection parity")
    app = Path(__file__).resolve().parents[1] / "scientist/web/app.js"
    rows = budget_records(identified=True)
    co = {"budget_failures": rows, "optional_skips": rows[:4], "budget": {"budget_failures": rows}, "events": [{"budget_failure": rows[0]}]}
    sources = [
        {"evidence_scope": "abstract", "metadata": {"original_evidence_scope": "full_text", "local_excerpt_only": True,
            "excerpt_start": 11889, "excerpt_end": 14151, "acquisition_origin": "background_reuse"}},
        {"evidence_scope": "full_text"}, {"evidence_scope": "abstract"}, {},
        {"evidence_scope": "full_text", "metadata": {"query_receipt": {"operation": "literature_search", "cache_origin": "live_response"}}},
        {"evidence_scope": "full_text", "metadata": {"acquisition_origin": "current_run_full_text"}},
        {"evidence_scope": "database", "metadata": {"acquisition": "background_reuse"}},
        {"evidence_scope": "full_text", "metadata": {"local_excerpt_only": True, "excerpt_start": -1, "excerpt_end": 3}},
        {"evidence_scope": "full_text", "metadata": {"acquisition_origin": "current_run_retrieval",
            "query_receipt": {"cache_origin": "exact_query_http_cache"}}},
    ]
    code = r'''const fs=require("node:fs"),vm=require("node:vm");const c={};vm.createContext(c);
const src=fs.readFileSync(process.argv[1],"utf8").replace(/\ninit\(\);\s*$/,"");
vm.runInContext(src+"\nglobalThis.api={publicFailureRecords,sourceScopeView,sourceScopeLabel};",c);
const input=JSON.parse(fs.readFileSync(0,"utf8"));process.stdout.write(JSON.stringify({records:c.api.publicFailureRecords(input.co),scopes:input.sources.map(c.api.sourceScopeView),labels:input.sources.map(c.api.sourceScopeLabel)}));'''
    result = subprocess.run([node, "-e", code, str(app)], input=json.dumps({"co": co, "sources": sources}),
        text=True, capture_output=True, check=True, timeout=20)
    actual = json.loads(result.stdout)
    assert actual["records"] == public_failure_records(co)
    assert actual["scopes"] == [source_scope_view(s) for s in sources]
    # Locale differs from the retained report language; canonical projection
    # equality above still enforces the same range/acquisition semantics.
    assert actual["labels"] == [
        "Original Full text · saved contiguous excerpt · original text window unverified · Reused saved excerpt",
        "Full text · fresh full-text retrieval unconfirmed",
        "Abstract only",
        "Material with unrecorded scope",
        "Full text · retrieved this run; full-text completeness requires review",
        "Full text · full text newly retrieved this run",
        "Database record · reused saved material",
        "Original Full text · saved contiguous excerpt · original text window unverified · Acquisition not recorded · excerpt scope",
        "Full text · reused saved material",
    ]
    assert "PRIVATE_SENTINEL" not in result.stdout
    assert public_budget_failure(rows[0])["receipt_id"] == "budget_action_0001"


def test_actual_narrow_observation_gate_remains_needs_evidence_in_markdown_and_javascript():
    from test_claim_relative_observation import observation_case

    from scientist.co_models import HypothesisReview
    from scientist.coscientist import ReflectionDraft

    engine, _, card, _, evidence, _, claim_review = observation_case(polarity="mixed")
    engine.call = lambda *args, **kwargs: ReflectionDraft(reviews=[HypothesisReview(
        hypothesis_id=card.hypothesis_id, version=card.version, verdict="retain",
        reason="SYNTHETIC exact source observation only", valid_evidence_ids=[evidence.evidence_id])],
        claim_reviews=[claim_review])
    engine.review([card], "SYNTHETIC scoped observation review")
    document = result_document(engine.run)
    assessment = document["candidate_assessments"][0]
    assert assessment["scientific_status"] == "needs_evidence" and assessment["observation_support"]
    assert assessment["observation_support"][0]["polarity"] == "mixed"
    assert assessment["opposing_evidence_ids"] == [evidence.evidence_id]
    report = render_report(engine.run)
    assert "已审窄来源观察" in report and "整体假说结论和临床适用性分别评估" in report
    assert "原证据极性 mixed" in report and "候选科学状态：needs_evidence" in report
    node = os.environ.get("SYNBIOSCOUT_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node is needed only for cross-language projection parity")
    app = Path(__file__).resolve().parents[1] / "scientist/web/app.js"
    code = r'''const fs=require("node:fs"),vm=require("node:vm"),c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],"utf8").replace(/\ninit\(\);\s*$/,"")+"\nglobalThis.project=publicCandidateAssessment;",c);
process.stdout.write(JSON.stringify(c.project(JSON.parse(fs.readFileSync(0,"utf8")))));'''
    result = subprocess.run([node, "-e", code, str(app)], input=json.dumps(assessment),
        text=True, capture_output=True, check=True, timeout=20)
    projected = json.loads(result.stdout)
    assert projected["scientific_status"] == "needs_evidence"
    assert projected["observation_support"] == assessment["observation_support"]

