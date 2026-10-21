"""Offline, synthetic CSV fixtures for the public HDAC2 demo boundary."""
from __future__ import annotations

import csv
import io
import json
import socket
import subprocess

import pytest

from scientist.demos import (
    DEMO_ID, MAX_BYTES, MAX_CELL_CHARS, MAX_DISPLAY_PER_MODEL, MAX_ROWS,
    MAX_VALIDATION_ISSUES, demo_catalog, get_demo, inspect_demo_scores,
)

COLUMNS = {"model": "model", "compound_id": "compound_id", "score": "score",
           "status": "status", "error": "error"}
HEADER = "model,compound_id,score,status,error\n"


def csv_fixture(rows, header=None):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(header or list(COLUMNS))
    writer.writerows(rows)
    return output.getvalue()


def issue_codes(report):
    return {issue["code"] for issue in report["validation_issues"]}


def model_result(report, name):
    return next(item for item in report["by_model"] if item["model"] == name)


def test_catalog_provenance_benchmarks_and_resource_limits_are_explicit():
    catalog = demo_catalog()
    demo = catalog["demos"][0]
    assert catalog["schema_version"] == 1
    assert demo["id"] == DEMO_ID
    assert demo["target"] == {"gene": "HDAC2", "pdb_id": "4LXZ", "fixed_target": True}
    assert demo["local_execution"] == {"status": "not_run", "executed": False}
    assert demo["source"]["kind"] == "repository_report"
    assert demo["source"]["commit"] == "7d9236fdbc96da55fb6e92a1b0c7ce1a4f48d8dc"
    common, capacity = demo["scenarios"]
    assert (common["molecule_count"], capacity["molecule_count"]) == (50, 1000)
    assert "50 分子界面示例" in common["selection_note"]
    benchmark = common["reported_benchmark"]
    assert (benchmark["successful"], benchmark["attempted"]) == (200, 200)
    assert benchmark["online_seconds"] == 0.4411
    assert benchmark["preparation_seconds"] == 11.26
    assert benchmark["scope"] == "four_model_aggregate"
    assert benchmark["source_url"].endswith("/USAGE_zh.md")
    assert benchmark["partial_cache_reuse"] is True
    assert benchmark["verified_locally"] is False
    assert benchmark["online_timing_excludes"] == [
        "model_loading", "encoding", "conformer_generation", "warmup",
    ]
    assert benchmark["hardware"] == {"gpu": "H20", "cpu_cores": 64, "memory_gib": 128}
    large = capacity["reported_benchmark"]
    assert (large["successful"], large["attempted"]) == (3998, 4000)
    assert (large["online_seconds"], large["preparation_seconds"]) == (0.8605, 98.41)
    archive = next(resource for resource in demo["resources"] if resource["id"] == "archive")
    assert archive["size_mib"] == 38.36 and archive["size_source"] == "repository_report"
    assert "PSICHIC 权重" in archive["included"]
    assert "SaProt 原始大权重" in archive["excluded"]
    assert all(not item["downloaded"] and not item["validated_locally"]
               for item in demo["resources"])
    assert demo["csv_import"]["example_only"] is True
    assert "实际列名以导入 CSV 为准" in demo["csv_import"]["schema_note"]
    assert demo["csv_import"]["allowed_statuses"] == ["ok", "success", "failed"]
    assert demo["csv_import"]["status_aliases"] == {"success": "ok"}
    assert "重建" in " ".join(demo["limitations"])
    json.dumps(catalog, allow_nan=False)


def test_catalog_cards_are_fresh_and_unknown_id_is_not_a_real_demo():
    first = get_demo(DEMO_ID)
    first["source"]["kind"] = "local_run"
    first["limitations"].clear()
    assert get_demo(DEMO_ID)["source"]["kind"] == "repository_report"
    assert get_demo(DEMO_ID)["limitations"]
    with pytest.raises(FileNotFoundError):
        get_demo("other_target")


def test_functions_never_use_network_or_execute_commands(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Demo metadata and inspection must remain offline and non-executing")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert demo_catalog()["demos"][0]["local_execution"]["status"] == "not_run"
    report = inspect_demo_scores(HEADER + "DrugCLIP,C1,0.9,ok,\n", COLUMNS)
    assert report["status"] == "ok"
    assert report["provenance"] == {"kind": "user_imported_not_verified", "verified": False,
                                    "persisted": False, "evidence_created": False,
                                    "target_association": "not_verified",
                                    "candidate_pool_association": "not_verified",
                                    "preprocessing_version": "not_verified"}


def test_scores_rank_within_each_model_with_ties_and_no_scientific_upgrade():
    text = csv_fixture([
        ["DrugCLIP", "C-low", "1", "ok", ""],
        ["SPRINT", "C-other", "1000", "ok", ""],
        ["DrugCLIP", "C-high", "9", "ok", ""],
        ["DrugCLIP", "C-tie", "9", "ok", ""],
    ])
    report = inspect_demo_scores(text, COLUMNS)
    assert report["status"] == "ok" and report["accepted"] == 4 and report["rejected"] == 0
    assert report["summary"]["successful"] == 4
    drugclip = model_result(report, "DrugCLIP")
    assert [(row["compound_id"], row["rank"]) for row in drugclip["rows"]] == [
        ("C-high", 1), ("C-tie", 1), ("C-low", 3),
    ]
    assert model_result(report, "SPRINT")["rows"][0]["rank"] == 1
    assert report["summary"]["ranking_scope"] == "within_model_only"
    assert report["summary"]["rank_derived"] is True
    assert report["local_execution"] == {"status": "not_run", "executed": False}
    assert report["provenance"]["evidence_created"] is False
    assert report["provenance"]["target_association"] == "not_verified"
    limitations = " ".join(report["limitations"])
    assert "模型内排序" in limitations and "固定 HDAC2 输入" in limitations
    assert "候选池和预处理版本" in limitations and "median/MAD" in limitations
    serialized = json.dumps(report, allow_nan=False)
    assert "universal_score" not in serialized and "efficacy_score" not in serialized


def test_failed_rows_are_accepted_without_score_or_rank_and_error_preserved():
    report = inspect_demo_scores(csv_fixture([
        ["pharmaconet", "C1", "", "FAILED", "Matching timeout after 10 seconds"],
        ["PSICHIC", "C2", "-0.25", "Ok", ""],
    ]), COLUMNS)
    assert report["status"] == "ok" and report["accepted"] == 2
    assert report["summary"]["failed"] == 1 and report["summary"]["successful"] == 1
    failure = model_result(report, "PharmacoNet")["rows"][0]
    assert failure["status"] == "failed" and failure["score"] is None and failure["rank"] is None
    assert failure["error"] == "Matching timeout after 10 seconds"
    assert failure["error_redacted"] is False


def test_explicit_nondefault_column_mapping_works_without_error_column():
    mapping = {"model": "Algorithm", "compound_id": "ID", "score": "Value", "status": "State"}
    text = "ID,Value,State,Algorithm,private_payload\nC1,1e-4,ok,SPRINT,PRIVATE_ROW_VALUE\n"
    report = inspect_demo_scores(text, mapping)
    assert report["accepted"] == 1
    row = model_result(report, "SPRINT")["rows"][0]
    assert row["score"] == 0.0001 and row["error"] is None
    assert "PRIVATE_ROW_VALUE" not in json.dumps(report)
    assert "private_payload" not in json.dumps(report)


@pytest.mark.parametrize("text,code", [("", "empty_input"), ("  \n", "empty_input"),
                                     (HEADER, "no_data_rows")])
def test_empty_input_or_header_only_is_not_a_successful_run(text, code):
    report = inspect_demo_scores(text, COLUMNS)
    assert report["status"] == "empty"
    assert report["accepted"] == report["rejected"] == 0
    assert code in issue_codes(report)
    assert report["local_execution"]["executed"] is False


@pytest.mark.parametrize("score,code", [
    ("NaN", "non_finite_score"), ("nan", "non_finite_score"),
    ("Infinity", "non_finite_score"), ("-inf", "non_finite_score"),
    ("1e9999", "non_finite_score"), ("", "invalid_score"),
    ("nonsense", "invalid_score"), ("=1+2", "invalid_score"),
])
def test_scores_must_be_finite_and_json_safe(score, code):
    report = inspect_demo_scores(csv_fixture([["PSICHIC", "C1", score, "ok", ""]]), COLUMNS)
    assert report["status"] == "invalid" and report["rejected"] == 1
    assert code in issue_codes(report)
    assert model_result(report, "PSICHIC")["rows"] == []
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("row,code", [
    (["unknown-model-PRIVATE", "C1", "1", "ok", ""], "unknown_model"),
    (["DrugCLIP", "", "1", "ok", ""], "missing_compound_id"),
    (["DrugCLIP", "C1", "1", "completed", ""], "invalid_status"),
    (["DrugCLIP", "C1", "1", "failed", ""], "failed_score_not_empty"),
    (["DrugCLIP", "=HYPERLINK(SECRET)", "1", "ok", ""], "unsafe_compound_id"),
    (["DrugCLIP", "/Users/person/private", "1", "ok", ""], "unsafe_compound_id"),
    (["DrugCLIP", "sk-abcdefgh123456789", "1", "ok", ""], "unsafe_compound_id"),
    (["DrugCLIP", "ghp_abcdefgh123456789", "1", "ok", ""], "unsafe_compound_id"),
    (["DrugCLIP", "api_key", "1", "ok", ""], "unsafe_compound_id"),
    (["DrugCLIP", "C1", "1", "ok"], "row_width"),
])
def test_invalid_rows_are_rejected_without_raw_record_echo(row, code):
    report = inspect_demo_scores(csv_fixture([row]), COLUMNS)
    assert report["rejected"] == 1 and report["accepted"] == 0
    assert code in issue_codes(report)
    assert report["rejected_rows"] == [{"row_number": 2, "issue_codes": [code]}]
    serialized = json.dumps(report)
    for private in ["unknown-model-PRIVATE", "=HYPERLINK(SECRET)", "/Users/person/private",
                    "sk-abcdefgh123456789", "ghp_abcdefgh123456789", "api_key"]:
        assert private not in serialized


def test_success_is_the_only_explicit_additional_ok_alias():
    report = inspect_demo_scores(csv_fixture([["DrugCLIP", "C1", "1", "success", ""]]), COLUMNS)
    assert report["status"] == "ok" and report["accepted"] == 1
    assert model_result(report, "DrugCLIP")["rows"][0]["status"] == "ok"


def test_partial_report_counts_rejected_and_observed_per_model():
    report = inspect_demo_scores(csv_fixture([
        ["DrugCLIP", "C1", "0.5", "ok", ""],
        ["DrugCLIP", "C2", "NaN", "ok", "PRIVATE_ERROR"],
        ["SPRINT", "C3", "", "failed", "timeout"],
        ["Alien", "C4", "1", "ok", ""],
    ]), COLUMNS)
    assert report["status"] == "partial"
    assert report["accepted"] == report["rejected"] == 2
    assert report["summary"]["rows_observed"] == 4
    assert report["summary"]["models_observed"] == 2
    stats = model_result(report, "DrugCLIP")
    assert (stats["observed"], stats["accepted"], stats["rejected"]) == (2, 1, 1)
    assert "PRIVATE_ERROR" not in json.dumps(report)


def test_all_duplicate_model_id_occurrences_are_rejected_but_other_model_is_distinct():
    report = inspect_demo_scores(csv_fixture([
        ["DrugCLIP", "C1", "1", "ok", ""],
        ["drugclip", "C1", "2", "ok", ""],
        ["SPRINT", "C1", "2", "ok", ""],
    ]), COLUMNS)
    assert report["accepted"] == 1 and report["rejected"] == 2
    assert len(report["rejected_rows"]) == 2
    assert model_result(report, "DrugCLIP")["rows"] == []
    assert model_result(report, "SPRINT")["rows"][0]["compound_id"] == "C1"
    assert report["validation_issue_count"] == 2


@pytest.mark.parametrize("mapping,code", [
    (None, "invalid_mapping"), ({}, "missing_mapping_role"),
    ({**COLUMNS, "private": "header"}, "unknown_mapping_role"),
    ({**COLUMNS, "score": "model"}, "ambiguous_mapping"),
    ({**COLUMNS, "score": ""}, "invalid_mapping_header"),
    ({**COLUMNS, "score": ["score"]}, "invalid_mapping_header"),
    ({**COLUMNS, "score": "not_present"}, "missing_mapped_column"),
])
def test_mapping_is_explicit_and_unambiguous(mapping, code):
    report = inspect_demo_scores(HEADER + "DrugCLIP,C1,1,ok,\n", mapping)
    assert report["status"] == "invalid" and report["accepted"] == 0
    assert code in issue_codes(report)
    assert report["validation_issues"][0]["scope"] == "mapping"


@pytest.mark.parametrize("header", [
    ["model", "compound_id", "score", "status", "error", "score"],
    ["model", "compound_id", "score", "status", "error", " score "],
])
def test_duplicate_headers_are_rejected_even_when_duplicate_column_is_unmapped(header):
    report = inspect_demo_scores(csv_fixture([], header=header), COLUMNS)
    assert report["status"] == "invalid" and "duplicate_header" in issue_codes(report)


@pytest.mark.parametrize("error", [
    "API_KEY=fixture-private-value", "Bearer fixture-private-value",
    "sk-abcdefgh123456789", "ghp_abcdefgh123456789", "/Users/SYNTHETIC/private-file.json",
    "file:///tmp/private-file", "C:\\Users\\private-file", "token=private-value",
    "https://private.example/?credential=private-value", "person@example.com",
    "Traceback (most recent call last):", "stdout: private-value", "raw_prompt: private-value",
    "-----BEGIN RSA PRIVATE KEY-----", "=HYPERLINK(\"private\")", "\t+CMD(private)",
])
def test_failed_error_summary_hides_credentials_paths_and_formula_strings(error):
    report = inspect_demo_scores(csv_fixture([["PharmacoNet", "C1", "", "failed", error]]), COLUMNS)
    assert report["accepted"] == 1 and report["summary"]["failed"] == 1
    row = model_result(report, "PharmacoNet")["rows"][0]
    assert row["error_redacted"] is True and row["error"]
    assert error.strip() not in json.dumps(report)


def test_safe_failure_error_is_whitespace_normalized_and_bounded():
    error = "timeout\nretry later " + "x" * 500
    report = inspect_demo_scores(csv_fixture([["PharmacoNet", "C1", "", "failed", error]]), COLUMNS)
    row = model_result(report, "PharmacoNet")["rows"][0]
    assert row["error"].startswith("timeout retry later ")
    assert len(row["error"]) == 241 and row["error"].endswith("…")
    assert row["error_redacted"] is True


def test_byte_limits_use_utf8_size_not_only_character_count():
    report = inspect_demo_scores("x" * (MAX_BYTES + 1), COLUMNS)
    assert report["status"] == "invalid" and "byte_limit" in issue_codes(report)
    report = inspect_demo_scores("界" * (MAX_BYTES // 3 + 1), COLUMNS)
    assert report["status"] == "invalid" and "byte_limit" in issue_codes(report)
    assert report["accepted"] == 0 and report["summary"]["rows_observed"] == 0


def test_row_limit_is_atomic_and_does_not_accept_a_misleading_prefix():
    rows = [["DrugCLIP", f"C{index}", "1", "ok", ""] for index in range(MAX_ROWS + 1)]
    report = inspect_demo_scores(csv_fixture(rows), COLUMNS)
    assert report["status"] == "invalid" and "row_limit" in issue_codes(report)
    assert report["accepted"] == 0 and all(not entry["rows"] for entry in report["by_model"])
    assert report["summary"]["rows_observed"] == MAX_ROWS + 1
    assert report["summary"]["input_complete"] is False


def test_cell_and_column_limits_cover_unmapped_fields():
    mapping = {role: name for role, name in COLUMNS.items() if role != "error"}
    text = csv_fixture([["DrugCLIP", "C1", "1", "ok", "x" * (MAX_CELL_CHARS + 1)]])
    report = inspect_demo_scores(text, mapping)
    assert report["accepted"] == 0 and "cell_limit" in issue_codes(report)
    report = inspect_demo_scores(csv_fixture([], header=[f"c{i}" for i in range(65)]), COLUMNS)
    assert report["status"] == "invalid" and "column_limit" in issue_codes(report)


def test_preview_limit_keeps_high_scores_and_failed_errors_with_explicit_omissions():
    rows = [["DrugCLIP", f"C{index}", str(index), "ok", ""] for index in range(120)]
    rows.extend([["DrugCLIP", f"F{index}", "", "failed", "timeout"] for index in range(3)])
    report = inspect_demo_scores(csv_fixture(rows), COLUMNS)
    model = model_result(report, "DrugCLIP")
    assert report["accepted"] == 123 and model["accepted"] == 123
    assert len(model["rows"]) == MAX_DISPLAY_PER_MODEL
    assert model["displayed"] == 100 and model["omitted"] == 23
    assert model["rows"][0]["score"] == 119.0 and model["rows"][0]["rank"] == 1
    assert sum(row["status"] == "failed" for row in model["rows"]) == 3
    assert all(row["error"] == "timeout" for row in model["rows"] if row["status"] == "failed")


def test_validation_issue_and_rejected_previews_are_bounded_without_losing_counts():
    rows = [["DrugCLIP", f"C{index}", "NaN", "ok", ""]
            for index in range(MAX_VALIDATION_ISSUES + 20)]
    report = inspect_demo_scores(csv_fixture(rows), COLUMNS)
    assert report["rejected"] == MAX_VALIDATION_ISSUES + 20
    assert report["validation_issue_count"] == MAX_VALIDATION_ISSUES + 20
    assert len(report["validation_issues"]) == MAX_VALIDATION_ISSUES
    assert report["issues_truncated"] is True
    assert len(report["rejected_rows"]) == 100 and report["rejected_rows_truncated"] is True
    json.dumps(report, allow_nan=False)


def test_malformed_csv_nul_and_invalid_encoding_never_return_partial_rankings():
    report = inspect_demo_scores(HEADER + 'DrugCLIP,C1,1,ok,\n"unterminated', COLUMNS)
    assert report["status"] == "invalid" and "malformed_csv" in issue_codes(report)
    assert report["accepted"] == 0 and report["summary"]["input_complete"] is False
    report = inspect_demo_scores(HEADER + "DrugCLIP,C1,1,ok,\x00\n", COLUMNS)
    assert "invalid_control_character" in issue_codes(report)
    report = inspect_demo_scores("\ud800", COLUMNS)
    assert "invalid_encoding" in issue_codes(report)


def test_utf8_bom_header_and_independent_result_objects_are_supported():
    report = inspect_demo_scores("\ufeff" + HEADER + "DrugCLIP,C1,1,ok,\n", COLUMNS)
    assert report["status"] == "ok"
    report["limitations"].clear()
    report["by_model"][0]["rows"][0]["score"] = 999
    fresh = inspect_demo_scores(HEADER + "DrugCLIP,C1,1,ok,\n", COLUMNS)
    assert fresh["limitations"] and fresh["by_model"][0]["rows"][0]["score"] == 1.0

