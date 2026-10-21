"""Public HDAC2 demo metadata and bounded, ephemeral score inspection.

This module performs no file, network, subprocess, model or persistence work.
The repository's historical benchmark is a source report, never a local run.
CSV columns must be mapped explicitly: the archive's actual CSV schema has not
been inspected here. Imported scores remain unverified user observations.
"""
from __future__ import annotations

import copy
import csv
import io
import math
import re
from collections import Counter
from typing import Any

DEMO_ID = "hdac2_h20"
SCHEMA_VERSION = 1
MODEL_NAMES = ("DrugCLIP", "SPRINT", "PSICHIC", "PharmacoNet")
MAX_BYTES = 500_000
MAX_ROWS = 5_000
MAX_COLUMNS = 64
MAX_CELL_CHARS = 1_024
MAX_DISPLAY_PER_MODEL = 100
MAX_VALIDATION_ISSUES = 500
MAX_REJECTED_PREVIEW = 100
MAX_ERROR_CHARS = 240
_REQUIRED_ROLES = {"model", "compound_id", "score", "status"}
_OPTIONAL_ROLES = {"error"}
_MODEL_LOOKUP = {name.casefold(): name for name in MODEL_NAMES}
_COMPOUND_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SENSITIVE = re.compile(
    r"(?i)(\b(?:authorization|api[-_ ]?key|access[-_ ]?token|password|"
    r"raw[-_ ]?prompt|system[-_ ]?prompt|secret|credentials?|stdout|stderr|traceback)\b|"
    r"\bbearer\s+\S+|\btoken\s*[=:]|\bsk-[A-Za-z0-9_-]{8,}|"
    r"\bgh[pousr]_[A-Za-z0-9_]{8,}|\bgithub_pat_[A-Za-z0-9_]{8,}|"
    r"\bAKIA[A-Z0-9]{16}\b|-----BEGIN .*PRIVATE KEY|"
    r"(?:file|vscode)://|/(?:Users|home|private|tmp|var|etc)/|[A-Za-z]:[\\/]|"
    r"(?:^|[\s('\"=:])(?:(?:\.{1,2}|~)?[/\\]|[A-Za-z0-9_.-]+[/\\])[^\s]+|"
    r"https?://|<\|(?:system|user|assistant)|\[INST\]|"
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b)"
)
_SOURCE_COMMIT = "7d9236fdbc96da55fb6e92a1b0c7ce1a4f48d8dc"
_REPOSITORY = "https://github.com/Xiang-Chen1207/IGEM2026_Model_1"
_DEMO_SOURCE = f"{_REPOSITORY}/blob/{_SOURCE_COMMIT}/demos/{DEMO_ID}"
_LIMITATIONS = [
    "HDAC2 / 4LXZ 固定靶点演示；更换靶点需要重建蛋白与口袋缓存，兼容的分子 embedding 可复用。",
    "展示来源仓库记录的运行结果；本地执行状态见运行信息。",
    "计时分为准备阶段与评分阶段；模型加载、编码、构象生成和预热属于准备范围，准备阶段部分复用缓存。",
    "演示分数用于模型内排序；根入口另以完整有效评分池的百分位构建跨模型共识。",
    "分数比较使用相同模型、候选池和预处理版本；DrugCLIP 的 median/MAD 标度分别对应 common50 和 capacity1000 候选池。",
    "用户导入评分：显示本次 CSV 的数值排序，靶点、候选池、预处理版本与模型来源待核验。",
    "本演示展示固定 HDAC2 输入下的四模型评分及 CSV 排序检查。",
    "用户导入评分独立保存在本次内存检查中；研究证据、运行历史与模型执行各自记录。",
]


def _benchmark(successful: int, attempted: int, online: float, preparation: float) -> dict:
    return {
        "source_kind": "repository_report", "scope": "four_model_aggregate",
        "source_url": f"{_DEMO_SOURCE}/USAGE_zh.md", "source_commit": _SOURCE_COMMIT,
        "successful": successful, "attempted": attempted,
        "online_seconds": online, "preparation_seconds": preparation,
        "verified_locally": False, "partial_cache_reuse": True,
        "online_timing_excludes": [
            "model_loading", "encoding", "conformer_generation", "warmup",
        ],
        "hardware": {"gpu": "H20", "cpu_cores": 64, "memory_gib": 128},
    }


def get_demo(demo_id: str) -> dict[str, Any]:
    """Return a fresh JSON-safe card; unknown IDs raise FileNotFoundError."""
    if demo_id != DEMO_ID:
        raise FileNotFoundError("Unknown demo")
    return {
        "schema_version": SCHEMA_VERSION, "id": DEMO_ID,
        "title": "HDAC2 四模型演示 · H20 来源报告",
        "description": "查看固定靶点的四模型流程、准备资源和仓库历史结果；可在内存中检查用户 CSV。",
        "target": {"gene": "HDAC2", "pdb_id": "4LXZ", "fixed_target": True},
        "source": {
            "kind": "repository_report", "repository": _REPOSITORY,
            "commit": _SOURCE_COMMIT, "readme_url": f"{_DEMO_SOURCE}/README.md",
            "usage_url": f"{_DEMO_SOURCE}/USAGE_zh.md", "verified_locally": False,
        },
        "local_execution": {"status": "not_run", "executed": False},
        "models": [
            {"id": "DrugCLIP", "label": "DrugCLIP", "score_scope": "within_model_only",
             "execution_mode": "已编码缓存的真实向量匹配；原始六折大权重未包含。"},
            {"id": "SPRINT", "label": "SPRINT", "score_scope": "within_model_only",
             "execution_mode": "已编码缓存的真实向量匹配；原始 SaProt 大权重未包含。"},
            {"id": "PSICHIC", "label": "PSICHIC", "score_scope": "within_model_only",
             "execution_mode": "模型 forward 推理；演示包包含 PSICHIC 权重。"},
            {"id": "PharmacoNet", "label": "PharmacoNet", "score_scope": "within_model_only",
             "execution_mode": "基于已准备口袋药效团和构象的 CPU matching。"},
        ],
        "scenarios": [
            {"id": "common50", "label": "常见 50 分子", "molecule_count": 50,
             "selection_note": "用于展示四模型评分流程的 50 分子界面示例。",
             "reported_benchmark": _benchmark(200, 200, 0.4411, 11.26)},
            {"id": "capacity1000", "label": "容量 1000 分子", "molecule_count": 1000,
             "selection_note": "常见 50 分子加 950 个容量测试分子；四模型共 4000 次评分尝试。",
             "reported_benchmark": _benchmark(3998, 4000, 0.8605, 98.41)},
        ],
        "resources": [
            {"id": "readme", "label": "来源 README", "kind": "documentation",
             "url": f"{_DEMO_SOURCE}/README.md", "downloaded": False,
             "validated_locally": False},
            {"id": "usage", "label": "来源中文使用说明", "kind": "documentation",
             "url": f"{_DEMO_SOURCE}/USAGE_zh.md", "downloaded": False,
             "validated_locally": False},
            {"id": "common50", "label": "来源常见 50 分子表", "kind": "molecule_table",
             "url": f"{_DEMO_SOURCE}/common50_molecules.csv", "downloaded": False,
             "validated_locally": False},
            {"id": "archive", "label": "完整 HDAC2 H20 演示包", "kind": "archive",
             "url": f"{_REPOSITORY}/raw/{_SOURCE_COMMIT}/demos/{DEMO_ID}/hdac2_demo_h20.tar.gz",
             "size_mib": 38.36, "size_source": "repository_report",
             "downloaded": False, "validated_locally": False,
             "included": ["PSICHIC 权重", "模型源码", "embeddings", "分子构象",
                          "HDAC2 固定靶点缓存", "来源实测 CSV"],
             "excluded": ["Python / CUDA 环境", "DrugCLIP 原始六折大权重", "SaProt 原始大权重"]},
        ],
        "workflow": [
            {"id": "review_source", "label": "阅读来源与范围", "status": "reference_only",
             "description": "核对固定 HDAC2 / 4LXZ、分子选择和来源报告的计时边界。"},
            {"id": "prepare_resources", "label": "准备环境与缓存", "status": "not_run",
             "description": "按来源说明准备环境与权重；新靶点编码使用对应的完整模型资源。"},
            {"id": "run_models", "label": "执行四模型", "status": "not_run",
             "description": "DrugCLIP / SPRINT 向量匹配、PSICHIC forward、PharmacoNet CPU matching。"},
            {"id": "inspect_scores", "label": "显式导入并检查 CSV", "status": "available",
             "description": "选择 CSV 列映射，检查格式与模型内排名；模型运行状态单独记录。"},
        ],
        "limitations": list(_LIMITATIONS),
        "csv_import": {
            "required_roles": ["model", "compound_id", "score", "status"],
            "optional_roles": ["error"], "allowed_statuses": ["ok", "success", "failed"],
            "status_aliases": {"success": "ok"},
            "limits": {"max_bytes": MAX_BYTES, "max_rows": MAX_ROWS,
                       "max_columns": MAX_COLUMNS, "max_cell_chars": MAX_CELL_CHARS,
                       "max_display_per_model": MAX_DISPLAY_PER_MODEL,
                       "max_validation_issues": MAX_VALIDATION_ISSUES},
            "example_columns": {"model": "model", "compound_id": "compound_id",
                                "score": "score", "status": "status", "error": "error"},
            "example_only": True,
            "schema_note": "列名映射示例；实际列名以导入 CSV 为准。",
            "ranking_note": "仅对本次导入的 ok 行按同模型数值降序派生 rank；并列分数共享 rank，未核验候选池或预处理版本。",
        },
    }


def demo_catalog() -> dict[str, Any]:
    """List public demo cards without probing the machine or downloading assets."""
    return {"schema_version": SCHEMA_VERSION, "demos": [get_demo(DEMO_ID)]}


def _empty_result() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "demo_id": DEMO_ID, "status": "empty",
        "provenance": {"kind": "user_imported_not_verified", "verified": False,
                       "persisted": False, "evidence_created": False,
                       "target_association": "not_verified",
                       "candidate_pool_association": "not_verified",
                       "preprocessing_version": "not_verified"},
        "local_execution": {"status": "not_run", "executed": False},
        "accepted": 0, "rejected": 0,
        "summary": {"rows_observed": 0, "successful": 0, "failed": 0,
                    "models_observed": 0, "input_complete": True,
                    "ranking_scope": "within_model_only", "rank_derived": True},
        "by_model": [{"model": name, "observed": 0, "accepted": 0, "rejected": 0,
                      "successful": 0, "failed": 0, "rows": [], "displayed": 0,
                      "omitted": 0} for name in MODEL_NAMES],
        "rejected_rows": [], "validation_issues": [], "validation_issue_count": 0,
        "issues_truncated": False, "rejected_rows_truncated": False,
        "limits": {"max_bytes": MAX_BYTES, "max_rows": MAX_ROWS,
                   "max_columns": MAX_COLUMNS, "max_cell_chars": MAX_CELL_CHARS,
                   "max_display_per_model": MAX_DISPLAY_PER_MODEL},
        "limitations": list(_LIMITATIONS),
    }


def _issue(result: dict, code: str, message: str, *, scope: str = "document",
           row_number: int | None = None, field: str | None = None) -> None:
    result["validation_issue_count"] += 1
    if len(result["validation_issues"]) < MAX_VALIDATION_ISSUES:
        result["validation_issues"].append({"scope": scope, "row_number": row_number,
                                            "field": field, "code": code, "message": message})
    else:
        result["issues_truncated"] = True


def _invalid(result: dict, code: str, message: str, *, scope: str = "document",
             field: str | None = None) -> dict:
    _issue(result, code, message, scope=scope, field=field)
    result["status"] = "invalid"
    result["rejected"] = result["summary"]["rows_observed"]
    return result


def _safe_error(value: str) -> tuple[str | None, bool]:
    """Keep a bounded human message; discard whole sensitive diagnostics."""
    value = value.strip()
    if not value:
        return None, False
    if (_SENSITIVE.search(value) or value.startswith(("=", "+", "-", "@"))
            or any(ord(char) < 32 and char not in "\n\r\t" for char in value)):
        return "[错误详情包含敏感或不安全内容，已隐藏]", True
    clean = " ".join(value.split())
    truncated = len(clean) > MAX_ERROR_CHARS
    return clean[:MAX_ERROR_CHARS] + ("…" if truncated else ""), truncated


def inspect_demo_scores(csv_text: str, columns: dict[str, str]) -> dict[str, Any]:
    """Validate a user CSV in memory and derive separate model score ranks.

    Mapping keys are model, compound_id, score, status and optional error; values
    identify exact, case-sensitive CSV headers. Status cells accept ok, success
    (an explicit alias for ok), and failed, case-insensitively. Failed rows require
    an empty score. Model names
    are case-insensitive, but unknown names are rejected. Duplicate model/ID
    pairs reject all occurrences. Compound IDs are bounded simple identifiers,
    never formulas, paths or arbitrary prose. Unmapped fields are never exposed.

    The result is JSON-safe, bounded and not persisted. Per-model previews keep
    high scores and up to half their slots for failed records; omitted counts
    remain explicit. Rank is numeric descending order, not efficacy validation.
    """
    result = _empty_result()
    if not isinstance(csv_text, str):
        return _invalid(result, "invalid_input_type", "CSV 输入必须为文本。")
    if len(csv_text) > MAX_BYTES:
        return _invalid(result, "byte_limit", "CSV 超过 500000 UTF-8 字节限制。")
    try:
        byte_length = len(csv_text.encode("utf-8"))
    except UnicodeEncodeError:
        return _invalid(result, "invalid_encoding", "CSV 包含非法 Unicode 字符。")
    if byte_length > MAX_BYTES:
        return _invalid(result, "byte_limit", "CSV 超过 500000 UTF-8 字节限制。")
    if "\x00" in csv_text:
        return _invalid(result, "invalid_control_character", "CSV 不允许 NUL 控制字符。")
    if not isinstance(columns, dict):
        return _invalid(result, "invalid_mapping", "必须显式提供列映射对象。", scope="mapping")
    if set(columns) - (_REQUIRED_ROLES | _OPTIONAL_ROLES):
        return _invalid(result, "unknown_mapping_role", "列映射包含未知角色。", scope="mapping")
    if not _REQUIRED_ROLES.issubset(columns):
        return _invalid(result, "missing_mapping_role", "必须映射 model、compound_id、score、status。",
                        scope="mapping")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 128
           for value in columns.values()):
        return _invalid(result, "invalid_mapping_header", "映射列名必须是 1–128 字符的文本。",
                        scope="mapping")
    if len(set(columns.values())) != len(columns):
        return _invalid(result, "ambiguous_mapping", "每个角色必须映射到不同列。", scope="mapping")
    if not csv_text.strip():
        _issue(result, "empty_input", "未提供 CSV 表头和数据。")
        return result

    reader = csv.reader(io.StringIO(csv_text, newline=""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        return _invalid(result, "malformed_csv", "CSV 表头无法解析。")
    if header:
        header[0] = header[0].removeprefix("\ufeff")
    if not header or len(header) > MAX_COLUMNS:
        return _invalid(result, "column_limit", "CSV 必须有表头，且列数不能超过 64。")
    if any(not item.strip() or len(item) > 128 for item in header):
        return _invalid(result, "invalid_header", "CSV 表头必须为 1–128 字符的非空列名。")
    if len({item.strip() for item in header}) != len(header):
        return _invalid(result, "duplicate_header", "CSV 包含重复或空格歧义表头。")
    if any(value not in header for value in columns.values()):
        return _invalid(result, "missing_mapped_column", "某个显式映射列不存在于 CSV 表头。",
                        scope="mapping")
    indices = {role: header.index(name) for role, name in columns.items()}
    raw_rows: list[list[str]] = []
    try:
        for raw in reader:
            raw_rows.append(raw)
            result["summary"]["rows_observed"] = len(raw_rows)
            if len(raw_rows) > MAX_ROWS:
                result["summary"]["input_complete"] = False
                return _invalid(result, "row_limit", "CSV 超过 5000 数据行；本次不接受任何行。")
    except csv.Error:
        result["summary"]["input_complete"] = False
        return _invalid(result, "malformed_csv", "CSV 引号或记录格式非法；本次不接受任何行。")

    records: list[dict] = []
    identities: Counter = Counter()
    for index, raw in enumerate(raw_rows, start=2):
        record = {"row_number": index, "issues": [], "model": None, "compound_id": None}
        records.append(record)

        def reject(code: str, message: str, field: str | None = None) -> None:
            record["issues"].append(code)
            _issue(result, code, message, scope="row", row_number=index, field=field)

        if len(raw) != len(header):
            reject("row_width", "数据行的列数与表头不一致。")
            continue
        if any(len(cell) > MAX_CELL_CHARS for cell in raw):
            reject("cell_limit", "数据单元格超过 1024 字符。")
            continue
        model = _MODEL_LOOKUP.get(raw[indices["model"]].strip().casefold())
        record["model"] = model
        if model is None:
            reject("unknown_model", "模型必须为 DrugCLIP、SPRINT、PSICHIC 或 PharmacoNet。", "model")
        compound_id = raw[indices["compound_id"]].strip()
        if not compound_id:
            reject("missing_compound_id", "化合物 ID 不能为空。", "compound_id")
        elif not _COMPOUND_ID.fullmatch(compound_id) or _SENSITIVE.search(compound_id):
            reject("unsafe_compound_id", "化合物 ID 必须是最多 128 字符的安全标识符。", "compound_id")
        else:
            record["compound_id"] = compound_id
            if model is not None:
                identities[(model, compound_id)] += 1
        status = raw[indices["status"]].strip().casefold()
        if status == "success":
            status = "ok"
        record["status"] = status
        score_text = raw[indices["score"]].strip()
        record["score"] = None
        if status not in {"ok", "failed"}:
            reject("invalid_status", "状态只能为 ok、success（ok 别名）或 failed。", "status")
        elif status == "failed":
            if score_text:
                reject("failed_score_not_empty", "failed 行的 score 必须为空。", "score")
        else:
            try:
                score = float(score_text)
            except ValueError:
                reject("invalid_score", "ok 行必须有有效的有限数值分数。", "score")
            else:
                if not math.isfinite(score):
                    reject("non_finite_score", "分数不允许 NaN 或无穷值。", "score")
                else:
                    record["score"] = score
        error = raw[indices["error"]] if "error" in indices else ""
        record["error"], record["error_redacted"] = _safe_error(error)

    model_records = {name: [] for name in MODEL_NAMES}
    model_stats = {entry["model"]: entry for entry in result["by_model"]}
    for record in records:
        model, compound_id = record["model"], record["compound_id"]
        if model is not None and compound_id is not None and identities[(model, compound_id)] > 1:
            record["issues"].append("duplicate_model_compound")
            _issue(result, "duplicate_model_compound", "同一模型与化合物 ID 重复；所有重复行均拒绝。",
                   scope="row", row_number=record["row_number"], field="compound_id")
        if model is not None:
            model_stats[model]["observed"] += 1
        if record["issues"]:
            result["rejected"] += 1
            if model is not None:
                model_stats[model]["rejected"] += 1
            if len(result["rejected_rows"]) < MAX_REJECTED_PREVIEW:
                result["rejected_rows"].append({"row_number": record["row_number"],
                                                 "issue_codes": list(record["issues"])})
            else:
                result["rejected_rows_truncated"] = True
            continue
        result["accepted"] += 1
        model_stats[model]["accepted"] += 1
        successful = record["status"] == "ok"
        count_key = "successful" if successful else "failed"
        result["summary"][count_key] += 1
        model_stats[model][count_key] += 1
        model_records[model].append({key: copy.deepcopy(record[key]) for key in
                                    ("row_number", "model", "compound_id", "status", "score",
                                     "error", "error_redacted")})

    for model, accepted_rows in model_records.items():
        successes = sorted((row for row in accepted_rows if row["status"] == "ok"),
                           key=lambda row: (-row["score"], row["compound_id"], row["row_number"]))
        failed_rows = [row for row in accepted_rows if row["status"] == "failed"]
        previous_score = None
        previous_rank = 0
        for position, row in enumerate(successes, start=1):
            if position == 1 or row["score"] != previous_score:
                previous_rank = position
            row["rank"] = previous_rank
            previous_score = row["score"]
        for row in failed_rows:
            row["rank"] = None
        failure_slots = min(len(failed_rows), MAX_DISPLAY_PER_MODEL // 2 if successes
                            else MAX_DISPLAY_PER_MODEL)
        success_slots = min(len(successes), MAX_DISPLAY_PER_MODEL - failure_slots)
        failure_slots = min(len(failed_rows), MAX_DISPLAY_PER_MODEL - success_slots)
        preview = successes[:success_slots] + failed_rows[:failure_slots]
        model_stats[model].update(rows=preview, displayed=len(preview),
                                  omitted=len(accepted_rows) - len(preview))
    result["summary"]["models_observed"] = sum(entry["observed"] > 0 for entry in result["by_model"])
    result["status"] = ("partial" if result["accepted"] and result["rejected"] else
                        "invalid" if result["rejected"] else
                        "ok" if result["accepted"] else "empty")
    if not raw_rows:
        _issue(result, "no_data_rows", "CSV 有表头，但没有数据行。")
    return result

