"""Schema allowlist for research receipts shared by results, reports and exports."""
from __future__ import annotations

import hashlib

from . import flow_models as models
from .source_scope import source_scope_view
from .tool_public import (
    _MISSING,
    _scalar,
    _text,
    _url,
    public_publication_metadata,
    public_tool_source,
)

NESTED = {
    "publications": models.PublicationGroup, "snapshots": models.SourceSnapshot,
    "claims": models.ClaimRecord, "queries": models.QueryReceipt, "links": models.EvidenceLink,
    "claim_reviews": models.ClaimReview, "novelty": models.NoveltyReceipt,
    "revisions": models.RevisionReceipt, "prior_work": models.PriorWork,
    "reference_coverage": models.ReferenceCoverage,
    "evidence_relations": models.ClaimEvidenceRelation,
}
STAGES = {"landscape", "supplement", "novelty", "feedback", "full_text"}


def _safe(value):
    clean = _scalar(value, 2000)
    return "[非公开诊断已省略]" if clean is _MISSING else clean


def _project(value, model):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in model.model_fields:
        if key not in value:
            continue
        item = value[key]
        if key == "claim_relation":
            # A legacy null relation is absence, not an approved v2 relation.
            result[key] = _project(item, models.ClaimEvidenceRelation) if isinstance(item, dict) else None
        elif key in NESTED:
            result[key] = [_project(v, NESTED[key]) for v in item[:300]] if isinstance(item, list) else []
        elif key in {"stage_limits", "stage_usage"}:
            result[key] = {k: v for k, v in item.items() if k in STAGES and type(v) is int and 0 <= v <= 100} if isinstance(item, dict) else {}
        elif key == "library_status":
            result[key] = [{k: _safe(v) for k, v in row.items() if k in {"library", "status", "records", "skipped", "pdf_count", "reused_records", "newly_acquired_records", "newly_selected_records", "selected_records", "observed_records", "version", "manifest_sha256"}}
                           for row in item[:5] if isinstance(row, dict)] if isinstance(item, list) else []
        elif key == "url":
            url = _url(item)
            result[key] = "" if url is _MISSING else url
        elif isinstance(item, list):
            result[key] = [_safe(v) for v in item[:100] if isinstance(v, (str, int, bool))]
        elif not isinstance(item, dict):
            result[key] = _safe(item)
    return result


def public_research_flow(value):
    return _project(value, models.ResearchFlowState) if value is not None else None


def public_literature_source(value):
    """A declared public paper is not permission to publish arbitrary metadata."""
    original = value.model_dump() if hasattr(value, "model_dump") else dict(value)
    row = {}
    for key in ("source_id", "provider", "title", "retrieved_at", "text", "pmid", "doi", "pmcid", "evidence_scope"):
        if key in original:
            clean_text = _text(original[key], 2000000 if key == "text" else 2000)
            row[key] = "[非公开诊断已省略]" if clean_text is _MISSING else clean_text
    clean_url = _url(original.get("url"))
    row["url"] = "" if clean_url is _MISSING else clean_url
    row["metadata"] = original.get("metadata")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    clean = {}
    if isinstance(original.get("text"), str):
        clean["retrieved_text_sha256"] = hashlib.sha256(original["text"].encode()).hexdigest()
    for key in ("year", "is_open_access", "query", "arxiv_id", "version", "synthetic", "not_a_real_publication",
                "retrieved_text_sha256", "original_text_sha256", "export_text_included", "local_excerpt_only",
                "acquisition_origin", "background_package_id", "background_version", "background_manifest_sha256",
                "original_evidence_scope", "excerpt_start", "excerpt_end", "excerpt_location", "excerpt_sha256",
                "excerpt_selection", "inclusion_reason", "evidence_level"):
        if key in metadata:
            clean[key] = _safe(metadata[key])
    clean.update(public_publication_metadata(metadata))
    for key in ("full_text_source_url",):
        url = _url(metadata.get(key))
        if url is not _MISSING:
            clean[key] = url
    row["metadata"] = clean
    # Acquisition is a recorded provenance fact; window validity must instead
    # follow the actual safe text being returned (which may be redacted).
    row["metadata"]["acquisition_origin"] = source_scope_view(original)["acquisition_origin"]
    scope = source_scope_view(row)
    row["metadata"]["source_scope"] = scope
    row["metadata"]["acquisition_origin"] = scope["acquisition_origin"]
    return row


def public_source_view(value):
    original = value.model_dump() if hasattr(value, "model_dump") else dict(value)
    row = public_literature_source(original)
    if original.get("evidence_scope") in {"database", "local_document"}:
        row["metadata"] = public_tool_source(original).get("metadata", {})
    return row


def research_observation_counts(run):
    """Count receipts by observed execution and scope, never by planned intent."""
    if hasattr(run, "model_dump"):
        run = run.model_dump(mode="json")
    sources = run.get("sources", [])
    evidence = [row for row in run.get("evidence", []) if row.get("validated_span") is True]
    co = run.get("co_scientist") or {}
    flow = co.get("research_flow") or {}
    groups = flow.get("publications", [])
    ingested = {row.get("source_id") for row in sources}
    tool_ids = {row.get("source_id") for tool in co.get("tool_calls", [])
                for row in tool.get("sources", []) if isinstance(row, dict)}
    by_kind = {kind: [group for group in groups if group.get("kind", "paper") == kind]
               for kind in ("paper", "database", "local_document")}
    in_run = lambda group: any(s.get("source_id") in ingested for s in group.get("snapshots", []))
    raw_tool = lambda group: (not in_run(group) and
        any(s.get("source_id") in tool_ids for s in group.get("snapshots", [])))
    counts = {
        "accepted_citation_count": len(evidence),
        "accepted_citation_source_count": len({row.get("source_id") for row in evidence}),
        "formal_hypothesis_review_count": len(co.get("reviews", [])),
        "canonical_paper_count": len(by_kind["paper"]),
        "database_group_count": len(by_kind["database"]),
        "local_document_group_count": len(by_kind["local_document"]),
        "ingested_paper_group_count": sum(in_run(g) for g in by_kind["paper"]),
        "tool_observation_only_paper_group_count": sum(raw_tool(g) for g in by_kind["paper"]),
        "paper_count": sum(s.get("evidence_scope") in {"abstract", "full_text"} for s in sources),
        "database_source_count": sum(s.get("evidence_scope") == "database" for s in sources),
        "source_snapshot_count": sum(len(g.get("snapshots", [])) for g in groups),
        "query_plan_count": len(flow.get("queries", [])),
        "executed_literature_query_count": 0, "executed_full_text_count": 0,
        "reused_query_count": 0, "unexecuted_query_count": 0, "unknown_execution_query_count": 0,
    }
    for q in flow.get("queries", []):
        if q.get("status") == "reused" or q.get("stage") == "reuse":
            counts["reused_query_count"] += 1
        elif q.get("request_count", 0) > 0 or q.get("cache_hits", 0) > 0:
            key = "executed_full_text_count" if q.get("stage") == "full_text" else "executed_literature_query_count"
            counts[key] += 1
        elif q.get("status") in {"planned", "pending", "skipped", "budget_exhausted", "cancelled"}:
            counts["unexecuted_query_count"] += 1
        else:
            counts["unknown_execution_query_count"] += 1
    return counts


def render_research_flow(value):
    data = public_research_flow(value)
    if not data:
        return ""
    lines = ["", "## 文献地图与逐主张研究记录", "", "此处按出版物归并同一论文，并记录逐主张的检索范围、来源与查新状态。", "",
             f"论文分组 {sum(g.get('kind') == 'paper' for g in data.get('publications', []))}；数据库分组 {sum(g.get('kind') == 'database' for g in data.get('publications', []))}；主张 {len(data.get('claims', []))}；查询计划/状态 {len(data.get('queries', []))}（含复用和未执行）。", ""]
    for q in data.get("queries", []):
        lines.append(f"- `{q.get('query_id', '')}` · {q.get('stage', '')}/{q.get('purpose', '')} · {q.get('status', '')}：{q.get('query', '')}；窗口 {q.get('date_from') or '未限定基础工作'} → {q.get('date_to') or '未限定'}；总命中 {q.get('total_hits') if q.get('total_hits') is not None else '未知'}；覆盖 {q.get('coverage', 'unknown')}；排序 {q.get('sort') or '未记录'}；停止 {q.get('stop_reason', '')}；来源 {', '.join(q.get('source_ids', []))}")
    for c in data.get("claims", []):
        lines.append(f"- `{c.get('claim_id', '')}` · {c.get('state', '')}：{c.get('text', '')}；引用 {', '.join(c.get('evidence_ids', []))}；缺口 {'; '.join(c.get('gaps', []))}")
    lines += ["", *["- " + x for x in data.get("limitations", [])]]
    return "\n".join(lines) + "\n"

