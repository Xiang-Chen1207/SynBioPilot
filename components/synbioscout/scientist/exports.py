"""Portable research downloads with an explicit allowlist, not workspace archives."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from . import __version__
from .models import ResearchRun
from .presentation import ROLE_LABELS, presented_run, result_document
from .reports import render_report
from .source_scope import source_scope_view
from .store import ProjectStore, safe_id

MAX_TARGET_FILE_BYTES = 20_000_000
MAX_ARCHIVE_INPUT_BYTES = 50_000_000
LOCAL_PATH = re.compile(r"(?:/Users/|/home/|/private/|/tmp/|/var/|/opt/)[^\s\"'<>]*")


def portable_metadata(value):
    """Redact local pointers in the exported copy; retain URLs, identifiers and hashes."""
    if isinstance(value, dict):
        return {key: portable_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [portable_metadata(item) for item in value]
    if isinstance(value, str):
        return LOCAL_PATH.sub("[local path omitted]", value)
    return value


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def spreadsheet_safe(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def csv_bytes(rows: list[dict], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: spreadsheet_safe(portable_metadata(row.get(key))) for key in fields})
    return stream.getvalue().encode("utf-8-sig")


def table_exports(run: ResearchRun) -> dict[str, bytes]:
    from .candidate_assessments import assessment_cards, public_evaluations
    evaluations = public_evaluations(run)
    view = presented_run(run)
    source_lookup = {s.source_id: s for s in view.sources}
    from .tool_public import public_candidate_identity, public_failure_records
    assessments = assessment_cards(view, evaluations)
    by_id = {row["candidate_id"]: row for row in assessments if row["candidate_id"]}
    candidates = [{**c.model_dump(), "role_label": ROLE_LABELS[c.role],
        "user_name_declarations": public_candidate_identity(c.model_dump())["user_name_declarations"],
        "origin": by_id.get(c.candidate_id, {}).get("origin", "research_record"),
        "opposing_evidence_ids": by_id.get(c.candidate_id, {}).get("opposing_evidence_ids", []),
        "population_requested": view.brief.population, "endpoint_requested": view.brief.phenotype,
        "scientific_status": by_id.get(c.candidate_id, {}).get("scientific_status", "needs_evidence"),
        "next_questions": by_id.get(c.candidate_id, {}).get("next_questions", [])} for c in view.candidates]
    candidates.extend({**row, "role_label": "评估输入的唯一实体关联待确定"} for row in assessments if not row["candidate_id"])
    for candidate in candidates:
        card = by_id.get(candidate.get("candidate_id"), {})
        evaluation = card.get("validated_evaluation") or {}
        candidate["assessment_status"] = card.get("assessment_status", "unreviewed")
        candidate["workflow_status"] = view.status.value
        candidate["hypothesis_expansion_status"] = card.get("hypothesis_expansion_status", "not_recorded")
        for key in ("assessment_id", "version", "review_id", "review_operation_id", "conclusion", "identity_fingerprint", "task_fingerprint", "conclusion_fingerprint", "evidence_fingerprint"):
            candidate["assessment_" + key if key == "version" else key] = evaluation.get(key)
        candidate["assessment_findings"] = evaluation.get("findings", [])
        candidate["assessment_unknowns"] = evaluation.get("unknowns", [])
    evidence = []
    for record in view.evidence:
        source = source_lookup.get(record.source_id)
        scope = source_scope_view(source)
        window = scope["original_window"] or {}
        evidence.append({**record.model_dump(), "source_title": source.title if source else "",
                         "source_url": source.url if source else "",
                         # Keep the former storage enum explicitly named for
                         # compatibility; the visible scope follows the same
                         # canonical material/window view as the product.
                         "source_scope": scope["current_scope"],
                         "stored_source_scope": source.evidence_scope if source else "unknown",
                         "original_scope": scope["original_scope"], "current_scope": scope["current_scope"],
                         "local_excerpt": scope["local_excerpt"],
                         "original_window_start": window.get("start"), "original_window_end": window.get("end"),
                         "original_window_unit": "unicode_code_points" if window else "",
                         "acquisition": scope["acquisition"], "acquisition_origin": scope["acquisition_origin"],
                         "reused": True if scope["acquisition"] == "reused" else False if scope["acquisition"] == "newly_acquired" else None,
                         "fresh_fulltext": scope["fresh_fulltext"]})
    return {
        "budget_failures.csv": csv_bytes(public_failure_records(view.co_scientist) if view.co_scientist else [],
            ["receipt_id", "operation_id", "stage", "reason", "record_types", "required_output_bytes",
             "remaining_output_bytes", "reserved_output_bytes", "inflight_output_bytes", "call_allowance",
             "effective_available_output_bytes", "output_limit_categories", "accounting_unit",
             "blocked_before_provider", "prior_timeout"]),
        "candidates.csv": csv_bytes(candidates, ["candidate_id", "name", "role", "role_label",
            "role_status", "role_reason", "origin", "decision", "identity_status", "smiles", "inchikey",
            "pubchem_cid", "entity_id", "identity_revision", "aliases", "user_name_declarations", "identity_source_ids", "routes", "evidence_ids", "role_evidence_ids", "target_relations",
            "rationale", "uncertainties", "opposing_evidence_ids", "population_requested", "endpoint_requested",
            "scientific_status", "workflow_status", "hypothesis_expansion_status", "assessment_status", "assessment_id", "assessment_version", "review_id", "review_operation_id", "conclusion",
            "identity_fingerprint", "task_fingerprint", "conclusion_fingerprint", "evidence_fingerprint", "assessment_findings", "assessment_unknowns",
            "next_questions", "admet", "gate"]),
        "candidate_evaluations.csv": csv_bytes(evaluations, ["schema_version", "assessment_id", "version", "candidate_id",
            "entity_id", "identity_revision", "status", "review_id", "review_operation_id", "scientific_status", "conclusion",
            "identity_fingerprint", "task_fingerprint", "conclusion_fingerprint", "evidence_fingerprint", "findings",
            "supporting_evidence_ids", "opposing_evidence_ids", "unknowns", "next_questions"]),
        "targets.csv": csv_bytes([t.model_dump() for t in view.targets], ["target_id", "gene_symbol",
            "uniprot_id", "ensembl_id", "species", "desired_direction", "related_cells", "pathways",
            "structure_status", "package_id", "evidence_ids", "rationale", "uncertainties"]),
        "evidence.csv": csv_bytes(evidence, ["evidence_id", "claim", "polarity", "source_id",
            "source_title", "source_url", "source_scope", "stored_source_scope", "original_scope", "current_scope",
            "local_excerpt", "original_window_start", "original_window_end", "original_window_unit",
            "acquisition", "acquisition_origin", "reused", "fresh_fulltext", "quote", "location", "study_system",
            "condition", "endpoint", "limitations", "validated_span"]),
    }


def export_document(run: ResearchRun):
    document = result_document(run)
    for source in document["sources"]:
        text = source["text"]
        source["text"] = ""
        source["metadata"].setdefault("retrieved_text_sha256", hashlib.sha256(text.encode()).hexdigest())
        source["metadata"]["export_text_included"] = False
        # Canonical scope describes the document actually exported. A checked
        # local window cannot be reasserted after its validating text is removed.
        source["metadata"]["source_scope"] = source_scope_view(source)
    if document.get("co_scientist"):
        for result in document["co_scientist"]["tool_calls"]:
            for source in result.get("sources", []) + result.get("records", []):
                if "text" in source and "source_id" in source:
                    text = source["text"]
                    source["text"] = ""
                    source.setdefault("metadata", {}).setdefault(
                        "retrieved_text_sha256", hashlib.sha256(text.encode()).hexdigest())
                    source["metadata"]["export_text_included"] = False
                    source["metadata"]["source_scope"] = source_scope_view(source)
    document["presentation"]["source_text_export"] = (
        "Original retrieved text remains local; source bodies are omitted from this export, "
        "while citations and evidence excerpts are included. Without the source body, its original "
        "window cannot be verified within this export, so canonical original_window is null. "
        "CSV and Markdown computed from source objects that still contain the original text may "
        "retain verified windows."
    )
    return document


def markdown_bytes(run: ResearchRun) -> bytes:
    note = ("> 研究结果视图：角色标注与展示决策保留历史记录来源。\n"
            "> 药效研究候选、标志物与风险分子按各自研究角色呈现，功能、底盘和暴露证据分别记录。\n\n")
    return (note + render_report(run)).encode()


def export_research(store: ProjectStore, run: ResearchRun, format_name: str):
    """Return (bytes, MIME, filename), without cache/credentials/logs or filesystem mutation."""
    stem = "SynBioScout_" + safe_id(run.run_id)
    if format_name == "markdown":
        return markdown_bytes(run), "text/markdown; charset=utf-8", stem + ".md"
    if format_name == "json":
        return json_bytes(export_document(run)), "application/json", stem + ".json"
    tables = table_exports(run)
    if format_name == "csv":
        return tables["candidates.csv"], "text/csv; charset=utf-8", stem + "_candidates.csv"
    if format_name != "zip":
        raise ValueError("Export format must be markdown, json, csv or zip")

    document = export_document(run)
    entries = {}
    skipped, mapping = [], {}
    project = store.project_dir(run.project_id).resolve()
    total = 0
    def add(name, content):
        nonlocal total
        updated = total - len(entries.get(name, b"")) + len(content)
        if updated > MAX_ARCHIVE_INPUT_BYTES:
            raise ValueError("Research archive exceeds the 50 MB total export limit; export individual files instead")
        entries[name] = content
        total = updated
    for name, content in tables.items():
        add(name, portable_metadata(content.decode("utf-8-sig")).encode("utf-8-sig"))
    add("report.md", portable_metadata(markdown_bytes(run).decode()).encode())
    add("sources.json", json_bytes(portable_metadata(document["sources"])))
    add("candidate_assessments.json", json_bytes(portable_metadata(document["candidate_assessments"])))
    add("candidate_evaluations.json", json_bytes(portable_metadata(document["candidate_evaluations"])))
    add("candidate_evaluation_records.json", json_bytes(portable_metadata(document["candidate_evaluation_records"])))
    add("evidence_digest.json", json_bytes(portable_metadata(document["evidence_digest"])))
    if document.get("co_scientist"):
        from .coscientist import render_contracts
        co = portable_metadata(document["co_scientist"])
        add("co_scientist.json", json_bytes(co))
        for field in ("hypotheses", "reviews", "comparisons", "events", "tool_calls", "tool_plans", "budget"):
            add(field + ".json", json_bytes(co[field]))
        if co.get("research_flow"):
            add("research_flow.json", json_bytes(co["research_flow"]))
        add("research_contract.md", portable_metadata(render_contracts(run)).encode())
    for package in document["target_packages"]:
        label = safe_id(package["package_id"])
        directory = "target_files/" + label
        mapping[label] = {}
        for kind, filename in (("protein_pdb", "protein.pdb"), ("sequence_fasta", "sequence.fasta"),
                               ("reference_ligand_sdf", "reference.sdf")):
            value = package.get(kind)
            package[kind] = ""
            if not value:
                continue
            path = Path(value).resolve()
            if not path.is_relative_to(project) or not path.is_file():
                skipped.append({"package_id": label, "kind": kind, "reason": "file_unavailable_or_outside_project"})
                continue
            if any(part.startswith(".") for part in path.relative_to(project).parts):
                skipped.append({"package_id": label, "kind": kind, "reason": "hidden_file_not_exported"})
                continue
            if path.stat().st_size > MAX_TARGET_FILE_BYTES or total + path.stat().st_size > MAX_ARCHIVE_INPUT_BYTES:
                skipped.append({"package_id": label, "kind": kind, "reason": "size_limit_use_individual_download"})
                continue
            with path.open("rb") as stream:
                content = stream.read(MAX_TARGET_FILE_BYTES + 1)
            if len(content) > MAX_TARGET_FILE_BYTES or total + len(content) > MAX_ARCHIVE_INPUT_BYTES:
                skipped.append({"package_id": label, "kind": kind, "reason": "size_limit_use_individual_download"})
                continue
            relative = directory + "/" + filename
            add(relative, content)
            mapping[label][kind] = package[kind] = relative
        package["metadata"] = portable_metadata(package["metadata"])
        package["metadata"]["export_raw_sources"] = "Raw source snapshots remain on the original host; URLs and hashes are retained"
        add(directory + "/checks.json", json_bytes(portable_metadata(package["checks"])))
        add(directory + "/metadata.json", json_bytes(package["metadata"]))
    document["report_path"] = "report.md"
    add("research.json", json_bytes(portable_metadata(document)))
    add("README.txt", ("SynBioScout AI Scientist 研究结果包\n"
        "report.md: 阅读报告；targets/candidates/evidence.csv: 数据表；research.json: 结构化结果。\n"
        "budget_failures.csv: 安全预算收据表；单次接收帽、有效余量和限制类别使用 UTF-8 字节计量；提供方计费另行记录。\n"
        "target_files/: 当前项目已有的 PDB/FASTA/SDF；文件状态见清单。\n"
        "presentation.derived_view=true 表示软件展示视图；历史角色标签注明文本来源和当前审查状态。\n"
        "不含模型权重、环境、API 密钥、推理日志、缓存或其他项目数据。\n"
        "包含引用片段与来源元数据，不打包论文全文；已获取原文继续在本地软件查看。\n"
        "evidence.csv 的 source_scope/current_scope 表示实际材料范围；stored_source_scope 保留原存储标签。\n"
        "CSV 的原文窗口仅在本地正文按 Unicode 码点核对跨度后填写；空值表示未核实。\n"
        "JSON 的来源正文在原始快照中保存，original_window 为 null；元数据中的声明位置保留其原记录状态。\n"
        "本地文件路径在导出副本中省略；原始快照留在本机，来源 URL 与哈希保留。\n"
        "科研用途。导出内容包含当前研究与证据；分享前检查私人研究资料。\n").encode())
    manifest = {"product": "SynBioScout AI Scientist", "version": __version__,
        "project_id": run.project_id, "run_id": run.run_id, "status": run.status.value,
        "file_hashes": {name: hashlib.sha256(content).hexdigest() for name, content in entries.items()},
        "target_files": mapping, "omitted_files": skipped,
        "limits": {"per_target_file_bytes": MAX_TARGET_FILE_BYTES, "total_archive_input_bytes": MAX_ARCHIVE_INPUT_BYTES}}
    add("manifest.json", json_bytes(manifest))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue(), "application/zip", stem + ".zip"

